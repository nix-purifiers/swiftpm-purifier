# SPDX-License-Identifier: MIT
"""Caller currency/selector failures must precede native tool execution."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import dependencies_v1 as protocol
import dependencies_v1_cache as cache
import dependencies_v1_host as host
import dependencies_v1_layout as layout
import pre_workspace
from test_dependencies_v1 import fixture


class Host(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "Root"
        self.root.mkdir()
        (self.root / "Package.swift").write_text("// swift-tools-version:4.0\n")
        self.lock = fixture()
        self.lock["manifestHash"] = pre_workspace.manifest_hash(self.root)
        self.lock["sourceLayoutHash"] = layout.source_layout(self.root)["hash"]
        self.path = self.root / "swiftpm-purifier.lock.json"
        self.save()

    def save(self):
        self.lock["contentHash"] = protocol.content_hash(self.lock)
        self.raw = json.dumps(self.lock).encode()
        self.path.write_bytes(self.raw)

    def test_current_source_and_explicit_absence(self):
        self.assertEqual(host.validate_source(self.root, self.raw), self.lock)

    def test_stale_manifest_and_source_structure_refuse(self):
        (self.root / "Package.swift").write_text("changed")
        with self.assertRaises(cache.Reject):
            host.validate_source(self.root, self.raw)
        (self.root / "Package.swift").write_text("// swift-tools-version:4.0\n")
        (self.root / "new-source.swift").write_text("print(42)")
        with self.assertRaises(cache.Reject):
            host.validate_source(self.root, self.raw)

    def test_external_selected_bytes_bind_independently_of_logical_root(self):
        pin = self.lock["pins"]["Leaf"]
        raw = json.dumps(
            {
                "version": 1,
                "object": {
                    "pins": [
                        {
                            "package": "Leaf",
                            "repositoryURL": pin["sourceLocation"],
                            "state": {
                                "branch": None,
                                "version": pin["version"],
                                "revision": pin["revision"],
                            },
                        }
                    ]
                },
            }
        ).encode()
        external = self.base / "native.json"
        external.write_bytes(raw)
        (self.root / "Package.resolved").write_bytes(b"unselected logical bytes")
        self.lock["nativeToolLock"]["sha256"] = hashlib.sha256(raw).hexdigest()
        self.save()
        self.assertEqual(
            host.validate_source(self.root, self.raw, native=external), self.lock
        )
        external.write_bytes(raw + b"\n")
        with self.assertRaises(cache.Reject):
            host.validate_source(self.root, self.raw, native=external)
        self.assertEqual(
            (self.root / "Package.resolved").read_bytes(), b"unselected logical bytes"
        )

    def test_sdk_mode_refuses_before_filesystem_or_tools(self):
        class API:
            @staticmethod
            def sdk_mode():
                return True

        with self.assertRaises(cache.Reject):
            host.runtime(None, None, None, None, API(), None)


if __name__ == "__main__":
    unittest.main()
