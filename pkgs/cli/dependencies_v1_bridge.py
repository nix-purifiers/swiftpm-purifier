# SPDX-License-Identifier: MIT
"""Prepared-only logical pin projection; recovery always restores prior bytes."""

import dependencies_v1_cache as cache
import dependencies_v1_layout as layout
import dependencies_v1_selectors as selectors
from dependencies_v1_transaction import Transaction, JOURNAL, read, need, identity


class NativePinBridge(Transaction):
    OPERATION = "native-pin-bridge"
    KEEP_PORTABLE = True
    ALLOW_DEFER_COMMIT = True

    def __init__(self, root, registry, portable=selectors.DEFAULT):
        super().__init__(root, registry, portable=portable)
        # Only the logical root pin role changes. The selected portable file
        # remains guarded/locked but its parent needs no staging or fsync.
        self.parents = [self.root]
        self.parent_ids = [self.root_id]

    def check(self):
        super().check()
        need(
            identity(layout.canonical(self.paths[1].parent))
            == self.bindings[1]["parentIdentity"],
            "bridge portable parent changed",
        )

    def validate(self, record):
        record = super().validate(record)
        need(
            record["old"][1] == record["new"][1]
            and record["oldModes"][1] == record["newModes"][1],
            "native bridge cannot publish portable bytes",
        )
        return record

    def recover(self, callback=None):
        self.check()
        raw = read(self.reservation, control=True)
        if raw is not None:
            record = self.validate(cache.decode(raw))
            journal = read(self.root / JOURNAL, control=True)
            if journal is not None:
                envelope = cache.decode(journal)
                if isinstance(envelope, dict) and envelope.get("phase") == "COMMITTED":
                    self.foreign_control(record)
        return super().recover(callback)
