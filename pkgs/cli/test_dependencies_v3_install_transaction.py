# SPDX-License-Identifier: MIT
"""Finite typed installation controls; synthetic hashes are not NAR evidence."""

import copy
import hashlib
import json
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import dependencies_v3 as protocol
import dependencies_v3_ownership as ownership
from dependencies_v1_install_transaction import snapshot
from dependencies_v1_transaction import encoded, identity, Transaction
from dependencies_v3_install_transaction import Installation
from test_dependencies_v3 import WireFixture


class Interrupted(BaseException):
    pass


class TypedInstallation(WireFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.registry = self.work / "registry"
        self.registry.mkdir(mode=0o700)
        self.select_graph()

    def select_graph(self):
        (self.root / "Package.resolved").write_bytes(protocol.canonical(self.selected))
        (self.root / "swiftpm-purifier.lock.json").write_bytes(
            protocol.canonical(self.lock)
        )

    def staged(self, old=None):
        number = len(list(self.work.glob("staging-*")))
        staging = self.work / ("staging-" + str(number))
        packages = staging / "Packages"
        (packages / "AlphaPkg").mkdir(parents=True)
        (packages / "AlphaPkg/Package.swift").write_text("// synthetic unit source\n")
        marker = {
            "version": 1,
            "protocol": "dependencies-state-v3",
            "rootIdentity": identity(self.root),
            "portable": copy.deepcopy(self.lock),
            "nativePins": encoded(protocol.canonical(self.selected)),
            "originalState": (
                old["originalState"] if old else ownership.native_state(self.root)
            ),
            "projection": ownership.projection(self.lock, self.root),
        }
        marker["contentHash"] = protocol.content_hash(marker)
        (packages / ownership.MARKER).write_bytes(protocol.canonical(marker) + b"\n")
        (packages / ownership.MARKER).chmod(0o600)
        build = staging / "build"
        build.mkdir(mode=0o700)
        state = build / "dependencies-state.json"
        state.write_bytes(protocol.canonical(marker["projection"]))
        state.chmod(0o600)
        roles = [packages, state if (self.root / ".build").exists() else build]
        return marker, roles

    def roles(self):
        return [
            self.root / "Packages",
            (
                self.root / ".build/dependencies-state.json"
                if (self.root / ".build").exists()
                else self.root / ".build"
            ),
        ]

    def owner(self, owners=None):
        return Installation(
            self.root,
            self.registry,
            owners=owners,
            hash_tree=lambda path: self.lock["pins"]["alpha"]["hash"],
        )

    def install(self):
        marker, roles = self.staged()
        with self.owner([None, marker]).locked() as owner:
            owner.publish(roles, [snapshot(path) for path in self.roles()])
        return marker

    def test_first_install_and_uninstall_preserve_absence(self):
        marker = self.install()
        self.assertEqual(
            ownership.existing(
                self.root, lambda path: self.lock["pins"]["alpha"]["hash"]
            ),
            marker,
        )
        with self.owner([marker, None]).locked() as owner:
            owner.publish([None, None], [snapshot(path) for path in self.roles()])
        self.assertFalse((self.root / "Packages").exists())
        self.assertIsNone(ownership.native_state(self.root))

    def test_original_bytes_mode_restored_exactly(self):
        (self.root / ".build").mkdir()
        raw = (
            b"  "
            + protocol.canonical(protocol.checkout_records(self.lock, self.root))
            + b"\n"
        )
        state = self.root / ".build/dependencies-state.json"
        state.write_bytes(raw)
        state.chmod(0o640)
        original = snapshot(state)
        marker = self.install()
        source = self.work / "original"
        source.write_bytes(raw)
        source.chmod(0o640)
        with self.owner([marker, None]).locked() as owner:
            owner.publish([None, source], [snapshot(path) for path in self.roles()])
        self.assertEqual(snapshot(state), original)

    def test_pretransition_manifest_edits_allow_replacement(self):
        old = self.install()
        for path in (self.root / "Package.swift", self.root / "LocalPkg/Package.swift"):
            path.write_text(path.read_text() + "// user edit before relock\n")
        self.lock["manifestHash"] = protocol.manifest_hash(
            self.root, [self.local_record]
        )
        self.lock["contentHash"] = protocol.content_hash(self.lock)
        self.select_graph()
        new, roles = self.staged(old)
        with self.owner([old, new]).locked() as owner:
            owner.publish(roles, [snapshot(path) for path in self.roles()])
        self.assertEqual(
            ownership.existing(
                self.root, lambda path: self.lock["pins"]["alpha"]["hash"]
            ),
            new,
        )
        self.assertEqual(new["originalState"], old["originalState"])

    def test_postreservation_manifest_drift_refuses_without_mutation(self):
        marker, roles = self.staged()

        def stop(label):
            if label == "install-reservation-durable":
                raise Interrupted()

        with self.owner([None, marker]).locked() as owner:
            with self.assertRaises(Interrupted):
                owner.publish(roles, [snapshot(path) for path in self.roles()], stop)
        (self.root / "LocalPkg/Package.swift").write_text(
            "// foreign postreservation edit\n"
        )
        with self.owner().locked() as owner:
            with self.assertRaises(protocol.Reject):
                owner.recover()
            self.assertTrue(owner.reservation.exists())
        self.assertFalse((self.root / "Packages").exists())
        self.assertFalse((self.root / ".build").exists())

    def transition(self, mode):
        old = self.install() if mode != "first" else None
        if mode == "uninstall":
            return [old, None], [None, None]
        if mode == "replacement":
            self.lock["products"].append({"name": "Additional", "type": "executable"})
            self.lock["contentHash"] = protocol.content_hash(self.lock)
            self.select_graph()
        new, roles = self.staged(old)
        return [old, new], roles

    def test_every_first_replacement_uninstall_callback_recovers(self):
        for mode in ("first", "replacement", "uninstall"):
            probe = TypedInstallation("runTest")
            probe.setUp()
            try:
                owners, roles = probe.transition(mode)
                labels = []
                with probe.owner(owners).locked() as owner:
                    owner.publish(
                        roles, [snapshot(path) for path in probe.roles()], labels.append
                    )
            finally:
                probe.doCleanups()
            for boundary in labels:
                with self.subTest(mode=mode, boundary=boundary):
                    case = TypedInstallation("runTest")
                    case.setUp()
                    try:
                        owners, roles = case.transition(mode)
                        paths = case.roles()
                        old = [snapshot(path) for path in paths]
                        new = [
                            None if path is None else snapshot(path) for path in roles
                        ]
                        seen = []

                        def stop(label):
                            seen.append(label)
                            if label == boundary:
                                raise Interrupted()

                        with case.owner(owners).locked() as owner:
                            with self.assertRaises(Interrupted):
                                owner.publish(roles, old, stop)
                        with case.owner().locked() as fresh:
                            fresh.recover()
                            self.assertFalse(fresh.reservation.exists())
                        self.assertEqual(
                            [snapshot(path) for path in paths],
                            new if "install-committed" in seen else old,
                        )
                    finally:
                        case.doCleanups()

    def test_foreign_second_role_prevents_first_role_rollback(self):
        old = self.install()
        new, roles = self.staged(old)

        def stop(label):
            if label == "install-new-renamed-0":
                raise Interrupted()

        with self.owner([old, new]).locked() as owner:
            with self.assertRaises(Interrupted):
                owner.publish(roles, [snapshot(path) for path in self.roles()], stop)
        packages = snapshot(self.root / "Packages")
        state = self.root / ".build/dependencies-state.json"
        state.write_bytes(b"foreign second-role bytes")
        with self.owner().locked() as owner:
            with self.assertRaises(protocol.Reject):
                owner.recover()
            self.assertTrue(owner.reservation.exists())
        self.assertEqual(snapshot(self.root / "Packages"), packages)
        self.assertEqual(state.read_bytes(), b"foreign second-role bytes")

    def test_oversized_envelopes_refused_before_project_or_reservation_write(self):
        marker, roles = self.staged()
        with self.owner([None, marker]).locked() as owner:
            with mock.patch("dependencies_v3_install_transaction.LIMIT", 1024):
                with self.assertRaises(protocol.Reject):
                    owner.publish(roles, [None, None])
            self.assertFalse(owner.reservation.exists())
            self.assertFalse(owner.pending.exists())
        self.assertFalse((self.root / "Packages").exists())
        self.assertFalse((self.root / ".build").exists())

    def test_oversized_original_state_refused(self):
        original = {
            "kind": "regular",
            "bytes": encoded(b" " * (1024 * 1024 + 1)),
            "mode": 0o600,
        }
        with self.assertRaisesRegex(protocol.Reject, "byte bound"):
            ownership.original_state(original, self.root)

    def test_shared_router_recovers_typed_installation(self):
        from dependencies_v1_lifecycle import recover_locked

        marker, roles = self.staged()

        def stop(label):
            if label == "install-new-renamed-0":
                raise Interrupted()

        with self.owner([None, marker]).locked() as owner:
            with self.assertRaises(Interrupted):
                owner.publish(roles, [None, None], stop)
        with Transaction(self.root, self.registry).locked() as guard:
            self.assertEqual(recover_locked(guard), "restored-install")
            self.assertFalse(guard.reservation.exists())
        self.assertFalse((self.root / "Packages").exists())

    def test_postreservation_configuration_change_retains_hold(self):
        marker, roles = self.staged()

        def stop(label):
            if label == "install-reservation-durable":
                raise Interrupted()

        with self.owner([None, marker]).locked() as owner:
            with self.assertRaises(Interrupted):
                owner.publish(roles, [None, None], stop)
        with mock.patch.dict("os.environ", {"SWIFTPM_BUILD_DIR": "/foreign-cache"}):
            with self.owner().locked() as owner:
                with self.assertRaises(protocol.Reject):
                    owner.recover()
                self.assertTrue(owner.reservation.exists())
                self.assertTrue(owner.hold.exists())
        self.assertFalse((self.root / "Packages").exists())

    def test_maximum_original_bytes_survive_replacement_and_uninstall(self):
        (self.root / ".build").mkdir()
        state = self.root / ".build/dependencies-state.json"
        body = protocol.canonical(protocol.checkout_records(self.lock, self.root))
        raw = b" " * (1024 * 1024 - len(body)) + body
        state.write_bytes(raw)
        state.chmod(0o640)
        original = snapshot(state)
        old = self.install()
        new, roles = self.staged(old)

        def stop(label):
            if label == "install-committed":
                raise Interrupted()

        with self.owner([old, new]).locked() as owner:
            with self.assertRaises(Interrupted):
                owner.publish(roles, [snapshot(path) for path in self.roles()], stop)
        with self.owner().locked() as fresh:
            fresh.recover()
        self.assertEqual(
            ownership.existing(
                self.root, lambda path: self.lock["pins"]["alpha"]["hash"]
            )["originalState"],
            original,
        )
        target = self.work / "maximum-original"
        target.write_bytes(raw)
        target.chmod(0o640)
        with self.owner([new, None]).locked() as owner:
            owner.publish([None, target], [snapshot(path) for path in self.roles()])
        self.assertEqual(snapshot(state), original)

    def interrupt_replacement(self, label):
        old = self.install()
        new, roles = self.staged(old)

        def stop(current):
            if current == label:
                raise Interrupted()

        with self.owner([old, new]).locked() as owner:
            with self.assertRaises(Interrupted):
                owner.publish(roles, [snapshot(path) for path in self.roles()], stop)
        return old, new

    def test_same_bytes_replaced_selected_inputs_retained_with_hold(self):
        for filename in ("Package.resolved", "swiftpm-purifier.lock.json"):
            with self.subTest(filename=filename):
                case = TypedInstallation("runTest")
                case.setUp()
                try:
                    case.interrupt_replacement("install-prepared")
                    selected = case.root / filename
                    target = case.work / "replacement-input"
                    target.write_bytes(selected.read_bytes())
                    target.chmod(selected.stat().st_mode & 0o777)
                    target.replace(selected)
                    before = [snapshot(path) for path in case.roles()]
                    with case.owner().locked() as owner:
                        with self.assertRaises(protocol.Reject):
                            owner.recover()
                        self.assertTrue(owner.hold.exists())
                        self.assertTrue(owner.reservation.exists())
                    self.assertEqual([snapshot(path) for path in case.roles()], before)
                finally:
                    case.doCleanups()

    def test_replaced_build_parent_retained_before_role_mutation(self):
        self.interrupt_replacement("install-prepared")
        build = self.root / ".build"
        original = self.work / "original-build"
        build.rename(original)
        shutil.copytree(original, build)
        before = [snapshot(path) for path in self.roles()]
        with self.owner().locked() as owner:
            with self.assertRaises(protocol.Reject):
                owner.recover()
            self.assertTrue(owner.hold.exists())
        self.assertEqual([snapshot(path) for path in self.roles()], before)
        self.assertTrue(original.exists())

    def test_foreign_committed_control_retains_complete_roles_and_backups(self):
        self.interrupt_replacement("install-committed")
        journal = self.root / ".swiftpm-purifier-transaction.json"
        journal.write_bytes(b"foreign committed control\n")
        before = [snapshot(path) for path in self.roles()]
        with self.owner().locked() as owner:
            record = json.loads(owner.reservation.read_bytes())
            stage = owner.stage(record)
            staged_before = snapshot(stage)
            with self.assertRaises(protocol.Reject):
                owner.recover()
            self.assertTrue(owner.hold.exists())
            self.assertEqual(snapshot(stage), staged_before)
        self.assertEqual([snapshot(path) for path in self.roles()], before)
        self.assertEqual(journal.read_bytes(), b"foreign committed control\n")

    def test_missing_marker_in_interrupted_owned_backup_cleanup_recovers(self):
        old = self.install()
        new, roles = self.staged(old)

        def stop(label):
            if label == "tree-remove-" + ownership.MARKER:
                raise Interrupted()

        with self.owner([old, new]).locked() as owner:
            with self.assertRaises(Interrupted):
                owner.publish(roles, [snapshot(path) for path in self.roles()], stop)
            record = json.loads(owner.reservation.read_bytes())
            self.assertFalse(
                (owner.stage(record) / "old-0" / ownership.MARKER).exists()
            )
        with self.owner().locked() as fresh:
            fresh.recover()
            self.assertFalse(fresh.reservation.exists())
        self.assertEqual(
            ownership.existing(
                self.root, lambda path: self.lock["pins"]["alpha"]["hash"]
            ),
            new,
        )

    def test_real_limit_large_closed_envelope_refused_before_any_write(self):
        from dependencies_v1_transaction import LIMIT

        marker, roles = self.staged()
        with self.owner([None, marker]).locked() as owner:
            new = [snapshot(path) for path in roles]
            empty_hash = hashlib.sha256(b"").hexdigest()
            for index in range(50000):
                name = "AlphaPkg/" + str(index).zfill(5) + "-" + "x" * 234
                new[0]["entries"][name] = {
                    "kind": "regular",
                    "mode": 0o600,
                    "sha256": empty_hash,
                    "size": 0,
                }
            record = {
                "version": 1,
                "protocol": owner.PROTOCOL,
                "operation": owner.INSTALL_OPERATION,
                "rootIdentity": owner.root_id,
                "buildIdentity": None,
                "nonce": "a" * 64,
                "old": [None, None],
                "new": new,
            }
            owner.planned_new = new
            record = owner.prepare_install_record(record)
            self.assertGreater(len(protocol.canonical(record)), LIMIT)
            with self.assertRaisesRegex(protocol.Reject, "byte bound"):
                owner.validate(record)
            self.assertFalse(owner.pending.exists())
            self.assertFalse(owner.reservation.exists())
        self.assertFalse((self.root / "Packages").exists())

    def test_foreign_installed_marker_tree_and_mode_retained_with_hold(self):
        for change in ("marker", "tree", "mode"):
            with self.subTest(change=change):
                case = TypedInstallation("runTest")
                case.setUp()
                try:
                    case.interrupt_replacement("install-new-renamed-0")
                    if change == "marker":
                        (case.root / "Packages" / ownership.MARKER).write_bytes(
                            b"foreign marker bytes\n"
                        )
                    elif change == "tree":
                        (case.root / "Packages/user-data").write_bytes(
                            b"foreign tree data\n"
                        )
                    else:
                        target = case.root / "Packages/AlphaPkg/Package.swift"
                        target.chmod((target.stat().st_mode & 0o777) ^ 0o100)
                    before = [snapshot(path) for path in case.roles()]
                    with case.owner().locked() as owner:
                        with self.assertRaises(protocol.Reject):
                            owner.recover()
                        self.assertTrue(owner.hold.exists())
                        self.assertTrue(owner.reservation.exists())
                    self.assertEqual([snapshot(path) for path in case.roles()], before)
                finally:
                    case.doCleanups()

    def test_foreign_entry_during_backup_cleanup_retained_with_hold(self):
        old = self.install()
        new, roles = self.staged(old)

        def stop(label):
            if label == "tree-remove-" + ownership.MARKER:
                raise Interrupted()

        with self.owner([old, new]).locked() as owner:
            with self.assertRaises(Interrupted):
                owner.publish(roles, [snapshot(path) for path in self.roles()], stop)
            record = json.loads(owner.reservation.read_bytes())
            stage = owner.stage(record)
            foreign = stage / "old-0/foreign-user-data"
            foreign.write_bytes(b"keep this data\n")
            before = snapshot(stage)
        with self.owner().locked() as owner:
            with self.assertRaises(protocol.Reject):
                owner.recover()
            self.assertTrue(owner.hold.exists())
            self.assertEqual(snapshot(stage), before)
        self.assertEqual(foreign.read_bytes(), b"keep this data\n")
