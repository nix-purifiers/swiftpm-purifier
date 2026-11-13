# SPDX-License-Identifier: MIT
"""Record an original native minimum-tools diagnostic and its actual status."""

import argparse
import hashlib
import json
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pkgs/cli"))
import bounded_native


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-prefix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--sdk-epoch", choices=("swift503", "swift515"), default="swift503"
    )
    args = parser.parse_args()
    required, installed = {"swift503": ("5.1", "5.0.0"), "swift515": ("5.2", "5.1.0")}[
        args.sdk_epoch
    ]
    work = args.output.resolve()
    work.mkdir()
    (work / "logs").mkdir()
    root = work / "project"
    root.mkdir()
    manifest = root / "Package.swift"
    manifest.write_text(
        f"// swift-tools-version:{required}\nimport PackageDescription\n"
        'let package = Package(name: "NewerTools")\n'
    )
    receipt = {
        "complete": False,
        "scope": f"Original SwiftPM {installed} native tools-version diagnostic/status only; no purifier override",
        "manifestSHA256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "commands": [],
    }

    def save():
        (work / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    record = {"label": "native-newer-tools"}
    receipt["commands"].append(record)
    result = bounded_native.run_recorded(
        [str(args.runtime_prefix.resolve() / "bin/swift-package"), "dump-package"],
        record=record,
        logs=work / "logs",
        save=save,
        cwd=root,
        timeout=30,
        output_limit=1024 * 1024,
    )
    assert result.returncode != 0
    assert f"using Swift tools version {required}.0".encode() in result.stderr
    assert f"installed version is {installed}".encode() in result.stderr
    # The observed original native SDK can diagnose this and then SIGILL.
    # Preserve that distinction rather than presenting it as clean exit 1.
    receipt.update(
        complete=True,
        nativeDiagnosticObserved=True,
        cleanErrorExit=result.returncode > 0,
        signal=signal.Signals(-result.returncode).name
        if result.returncode < 0
        else None,
    )
    save()


if __name__ == "__main__":
    main()
