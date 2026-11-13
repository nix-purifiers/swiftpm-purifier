# SPDX-License-Identifier: MIT
"""Recoverable selected-pin projection for portable4 / native checkout2.

Only the existing byte-transaction envelope is reused. Native cache admission
and source/currentness validation remain those of portable format4.
"""

import os
from pathlib import Path
import signal
import stat
import subprocess

import dependencies_v1_cache as cache
import dependencies_v1_layout as layout
import dependencies_v1_selectors as selectors
from dependencies_v1_bridge import NativePinBridge
from dependencies_v1_transaction import (
    Transaction,
    decoded,
    digest,
    identity,
    read,
    need,
)


def lock_value(raw, api):
    value = cache.decode(raw)
    need(
        isinstance(value, dict) and set(value) == set(api.LOCK_KEYS),
        "legacy bridge requires the existing format4 fields",
    )
    need(
        type(value["version"]) is int
        and value["version"] == 4
        and type(value["workspaceStateVersion"]) is int
        and value["workspaceStateVersion"] == 2,
        "legacy bridge requires portable4 / checkout2",
    )
    need(value["contentHash"] == api.content_hash(value), "legacy bridge lock hash")
    need(
        isinstance(value["pins"], dict)
        and isinstance(value["products"], list)
        and isinstance(value["localDependencies"], list)
        and value["binaryTargets"] == [],
        "legacy bridge lock graph shape",
    )
    return value


def registry(root, create=False):
    configured = os.environ.get("XDG_STATE_HOME")
    parent = (
        Path(configured)
        if configured and Path(configured).is_absolute()
        else Path.home() / ".local/state"
    )
    state = (parent / "swiftpm-purifier/dependencies-v1").resolve()
    if create:
        need(not state.is_relative_to(root), "bridge registry must be external")
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
    return state


class Guard(Transaction):
    ALLOW_INTERNAL_SELECTORS = True


class Bridge(NativePinBridge):
    ALLOW_INTERNAL_SELECTORS = True

    def __init__(self, root, state, api, portable=selectors.DEFAULT):
        self.api = api
        super().__init__(root, state, portable=portable)

    def validate(self, record):
        record = super().validate(record)
        # This gate applies to reserved portable bytes, never the current CLI
        # lock as a shortcut to a more permissive recovery policy.
        lock_value(decoded(record["old"][1]), self.api)
        return record

    def output_allowed(self, record, index, raw, mode, phase):
        self.validate(record)
        if super().output_allowed(record, index, raw, mode, phase):
            return True
        if phase == "PREPARED" and index == 0 and raw is None and mode is None:
            # Native SwiftPM deletes pins when every dependency is edited.
            # There is no foreign present data to overwrite in this state.
            return True
        self.foreign_control(record)


def selections(api, root, native=selectors.DEFAULT, portable=selectors.DEFAULT):
    if native is selectors.DEFAULT:
        native = api.resolved_path(root)
    if portable is selectors.DEFAULT:
        portable = api.lock_path(root)
    return native, portable


def projection(guard, api):
    result = Bridge(guard.root, guard.registry, api, portable=guard.paths[1])
    result.descriptors = guard.descriptors
    return result


def reservation_hint(api, root, state):
    """A bounded private reservation is only a recovery-routing hint."""
    key = digest(["root", identity(root)])
    for suffix in (".json", ".pending"):
        path = state / (key + suffix)
        if not os.path.lexists(path):
            continue
        raw = read(path, control=True)
        value = cache.decode(raw)
        if not isinstance(value, dict) or value.get("operation") != "native-pin-bridge":
            continue
        try:
            need(value["old"][1] == value["new"][1], "bridge portable changed")
            lock_value(decoded(value["old"][1]), api)
        except (ValueError, KeyError, TypeError, IndexError):
            continue
        return True
    return False


def recover_if_needed(api, root):
    root = Path(root).resolve()
    try:
        state = registry(root)
        pending = reservation_hint(api, root, state)
    except (ValueError, OSError):
        return False
    if not pending:
        return False
    native, portable = selections(api, root)
    with Guard(root, state, native, portable).locked() as guard:
        projection(guard, api).recover()
    return True


def selected_mode(selected):
    return (
        None
        if selected.path is None or selected.raw is None
        else stat.S_IMODE(selected.path.lstat().st_mode)
    )


def verify(selected, mode):
    selected.verify()
    need(selected_mode(selected) == mode, "selected file mode changed")


def current(api, root, raw, native):
    value = lock_value(raw, api)
    resolved = None if native.raw is None else cache.decode(native.raw)
    need(
        api.legacy_state_lock_valid(root, value, resolved_override=resolved),
        "legacy bridge native/local graph shape",
    )
    need(
        value["manifestHash"] == api.manifest_hash(root),
        "legacy bridge manifest changed",
    )
    identities = {
        pin["sourceLocation"]: identity for identity, pin in value["pins"].items()
    }
    pins = [] if native.path is None else api.resolved_pins(native.path, identities)
    need(
        {pin["identity"] for pin in pins} == set(value["pins"]),
        "legacy bridge selected pins changed",
    )
    for pin in pins:
        locked = value["pins"][pin["identity"]]
        need(
            locked["revision"] == api.pinned_at(pin)
            and locked["sourceLocation"] == pin["location"]
            and locked["name"] == pin.get("legacyPackage"),
            "legacy bridge pin identity changed",
        )
    return value


class SignalRelay:
    def __init__(self):
        self.child = None
        self.pending = []
        self.previous = {}

    def forward(self, signum, _frame):
        self.pending.append(signum)
        self.send(signum)

    def send(self, signum):
        if self.child is not None and self.child.poll() is None:
            try:
                os.killpg(self.child.pid, signum)
            except ProcessLookupError:
                pass

    def install(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            self.previous[signum] = signal.signal(signum, self.forward)
        return self

    def restore(self):
        for signum, handler in self.previous.items():
            signal.signal(signum, handler)


def foreground(
    command, root, descriptors, child_started=None, before_spawn=None, relay=None
):
    """Forward foreground signals and retain inherited ownership locks."""
    owned = relay is None
    relay = SignalRelay().install() if owned else relay
    try:
        if before_spawn is not None:
            before_spawn()
        child = subprocess.Popen(
            command, cwd=root, start_new_session=True, pass_fds=tuple(descriptors)
        )
        relay.child = child
        for signum in relay.pending:
            relay.send(signum)
        if child_started is not None:
            child_started(child.pid, tuple(descriptors))
        return child.wait(), relay.restore
    except BaseException:
        if relay.child is not None:
            if relay.child.poll() is None:
                relay.send(signal.SIGKILL)
            relay.child.wait()
        if owned:
            relay.restore()
        raise


def hold_active(bridge):
    raw = read(bridge.reservation, control=True)
    if raw is not None:
        record = bridge.validate(cache.decode(raw))
        bridge.foreign_control(record)


def run_native(
    api,
    root,
    command,
    native=selectors.DEFAULT,
    portable=selectors.DEFAULT,
    child_started=None,
):
    root = layout.canonical(root)
    state = registry(root, create=True)
    native, portable = selections(api, root, native, portable)
    with Guard(root, state, native, portable).locked() as guard:
        bridge = projection(guard, api)
        bridge.recover()
        pin, output, logical = selectors.pair(
            root, native, portable, allow_internal=True
        )
        need(output.raw is not None, "legacy bridge portable input missing")
        current(api, root, output.raw, pin)
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
                    if pin.path != logical.path:
                        verify(pin, native_mode)
                    verify(output, portable_mode)
                except (ValueError, OSError):
                    hold_active(bridge)
                    raise
                bridge.recover()
            finally:
                relay.restore()
        if pin.path != logical.path:
            verify(pin, native_mode)
        verify(output, portable_mode)
        # Re-select logical pins after recovery: publication/restoration may
        # replace their inode, but external selections must retain identity.
        final_pin = selectors.select(root, native, allow_internal=True)
        current(api, root, output.raw, final_pin)
        return status


def exit_status(status):
    if status < 0:
        signum = -status
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
        raise SystemExit(128 + signum)
    raise SystemExit(status)
