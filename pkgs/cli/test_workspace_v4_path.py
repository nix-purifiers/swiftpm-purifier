# SPDX-License-Identifier: MIT
"""Closed codec controls; synthetic NAR values are not native/build evidence."""

import base64
import copy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import tempfile
import unittest

import dependencies_v3 as old
import native_path_protocols as protocols
import workspace_v4_path as workspace
import swiftpm_purifier as purifier


FIXTURE = Path(__file__).parent / "fixtures/native533"


class WorkspacePathCodec(unittest.TestCase):
    def setUp(self):
        self.native = json.loads((FIXTURE / "workspace-state.json").read_bytes())
        self.pins = json.loads((FIXTURE / "Package.resolved").read_bytes())
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "Package.swift").write_text("// closed codec manifest control\n")

    def portable(self):
        pins = {}
        for dep in self.native["object"]["dependencies"]:
            ref, state = dep["packageRef"], dep["state"]["checkoutState"]
            pins[ref["identity"]] = {
                "name": ref["name"],
                "sourceLocation": ref["path"],
                "revision": state["revision"],
                "version": state["version"],
                "hash": "sha256-" + base64.b64encode(bytes(32)).decode(),
            }
        value = {
            "version": 10,
            "workspaceStateVersion": 4,
            "package": "Root",
            "rootDirectory": self.root.name,
            "pins": pins,
            "products": [],
            "localDependencies": [],
            "binaryTargets": [],
            "manifestHash": old.manifest_hash(self.root, []),
        }
        value["contentHash"] = old.content_hash(value)
        return value

    def test_cli_detects_complete_path_geometry_and_keeps_empty4_ambiguous(self):
        self.assertTrue(purifier.workspace_path_candidate(self.native))
        later = json.loads(
            (FIXTURE.parent / "native553/workspace-state.json").read_bytes()
        )
        self.assertFalse(purifier.workspace_path_candidate(later))
        self.assertFalse(
            purifier.workspace_path_candidate(
                {"version": 4, "object": {"dependencies": [], "artifacts": []}}
            )
        )
        for mutate in (
            lambda value: value["object"]["dependencies"][0]["packageRef"].update(
                location="https://example.org/ambiguous"
            ),
            lambda value: value["object"].update(artifacts=[{}]),
            lambda value: value["object"]["dependencies"][0]["packageRef"].pop("name"),
        ):
            value = copy.deepcopy(self.native)
            mutate(value)
            with self.assertRaises(purifier.PurifierError):
                purifier.workspace_path_candidate(value)

    def test_original_native_point_and_closed_portable_join(self):
        self.assertEqual(set(workspace.native_index(self.native)), {"alpha", "beta"})
        value = self.portable()
        self.assertEqual(workspace.validate_lock(value, self.pins, self.root), value)
        self.assertEqual(workspace.checkout_records(value, self.root), self.native)

    def test_format9_and_format10_do_not_accept_each_other(self):
        with self.assertRaises(old.Reject):
            old.native_index(self.native)
        value = self.portable()
        with self.assertRaises(old.Reject):
            old.validate_lock(value, self.pins, self.root)
        value["version"], value["workspaceStateVersion"] = 9, 3
        value["contentHash"] = old.content_hash(value)
        with self.assertRaises(old.Reject):
            workspace.validate_lock(value, self.pins, self.root)

    def test_numeric4_location_and_mixed_references_are_not_path_protocol(self):
        for mixed in [False, True]:
            value = copy.deepcopy(self.native)
            ref = value["object"]["dependencies"][0]["packageRef"]
            ref["location"] = ref["path"]
            if not mixed:
                del ref["path"]
            with self.subTest(mixed=mixed), self.assertRaises(old.Reject):
                workspace.native_index(value)

    def test_artifacts_and_native_envelope_are_closed(self):
        for change in ["missing", "nonempty", "extra", "wrong-version"]:
            value = copy.deepcopy(self.native)
            if change == "missing":
                del value["object"]["artifacts"]
            elif change == "nonempty":
                value["object"]["artifacts"] = [{}]
            elif change == "extra":
                value["object"]["prebuilts"] = []
            else:
                value["version"] = 3
            with self.subTest(change=change), self.assertRaises(old.Reject):
                workspace.native_index(value)

    def test_edited_geometry_retains_full_original_checkout(self):
        before = copy.deepcopy(self.native)
        edited = workspace.edited_state(self.native)
        self.assertEqual(self.native, before)
        original = workspace.native_index(before)
        projected = workspace.native_index(edited, allow_edited=True)
        for identity, record in projected.items():
            self.assertEqual(record["basedOn"], original[identity])
            self.assertEqual(record["subpath"], record["packageRef"]["name"])
        with self.assertRaises(old.Reject):
            workspace.native_index(edited)

    def test_selected_pin_closure_is_not_relaxed_by_new_format(self):
        value = self.portable()
        value["pins"]["alpha"]["revision"] = "f" * 40
        value["contentHash"] = old.content_hash(value)
        with self.assertRaises(old.Reject):
            workspace.validate_lock(value, self.pins, self.root)

    def test_descriptor_is_immutable_and_forged_identity_is_rejected(self):
        descriptor = protocols.WORKSPACE_V4_PATH
        with self.assertRaises(FrozenInstanceError):
            descriptor.state_basename = "dependencies-state.json"
        with self.assertRaises(old.Reject):
            old.native_index(self.native, protocol=replace(descriptor))
        with self.assertRaises(TypeError):
            workspace.native_index(self.native, protocol=protocols.DEPENDENCIES_V3)

    def test_raw_byte_bounds_and_closed_version_are_retained(self):
        value = self.portable()
        self.assertEqual(
            workspace.validate_raw(
                old.canonical(value), old.canonical(self.pins), self.root
            ),
            value,
        )
        with self.assertRaises(old.Reject):
            workspace.validate_raw(b" " * (1024 * 1024 + 1), b"{}", self.root)


if __name__ == "__main__":
    unittest.main()
