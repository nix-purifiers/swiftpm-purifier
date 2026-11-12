# SPDX-License-Identifier: MIT
"""Format8 structural inventory parity, including recovery and source aliases."""

from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
base = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(base / "pkgs/cli"))
import dependencies_v1_layout as layout

work = args.output.resolve()
work.mkdir()


def python_layout(root):
    try:
        result = layout.source_layout(root)
        return {"valid": True, "paths": result["entries"], "hash": result["hash"]}
    except layout.LayoutError:
        return {"valid": False, "paths": None, "hash": None}


report = {
    "complete": False,
    "scope": "Format8 source-layout Python/Nix parity; pending control artifacts invalidate inventory",
    "cases": [],
}
cases = [
    "plain",
    "empty-dirs",
    "unicode",
    "lexical-sort",
    "native-pins",
    "native-pins-absent",
    "native-debug-link",
    "journal",
    "malformed-journal",
    "orphan-stage",
    "stage-with-backup",
    "stage-file",
    "similar-user-name",
    "ordinary-dotfile",
    "source-link",
    "build-link",
    "native-pin-link",
    "mixed-pins",
    "nonregular-source",
    "git-worktree-file",
    "packages-wrong-type",
    "control-newline",
    "control-tab",
    "control-del",
    "quoted-name",
    "old-lock-name-included",
]
for name in cases:
    root = work / name
    root.mkdir()
    (root / "Package.swift").write_text("import PackageDescription\n")
    (root / "Sources").mkdir()
    (root / "Sources/main.swift").write_text("print(42)\n")
    expected = name not in {
        "journal",
        "malformed-journal",
        "orphan-stage",
        "stage-with-backup",
        "stage-file",
        "source-link",
        "build-link",
        "native-pin-link",
        "mixed-pins",
        "nonregular-source",
        "packages-wrong-type",
        "control-newline",
        "control-tab",
        "control-del",
    }
    stage = root / (".swiftpm-purifier-stage-" + "a" * 64)
    if name.startswith("control-"):
        character = {
            "control-newline": "\n",
            "control-tab": "\t",
            "control-del": "\x7f",
        }[name]
        (root / "Sources" / ("bad" + character + ".swift")).write_text("invalid path")
    elif name == "quoted-name":
        (root / 'Sources/a"b.swift').write_text("// included")
    elif name == "old-lock-name-included":
        (root / ".swiftpm-purifier-transaction.lock").write_text(
            "ordinary user data; no root lock role"
        )
    elif name == "empty-dirs":
        (root / "Sources/empty/nested").mkdir(parents=True)
    elif name == "unicode":
        (root / "Sources/éclair.swift").write_text("// UTF8\n")
    elif name == "lexical-sort":
        (root / "foo").mkdir()
        (root / "foo/z").write_text("x")
        (root / "foo.").write_text("x")
    elif name == "native-pins":
        (root / "Package.resolved").write_text('{"version":1}')
    elif name == "native-debug-link":
        (root / ".build/x86_64-unknown-linux/debug").mkdir(parents=True)
        (root / ".build/debug").symlink_to(
            "x86_64-unknown-linux/debug", target_is_directory=True
        )
    elif name in ("journal", "malformed-journal"):
        (root / ".swiftpm-purifier-transaction.json").write_text(
            "{}" if name == "journal" else "bad"
        )
    elif name == "orphan-stage":
        stage.mkdir()
    elif name == "stage-with-backup":
        stage.mkdir()
        (stage / "backup").write_text("preserve")
    elif name == "stage-file":
        stage.write_text("not a directory")
    elif name == "similar-user-name":
        (root / ".swiftpm-purifier-stage-documentation").mkdir()
    elif name == "ordinary-dotfile":
        (root / ".user-config").write_text("included")
    elif name == "source-link":
        (root / "Sources/alias.swift").symlink_to("main.swift")
    elif name == "build-link":
        (root / ".build").symlink_to("Sources", target_is_directory=True)
    elif name == "native-pin-link":
        (root / "Package.resolved").symlink_to("Package.swift")
    elif name == "mixed-pins":
        (root / "Package.pins").write_text("{}")
    elif name == "nonregular-source":
        os.mkfifo(root / "Sources/pipe")
    elif name == "git-worktree-file":
        (root / ".git").write_text("gitdir: /not-followed\n")
    elif name == "packages-wrong-type":
        (root / "Packages").write_text("user-file")
    python = python_layout(root)
    expression = (
        "import "
        + str(base / "lib-overlays/default/dependencies-v1-layout.nix")
        + " { src = "
        + str(root)
        + "; }"
    )
    result = subprocess.run(
        ["nix", "eval", "--impure", "--json", "--expr", expression],
        capture_output=True,
        timeout=30,
    )
    (work / (name + ".stdout")).write_bytes(result.stdout)
    (work / (name + ".stderr")).write_bytes(result.stderr)
    assert result.returncode == 0, result.stderr
    nix = json.loads(result.stdout)
    assert python == nix and python["valid"] == expected, (name, python, nix)
    report["cases"].append(
        {
            "name": name,
            "expected": expected,
            "result": python,
            "stdoutSHA256": hashlib.sha256(result.stdout).hexdigest(),
            "stderrSHA256": hashlib.sha256(result.stderr).hexdigest(),
        }
    )
    (work / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
report["complete"] = True
(work / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
print("PASS 26 private Python/Nix domain-v1 layout parity cases")
