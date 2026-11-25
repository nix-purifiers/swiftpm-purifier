# SPDX-License-Identifier: MIT
"""Finite source transaction controls; mock digests are not NAR evidence."""

import copy
from pathlib import Path
import tempfile
import unittest

from dependencies_v1_transaction import Transaction, encoded, identity
from dependencies_v1_install_transaction import snapshot
from dependencies_v1_lifecycle import recover_locked
import workspace_v7_pins2 as codec
import workspace_v7_pins2_ownership as ownership
import workspace_v7_pins2_transaction as transactions
from workspace_v7_pins2_install_transaction import Installation
import test_workspace_v7_pins2 as core_tests


class Interrupted(BaseException):
    pass


class Transactions(unittest.TestCase):
    lock_value = core_tests.Workspace7Pins2.lock

    def setUp(self):
        core_tests.Workspace7Pins2.setUp(self)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.registry = Path(temporary.name).resolve()
        self.lock = self.lock_value()
        self.native = self.root / "Package.resolved"
        self.portable = self.root / "swiftpm-purifier.lock.json"
        self.native.write_bytes(codec.canonical(self.pins))
        self.portable.write_bytes(codec.canonical(self.lock))
        self.native.chmod(0o600)
        self.portable.chmod(0o640)

    def target(self):
        pins = copy.deepcopy(self.pins)
        pins["pins"][0]["state"]["version"] = "1.0.1"
        lock = copy.deepcopy(self.lock)
        lock["pins"]["alpha"]["version"] = "1.0.1"
        lock["contentHash"] = codec.content_hash(lock)
        return [codec.canonical(pins), codec.canonical(lock)]

    def test_capture_publishes_validated_pins2_pair(self):
        target = self.target()
        with transactions.Capture(self.root, self.registry).locked() as owner:
            owner.publish(target, owner.pair())
            self.assertEqual(owner.pair(), target)
            self.assertFalse(owner.reservation.exists())
        self.assertFalse((self.root / "Packages").exists())

    def test_interrupted_capture_routes_to_exact13_and_restores_pair_modes(self):
        paths = [self.native, self.portable]
        before = [(p.read_bytes(), p.stat().st_mode & 0o777) for p in paths]

        def stop(label):
            if label == "output-0-renamed":
                raise Interrupted()

        with transactions.Capture(self.root, self.registry).locked() as owner:
            with self.assertRaises(Interrupted):
                owner.publish(self.target(), owner.pair(), stop)
        with Transaction(self.root, self.registry).locked() as owner:
            self.assertEqual(recover_locked(owner), "restored-old-pair")
        self.assertEqual(
            [(p.read_bytes(), p.stat().st_mode & 0o777) for p in paths], before
        )

    def staged(self):
        staging = self.registry / "staging"
        packages = staging / "Packages"
        for identity_ in self.lock["pins"]:
            tree = packages / identity_
            tree.mkdir(parents=True)
            (tree / "Package.swift").write_text("// synthetic unit source only\n")
        marker = {
            "version": 1,
            "protocol": "workspace-state-v7-location-pins2",
            "rootIdentity": identity(self.root),
            "portable": self.lock,
            "nativePins": encoded(codec.canonical(self.pins)),
            "originalState": None,
            "projection": ownership.projection(self.lock, self.root),
        }
        marker["contentHash"] = codec.content_hash(marker)
        (packages / ownership.MARKER).write_bytes(codec.canonical(marker) + b"\n")
        (packages / ownership.MARKER).chmod(0o600)
        build = staging / "build"
        build.mkdir(mode=0o700)
        (build / "workspace-state.json").write_bytes(
            codec.canonical(marker["projection"])
        )
        (build / "workspace-state.json").chmod(0o600)
        return marker, [packages, build]

    def owner(self, owners):
        return Installation(
            self.root,
            self.registry,
            owners=owners,
            hash_tree=lambda path: next(iter(self.lock["pins"].values()))["hash"],
        )

    def test_install_uninstall_restores_absent_roles(self):
        marker, sources = self.staged()
        roles = [self.root / "Packages", self.root / ".build"]
        with self.owner([None, marker]).locked() as owner:
            owner.publish(sources, [snapshot(p) for p in roles])
        self.assertEqual(
            ownership.existing(
                self.root, lambda path: next(iter(self.lock["pins"].values()))["hash"]
            ),
            marker,
        )
        roles = [self.root / "Packages", self.root / ".build/workspace-state.json"]
        with self.owner([marker, None]).locked() as owner:
            owner.publish([None, None], [snapshot(p) for p in roles])
        self.assertFalse((self.root / "Packages").exists())
        self.assertIsNone(ownership.native_state(self.root))

    def test_interrupted_install_shared_recovery_restores_absence(self):
        marker, sources = self.staged()
        roles = [self.root / "Packages", self.root / ".build"]

        def stop(label):
            if label == "install-new-renamed-0":
                raise Interrupted()

        with self.owner([None, marker]).locked() as owner:
            with self.assertRaises(Interrupted):
                owner.publish(sources, [None, None], stop)
        with Transaction(self.root, self.registry).locked() as owner:
            self.assertEqual(recover_locked(owner), "restored-install")
        self.assertFalse(any(p.exists() for p in roles))


if __name__ == "__main__":
    unittest.main()
