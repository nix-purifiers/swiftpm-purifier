# SPDX-License-Identifier: MIT
"""Real Git controls for owned, self-contained pre-workspace object stores."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

parser = argparse.ArgumentParser()
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--runtime-prefix", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
source = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(source / "pkgs/cli"))
import bounded_native
import pre_workspace as protocol
import pre_workspace_git as verifier

work = args.output.absolute()
work.mkdir()
(work / "logs").mkdir()
(work / "tmp").mkdir()
environment = dict(os.environ, TMPDIR=str(work / "tmp"))
for key in verifier.OBJECT_ENV:
    environment.pop(key, None)
report = {
    "complete": False,
    "scope": "Production capture and real metadata verifier reject actual Git alternates/environment pointers before Git/native commands; physical input NARs unchanged; no native/cold/public support claim",
    "commands": [],
    "cases": [],
}


def save():
    (work / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")


def run(label, argv):
    row = {"label": label}
    report["commands"].append(row)
    result = bounded_native.run_recorded(
        argv,
        record=row,
        logs=work / "logs",
        save=save,
        env=environment,
        timeout=60,
        output_limit=4 * 1024 * 1024,
    )
    assert result.returncode == 0, (label, result.stderr)
    return result.stdout.decode().strip()


nix_hash = shutil.which("nix-hash")
git = shutil.which("git")


def digest(path):
    return verifier.command([nix_hash, "--type", "sha256", str(path)])


def sri(path):
    return "sha256-" + base64.b64encode(bytes.fromhex(digest(path))).decode()


api = SimpleNamespace(
    sdk_mode=lambda: False,
    TOOL_LOCK_OVERRIDE=None,
    purifier_lock_reference=lambda root: root / "swiftpm-purifier.lock.json",
    native_command=lambda name: [str(args.runtime_prefix / "bin" / name)],
    tool=lambda name: shutil.which(name),
)

for kind in ("alternates", "http-alternates"):
    case = work / kind
    case.mkdir()
    root = case / "project"
    root.mkdir()
    (root / "Package.swift").write_text(
        'import PackageDescription\nlet package=Package(name:"Guard")\n'
    )
    checkout = root / "Packages/Leaf-1.0.0"
    checkout.parent.mkdir()
    protocol.writable_copy(args.source, checkout)
    outside = case / "outside-objects"
    if kind == "alternates":
        (checkout / ".git/objects").rename(outside)
        (checkout / ".git/objects/info").mkdir(parents=True)
    else:
        outside.mkdir()
        (outside / "keep").write_text("preserve outside bytes")
    (checkout / ".git/objects/info" / kind).write_text(str(outside) + "\n")
    run(kind + "-real-fsck-passes", [git, "-C", str(checkout), "fsck", "--full"])
    before, outside_before = digest(root), digest(outside)
    revision = run(
        kind + "-actual-head", [git, "-C", str(checkout), "rev-parse", "HEAD"]
    )
    # Both known hashes are genuine; confinement must reject before consuming
    # either, so a hash mismatch cannot accidentally make this control pass.
    metadata_hash = sri(checkout)
    plain = case / "plain"
    protocol.plain_copy(checkout, plain)
    source_hash = sri(plain)
    output = case / "verified-output"
    try:
        verifier.verify(
            checkout,
            output,
            revision,
            source_hash,
            metadata_hash,
            "https://example.invalid/Leaf.git",
            git,
            nix_hash,
        )
    except ValueError as error:
        assert "alternate object" in str(error), str(error)
    else:
        raise AssertionError("metadata verifier accepted " + kind)
    assert not output.exists()
    temporary = case / "capture-work"
    temporary.mkdir()
    old_environment = dict(os.environ)
    os.environ.clear()
    os.environ.update(environment)
    try:
        try:
            protocol.capture(root, temporary, api)
        except protocol.StateError as error:
            assert "alternate object" in str(error), str(error)
        else:
            raise AssertionError("capture accepted " + kind)
    finally:
        os.environ.clear()
        os.environ.update(old_environment)
    assert not (root / "swiftpm-purifier.lock.json").exists()
    after, outside_after = digest(root), digest(outside)
    assert before == after and outside_before == outside_after
    report["cases"].append(
        {
            "name": kind,
            "realGitFsckPassed": True,
            "projectBeforeNAR": before,
            "projectAfterNAR": after,
            "outsideBeforeNAR": outside_before,
            "outsideAfterNAR": outside_after,
        }
    )
    save()

clean = work / "clean-project"
clean.mkdir()
(clean / "Package.swift").write_text(
    'import PackageDescription\nlet package=Package(name:"Guard")\n'
)
valid_revision = run(
    "valid-source-revision",
    [
        git,
        "-c",
        "safe.directory=" + str(args.source),
        "-C",
        str(args.source),
        "rev-parse",
        "HEAD",
    ],
)
valid_plain = work / "valid-plain"
protocol.plain_copy(args.source, valid_plain)
valid_source_hash, valid_metadata_hash = sri(valid_plain), sri(args.source)
for key in verifier.OBJECT_ENV:
    for value in ("", str(work / "external-pointer")):
        modified = dict(environment, **{key: value})
        temporary = work / ("capture-" + key + ("-empty" if not value else "-external"))
        temporary.mkdir()
        before = digest(clean)
        previous = dict(os.environ)
        os.environ.clear()
        os.environ.update(modified)
        try:
            try:
                verifier.verify(
                    args.source,
                    temporary / "output",
                    valid_revision,
                    valid_source_hash,
                    valid_metadata_hash,
                    "https://example.invalid/Leaf.git",
                    git,
                    nix_hash,
                )
            except ValueError as error:
                assert "environment overrides" in str(error)
            else:
                raise AssertionError("metadata verifier accepted " + key)
            try:
                protocol.capture(clean, temporary, api)
            except protocol.StateError as error:
                assert "environment overrides" in str(error)
            else:
                raise AssertionError("capture accepted " + key)
        finally:
            os.environ.clear()
            os.environ.update(previous)
        assert digest(clean) == before and list(temporary.iterdir()) == []
        report["cases"].append(
            {
                "name": key + ("-empty" if not value else "-external"),
                "projectNAR": before,
            }
        )
        save()
report.update(
    complete=True,
    sourceFiles={
        name: hashlib.sha256((source / name).read_bytes()).hexdigest()
        for name in ("pkgs/cli/pre_workspace.py", "pkgs/cli/pre_workspace_git.py")
    },
)
sessions = list((work / "tmp").glob("swiftpm-purifier-preworkspace-*/receipt.json"))
assert sessions and all(
    json.loads(path.read_text())["commands"] == [] for path in sessions
)
report["rejectedBeforeGitOrNativeCommands"] = True
save()
print("PASS", len(report["cases"]), "actual Git confinement cases")
