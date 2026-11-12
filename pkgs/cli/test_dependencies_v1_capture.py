# SPDX-License-Identifier: MIT
"""Capture preconditions and exact native semantic joins, without native execution."""

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import dependencies_v1_capture as capture
import dependencies_v1_cache as cache


class Capture(unittest.TestCase):
    def setUp(self):
        self.state = {
            "object": {
                "dependencies": [
                    {
                        "name": "Leaf",
                        "repositoryURL": "https://example.invalid/Leaf.git",
                        "state": {
                            "checkoutState": {
                                "branch": None,
                                "revision": "a" * 40,
                                "version": "1.0.0",
                            }
                        },
                    }
                ]
            }
        }
        self.native = {
            "version": 1,
            "object": {
                "pins": [
                    {
                        "package": "Leaf",
                        "repositoryURL": "https://example.invalid/Leaf.git",
                        "state": {
                            "branch": None,
                            "revision": "a" * 40,
                            "version": "1.0.0",
                        },
                    }
                ]
            },
        }

    def test_absence_and_exact_native_identity(self):
        capture.native_pins(None, self.state)
        capture.native_pins(json.dumps(self.native).encode(), self.state)

    def test_disagreeing_or_unknown_native_records_refuse(self):
        for name, change in [
            (
                "revision",
                lambda x: x["object"]["pins"][0]["state"].update(revision="b" * 40),
            ),
            ("package", lambda x: x["object"]["pins"][0].update(package="Other")),
            (
                "URL",
                lambda x: x["object"]["pins"][0].update(
                    repositoryURL="https://example.invalid/Other.git"
                ),
            ),
            ("unknown", lambda x: x["object"]["pins"][0].update(guessed=True)),
            (
                "duplicate",
                lambda x: x["object"]["pins"].append(
                    copy.deepcopy(x["object"]["pins"][0])
                ),
            ),
            ("missing", lambda x: x["object"].update(pins=[])),
            ("boolean-version", lambda x: x.update(version=True)),
        ]:
            with self.subTest(name=name):
                value = copy.deepcopy(self.native)
                change(value)
                with self.assertRaises(cache.Reject):
                    capture.native_pins(json.dumps(value).encode(), self.state)

    def test_valid_selected_pins_can_advance_a_stale_cache_before_resolution(self):
        value = copy.deepcopy(self.native)
        value["object"]["pins"][0]["state"].update(revision="b" * 40, version="1.1.0")
        raw = json.dumps(value).encode()
        capture.native_pins(raw, self.state, allow_stale=True)
        with self.assertRaises(cache.Reject):
            capture.native_pins(raw, self.state)
        value["object"]["pins"][0]["state"]["branch"] = "main"
        with self.assertRaises(cache.Reject):
            capture.native_pins(
                json.dumps(value).encode(), self.state, allow_stale=True
            )
        value["object"]["pins"][0]["state"]["branch"] = None
        value["object"]["pins"][0]["repositoryURL"] = (
            "https://example.invalid/Other.git"
        )
        with self.assertRaises(cache.Reject):
            capture.native_pins(
                json.dumps(value).encode(), self.state, allow_stale=True
            )

    def test_sdk_bound_capture_refuses_before_source_or_tool_access(self):
        class API:
            @staticmethod
            def sdk_mode():
                return True

        with self.assertRaises(cache.Reject):
            capture.prepare(None, None, API())

    def test_evidence_ignores_in_project_global_tmpdir(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "caller"
            root.mkdir()
            evidence = base / "evidence"
            evidence.mkdir()
            with patch.dict(os.environ, {"TMPDIR": str(root)}):
                session = capture.NativeSession(root, {}, evidence)
            self.assertEqual(list(root.iterdir()), [])
            self.assertEqual(session.evidence.parent, evidence)
            self.assertFalse(session.receipt["complete"])


if __name__ == "__main__":
    unittest.main()
