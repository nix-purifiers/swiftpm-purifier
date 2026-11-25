# SPDX-License-Identifier: MIT
"""Private CLI source routing controls; every native/write action is mocked."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import native_protocols as protocols
import swiftpm_purifier as cli
import workspace_v7_pins2_lifecycle
import workspace_v7_pins3_lifecycle

FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures"


class CLIObservation(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for name in [
            "LOCK_OVERRIDE",
            "RAW_LOCK_OVERRIDE",
            "TOOL_LOCK_OVERRIDE",
            "RAW_TOOL_LOCK_OVERRIDE",
            "RAW_SOURCE_REFERENCE",
        ]:
            patch = mock.patch.object(cli, name, None)
            patch.start()
            self.addCleanup(patch.stop)

    def fixture(self, pins):
        fixture = FIXTURE / ("workspace7-pins" + str(pins))
        (self.root / "Package.swift").write_bytes(
            (fixture / "Package.swift").read_bytes()
        )
        (self.root / ".build").mkdir(exist_ok=True)
        (self.root / ".build/workspace-state.json").write_bytes(
            (fixture / "workspace-state.json").read_bytes()
        )
        (self.root / "Package.resolved").write_bytes(
            (fixture / "Package.resolved").read_bytes()
        )
        portable = self.root / "swiftpm-purifier.lock.json"
        portable.write_text('{"version":10}')
        portable.chmod(0o640)

    def test_fresh_actual_pairs_route_to_exact_format_without_mutation(self):
        for pins, version in [(3, 12), (2, 13)]:
            self.fixture(pins)
            paths = [
                self.root / "Package.resolved",
                self.root / "swiftpm-purifier.lock.json",
                self.root / ".build/workspace-state.json",
            ]
            before = [
                (p.read_bytes(), p.stat().st_ino, p.stat().st_mode) for p in paths
            ]
            with (
                mock.patch.object(cli, "sdk_mode", return_value=False),
                mock.patch.object(cli, "recover_dependencies_v1"),
                mock.patch.object(
                    cli, "relock_dependencies_v3", return_value="selected"
                ) as capture,
            ):
                self.assertEqual(cli.relock(self.root), "selected")
                capture.assert_called_once_with(self.root, None, version)
            self.assertEqual(
                before,
                [(p.read_bytes(), p.stat().st_ino, p.stat().st_mode) for p in paths],
            )

    def test_fresh_cross_pair_selection_requires_absent_packages(self):
        for pins, old_version, selected_version in [(2, 12, 13), (3, 13, 12)]:
            self.fixture(pins)
            observed = cli.fresh_workspace7(self.root, {"version": old_version})
            self.assertEqual(observed.portable_format, selected_version)
            packages = self.root / "Packages"
            packages.mkdir()
            self.assertIsNone(cli.fresh_workspace7(self.root, {"version": old_version}))
            packages.rmdir()

    def test_readonly_external_pair_is_observed_without_touching_logical_pins(self):
        with tempfile.TemporaryDirectory() as temporary:
            external = Path(temporary).resolve()
            for pins, version in [(3, 12), (2, 13)]:
                self.fixture(pins)
                selected, portable = (
                    external / f"pins-{pins}.json",
                    external / f"lock-{pins}.json",
                )
                selected.write_bytes((self.root / "Package.resolved").read_bytes())
                portable.write_text('{"version":10}')
                selected.chmod(0o444)
                (self.root / "Package.resolved").write_bytes(
                    b"unselected developer bytes\n"
                )
                paths = [selected, portable, self.root / "Package.resolved"]
                before = [
                    (p.read_bytes(), p.stat().st_ino, p.stat().st_mode) for p in paths
                ]
                with (
                    mock.patch.object(cli, "TOOL_LOCK_OVERRIDE", str(selected)),
                    mock.patch.object(cli, "RAW_TOOL_LOCK_OVERRIDE", str(selected)),
                    mock.patch.object(cli, "LOCK_OVERRIDE", str(portable)),
                    mock.patch.object(cli, "RAW_LOCK_OVERRIDE", str(portable)),
                    mock.patch.object(cli, "sdk_mode", return_value=False),
                    mock.patch.object(cli, "recover_dependencies_v1"),
                    mock.patch.object(
                        cli, "relock_dependencies_v3", return_value="selected"
                    ) as capture,
                ):
                    self.assertEqual(cli.relock(self.root), "selected")
                    capture.assert_called_once_with(self.root, None, version)
                self.assertEqual(
                    before,
                    [
                        (p.read_bytes(), p.stat().st_ino, p.stat().st_mode)
                        for p in paths
                    ],
                )

    def test_read_lock_validates_both_local_typed_formats(self):
        import test_workspace_v7_pins3 as format12
        import test_workspace_v7_pins2 as format13

        for pins, helper in [
            (3, format12.Workspace7Pins3),
            (2, format13.Workspace7Pins2),
        ]:
            self.fixture(pins)
            self.state = json.loads(
                (self.root / ".build/workspace-state.json").read_text()
            )
            self.pins = json.loads((self.root / "Package.resolved").read_text())
            lock = helper.lock(self)
            (self.root / "swiftpm-purifier.lock.json").write_text(json.dumps(lock))
            with (
                mock.patch.object(cli, "sdk_mode", return_value=False),
                mock.patch.object(cli, "recover_dependencies_v1"),
            ):
                self.assertEqual(cli.read_lock(self.root), lock)

    def test_existing_formats4_and5_relock_keep_legacy_runtime_path(self):
        class LegacyPathReached(BaseException):
            pass

        self.fixture(2)
        registry = self.root / "registry"
        registry.mkdir()
        for version in [4, 5]:
            (self.root / "swiftpm-purifier.lock.json").write_text(
                json.dumps({"version": version})
            )
            with (
                mock.patch.object(cli, "sdk_mode", return_value=False),
                mock.patch.object(cli, "recover_dependencies_v1"),
                mock.patch.object(
                    cli,
                    "fresh_workspace7",
                    side_effect=AssertionError("new fresh observer consulted"),
                ),
                mock.patch.object(
                    cli,
                    "workspace7_candidate",
                    side_effect=AssertionError("new pair observer consulted"),
                ),
                mock.patch.object(
                    cli.dependencies_v1_lifecycle, "registry", return_value=registry
                ),
                mock.patch.object(
                    cli.dependencies_v1_capture, "candidate", return_value=False
                ),
                mock.patch.object(cli.pre_workspace, "candidate", return_value=False),
                mock.patch.object(
                    cli, "observe_host_runtime", side_effect=LegacyPathReached
                ),
            ):
                with self.assertRaises(LegacyPathReached):
                    cli.relock(self.root)

    def test_existing_formats4_and5_are_not_fresh_adoption_authority(self):
        self.fixture(2)
        for version in [4, 5]:
            self.assertIsNone(cli.fresh_workspace7(self.root, {"version": version}))

    def test_all_foreign_companions_block_fresh_adoption(self):
        self.fixture(3)
        for relative in [
            "Packages",
            "Package.pins",
            ".build/dependencies-state.json",
            ".build/swiftpm-purifier/links.json",
            ".build/swiftpm-purifier/preworkspace-owned.json",
        ]:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(self.root / "missing")
            before = path.lstat()
            self.assertIsNone(cli.fresh_workspace7(self.root, {"version": 10}))
            self.assertEqual(path.lstat(), before)
            path.unlink()

    def test_old_native_state_does_not_consult_new_selected_pins(self):
        self.fixture(2)
        (self.root / ".build/workspace-state.json").write_text(
            '{"version":4,"object":{}}'
        )
        with mock.patch.object(
            cli, "read_json", side_effect=AssertionError("new selected pins consulted")
        ):
            self.assertIsNone(cli.fresh_workspace7(self.root, {"version": 10}))

    def test_stale_origin_and_unknown_pair_refuse_before_capture(self):
        self.fixture(3)
        (self.root / "Package.swift").write_text("// changed origin")
        with (
            mock.patch.object(cli, "sdk_mode", return_value=False),
            mock.patch.object(cli, "recover_dependencies_v1"),
            mock.patch.object(cli, "relock_dependencies_v3") as capture,
        ):
            with self.assertRaises(cli.PurifierError):
                cli.relock(self.root)
            capture.assert_not_called()
        self.fixture(2)
        (self.root / "Package.resolved").write_text('{"version":99,"pins":[]}')
        with self.assertRaises(cli.PurifierError):
            cli.fresh_workspace7(self.root, {"version": 10})

    def test_literal_lifecycle_and_sdk_bound_contract_stay_distinct(self):
        self.assertIs(cli.state_lifecycle(12), workspace_v7_pins3_lifecycle)
        self.assertIs(cli.state_lifecycle(13), workspace_v7_pins2_lifecycle)
        self.assertNotEqual(
            protocols.WORKSPACE_V7_PINS3.ownership_marker,
            protocols.WORKSPACE_V7_PINS2.ownership_marker,
        )
        with mock.patch.object(cli, "sdk_mode", return_value=True):
            with self.assertRaises(cli.PurifierError):
                cli.validate_dependencies_v3(self.root, b"{}", 12)
            with self.assertRaises(cli.PurifierError):
                cli.validate_dependencies_v3(self.root, b"{}", 13)

    def test_isolation_preserves_new_native_features_and_legacy_default(self):
        options = frozenset(
            {
                "--disable-dependency-cache",
                "--manifest-cache",
                "--cache-path",
                "--config-path",
                "--security-path",
                "--disable-experimental-prebuilts",
            }
        )
        with (
            mock.patch.object(cli, "native_options", return_value=options),
            mock.patch.object(cli, "native_command", return_value=["not-executed"]),
            mock.patch.object(Path, "home", return_value=self.root / "unused-home"),
        ):
            for name, sdk, disable in [
                ("legacy", False, True),
                ("native7", False, False),
                ("sdk", True, True),
            ]:
                work = self.root / name
                work.mkdir()
                with mock.patch.object(cli, "sdk_mode", return_value=sdk):
                    flags = (
                        cli.swiftpm_isolation(work, disable_prebuilts=False)
                        if name == "native7"
                        else cli.swiftpm_isolation(work)
                    )
                self.assertEqual("--disable-experimental-prebuilts" in flags, disable)
                self.assertIn("--cache-path", flags)

    def test_typed_native7_capture_requests_feature_neutral_isolation(self):
        import dependencies_v3_capture as capture

        for version, descriptor in [
            (3, protocols.WORKSPACE_V7_PINS3),
            (2, protocols.WORKSPACE_V7_PINS2),
        ]:
            with self.subTest(pins=version), tempfile.TemporaryDirectory() as temporary:
                self.fixture(version)
                arena = Path(temporary).resolve()
                isolation = mock.Mock(side_effect=RuntimeError("isolation-stop"))
                with (
                    mock.patch.object(cli, "sdk_mode", return_value=False),
                    mock.patch.object(
                        cli, "native_command", return_value=["not-executed"]
                    ),
                    mock.patch.object(cli, "swiftpm_isolation", isolation),
                ):
                    with self.assertRaisesRegex(RuntimeError, "isolation-stop"):
                        capture.prepare(
                            self.root, arena, cli, native_protocol=descriptor
                        )
                isolation.assert_called_once_with(arena, disable_prebuilts=False)

    def test_fresh_discovery_is_neutral_and_existing4_and5_keep_legacy_control(self):
        (self.root / "Package.swift").write_text("// swift-tools-version:5.9\n")
        for existing in [None, 4, 5]:
            with (
                self.subTest(existing=existing),
                tempfile.TemporaryDirectory() as temporary,
            ):
                if existing is not None:
                    (self.root / "swiftpm-purifier.lock.json").write_text(
                        json.dumps({"version": existing})
                    )
                isolation = mock.Mock(side_effect=RuntimeError("isolation-stop"))
                with (
                    mock.patch.object(cli, "sdk_mode", return_value=False),
                    mock.patch.object(cli, "recover_dependencies_v1"),
                    mock.patch.object(
                        cli.dependencies_v1_lifecycle,
                        "registry",
                        return_value=Path(temporary).resolve(),
                    ),
                    mock.patch.object(
                        cli.dependencies_v1_capture, "candidate", return_value=False
                    ),
                    mock.patch.object(
                        cli.pre_workspace, "candidate", return_value=False
                    ),
                    mock.patch.object(cli, "observe_host_runtime"),
                    mock.patch.object(cli, "swiftpm_isolation", isolation),
                ):
                    with self.assertRaisesRegex(RuntimeError, "isolation-stop"):
                        cli.relock(self.root)
                isolation.assert_called_once()
                self.assertEqual(
                    isolation.call_args.kwargs,
                    {"disable_prebuilts": existing is not None},
                )


if __name__ == "__main__":
    unittest.main()
