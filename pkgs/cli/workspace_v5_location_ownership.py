# SPDX-License-Identifier: MIT
"""Closed format-11 ownership on its fixed workspace-state basename."""

import os

import dependencies_v1_cache as cache
import dependencies_v1_layout as layout
import dependencies_v3_ownership as shared
import workspace_v5_location as protocol
from dependencies_v1_transaction import decoded
from native_protocols import WORKSPACE_V5_LOCATION

MARKER = WORKSPACE_V5_LOCATION.ownership_marker
MARKER_LIMIT = shared.MARKER_LIMIT
FIELDS = shared.FIELDS


def validate_native_layout(root):
    root = shared.validate_native_layout(root, native_protocol=WORKSPACE_V5_LOCATION)
    for relative in (
        ".build/swiftpm-purifier/links.json",
        ".build/swiftpm-purifier/preworkspace-owned.json",
    ):
        cache.require(
            not os.path.lexists(root / relative), "foreign legacy ownership marker"
        )
    return root


def native_state(root):
    return shared.native_state(root, native_protocol=WORKSPACE_V5_LOCATION)


def original_state(value, root):
    return shared.original_state(value, root, native_protocol=WORKSPACE_V5_LOCATION)


def projection(lock, root):
    return shared.projection(lock, root, native_protocol=WORKSPACE_V5_LOCATION)


def projection_matches(raw, expected, root):
    return shared.projection_matches(
        raw, expected, root, native_protocol=WORKSPACE_V5_LOCATION
    )


def validate_marker(value, root):
    return shared.validate_marker(value, root, native_protocol=WORKSPACE_V5_LOCATION)


def existing(root, hash_tree):
    return shared.existing(root, hash_tree, native_protocol=WORKSPACE_V5_LOCATION)


def prepare_tree(root, lock, selected_raw, sources, destination, original, hash_tree):
    return shared.prepare_tree(
        root,
        lock,
        selected_raw,
        sources,
        destination,
        original,
        hash_tree,
        native_protocol=WORKSPACE_V5_LOCATION,
    )


def require_same_family(root, hash_tree):
    """Refuse foreign caches before any new-protocol publication or staging."""
    validate_native_layout(root)
    packages = layout.canonical(root) / "Packages"
    if os.path.lexists(packages):
        from native_protocols import DEPENDENCIES_V3, WORKSPACE_V4_PATH

        cache.directory(packages)
        cache.require(
            not any(
                os.path.lexists(packages / descriptor.ownership_marker)
                for descriptor in (DEPENDENCIES_V3, WORKSPACE_V4_PATH)
            ),
            "foreign PATH ownership marker",
        )
        cache.require(
            os.path.lexists(packages / MARKER), "missing LOCATION ownership marker"
        )
        cache.require(callable(hash_tree), "owned source NAR verifier required")
    owner = existing(root, hash_tree)
    if owner is None:
        original_state(native_state(root), root)
    return owner


def family_boundary(root):
    """Read-only namespace guard for paired-file recovery, not tree authority.

    A recorded capture/bridge can restore only its two selected files. This
    guard does not certify source NARs or authorize deleting package trees.
    Fresh capture additionally calls require_same_family with a NAR verifier.
    """
    root = validate_native_layout(root)
    packages = root / "Packages"
    if not os.path.lexists(packages):
        original_state(native_state(root), root)
        return None
    cache.directory(packages)
    raw = cache.regular_bytes(packages, MARKER, MARKER_LIMIT)
    marker = validate_marker(cache.decode(raw), root)
    cache.require(
        raw == protocol.canonical(marker) + b"\n", "ownership marker spelling"
    )
    names = set(marker["portable"]["pins"])
    cache.require(
        {p.name for p in packages.iterdir()} == names | {MARKER}, "foreign package set"
    )
    state = native_state(root)
    cache.require(state is not None, "owned workspace state missing")
    projection_matches(decoded(state["bytes"]), marker["projection"], root)
    return marker
