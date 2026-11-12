# SPDX-License-Identifier: MIT
"""Observe existing local Swift tools without selecting or realizing a Nix SDK.

Observations are diagnostic data, never an immutable SDK or ABI attestation.
The caller supplies PATH captured before adding purifier management tools.
"""

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sys

import bounded_native


class HostRuntimeError(ValueError):
    pass


NAMES = {
    "swift": "swift",
    "swiftc": "swiftc",
    "package": "swift-package",
    "build": "swift-build",
    "test": "swift-test",
    "run": "swift-run",
}


def executable(value, host_path):
    """Keep the lexical invocation path: swiftc may be a dispatch symlink."""
    found = shutil.which(value, path=host_path)
    if found is None:
        return None
    return os.path.abspath(found)


def discover(host_path, environment):
    if not isinstance(host_path, str):
        raise HostRuntimeError("original host PATH was not captured")
    driver = executable("swift", host_path)
    if driver is None:
        raise HostRuntimeError(
            "no Swift runtime on the original host PATH; install or select your runtime"
        )
    # A toolchain commonly exposes Swift through a symlink. Its siblings are
    # the commands that the native driver itself locates before searching PATH.
    families = [Path(driver).resolve().parent, Path(driver).parent]
    commands = {"swift": driver}
    for role, name in NAMES.items():
        if role == "swift":
            continue
        explicit = environment.get("SWIFT_EXEC") if role == "swiftc" else None
        if explicit:
            selected = executable(explicit, host_path)
        else:
            selected = next(
                (
                    str(directory / name)
                    for directory in families
                    if os.access(directory / name, os.X_OK)
                    and (directory / name).is_file()
                ),
                None,
            )
            selected = selected or executable(name, host_path)
        if selected is not None:
            commands[role] = selected
    overrides = {
        name: environment[name]
        for name in [
            "SWIFT_EXEC",
            "SWIFT_EXEC_MANIFEST",
            "_SWIFT_EXEC_MANIFEST",
            "SWIFT_DRIVER_SWIFT_FRONTEND_EXEC",
        ]
        if name in environment
    }
    return {"commands": commands, "overrides": overrides}


def observe(runtime, environment, evidence, options=None):
    """Bound actual native probes; do not compare a release against a table."""
    evidence = Path(evidence)
    evidence.mkdir()
    (evidence / "logs").mkdir()
    receipt = {
        "complete": False,
        "scope": "Host runtime observation only; no SDK/ABI attestation",
        "commands": [],
    }

    def save():
        (evidence / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    def version(role):
        row = {"label": role + "-version"}
        receipt["commands"].append(row)
        result = bounded_native.run_recorded(
            (
                [runtime["commands"]["swift"], "package"]
                if role == "package"
                else [runtime["commands"].get(role, runtime["commands"]["swift"])]
            )
            + ["--version"],
            record=row,
            logs=evidence / "logs",
            save=save,
            cwd=evidence,
            env=environment,
            timeout=20,
            output_limit=1024 * 1024,
        )
        if result.returncode:
            raise HostRuntimeError(
                role
                + " version probe failed: "
                + result.stderr.decode(errors="replace")[-2000:]
            )
        text = result.stdout.decode().strip()
        if not text or len(text.encode()) > 4096:
            raise HostRuntimeError("invalid actual " + role + " version output")
        return text

    compiler = version("swiftc")
    manager = version("package")
    observed = {
        "version": 1,
        "commands": {
            role: {"path": path, "resolved": str(Path(path).resolve())}
            for role, path in runtime["commands"].items()
        },
        "overrides": runtime["overrides"],
        "versions": {"compiler": compiler, "swiftpm": manager},
        "host": {"platform": sys.platform, "machine": platform.machine()},
        "compilerTarget": next(
            (
                line.removeprefix("Target:").strip()
                for line in compiler.splitlines()
                if line.startswith("Target:")
            ),
            None,
        ),
    }
    if options is not None:
        roles = ["package"] + [
            role for role in ("build", "test", "run") if role in runtime["commands"]
        ]
        observed["capabilities"] = {
            role: sorted(
                options(
                    (runtime["commands"]["swift"], role),
                    "resolve" if role == "package" else None,
                )
            )
            for role in roles
        }
    receipt["complete"] = True
    receipt["observationSha256"] = hashlib.sha256(
        json.dumps(observed, sort_keys=True).encode()
    ).hexdigest()
    save()
    return observed
