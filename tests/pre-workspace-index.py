# SPDX-License-Identifier: MIT
"""Real Git index materialization, read-only ownership guards and stat refresh."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--tree", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
source = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(source / "pkgs/cli"))
import bounded_native
import pre_workspace as protocol
import pre_workspace_git as metadata
import pre_workspace_install as installer

work = args.output.absolute()
work.mkdir()
(work / "logs").mkdir()
git, nix_hash = shutil.which("git"), shutil.which("nix-hash")
records = json.loads((args.tree / "pre-workspace-sources.json").read_text())
assert records
verified = Path(records[0]["source"])
report = {
    "complete": False,
    "scope": "Actual Git index construction/determinism and production owned-cache v2 semantic guards; no native SDK, cold or public support claim",
    "commands": [],
    "cases": [],
}


def save():
    (work / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")


def run(label, argv, input=None):
    row = {"label": label}
    report["commands"].append(row)
    result = bounded_native.run_recorded(
        argv,
        record=row,
        logs=work / "logs",
        save=save,
        cwd=work,
        input=input,
        timeout=60,
        output_limit=8 * 1024 * 1024,
    )
    assert result.returncode == 0, (label, result.stderr)
    return result.stdout


stages = []
for number in (1, 2):
    stage = work / ("stage" + str(number))
    protocol.writable_copy(verified, stage)
    # Derived index operations apply only to these owned test stages.
    (stage / ".git/index").unlink()
    plain = work / ("plain" + str(number))
    protocol.plain_copy(stage, plain)
    plain_before = installer.hash_path(plain, nix_hash)
    metadata.initialize_indices(stage, git)
    plain_after = work / ("plain-after" + str(number))
    protocol.plain_copy(stage, plain_after)
    assert installer.hash_path(plain_after, nix_hash) == plain_before
    stages.append(
        {
            "indexSHA256": hashlib.sha256(
                (stage / ".git/index").read_bytes()
            ).hexdigest(),
            "fullNAR": installer.hash_path(stage, nix_hash),
            "sourceNAR": plain_before,
        }
    )
assert stages[0] == stages[1]
report["deterministicIndependentStages"] = stages
root = work / "project"
root.mkdir()
(root / "Package.swift").write_text(
    'import PackageDescription\nlet package=Package(name:"Guard")\n'
)
assert installer.install(root, records, nix_hash, git)
state = root / "Packages" / installer.STATE
original_state = state.read_bytes()
assert json.loads(original_state)["version"] == 2
checkout = root / records[0]["path"]
index = checkout / ".git/index"
canonical_index = index.read_bytes()
original_nar = installer.hash_path(checkout, nix_hash)
assert (
    run("ordinary-native-status", [git, "-C", str(checkout), "status", "--porcelain"])
    == b""
)
refreshed_nar = installer.hash_path(checkout, nix_hash)
assert refreshed_nar != original_nar
assert not installer.install(root, records, nix_hash, git)
assert state.read_bytes() == original_state
report["cases"].append(
    {
        "name": "ordinary-git-status-refresh-accepted",
        "beforeNAR": original_nar,
        "afterNAR": refreshed_nar,
    }
)
relocated = work / "relocated"
root.rename(relocated)
root = relocated
state = root / "Packages" / installer.STATE
checkout = root / records[0]["path"]
index = checkout / ".git/index"
assert (
    run("relocated-native-status", [git, "-C", str(checkout), "status", "--porcelain"])
    == b""
)
assert not installer.install(root, records, nix_hash, git)
report["cases"].append({"name": "relocation-and-stat-refresh-accepted"})
entries = run("real-tree", [git, "-C", str(checkout), "ls-tree", "-rz", "HEAD"]).split(
    b"\0"
)
entries = [entry.split(b"\t", 1) for entry in entries if entry]
regular = [
    (header.split(), path)
    for header, path in entries
    if header.startswith(b"100644 blob ")
]
assert len(regular) >= 2
path = os.fsdecode(regular[0][1])
other_oid = regular[1][0][2].decode()
current_oid = regular[0][0][2].decode()
prefix = [git, "-C", str(checkout)]


def reject(name):
    before = installer.hash_path(root, nix_hash)
    for operation in (
        lambda: installer.install(root, records, nix_hash, git),
        lambda: installer.uninstall(root, nix_hash, git),
    ):
        try:
            operation()
        except (protocol.StateError, ValueError, OSError):
            pass
        else:
            raise AssertionError("accepted " + name)
        assert installer.hash_path(root, nix_hash) == before
    report["cases"].append(
        {
            "name": name,
            "beforeNAR": before,
            "afterNAR": installer.hash_path(root, nix_hash),
        }
    )
    save()


for name, arguments in (
    ("staged-mode", ["update-index", "--chmod=+x", "--", path]),
    ("staged-oid", ["update-index", "--cacheinfo", "100644," + other_oid + "," + path]),
    ("staged-delete", ["update-index", "--force-remove", "--", path]),
    ("assume-unchanged", ["update-index", "--assume-unchanged", "--", path]),
    ("skip-worktree", ["update-index", "--skip-worktree", "--", path]),
):
    index.write_bytes(canonical_index)
    run(name, [*prefix, *arguments])
    reject(name)
index.write_bytes(canonical_index)
zero = "0" * len(current_oid)
conflicts = (
    "0 "
    + zero
    + "\t"
    + path
    + "\n100644 "
    + current_oid
    + " 1\t"
    + path
    + "\n100644 "
    + other_oid
    + " 2\t"
    + path
    + "\n100644 "
    + current_oid
    + " 3\t"
    + path
    + "\n"
).encode()
run(
    "create-real-unmerged-index",
    [*prefix, "update-index", "--index-info"],
    input=conflicts,
)
reject("unmerged-index")
index.write_bytes(b"corrupt index")
reject("corrupt-index")
index.unlink()
reject("missing-index")
index.write_bytes(canonical_index)
lock = checkout / ".git/index.lock"
lock.write_text("owned by another operation")
reject("index-lock")
lock.unlink()
lock.symlink_to(work / "absent")
reject("dangling-index-lock")
lock.unlink()
shared = checkout / (".git/sharedindex." + "a" * 40)
shared.write_text("unsupported shared index")
reject("shared-index")
shared.unlink()
original = (checkout / path).read_bytes()
(checkout / path).write_bytes(original + b"\n// user edit\n")
reject("edited-source")
(checkout / path).write_bytes(original)
extra = checkout / "user-untracked.txt"
extra.write_text("preserve")
reject("untracked-source")
extra.unlink()
state.write_text(json.dumps({"version": 1, "packages": {}}))
reject("old-private-ownership-not-reinterpreted")
state.write_bytes(original_state)
for name, raw in (
    ("marker-extra-field", original_state[:-2] + b',"extra":true}\n'),
    ("marker-duplicate-field", original_state[:-2] + b',"version":2}\n'),
    ("marker-edited-format", b" " + original_state),
):
    state.write_bytes(raw)
    reject(name)
state.unlink()
reject("missing-marker")
state.symlink_to(work / "absent-marker")
reject("dangling-marker")
state.unlink()
external_marker = work / "outside-marker.json"
external_marker.write_bytes(original_state)
state.symlink_to(external_marker)
reject("aliased-marker")
assert external_marker.read_bytes() == original_state
state.unlink()
state.write_bytes(original_state)
legacy = root / installer.LEGACY_STATE
legacy.parent.mkdir(parents=True)
legacy.write_bytes(original_state)
reject("old-private-path-even-with-new-marker")
legacy.unlink()
assert not installer.install(root, records, nix_hash, git)
report["complete"] = True
report["sourceFiles"] = {
    name: hashlib.sha256((source / name).read_bytes()).hexdigest()
    for name in ("pkgs/cli/pre_workspace_git.py", "pkgs/cli/pre_workspace_install.py")
}
save()
print("PASS", len(report["cases"]), "real Git index ownership cases")
