# SPDX-License-Identifier: MIT
"""Cold HTTP Git fixture, selected native SDK, offline and relocated consumers.

No fixed-output seeds are accepted. The origin stays open through prefetch,
then closes before native consumers and the sandboxed Nix build.
"""

import argparse
import functools
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import threading
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epoch", choices=["swift553", "default"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    work = args.output.resolve()
    work.mkdir()
    for name in ["tmp", "home", "logs", "remote"]:
        (work / name).mkdir()
    spec = importlib.util.spec_from_file_location(
        "bounded_native", root / "pkgs/cli/bounded_native.py"
    )
    bounded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bounded)
    receipt = {
        "complete": False,
        "epoch": args.epoch,
        "seeded": False,
        "commands": [],
        "requests": [],
    }

    def save():
        (work / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    env = dict(
        os.environ,
        HOME=str(work / "home"),
        TMPDIR=str(work / "tmp"),
        XDG_CACHE_HOME=str(work / "cache"),
        XDG_CONFIG_HOME=str(work / "config"),
        XDG_STATE_HOME=str(work / "state"),
        XDG_DATA_HOME=str(work / "data"),
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_SYSTEM="/dev/null",
        GIT_AUTHOR_NAME="Native fixture",
        GIT_AUTHOR_EMAIL="fixture@example.invalid",
        GIT_COMMITTER_NAME="Native fixture",
        GIT_COMMITTER_EMAIL="fixture@example.invalid",
    )
    for name in ["LD_PRELOAD", "LD_LIBRARY_PATH", "CPATH", "LIBRARY_PATH"]:
        env.pop(name, None)

    def run(label, command, cwd=work, timeout=240, rejection=None):
        row = {"label": label}
        receipt["commands"].append(row)
        result = bounded.run_recorded(
            command,
            record=row,
            logs=work / "logs",
            save=save,
            cwd=cwd,
            env=env,
            timeout=timeout,
            output_limit=16 * 1024 * 1024,
        )
        if rejection is not None:
            if result.returncode == 0 or rejection not in result.stderr:
                raise RuntimeError(label + " did not reject the conflicting SDK")
        elif result.returncode:
            raise RuntimeError(
                label + " failed: " + result.stderr.decode(errors="replace")[-3000:]
            )
        return result.stdout

    receipt["sourceRevision"] = (
        run("source-revision", ["git", "rev-parse", "HEAD"], root).decode().strip()
    )
    receipt["sourceStatus"] = run(
        "source-status", ["git", "status", "--porcelain=v1"], root
    ).decode()
    names = run("source-files", ["git", "ls-files", "-z"], root).decode().split("\0")
    receipt["sourceFiles"] = {
        name: hashlib.sha256(
            os.readlink(root / name).encode()
            if (root / name).is_symlink()
            else (root / name).read_bytes()
        ).hexdigest()
        for name in names
        if name
    }
    save()

    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    source = work / "leaf-source"
    write(
        source / "Package.swift",
        '// swift-tools-version:5.5\nimport PackageDescription\nlet package=Package(name:"LegacyLeaf",products:[.library(name:"LegacyLeaf",targets:["LegacyLeaf"])],targets:[.target(name:"LegacyLeaf")])\n',
    )
    nonce = uuid.uuid4().hex
    write(
        source / "Sources/LegacyLeaf/Leaf.swift",
        "// Fresh fixture " + nonce + "\npublic func leafAnswer()->Int { return 42 }\n",
    )
    for label, command in [
        ("git-init", ["git", "init"]),
        ("git-add", ["git", "add", "."]),
        ("git-commit", ["git", "commit", "-m", "native fixture"]),
        ("git-tag", ["git", "tag", "1.0.0"]),
    ]:
        run(label, command, source)
    run(
        "git-bare",
        ["git", "clone", "--bare", str(source), str(work / "remote/Leaf.git")],
    )
    run(
        "git-http",
        ["git", "--git-dir", str(work / "remote/Leaf.git"), "update-server-info"],
    )

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *arguments):
            pass

        def do_GET(self):
            receipt["requests"].append(self.path)
            super().do_GET()

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(work / "remote"))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = "http://127.0.0.1:" + str(server.server_port) + "/Leaf.git"
    receipt["origin"] = origin
    project = work / "original"
    write(
        project / "Package.swift",
        '// swift-tools-version:5.5\nimport PackageDescription\nlet package=Package(name:"NativeApp",products:[.library(name:"AppCore",targets:["AppCore"]),.executable(name:"NativeApp",targets:["NativeApp"])],dependencies:[.package(url:'
        + json.dumps(origin)
        + ',from:"1.0.0")],targets:[.target(name:"AppCore",dependencies:[.product(name:"LegacyLeaf",package:"Leaf")]),.executableTarget(name:"NativeApp",dependencies:["AppCore"]),.testTarget(name:"AppCoreTests",dependencies:["AppCore"])])\n',
    )
    guard = (
        '#if compiler(>=5.6)\n#error("requires original Swift 5.5")\n#endif\n'
        if args.epoch == "swift553"
        else ""
    )
    write(
        project / "Sources/AppCore/Core.swift",
        "import LegacyLeaf\n"
        + guard
        + "public func answer()->Int { return leafAnswer() }\n",
    )
    write(
        project / "Sources/NativeApp/main.swift",
        'import AppCore\nif answer() != 42 { fatalError("wrong answer") }\nprint("ANSWER|42")\n',
    )
    write(
        project / "Tests/AppCoreTests/CoreTests.swift",
        "import XCTest\n@testable import AppCore\nfinal class CoreTests:XCTestCase { func testAnswer() { XCTAssertEqual(answer(),42) } }\n",
    )
    # Read source path as data, never interpolate a user-controlled Nix literal.
    env["NATIVE_SOURCE"] = str(root)
    common = """let
      flake = builtins.getFlake ("path:" + builtins.getEnv "NATIVE_SOURCE");
      pkgs = import flake.inputs.nixpkgs { system = builtins.currentSystem; overlays = [ flake.overlays.default ]; };
      swiftPackages = EPOCH;
    in """.replace(
        "EPOCH",
        'import (flake.outPath + "/tests/sdk/swift553/package-set.nix") { inherit pkgs; }'
        if args.epoch == "swift553"
        else "pkgs.swiftPackages",
    )
    try:
        built = json.loads(
            run(
                "build-cli",
                [
                    "nix",
                    "build",
                    "--impure",
                    "--no-link",
                    "--json",
                    "--expr",
                    common
                    + 'pkgs.swiftpm-purifier.cli.override { inherit swiftPackages; runtimeMode = "sdk"; }',
                ],
                timeout=1200,
            )
        )
        cli = str(Path(built[0]["outputs"]["out"]) / "bin/swiftpm-purifier")

        def purifier(label, arguments, directory=project):
            return run(
                label, [cli, "--package-path", str(directory), *arguments], directory
            )

        purifier("cold-lock", ["lock"])
        saved_files = {
            name: (project / name).read_bytes()
            for name in ["Package.resolved", "swiftpm-purifier.lock.json"]
        }
        sdk = json.loads(saved_files["swiftpm-purifier.lock.json"])["nativeSDK"]
        previous_override = env.get("SWIFT_EXEC_MANIFEST")
        env["SWIFT_EXEC_MANIFEST"] = sdk["descriptor"]["executables"]["package"]
        row = {"label": "reject-conflicting-native-role"}
        receipt["commands"].append(row)
        try:
            rejected = bounded.run_recorded(
                [cli, "--package-path", str(project), "lock"],
                record=row,
                logs=work / "logs",
                save=save,
                cwd=project,
                env=env,
                timeout=30,
                output_limit=1024 * 1024,
            )
            assert rejected.returncode != 0
            assert b"override disagrees" in rejected.stderr
            assert all(
                (project / name).read_bytes() == data
                for name, data in saved_files.items()
            )
            receipt["conflictingCompilerRejectedBeforeWrites"] = True
        finally:
            if previous_override is None:
                env.pop("SWIFT_EXEC_MANIFEST", None)
            else:
                env["SWIFT_EXEC_MANIFEST"] = previous_override
        receipt["requestsAfterLock"] = len(receipt["requests"])
        purifier("cold-prefetch", ["prefetch"])
        receipt["requestsAfterPrefetch"] = len(receipt["requests"])
        assert any(
            "/objects/" in path
            for path in receipt["requests"][receipt["requestsAfterLock"] :]
        ), "cold Nix prefetch saw no Git object GETs"
        assert any("/objects/" in path for path in receipt["requests"]), (
            "cold origin saw no Git object GETs"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        receipt["originClosed"] = True
        save()
    resolved = (project / "Package.resolved").read_bytes()
    lock = (project / "swiftpm-purifier.lock.json").read_bytes()
    for label, arguments in [
        ("build", ["swift", "build"]),
        ("test", ["swift", "test"]),
        ("run", ["swift", "run"]),
    ]:
        result = purifier(label, arguments)
        if label == "run":
            assert b"ANSWER|42" in result
    env["NATIVE_PROJECT"] = str(project)
    expression = (
        common
        + """((pkgs.swiftpm-purifier.buildSwiftPMProject.override { inherit swiftPackages; }) {
      src = builtins.path { path = /. + builtins.getEnv "NATIVE_PROJECT"; name = "native-project";
        filter = path: type: !(builtins.elem (baseNameOf path) [ ".build" ".swiftpm" ]); };
    }).tests"""
    )
    run(
        "sandbox-build-test",
        ["nix", "build", "--impure", "--no-link", "--json", "--expr", expression],
        timeout=900,
    )
    env["NATIVE_CONFLICTING_COMPILER"] = sdk["descriptor"]["executables"]["package"]
    for variable in [
        "SWIFT_EXEC_MANIFEST",
        "_SWIFT_EXEC_MANIFEST",
        "SWIFT_DRIVER_SWIFT_FRONTEND_EXEC",
    ]:
        run(
            "sandbox-reject-" + variable.lower(),
            [
                "nix",
                "build",
                "--impure",
                "--no-link",
                "--print-build-logs",
                "--expr",
                "("
                + expression
                + ").overrideAttrs { "
                + variable
                + ' = builtins.getEnv "NATIVE_CONFLICTING_COMPILER"; }',
            ],
            timeout=180,
            rejection=b"native compiler override disagrees",
        )
    relocated = work / "relocated"
    shutil.move(project, relocated)
    assert not project.exists()
    purifier("relocated-clean", ["swift", "package", "clean"], relocated)
    for label, arguments in [
        ("relocated-build", ["swift", "build"]),
        ("relocated-test", ["swift", "test"]),
        ("relocated-run", ["swift", "run"]),
    ]:
        result = purifier(label, arguments, relocated)
        if label == "relocated-run":
            assert b"ANSWER|42" in result
    assert (relocated / "Package.resolved").read_bytes() == resolved
    assert (relocated / "swiftpm-purifier.lock.json").read_bytes() == lock
    receipt.update(
        complete=True,
        nativeLockUnchanged=True,
        purifierLockUnchanged=True,
        originalRemoved=True,
        nativeLockSha256=hashlib.sha256(resolved).hexdigest(),
        purifierLockSha256=hashlib.sha256(lock).hexdigest(),
    )
    save()
    print(args.epoch + " native SDK lifecycle PASS")


if __name__ == "__main__":
    main()
