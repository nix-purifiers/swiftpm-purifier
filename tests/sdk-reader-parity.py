# SPDX-License-Identifier: MIT
"""Python/Nix metadata parity over a physically measured SDK fixture.

Mutations exercise readers only; they never substitute a native SDK.
"""

import argparse
import copy
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
    sys.path.insert(0, str(root / "pkgs/cli"))
    import native_sdk

    original = json.loads(
        (root / "pkgs/cli/fixtures/native553/sdk-identity.json").read_text()
    )
    cases = [("measured", original, True)]

    def mutate(name, action):
        value = copy.deepcopy(original)
        action(value)
        cases.append((name, value, False))

    for field in original:
        mutate("missing-" + field, lambda value, field=field: value.pop(field))
    mutate("extra", lambda value: value.update(extra=True))
    mutate("bool-version", lambda value: value.update(version=True))
    mutate("float-version", lambda value: value.update(version=1.0))
    mutate("descriptor-null", lambda value: value.update(descriptor=None))
    mutate("closure-empty", lambda value: value.update(closure={}))
    mutate(
        "closure-missing-compiler",
        lambda value: value["closure"].pop(
            value["descriptor"]["components"]["swift"]["path"]
        ),
    )
    mutate(
        "recipe-missing",
        lambda value: value["descriptor"]["components"]["stdenv"].pop("recipe"),
    )
    mutate(
        "compiler-command",
        lambda value: value["descriptor"]["executables"].update(
            swiftc=value["descriptor"]["executables"]["swift"]
        ),
    )
    mutate(
        "environment-compiler",
        lambda value: value["environment"]["variables"].update(
            SWIFT_EXEC=value["descriptor"]["executables"]["swift"]
        ),
    )
    mutate("capability-type", lambda value: value["capabilities"].update(package=[{}]))
    mutate(
        "capability-duplicate",
        lambda value: value["capabilities"].update(
            package=["--cache-path", "--cache-path"]
        ),
    )
    mutate(
        "version-too-long", lambda value: value["versions"].update(compiler="x" * 4097)
    )
    mutate(
        "resolved-traversal",
        lambda value: value["executables"]["swiftc"].update(
            resolved=value["executables"]["swiftc"]["resolved"] + "/../swiftc"
        ),
    )
    args.output.mkdir()
    (args.output / "tmp").mkdir()
    receipt = {
        "complete": False,
        "scope": "SDK metadata reader parity only",
        "cases": [],
    }
    deadline = time.monotonic() + 90
    expression = 'let value = builtins.fromJSON (builtins.readFile (/. + builtins.getEnv "SDK_CASE")); in (import (/. + builtins.getEnv "SDK_READER") { identity = value; }).valid'
    for name, value, expected in cases:
        file = args.output / (name + ".json")
        file.write_text(json.dumps(value))
        try:
            native_sdk.validate_identity(value)
            python_ok = True
        except (native_sdk.SDKError, TypeError, KeyError):
            python_ok = False
        env = dict(
            os.environ,
            TMPDIR=str(args.output / "tmp"),
            SDK_CASE=str(file),
            SDK_READER=str(root / "nix/lib/sdk-contract.nix"),
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("parity deadline")
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            env=env,
            capture_output=True,
            timeout=min(15, remaining),
        )
        (args.output / (name + ".stderr")).write_bytes(result.stderr)
        nix_ok = result.returncode == 0 and json.loads(result.stdout) is True
        receipt["cases"].append(
            {
                "name": name,
                "expected": expected,
                "python": python_ok,
                "nix": nix_ok,
                "nixExit": result.returncode,
            }
        )
        (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        if python_ok != expected or nix_ok != expected:
            raise ValueError("reader disagreement: " + name)
    receipt["complete"] = True
    (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(str(len(cases)) + " SDK reader cases PASS")


if __name__ == "__main__":
    main()
