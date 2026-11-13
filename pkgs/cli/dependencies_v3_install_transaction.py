# SPDX-License-Identifier: MIT
"""Typed owned-install-v3 on the shared fixed-role durable committer."""

import hashlib
import os
from pathlib import Path

import dependencies_v1_cache as cache
import dependencies_v1_selectors as selectors
import dependencies_v3 as protocol
import dependencies_v3_ownership as ownership
import legacy_runtime
from dependencies_v1_install_transaction import (
    Installation as BaseInstallation,
    snapshot,
)
from dependencies_v1_transaction import (
    Transaction,
    JOURNAL,
    PREFIX,
    LIMIT,
    identity,
    need,
    decoded,
    read,
)
from dependencies_v3_transaction import binding


def transition_basis(root, nonce):
    """Exclude only this operation's fixed controls from caller source currency."""
    return legacy_runtime.source_fingerprint(
        root, ignored_root=(JOURNAL, PREFIX + nonce)
    )


def configuration_basis(root):
    names = ("HOME", "XDG_CONFIG_HOME", "SWIFTPM_MIRROR_CONFIG", "SWIFTPM_BUILD_DIR")
    environment = {name: os.environ.get(name) for name in names}
    home = Path.home()
    paths = {
        home / ".swiftpm/config",
        home / ".swiftpm/configuration/mirrors.json",
        home / ".swiftpm/configuration/registries.json",
    }
    override = environment["SWIFTPM_MIRROR_CONFIG"]
    if override:
        paths.add(Path(override) if Path(override).is_absolute() else root / override)
    files = {}
    for path in sorted(paths):
        raw = (
            cache.regular_bytes(path.parent, path.name)
            if os.path.lexists(path)
            else None
        )
        files[str(path)] = None if raw is None else hashlib.sha256(raw).hexdigest()
    return {"environment": environment, "files": files}


def marker_snapshot(marker, snapshot):
    """Bind complete typed ownership metadata to the Packages byte inventory."""
    if marker is None:
        need(snapshot is None, "unowned Packages role is not admissible")
        return
    need(
        snapshot is not None and snapshot["kind"] == "directory",
        "owned Packages snapshot missing",
    )
    entries = snapshot["entries"]
    raw = protocol.canonical(marker) + b"\n"
    entry = entries.get(ownership.MARKER)
    need(
        entry
        == {
            "kind": "regular",
            "mode": 0o600,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
        },
        "owned marker/snapshot binding",
    )
    names = {pin["name"] for pin in marker["portable"]["pins"].values()}
    need(
        {Path(name).parts[0] for name in entries if name != "."}
        == names | {ownership.MARKER},
        "owned snapshot package closure",
    )
    for name in names:
        need(
            entries.get(name, {}).get("kind") == "directory",
            "owned snapshot package directory",
        )
        need(name + "/Package.swift" in entries, "owned snapshot package manifest")
    need(
        not any(".git" in Path(name).parts for name in entries),
        "owned snapshot VCS metadata",
    )


def state_snapshot_matches(snapshot, raw, *, build_directory=False):
    if build_directory:
        need(
            snapshot is not None and snapshot["kind"] == "directory",
            "new build snapshot missing",
        )
        entries = snapshot["entries"]
        need(set(entries) == {".", "dependencies-state.json"}, "new build role closure")
        need(
            entries["dependencies-state.json"]
            == {
                "kind": "regular",
                "mode": 0o600,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            },
            "new build state binding",
        )
    else:
        need(
            snapshot is not None
            and snapshot["kind"] == "regular"
            and decoded(snapshot["bytes"]) == raw
            and snapshot["mode"] == 0o600,
            "owned state snapshot binding",
        )


class Installation(BaseInstallation):
    PROTOCOL = "dependencies-state-v3"
    INSTALL_OPERATION = "owned-install-v3"
    INSTALL_EXTRA_FIELDS = {
        "ownership",
        "selectedNative",
        "selectedPortable",
        "transitionBasis",
        "configurationBasis",
    }

    def __init__(
        self,
        root,
        registry,
        native=selectors.DEFAULT,
        portable=selectors.DEFAULT,
        *,
        owners=None,
        hash_tree=None,
    ):
        # Fixed mutation roles come from BaseInstallation, but selected input
        # locks/bindings must also include external native and portable files.
        Transaction.__init__(self, root, registry, native, portable)
        self.native_selection = selectors.select(self.root, native)
        self.portable_selection = selectors.select(self.root, portable, portable=True)
        self.owners = owners
        self.hash_tree = hash_tree
        self.planned_new = None

    def recover(self, callback=None):
        try:
            return super().recover(callback)
        except ValueError as error:
            # A matching private typed reservation identifies only the hold
            # key. Failed full validation never authorizes role mutation.
            try:
                raw = read(self.reservation, control=True)
                if raw is None:
                    raw = read(self.pending, control=True)
                record = cache.decode(raw) if raw is not None else None
            except (OSError, ValueError, TypeError):
                raise error
            if (
                isinstance(record, dict)
                and record.get("protocol") == self.PROTOCOL
                and record.get("operation") == self.INSTALL_OPERATION
                and record.get("rootIdentity") == self.root_id
                and isinstance(record.get("nonce"), str)
                and len(record["nonce"]) == 64
                and all(c in "0123456789abcdef" for c in record["nonce"])
            ):
                try:
                    self.foreign_control(record)
                except cache.Reject as held:
                    held.add_note(str(error))
                    raise held from error
            raise

    def publish(self, sources, expected, callback=None):
        self.check()
        need(
            callable(self.hash_tree),
            "fresh install requires bounded source NAR verification",
        )
        need(
            isinstance(self.owners, list) and len(self.owners) == 2,
            "typed old/new ownership required",
        )
        need(
            isinstance(sources, list) and len(sources) == 2, "typed installation roles"
        )
        self.planned_new = [
            None if path is None else snapshot(Path(path)) for path in sources
        ]
        need(
            ownership.existing(self.root, self.hash_tree) == self.owners[0],
            "old owned set changed",
        )
        new = self.owners[1]
        if new is not None:
            need(sources[0] is not None, "new owned staging missing")
            for pin in new["portable"]["pins"].values():
                path = Path(sources[0]) / pin["name"]
                cache.source_tree(path)
                need(
                    not os.path.lexists(path / ".git"), "new owned staging VCS metadata"
                )
                need(
                    self.hash_tree(path) == pin["hash"],
                    "new staged source NAR mismatch",
                )
        need(
            self.planned_new
            == [None if path is None else snapshot(Path(path)) for path in sources],
            "new staging changed during NAR verification",
        )
        return super().publish(sources, expected, callback)

    def prepare_install_record(self, record):
        need(
            isinstance(self.owners, list) and len(self.owners) == 2,
            "typed old/new ownership required",
        )
        need(
            record["new"] == self.planned_new,
            "new owned sources changed after NAR checks",
        )
        self.native_selection.verify()
        self.portable_selection.verify()
        return {
            **record,
            "ownership": self.owners,
            "selectedNative": binding(self.native_selection),
            "selectedPortable": binding(self.portable_selection),
            "transitionBasis": transition_basis(self.root, record["nonce"]),
            "configurationBasis": configuration_basis(self.root),
        }

    def validate(self, record):
        record = super().validate(record)
        need(
            isinstance(record["ownership"], list) and len(record["ownership"]) == 2,
            "typed ownership pair",
        )
        native = selectors.select(self.root, self.native_selection.path)
        portable = selectors.select(
            self.root, self.portable_selection.path, portable=True
        )
        need(
            record["selectedNative"] == binding(native),
            "owned install selected native changed",
        )
        need(
            record["selectedPortable"] == binding(portable),
            "owned install selected portable changed",
        )
        need(
            record["transitionBasis"] == transition_basis(self.root, record["nonce"]),
            "owned install source changed after reservation",
        )
        need(
            record["configurationBasis"] == configuration_basis(self.root),
            "owned install configuration changed",
        )
        old, new = record["ownership"]
        need(old is not None or new is not None, "empty ownership transition")
        for label, marker in zip(("old", "new"), (old, new)):
            if marker is not None:
                ownership.validate_marker(marker, self.root)
            marker_snapshot(marker, record[label][0])
        if new is not None:
            protocol.validate_raw(portable.raw, native.raw, self.root)
            need(
                cache.decode(portable.raw) == new["portable"]
                and native.raw == decoded(new["nativePins"]),
                "new owner differs from selected graph",
            )
            state_snapshot_matches(
                record["new"][1],
                protocol.canonical(new["projection"]),
                build_directory=record["buildIdentity"] is None,
            )
        if old is None:
            need(
                new["originalState"] == record["old"][1],
                "first install original state binding",
            )
        else:
            # Native tools may reorder dependency arrays. Bind exact old bytes
            # to the snapshot while comparing native semantics by identity.
            state = record["old"][1]
            need(
                state is not None and state["kind"] == "regular",
                "old owned native state missing",
            )
            ownership.projection_matches(
                decoded(state["bytes"]), old["projection"], self.root
            )
            if new is not None:
                need(
                    old["originalState"] == new["originalState"],
                    "replacement changed original backup",
                )
            else:
                need(
                    record["new"][1] == old["originalState"],
                    "uninstall changed original backup",
                )
        need(
            len(protocol.canonical(record)) <= LIMIT,
            "typed install reservation byte bound",
        )
        for phase in ("PREPARED", "COMMITTED"):
            need(
                len(protocol.canonical(self.journal(record, phase))) <= LIMIT,
                "typed install journal byte bound",
            )
        return record
