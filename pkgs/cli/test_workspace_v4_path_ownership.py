# SPDX-License-Identifier: MIT
"""Ownership marker, original mode/absence and foreign-tree refusal controls."""

import copy
import unittest

import workspace_v4_path as protocol
import workspace_v4_path_ownership as ownership
from dependencies_v1_transaction import encoded, identity
from test_dependencies_v3 import WireFixture


class Ownership(WireFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.lock.update(version=10, workspaceStateVersion=4)
        self.rehash(self.lock)

    def marker(self, original=None):
        value = {
            "version": 1,
            "protocol": "workspace-state-v4-path",
            "rootIdentity": identity(self.root),
            "portable": self.lock,
            "nativePins": encoded(protocol.canonical(self.selected)),
            "originalState": original,
            "projection": ownership.projection(self.lock, self.root),
        }
        value["contentHash"] = protocol.content_hash(value)
        return value

    def test_original_exact_bytes_mode_and_absence_retained(self):
        raw = (
            b"  "
            + protocol.canonical(protocol.checkout_records(self.lock, self.root))
            + b"\n"
        )
        original = {"kind": "regular", "bytes": encoded(raw), "mode": 0o640}
        for state in (None, original):
            value = self.marker(state)
            validated = ownership.validate_marker(value, self.root)
            self.assertEqual(validated["originalState"], state)

    def test_wrong_root_protocol_mode_and_projection_refused(self):
        cases = []
        for key, value in [
            ("rootIdentity", [0, 0]),
            ("protocol", "dependencies-state-v1"),
            ("nativePins", None),
        ]:
            marker = self.marker()
            marker[key] = value
            cases.append(marker)
        marker = self.marker(
            {"kind": "regular", "bytes": encoded(b"{}"), "mode": 0o600}
        )
        cases.append(marker)
        marker = self.marker()
        marker["projection"]["object"]["dependencies"][0]["state"] = {"name": "local"}
        cases.append(marker)
        for marker in cases:
            with self.subTest(marker=marker):
                marker["contentHash"] = protocol.content_hash(marker)
                with self.assertRaises((protocol.Reject, ValueError)):
                    ownership.validate_marker(marker, self.root)

    def install_fixture(self):
        value = self.marker()
        packages = self.root / "Packages"
        (packages / "AlphaPkg").mkdir(parents=True)
        (packages / "AlphaPkg/Package.swift").write_text("// owned fixture\n")
        (packages / ownership.MARKER).write_bytes(protocol.canonical(value) + b"\n")
        (self.root / ".build").mkdir()
        (self.root / ".build/workspace-state.json").write_bytes(
            protocol.canonical(value["projection"])
        )
        return value

    def test_complete_set_and_foreign_additions_checked_read_only(self):
        value = self.install_fixture()
        fake_nar = lambda path: self.lock["pins"]["alpha"]["hash"]
        self.assertEqual(ownership.existing(self.root, fake_nar), value)
        foreign = self.root / "Packages/foreign"
        foreign.write_bytes(b"user data")
        with self.assertRaises(protocol.Reject):
            ownership.existing(self.root, fake_nar)
        self.assertEqual(foreign.read_bytes(), b"user data")
        foreign.unlink()
        with self.assertRaises(protocol.Reject):
            ownership.existing(self.root, lambda path: "sha256-changed")

    def test_native_data_cannot_authorize_unowned_tree(self):
        self.install_fixture()
        (self.root / "Packages" / ownership.MARKER).unlink()
        with self.assertRaises(OSError):
            ownership.existing(
                self.root, lambda path: self.lock["pins"]["alpha"]["hash"]
            )
        self.assertTrue((self.root / "Packages/AlphaPkg/Package.swift").exists())
