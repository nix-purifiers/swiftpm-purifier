# SPDX-License-Identifier: MIT
"""Closed portable 10 / workspace-state 4 with path references.

Sharing path-record validation does not admit the location-reference protocol
or confer ownership authority. File and transaction dispatch remain separate.
"""

import dependencies_v3 as path_records
from native_path_protocols import WORKSPACE_V4_PATH

FORMAT = WORKSPACE_V4_PATH.portable_format
NATIVE_VERSION = WORKSPACE_V4_PATH.native_version

# These helpers describe the shared, closed path-record/pins-1 data model;
# none selects native state files, journal protocols or ownership authority.
Reject = path_records.Reject
require = path_records.require
LIMIT = path_records.LIMIT
TOP = path_records.TOP
PIN_FIELDS = path_records.PIN_FIELDS
safe_name = path_records.safe_name
reference = path_records.reference
checkout = path_records.checkout
checkout_subpath = path_records.checkout_subpath
local_directory = path_records.local_directory
local_records = path_records.local_records
pins_index = path_records.pins_index
manifest_hash = path_records.manifest_hash
canonical = path_records.canonical
content_hash = path_records.content_hash


def native_index(value, root=None, *, allow_edited=False, physical_locals=True):
    return path_records.native_index(
        value,
        root,
        allow_edited=allow_edited,
        physical_locals=physical_locals,
        protocol=WORKSPACE_V4_PATH,
    )


def edited_state(value, root=None, *, physical_locals=True):
    return path_records.edited_state(
        value, root, physical_locals=physical_locals, protocol=WORKSPACE_V4_PATH
    )


def checkout_records(lock, root, *, physical_locals=True):
    return path_records.checkout_records(
        lock, root, physical_locals=physical_locals, protocol=WORKSPACE_V4_PATH
    )


def validate_lock(lock, selected_pins, root, *, current=True):
    return path_records.validate_lock(
        lock, selected_pins, root, current=current, protocol=WORKSPACE_V4_PATH
    )


def validate_raw(raw, selected_raw, root):
    return path_records.validate_raw(
        raw, selected_raw, root, protocol=WORKSPACE_V4_PATH
    )
