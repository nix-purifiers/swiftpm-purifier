# SPDX-License-Identifier: MIT
"""Source-only controls for actual tools5.9/native7/pins2 geometry."""

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import shutil
import unittest

import native_path_family as family
import native_protocols as protocols
import workspace_v7_pins2 as codec
import workspace_v7_pins3 as pins3

FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/workspace7-pins2"


class Workspace7Pins2(unittest.TestCase):
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
            "version": 13,
            "workspaceStateVersion": 7,
            "package": "PureApp",
            "rootDirectory": "caller",
            "manifestHash": codec.manifest_hash(self.root, []),
            "pins": pins,
            "localDependencies": [],
            "binaryTargets": [],
            "products": [{"name": "PureApp", "type": "executable"}],
        }
        lock["contentHash"] = codec.content_hash(lock)
        return lock

    def test_actual_native_pins_join_and_portable_roundtrip(self):
        rows = codec.native_index(self.state, self.root)
        selected = codec.pins_index(self.pins)
        pins = {}
        for identity, row in rows.items():
            ref = row["packageRef"]
            state = row["state"]["checkoutState"]
            self.assertTrue(codec.selected_pin_matches(selected[ref["location"]], row))
            pins[identity] = {
                "name": ref["name"],
                "sourceLocation": ref["location"],
                "revision": state["revision"],
                "version": state["version"],
                "branch": None,
                "hash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            }
        lock = {
            "version": 13,
            "workspaceStateVersion": 7,
            "package": "PureApp",
            "rootDirectory": "caller",
            "manifestHash": codec.manifest_hash(self.root, []),
            "pins": pins,
            "localDependencies": [],
            "binaryTargets": [],
            "products": [{"name": "PureApp", "type": "executable"}],
        }
        lock["contentHash"] = codec.content_hash(lock)
        codec.validate_lock(lock, self.pins, self.root)
        self.assertEqual(codec.checkout_records(lock, self.root), self.state)
        (self.root / "Package.swift").write_text("// changed")
        with self.assertRaises(ValueError):
            codec.validate_lock(lock, self.pins, self.root)
        codec.validate_lock(lock, self.pins, self.root, current=False)

    def test_capture_joins_identity_and_refuses_mismatched_state(self):
        import workspace_v7_pins2_capture as capture

        (self.root / ".build/checkouts").mkdir(parents=True)
        (self.root / ".build/workspace-state.json").write_text(json.dumps(self.state))
        (self.root / "Package.resolved").write_text(json.dumps(self.pins))
        for row in self.state["object"]["dependencies"]:
            tree = self.root / ".build/checkouts" / row["subpath"]
            tree.mkdir()
            (tree / "Package.swift").write_text("// structural fixture")
        records, raw = capture.native_graph(self.root, {})
        self.assertEqual(set(records), {"alpha", "beta"})
        self.assertEqual(json.loads(raw), self.pins)
        self.pins["pins"][0]["state"]["revision"] = "f" * 40
        (self.root / "Package.resolved").write_text(json.dumps(self.pins))
        with self.assertRaises(ValueError):
            capture.native_graph(self.root, {})

    def test_pins3_and_forged_authority_refused(self):
        with self.assertRaises(ValueError):
            pins3.pins_index(self.pins)
        for patch in [{"version": 3}, {"originHash": "f" * 64}]:
            value = dict(self.pins, **patch)
            with self.assertRaises(ValueError):
                codec.pins_index(value)
        self.assertIs(family.codec(protocols.WORKSPACE_V7_PINS2), codec)
        self.assertNotEqual(
            protocols.WORKSPACE_V7_PINS2.ownership_marker,
            protocols.WORKSPACE_V7_PINS3.ownership_marker,
        )
        with self.assertRaises(ValueError):
            protocols.checked(replace(protocols.WORKSPACE_V7_PINS2))
        import workspace_v7_pins2_ownership

        self.assertIs(
            family.ownership(protocols.WORKSPACE_V7_PINS2), workspace_v7_pins2_ownership
        )

    def test_actual_owned_tag_projection_and_nonempty_pin_rewrite_hold(self):
        import dependencies_v3_observation as observation

        owned = FIXTURE.parent / "workspace7-pins2-owned"
        initial = json.loads((owned / "initial.state.json").read_text())
        edited = json.loads((owned / "edited.state.json").read_text())
        rendered = codec.edited_state(initial, self.root)
        rows = codec.native_index(rendered, self.root, allow_edited=True)
        actual = codec.native_index(edited, self.root, allow_edited=True)
        self.assertEqual(rows["alpha"], actual["alpha"])
        with self.assertRaises(ValueError):
            codec.native_index(edited, self.root)
        selected = (owned / "initial.pins.json").read_bytes()
        (self.root / "Package.resolved").write_bytes(selected)
        before = observation.observe(self.root, selected)
        (self.root / "Package.resolved").write_bytes(
            (owned / "edited-after-build.pins.json").read_bytes()
        )
        with self.assertRaises(ValueError):
            observation.observe(self.root, selected, before)
        (self.root / "Package.resolved").unlink()
        observation.observe(self.root, selected, before)

    def test_unowned_edited_and_unobserved_local_source_control_refused(self):
        value = copy.deepcopy(self.state)
        value["object"]["dependencies"][0]["packageRef"]["kind"] = "localSourceControl"
        with self.assertRaises(ValueError):
            codec.native_index(value, self.root)
        projected = codec.edited_state(self.state, self.root)
        codec.native_index(projected, self.root, allow_edited=True)
        with self.assertRaises(ValueError):
            codec.native_index(projected, self.root)
        with self.assertRaises(ValueError):
            codec.local_records({"localDependencies": [{}]}, self.root)

    def context(self, mode):
        fixture = FIXTURE.parent / "workspace7-pins2-contexts"
        evidence = json.loads((fixture / "source-evidence.json").read_text())
        root = self.root / mode
        shutil.copytree(fixture / mode, root)
        value = json.loads((root / "workspace-state.json").read_text())
        self.normalize_local(value, evidence["contexts"][mode]["originalRoot"], root)
        return (
            root,
            value,
            json.loads((root / "Package.resolved").read_text()),
            evidence,
        )

    def normalize_local(self, value, original_root, root):
        for row in value["object"]["dependencies"]:
            if row["packageRef"]["kind"] == "fileSystem":
                self.assertEqual(
                    row["packageRef"]["location"], original_root + "/Local"
                )
                self.assertEqual(row["state"]["path"], original_root + "/Local")
                row["packageRef"]["location"] = str(root / "Local")
                row["state"]["path"] = str(root / "Local")

    def context_lock(self, root, value):
        pins, locals_ = {}, []
        for identity, row in codec.native_index(value, root).items():
            ref = row["packageRef"]
            if ref["kind"] == "fileSystem":
                local = copy.deepcopy(row)
                relative = Path(ref["location"]).relative_to(root).as_posix()
                local["packageRef"]["location"] = "@root@/" + relative
                local["state"]["path"] = "@root@/" + relative
                locals_.append(local)
            else:
                state = row["state"]["checkoutState"]
                pins[identity] = {
                    "name": ref["name"],
                    "sourceLocation": ref["location"],
                    "revision": state["revision"],
                    "version": state.get("version"),
                    "branch": state.get("branch"),
                    "hash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                }
        lock = {
            "version": 13,
            "workspaceStateVersion": 7,
            "package": "PureApp",
            "rootDirectory": root.name,
            "pins": pins,
            "localDependencies": locals_,
            "binaryTargets": [],
            "products": [{"name": "PureApp", "type": "executable"}],
        }
        lock["manifestHash"] = codec.manifest_hash(
            root, codec.local_records(lock, root)
        )
        lock["contentHash"] = codec.content_hash(lock)
        return lock

    def test_actual_branch_revision_and_mixed_local_roundtrip_and_dispatch(self):
        import workspace_v7_dispatch as dispatch

        for mode in ["branch", "revision", "local"]:
            with self.subTest(mode=mode):
                root, value, selected, _ = self.context(mode)
                lock = self.context_lock(root, value)
                codec.validate_lock(lock, selected, root)
                self.assertEqual(
                    codec.native_index(codec.checkout_records(lock, root), root),
                    codec.native_index(value, root),
                )
                self.assertIs(
                    dispatch.candidate(value, selected, root=root),
                    protocols.WORKSPACE_V7_PINS2,
                )
                self.assertNotIn("originHash", lock)

    def test_actual_owned_contexts_project_full_based_on_and_preserve_local(self):
        for mode in ["branch", "revision", "local"]:
            with self.subTest(mode=mode):
                root, value, _, evidence = self.context(mode)
                observed = json.loads((root / "owned/edited.state.json").read_text())
                self.normalize_local(
                    observed, evidence["contexts"][mode]["ownedOriginalRoot"], root
                )
                expected = codec.edited_state(value, root)
                self.assertEqual(
                    codec.native_index(expected, root, allow_edited=True),
                    codec.native_index(observed, root, allow_edited=True),
                )
                with self.assertRaises(ValueError):
                    codec.native_index(observed, root)
                for row in observed["object"]["dependencies"]:
                    if row["packageRef"]["kind"] == "remoteSourceControl":
                        row["basedOn"]["packageRef"]["identity"] = "foreign"
                        with self.assertRaises(ValueError):
                            codec.native_index(observed, root, allow_edited=True)
                        break

    def test_actual_context_capture_joins_selected_pin_state(self):
        import workspace_v7_pins2_capture as capture

        for mode in ["branch", "revision", "local"]:
            with self.subTest(mode=mode):
                root, value, selected, _ = self.context(mode)
                (root / ".build/checkouts").mkdir(parents=True)
                for row in value["object"]["dependencies"]:
                    if row["packageRef"]["kind"] == "remoteSourceControl":
                        tree = root / ".build/checkouts" / row["subpath"]
                        tree.mkdir()
                        (tree / "Package.swift").write_text(
                            "// structural checkout control"
                        )
                (root / ".build/workspace-state.json").write_text(json.dumps(value))
                records, raw = capture.native_graph(root, {})
                self.assertEqual(set(records), set(codec.native_index(value, root)))
                self.assertEqual(json.loads(raw), selected)
                selected["pins"][0]["state"]["revision"] = "0" * 40
                (root / "Package.resolved").write_text(json.dumps(selected))
                with self.assertRaises(ValueError):
                    capture.native_graph(root, {})

    def test_context_alias_and_conflicting_checkout_fields_refused(self):
        root, value, selected, _ = self.context("branch")
        duplicate = copy.deepcopy(value)
        duplicate["object"]["dependencies"].append(
            copy.deepcopy(duplicate["object"]["dependencies"][0])
        )
        with self.assertRaises(ValueError):
            codec.native_index(duplicate, root)
        state = value["object"]["dependencies"][0]["state"]["checkoutState"]
        state["version"] = "1.0.0"
        with self.assertRaises(ValueError):
            codec.native_index(value, root)
        selected["pins"].append(copy.deepcopy(selected["pins"][0]))
        with self.assertRaises(ValueError):
            codec.pins_index(selected)

    def test_local_manifest_currency_escape_and_symlink_refused(self):
        root, value, selected, _ = self.context("local")
        lock = self.context_lock(root, value)
        manifest = root / "Local/Package.swift"
        manifest.write_bytes(manifest.read_bytes() + b"\n")
        with self.assertRaises(ValueError):
            codec.validate_lock(lock, selected, root)
        codec.validate_lock(lock, selected, root, current=False)
        bad = copy.deepcopy(lock)
        bad["localDependencies"][0]["packageRef"]["location"] = "@root@/../outside"
        bad["localDependencies"][0]["state"]["path"] = "@root@/../outside"
        with self.assertRaises(ValueError):
            codec.local_records(bad, root)
        (root / "Local").rename(root / "OriginalLocal")
        (root / "Local").symlink_to("OriginalLocal", target_is_directory=True)
        with self.assertRaises(ValueError):
            codec.native_index(value, root)


if __name__ == "__main__":
    unittest.main()
