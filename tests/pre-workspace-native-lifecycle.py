# SPDX-License-Identifier: MIT
"""Cold pre-workspace Git lifecycle with an already prepared original Swift3 fixture.

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
        "scope": "Original Swift3.0.2 fixture, portable7 pre-workspace lifecycle",
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
    assert "Swift version 3.0.2" in receipt["compilerVersion"]
    assert receipt["packageVersion"].strip() == "Swift Package Manager - Swift 3.0.2"
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
        'import PackageDescription\nlet package=Package(name:"LegacyLeaf")\n',
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

    (work / "http-bodies").mkdir()

    def unpack_response(raw):
        chunks = []
        offset = 0
        while offset < len(raw):
            if raw[offset:offset+4] == b"PACK":
                chunks.append(raw[offset:])
                break
            length = int(raw[offset:offset+4], 16)
            offset += 4
            if length <= 2:
                continue
            assert length >= 4 and offset + length - 4 <= len(raw)
            payload = raw[offset:offset+length-4]
            offset += length-4
            if payload[:1] == b"\x01":
                chunks.append(payload[1:])
        pack = b"".join(chunks)
        if not pack:
            return None
        assert pack[:4] == b"PACK" and len(pack) >= 32
        assert hashlib.sha1(pack[:-20]).digest() == pack[-20:]
        return pack

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *arguments):
            pass

        def backend(self):
            import urllib.parse
            event = {"method": self.command, "path": self.path,
                "phase": receipt.get("activeCommand"),
                "startedMonotonicNs": time.monotonic_ns(), "bodyComplete": False}
            parsed = urllib.parse.urlsplit(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            assert 0 <= length <= 1024*1024
            request = self.rfile.read(length)
            assert len(request) == length
            result = bounded_native.run(["git", "http-backend"], input=request,
                env=dict(env, GIT_PROJECT_ROOT=str(work / "remote"), GIT_HTTP_EXPORT_ALL="1",
                    PATH_INFO=parsed.path, QUERY_STRING=parsed.query, REQUEST_METHOD=self.command,
                    CONTENT_TYPE=self.headers.get("Content-Type", ""), CONTENT_LENGTH=str(length),
                    REMOTE_ADDR="127.0.0.1"), timeout=30, output_limit=16*1024*1024)
            assert result.returncode == 0
            headers, body = result.stdout.split(b"\r\n\r\n", 1)
            pairs = [line.decode().split(":", 1) for line in headers.split(b"\r\n")]
            status = next((int(value.strip().split()[0]) for key, value in pairs if key.lower() == "status"), 200)
            index = uuid.uuid4().hex
            body_file = work / "http-bodies" / (index + ".body")
            body_file.write_bytes(body)
            event.update(status=status, bytes=len(body), expectedBytes=len(body),
                bodySha256=hashlib.sha256(body).hexdigest(), bodyFile=str(body_file.relative_to(work)))
            if self.command == "POST" and parsed.path.endswith("/git-upload-pack") and status == 200:
                pack = unpack_response(body)
                if pack is not None:
                    pack_file = work / "http-bodies" / (index + ".pack")
                    pack_file.write_bytes(pack)
                    event.update(packFile=str(pack_file.relative_to(work)), packSha256=hashlib.sha256(pack).hexdigest(), packBytes=len(pack))
            self.send_response(status)
            for key, value in pairs:
                if key.lower() not in ("status", "content-length"):
                    self.send_header(key, value.strip())
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            event.update(bodyComplete=True, finishedMonotonicNs=time.monotonic_ns())
            receipt["requests"].append(event)

        do_GET = backend
        do_POST = backend

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = "http://127.0.0.1:" + str(server.server_port) + "/Leaf.git"
    receipt["origin"] = origin
    project = work / "original"
    write(
        project / "Package.swift",
        'import PackageDescription\nlet package=Package(name:"OldApp",dependencies:[.Package(url:'
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
        assert not (project / "Package.pins").exists() and not (project / "Package.resolved").exists()
        assert not any((project / ".build" / name).exists() for name in ("workspace-state.json", "dependencies-state.json"))
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
            lock["version"] == 7
            and lock["nativeProtocol"] == "pre-workspace-git"
            and "nativeSDK" not in lock
        )
        assert all(inputs(project)[name] == digest for name, digest in baseline.items())
        baseline = inputs(project)
        receipt["inputsAfterCapture"] = baseline
        receipt["requestsAfterLock"] = len(receipt["requests"])
        # Existing native Packages is deliberately unowned. Replay into a
        # fresh project rather than silently overwrite the developer cache.
        replay = work / "replay"
        shutil.copytree(project, replay, ignore=shutil.ignore_patterns("Packages", ".build"))
        project = replay
        run("cold-prefetch", [cli, "prefetch"], project)
        command = receipt["commands"][-1]
        successful = [
            event
            for event in receipt["requests"]
            if event["phase"] == "cold-prefetch"
            and event["method"] == "POST"
            and event["path"] == "/Leaf.git/git-upload-pack"
            and event.get("packFile")
            and event.get("status") == 200
            and event["bytes"] > 0
            and event["bodyComplete"]
            and command["startedMonotonicNs"] <= event["startedMonotonicNs"]
            and event["finishedMonotonicNs"] <= command["finishedMonotonicNs"]
        ]
        assert successful, (
            "cold Nix prefetch saw no complete validated Git pack response"
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
        assert path.is_dir() and not path.is_symlink()
        assert (path / ".git").is_dir()
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
          swiftPackages = import (flake.outPath + "/tests/sdk/swift302/package-set.nix") { inherit pkgs; };
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
        assert path.is_dir() and not path.is_symlink() and (path / ".git").is_dir()
    receipt.update(complete=True, inputsUnchanged=True, realOwnedGitProjection=True)
    save()


if __name__ == "__main__":
    main()
