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


if __name__ == "__main__":
    unittest.main()
