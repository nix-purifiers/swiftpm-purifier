# SPDX-License-Identifier: MIT
"""Locked host lifecycle for the measured dependencies-state-v1 protocol."""

import base64
import os
from pathlib import Path
import tempfile
import subprocess

import dependencies_v1_cache as cache
import dependencies_v1_host as host
import dependencies_v1_layout as layout
import dependencies_v1_ownership as ownership
import dependencies_v1_selectors as selectors
from dependencies_v1_install_transaction import Installation, snapshot
from dependencies_v1_bridge import NativePinBridge
from dependencies_v1_transaction import (
    Transaction,
    canonical,
    read,
    digest,
    identity,
    JOURNAL,
    PREFIX,
)


def registry(root, create=True):
    """Use private external state; never create a registry in caller sources."""
    root = layout.canonical(root)
    state = os.environ.get("XDG_STATE_HOME")
    parent = Path(state) if state else Path.home() / ".local/state"
    destination = layout.canonical(parent / "swiftpm-purifier/dependencies-v1")
    cache.require(
        not destination.is_relative_to(root), "transaction registry must be external"
    )
    if create:
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    return destination


def recovery_needed(root, state):
    root = layout.canonical(root)
    if os.path.lexists(root / JOURNAL) or any(
        name.name.startswith(PREFIX)
        and len(name.name) == len(PREFIX) + 64
        and all(c in "0123456789abcdef" for c in name.name[len(PREFIX) :])
        for name in root.iterdir()
    ):
        return True
    if not os.path.lexists(state):
        return False
    cache.directory(state)
    key = digest(["root", identity(root)])
    return any(
        os.path.lexists(state / (key + suffix))
        for suffix in (".json", ".pending", ".hold")
    )


def recovery_hint(root):
    """Read-only discovery must not impose format8 policy on other projects.

    This hint grants no path authority. A positive result is followed by the
    canonical/private registry and full reservation checks before recovery.
    """
    root = Path(root)
    if not root.exists():
        return False
    if os.path.lexists(root / JOURNAL) or any(
        p.name.startswith(PREFIX)
        and len(p.name) == len(PREFIX) + 64
        and all(c in "0123456789abcdef" for c in p.name[len(PREFIX) :])
        for p in root.iterdir()
    ):
        return True
    parent = (
        Path(os.environ["XDG_STATE_HOME"])
        if os.environ.get("XDG_STATE_HOME")
        else Path.home() / ".local/state"
    )
    state = parent / "swiftpm-purifier/dependencies-v1"
    facts = root.stat()
    key = digest(["root", [facts.st_dev, facts.st_ino]])
    return any(
        os.path.lexists(state / (key + suffix))
        for suffix in (".json", ".pending", ".hold")
    )


def installation(guard):
    """Borrow the already-held root lock, without acquiring a second flock."""
    guard.check()
    result = Installation(guard.root, guard.registry)
    result.descriptors = guard.descriptors
    return result


def recover_locked(guard):
    """Route only by a bounded private reservation, then validate its full type."""
    guard.check()
    raw = read(guard.reservation, control=True)
    if raw is None:
        raw = read(guard.pending, control=True)
    if raw is not None:
        value = cache.decode(raw)
        cache.require(isinstance(value, dict), "transaction reservation type")
        if value.get("protocol") == "workspace-state-v5-location-pins1":
            import workspace_v5_location_transaction

            return workspace_v5_location_transaction.recover_locked(
                guard, value.get("operation")
            )
        if value.get("protocol") == "workspace-state-v4-path":
            import workspace_v4_path_transaction

            return workspace_v4_path_transaction.recover_locked(
                guard, value.get("operation")
            )
        if value.get("protocol") == "dependencies-state-v3":
            import dependencies_v3_transaction

            return dependencies_v3_transaction.recover_locked(
                guard, value.get("operation")
            )
        if value.get("operation") == "owned-install":
            return installation(guard).recover()
        if value.get("operation") == "native-pin-bridge":
            return bridge(guard).recover()
    return guard.recover()


def bridge(guard):
    guard.check()
    result = NativePinBridge(guard.root, guard.registry, portable=guard.paths[1])
    result.descriptors = guard.descriptors
    return result


def run_native(
    root,
    raw,
    command,
    hash_tree,
    state,
    native=selectors.DEFAULT,
    portable=selectors.DEFAULT,
):
    """Hold the root lock and recoverably project independently selected pins."""
    root = layout.canonical(root)
    with Transaction(root, state, native, portable).locked() as guard:
        recover_locked(guard)
        host.validate_source(root, raw, native, portable)
        cache.require(
            ownership.existing(root, hash_tree) is not None,
            "native execution requires verified owned dependencies",
        )
        pin, output, logical = selectors.pair(root, native, portable)
        projection = bridge(guard)
        projection.publish([pin.raw, output.raw], projection.pair(), defer_commit=True)
        try:
            status = subprocess.run(command, cwd=root, check=False).returncode
        finally:
            projection.recover()
        if pin.path != logical.path:
            pin.verify()
        output.verify()
        ownership.existing(root, hash_tree)
        host.validate_source(root, raw, native, portable)
        return status


def recover(root, state, native=selectors.DEFAULT, portable=selectors.DEFAULT):
    with Transaction(root, state, native, portable).locked() as guard:
        return recover_locked(guard)


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
    cache.require(not api.sdk_mode(), "format8 has no SDK-bound CLI lifecycle")
    root = layout.canonical(root)
    with Transaction(root, state, native, portable).locked() as guard:
        recover_locked(guard)
        selected = selectors.pair(root, native, portable)
        lock = host.validate_source(root, raw, native, portable)
        basis = ownership.installation_basis(root, hash_tree)
        if basis["owned"] is None and basis["originalState"] is not None:
            cache.validate(root, os.environ)
        with tempfile.TemporaryDirectory(
            prefix="host-install-", dir=state
        ) as temporary:
            work = Path(temporary)
            runtime_work = work / "runtime"
            runtime_work.mkdir()
            evidence = host.runtime(
                root,
                raw,
                sources,
                runtime_work,
                api,
                hash_tree,
                native,
                portable,
                evidence_parent=state,
            )
            packages = work / "Packages"
            marker = ownership.prepare_tree(
                root, lock, sources, packages, basis["originalState"], hash_tree
            )
            new_state = work / "native-state"
            new_state.write_bytes(canonical(marker["projection"]))
            if basis["buildIdentity"] is None:
                build = work / "new-build"
                build.mkdir()
                new_state.rename(build / "dependencies-state.json")
                new_state = build
            cache.require(
                ownership.installation_basis(root, hash_tree) == basis,
                "caller ownership basis changed during preparation",
            )
            host.validate_source(root, raw, native, portable)
            for selection in selected:
                selection.verify()
            committer = installation(guard)
            paths = [
                root / "Packages",
                (
                    root / ".build/dependencies-state.json"
                    if basis["buildIdentity"] is not None
                    else root / ".build"
                ),
            ]
            committer.publish([packages, new_state], [snapshot(path) for path in paths])
            ownership.existing(root, hash_tree)
            evidence.receipt.update(installation="committed")
            evidence.save()
            return evidence


def uninstall(
    root, hash_tree, state, native=selectors.DEFAULT, portable=selectors.DEFAULT
):
    root = layout.canonical(root)
    with Transaction(root, state, native, portable).locked() as guard:
        recover_locked(guard)
        basis = ownership.installation_basis(root, hash_tree)
        owned = basis["owned"]
        if owned is None:
            return False
        with tempfile.TemporaryDirectory(
            prefix="host-uninstall-", dir=state
        ) as temporary:
            restored = None
            if owned["originalState"] is not None:
                restored = Path(temporary) / "native-state"
                restored.write_bytes(
                    base64.b64decode(owned["originalState"], validate=True)
                )
            cache.require(
                ownership.installation_basis(root, hash_tree) == basis,
                "caller ownership basis changed before removal",
            )
            installation(guard).publish(
                [None, restored],
                [
                    snapshot(root / "Packages"),
                    snapshot(root / ".build/dependencies-state.json"),
                ],
            )
            return True
