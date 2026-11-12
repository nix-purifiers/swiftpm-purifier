# SPDX-License-Identifier: MIT
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import dependencies_v1_cache as cache
from dependencies_v1_bridge import NativePinBridge
from dependencies_v1_lifecycle import recover
from dependencies_v1_transaction import JOURNAL, canonical


class Bridge(unittest.TestCase):
    def test_readonly_external_portable_parent_needs_no_staging_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "root"
            root.mkdir()
            state = base / "state"
            state.mkdir(mode=0o700)
            external = base / "external"
            external.mkdir()
            portable = external / "lock.json"
            portable.write_bytes(b"portable")
            portable.chmod(0o400)
            external.chmod(0o500)
            original_inode = portable.stat().st_ino
            try:
                with NativePinBridge(root, state, portable=portable).locked() as bridge:
                    bridge.publish(
                        [b"projected", b"portable"], bridge.pair(), defer_commit=True
                    )
                self.assertEqual([p.name for p in external.iterdir()], ["lock.json"])
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; import sys; from dependencies_v1_lifecycle import recover; recover(Path(sys.argv[1]),Path(sys.argv[2]),portable=Path(sys.argv[3]))",
                        str(root),
                        str(state),
                        str(portable),
                    ],
                    cwd=Path(__file__).resolve().parent,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse((root / "Package.resolved").exists())
                self.assertEqual(portable.read_bytes(), b"portable")
                self.assertEqual(portable.stat().st_ino, original_inode)
            finally:
                external.chmod(0o700)

    def test_projection_and_fresh_process_recovery_restore_bytes_or_absence(self):
        for original in (None, b"unselected logical bytes\n"):
            for selected in (None, b"selected external bytes\n"):
                with (
                    self.subTest(original=original, selected=selected),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    base = Path(temporary).resolve()
                    root = base / "root"
                    root.mkdir()
                    state = base / "state"
                    state.mkdir(mode=0o700)
                    native = base / "external-native"
                    portable = root / "swiftpm-purifier.lock.json"
                    portable.write_bytes(b"portable")
                    if selected is not None:
                        native.write_bytes(selected)
                    logical = root / "Package.resolved"
                    if original is not None:
                        logical.write_bytes(original)
                        logical.chmod(0o600)
                    portable_id = portable.stat().st_ino
                    with NativePinBridge(root, state).locked() as bridge:
                        bridge.publish(
                            [selected, b"portable"], bridge.pair(), defer_commit=True
                        )
                    self.assertEqual(
                        logical.read_bytes() if logical.exists() else None, selected
                    )
                    self.assertTrue((root / JOURNAL).exists())
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-c",
                            "from pathlib import Path; import sys; from dependencies_v1_lifecycle import recover; recover(Path(sys.argv[1]),Path(sys.argv[2]),native=Path(sys.argv[3]))",
                            str(root),
                            str(state),
                            str(native),
                        ],
                        cwd=Path(__file__).resolve().parent,
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        logical.read_bytes() if logical.exists() else None, original
                    )
                    if original is not None:
                        self.assertEqual(logical.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(portable.stat().st_ino, portable_id)
                    self.assertFalse((root / JOURNAL).exists())

    def test_foreign_logical_bytes_refuse_and_remain(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "root"
            root.mkdir()
            state = base / "state"
            state.mkdir(mode=0o700)
            (root / "swiftpm-purifier.lock.json").write_bytes(b"portable")
            with NativePinBridge(root, state).locked() as bridge:
                bridge.publish(
                    [b"projected", b"portable"], bridge.pair(), defer_commit=True
                )
            logical = root / "Package.resolved"
            logical.write_bytes(b"foreign caller edit")
            with self.assertRaises(cache.Reject):
                recover(root, state)
            self.assertEqual(logical.read_bytes(), b"foreign caller edit")

    def test_impossible_committed_phase_persists_hold(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "root"
            root.mkdir()
            state = base / "state"
            state.mkdir(mode=0o700)
            (root / "swiftpm-purifier.lock.json").write_bytes(b"portable")
            with NativePinBridge(root, state).locked() as bridge:
                bridge.publish(
                    [b"projected", b"portable"], bridge.pair(), defer_commit=True
                )
            value = json.loads((root / JOURNAL).read_bytes())
            value["phase"] = "COMMITTED"
            (root / JOURNAL).write_bytes(canonical(value))
            with self.assertRaises(cache.Reject):
                recover(root, state)
            self.assertEqual(len(list(state.glob("*.hold"))), 1)
            self.assertEqual((root / "Package.resolved").read_bytes(), b"projected")


if __name__ == "__main__":
    unittest.main()
