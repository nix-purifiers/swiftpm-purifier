# SPDX-License-Identifier: MIT
"""Typed workspace4/PATH pin publication on the existing shared root locks."""

import dependencies_v1_cache as cache
import dependencies_v1_selectors as selectors
from dependencies_v1_transaction import read, need
import dependencies_v3_transaction as shared
import workspace_v4_path as codec
import workspace_v4_path_ownership as ownership
from native_path_protocols import WORKSPACE_V4_PATH


class FamilyBoundary:
    def validate(self, record):
        record = super().validate(record)
        ownership.family_boundary(self.root)
        return record

    def guard_control(self, record, phase):
        try:
            ownership.family_boundary(self.root)
        except (ValueError, OSError):
            self.foreign_control(record)
        return super().guard_control(record, phase)

    def guard_pending(self, record):
        try:
            ownership.family_boundary(self.root)
        except (ValueError, OSError):
            self.foreign_control(record)
        return super().guard_pending(record)

    def hold_matching_root(self):
        """A bounded private control can identify a HOLD key, never data roles."""
        try:
            raw = read(self.reservation, control=True)
            if raw is None:
                raw = read(self.pending, control=True)
            value = cache.decode(raw) if raw is not None else None
        except (OSError, ValueError, TypeError):
            return
        if (
            isinstance(value, dict)
            and value.get("rootIdentity") == self.root_id
            and isinstance(value.get("nonce"), str)
            and len(value["nonce"]) == 64
            and all(c in "0123456789abcdef" for c in value["nonce"])
        ):
            self.foreign_control(value)

    def recover(self, callback=None):
        try:
            return super().recover(callback)
        except (ValueError, OSError) as error:
            try:
                self.hold_matching_root()
            except cache.Reject as held:
                held.add_note(str(error))
                raise held from error
            raise


class Capture(FamilyBoundary, shared.Capture):
    PROTOCOL = WORKSPACE_V4_PATH.name
    OPERATION = "capture-update-workspace-v4-path"
    CODEC = codec

    def __init__(
        self,
        root,
        state,
        native=selectors.DEFAULT,
        portable=selectors.DEFAULT,
        *,
        hash_tree=None,
    ):
        super().__init__(root, state, native, portable)
        self.hash_tree = hash_tree

    def publish(self, target, expected, callback=None, *, defer_commit=False):
        self.check()
        from dependencies_v1_lifecycle import recover_locked as recover_recorded

        need(
            recover_recorded(self) == "clean",
            "recover the recorded operation and recapture before publication",
        )
        ownership.require_same_family(self.root, self.hash_tree)
        return super().publish(target, expected, callback, defer_commit=defer_commit)


class Bridge(FamilyBoundary, shared.Bridge):
    PROTOCOL = WORKSPACE_V4_PATH.name
    OPERATION = "native-pin-bridge-workspace-v4-path"
    CODEC = codec


def recover_locked(guard, operation):
    """Use only the recorded typed operation; caller locks are borrowed."""
    guard.check()
    if operation == Capture.OPERATION:
        owner = Capture(guard.root, guard.registry, guard.paths[0], guard.paths[1])
    elif operation == Bridge.OPERATION:
        owner = Bridge(guard.root, guard.registry, guard.paths[0], guard.paths[1])
    elif operation == "owned-install-workspace-v4-path":
        from workspace_v4_path_install_transaction import Installation

        owner = Installation(guard.root, guard.registry, guard.paths[0], guard.paths[1])
    else:
        owner = Capture(guard.root, guard.registry, guard.paths[0], guard.paths[1])
        owner.descriptors = guard.descriptors
        owner.hold_matching_root()
        need(False, "unknown workspace4/path transaction operation")
    owner.descriptors = guard.descriptors
    return owner.recover()
