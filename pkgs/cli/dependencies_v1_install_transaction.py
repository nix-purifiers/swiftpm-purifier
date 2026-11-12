# SPDX-License-Identifier: MIT
"""Recoverable swaps of fixed owned Packages/native-state roles.

Ownership and runtime capability checks belong to the caller. This committer
accepts only those two fixed roles and never follows paths supplied by a journal.
It shares the capture transaction's root lock, reservation and recovery hold.
"""

import os
from pathlib import Path
import secrets
import shutil
import stat

import dependencies_v1_cache as cache
import dependencies_v1_tree_transaction as trees
from dependencies_v1_transaction import (
    Transaction,
    JOURNAL,
    PREFIX,
    LIMIT,
    canonical,
    decoded,
    encoded,
    fault,
    identity,
    need,
    read,
    sync,
    write,
)


def snapshot(path):
    if not os.path.lexists(path):
        return None
    facts = path.lstat()
    if stat.S_ISDIR(facts.st_mode):
        return {"kind": "directory", "entries": trees.inventory(path)}
    mode = stat.S_IMODE(facts.st_mode)
    need(mode <= 0o777, "native state special mode")
    return {"kind": "regular", "bytes": encoded(read(path)), "mode": mode}


def validate_snapshot(value):
    if value is None:
        return
    need(isinstance(value, dict), "install role snapshot")
    if value.get("kind") == "directory":
        need(set(value) == {"kind", "entries"}, "install directory fields")
        trees.validate(value["entries"])
    else:
        need(
            value.get("kind") == "regular"
            and set(value) == {"kind", "bytes", "mode"}
            and type(value["mode"]) is int
            and 0 <= value["mode"] <= 0o777
            and decoded(value["bytes"]) is not None,
            "install regular fields",
        )


def sync_tree(root, entries):
    """Flush complete copied files before making the prepared journal durable."""
    for name, entry in entries.items():
        if entry["kind"] != "regular":
            continue
        fd = os.open(root / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            facts = os.fstat(fd)
            need(
                stat.S_ISREG(facts.st_mode) and facts.st_nlink == 1,
                "install staged file changed before fsync",
            )
            os.fsync(fd)
        finally:
            os.close(fd)
    for name in sorted(entries, key=lambda name: len(Path(name).parts), reverse=True):
        if entries[name]["kind"] == "directory":
            sync(root / name)


class Installation(Transaction):
    def __init__(self, root, registry):
        super().__init__(root, registry, native=None)

    def validate(self, record):
        self.check()
        need(
            isinstance(record, dict)
            and set(record)
            == {
                "version",
                "protocol",
                "operation",
                "rootIdentity",
                "buildIdentity",
                "nonce",
                "old",
                "new",
            },
            "closed install reservation",
        )
        need(
            type(record["version"]) is int
            and record["version"] == 1
            and record["protocol"] == "dependencies-state-v1"
            and record["operation"] == "owned-install"
            and isinstance(record["rootIdentity"], list)
            and len(record["rootIdentity"]) == 2
            and all(type(n) is int for n in record["rootIdentity"])
            and record["rootIdentity"] == self.root_id,
            "install reservation binding",
        )
        need(
            isinstance(record["nonce"], str)
            and len(record["nonce"]) == 64
            and all(c in "0123456789abcdef" for c in record["nonce"]),
            "install nonce",
        )
        build_id = record["buildIdentity"]
        need(
            build_id is None
            or isinstance(build_id, list)
            and len(build_id) == 2
            and all(type(n) is int for n in build_id),
            "install build parent identity",
        )
        if build_id is not None:
            cache.directory(self.root / ".build")
            need(
                identity(self.root / ".build") == build_id,
                "install build parent changed",
            )
        for label in ("old", "new"):
            need(
                isinstance(record[label], list) and len(record[label]) == 2,
                "install role count",
            )
            for value in record[label]:
                validate_snapshot(value)
            need(
                record[label][0] is None or record[label][0]["kind"] == "directory",
                "Packages role kind",
            )
            need(
                record[label][1] is None
                or record[label][1]["kind"]
                == ("directory" if build_id is None else "regular"),
                "native state role kind",
            )
        if build_id is None:
            need(record["old"][1] is None, "absent build role origin")
        return record

    def role_paths(self, record):
        return [
            self.root / "Packages",
            self.root / ".build" / "dependencies-state.json"
            if record["buildIdentity"] is not None
            else self.root / ".build",
        ]

    def stage(self, record):
        return self.root / (PREFIX + record["nonce"])

    def current(self, record):
        self.check()
        self.validate(record)
        return [snapshot(path) for path in self.role_paths(record)]

    def stage_contents(self, record):
        stage = self.stage(record)
        if not os.path.lexists(stage):
            return
        cache.directory(stage)
        facts = stage.lstat()
        need(
            facts.st_uid == os.getuid() and stat.S_IMODE(facts.st_mode) == 0o700,
            "private install stage",
        )
        allowed = {"journal.pending"}
        for label in ("old", "new"):
            for index, expected in enumerate(record[label]):
                name = label + "-" + str(index)
                if expected is None:
                    continue
                allowed.add(name)
                path = stage / name
                if not os.path.lexists(path):
                    continue
                if expected["kind"] == "directory":
                    trees.verify(path, expected["entries"], partial=True)
                else:
                    need(snapshot(path) == expected, "changed install staging file")
        need(
            {path.name for path in stage.iterdir()} <= allowed,
            "foreign install staging entry",
        )
        pending = read(stage / "journal.pending", control=True)
        need(
            pending is None
            or pending
            in [
                canonical(self.journal(record, phase))
                for phase in ("PREPARED", "COMMITTED")
            ],
            "changed install journal staging",
        )

    def cleanup(self, record, callback=None, phase=None):
        expected_outputs = self.current(record)
        need(
            expected_outputs in (record["old"], record["new"]),
            "cleanup requires complete install roles",
        )
        if phase is not None:
            need(
                expected_outputs == record["new" if phase == "COMMITTED" else "old"],
                "cleanup phase/output binding",
            )

        def guard():
            self.guard_control(record, phase)
            self.stage_contents(record)
            need(
                self.current(record) == expected_outputs,
                "install outputs changed during cleanup",
            )

        guard()
        stage = self.stage(record)
        if os.path.lexists(stage):
            for label in ("old", "new"):
                for index, expected in enumerate(record[label]):
                    path = stage / (label + "-" + str(index))
                    if not os.path.lexists(path):
                        continue
                    guard()
                    if expected["kind"] == "directory":
                        trees.cleanup(path, expected["entries"], guard, callback)
                    else:
                        path.unlink()
                        sync(stage)
                    fault(callback, "install-cleanup-" + label + "-" + str(index))
            guard()
            (stage / "journal.pending").unlink(missing_ok=True)
            sync(stage)
            guard()
            stage.rmdir()
            sync(self.root)
            fault(callback, "install-stage-removed")
        guard()
        (self.root / JOURNAL).unlink(missing_ok=True)
        sync(self.root)
        fault(callback, "install-journal-removed")
        self.guard_control(record, None)
        need(
            self.current(record) == expected_outputs,
            "install output changed after journal removal",
        )
        self.reservation.unlink()
        sync(self.registry)
        fault(callback, "install-reservation-removed")

    def rollback_geometry(self, record):
        """Reject foreign data in either role before any rollback mutation."""
        self.guard_control(record, "PREPARED")
        self.stage_contents(record)
        for index, target in enumerate(self.role_paths(record)):
            old, new = record["old"][index], record["new"][index]
            current = snapshot(target)
            backup = self.stage(record) / ("old-" + str(index))
            staged = self.stage(record) / ("new-" + str(index))
            if os.path.lexists(staged):
                need(snapshot(staged) == new, "incomplete new rollback role")
            if os.path.lexists(backup):
                need(snapshot(backup) == old, "incomplete old install backup")
                need(
                    current is None or current == new, "foreign install rollback target"
                )
                need(
                    current is None or not os.path.lexists(staged),
                    "duplicate new install role",
                )
            elif old is None:
                need(
                    current is None or current == new,
                    "foreign first-install rollback role",
                )
                need(
                    current is None or not os.path.lexists(staged),
                    "duplicate first-install new role",
                )
            else:
                need(current == old, "old install role missing without backup")

    def recover(self, callback=None):
        self.check()
        need(not os.path.lexists(self.hold), "install foreign-control recovery hold")
        raw = read(self.reservation, control=True)
        if raw is None:
            pending = read(self.pending, control=True)
            if pending is not None:
                record = self.validate(cache.decode(pending))
                need(
                    pending == canonical(record),
                    "canonical pending install reservation",
                )
                need(
                    self.current(record) == record["old"],
                    "install inputs changed before reservation",
                )
                self.guard_pending(record)
                self.pending.unlink()
                sync(self.registry)
                return "aborted-install-reservation"
            # The shared capture guard detects unreserved root stages.
            return super().recover(callback)
        record = self.validate(cache.decode(raw))
        if raw != canonical(record) or os.path.lexists(self.pending):
            self.foreign_control(record)
        journal = read(self.root / JOURNAL, control=True)
        if journal is None:
            self.guard_control(record, None)
            need(
                self.current(record) in (record["old"], record["new"]),
                "partial install without journal",
            )
            self.cleanup(record, callback)
            return "cleaned-install-staging"
        envelope = cache.decode(journal)
        need(
            isinstance(envelope, dict)
            and set(envelope) == {"reservation", "phase"}
            and envelope["reservation"] == record
            and envelope["phase"] in ("PREPARED", "COMMITTED"),
            "bound install journal",
        )
        phase = envelope["phase"]
        self.guard_control(record, phase)
        self.stage_contents(record)
        if phase == "COMMITTED":
            need(
                self.current(record) == record["new"],
                "committed install outputs changed",
            )
            self.cleanup(record, callback, phase)
            return "committed-install"
        if self.current(record) == record["old"]:
            self.cleanup(record, callback, phase)
            return "restored-install"
        self.rollback_geometry(record)
        for index, target in enumerate(self.role_paths(record)):
            self.rollback_geometry(record)
            old, new = record["old"][index], record["new"][index]
            current = snapshot(target)
            backup = self.stage(record) / ("old-" + str(index))
            staged = self.stage(record) / ("new-" + str(index))
            if os.path.lexists(backup):
                need(snapshot(backup) == old, "incomplete old install backup")
                need(
                    current is None or current == new, "foreign install rollback target"
                )
                if current is not None:
                    need(not os.path.lexists(staged), "duplicate new install role")
                    os.rename(target, staged)
                    sync(target.parent)
                    sync(staged.parent)
                    fault(callback, "install-rollback-new-" + str(index))
                self.rollback_geometry(record)
                os.rename(backup, target)
                sync(target.parent)
                sync(backup.parent)
                fault(callback, "install-rollback-old-" + str(index))
            elif old is None:
                need(
                    current is None or current == new,
                    "foreign first-install rollback role",
                )
                if current is not None:
                    need(
                        not os.path.lexists(staged), "duplicate first-install new role"
                    )
                    os.rename(target, staged)
                    sync(target.parent)
                    sync(staged.parent)
                    fault(callback, "install-rollback-new-" + str(index))
            else:
                need(current == old, "old install role missing without backup")
        need(self.current(record) == record["old"], "install rollback verification")
        self.cleanup(record, callback, phase)
        return "restored-install"

    def publish(self, sources, expected, callback=None):
        """Publish caller-verified external staging roles, or None to remove."""
        self.check()
        need(self.recover() == "clean", "recover before planning installation")
        need(isinstance(sources, list) and len(sources) == 2, "install source roles")
        need(
            isinstance(expected, list) and len(expected) == 2, "install expected roles"
        )
        for source in sources:
            if source is not None:
                from dependencies_v1_layout import canonical as canonical_path

                need(
                    not canonical_path(source).is_relative_to(self.root),
                    "external install source required",
                )
        build = self.root / ".build"
        record = {
            "version": 1,
            "protocol": "dependencies-state-v1",
            "operation": "owned-install",
            "rootIdentity": self.root_id,
            "buildIdentity": identity(build) if os.path.lexists(build) else None,
            "nonce": secrets.token_hex(32),
            "old": expected,
            "new": [
                None if source is None else snapshot(Path(source)) for source in sources
            ],
        }
        self.validate(record)
        need(self.current(record) == expected, "install inputs changed since planning")
        need(
            len(canonical(self.journal(record, "COMMITTED"))) <= LIMIT,
            "install envelope byte bound",
        )
        need(not os.path.lexists(self.stage(record)), "install stage collision")
        write(self.pending, canonical(record))
        fault(callback, "install-reservation-file")
        # guard_pending expects capture selector fields only through stage paths;
        # this fixed-root operation uses the same single root stage geometry.
        self.guard_pending(record)
        os.replace(self.pending, self.reservation)
        sync(self.registry)
        fault(callback, "install-reservation-durable")
        self.guard_control(record, None)
        stage = self.stage(record)
        stage.mkdir(mode=0o700)
        sync(self.root)
        fault(callback, "install-stage-created")
        for index, source in enumerate(sources):
            self.guard_control(record, None)
            if source is not None:
                target = stage / ("new-" + str(index))
                if record["new"][index]["kind"] == "directory":
                    shutil.copytree(source, target, symlinks=True)
                    need(
                        snapshot(target) == record["new"][index],
                        "install staged source changed",
                    )
                    sync_tree(target, record["new"][index]["entries"])
                else:
                    write(
                        target,
                        decoded(record["new"][index]["bytes"]),
                        record["new"][index]["mode"],
                    )
                need(
                    snapshot(target) == record["new"][index],
                    "install staged source changed",
                )
            fault(callback, "install-role-staged-" + str(index))
        sync(stage)
        self.guard_control(record, None)
        write(stage / "journal.pending", canonical(self.journal(record, "PREPARED")))
        os.replace(stage / "journal.pending", self.root / JOURNAL)
        sync(self.root)
        fault(callback, "install-prepared")
        for index, target in enumerate(self.role_paths(record)):
            self.guard_control(record, "PREPARED")
            self.stage_contents(record)
            need(
                self.current(record) == record["new"][:index] + expected[index:],
                "install roles changed during publication",
            )
            if expected[index] is not None:
                os.rename(target, stage / ("old-" + str(index)))
                sync(target.parent)
                sync(stage)
                fault(callback, "install-old-renamed-" + str(index))
            self.rollback_geometry(record)
            need(
                self.current(record)
                == record["new"][:index] + [None] + expected[index + 1 :],
                "install roles changed between publication renames",
            )
            if record["new"][index] is not None:
                os.rename(stage / ("new-" + str(index)), target)
                sync(target.parent)
                sync(stage)
                fault(callback, "install-new-renamed-" + str(index))
        need(self.current(record) == record["new"], "install new roles verification")
        self.guard_control(record, "PREPARED")
        write(stage / "journal.pending", canonical(self.journal(record, "COMMITTED")))
        os.replace(stage / "journal.pending", self.root / JOURNAL)
        sync(self.root)
        fault(callback, "install-committed")
        self.cleanup(record, callback, "COMMITTED")
        return record
