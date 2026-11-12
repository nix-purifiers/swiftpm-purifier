#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Real original-Swift4 capture -> VCS-free pure builds/tests/installed consumers."""
import argparse
import hashlib
import http.server
import json
import os
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

SDK = Path("/nix/store/1hnldmhza7zqfb6m12m9q3lmzki2z0x3-original-swift-sdk-4.0.3")
TOOLS = {name: shutil.which(name) for name in ("git", "nix", "nix-prefetch-git")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--nix-prefetch-git", type=Path, default=TOOLS["nix-prefetch-git"])
    parser.add_argument("--transitive", action="store_true", help="Root -> Alpha -> Beta actual remote graph")
    args = parser.parse_args()
    if args.nix_prefetch_git is None:
        parser.error("provide the existing nix-prefetch-git executable")
    TOOLS["nix-prefetch-git"] = str(args.nix_prefetch_git)
    work = args.work.resolve()
    work.mkdir(parents=True)
    (work / "logs").mkdir()
    (work / "tmp").mkdir()
    (work / "home").mkdir()
    env = dict(os.environ, PATH=str(SDK / "bin") + ":" + os.environ["PATH"], TMPDIR=str(work / "tmp"), HOME=str(work / "home"), NO_PROXY="127.0.0.1,localhost", GIT_AUTHOR_NAME="Pure fixture", GIT_AUTHOR_EMAIL="pure@example.invalid", GIT_COMMITTER_NAME="Pure fixture", GIT_COMMITTER_EMAIL="pure@example.invalid")
    for name in ("CC", "CXX", "SWIFT_EXEC", "LD_LIBRARY_PATH", "LD_PRELOAD", "CPATH", "LIBRARY_PATH"):
        env.pop(name, None)
        os.environ.pop(name, None)
    os.environ.update(env)
    receipt = {"complete": False, "scope": "private explicit original SDK 4.0.3 Linux fixture only; no host lifecycle/public support claim", "commands": [], "captures": [], "consumers": []}

    def save():
        (work / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    def run(label, command, cwd=work, expected=0):
        command = list(map(str, command))
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=600)
        (work / "logs" / (label + ".stdout")).write_bytes(result.stdout)
        (work / "logs" / (label + ".stderr")).write_bytes(result.stderr)
        receipt["commands"].append({"label": label, "argv": command, "cwd": str(cwd), "exit": result.returncode})
        save()
        assert result.returncode == expected, (label, result.stderr[-3000:].decode(errors="replace"))
        return result.stdout

    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    run("sdk-version", [SDK / "bin/swift", "--version"])
    run("sdk-package-version", [SDK / "bin/swift-package", "--version"])
    remote = work / "remote"
    remote.mkdir()
    requests = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self): self.backend()
        def do_POST(self): self.backend()
        def backend(self):
            url = urllib.parse.urlsplit(self.path)
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            backend_env = dict(env, GIT_PROJECT_ROOT=str(remote), GIT_HTTP_EXPORT_ALL="1", PATH_INFO=url.path, QUERY_STRING=url.query, REQUEST_METHOD=self.command, CONTENT_TYPE=self.headers.get("Content-Type", ""), CONTENT_LENGTH=str(len(body)), REMOTE_ADDR="127.0.0.1")
            result = subprocess.run([TOOLS["git"], "http-backend"], input=body, capture_output=True, env=backend_env, timeout=30, check=True)
            headers, payload = result.stdout.split(b"\r\n\r\n", 1)
            requests.append({"method": self.command, "path": self.path})
            self.send_response(200)
            for line in headers.split(b"\r\n"):
                key, value = line.decode().split(":", 1)
                if key.lower() != "status": self.send_header(key, value.strip())
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = "http://127.0.0.1:" + str(server.server_port)
    try:
        for module, package, value in (("Alpha", "AlphaPkg", 20), ("Beta", "BetaPkg", 22)):
            source = work / (module + "-origin")
            write(source / "Package.swift", '// swift-tools-version:4.0\nimport PackageDescription\nlet package=Package(name:' + json.dumps(package) + ',products:[.library(name:' + json.dumps(module + "Lib") + ',targets:[' + json.dumps(module) + '])],targets:[.target(name:' + json.dumps(module) + ')])\n')
            write(source / "Sources" / module / (module + ".swift"), "public func " + module.lower() + "() -> Int { return " + str(value) + " }\n")
            if args.transitive and module == "Alpha":
                write(source / "Package.swift", '// swift-tools-version:4.0\nimport PackageDescription\nlet package=Package(name:"AlphaPkg",products:[.library(name:"AlphaLib",targets:["Alpha"])],dependencies:[.package(url:' + json.dumps(origin + "/Beta.git") + ',from:"1.0.0")],targets:[.target(name:"Alpha",dependencies:[.product(name:"BetaLib",package:"BetaPkg")])])\n')
                write(source / "Sources/Alpha/Alpha.swift", "import Beta\npublic func alpha() -> Int { return beta()-2 }\n")
            # Fresh real Git identity, never a doctored captured URL/revision.
            write(source / "fixture-nonce", str(work) + "\n")
            for suffix, command in (("init", [TOOLS["git"], "init", "-b", "main", source]), ("add", [TOOLS["git"], "-C", source, "add", "."]), ("commit", [TOOLS["git"], "-C", source, "commit", "-m", "Actual original Swift4 pure fixture"]), ("tag", [TOOLS["git"], "-C", source, "tag", "1.0.0"]), ("bare", [TOOLS["git"], "clone", "--bare", source, remote / (module + ".git")])):
                run(module + "-" + suffix, command)
        root = work / "project"
        write(root / "Package.swift", '''// swift-tools-version:4.0
import PackageDescription
let package=Package(name:"PureRoot",products:[.library(name:"Static",type:.static,targets:["Core"]),.library(name:"Dynamic",type:.dynamic,targets:["Core"]),.library(name:"Automatic",targets:["Core"]),.executable(name:"PureApp",targets:["Run"])],dependencies:[.package(url:URL_ALPHA,from:"1.0.0"),.package(url:URL_BETA,from:"1.0.0")],targets:[.target(name:"Core",dependencies:[.product(name:"AlphaLib",package:"AlphaPkg"),.product(name:"BetaLib",package:"BetaPkg")]),.target(name:"Run",dependencies:["Core"]),.target(name:"Extra",dependencies:["Core"]),.testTarget(name:"CoreTests",dependencies:["Core"])])
'''.replace("URL_ALPHA", json.dumps(origin + "/Alpha.git")).replace("URL_BETA", json.dumps(origin + "/Beta.git")))
        if args.transitive:
            package_manifest = root / "Package.swift"
            text = package_manifest.read_text()
            text = text.replace(',' + '.package(url:' + json.dumps(origin + "/Beta.git") + ',from:"1.0.0")', '')
            text = text.replace(',.product(name:"BetaLib",package:"BetaPkg")', '')
            package_manifest.write_text(text)
        write(root / "Sources/Core/AlphaBridge.swift", "import Alpha\npublic func left() -> Int { return alpha() }\n")
        write(root / "Sources/Core/BetaBridge.swift", "import Beta\npublic func right() -> Int { return beta() }\n")
        if args.transitive:
            write(root / "Sources/Core/BetaBridge.swift", "public func right() -> Int { return 22 }\n")
        receipt["graph"] = "root -> Alpha -> Beta" if args.transitive else "root -> Alpha, Beta"
        write(root / "Sources/Core/Core.swift", "public func answer() -> Int { return left()+right() }\n")
        for target in ("Run", "Extra"):
            write(root / "Sources" / target / "main.swift", 'import Core\nprint("ANSWER|\\(answer())")\n')
        write(root / "Tests/CoreTests/CoreTests.swift", "import XCTest\n@testable import Core\nclass CoreTests:XCTestCase { func testAnswer() { XCTAssertEqual(answer(),42) }; static var allTests=[(\"testAnswer\",testAnswer)] }\n")
        write(root / "Tests/LinuxMain.swift", "import XCTest\n@testable import CoreTests\nXCTMain([testCase(CoreTests.allTests)])\n")
        (root / "empty-directory").mkdir()
        run("native-fetch", [SDK / "bin/swift-package", "fetch"], root)
        run("native-build", [SDK / "bin/swift-build"], root)
        run("native-test", [SDK / "bin/swift-test"], root)
        before = run("caller-before", [TOOLS["nix"], "hash", "path", root]).decode().strip()
        original_pins = (root / "Package.resolved").read_bytes()
        class API:
            @staticmethod
            def sdk_mode(): return False
            @staticmethod
            def native_command(name): return [str(SDK / "bin" / name)]
            @staticmethod
            def tool(name): return TOOLS[name]
        for selection in ("default", "null", "external"):
            temporary = work / ("capture-" + selection)
            temporary.mkdir()
            kwargs = {}
            selected = work / "selected-external.json"
            if selection == "null": kwargs["native"] = None
            elif selection == "external":
                selected.write_bytes(original_pins + b"\n")
                kwargs["native"] = selected
            lock, native_raw, session = capture.prepare(root, temporary, API(), **kwargs)
            fixture = work / ("pure-" + selection) / "project"
            shutil.copytree(root, fixture, ignore=shutil.ignore_patterns(".build", ".git", "Packages"))
            (fixture / "swiftpm-purifier.lock.json").write_text(json.dumps(lock, sort_keys=True) + "\n")
            # Deliberately retain unrelated logical bytes for null/external.
            if selection != "default": (fixture / "Package.resolved").write_bytes(b"unselected logical file\n")
            assert not (fixture / ".build").exists() and not (fixture / ".git").exists() and not (fixture / "Packages").exists()
            receipt["captures"].append({"selection": selection, "source": str(fixture), "evidence": str(session.evidence), "contentHash": lock["contentHash"], "pinsHash": lock["nativeToolLock"]["sha256"], "products": lock["products"]})
            save()
            command = [TOOLS["nix"], "build", "--impure", "--no-link", "--print-out-paths", "-L", "--file", REPO / "tests/dependencies-v1-pure.nix", "--argstr", "sourcePath", fixture, "--argstr", "selector", selection]
            if selection == "external": command += ["--argstr", "nativePath", selected]
            count = len(requests)
            outputs = run("pure-" + selection, command + ["project", "tests", "packages.Static", "packages.Dynamic", "packages.Automatic", "packages.PureApp", "packages.Extra"]).decode().splitlines()
            assert len(outputs) == 7, outputs
            project, tests, static, dynamic, automatic, executable, inferred = map(Path, outputs)
            if selection == "default":
                assert len(requests) > count, "expected actual independent consumer Git fetch"
            for name, output in (("explicit", executable), ("inferred", inferred)):
                binary = output / "bin" / ("PureApp" if name == "explicit" else "Extra")
                assert run(selection + "-" + name, [binary]) == b"ANSWER|42\n"
            consumer = work / (selection + "-consumer.swift")
            consumer.write_text("import Core\nprint(answer())\n")
            for name, output in (("static", static), ("dynamic", dynamic), ("automatic", automatic)):
                record = json.loads((output / "share/swiftpm-purifier/products.json").read_text())[0]
                objects = [output / value for value in record["objectFiles"]]
                assert len(objects) == 5 and set(record["modules"]) == {"Alpha", "Beta", "Core"}
                if name == "automatic":
                    assert record["artifact"] is None and not list((output / "lib").glob("libAutomatic.*"))
                    inputs = objects
                else: inputs = [output / record["artifact"]]
                binary = work / (selection + "-" + name)
                run(selection + "-compile-" + name, [SDK / "bin/swiftc", "-I", output / "lib/swift/linux", consumer, *inputs, "-Xlinker", "-rpath", "-Xlinker", output / "lib", "-o", binary])
                assert run(selection + "-consumer-" + name, [binary]) == b"42\n"
                receipt["consumers"].append({"selection": selection, "linkage": name, "output": str(output), "objectCount": len(objects), "answer": 42})
            receipt["captures"][-1].update(project=str(project), tests=str(tests), actualConsumerFetchRequests=len(requests)-count)
            save()
        # A well-shaped but invented native product closure must fail after
        # actual compilation, before anything is installed as that product.
        import dependencies_v1 as protocol
        bad = work / "negative-product" / "project"
        shutil.copytree(work / "pure-default/project", bad)
        value = json.loads((bad / "swiftpm-purifier.lock.json").read_bytes())
        next(p for p in value["products"] if p["name"] == "Automatic")["modules"] = ["Core"]
        value["contentHash"] = protocol.content_hash(value)
        (bad / "swiftpm-purifier.lock.json").write_text(json.dumps(value))
        negative = [TOOLS["nix"], "build", "--impure", "--no-link", "-L", "--file", REPO / "tests/dependencies-v1-pure.nix", "--argstr", "sourcePath", bad, "project"]
        run("reject-invented-product-closure", negative, expected=1)
        require_message = (work / "logs/reject-invented-product-closure.stderr").read_text()
        assert "actual native declarations/module/object closure differs from lock" in require_message
        bad_hash = work / "negative-source-hash" / "project"
        shutil.copytree(work / "pure-default/project", bad_hash)
        value = json.loads((bad_hash / "swiftpm-purifier.lock.json").read_bytes())
        value["pins"]["AlphaPkg"]["hash"] = "sha256-" + "A"*43 + "="
        value["contentHash"] = protocol.content_hash(value)
        (bad_hash / "swiftpm-purifier.lock.json").write_text(json.dumps(value))
        negative = [TOOLS["nix"], "build", "--impure", "--no-link", "-L", "--file", REPO / "tests/dependencies-v1-pure.nix", "--argstr", "sourcePath", bad_hash, "project"]
        run("reject-wrong-source-NAR", negative, expected=1)
        assert "hash mismatch" in (work / "logs/reject-wrong-source-NAR.stderr").read_text()
        # Pure flake evaluation declares each immutable input. No ambient /
        # traversal, host inode probe, IFD, or toolchain build is needed.
        external_input = work / "external-input"
        external_input.mkdir()
        (external_input / "Package.resolved").write_bytes(selected.read_bytes())
        flake = work / "reader-flake"
        flake.mkdir()
        definitions = {"implementation": REPO, "defaultSource": work/"pure-default/project", "nullSource": work/"pure-null/project", "externalSource": work/"pure-external/project", "externalPins": external_input}
        code = "{ inputs = {" + "".join(name + " = { url = " + json.dumps("path:" + str(path)) + "; flake = false; };" for name,path in definitions.items()) + "}; outputs = {self, implementation, defaultSource, nullSource, externalSource, externalPins}: let read = source: selected: (import (implementation.outPath + \"/lib-overlays/default/lock.nix\") ({ src=source.outPath; lockFile=source.outPath + \"/swiftpm-purifier.lock.json\"; } // selected)).lock.problems == []; in { valid = { default=read defaultSource {}; null=read nullSource {packageResolved=null;}; external=read externalSource {packageResolved=externalPins.outPath + \"/Package.resolved\";}; }; }; }"
        (flake / "flake.nix").write_text(code)
        run("pure-reader-flake-lock", [TOOLS["nix"], "flake", "lock", "path:"+str(flake)])
        result = json.loads(run("pure-reader-evaluation", [TOOLS["nix"], "eval", "--json", "path:"+str(flake)+"#valid"]))
        assert result == {"default": True, "null": True, "external": True}
        receipt["pureEvaluation"] = result
        assert before == run("caller-after", [TOOLS["nix"], "hash", "path", root]).decode().strip()
        assert (root / "Package.resolved").read_bytes() == original_pins
        receipt.update(complete=True, callerNARUnchanged=True, originalNativePinsUnchanged=True, requests=requests)
        save()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__": main()
