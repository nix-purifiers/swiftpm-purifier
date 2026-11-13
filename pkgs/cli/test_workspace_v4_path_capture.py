# SPDX-License-Identifier: MIT
"""Capture rejects mismatched pins and cache paths before executing Git."""

import copy
import unittest
from unittest import mock
import tempfile
from pathlib import Path

import workspace_v4_path as protocol
import dependencies_v1_cache as cache
import workspace_v4_path_capture as capture
from test_dependencies_v3 import WireFixture


class CaptureObservation(WireFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.lock.update(version=10, workspaceStateVersion=4)
        self.rehash(self.lock)
        self.state = protocol.checkout_records(self.lock, self.root)
        (self.root / ".build/checkouts/Alpha").mkdir(parents=True)
        (self.root / ".build/checkouts/Alpha/Package.swift").write_text("// fixture\n")
        self.write_inputs()

    def write_inputs(self):
        (self.root / ".build/workspace-state.json").write_bytes(
            protocol.canonical(self.state)
        )
        (self.root / "Package.resolved").write_bytes(protocol.canonical(self.selected))

    def test_complete_native_graph_and_pins_join(self):
        records, raw = capture.native_graph(self.root, {})
        self.assertEqual(set(records), {"alpha", "localpkg"})
        self.assertEqual(raw, protocol.canonical(self.selected))

    def test_missing_extra_or_different_pins_refused(self):
        original = copy.deepcopy(self.selected)
        variants = []
        absent = copy.deepcopy(original)
        absent["object"]["pins"] = []
        variants.append(absent)
        for field, value in [
            ("package", "OtherPkg"),
            ("revision", "b" * 40),
            ("version", "1.0.2"),
        ]:
            changed = copy.deepcopy(original)
            pin = changed["object"]["pins"][0]
            (pin if field == "package" else pin["state"])[field] = value
            variants.append(changed)
        for selected in variants:
            with self.subTest(selected=selected):
                self.selected = selected
                self.write_inputs()
                with self.assertRaises(protocol.Reject):
                    capture.native_graph(self.root, {})

    def test_mixed_state_and_symlink_checkout_refused(self):
        mixed = self.root / ".build/dependencies-state.json"
        mixed.write_text("{}")
        with self.assertRaises(protocol.Reject):
            capture.native_graph(self.root, {})
        mixed.unlink()
        checkout = self.root / ".build/checkouts/Alpha"
        checkout.rename(checkout.with_name("Elsewhere"))
        checkout.symlink_to("Elsewhere", target_is_directory=True)
        with self.assertRaises(protocol.Reject):
            capture.native_graph(self.root, {})


class UnsupportedCapture(unittest.TestCase):
    def test_explicit_null_refused_before_any_native_command(self):
        with (
            tempfile.TemporaryDirectory() as source,
            tempfile.TemporaryDirectory() as work,
        ):
            api = mock.Mock()
            api.sdk_mode.return_value = False
            with self.assertRaises(protocol.Reject):
                capture.prepare(Path(source), Path(work), api, native=None)
            api.native_command.assert_not_called()
            api.copy_package.assert_not_called()

    def test_sdk_bound_capture_refused_before_source_read(self):
        api = mock.Mock()
        api.sdk_mode.return_value = True
        with self.assertRaises(protocol.Reject):
            capture.prepare(Path("/does-not-exist"), Path("/does-not-exist-work"), api)
        api.native_command.assert_not_called()


class CaptureCurrency(WireFixture, unittest.TestCase):
    """Python delegates exercise capture boundaries; no native/Nix evidence."""

    def setUp(self):
        super().setUp()
        self.lock.update(version=10, workspaceStateVersion=4)
        self.rehash(self.lock)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        (self.root / "Sources").mkdir()
        (self.root / "Sources/input.swift").write_text("root input\n")
        (self.root / "LocalPkg/Sources").mkdir()
        (self.root / "LocalPkg/Sources/input.swift").write_text("local input\n")
        self.selected["object"]["pins"] = []
        (self.root / "Package.resolved").write_bytes(protocol.canonical(self.selected))

    def run_capture(self, effect, phase="dump-package"):
        import sys
        from types import SimpleNamespace
        import swiftpm_purifier as purifier

        script = self.work / "capture-delegate.py"
        script.write_text(
            """import json,os,sys
from pathlib import Path
phase=sys.argv[-1]
if phase=='resolve':
 Path('.build').mkdir()
 dep={'basedOn':None,'packageRef':{'identity':'localpkg','kind':'local','name':'LocalPkg','path':str(Path.cwd()/'LocalPkg')},'state':{'name':'local'},'subpath':'localpkg'}
 Path('.build/workspace-state.json').write_text(json.dumps({'version':4,'object':{'dependencies':[dep],'artifacts':[]}}))
 Path('Package.resolved').write_text(' '+json.dumps({'version':1,'object':{'pins':[]}})+'\\n')
else:
 print(json.dumps({'name':'Root','products':[{'name':'Run','type':{'executable':None}}]}))
if phase=="""
            + repr(phase)
            + ":\n "
            + effect.replace("\n", "\n ")
            + "\n"
        )
        api = SimpleNamespace(
            sdk_mode=lambda: False,
            copy_package=purifier.copy_package,
            native_command=lambda role: [sys.executable, str(script)],
            swiftpm_isolation=lambda work: [],
            mirrors=lambda *args, **kwargs: {},
            product_description=purifier.product_description,
        )
        arena = self.work / ("arena-" + str(len(list(self.work.glob("arena-*")))))
        arena.mkdir()
        return capture.prepare(self.root, arena, api)

    def test_pin_normalization_is_intentional_output_with_unchanged_source(self):
        import legacy_runtime

        before = legacy_runtime.source_fingerprint(self.root)
        original = (self.root / "Package.resolved").read_bytes()
        lock, pins, evidence = self.run_capture("pass")
        self.assertEqual(lock["version"], 10)
        self.assertEqual(protocol.pins_index(cache.decode(pins)), {})
        self.assertNotEqual(pins, original)
        self.assertTrue(evidence.receipt["complete"])
        self.assertEqual(
            evidence.receipt["sourceBefore"], evidence.receipt["sourceAfter"]
        )
        self.assertEqual(
            evidence.receipt["callerBefore"], evidence.receipt["callerAfter"]
        )
        self.assertEqual(legacy_runtime.source_fingerprint(self.root), before)
        self.assertFalse((self.root / "swiftpm-purifier.lock.json").exists())

    def test_resolution_and_dump_source_mutations_refused(self):
        import legacy_runtime

        before = legacy_runtime.source_fingerprint(self.root)
        for phase in ("resolve", "dump-package"):
            for effect in (
                "Path('Sources/input.swift').write_text('ROOT INPUT\\n')",
                "Path('LocalPkg/Sources/input.swift').write_text('LOCAL INPUT\\n')",
                "Path('generated.swift').write_text('new source\\n')",
                "Path('Sources/input.swift').unlink(); os.mkfifo('Sources/input.swift')",
                "with open('Sources/input.swift','wb') as stream: stream.truncate(64*1024*1024+1)",
            ):
                with self.subTest(phase=phase, effect=effect):
                    with self.assertRaises(ValueError):
                        self.run_capture(effect, phase)
                    self.assertEqual(
                        legacy_runtime.source_fingerprint(self.root), before
                    )
                    self.assertFalse(
                        (self.root / "swiftpm-purifier.lock.json").exists()
                    )

    def test_caller_side_effect_during_capture_refused(self):
        target = self.root / "Sources/input.swift"
        with self.assertRaisesRegex(ValueError, "caller source"):
            self.run_capture(
                "Path(" + repr(str(target)) + ").write_text('CALLER INPUT\\n')"
            )
        self.assertEqual(target.read_text(), "CALLER INPUT\n")
        self.assertFalse((self.root / "swiftpm-purifier.lock.json").exists())
