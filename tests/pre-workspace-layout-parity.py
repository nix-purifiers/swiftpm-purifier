"""Source-owned format7 structural-inventory parity; no runtime/support claim."""

from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
source = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(source / "pkgs/cli"))
import pre_workspace_layout as layout
import bounded_native

w = args.output.resolve()
w.mkdir()
(w / "logs").mkdir()
(w / "tmp").mkdir()
r = {
    "complete": False,
    "scope": "Source-owned Python/Nix format7 source-layout/default-or-external destination parity; no producer/native support claim",
    "commands": [],
    "cases": [],
}


def save():
    (w / "receipt.json").write_text(json.dumps(r, indent=2) + "\n")


def project(name):
    p = w / name
    p.mkdir()
    (p / "Package.swift").write_text(
        'import PackageDescription\nlet package = Package(name: "Layout")\n'
    )
    (p / "Sources/One").mkdir(parents=True)
    (p / "Sources/One/One.swift").write_text("public func one()->Int{return 1}\n")
    return p


base = project("baseline")
baseline = layout.source_layout(base)["hash"]
manifest_before = hashlib.sha256((base / "Package.swift").read_bytes()).hexdigest()


def run(name, root, valid=True, same=True, selected=None, store=False, pure_flake=None):
    try:
        python = layout.source_layout(root, selected)
    except layout.LayoutError as error:
        python = {"valid": False, "error": str(error), "hash": None, "entries": []}
    else:
        python["valid"] = True
    expression = (
        "let root = "
        + (
            'builtins.path {path = /. + builtins.getEnv "LAYOUT_SOURCE";name="layout-source";}'
            if store
            else 'builtins.getEnv "LAYOUT_SOURCE"'
        )
        + "; in import "
        + str(source / "nix/lib/pre-workspace-layout.nix")
        + " {src=root;"
        + ('lockFile=builtins.getEnv "LAYOUT_LOCK";' if selected is not None else "")
        + "}"
    )
    if pure_flake is not None:
        expression = (
            "let f=builtins.getFlake "
            + json.dumps(str(pure_flake))
            + '; in import (f.outPath+"/nix/lib/pre-workspace-layout.nix") {src=f.outPath+"/tests/fixtures/pre-workspace-layout";'
            + ('lockFile=f.outPath+"/flake.lock";' if selected is not None else "")
            + "}"
        )
    row = {"label": name}
    r["commands"].append(row)
    result = bounded_native.run_recorded(
        [
            "nix",
            "eval",
            *([] if pure_flake is not None else ["--impure"]),
            "--json",
            "--expr",
            expression,
        ],
        record=row,
        logs=w / "logs",
        save=save,
        cwd=source,
        env=dict(
            os.environ,
            LAYOUT_SOURCE=str(root),
            LAYOUT_LOCK="" if selected is None else str(selected),
            TMPDIR=str(w / "tmp"),
        ),
        timeout=60,
        output_limit=8 * 1024 * 1024,
    )
    assert result.returncode == 0, (name, result.stderr)
    nix = json.loads(result.stdout)
    assert python["valid"] == nix["valid"] == valid, (name, python, nix)
    assert python["hash"] == nix["hash"] and python["entries"] == nix["entries"], name
    if valid:
        assert (python["hash"] == baseline) == same, name
    else:
        assert nix["entries"] == [] and nix["hash"] is None
    r["cases"].append(
        {
            "name": name,
            "expectedValid": valid,
            "python": python,
            "nix": nix,
            "sameAsBaseline": same if valid else None,
        }
    )
    save()
    print(name, "PASS", flush=True)


run("baseline", base)
run("store-context", base, store=True)
for case in (
    "content-edit",
    "manifest-edit",
    "add-module",
    "remove-source",
    "rename-source",
    "rename-module",
    "add-root-main",
    "empty-directory",
    "file-to-directory",
    "unicode",
    "sort-prefix",
    "nested-build",
    "nested-packages",
    "nested-git",
    "hidden-source",
    "resource",
    "c-header",
    "modulemap",
    "generated-root",
    "default-lock",
    "git-worktree-file",
):
    p = project(case)
    if case == "content-edit":
        (p / "Sources/One/One.swift").write_text("public func one()->Int{return 42}\n")
    elif case == "manifest-edit":
        (p / "Package.swift").write_text(
            'import PackageDescription\nlet package = Package(name: "Different")\n'
        )
    elif case == "add-module":
        (p / "Sources/New").mkdir()
        (p / "Sources/New/main.swift").write_text("print(42)\n")
    elif case == "remove-source":
        (p / "Sources/One/One.swift").unlink()
    elif case == "rename-source":
        (p / "Sources/One/One.swift").rename(p / "Sources/One/main.swift")
    elif case == "rename-module":
        (p / "Sources/One").rename(p / "Sources/Other")
    elif case == "add-root-main":
        (p / "main.swift").write_text("print(42)\n")
    elif case == "empty-directory":
        (p / "Sources/Empty").mkdir()
    elif case == "file-to-directory":
        (p / "Sources/One/One.swift").unlink()
        (p / "Sources/One/One.swift").mkdir()
    elif case == "unicode":
        (p / "Sources/源😀").mkdir()
        (p / "Sources/源😀/é.swift").write_text("// UTF-8\n")
    elif case == "sort-prefix":
        (p / "foo").mkdir()
        (p / "foo/x").write_text("x")
        (p / "foo.bar").write_text("x")
    elif case in ("nested-build", "nested-packages", "nested-git"):
        name = {
            "nested-build": ".build",
            "nested-packages": "Packages",
            "nested-git": ".git",
        }[case]
        (p / "Sources" / name).mkdir()
        (p / "Sources" / name / "input").write_text("x")
    elif case == "hidden-source":
        (p / "Sources/.Hidden").mkdir()
        (p / "Sources/.Hidden/main.swift").write_text("print(42)\n")
    elif case == "resource":
        (p / "Resources").mkdir()
        (p / "Resources/data.bin").write_bytes(b"\0")
    elif case == "c-header":
        (p / "Sources/One/public.h").write_text("int one(void);\n")
    elif case == "modulemap":
        (p / "module.modulemap").write_text("module Native {}\n")
    elif case == "generated-root":
        for name in (".build", "Packages", ".git"):
            (p / name).mkdir()
            (p / name / "ignored").write_text("x")
    elif case == "default-lock":
        (p / layout.DEFAULT_LOCK).write_text("{}")
    elif case == "git-worktree-file":
        (p / ".git").write_text("gitdir: /not-followed\n")
    run(
        case,
        p,
        same=case
        in (
            "content-edit",
            "manifest-edit",
            "generated-root",
            "default-lock",
            "git-worktree-file",
        ),
    )
    if case == "manifest-edit":
        assert (
            hashlib.sha256((p / "Package.swift").read_bytes()).hexdigest()
            != manifest_before
        )
    if case == "content-edit":
        assert (
            hashlib.sha256((p / "Package.swift").read_bytes()).hexdigest()
            == manifest_before
        )
external = w / "external.lock"
run("external-missing", base, selected=external)
external.write_text("{}")
run("external-existing", base, selected=external)
relocated = w / "relocated"
shutil.copytree(base, relocated)
run("relocated-default", relocated)
run("relocated-external", relocated, selected=external)
for case in (
    "source-symlink",
    "source-dangling",
    "source-directory-symlink",
    "root-build-symlink",
    "root-packages-symlink",
    "root-git-symlink",
    "default-lock-symlink",
    "default-lock-directory",
    "control-name",
    "fifo",
    "in-tree-root-lock",
    "in-tree-nested-lock",
    "external-symlink",
    "external-dangling",
    "external-ancestor-symlink",
    "external-ancestor-file",
    "relative-lock",
):
    p = project(case)
    selected = None
    if case in ("source-symlink", "source-dangling", "source-directory-symlink"):
        (p / "Sources/link").symlink_to(
            p / "Sources/One"
            if case == "source-directory-symlink"
            else p / "Sources/One/One.swift"
            if case == "source-symlink"
            else p / "absent"
        )
    elif case.startswith("root-"):
        name = {
            "root-build-symlink": ".build",
            "root-packages-symlink": "Packages",
            "root-git-symlink": ".git",
        }[case]
        (p / name).symlink_to(w)
    elif case == "default-lock-symlink":
        (p / layout.DEFAULT_LOCK).symlink_to(external)
    elif case == "default-lock-directory":
        (p / layout.DEFAULT_LOCK).mkdir()
    elif case == "control-name":
        (p / "bad\nname").write_text("x")
    elif case == "fifo":
        os.mkfifo(p / "fifo")
    elif case == "in-tree-root-lock":
        selected = p / "other.json"
    elif case == "in-tree-nested-lock":
        selected = p / "metadata/lock.json"
    elif case in ("external-symlink", "external-dangling"):
        selected = w / (case + ".json")
        selected.symlink_to(external if case == "external-symlink" else w / "missing")
    elif case == "external-ancestor-symlink":
        alias = w / "alias"
        alias.symlink_to(w)
        selected = alias / "lock.json"
    elif case == "external-ancestor-file":
        selected = external / "child.json"
    elif case == "relative-lock":
        selected = Path("relative.json")
    run(case, p, valid=False, selected=selected)
root_alias = w / "root-alias"
root_alias.symlink_to(base)
run("source-root-symlink", root_alias, valid=False)


def setup(label, argv, env=None):
    row = {"label": label}
    r["commands"].append(row)
    result = bounded_native.run_recorded(
        argv,
        record=row,
        logs=w / "logs",
        save=save,
        cwd=source,
        env=dict(os.environ, TMPDIR=str(w / "tmp"), **(env or {})),
        timeout=120,
        output_limit=1024 * 1024,
    )
    assert result.returncode == 0, (label, result.stderr)
    return Path(result.stdout.decode().strip())


revision = setup("source-revision", ["git", "rev-parse", "HEAD"])
locked_flake = "git+file://" + str(source) + "?rev=" + str(revision) + "&shallow=1"
fixture = source / "tests/fixtures/pre-workspace-layout"
run("pure-store-default", fixture, pure_flake=locked_flake)
run(
    "pure-store-external",
    fixture,
    selected=source / "flake.lock",
    pure_flake=locked_flake,
)
r["complete"] = True
save()
print("PASS", len(r["cases"]), "layout controls")
