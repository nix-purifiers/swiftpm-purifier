# SPDX-License-Identifier: MIT
"""Exercise local adoption using the runtime already installed on this host.

The fixture requires Swift tools 5.5. No Swift SDK is installed or built by
this harness. The runtime is observed, never substituted or attested as a
pure-build SDK. Local replay begins only after locked artifacts are fetched.
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
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "pkgs/cli"))
    import bounded_native
    import host_runtime

    work = args.output.resolve()
    work.mkdir()
    for name in ["tmp", "logs", "home", "remote", "empty-path"]:
        (work / name).mkdir()
    original_path = os.environ.get("PATH", "")
    selected = host_runtime.discover(original_path, os.environ)
    assert not selected["commands"]["swift"].startswith("/nix/store/"), (
        "this fixture requires an existing non-store Swift installation"
    )
    assert not str(Path(selected["commands"]["swift"]).resolve()).startswith(
        "/nix/store/"
    )
    receipt = {
        "complete": False,
        "mode": "local",
        "installedRuntime": selected,
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
        GIT_AUTHOR_NAME="Host fixture",
        GIT_AUTHOR_EMAIL="fixture@example.invalid",
        GIT_COMMITTER_NAME="Host fixture",
        GIT_COMMITTER_EMAIL="fixture@example.invalid",
    )

    def run(label, command, cwd=work, timeout=240, rejection=None, child_env=None):
        row = {"label": label}
        receipt["commands"].append(row)
        result = bounded_native.run_recorded(
            command,
            record=row,
            logs=work / "logs",
            save=save,
            cwd=cwd,
            env=env if child_env is None else child_env,
            timeout=timeout,
            output_limit=16 * 1024 * 1024,
        )
        if rejection is None:
            if result.returncode:
                raise RuntimeError(
                    label + " failed: " + result.stderr.decode(errors="replace")[-3000:]
                )
        elif result.returncode == 0 or rejection not in result.stderr:
            raise RuntimeError(label + " did not report the expected native failure")
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
    env["HOST_PURIFIER_SOURCE"] = str(root)
    expression = """let
      flake = builtins.getFlake ("path:" + builtins.getEnv "HOST_PURIFIER_SOURCE");
      pkgs = import flake.inputs.nixpkgs { system = builtins.currentSystem; overlays = [ flake.overlays.default ]; };
    in pkgs.swiftpm-purifier.cli"""
    built = json.loads(
        run(
            "build-default-local-cli",
            ["nix", "build", "--impure", "--no-link", "--json", "--expr", expression],
            timeout=900,
        )
    )
    cli_root = Path(built[0]["outputs"]["out"])
    cli = str(cli_root / "bin/swiftpm-purifier")
    raw = run(
        "cli-closure", ["nix", "path-info", "--recursive", "--json", str(cli_root)]
    )
    inventory = json.loads(raw)
    paths = (
        list(inventory)
        if isinstance(inventory, dict)
        else [item["path"] for item in inventory]
    )
    assert not any(
        re.search(r"-(?:original-swift-sdk|swift|swiftpm|sourcekit-lsp)-[0-9]", path)
        for path in paths
    )
    receipt["cliClosureHasNoSwiftSDK"] = True

    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    missing = work / "missing"
    write(missing / "Package.swift", "// swift-tools-version:5.5\n")
    sentinel = (missing / "Package.swift").read_bytes()
    run(
        "missing-original-runtime",
        [cli, "--package-path", str(missing), "lock"],
        missing,
        rejection=b"no Swift runtime on the original host PATH",
        child_env=dict(env, PATH=str(work / "empty-path")),
    )
    assert (missing / "Package.swift").read_bytes() == sentinel
    assert not (missing / "swiftpm-purifier.lock.json").exists()

    leaf = work / "leaf"
    write(
        leaf / "Package.swift",
        '// swift-tools-version:5.5\nimport PackageDescription\nlet package=Package(name:"HostLeaf",products:[.library(name:"HostLeaf",targets:["HostLeaf"])],targets:[.target(name:"HostLeaf")])\n',
    )
    write(
        leaf / "Sources/HostLeaf/Leaf.swift",
        "// " + uuid.uuid4().hex + "\npublic func answer()->Int { return 42 }\n",
    )
    for label, command in [
        ("git-init", ["git", "init", "-q"]),
        ("git-add", ["git", "add", "."]),
        ("git-commit", ["git", "commit", "-m", "host fixture"]),
        ("git-tag", ["git", "tag", "1.0.0"]),
    ]:
        run(label, command, leaf)
    run(
        "git-bare",
        ["git", "clone", "--bare", str(leaf), str(work / "remote/HostLeaf.git")],
    )
    run(
        "git-http",
        ["git", "--git-dir", str(work / "remote/HostLeaf.git"), "update-server-info"],
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
    origin = "http://127.0.0.1:" + str(server.server_port) + "/HostLeaf.git"
    receipt["origin"] = origin
    project = work / "original"
    write(
        project / "Package.swift",
        '// swift-tools-version:5.5\nimport PackageDescription\nlet package=Package(name:"HostApp",products:[.executable(name:"HostApp",targets:["HostApp"])],dependencies:[.package(url:'
        + json.dumps(origin)
        + ',from:"1.0.0")],targets:[.executableTarget(name:"HostApp",dependencies:[.product(name:"HostLeaf",package:"HostLeaf")]),.testTarget(name:"HostTests",dependencies:[.product(name:"HostLeaf",package:"HostLeaf")])])\n',
    )
    write(
        project / "Sources/HostApp/main.swift",
        'import HostLeaf\nif answer() != 42 { fatalError("wrong answer") }\nprint("ANSWER|42")\n',
    )
    write(
        project / "Tests/HostTests/CoreTests.swift",
        "import XCTest\nimport HostLeaf\nfinal class CoreTests:XCTestCase { func testAnswer() { XCTAssertEqual(answer(),42) } }\n",
    )

    def inputs(directory):
        return {
            str(path.relative_to(directory)): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in directory.rglob("*")
            if path.is_file()
            and path.name != "swiftpm-purifier.lock.json"
            and not any(
                part in [".build", "Packages", ".git"]
                for part in path.relative_to(directory).parts
            )
        }

    try:
        run(
            "existing-host-resolve",
            [selected["commands"]["swift"], "package", "resolve"],
            project,
        )
        baseline = inputs(project)
        run("local-lock", [cli, "--package-path", str(project), "lock"])
        lock = json.loads((project / "swiftpm-purifier.lock.json").read_text())
        assert lock["version"] == 4 and "nativeSDK" not in lock
        assert inputs(project) == baseline
        receipt["requestsBeforePrefetch"] = len(receipt["requests"])
        run("local-prefetch", [cli, "--package-path", str(project), "prefetch"])
        assert any(
            "/objects/" in path
            for path in receipt["requests"][receipt["requestsBeforePrefetch"] :]
        ), "cold Nix fetch saw no object GETs"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        receipt["originClosed"] = True
        save()
    lock_bytes = (project / "swiftpm-purifier.lock.json").read_bytes()
    for label, arguments in [
        ("host-build", ["build"]),
        ("host-test", ["test"]),
        ("host-run", ["run"]),
    ]:
        output = run(label, [cli, "--package-path", str(project), "swift", *arguments])
        if label == "host-run":
            assert b"ANSWER|42" in output
    observed = json.loads(
        (project / ".build/swiftpm-purifier/observed-host-runtime.json").read_text()
    )
    assert observed["commands"]["swift"]["path"] == selected["commands"]["swift"]
    assert observed["overrides"] == selected["overrides"]
    assert not (project / ".build/swiftpm-purifier/toolchain").exists()
    assert inputs(project) == baseline
    assert (project / "swiftpm-purifier.lock.json").read_bytes() == lock_bytes
    relocated = work / "relocated"
    shutil.move(project, relocated)
    assert not project.exists()
    run(
        "relocated-clean",
        [cli, "--package-path", str(relocated), "swift", "package", "clean"],
    )
    for label, arguments in [
        ("relocated-build", ["build"]),
        ("relocated-test", ["test"]),
        ("relocated-run", ["run"]),
    ]:
        output = run(
            label, [cli, "--package-path", str(relocated), "swift", *arguments]
        )
        if label == "relocated-run":
            assert b"ANSWER|42" in output
    assert inputs(relocated) == baseline
    assert (relocated / "swiftpm-purifier.lock.json").read_bytes() == lock_bytes
    receipt.update(
        complete=True,
        userInputsUnchanged=True,
        originalRemoved=True,
        runtimePreserved=True,
        noSDKRealization=True,
        observation=observed,
    )
    save()
    print("Existing host runtime local lifecycle PASS")


if __name__ == "__main__":
    main()
