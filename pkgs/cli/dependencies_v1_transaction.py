# SPDX-License-Identifier: MIT
"""Recoverable ordered publication of format8 selected pin and portable files.

A durable private reservation precedes project staging. Each file stages in
its own parent filesystem. Recovery follows only currently configured paths,
never paths taken on authority from a retained journal. Locks coordinate
purifier processes; detected foreign changes are refused before further writes.
"""

import base64
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import secrets
import stat

import dependencies_v1_cache as cache
import dependencies_v1_layout as layout
import dependencies_v1_selectors as selectors

JOURNAL = ".swiftpm-purifier-transaction.json"
PREFIX = ".swiftpm-purifier-stage-"
LIMIT = 16 * 1024 * 1024
FILE_LIMIT = 1024 * 1024
FIELDS = {
    "version",
    "protocol",
    "operation",
    "rootIdentity",
    "nonce",
    "selectors",
    "old",
    "new",
    "oldModes",
    "newModes",
}


class ForeignControl(cache.Reject):
    """A detected control change must not be adopted by automatic recovery."""

    pass


def need(value, message):
    cache.require(value, message)


def canonical(value):
    import json

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def identity(path):
    return list(selectors.identity(path))


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def encoded(raw):
    return None if raw is None else base64.b64encode(raw).decode()


def decoded(value):
    need(value is None or isinstance(value, str), "transaction byte encoding")
    raw = None if value is None else base64.b64decode(value, validate=True)
    need(raw is None or len(raw) <= FILE_LIMIT, "transaction output byte bound")
    return raw


def sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write(path, raw, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fchmod(stream.fileno(), mode)
        os.fsync(stream.fileno())


def read(path, limit=LIMIT, control=False):
    if not os.path.lexists(path):
        return None
    if control:
        facts = path.lstat()
        need(
            facts.st_uid == os.getuid() and not stat.S_IMODE(facts.st_mode) & 0o077,
            "transaction control ownership/mode",
        )
    return cache.regular_bytes(path.parent, path.name, limit)


def fault(callback, label):
    if callback is not None:
        callback(label)


def lock_file(path):
    if os.path.lexists(path):
        facts = path.lstat()
        need(
            stat.S_ISREG(facts.st_mode)
            and facts.st_nlink == 1
            and facts.st_uid == os.getuid(),
            "transaction lock file identity",
        )
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        facts = os.fstat(fd)
        need(
            stat.S_ISREG(facts.st_mode)
            and facts.st_nlink == 1
            and facts.st_uid == os.getuid(),
            "transaction lock identity",
        )
        need(
            identity(path) == [facts.st_dev, facts.st_ino], "transaction lock replaced"
        )
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd)
        raise


class Transaction:
    OPERATION = "capture-update"
    KEEP_PORTABLE = False
    ALLOW_INTERNAL_SELECTORS = False

    def __init__(
        self, root, registry, native=selectors.DEFAULT, portable=selectors.DEFAULT
    ):
        self.root = layout.canonical(root)
        cache.directory(self.root)
        self.registry = layout.canonical(registry)
        cache.directory(self.registry)
        need(
            not self.registry.is_relative_to(self.root),
            "transaction registry must be external",
        )
        facts = self.registry.lstat()
        need(
            facts.st_uid == os.getuid() and not stat.S_IMODE(facts.st_mode) & 0o077,
            "private owned transaction registry required",
        )
        self.root_id = identity(self.root)
        self.registry_id = identity(self.registry)
        pin, lock, logical = selectors.pair(
            self.root, native, portable, allow_internal=self.ALLOW_INTERNAL_SELECTORS
        )
        self.paths = [pin.path, lock.path]
        for path in self.paths:
            if path is None:
                continue
            need(
                not path.is_relative_to(self.registry),
                "selected output overlaps transaction registry",
            )
            need(
                path.name != JOURNAL
                and not any(
                    part.startswith(PREFIX)
                    and len(part) == len(PREFIX) + 64
                    and all(c in "0123456789abcdef" for c in part[len(PREFIX) :])
                    for part in path.parts
                ),
                "selected output overlaps transaction control path",
            )
        self.parents = [self.root]
        for path in self.paths:
            if path is not None and identity(path.parent) not in [
                identity(parent) for parent in self.parents
            ]:
                self.parents.append(path.parent)
        self.parent_ids = [identity(parent) for parent in self.parents]
        self.bindings = [
            None
            if path is None
            else {
                "location": {"root": path.name}
                if path.parent == self.root
                else {"external": str(path)},
                "parentIdentity": identity(path.parent),
            }
            for path in self.paths
        ]
        self.key = digest(["root", self.root_id])
        self.reservation = self.registry / (self.key + ".json")
        self.pending = self.registry / (self.key + ".pending")
        self.hold = self.registry / (self.key + ".hold")
        locked_paths = {path for path in self.paths if path is not None}
        locked_paths.update(
            (self.root / "Package.resolved", self.root / "swiftpm-purifier.lock.json")
        )
        self.lock_names = sorted(
            {
                self.key,
                *[
                    digest(["file", identity(path.parent), path.name])
                    for path in locked_paths
                ],
            }
        )
        self.descriptors = []

    def check(self):
        need(bool(self.descriptors), "transaction ownership lock required")
        need(
            identity(layout.canonical(self.root)) == self.root_id,
            "transaction root changed",
        )
        need(
            identity(layout.canonical(self.registry)) == self.registry_id,
            "transaction registry changed",
        )
        for parent, expected in zip(self.parents, self.parent_ids):
            need(
                identity(layout.canonical(parent)) == expected,
                "transaction selector parent changed",
            )

    @contextmanager
    def locked(self):
        need(not self.descriptors, "transaction context already active")
        try:
            for name in self.lock_names:
                self.descriptors.append(lock_file(self.registry / (name + ".lock")))
            self.check()
            yield self
        finally:
            for descriptor in reversed(self.descriptors):
                os.close(descriptor)
            self.descriptors = []

    def pair(self):
        self.check()
        return [None if path is None else read(path, FILE_LIMIT) for path in self.paths]

    def modes(self):
        self.check()
        result = []
        for path in self.paths:
            mode = (
                None
                if path is None or not os.path.lexists(path)
                else stat.S_IMODE(path.lstat().st_mode)
            )
            need(
                mode is None or mode <= 0o777,
                "special transaction output permission bits",
            )
            result.append(mode)
        return result

    def validate(self, record):
        self.check()
        need(
            isinstance(record, dict) and set(record) == FIELDS,
            "closed transaction reservation",
        )
        need(
            type(record["version"]) is int
            and record["version"] == 1
            and record["protocol"] == "dependencies-state-v1"
            and record["operation"] == self.OPERATION,
            "transaction protocol/operation",
        )
        need(
            isinstance(record["rootIdentity"], list)
            and len(record["rootIdentity"]) == 2
            and all(type(value) is int for value in record["rootIdentity"]),
            "transaction root identity types",
        )
        need(
            record["rootIdentity"] == self.root_id
            and record["selectors"] == self.bindings,
            "transaction current root/selector binding",
        )
        need(
            isinstance(record["nonce"], str)
            and len(record["nonce"]) == 64
            and all(c in "0123456789abcdef" for c in record["nonce"]),
            "transaction nonce",
        )
        for label in ("old", "new"):
            need(
                isinstance(record[label], list) and len(record[label]) == 2,
                "transaction pair shape",
            )
            values = list(map(decoded, record[label]))
            modes = record[label + "Modes"]
            need(isinstance(modes, list) and len(modes) == 2, "transaction mode shape")
            for index, (raw, mode) in enumerate(zip(values, modes)):
                need(
                    (raw is None and mode is None)
                    or (raw is not None and type(mode) is int and 0 <= mode <= 0o777),
                    "transaction bytes/mode binding",
                )
                need(
                    self.paths[index] is not None or raw is None,
                    "null selector cannot publish native file",
                )
        need(record["new"][1] is not None, "capture publication requires portable lock")
        return record

    def stages(self, record):
        return [parent / (PREFIX + record["nonce"]) for parent in self.parents]

    def stage_for(self, record, index):
        path = self.paths[index]
        need(path is not None, "null transaction output")
        parent_id = identity(path.parent)
        return self.stages(record)[self.parent_ids.index(parent_id)]

    def journal(self, record, phase):
        return {"reservation": record, "phase": phase}

    def stage_contents(self, record):
        for parent_id, stage in zip(self.parent_ids, self.stages(record)):
            if not os.path.lexists(stage):
                continue
            facts = stage.lstat()
            need(
                stat.S_ISDIR(facts.st_mode)
                and facts.st_uid == os.getuid()
                and not stat.S_IMODE(facts.st_mode) & 0o077,
                "physical private transaction stage",
            )
            allowed = {
                "owner.json": [
                    canonical(
                        {
                            "rootIdentity": self.root_id,
                            "parentIdentity": parent_id,
                            "nonce": record["nonce"],
                        }
                    )
                ]
            }
            for index, path in enumerate(self.paths):
                if self.KEEP_PORTABLE and index == 1:
                    continue
                if path is None or identity(path.parent) != parent_id:
                    continue
                for label, field in (("new", "new"), ("restore", "old")):
                    raw = decoded(record[field][index])
                    if raw is not None:
                        allowed[label + "-" + str(index)] = [raw]
            if stage.parent == self.root:
                allowed["journal.pending"] = [
                    canonical(self.journal(record, phase))
                    for phase in ("PREPARED", "COMMITTED")
                ]
            for path in stage.iterdir():
                need(path.name in allowed, "unknown transaction stage entry")
                need(
                    read(path, control=path.name in ("owner.json", "journal.pending"))
                    in allowed[path.name],
                    "changed transaction staging bytes",
                )
                for prefix, field in (("new-", "newModes"), ("restore-", "oldModes")):
                    if path.name.startswith(prefix):
                        index = int(path.name[len(prefix) :])
                        need(
                            stat.S_IMODE(path.lstat().st_mode) == record[field][index],
                            "changed transaction staging mode",
                        )

    def foreign_control(self, record):
        self.check()
        error = ForeignControl(
            "Transaction controls changed; foreign data retained. Recovery hold: "
            + str(self.hold)
        )
        if not os.path.lexists(self.hold):
            try:
                write(
                    self.hold,
                    canonical(
                        {
                            "version": 1,
                            "protocol": "dependencies-state-v1",
                            "rootIdentity": self.root_id,
                            "nonce": record["nonce"],
                            "expectedReservationSHA256": hashlib.sha256(
                                canonical(record)
                            ).hexdigest(),
                        }
                    ),
                )
                sync(self.registry)
            except OSError as hold_error:
                error.add_note("Could not persist recovery hold: " + str(hold_error))
        raise error

    def guard_control(self, record, phase):
        self.check()
        expected = None if phase is None else canonical(self.journal(record, phase))
        try:
            valid = (
                read(self.reservation, control=True) == canonical(record)
                and read(self.root / JOURNAL, control=True) == expected
                and not os.path.lexists(self.pending)
                and not os.path.lexists(self.hold)
            )
        except (ValueError, OSError):
            self.foreign_control(record)
        if not valid:
            self.foreign_control(record)

    def guard_pending(self, record):
        self.check()
        try:
            valid = (
                read(self.pending, control=True) == canonical(record)
                and not os.path.lexists(self.reservation)
                and not os.path.lexists(self.root / JOURNAL)
                and not os.path.lexists(self.hold)
                and not any(os.path.lexists(stage) for stage in self.stages(record))
            )
        except (ValueError, OSError):
            self.foreign_control(record)
        if not valid:
            self.foreign_control(record)

    def cleanup(self, record, callback=None, phase=None):
        self.guard_control(record, phase)
        self.stage_contents(record)
        current = self.pair()
        modes = self.modes()
        need(
            any(
                current == list(map(decoded, record[label]))
                and modes == record[label + "Modes"]
                for label in ("old", "new")
            ),
            "cleanup requires complete known output pair",
        )

        def guard():
            self.guard_control(record, phase)
            self.stage_contents(record)
            need(
                self.pair() == current and self.modes() == modes,
                "outputs changed during cleanup",
            )

        for index, stage in enumerate(self.stages(record)):
            if os.path.lexists(stage):
                for path in sorted(stage.iterdir()):
                    guard()
                    path.unlink()
                    fault(callback, "cleanup-" + str(index) + "-" + path.name)
                guard()
                sync(stage)
                fault(callback, "cleanup-stage-" + str(index) + "-synced")
                guard()
                stage.rmdir()
                sync(stage.parent)
                fault(callback, "cleanup-stage-" + str(index) + "-removed")
        guard()
        (self.root / JOURNAL).unlink(missing_ok=True)
        sync(self.root)
        fault(callback, "cleanup-journal-removed")
        self.guard_control(record, None)
        need(
            self.pair() == current and self.modes() == modes,
            "outputs changed before cleanup completion",
        )
        self.reservation.unlink()
        sync(self.registry)
        fault(callback, "cleanup-reservation-removed")

    def output_allowed(self, record, index, raw, mode, phase):
        return (raw, mode) in (
            (decoded(record["old"][index]), record["oldModes"][index]),
            (decoded(record["new"][index]), record["newModes"][index]),
        )

    def recover(self, callback=None):
        self.check()
        need(
            not os.path.lexists(self.hold),
            "foreign-control recovery hold requires inspection: " + str(self.hold),
        )
        raw = read(self.reservation, control=True)
        pending = read(self.pending, control=True)
        if raw is None:
            if pending is not None:
                record = self.validate(cache.decode(pending))
                need(
                    self.pair() == list(map(decoded, record["old"]))
                    and self.modes() == record["oldModes"],
                    "foreign outputs before reservation commit",
                )
                need(
                    not any(os.path.lexists(stage) for stage in self.stages(record))
                    and not os.path.lexists(self.root / JOURNAL),
                    "unreserved project staging",
                )
                self.guard_pending(record)
                self.pending.unlink()
                sync(self.registry)
                return "aborted-before-reservation"
            need(
                not os.path.lexists(self.root / JOURNAL),
                "journal without current-root reservation",
            )
            need(
                not any(
                    path.name.startswith(PREFIX)
                    and len(path.name) == len(PREFIX) + 64
                    and all(c in "0123456789abcdef" for c in path.name[len(PREFIX) :])
                    for path in self.root.iterdir()
                ),
                "unreserved root transaction stage",
            )
            return "clean"
        record = self.validate(cache.decode(raw))
        if raw != canonical(record) or pending is not None:
            self.foreign_control(record)
        self.stage_contents(record)
        original, target = [list(map(decoded, record[name])) for name in ("old", "new")]
        current = self.pair()
        modes = self.modes()
        journal = read(self.root / JOURNAL, control=True)
        if journal is None:
            if (
                current == target
                and modes == record["newModes"]
                and not any(os.path.lexists(stage) for stage in self.stages(record))
            ):
                self.cleanup(record, callback)
                return "committed-cleanup"
            need(
                current == original and modes == record["oldModes"],
                "foreign or partial outputs without journal",
            )
            self.cleanup(record, callback)
            return "aborted-before-journal"
        journal = cache.decode(journal)
        need(
            isinstance(journal, dict)
            and set(journal) == {"reservation", "phase"}
            and journal["reservation"] == record
            and journal["phase"] in ("PREPARED", "COMMITTED"),
            "closed bound transaction journal",
        )
        self.guard_control(record, journal["phase"])
        for index, (raw, mode) in enumerate(zip(current, modes)):
            need(
                self.output_allowed(record, index, raw, mode, journal["phase"]),
                "detected foreign output change",
            )
        if journal["phase"] == "COMMITTED":
            need(
                current == target and modes == record["newModes"],
                "committed outputs changed",
            )
            self.cleanup(record, callback, phase="COMMITTED")
            return "committed-cleanup"
        if current == original and modes == record["oldModes"]:
            self.cleanup(record, callback, phase="PREPARED")
            return "restored-old-pair"
        for index, (path, raw) in enumerate(zip(self.paths, original)):
            if path is None:
                continue
            self.check()
            self.guard_control(record, "PREPARED")
            self.stage_contents(record)
            need(
                self.pair() == original[:index] + current[index:]
                and self.modes() == record["oldModes"][:index] + modes[index:],
                "detected foreign restoration output change",
            )
            if self.KEEP_PORTABLE and index == 1:
                need(
                    current[index] == original[index]
                    and modes[index] == record["oldModes"][index],
                    "bridge portable bytes changed during execution",
                )
                continue
            if raw is None:
                path.unlink(missing_ok=True)
            else:
                temporary = self.stage_for(record, index) / ("restore-" + str(index))
                if os.path.lexists(temporary):
                    need(read(temporary) == raw, "changed restoration staging bytes")
                else:
                    write(temporary, raw, record["oldModes"][index])
                fault(callback, "restore-" + str(index) + "-staged")
                self.guard_control(record, "PREPARED")
                self.stage_contents(record)
                os.replace(temporary, path)
            fault(callback, "restore-" + str(index) + "-renamed")
            self.guard_control(record, "PREPARED")
            sync(path.parent)
            fault(callback, "restore-" + str(index) + "-directory-fsync")
        need(
            self.pair() == original and self.modes() == record["oldModes"],
            "restored output verification",
        )
        self.cleanup(record, callback, phase="PREPARED")
        return "restored-old-pair"

    def publish(self, target, expected, callback=None, *, defer_commit=False):
        self.check()
        need(
            not defer_commit or self.OPERATION == "native-pin-bridge",
            "only native pin bridge may defer commit",
        )
        need(
            isinstance(target, list)
            and len(target) == 2
            and all(
                raw is None or isinstance(raw, bytes) and len(raw) <= FILE_LIMIT
                for raw in target
            ),
            "publication target bytes",
        )
        need(self.recover() == "clean", "recover and recapture before publication")
        need(self.pair() == expected, "publication inputs changed since capture")
        old_modes = self.modes()
        new_modes = [
            None
            if raw is None
            else old_modes[index]
            if old_modes[index] is not None
            else 0o644
            for index, raw in enumerate(target)
        ]
        record = {
            "version": 1,
            "protocol": "dependencies-state-v1",
            "operation": self.OPERATION,
            "rootIdentity": self.root_id,
            "nonce": secrets.token_hex(32),
            "selectors": self.bindings,
            "old": list(map(encoded, expected)),
            "new": list(map(encoded, target)),
            "oldModes": old_modes,
            "newModes": new_modes,
        }
        self.validate(record)
        for value in (
            record,
            self.journal(record, "PREPARED"),
            self.journal(record, "COMMITTED"),
        ):
            need(
                len(canonical(value)) <= LIMIT, "serialized transaction envelope bound"
            )
        need(
            not any(os.path.lexists(stage) for stage in self.stages(record)),
            "transaction stage collision",
        )
        try:
            write(self.pending, canonical(record))
            fault(callback, "reservation-file-fsync")
            self.guard_pending(record)
            os.replace(self.pending, self.reservation)
            fault(callback, "reservation-renamed")
            self.guard_control(record, None)
            sync(self.registry)
            fault(callback, "reservation-directory-fsync")
            for index, (parent_id, stage) in enumerate(
                zip(self.parent_ids, self.stages(record))
            ):
                self.guard_control(record, None)
                stage.mkdir(mode=0o700)
                fault(callback, "stage-" + str(index) + "-created")
                sync(stage.parent)
                fault(callback, "stage-" + str(index) + "-directory-fsync")
                self.guard_control(record, None)
                write(
                    stage / "owner.json",
                    canonical(
                        {
                            "rootIdentity": self.root_id,
                            "parentIdentity": parent_id,
                            "nonce": record["nonce"],
                        }
                    ),
                )
                fault(callback, "stage-" + str(index) + "-owner-written")
            for index, (path, raw) in enumerate(zip(self.paths, target)):
                self.guard_control(record, None)
                if self.KEEP_PORTABLE and index == 1:
                    continue
                if raw is not None:
                    write(
                        self.stage_for(record, index) / ("new-" + str(index)),
                        raw,
                        new_modes[index],
                    )
                fault(callback, "new-" + str(index) + "-staged")
            for stage in self.stages(record):
                sync(stage)
            temporary = self.stages(record)[0] / "journal.pending"
            self.guard_control(record, None)
            write(temporary, canonical(self.journal(record, "PREPARED")))
            fault(callback, "journal-file-fsync")
            self.guard_control(record, None)
            os.replace(temporary, self.root / JOURNAL)
            fault(callback, "journal-renamed")
            sync(self.root)
            fault(callback, "journal-directory-fsync")
            for index, (path, raw) in enumerate(zip(self.paths, target)):
                self.guard_control(record, "PREPARED")
                self.stage_contents(record)
                need(
                    self.pair() == target[:index] + expected[index:]
                    and self.modes() == new_modes[:index] + old_modes[index:],
                    "detected foreign precommit output change",
                )
                if self.KEEP_PORTABLE and index == 1:
                    need(
                        raw == expected[index] and new_modes[index] == old_modes[index],
                        "bridge portable output must remain unchanged",
                    )
                elif path is not None:
                    if raw is None:
                        path.unlink(missing_ok=True)
                    else:
                        os.replace(
                            self.stage_for(record, index) / ("new-" + str(index)), path
                        )
                    fault(callback, "output-" + str(index) + "-renamed")
                    self.guard_control(record, "PREPARED")
                    sync(path.parent)
                    fault(callback, "output-" + str(index) + "-directory-fsync")
            need(
                self.pair() == target and self.modes() == new_modes,
                "published pair verification",
            )
            self.guard_control(record, "PREPARED")
            if defer_commit:
                fault(callback, "bridge-ready")
                return record
            write(temporary, canonical(self.journal(record, "COMMITTED")))
            fault(callback, "commit-file-fsync")
            self.guard_control(record, "PREPARED")
            os.replace(temporary, self.root / JOURNAL)
            fault(callback, "commit-renamed")
            self.guard_control(record, "COMMITTED")
            sync(self.root)
            fault(callback, "commit-directory-fsync")
            self.cleanup(record, callback, phase="COMMITTED")
        except ForeignControl:
            raise
        except Exception as error:
            try:
                self.recover()
            except Exception as recovery_error:
                error.add_note(
                    "Retained transaction recovery also refused: " + str(recovery_error)
                )
            raise
