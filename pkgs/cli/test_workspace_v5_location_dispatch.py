# SPDX-License-Identifier: MIT
"""Discovery selects a codec; exact ownership still controls every write."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import native_path_protocols
import native_protocols
import swiftpm_purifier as cli
import workspace_v5_location as model
from test_workspace_v5_location_fixture import WireFixture, path_lock


class Discovery(WireFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.old = path_lock(self.lock, self.root, version=10)
        (self.root / ".build").mkdir()
        self.state = self.root / ".build/workspace-state.json"
        self.state.write_bytes(
            model.canonical(model.checkout_records(self.lock, self.root))
        )
        self.state.chmod(0o640)
        (self.root / "Package.resolved").write_bytes(model.canonical(self.selected))
        (self.root / "swiftpm-purifier.lock.json").write_bytes(
            model.canonical(self.old)
        )
        bindings = dict(
            LOCK_OVERRIDE=None,
            RAW_LOCK_OVERRIDE=None,
            TOOL_LOCK_OVERRIDE=None,
            RAW_TOOL_LOCK_OVERRIDE=None,
            RAW_SOURCE_REFERENCE=None,
        )
        for name, value in bindings.items():
            patch = mock.patch.object(cli, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def test_complete_native5_pins1_discovery_preserves_old_pair_and_state(self):
        paths = [
            self.root / "Package.resolved",
            self.root / "swiftpm-purifier.lock.json",
            self.state,
        ]
        before = [(p.read_bytes(), p.stat().st_ino, p.stat().st_mode) for p in paths]
        self.assertTrue(cli.fresh_location_workspace(self.root, self.old))
        self.assertEqual(
            before, [(p.read_bytes(), p.stat().st_ino, p.stat().st_mode) for p in paths]
        )

    def test_retained_old9_fresh_selection_keeps_the_old_pair_until_capture(self):
        old9 = path_lock(self.lock, self.root, version=9)
        portable = self.root / "swiftpm-purifier.lock.json"
        portable.write_bytes(model.canonical(old9))
        before = portable.read_bytes(), portable.stat().st_ino, portable.stat().st_mode
        self.assertTrue(cli.fresh_location_workspace(self.root, old9))
        with (
            mock.patch.object(cli, "recover_dependencies_v1"),
            mock.patch.object(
                cli, "relock_dependencies_v3", return_value="selected11"
            ) as capture,
        ):
            self.assertEqual(cli.relock(self.root), "selected11")
            capture.assert_called_once_with(self.root, None, 11)
        self.assertEqual(
            before,
            (portable.read_bytes(), portable.stat().st_ino, portable.stat().st_mode),
        )

    def test_old9_mixed_basename_or_owned_artifacts_still_block_discovery(self):
        old9 = path_lock(self.lock, self.root, version=9)
        for relative in (".build/dependencies-state.json", "Packages"):
            with self.subTest(relative=relative):
                path = self.root / relative
                path.symlink_to(self.root / "missing-foreign-target")
                self.assertFalse(cli.fresh_location_workspace(self.root, old9))
                self.assertTrue(path.is_symlink())
                path.unlink()
        for version in (4, 6, 7, 8, 11, True):
            self.assertFalse(
                cli.fresh_location_workspace(self.root, {"version": version})
            )

    def test_every_foreign_companion_including_dangling_links_blocks_adoption(self):
        for relative in (
            "Packages",
            ".build/dependencies-state.json",
            "Package.pins",
            ".build/swiftpm-purifier/links.json",
            ".build/swiftpm-purifier/preworkspace-owned.json",
        ):
            with self.subTest(relative=relative):
                path = self.root / relative
                path.parent.mkdir(exist_ok=True, parents=True)
                path.symlink_to(self.root / "missing-foreign-target")
                before = path.lstat()
                self.assertFalse(cli.fresh_location_workspace(self.root, self.old))
                self.assertEqual(path.lstat(), before)
                self.assertTrue(path.is_symlink())
                path.unlink()

    def test_empty_or_wrong_pin_graph_is_not_a_discovery_hint(self):
        empty = {"version": 5, "object": {"artifacts": [], "dependencies": []}}
        self.assertFalse(
            cli.workspace_location_candidate(
                empty, root=self.root, selected_pins=self.selected
            )
        )
        pins = json.loads(model.canonical(self.selected))
        pins["object"]["pins"][0]["state"]["revision"] = "f" * 40
        with self.assertRaises(cli.PurifierError):
            cli.workspace_location_candidate(
                model.checkout_records(self.lock, self.root),
                root=self.root,
                selected_pins=pins,
            )

    def test_modern_pins2_stays_outside_the_new_family(self):
        self.assertFalse(
            cli.workspace_location_candidate(
                model.checkout_records(self.lock, self.root),
                root=self.root,
                selected_pins={"version": 2, "pins": []},
            )
        )

    def test_old_native4_path_does_not_open_new_selectors(self):
        self.state.write_bytes(b'{"version":4,"object":{}}')
        with mock.patch.object(
            cli.dependencies_v1_selectors,
            "pair",
            side_effect=AssertionError("new selectors must not be consulted"),
        ):
            self.assertFalse(cli.fresh_location_workspace(self.root, self.old))

    def test_selected_readonly_external_pins_override_unselected_logical_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            external = Path(temporary)
            pins, lock = external / "pins.json", external / "lock.json"
            pins.write_bytes(model.canonical(self.selected))
            lock.write_bytes(model.canonical(self.old))
            pins.chmod(0o444)
            (self.root / "Package.resolved").write_bytes(
                b"unselected developer bytes\n"
            )
            with (
                mock.patch.object(cli, "TOOL_LOCK_OVERRIDE", str(pins)),
                mock.patch.object(cli, "RAW_TOOL_LOCK_OVERRIDE", str(pins)),
                mock.patch.object(cli, "LOCK_OVERRIDE", str(lock)),
                mock.patch.object(cli, "RAW_LOCK_OVERRIDE", str(lock)),
            ):
                self.assertTrue(cli.fresh_location_workspace(self.root, self.old))
            self.assertEqual(pins.stat().st_mode & 0o777, 0o444)
            self.assertEqual(
                (self.root / "Package.resolved").read_bytes(),
                b"unselected developer bytes\n",
            )

    def test_recorded_recovery_precedes_fresh_selection_without_early_publication(self):
        events = []
        old_bytes = (self.root / "swiftpm-purifier.lock.json").read_bytes()

        def selected(root, update, version):
            events.append("capture")
            self.assertEqual(version, 11)
            self.assertEqual(
                (root / "swiftpm-purifier.lock.json").read_bytes(), old_bytes
            )
            return "selected11"

        with (
            mock.patch.object(
                cli,
                "recover_dependencies_v1",
                side_effect=lambda root: events.append("recover"),
            ),
            mock.patch.object(cli, "relock_dependencies_v3", side_effect=selected),
        ):
            self.assertEqual(cli.relock(self.root), "selected11")
        self.assertEqual(events, ["recover", "capture"])

    def test_literal_location_identity_never_enters_the_path_registry(self):
        descriptor = native_protocols.WORKSPACE_V5_LOCATION
        with self.assertRaises(model.Reject):
            native_path_protocols.checked(descriptor)
        clone = native_protocols.NativeLocationProtocol(**descriptor.__dict__)
        with self.assertRaises(model.Reject):
            native_protocols.checked(clone)


if __name__ == "__main__":
    unittest.main()
