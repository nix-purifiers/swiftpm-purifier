# SPDX-License-Identifier: MIT
"""Source controls for literal native-pair observation, never ownership."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

import native_protocols as protocols
import workspace_v7_dispatch as dispatch
import workspace_v7_pins2 as pins2
import workspace_v7_pins3 as pins3

FIXTURES = Path(__file__).resolve().parents[2] / "tests/fixtures"


class Dispatch(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def fixture(self, version):
        fixture = FIXTURES / ("workspace7-pins" + str(version))
        (self.root / "Package.swift").write_bytes(
            (fixture / "Package.swift").read_bytes()
        )
        return json.loads((fixture / "workspace-state.json").read_text()), json.loads(
            (fixture / "Package.resolved").read_text()
        )

    def test_actual_pairs_choose_distinct_literal_authorities(self):
        for version, descriptor in [
            (3, protocols.WORKSPACE_V7_PINS3),
            (2, protocols.WORKSPACE_V7_PINS2),
        ]:
            state, selected = self.fixture(version)
            self.assertIs(
                dispatch.candidate(state, selected, root=self.root), descriptor
            )
        self.assertEqual({p.name for p in self.root.iterdir()}, {"Package.swift"})

    def test_legacy_location_envelope_remains_with_existing_profile(self):
        state, selected = self.fixture(3)
        state["object"].pop("prebuilts")
        self.assertIsNone(dispatch.candidate(state, selected, root=self.root))

    def test_actual_native633_pins1_is_delegated_without_new_authority(self):
        from unittest import mock

        legacy = Path(__file__).parent / "fixtures/native633-v1"
        state = json.loads((legacy / "workspace-state.json").read_text())
        pins = json.loads((legacy / "Package.resolved").read_text())
        with mock.patch.object(
            dispatch.family, "codec", side_effect=AssertionError("new codec consulted")
        ):
            self.assertIsNone(dispatch.candidate(state, pins, root=self.root))

    def test_unknown_pin_pair_has_no_legacy_fallback(self):
        state, selected = self.fixture(2)
        for value in [
            None,
            {},
            {"version": 99, "pins": []},
            dict(selected, version=True),
        ]:
            with self.assertRaises(ValueError):
                dispatch.candidate(state, value, root=self.root)

    def test_nonempty_unknown_native_surfaces_are_refused(self):
        state, selected = self.fixture(3)
        for key in ["artifacts", "prebuilts"]:
            value = copy.deepcopy(state)
            value["object"][key] = [{}]
            with self.assertRaises(ValueError):
                dispatch.candidate(value, selected, root=self.root)
        state["object"]["unknown"] = []
        with self.assertRaises(ValueError):
            dispatch.candidate(state, selected, root=self.root)

    def test_stale_origin_and_incomplete_pin_closure_refused(self):
        state, selected = self.fixture(3)
        (self.root / "Package.swift").write_text("// changed manifest")
        with self.assertRaises(ValueError):
            dispatch.candidate(state, selected, root=self.root)
        state, selected = self.fixture(2)
        selected["pins"].pop()
        with self.assertRaises(ValueError):
            dispatch.candidate(state, selected, root=self.root)

    def test_cold_edited_refused_but_revision_only_admitted(self):
        for version, codec in [(3, pins3), (2, pins2)]:
            state, selected = self.fixture(version)
            edited = codec.edited_state(state, self.root)
            with self.assertRaises(ValueError):
                dispatch.candidate(edited, selected, root=self.root)
        state, selected = self.fixture(2)
        state["object"]["dependencies"][0]["state"]["checkoutState"].pop("version")
        selected["pins"][0]["state"].pop("version")
        self.assertIs(
            dispatch.candidate(state, selected, root=self.root),
            protocols.WORKSPACE_V7_PINS2,
        )

    def test_other_native_versions_and_empty_graph_do_not_infer_a_pair(self):
        for value in [None, {}, {"version": 5}, {"version": True}]:
            self.assertIsNone(dispatch.candidate(value, None, root=self.root))
        self.assertIsNone(
            dispatch.candidate(
                {"version": 7, "object": {"dependencies": []}}, None, root=self.root
            )
        )


class RegistryPreservation(unittest.TestCase):
    """Actual registry envelopes select the established profile, not 12/13."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def fixture(self, version):
        fixture = FIXTURES / ("workspace7-registry-pins" + str(version))
        (self.root / "Package.swift").write_bytes(
            (fixture / "Package.swift").read_bytes()
        )
        return json.loads((fixture / "workspace-state.json").read_text()), json.loads(
            (fixture / "Package.resolved").read_text()
        )

    def test_actual_both_pairs_delegate_without_new_codec_authority(self):
        from unittest import mock

        for version in [2, 3]:
            state, pins = self.fixture(version)
            before = copy.deepcopy((state, pins))
            with mock.patch.object(
                dispatch.family,
                "codec",
                side_effect=AssertionError("new codec consulted"),
            ):
                self.assertIsNone(dispatch.candidate(state, pins, root=self.root))
            self.assertEqual((state, pins), before)
            self.assertEqual({p.name for p in self.root.iterdir()}, {"Package.swift"})

    def test_registry_malformed_values_cannot_delegate(self):
        def mutate(value, selected, label):
            row = value["object"]["dependencies"][0]
            pin = selected["pins"][0]
            if label == "native extra":
                value["future"] = []
            elif label == "native bool":
                value["version"] = True
            elif label == "native float":
                value["version"] = 7.0
            elif label == "object extra":
                value["object"]["future"] = []
            elif label == "artifacts":
                value["object"]["artifacts"] = [{}]
            elif label == "prebuilts":
                value["object"]["prebuilts"] = [{}]
            elif label == "record extra":
                row["future"] = []
            elif label == "duplicate record":
                value["object"]["dependencies"].append(copy.deepcopy(row))
            elif label == "edited":
                row["basedOn"] = copy.deepcopy(row)
            elif label == "unknown ref":
                row["packageRef"]["future"] = 0
            elif label == "native identity":
                row["packageRef"]["identity"] = "nix.other"
            elif label == "native name":
                row["packageRef"]["name"] = "Other"
            elif label == "native location":
                row["packageRef"]["location"] = "nix.other"
            elif label == "subpath":
                row["subpath"] = "../greeter"
            elif label == "native state":
                row["state"]["name"] = "edited"
            elif label == "native version":
                row["state"]["version"] = "../1.0.0"
            elif label == "state extra":
                row["state"]["revision"] = "a" * 40
            elif label == "pins extra":
                selected["future"] = []
            elif label == "pins bool":
                selected["version"] = True
            elif label == "pins float":
                selected["version"] = float(selected["version"])
            elif label == "missing pin":
                selected["pins"] = []
            elif label == "duplicate pin":
                selected["pins"].append(copy.deepcopy(pin))
            elif label == "pin extra":
                pin["future"] = []
            elif label == "pin identity":
                pin["identity"] = "nix.other"
            elif label == "pin kind":
                pin["kind"] = "unknown"
            elif label == "pin location":
                pin["location"] = "nix.greeter"
            elif label == "pin state extra":
                pin["state"]["revision"] = "a" * 40
            elif label == "pin version":
                pin["state"]["version"] = "2.0.0"
            else:
                raise AssertionError(label)

        labels = [
            "native extra",
            "native bool",
            "native float",
            "object extra",
            "artifacts",
            "prebuilts",
            "record extra",
            "duplicate record",
            "edited",
            "unknown ref",
            "native identity",
            "native name",
            "native location",
            "subpath",
            "native state",
            "native version",
            "state extra",
            "pins extra",
            "pins bool",
            "pins float",
            "missing pin",
            "duplicate pin",
            "pin extra",
            "pin identity",
            "pin kind",
            "pin location",
            "pin state extra",
            "pin version",
        ]
        for version in [2, 3]:
            for label in labels:
                with self.subTest(version=version, label=label):
                    state, pins = self.fixture(version)
                    mutate(state, pins, label)
                    with self.assertRaises(ValueError):
                        dispatch.candidate(state, pins, root=self.root)

    def test_unknown_mixed_native_kind_does_not_escape_to_legacy(self):
        for version in [2, 3]:
            for kind in ["futureKind", "localSourceControl"]:
                state, pins = self.fixture(version)
                row = copy.deepcopy(state["object"]["dependencies"][0])
                row["packageRef"].update(
                    identity="other",
                    name="other",
                    kind=kind,
                    location="https://example.invalid/other",
                )
                state["object"]["dependencies"].append(row)
                with self.assertRaises(ValueError):
                    dispatch.candidate(state, pins, root=self.root)

    def test_known_mixed_remote_graph_is_complete_and_typed(self):
        # Composition control, not a claim of native mixed-graph execution.
        for version in [2, 3]:
            state, pins = self.fixture(version)
            fixture = FIXTURES / ("workspace7-pins" + str(version))
            actual_state = json.loads((fixture / "workspace-state.json").read_text())
            actual_pins = json.loads((fixture / "Package.resolved").read_text())
            state["object"]["dependencies"] += actual_state["object"]["dependencies"]
            pins["pins"] += actual_pins["pins"]
            self.assertIsNone(dispatch.candidate(state, pins, root=self.root))
            pins["pins"][-1]["state"]["revision"] = "a" * 40
            with self.assertRaises(ValueError):
                dispatch.candidate(state, pins, root=self.root)

    def test_known_mixed_local_composition_remains_confined(self):
        leaf = self.root / "Extras"
        leaf.mkdir()
        (leaf / "Package.swift").write_text("// owned fixture manifest\n")
        prior = json.loads(
            (
                FIXTURES / "workspace7-pins2-contexts/local/owned/initial.state.json"
            ).read_text()
        )
        local = next(
            row
            for row in prior["object"]["dependencies"]
            if row["packageRef"]["kind"] == "fileSystem"
        )
        for version in [2, 3]:
            state, pins = self.fixture(version)
            row = copy.deepcopy(local)
            row["packageRef"]["location"] = str(leaf)
            row["state"]["path"] = str(leaf)
            state["object"]["dependencies"].append(row)
            self.assertIsNone(dispatch.candidate(state, pins, root=self.root))
            row["packageRef"]["location"] = str(self.root.parent)
            row["state"]["path"] = str(self.root.parent)
            with self.assertRaises(ValueError):
                dispatch.candidate(state, pins, root=self.root)

    def test_registry_array_bounds_precede_record_iteration(self):
        import workspace_v7_pins3 as codec

        for version in [2, 3]:
            state, pins = self.fixture(version)
            base_row = state["object"]["dependencies"][0]
            base_pin = pins["pins"][0]
            rows, selected = [], []
            for index in range(codec.LIMIT + 1):
                identity = "nix.greeter" + str(index)
                row = copy.deepcopy(base_row)
                row["packageRef"].update(
                    identity=identity, name=identity, location=identity
                )
                row["subpath"] = "nix/greeter" + str(index) + "/1.0.0"
                pin = copy.deepcopy(base_pin)
                pin["identity"] = identity
                rows.append(row)
                selected.append(pin)
            state["object"]["dependencies"] = rows[: codec.LIMIT]
            pins["pins"] = selected[: codec.LIMIT]
            self.assertIsNone(dispatch.candidate(state, pins, root=self.root))
            # Each oversized array must stop before per-record validation,
            # independently of the later complete-closure join.
            state["object"]["dependencies"] = rows
            with self.assertRaisesRegex(ValueError, "legacy registry native bound"):
                dispatch.candidate(state, pins, root=self.root)
            state["object"]["dependencies"] = rows[:1]
            pins["pins"] = selected
            with self.assertRaisesRegex(ValueError, "legacy registry pin bound"):
                dispatch.candidate(state, pins, root=self.root)

    def test_prior_registry_native_versions_and_location_envelope_keep_routes(self):
        for version in [2, 3]:
            state, pins = self.fixture(version)
            for old in [4, 5]:
                prior = copy.deepcopy(state)
                prior["version"] = old
                self.assertIsNone(dispatch.candidate(prior, None, root=self.root))
            state["object"].pop("prebuilts")
            self.assertIsNone(dispatch.candidate(state, None, root=self.root))

    def test_registry_legacy_origin_is_preserved_not_format12_attestation(self):
        state, pins = self.fixture(3)
        pins["originHash"] = "f" * 64
        self.assertIsNone(dispatch.candidate(state, pins, root=self.root))
        self.assertEqual(pins["originHash"], "f" * 64)
        for invalid in [None, True, 1.0, "F" * 64, "0" * 63]:
            pins["originHash"] = invalid
            with self.assertRaises(ValueError):
                dispatch.candidate(state, pins, root=self.root)


if __name__ == "__main__":
    unittest.main()
