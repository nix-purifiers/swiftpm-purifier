# SPDX-License-Identifier: MIT
"""Closed codec controls; synthetic NAR values are not native/build evidence."""

import base64
import copy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

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
        self.assertTrue(purifier.workspace_path_candidate(self.native, root=self.root))
        later = json.loads(
            (FIXTURE.parent / "native553/workspace-state.json").read_bytes()
        )
        self.assertFalse(purifier.workspace_path_candidate(later, root=self.root))
        self.assertFalse(
            purifier.workspace_path_candidate(
                {"version": 4, "object": {"dependencies": [], "artifacts": []}},
                root=self.root,
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
                purifier.workspace_path_candidate(value, root=self.root)

    def test_cli_local_path_discovery_requires_actual_source_root(self):
        # Root-containment control; actual local-native execution has separate receipts.
        value = copy.deepcopy(self.native)
        value["object"]["dependencies"].append(
            {
                "basedOn": None,
                "packageRef": {
                    "identity": "localpkg",
                    "kind": "local",
                    "name": "LocalPkg",
                    "path": str(self.root / "LocalPkg"),
                },
                "state": {"name": "local"},
                "subpath": "localpkg",
            }
        )
        self.assertTrue(purifier.workspace_path_candidate(value, root=self.root))
        wrong = self.root / "DifferentSource"
        wrong.mkdir()
        with self.assertRaises(purifier.PurifierError):
            purifier.workspace_path_candidate(value, root=wrong)
        with tempfile.TemporaryDirectory() as temporary:
            probe = Path(temporary)
            value["object"]["dependencies"][-1]["packageRef"]["path"] = str(
                probe / "LocalPkg"
            )
            self.assertTrue(purifier.workspace_path_candidate(value, root=probe))
            with self.assertRaises(purifier.PurifierError):
                purifier.workspace_path_candidate(value, root=self.root)

    def write_migrated_caller(self):
        value = self.portable()
        value.update(version=9, workspaceStateVersion=3)
        value["contentHash"] = old.content_hash(value)
        (self.root / "swiftpm-purifier.lock.json").write_bytes(old.canonical(value))
        (self.root / ".build").mkdir()
        (self.root / ".build/workspace-state.json").write_bytes(
            old.canonical(self.native)
        )
        return value

    def test_uninstalled_native_migration_selects_fresh10_without_deleting_old_pair(
        self,
    ):
        self.write_migrated_caller()
        paths = [
            self.root / "swiftpm-purifier.lock.json",
            self.root / ".build/workspace-state.json",
        ]
        before = [p.read_bytes() for p in paths]
        with (
            mock.patch.object(purifier, "recover_dependencies_v1"),
            mock.patch.object(
                purifier, "relock_dependencies_v3", return_value="selected"
            ) as capture,
        ):
            self.assertEqual(purifier.relock(self.root, ["beta"]), "selected")
            capture.assert_called_once_with(self.root, ["beta"], 10)
        self.assertEqual([p.read_bytes() for p in paths], before)

    def test_existing_package_paths_preserve_old_protocol_authority(self):
        self.write_migrated_caller()
        packages = self.root / "Packages"
        for kind in ("foreign", "owned9", "dangling-link"):
            with self.subTest(kind=kind):
                if kind == "dangling-link":
                    packages.symlink_to(self.root / "absent")
                else:
                    packages.mkdir()
                    if kind == "owned9":
                        (
                            packages / ".swiftpm-purifier-dependencies-v3-owned.json"
                        ).write_text("{}")
                with (
                    mock.patch.object(purifier, "recover_dependencies_v1"),
                    mock.patch.object(purifier, "relock_dependencies_v3") as capture,
                ):
                    purifier.relock(self.root)
                    capture.assert_called_once_with(self.root, None)
                if packages.is_symlink():
                    packages.unlink()
                else:
                    for p in packages.iterdir():
                        p.unlink()
                    packages.rmdir()

    def test_legacy_companion_blocks_fresh_selection_even_when_dangling(self):
        value = self.write_migrated_caller()
        companion = self.root / ".build/dependencies-state.json"
        for kind in ("regular", "dangling-link"):
            with self.subTest(kind=kind):
                if kind == "regular":
                    companion.write_text("{}")
                else:
                    companion.symlink_to(self.root / "absent")
                self.assertFalse(purifier.fresh_path_workspace(self.root, value))
                companion.unlink()

    def test_fresh_discovery_requires_literal9_and_complete_nonempty_path4(self):
        value = self.write_migrated_caller()
        for version in (True, 4, 10, "9", None):
            with self.subTest(version=version):
                self.assertFalse(
                    purifier.fresh_path_workspace(
                        self.root, value | {"version": version}
                    )
                )
        path = self.root / ".build/workspace-state.json"
        for native in (
            {"version": 4, "object": {"dependencies": [], "artifacts": []}},
            json.loads(
                (FIXTURE.parent / "native553/workspace-state.json").read_bytes()
            ),
        ):
            path.write_bytes(old.canonical(native))
            self.assertFalse(purifier.fresh_path_workspace(self.root, value))
        malformed = copy.deepcopy(self.native)
        malformed["object"]["dependencies"][0]["packageRef"]["location"] = (
            "https://example.org/mixed"
        )
        path.write_bytes(old.canonical(malformed))
        with self.assertRaises(purifier.PurifierError):
            purifier.fresh_path_workspace(self.root, value)

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
