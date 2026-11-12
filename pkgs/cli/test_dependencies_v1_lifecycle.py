# SPDX-License-Identifier: MIT
"""Public routing and mutation preconditions; no native compatibility claim."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import dependencies_v1 as protocol
import dependencies_v1_layout as layout
import pre_workspace
import swiftpm_purifier as cli
from test_dependencies_v1 import fixture


class Lifecycle(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "Root"
        self.root.mkdir()
        (self.root / "Package.swift").write_text("// swift-tools-version:4.0\n")
        lock = fixture()
        lock["manifestHash"] = pre_workspace.manifest_hash(self.root)
        lock["sourceLayoutHash"] = layout.source_layout(self.root)["hash"]
        lock["contentHash"] = protocol.content_hash(lock)
        (self.root / "swiftpm-purifier.lock.json").write_text(json.dumps(lock))
        self.environment = patch.dict(
            os.environ, {"XDG_STATE_HOME": str(self.base / "state")}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        for name in (
            "LOCK_OVERRIDE",
            "RAW_LOCK_OVERRIDE",
            "TOOL_LOCK_OVERRIDE",
            "RAW_TOOL_LOCK_OVERRIDE",
            "RAW_SOURCE_REFERENCE",
        ):
            change = patch.object(cli, name, None)
            change.start()
            self.addCleanup(change.stop)

    def test_sdk_format8_lock_and_uninstall_refuse_without_realizing_tools(self):
        managed = self.root / ".build/swiftpm-purifier"
        managed.mkdir(parents=True)
        sentinel = managed / "retain"
        sentinel.write_bytes(b"caller data")
        with (
            patch.object(cli, "sdk_mode", return_value=True),
            patch.object(cli, "tool", side_effect=AssertionError("tool realization")),
        ):
            with self.assertRaises(cli.PurifierError):
                cli.read_lock(self.root)
            with self.assertRaises(cli.PurifierError):
                cli.unlink(self.root)
        self.assertEqual(sentinel.read_bytes(), b"caller data")
        self.assertFalse((self.base / "state").exists())

    def test_unlocked_project_does_not_adopt_format8_registry_policy(self):
        (self.root / "swiftpm-purifier.lock.json").unlink()
        physical = self.base / "physical-state"
        physical.mkdir()
        alias = self.base / "state-alias"
        alias.symlink_to(physical, target_is_directory=True)
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(alias)}):
            self.assertIsNone(cli.read_lock(self.root))
        self.assertEqual(list(physical.iterdir()), [])

    def test_raw_native_dot_alias_refuses_before_project_native_command(self):
        raw = str(self.base) + "/./native.json"
        with (
            patch.object(cli, "sdk_mode", return_value=False),
            patch.object(cli, "TOOL_LOCK_OVERRIDE", str(self.base / "native.json")),
            patch.object(cli, "RAW_TOOL_LOCK_OVERRIDE", raw),
            patch.object(
                cli,
                "native_command",
                side_effect=AssertionError("project native command"),
            ),
        ):
            with self.assertRaises(ValueError):
                cli.relock(self.root)
        self.assertFalse((self.root / ".build").exists())
        self.assertFalse((self.root / "Packages").exists())

    def test_sdk_install_helper_refuses_before_any_paths(self):
        from dependencies_v1_lifecycle import install

        class API:
            @staticmethod
            def sdk_mode():
                return True

        with self.assertRaises(ValueError):
            install(None, None, None, API(), None, None)


if __name__ == "__main__":
    unittest.main()
