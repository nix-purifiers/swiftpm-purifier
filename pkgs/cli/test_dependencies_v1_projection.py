# SPDX-License-Identifier: MIT
"""Owned probe graph/scope controls; actual compiler evidence is separate."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import dependencies_v1_projection as projection
import dependencies_v1_manifest as manifest
import dependencies_v1_cache as cache


class Projection(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / "probe"
        self.root.mkdir()
        (self.root / "Package.swift").write_text("import PackageDescription\n")
        self.pins = {}
        self.sources = {}
        for name in ("Alpha", "Beta", "Unused"):
            path = self.base / name
            path.mkdir()
            (path / "Package.swift").write_text("import PackageDescription\n")
            (path / "Sources").mkdir()
            (path / "Sources/Code.swift").write_text(
                "public func value() -> Int { return 42 }\n"
            )
            self.sources[name] = path
            self.pins[name] = {
                "name": name,
                "sourceLocation": "https://example.invalid/" + name + ".git",
            }

    def test_complete_locked_graph_and_direct_dependency_subset(self):
        allowed = projection.prepare_probe(self.root, self.pins, self.sources, "Alpha")
        self.assertEqual(
            allowed, {self.root / "Packages/Beta", self.root / "Packages/Unused"}
        )
        self.assertFalse((self.root / "Packages/Alpha").exists())
        description = {"dependencies": [{"url": self.pins["Beta"]["sourceLocation"]}]}
        projection.verify_probe(self.root, self.pins, "Alpha", description)
        path = self.root / ".build/dependencies-state.json"
        state = json.loads(path.read_text())
        state["object"]["dependencies"] = [
            dep for dep in state["object"]["dependencies"] if dep["name"] == "Beta"
        ]
        path.write_text(json.dumps(state))
        projection.verify_probe(self.root, self.pins, "Alpha", description)
        state["object"]["dependencies"] = []
        path.write_text(json.dumps(state))
        with self.assertRaises(cache.Reject):
            projection.verify_probe(self.root, self.pins, "Alpha", description)

    def test_checkout_or_foreign_edited_projection_refuses(self):
        projection.prepare_probe(self.root, self.pins, self.sources, "Alpha")
        path = self.root / ".build/dependencies-state.json"
        original = json.loads(path.read_text())
        for name, value in [("name", "checkout"), ("path", "/outside/source")]:
            state = json.loads(json.dumps(original))
            state["object"]["dependencies"][0]["state"][name] = value
            path.write_text(json.dumps(state))
            with self.assertRaises(cache.Reject):
                projection.verify_probe(
                    self.root, self.pins, "Alpha", {"dependencies": []}
                )

    def test_missing_source_and_existing_metadata_refuse(self):
        with self.assertRaises(cache.Reject):
            projection.prepare_probe(self.root, self.pins, {}, "Alpha")
        (self.root / "Packages").mkdir()
        (self.root / "Packages/user-data").write_text("keep")
        with self.assertRaises(cache.Reject):
            projection.prepare_probe(self.root, self.pins, self.sources, "Alpha")
        self.assertEqual((self.root / "Packages/user-data").read_text(), "keep")

    def test_explicit_invocation_authority_is_exact(self):
        allowed = projection.prepare_probe(self.root, self.pins, self.sources, "Alpha")

        def observed(parent):
            return "/native/swiftc " + str(parent / "Package.swift") + " -fileno 5\n"

        description = '{\n"name":"Alpha"\n}\n'
        root_line = observed(self.root)
        child = observed(self.root / "Packages/Beta")
        with patch.object(
            manifest.legacy_manifest,
            "invocation",
            return_value=(["/native/swiftc"], {"name": "Alpha"}),
        ):
            manifest.invocation(
                (root_line + child + description).encode(), self.root, allowed
            )
            for payload in (
                root_line + observed(self.root / "Packages/Other") + description,
                root_line + root_line + description,
                "Fetching foreign repository\n" + root_line + description,
            ):
                with self.assertRaises(ValueError):
                    manifest.invocation(payload.encode(), self.root, allowed)
            with self.assertRaises(ValueError):
                manifest.invocation(
                    (root_line + description).encode(),
                    self.root,
                    [self.base / "outside"],
                )


if __name__ == "__main__":
    unittest.main()
