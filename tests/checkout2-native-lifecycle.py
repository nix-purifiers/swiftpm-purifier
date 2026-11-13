#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Real original-Swift4 capture -> VCS-free pure builds/tests/installed consumers."""

import argparse
import hashlib
import http.server
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import urllib.parse

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "pkgs/cli"))
import dependencies_v1_capture as capture
import dependencies_v1_selectors as selectors

SDK = None
TOOLS = {name: shutil.which(name) for name in ("git", "nix", "nix-prefetch-git")}


def main():
    global SDK
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--runtime-prefix", required=True, type=Path)
    parser.add_argument("--cli", type=Path, required=True, help="installed local CLI")
    parser.add_argument(
        "--nix-prefetch-git", type=Path, default=TOOLS["nix-prefetch-git"]
    )
    parser.add_argument(
        "--transitive",
        action="store_true",
        help="Root -> Alpha -> Beta actual remote graph",
    )
    parser.add_argument("--external", action="store_true")
    parser.add_argument(
        "--sdk-epoch", choices=("swift413", "swift503", "swift515"), default="swift413"
    )
    args = parser.parse_args()
    compiler, package_manager = {
        "swift413": ("4.1.3", "4.1.0"),
        "swift503": ("5.0.3", "5.0.0"),
        "swift515": ("5.1.5", "5.1.0"),
    }[args.sdk_epoch]
    SDK = args.runtime_prefix.resolve(strict=True)
    if args.nix_prefetch_git is None:
        parser.error("provide the existing nix-prefetch-git executable")
    TOOLS["nix-prefetch-git"] = str(args.nix_prefetch_git)
    work = args.work.resolve()
    work.mkdir(parents=True)
    (work / "logs").mkdir()
    (work / "tmp").mkdir()
    (work / "home").mkdir()
    env = dict(
        os.environ,
        PATH=str(SDK / "bin") + ":" + os.environ["PATH"],
        TMPDIR=str(work / "tmp"),
        HOME=str(work / "home"),
        NO_PROXY="127.0.0.1,localhost",
        GIT_AUTHOR_NAME="Pure fixture",
        GIT_AUTHOR_EMAIL="pure@example.invalid",
        GIT_COMMITTER_NAME="Pure fixture",
        GIT_COMMITTER_EMAIL="pure@example.invalid",
    )
    for name in (
        "CC",
        "CXX",
        "SWIFT_EXEC",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "CPATH",
        "LIBRARY_PATH",
    ):
        env.pop(name, None)
        os.environ.pop(name, None)
    os.environ.update(env)

    def source_manifest():
        return [
            {
                "path": str(path.relative_to(REPO)),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted(REPO.rglob("*"))
            if not {".git", "__pycache__"}.intersection(path.relative_to(REPO).parts)
            and path.is_file()
        ]

    implementation_files = source_manifest()
    receipt = {
        "implementationSource": str(REPO),
        "implementationFiles": implementation_files,
        "complete": False,
        "scope": "Original Swift4.1.3 native-only first probe; no portable format or lifecycle claim.",
        "commands": [],
        "captures": [],
        "consumers": [],
    }

    def save():
        (work / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    def run(label, command, cwd=work, expected=0):
        command = list(map(str, command))
        result = subprocess.run(
            command, cwd=cwd, env=env, capture_output=True, timeout=600
        )
        (work / "logs" / (label + ".stdout")).write_bytes(result.stdout)
        (work / "logs" / (label + ".stderr")).write_bytes(result.stderr)
        receipt["commands"].append(
            {
                "label": label,
                "argv": command,
                "cwd": str(cwd),
                "exit": result.returncode,
                "stdoutSHA256": hashlib.sha256(result.stdout).hexdigest(),
                "stderrSHA256": hashlib.sha256(result.stderr).hexdigest(),
            }
        )
        save()
        assert result.returncode == expected, (
            label,
            "stdout tail: " + result.stdout[-6000:].decode(errors="replace"),
            "stderr tail: " + result.stderr[-6000:].decode(errors="replace"),
        )
        return result.stdout

    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    compiler_raw = run("sdk-version", [SDK / "bin/swift", "--version"])
    observed_compiler = (
        re.search(rb"Swift version ([0-9]+\.[0-9]+\.[0-9]+)\b", compiler_raw)
        .group(1)
        .decode()
    )
    assert observed_compiler == compiler, compiler_raw
    receipt["compilerObserved"] = observed_compiler
    package_raw = run("sdk-package-version", [SDK / "bin/swift-package", "--version"])
    observed_package = (
        re.search(
            rb"Swift Package Manager - Swift ([0-9]+\.[0-9]+\.[0-9]+)\b", package_raw
        )
        .group(1)
        .decode()
    )
    assert observed_package == package_manager, package_raw
    receipt["packageManagerObserved"] = observed_package
    receipt["sdkEpoch"] = args.sdk_epoch
    remote = work / "remote"
    remote.mkdir()
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.backend()

        def do_POST(self):
            self.backend()

        def backend(self):
            url = urllib.parse.urlsplit(self.path)
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            backend_env = dict(
                env,
                GIT_PROJECT_ROOT=str(remote),
                GIT_HTTP_EXPORT_ALL="1",
                PATH_INFO=url.path,
                QUERY_STRING=url.query,
                REQUEST_METHOD=self.command,
                CONTENT_TYPE=self.headers.get("Content-Type", ""),
                CONTENT_LENGTH=str(len(body)),
                REMOTE_ADDR="127.0.0.1",
            )
            result = subprocess.run(
                [TOOLS["git"], "http-backend"],
                input=body,
                capture_output=True,
                env=backend_env,
                timeout=30,
                check=True,
            )
            headers, payload = result.stdout.split(b"\r\n\r\n", 1)
            requests.append({"method": self.command, "path": self.path})
            self.send_response(200)
            for line in headers.split(b"\r\n"):
                key, value = line.decode().split(":", 1)
                if key.lower() != "status":
                    self.send_header(key, value.strip())
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = "http://127.0.0.1:" + str(server.server_port)
    try:
        for module, package, value in (
            ("Alpha", "AlphaPkg", 20),
            ("Beta", "BetaPkg", 22),
        ):
            source = work / (module + "-origin")
            write(
                source / "Package.swift",
                "// swift-tools-version:4.0\nimport PackageDescription\nlet package=Package(name:"
                + json.dumps(package)
                + ",products:[.library(name:"
                + json.dumps(module + "Lib")
                + ",targets:["
                + json.dumps(module)
                + "])],targets:[.target(name:"
                + json.dumps(module)
                + ")])\n",
            )
            write(
                source / "Sources" / module / (module + ".swift"),
                "public func "
                + module.lower()
                + "() -> Int { return "
                + str(value)
                + " }\n",
            )
            if args.transitive and module == "Alpha":
                write(
                    source / "Package.swift",
                    '// swift-tools-version:4.0\nimport PackageDescription\nlet package=Package(name:"AlphaPkg",products:[.library(name:"AlphaLib",targets:["Alpha"])],dependencies:[.package(url:'
                    + json.dumps(origin + "/Beta.git")
                    + ',from:"1.0.0")],targets:[.target(name:"Alpha",dependencies:[.product(name:"BetaLib",package:"Beta")])])\n',
                )
                write(
                    source / "Sources/Alpha/Alpha.swift",
                    "import Beta\npublic func alpha() -> Int { return beta()-2 }\n",
                )
            # Fresh real Git identity, never a doctored captured URL/revision.
            write(source / "fixture-nonce", str(work) + "\n")
            for suffix, command in (
                ("init", [TOOLS["git"], "init", "-b", "main", source]),
                ("add", [TOOLS["git"], "-C", source, "add", "."]),
                (
                    "commit",
                    [
                        TOOLS["git"],
                        "-C",
                        source,
                        "commit",
                        "-m",
                        "Actual original Swift4 pure fixture",
                    ],
                ),
                ("tag", [TOOLS["git"], "-C", source, "tag", "1.0.0"]),
                (
                    "bare",
                    [
                        TOOLS["git"],
                        "clone",
                        "--bare",
                        source,
                        remote / (module + ".git"),
                    ],
                ),
            ):
                run(module + "-" + suffix, command)
        root = work / "project"
        write(
            root / "Package.swift",
            """// swift-tools-version:4.0
import PackageDescription
let package=Package(name:"PureRoot",products:[.library(name:"Static",type:.static,targets:["Core"]),.library(name:"Dynamic",type:.dynamic,targets:["Core"]),.library(name:"Automatic",targets:["Core"]),.executable(name:"PureApp",targets:["Run"])],dependencies:[.package(url:URL_ALPHA,from:"1.0.0"),.package(url:URL_BETA,from:"1.0.0")],targets:[.target(name:"Core",dependencies:[.product(name:"AlphaLib",package:"Alpha"),.product(name:"BetaLib",package:"Beta")]),.target(name:"Run",dependencies:["Core"]),.target(name:"Extra",dependencies:["Core"]),.testTarget(name:"CoreTests",dependencies:["Core"])])
""".replace("URL_ALPHA", json.dumps(origin + "/Alpha.git")).replace(
                "URL_BETA", json.dumps(origin + "/Beta.git")
            ),
        )
        if args.transitive:
            package_manifest = root / "Package.swift"
            text = package_manifest.read_text()
            text = text.replace(
                ","
                + ".package(url:"
                + json.dumps(origin + "/Beta.git")
                + ',from:"1.0.0")',
                "",
            )
            text = text.replace(',.product(name:"BetaLib",package:"Beta")', "")
            package_manifest.write_text(text)
        write(
            root / "Sources/Core/AlphaBridge.swift",
            "import Alpha\npublic func left() -> Int { return alpha() }\n",
        )
        write(
            root / "Sources/Core/BetaBridge.swift",
            "import Beta\npublic func right() -> Int { return beta() }\n",
        )
        if args.transitive:
            write(
                root / "Sources/Core/BetaBridge.swift",
                "public func right() -> Int { return 22 }\n",
            )
        receipt["graph"] = (
            "root -> Alpha -> Beta" if args.transitive else "root -> Alpha, Beta"
        )
        write(
            root / "Sources/Core/Core.swift",
            "public func answer() -> Int { return left()+right() }\n",
        )
        for target in ("Run", "Extra"):
            write(
                root / "Sources" / target / "main.swift",
                'import Core\nprint("ANSWER|\\(answer())")\n',
            )
        write(
            root / "Tests/CoreTests/CoreTests.swift",
            'import XCTest\n@testable import Core\nclass CoreTests:XCTestCase { func testAnswer() { XCTAssertEqual(answer(),42) }; static var allTests=[("testAnswer",testAnswer)] }\n',
        )
        write(
            root / "Tests/LinuxMain.swift",
            "import XCTest\n@testable import CoreTests\nXCTMain([testCase(CoreTests.allTests)])\n",
        )
        (root / "empty-directory").mkdir()
        run("native-fetch", [SDK / "bin/swift-package", "fetch"], root)
        run("native-build", [SDK / "bin/swift-build"], root)
        run("native-test", [SDK / "bin/swift-test"], root)
        observed = work / "observed-native"
        observed.mkdir()
        records = []
        for name in (
            "Package.resolved",
            "Package.pins",
            ".build/dependencies-state.json",
            ".build/workspace-state.json",
            ".build/repositories/checkouts-state.json",
            ".build/debug.yaml",
            ".build/x86_64-unknown-linux/debug.yaml",
        ):
            path = root / name
            if not path.is_file():
                records.append({"path": name, "present": False})
                continue
            raw = path.read_bytes()
            (observed / name.replace("/", "--")).write_bytes(raw)
            row = {
                "path": name,
                "present": True,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
            if name.endswith(".json") or name in ("Package.resolved", "Package.pins"):
                value = json.loads(raw)
                row["topKeys"] = sorted(value)
                row["version"] = value.get("version")
            records.append(row)
        assert source_manifest() == implementation_files
        receipt.update(
            complete=True,
            scope=f"Original Swift{compiler}/SwiftPM{package_manager} native-only transitive graph; no purifier lifecycle executed yet.",
            observedNative=records,
            requests=requests,
        )
        receipt["complete"] = False
        save()
        cli = args.cli.resolve()
        fixture = work / "host-default/project"
        fixture.parent.mkdir()
        shutil.copytree(
            root,
            fixture,
            symlinks=True,
            ignore=lambda parent, names: (
                [".build"] if Path(parent) == root and ".build" in names else []
            ),
        )
        original_pins = (fixture / "Package.resolved").read_bytes()
        env.update(
            SWIFTPM_PURIFIER_RUNTIME_MODE="local",
            SWIFTPM_PURIFIER_FLAKE=str(REPO),
            SWIFTPM_PURIFIER_SWIFT=str(SDK / "bin/swift"),
            SWIFTPM_PURIFIER_NIX=TOOLS["nix"],
            SWIFTPM_PURIFIER_GIT=TOOLS["git"],
            SWIFTPM_PURIFIER_NIX_PREFETCH_GIT=TOOLS["nix-prefetch-git"],
            XDG_STATE_HOME=str(work / "host-state"),
        )
        command = [cli, "--package-path", fixture]
        selected_native = fixture / "Package.resolved"
        selected_portable = fixture / "swiftpm-purifier.lock.json"
        logical_bytes = original_pins
        if args.external:
            native_parent, portable_parent = (
                work / "external-native",
                work / "external-portable",
            )
            native_parent.mkdir()
            portable_parent.mkdir()
            selected_native, selected_portable = (
                native_parent / "pins.json",
                portable_parent / "lock.json",
            )
            selected_native.write_bytes(original_pins + b"\n")
            logical_bytes = b"unselected logical input\n"
            (fixture / "Package.resolved").write_bytes(logical_bytes)
            (fixture / "Package.resolved").chmod(0o600)
            command += [
                "--lockfile",
                selected_portable,
                "--tool-lockfile",
                selected_native,
            ]
        run("host-relock", [*command, "relock"])
        if args.external:
            selected_native.chmod(0o600)
            selected_portable.chmod(0o600)
            selected_before = [
                (p.read_bytes(), p.stat().st_ino)
                for p in (selected_native, selected_portable)
            ]
            native_parent.chmod(0o500)
            portable_parent.chmod(0o500)
        portable = json.loads(selected_portable.read_text())
        assert portable["version"] == 4
        assert not (fixture / ".build").exists()
        receipt["coldBeforePrefetch"] = True
        save()
        pure_source = work / "pure-source"
        shutil.copytree(fixture, pure_source, symlinks=True)
        (pure_source / "swiftpm-purifier.lock.json").write_bytes(
            selected_portable.read_bytes()
        )
        selection_args = (
            [
                "--argstr",
                "selector",
                "external",
                "--argstr",
                "nativePath",
                selected_native,
            ]
            if args.external
            else []
        )
        outputs = (
            run(
                "pure-selected",
                [
                    TOOLS["nix"],
                    "build",
                    "--impure",
                    "--no-link",
                    "--print-out-paths",
                    "-L",
                    "--file",
                    REPO / "tests/checkout2-pure.nix",
                    "--argstr",
                    "sourcePath",
                    pure_source,
                    "--argstr",
                    "sdkEpoch",
                    args.sdk_epoch,
                    *selection_args,
                    "project",
                    "tests",
                    "packages.Static",
                    "packages.Dynamic",
                    "packages.Automatic",
                    "packages.PureApp",
                ],
            )
            .decode()
            .splitlines()
        )
        assert len(outputs) == 6, outputs
        for name, output in zip(("PureApp",), outputs[-1:]):
            assert (
                run("pure-executable-" + name, [Path(output) / "bin" / name])
                == b"ANSWER|42\n"
            )
        consumer = work / "consumer.swift"
        consumer.write_text("import Core\nprint(answer())\n")
        for name, output in zip(("Static", "Dynamic"), outputs[2:4]):
            output = Path(output)
            binary = work / ("consumer-" + name)
            library = (
                output / "lib" / ("lib" + name + (".a" if name == "Static" else ".so"))
            )
            run(
                "consumer-compile-" + name,
                [
                    SDK / "bin/swiftc",
                    "-I",
                    output / "lib/swift/linux",
                    consumer,
                    library,
                    "-Xlinker",
                    "-rpath",
                    "-Xlinker",
                    output / "lib",
                    "-o",
                    binary,
                ],
            )
            assert run("consumer-run-" + name, [binary]) == b"42\n"
        receipt["pureOutputs"] = outputs
        save()
        run("host-prefetch", [*command, "prefetch"])
        run("host-build", [*command, "swift", "build"])
        run("host-test", [*command, "swift", "test"])
        run("host-clean", [*command, "swift", "package", "clean"])
        run("host-rebuild", [*command, "swift", "build"])
        run("host-uninstall", [*command, "uninstall"])
        assert not (fixture / "Packages").exists() or not list(
            (fixture / "Packages").iterdir()
        )
        assert (fixture / "Package.resolved").read_bytes() == logical_bytes
        assert source_manifest() == implementation_files
        capsules = sorted(
            (work / "tmp").glob("swiftpm-purifier-legacy-runtime-*/receipt.json")
        )
        assert capsules
        assert all(json.loads(path.read_text())["complete"] for path in capsules)
        receipt["runtimeCapsules"] = [
            {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in capsules
        ]
        if args.external:
            for selected, (raw, inode) in zip(
                (selected_native, selected_portable), selected_before
            ):
                assert selected.read_bytes() == raw and selected.stat().st_ino == inode
                assert selected.stat().st_mode & 0o777 == 0o600
            assert all(
                p.stat().st_mode & 0o777 == 0o500
                for p in (native_parent, portable_parent)
            )
            assert (fixture / "Package.resolved").stat().st_mode & 0o777 == 0o600
        receipt.update(
            complete=True,
            scope=f"Original Swift{compiler}/SwiftPM{package_manager} selected format4 cold host lifecycle, pure XCTest and explicit product consumers; automatic output is source archive; no inferred product, interval or Darwin claim.",
            hostCLI={
                "executable": str(cli),
                "portableVersion": 4,
                "originalPinsRestored": True,
            },
        )
        save()
        return
    finally:
        if "native_parent" in locals():
            native_parent.chmod(0o700)
            portable_parent.chmod(0o700)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
