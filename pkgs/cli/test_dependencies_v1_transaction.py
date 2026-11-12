# SPDX-License-Identifier: MIT
"""Refuse detected foreign writes and overlapping publication authority."""

from pathlib import Path
import os
import tempfile
import unittest

import dependencies_v1_cache as cache
import dependencies_v1_transaction as transaction


class Transaction(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "root"
        self.root.mkdir()
        self.registry = self.base / "registry"
        self.registry.mkdir(mode=0o700)
        self.pin = self.root / "Package.resolved"
        self.pin.write_bytes(b"old pin")
        self.lock = self.root / "swiftpm-purifier.lock.json"
        self.lock.write_bytes(b"old portable")

    def test_detected_foreign_write_is_not_overwritten(self):
        service = transaction.Transaction(self.root, self.registry)

        def change(label):
            if label == "output-0-renamed":
                self.lock.write_bytes(b"foreign caller write")

        with service.locked():
            with self.assertRaises(cache.Reject):
                service.publish([b"new pin", b"new lock"], service.pair(), change)
            self.assertEqual(self.lock.read_bytes(), b"foreign caller write")
            self.assertTrue(service.reservation.exists())
            with self.assertRaises(cache.Reject):
                service.recover()
            self.assertEqual(self.lock.read_bytes(), b"foreign caller write")

    def test_changed_stage_mode_is_refused_before_publication(self):
        service = transaction.Transaction(self.root, self.registry)

        def change(label):
            if label == "new-0-staged":
                stage = next(self.root.glob(transaction.PREFIX + "*"))
                (stage / "new-0").chmod(0o755)

        with service.locked():
            with self.assertRaises(cache.Reject):
                service.publish([b"new pin", b"new lock"], service.pair(), change)
            self.assertEqual(service.pair(), [b"old pin", b"old portable"])

    def test_foreign_write_during_cleanup_is_retained(self):
        service = transaction.Transaction(self.root, self.registry)

        def change(label):
            if label == "cleanup-0-owner.json":
                self.lock.write_bytes(b"foreign after commit")

        with service.locked():
            with self.assertRaises(cache.Reject):
                service.publish([b"new pin", b"new lock"], service.pair(), change)
            self.assertEqual(self.lock.read_bytes(), b"foreign after commit")
            self.assertTrue(service.reservation.exists())

    def test_output_cannot_overlap_private_transaction_metadata(self):
        reserved = self.registry / (
            transaction.digest(["root", transaction.identity(self.root)]) + ".json"
        )
        with self.assertRaises(cache.Reject):
            transaction.Transaction(self.root, self.registry, reserved)
        outside = self.base / "outside"
        outside.mkdir()
        with self.assertRaises(cache.Reject):
            transaction.Transaction(
                self.root, self.registry, outside / transaction.JOURNAL
            )
        stage = outside / (transaction.PREFIX + "a" * 64)
        stage.mkdir()
        with self.assertRaises(cache.Reject):
            transaction.Transaction(self.root, self.registry, stage / "new-0")

    def test_existing_operation_excludes_another_root_sharing_external_output(self):
        external = self.base / "shared-native"
        external.write_bytes(b"shared")
        other = self.base / "other-root"
        other.mkdir()
        first = transaction.Transaction(self.root, self.registry, external)
        second = transaction.Transaction(other, self.registry, external)
        with first.locked():
            with self.assertRaises(BlockingIOError):
                with second.locked():
                    self.fail("overlapping operation acquired locks")
        self.assertEqual(external.read_bytes(), b"shared")

    def test_stale_input_does_not_begin_transaction(self):
        service = transaction.Transaction(self.root, self.registry)
        with service.locked():
            old = service.pair()
            self.pin.write_bytes(b"new caller pins")
            with self.assertRaises(cache.Reject):
                service.publish([b"new pin", b"new lock"], old)
            self.assertFalse(service.reservation.exists())
            self.assertEqual(self.pin.read_bytes(), b"new caller pins")

    def test_stale_copied_journal_is_not_authority_to_follow_old_root(self):
        service = transaction.Transaction(self.root, self.registry)
        copied = self.base / "copied"
        copied.mkdir()
        (copied / transaction.JOURNAL).write_bytes(b"{}")
        (copied / transaction.JOURNAL).chmod(0o600)
        other = transaction.Transaction(copied, self.registry)
        with other.locked():
            with self.assertRaises(cache.Reject):
                other.recover()
        self.assertEqual((copied / transaction.JOURNAL).read_bytes(), b"{}")


if __name__ == "__main__":
    unittest.main()
