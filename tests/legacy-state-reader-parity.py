from pathlib import Path
import copy, hashlib, importlib.util, json, os, shutil, stat, sys
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
source = Path(__file__).resolve().parents[1]
w = args.output.resolve()
w.mkdir()
(w / "logs").mkdir()
(w / "tmp").mkdir()
sys.path.insert(0, str(source / "pkgs/cli"))
import swiftpm_purifier as sp, legacy_state

spec = importlib.util.spec_from_file_location(
    "bounded", source / "pkgs/cli/bounded_native.py"
)
bounded = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bounded)
fixture = source / "pkgs/cli/fixtures/native311"
native = (fixture / "Package.pins").read_bytes()
native_pin = json.loads(native)["pins"][0]
workspace = json.loads((fixture / "workspace-state.json").read_text())["dependencies"][
    0
]
base = {
    "version": 6,
    "workspaceStateVersion": 1,
    "package": "Project",
    "rootDirectory": "Project",
    "pins": {
        native_pin["package"]: {
            "name": native_pin["package"],
            "sourceLocation": native_pin["repositoryURL"],
            "revision": workspace["currentRevision"],
            "version": workspace["currentVersion"],
            "hash": "sha256-" + "A" * 43 + "=",
        }
    },
    "localDependencies": [],
    "binaryTargets": [],
    "products": [],
    "nativeToolLock": {"file": "Package.pins", "sha256": None},
}
cases = []


def case(name, expected, change=lambda lock, data: None, **options):
    cases.append((name, expected, change, options))


case("actual-native-file", True)
case("store-path-context", True, stored=True)
case("empty-absent", True, lambda l, d: (l.update(pins={}), d.clear()), absent=True)
case("empty-native-file", True, lambda l, d: (l.update(pins={}), d.update(pins=[])))
case("wrong-workspace", False, lambda l, d: l.update(workspaceStateVersion=2))
case("old-format1-pair", False, lambda l, d: l.update(version=4))
case("native-sdk-field", False, lambda l, d: l.update(nativeSDK=None))
case(
    "wrong-native-filename",
    False,
    lambda l, d: l["nativeToolLock"].update(file="Package.resolved"),
)
case("changed-file-bytes", False, stale=True)
case("missing-file", False, absent=True)
case("null-hash-remote", False, nullhash=True)
case("null-hash-file-present", False, lambda l, d: l.update(pins={}), nullhash=True)
case("native-version-bool", False, lambda l, d: d.update(version=True))
case("autopin-number", False, lambda l, d: d.update(autoPin=1))
case("pin-version-mismatch", False, lambda l, d: d["pins"][0].update(version="1.1.0"))
case("pin-name-mismatch", False, lambda l, d: d["pins"][0].update(package="Other"))
case(
    "pin-url-mismatch",
    False,
    lambda l, d: d["pins"][0].update(
        repositoryURL="https://different.invalid/Leaf.git"
    ),
)
case(
    "reason-json-looking-text",
    True,
    lambda l, d: d["pins"][0].update(
        reason='{"version":1,"autoPin":false,"package":"text"}'
    ),
)
case("duplicate-top-key", False, duplicate="top")
case("duplicate-pin-key", False, duplicate="pin")
case("escaped-native-key", True, escaped=True)
case("invalid-native-json", False, invalidjson=True)
case("bad-semver", False, lambda l, d: l["pins"]["LegacyLeaf"].update(version="1.01.0"))
case(
    "local-git-path",
    False,
    lambda l, d: l["pins"]["LegacyLeaf"].update(sourceLocation="/home/other/Leaf"),
)
case(
    "extra-pin-field", False, lambda l, d: l["pins"]["LegacyLeaf"].update(unknown=True)
)
case(
    "unsafe-native-name",
    False,
    lambda l, d: l["pins"]["LegacyLeaf"].update(name="../escape"),
)
case(
    "bad-hash",
    False,
    lambda l, d: l["pins"]["LegacyLeaf"].update(hash="sha256-not-a-hash"),
)
case("local-record", False, lambda l, d: l.update(localDependencies=[{}]))
case("native-file-directory", False, directory=True)
case("native-file-symlink", False, symlink=True)
case("native-file-dangling", False, dangling=True)
case("both-default-native-files", False, foreign=True)
case("both-explicit-native-files", True, foreign=True, explicit=True)
case("external-native-file", True, external=True, explicit=True)
case("utf8-bom-native-file", True, bom=True)
case("utf16-native-file", False, utf16=True)
case(
    "unknown-product-kind",
    False,
    lambda l, d: l.update(products=[{"name": "X", "type": "macro", "modules": ["X"]}]),
)
case(
    "unsafe-implicit-output",
    False,
    lambda l, d: l.update(
        products=[{"name": "../escape", "type": "executable", "modules": ["X"]}]
    ),
)
r = {
    "complete": False,
    "scope": "Private synthetic format6 reader contract controls seeded with actual native pin/workspace metadata; no producer/native consumer or NAR correctness claim",
    "commands": [],
    "cases": [],
}


def save():
    (w / "receipt.json").write_text(json.dumps(r, indent=2) + "\n")


for name, expected, change, options in cases:
    root = w / name
    root.mkdir()
    (root / "Package.swift").write_text(
        '// swift-tools-version:3.1\nimport PackageDescription\nlet package=Package(name:"Project")\n'
    )
    lock = copy.deepcopy(base)
    data = json.loads(native)
    change(lock, data)
    target = (
        (w / (name + "-external.pins"))
        if options.get("external")
        else root / "Package.pins"
    )
    raw = json.dumps(data, indent=2).encode() + b"\n"
    if options.get("duplicate") == "top":
        raw = raw.replace(b'"autoPin": true', b'"autoPin": false,"autoPin": true')
    if options.get("duplicate") == "pin":
        raw = raw.replace(
            b'"version": "1.0.0"', b'"version": "1.0.0","\\u0076ersion": "1.0.0"'
        )
    if options.get("escaped"):
        raw = raw.replace(b'"autoPin"', b'"auto\\u0050in"')
    if options.get("invalidjson"):
        raw = b"{not-json"
    if options.get("bom"):
        raw = b"\xef\xbb\xbf" + raw
    if options.get("utf16"):
        raw = raw.decode().encode("utf-16")
    if not options.get("absent"):
        target.write_bytes(raw)
    lock["nativeToolLock"]["sha256"] = (
        None
        if options.get("nullhash") or (options.get("absent") and not lock["pins"])
        else hashlib.sha256(raw).hexdigest()
    )
    if options.get("stale"):
        target.write_bytes(raw + b" ")
    if options.get("directory"):
        target.unlink()
        target.mkdir()
    if options.get("symlink"):
        actual = root / "real-pins"
        target.rename(actual)
        target.symlink_to(actual.name)
    if options.get("dangling"):
        target.unlink()
        target.symlink_to("missing")
    if options.get("foreign"):
        (root / "Package.resolved").write_text("{}")
    lock["manifestHash"] = sp.manifest_hash(root)
    lock["contentHash"] = sp.content_hash(lock)
    (root / sp.LOCK_NAME).write_text(json.dumps(lock))
    sp.LOCK_OVERRIDE = None
    sp.TOOL_LOCK_OVERRIDE = str(target) if options.get("explicit") else None
    try:
        parsed = sp.read_lock(root)
        accepted = sp.is_current(root, parsed)
    except (sp.PurifierError, ValueError, OSError) as error:
        accepted = False
        python_error = str(error)
    else:
        python_error = None
    expression = (
        "let read = import "
        + str(source / "nix/lib/lock.nix")
        + " { src = /. + "
        + json.dumps(str(root))
        + "; lockFile = /. + "
        + json.dumps(str(root / sp.LOCK_NAME))
        + "; "
        + (
            "packageResolved = /. + " + json.dumps(str(target)) + ";"
            if options.get("explicit")
            else ""
        )
        + " }; in { problems = read.lock.problems; entries = read.lock.entries; outputs = read.lock.outputs; workspaceState = if read.lock.problems == [] then read.workspaceState else null; }"
    )
    if options.get("stored"):
        expression = (
            "let source = builtins.path { path = /. + "
            + json.dumps(str(root))
            + '; name = "legacy-reader-source"; }; '
            "read = import "
            + str(source / "nix/lib/lock.nix")
            + ' { src = source; lockFile = source + "/swiftpm-purifier.lock.json"; }; '
            "in { problems = read.lock.problems; entries = read.lock.entries; outputs = read.lock.outputs; workspaceState = if read.lock.problems == [] then read.workspaceState else null; }"
        )
    row = {"label": name}
    r["commands"].append(row)
    result = bounded.run_recorded(
        ["nix", "eval", "--impure", "--json", "--expr", expression],
        record=row,
        logs=w / "logs",
        save=save,
        cwd=source,
        env=dict(os.environ, TMPDIR=str(w / "tmp")),
        timeout=60,
        output_limit=1024 * 1024,
    )
    view = json.loads(result.stdout) if result.returncode == 0 else None
    nix_ok = view is not None and view["problems"] == []
    assert accepted == nix_ok == expected, (
        name,
        accepted,
        nix_ok,
        python_error,
        result.stderr.decode(),
        view,
    )
    if nix_ok:
        assert view["workspaceState"] == legacy_state.workspace(lock)
        assert all(
            "git" in entry and "archive" not in entry for entry in view["entries"]
        )
        assert not (root / ".build").exists()
    elif view is not None:
        assert view["entries"] == [] and view["outputs"] == {}
    r["cases"].append(
        {
            "name": name,
            "expected": expected,
            "pythonAccepted": accepted,
            "pythonError": python_error,
            "nixAccepted": nix_ok,
            "nixExit": result.returncode,
            "nativeFileType": "symlink"
            if target.is_symlink()
            else "regular"
            if target.is_file()
            else "directory"
            if target.is_dir()
            else "absent",
        }
    )
    save()
    print(name, "PASS", flush=True)
r["complete"] = True
save()
