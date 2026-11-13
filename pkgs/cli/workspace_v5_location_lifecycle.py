# SPDX-License-Identifier: MIT
"""Workspace5/LOCATION lifecycle bound to its literal native protocol."""

import dependencies_v1_cache as cache
import dependencies_v1_selectors as selectors
from dependencies_v1_lifecycle import recover_locked
import workspace_v5_location as protocol
import workspace_v5_location_capture as capture
import workspace_v5_location_ownership as ownership
from workspace_v5_location_transaction import Capture
from workspace_v5_location_transaction import Bridge
from native_protocols import WORKSPACE_V5_LOCATION
import dependencies_v3_lifecycle as shared


def capture_and_publish(
    root,
    temporary,
    api,
    state,
    update=None,
    native=selectors.DEFAULT,
    portable=selectors.DEFAULT,
):
    cache.require(not api.sdk_mode(), "portable11 SDK-bound CLI capture is unavailable")
    with Capture(
        root, state, native, portable, hash_tree=api.dependencies_v1_hash
    ).locked() as guard:
        recover_locked(guard)
        ownership.require_same_family(root, api.dependencies_v1_hash)
        expected = guard.pair()
        lock, pins, session = capture.prepare(
            root, temporary, api, update, native=native, portable=portable
        )
        guard.publish([pins, protocol.canonical(lock) + b"\n"], expected)
        session.receipt.update(publication="committed")
        session.save()
        return lock, session


def validate_source(
    root, raw, native=selectors.DEFAULT, portable=selectors.DEFAULT, *, current=True
):
    return shared.validate_source(
        root,
        raw,
        native,
        portable,
        current=current,
        native_protocol=WORKSPACE_V5_LOCATION,
    )


def runtime(root, lock, selected_raw, sources, work, api, hash_tree, evidence_parent):
    return shared.runtime(
        root,
        lock,
        selected_raw,
        sources,
        work,
        api,
        hash_tree,
        evidence_parent,
        native_protocol=WORKSPACE_V5_LOCATION,
    )


def install(
    root,
    raw,
    sources,
    api,
    hash_tree,
    state,
    native=selectors.DEFAULT,
    portable=selectors.DEFAULT,
):
    return shared.install(
        root,
        raw,
        sources,
        api,
        hash_tree,
        state,
        native,
        portable,
        native_protocol=WORKSPACE_V5_LOCATION,
    )


def uninstall(
    root, hash_tree, state, native=selectors.DEFAULT, portable=selectors.DEFAULT
):
    return shared.uninstall(
        root, hash_tree, state, native, portable, native_protocol=WORKSPACE_V5_LOCATION
    )


def run_native(
    root,
    raw,
    command,
    hash_tree,
    state,
    native=selectors.DEFAULT,
    portable=selectors.DEFAULT,
    *,
    api,
    child_started=None,
):
    return shared.run_native(
        root,
        raw,
        command,
        hash_tree,
        state,
        native,
        portable,
        api=api,
        child_started=child_started,
        native_protocol=WORKSPACE_V5_LOCATION,
    )
