# SPDX-License-Identifier: MIT
"""Native-v3 schema and projection boundaries, before lifecycle integration."""

import copy
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import dependencies_v3 as protocol


def remote(identity="alpha", name="AlphaPkg", leaf="Alpha"):
    return {
        "basedOn": None,
        "packageRef": {
            "identity": identity,
            "kind": "remote",
            "name": name,
            "path": "https://example.invalid/" + leaf + ".git",
        },
        "state": {
            "name": "checkout",
            "checkoutState": {"branch": None, "revision": "a" * 40, "version": "1.0.0"},
        },
        "subpath": leaf,
    }


def native(*records):
    return {"version": 3, "object": {"dependencies": list(records)}}


class NativeSchema(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.local = self.root / "LocalPkg"
        self.local.mkdir()
        (self.local / "Package.swift").write_text("// actual in-tree manifest\n")
        self.local_record = {
            "basedOn": None,
            "packageRef": {
                "identity": "localpkg",
                "kind": "local",
                "name": "LocalPkg",
                "path": str(self.local),
            },
            "state": {"name": "local"},
            "subpath": "localpkg",
        }

    def test_native_and_projected_geometry_preserve_full_checkout(self):
        original = native(remote(), self.local_record)
        before = copy.deepcopy(original)
        projected = protocol.edited_state(original, self.root)
        self.assertEqual(original, before)
        records = protocol.native_index(projected, self.root, allow_edited=True)
        self.assertEqual(records["alpha"]["basedOn"], remote())
        self.assertEqual(records["alpha"]["basedOn"]["subpath"], "Alpha")
        self.assertEqual(records["alpha"]["subpath"], "AlphaPkg")
        self.assertEqual(records["alpha"]["state"], {"name": "edited", "path": None})
        self.assertEqual(records["localpkg"], self.local_record)
        self.assertEqual(projected["version"], 3)

    def test_native_array_order_does_not_change_identity_records(self):
        records = [remote(), remote("beta", "BetaPkg", "Beta"), self.local_record]
        self.assertEqual(
            protocol.native_index(native(*records), self.root),
            protocol.native_index(native(*reversed(records)), self.root),
        )

    def test_reject_protocol_and_reference_variants(self):
        cases = []
        for version in (True, False, 1, 2, 4, "3", None):
            value = native(remote())
            value["version"] = version
            cases.append(("version-" + repr(version), value))
        for field in ("extra", "location", "isLocal"):
            value = native(remote())
            value["object"]["dependencies"][0]["packageRef"][field] = False
            cases.append(("mixed-reference-" + field, value))
        for kind in (None, "root", "registry", "remoteSourceControl", "fileSystem"):
            value = native(remote())
            value["object"]["dependencies"][0]["packageRef"]["kind"] = kind
            cases.append(("kind-" + repr(kind), value))
        for key in ("artifacts", "prebuilts", "extra"):
            value = native(remote())
            value["object"][key] = []
            cases.append(("envelope-" + key, value))
        value = native(remote())
        value["object"]["dependencies"][0]["state"]["checkoutState"]["branch"] = "main"
        cases.append(("unmeasured-branch", value))
        value = native(remote())
        value["object"]["dependencies"][0]["state"]["checkoutState"]["revision"] = (
            "HEAD"
        )
        cases.append(("nonliteral-revision", value))
        value = native(remote())
        value["object"]["dependencies"][0]["subpath"] = "AlphaPkg"
        cases.append(("edited-name-as-checkout-path", value))
        value = native(remote())
        value["object"]["dependencies"][0]["packageRef"].pop("path")
        cases.append(("missing-path", value))
        for name in (
            "..",
            ".",
            "escape/name",
            "bad\\name",
            "bad\nname",
            "\ud800",
            "x" * 256,
        ):
            value = native(remote())
            value["object"]["dependencies"][0]["packageRef"]["name"] = name
            cases.append(("unsafe-name-" + repr(name), value))
        value = native(remote())
        value["object"]["dependencies"][0]["packageRef"]["path"] = "\ud800"
        cases.append(("invalid-url-Unicode", value))
        for label, value in cases:
            with self.subTest(label=label), self.assertRaises(protocol.Reject):
                protocol.native_index(value, self.root)

    def test_duplicates_join_remote_and_local_namespaces(self):
        cases = [
            native(remote(), remote()),
            native(remote(), remote("beta", "AlphaPkg", "Beta")),
        ]
        duplicate_url = remote("beta", "BetaPkg")
        cases.append(native(remote(), duplicate_url))
        local = copy.deepcopy(self.local_record)
        local["packageRef"]["identity"] = "alpha"
        local["subpath"] = "alpha"
        cases.append(native(remote(), local))
        local = copy.deepcopy(self.local_record)
        local["packageRef"]["name"] = "AlphaPkg"
        cases.append(native(remote(), local))
        for value in cases:
            with self.subTest(value=value), self.assertRaises(protocol.Reject):
                protocol.native_index(value, self.root)

    def test_local_path_and_state_refusal(self):
        cases = []
        for path in (
            str(self.root.parent),
            str(self.local) + "/.",
            str(self.root / "Packages/AlphaPkg"),
        ):
            value = copy.deepcopy(self.local_record)
            value["packageRef"]["path"] = path
            cases.append(value)
        value = copy.deepcopy(self.local_record)
        value["state"] = {"name": "fileSystem"}
        cases.append(value)
        value = copy.deepcopy(self.local_record)
        value["subpath"] = "LocalPkg"
        cases.append(value)
        link = self.root / "Alias"
        link.symlink_to(self.local, target_is_directory=True)
        value = copy.deepcopy(self.local_record)
        value["packageRef"]["path"] = str(link)
        cases.append(value)
        for value in cases:
            with (
                self.subTest(value=value),
                self.assertRaises((protocol.Reject, OSError)),
            ):
                protocol.native_index(native(value), self.root)

    def test_local_manifest_hardlink_refused(self):
        alias = self.root / "manifest-alias"
        os.link(self.local / "Package.swift", alias)
        with self.assertRaises(protocol.Reject):
            protocol.native_index(native(self.local_record), self.root)

    def test_edited_input_requires_explicit_role_and_original_record(self):
        value = protocol.edited_state(native(remote()))
        with self.assertRaises(protocol.Reject):
            protocol.native_index(value)
        changed = copy.deepcopy(value)
        changed["object"]["dependencies"][0]["basedOn"]["subpath"] = "AlphaPkg"
        with self.assertRaises(protocol.Reject):
            protocol.native_index(changed, allow_edited=True)
        changed = copy.deepcopy(value)
        changed["object"]["dependencies"][0]["state"]["path"] = "/tmp/foreign"
        with self.assertRaises(protocol.Reject):
            protocol.native_index(changed, allow_edited=True)


class WireFixture:
    def setUp(self):
        NativeSchema.setUp(self)
        (self.root / "Package.swift").write_text("// root manifest\n")
        self.selected = {
            "version": 1,
            "object": {
                "pins": [
                    {
                        "package": "AlphaPkg",
                        "repositoryURL": "https://example.invalid/Alpha.git",
                        "state": {
                            "branch": None,
                            "revision": "a" * 40,
                            "version": "1.0.0",
                        },
                    }
                ]
            },
        }
        local = copy.deepcopy(self.local_record)
        local["packageRef"]["path"] = "@root@/LocalPkg"
        self.lock = {
            "version": 9,
            "workspaceStateVersion": 3,
            "package": "Root",
            "rootDirectory": "project",
            "manifestHash": protocol.manifest_hash(self.root, [self.local_record]),
            "products": [{"name": "Run", "type": "executable"}],
            "binaryTargets": [],
            "localDependencies": [local],
            "pins": {
                "alpha": {
                    "name": "AlphaPkg",
                    "sourceLocation": "https://example.invalid/Alpha.git",
                    "revision": "a" * 40,
                    "version": "1.0.0",
                    "hash": "sha256-" + base64.b64encode(bytes(32)).decode(),
                }
            },
        }
        self.rehash(self.lock)

    def rehash(self, value):
        value["contentHash"] = protocol.content_hash(value)
        return value


class PortableWire(WireFixture, unittest.TestCase):
    def test_exact_wire_and_derived_native_graph(self):
        raw = protocol.canonical(self.lock)
        selected = protocol.canonical(self.selected)
        self.assertEqual(protocol.validate_raw(raw, selected, self.root), self.lock)
        state = protocol.checkout_records(self.lock, self.root)
        index = protocol.native_index(state, self.root)
        self.assertEqual(index["alpha"], remote())
        self.assertEqual(index["localpkg"], self.local_record)

    def test_wrong_portable_native_pairings_and_extra_fields_refused(self):
        cases = []
        for version in (True, 4, 5, 8, "9"):
            value = copy.deepcopy(self.lock)
            value["version"] = version
            cases.append(value)
        for version in (True, 1, 2, 4, "3"):
            value = copy.deepcopy(self.lock)
            value["workspaceStateVersion"] = version
            cases.append(value)
        value = copy.deepcopy(self.lock)
        value["nativeSDK"] = {}
        cases.append(value)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(protocol.Reject):
                protocol.validate_lock(self.rehash(value), self.selected, self.root)

    def test_old_portable_pairings_refused_at_cli_gates_before_native_calls(self):
        import swiftpm_purifier as api

        with tempfile.TemporaryDirectory() as state:
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": state}):
                for version in (4, 5):
                    value = copy.deepcopy(self.lock)
                    value["version"] = version
                    self.rehash(value)
                    (self.root / "swiftpm-purifier.lock.json").write_text(
                        json.dumps(value)
                    )
                    with (
                        mock.patch.object(api, "run") as native_call,
                        mock.patch.object(api, "nix_build") as build_call,
                    ):
                        with self.assertRaisesRegex(
                            api.PurifierError, "requires portable format 9"
                        ):
                            api.read_lock(self.root)
                        with self.assertRaisesRegex(
                            api.PurifierError, "requires portable format 9"
                        ):
                            api.is_current(self.root, value)
                        native_call.assert_not_called()
                        build_call.assert_not_called()

    def test_selected_pin_join_covers_name_url_revision_version_and_branch(self):
        for role in ("package", "repositoryURL", "revision", "version", "branch"):
            value = copy.deepcopy(self.selected)
            pin = value["object"]["pins"][0]
            if role == "package":
                pin[role] = "OtherPkg"
            elif role == "repositoryURL":
                pin[role] = "https://example.invalid/Other.git"
            else:
                pin["state"][role] = {
                    "revision": "b" * 40,
                    "version": "1.0.1",
                    "branch": "main",
                }[role]
            with self.subTest(role=role), self.assertRaises(protocol.Reject):
                protocol.validate_lock(self.lock, value, self.root)

    def test_root_and_local_manifests_are_currency_inputs(self):
        for path in (self.root / "Package.swift", self.local / "Package.swift"):
            prior = path.read_bytes()
            path.write_bytes(prior + b"// changed dependency declaration\n")
            with self.subTest(path=path), self.assertRaises(protocol.Reject):
                protocol.validate_lock(self.lock, self.selected, self.root)
            path.write_bytes(prior)
        code = self.local / "Local.swift"
        code.write_text("// ordinary local source edit\n")
        self.assertEqual(
            protocol.validate_lock(self.lock, self.selected, self.root), self.lock
        )

    def test_version_specific_manifest_is_bound_and_must_not_be_aliased(self):
        path = self.local / "Package@swift-5.2.swift"
        path.write_text("// a selected versioned manifest\n")
        with self.assertRaises(protocol.Reject):
            protocol.validate_lock(self.lock, self.selected, self.root)
        path.unlink()
        path.symlink_to("Package.swift")
        with self.assertRaises((protocol.Reject, OSError)):
            protocol.validate_lock(self.lock, self.selected, self.root)

    def test_null_unknown_pin_schema_and_duplicate_json_refused(self):
        with self.assertRaises(protocol.Reject):
            protocol.validate_raw(protocol.canonical(self.lock), None, self.root)
        selected = copy.deepcopy(self.selected)
        selected["version"] = True
        with self.assertRaises(protocol.Reject):
            protocol.validate_lock(self.lock, selected, self.root)
        raw = protocol.canonical(self.lock)
        raw = raw.replace(b'"version":9', b'"version":9,"version":9')
        with self.assertRaises(protocol.Reject):
            protocol.validate_raw(raw, protocol.canonical(self.selected), self.root)


if __name__ == "__main__":
    unittest.main()
