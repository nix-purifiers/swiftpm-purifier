# SPDX-License-Identifier: MIT
"""Model controls against original 5.6.3 tag/branch/revision observations."""

import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import workspace_v5_location as model

FIXTURES = Path(__file__).parent / "fixtures/workspace563"


class Workspace5Location(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "Package.swift").write_text("// root model fixture\n")
        (self.root / "Local").mkdir()
        (self.root / "Local/Package.swift").write_text("// local model fixture\n")

    def fixture(self, form, suffix):
        value = json.loads((FIXTURES / (form + "-" + suffix + ".json")).read_bytes())
        if suffix != "pins":
            # Explicit test-only root relocation; fixture bytes remain original.
            for record in value["object"]["dependencies"]:
                if record["packageRef"]["kind"] == "fileSystem":
                    path = str(self.root / "Local")
                    record["packageRef"]["location"] = path
                    record["state"]["path"] = path
        return value

    def lock(self, form):
        native = self.fixture(form, "state")
        selected = self.fixture(form, "pins")
        pins = model.pins_index(selected)
        records = model.native_index(native, self.root)
        locked, locals_ = {}, []
        for identity, record in records.items():
            ref = record["packageRef"]
            if ref["kind"] == "fileSystem":
                row = copy.deepcopy(record)
                row["packageRef"]["location"] = "@root@/Local"
                row["state"]["path"] = "@root@/Local"
                locals_.append(row)
            else:
                # A digest-shaped model test input, never source-fetch evidence.
                digest = base64.b64encode(
                    hashlib.sha256(b"model input").digest()
                ).decode()
                locked[identity] = dict(
                    name=ref["name"],
                    sourceLocation=ref["location"],
                    hash="sha256-" + digest,
                    **pins[ref["location"]]["state"],
                )
        lock = dict(
            version=11,
            package="Root",
            rootDirectory=self.root.name,
            manifestHash=model.manifest_hash(
                self.root,
                [
                    r
                    for r in records.values()
                    if r["packageRef"]["kind"] == "fileSystem"
                ],
            ),
            products=[],
            workspaceStateVersion=5,
            pins=locked,
            binaryTargets=[],
            localDependencies=locals_,
        )
        lock["contentHash"] = model.content_hash(lock)
        return lock, selected

    def test_actual_optional_fields_and_edited_geometry(self):
        for form in ("tag", "branch", "revision"):
            with self.subTest(form=form):
                state, pins = self.fixture(form, "state"), self.fixture(form, "pins")
                records = model.native_index(state, self.root)
                selected = model.pins_index(pins)
                for record in records.values():
                    ref = record["packageRef"]
                    if ref["kind"] == "remoteSourceControl":
                        self.assertEqual(
                            model.checkout_state(
                                selected[ref["location"]]["state"], selected=True
                            ),
                            record["state"]["checkoutState"],
                        )
                expected = model.native_index(
                    self.fixture(form, "edited"), self.root, allow_edited=True
                )
                actual = model.native_index(
                    model.edited_state(state, self.root), self.root, allow_edited=True
                )
                self.assertEqual(actual["alpha"], expected["alpha"])
                self.assertEqual(actual["alpha"]["subpath"], "alpha")
                self.assertEqual(actual["alpha"]["basedOn"]["subpath"], "Alpha")
                with self.assertRaises(model.Reject):
                    model.native_index(self.fixture(form, "edited"), self.root)

    def test_lock_roundtrip_for_tag_branch_and_revision(self):
        for form in ("tag", "branch", "revision"):
            with self.subTest(form=form):
                lock, pins = self.lock(form)
                self.assertEqual(
                    model.validate_raw(
                        model.canonical(lock), model.canonical(pins), self.root
                    ),
                    lock,
                )
                self.assertEqual(
                    model.native_index(
                        model.checkout_records(lock, self.root), self.root
                    ),
                    model.native_index(self.fixture(form, "state"), self.root),
                )

    def test_path_family_and_wrong_version_are_rejected(self):
        for version in (3, 4, 6, 7, True):
            state = self.fixture("tag", "state")
            state["version"] = version
            with self.assertRaises(model.Reject):
                model.native_index(state, self.root)
        state = self.fixture("tag", "state")
        ref = state["object"]["dependencies"][0]["packageRef"]
        ref["path"] = ref.pop("location")
        with self.assertRaises(model.Reject):
            model.native_index(state, self.root)

    def test_local_both_paths_and_physical_confinement_are_required(self):
        for mutation in ("disagree", "escape", "link"):
            state = self.fixture("tag", "state")
            record = next(
                r
                for r in state["object"]["dependencies"]
                if r["packageRef"]["kind"] == "fileSystem"
            )
            if mutation == "disagree":
                record["state"]["path"] = str(self.root / "Other")
            elif mutation == "escape":
                record["packageRef"]["location"] = record["state"]["path"] = str(
                    self.root.parent
                )
            else:
                (self.root / "Alias").symlink_to(
                    self.root / "Local", target_is_directory=True
                )
                record["packageRef"]["location"] = record["state"]["path"] = str(
                    self.root / "Alias"
                )
            with self.assertRaises(model.Reject):
                model.native_index(state, self.root)

    def test_null_native_fields_conflicts_and_pins2_are_rejected(self):
        for state in (
            {"revision": "a" * 40, "branch": None},
            {"revision": "a" * 40, "version": None},
            {"revision": "a" * 40, "branch": "main", "version": "1.0.0"},
        ):
            with self.assertRaises(model.Reject):
                model.checkout_state(state)
        with self.assertRaises(model.Reject):
            model.pins_index({"version": 2, "pins": []})

    def test_selected_pin_mismatch_and_foreign_portable_are_rejected(self):
        for change in ("branch", "revision", "portable", "local"):
            lock, pins = self.lock("branch")
            if change == "branch":
                lock["pins"]["alpha"]["branch"] = "other"
            elif change == "revision":
                lock["pins"]["alpha"]["revision"] = "a" * 40
            elif change == "portable":
                lock["version"] = 10
            else:
                lock["localDependencies"][0]["state"]["path"] = "@root@/Other"
            lock["contentHash"] = model.content_hash(lock)
            with self.assertRaises(model.Reject):
                model.validate_lock(lock, pins, self.root)


if __name__ == "__main__":
    unittest.main()
