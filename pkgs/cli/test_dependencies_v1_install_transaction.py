# SPDX-License-Identifier: MIT
from pathlib import Path
import subprocess
import sys
import json
import tempfile
import unittest

from dependencies_v1_install_transaction import Installation, snapshot
from dependencies_v1_transaction import JOURNAL
from dependencies_v1_transaction import ForeignControl, canonical
import dependencies_v1_cache as cache


class Interrupted(Exception):
    pass


class InstallTransaction(unittest.TestCase):
    def fixture(self, base, existing):
        root = base / "root"
        registry = base / "registry"
        sources = base / "sources"
        for path in (root, registry, sources):
            path.mkdir(mode=0o700)
        packages = sources / "Packages"
        (packages / "Leaf").mkdir(parents=True)
        (packages / "Leaf/source.swift").write_bytes(b"new source")
        state = sources / "state"
        state.write_bytes(b"new state")
        if existing:
            (root / "Packages").mkdir()
            (root / "Packages/old").write_bytes(b"old source")
            (root / ".build").mkdir()
            (root / ".build/dependencies-state.json").write_bytes(b"old state")
            (root / ".build/cache").write_bytes(b"retain native cache")
            roles = [packages, state]
            paths = [root / "Packages", root / ".build/dependencies-state.json"]
        else:
            build = sources / "build"
            build.mkdir()
            (build / "dependencies-state.json").write_bytes(state.read_bytes())
            roles = [packages, build]
            paths = [root / "Packages", root / ".build"]
        return root, registry, roles, paths

    def test_publish_and_every_callback_boundary_recovers(self):
        for existing in (False, True):
            with tempfile.TemporaryDirectory() as temporary:
                root, registry, roles, paths = self.fixture(Path(temporary), existing)
                labels = []
                with Installation(root, registry).locked() as transaction:
                    transaction.publish(
                        roles, [snapshot(path) for path in paths], labels.append
                    )
                expected_new = [snapshot(path) for path in paths]
            for boundary in range(len(labels)):
                with (
                    self.subTest(existing=existing, boundary=labels[boundary]),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root, registry, roles, paths = self.fixture(
                        Path(temporary), existing
                    )
                    old = [snapshot(path) for path in paths]
                    seen = []

                    def interrupt(label):
                        seen.append(label)
                        if len(seen) == boundary + 1:
                            raise Interrupted(label)

                    with self.assertRaises(Interrupted):
                        with Installation(root, registry).locked() as transaction:
                            transaction.publish(roles, old, interrupt)
                    recovery = subprocess.run(
                        [
                            sys.executable,
                            "-c",
                            "import sys; from dependencies_v1_install_transaction import Installation; "
                            "t=Installation(sys.argv[1],sys.argv[2]); "
                            "exec('with t.locked():\\n t.recover()')",
                            str(root),
                            str(registry),
                        ],
                        cwd=Path(__file__).resolve().parent,
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    self.assertEqual(recovery.returncode, 0, recovery.stderr)
                    current = [snapshot(path) for path in paths]
                    self.assertIn(current, [old, expected_new])
                    committed = "install-committed" in seen
                    self.assertEqual(current, expected_new if committed else old)
                    self.assertFalse((root / JOURNAL).exists())
                    self.assertEqual(
                        sorted(p.name for p in root.iterdir()),
                        [".build", "Packages"] if (existing or committed) else [],
                    )
                    if existing:
                        self.assertEqual(
                            (root / ".build/cache").read_bytes(), b"retain native cache"
                        )

    def test_foreign_installed_source_is_preserved_on_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, registry, roles, paths = self.fixture(Path(temporary), True)

            def interrupt(label):
                if label == "install-new-renamed-0":
                    (root / "Packages/foreign").write_bytes(b"caller data")
                    raise Interrupted(label)

            with self.assertRaises(Interrupted):
                with Installation(root, registry).locked() as transaction:
                    transaction.publish(
                        roles, [snapshot(path) for path in paths], interrupt
                    )
            with self.assertRaises(cache.Reject):
                with Installation(root, registry).locked() as transaction:
                    transaction.recover()
            self.assertEqual((root / "Packages/foreign").read_bytes(), b"caller data")
            self.assertEqual(
                (root / ".build/dependencies-state.json").read_bytes(), b"old state"
            )

    def test_premature_committed_journal_causes_durable_hold(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, registry, roles, paths = self.fixture(Path(temporary), True)

            def change_control(label):
                if label == "install-new-renamed-0":
                    journal = root / JOURNAL
                    value = json.loads(journal.read_bytes())
                    value["phase"] = "COMMITTED"
                    journal.write_bytes(canonical(value))

            with self.assertRaises(ForeignControl):
                with Installation(root, registry).locked() as transaction:
                    transaction.publish(
                        roles, [snapshot(path) for path in paths], change_control
                    )
            self.assertEqual(len(list(registry.glob("*.hold"))), 1)
            before = [snapshot(path) for path in paths]
            with self.assertRaises(cache.Reject):
                with Installation(root, registry).locked() as transaction:
                    transaction.recover()
            self.assertEqual([snapshot(path) for path in paths], before)

    def test_foreign_second_role_refuses_before_any_rollback(self):
        for existing in (False, True):
            with (
                self.subTest(existing=existing),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root, registry, roles, paths = self.fixture(Path(temporary), existing)

                def interrupt(label):
                    if label == "install-new-renamed-1":
                        raise Interrupted(label)

                with self.assertRaises(Interrupted):
                    with Installation(root, registry).locked() as transaction:
                        transaction.publish(
                            roles, [snapshot(path) for path in paths], interrupt
                        )
                (root / ".build/dependencies-state.json").write_bytes(
                    b"foreign native state"
                )
                before = [snapshot(path) for path in paths]
                with self.assertRaises(cache.Reject):
                    with Installation(root, registry).locked() as transaction:
                        transaction.recover()
                self.assertEqual([snapshot(path) for path in paths], before)

    def test_interrupted_rollback_can_itself_resume(self):
        # Exercise interruption after both halves of the directory rename-back,
        # then interruption during verified backup cleanup.
        for recovery_boundary in range(1, 7):
            with (
                self.subTest(boundary=recovery_boundary),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root, registry, roles, paths = self.fixture(Path(temporary), True)
                old = [snapshot(path) for path in paths]

                def interrupt_publish(label):
                    if label == "install-new-renamed-1":
                        raise Interrupted(label)

                with self.assertRaises(Interrupted):
                    with Installation(root, registry).locked() as transaction:
                        transaction.publish(roles, old, interrupt_publish)
                count = 0

                def interrupt_recovery(label):
                    nonlocal count
                    count += 1
                    if count == recovery_boundary:
                        raise Interrupted(label)

                with self.assertRaises(Interrupted):
                    with Installation(root, registry).locked() as transaction:
                        transaction.recover(interrupt_recovery)
                with Installation(root, registry).locked() as transaction:
                    transaction.recover()
                self.assertEqual([snapshot(path) for path in paths], old)

    def test_foreign_second_role_between_rollback_renames_stops_next_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, registry, roles, paths = self.fixture(Path(temporary), True)

            def stop_publish(label):
                if label == "install-new-renamed-1":
                    raise Interrupted(label)

            with self.assertRaises(Interrupted):
                with Installation(root, registry).locked() as transaction:
                    transaction.publish(
                        roles, [snapshot(path) for path in paths], stop_publish
                    )

            def change_second_role(label):
                if label == "install-rollback-new-0":
                    (root / ".build/dependencies-state.json").write_bytes(
                        b"foreign after rename"
                    )
                    self.assertFalse((root / "Packages").exists())

            with self.assertRaises(cache.Reject):
                with Installation(root, registry).locked() as transaction:
                    transaction.recover(change_second_role)
            self.assertFalse((root / "Packages").exists())
            self.assertEqual(
                (root / ".build/dependencies-state.json").read_bytes(),
                b"foreign after rename",
            )


if __name__ == "__main__":
    unittest.main()
