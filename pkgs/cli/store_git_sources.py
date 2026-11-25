# SPDX-License-Identifier: MIT
"""Closed immutable store-Git inputs for private portable12/13 only.

Syntax is not physical authority. Capture checks registration/canonical identity;
the daemon-free build validator checks the actual NAR and selected Git graph.
Neither changes the developer SDK's Git configuration or executes source hooks.
"""

from contextlib import contextmanager
import configparser
import base64
import hashlib
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from urllib.parse import quote, unquote_to_bytes, urlsplit

import dependencies_v1_cache as cache
import legacy_state as legacy

require = cache.require
KIND = "localSourceControl"
FIELDS = {"path", "narHash", "subdirectory"}
PIN_FIELDS = {"sourceKind", "storeSource"}
STORE = re.compile(
    r"/nix/store/[0123456789abcdfghijklmnpqrsvwxyz]{32}-([A-Za-z0-9+._?=-]+)"
)
LEAF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
REVISION = re.compile(r"[0-9a-f]{40}")


def valid_store_name(name):
    # Nix StorePath::checkName / MaxPathLen; keep this lexical check pure.
    return (
        isinstance(name, str)
        and 0 < len(name) <= 211
        and re.fullmatch(r"[A-Za-z0-9+._?=-]+", name) is not None
        and re.match(r"^\.\.?($|-)", name) is None
    )


def content_addressed_root(nar_hash, name):
    """Nix recursive SHA256 source identity (no references), not store access.

    Specification: https://nix.dev/manual/nix/2.34/protocols/store-path
    Actual original/counter-name store paths are independent test vectors.
    """
    require(
        isinstance(nar_hash, str) and legacy.HASH.fullmatch(nar_hash) is not None,
        "store Git input NAR hash",
    )
    require(valid_store_name(name), "store Git source name")
    digest = base64.b64decode(nar_hash.split("-", 1)[1], validate=True)
    preimage = "source:sha256:" + digest.hex() + ":/nix/store:" + name
    raw = hashlib.sha256(preimage.encode("ascii")).digest()
    compressed = bytearray(20)
    for index, byte in enumerate(raw):
        compressed[index % 20] ^= byte
    alphabet = "0123456789abcdfghijklmnpqrsvwxyz"
    result = []
    for group in range(31, -1, -1):
        bit = group * 5
        index, shift = divmod(bit, 8)
        value = compressed[index] >> shift
        if index + 1 < len(compressed):
            value |= compressed[index + 1] << (8 - shift)
        result.append(alphabet[value & 31])
    return "/nix/store/" + "".join(result) + "-" + name


def split_location(location):
    require(isinstance(location, str) and len(location) <= 4096, "store Git location")
    parts = location.split("/")
    require(
        len(parts) == 5 and parts[:3] == ["", "nix", "store"], "store Git path boundary"
    )
    root, leaf = "/nix/store/" + parts[3], parts[4]
    match = STORE.fullmatch(root)
    require(
        match is not None and valid_store_name(match[1]), "canonical store Git root"
    )
    require(
        LEAF.fullmatch(leaf) is not None and len(leaf) <= 255,
        "store Git repository leaf",
    )
    name = leaf[:-4] if leaf.endswith(".git") else leaf
    require(name not in ("", ".", ".."), "store Git checkout subpath")
    return root, leaf


def canonical_uri(location):
    split_location(location)
    return "file://" + quote(location, safe="/")


def uri_location(value):
    require(isinstance(value, str) and len(value) <= 4096, "store Git URI")
    try:
        parsed = urlsplit(value)
        require(
            parsed.scheme == "file"
            and not parsed.netloc
            and not parsed.query
            and not parsed.fragment,
            "store Git URI boundary",
        )
        location = unquote_to_bytes(parsed.path).decode("utf-8")
    except (ValueError, UnicodeError) as error:
        raise cache.Reject("store Git URI encoding") from error
    require(canonical_uri(location) == value, "canonical store Git URI spelling")
    return location


def checkout_subpath(location):
    _, leaf = split_location(location)
    return leaf[:-4] if leaf.endswith(".git") else leaf


@dataclass(frozen=True)
class Source:
    path: str
    nar_hash: str
    subdirectory: str

    @classmethod
    def decode(cls, value):
        require(
            isinstance(value, dict) and set(value) == FIELDS,
            "closed store Git source fields",
        )
        require(
            isinstance(value["narHash"], str)
            and legacy.HASH.fullmatch(value["narHash"]) is not None,
            "store Git input NAR hash",
        )
        location = (
            value["path"] + "/" + value["subdirectory"]
            if isinstance(value["path"], str) and isinstance(value["subdirectory"], str)
            else ""
        )
        root, leaf = split_location(location)
        require(
            root == value["path"] and leaf == value["subdirectory"],
            "store Git source spelling",
        )
        name = Path(root).name[33:]
        require(
            content_addressed_root(value["narHash"], name) == root,
            "store Git canonical content-addressed identity",
        )
        return cls(root, value["narHash"], leaf)

    @property
    def location(self):
        return self.path + "/" + self.subdirectory

    @property
    def uri(self):
        return canonical_uri(self.location)

    @property
    def name(self):
        return Path(self.path).name[33:]

    def encode(self):
        return {
            "path": self.path,
            "narHash": self.nar_hash,
            "subdirectory": self.subdirectory,
        }


def pin_source(pin):
    """Decode the closed file-pin extension; omitted fields still mean remote."""
    require(isinstance(pin, dict), "store Git pin object")
    present = PIN_FIELDS.intersection(pin)
    if not present:
        return None
    require(
        present == PIN_FIELDS and pin["sourceKind"] == KIND and "url" not in pin,
        "store Git pin discriminator/mirror",
    )
    source = Source.decode(pin["storeSource"])
    require(pin.get("sourceLocation") == source.uri, "store Git pin URI/input identity")
    require(
        isinstance(pin.get("revision"), str)
        and REVISION.fullmatch(pin["revision"]) is not None,
        "store Git SHA1 revision",
    )
    return source


def _ref_name(value):
    return (
        isinstance(value, bytes)
        and value.startswith(b"refs/")
        and not value.startswith(b"refs/replace/")
        and len(value) <= 4096
        and b".." not in value
        and b"@{" not in value
        and not value.endswith(b".")
        and not any(c <= 32 or c == 127 or c in b"~^:?*[\\" for c in value)
        and all(
            part and not part.startswith(b".") and not part.endswith(b".lock")
            for part in value.split(b"/")
        )
    )


def _ref_contents(value):
    value = value.removesuffix(b"\n")
    return re.fullmatch(b"[0-9a-fA-F]{40}", value) is not None or (
        value.startswith(b"ref: ") and _ref_name(value[5:])
    )


def _check_layout(source, deadline):
    root, repo = Path(source.path), Path(source.location)
    require(
        root.is_dir() and not root.is_symlink() and root == root.resolve(),
        "physical canonical store Git input",
    )
    require(
        repo.is_dir() and not repo.is_symlink() and repo == repo.resolve(),
        "physical bare Git repository",
    )
    allowed = {
        "HEAD",
        "config",
        "description",
        "hooks",
        "info",
        "objects",
        "packed-refs",
        "refs",
        "branches",
        "logs",
        "FETCH_HEAD",
        "ORIG_HEAD",
    }
    require(
        {p.name for p in repo.iterdir()} <= allowed
        and (repo / "HEAD").is_file()
        and (repo / "config").is_file(),
        "closed bare Git layout",
    )
    for path in repo.rglob("*"):
        require(time.monotonic() < deadline, "store Git inspection deadline")
        mode = path.lstat().st_mode
        require(
            stat.S_ISREG(mode) or stat.S_ISDIR(mode),
            "store Git repository links/special files",
        )
        require(not path.name.endswith(".promisor"), "store Git promisor input")
    for relative in (
        "shallow",
        "commondir",
        "gitdir",
        "worktrees",
        "info/grafts",
        "objects/info/alternates",
        "objects/info/http-alternates",
        "refs/replace",
    ):
        require(
            not os.path.lexists(repo / relative),
            "store Git external/partial object context",
        )
    require(
        (repo / "objects").is_dir() and (repo / "refs").is_dir(),
        "store Git object/ref directories",
    )
    require(
        (repo / "HEAD").stat().st_size <= 4096
        and _ref_contents((repo / "HEAD").read_bytes()),
        "store Git HEAD context",
    )
    for path in (repo / "refs").rglob("*"):
        if path.is_file():
            name = str(path.relative_to(repo)).encode("utf-8", errors="surrogateescape")
            require(
                _ref_name(name)
                and path.stat().st_size <= 4096
                and _ref_contents(path.read_bytes()),
                "store Git external ref",
            )
    packed = repo / "packed-refs"
    if packed.exists():
        require(
            packed.stat().st_size <= 16 * 1024 * 1024, "store Git packed refs bound"
        )
        for line in packed.read_bytes().splitlines():
            if line.startswith(b"#"):
                continue
            if line.startswith(b"^"):
                require(
                    re.fullmatch(b"[0-9a-fA-F]{40}", line[1:]) is not None,
                    "store Git peeled ref",
                )
                continue
            oid, separator, name = line.partition(b" ")
            require(
                separator
                and re.fullmatch(b"[0-9a-fA-F]{40}", oid) is not None
                and _ref_name(name),
                "store Git packed ref context",
            )
    if (repo / "hooks").exists():
        require(
            all(
                p.is_file() and p.name.endswith(".sample")
                for p in (repo / "hooks").iterdir()
            ),
            "store Git active hooks",
        )
    with (repo / "config").open("rb") as stream:
        raw = stream.read(64 * 1024 + 1)
    require(len(raw) <= 64 * 1024, "store Git config bound")
    parser = configparser.RawConfigParser(
        strict=True, interpolation=None, empty_lines_in_values=False
    )
    try:
        parser.read_string(raw.decode("utf-8"))
    except (UnicodeError, configparser.Error) as error:
        raise cache.Reject("store Git config syntax") from error
    require(not parser.defaults() and "core" in parser, "store Git core config")
    core = {
        "repositoryformatversion",
        "bare",
        "filemode",
        "logallrefupdates",
        "ignorecase",
        "precomposeunicode",
        "protecthfs",
        "protectntfs",
        "sharedrepository",
    }
    for section in parser.sections():
        if section == "core":
            require(
                set(parser[section]) <= core,
                "store Git executable/external core config",
            )
        else:
            require(
                re.fullmatch(r'remote "[A-Za-z0-9._-]+"', section) is not None
                and set(parser[section]) <= {"url", "fetch", "mirror", "tagopt"},
                "store Git config indirection/extensions",
            )
    require(
        parser["core"].get("repositoryformatversion", "").strip('"') == "0"
        and parser["core"].get("bare", "").lower().strip('"') == "true",
        "store Git requires bare SHA1 format0",
    )
    return repo


@contextmanager
def metadata_environment(session, repo):
    """Isolate metadata tools only; restore the SDK's original environment."""
    original = session.env
    with tempfile.TemporaryDirectory(
        prefix="store-git-metadata-", dir=session.evidence
    ) as work:
        work = Path(work)
        config = work / "gitconfig"
        config.write_text(
            "[core]\n\thooksPath = /dev/null\n[safe]\n\tdirectory = "
            + json.dumps(str(repo))
            + "\n"
        )
        config.chmod(0o600)
        isolated = {
            k: v
            for k, v in original.items()
            if not k.startswith(("GIT_", "NIX_", "DYLD_"))
            and k not in {"LD_PRELOAD", "LD_LIBRARY_PATH"}
        }
        isolated.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_SYSTEM="/dev/null",
            GIT_CONFIG_GLOBAL=str(config),
            GIT_NO_REPLACE_OBJECTS="1",
            GIT_ALLOW_PROTOCOL="",
            NIX_CONF_DIR=str(work),
            NIX_USER_CONF_FILES="",
            NIX_CONFIG="experimental-features = nix-command flakes\n",
        )
        session.env = isolated
        try:
            yield
        finally:
            session.env = original


def validate_tree(tree):
    """Check real ls-tree -rz output without granting repository authority."""
    require(
        isinstance(tree, bytes) and (not tree or tree.endswith(b"\0")),
        "store Git tree byte framing",
    )
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        header, separator, path = entry.partition(b"\t")
        require(
            separator
            and not path.startswith(b"/")
            and all(part not in (b"", b".", b"..") for part in path.split(b"/")),
            "store Git selected tree path",
        )
        fields = header.split(b" ")
        require(len(fields) == 3, "store Git tree record")
        mode, kind, oid = fields
        require(
            mode in (b"100644", b"100755")
            and kind == b"blob"
            and re.fullmatch(b"[0-9a-f]{40}", oid) is not None,
            "store Git selected links/submodules",
        )
        require(
            not any(
                part.lower() in (b".git", b".gitmodules") for part in path.split(b"/")
            ),
            "store Git unbound submodule/VCS tree",
        )


def inspect(source, revision, branch, version, session, git, nix, *, label):
    """Daemon-free physical/NAR/Git validation, also used inside Nix builds."""
    require(isinstance(source, Source), "typed store Git source")
    source = Source.decode(source.encode())
    require(
        isinstance(revision, str) and REVISION.fullmatch(revision) is not None,
        "store Git revision",
    )
    require(
        branch is None
        or (
            isinstance(branch, str)
            and 0 < len(branch.encode("utf-8")) <= 4096
            and not any(ord(c) < 32 or ord(c) == 127 for c in branch)
        ),
        "store Git branch",
    )
    require(
        branch is None or _ref_name(("refs/heads/" + branch).encode("utf-8")),
        "store Git literal branch reference",
    )
    require(
        version is None or legacy.version_valid(version), "store Git semantic version"
    )
    require(branch is None or version is None, "store Git branch/version conflict")
    repo = _check_layout(source, session.deadline)
    with metadata_environment(session, repo):
        actual = (
            session.run(
                label + "-nar",
                [
                    nix,
                    "--offline",
                    "--store",
                    "dummy://",
                    "hash",
                    "path",
                    "--type",
                    "sha256",
                    source.path,
                ],
            )
            .stdout.decode()
            .strip()
        )
        require(actual == source.nar_hash, "store Git actual input NAR identity")
        command = [git, "--no-optional-locks", "--git-dir", str(repo)]
        require(
            session.run(
                label + "-bare", [*command, "rev-parse", "--is-bare-repository"]
            ).stdout
            == b"true\n",
            "actual bare Git source",
        )
        require(
            session.run(label + "-type", [*command, "cat-file", "-t", revision]).stdout
            == b"commit\n",
            "actual store Git commit",
        )
        session.run(
            label + "-objects",
            [*command, "fsck", "--no-reflogs", "--no-dangling", revision],
        )
        if branch is not None:
            head = (
                session.run(
                    label + "-branch",
                    [
                        *command,
                        "rev-parse",
                        "--verify",
                        "refs/heads/" + branch + "^{commit}",
                    ],
                )
                .stdout.decode()
                .strip()
            )
            require(head == revision, "store Git branch/revision join")
        if version is not None:
            matched = False
            for index, tag in enumerate((version, "v" + version)):
                result = session.run(
                    label + "-tag-" + str(index),
                    [
                        *command,
                        "rev-parse",
                        "--verify",
                        "refs/tags/" + tag + "^{commit}",
                    ],
                    success=False,
                )
                matched |= (
                    result.returncode == 0
                    and result.stdout.decode().strip() == revision
                )
            require(matched, "store Git tag/revision join")
        tree = session.run(
            label + "-tree", [*command, "ls-tree", "-r", "-z", "--full-tree", revision]
        ).stdout
        validate_tree(tree)
    return source


def capture(location, revision, branch, version, session, api, *, label):
    """Capture-only registration and pure canonical identity, then physical checks."""
    root, leaf = split_location(location)
    nix = api.tool("nix")
    options = [
        nix,
        "--offline",
        "--option",
        "allow-import-from-derivation",
        "false",
        "--option",
        "substitute",
        "false",
        "--option",
        "builders",
        "",
    ]
    with metadata_environment(session, Path(location)):
        info = cache.decode(
            session.run(
                label + "-registration",
                [*options, "path-info", "--json", "--json-format", "1", root],
            ).stdout
        )
    require(
        isinstance(info, dict) and set(info) == {root} and isinstance(info[root], dict),
        "store Git registration identity",
    )
    require(
        info[root].get("references") == [],
        "store Git referenced input outside first scope",
    )
    source = Source.decode(
        {"path": root, "subdirectory": leaf, "narHash": info[root].get("narHash")}
    )
    inspect(
        source, revision, branch, version, session, api.tool("git"), nix, label=label
    )
    expression = (
        "let p=builtins.path {path=builtins.toPath "
        + json.dumps(root)
        + ";name="
        + json.dumps(source.name)
        + ";sha256="
        + json.dumps(source.nar_hash)
        + ';};in {path=toString p;context=builtins.getContext ("file://${p}/'
        + leaf
        + '");}'
    )
    with metadata_environment(session, Path(location)):
        identity = cache.decode(
            session.run(
                label + "-canonical", [*options, "eval", "--json", "--expr", expression]
            ).stdout
        )
    require(
        identity == {"path": root, "context": {root: {"path": True}}},
        "store Git canonical content-addressed input/context",
    )
    return source
