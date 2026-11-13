# SPDX-License-Identifier: MIT
"""Finite typed-reservation controls; these are not native-process crash claims."""

import copy
import os
from pathlib import Path
import tempfile
import unittest

import workspace_v4_path as protocol
import workspace_v4_path_transaction as transactions
from dependencies_v1_transaction import Transaction
from dependencies_v1_bridge import NativePinBridge
from test_dependencies_v3 import WireFixture


class Interrupted(BaseException):
    pass


class TypedPublication(WireFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.lock.update(version=10, workspaceStateVersion=4)
        self.rehash(self.lock)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name)
        self.native = self.root / "Package.resolved"
        self.portable = self.root / "swiftpm-purifier.lock.json"
        self.native.write_bytes(protocol.canonical(self.selected))
        self.portable.write_bytes(protocol.canonical(self.lock))
        self.native.chmod(0o600)
        self.portable.chmod(0o600)

    def target(self):
        selected = copy.deepcopy(self.selected)
        selected["object"]["pins"][0]["state"]["version"] = "1.0.1"
        lock = copy.deepcopy(self.lock)
        lock["pins"]["alpha"]["version"] = "1.0.1"
        self.rehash(lock)
        return [protocol.canonical(selected), protocol.canonical(lock)]

    def test_capture_publishes_only_validated_portable10_native_pair(self):
        target = self.target()
        with transactions.Capture(self.root, self.state).locked() as owner:
            owner.publish(target, owner.pair())
            self.assertEqual(owner.pair(), target)
            self.assertFalse(owner.reservation.exists())
        self.assertEqual(self.native.stat().st_mode & 0o777, 0o600)

    def test_invalid_target_refused_before_reservation_or_output_write(self):
        original = [self.native.read_bytes(), self.portable.read_bytes()]
        for native, portable in ((None, original[1]), (original[0], b"{}")):
            with transactions.Capture(self.root, self.state).locked() as owner:
                with self.assertRaises(protocol.Reject):
                    owner.publish([native, portable], owner.pair())
                self.assertEqual(owner.pair(), original)
                self.assertFalse(owner.reservation.exists())
                self.assertFalse(owner.pending.exists())

    def interrupt_after_first_output(self):
        def stop(label):
            if label == "output-0-renamed":
                raise Interrupted()

        with transactions.Capture(self.root, self.state).locked() as owner:
            with self.assertRaises(Interrupted):
                owner.publish(self.target(), owner.pair(), stop)
            self.assertTrue(owner.reservation.exists())

    def test_fresh_typed_owner_restores_prepared_pair(self):
        original = [self.native.read_bytes(), self.portable.read_bytes()]
        self.interrupt_after_first_output()
        with transactions.Capture(self.root, self.state).locked() as owner:
            self.assertEqual(owner.recover(), "restored-old-pair")
            self.assertEqual(owner.pair(), original)

    def test_shared_router_uses_typed_capture_validator(self):
        from dependencies_v1_lifecycle import recover_locked

        original = [self.native.read_bytes(), self.portable.read_bytes()]
        self.interrupt_after_first_output()
        with Transaction(self.root, self.state).locked() as owner:
            self.assertEqual(recover_locked(owner), "restored-old-pair")
            self.assertEqual(owner.pair(), original)

    def test_shared_router_refuses_unknown_root10_operation(self):
        import json
        from dependencies_v1_lifecycle import recover_locked

        self.interrupt_after_first_output()
        with Transaction(self.root, self.state).locked() as owner:
            record = json.loads(owner.reservation.read_bytes())
            record["operation"] = "owned-install"
            owner.reservation.write_bytes(protocol.canonical(record))
            current = owner.pair()
            with self.assertRaises(protocol.Reject):
                recover_locked(owner)
            self.assertEqual(owner.pair(), current)
            self.assertTrue(owner.reservation.exists())

    def test_old_operation_cannot_recover_root10_reservation(self):
        self.interrupt_after_first_output()
        current = [self.native.read_bytes(), self.portable.read_bytes()]
        with Transaction(self.root, self.state).locked() as owner:
            with self.assertRaises(protocol.Reject):
                owner.recover()
            self.assertEqual(owner.pair(), current)
            self.assertTrue(owner.reservation.exists())

    def test_root_identity_lock_is_shared_with_old_operations(self):
        with transactions.Capture(self.root, self.state).locked():
            with self.assertRaises(BlockingIOError):
                with Transaction(self.root, self.state).locked():
                    self.fail("older owner acquired the same root")

    def test_capture_cannot_request_prepared_bridge_semantics(self):
        with transactions.Capture(self.root, self.state).locked() as owner:
            with self.assertRaises(protocol.Reject):
                owner.publish(self.target(), owner.pair(), defer_commit=True)
            self.assertFalse(owner.reservation.exists())

    def test_root10_bridge_restores_native_deletion_and_mode(self):
        original = self.native.read_bytes()
        with transactions.Bridge(self.root, self.state).locked() as owner:
            owner.publish(
                [original, self.portable.read_bytes()], owner.pair(), defer_commit=True
            )
        self.native.unlink()
        with transactions.Bridge(self.root, self.state).locked() as owner:
            self.assertEqual(owner.recover(), "restored-old-pair")
        self.assertEqual(self.native.read_bytes(), original)
        self.assertEqual(self.native.stat().st_mode & 0o777, 0o600)

    def test_old_bridge_cannot_admit_root10_native_deletion(self):
        with transactions.Bridge(self.root, self.state).locked() as owner:
            owner.publish(
                [self.native.read_bytes(), self.portable.read_bytes()],
                owner.pair(),
                defer_commit=True,
            )
        self.native.unlink()
        with NativePinBridge(self.root, self.state).locked() as owner:
            with self.assertRaises(protocol.Reject):
                owner.recover()
            self.assertTrue(owner.reservation.exists())
            self.assertFalse(self.native.exists())

    def external_bridge(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        external = Path(temporary.name)
        native = external / "selected-pins.json"
        portable = external / "selected-lock.json"
        native.write_bytes(protocol.canonical(self.selected))
        portable.write_bytes(protocol.canonical(self.lock))
        native.chmod(0o600)
        portable.chmod(0o600)
        self.native.write_bytes(b"unselected malformed logical bytes\n")
        original = self.native.read_bytes()
        external.chmod(0o500)
        self.addCleanup(lambda: external.chmod(0o700))
        with transactions.Bridge(
            self.root, self.state, native, portable
        ).locked() as owner:
            owner.publish(
                [native.read_bytes(), portable.read_bytes()],
                owner.pair(),
                defer_commit=True,
            )
        self.native.unlink()
        return external, native, portable, original

    def test_readonly_external_inputs_unchanged_and_logical_bytes_restored(self):
        external, native, portable, original = self.external_bridge()
        before = [
            (path.stat().st_ino, path.read_bytes(), path.stat().st_mode)
            for path in (native, portable)
        ]
        with transactions.Bridge(
            self.root, self.state, native, portable
        ).locked() as owner:
            owner.recover()
        self.assertEqual(self.native.read_bytes(), original)
        self.assertEqual(
            [
                (path.stat().st_ino, path.read_bytes(), path.stat().st_mode)
                for path in (native, portable)
            ],
            before,
        )
        self.assertEqual(external.stat().st_mode & 0o777, 0o500)

    def test_same_bytes_replaced_external_selector_refuses_recovery(self):
        external, native, portable, _ = self.external_bridge()
        external.chmod(0o700)
        replacement = external / "replacement"
        replacement.write_bytes(portable.read_bytes())
        replacement.chmod(0o600)
        os.replace(replacement, portable)
        with transactions.Bridge(
            self.root, self.state, native, portable
        ).locked() as owner:
            with self.assertRaises(protocol.Reject):
                owner.recover()
            self.assertTrue(owner.reservation.exists())
        self.assertFalse(self.native.exists())

    def test_owned9_refused_before_pin_portable_or_cache_write(self):
        import dependencies_v3 as old_codec
        import dependencies_v3_ownership as old_owner
        from dependencies_v1_transaction import encoded, identity

        old_lock = copy.deepcopy(self.lock)
        old_lock.update(version=9, workspaceStateVersion=3)
        self.rehash(old_lock)
        marker = {
            "version": 1,
            "protocol": "dependencies-state-v3",
            "rootIdentity": identity(self.root),
            "portable": old_lock,
            "nativePins": encoded(protocol.canonical(self.selected)),
            "originalState": None,
            "projection": old_owner.projection(old_lock, self.root),
        }
        marker["contentHash"] = protocol.content_hash(marker)
        old_owner.validate_marker(marker, self.root)
        packages = self.root / "Packages"
        (packages / "AlphaPkg").mkdir(parents=True)
        (packages / "AlphaPkg/input").write_bytes(b"owned9 source")
        (packages / old_owner.MARKER).write_bytes(protocol.canonical(marker) + b"\n")
        (self.root / ".build").mkdir()
        state = self.root / ".build/dependencies-state.json"
        state.write_bytes(protocol.canonical(marker["projection"]))
        self.portable.write_bytes(protocol.canonical(old_lock))
        paths = [self.native, self.portable, state, packages / old_owner.MARKER]
        before = [(p.read_bytes(), p.stat().st_ino, p.stat().st_mode) for p in paths]
        with transactions.Capture(self.root, self.state).locked() as owner:
            with self.assertRaises(protocol.Reject):
                owner.publish(self.target(), owner.pair())
            self.assertFalse(owner.reservation.exists())
            self.assertFalse(owner.pending.exists())
        self.assertEqual(
            before, [(p.read_bytes(), p.stat().st_ino, p.stat().st_mode) for p in paths]
        )
        self.assertFalse((self.root / ".build/workspace-state.json").exists())

    def test_recorded9_recovers_through_shared_router_under_new_guard(self):
        import dependencies_v3_transaction as old_transactions
        from dependencies_v1_lifecycle import recover_locked

        original = copy.deepcopy(self.lock)
        original.update(version=9, workspaceStateVersion=3)
        self.rehash(original)
        self.portable.write_bytes(protocol.canonical(original))
        before = [self.native.read_bytes(), self.portable.read_bytes()]
        target = self.target()
        lock = __import__("json").loads(target[1])
        lock.update(version=9, workspaceStateVersion=3)
        self.rehash(lock)
        target[1] = protocol.canonical(lock)

        def stop(label):
            if label == "output-0-renamed":
                raise Interrupted()

        with old_transactions.Capture(self.root, self.state).locked() as owner:
            with self.assertRaises(Interrupted):
                owner.publish(target, owner.pair(), stop)
        with transactions.Capture(self.root, self.state).locked() as owner:
            self.assertEqual(recover_locked(owner), "restored-old-pair")
            self.assertEqual(owner.pair(), before)
        self.assertFalse((self.root / ".build/workspace-state.json").exists())

    def test_fresh_publication_recovers_recorded9_then_requires_recapture(self):
        import dependencies_v3_transaction as old_transactions

        original = copy.deepcopy(self.lock)
        original.update(version=9, workspaceStateVersion=3)
        self.rehash(original)
        self.portable.write_bytes(protocol.canonical(original))
        before = [self.native.read_bytes(), self.portable.read_bytes()]
        target = self.target()
        lock = __import__("json").loads(target[1])
        lock.update(version=9, workspaceStateVersion=3)
        self.rehash(lock)
        target[1] = protocol.canonical(lock)

        def stop(label):
            if label == "output-0-renamed":
                raise Interrupted()

        with old_transactions.Capture(self.root, self.state).locked() as owner:
            with self.assertRaises(Interrupted):
                owner.publish(target, owner.pair(), stop)
        with transactions.Capture(self.root, self.state).locked() as owner:
            with self.assertRaisesRegex(protocol.Reject, "recapture"):
                owner.publish(self.target(), owner.pair())
            self.assertFalse(owner.hold.exists())
            self.assertEqual(owner.pair(), before)
        self.assertFalse((self.root / ".build/workspace-state.json").exists())

    def test_invalid_journal_with_typed_private_reservation_enters_hold(self):
        from dependencies_v1_transaction import JOURNAL

        self.interrupt_after_first_output()
        (self.root / JOURNAL).write_bytes(b"not JSON\n")
        with transactions.Capture(self.root, self.state).locked() as owner:
            before = owner.pair()
            with self.assertRaises(protocol.Reject):
                owner.recover()
            self.assertTrue(owner.hold.exists())
            self.assertEqual(owner.pair(), before)
        self.assertEqual((self.root / JOURNAL).read_bytes(), b"not JSON\n")

    def test_late_foreign_cache_enters_hold_before_further_outputs(self):
        foreign = self.root / "Packages"

        def inject(label):
            if label == "output-0-renamed":
                foreign.mkdir()
                (foreign / "user-data").write_bytes(b"foreign data")

        with transactions.Capture(self.root, self.state).locked() as owner:
            portable = self.portable.read_bytes()
            with self.assertRaises(protocol.Reject):
                owner.publish(self.target(), owner.pair(), inject)
            self.assertTrue(owner.hold.exists())
            self.assertEqual(self.portable.read_bytes(), portable)
        self.assertEqual((foreign / "user-data").read_bytes(), b"foreign data")


if __name__ == "__main__":
    unittest.main()
