# SPDX-License-Identifier: MIT
"""Refuse detected foreign writes and overlapping publication authority."""

from pathlib import Path
import os
import subprocess
import sys
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

    def control_fixture(self):
        base = Path(tempfile.mkdtemp(dir=self.base)).resolve()
        root = base / "root"
        root.mkdir()
        registry = base / "registry"
        registry.mkdir(mode=0o700)
        (root / "Package.resolved").write_bytes(b"old pin")
        (root / "swiftpm-purifier.lock.json").write_bytes(b"old lock")
        return transaction.Transaction(root, registry)

    def test_foreign_controls_between_publication_boundaries_are_retained(self):
        for point, control in [
            ("reservation-file-fsync", "pending"),
            ("reservation-file-fsync", "reservation"),
            ("journal-file-fsync", "journal"),
            ("output-0-renamed", "journal"),
            ("output-0-renamed", "reservation"),
            ("output-0-renamed", "pending"),
            ("commit-file-fsync", "journal"),
            ("commit-renamed", "journal"),
        ]:
            with self.subTest(point=point, control=control):
                service = self.control_fixture()
                path = (
                    service.root / transaction.JOURNAL
                    if control == "journal"
                    else getattr(service, control)
                )
                foreign = b"foreign control evidence"

                def change(label):
                    if label == point:
                        path.write_bytes(foreign)

                with service.locked():
                    with self.assertRaises(transaction.ForeignControl):
                        service.publish(
                            [b"new pin", b"new lock"], service.pair(), change
                        )
                    self.assertEqual(path.read_bytes(), foreign)
                    self.assertTrue(service.hold.exists())
                    if point == "output-0-renamed":
                        self.assertEqual(service.pair(), [b"new pin", b"old lock"])
                    with self.assertRaises(cache.Reject):
                        service.recover()
                    self.assertEqual(path.read_bytes(), foreign)
                child_code = (
                    "import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
                    "from dependencies_v1_transaction import Transaction;"
                    "t=Transaction(Path(sys.argv[2]),Path(sys.argv[3]));\n"
                    "try:\n with t.locked():t.recover()\n"
                    "except ValueError:sys.exit(3)\n"
                )
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        child_code,
                        str(Path(transaction.__file__).parent),
                        str(service.root),
                        str(service.registry),
                    ],
                    capture_output=True,
                    timeout=5,
                )
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertEqual(path.read_bytes(), foreign)

    def test_canonical_foreign_phase_change_is_not_adopted_by_recovery(self):
        service = self.control_fixture()
        foreign = None

        def change(label):
            nonlocal foreign
            if label == "commit-file-fsync":
                record = cache.decode((service.root / transaction.JOURNAL).read_bytes())
                record["phase"] = "COMMITTED"
                foreign = transaction.canonical(record)
                (service.root / transaction.JOURNAL).write_bytes(foreign)

        with service.locked():
            with self.assertRaises(transaction.ForeignControl):
                service.publish([b"new pin", b"new lock"], service.pair(), change)
            self.assertEqual((service.root / transaction.JOURNAL).read_bytes(), foreign)
            with self.assertRaises(cache.Reject):
                service.recover()
            self.assertEqual((service.root / transaction.JOURNAL).read_bytes(), foreign)

    def test_control_changes_stop_rollback_before_next_output(self):
        class Interrupted(BaseException):
            pass

        for control in ("journal", "reservation", "pending"):
            with self.subTest(control=control):
                service = self.control_fixture()

                def stop(label):
                    if label == "output-1-renamed":
                        raise Interrupted()

                with service.locked():
                    with self.assertRaises(Interrupted):
                        service.publish([b"new pin", b"new lock"], service.pair(), stop)
                path = (
                    service.root / transaction.JOURNAL
                    if control == "journal"
                    else getattr(service, control)
                )
                foreign = b"foreign during rollback"

                def change(label):
                    if label == "restore-0-renamed":
                        path.write_bytes(foreign)

                with service.locked():
                    with self.assertRaises(transaction.ForeignControl):
                        service.recover(change)
                    self.assertEqual(service.pair(), [b"old pin", b"new lock"])
                    self.assertEqual(path.read_bytes(), foreign)
                    with self.assertRaises(cache.Reject):
                        service.recover()
                    self.assertEqual(path.read_bytes(), foreign)

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
