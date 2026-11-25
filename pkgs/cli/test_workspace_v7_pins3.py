# SPDX-License-Identifier: MIT
"""Actual native fixture grammar and finite fail-closed source controls.

These tests invoke no SDK, CLI, network or FOD builder. Placeholder FOD hashes
exercise structure only; they are never source-provenance evidence.
"""

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

import native_path_family as family
import native_path_protocols as paths
import native_protocols as protocols
import workspace_v5_location as old
import workspace_v7_pins3 as codec
import workspace_v7_pins3_capture as capture

FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/workspace7-pins3"


class Workspace7Pins3(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "Package.swift").write_bytes(
            (FIXTURE / "Package.swift").read_bytes()
        )
        self.state = json.loads((FIXTURE / "workspace-state.json").read_text())
        self.pins = json.loads((FIXTURE / "Package.resolved").read_text())

    def lock(self):
        pins = {}
        for row in self.state["object"]["dependencies"]:
            ref = row["packageRef"]
            state = row["state"]["checkoutState"]
            pins[ref["identity"]] = {
                "name": ref["name"],
                "sourceLocation": ref["location"],
                "revision": state["revision"],
                "version": state["version"],
                "branch": None,
                "hash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            }
        lock = {
            "version": 12,
            "workspaceStateVersion": 7,
            "package": "PureApp",
            "rootDirectory": "caller",
            "manifestHash": codec.manifest_hash(self.root, []),
            "originHash": self.pins["originHash"],
            "pins": pins,
            "localDependencies": [],
            "binaryTargets": [],
            "products": [{"name": "PureApp", "type": "executable"}],
        }
        lock["contentHash"] = codec.content_hash(lock)
        return lock

    def test_actual_native_and_selected_closure(self):
        rows = codec.native_index(self.state, self.root)
        pins = codec.pins_index(self.pins)
        self.assertEqual(set(rows), {"alpha", "beta"})
        self.assertEqual(len(pins), 2)
        codec.attest_pins(self.pins, self.root)
        for row in rows.values():
            self.assertTrue(
                codec.selected_pin_matches(pins[row["packageRef"]["location"]], row)
            )

    def captured_root(self):
        (self.root / ".build/checkouts").mkdir(parents=True)
        (self.root / ".build/workspace-state.json").write_text(json.dumps(self.state))
        (self.root / "Package.resolved").write_text(json.dumps(self.pins))
        for name in ["alpha", "beta"]:
            checkout = self.root / ".build/checkouts" / name
            checkout.mkdir()
            (checkout / "Package.swift").write_text("// structural tree fixture only\n")

    def test_read_only_capture_join_uses_native_pins3_identity(self):
        self.captured_root()
        records, raw = capture.native_graph(self.root, {})
        self.assertEqual(set(records), {"alpha", "beta"})
        self.assertEqual(json.loads(raw), self.pins)

    def test_read_only_capture_refuses_pin_state_and_manifest_mismatch(self):
        self.captured_root()
        pins = copy.deepcopy(self.pins)
        pins["pins"][0]["state"]["revision"] = "f" * 40
        (self.root / "Package.resolved").write_text(json.dumps(pins))
        with self.assertRaises(ValueError):
            capture.native_graph(self.root, {})
        (self.root / "Package.resolved").write_text(json.dumps(self.pins))
        (self.root / "Package.swift").write_text("// changed origin\n")
        with self.assertRaises(ValueError):
            capture.native_graph(self.root, {})

    def test_portable_tag_roundtrip(self):
        lock = self.lock()
        codec.validate_lock(lock, self.pins, self.root)
        self.assertEqual(codec.checkout_records(lock, self.root), self.state)
        codec.validate_raw(codec.canonical(lock), codec.canonical(self.pins), self.root)

    def test_literal_authorities_are_distinct(self):
        self.assertIs(
            protocols.checked(protocols.WORKSPACE_V7_PINS3),
            protocols.WORKSPACE_V7_PINS3,
        )
        self.assertIs(family.codec(protocols.WORKSPACE_V7_PINS3), codec)
        with self.assertRaises(ValueError):
            protocols.checked(replace(protocols.WORKSPACE_V7_PINS3))
        with self.assertRaises(ValueError):
            paths.checked(protocols.WORKSPACE_V7_PINS3)
        with self.assertRaises(ValueError):
            old.native_index(self.state, self.root)
        import workspace_v7_pins3_ownership

        self.assertIs(
            family.ownership(protocols.WORKSPACE_V7_PINS3), workspace_v7_pins3_ownership
        )
        self.assertEqual(protocols.WORKSPACE_V5_LOCATION.portable_format, 11)
        self.assertEqual(protocols.WORKSPACE_V4_PATH.portable_format, 10)
        self.assertEqual(protocols.DEPENDENCIES_V3.portable_format, 9)

    def test_unknown_native_fields_and_nonempty_surfaces(self):
        for key in ["artifacts", "prebuilts"]:
            value = copy.deepcopy(self.state)
            value["object"][key] = [{"unknown": "not admitted"}]
            with self.assertRaises(ValueError):
                codec.native_index(value, self.root)
        value = copy.deepcopy(self.state)
        value["object"]["future"] = []
        with self.assertRaises(ValueError):
            codec.native_index(value, self.root)
        value = copy.deepcopy(self.state)
        value["version"] = True
        with self.assertRaises(ValueError):
            codec.native_index(value, self.root)

    def test_duplicate_identity_name_and_source(self):
        for field in ["identity", "name", "location"]:
            value = copy.deepcopy(self.state)
            value["object"]["dependencies"][1]["packageRef"][field] = value["object"][
                "dependencies"
            ][0]["packageRef"][field]
            with self.assertRaises(ValueError):
                codec.native_index(value, self.root)

    def test_unobserved_local_source_control_refused(self):
        value = copy.deepcopy(self.state)
        value["object"]["dependencies"][0]["packageRef"]["kind"] = "localSourceControl"
        with self.assertRaises(ValueError):
            codec.native_index(value, self.root)

    def test_observed_branch_revision_and_owned_edit_projection(self):
        for state in [
            {"revision": "f" * 40},
            {"revision": "f" * 40, "branch": "master"},
        ]:
            value = copy.deepcopy(self.state)
            value["object"]["dependencies"][0]["state"]["checkoutState"] = state
            codec.native_index(value, self.root)
            edited = codec.edited_state(value, self.root)
            codec.native_index(edited, self.root, allow_edited=True)
            with self.assertRaises(ValueError):
                codec.native_index(edited, self.root)
            row = edited["object"]["dependencies"][0]
            self.assertEqual(row["state"], {"name": "edited", "path": None})
            self.assertEqual(row["basedOn"], value["object"]["dependencies"][0])
            row["basedOn"]["packageRef"]["name"] = "Wrong"
            with self.assertRaises(ValueError):
                codec.native_index(edited, self.root, allow_edited=True)

    def test_observed_local_geometry_confines_both_paths(self):
        local = self.root / "Local"
        local.mkdir()
        (local / "Package.swift").write_text("// local structural fixture")
        row = {
            "basedOn": None,
            "packageRef": {
                "identity": "local",
                "name": "Local",
                "kind": "fileSystem",
                "location": str(local),
            },
            "state": {"name": "fileSystem", "path": str(local)},
            "subpath": "local",
        }
        value = copy.deepcopy(self.state)
        value["object"]["dependencies"].append(row)
        codec.native_index(value, self.root)
        self.assertEqual(
            codec.edited_state(value, self.root)["object"]["dependencies"][-1], row
        )
        row["state"]["path"] = str(self.root.parent)
        with self.assertRaises(ValueError):
            codec.native_index(value, self.root)

    def test_portable_local_template_and_manifest_currency(self):
        local = self.root / "Local"
        local.mkdir()
        (local / "Package.swift").write_text("// local manifest structural control")
        lock = self.lock()
        lock["localDependencies"] = [
            {
                "basedOn": None,
                "packageRef": {
                    "identity": "local",
                    "name": "Local",
                    "kind": "fileSystem",
                    "location": "@root@/Local",
                },
                "state": {"name": "fileSystem", "path": "@root@/Local"},
                "subpath": "local",
            }
        ]
        lock["manifestHash"] = codec.manifest_hash(
            self.root, codec.local_records(lock, self.root)
        )
        lock["contentHash"] = codec.content_hash(lock)
        codec.validate_lock(lock, self.pins, self.root)
        rendered = codec.local_records(lock, self.root)[0]
        self.assertEqual(rendered["packageRef"]["location"], str(local))
        self.assertEqual(rendered["state"]["path"], str(local))
        (local / "Package.swift").write_text("// changed local manifest")
        with self.assertRaises(ValueError):
            codec.validate_lock(lock, self.pins, self.root)
        codec.validate_lock(lock, self.pins, self.root, current=False)
        lock["localDependencies"][0]["packageRef"]["location"] = "@root@/../outside"
        lock["localDependencies"][0]["state"]["path"] = "@root@/../outside"
        with self.assertRaises(ValueError):
            codec.local_records(lock, self.root)

    def test_owned_projection_keeps_original_and_rejects_cross_protocol_markers(self):
        import workspace_v7_pins3_ownership as ownership

        self.captured_root()
        lock = self.lock()
        projection = ownership.projection(lock, self.root)
        rows = codec.native_index(projection, self.root, allow_edited=True)
        for identity, row in rows.items():
            original = codec.native_index(self.state, self.root)[identity]
            self.assertEqual(row["basedOn"], original)
            self.assertEqual(row["state"], {"name": "edited", "path": None})
        ownership.projection_matches(codec.canonical(projection), projection, self.root)
        self.assertIsNone(ownership.require_same_family(self.root, lambda p: "unused"))
        packages = self.root / "Packages"
        packages.mkdir()
        for descriptor in [
            protocols.DEPENDENCIES_V3,
            protocols.WORKSPACE_V4_PATH,
            protocols.WORKSPACE_V5_LOCATION,
            protocols.WORKSPACE_V7_PINS2,
        ]:
            marker = packages / descriptor.ownership_marker
            marker.write_text("{}")
            with self.assertRaises(ValueError):
                ownership.require_same_family(self.root, lambda p: "unused")
            self.assertEqual(marker.read_text(), "{}")
            marker.unlink()

    def test_origin_hash_malformed_and_foreign_refused(self):
        for value in [None, "", "G" * 64, "f" * 63]:
            pins = copy.deepcopy(self.pins)
            pins["originHash"] = value
            with self.assertRaises(ValueError):
                codec.pins_index(pins)
        pins = copy.deepcopy(self.pins)
        pins["originHash"] = "f" * 64
        with self.assertRaises(ValueError):
            codec.attest_pins(pins, self.root)
        pins.pop("originHash")
        with self.assertRaises(ValueError):
            codec.pins_index(pins)

    def test_manifest_change_rejects_currency_but_retains_recovery_schema(self):
        lock = self.lock()
        (self.root / "Package.swift").write_text("// changed manifest\n")
        with self.assertRaises(ValueError):
            codec.validate_lock(lock, self.pins, self.root)
        codec.validate_lock(lock, self.pins, self.root, current=False)

    def test_version_selected_manifest_context_refused(self):
        (self.root / "Package@swift-6.0.swift").write_bytes(
            (self.root / "Package.swift").read_bytes()
        )
        with self.assertRaises(ValueError):
            codec.origin_hash(self.root)

    def test_selected_revision_and_origin_hash_joins(self):
        lock = self.lock()
        pins = copy.deepcopy(self.pins)
        pins["pins"][0]["state"]["revision"] = "f" * 40
        with self.assertRaises(ValueError):
            codec.validate_lock(lock, pins, self.root)
        lock["originHash"] = "f" * 64
        lock["contentHash"] = codec.content_hash(lock)
        with self.assertRaises(ValueError):
            codec.validate_lock(lock, self.pins, self.root)


if __name__ == "__main__":
    unittest.main()
