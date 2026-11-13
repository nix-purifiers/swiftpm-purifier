# SPDX-License-Identifier: MIT
"""Bounded Python delegates test admission boundaries, not native compatibility."""

import copy
import json
import os
from pathlib import Path
import sys
import signal
import time
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import dependencies_v3 as protocol
import dependencies_v3_lifecycle as lifecycle
import dependencies_v3_ownership as ownership
import legacy_runtime
import swiftpm_purifier as purifier
from test_dependencies_v3 import WireFixture
import test_dependencies_v3_install_transaction as install_fixture


class RuntimeCurrency(WireFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        (self.root / "Sources").mkdir()
        (self.root / "Sources/input.swift").write_text("root input\n")
        (self.root / "LocalPkg/Sources").mkdir()
        (self.root / "LocalPkg/Sources/input.swift").write_text("local input\n")
        self.lock["pins"] = {}
        self.selected["object"]["pins"] = []
        self.rehash(self.lock)
        (self.root / "Package.resolved").write_bytes(protocol.canonical(self.selected))

    def run_probe(self, effect):
        script = self.work / "delegate.py"
        script.write_text(
            """import json,os,sys
from pathlib import Path
if sys.argv[1]=='describe':
 print(json.dumps({'name':'Root','products':[{'name':'Run','type':{'executable':None}}]}))
else:
 """
            + effect.replace("\n", "\n ")
            + "\n"
        )
        api = SimpleNamespace(
            sdk_mode=lambda: False,
            copy_package=purifier.copy_package,
            native_command=lambda role: [
                sys.executable,
                str(script),
                "describe" if role == "swift-package" else "build",
            ],
            product_description=purifier.product_description,
        )
        arena = self.work / ("arena-" + str(len(list(self.work.glob("arena-*")))))
        arena.mkdir()
        return lifecycle.runtime(
            self.root,
            self.lock,
            protocol.canonical(self.selected),
            {},
            arena,
            api,
            lambda path: self.fail("empty remote graph must not hash dependency trees"),
            self.work,
        )

    def test_unchanged_source_and_measured_logical_pin_deletion_complete(self):
        before = legacy_runtime.source_fingerprint(self.root)
        for effect in ("pass", "Path('Package.resolved').unlink()"):
            with self.subTest(effect=effect):
                evidence = self.run_probe(effect)
                self.assertTrue(evidence.receipt["complete"])
                self.assertEqual(
                    evidence.receipt["sourceBefore"], evidence.receipt["sourceAfter"]
                )
                self.assertEqual(
                    evidence.receipt["callerBefore"], evidence.receipt["callerAfter"]
                )
                self.assertEqual(legacy_runtime.source_fingerprint(self.root), before)

    def test_root_local_generated_and_same_size_mutations_refused(self):
        before = legacy_runtime.source_fingerprint(self.root)
        for effect in (
            "Path('Sources/input.swift').write_text('root other\\n')",
            "Path('LocalPkg/Sources/input.swift').write_text('local other\\n')",
            "Path('generated.swift').write_text('generated input\\n')",
            "Path('Sources/input.swift').write_text('ROOT INPUT\\n')",
        ):
            with self.subTest(effect=effect):
                with self.assertRaises(ValueError):
                    self.run_probe(effect)
                self.assertEqual(legacy_runtime.source_fingerprint(self.root), before)

    def test_fifo_oversize_and_foreign_logical_pins_refused_without_reads(self):
        before = legacy_runtime.source_fingerprint(self.root)
        for effect in (
            "Path('Sources/input.swift').unlink(); os.mkfifo('Sources/input.swift')",
            "with open('Sources/input.swift','wb') as stream: stream.truncate(64*1024*1024+1)",
            "Path('Package.resolved').write_text('foreign pins')",
            "Path('Package.resolved').unlink(); Path('Package.resolved').symlink_to('Package.swift')",
            "Path('Package.resolved').unlink(); os.mkfifo('Package.resolved')",
        ):
            with self.subTest(effect=effect):
                with self.assertRaises(ValueError):
                    self.run_probe(effect)
                self.assertEqual(legacy_runtime.source_fingerprint(self.root), before)

    def test_caller_side_effect_during_observation_refused(self):
        original = (self.root / "Sources/input.swift").read_bytes()
        with self.assertRaisesRegex(ValueError, "caller source"):
            self.run_probe(
                "Path("
                + repr(str(self.root / "Sources/input.swift"))
                + ").write_text('CALLER INPUT\\n')"
            )
        self.assertNotEqual((self.root / "Sources/input.swift").read_bytes(), original)
        self.assertFalse((self.root / "Packages").exists())
        self.assertFalse((self.root / ".build").exists())


class SelectedRuntimeAdmission(unittest.TestCase):
    def setUp(self):
        self.fixture = install_fixture.TypedInstallation("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.marker = self.fixture.install()
        self.f = self.fixture
        self.api = SimpleNamespace(sdk_mode=lambda: False)
        self.raw = (self.f.root / "swiftpm-purifier.lock.json").read_bytes()
        self.hash_tree = lambda path: self.f.lock["pins"]["alpha"]["hash"]

    def test_same_lock_installed_graph_still_requires_selected_runtime(self):
        before = legacy_runtime.source_fingerprint(self.f.root)
        with mock.patch.object(
            lifecycle,
            "runtime",
            side_effect=protocol.Reject("different selected runtime refuses protocol"),
        ) as admission:
            with self.assertRaises(protocol.Reject):
                lifecycle.install(
                    self.f.root, self.raw, {}, self.api, self.hash_tree, self.f.registry
                )
            admission.assert_called_once()
        self.assertEqual(legacy_runtime.source_fingerprint(self.f.root), before)
        self.assertEqual(
            lifecycle.ownership.existing(self.f.root, self.hash_tree), self.marker
        )

    def test_foreground_cannot_bypass_changed_selected_runtime(self):
        with (
            mock.patch.object(
                lifecycle,
                "runtime",
                side_effect=protocol.Reject(
                    "different selected runtime migrates protocol"
                ),
            ) as admission,
            mock.patch.object(lifecycle, "foreground") as foreground,
        ):
            with self.assertRaises(protocol.Reject):
                lifecycle.run_native(
                    self.f.root,
                    self.raw,
                    ["must-not-run"],
                    self.hash_tree,
                    self.f.registry,
                    api=self.api,
                )
            admission.assert_called_once()
            foreground.assert_not_called()
        self.assertFalse((self.f.root / ".swiftpm-purifier-transaction.json").exists())

    def delegate_api(self, effect):
        script = self.f.work / "selected-native-delegate.py"
        script.write_text(
            "import json,sys\nfrom pathlib import Path\nif sys.argv[1]=='describe':\n print(json.dumps({'name':'Root','products':[{'name':'Run','type':{'executable':None}}]}))\nelse:\n "
            + effect.replace("\n", "\n ")
            + "\n"
        )
        return SimpleNamespace(
            sdk_mode=lambda: False,
            copy_package=purifier.copy_package,
            native_command=lambda role: [
                sys.executable,
                str(script),
                "describe" if role == "swift-package" else "build",
            ],
            product_description=purifier.product_description,
        )

    def test_unchanged_delegate_is_admitted_before_idempotent_return(self):
        api = self.delegate_api("pass")
        before = legacy_runtime.source_fingerprint(self.f.root)
        sources = {"alpha": self.f.root / "Packages/AlphaPkg"}
        result = lifecycle.install(
            self.f.root, self.raw, sources, api, self.hash_tree, self.f.registry
        )
        self.assertEqual(result, self.marker)
        receipts = list(
            self.f.registry.glob("swiftpm-purifier-dependencies-v1-*/receipt.json")
        )
        self.assertTrue(receipts)
        self.assertEqual(
            json.loads(receipts[-1].read_bytes())["installation"], "already-owned"
        )
        self.assertEqual(legacy_runtime.source_fingerprint(self.f.root), before)

    def test_cli_changed_delegate_refusal_or_migration_prevents_foreground(self):
        before = legacy_runtime.source_fingerprint(self.f.root)
        for effect in (
            "sys.exit(1)",
            "p=Path('.build/dependencies-state.json'); value=json.loads(p.read_bytes()); value['version']=4; p.write_text(json.dumps(value))",
        ):
            with self.subTest(effect=effect):
                api = self.delegate_api(effect)
                original_cwd = Path.cwd()
                try:
                    os.chdir(self.f.root)
                    with (
                        mock.patch.multiple(
                            purifier,
                            LOCK_OVERRIDE=None,
                            RAW_LOCK_OVERRIDE=None,
                            TOOL_LOCK_OVERRIDE=None,
                            RAW_TOOL_LOCK_OVERRIDE=None,
                            RAW_SOURCE_REFERENCE=None,
                        ),
                        mock.patch.object(purifier, "sdk_mode", return_value=False),
                        mock.patch.object(
                            purifier, "tool", return_value="/never-invoked-swift"
                        ),
                        mock.patch.object(
                            purifier, "native_command", side_effect=api.native_command
                        ),
                        mock.patch.object(
                            purifier,
                            "dependencies_v3_sources",
                            return_value={"alpha": self.f.root / "Packages/AlphaPkg"},
                        ),
                        mock.patch.object(
                            purifier, "dependencies_v1_hash", side_effect=self.hash_tree
                        ),
                        mock.patch.object(
                            purifier.dependencies_v1_lifecycle,
                            "registry",
                            return_value=self.f.registry,
                        ),
                        mock.patch.object(lifecycle, "foreground") as foreground,
                    ):
                        with self.assertRaises(ValueError):
                            purifier.swift_command(["build"])
                        foreground.assert_not_called()
                finally:
                    os.chdir(original_cwd)
                self.assertEqual(legacy_runtime.source_fingerprint(self.f.root), before)
                self.assertEqual(
                    ownership.existing(self.f.root, self.hash_tree), self.marker
                )

    def test_foreground_inherits_actual_lock_fds_and_restores_deleted_pins(self):
        api = self.delegate_api("pass")
        control, output = (
            self.f.work / "fd-control.json",
            self.f.work / "fd-output.json",
        )
        pin = self.f.root / "Package.resolved"
        pin.chmod(0o600)
        original = pin.read_bytes()
        expected = []

        def started(pid, descriptors):
            expected.extend(
                [[fd, os.fstat(fd).st_dev, os.fstat(fd).st_ino] for fd in descriptors]
            )
            control.write_text(json.dumps(expected))

        body = "import os,json,sys,time\nfrom pathlib import Path\ncontrol=Path(sys.argv[1]); deadline=time.monotonic()+5\nwhile not control.exists():\n assert time.monotonic()<deadline\n time.sleep(.01)\nrows=json.loads(control.read_text()); actual=[[fd,os.fstat(fd).st_dev,os.fstat(fd).st_ino] for fd,_,_ in rows]\nPath('Package.resolved').unlink()\nPath(sys.argv[2]).write_text(json.dumps(actual))\n"
        status = lifecycle.run_native(
            self.f.root,
            self.raw,
            [sys.executable, "-c", body, str(control), str(output)],
            self.hash_tree,
            self.f.registry,
            api=api,
            child_started=started,
        )
        self.assertEqual(status, 0)
        self.assertTrue(expected)
        self.assertEqual(json.loads(output.read_text()), expected)
        self.assertEqual(pin.read_bytes(), original)
        self.assertEqual(pin.stat().st_mode & 0o777, 0o600)

    def test_signal_during_bridge_preparation_is_deferred_until_child(self):
        api = self.delegate_api("pass")
        original = (self.f.root / "Package.resolved").read_bytes()
        publish = lifecycle.Bridge.publish

        def signal_then_publish(bridge, *args, **kwargs):
            os.kill(os.getpid(), signal.SIGTERM)
            return publish(bridge, *args, **kwargs)

        with mock.patch.object(
            lifecycle.Bridge, "publish", autospec=True, side_effect=signal_then_publish
        ):
            status = lifecycle.run_native(
                self.f.root,
                self.raw,
                [sys.executable, "-c", "import time; time.sleep(5)"],
                self.hash_tree,
                self.f.registry,
                api=api,
            )
        self.assertEqual(status, -signal.SIGTERM)
        self.assertEqual((self.f.root / "Package.resolved").read_bytes(), original)
        self.assertFalse((self.f.root / ".swiftpm-purifier-transaction.json").exists())

    def test_foreground_signal_restores_pin_absence_change_before_return(self):
        api = self.delegate_api("pass")
        ready = self.f.work / "foreground-ready"
        original = (self.f.root / "Package.resolved").read_bytes()
        body = "import sys,time\nfrom pathlib import Path\nPath('Package.resolved').unlink()\nPath(sys.argv[1]).write_text('ready')\ntime.sleep(10)\n"

        def started(pid, descriptors):
            deadline = time.monotonic() + 5
            while not ready.exists():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            os.kill(os.getpid(), signal.SIGTERM)

        status = lifecycle.run_native(
            self.f.root,
            self.raw,
            [sys.executable, "-c", body, str(ready)],
            self.hash_tree,
            self.f.registry,
            api=api,
            child_started=started,
        )
        self.assertEqual(status, -signal.SIGTERM)
        self.assertEqual((self.f.root / "Package.resolved").read_bytes(), original)
        self.assertFalse((self.f.root / ".swiftpm-purifier-transaction.json").exists())
