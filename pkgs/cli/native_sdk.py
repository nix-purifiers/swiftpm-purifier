# SPDX-License-Identifier: MIT
"""Bind the concrete selected SDK, environment, recipes, and physical closure.

Host Python/Nix are management helpers. Project compiler and SwiftPM commands
come exclusively from the source-owned descriptor of swiftPackages.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time

import bounded_native


class SDKError(ValueError):
    pass


ROLES = ("swift", "swiftc", "package", "build", "test", "run")
STORE = re.compile(r"/nix/store/[0-9a-z]{32}-[A-Za-z0-9+._?=-]{1,211}")
HASH = re.compile(r"sha256-[A-Za-z0-9+/]{43}=")


def store_root(path):
    if (
        not isinstance(path, str)
        or not path.startswith("/nix/store/")
        or "\x00" in path
    ):
        raise SDKError("SDK path is outside the Nix store")
    parts = path.split("/")
    root = "/".join(parts[:4])
    if not STORE.fullmatch(root) or any(part in ("", ".", "..") for part in parts[4:]):
        raise SDKError("invalid SDK store path")
    return root


def validate_descriptor(value):
    if (
        not isinstance(value, dict)
        or set(value)
        != {"version", "system", "environmentShell", "components", "executables"}
        or type(value["version"]) is not int
        or value["version"] != 1
    ):
        raise SDKError("unsupported selected SDK descriptor")
    if not isinstance(value["system"], str) or not value["system"]:
        raise SDKError("missing selected SDK system")
    components = value["components"]
    if not isinstance(components, dict) or set(components) != {
        "swift",
        "swiftpm",
        "stdenv",
        "environment",
    }:
        raise SDKError("incomplete selected SDK components")
    for component in components.values():
        if not isinstance(component, dict) or set(component) != {"path", "recipe"}:
            raise SDKError("invalid selected SDK component")
        if (
            store_root(component["path"]) != component["path"]
            or store_root(component["recipe"]) != component["recipe"]
            or not component["recipe"].endswith(".drv")
        ):
            raise SDKError("SDK component lacks its concrete derivation recipe")
    commands = value["executables"]
    if not isinstance(commands, dict) or set(commands) != set(ROLES):
        raise SDKError("incomplete selected SDK executable tuple")
    for role in ROLES:
        component = components["swift" if role in ("swift", "swiftc") else "swiftpm"][
            "path"
        ]
        name = role if role in ("swift", "swiftc") else "swift-" + role
        if commands[role] != component + "/bin/" + name:
            raise SDKError("SDK executable disagrees with selected package: " + role)
    store_root(value["environmentShell"])
    return value


def validate_identity(value):
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "version",
            "descriptor",
            "closure",
            "executables",
            "environment",
            "capabilities",
            "versions",
        }
        or type(value["version"]) is not int
        or value["version"] != 1
    ):
        raise SDKError("invalid native SDK identity")
    descriptor = validate_descriptor(value["descriptor"])
    closure = value["closure"]
    if not isinstance(closure, dict) or not closure or len(closure) > 4096:
        raise SDKError("invalid native SDK closure")
    for path, nar_hash in closure.items():
        if (
            store_root(path) != path
            or not isinstance(nar_hash, str)
            or not HASH.fullmatch(nar_hash)
        ):
            raise SDKError("invalid native SDK closure identity")
    for component in descriptor["components"].values():
        if component["path"] not in closure:
            raise SDKError("native SDK closure omits a selected component")
    if store_root(descriptor["environmentShell"]) not in closure:
        raise SDKError("native SDK closure omits its environment shell")
    if not isinstance(value["executables"], dict) or set(value["executables"]) != set(
        ROLES
    ):
        raise SDKError("incomplete actual native executable tuple")
    for role, executable in value["executables"].items():
        if (
            not isinstance(executable, dict)
            or set(executable) != {"path", "resolved", "sha256"}
            or executable["path"] != descriptor["executables"][role]
            or store_root(executable["resolved"]) not in closure
            or not isinstance(executable["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", executable["sha256"])
        ):
            raise SDKError("invalid actual native executable: " + role)
    environment = value["environment"]
    if (
        not isinstance(environment, dict)
        or set(environment) != {"variables", "pathPrefix"}
        or not isinstance(environment["variables"], dict)
        or not all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in environment["variables"].items()
        )
        or not isinstance(environment["pathPrefix"], str)
        or not environment["pathPrefix"]
    ):
        raise SDKError("invalid selected SDK environment")
    if (
        environment["variables"].get("SWIFT_EXEC")
        != descriptor["executables"]["swiftc"]
    ):
        raise SDKError("SDK environment selects a different compiler")
    if not isinstance(value["capabilities"], dict) or set(value["capabilities"]) != {
        "package",
        "build",
        "test",
        "run",
    }:
        raise SDKError("incomplete native capabilities")
    for options in value["capabilities"].values():
        if (
            not isinstance(options, list)
            or not all(
                isinstance(option, str)
                and re.fullmatch(r"--[a-z0-9]+(?:-[a-z0-9]+)*", option)
                for option in options
            )
            or options != sorted(set(options))
        ):
            raise SDKError("invalid native option capabilities")
    if (
        not isinstance(value["versions"], dict)
        or set(value["versions"]) != {"compiler", "swiftpm"}
        or not all(
            isinstance(version, str) and 0 < len(version.encode("utf-8")) <= 4096
            for version in value["versions"].values()
        )
    ):
        raise SDKError("invalid actual native version output")
    return value


def load_descriptor(path):
    file = Path(path).resolve(strict=True)
    store_root(str(file))
    return validate_descriptor(json.loads(file.read_text()))


def check_bindings(descriptor):
    validate_descriptor(descriptor)
    names = {
        "swift": "SWIFTPM_PURIFIER_SWIFT",
        "swiftc": "SWIFT_EXEC",
        "package": "SWIFTPM_PURIFIER_SWIFT_PACKAGE",
        "build": "SWIFTPM_PURIFIER_SWIFT_BUILD",
        "test": "SWIFTPM_PURIFIER_SWIFT_TEST",
        "run": "SWIFTPM_PURIFIER_SWIFT_RUN",
    }
    for role, name in names.items():
        if os.environ.get(name) != descriptor["executables"][role]:
            raise SDKError("active native command differs from selected SDK: " + role)
    check_compiler_overrides(descriptor)


def check_compiler_overrides(descriptor):
    """Shared native guard, independent of CLI-only role variables."""
    validate_descriptor(descriptor)
    for name, expected in [
        ("SWIFT_EXEC_MANIFEST", descriptor["executables"]["swiftc"]),
        ("_SWIFT_EXEC_MANIFEST", descriptor["executables"]["swiftc"]),
        (
            "SWIFT_DRIVER_SWIFT_FRONTEND_EXEC",
            descriptor["components"]["swift"]["path"] + "/bin/swift-frontend",
        ),
    ]:
        override = os.environ.get(name)
        if override and Path(override).resolve() != Path(expected).resolve():
            raise SDKError(
                "native compiler override disagrees with selected SDK: " + name
            )


def check_build_bindings(descriptor):
    """Check the native commands an ordinary SwiftPM build hook will invoke."""
    check_compiler_overrides(descriptor)
    compiler = os.environ.get("SWIFT_EXEC")
    if (
        not compiler
        or Path(compiler).resolve()
        != Path(descriptor["executables"]["swiftc"]).resolve()
    ):
        raise SDKError("build compiler differs from selected SDK")
    for role, expected in descriptor["executables"].items():
        name = role if role in ("swift", "swiftc") else "swift-" + role
        actual = shutil.which(name)
        if actual is None or Path(actual).resolve() != Path(expected).resolve():
            raise SDKError("build PATH selects a different native command: " + name)


def check_active(identity):
    """Check every call, even when immutable SDK measurements are cached."""
    validate_identity(identity)
    check_bindings(identity["descriptor"])
    for name, expected in identity["environment"]["variables"].items():
        if os.environ.get(name) != expected:
            raise SDKError("active SDK environment differs: " + name)
    prefix = identity["environment"]["pathPrefix"]
    actual = os.environ.get("PATH", "")
    if actual != prefix and not actual.startswith(prefix + ":"):
        raise SDKError("active PATH differs from selected SDK environment")


def measure(
    descriptor, nix, options, verify_active=True, evidence=None, closure_paths=None
):
    validate_descriptor(descriptor)
    check_compiler_overrides(descriptor)
    temporary = (
        Path(evidence)
        if evidence is not None
        else Path(tempfile.mkdtemp(prefix="swiftpm-purifier-sdk-"))
    )
    if evidence is not None:
        temporary.mkdir()
    logs = temporary / "logs"
    logs.mkdir()
    receipt = {"commands": []}
    deadline = time.monotonic() + 300

    def save():
        (temporary / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    def run(label, arguments, env=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SDKError("native SDK identity deadline")
        row = {"label": label}
        receipt["commands"].append(row)
        result = bounded_native.run_recorded(
            arguments,
            record=row,
            logs=logs,
            save=save,
            cwd=temporary,
            env=env or os.environ,
            timeout=min(75, remaining),
            output_limit=4 * 1024 * 1024,
        )
        if result.returncode:
            raise SDKError(
                label + " failed: " + result.stderr.decode(errors="replace")[-2000:]
            )
        return result.stdout

    def sha(path):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while True:
                if time.monotonic() > deadline:
                    raise SDKError("native executable identity deadline")
                data = stream.read(1024 * 1024)
                if not data:
                    return digest.hexdigest()
                digest.update(data)

    try:
        roots = sorted(
            {component["path"] for component in descriptor["components"].values()}
            | {store_root(descriptor["environmentShell"])}
        )
        nix_command = [nix, "--extra-experimental-features", "nix-command"]
        if closure_paths is None:
            inventory = json.loads(
                run(
                    "closure-inventory",
                    [*nix_command, "path-info", "--recursive", "--json", *roots],
                )
            )
            if isinstance(inventory, list):
                inventory = {item["path"]: item for item in inventory}
        else:
            if (
                not isinstance(closure_paths, list)
                or not all(isinstance(path, str) for path in closure_paths)
                or len(closure_paths) != len(set(closure_paths))
            ):
                raise SDKError("invalid source-owned SDK closure graph")
            inventory = {path: {} for path in closure_paths}
        if not isinstance(inventory, dict) or not inventory or len(inventory) > 4096:
            raise SDKError("unexpected native SDK closure inventory")
        closure = {}
        for index, (path, metadata) in enumerate(sorted(inventory.items())):
            if store_root(path) != path:
                raise SDKError("unexpected closure path")
            measured = (
                run(
                    "physical-" + str(index),
                    [
                        *nix_command,
                        "--store",
                        "dummy://",
                        "hash",
                        "path",
                        "--type",
                        "sha256",
                        "--sri",
                        path,
                    ],
                )
                .decode()
                .strip()
            )
            if "narHash" in metadata and measured != metadata["narHash"]:
                raise SDKError(
                    "SDK physical NAR differs from its registered identity: " + path
                )
            closure[path] = measured
        executables = {}
        for role, logical in descriptor["executables"].items():
            actual = Path(logical).resolve(strict=True)
            if (
                store_root(str(actual)) not in closure
                or not actual.is_file()
                or not os.access(actual, os.X_OK)
            ):
                raise SDKError("native executable leaves selected SDK closure: " + role)
            executables[role] = {
                "path": logical,
                "resolved": str(actual),
                "sha256": sha(actual),
            }
        environment_file = Path(descriptor["components"]["environment"]["path"])
        names = sorted(
            set(
                re.findall(
                    r"^export ([A-Za-z_][A-Za-z0-9_]*)=",
                    environment_file.read_text(),
                    re.MULTILINE,
                )
            )
        )
        if "PATH" not in names or "SWIFT_EXEC" not in names:
            raise SDKError("selected environment lacks PATH or compiler binding")
        code = "import json,os,sys; print(json.dumps({name:os.environ.get(name) for name in json.loads(sys.argv[1])}))"
        raw = run(
            "source-owned-environment",
            [
                descriptor["environmentShell"],
                "--noprofile",
                "--norc",
                "-c",
                'source "$1"; exec "$2" -c "$3" "$4"',
                "selected-sdk-environment",
                str(environment_file),
                sys.executable,
                code,
                json.dumps(names),
            ],
            env={"PATH": "", "LC_ALL": "C"},
        )
        variables = json.loads(raw)
        prefix = variables.pop("PATH")
        if not all(isinstance(item, str) for item in variables.values()):
            raise SDKError("selected environment did not export its variables")
        if verify_active:
            if any(
                os.environ.get(name) != expected for name, expected in variables.items()
            ):
                raise SDKError("active compiler/environment differs from selected SDK")
            active_path = os.environ.get("PATH", "")
            if active_path != prefix and not active_path.startswith(prefix + ":"):
                raise SDKError("active PATH differs from selected SDK environment")
        capabilities = {
            role: sorted(
                options(
                    descriptor["executables"][role],
                    "resolve" if role == "package" else None,
                )
            )
            for role in ("package", "build", "test", "run")
        }
        versions = {
            "compiler": run(
                "actual-compiler-version",
                [descriptor["executables"]["swiftc"], "--version"],
            )
            .decode()
            .strip(),
            "swiftpm": run(
                "actual-swiftpm-version",
                [descriptor["executables"]["package"], "--version"],
            )
            .decode()
            .strip(),
        }
        identity = {
            "version": 1,
            "descriptor": descriptor,
            "closure": closure,
            "executables": executables,
            "environment": {"variables": variables, "pathPrefix": prefix},
            "capabilities": capabilities,
            "versions": versions,
        }
        validate_identity(identity)
        if verify_active:
            check_active(identity)
    except Exception as error:
        raise SDKError(
            f"native SDK verification failed; raw evidence retained at {temporary}: {error}"
        ) from error
    receipt["complete"] = True
    receipt["identitySha256"] = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    save()
    if evidence is None:
        shutil.rmtree(temporary)
    return identity
