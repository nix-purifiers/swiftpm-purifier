"""Actual native discovery controls for the format-7 source layout definition."""

from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--runtime-prefix", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
source = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(source / "pkgs/cli"))
import pre_workspace_layout as layout
import pre_workspace_plan as plan
import bounded_native

w = args.output.resolve()
w.mkdir()
(w / "tmp").mkdir()
(w / "logs").mkdir()
(w / "home").mkdir()
runtime = args.runtime_prefix.resolve()
env = dict(
    os.environ,
    PATH=str(runtime / "bin") + ":" + os.environ["PATH"],
    HOME=str(w / "home"),
    TMPDIR=str(w / "tmp"),
)
for key in (
    "CC",
    "CXX",
    "SWIFT_EXEC",
    "SWIFT_EXEC_MANIFEST",
    "_SWIFT_EXEC_MANIFEST",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "CPATH",
    "LIBRARY_PATH",
):
    env.pop(key, None)
r = {
    "complete": False,
    "scope": "Actual supplied historical runtime native layout/product discovery measurements; no codec/cold/public compatibility claim",
    "commands": [],
    "cases": [],
}


def save():
    (w / "receipt.json").write_text(json.dumps(r, indent=2) + "\n")


def command(label, argv, cwd):
    row = {"label": label}
    r["commands"].append(row)
    x = bounded_native.run_recorded(
        argv,
        record=row,
        logs=w / "logs",
        save=save,
        cwd=cwd,
        env=env,
        timeout=120,
        output_limit=8 * 1024 * 1024,
    )
    return x


for label, tool in [
    ("compiler-version", "swiftc"),
    ("package-version", "swift-package"),
]:
    x = command(label, [str(runtime / "bin" / tool), "--version"], w)
    assert x.returncode == 0
    r[label] = x.stdout.decode()
manifest = 'import PackageDescription\nlet package = Package(name: "Layout")\nproducts = [Product(name: "Combo", type: .Library(.Dynamic), modules: ["One","Two"])]\n'
reference = None
implicit = None
for case in (
    "baseline",
    "content-edit",
    "add-implicit-executable",
    "rename-main",
    "remove-module",
    "rename-module",
    "empty-directory",
    "hidden-source",
    "root-main",
    "header",
    "modulemap",
    "resource",
):
    p = w / case
    p.mkdir()
    (p / "Package.swift").write_text(manifest)
    for module in ("One", "Two"):
        (p / "Sources" / module).mkdir(parents=True)
        (p / "Sources" / module / (module + ".swift")).write_text(
            "public func " + module.lower() + "()->Int{return 1}\n"
        )
    if case == "content-edit":
        (p / "Sources/One/One.swift").write_text("public func one()->Int{return 42}\n")
    elif case in ("add-implicit-executable", "rename-main"):
        (p / "Sources/New").mkdir()
        (
            p
            / "Sources/New"
            / ("main.swift" if case == "add-implicit-executable" else "library.swift")
        ).write_text(
            'print("LAYOUT|42")\n'
            if case == "add-implicit-executable"
            else "public func new()->Int{return 42}\n"
        )
    elif case == "remove-module":
        shutil.rmtree(p / "Sources/One")
    elif case == "rename-module":
        (p / "Sources/One").rename(p / "Sources/Other")
    elif case == "empty-directory":
        (p / "Sources/Empty").mkdir()
    elif case == "hidden-source":
        (p / "Sources/.Hidden").mkdir()
        (p / "Sources/.Hidden/main.swift").write_text("print(42)\n")
    elif case == "root-main":
        (p / "main.swift").write_text("print(42)\n")
    elif case == "header":
        (p / "Sources/One/public.h").write_text("int one(void);\n")
    elif case == "modulemap":
        (p / "module.modulemap").write_text("module Native {}\n")
    elif case == "resource":
        (p / "Resources").mkdir()
        (p / "Resources/data.bin").write_bytes(b"\0")
    before = layout.source_layout(p)["hash"]
    x = command(case + "-build", [str(runtime / "bin/swift-build")], p)
    native_plan = p / ".build/debug.yaml"
    parsed = plan.plan(native_plan.read_bytes()) if native_plan.exists() else None
    if case == "baseline":
        assert x.returncode == 0
        reference = before
    else:
        assert (before == reference) == (case == "content-edit")
    if case in ("content-edit", "add-implicit-executable", "rename-main"):
        assert x.returncode == 0
    if case == "add-implicit-executable":
        assert "<New.exe>" in parsed["targets"]["main"]
        implicit = before
        y = command("new-executable-run", [str(p / ".build/debug/New")], p)
        assert y.returncode == 0 and y.stdout.strip() == b"LAYOUT|42"
    if case == "rename-main":
        assert before != implicit and "<New.exe>" not in parsed["targets"]["main"]
    assert (p / "Package.swift").read_text() == manifest and layout.source_layout(p)[
        "hash"
    ] == before
    r["cases"].append(
        {
            "case": case,
            "sourceLayoutHash": before,
            "nativeExit": x.returncode,
            "nativeMainTargets": None if parsed is None else parsed["targets"]["main"],
            "nativePlanSha256": None
            if parsed is None
            else hashlib.sha256(native_plan.read_bytes()).hexdigest(),
            "manifestUnchanged": True,
            "sourceLayoutUnchangedByNative": True,
        }
    )
    save()
    print(case, x.returncode, flush=True)
r["complete"] = True
save()
