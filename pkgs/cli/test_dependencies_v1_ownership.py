# SPDX-License-Identifier: MIT
"""Ownership content checks; fixture digests here are not native/NAR evidence."""

import base64
import hashlib
import json
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import dependencies_v1_ownership as ownership
import dependencies_v1_cache as cache
import dependencies_v1 as protocol
from dependencies_v1_transaction import canonical


def fixture_hash(root):
    rows = [
        [str(path.relative_to(root)), hashlib.sha256(path.read_bytes()).hexdigest()]
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]
    return (
        "sha256-" + base64.b64encode(hashlib.sha256(canonical(rows)).digest()).decode()
    )


class Ownership(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.packages = self.root / "Packages"
        self.packages.mkdir()
        self.leaf = self.packages / "Leaf"
        (self.leaf / "Sources").mkdir(parents=True)
        (self.leaf / "Package.swift").write_text("import PackageDescription\n")
        (self.leaf / "Sources/Leaf.swift").write_text(
            "public func leaf() -> Int { return 42 }\n"
        )
        self.sources = {
            "Leaf": {
                "name": "Leaf",
                "sourceLocation": "https://example.invalid/Leaf.git",
                "revision": "a" * 40,
                "version": "1.0.0",
                "hash": fixture_hash(self.leaf),
            }
        }
        self.original = {
            "version": 1,
            "object": {
                "dependencies": [
                    {
                        "basedOn": None,
                        "name": "Leaf",
                        "repositoryURL": "https://example.invalid/Leaf.git",
                        "subpath": "Leaf.git-opaque",
                        "state": {
                            "name": "checkout",
                            "checkoutState": {
                                "branch": None,
                                "revision": "a" * 40,
                                "version": "1.0.0",
                            },
                        },
                    }
                ]
            },
        }
        self.original_raw = json.dumps(self.original, indent=4).encode() + b"\n"
        self.marker = {
            "version": 1,
            "protocol": "dependencies-state-v1",
            "root": str(self.root),
            "lockHash": "sha256-" + "A" * 43 + "=",
            "originalState": base64.b64encode(self.original_raw).decode(),
            "sources": self.sources,
            "projection": ownership.projection(self.root, self.sources),
        }
        self.save_marker()
        (self.root / ".build").mkdir()
        self.state = self.root / ".build/dependencies-state.json"
        self.state.write_text(json.dumps(self.marker["projection"], indent=4))

    def save_marker(self):
        self.marker["contentHash"] = protocol.content_hash(self.marker)
        (self.packages / ownership.MARKER).write_bytes(canonical(self.marker) + b"\n")

    def test_exact_original_state_and_semantic_projection(self):
        loaded = ownership.existing(self.root, fixture_hash)
        self.assertEqual(base64.b64decode(loaded["originalState"]), self.original_raw)
        self.assertEqual(loaded["projection"], self.marker["projection"])

    def test_reinstallation_preserves_original_checkout_bytes(self):
        basis = ownership.installation_basis(self.root, fixture_hash)
        self.assertEqual(basis["originalState"], self.original_raw)
        self.assertEqual(basis["currentState"], self.state.read_bytes())
        self.assertIsNotNone(basis["packagesIdentity"])

    def test_maximum_original_state_backup_remains_readable(self):
        original = self.original_raw + b" " * (1024 * 1024 - len(self.original_raw))
        self.marker["originalState"] = base64.b64encode(original).decode()
        self.save_marker()
        self.assertGreater(
            (self.packages / ownership.MARKER).stat().st_size, 1024 * 1024
        )
        loaded = ownership.existing(self.root, fixture_hash)
        self.assertEqual(base64.b64decode(loaded["originalState"]), original)

    def test_first_install_keeps_exact_native_state_without_mutation(self):
        shutil.rmtree(self.packages)
        self.state.write_bytes(self.original_raw)
        basis = ownership.installation_basis(self.root, fixture_hash)
        self.assertEqual(basis["originalState"], self.original_raw)
        self.assertIsNone(basis["packagesIdentity"])
        self.assertFalse(self.packages.exists())
        self.assertEqual(self.state.read_bytes(), self.original_raw)

    def test_empty_project_basis_does_not_create_build_directory(self):
        shutil.rmtree(self.packages)
        shutil.rmtree(self.root / ".build")
        basis = ownership.installation_basis(self.root, fixture_hash)
        self.assertIsNone(basis["originalState"])
        self.assertIsNone(basis["buildIdentity"])
        self.assertFalse((self.root / ".build").exists())

    def test_first_install_refuses_unknown_native_state(self):
        shutil.rmtree(self.packages)
        raw = b'{"version":99,"object":{}}'
        self.state.write_bytes(raw)
        with self.assertRaises(cache.Reject):
            ownership.installation_basis(self.root, fixture_hash)
        self.assertEqual(self.state.read_bytes(), raw)

    def test_edited_owned_sources_and_extra_paths_refuse_without_deletion(self):
        source = self.leaf / "Sources/Leaf.swift"
        source.write_text("caller edits")
        with self.assertRaises(cache.Reject):
            ownership.existing(self.root, fixture_hash)
        self.assertEqual(source.read_text(), "caller edits")
        (self.packages / "unowned").write_text("keep")
        with self.assertRaises(cache.Reject):
            ownership.existing(self.root, fixture_hash)
        self.assertEqual((self.packages / "unowned").read_text(), "keep")

    def test_external_projection_path_and_boolean_version_refuse(self):
        for changed in ("path", "version"):
            state = json.loads(json.dumps(self.marker["projection"]))
            if changed == "path":
                state["object"]["dependencies"][0]["state"]["path"] = (
                    "/outside/edited-source"
                )
            else:
                state["version"] = True
            self.state.write_text(json.dumps(state))
            with self.subTest(changed=changed):
                with self.assertRaises(cache.Reject):
                    ownership.existing(self.root, fixture_hash)

    def test_relocation_comparison_never_follows_recorded_old_root(self):
        old = "/nonexistent-purifier-old-root"
        self.marker["root"] = old
        self.marker["projection"] = ownership.projection(old, self.sources)
        self.save_marker()
        self.state.write_text(json.dumps(self.marker["projection"]))
        original_lstat = Path.lstat

        def guarded(path, *args, **kwargs):
            if str(path).startswith(old):
                raise AssertionError("followed old root")
            return original_lstat(path, *args, **kwargs)

        with patch.object(Path, "lstat", guarded):
            loaded = ownership.existing(self.root, fixture_hash)
        self.assertEqual(loaded["root"], old)

    def test_source_link_rejected_before_digest_callback(self):
        source = self.leaf / "Sources/Leaf.swift"
        source.unlink()
        source.symlink_to("/outside/not-followed")
        with self.assertRaises(cache.Reject):
            ownership.existing(
                self.root, lambda path: self.fail("unsafe source reached digest")
            )

    def test_missing_state_requires_explicit_reconstruction_context(self):
        self.state.unlink()
        with self.assertRaises(cache.Reject):
            ownership.existing(self.root, fixture_hash)
        self.assertIsNotNone(
            ownership.existing(self.root, fixture_hash, allow_missing_state=True)
        )


if __name__ == "__main__":
    unittest.main()
