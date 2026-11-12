from pathlib import Path
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import sys
import argparse

parser = argparse.ArgumentParser(
    description="Strict state2 local reader parity from actual native fixture metadata"
)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
source = Path(__file__).resolve().parents[1]
w = args.output.resolve()
w.mkdir()
(w / "tmp").mkdir()
(w / "logs").mkdir()
fixture = source / "pkgs/cli/fixtures/native424"
sys.path.insert(0, str(source / "pkgs/cli"))
import swiftpm_purifier as sp

sp.LOCK_OVERRIDE = None
sp.TOOL_LOCK_OVERRIDE = None
spec = importlib.util.spec_from_file_location(
    "bounded", source / "pkgs/cli/bounded_native.py"
)
bounded = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bounded)
original = json.loads((fixture / "swiftpm-purifier.lock.json").read_text())
cases = [("actual-native-local", None, True)]
for label, key, value in [
    ("escape", "path", "@root@/../Local"),
    ("absolute", "path", "/tmp/Local"),
    ("relative", "path", "Local"),
    ("dot", "path", "@root@/./Local"),
    ("double-slash", "path", "@root@//Local"),
    ("empty", "path", "@root@/"),
    ("missing", "path", "@root@/Missing"),
    ("not-local", "isLocal", False),
    ("duplicate-id", "identity", "leaf"),
    ("duplicate-name", "name", "LegacyLeaf"),
]:
    cases.append(
        (
            label,
            lambda lock, key=key, value=value: lock["localDependencies"][0][
                "packageRef"
            ].__setitem__(key, value),
            False,
        )
    )
cases += [
    (
        "unknown-ref-field",
        lambda lock: lock["localDependencies"][0]["packageRef"].__setitem__(
            "kind", "fileSystem"
        ),
        False,
    ),
    (
        "unknown-record-field",
        lambda lock: lock["localDependencies"][0].__setitem__("extra", True),
        False,
    ),
    (
        "wrong-state",
        lambda lock: lock["localDependencies"][0].__setitem__(
            "state", {"name": "fileSystem"}
        ),
        False,
    ),
    (
        "wrong-subpath",
        lambda lock: lock["localDependencies"][0].__setitem__("subpath", "other"),
        False,
    ),
    (
        "based-on",
        lambda lock: lock["localDependencies"][0].__setitem__("basedOn", {}),
        False,
    ),
]
filesystem_cases = (
    "symlink-directory-escape",
    "symlink-manifest-escape",
    "directory-manifest",
    "nested-symlink-escape",
    "unreadable-manifest",
)
cases += [(name, None, False) for name in filesystem_cases]
r = {
    "complete": False,
    "harnessSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "scope": "Python/Nix strict local dependency-state2 reader controls from actual native4.2 record; malformed data and actual filesystem controls, no substituted SDK API",
    "commands": [],
    "cases": [],
}


def save():
    (w / "receipt.json").write_text(json.dumps(r, indent=2) + "\n")


env = dict(
    os.environ,
    TMPDIR=str(w / "tmp"),
    READER=str(source / "lib-overlays/default/lock.nix"),
)
expr = 'let root = /. + builtins.getEnv "CASE_ROOT"; r=import (/. + builtins.getEnv "READER") {src=root;lockFile=root+"/swiftpm-purifier.lock.json";}; in {problems=r.lock.problems;state=r.workspaceState;}'
for label, mutate, expected in cases:
    p = w / label
    shutil.copytree(
        fixture, p, ignore=shutil.ignore_patterns(".build", ".swiftpm", "Packages")
    )
    lock = copy.deepcopy(original)
    if mutate:
        mutate(lock)
    filesystem = []
    if label in filesystem_cases:
        external = w / (label + "-external")
        shutil.copytree(fixture / "Local", external)
        manifest = p / "Local/Package.swift"
        if label == "symlink-directory-escape":
            shutil.rmtree(p / "Local")
            (p / "Local").symlink_to(external, target_is_directory=True)
        elif label == "symlink-manifest-escape":
            manifest.unlink()
            manifest.symlink_to(external / "Package.swift")
        elif label == "directory-manifest":
            manifest.unlink()
            manifest.mkdir()
        elif label == "nested-symlink-escape":
            (p / "Container").mkdir()
            (p / "Container/Local").symlink_to(external, target_is_directory=True)
            lock["localDependencies"][0]["packageRef"]["path"] = (
                "@root@/Container/Local"
            )
        elif label == "unreadable-manifest":
            manifest.chmod(0)
        current = p
        parts = lock["localDependencies"][0]["packageRef"]["path"][
            len("@root@/") :
        ].split("/") + ["Package.swift"]
        for part in parts:
            current = current / part
            info = current.lstat()
            facts = {
                "path": str(current.relative_to(p)),
                "mode": stat.S_IMODE(info.st_mode),
                "type": "symlink"
                if current.is_symlink()
                else "directory"
                if current.is_dir()
                else "file",
            }
            if current.is_symlink():
                facts["target"] = os.readlink(current)
            filesystem.append(facts)
            if current.is_symlink():
                break
    lock["contentHash"] = sp.content_hash(lock)
    (p / "swiftpm-purifier.lock.json").write_text(json.dumps(lock))
    try:
        python = sp.is_current(p, lock)
    except (sp.PurifierError, KeyError, TypeError, ValueError, AttributeError):
        python = False
    row = {"label": label}
    r["commands"].append(row)
    env["CASE_ROOT"] = str(p)
    x = bounded.run_recorded(
        ["nix", "eval", "--impure", "--json", "--expr", expr],
        record=row,
        logs=w / "logs",
        save=save,
        cwd=w,
        env=env,
        timeout=60,
        output_limit=2 * 1024 * 1024,
    )
    nix = x.returncode == 0 and not json.loads(x.stdout)["problems"]
    if label == "unreadable-manifest":
        manifest.chmod(0o644)
    r["cases"].append(
        {
            "label": label,
            "pythonAccepted": python,
            "nixAccepted": nix,
            "expected": expected,
            "filesystemBefore": filesystem,
        }
    )
    save()
    assert python == nix == expected, (
        label,
        python,
        nix,
        x.stderr.decode(errors="replace")[-1000:],
    )
r["complete"] = True
save()
print("PASS", len(cases), "reader cases")
