# SPDX-License-Identifier: MIT
"""Owned synthetic lifecycle controls; no SDK commands or responses."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import bounded_native


@unittest.skipUnless(os.name == "posix", "owned process groups require POSIX")
class Cancellation(unittest.TestCase):
    def wait_for(self, path, child):
        deadline = time.monotonic() + 8
        while not path.exists():
            self.assertIsNone(child.poll(), "worker exited before control marker")
            self.assertLess(time.monotonic(), deadline, "control marker deadline")
            time.sleep(0.01)

    def alive(self, pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def exercise(self, mode):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = Path(bounded_native.__file__).resolve().parent
            script = r"""
import ast,json,os,signal,subprocess,sys,time
from pathlib import Path
source=Path(sys.argv[1]);root=Path(sys.argv[2]);mode=sys.argv[3]
sys.path.insert(0,str(source))
import bounded_native as b
import swiftpm_purifier as cli
actor='import os,time;from pathlib import Path;p=Path('+repr(str(root/'actor.pid'))+');t=p.with_suffix(".tmp");t.write_text(str(os.getpid()));t.replace(p);time.sleep(60)'
real_popen=b.subprocess.Popen
real_selector=b.selectors.DefaultSelector

def wait(path):
    deadline=time.monotonic()+5
    while not path.exists():
        assert time.monotonic()<deadline
        time.sleep(0.01)

def popen(*args,**kwargs):
    child=real_popen(*args,**kwargs)
    if mode=='spawn':
        wait(root/'actor.pid');(root/'spawn-ready').write_text('ready');wait(root/'release')
    if mode=='second':
        original_wait=child.wait
        def delayed_wait(*args,**kwargs):
            (root/'cleanup-ready').write_text('ready');wait(root/'release')
            result=original_wait(*args,**kwargs)
            (root/'wait-completed').write_text('reaped')
            return result
        child.wait=delayed_wait
    return child

if mode in ['spawn','second']:b.subprocess.Popen=popen
if mode=='selector':
    def selector():
        wait(root/'actor.pid')
        raise OSError('injected selector setup failure')
    b.selectors.DefaultSelector=selector
record={'label':'synthetic'}
def save():(root/'record.json').write_text(json.dumps(record))
def main():
    try:
        save()
        if mode=='ordinary':
            cli.run([sys.executable,'-c',actor])
        elif mode=='broad':
            try:b.run_recorded([sys.executable,'-c',actor],record=record,logs=root,save=save,timeout=60)
            except Exception:
                (root/'ordinary-error-caught').write_text('wrong classification')
                return 17
        else:
            try:b.run_recorded([sys.executable,'-c',actor],record=record,logs=root,save=save,timeout=60)
            except OSError as error:
                assert mode=='selector' and 'injected selector' in str(error)
        return 0
    finally:(root/'outer-finally').write_text('unwound')
entry=ast.parse((source/'swiftpm_purifier.py').read_text()).body[-1]
assert isinstance(entry,ast.If)
module=ast.Module(body=entry.body,type_ignores=[]);ast.fix_missing_locations(module)
exec(compile(module,'actual-cli-entrypoint','exec'),{'sys':sys,'main':main,'bounded_native':b})
"""
            child = subprocess.Popen(
                [sys.executable, "-c", script, str(source), str(root), mode],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            passed = False
            try:
                self.wait_for(root / "actor.pid", child)
                actor = int((root / "actor.pid").read_text())
                if mode != "selector":
                    if mode == "spawn":
                        self.wait_for(root / "spawn-ready", child)
                    os.kill(child.pid, signal.SIGTERM)
                    if mode == "spawn":
                        time.sleep(0.05)
                        (root / "release").write_text("continue")
                    if mode == "second":
                        self.wait_for(root / "cleanup-ready", child)
                        os.kill(child.pid, signal.SIGTERM)
                        time.sleep(0.05)
                        (root / "release").write_text("continue")
                stdout, stderr = child.communicate(timeout=8)
                self.assertEqual(
                    child.returncode,
                    0 if mode == "selector" else -signal.SIGTERM,
                    (stdout, stderr),
                )
                self.assertTrue((root / "outer-finally").is_file())
                self.assertFalse(self.alive(actor), "native child was not reaped")
                record = json.loads((root / "record.json").read_text())
                if mode != "ordinary":
                    self.assertIn("boundedFailure", record)
                    self.assertEqual(
                        record["boundedFailure"]["type"],
                        "OSError" if mode == "selector" else "Termination",
                    )
                self.assertFalse((root / "ordinary-error-caught").exists())
                if mode == "second":
                    self.assertTrue((root / "wait-completed").is_file())
                passed = True
            finally:
                if child.poll() is None or not passed:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait(timeout=3)
                if not passed and (root / "actor.pid").is_file():
                    actor = int((root / "actor.pid").read_text())
                    try:
                        os.killpg(actor, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                for stream in [child.stdout, child.stderr]:
                    stream.close()

    def test_capture_sigterm_unwinds_entrypoint_and_reaps_native_group(self):
        self.exercise("normal")

    def test_sigterm_during_spawn_registration_reaps_native_group(self):
        self.exercise("spawn")

    def test_second_sigterm_does_not_interrupt_cleanup(self):
        self.exercise("second")

    def test_postspawn_selector_failure_reaps_native_group(self):
        self.exercise("selector")

    def test_signal_is_not_wrapped_as_an_ordinary_probe_failure(self):
        self.exercise("broad")

    def test_ordinary_cli_command_reaps_its_private_group(self):
        self.exercise("ordinary")

    def test_ordinary_command_preserves_text_input_output_and_failure(self):
        result = bounded_native.run_command(
            [sys.executable, "-c", "import sys;print(sys.stdin.read().upper(),end='')"],
            input="fixture\n",
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr), (0, "FIXTURE\n", "")
        )
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            bounded_native.run_command(
                [
                    sys.executable,
                    "-c",
                    "import sys;print('out');print('err',file=sys.stderr);sys.exit(7)",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
        self.assertEqual(
            (
                caught.exception.returncode,
                caught.exception.stdout,
                caught.exception.stderr,
            ),
            (7, "out\n", "err\n"),
        )

    def test_default_handler_restored_and_custom_relay_preserved(self):
        previous = signal.getsignal(signal.SIGTERM)
        try:
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            with bounded_native.default_sigterm():
                self.assertNotEqual(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)
            self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)
            with self.assertRaisesRegex(ValueError, "control"):
                with bounded_native.default_sigterm():
                    raise ValueError("control")
            self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)
            received = []

            def custom(signum, frame):
                received.append(signum)

            signal.signal(signal.SIGTERM, custom)
            with bounded_native.default_sigterm():
                self.assertIs(signal.getsignal(signal.SIGTERM), custom)
                os.kill(os.getpid(), signal.SIGTERM)
            self.assertIs(signal.getsignal(signal.SIGTERM), custom)
            self.assertEqual(received, [signal.SIGTERM])
        finally:
            signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    unittest.main()
