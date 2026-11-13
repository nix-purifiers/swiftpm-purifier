# SPDX-License-Identifier: MIT
"""swiftpm-purifier: Nix as the dependency backend of SwiftPM packages.

`swiftpm-purifier lock` resolves a copy with the selected SwiftPM and
records every dependency SwiftPM fetched in
swiftpm-purifier.lock.json, with its content hash. Nix then fetches the
locked dependencies and builds the package in its sandbox
(`buildSwiftPMProject`), and `swiftpm-purifier swift` runs SwiftPM on
them offline.
"""

import argparse
import base64
import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

import bounded_native
import native_sdk
import host_runtime
import legacy_manifest
import legacy_runtime
import legacy_pin_bridge
import legacy_state
import pre_workspace
import pre_workspace_install
import dependencies_v1
import dependencies_v1_cache
import dependencies_v1_capture
import dependencies_v1_host
import dependencies_v1_lifecycle
import dependencies_v1_ownership
import dependencies_v1_selectors
import dependencies_v3
import dependencies_v3_lifecycle
import dependencies_v3_ownership
import workspace_v4_path
import workspace_v4_path_lifecycle
import workspace_v4_path_ownership
import workspace_v5_location
import workspace_v5_location_lifecycle
import workspace_v5_location_ownership

LOCK_NAME = "swiftpm-purifier.lock.json"
LOCK_VERSION = 5
LEGACY_LOCK_VERSION = 4
# The lockfile `--lockfile` names, or for the `swift` verb
# SWIFTPM_PURIFIER_LOCKFILE; without either, the lockfile in the package.
LOCK_OVERRIDE = os.environ.get("SWIFTPM_PURIFIER_LOCKFILE")
RAW_LOCK_OVERRIDE = (
    None
    if LOCK_OVERRIDE is None
    else (
        LOCK_OVERRIDE
        if os.path.isabs(LOCK_OVERRIDE)
        else os.path.join(os.getcwd(), LOCK_OVERRIDE)
    )
)
RAW_SOURCE_REFERENCE = None


def lock_path(root: Path) -> Path:
    return Path(LOCK_OVERRIDE).resolve() if LOCK_OVERRIDE else root / LOCK_NAME


def purifier_lock_reference(root):
    if RAW_LOCK_OVERRIDE is None:
        return root / LOCK_NAME
    return RAW_LOCK_OVERRIDE


def source_reference(root):
    return RAW_SOURCE_REFERENCE if RAW_SOURCE_REFERENCE is not None else root


def validate_pre_workspace(root, raw):
    try:
        pre_workspace.layout.canonical(source_reference(root))
        return pre_workspace.validate_raw(
            raw, root, TOOL_LOCK_OVERRIDE, purifier_lock_reference(root)
        )
    except (pre_workspace.StateError, pre_workspace.layout.LayoutError) as error:
        raise PurifierError(str(error) + "; run `swiftpm-purifier relock`") from error


# The copy of Package.resolved that `--tool-lockfile` or
# SWIFTPM_PURIFIER_TOOL_LOCKFILE names. `lock` and `update` write it there
# and leave the copy in the package as it is; without either, the copy in
# the package.
TOOL_LOCK_OVERRIDE = os.environ.get("SWIFTPM_PURIFIER_TOOL_LOCKFILE")
RAW_TOOL_LOCK_OVERRIDE = (
    None
    if TOOL_LOCK_OVERRIDE is None
    else (
        TOOL_LOCK_OVERRIDE
        if os.path.isabs(TOOL_LOCK_OVERRIDE)
        else os.path.join(os.getcwd(), TOOL_LOCK_OVERRIDE)
    )
)


def dependencies_v1_selections(root):
    dependencies_v1_host.layout.canonical(source_reference(root))
    native = (
        dependencies_v1_selectors.DEFAULT
        if TOOL_LOCK_OVERRIDE is None
        else (
            RAW_TOOL_LOCK_OVERRIDE
            if RAW_TOOL_LOCK_OVERRIDE is not None
            else (
                TOOL_LOCK_OVERRIDE
                if os.path.isabs(TOOL_LOCK_OVERRIDE)
                else os.path.join(os.getcwd(), TOOL_LOCK_OVERRIDE)
            )
        )
    )
    return native, purifier_lock_reference(root)


def recover_dependencies_v1(root):
    if legacy_pin_bridge.recover_if_needed(sys.modules[__name__], root):
        return
    if not dependencies_v1_lifecycle.recovery_hint(root):
        return
    state = dependencies_v1_lifecycle.registry(root, create=False)
    if dependencies_v1_lifecycle.recovery_needed(root, state):
        if sdk_mode():
            raise PurifierError("format8 has no SDK-bound CLI lifecycle")
        native, portable = dependencies_v1_selections(root)
        dependencies_v1_lifecycle.recover(root, state, native, portable)


def validate_dependencies_v1(root, raw):
    if sdk_mode():
        raise PurifierError("format8 has no SDK-bound CLI runtime contract")
    try:
        native, portable = dependencies_v1_selections(root)
        return dependencies_v1_host.validate_source(root, raw, native, portable)
    except (ValueError, OSError) as error:
        raise PurifierError(str(error)) from error


def dependencies_v1_hash(path):
    result = subprocess.run(
        [tool("nix"), "hash", "path", str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    value = result.stdout.strip()
    dependencies_v1_cache.require(
        dependencies_v1.portable.digest(value), "native source NAR hash output"
    )
    return value


def dependencies_v1_sources(root, lock):
    tree = Path(
        nix_build(
            f"""(pkgs.swiftpm-purifier.projectData {{
          src = /. + builtins.getEnv "SWIFTPM_PURIFIER_ROOT";
          platform = builtins.currentSystem;
          {nix_lock_arguments(root)}
        }}).tree""",
            nix_lock_environment(root),
        )
    )
    packages = tree / "Packages"
    dependencies_v1_cache.directory(packages)
    dependencies_v1_cache.require(
        {p.name for p in packages.iterdir()} == set(lock["pins"]),
        "realized format8 dependency set",
    )
    result = {}
    for name in lock["pins"]:
        path = packages / name
        dependencies_v1_cache.require(path.is_symlink(), "realized source link")
        result[name] = path.resolve(strict=True)
    return result


def relock_dependencies_v1(root, update=None):
    if sdk_mode():
        raise PurifierError("format8 has no SDK-bound CLI capture contract")
    native, portable = dependencies_v1_selections(root)
    state = dependencies_v1_lifecycle.registry(root)
    with tempfile.TemporaryDirectory(prefix="host-capture-", dir=state) as temporary:
        lock, session = dependencies_v1_capture.capture_and_publish(
            root,
            Path(temporary),
            sys.modules[__name__],
            state,
            update,
            native,
            portable,
        )
    print(
        f"swiftpm-purifier: wrote {lock_path(root)} with {len(lock['pins'])} pins",
        file=sys.stderr,
    )
    return lock


def path_lifecycle(version):
    if version == dependencies_v3.FORMAT:
        return dependencies_v3_lifecycle
    if version == workspace_v4_path.FORMAT:
        return workspace_v4_path_lifecycle
    raise PurifierError("unknown native path protocol")


def state_lifecycle(version):
    if version == workspace_v5_location.FORMAT:
        return workspace_v5_location_lifecycle
    return path_lifecycle(version)


def validate_dependencies_v3(root, raw, version=dependencies_v3.FORMAT):
    if sdk_mode():
        raise PurifierError(
            f"portable format {version} has no SDK-bound CLI runtime contract"
        )
    try:
        native, portable = dependencies_v1_selections(root)
        return state_lifecycle(version).validate_source(root, raw, native, portable)
    except (ValueError, OSError) as error:
        raise PurifierError(str(error)) from error


def dependencies_v3_sources(root, lock):
    tree = Path(
        nix_build(
            f"""(pkgs.swiftpm-purifier.projectData {{
          src = /. + builtins.getEnv "SWIFTPM_PURIFIER_ROOT";
          platform = builtins.currentSystem;
          {nix_lock_arguments(root)}
        }}).tree""",
            nix_lock_environment(root),
        )
    )
    packages = tree / "Packages"
    dependencies_v1_cache.directory(packages)
    dependencies_v1_cache.require(
        {p.name for p in packages.iterdir()}
        == (
            {key for key in lock["pins"]}
            if lock["version"] == workspace_v5_location.FORMAT
            else {pin["name"] for pin in lock["pins"].values()}
        ),
        "realized native path dependency set",
    )
    result = {}
    for identity, pin in lock["pins"].items():
        path = packages / (
            identity if lock["version"] == workspace_v5_location.FORMAT else pin["name"]
        )
        dependencies_v1_cache.require(path.is_symlink(), "realized source link")
        result[identity] = path.resolve(strict=True)
    return result


def relock_dependencies_v3(root, update=None, version=dependencies_v3.FORMAT):
    if sdk_mode():
        raise PurifierError(
            f"portable format {version} has no SDK-bound CLI capture contract"
        )
    native, portable = dependencies_v1_selections(root)
    state = dependencies_v1_lifecycle.registry(root)
    with tempfile.TemporaryDirectory(prefix="native3-capture-", dir=state) as temporary:
        lock, session = state_lifecycle(version).capture_and_publish(
            root,
            Path(temporary),
            sys.modules[__name__],
            state,
            update,
            native,
            portable,
        )
    print(
        f"swiftpm-purifier: wrote {lock_path(root)} with {len(lock['pins'])} pins",
        file=sys.stderr,
    )
    return lock


def resolved_path(root: Path) -> Path:
    if TOOL_LOCK_OVERRIDE:
        return Path(TOOL_LOCK_OVERRIDE).resolve()
    return root / "Package.resolved"


def legacy_native_path(root: Path) -> Path:
    logical = root / "Package.pins"
    if logical.is_symlink() or (logical.exists() and not logical.is_file()):
        raise PurifierError("logical Package.pins must be a regular file or absent")
    if TOOL_LOCK_OVERRIDE:
        return resolved_path(root)
    if logical.exists() and (root / "Package.resolved").exists():
        raise PurifierError(
            "both native pin formats are present; select --tool-lockfile explicitly"
        )
    return logical


def nix_lock_arguments(root: Path) -> str:
    """The arguments of buildSwiftPMProject that name the lockfiles, for an
    expression that reads SWIFTPM_PURIFIER_LOCK and SWIFTPM_PURIFIER_RESOLVED
    with builtins.getEnv."""
    arguments = 'lockFile = /. + builtins.getEnv "SWIFTPM_PURIFIER_LOCK";'
    if TOOL_LOCK_OVERRIDE:
        arguments += (
            ' packageResolved = /. + builtins.getEnv "SWIFTPM_PURIFIER_RESOLVED";'
        )
    return arguments


def nix_lock_environment(root: Path) -> dict:
    environment = {
        "SWIFTPM_PURIFIER_ROOT": str(root),
        "SWIFTPM_PURIFIER_LOCK": str(lock_path(root)),
    }
    if TOOL_LOCK_OVERRIDE:
        environment["SWIFTPM_PURIFIER_RESOLVED"] = str(resolved_path(root))
    return environment


def content_hash(lock: dict) -> str:
    """The hash of a lockfile without its contentHash, of the JSON that
    builtins.toJSON writes for it: keys in order and no spaces."""
    content = {key: value for key, value in lock.items() if key != "contentHash"}
    text = json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return sri_sha256(hashlib.sha256(text.encode()).digest())


ROOT_PLACEHOLDER = "@root@"
MANIFEST_PATTERN = re.compile(r"Package(@swift-[0-9.]+)?\.swift")
# What the interactive mode keeps under .build: the Nix GC root of the
# realized tree, and a record of the links it made.
STATE_DIR = Path(".build") / "swiftpm-purifier"
# The scratch directories whose workspace state the interactive mode
# writes: the scratch directory of SwiftPM, and the one where sourcekit-lsp
# builds its background index, which keeps a workspace state of its own.
SCRATCH_DIRECTORIES = (Path(".build"), Path(".build") / "index-build")


class PurifierError(Exception):
    pass


def sri_sha256(digest: bytes) -> str:
    return "sha256-" + base64.b64encode(digest).decode()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_hash(root: Path) -> str:
    """The hash of the package's manifests, as build-swiftpm-project.nix
    computes it: the SHA-256 of the JSON list of [name, hex SHA-256] of
    each manifest, in name order, written without spaces."""
    manifests = sorted(
        p.name for p in root.iterdir() if MANIFEST_PATTERN.fullmatch(p.name)
    )
    listing = [[name, file_sha256(root / name)] for name in manifests]
    return hashlib.sha256(
        json.dumps(listing, separators=(",", ":")).encode()
    ).hexdigest()


def resolved_pins(resolved_file: Path, legacy_identities=None) -> list:
    """The pins of a Package.resolved, which SwiftPM does not write for a
    package without remote dependencies."""
    resolved = read_json(resolved_file)
    if resolved is None:
        return []
    if not isinstance(resolved, dict) or type(resolved.get("version")) is not int:
        raise PurifierError(f"{resolved_file}: invalid native lock format")
    if resolved.get("version") == 1:
        body = resolved.get("object")
        raw = body.get("pins") if isinstance(body, dict) else None
        if not isinstance(raw, list):
            raise PurifierError(f"{resolved_file}: invalid version-1 pins")
        identities = legacy_identities or {}
        pins = []
        seen = set()
        for pin in raw:
            if not isinstance(pin, dict) or set(pin) != {
                "package",
                "repositoryURL",
                "state",
            }:
                raise PurifierError(f"{resolved_file}: invalid version-1 pin")
            name, location, state = pin["package"], pin["repositoryURL"], pin["state"]
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(location, str)
                or not location
            ):
                raise PurifierError(
                    f"{resolved_file}: invalid version-1 package or location"
                )
            if (
                not isinstance(state, dict)
                or set(state) - {"branch", "revision", "version"}
                or not isinstance(state.get("revision"), str)
                or not re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", state["revision"])
                or any(
                    state.get(key) is not None
                    and (not isinstance(state[key], str) or not state[key])
                    for key in ("branch", "version")
                )
                or (
                    state.get("branch") is not None and state.get("version") is not None
                )
            ):
                raise PurifierError(
                    f"{resolved_file}: invalid version-1 checkout state"
                )
            identity = identities.get(location)
            if not isinstance(identity, str) or not identity or identity in seen:
                raise PurifierError(
                    f"{resolved_file}: missing or ambiguous native identity for {location!r}"
                )
            seen.add(identity)
            canonical_state = {
                "branch": state.get("branch"),
                "revision": state["revision"],
                "version": state.get("version"),
            }
            pins.append(
                {
                    "identity": identity,
                    "kind": "remote",
                    "location": location,
                    "state": canonical_state,
                    "legacyPackage": name,
                }
            )
        return pins
    if resolved.get("version") not in (2, 3):
        raise PurifierError(
            f"{resolved_file} has format version {resolved.get('version')}, "
            "which swiftpm-purifier does not support"
        )
    return resolved["pins"]


def pinned_at(pin: dict) -> str:
    """The revision, or for a registry pin the version, a pin names."""
    return pin["state"].get("revision") or pin["state"]["version"]


def is_current(root: Path, lock: dict) -> bool:
    """Whether the lockfile covers the pins of Package.resolved, as
    lock.nix checks, and the manifests the lock run saw."""
    refuse_mixed_dependency_state3(lock)
    if lock.get("version") in (
        dependencies_v3.FORMAT,
        workspace_v4_path.FORMAT,
        workspace_v5_location.FORMAT,
    ):
        validate_dependencies_v3(root, lock_path(root).read_bytes(), lock["version"])
        return True
    if lock.get("version") == 8:
        validate_dependencies_v1(root, lock_path(root).read_bytes())
        return True
    if lock.get("version") == pre_workspace.FORMAT:
        validate_pre_workspace(root, json.dumps(lock, ensure_ascii=False).encode())
        return True
    if lock.get("version") == legacy_state.FORMAT:
        try:
            legacy_state.validate(lock, legacy_native_path(root))
        except legacy_state.StateError as error:
            raise PurifierError(
                str(error) + "; run `swiftpm-purifier relock`"
            ) from error
        return lock.get("manifestHash") == manifest_hash(root)
    if lock.get("workspaceStateVersion") == 1:
        raise PurifierError(
            "native workspace format 1 requires portable lock format 6; run `swiftpm-purifier relock`"
        )
    if lock.get("workspaceStateVersion") == 2 and not legacy_state_lock_valid(
        root, lock
    ):
        raise PurifierError(
            "unsupported or unsafe dependency-state format-2 lock; run `swiftpm-purifier relock`"
        )
    locked = lock.get("pins", {})
    identities = {}
    for identity, value in locked.items():
        if "sourceLocation" in value:
            location = value["sourceLocation"]
            if location in identities:
                raise PurifierError(
                    "multiple locked native identities name the same source location"
                )
            identities[location] = identity
    pins = resolved_pins(resolved_path(root), identities)
    return (
        sorted(pin["identity"] for pin in pins) == sorted(locked)
        and all(
            (
                locked[pin["identity"]].get("revision")
                or locked[pin["identity"]].get("version")
            )
            == pinned_at(pin)
            for pin in pins
        )
        and lock.get("manifestHash") == manifest_hash(root)
        and all(
            "legacyPackage" not in pin
            or locked[pin["identity"]].get("name") == pin["legacyPackage"]
            for pin in pins
        )
    )


def legacy_local_path_valid(root: Path, portable: str) -> bool:
    if not isinstance(portable, str) or not portable.startswith("@root@/"):
        return False
    parts = portable[len("@root@/") :].split("/")
    current = root
    valid = not any(part in ("", ".", "..") for part in parts)
    for part in parts:
        current = current / part
        valid = valid and not current.is_symlink() and current.is_dir()
    manifest = current / "Package.swift"
    if not valid or manifest.is_symlink() or not manifest.is_file():
        return False
    try:
        with manifest.open("rb") as stream:
            stream.read(1)
    except OSError:
        return False
    return True


_RESOLVED_DEFAULT = object()


def legacy_state_lock_valid(
    root: Path, lock: dict, resolved_override=_RESOLVED_DEFAULT
) -> bool:
    try:
        resolved = (
            read_json(resolved_path(root))
            if resolved_override is _RESOLVED_DEFAULT
            else resolved_override
        )
    except (json.JSONDecodeError, OSError):
        return False
    pins = lock.get("pins")
    local = lock.get("localDependencies")
    if (
        (
            resolved is not None
            and (not isinstance(resolved, dict) or resolved.get("version") != 1)
        )
        or not isinstance(pins, dict)
        or not isinstance(local, list)
        or lock.get("binaryTargets") != []
    ):
        return False
    names = []
    identities = list(pins)
    for pin in pins.values():
        name = pin.get("name") if isinstance(pin, dict) else None
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", name) is None
        ):
            return False
        names.append(name)
    for dependency in local:
        if not isinstance(dependency, dict) or set(dependency) != {
            "basedOn",
            "packageRef",
            "state",
            "subpath",
        }:
            return False
        ref = dependency["packageRef"]
        if (
            not isinstance(ref, dict)
            or set(ref) != {"identity", "isLocal", "name", "path"}
            or ref["isLocal"] is not True
            or dependency["basedOn"] is not None
            or dependency["state"] != {"name": "local"}
            or not isinstance(ref["identity"], str)
            or not ref["identity"]
            or not isinstance(ref["name"], str)
            or not ref["name"]
            or dependency["subpath"] != ref["identity"]
            or not isinstance(ref["path"], str)
            or not ref["path"].startswith("@root@/")
        ):
            return False
        if not legacy_local_path_valid(root, ref["path"]):
            return False
        names.append(ref["name"])
        identities.append(ref["identity"])
    return len(set(names)) == len(names) and len(set(identities)) == len(identities)


def find_root(start: Path) -> Path:
    for directory in [start, *start.parents]:
        if (directory / "Package.swift").exists():
            return directory
    raise PurifierError(f"no Package.swift in {start} or above it")


def sdk_mode():
    return os.environ.get("SWIFTPM_PURIFIER_RUNTIME_MODE", "local") == "sdk"


def selected_host_runtime():
    try:
        return host_runtime.discover(
            os.environ.get("SWIFTPM_PURIFIER_HOST_PATH", os.environ.get("PATH", "")),
            os.environ,
        )
    except host_runtime.HostRuntimeError as error:
        raise PurifierError(str(error)) from error


def observe_host_runtime(*, pre_workspace_protocol=False):
    work = Path(tempfile.mkdtemp(prefix="swiftpm-purifier-host-"))
    try:
        observation = host_runtime.observe(
            selected_host_runtime(),
            os.environ,
            work / "observation",
            None if pre_workspace_protocol else native_options,
        )
    except Exception as error:
        raise PurifierError(
            f"host runtime probe failed; evidence at {work}: {error}"
        ) from error
    shutil.rmtree(work)
    return observation


def observe_pre_workspace_runtime():
    # This independently exercised protocol has no modern option-parser
    # attestation. Observe the actual selected tools and version outputs.
    return observe_host_runtime(pre_workspace_protocol=True)


def require_runtime(root, lock):
    if lock["version"] in (
        dependencies_v3.FORMAT,
        workspace_v4_path.FORMAT,
        workspace_v5_location.FORMAT,
    ):
        validate_dependencies_v3(root, lock_path(root).read_bytes(), lock["version"])
        native, portable = dependencies_v1_selections(root)
        state = dependencies_v1_lifecycle.registry(root)
        sources = dependencies_v3_sources(root, lock)
        with tempfile.TemporaryDirectory(
            prefix="native3-runtime-", dir=state
        ) as temporary:
            return state_lifecycle(lock["version"]).runtime(
                root,
                lock,
                dependencies_v1_selectors.select(root, native).raw,
                sources,
                Path(temporary),
                sys.modules[__name__],
                dependencies_v1_hash,
                state,
            )
    if lock["version"] == 8:
        validate_dependencies_v1(root, lock_path(root).read_bytes())
        native, portable = dependencies_v1_selections(root)
        state = dependencies_v1_lifecycle.registry(root)
        sources = dependencies_v1_sources(root, lock)
        with tempfile.TemporaryDirectory(
            prefix="host-runtime-", dir=state
        ) as temporary:
            return dependencies_v1_host.runtime(
                root,
                lock_path(root).read_bytes(),
                sources,
                Path(temporary),
                sys.modules[__name__],
                dependencies_v1_hash,
                native,
                portable,
                evidence_parent=state,
            )
    if lock["version"] == pre_workspace.FORMAT:
        if sdk_mode():
            raise PurifierError(
                "portable format 7 has no SDK-bound local runtime contract"
            )
        validate_pre_workspace(root, lock_path(root).read_bytes())
        try:
            return pre_workspace.validate_runtime(root, lock, sys.modules[__name__])
        except (pre_workspace.StateError, legacy_manifest.ManifestError) as error:
            raise PurifierError(str(error)) from error
    if sdk_mode():
        require_native_sdk(lock)
        return None
    if lock["version"] == LOCK_VERSION:
        raise PurifierError(
            "this format 5 lock is bound to an immutable SDK; use explicit SDK mode, or deliberately run local `relock` to capture a portable format 4 lock"
        )
    if lock["version"] == legacy_state.FORMAT:
        try:
            legacy_state.validate(lock, legacy_native_path(root))
        except legacy_state.StateError as error:
            raise PurifierError(str(error)) from error
        observation = observe_host_runtime()
        description = legacy_manifest.json_value(
            output(
                [*native_command("swift-package"), "dump-package"],
                cwd=root,
                env=os.environ,
            )
        )
        if (
            not isinstance(description, dict)
            or description.get("name") != lock["package"]
        ):
            raise PurifierError(
                "selected native runtime disagrees with the legacy package manifest"
            )
        return observation
    observation = observe_host_runtime()
    # The real host compiler/SwiftPM validate the project's manifest API.
    # Observations never impose a version, path, or binary-hash lock-in.
    if lock.get("version") == 4 and lock.get("workspaceStateVersion") == 2:
        legacy_runtime.validate(sys.modules[__name__], root, lock)
    else:
        products(root, os.environ, [])
    return observation


def tool(name: str) -> str:
    """Native local commands come only from the original host selection."""
    roles = {value: role for role, value in host_runtime.NAMES.items()}
    if not sdk_mode() and name in roles:
        role = roles[name]
        command = selected_host_runtime()["commands"].get(role)
        if command is None:
            raise PurifierError("the selected host runtime has no " + name)
        return command
    return os.environ.get(f"SWIFTPM_PURIFIER_{name.upper().replace('-', '_')}") or name


def native_command(name):
    if not sdk_mode() and name in (
        "swift-package",
        "swift-build",
        "swift-test",
        "swift-run",
    ):
        return [tool("swift"), name.removeprefix("swift-")]
    return [tool(name)]


def run(command, **kwargs):
    return subprocess.run([str(c) for c in command], check=True, **kwargs)


def output(command, **kwargs) -> str:
    return run(command, stdout=subprocess.PIPE, text=True, **kwargs).stdout


# The lock run.


@functools.lru_cache(maxsize=16)
def native_options(command, subcommand="resolve"):
    """Recognize options with the actual selected parser, without a package.

    Version/help actions can ignore unknown options. A successful missing-
    manifest control and a failing unknown-option control prevent that false
    positive. No real package or dependency resolution is used for probes.
    """
    prefix = [command] if isinstance(command, str) else list(command)
    temporary = Path(tempfile.mkdtemp(prefix="swiftpm-purifier-probe-"))
    logs = temporary / "logs"
    logs.mkdir()
    empty = temporary / "empty"
    empty.mkdir()
    if any((parent / "Package.swift").exists() for parent in empty.parents):
        shutil.rmtree(temporary)
        raise PurifierError("native option probes need TMPDIR outside a Swift package")
    probes = [
        [],
        ["--purifier-option-that-does-not-exist"],
        ["--disable-dependency-cache"],
        ["--disable-repository-cache"],
        ["--manifest-cache", "none"],
        ["--disable-package-manifest-caching"],
        ["--cache-path", str(temporary / "cache")],
        ["--config-path", str(temporary / "config")],
        ["--security-path", str(temporary / "security")],
        ["--disable-experimental-prebuilts"],
    ]
    receipt = {"command": command, "probes": []}
    recognized = set()

    def save():
        (temporary / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    try:
        for index, arguments in enumerate(probes):
            row = {"label": str(index)}
            receipt["probes"].append(row)
            result = bounded_native.run_recorded(
                [*prefix, *arguments, *([subcommand] if subcommand else [])],
                record=row,
                logs=logs,
                save=save,
                cwd=empty,
                env=dict(
                    os.environ,
                    XDG_CACHE_HOME=str(temporary / "cache"),
                    XDG_CONFIG_HOME=str(temporary / "config"),
                ),
                timeout=20,
                output_limit=1024 * 1024,
            )
            error = result.stderr.decode(errors="replace").lower()
            missing = any(
                marker in error
                for marker in (
                    "could not find package.swift",
                    "root manifest not found",
                    "rootmanifestfilenotfound",
                )
            )
            unknown = "unknown option" in error
            if (
                result.returncode == 0
                or missing == unknown
                or (index == 0 and not missing)
                or (index == 1 and not unknown)
            ):
                raise PurifierError(
                    "selected native parser did not establish an isolated option probe"
                )
            if arguments and missing:
                recognized.add(arguments[0])
    except Exception as error:
        raise PurifierError(
            f"native SDK option probe failed; retained raw evidence at {temporary}: {error}"
        ) from error
    shutil.rmtree(temporary)
    return frozenset(recognized)


def swiftpm_isolation(work: Path):
    """Flags that point SwiftPM's caches at empty directories in the lock
    run's directory. SwiftPM's configuration is a copy of the user
    configuration, so registries, mirrors and credentials still apply."""
    options = native_options(tuple(native_command("swift-package")), "resolve")
    repository = next(
        (
            option
            for option in ("--disable-dependency-cache", "--disable-repository-cache")
            if option in options
        ),
        None,
    )
    manifest = (
        ["--manifest-cache", "none"]
        if "--manifest-cache" in options
        else (
            ["--disable-package-manifest-caching"]
            if "--disable-package-manifest-caching" in options
            else None
        )
    )
    if sdk_mode() and (not repository or not manifest or "--cache-path" not in options):
        raise PurifierError(
            "selected SwiftPM cannot provide the required isolated cache controls"
        )
    config = work / "config"
    user_config = Path.home() / ".swiftpm" / "configuration"
    if user_config.is_dir():
        shutil.copytree(user_config, config)
    else:
        config.mkdir()
    flags = ([repository] if repository else []) + (manifest or [])
    if "--cache-path" in options:
        flags += ["--cache-path", work / "cache"]
    if "--config-path" in options:
        flags += ["--config-path", config]
    if "--security-path" in options:
        flags += ["--security-path", work / "security"]
    if "--disable-experimental-prebuilts" in options:
        flags += ["--disable-experimental-prebuilts"]
    return flags


@functools.lru_cache(maxsize=8)
def measured_native_sdk(path, nix):
    descriptor = native_sdk.load_descriptor(path)
    return native_sdk.measure(descriptor, nix, native_options, verify_active=False)


def native_sdk_identity():
    path = os.environ.get("SWIFTPM_PURIFIER_NATIVE_SDK")
    if not path:
        raise PurifierError(
            "no source-owned selected SDK descriptor; build the CLI with the project's swiftPackages override"
        )
    try:
        descriptor = native_sdk.load_descriptor(path)
        native_sdk.check_bindings(descriptor)
        identity = measured_native_sdk(path, tool("nix"))
        native_sdk.check_active(identity)
        return identity
    except (native_sdk.SDKError, OSError, ValueError) as error:
        raise PurifierError(str(error)) from error


def require_native_sdk(lock):
    if lock["version"] == legacy_state.FORMAT:
        raise PurifierError(
            "portable legacy format 6 has no SDK-bound CLI contract; use local mode or an explicit pure-builder SDK"
        )
    if lock["version"] == LEGACY_LOCK_VERSION:
        return
    actual = native_sdk_identity()
    if actual != lock["nativeSDK"]:
        raise PurifierError(
            "selected native SDK, compiler, environment, or physical closure differs from the lock; relock with the project's swiftPackages"
        )


def copy_package(root: Path, destination: Path):
    def ignore(directory, names):
        if Path(directory) == root:
            return [n for n in names if n in (".build", "Packages", ".git")]
        return [n for n in names if n == ".build"]

    shutil.copytree(root, destination, ignore=ignore, symlinks=True)


def read_json(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


LOCK_KEYS = (
    "version",
    "package",
    "rootDirectory",
    "manifestHash",
    "products",
    "workspaceStateVersion",
    "pins",
    "binaryTargets",
    "localDependencies",
    "contentHash",
)


def refuse_mixed_dependency_state3(lock):
    if (
        isinstance(lock, dict)
        and lock.get("version") in (4, 5)
        and lock.get("workspaceStateVersion") == 3
    ):
        raise PurifierError(
            "native dependency-state 3 requires portable format 9; "
            "formats 4 and 5 cannot describe this protocol"
        )


def read_lock(root: Path):
    """The package's lockfile, or None when it has none. A lockfile that is
    not valid JSON, lacks a field, or was changed after the lock run wrote
    it stops with the command that renews it."""
    recover_dependencies_v1(root)
    path = lock_path(root)
    if not path.exists():
        return None
    try:
        lock = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise PurifierError(
            f"{path} is not valid JSON ({error}); run `swiftpm-purifier relock` to write it again"
        )
    refuse_mixed_dependency_state3(lock)
    if (
        isinstance(lock, dict)
        and type(lock.get("version")) is int
        and lock["version"]
        in (
            dependencies_v3.FORMAT,
            workspace_v4_path.FORMAT,
            workspace_v5_location.FORMAT,
        )
    ):
        return validate_dependencies_v3(root, path.read_bytes(), lock["version"])
    if (
        isinstance(lock, dict)
        and type(lock.get("version")) is int
        and lock["version"] == 8
    ):
        return validate_dependencies_v1(root, path.read_bytes())
    if (
        isinstance(lock, dict)
        and type(lock.get("version")) is int
        and lock["version"] == pre_workspace.FORMAT
    ):
        return validate_pre_workspace(root, path.read_bytes())
    missing = (
        [key for key in LOCK_KEYS if key not in lock]
        if isinstance(lock, dict)
        else list(LOCK_KEYS)
    )
    if missing:
        raise PurifierError(
            f"{path} lacks {', '.join(missing)}; run `swiftpm-purifier relock` to write it again"
        )
    if type(lock["version"]) is not int or lock["version"] not in (
        LEGACY_LOCK_VERSION,
        LOCK_VERSION,
        legacy_state.FORMAT,
    ):
        raise PurifierError(
            f"{path} has lock format version {lock['version']}, and this swiftpm-purifier reads "
            f"versions {LEGACY_LOCK_VERSION}, {LOCK_VERSION}, {legacy_state.FORMAT}, {pre_workspace.FORMAT}, 8, {dependencies_v3.FORMAT}, {workspace_v4_path.FORMAT} and {workspace_v5_location.FORMAT}; run `swiftpm-purifier relock`"
        )
    if lock["version"] == legacy_state.FORMAT:
        try:
            legacy_state.validate(lock, legacy_native_path(root))
        except legacy_state.StateError as error:
            raise PurifierError(
                str(error) + "; run `swiftpm-purifier relock`"
            ) from error
    elif lock.get("workspaceStateVersion") == 1:
        raise PurifierError(
            "native workspace format 1 requires portable lock format 6; run `swiftpm-purifier relock`"
        )
    if lock["version"] == LOCK_VERSION:
        if "nativeSDK" not in lock:
            raise PurifierError(
                f"{path} lacks nativeSDK; run `swiftpm-purifier relock`"
            )
        try:
            native_sdk.validate_identity(lock["nativeSDK"])
        except native_sdk.SDKError as error:
            raise PurifierError(
                f"{path}: {error}; run `swiftpm-purifier relock`"
            ) from error
    if lock["contentHash"] != content_hash(lock):
        raise PurifierError(
            f"{path} does not match its contentHash, so it was changed after the lock run "
            "wrote it; run `swiftpm-purifier relock` to write it again"
        )
    return lock


def mirrors(package: Path, config: Path, legacy=False) -> dict:
    """SwiftPM's mirrors, original URL to mirror URL. The package
    configuration wins over the user configuration, as in SwiftPM."""
    result = {}
    sources = (
        config / "mirrors.json",
        package / ".swiftpm" / "configuration" / "mirrors.json",
    )
    if legacy:
        override = os.environ.get("SWIFTPM_MIRROR_CONFIG")
        sources = (Path(override) if override else package / ".swiftpm" / "config",)
    for source in sources:
        data = read_json(source) or {}
        for entry in data.get("object", []):
            result[entry["original"]] = entry["mirror"]
    return result


def registries(package: Path, config: Path) -> dict:
    """SwiftPM's registries: the default URL under "[default]", and one per
    scope. The package configuration wins over the user configuration."""
    result = {}
    for source in (
        config / "registries.json",
        package / ".swiftpm" / "configuration" / "registries.json",
    ):
        data = read_json(source) or {}
        for scope, registry in data.get("registries", {}).items():
            result[scope] = registry["url"].rstrip("/")
    return result


def prefetch_git(url: str, rev: str) -> str:
    result = json.loads(
        output(
            [
                tool("nix-prefetch-git"),
                "--quiet",
                "--fetch-submodules",
                "--url",
                url,
                "--rev",
                rev,
            ]
        )
    )
    return result["hash"]


def prefetch_file(url: str) -> str:
    result = json.loads(
        output(
            [
                tool("nix"),
                "store",
                "prefetch-file",
                "--hash-type",
                "sha256",
                "--json",
                url,
            ]
        )
    )
    return result["hash"]


def subpath_of(pin: dict) -> str:
    """Where SwiftPM keeps a pinned dependency, as lock.nix derives it: a
    checkout under the last component of the URL without its `.git`
    suffix, a registry download under scope/name/version."""
    if pin["kind"] == "registry":
        scope, _, name = pin["identity"].partition(".")
        return f"{scope}/{name}/{pin['state']['version']}"
    base = pin["location"].rstrip("/").rsplit("/", 1)[-1]
    return base.removesuffix(".git")


def record_of(pin: dict, name: str, state_version=None) -> dict:
    """The workspace-state record of a pinned dependency marked as edited,
    as lock.nix derives it from the pin and the package name the lockfile
    records."""
    if "legacyPackage" in pin and state_version == 7:
        # Package.resolved's schema is independent of the workspace schema.
        # Current SwiftPM can keep v1 pins while writing workspace format 7.
        pin = dict(
            pin,
            kind="remoteSourceControl",
            state={
                key: value for key, value in pin["state"].items() if value is not None
            },
        )
    registry = pin["kind"] == "registry"
    ref = {
        "identity": pin["identity"],
        "kind": pin["kind"],
        "location": pin["identity"] if registry else pin["location"],
        "name": name,
    }
    subpath = subpath_of(pin)
    if state_version == 2:
        ref = {
            "identity": pin["identity"],
            "isLocal": False,
            "name": name,
            "path": pin["location"],
        }
        subpath = name
    state = (
        {"name": "registryDownload", "version": pin["state"]["version"]}
        if registry
        else {
            "name": "checkout" if pin["kind"] == "remote" else "sourceControlCheckout",
            "checkoutState": pin["state"],
        }
    )
    return {
        "basedOn": {
            "basedOn": None,
            "packageRef": ref,
            "state": state,
            "subpath": subpath,
        },
        "packageRef": ref,
        "state": {"name": "edited", "path": None},
        "subpath": subpath,
    }


def lock_pin(
    pin: dict, dependency: dict, package: Path, config: Path, state_version=None
) -> dict:
    """The lockfile's record of one pin of Package.resolved: what a build
    needs beyond the pin. `dependency` is SwiftPM's workspace-state record
    of the same dependency, which the record lock.nix derives must equal."""
    ref = dependency["packageRef"]
    kind = pin["kind"]
    identity = pin["identity"]
    if kind == "localSourceControl":
        raise PurifierError(
            f"{identity} is a git repository at {pin['location']} on this machine. "
            "A lockfile cannot name a repository that exists only on one machine, so a Nix "
            "build elsewhere could not fetch it. Depend on it by path (`.package(path:)`) "
            "inside the package, or by its remote URL."
        )
    if kind not in ("remote", "remoteSourceControl", "registry"):
        raise PurifierError(f"{identity}: a pin of kind {kind!r} is not supported")
    if "legacyPackage" in pin and pin["legacyPackage"] != ref["name"]:
        raise PurifierError(
            f"{identity}: version-1 package name disagrees with native workspace state"
        )
    derived = record_of(pin, ref["name"], state_version)
    if state_version == 2:
        if (
            kind != "remote"
            or dependency.get("basedOn") is not None
            or not isinstance(ref.get("name"), str)
            or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", ref["name"]) is None
            or not isinstance(dependency.get("subpath"), str)
            or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", dependency["subpath"])
            is None
        ):
            raise PurifierError(
                "unsupported or unsafe native dependency-state format-2 checkout"
            )
        # The old native checkout subpath contains an opaque repository hash.
        # Edited dependencies use the package name; the dormant basedOn checkout
        # can use that same deterministic path (verified with native SwiftPM).
        dependency = dict(dependency, subpath=ref["name"])
    recorded = {
        "basedOn": dict(dependency, basedOn=None),
        "packageRef": ref,
        "state": {"name": "edited", "path": None},
        "subpath": dependency["subpath"],
    }
    if derived != recorded:
        raise PurifierError(
            f"SwiftPM recorded {identity} in a form swiftpm-purifier does not derive from "
            f"Package.resolved, so a Nix build could not reproduce it.\n"
            f"  derived:  {json.dumps(derived['basedOn'], sort_keys=True)}\n"
            f"  recorded: {json.dumps(recorded['basedOn'], sort_keys=True)}"
        )
    locked = {"name": ref["name"]}
    if "legacyPackage" in pin:
        # Version 1 stores the package name, not its native identity. Preserve
        # the source-to-identity join instead of inventing URL case folding.
        locked["sourceLocation"] = pin["location"]
    if kind == "registry":
        scope, _, name = identity.partition(".")
        known = registries(package, config)
        registry = known.get(scope) or known.get("[default]")
        if registry is None:
            raise PurifierError(f"no registry is configured for {identity}")
        version = pin["state"]["version"]
        locked["version"] = version
        locked["url"] = (
            f"{registry}/{urllib.parse.quote(scope)}/{urllib.parse.quote(name)}/{version}.zip"
        )
        locked["hash"] = prefetch_file(locked["url"])
        return locked
    rev = pin["state"]["revision"]
    url = mirrors(package, config, legacy=kind == "remote" and state_version != 7).get(
        pin["location"], pin["location"]
    )
    # The fetch URL is recorded only when a mirror replaces the location
    # that Package.resolved names.
    if url != pin["location"]:
        locked["url"] = url
    locked["revision"] = rev
    locked["hash"] = prefetch_git(url, rev)
    return locked


def template(value, package: Path):
    """`value` with the lock run's package directory replaced by
    @root@ in every string."""
    prefixes = sorted({str(package), str(package.resolve())}, key=len, reverse=True)
    if isinstance(value, str):
        for prefix in prefixes:
            value = value.replace(prefix, ROOT_PLACEHOLDER)
        return value
    if isinstance(value, list):
        return [template(v, package) for v in value]
    if isinstance(value, dict):
        return {k: template(v, package) for k, v in value.items()}
    return value


def products(package: Path, env, isolation):
    description = json.loads(
        output(
            [*native_command("swift-package"), *isolation, "dump-package"],
            cwd=package,
            env=env,
        )
    )
    return product_description(description)


def product_description(description):
    result = []
    for product in description["products"]:
        if "product_type" in product:
            kind = product["product_type"]
            detail = [product["type"]] if product.get("type") else []
        else:
            kind, detail = next(iter(product["type"].items()))
        entry = {"name": product["name"], "type": kind}
        if kind == "library":
            entry["linkage"] = detail[0] if detail else "automatic"
        result.append(entry)
    return result, description["name"]


def workspace_path_candidate(value, *, root):
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value["version"] != 4
    ):
        return False
    body = value.get("object")
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("dependencies"), list)
        or not body["dependencies"]
    ):
        return False
    refs = [
        record.get("packageRef") if isinstance(record, dict) else None
        for record in body["dependencies"]
    ]
    path_refs = [isinstance(ref, dict) and "path" in ref for ref in refs]
    if not any(path_refs):
        return False
    try:
        workspace_v4_path.native_index(
            value, root, allow_edited=True, physical_locals=False
        )
    except (ValueError, OSError) as error:
        raise PurifierError(str(error)) from error
    return True


def workspace_location_candidate(value, *, root, selected_pins):
    """Observe a complete nonempty V5/pins1 graph, never mutation authority."""
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value["version"] != 5
    ):
        return False
    if selected_pins is None or (
        isinstance(selected_pins, dict)
        and type(selected_pins.get("version")) is int
        and selected_pins["version"] == 2
    ):
        return False
    body = value.get("object")
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("dependencies"), list)
        or not body["dependencies"]
    ):
        return False
    try:
        records = workspace_v5_location.native_index(value, root)
        pins = workspace_v5_location.pins_index(selected_pins)
        remote = {
            r["packageRef"]["location"]: r
            for r in records.values()
            if r["packageRef"]["kind"] == "remoteSourceControl"
        }
        dependencies_v1_cache.require(
            set(remote) == set(pins), "workspace5 complete source/pin closure"
        )
        for location, record in remote.items():
            dependencies_v1_cache.require(
                pins[location]["package"] == record["packageRef"]["name"]
                and workspace_v5_location.checkout_state(
                    pins[location]["state"], selected=True
                )
                == record["state"]["checkoutState"],
                "workspace5 source/pin state join",
            )
    except (ValueError, OSError) as error:
        raise PurifierError(str(error)) from error
    return True


def fresh_location_workspace(root, existing):
    """Explicit fresh adoption after recorded old ownership was removed."""
    if not (
        isinstance(existing, dict)
        and type(existing.get("version")) is int
        and existing["version"] in (dependencies_v3.FORMAT, workspace_v4_path.FORMAT)
    ):
        return False
    if any(
        os.path.lexists(root / relative)
        for relative in (
            "Packages",
            ".build/dependencies-state.json",
            "Package.pins",
            STATE_DIR / "links.json",
            pre_workspace_install.LEGACY_STATE,
        )
    ):
        return False
    state_path = root / ".build/workspace-state.json"
    if not os.path.lexists(state_path):
        return False
    value = dependencies_v1_cache.decode(
        dependencies_v1_cache.regular_bytes(root, ".build/workspace-state.json")
    )
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value["version"] != 5
    ):
        return False
    native, portable = dependencies_v1_selections(root)
    selected, output, logical = dependencies_v1_selectors.pair(root, native, portable)
    pins = (
        dependencies_v1_cache.decode(selected.raw) if selected.raw is not None else None
    )
    result = workspace_location_candidate(value, root=root, selected_pins=pins)
    for selection in (selected, output, logical):
        selection.verify()
    return result


def fresh_path_workspace(root, existing):
    """Discover native PATH state after the old owned graph was removed.

    This selects a capture protocol only. Its locked ownership and publication
    guards still determine whether any selected files may be replaced.
    """
    if not (
        isinstance(existing, dict)
        and type(existing.get("version")) is int
        and existing["version"] == dependencies_v3.FORMAT
    ):
        return False
    if os.path.lexists(root / "Packages") or os.path.lexists(
        root / ".build/dependencies-state.json"
    ):
        return False
    path = root / ".build/workspace-state.json"
    if not os.path.lexists(path):
        return False
    value = dependencies_v1_cache.decode(
        dependencies_v1_cache.regular_bytes(root, ".build/workspace-state.json")
    )
    return workspace_path_candidate(value, root=root)


def relock(root: Path, update=None):
    """Locks the package. SwiftPM resolves it, keeping the pins of
    Package.resolved; with `update`, a list of package identities, SwiftPM
    updates those packages first, or every package for an empty list."""
    recover_dependencies_v1(root)
    try:
        existing = read_json(lock_path(root))
    except (ValueError, OSError):
        existing = None
    if (
        isinstance(existing, dict)
        and type(existing.get("version")) is int
        and existing["version"] == workspace_v5_location.FORMAT
    ) or os.path.lexists(root / "Packages" / workspace_v5_location_ownership.MARKER):
        return relock_dependencies_v3(root, update, workspace_v5_location.FORMAT)
    if fresh_location_workspace(root, existing):
        return relock_dependencies_v3(root, update, workspace_v5_location.FORMAT)
    if fresh_path_workspace(root, existing):
        return relock_dependencies_v3(root, update, workspace_v4_path.FORMAT)
    if (
        isinstance(existing, dict)
        and type(existing.get("version")) is int
        and existing["version"] == dependencies_v3.FORMAT
    ) or os.path.lexists(root / "Packages" / dependencies_v3_ownership.MARKER):
        return relock_dependencies_v3(root, update)
    if (
        isinstance(existing, dict)
        and type(existing.get("version")) is int
        and existing["version"] == workspace_v4_path.FORMAT
    ) or os.path.lexists(root / "Packages" / workspace_v4_path_ownership.MARKER):
        return relock_dependencies_v3(root, update, workspace_v4_path.FORMAT)
    workspace_path = root / ".build/workspace-state.json"
    if os.path.lexists(workspace_path):
        value = dependencies_v1_cache.decode(
            dependencies_v1_cache.regular_bytes(root, ".build/workspace-state.json")
        )
        if workspace_path_candidate(value, root=root):
            return relock_dependencies_v3(root, update, workspace_v4_path.FORMAT)
        selected = read_json(resolved_path(root))
        if workspace_location_candidate(value, root=root, selected_pins=selected):
            return relock_dependencies_v3(root, update, workspace_v5_location.FORMAT)
    native_state = root / ".build/dependencies-state.json"
    if (
        isinstance(existing, dict)
        and type(existing.get("version")) is int
        and existing["version"] == 8
    ) or os.path.lexists(root / "Packages" / dependencies_v1_ownership.MARKER):
        return relock_dependencies_v1(root, update)
    if os.path.lexists(native_state):
        value = dependencies_v1_cache.decode(
            dependencies_v1_cache.regular_bytes(root, ".build/dependencies-state.json")
        )
        if (
            isinstance(value, dict)
            and type(value.get("version")) is int
            and value["version"] == 1
        ):
            return relock_dependencies_v1(root, update)
        if (
            isinstance(value, dict)
            and type(value.get("version")) is int
            and value["version"] == dependencies_v3.NATIVE_VERSION
        ):
            return relock_dependencies_v3(root, update)
    if not sdk_mode():
        state = dependencies_v1_lifecycle.registry(root)
        with tempfile.TemporaryDirectory(
            prefix="protocol-probe-", dir=state
        ) as temporary:
            if dependencies_v1_capture.candidate(
                sys.modules[__name__], Path(temporary), os.environ
            ):
                return relock_dependencies_v1(root, update)
    sdk_identity = native_sdk_identity() if sdk_mode() else None
    with tempfile.TemporaryDirectory(prefix="swiftpm-purifier-") as temporary:
        work = Path(temporary)
        probe = work / "protocol-probe"
        probe.mkdir()
        try:
            if pre_workspace.candidate(sys.modules[__name__], probe, os.environ):
                return pre_workspace.capture(root, work, sys.modules[__name__], update)
        except (
            pre_workspace.StateError,
            pre_workspace.layout.LayoutError,
            legacy_manifest.ManifestError,
        ) as error:
            raise PurifierError(str(error)) from error
        if not sdk_mode():
            observe_host_runtime()
        # SwiftPM names the root package after its directory, and records
        # binary targets under that name, so the copy keeps the name.
        package = work / "packages" / root.name
        package.parent.mkdir()
        copy_package(root, package)
        # With a lockfile of SwiftPM kept outside the package, the lock run
        # starts from that copy when it exists, and from the copy in the
        # package otherwise.
        override_legacy = None
        if TOOL_LOCK_OVERRIDE and resolved_path(root).exists():
            external = read_json(resolved_path(root))
            override_legacy = (
                isinstance(external, dict)
                and external.get("version") == 1
                and "object" not in external
                and "pins" in external
            )
            if override_legacy:
                try:
                    legacy_state.native_pins(resolved_path(root))
                except legacy_state.StateError as error:
                    raise PurifierError(str(error)) from error
            shutil.copyfile(
                resolved_path(root),
                package / ("Package.pins" if override_legacy else "Package.resolved"),
            )
        env = dict(os.environ, CLANG_MODULE_CACHE_PATH=str(work / "clang-module-cache"))
        isolation = swiftpm_isolation(work)
        print(
            (
                "swiftpm-purifier: resolving with an empty cache"
                if sdk_mode()
                else "swiftpm-purifier: resolving with the selected host SwiftPM"
            ),
            file=sys.stderr,
        )
        if update is None:
            run(
                [*native_command("swift-package"), *isolation, "resolve"],
                cwd=package,
                env=env,
            )
        else:
            run(
                [*native_command("swift-package"), *isolation, "update", *update],
                cwd=package,
                env=env,
            )

        resolved = package / "Package.resolved"
        resolved_bytes = resolved.read_bytes() if resolved.exists() else None

        state_path = package / ".build" / "workspace-state.json"
        if not state_path.exists():
            legacy_state_path = package / ".build" / "dependencies-state.json"
            if legacy_state_path.exists():
                version = read_json(legacy_state_path).get("version")
                if type(version) is int and version == 1:
                    return relock_dependencies_v1(root, update)
                if version == dependencies_v3.NATIVE_VERSION:
                    return relock_dependencies_v3(root, update)
                if version != 2:
                    raise PurifierError(
                        f"SwiftPM dependency-state format {version} is not supported by this purifier"
                    )
                state_path = legacy_state_path
            else:
                # Some original parsers accept `resolve` without materializing
                # workspace state. Ask their actual `fetch` parser and run it.
                native_options(tuple(native_command("swift-package")), "fetch")
                run(
                    [*native_command("swift-package"), *isolation, "fetch"],
                    cwd=package,
                    env=env,
                )
                if not state_path.exists():
                    raise PurifierError(
                        "SwiftPM did not produce a recognized dependency-state file"
                    )
        state = json.loads(state_path.read_text())
        if workspace_path_candidate(state, root=package):
            return relock_dependencies_v3(root, update, workspace_v4_path.FORMAT)
        if workspace_location_candidate(
            state, root=package, selected_pins=read_json(resolved)
        ):
            return relock_dependencies_v3(root, update, workspace_v5_location.FORMAT)
        if state.get("version") == 1:
            if override_legacy is False:
                raise PurifierError(
                    "selected external file is not original native Package.pins metadata"
                )
            try:
                return legacy_state.capture(
                    root, package, state, env, isolation, sys.modules[__name__]
                )
            except (legacy_state.StateError, legacy_manifest.ManifestError) as error:
                raise PurifierError(str(error)) from error
        if override_legacy:
            raise PurifierError(
                "selected external Package.pins format does not match the actual runtime workspace"
            )
        if state["version"] == 2:
            names = []
            for dependency in state["object"]["dependencies"]:
                ref = dependency.get("packageRef", {})
                if ref.get("isLocal") is not False and ref.get("isLocal") is not True:
                    raise PurifierError(
                        "dependency-state format 2 has an unknown package reference"
                    )
                names.append(ref.get("name"))
            if any(not isinstance(name, str) for name in names) or len(
                set(names)
            ) != len(names):
                raise PurifierError(
                    "dependency-state format 2 has missing or duplicate package names"
                )
        config = work / "config"
        recorded = {}
        for dependency in state["object"]["dependencies"]:
            ref = (
                dependency.get("packageRef", {}) if isinstance(dependency, dict) else {}
            )
            identity = ref.get("identity") if isinstance(ref, dict) else None
            if not isinstance(identity, str) or not identity or identity in recorded:
                raise PurifierError(
                    "native workspace has a missing or duplicate package identity"
                )
            recorded[identity] = dependency
        legacy_identities = {}
        for identity, dependency in recorded.items():
            ref = dependency["packageRef"]
            legacy_ref = state["version"] == 2 and ref.get("isLocal") is False
            if legacy_ref or ref.get("kind") in ("remote", "remoteSourceControl"):
                location = ref["path"] if legacy_ref else ref["location"]
                if location in legacy_identities:
                    raise PurifierError("native workspace repeats a source location")
                legacy_identities[location] = identity
        pins = {}
        for pin in resolved_pins(package / "Package.resolved", legacy_identities):
            dependency = recorded.pop(pin["identity"], None)
            if dependency is None:
                raise PurifierError(
                    f"Package.resolved pins {pin['identity']}, which SwiftPM's workspace state lacks"
                )
            pins[pin["identity"]] = lock_pin(
                pin, dependency, package, config, state["version"]
            )
        # What remains are the path dependencies, which Package.resolved
        # does not list.
        local = []
        for identity, dependency in recorded.items():
            if state["version"] == 2:
                ref = dependency["packageRef"]
                if (
                    set(dependency) != {"basedOn", "packageRef", "state", "subpath"}
                    or set(ref) != {"identity", "isLocal", "name", "path"}
                    or ref.get("isLocal") is not True
                    or dependency["basedOn"] is not None
                    or dependency["state"] != {"name": "local"}
                    or dependency["subpath"] != identity
                    or not isinstance(ref.get("path"), str)
                    or not Path(ref["path"]).is_absolute()
                    or not Path(ref["path"]).resolve().is_relative_to(package.resolve())
                    or not (Path(ref["path"]) / "Package.swift").is_file()
                ):
                    raise PurifierError(
                        "unsupported or escaping dependency-state format-2 local package"
                    )
                try:
                    relative = Path(ref["path"]).relative_to(package)
                except ValueError as error:
                    raise PurifierError(
                        "local package path is outside the captured source"
                    ) from error
                if (
                    not relative.parts
                    or ".." in relative.parts
                    or str(Path(ref["path"])) != ref["path"]
                    or not legacy_local_path_valid(
                        package, "@root@/" + relative.as_posix()
                    )
                ):
                    raise PurifierError(
                        "local package path is not a portable in-tree path"
                    )
                local.append(dependency)
                continue
            if dependency["state"]["name"] != "fileSystem":
                raise PurifierError(
                    f"SwiftPM's workspace state has {identity} in state "
                    f"{dependency['state']['name']!r}, and Package.resolved does not pin it"
                )
            local.append(dependency)
        if state["object"].get("prebuilts"):
            raise PurifierError(
                "SwiftPM downloaded prebuilt libraries, which swiftpm-purifier does not lock yet"
            )

        package_products, package_name = products(package, env, isolation)
        lock = {
            "version": LOCK_VERSION if sdk_mode() else LEGACY_LOCK_VERSION,
            "package": package_name,
            "rootDirectory": root.name,
            "manifestHash": manifest_hash(root),
            "products": package_products,
            "workspaceStateVersion": state["version"],
            "pins": pins,
            # Binary targets are declared in the manifests, and their
            # records carry the URL and checksum the build fetches with.
            "binaryTargets": template(state["object"].get("artifacts", []), package),
            "localDependencies": template(local, package),
        }
        if sdk_identity is not None:
            lock["nativeSDK"] = sdk_identity
    # Validate and serialize both results before changing either user file.
    # Native resolution may succeed while workspace validation, product
    # inspection, or serialization still fails.
    try:
        lock["contentHash"] = content_hash(lock)
        lock_bytes = (
            json.dumps(
                lock, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise PurifierError(
            f"native lock metadata cannot be serialized: {error}"
        ) from error
    if resolved_bytes is not None:
        existing = resolved_path(root)
        if not existing.exists() or existing.read_bytes() != resolved_bytes:
            existing.parent.mkdir(parents=True, exist_ok=True)
            existing.write_bytes(resolved_bytes)
            print(f"swiftpm-purifier: wrote {existing}", file=sys.stderr)
    path = lock_path(root)
    path.write_bytes(lock_bytes)
    print(
        f"swiftpm-purifier: wrote {path} with {len(pins)} pins and "
        f"{len(lock['binaryTargets'])} binary targets",
        file=sys.stderr,
    )


def lock_command(root: Path, force: bool):
    recover_dependencies_v1(root)
    try:
        lock = read_lock(root)
    except PurifierError as error:
        # A lockfile that cannot be read is replaced.
        print(f"swiftpm-purifier: {error}", file=sys.stderr)
        lock = None
    expected_version = (
        LOCK_VERSION
        if sdk_mode()
        else (
            lock["version"]
            if lock
            and lock["version"]
            in (
                legacy_state.FORMAT,
                pre_workspace.FORMAT,
                8,
                dependencies_v3.FORMAT,
                workspace_v4_path.FORMAT,
                workspace_v5_location.FORMAT,
            )
            else LEGACY_LOCK_VERSION
        )
    )
    if lock and not force and lock["version"] == expected_version:
        require_runtime(root, lock)
    if (
        lock
        and not force
        and lock["version"] == expected_version
        and is_current(root, lock)
    ):
        print(f"swiftpm-purifier: {lock_path(root)} is up to date", file=sys.stderr)
        return
    relock(root)


# Publishing.


def multipart(fields):
    """A multipart/form-data body of (name, content type, bytes) fields."""
    boundary = "swiftpm-purifier-" + hashlib.sha256(os.urandom(16)).hexdigest()[:24]
    body = b""
    for name, content_type, data in fields:
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode()
        body += data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", body


def publish(root: Path, identity: str, version: str, registry):
    """Builds the package's source archive through Nix and publishes it to
    a package registry (SE-0391), the archive the registry then serves to
    SwiftPM."""
    import urllib.request

    lock = read_lock(root)
    if lock is not None:
        require_runtime(root, lock)
    if lock is None or not is_current(root, lock):
        raise PurifierError(
            f"{lock_path(root)} is missing or out of date; run `swiftpm-purifier lock` first"
        )
    if sdk_mode():
        native_sdk_identity()
    scope, dot, name = identity.partition(".")
    if not dot or not scope or not name:
        raise PurifierError(
            f"{identity} is not a registry identity of the form scope.name"
        )
    if registry is None:
        known = registries(root, Path.home() / ".swiftpm" / "configuration")
        registry = known.get(scope) or known.get("[default]")
        if registry is None:
            raise PurifierError(
                f"no registry is configured for {identity}; pass --registry"
            )
    archive = nix_build(
        f"""(pkgs.swiftpm-purifier.projectData {{
          src = /. + builtins.getEnv "SWIFTPM_PURIFIER_ROOT";
          platform = if selectedSDK == null then builtins.currentSystem else selectedSDK.system;
          inherit selectedSDK;
          {nix_lock_arguments(root)}
        }}).release""",
        nix_lock_environment(root),
    )
    content_type, body = multipart(
        [
            ("source-archive", "application/zip", Path(archive).read_bytes()),
            ("metadata", "application/json", b"{}"),
        ]
    )
    url = f"{registry.rstrip('/')}/{urllib.parse.quote(scope)}/{urllib.parse.quote(name)}/{version}"
    request = urllib.request.Request(url, data=body, method="PUT")
    request.add_header("Content-Type", content_type)
    request.add_header("Accept", "application/vnd.swift.registry.v1+json")
    token = os.environ.get("SWIFTPM_PURIFIER_REGISTRY_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request) as response:
            print(
                f"swiftpm-purifier: published {identity} {version} to {registry} ({response.status})",
                file=sys.stderr,
            )
    except urllib.error.HTTPError as error:
        raise PurifierError(
            f"{url} answered {error.code}: {error.read().decode(errors='replace')}"
        )


# Interactive mode.


def nix_build(attribute: str, env: dict, out_link=None) -> str:
    """Build source-owned data helpers over pkgs and the concrete SDK.

    pkgs supplies fetch/archive utilities; it never selects a replacement
    Swift SDK for the project's native commands.
    """
    flake = os.environ.get(
        "SWIFTPM_PURIFIER_FLAKE", str(Path(__file__).resolve().parent.parent.parent)
    )
    expression = f"""
      let
        flake = builtins.getFlake ("path:" + builtins.getEnv "SWIFTPM_PURIFIER_FLAKE_SOURCE");
        selectedSDK = if builtins.getEnv "SWIFTPM_PURIFIER_RUNTIME_MODE" == "sdk" then
          builtins.fromJSON (builtins.unsafeDiscardStringContext (builtins.readFile
            (/. + builtins.getEnv "SWIFTPM_PURIFIER_NATIVE_SDK"))) else null;
        pkgs = import flake.inputs.nixpkgs {{
          system = builtins.currentSystem;
          overlays = [ flake.overlays.default ];
        }};
      in
      {attribute}
    """
    command = [
        tool("nix"),
        "build",
        "--impure",
        "--print-out-paths",
        "--expr",
        expression,
    ]
    command += ["--out-link", out_link] if out_link else ["--no-link"]
    return output(
        command, env=dict(os.environ, **env, SWIFTPM_PURIFIER_FLAKE_SOURCE=flake)
    ).strip()


def recorded_links(root: Path) -> list:
    record = read_json(root / STATE_DIR / "links.json")
    return record or []


def unlink(root: Path):
    """Removes the links and the workspace state the interactive mode made,
    so SwiftPM resolves and fetches the usual way."""
    recover_dependencies_v1(root)
    try:
        portable_value = read_json(lock_path(root))
    except (ValueError, OSError):
        portable_value = None
    if os.path.lexists(root / "Packages" / workspace_v5_location_ownership.MARKER) or (
        isinstance(portable_value, dict)
        and type(portable_value.get("version")) is int
        and portable_value["version"] == workspace_v5_location.FORMAT
    ):
        if sdk_mode():
            raise PurifierError("portable format 11 has no SDK-bound CLI lifecycle")
        native, portable = dependencies_v1_selections(root)
        workspace_v5_location_lifecycle.uninstall(
            root,
            dependencies_v1_hash,
            dependencies_v1_lifecycle.registry(root),
            native,
            portable,
        )
        return
    if os.path.lexists(root / "Packages" / workspace_v4_path_ownership.MARKER) or (
        isinstance(portable_value, dict)
        and type(portable_value.get("version")) is int
        and portable_value["version"] == workspace_v4_path.FORMAT
    ):
        if sdk_mode():
            raise PurifierError("portable format 10 has no SDK-bound CLI lifecycle")
        native, portable = dependencies_v1_selections(root)
        workspace_v4_path_lifecycle.uninstall(
            root,
            dependencies_v1_hash,
            dependencies_v1_lifecycle.registry(root),
            native,
            portable,
        )
        return
    if os.path.lexists(root / "Packages" / dependencies_v3_ownership.MARKER) or (
        isinstance(portable_value, dict)
        and type(portable_value.get("version")) is int
        and portable_value["version"] == dependencies_v3.FORMAT
    ):
        if sdk_mode():
            raise PurifierError("portable format 9 has no SDK-bound CLI lifecycle")
        native, portable = dependencies_v1_selections(root)
        dependencies_v3_lifecycle.uninstall(
            root,
            dependencies_v1_hash,
            dependencies_v1_lifecycle.registry(root),
            native,
            portable,
        )
        return
    if os.path.lexists(root / "Packages" / dependencies_v1_ownership.MARKER) or (
        isinstance(portable_value, dict)
        and type(portable_value.get("version")) is int
        and portable_value["version"] == 8
    ):
        if sdk_mode():
            raise PurifierError("format8 has no SDK-bound CLI lifecycle")
        native, portable = dependencies_v1_selections(root)
        dependencies_v1_lifecycle.uninstall(
            root,
            dependencies_v1_hash,
            dependencies_v1_lifecycle.registry(root),
            native,
            portable,
        )
        return
    if os.path.lexists(
        root / "Packages" / pre_workspace_install.STATE
    ) or os.path.lexists(root / pre_workspace_install.LEGACY_STATE):
        try:
            pre_workspace_install.uninstall(root, tool("nix-hash"), tool("git"))
        except (pre_workspace.StateError, ValueError) as error:
            raise PurifierError(str(error)) from error
        return
    for relative in recorded_links(root):
        link = root / relative
        if link.is_symlink():
            link.unlink()
    if (root / STATE_DIR / "links.json").exists():
        for scratch in SCRATCH_DIRECTORIES:
            for filename in ("workspace-state.json", "dependencies-state.json"):
                state = root / scratch / filename
                if state.exists():
                    state.unlink()
    shutil.rmtree(root / STATE_DIR, ignore_errors=True)


def tree_entries(tree: Path) -> list:
    """The paths of the links in a built tree, relative to its root: one per
    fetched dependency or binary target, each pointing into the store."""
    found = []
    for directory, names, files in os.walk(tree):
        for name in names + files:
            path = Path(directory) / name
            if path.is_symlink():
                found.append(str(path.relative_to(tree)))
    return sorted(found)


def prefetch(root: Path):
    """Realizes, through Nix, everything day-to-day development needs: the
    locked dependencies and the toolchain with sourcekit-lsp, each kept by a
    GC root under .build/swiftpm-purifier. It links the dependencies into
    the package, with the workspace state that marks them as edited, so
    `swift build`, `swift test`, `swift run` and the editor work offline."""
    lock_file = lock_path(root)
    lock = read_lock(root)
    if lock is None:
        raise PurifierError(
            f"{root} has no {LOCK_NAME}; run `swiftpm-purifier lock` first"
        )
    if lock["version"] in (
        dependencies_v3.FORMAT,
        workspace_v4_path.FORMAT,
        workspace_v5_location.FORMAT,
    ):
        native, portable = dependencies_v1_selections(root)
        sources = dependencies_v3_sources(root, lock)
        state_lifecycle(lock["version"]).install(
            root,
            lock_file.read_bytes(),
            sources,
            sys.modules[__name__],
            dependencies_v1_hash,
            dependencies_v1_lifecycle.registry(root),
            native,
            portable,
        )
        return
    if lock["version"] == 8:
        native, portable = dependencies_v1_selections(root)
        sources = dependencies_v1_sources(root, lock)
        dependencies_v1_lifecycle.install(
            root,
            lock_file.read_bytes(),
            sources,
            sys.modules[__name__],
            dependencies_v1_hash,
            dependencies_v1_lifecycle.registry(root),
            native,
            portable,
        )
        return
    observation = require_runtime(root, lock)
    if lock["version"] == pre_workspace.FORMAT:
        try:
            tree = Path(
                nix_build(
                    f"""(pkgs.swiftpm-purifier.projectData {{
              src = /. + builtins.getEnv "SWIFTPM_PURIFIER_ROOT";
              platform = builtins.currentSystem;
              {nix_lock_arguments(root)}
            }}).tree""",
                    nix_lock_environment(root),
                )
            )
            validate_pre_workspace(root, lock_file.read_bytes())
            records = json.loads((tree / "pre-workspace-sources.json").read_text())
            pre_workspace_install.install(root, records, tool("nix-hash"), tool("git"))
        except (
            pre_workspace.StateError,
            pre_workspace.layout.LayoutError,
            ValueError,
        ) as error:
            raise PurifierError(str(error)) from error
        return
    if not is_current(root, lock):
        raise PurifierError(
            f"{lock_file} is out of date with Package.swift or Package.resolved; "
            "run `swiftpm-purifier lock`"
        )
    selected = None
    if sdk_mode():
        selected = native_sdk_identity()["descriptor"]
        cli = os.environ.get("SWIFTPM_PURIFIER_CLI_ROOT")
        try:
            if native_sdk.store_root(cli) != cli:
                raise native_sdk.SDKError(
                    "CLI is not an immutable installed store component"
                )
        except native_sdk.SDKError as error:
            raise PurifierError(str(error)) from error
        editor = None
        editor_recipe = os.environ.get("SWIFTPM_PURIFIER_EDITOR_RECIPE")
        if editor_recipe:
            editor_output = os.environ["SWIFTPM_PURIFIER_EDITOR_OUTPUT"]
            result = json.loads(
                output(
                    [
                        tool("nix"),
                        "build",
                        "--json",
                        "--no-link",
                        editor_recipe + "^" + editor_output,
                    ]
                )
            )
            editor = os.environ["SWIFTPM_PURIFIER_EDITOR_PATH"]
            if len(result) != 1 or result[0]["outputs"].get(editor_output) != editor:
                raise PurifierError(
                    "selected editor derivation produced a different component"
                )
    unlink(root)
    (root / STATE_DIR).mkdir(parents=True, exist_ok=True)
    gc_root = root / STATE_DIR / "tree"
    nix_build(
        f"""(pkgs.swiftpm-purifier.projectData {{
          src = /. + builtins.getEnv "SWIFTPM_PURIFIER_ROOT";
          platform = if selectedSDK == null then builtins.currentSystem else selectedSDK.system;
          inherit selectedSDK;
          {nix_lock_arguments(root)}
        }}).tree""",
        nix_lock_environment(root),
        out_link=gc_root,
    )
    if sdk_mode():
        nix_build(
            'pkgs.swiftpm-purifier.retainedToolchain (builtins.fromJSON (builtins.getEnv "SWIFTPM_PURIFIER_RETAINED"))',
            {
                "SWIFTPM_PURIFIER_RETAINED": json.dumps(
                    {
                        "cli": cli,
                        "swift": selected["components"]["swift"]["path"],
                        "swiftpm": selected["components"]["swiftpm"]["path"],
                        "editor": editor,
                    }
                )
            },
            out_link=root / STATE_DIR / "toolchain",
        )
    elif observation is not None:
        (root / STATE_DIR / "observed-host-runtime.json").write_text(
            json.dumps(observation, indent=2) + "\n"
        )
    tree = gc_root.resolve()
    links = []
    for entry in tree_entries(tree):
        target = root / entry
        if target.exists() or target.is_symlink():
            if target.is_symlink() and str(os.readlink(target)).startswith(
                "/nix/store/"
            ):
                target.unlink()
            else:
                raise PurifierError(
                    f"{target} exists and is not a link swiftpm-purifier made; "
                    "move it away, or run SwiftPM without swiftpm-purifier"
                )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(os.readlink(tree / entry))
        links.append(entry)
    state = (
        (tree / "workspace-state.json.in")
        .read_text()
        .replace(ROOT_PLACEHOLDER, str(root.resolve()))
    )
    for scratch in SCRATCH_DIRECTORIES:
        (root / scratch).mkdir(parents=True, exist_ok=True)
        filename = (
            "dependencies-state.json"
            if json.loads(state)["version"] == 2
            else "workspace-state.json"
        )
        (root / scratch / filename).write_text(state)
    (root / STATE_DIR / "links.json").write_text(json.dumps(links) + "\n")


# The SwiftPM subcommands that build, and so would download prebuilt
# swift-syntax libraries for macros. The lock does not record prebuilts,
# so these build swift-syntax from source, as the sandboxed build does.
BUILDING_SUBCOMMANDS = ("build", "test", "run")


def swift_command(arguments):
    swift = tool("swift")
    if not sdk_mode() and arguments[:1] in ([], ["--version"], ["--help"]):
        os.execvp(swift, [swift, *arguments])
    try:
        root = find_root(Path.cwd())
    except PurifierError:
        print(
            "swiftpm-purifier: warning: no swiftpm-purifier.lock.json found; running swift impurely. Run `swiftpm-purifier lock` to use Nix-resolved dependencies.",
            file=sys.stderr,
        )
        os.execvp(swift, [swift, *arguments])
    lock = read_lock(root)
    if lock is None:
        print(
            "swiftpm-purifier: warning: no swiftpm-purifier.lock.json found; running swift impurely. Run `swiftpm-purifier lock` to use Nix-resolved dependencies.",
            file=sys.stderr,
        )
        os.execvp(swift, [swift, *arguments])
    if sdk_mode() or lock["version"] == LOCK_VERSION:
        require_runtime(root, lock)
    if (
        lock["version"]
        not in (
            pre_workspace.FORMAT,
            8,
            dependencies_v3.FORMAT,
            workspace_v4_path.FORMAT,
            workspace_v5_location.FORMAT,
        )
        and arguments
        and arguments[0] in BUILDING_SUBCOMMANDS
    ):
        native = (
            tool("swift-" + arguments[0])
            if sdk_mode()
            else (
                tuple(native_command("swift-" + arguments[0]))
                if arguments[0] in selected_host_runtime()["commands"]
                else None
            )
        )
        if native is not None and "--disable-experimental-prebuilts" in native_options(
            native, None
        ):
            arguments = [
                arguments[0],
                "--disable-experimental-prebuilts",
                *arguments[1:],
            ]
    if arguments[:1] in ([], ["--version"], ["--help"]):
        os.execvp(swift, [swift, *arguments])
    if not is_current(root, lock):
        # SwiftPM would resolve and download what the lock does not hold.
        if lock["version"] not in (
            pre_workspace.FORMAT,
            8,
            dependencies_v3.FORMAT,
            workspace_v4_path.FORMAT,
            workspace_v5_location.FORMAT,
        ):
            unlink(root)
        raise PurifierError(
            f"{lock_path(root)} is out of date with Package.swift or Package.resolved; "
            "run `swiftpm-purifier lock`, which resolves and records the new dependencies"
        )
    prefetch(root)
    if lock["version"] in (
        dependencies_v3.FORMAT,
        workspace_v4_path.FORMAT,
        workspace_v5_location.FORMAT,
    ):
        native, portable = dependencies_v1_selections(root)
        status = state_lifecycle(lock["version"]).run_native(
            root,
            lock_path(root).read_bytes(),
            [swift, *arguments],
            dependencies_v1_hash,
            dependencies_v1_lifecycle.registry(root),
            native,
            portable,
            api=sys.modules[__name__],
        )
        legacy_pin_bridge.exit_status(status)
    if lock["version"] == 8:
        native, portable = dependencies_v1_selections(root)
        raise SystemExit(
            dependencies_v1_lifecycle.run_native(
                root,
                lock_path(root).read_bytes(),
                [swift, *arguments],
                dependencies_v1_hash,
                dependencies_v1_lifecycle.registry(root),
                native,
                portable,
            )
        )
    if lock["version"] == legacy_state.FORMAT:
        selected = legacy_native_path(root)
        logical = root / "Package.pins"
        original = logical.read_bytes() if logical.exists() else None
        if selected != logical:
            if selected.exists():
                logical.write_bytes(selected.read_bytes())
            elif logical.exists():
                logical.unlink()
        try:
            status = subprocess.run([swift, *arguments], check=False).returncode
        finally:
            if original is None:
                if logical.exists():
                    logical.unlink()
            elif not logical.exists() or logical.read_bytes() != original:
                logical.write_bytes(original)
        raise SystemExit(status)
    if lock["version"] == 4 and lock.get("workspaceStateVersion") == 2:
        status = legacy_pin_bridge.run_native(
            sys.modules[__name__], root, [swift, *arguments]
        )
        legacy_pin_bridge.exit_status(status)
    # SwiftPM deletes Package.resolved when every dependency is edited,
    # because no pin is left for it to write. The lockfile reads its pins
    # from Package.resolved, so the file goes back after SwiftPM exits.
    resolved = root / "Package.resolved"
    pins = resolved.read_bytes() if resolved.exists() else None
    try:
        status = subprocess.run([swift, *arguments], check=False).returncode
    finally:
        if pins is not None and (
            not resolved.exists() or resolved.read_bytes() != pins
        ):
            resolved.write_bytes(pins)
    raise SystemExit(status)


def main(argv=None):
    if not sdk_mode():
        os.environ.update(host_runtime.anchored_environment(os.environ, os.getcwd()))
    argv = sys.argv[1:] if argv is None else argv
    # Everything after `swift` belongs to SwiftPM, options included, so it
    # bypasses argparse.
    if argv[:1] == ["swift"]:
        try:
            swift_command(argv[1:])
        except (
            PurifierError,
            subprocess.SubprocessError,
            ValueError,
            OSError,
        ) as error:
            print(f"swiftpm-purifier: {error}", file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(
        prog="swiftpm-purifier", description=__doc__.splitlines()[0]
    )
    parser.add_argument(
        "--package-path",
        type=str,
        help="the package root (default: found from the current directory)",
    )
    parser.add_argument(
        "--lockfile",
        type=str,
        help=f"the purifier lockfile (default: {LOCK_NAME} in the package root)",
    )
    parser.add_argument(
        "--tool-lockfile",
        type=str,
        help="the Package.resolved to read and write instead of the one in the "
        "package root, which `lock` and `update` then leave as it is",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "lock", help="lock the package, unless its lockfile is up to date"
    )
    commands.add_parser("relock", help="lock the package")
    updating = commands.add_parser(
        "update",
        help="update the dependencies in Package.resolved through SwiftPM, then lock",
    )
    updating.add_argument(
        "packages",
        nargs="*",
        help="the package identities to update (default: every package)",
    )
    commands.add_parser(
        "prefetch",
        help="realize the locked dependencies and the toolchain, and link them into the package",
    )
    commands.add_parser("uninstall", help="remove the links prefetch made")
    publishing = commands.add_parser(
        "publish", help="publish the package's source archive to a registry"
    )
    publishing.add_argument(
        "identity", help="the package's registry identity, scope.name"
    )
    publishing.add_argument("version")
    publishing.add_argument(
        "--registry",
        help="the registry URL (default: the one configured for the scope)",
    )
    swift = commands.add_parser("swift", help="run swift on the locked dependencies")
    swift.add_argument("arguments", nargs=argparse.REMAINDER)
    options = parser.parse_args(argv)
    global \
        LOCK_OVERRIDE, \
        RAW_LOCK_OVERRIDE, \
        RAW_SOURCE_REFERENCE, \
        TOOL_LOCK_OVERRIDE, \
        RAW_TOOL_LOCK_OVERRIDE
    if LOCK_OVERRIDE is not None:
        LOCK_OVERRIDE = str(Path(LOCK_OVERRIDE).resolve())
    if options.package_path is not None:
        RAW_SOURCE_REFERENCE = (
            options.package_path
            if os.path.isabs(options.package_path)
            else os.path.join(os.getcwd(), options.package_path)
        )
    if options.lockfile is not None:
        RAW_LOCK_OVERRIDE = (
            options.lockfile
            if os.path.isabs(options.lockfile)
            else os.path.join(os.getcwd(), options.lockfile)
        )
        LOCK_OVERRIDE = str(Path(options.lockfile).resolve())
    if options.tool_lockfile is not None:
        RAW_TOOL_LOCK_OVERRIDE = (
            options.tool_lockfile
            if os.path.isabs(options.tool_lockfile)
            else os.path.join(os.getcwd(), options.tool_lockfile)
        )
        TOOL_LOCK_OVERRIDE = str(Path(options.tool_lockfile).resolve())

    try:
        if options.command == "swift":
            if options.package_path is not None:
                os.chdir(options.package_path)
            swift_command(options.arguments)
        root = (
            Path(options.package_path)
            if options.package_path is not None
            else find_root(Path.cwd())
        ).resolve()
        if options.command == "lock":
            lock_command(root, force=False)
        elif options.command == "relock":
            lock_command(root, force=True)
        elif options.command == "update":
            relock(root, update=options.packages)
        elif options.command == "prefetch":
            prefetch(root)
        elif options.command == "uninstall":
            unlink(root)
        elif options.command == "publish":
            publish(root, options.identity, options.version, options.registry)
    except (PurifierError, subprocess.SubprocessError, ValueError, OSError) as error:
        print(f"swiftpm-purifier: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
