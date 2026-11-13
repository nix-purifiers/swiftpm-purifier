# SPDX-License-Identifier: MIT
"""Typed root9 publication and PREPARED pin projection on the shared locks.

The registry and lock key deliberately match the older protocols. Routing a
reservation never grants mutation authority; each operation validates its
own complete portable9/native3 payload and current selector bindings.
"""

import hashlib
import stat

import dependencies_v1_selectors as selectors
from dependencies_v1_bridge import NativePinBridge
from dependencies_v1_transaction import Transaction, decoded, identity, need
import dependencies_v3 as protocol


class Capture(Transaction):
    PROTOCOL = "dependencies-state-v3"
    OPERATION = "capture-update-v3"

    def validate(self, record):
        record = super().validate(record)
        protocol.validate_raw(
            decoded(record["new"][1]), decoded(record["new"][0]), self.root
        )
        return record


def binding(selection, *, logical=False):
    need(selection.path is not None, "portable9 selectors cannot be null")
    if logical:
        return {
            "role": "logical",
            "path": "Package.resolved",
            "parentIdentity": identity(selection.path.parent),
        }
    need(
        selection.raw is not None and selection.file_identity is not None,
        "portable9 selected input missing",
    )
    mode = stat.S_IMODE(selection.path.lstat().st_mode)
    need(mode <= 0o777, "portable9 selected input special mode")
    return {
        "role": "fixed",
        "path": str(selection.path),
        "parentIdentity": list(selection.parent_identity),
        "fileIdentity": list(selection.file_identity),
        "sha256": hashlib.sha256(selection.raw).hexdigest(),
        "mode": mode,
    }


class Bridge(NativePinBridge):
    PROTOCOL = "dependencies-state-v3"
    OPERATION = "native-pin-bridge-v3"
    EXTRA_FIELDS = {"selectedNative", "selectedPortable"}

    def __init__(
        self, root, state, native=selectors.DEFAULT, portable=selectors.DEFAULT
    ):
        self.native_selection = selectors.select(root, native)
        self.portable_selection = selectors.select(root, portable, portable=True)
        need(
            self.native_selection.path is not None,
            "portable9 native selector cannot be null",
        )
        super().__init__(root, state, portable=portable)

    def prepare_record(self, record):
        self.native_selection.verify()
        self.portable_selection.verify()
        need(self.native_selection.raw is not None, "portable9 selected pins missing")
        need(
            decoded(record["new"][0]) == self.native_selection.raw,
            "portable9 bridge target differs from selected pins",
        )
        return {
            **record,
            "selectedNative": binding(
                self.native_selection,
                logical=self.native_selection.path == self.root / "Package.resolved",
            ),
            "selectedPortable": binding(self.portable_selection),
        }

    def validate(self, record):
        record = super().validate(record)
        native = selectors.select(self.root, self.native_selection.path)
        portable = selectors.select(
            self.root, self.portable_selection.path, portable=True
        )
        expected_native = binding(
            native, logical=native.path == self.root / "Package.resolved"
        )
        need(
            record["selectedNative"] == expected_native,
            "portable9 native selector binding changed",
        )
        need(
            record["selectedPortable"] == binding(portable),
            "portable9 portable selector binding changed",
        )
        if expected_native["role"] != "logical":
            need(
                native.raw == decoded(record["new"][0]),
                "portable9 external pins changed",
            )
        need(
            portable.raw == decoded(record["old"][1]), "portable9 selected lock changed"
        )
        protocol.validate_raw(
            decoded(record["new"][1]), decoded(record["new"][0]), self.root
        )
        return record

    def output_allowed(self, record, index, raw, mode, phase):
        self.validate(record)
        if super().output_allowed(record, index, raw, mode, phase):
            return True
        if phase == "PREPARED" and index == 0 and raw is None and mode is None:
            # Only absence is admitted here; foreign present bytes are retained.
            return True
        self.foreign_control(record)


def recover_locked(guard, operation):
    """Select a typed validator; the routing hint grants no mutation authority."""
    guard.check()
    if operation == Capture.OPERATION:
        owner = Capture(guard.root, guard.registry, guard.paths[0], guard.paths[1])
    elif operation == Bridge.OPERATION:
        owner = Bridge(guard.root, guard.registry, guard.paths[0], guard.paths[1])
    elif operation == "owned-install-v3":
        from dependencies_v3_install_transaction import Installation

        owner = Installation(guard.root, guard.registry, guard.paths[0], guard.paths[1])
    else:
        need(False, "unknown dependencies-state-v3 transaction operation")
    owner.descriptors = guard.descriptors
    return owner.recover()
