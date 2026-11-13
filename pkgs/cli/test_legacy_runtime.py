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


class LegacyRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
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


if __name__ == "__main__":
    unittest.main()
