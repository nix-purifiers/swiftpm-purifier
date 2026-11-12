# SPDX-License-Identifier: MIT
"""Cross-reader contract checks; synthetic locks are not native support evidence."""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
source = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(source / "pkgs/cli"))
import pre_workspace as protocol
import bounded_native

output = args.output.absolute()
output.mkdir()
root = output / "project"
shutil.copytree(source / "tests/fixtures/pre-workspace-layout", root)
lockfile = root / "swiftpm-purifier.lock.json"
base = {
    "version": 7,
    "nativeProtocol": "pre-workspace-git",
    "package": "Layout",
    "rootDirectory": "project",
    "manifestHash": protocol.manifest_hash(root),
    "sourceLayoutHash": protocol.layout.source_layout(root)["hash"],
    "nativeLockFiles": {"Package.pins": None, "Package.resolved": None},
    "localDependencies": [],
    "binaryTargets": [],
    "pins": {
        "Leaf": {
            "name": "Leaf",
            "sourceLocation": "https://example.invalid/Leaf.git",
            "revision": "a" * 40,
            "version": "1.0.0",
            "hash": "sha256-" + "A" * 43 + "=",
            "gitHash": "sha256-" + "A" * 43 + "=",
        }
    },
    "products": [
        {"name": "Combo", "type": "library", "linkage": "static", "modules": ["One"]}
    ],
}
expression = """let
 root = /. + builtins.getEnv "PARITY_ROOT";
 file = root + "/swiftpm-purifier.lock.json";
 raw = builtins.readFile file;
 read = import (/. + builtins.getEnv "PARITY_READER") {
   src = root; lockFile = file; inherit raw;
   lock = builtins.fromJSON raw;
   packageResolved = if builtins.getEnv "PARITY_OVERRIDE" == "yes" then root + "/missing-native" else null;
 };
in { inherit (read) valid entries; }
"""
report = {
    "complete": False,
    "scope": "Synthetic portable7 production Python/Nix contract parity; no native/cold/support claim",
    "cases": [],
}


def save():
    (output / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")


def check(
    name, expected, change=lambda lock: None, raw_change=lambda raw: raw, override=False
):
    lock = copy.deepcopy(base)
    change(lock)
    lock["contentHash"] = protocol.content_hash(lock)
    raw = raw_change(json.dumps(lock, ensure_ascii=False).encode())
    lockfile.write_bytes(raw)
    try:
        protocol.validate_raw(raw, root, "" if override else None)
        python_ok = True
    except (protocol.StateError, ValueError):
        python_ok = False
    result = bounded_native.run(
        ["nix", "eval", "--impure", "--json", "--expr", expression],
        env=dict(
            os.environ,
            PARITY_ROOT=str(root),
            PARITY_READER=str(source / "nix/lib/pre-workspace-state.nix"),
            PARITY_OVERRIDE="yes" if override else "",
        ),
        timeout=30,
        output_limit=1024 * 1024,
    )
    (output / (name + ".stdout")).write_bytes(result.stdout)
    (output / (name + ".stderr")).write_bytes(result.stderr)
    nix_ok = result.returncode == 0 and json.loads(result.stdout)["valid"]
    report["cases"].append(
        {"name": name, "expected": expected, "python": python_ok, "nix": nix_ok}
    )
    save()
    assert python_ok == nix_ok == expected, report["cases"][-1]


check("valid-dual-nars", True)
check("valid-no-dependencies", True, lambda l: l.update(pins={}))
check("native-sdk-forbidden", False, lambda l: l.update(nativeSDK=None))
check("workspace-state-forbidden", False, lambda l: l.update(workspaceStateVersion=1))
check("wrong-protocol", False, lambda l: l.update(nativeProtocol="workspace"))
check("wrong-version", False, lambda l: l.update(version=6))
check("boolean-version", False, lambda l: l.update(version=True))
check("bad-source-hash", False, lambda l: l["pins"]["Leaf"].update(hash="sha256-bad"))
check(
    "bad-metadata-hash", False, lambda l: l["pins"]["Leaf"].update(gitHash="sha256-bad")
)
check("unsafe-native-name", False, lambda l: l["pins"]["Leaf"].update(name="../Leaf"))
check(
    "local-origin",
    False,
    lambda l: l["pins"]["Leaf"].update(sourceLocation="/tmp/Leaf"),
)
check("bad-version", False, lambda l: l["pins"]["Leaf"].update(version="1.01.0"))
check("bad-revision", False, lambda l: l["pins"]["Leaf"].update(revision="main"))
check("extra-pin-field", False, lambda l: l["pins"]["Leaf"].update(extra=True))
check(
    "mirror",
    True,
    lambda l: l["pins"]["Leaf"].update(url="https://mirror.invalid/Leaf.git"),
)
check("bad-mirror", False, lambda l: l["pins"]["Leaf"].update(url="/tmp/Leaf"))
check("unsupported-local", False, lambda l: l.update(localDependencies=[{}]))
check("unsupported-binary", False, lambda l: l.update(binaryTargets=[{}]))
check("unsafe-module", False, lambda l: l["products"][0].update(modules=["../One"]))
check(
    "duplicate-module", False, lambda l: l["products"][0].update(modules=["One", "One"])
)
check("invalid-linkage", False, lambda l: l["products"][0].update(linkage="automatic"))
check("layout-mismatch", False, lambda l: l.update(sourceLayoutHash="f" * 64))
check("manifest-mismatch", False, lambda l: l.update(manifestHash="f" * 64))
check("native-override", False, override=True)
check("duplicate-json", False, raw_change=lambda raw: raw[:-1] + b',"version":7}')
check(
    "duplicate-escaped-json",
    False,
    raw_change=lambda raw: raw[:-1] + b',"vers\\u0069on":7}',
)
for filename in ("Package.pins", "Package.resolved"):
    (root / filename).write_text("{}")
    check("present-" + filename.replace(".", "-"), False)
    (root / filename).unlink()
(root / ".build").mkdir()
(root / ".build/workspace-state.json").write_text("{}")
check("mixed-workspace", False)
(root / ".build/workspace-state.json").unlink()
(root / ".build/unknown.json").write_text("{}")
check("unknown-native-metadata", False)
(root / ".build/unknown.json").unlink()
check("empty-build-dir", True)
(root / "Sources/One/One.swift").write_text("// content edit\n")
check("content-edit-layout-stable", True)
(root / "Sources/Added").mkdir()
check("empty-directory-addition-stale", False)
report["complete"] = True
report["readerSha256"] = {
    name: hashlib.sha256((source / name).read_bytes()).hexdigest()
    for name in ("pkgs/cli/pre_workspace.py", "nix/lib/pre-workspace-state.nix")
}
save()
print("PASS", len(report["cases"]), "production reader parity cases")
