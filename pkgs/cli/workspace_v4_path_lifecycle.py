# SPDX-License-Identifier: MIT
"""Workspace4/PATH lifecycle bound to its literal native protocol."""

import dependencies_v1_cache as cache
import dependencies_v1_selectors as selectors
from dependencies_v1_lifecycle import recover_locked
import workspace_v4_path as protocol
import workspace_v4_path_capture as capture
import workspace_v4_path_ownership as ownership
from workspace_v4_path_transaction import Capture
from workspace_v4_path_transaction import Bridge
from native_path_protocols import WORKSPACE_V4_PATH
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
    cache.require(not api.sdk_mode(), "portable10 SDK-bound CLI capture is unavailable")
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
        root, raw, native, portable, current=current, native_protocol=WORKSPACE_V4_PATH
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
        native_protocol=WORKSPACE_V4_PATH,
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
        native_protocol=WORKSPACE_V4_PATH,
    )


def uninstall(
    root, hash_tree, state, native=selectors.DEFAULT, portable=selectors.DEFAULT
):
    return shared.uninstall(
        root, hash_tree, state, native, portable, native_protocol=WORKSPACE_V4_PATH
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
        native_protocol=WORKSPACE_V4_PATH,
    )
