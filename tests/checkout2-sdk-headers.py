# SPDX-License-Identifier: MIT
"""Exercise original test-SDK headers and arguments in an owned modern sysroot."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pkgs/cli"))
import bounded_native


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-prefix", type=Path, required=True)
    parser.add_argument("--modern-headers", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sdk-epoch", choices=("legacy", "swift563"), default="legacy")
    args = parser.parse_args()
    sdk = args.runtime_prefix.resolve(strict=True)
    headers = args.modern_headers.resolve(strict=True)
    limits = (headers / "limits.h").read_bytes()
    assert b"__GLIBC_USE" in limits
    work = args.output.resolve()
    work.mkdir()
    sysroot = work / "owned-sysroot"
    (sysroot / "usr").mkdir(parents=True)
    (sysroot / "usr/include").symlink_to(headers, target_is_directory=True)
    receipt = {
        "complete": False,
        "scope": "Original SDK packaging controls only; real supplied modern headers, owned sysroot, no header/macro/compiler/version edits",
        "sdk": str(sdk),
        "sdkEpoch": args.sdk_epoch,
        "modernHeaders": str(headers),
        "modernLimitsSHA256": hashlib.sha256(limits).hexdigest(),
        "commands": [],
    }

    def save():
        (work / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    for role in ("argv", "interpreter", "swift-frontend", "swiftc-frontend"):
        arena = work / role
        arena.mkdir()
        for name in ("logs", "home", "tmp"):
            (arena / name).mkdir()
        script = arena / "control.swift"
        script.write_text(
            'print(CommandLine.arguments.joined(separator: "|"))\n'
            if role == "argv"
            else 'import Foundation\nprint("HEADER_OK")\n'
        )
        env = dict(os.environ)
        for name in (
            "CC",
            "CXX",
            "CPATH",
            "C_INCLUDE_PATH",
            "CPLUS_INCLUDE_PATH",
            "SWIFT_EXEC",
            "SWIFT_EXEC_MANIFEST",
            "_SWIFT_EXEC_MANIFEST",
            "LD_LIBRARY_PATH",
            "LD_PRELOAD",
            "LIBRARY_PATH",
        ):
            env.pop(name, None)
        env.update(
            HOME=str(arena / "home"),
            TMPDIR=str(arena / "tmp"),
            XDG_CACHE_HOME=str(arena / "cache"),
            PATH=str(sdk / "bin") + ":" + env.get("PATH", ""),
        )
        interpreter = (
            [sdk / "bin/swift"]
            if args.sdk_epoch == "swift563"
            else [sdk / "bin/swiftc", "--driver-mode=swift"]
        )
        if role == "argv":
            command = [
                *interpreter,
                "-module-cache-path",
                arena / "module-cache",
                script,
                "OWNED ARG WITH SPACE",
            ]
        elif role == "interpreter":
            command = [
                *interpreter,
                "-module-cache-path",
                arena / "module-cache",
                "-sdk",
                sysroot,
                script,
            ]
        else:
            command = [
                sdk / "bin" / ("swift" if role == "swift-frontend" else "swiftc"),
                "-frontend",
                "-typecheck",
                "-module-cache-path",
                arena / "module-cache",
                "-sdk",
                sysroot,
                script,
            ]
        record = {
            "label": role,
            "scriptSHA256": hashlib.sha256(script.read_bytes()).hexdigest(),
        }
        receipt["commands"].append(record)
        result = bounded_native.run_recorded(
            list(map(str, command)),
            record=record,
            logs=arena / "logs",
            save=save,
            cwd=arena,
            env=env,
            timeout=30,
            output_limit=1024 * 1024,
        )
        assert result.returncode == 0
        if role == "argv":
            assert result.stdout == (str(script) + "|OWNED ARG WITH SPACE\n").encode()
        elif role == "interpreter":
            assert result.stdout == b"HEADER_OK\n"
        record["assertionsPassed"] = True
        save()
    receipt["complete"] = True
    save()


if __name__ == "__main__":
    main()
