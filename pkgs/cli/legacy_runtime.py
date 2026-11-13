# SPDX-License-Identifier: MIT
"""Observe dependency-state-2 metadata without resolving in the caller."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

import bounded_native
import legacy_manifest
import native_sdk


class ObservationError(ValueError):
    pass


def source_fingerprint(root):
    """Fingerprint precisely the source geometry copied by copy_package."""
    digest = hashlib.sha256()
    count = 0
    for directory, directories, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        ignored = {".build", "Packages", ".git"} if parent == root else {".build"}
        directories[:] = sorted(n for n in directories if n not in ignored)
        names = sorted(set(directories + files) - ignored)
        for path in [parent, *(parent / name for name in names)]:
            info = path.lstat()
            if path != parent and stat.S_ISDIR(info.st_mode):
                continue
            row = [str(path.relative_to(root)), stat.S_IMODE(info.st_mode)]
            if stat.S_ISLNK(info.st_mode):
                row += ["symlink", os.readlink(path)]
            elif stat.S_ISDIR(info.st_mode):
                row += ["directory"]
            elif stat.S_ISREG(info.st_mode):
                content = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        content.update(block)
                row += ["regular", content.hexdigest()]
            else:
                raise ObservationError("unsupported source entry in native observation")
            digest.update(json.dumps(row, ensure_ascii=False).encode() + b"\n")
            count += 1
    return {"sha256": digest.hexdigest(), "entries": count}


def file_record(path):
    if not os.path.lexists(path):
        return None
    info = path.lstat()
    result = {
        "device": info.st_dev,
        "inode": info.st_ino,
        "mode": info.st_mode,
        "mtimeNs": info.st_mtime_ns,
    }
    if path.is_file():
        result["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    if path.is_symlink():
        result["target"] = os.readlink(path)
    return result


def basis(api, root):
    return {
        "source": source_fingerprint(root),
        "portable": file_record(api.lock_path(root)),
        "native": file_record(api.resolved_path(root)),
        "cache": {
            name: file_record(root / name)
            for name in (
                ".build",
                ".build/dependencies-state.json",
                ".build/workspace-state.json",
                ".build/repositories/checkouts-state.json",
            )
        },
    }


def workspace(root):
    """A caller-controlled TMPDIR must not put the capsule inside its source."""
    for name in dict.fromkeys((tempfile.gettempdir(), "/tmp", "/var/tmp")):
        parent = Path(name).resolve()
        if parent == root or root in parent.parents:
            continue
        try:
            return Path(
                tempfile.mkdtemp(prefix="swiftpm-purifier-legacy-runtime-", dir=parent)
            )
        except OSError:
            continue
    raise ObservationError("no private temporary directory outside the package")


def state_index(value):
    if not isinstance(value, dict) or value.get("version") != 2:
        raise ObservationError("native observation changed the checkout protocol")
    dependencies = value.get("object", {}).get("dependencies")
    if not isinstance(dependencies, list):
        raise ObservationError("invalid native observation graph")
    result = {}
    for dependency in dependencies:
        identity = dependency["packageRef"]["identity"]
        if identity in result:
            raise ObservationError("duplicate native observation identity")
        result[identity] = dependency
    return result


def validate(api, root, lock):
    """Use the existing portable graph and the actual selected native runtime.

    SwiftPM 4.1 loads its graph before printing dump-package JSON. Resolving
    inside a cold caller both mutates its cache and prefixes JSON with progress.
    An owned edited graph avoids both effects without relaxing JSON parsing.
    """
    if lock.get("version") != 4 or lock.get("workspaceStateVersion") != 2:
        raise ObservationError("native capsule requires portable 4 / checkout state 2")
    if not api.is_current(root, lock):
        raise ObservationError(
            "native observation requires a current lock; relock first"
        )
    original = basis(api, root)
    pins = api.resolved_path(root)
    native_bytes = pins.read_bytes() if pins.exists() else None
    work = workspace(root)
    logs = work / "logs"
    logs.mkdir()
    receipt = {
        "complete": False,
        "scope": "Owned format4/state2 native metadata observation; no SDK attestation",
        "commands": [],
        "sourceNARJoins": [],
        "callerBefore": original,
    }

    def save():
        (work / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    def command(label, argv):
        row = {"label": label}
        receipt["commands"].append(row)
        result = bounded_native.run_recorded(
            argv,
            record=row,
            logs=logs,
            save=save,
            cwd=stage,
            env=environment,
            timeout=90,
            output_limit=2 * 1024 * 1024,
        )
        if result.returncode:
            raise ObservationError(
                label + " failed: " + result.stderr.decode(errors="replace")[-2000:]
            )
        return result.stdout

    save()
    try:
        stage = work / "packages" / root.name
        stage.parent.mkdir()
        api.copy_package(root, stage)
        if source_fingerprint(stage) != original["source"]:
            raise ObservationError(
                "package source changed while making its native capsule"
            )
        stage.chmod(stat.S_IMODE(stage.stat().st_mode) | 0o700)
        for name in ("Package.resolved", "Package.pins"):
            path = stage / name
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            elif os.path.lexists(path):
                path.unlink()
        if native_bytes is not None:
            (stage / "Package.resolved").write_bytes(native_bytes)
            (logs / "projected-native-pins").write_bytes(native_bytes)
        receipt["nativePinsPresent"] = native_bytes is not None
        receipt["capsuleRoot"] = str(stage)
        for name in ("tmp", "cache"):
            (work / name).mkdir()
        environment = dict(
            os.environ,
            TMPDIR=str(work / "tmp"),
            CLANG_MODULE_CACHE_PATH=str(work / "cache"),
            XDG_CACHE_HOME=str(work / "cache"),
            # Native SwiftPM checks this before its --build-path option.
            SWIFT_BUILD_PATH=str(stage / ".build"),
        )
        tree = Path(
            api.nix_build(
                f"""(pkgs.swiftpm-purifier.projectData {{
              src = /. + builtins.getEnv "SWIFTPM_PURIFIER_ROOT";
              platform = builtins.currentSystem;
              {api.nix_lock_arguments(root)}
            }}).tree""",
                api.nix_lock_environment(root),
                out_link=work / "tree",
            )
        ).resolve(strict=True)
        receipt["tree"] = str(tree)
        expected_names = {pin["name"] for pin in lock["pins"].values()}
        actual_names = (
            {path.name for path in (tree / "Packages").iterdir()}
            if (tree / "Packages").exists()
            else set()
        )
        if actual_names != expected_names:
            raise ObservationError("native capsule graph disagrees with locked sources")
        (stage / "Packages").mkdir()
        for index, (identity, pin) in enumerate(lock["pins"].items()):
            link = tree / "Packages" / pin["name"]
            if not link.is_symlink():
                raise ObservationError("native capsule source is not an immutable link")
            target = link.resolve(strict=True)
            if native_sdk.store_root(str(target)) != str(target) or not target.is_dir():
                raise ObservationError(
                    "native capsule source is not an immutable store root"
                )
            actual = (
                command(
                    "source-" + str(index),
                    [api.tool("nix"), "hash", "path", str(target)],
                )
                .decode()
                .strip()
            )
            if actual != pin["hash"]:
                raise ObservationError(
                    "native capsule source hash differs from its pin"
                )
            (stage / "Packages" / pin["name"]).symlink_to(target)
            receipt["sourceNARJoins"].append(
                {"identity": identity, "path": str(target), "hash": actual}
            )
        state = (
            (tree / "workspace-state.json.in")
            .read_text()
            .replace(api.ROOT_PLACEHOLDER, str(stage))
        )
        expected_graph = state_index(json.loads(state))
        (logs / "projected-state.json").write_text(state)
        for scratch in api.SCRATCH_DIRECTORIES:
            directory = stage / scratch
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "dependencies-state.json").write_text(state)
        capsule_before = source_fingerprint(stage)
        receipt["capsuleBefore"] = capsule_before
        save()
        raw = command(
            "native-dump-package",
            [
                *api.native_command("swift-package"),
                "--build-path",
                str(stage / ".build"),
                "dump-package",
            ],
        )
        description = legacy_manifest.json_value(raw)
        capsule_after = source_fingerprint(stage)
        receipt["capsuleAfter"] = capsule_after
        observed_state = (stage / ".build/dependencies-state.json").read_bytes()
        (logs / "observed-state.json").write_bytes(observed_state)
        if (stage / "Package.resolved").is_file():
            (logs / "observed-native-pins").write_bytes(
                (stage / "Package.resolved").read_bytes()
            )
        save()
        if capsule_after != capsule_before:
            raise ObservationError("package source changed during native observation")
        actual_products, name = api.product_description(description)
        if name != lock["package"] or sorted(
            actual_products, key=lambda p: p["name"]
        ) != sorted(lock["products"], key=lambda p: p["name"]):
            raise ObservationError(
                "selected native manifest disagrees with locked products"
            )
        if (
            state_index(json.loads(observed_state)) != expected_graph
            or (stage / ".build/workspace-state.json").exists()
        ):
            raise ObservationError("selected native runtime changed the locked graph")
        after = basis(api, root)
        if after != original or not api.is_current(root, lock):
            raise ObservationError("caller inputs changed during native observation")
        receipt.update(
            complete=True, callerAfter=after, package=name, products=actual_products
        )
        save()
    except Exception as error:
        save()
        raise ObservationError(
            f"native metadata observation failed; evidence at {work}: {error}"
        ) from error
    else:
        shutil.rmtree(work / "packages")
        (work / "tree").unlink(missing_ok=True)
        shutil.rmtree(work / "tmp")
        shutil.rmtree(work / "cache")
