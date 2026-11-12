# SPDX-License-Identifier: MIT
"""Closed format8 contract; synthetic schema cases make no runtime claim."""

import copy
import json
import unittest

import dependencies_v1 as protocol


def fixture():
    lock = {
        "version": 8,
        "nativeProtocol": "dependencies-state-v1",
        "nativeState": {"file": ".build/dependencies-state.json", "version": 1},
        "package": "Root",
        "rootDirectory": "Root",
        "manifestHash": "0" * 64,
        "sourceLayoutHash": "1" * 64,
        "nativeToolLock": {"file": "Package.resolved", "sha256": None},
        "pins": {
            "Leaf": {
                "name": "Leaf",
                "sourceLocation": "https://example.invalid/Leaf.git",
                "revision": "a" * 40,
                "version": "1.0.0",
                "hash": "sha256-" + "A" * 43 + "=",
            }
        },
        "products": [
            {
                "name": "One",
                "type": "library",
                "modules": ["One", "Two"],
                "linkage": "automatic",
                "declaration": {"kind": "explicit", "targets": ["One"]},
            }
        ],
        "localDependencies": [],
        "binaryTargets": [],
    }
    lock["contentHash"] = protocol.content_hash(lock)
    return lock


class Contract(unittest.TestCase):
    def test_exact_bytes_and_absence_have_distinct_bindings(self):
        absent = fixture()
        present = copy.deepcopy(absent)
        present["nativeToolLock"]["sha256"] = "2" * 64
        present["contentHash"] = protocol.content_hash(present)
        self.assertNotEqual(absent["contentHash"], present["contentHash"])
        for lock in (absent, present):
            self.assertEqual(protocol.decode(json.dumps(lock).encode()), lock)

    def test_unknown_and_incompatible_contracts_refused_even_with_valid_hash(self):
        changes = {
            "SDK field": lambda x: x.update(nativeSDK={}),
            "old protocol": lambda x: x.update(nativeProtocol="pre-workspace-git"),
            "boolean version": lambda x: x.update(version=True),
            "empty graph": lambda x: x.update(pins={}),
            "mixed pin format": lambda x: x["nativeToolLock"].update(
                file="Package.pins"
            ),
            "boolean native version": lambda x: x["nativeState"].update(version=True),
            "unknown descriptor": lambda x: x["nativeState"].update(other=1),
            "branch without semver": lambda x: x["pins"]["Leaf"].update(version=None),
            "invented revision": lambda x: x["pins"]["Leaf"].update(revision="1.0.0"),
            "foreign pin field": lambda x: x["pins"]["Leaf"].update(branch="main"),
            "duplicate export": lambda x: x["products"][0].update(
                modules=["One", "One"]
            ),
            "no declaration": lambda x: x["products"][0].pop("declaration"),
            "library inferred": lambda x: x["products"][0].update(
                declaration={"kind": "inferred-executable", "target": "One"}
            ),
            "duplicate product": lambda x: x["products"].append(
                copy.deepcopy(x["products"][0])
            ),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                lock = fixture()
                change(lock)
                lock["contentHash"] = protocol.content_hash(lock)
                with self.assertRaises(protocol.StateError):
                    protocol.validate(lock)

    def test_duplicate_json_keys_and_bad_utf8_refused(self):
        for raw in (b'{"version":8,"version":8}', b"\xff"):
            with self.assertRaises(protocol.StateError):
                protocol.decode(raw)

    def test_declared_targets_remain_distinct_from_export_closure(self):
        lock = fixture()
        protocol.validate(lock)
        self.assertEqual(lock["products"][0]["declaration"]["targets"], ["One"])
        self.assertEqual(lock["products"][0]["modules"], ["One", "Two"])


if __name__ == "__main__":
    unittest.main()
