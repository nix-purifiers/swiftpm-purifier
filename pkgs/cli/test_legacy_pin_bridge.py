# SPDX-License-Identifier: MIT
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch

import dependencies_v1_cache as cache
import legacy_pin_bridge as bridge
import swiftpm_purifier as api


class Recovery(unittest.TestCase):
    def exercise(self, original, mutation):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        root = base / "root"
        root.mkdir()
        state = base / "state"
        state.mkdir(mode=0o700)
        value = dict.fromkeys(api.LOCK_KEYS)
        value.update(
            version=4,
            workspaceStateVersion=2,
            pins={},
            products=[],
            localDependencies=[],
            binaryTargets=[],
        )
        value["contentHash"] = api.content_hash(value)
        portable = root / "swiftpm-purifier.lock.json"
        portable.write_text(json.dumps(value))
        logical = root / "Package.resolved"
        if original is not None:
            logical.write_bytes(original)
            logical.chmod(0o600)
        with bridge.Bridge(root, state, api).locked() as transaction:
            transaction.publish(
                [b"selected", portable.read_bytes()],
                transaction.pair(),
                defer_commit=True,
            )
        mutation(logical)
        return root, state, logical

    def test_native_deletion_restores_prior_bytes_and_mode_or_absence(self):
        for original in (None, b"original"):
            with self.subTest(original=original):
                root, state, logical = self.exercise(original, Path.unlink)
                with bridge.Bridge(root, state, api).locked() as transaction:
                    transaction.recover()
                self.assertEqual(
                    logical.read_bytes() if logical.exists() else None, original
                )
                if original is not None:
                    self.assertEqual(logical.stat().st_mode & 0o777, 0o600)

    def test_foreign_present_bytes_are_held(self):
        root, state, logical = self.exercise(
            b"original", lambda p: p.write_bytes(b"foreign")
        )
        with bridge.Bridge(root, state, api).locked() as transaction:
            with self.assertRaises(cache.Reject):
                transaction.recover()
            self.assertTrue(transaction.hold.exists())
        self.assertEqual(logical.read_bytes(), b"foreign")

    def test_foreground_positive_status_and_signal_status(self):
        for script, expected in (
            ("raise SystemExit(23)", 23),
            ("import os,signal; os.kill(os.getpid(),signal.SIGTERM)", -signal.SIGTERM),
        ):
            previous = signal.getsignal(signal.SIGTERM)
            status, restore = bridge.foreground(
                [sys.executable, "-c", script], Path.cwd(), []
            )
            try:
                self.assertEqual(status, expected)
            finally:
                restore()
            self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_spawn_failure_restores_signal_handlers(self):
        previous = signal.getsignal(signal.SIGTERM)
        with self.assertRaises(FileNotFoundError):
            bridge.foreground(["/nonexistent/swift"], Path.cwd(), [])
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_signal_during_projection_is_forwarded_after_spawn(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signum=signum):
                previous = signal.getsignal(signum)
                status, restore = bridge.foreground(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    Path.cwd(),
                    [],
                    before_spawn=lambda: os.kill(os.getpid(), signum),
                )
                try:
                    self.assertEqual(status, -signum)
                finally:
                    restore()
                self.assertEqual(signal.getsignal(signum), previous)

    def test_spawn_failure_defers_signal_through_real_transaction_recovery(self):
        root, state, logical = self.exercise(b"original", lambda p: None)
        with bridge.Bridge(root, state, api).locked() as transaction:
            transaction.recover()
        previous = signal.getsignal(signal.SIGTERM)
        recover = bridge.Bridge.recover
        observed = []

        def interrupted_recovery(transaction, *args, **kwargs):
            if transaction.reservation.exists():
                observed.append(signal.getsignal(signal.SIGTERM) != previous)
                os.kill(os.getpid(), signal.SIGTERM)
            return recover(transaction, *args, **kwargs)

        # Isolate admission from this interruption control; publication,
        # failed spawn, locks, signals and rollback all use the real code.
        with (
            patch.object(bridge, "registry", return_value=state),
            patch.object(bridge, "current"),
            patch.object(bridge.Bridge, "recover", interrupted_recovery),
        ):
            with self.assertRaises(FileNotFoundError):
                bridge.run_native(api, root, ["/nonexistent/swift"])
        self.assertEqual(observed, [True])
        self.assertEqual(logical.read_bytes(), b"original")
        self.assertEqual(logical.stat().st_mode & 0o777, 0o600)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
        self.assertFalse(list(state.glob("*.json")))


if __name__ == "__main__":
    unittest.main()
