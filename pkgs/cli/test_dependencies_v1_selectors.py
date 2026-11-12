# SPDX-License-Identifier: MIT
"""Physical selector bindings; all mutations are confined to temporary fixtures."""

from pathlib import Path
import os
import tempfile
import unittest

import dependencies_v1_cache as cache
import dependencies_v1_layout as layout
import dependencies_v1_selectors as selectors


class Selectors(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "root"
        self.root.mkdir()
        self.native = self.root / "Package.resolved"
        self.native.write_bytes(b"root native bytes")
        self.external = self.base / "external.json"
        self.external.write_bytes(b"external native bytes")

    def test_default_null_and_external_remain_distinct(self):
        default, _, logical = selectors.pair(self.root)
        absent, _, preserved = selectors.pair(self.root, None)
        external, _, _ = selectors.pair(self.root, self.external)
        self.assertEqual(default.raw, logical.raw)
        self.assertIsNone(absent.path)
        self.assertIsNone(absent.sha256)
        self.assertEqual(preserved.raw, b"root native bytes")
        self.assertEqual(external.raw, b"external native bytes")
        self.assertNotEqual(external.sha256, default.sha256)
        self.assertEqual(self.native.read_bytes(), b"root native bytes")

    def test_changed_file_or_parent_refuses(self):
        selection = selectors.select(self.root, self.external)
        self.external.write_bytes(b"caller changed bytes")
        with self.assertRaises(cache.Reject):
            selection.verify()
        parent = self.base / "parent"
        parent.mkdir()
        path = parent / "pins"
        path.write_bytes(b"original")
        selection = selectors.select(self.root, path)
        parent.rename(self.base / "old-parent")
        parent.mkdir()
        path.write_bytes(b"original")
        with self.assertRaises(cache.Reject):
            selection.verify()

    def test_alias_link_nonregular_and_in_tree_selectors_refuse(self):
        inner = self.root / "other.json"
        inner.write_bytes(b"not a permitted role")
        link = self.base / "linked"
        link.symlink_to(self.external)
        fifo = self.base / "fifo"
        os.mkfifo(fifo)
        alias = self.base / "hardlink"
        os.link(self.native, alias)
        for value in (inner, link, fifo, alias, str(self.base) + "/./external.json"):
            with self.subTest(value=value):
                with self.assertRaises((cache.Reject, layout.LayoutError, OSError)):
                    selectors.select(self.root, value)

    def test_absent_file_becoming_present_refuses(self):
        absent = self.base / "absent"
        selection = selectors.select(self.root, absent)
        absent.write_bytes(b"caller created")
        with self.assertRaises(cache.Reject):
            selection.verify()

    def test_native_portable_same_path_refuses(self):
        with self.assertRaises(cache.Reject):
            selectors.pair(self.root, self.external, self.external)


if __name__ == "__main__":
    unittest.main()
