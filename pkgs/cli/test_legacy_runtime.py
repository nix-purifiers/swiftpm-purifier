# SPDX-License-Identifier: MIT
"""Currency and projection controls using real, bounded subprocess delegates.

The API adapter supplies a trusted empty graph; these are not SDK/NAR tests.
The native integration fixture separately exercises original Swift and real NARs.
"""

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import legacy_runtime as runtime
import swiftpm_purifier as cli
import bounded_native


class LegacyRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "caller/project"
        self.root.mkdir(parents=True)
        (self.root / "Package.swift").write_text("manifest input\n")
        self.pins = self.root / "Package.resolved"
        self.native = b'{"version":1,"object":{"pins":[]}}\n'
        self.pins.write_bytes(self.native)
        self.pins.chmod(0o600)
        self.lock = {
            "version": 4,
            "workspaceStateVersion": 2,
            "package": "Example",
            "products": [],
            "pins": {},
        }
        (self.root / cli.LOCK_NAME).write_text(json.dumps(self.lock))
        self.tree = self.base / "tree"
        self.tree.mkdir()
        (self.tree / "workspace-state.json.in").write_text(
            '{"version":2,"object":{"dependencies":[]}}'
        )
        self.work = self.base / "evidence"
        self.work.mkdir()

    def tearDown(self):
        for path in self.base.glob("external*"):
            path.chmod(0o700)
        self.temporary.cleanup()

    def observe(self, action=""):
        code = (
            "from pathlib import Path\nimport os,json\n"
            "assert os.environ['SWIFT_BUILD_PATH']==str(Path.cwd()/'.build')\n"
            + action
            + "print(json.dumps({'name':'Example','products':[]}))\n"
        )
        api = types.SimpleNamespace(
            is_current=lambda root, lock: True,
            lock_path=lambda root: root / cli.LOCK_NAME,
            resolved_path=lambda root: self.pins,
            copy_package=cli.copy_package,
            nix_build=lambda *args, **kwargs: str(self.tree),
            nix_lock_arguments=lambda root: "",
            nix_lock_environment=lambda root: {},
            ROOT_PLACEHOLDER=cli.ROOT_PLACEHOLDER,
            SCRATCH_DIRECTORIES=cli.SCRATCH_DIRECTORIES,
            native_command=lambda name: [sys.executable, "-c", code],
            product_description=cli.product_description,
        )
        before = runtime.basis(api, self.root)
        with patch.object(runtime, "workspace", return_value=self.work):
            try:
                runtime.validate(api, self.root, self.lock)
            finally:
                self.assertEqual(runtime.basis(api, self.root), before)

    def receipt(self):
        return json.loads((self.work / "receipt.json").read_text())

    def prompt_refusal(self, action, message):
        # A regressed FIFO read must fail the test, never hang the check runner.
        code = (
            "from test_legacy_runtime import LegacyRuntimeTests,runtime\n"
            "test=LegacyRuntimeTests('test_unchanged_delegate_preserves_cold_caller')\n"
            "test.setUp()\ntry:\n"
            " try:\n  test.observe(" + repr(action) + ")\n"
            " except runtime.ObservationError as error:\n"
            "  assert " + repr(message) + " in str(error), str(error)\n"
            " else:\n  raise AssertionError('unsafe output accepted')\n"
            "finally:\n test.tearDown()\n"
        )
        result = bounded_native.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parent,
            timeout=5,
            output_limit=64 * 1024,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_unchanged_delegate_preserves_cold_caller(self):
        self.observe()
        self.assertTrue(self.receipt()["complete"])
        self.assertFalse((self.root / ".build").exists())
        self.assertFalse((self.root / "Packages").exists())
        self.assertFalse((self.work / "packages").exists())
        self.assertTrue((self.work / "logs/native-dump-package.stdout").is_file())

    def test_manifest_mutation_cannot_complete_observation(self):
        with self.assertRaisesRegex(runtime.ObservationError, "source changed"):
            self.observe(
                "p=Path('Package.swift');p.write_bytes(p.read_bytes()+b'changed')\n"
            )
        self.assertFalse(self.receipt()["complete"])
        self.assertNotEqual(
            self.receipt()["capsuleBefore"], self.receipt()["capsuleAfter"]
        )

    def test_other_source_mutation_cannot_complete_observation(self):
        with self.assertRaisesRegex(runtime.ObservationError, "source changed"):
            self.observe("Path('generated.swift').write_text('changed')\n")
        self.assertFalse(self.receipt()["complete"])

    def test_selected_pin_byte_mutation_cannot_complete_observation(self):
        with self.assertRaisesRegex(runtime.ObservationError, "source changed"):
            self.observe(
                "p=Path('Package.resolved');p.write_bytes(p.read_bytes()+b'\\n')\n"
            )
        self.assertFalse(self.receipt()["complete"])

    def test_new_workspace_protocol_is_not_accepted_as_state_two(self):
        with self.assertRaisesRegex(
            runtime.ObservationError, "changed the locked graph"
        ):
            self.observe("Path('.build/workspace-state.json').write_text('{}')\n")
        self.assertFalse(self.receipt()["complete"])

    def test_progress_prelude_is_not_stripped(self):
        with self.assertRaisesRegex(
            runtime.ObservationError, "invalid native manifest JSON"
        ):
            self.observe("print('Fetching unrelated')\n")
        self.assertFalse(self.receipt()["complete"])

    def test_readonly_external_pin_projection_preserves_unselected_logical_file(self):
        (self.root / "Package.resolved").write_bytes(b"unselected logical input\n")
        parent = self.base / "external-pins"
        parent.mkdir()
        self.pins = parent / "pins.json"
        self.pins.write_bytes(self.native)
        self.pins.chmod(0o600)
        parent.chmod(0o500)
        before = runtime.file_record(self.pins)
        self.observe(
            "assert Path('Package.resolved').read_bytes()==" + repr(self.native) + "\n"
        )
        self.assertEqual(runtime.file_record(self.pins), before)
        self.assertEqual(
            (self.root / "Package.resolved").read_bytes(), b"unselected logical input\n"
        )
        self.assertTrue(self.receipt()["complete"])

    def test_tmpdir_inside_caller_does_not_host_the_capsule(self):
        inside = self.root / "temporary"
        inside.mkdir()
        with patch.object(runtime.tempfile, "gettempdir", return_value=str(inside)):
            work = runtime.workspace(self.root)
        try:
            self.assertNotEqual(work, self.root)
            self.assertNotIn(self.root, work.parents)
        finally:
            shutil.rmtree(work)

    def test_native_state_fifo_is_refused_without_waiting_for_a_writer(self):
        self.prompt_refusal(
            "p=Path('.build/dependencies-state.json');p.unlink();os.mkfifo(p)\n",
            "physical regular file",
        )

    def test_native_state_symlink_is_not_followed(self):
        self.prompt_refusal(
            "p=Path('.build/dependencies-state.json');p.unlink();"
            "target=Path('../state-fifo');os.mkfifo(target);p.symlink_to(target.resolve())\n",
            "native metadata observation failed",
        )

    def test_native_state_parent_symlink_is_not_followed(self):
        self.prompt_refusal(
            "import shutil\n"
            "target=Path('../outside-cache');target.mkdir();"
            '(target/\'dependencies-state.json\').write_text(\'{"version":2,"object":{"dependencies":[]}}\');'
            "shutil.rmtree('.build');Path('.build').symlink_to(target.resolve())\n",
            "native metadata observation failed",
        )

    def test_oversize_native_state_is_refused_before_decoding(self):
        self.prompt_refusal(
            "with Path('.build/dependencies-state.json').open('r+b') as stream: "
            "stream.truncate(16*1024*1024+1)\n",
            "output exceeds its bound",
        )

    def test_oversize_changed_pin_is_refused_before_fingerprinting(self):
        self.prompt_refusal(
            "with Path('Package.resolved').open('r+b') as stream: stream.truncate(1<<40)\n",
            "source changed during native observation",
        )

    def test_changed_pin_alias_is_refused_before_diagnostic_reads(self):
        attempts = []
        original = runtime.regular_content

        def guarded(root, relative, *args, **kwargs):
            if (
                Path(relative) == Path("Package.resolved")
                and (Path(root) / relative).is_symlink()
            ):
                attempts.append(str(Path(root) / relative))
                raise AssertionError("changed pin alias reached a diagnostic reader")
            return original(root, relative, *args, **kwargs)

        action = (
            "target=Path('../outside-pins');target.write_text('unselected target');"
            "p=Path('Package.resolved');p.unlink();p.symlink_to(target.resolve())\n"
        )
        with patch.object(runtime, "regular_content", side_effect=guarded):
            with self.assertRaisesRegex(runtime.ObservationError, "source changed"):
                self.observe(action)
        self.assertEqual(attempts, [])


if __name__ == "__main__":
    unittest.main()
