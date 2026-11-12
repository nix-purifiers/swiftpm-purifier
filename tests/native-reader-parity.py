"""Strict Python/Nix reader parity, using original Swift553 schema bytes.

This tests readers only; no native SDK or network command is substituted.
"""

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    module_path = root / "pkgs/cli/swiftpm_purifier.py"
    sys.path.insert(0, str(module_path.parent))
    spec = importlib.util.spec_from_file_location("swiftpm_purifier", module_path)
    sp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sp)
    sp.LOCK_OVERRIDE = None
    sp.TOOL_LOCK_OVERRIDE = None
    fixture = root / "pkgs/cli/fixtures/native553"
    native = json.loads((fixture / "Package.resolved").read_text())
    workspace = json.loads((fixture / "workspace-state.json").read_text())
    dependency = workspace["object"]["dependencies"][0]
    identity = dependency["packageRef"]["identity"]
    source = native["object"]["pins"][0]
    locked = {
        identity: {
            "name": source["package"],
            "sourceLocation": source["repositoryURL"],
            "revision": source["state"]["revision"],
            "hash": "sha256-" + "A" * 43 + "=",
        }
    }
    cases = [("native-v1", native, locked, True)]

    def mutate(name, change, expected=False):
        value, pins = copy.deepcopy(native), copy.deepcopy(locked)
        change(value, pins)
        cases.append((name, value, pins, expected))

    mutate(
        "missing-null-branch",
        lambda value, pins: value["object"]["pins"][0]["state"].pop("branch"),
        True,
    )
    mutate(
        "missing-native-identity",
        lambda value, pins: pins[identity].pop("sourceLocation"),
    )
    mutate(
        "ambiguous-native-identity",
        lambda value, pins: pins.update(other=copy.deepcopy(pins[identity])),
    )
    mutate(
        "package-name-mismatch",
        lambda value, pins: value["object"]["pins"][0].update(package="Other"),
    )
    mutate(
        "duplicate-pin",
        lambda value, pins: value["object"]["pins"].append(copy.deepcopy(source)),
    )
    mutate(
        "unknown-pin-field",
        lambda value, pins: value["object"]["pins"][0].update(extra=True),
    )
    mutate(
        "empty-package",
        lambda value, pins: value["object"]["pins"][0].update(package=""),
    )
    mutate(
        "nonstring-location",
        lambda value, pins: value["object"]["pins"][0].update(repositoryURL=17),
    )
    mutate("missing-state", lambda value, pins: value["object"]["pins"][0].pop("state"))
    mutate(
        "unknown-state-field",
        lambda value, pins: value["object"]["pins"][0]["state"].update(extra=True),
    )
    mutate(
        "short-revision",
        lambda value, pins: value["object"]["pins"][0]["state"].update(revision="abc"),
    )
    mutate(
        "nonstring-revision",
        lambda value, pins: value["object"]["pins"][0]["state"].update(revision=True),
    )
    mutate(
        "branch-and-version",
        lambda value, pins: value["object"]["pins"][0]["state"].update(branch="main"),
    )
    mutate(
        "nonstring-version",
        lambda value, pins: value["object"]["pins"][0]["state"].update(version=[]),
    )
    mutate("boolean-format", lambda value, pins: value.update(version=True))
    mutate("float-format", lambda value, pins: value.update(version=1.0))
    mutate("future-format", lambda value, pins: value.update(version=999))
    mutate("invalid-pins", lambda value, pins: value["object"].update(pins={}))
    mutate("invalid-object", lambda value, pins: value.update(object=[]))

    args.output.mkdir()
    (args.output / "tmp").mkdir()
    deadline = time.monotonic() + 90
    receipt = {
        "complete": False,
        "scope": "Reader behavior parity only",
        "cases": [],
        "harnessSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    expression = """let
      root = /. + builtins.getEnv "SWIFTPM_READER_CASE";
      reader = /. + builtins.getEnv "SWIFTPM_READER_LIBRARY";
      result = import reader { src = root; lockFile = root + "/swiftpm-purifier.lock.json"; };
    in { problems = result.lock.problems; workspace = result.workspaceState; entries = result.lock.entries; }"""
    cases = [
        (f"{name}-workspace{version}", value, pins, expected, version)
        for name, value, pins, expected in cases
        for version in (4, 7)
    ]
    for name, value, pins, expected, state_version in cases:
        work = args.output / name
        work.mkdir()
        (work / "Package.swift").write_text("// swift-tools-version:5.5\n")
        (work / "Package.resolved").write_text(json.dumps(value))
        lock = {
            "version": sp.LEGACY_LOCK_VERSION,
            "package": "Root",
            "rootDirectory": work.name,
            "manifestHash": sp.manifest_hash(work),
            "products": [],
            "workspaceStateVersion": state_version,
            "pins": pins,
            "binaryTargets": [],
            "localDependencies": [],
        }
        lock["contentHash"] = sp.content_hash(lock)
        (work / sp.LOCK_NAME).write_text(json.dumps(lock))
        python_error = None
        try:
            python_ok = sp.is_current(work, lock)
        except (sp.PurifierError, KeyError, TypeError, AttributeError) as error:
            python_ok = False
            python_error = str(error)
        env = dict(
            os.environ,
            TMPDIR=str(args.output / "tmp"),
            XDG_CACHE_HOME=str(args.output / "cache"),
            SWIFTPM_READER_CASE=str(work),
            SWIFTPM_READER_LIBRARY=str(root / "nix/lib/lock.nix"),
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("reader parity deadline")
        command = ["nix", "eval", "--impure", "--json", "--expr", expression]
        result = subprocess.run(
            command, env=env, capture_output=True, timeout=min(15, remaining)
        )
        (work / "nix.stdout").write_bytes(result.stdout)
        (work / "nix.stderr").write_bytes(result.stderr)
        nix_result = json.loads(result.stdout) if result.returncode == 0 else None
        nix_ok = nix_result is not None and nix_result["problems"] == []
        row = {
            "case": name,
            "expected": expected,
            "pythonAccepted": python_ok,
            "pythonError": python_error,
            "nixAccepted": nix_ok,
            "nixExit": result.returncode,
            "nixStdoutSha256": hashlib.sha256(result.stdout).hexdigest(),
            "nixStderrSha256": hashlib.sha256(result.stderr).hexdigest(),
        }
        receipt["cases"].append(row)
        (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        if not (python_ok == nix_ok == expected):
            raise ValueError(
                "reader disagreement: "
                + name
                + "\n"
                + result.stderr.decode(errors="replace")
            )
        if expected:
            identities = {pin["sourceLocation"]: key for key, pin in pins.items()}
            normalized = sp.resolved_pins(work / "Package.resolved", identities)
            records = [
                sp.record_of(pin, pins[pin["identity"]]["name"], state_version)
                for pin in normalized
            ]
            assert nix_result["workspace"]["object"]["dependencies"] == records
            assert ("prebuilts" in nix_result["workspace"]["object"]) == (
                state_version == 7
            )
    receipt["complete"] = True
    (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"complete": True, "cases": len(cases)}))


if __name__ == "__main__":
    main()
