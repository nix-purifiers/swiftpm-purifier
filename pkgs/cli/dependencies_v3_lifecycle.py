# SPDX-License-Identifier: MIT
"""Locked local-toolchain lifecycle for portable9/native3/pins1."""

import os
from pathlib import Path
import tempfile

import dependencies_v1_cache as cache
import dependencies_v1_layout as layout
import dependencies_v1_selectors as selectors
import dependencies_v3 as protocol
import native_protocols as protocols
import native_path_family as family
import dependencies_v3_capture as capture
import dependencies_v3_ownership as ownership
import dependencies_v3_observation as observation
from dependencies_v1_capture import NativeSession
from dependencies_v1_lifecycle import recover_locked, registry
from dependencies_v1_install_transaction import snapshot
from dependencies_v1_projection import writable_source
from dependencies_v1_transaction import Transaction
from dependencies_v3_install_transaction import Installation
from dependencies_v3_transaction import Capture, Bridge
import legacy_manifest
from legacy_pin_bridge import (
    SignalRelay,
    foreground,
    selected_mode,
    verify,
    hold_active,
)


def validate_source(
    root,
    raw,
    native=selectors.DEFAULT,
    portable=selectors.DEFAULT,
    *,
    current=True,
    native_protocol=protocols.DEPENDENCIES_V3,
):
    native_protocol = protocols.checked(native_protocol)
    protocol = family.codec(native_protocol)
    ownership = family.ownership(native_protocol)
    pin, output, logical = selectors.pair(root, native, portable)
    cache.require(
        pin.raw is not None and output.raw == raw,
        "selected root9 inputs missing or changed",
    )
    cache.require(
        isinstance(raw, bytes) and len(raw) <= 1024 * 1024,
        "portable9 selected byte bound",
    )
    lock = protocol.validate_lock(
        cache.decode(raw), cache.decode(pin.raw), root, current=current
    )
    for selection in (pin, output, logical):
        selection.verify()
    return lock


def committer(
    guard, owners=None, hash_tree=None, *, native_protocol=protocols.DEPENDENCIES_V3
):
    native_protocol = protocols.checked(native_protocol)
    protocol = family.codec(native_protocol)
    ownership = family.ownership(native_protocol)
    Installation = family.installation(native_protocol)
    guard.check()
    result = Installation(
        guard.root,
        guard.registry,
        guard.paths[0],
        guard.paths[1],
        owners=owners,
        hash_tree=hash_tree,
    )
    result.descriptors = guard.descriptors
    return result


def runtime(
    root,
    lock,
    selected_raw,
    sources,
    work,
    api,
    hash_tree,
    evidence_parent,
    *,
    native_protocol=protocols.DEPENDENCIES_V3,
):
    """Require actual selected native recognition before caller installation."""
    native_protocol = protocols.checked(native_protocol)
    protocol = family.codec(native_protocol)
    ownership = family.ownership(native_protocol)
    cache.require(not api.sdk_mode(), "portable9 SDK-bound CLI runtime is unavailable")
    cache.require(
        "SWIFTPM_BUILD_DIR" not in os.environ, "external native build directory"
    )
    caller_before = observation.snapshot(root)
    probe = work / root.name
    api.copy_package(root, probe)
    observation.project_pins(probe, selected_raw)
    (probe / "Packages").mkdir()
    cache.require(set(sources) == set(lock["pins"]), "runtime source closure")
    for key, pin in lock["pins"].items():
        cache.require(
            hash_tree(sources[key]) == pin["hash"], "runtime source NAR mismatch"
        )
        path = (
            probe / "Packages" / protocols.installation_name(native_protocol, key, pin)
        )
        writable_source(sources[key], path)
        cache.require(
            hash_tree(path) == pin["hash"], "runtime copied source NAR mismatch"
        )
    (probe / ".build").mkdir()
    state = protocol.edited_state(protocol.checkout_records(lock, probe), probe)
    (probe / ".build" / native_protocol.state_basename).write_bytes(
        protocol.canonical(state)
    )
    ownership.validate_native_layout(probe)
    (work / "tmp").mkdir()
    session = NativeSession(
        probe,
        dict(
            os.environ,
            TMPDIR=str(work / "tmp"),
            CLANG_MODULE_CACHE_PATH=str(work / "clang-module-cache"),
        ),
        evidence_parent,
    )
    source_before = observation.observe(probe, selected_raw)
    session.receipt.update(
        sourceBefore=source_before["fingerprint"],
        sourceAfter=None,
        callerBefore=caller_before["fingerprint"],
        callerAfter=None,
    )
    session.save()
    command = api.native_command("swift-package")
    description = legacy_manifest.json_value(
        session.run("runtime-description", [*command, "dump-package"]).stdout
    )
    products, name = api.product_description(description)
    cache.require(
        name == lock["package"] and products == lock["products"],
        "runtime package/products differ from captured graph",
    )
    session.run("runtime-build", api.native_command("swift-build"))
    source_after = observation.observe(probe, selected_raw, source_before)
    observation.verify(root, caller_before)
    ownership.validate_native_layout(probe)
    session.receipt.update(
        sourceAfter=source_after["fingerprint"],
        callerAfter=caller_before["fingerprint"],
    )
    session.save()
    ownership.projection_matches(
        cache.regular_bytes(probe, ".build/" + native_protocol.state_basename),
        state,
        probe,
    )
    for relative in (".build/checkouts", ".build/repositories"):
        path = probe / relative
        if os.path.lexists(path):
            cache.directory(path)
            cache.require(not any(path.iterdir()), "runtime recreated native Git cache")
    for key, pin in lock["pins"].items():
        path = (
            probe / "Packages" / protocols.installation_name(native_protocol, key, pin)
        )
        cache.source_tree(path)
        cache.require(
            hash_tree(path) == pin["hash"], "runtime modified projected source"
        )
    cache.require(
        protocol.manifest_hash(probe, protocol.local_records(lock, probe))
        == lock["manifestHash"],
        "runtime changed root/local manifests",
    )
    session.receipt.update(
        complete=True,
        protocol=native_protocol.name,
        portableContentHash=lock["contentHash"],
    )
    session.save()
    return session


def install(
    root,
    raw,
    sources,
    api,
    hash_tree,
    state,
    native=selectors.DEFAULT,
    portable=selectors.DEFAULT,
    *,
    native_protocol=protocols.DEPENDENCIES_V3,
):
    native_protocol = protocols.checked(native_protocol)
    protocol = family.codec(native_protocol)
    ownership = family.ownership(native_protocol)
    cache.require(
        not api.sdk_mode(), "portable9 SDK-bound CLI installation is unavailable"
    )
    root = layout.canonical(root)
    with Transaction(root, state, native, portable).locked() as guard:
        recover_locked(guard)
        lock = validate_source(
            root, raw, native, portable, native_protocol=native_protocol
        )
        old = (
            ownership.require_same_family(root, hash_tree)
            if native_protocol
            in (protocols.WORKSPACE_V4_PATH, protocols.WORKSPACE_V5_LOCATION)
            else ownership.existing(root, hash_tree)
        )
        original = ownership.native_state(root) if old is None else old["originalState"]
        with tempfile.TemporaryDirectory(
            prefix=native_protocol.name + "-install-", dir=state
        ) as temporary:
            work = Path(temporary)
            probe = work / "runtime"
            probe.mkdir()
            selected = selectors.select(root, native)
            evidence = runtime(
                root,
                lock,
                selected.raw,
                sources,
                probe,
                api,
                hash_tree,
                state,
                native_protocol=native_protocol,
            )
            if old is not None and old["portable"] == lock:
                ownership.existing(root, hash_tree)
                evidence.receipt.update(installation="already-owned")
                evidence.save()
                return old
            packages = work / "Packages"
            new = ownership.prepare_tree(
                root, lock, selected.raw, sources, packages, original, hash_tree
            )
            build_exists = os.path.lexists(root / ".build")
            target = work / "native-state"
            target.write_bytes(protocol.canonical(new["projection"]))
            target.chmod(0o600)
            if not build_exists:
                build = work / "new-build"
                build.mkdir(mode=0o700)
                target.rename(build / native_protocol.state_basename)
                target = build
            validate_source(
                root, raw, native, portable, native_protocol=native_protocol
            )
            paths = [
                root / "Packages",
                (
                    root / ".build" / native_protocol.state_basename
                    if build_exists
                    else root / ".build"
                ),
            ]
            committer(
                guard, [old, new], hash_tree, native_protocol=native_protocol
            ).publish([packages, target], [snapshot(path) for path in paths])
            ownership.existing(root, hash_tree)
            evidence.receipt.update(installation="committed")
            evidence.save()
            return new


def uninstall(
    root,
    hash_tree,
    state,
    native=selectors.DEFAULT,
    portable=selectors.DEFAULT,
    *,
    native_protocol=protocols.DEPENDENCIES_V3,
):
    native_protocol = protocols.checked(native_protocol)
    protocol = family.codec(native_protocol)
    ownership = family.ownership(native_protocol)
    root = layout.canonical(root)
    with Transaction(root, state, native, portable).locked() as guard:
        recover_locked(guard)
        old = (
            ownership.require_same_family(root, hash_tree)
            if native_protocol
            in (protocols.WORKSPACE_V4_PATH, protocols.WORKSPACE_V5_LOCATION)
            else ownership.existing(root, hash_tree)
        )
        if old is None:
            return
        with tempfile.TemporaryDirectory(
            prefix=native_protocol.name + "-uninstall-", dir=state
        ) as temporary:
            original = old["originalState"]
            target = None
            if original is not None:
                from dependencies_v1_transaction import decoded

                target = Path(temporary) / "original-state"
                target.write_bytes(decoded(original["bytes"]))
                target.chmod(original["mode"])
            paths = [
                root / "Packages",
                root / ".build" / native_protocol.state_basename,
            ]
            committer(
                guard, [old, None], hash_tree, native_protocol=native_protocol
            ).publish([None, target], [snapshot(path) for path in paths])


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
    native_protocol=protocols.DEPENDENCIES_V3,
):
    native_protocol = protocols.checked(native_protocol)
    protocol = family.codec(native_protocol)
    ownership = family.ownership(native_protocol)
    Bridge = family.bridge(native_protocol)
    root = layout.canonical(root)
    with Transaction(root, state, native, portable).locked() as guard:
        recover_locked(guard)
        lock = validate_source(
            root, raw, native, portable, native_protocol=native_protocol
        )
        owned = ownership.existing(root, hash_tree)
        cache.require(
            owned is not None and owned["portable"] == lock,
            "native execution requires the selected owned graph",
        )
        pin, output, _ = selectors.pair(root, native, portable)
        with tempfile.TemporaryDirectory(
            prefix=native_protocol.name + "-foreground-admission-", dir=state
        ) as temporary:
            sources = {
                key: root
                / "Packages"
                / protocols.installation_name(native_protocol, key, value)
                for key, value in lock["pins"].items()
            }
            runtime(
                root,
                lock,
                pin.raw,
                sources,
                Path(temporary),
                api,
                hash_tree,
                state,
                native_protocol=native_protocol,
            )
        validate_source(root, raw, native, portable, native_protocol=native_protocol)
        ownership.existing(root, hash_tree)
        bridge = Bridge(root, state, native, portable)
        bridge.descriptors = guard.descriptors
        native_mode, portable_mode = selected_mode(pin), selected_mode(output)
        relay = SignalRelay().install()
        try:
            status, _ = foreground(
                command,
                root,
                guard.descriptors,
                child_started,
                relay=relay,
                before_spawn=lambda: bridge.publish(
                    [pin.raw, output.raw], bridge.pair(), defer_commit=True
                ),
            )
        finally:
            try:
                try:
                    guard.check()
                    if pin.path != root / "Package.resolved":
                        verify(pin, native_mode)
                    verify(output, portable_mode)
                except (ValueError, OSError):
                    hold_active(bridge)
                    raise
                bridge.recover()
            finally:
                relay.restore()
        if pin.path != root / "Package.resolved":
            verify(pin, native_mode)
        verify(output, portable_mode)
        ownership.existing(root, hash_tree)
        validate_source(root, raw, native, portable, native_protocol=native_protocol)
        return status


def capture_and_publish(
    root,
    temporary,
    api,
    state,
    update=None,
    native=selectors.DEFAULT,
    portable=selectors.DEFAULT,
):
    cache.require(not api.sdk_mode(), "portable9 SDK-bound CLI capture is unavailable")
    with Capture(root, state, native, portable).locked() as guard:
        recover_locked(guard)
        expected = guard.pair()
        lock, pins, session = capture.prepare(
            root, temporary, api, update, native=native, portable=portable
        )
        guard.publish([pins, protocol.canonical(lock) + b"\n"], expected)
        session.receipt.update(publication="committed")
        session.save()
        return lock, session
