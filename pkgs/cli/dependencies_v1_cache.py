# SPDX-License-Identifier: MIT
"""Format-8 native checkout cache preflight; no Git or Swift execution."""

from pathlib import Path
import configparser
import json
import os
import re
import stat

import legacy_state as legacy


class Reject(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Reject(message)


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=pairs)


def directory(path):
    require(stat.S_ISDIR(path.lstat().st_mode), "directory must not be a link")


def regular_bytes(root, relative, limit=1024 * 1024):
    """Read through confined directory descriptors, never following links."""
    parts = Path(relative).parts
    require(
        parts and all(part not in ("", ".", "..") for part in parts), "relative name"
    )
    require(not Path(relative).is_absolute(), "relative name")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
        prior = os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
        require(
            stat.S_ISREG(prior.st_mode) and prior.st_nlink == 1,
            "regular unaliased file before open",
        )
        require(prior.st_size <= limit, "file bound before open")
        leaf = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor
        )
        try:
            facts = os.fstat(leaf)
            require(
                (facts.st_dev, facts.st_ino) == (prior.st_dev, prior.st_ino),
                "changed file identity",
            )
            require(
                stat.S_ISREG(facts.st_mode) and facts.st_nlink == 1,
                "regular unaliased file",
            )
            require(facts.st_size <= limit, "file bound")
            with os.fdopen(os.dup(leaf), "rb") as stream:
                raw = stream.read(limit + 1)
            require(len(raw) <= limit, "file bound")
            return raw
        finally:
            os.close(leaf)
    finally:
        os.close(descriptor)


def validate(root, environment=None):
    """Pure filesystem validation: no Git, Swift, subprocess or runtime probe."""
    root = Path(root)
    directory(root)
    require(root.absolute() == root.resolve(), "canonical native cache root")
    for name in (".build", ".build/checkouts", ".build/repositories"):
        directory(root / name)
    forbidden = {
        "GIT_DIR",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
    }
    require(
        not forbidden.intersection(os.environ if environment is None else environment),
        "external Git environment",
    )
    require(
        not os.path.lexists(root / ".build/workspace-state.json"), "mixed native state"
    )
    require(not os.path.lexists(root / "Package.pins"), "mixed native pin format")
    state = decode(regular_bytes(root, ".build/dependencies-state.json"))
    require(
        isinstance(state, dict) and set(state) == {"version", "object"},
        "native state fields",
    )
    require(
        type(state["version"]) is int and state["version"] == 1, "native state version"
    )
    require(
        isinstance(state["object"], dict) and set(state["object"]) == {"dependencies"},
        "native object",
    )
    dependencies = state["object"]["dependencies"]
    require(
        isinstance(dependencies, list) and 0 < len(dependencies) <= 1024,
        "bounded nonempty graph",
    )
    registry = decode(regular_bytes(root, ".build/repositories/checkouts-state.json"))
    require(
        isinstance(registry, dict)
        and set(registry) == {"version", "object"}
        and type(registry["version"]) is int
        and registry["version"] == 1,
        "registry version",
    )
    require(
        isinstance(registry["object"], dict)
        and set(registry["object"]) == {"repositories"},
        "registry object",
    )
    registered = registry["object"]["repositories"]
    require(isinstance(registered, dict), "registry mapping")
    expected_names = set()
    expected_urls = set()
    expected_subpaths = set()
    for dep in dependencies:
        require(isinstance(dep, dict), "dependency record")
        url, subpath = dep.get("repositoryURL"), dep.get("subpath")
        require(
            isinstance(url, str) and bool(url) and isinstance(subpath, str),
            "dependency identity types",
        )
        require(
            url not in expected_urls and subpath not in expected_subpaths,
            "duplicate graph identity",
        )
        name = dep.get("name")
        require(
            legacy.safe_name(name) and name not in expected_names,
            "unique native package name",
        )
        require(legacy.git_location(url), "native Git source")
        expected_names.add(name)
        expected_urls.add(url)
        expected_subpaths.add(subpath)
    require(set(registered) == expected_urls, "registry URL identities")
    results = []
    for dep in dependencies:
        require(
            isinstance(dep, dict)
            and set(dep) == {"basedOn", "name", "repositoryURL", "state", "subpath"},
            "dependency fields",
        )
        require(dep["basedOn"] is None, "unowned edited state")
        subpath = dep["subpath"]
        require(
            isinstance(subpath, str)
            and 0 < len(subpath) <= 255
            and subpath not in (".", "..")
            and not any(c in subpath for c in "/\\")
            and not any(ord(c) < 32 for c in subpath),
            "opaque confined subpath",
        )
        native = dep["state"]
        require(
            isinstance(native, dict)
            and set(native) == {"name", "checkoutState"}
            and native["name"] == "checkout",
            "checkout state",
        )
        checkout_state = native["checkoutState"]
        require(
            isinstance(checkout_state, dict)
            and set(checkout_state) == {"branch", "revision", "version"},
            "checkout identity",
        )
        require(
            checkout_state["branch"] is None
            and re.fullmatch("[0-9a-f]{40}", checkout_state["revision"] or "")
            is not None,
            "checkout revision",
        )
        require(
            legacy.version_valid(checkout_state["version"]), "semver native checkout"
        )
        entry = registered[dep["repositoryURL"]]
        require(
            entry
            == {
                "repositoryURL": dep["repositoryURL"],
                "status": "available",
                "subpath": subpath,
            },
            "registry opaque locator",
        )
        checkout = root / ".build/checkouts" / subpath
        bare = root / ".build/repositories" / subpath
        gitdir = checkout / ".git"
        for path in (checkout, bare, gitdir):
            directory(path)
        require(
            {p.name for p in (root / ".build/repositories").iterdir()}
            == expected_subpaths | {"checkouts-state.json"},
            "extra repository",
        )
        require(
            {p.name for p in (root / ".build/checkouts").iterdir()}
            == expected_subpaths,
            "extra checkout",
        )
        for tree in (gitdir, bare):
            count = 0
            for path in tree.rglob("*"):
                facts = path.lstat()
                require(
                    stat.S_ISDIR(facts.st_mode) or stat.S_ISREG(facts.st_mode),
                    "metadata link or nonregular file",
                )
                if stat.S_ISREG(facts.st_mode):
                    require(facts.st_nlink == 1, "metadata hardlink alias")
                count += 1
                require(count <= 100000, "metadata count")
            for name in (
                "commondir",
                "objects/info/http-alternates",
                "info/grafts",
                "index.lock",
            ):
                require(
                    not os.path.lexists(tree / name),
                    "unknown metadata pointer or active index",
                )
            require(not any((tree / "refs/replace").glob("*")), "replace refs")
            require(not any(tree.glob("sharedindex.*")), "unmeasured split index")
            hooks = tree / "hooks"
            require(
                not hooks.exists()
                or all(p.name.endswith(".sample") for p in hooks.iterdir()),
                "active native hooks",
            )
        require(
            regular_bytes(
                root, str((gitdir / "objects/info/alternates").relative_to(root))
            )
            .decode()
            .splitlines()
            == [str(bare / "objects")],
            "alternate chain",
        )
        require(
            not os.path.lexists(bare / "objects/info/alternates"),
            "extra bare alternate chain",
        )
        for tree, expected in ((gitdir, str(bare)), (bare, dep["repositoryURL"])):
            parser = configparser.ConfigParser(interpolation=None, strict=True)
            parser.read_string(
                regular_bytes(root, str((tree / "config").relative_to(root))).decode()
            )
            require(not parser.defaults(), "unmeasured Git config defaults")
            require(
                set(parser["core"])
                <= {"repositoryformatversion", "filemode", "bare", "logallrefupdates"},
                "unmeasured Git core configuration",
            )
            require(
                parser["core"].get("repositoryformatversion") == "0",
                "Git repository format",
            )
            require(
                parser["core"].get("bare") == ("false" if tree == gitdir else "true"),
                "Git bare role",
            )
            for key in ("filemode", "logallrefupdates"):
                require(
                    key not in parser["core"]
                    or parser["core"][key] in ("true", "false"),
                    "Git boolean configuration",
                )
            origin = parser['remote "origin"']
            require(
                set(origin) <= {"url", "fetch", "tagopt", "mirror"},
                "unmeasured Git remote configuration",
            )
            require(origin["url"] == expected, "origin chain")
            require(
                origin.get("fetch")
                == (
                    "+refs/heads/*:refs/remotes/origin/*"
                    if tree == gitdir
                    else "+refs/*:refs/*"
                ),
                "native fetch refspec",
            )
            require(
                "tagopt" not in origin or origin["tagopt"] == "--no-tags",
                "native tag option",
            )
            require(
                "mirror" not in origin or origin["mirror"] == "true",
                "native mirror option",
            )
            for section in parser.sections():
                if section in ("core", 'remote "origin"'):
                    continue
                require(
                    re.fullmatch(r'branch "[^"\r\n]+"', section) is not None,
                    "unmeasured Git configuration section",
                )
                branch = parser[section]
                require(
                    set(branch) == {"remote", "merge"}
                    and branch["remote"] == "origin"
                    and branch["merge"].startswith("refs/heads/")
                    and not any(ord(c) < 32 or ord(c) == 127 for c in branch["merge"]),
                    "native branch tracking configuration",
                )
        results.append(
            {
                "subpath": subpath,
                "revision": checkout_state["revision"],
                "URL": dep["repositoryURL"],
            }
        )
    return results
