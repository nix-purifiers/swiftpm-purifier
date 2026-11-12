# SPDX-License-Identifier: MIT
"""Cold workspace-state-1 lifecycle with an already prepared original Swift3 fixture.

The caller supplies the installed runtime. Capture/prefetch never installs one.
This test's observed version assertions establish the historical fixture point, not a
product runtime allowlist. The optional pure check explicitly selects its SDK.
"""

import argparse
import functools
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-prefix", type=Path, required=True)
    parser.add_argument("--pure", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "pkgs/cli"))
    import bounded_native

    runtime = args.runtime_prefix.resolve()
    work = args.output.resolve()
    work.mkdir()
    for name in ("tmp", "logs", "home", "remote"):
        (work / name).mkdir()
    receipt = {
        "complete": False,
        "scope": "Original Swift3.1 fixture, portable6/workspace1 lifecycle",
        "runtime": str(runtime),
        "harnessSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "seeded": False,
        "commands": [],
        "requests": [],
    }

    def save():
        (work / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    env = dict(
        os.environ,
        PATH=str(runtime / "bin") + ":" + os.environ.get("PATH", ""),
        SWIFT_EXEC=str(runtime / "bin/swiftc"),
        HOME=str(work / "home"),
        TMPDIR=str(work / "tmp"),
        XDG_CACHE_HOME=str(work / "cache"),
        XDG_CONFIG_HOME=str(work / "config"),
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_SYSTEM="/dev/null",
        GIT_AUTHOR_NAME="Legacy fixture",
        GIT_AUTHOR_EMAIL="fixture@example.invalid",
        GIT_COMMITTER_NAME="Legacy fixture",
        GIT_COMMITTER_EMAIL="fixture@example.invalid",
        NATIVE_SOURCE=str(root),
        SWIFTPM_PURIFIER_FLAKE=str(root),
    )
    for key in (
        "CC",
        "CXX",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "CPATH",
        "LIBRARY_PATH",
        "SWIFT_EXEC_MANIFEST",
        "_SWIFT_EXEC_MANIFEST",
        "SWIFT_DRIVER_SWIFT_FRONTEND_EXEC",
        "SWIFTPM_PURIFIER_RUNTIME_MODE",
    ):
        env.pop(key, None)

    def run(label, command, cwd=work, timeout=240):
        row = {"label": label, "startedMonotonicNs": time.monotonic_ns()}
        receipt["commands"].append(row)
        receipt["activeCommand"] = label
        result = bounded_native.run_recorded(
            command,
            record=row,
            logs=work / "logs",
            save=save,
            cwd=cwd,
            env=env,
            timeout=timeout,
            output_limit=16 * 1024 * 1024,
        )
        row["finishedMonotonicNs"] = time.monotonic_ns()
        receipt["activeCommand"] = None
        save()
        if result.returncode:
            raise RuntimeError(
                label + ": " + result.stderr.decode(errors="replace")[-3000:]
            )
        return result.stdout

    receipt["sourceRevision"] = (
        run("source-revision", ["git", "rev-parse", "HEAD"], root).decode().strip()
    )
    receipt["sourceStatus"] = run(
        "source-status", ["git", "status", "--porcelain=v1"], root
    ).decode()
    assert not receipt["sourceStatus"], (
        "run the native lifecycle from a frozen clean source"
    )
    names = run("source-files", ["git", "ls-files", "-z"], root).decode().split("\0")
    receipt["sourceFiles"] = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in names
        if name and (root / name).is_file()
    }
    receipt["compilerVersion"] = run(
        "compiler-version", [str(runtime / "bin/swiftc"), "--version"]
    ).decode()
    receipt["packageVersion"] = run(
        "package-version", [str(runtime / "bin/swift-package"), "--version"]
    ).decode()
    assert "Swift version 3.1.1" in receipt["compilerVersion"]
    assert receipt["packageVersion"].strip() == "Swift Package Manager - Swift 3.1.0"
    common = """let
      flake = builtins.getFlake ("path:" + builtins.getEnv "NATIVE_SOURCE");
      pkgs = import flake.inputs.nixpkgs { system = builtins.currentSystem; overlays = [ flake.overlays.default ]; };
    in """
    built = json.loads(
        run(
            "build-local-cli",
            [
                "nix",
                "build",
                "--impure",
                "--no-link",
                "--json",
                "--expr",
                common + "pkgs.swiftpm-purifier.cli",
            ],
            timeout=1200,
        )
    )
    cli = str(Path(built[0]["outputs"]["out"]) / "bin/swiftpm-purifier")

    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    leaf = work / "leaf"
    write(
        leaf / "Package.swift",
        '// swift-tools-version:3.1\nimport PackageDescription\nlet package=Package(name:"LegacyLeaf")\n',
    )
    write(
        leaf / "Sources/LegacyLeaf/Leaf.swift",
        "// " + uuid.uuid4().hex + "\npublic func leafAnswer()->Int{return 42}\n",
    )
    for label, command in (
        ("git-init", ["git", "init", "-q", "--object-format=sha1"]),
        ("git-add", ["git", "add", "."]),
        ("git-commit", ["git", "commit", "-m", "historical fixture"]),
        ("git-tag", ["git", "tag", "1.0.0"]),
    ):
        run(label, command, leaf)
    run(
        "git-bare", ["git", "clone", "--bare", str(leaf), str(work / "remote/Leaf.git")]
    )
    run(
        "git-http",
        ["git", "--git-dir", str(work / "remote/Leaf.git"), "update-server-info"],
    )

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *arguments):
            pass

        def send_response(self, code, message=None):
            self.event["status"] = code
            super().send_response(code, message)

        def copyfile(self, source, outputfile):
            digest = hashlib.sha256()
            offset = source.tell()
            source.seek(0, os.SEEK_END)
            self.event["expectedBytes"] = source.tell() - offset
            source.seek(offset)
            while chunk := source.read(64 * 1024):
                outputfile.write(chunk)
                self.event["bytes"] += len(chunk)
                digest.update(chunk)
            self.event["bodySha256"] = digest.hexdigest()
            self.event["bodyComplete"] = (
                self.event["bytes"] == self.event["expectedBytes"]
            )

        def record_request(self, method, action):
            self.event = {
                "method": method,
                "path": self.path,
                "phase": receipt.get("activeCommand"),
                "startedMonotonicNs": time.monotonic_ns(),
                "bytes": 0,
                "bodyComplete": False,
            }
            try:
                action()
            finally:
                self.event["finishedMonotonicNs"] = time.monotonic_ns()
                receipt["requests"].append(self.event)

        def do_GET(self):
            self.record_request("GET", super().do_GET)

        def do_HEAD(self):
            self.record_request("HEAD", super().do_HEAD)

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
        '// swift-tools-version:3.1\nimport PackageDescription\nlet package=Package(name:"OldApp",dependencies:[.Package(url:'
        + json.dumps(origin)
        + ",majorVersion:1)])\n",
    )
    write(
        project / "Sources/main.swift",
        'import LegacyLeaf\nif leafAnswer() != 42 {fatalError("wrong answer")}\nprint("ANSWER|42|compiler3")\n',
    )
    write(
        project / "Tests/OldAppTests/OldAppTests.swift",
        'import XCTest\nimport LegacyLeaf\nclass OldAppTests:XCTestCase {func testAnswer(){XCTAssertEqual(leafAnswer(),42)};static var allTests=[("testAnswer",testAnswer)]}\n',
    )
    write(
        project / "Tests/LinuxMain.swift",
        "import XCTest\n@testable import OldAppTests\nXCTMain([testCase(OldAppTests.allTests)])\n",
    )

    def inputs(directory):
        return {
            str(path.relative_to(directory)): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in directory.rglob("*")
            if path.is_file()
            and not any(
                part in (".build", "Packages", ".git")
                for part in path.relative_to(directory).parts
            )
        }

    try:
        run(
            "existing-runtime-fetch",
            [str(runtime / "bin/swift-package"), "fetch"],
            project,
        )
        native = project / ".build/workspace-state.json"
        (work / "native-workspace-state.json").write_bytes(native.read_bytes())
        assert json.loads(native.read_text())["version"] == 1
        baseline = inputs(project)
        receipt["inputsBeforeCapture"] = baseline
        shutil.copytree(
            project,
            work / "fixture-before-capture",
            ignore=shutil.ignore_patterns(".build", ".swiftpm", "Packages"),
        )
        run("local-lock", [cli, "lock"], project)
        lock = json.loads((project / "swiftpm-purifier.lock.json").read_text())
        assert (
            lock["version"] == 6
            and lock["workspaceStateVersion"] == 1
            and "nativeSDK" not in lock
        )
        assert all(inputs(project)[name] == digest for name, digest in baseline.items())
        baseline = inputs(project)
        receipt["inputsAfterCapture"] = baseline
        receipt["requestsAfterLock"] = len(receipt["requests"])
        run("cold-prefetch", [cli, "prefetch"], project)
        command = receipt["commands"][-1]
        successful = [
            event
            for event in receipt["requests"]
            if event["phase"] == "cold-prefetch"
            and event["method"] == "GET"
            and re.fullmatch(
                r"/Leaf\.git/objects/(?:[0-9a-f]{2}/[0-9a-f]{38}|pack/pack-[0-9a-f]{40}\.pack)",
                event["path"],
            )
            and event.get("status") == 200
            and event["bytes"] > 0
            and event["bodyComplete"]
            and command["startedMonotonicNs"] <= event["startedMonotonicNs"]
            and event["finishedMonotonicNs"] <= command["finishedMonotonicNs"]
        ]
        assert successful, (
            "cold Nix prefetch saw no complete successful Git object response"
        )
        receipt["successfulColdObjectResponses"] = successful
        receipt["requestsAfterPrefetch"] = len(receipt["requests"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        receipt["originClosed"] = True
        save()
    for path in (project / "Packages").iterdir():
        assert path.is_symlink() and str(path.resolve()).startswith("/nix/store/")
        assert not (path / ".git").exists()
    relocated = work / "relocated"
    shutil.move(str(project), relocated)
    for label in ("build", "test", "run", "package"):
        arguments = ["package", "clean"] if label == "package" else [label]
        out = run(
            "relocated-" + label,
            [str(relocated / ".build/debug/OldApp")]
            if label == "run"
            else [cli, "swift", *arguments],
            relocated,
        )
        if label == "run":
            assert b"ANSWER|42|compiler3" in out
    receipt["inputsAfterReplay"] = inputs(relocated)
    assert receipt["inputsAfterReplay"] == baseline
    if args.pure:
        env["NATIVE_PROJECT"] = str(relocated)
        expression = (
            common
            + """let
          swiftPackages = import (flake.outPath + "/tests/sdk/swift311/package-set.nix") { inherit pkgs; };
        in ((pkgs.swiftpm-purifier.buildSwiftPMProject.override { inherit swiftPackages; }) {
          buildConfig = "debug";
          src = builtins.path { path = /. + builtins.getEnv "NATIVE_PROJECT"; name = "legacy-project";
            filter = path: type: !(builtins.elem (baseNameOf path) [ ".build" ".swiftpm" "Packages" ]); };
        }).tests"""
        )
        run(
            "pure-build-test",
            ["nix", "build", "--impure", "--no-link", "--json", "--expr", expression],
            timeout=1200,
        )
    for path in (relocated / "Packages").iterdir():
        assert (
            path.is_symlink()
            and str(path.resolve()).startswith("/nix/store/")
            and not (path / ".git").exists()
        )
    receipt.update(complete=True, inputsUnchanged=True, projectionWithoutVCS=True)
    save()


if __name__ == "__main__":
    main()
