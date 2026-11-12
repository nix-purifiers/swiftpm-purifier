# SPDX-License-Identifier: MIT
"""Repo-local format 7: actual pre-workspace native Git checkouts.

No runtime release allowlist, selected SDK, or invented native lock file.
"""

import base64
import hashlib
import json
import os
from pathlib import Path
import re

import legacy_state as legacy
import pre_workspace_plan as plan
import pre_workspace_layout as layout

FORMAT = 7
StateError = plan.Reject
require = plan.require


def content_hash(lock):
    content = {key: value for key, value in lock.items() if key != "contentHash"}
    raw = json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return "sha256-" + base64.b64encode(hashlib.sha256(raw).digest()).decode()


def manifest_hash(root):
    root = Path(root)
    require(
        (root / "Package.swift").is_file()
        and not (root / "Package.swift").is_symlink(),
        "native Package.swift must be regular",
    )
    manifests = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if re.fullmatch(r"Package(@swift-[0-9.]+)?\.swift", path.name):
            require(
                path.is_file() and not path.is_symlink(),
                "native manifests must be regular",
            )
            manifests.append([path.name, hashlib.sha256(path.read_bytes()).hexdigest()])
    raw = json.dumps(manifests, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


TOP = {
    "version",
    "nativeProtocol",
    "package",
    "rootDirectory",
    "manifestHash",
    "pins",
    "products",
    "localDependencies",
    "binaryTargets",
    "nativeLockFiles",
    "contentHash",
    "sourceLayoutHash",
}


def digest(value):
    return isinstance(value, str) and legacy.HASH.fullmatch(value) is not None


def validate_raw(raw, root, override=None, purifier_lock=None):
    require(isinstance(raw, bytes) and len(raw) <= 1024 * 1024, "lock byte bound")
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise StateError("lock UTF-8")
    lock = plan.decode(text)
    require(isinstance(lock, dict) and set(lock) == TOP, "closed top schema")
    require(
        type(lock["version"]) is int
        and lock["version"] == 7
        and lock["nativeProtocol"] == "pre-workspace-git",
        "protocol discriminator",
    )
    require(
        legacy.safe_name(lock["package"]) and legacy.safe_name(lock["rootDirectory"]),
        "root/package name",
    )
    require(
        isinstance(lock["manifestHash"], str)
        and re.fullmatch("[0-9a-f]{64}", lock["manifestHash"]) is not None,
        "manifest hash type",
    )
    require(
        lock["localDependencies"] == [] and lock["binaryTargets"] == [],
        "unsupported local/binary records",
    )
    require(
        isinstance(lock["nativeLockFiles"], dict)
        and set(lock["nativeLockFiles"]) == {"Package.pins", "Package.resolved"}
        and all(v is None for v in lock["nativeLockFiles"].values()),
        "native exact absence binding",
    )
    native_absence(root, override)
    require(
        isinstance(lock["sourceLayoutHash"], str)
        and re.fullmatch("[0-9a-f]{64}", lock["sourceLayoutHash"]) is not None,
        "source layout hash type",
    )
    try:
        actual_layout = layout.source_layout(root, purifier_lock)["hash"]
    except layout.LayoutError as error:
        raise StateError(str(error)) from error
    require(lock["sourceLayoutHash"] == actual_layout, "source layout binding")
    require(isinstance(lock["pins"], dict), "pins map")
    locations = set()
    for name, pin in lock["pins"].items():
        require(
            isinstance(pin, dict)
            and set(pin)
            in (
                {"name", "sourceLocation", "revision", "version", "hash", "gitHash"},
                {
                    "name",
                    "sourceLocation",
                    "revision",
                    "version",
                    "hash",
                    "gitHash",
                    "url",
                },
            ),
            "closed pin schema",
        )
        require(legacy.safe_name(name) and pin["name"] == name, "native pin name")
        require(
            legacy.git_location(pin["sourceLocation"])
            and pin["sourceLocation"] not in locations,
            "portable unambiguous source",
        )
        locations.add(pin["sourceLocation"])
        require(
            isinstance(pin["revision"], str)
            and legacy.REVISION.fullmatch(pin["revision"]) is not None,
            "actual revision shape",
        )
        require(legacy.version_valid(pin["version"]), "actual version shape")
        require(
            digest(pin["hash"]) and digest(pin["gitHash"]),
            "dual source/real Git hashes",
        )
        if "url" in pin:
            require(legacy.git_location(pin["url"]), "portable mirror")
    require(isinstance(lock["products"], list), "products list")
    names = set()
    for product in lock["products"]:
        require(
            isinstance(product, dict)
            and product.get("type") in ("executable", "library"),
            "product kind",
        )
        expected = {"name", "type", "modules"} | (
            {"linkage"} if product["type"] == "library" else set()
        )
        require(set(product) == expected, "closed product schema")
        require(
            legacy.safe_name(product["name"]) and product["name"] not in names,
            "safe unique product",
        )
        names.add(product["name"])
        require(
            isinstance(product["modules"], list)
            and product["modules"]
            and all(
                isinstance(x, str)
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", x) is not None
                for x in product["modules"]
            )
            and len(product["modules"]) == len(set(product["modules"])),
            "safe unique actual modules",
        )
        if product["type"] == "library":
            require(product["linkage"] in ("static", "dynamic"), "actual linkage")
    require(
        digest(lock["contentHash"]) and lock["contentHash"] == content_hash(lock),
        "content hash",
    )
    require(lock["manifestHash"] == manifest_hash(root), "source manifest binding")
    return lock


def native_absence(root, override=None):
    root = Path(root)
    require(override is None, "format 7 does not support native lockfile overrides")
    for name in ("Package.pins", "Package.resolved"):
        require(
            not os.path.lexists(root / name),
            "format 7 requires native pin files to remain absent",
        )
    build = root / ".build"
    require(not build.is_symlink(), "format 7 build metadata must not be a symlink")
    if os.path.lexists(build):
        require(build.is_dir(), "format 7 build metadata must be a directory")
        for name in ("debug", "release"):
            path = build / name
            require(
                not os.path.lexists(path) or (path.is_dir() and not path.is_symlink()),
                "format 7 generated output root must be a real directory",
            )
        import stat

        entries = 0
        for directory, names, files in os.walk(build, followlinks=False):
            require(
                len(Path(directory).relative_to(build).parts) <= 256,
                "format 7 build tree exceeds depth bound",
            )
            for name in names + files:
                entries += 1
                require(entries <= 100000, "format 7 build tree exceeds entry bound")
                mode = (Path(directory) / name).lstat().st_mode
                require(
                    stat.S_ISREG(mode) or stat.S_ISDIR(mode),
                    "format 7 build tree contains a symlink or nonregular output",
                )
        for directory in (build, build / "index-build"):
            require(
                not directory.is_symlink(),
                "format 7 index metadata must not be a symlink",
            )
            if directory.exists():
                require(
                    directory.is_dir(), "format 7 index metadata must be a directory"
                )
                require(
                    not any(
                        "state" in item.name.lower()
                        or "workspace" in item.name.lower()
                        or item.suffix == ".json"
                        for item in directory.iterdir()
                    ),
                    "format 7 rejects mixed or unknown native workspace metadata",
                )


class NativeSession:
    """Retain bounded actual command observations outside the source copy."""

    def __init__(self, cwd, environment):
        import tempfile
        import time

        self.cwd = Path(cwd)
        self.env = dict(environment)
        self.evidence = Path(tempfile.mkdtemp(prefix="swiftpm-purifier-preworkspace-"))
        self.logs = self.evidence / "logs"
        self.logs.mkdir()
        self.receipt = {"complete": False, "commands": []}
        self.deadline = time.monotonic() + 720
        self.save()

    def save(self):
        (self.evidence / "receipt.json").write_text(
            json.dumps(self.receipt, indent=2) + "\n"
        )

    def run(self, label, argv, cwd=None, protocol_flag=None, success=True, timeout=120):
        import bounded_native
        import time

        remaining = self.deadline - time.monotonic()
        require(remaining > 0, "pre-workspace capture exceeded its pipeline deadline")
        row = {"label": label}
        require(
            all(old["label"] != label for old in self.receipt["commands"]),
            "duplicate native evidence label",
        )
        self.receipt["commands"].append(row)
        try:
            result = bounded_native.run_recorded(
                [str(arg) for arg in argv],
                record=row,
                logs=self.logs,
                save=self.save,
                cwd=self.cwd if cwd is None else cwd,
                env=self.env,
                timeout=min(timeout, remaining),
                output_limit=16 * 1024 * 1024,
                protocol_fd_flag=protocol_flag,
            )
        except (TimeoutError, OSError, ValueError) as error:
            raise StateError(
                "bounded native command failed; evidence at " + str(self.evidence)
            ) from error
        if success and result.returncode != 0:
            raise StateError(
                "selected native command rejected the project; evidence at "
                + str(self.evidence)
            )
        return result


def candidate(api, temporary, environment):
    """Help is only a hint; actual unknown-command behavior discriminates."""
    session = NativeSession(temporary, environment)
    command = api.native_command("swift-package")
    try:
        help_result = session.run(
            "package-help", [*command, "--help"], success=False, timeout=30
        )
    except StateError:
        # An unavailable hint cannot select this new protocol; unrelated
        # existing adapters retain their own native validation and diagnostics.
        session.receipt.update(complete=True, candidate=False, hintUnavailable=True)
        session.save()
        return False
    if help_result.returncode != 0:
        session.receipt.update(complete=True, candidate=False)
        session.save()
        return False
    help_text = help_result.stdout.decode("utf-8", errors="replace")
    if not re.search(r"(?m)^\s+show-dependencies\b", help_text) or re.search(
        r"(?m)^\s+describe\b", help_text
    ):
        session.receipt.update(complete=True, candidate=False)
        session.save()
        return False
    require(
        not any(
            (parent / "Package.swift").exists() for parent in Path(temporary).parents
        ),
        "native protocol probes require TMPDIR outside a Swift package",
    )
    described = session.run(
        "describe-discriminator",
        [*command, "describe", "--type", "json"],
        success=False,
        timeout=30,
    )
    accepted = (
        described.returncode != 0
        and not described.stdout
        and re.search(rb"unknown command:\s*describe\b", described.stderr) is not None
    )
    session.receipt.update(complete=True, candidate=accepted)
    session.save()
    return accepted


def copy_source(root, destination):
    import shutil

    root = Path(root)

    def ignore(directory, names):
        return (
            [
                name
                for name in names
                if name in layout.ROOT_DIRECTORIES | {layout.DEFAULT_LOCK}
            ]
            if Path(directory) == root
            else []
        )

    shutil.copytree(root, destination, ignore=ignore, symlinks=True)
    # Existing Packages is the original native resolution state. Copy real
    # repository payloads; never synthesize pins or re-resolve it from versions.
    cache = root / "Packages"
    if cache.is_dir():
        target = destination / "Packages"
        target.mkdir()
        for item in cache.iterdir():
            if item.is_dir():
                shutil.copytree(item.resolve(), target / item.name, symlinks=True)
            else:
                shutil.copy2(item, target / item.name, follow_symlinks=False)


def graph_dependencies(graph, package, package_name):
    """Join actual native paths and names, flattening a consistent DAG."""
    package = Path(package)
    fields = {"name", "path", "url", "version", "dependencies"}
    require(
        isinstance(graph, dict) and set(graph) == fields,
        "unsupported pre-workspace graph",
    )
    require(
        graph["name"] == package_name
        and graph["path"] == str(package)
        and graph["url"] == str(package)
        and graph["version"] == "unspecified",
        "native root graph disagrees with the manifest/capture path",
    )
    found = {}
    locations = {}
    active = set()
    count = 0

    def visit(node, root=False, depth=0):
        nonlocal count
        count += 1
        require(depth <= 256 and count <= 10000, "native graph exceeds its bound")
        require(
            isinstance(node, dict)
            and set(node) == fields
            and isinstance(node["dependencies"], list),
            "unsupported native graph node",
        )
        if not root:
            name, location, version = node["name"], node["url"], node["version"]
            require(
                legacy.safe_name(name)
                and legacy.git_location(location)
                and legacy.version_valid(version),
                "unsafe or unsupported native graph identity",
            )
            expected = package / "Packages" / (name + "-" + version)
            require(
                node["path"] == str(expected)
                and expected.is_dir()
                and not expected.is_symlink(),
                "native checkout layout is not a contained actual package",
            )
            require(
                expected.resolve().is_relative_to(package / "Packages")
                and (expected / ".git").is_dir()
                and not (expected / ".git").is_symlink(),
                "native checkout Git metadata escapes its source tree",
            )
            basic = {key: node[key] for key in ("name", "path", "url", "version")}
            require(
                name not in found or found[name] == basic,
                "ambiguous native package identity",
            )
            require(
                location not in locations or locations[location] == name,
                "ambiguous native source identity",
            )
            require(location not in active, "cyclic native dependency graph")
            found[name] = basic
            locations[location] = name
            active.add(location)
        for child in node["dependencies"]:
            visit(child, depth=depth + 1)
        if not root:
            active.remove(node["url"])

    visit(graph, root=True)
    return found


def atomic_lock(destination, data):
    """Replace one validated lock atomically, preserving inputs on failure."""
    import tempfile

    destination = layout.canonical(destination)
    missing = []
    created = []
    current = destination.parent
    while not current.exists():
        missing.append(current)
        current = current.parent
    temporary = None
    committed = False
    try:
        for directory in reversed(missing):
            try:
                directory.mkdir()
            except FileExistsError:
                require(
                    directory.is_dir() and not directory.is_symlink(),
                    "lock ancestor changed during capture",
                )
            else:
                created.append(directory)
        layout.canonical(destination)
        with tempfile.NamedTemporaryFile(
            prefix=".swiftpm-purifier-lock-", dir=destination.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(
            destination.stat().st_mode & 0o777 if destination.exists() else 0o644
        )
        os.replace(temporary, destination)
        temporary = None
        committed = True
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if not committed:
            for directory in reversed(created):
                try:
                    directory.rmdir()
                except OSError:
                    break


def metadata_name(name, revision):
    raw = json.dumps(
        [name, revision], ensure_ascii=False, separators=(",", ":")
    ).encode()
    return "preworkspace-" + hashlib.sha256(raw).hexdigest()[:24]


def hash_path(session, api, label, path):
    value = (
        session.run(label, [api.tool("nix"), "hash", "path", "--type", "sha256", path])
        .stdout.decode()
        .strip()
    )
    require(digest(value), "native source NAR hash is not canonical SHA-256")
    return value


def writable_copy(source, destination):
    """Materialize real source/metadata with its new owner; change no bytes."""
    import shutil

    shutil.copytree(source, destination, symlinks=True)
    for directory, names, files in os.walk(destination):
        Path(directory).chmod(Path(directory).stat().st_mode | 0o700)
        for name in names + files:
            path = Path(directory) / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | 0o200)


def git_command(api, directory, *arguments, immutable=False):
    command = [api.tool("git")]
    if immutable:
        # Trust only this concrete immutable fetched repository for a read;
        # never change the developer's Git configuration or native runtime.
        command += ["-c", "safe.directory=" + str(directory)]
    return [*command, "-C", directory, *arguments]


def verify_git(session, api, label, directory, revision, immutable=False):
    directory = Path(directory)
    require(
        (directory / ".git").is_dir() and not (directory / ".git").is_symlink(),
        "actual Git metadata directory is required",
    )
    head = (
        session.run(
            label + "-HEAD",
            git_command(
                api,
                directory,
                "rev-parse",
                "--verify",
                "HEAD^{commit}",
                immutable=immutable,
            ),
        )
        .stdout.decode()
        .strip()
    )
    require(head == revision, "actual Git HEAD disagrees with the captured checkout")
    session.run(
        label + "-objects",
        git_command(api, directory, "fsck", "--full", immutable=immutable),
    )
    common = (
        session.run(
            label + "-git-directory",
            git_command(
                api, directory, "rev-parse", "--absolute-git-dir", immutable=immutable
            ),
        )
        .stdout.decode()
        .strip()
    )
    require(
        Path(common).resolve().is_relative_to(directory.resolve()),
        "Git object metadata escapes its checkout",
    )


def plain_copy(source, destination):
    import shutil

    shutil.copytree(
        source, destination, ignore=shutil.ignore_patterns(".git"), symlinks=True
    )


def confine_cached_git(session, api, package):
    """Reject metadata pointers outside the owned snapshot before native update."""
    cache = Path(package) / "Packages"
    if not cache.exists():
        return
    for index, checkout in enumerate(
        sorted(cache.iterdir(), key=lambda item: item.name)
    ):
        dotgit = checkout / ".git"
        if not os.path.lexists(dotgit):
            continue
        require(
            checkout.is_dir()
            and not checkout.is_symlink()
            and dotgit.is_dir()
            and not dotgit.is_symlink(),
            "unsupported native cached Git repository layout",
        )
        for nested, metadata in enumerate(
            [dotgit, *[item for item in checkout.rglob(".git") if item != dotgit]]
        ):
            require(
                not metadata.is_symlink(), "cached Git metadata must not be symlinked"
            )
            repository = metadata.parent
            label = "cached-" + str(index) + "-" + str(nested)
            actual = (
                session.run(
                    label + "-metadata",
                    git_command(api, repository, "rev-parse", "--absolute-git-dir"),
                )
                .stdout.decode()
                .strip()
            )
            common = (
                session.run(
                    label + "-common",
                    git_command(api, repository, "rev-parse", "--git-common-dir"),
                )
                .stdout.decode()
                .strip()
            )
            common_path = (
                Path(common) if Path(common).is_absolute() else repository / common
            )
            require(
                Path(actual).resolve().is_relative_to(checkout.resolve())
                and common_path.resolve().is_relative_to(checkout.resolve()),
                "cached Git metadata points outside the owned snapshot",
            )
            worktree = (
                session.run(
                    label + "-worktree",
                    git_command(api, repository, "rev-parse", "--show-toplevel"),
                )
                .stdout.decode()
                .strip()
            )
            require(
                Path(worktree).resolve() == repository.resolve(),
                "cached Git worktree points outside the owned snapshot",
            )
            require(
                not any(item.is_symlink() for item in Path(actual).rglob("*")),
                "cached Git metadata contains symlinks",
            )


def capture(root, temporary, api, update=None):
    """Capture native state without touching caller source/native inputs."""
    import legacy_manifest
    import shutil
    from native_sdk import store_root

    root = Path(root)
    require(not api.sdk_mode(), "portable format 7 has no SDK-bound capture contract")
    layout.canonical(getattr(api, "source_reference", lambda value: value)(root))
    reference = api.purifier_lock_reference(root)
    destination = layout.lock_destination(root, reference)
    native_absence(root, api.TOOL_LOCK_OVERRIDE)
    initial_layout = layout.source_layout(root, reference)["hash"]
    initial_manifest = manifest_hash(root)
    original_lock = destination.read_bytes() if destination.exists() else None
    require(legacy.safe_name(root.name), "unsafe native root directory")
    parent = Path(temporary) / "preworkspace"
    parent.mkdir()
    package = parent / root.name
    copy_source(root, package)
    # Copied immutable cache entries must be writable for genuine native
    # fetch/update. NAR identity ignores these ownership/write permissions.
    if (package / "Packages").exists():
        for directory, names, files in os.walk(package / "Packages"):
            Path(directory).chmod(Path(directory).stat().st_mode | 0o700)
            for name in names + files:
                path = Path(directory) / name
                if not path.is_symlink():
                    path.chmod(path.stat().st_mode | 0o200)
    environment = dict(
        os.environ, CLANG_MODULE_CACHE_PATH=str(Path(temporary) / "clang-module-cache")
    )
    session = NativeSession(package, environment)
    package_command = api.native_command("swift-package")
    confine_cached_git(session, api, package)
    action = ["fetch"] if update is None else ["update", *update]
    session.run("native-resolution", [*package_command, *action])
    native_absence(package)
    validated = session.run(
        "native-validation", [*package_command, "--verbose", "dump-package"]
    )
    compiler, description = legacy_manifest.invocation(validated.stdout, package)
    raw_manifest = session.run(
        "native-manifest-protocol", compiler, protocol_flag="-fileno"
    )
    require(not raw_manifest.stdout, "native manifest emitted unexpected stdout")
    explicit = legacy_manifest.protocol(raw_manifest.protocol, description)
    graph_result = session.run(
        "native-graph", [*package_command, "show-dependencies", "--format", "json"]
    )
    graph = legacy_manifest.json_value(graph_result.stdout)
    dependencies = graph_dependencies(graph, package, description["name"])
    (session.evidence / "native-graph.json").write_bytes(graph_result.stdout)
    pins = {}
    for index, name in enumerate(sorted(dependencies)):
        node = dependencies[name]
        checkout = Path(node["path"])
        label = "dependency-" + str(index)
        revision = (
            session.run(
                label + "-native-revision",
                git_command(api, checkout, "rev-parse", "--verify", "HEAD^{commit}"),
            )
            .stdout.decode()
            .strip()
        )
        require(
            legacy.REVISION.fullmatch(revision) is not None,
            "invalid actual checkout revision",
        )
        verify_git(session, api, label + "-native", checkout, revision)
        origin = (
            session.run(
                label + "-native-origin",
                git_command(api, checkout, "config", "--get", "remote.origin.url"),
            )
            .stdout.decode()
            .strip()
        )
        require(
            origin == node["url"], "native graph URL disagrees with actual Git origin"
        )
        fetched = session.run(
            label + "-prefetch-real-git",
            [
                api.tool("nix-prefetch-git"),
                "--quiet",
                "--fetch-submodules",
                "--leave-dotGit",
                "--name",
                metadata_name(name, revision),
                "--url",
                node["url"],
                "--rev",
                revision,
            ],
        )
        metadata = legacy_manifest.json_value(fetched.stdout)
        require(
            isinstance(metadata, dict)
            and metadata.get("rev") == revision
            and digest(metadata.get("hash"))
            and isinstance(metadata.get("path"), str),
            "invalid real Git prefetch observation",
        )
        fetched_path = Path(metadata["path"])
        require(
            store_root(str(fetched_path)) == str(fetched_path)
            and fetched_path.is_dir()
            and not fetched_path.is_symlink(),
            "prefetched real Git checkout must be an immutable store directory",
        )
        require(
            hash_path(session, api, label + "-metadata-NAR", fetched_path)
            == metadata["hash"],
            "real Git NAR bytes disagree with prefetch observation",
        )
        verify_git(
            session, api, label + "-fetched", fetched_path, revision, immutable=True
        )
        plain = Path(temporary) / (label + "-plain")
        actual = Path(temporary) / (label + "-actual")
        plain_copy(fetched_path, plain)
        plain_copy(checkout, actual)
        source_hash = hash_path(session, api, label + "-source-NAR", plain)
        require(
            hash_path(session, api, label + "-actual-NAR", actual) == source_hash,
            "native checkout contains source changes or extra files absent from its real Git revision",
        )
        pins[name] = {
            "name": name,
            "sourceLocation": node["url"],
            "revision": revision,
            "version": node["version"],
            "hash": source_hash,
            "gitHash": metadata["hash"],
        }
    session.run("native-build-plan", [*api.native_command("swift-build")], timeout=600)
    native_absence(package)
    plan_file = package / ".build" / "debug.yaml"
    require(
        plan_file.is_file() and not plan_file.is_symlink(),
        "native build did not produce a regular authoritative plan",
    )
    with plan_file.open("rb") as stream:
        raw_plan = stream.read(4 * 1024 * 1024 + 1)
    parsed = plan.plan(raw_plan)
    build_compiler = api.native_command("swiftc")
    require(len(build_compiler) == 1, "unsupported selected compiler command prefix")
    products = plan.products(parsed, package, explicit, build_compiler[0])
    (session.evidence / "native-build-plan.yaml").write_bytes(raw_plan)
    after_graph = legacy_manifest.json_value(
        session.run(
            "native-graph-after-build",
            [*package_command, "show-dependencies", "--format", "json"],
        ).stdout
    )
    require(
        graph_dependencies(after_graph, package, description["name"]) == dependencies,
        "native dependency graph changed while capturing the build",
    )
    require(
        manifest_hash(package) == initial_manifest,
        "native capture modified its manifest inputs",
    )
    # The actual caller remains the source of the root definition. Only file
    # layout, not ordinary source contents, invalidates its product projection.
    native_absence(root, api.TOOL_LOCK_OVERRIDE)
    require(
        manifest_hash(root) == initial_manifest
        and layout.source_layout(root, reference)["hash"] == initial_layout,
        "source definition changed during capture",
    )
    consumed = [root / "Package.swift"]
    for row in parsed["commands"].values():
        if row.get("tool") == "swift-compiler":
            for path in row["sources"]:
                relative = Path(path).relative_to(package)
                if relative.parts[0] != "Packages":
                    consumed.append(root / relative)
    for path in consumed:
        require(
            not destination.exists()
            or not path.exists()
            or not os.path.samefile(destination, path),
            "purifier lock destination intersects a native source or manifest",
        )
    lock = {
        "version": FORMAT,
        "nativeProtocol": "pre-workspace-git",
        "package": description["name"],
        "rootDirectory": root.name,
        "manifestHash": initial_manifest,
        "sourceLayoutHash": initial_layout,
        "pins": pins,
        "products": sorted(products, key=lambda value: value["name"]),
        "localDependencies": [],
        "binaryTargets": [],
        "nativeLockFiles": {"Package.pins": None, "Package.resolved": None},
    }
    lock["contentHash"] = content_hash(lock)
    raw_lock = (
        json.dumps(lock, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode()
    validate_raw(raw_lock, root, api.TOOL_LOCK_OVERRIDE, reference)
    require(
        (destination.read_bytes() if destination.exists() else None) == original_lock,
        "purifier lock changed during capture",
    )
    # Observation is private and occurs only after all native/hash guards.
    getattr(api, "observe_pre_workspace_runtime", api.observe_host_runtime)()
    validate_raw(raw_lock, root, api.TOOL_LOCK_OVERRIDE, reference)
    if original_lock != raw_lock:
        atomic_lock(destination, raw_lock)
    session.receipt.update(
        complete=True,
        lock=lock,
        manifestCompiler=compiler[0],
        buildCompiler=build_compiler[0],
    )
    session.save()
    print(
        "swiftpm-purifier: wrote portable pre-workspace format 7 with "
        + str(len(pins))
        + " actual Git checkouts",
        file=__import__("sys").stderr,
    )
    return lock


def validate_runtime(root, lock, api):
    """Validate the actual native protocol and manifest in an owned copy."""
    import tempfile
    import legacy_manifest

    with tempfile.TemporaryDirectory(
        prefix="swiftpm-purifier-preworkspace-runtime-"
    ) as temporary:
        work = Path(temporary)
        probe = work / "probe"
        probe.mkdir()
        require(
            candidate(api, probe, os.environ),
            "selected runtime does not implement the captured pre-workspace protocol",
        )
        package = work / Path(root).name
        copy_source(root, package)
        session = NativeSession(
            package,
            dict(os.environ, CLANG_MODULE_CACHE_PATH=str(work / "clang-module-cache")),
        )
        result = session.run(
            "native-validation",
            [*api.native_command("swift-package"), "--verbose", "dump-package"],
        )
        compiler, description = legacy_manifest.invocation(result.stdout, package)
        result = session.run(
            "native-manifest-protocol", compiler, protocol_flag="-fileno"
        )
        require(not result.stdout, "native manifest emitted unexpected stdout")
        legacy_manifest.protocol(result.protocol, description)
        require(
            description.get("name") == lock["package"],
            "selected runtime disagrees with captured package name",
        )
        session.receipt.update(
            complete=True,
            scope="actual pre-workspace protocol and manifest validation only",
        )
        session.save()
    return getattr(api, "observe_pre_workspace_runtime", api.observe_host_runtime)()
