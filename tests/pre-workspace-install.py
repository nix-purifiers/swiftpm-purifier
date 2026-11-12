# SPDX-License-Identifier: MIT
"""Exercise real verified-store copies, ownership guards, and rollback."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument("--tree", type=Path, required=True)
parser.add_argument("--nix-hash", required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
source = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(source / "pkgs/cli"))
import pre_workspace_install as install

output = args.output.absolute()
output.mkdir()
records = json.loads((args.tree / "pre-workspace-sources.json").read_text())
assert records, "real Git fixture required"
root = output / "project"
root.mkdir()
(root / "Package.swift").write_text(
    'import PackageDescription\nlet package = Package(name: "Example")\n'
)
state = root / ".build/swiftpm-purifier" / install.STATE
report = {
    "complete": False,
    "scope": "Private production install component with actual verified-store Git trees; not CLI/native replay/cold support evidence",
    "cases": [],
}


def record(name):
    report["cases"].append(name)
    (output / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")


def baseline():
    return install.hash_path(root / "Packages", args.nix_hash), state.read_bytes()


def rejects(name, operation, unchanged=True):
    before = baseline() if unchanged else None
    try:
        operation()
    except (install.protocol.StateError, OSError):
        pass
    else:
        raise AssertionError("accepted " + name)
    if unchanged:
        assert baseline() == before, name
    record(name)


assert install.install(root, records, args.nix_hash)
for entry in records:
    checkout = root / entry["path"]
    assert not checkout.is_symlink() and (checkout / ".git").is_dir()
    assert install.hash_path(checkout, args.nix_hash) == install.hash_path(
        Path(entry["source"]), args.nix_hash
    )
record("initial-full-owned-byte-identical-copies")
assert not install.install(root, records, args.nix_hash)
record("idempotent-unchanged-bytes")

original_replace = install.os.replace
failed = False


def failing_replace(src, dst):
    global failed
    if Path(dst) == state and not failed:
        failed = True
        raise OSError("injected ownership replacement failure after cache replacement")
    return original_replace(src, dst)


with patch.object(install.os, "replace", failing_replace):
    rejects(
        "rollback-after-replacing-packages",
        lambda: install.install(root, [], args.nix_hash),
    )
assert failed
assert not list(output.glob(".swiftpm-purifier-install-*"))
record("rollback-cleans-transaction")
checkout = root / records[0]["path"]
extra = checkout / "user-edit.txt"
extra.write_text("preserve me\n")
rejects("edited-owned-checkout", lambda: install.install(root, [], args.nix_hash))
extra.unlink()
extra = root / "Packages/unowned"
extra.mkdir()
rejects("extra-unowned-native-path", lambda: install.install(root, [], args.nix_hash))
extra.rmdir()
missing = checkout.with_name(checkout.name + ".moved")
checkout.rename(missing)
rejects("missing-owned-checkout", lambda: install.install(root, [], args.nix_hash))
missing.rename(checkout)
(root / "Package.pins").write_text("{}")
rejects(
    "mixed-native-pins-before-write", lambda: install.install(root, [], args.nix_hash)
)
(root / "Package.pins").unlink()
bad = [dict(records[0], path="Packages/../escape")]
rejects("unsafe-record-before-write", lambda: install.install(root, bad, args.nix_hash))
rejects(
    "duplicate-record-before-write",
    lambda: install.install(root, records + records, args.nix_hash),
)
assert install.install(root, [], args.nix_hash)
assert list((root / "Packages").iterdir()) == []
record("owned-dependency-removal")
assert install.install(root, records, args.nix_hash)
record("owned-dependency-readdition")
relocated = output / "relocated"
root.rename(relocated)
root = relocated
state = root / ".build/swiftpm-purifier" / install.STATE
assert not install.install(root, records, args.nix_hash)
record("relocated-ownership-idempotent")
unowned = output / "unowned-project"
unowned.mkdir()
(unowned / "Packages").mkdir()
(unowned / "Packages/user-data").write_text("keep")
rejects(
    "unowned-cache-before-write",
    lambda: install.install(unowned, records, args.nix_hash),
    unchanged=False,
)
assert (unowned / "Packages/user-data").read_text() == "keep" and not (
    unowned / ".build"
).exists()
report["complete"] = True
report["sourceSha256"] = hashlib.sha256(
    (source / "pkgs/cli/pre_workspace_install.py").read_bytes()
).hexdigest()
record("unowned-input-bytes-preserved")
print("PASS", len(report["cases"]), "owned install cases")
