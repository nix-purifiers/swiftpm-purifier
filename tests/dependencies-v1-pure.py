#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Immutable format8 reader/selector controls; host inode authority is separate."""

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "pkgs/cli"))
import dependencies_v1 as codec
import dependencies_v1_capture as capture
import dependencies_v1_layout as layout
import dependencies_v1_selectors as selectors
import pre_workspace


def encode(value):
    value = copy.deepcopy(value)
    value["contentHash"] = codec.content_hash(value)
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def nix_read(root, portable, selection="omitted", *, raw_result=False):
    attributes = (
        "src = builtins.toPath "
        + json.dumps(str(root))
        + "; lockFile = builtins.toPath "
        + json.dumps(str(portable))
        + ";"
    )
    if selection != "omitted":
        attributes += (
            "packageResolved = "
            + ("null" if selection is None else json.dumps(str(selection)))
            + ";"
        )
    expression = (
        "let r = import "
        + str(REPO / "lib-overlays/default/lock.nix")
        + " {"
        + attributes
        + "}; in "
    )
    expression += (
        "r"
        if raw_result
        else "{ problems = r.lock.problems; entries = r.lock.entries; state = r.workspaceState; nativeFile = r.toolLockFile; }"
    )
    return subprocess.run(
        ["nix", "eval", "--impure", "--json", "--expr", expression],
        capture_output=True,
        text=True,
        timeout=30,
    )


def python_read(root, portable, selection):
    try:
        value = codec.decode(portable.read_bytes())
        native = selectors.DEFAULT if selection == "omitted" else selection
        pin, output, logical = selectors.pair(root, native, portable)
        assert (
            layout.source_layout(root, output.path)["hash"] == value["sourceLayoutHash"]
        )
        assert pre_workspace.manifest_hash(root) == value["manifestHash"]
        assert pin.sha256 == value["nativeToolLock"]["sha256"]
        state = {
            "object": {
                "dependencies": [
                    {
                        "name": name,
                        "repositoryURL": item["sourceLocation"],
                        "state": {
                            "checkoutState": {
                                "branch": None,
                                "revision": item["revision"],
                                "version": item["version"],
                            }
                        },
                    }
                    for name, item in sorted(value["pins"].items())
                ]
            }
        }
        capture.native_pins(pin.raw, state)
        return True
    except (ValueError, AssertionError, OSError):
        return False


def main():
    with tempfile.TemporaryDirectory(
        prefix="swiftpm-v1-pure-", dir=os.environ.get("TMPDIR")
    ) as temporary:
        base = Path(temporary)
        root = base / "project"
        root.mkdir()
        (root / "Sources").mkdir()
        (root / "Sources/main.swift").write_text("print(42)\n")
        (root / "empty").mkdir()
        (root / "Package.swift").write_text(
            '// swift-tools-version:4.0\nimport PackageDescription\nlet package = Package(name:"Root")\n'
        )
        native = {
            "version": 1,
            "object": {
                "pins": [
                    {
                        "package": "Leaf",
                        "repositoryURL": "https://example.invalid/Leaf.git",
                        "state": {
                            "branch": None,
                            "revision": "a" * 40,
                            "version": "1.0.0",
                        },
                    }
                ]
            },
        }
        raw = json.dumps(native).encode()
        (root / "Package.resolved").write_bytes(raw)
        value = {
            "version": 8,
            "nativeProtocol": "dependencies-state-v1",
            "nativeState": {"file": ".build/dependencies-state.json", "version": 1},
            "package": "Root",
            "rootDirectory": "project",
            "manifestHash": pre_workspace.manifest_hash(root),
            "sourceLayoutHash": layout.source_layout(root)["hash"],
            "nativeToolLock": {
                "file": "Package.resolved",
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
            "pins": {
                "Leaf": {
                    "name": "Leaf",
                    "sourceLocation": "https://example.invalid/Leaf.git",
                    "revision": "a" * 40,
                    "version": "1.0.0",
                    "hash": "sha256-" + "A" * 43 + "=",
                }
            },
            "products": [
                {
                    "name": "Root",
                    "type": "executable",
                    "modules": ["Root"],
                    "declaration": {"kind": "inferred-executable", "target": "Root"},
                }
            ],
            "localDependencies": [],
            "binaryTargets": [],
        }
        portable = root / "swiftpm-purifier.lock.json"
        cases = []

        def check(
            name,
            candidate,
            selection="omitted",
            accepted=True,
            payload=None,
            destination=portable,
        ):
            destination.write_bytes(encode(candidate) if payload is None else payload)
            result = nix_read(root, destination, selection)
            actual = (
                result.returncode == 0 and json.loads(result.stdout)["problems"] == []
            )
            python = python_read(root, destination, selection)
            assert actual == python, (
                name,
                "Python/Nix admission parity",
                python,
                result.stdout,
                result.stderr,
            )
            assert actual == accepted, (name, result.stdout, result.stderr)
            if actual:
                codec.decode(destination.read_bytes())
                facts = json.loads(result.stdout)
                assert facts["state"]["version"] == 1
                assert facts["state"]["object"]["dependencies"][0]["basedOn"] is None
                assert facts["entries"][0]["git"]["leaveDotGit"] is False
            cases.append({"name": name, "accepted": actual})

        check("default", value)
        check("explicit-root", value, root / "Package.resolved")
        absent = copy.deepcopy(value)
        absent["nativeToolLock"]["sha256"] = None
        check("explicit-null-independent-root", absent, None)
        check("null-present-binding-reject", value, None, False)
        check("default-absence-binding-reject", absent, accepted=False)
        external = base / "native.json"
        external.write_bytes(raw + b"\n")
        changed = copy.deepcopy(value)
        changed["nativeToolLock"]["sha256"] = hashlib.sha256(
            external.read_bytes()
        ).hexdigest()
        check("external-bytes", changed, external)
        check("external-byte-binding-reject", value, external, False)
        check("external-portable", value, destination=base / "portable.json")
        nested = root / "nested.json"
        check("in-tree-portable-reject", value, destination=nested, accepted=False)
        nested.unlink()
        check("selector-alias-portable", value, portable, False)
        check("dot-path-alias", value, str(root) + "/./Package.resolved", False)
        alias = base / "alias"
        alias.symlink_to(root, target_is_directory=True)
        check("ancestor-link", value, alias / "Package.resolved", False)
        for name, mutate in [
            ("unknown-top", lambda x: x.update(extra=True)),
            ("sdk-attestation-forbidden", lambda x: x.update(nativeSDK={})),
            (
                "unknown-pin",
                lambda x: x["pins"]["Leaf"].update(gitHash=x["pins"]["Leaf"]["hash"]),
            ),
            (
                "unknown-native-protocol",
                lambda x: x.update(nativeProtocol="workspace-state"),
            ),
            ("native-state-version", lambda x: x["nativeState"].update(version=2)),
            (
                "duplicate-product",
                lambda x: x["products"].append(copy.deepcopy(x["products"][0])),
            ),
            ("empty-graph", lambda x: x.update(pins={})),
        ]:
            bad = copy.deepcopy(value)
            mutate(bad)
            check(name, bad, accepted=False)
        check(
            "duplicate-json-key",
            value,
            payload=encode(value).replace(b"{", b'{"version":8,', 1),
            accepted=False,
        )
        check("bom", value, payload=b"\xef\xbb\xbf" + encode(value), accepted=False)
        (root / "Sources/added.swift").write_text("// changed layout\n")
        check("source-layout-stale", value, accepted=False)
        (root / "Sources/added.swift").unlink()
        (root / "Package.swift").write_text(
            (root / "Package.swift").read_text() + "// changed manifest\n"
        )
        check("manifest-bytes-stale", value, accepted=False)
        (root / "Package.swift").write_text(
            (root / "Package.swift").read_text().removesuffix("// changed manifest\n")
        )
        (root / ".build").mkdir()
        (root / ".build/debug").symlink_to("platform/debug")
        check("legitimate-native-debug-link", value)
        (root / ".swiftpm-purifier-transaction.json").write_text("{}")
        check("journal-requires-recovery", value, accepted=False)
        (root / ".swiftpm-purifier-transaction.json").unlink()
        (root / "Package.resolved").write_bytes(b"unselected logical bytes")
        check("null-ignores-unselected-logical-protocol", absent, None)
        (root / "Package.resolved").unlink()
        check("default-native-absent", absent)
        for name, change in [
            ("native-version-unknown", lambda x: x.update(version=2)),
            ("native-unknown-field", lambda x: x.update(extra=True)),
            (
                "native-extra-pin-field",
                lambda x: x["object"]["pins"][0].update(extra=True),
            ),
            (
                "native-branch",
                lambda x: x["object"]["pins"][0]["state"].update(branch="main"),
            ),
            (
                "native-revision",
                lambda x: x["object"]["pins"][0]["state"].update(revision="b" * 40),
            ),
            (
                "native-package-name",
                lambda x: x["object"]["pins"][0].update(package="Other"),
            ),
            (
                "native-URL",
                lambda x: x["object"]["pins"][0].update(
                    repositoryURL="https://example.invalid/Other.git"
                ),
            ),
            (
                "native-duplicate-pin",
                lambda x: x["object"]["pins"].append(
                    copy.deepcopy(x["object"]["pins"][0])
                ),
            ),
        ]:
            bad = copy.deepcopy(native)
            change(bad)
            payload = json.dumps(bad).encode()
            (root / "Package.resolved").write_bytes(payload)
            locked = copy.deepcopy(value)
            locked["nativeToolLock"]["sha256"] = hashlib.sha256(payload).hexdigest()
            check(name, locked, accepted=False)
        (root / "Package.resolved").write_bytes(raw)
        (root / "Package.pins").write_text("{}")
        check("mixed-native-protocol", value, accepted=False)
        (root / "Package.pins").unlink()
        (root / "source-link").symlink_to("Sources")
        check("source-link", value, accepted=False)
        (root / "source-link").unlink()
        (root / (".swiftpm-purifier-stage-" + "a" * 64)).mkdir()
        check("orphan-owned-stage", value, accepted=False)
        (root / (".swiftpm-purifier-stage-" + "a" * 64)).rmdir()
        (root / "Package.resolved").write_bytes(b"x" * (1024 * 1024 + 1))
        check("bounded-unselected-logical-file", absent, None, False)
        print(
            json.dumps(
                {
                    "scope": "immutable Nix protocol/bytes/path/layout; no host inode authority claim",
                    "cases": cases,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
