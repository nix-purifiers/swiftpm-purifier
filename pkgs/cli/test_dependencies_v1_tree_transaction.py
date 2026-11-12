# SPDX-License-Identifier: MIT
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import dependencies_v1_cache as cache
import dependencies_v1_tree_transaction as trees


class Interrupted(Exception):
    pass


class OwnedTreeCleanup(unittest.TestCase):
    def fixture(self, parent):
        root = parent / "stage"
        (root / "a/empty").mkdir(parents=True)
        (root / "a/one").write_bytes(b"one")
        (root / "two").write_bytes(b"two")
        (root / "two").chmod(0o755)
        return root

    def test_every_deletion_boundary_resumes_with_original_inventory(self):
        # Five entries: two files, two nested directories, root.
        for boundary in range(1, 6):
            with (
                self.subTest(boundary=boundary),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = self.fixture(Path(temporary))
                record = trees.inventory(root)
                count = 0

                def interrupt(label):
                    nonlocal count
                    count += 1
                    if count == boundary:
                        raise Interrupted(label)

                with self.assertRaises(Interrupted):
                    trees.cleanup(root, record, lambda: None, interrupt)
                trees.cleanup(root, record, lambda: None)
                self.assertFalse(root.exists())

    def test_foreign_entry_after_interruption_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(Path(temporary))
            record = trees.inventory(root)

            def interrupt(label):
                raise Interrupted(label)

            with self.assertRaises(Interrupted):
                trees.cleanup(root, record, lambda: None, interrupt)
            foreign = root / "foreign"
            foreign.write_bytes(b"retain me")
            before = trees.inventory(root)
            with self.assertRaises(cache.Reject):
                trees.cleanup(root, record, lambda: None)
            self.assertEqual(trees.inventory(root), before)

    def test_fresh_process_resumes_serialized_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = self.fixture(parent)
            record = trees.inventory(root)
            saved = parent / "inventory.json"
            saved.write_text(json.dumps(record))

            def interrupt(label):
                raise Interrupted(label)

            with self.assertRaises(Interrupted):
                trees.cleanup(root, record, lambda: None, interrupt)
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import json, sys; from pathlib import Path; "
                    "import dependencies_v1_tree_transaction as t; "
                    "t.cleanup(Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()), lambda: None)",
                    str(root),
                    str(saved),
                ],
                cwd=Path(trees.__file__).parent,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(root.exists())

    def test_changed_file_mode_and_alias_are_refused_before_deletion(self):
        for change in ("bytes", "mode", "symlink", "hardlink"):
            with (
                self.subTest(change=change),
                tempfile.TemporaryDirectory() as temporary,
            ):
                parent = Path(temporary)
                root = self.fixture(parent)
                record = trees.inventory(root)
                target = root / "two"
                if change == "bytes":
                    target.write_bytes(b"edited")
                elif change == "mode":
                    target.chmod(0o644)
                elif change == "symlink":
                    target.unlink()
                    target.symlink_to(parent / "external")
                else:
                    os.link(target, parent / "alias")
                with self.assertRaises(cache.Reject):
                    trees.cleanup(root, record, lambda: None)
                self.assertEqual((root / "a/one").read_bytes(), b"one")
                self.assertTrue(os.path.lexists(target))

    def test_control_guard_refusal_preserves_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(Path(temporary))
            record = trees.inventory(root)

            def refuse():
                raise cache.Reject("foreign journal")

            with self.assertRaises(cache.Reject):
                trees.cleanup(root, record, refuse)
            self.assertEqual(trees.inventory(root), record)

    def test_unconfined_record_cannot_authorize_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(Path(temporary))
            record = trees.inventory(root)
            record["../foreign"] = {"kind": "directory", "mode": 0o700}
            with self.assertRaises(cache.Reject):
                trees.cleanup(root, record, lambda: None)
            self.assertTrue((root / "two").exists())


if __name__ == "__main__":
    unittest.main()
