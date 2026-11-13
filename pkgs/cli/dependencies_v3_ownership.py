# SPDX-License-Identifier: MIT
"""Typed root9 ownership data; native state alone grants no mutation authority."""

import os

import dependencies_v1_cache as cache
import dependencies_v1_layout as layout
import dependencies_v3 as protocol
import native_protocols as protocols
from native_path_family import codec
from dependencies_v1_install_transaction import snapshot, validate_snapshot
from dependencies_v1_projection import writable_source
from dependencies_v1_transaction import decoded, identity
from native_sdk import store_root

MARKER = ".swiftpm-purifier-dependencies-v3-owned.json"
MARKER_LIMIT = 16 * 1024 * 1024
FIELDS = {
    "version",
    "protocol",
    "rootIdentity",
    "portable",
    "nativePins",
    "originalState",
    "projection",
    "contentHash",
}


def validate_native_layout(root, *, native_protocol=protocols.DEPENDENCIES_V3):
    """Reject mixed native protocols without opening their state files."""
    native_protocol = protocols.checked(native_protocol)
    protocol = codec(native_protocol)
    root = layout.canonical(root)
    cache.require(not os.path.lexists(root / "Package.pins"), "mixed native pin format")
    build = root / ".build"
    if os.path.lexists(build):
        cache.directory(build)
        cache.require(
            not os.path.lexists(
                build
                / (
                    "workspace-state.json"
                    if native_protocol is protocols.DEPENDENCIES_V3
                    else "dependencies-state.json"
                )
            ),
            "mixed native state",
        )
    return root


def native_state(root, *, native_protocol=protocols.DEPENDENCIES_V3):
    native_protocol = protocols.checked(native_protocol)
    protocol = codec(native_protocol)
    root = validate_native_layout(root, native_protocol=native_protocol)
    build = root / ".build"
    if not os.path.lexists(build):
        return None
    result = snapshot(build / native_protocol.state_basename)
    cache.require(
        result is None or result["kind"] == "regular", "native-v3 state must be regular"
    )
    return result


def original_state(value, root, *, native_protocol=protocols.DEPENDENCIES_V3):
    """Preserve exact original bytes, mode and absence; never derive authority."""
    native_protocol = protocols.checked(native_protocol)
    protocol = codec(native_protocol)
    validate_snapshot(value)
    if value is not None:
        cache.require(
            value["kind"] == "regular", "original native-v3 state must be regular"
        )
        raw = decoded(value["bytes"])
        cache.require(len(raw) <= 1024 * 1024, "original native-v3 state byte bound")
        protocol.native_index(cache.decode(raw), root, physical_locals=False)
    return value


def projection(lock, root, *, native_protocol=protocols.DEPENDENCIES_V3):
    native_protocol = protocols.checked(native_protocol)
    protocol = codec(native_protocol)
    return protocol.edited_state(
        protocol.checkout_records(lock, root, physical_locals=False),
        root,
        physical_locals=False,
    )


def projection_matches(
    raw, expected, root, *, native_protocol=protocols.DEPENDENCIES_V3
):
    native_protocol = protocols.checked(native_protocol)
    protocol = codec(native_protocol)
    cache.require(
        protocol.native_index(
            cache.decode(raw), root, allow_edited=True, physical_locals=False
        )
        == protocol.native_index(
            expected, root, allow_edited=True, physical_locals=False
        ),
        "owned native-v3 projection changed",
    )


def validate_marker(value, root, *, native_protocol=protocols.DEPENDENCIES_V3):
    native_protocol = protocols.checked(native_protocol)
    protocol = codec(native_protocol)
    root = layout.canonical(root)
    cache.require(
        isinstance(value, dict) and set(value) == FIELDS,
        "closed root9 ownership fields",
    )
    cache.require(
        len(protocol.canonical(value)) + 1 <= MARKER_LIMIT, "root9 ownership byte bound"
    )
    cache.require(
        type(value["version"]) is int
        and value["version"] == 1
        and value["protocol"] == native_protocol.name
        and isinstance(value["rootIdentity"], list)
        and len(value["rootIdentity"]) == 2
        and all(type(n) is int for n in value["rootIdentity"])
        and value["rootIdentity"] == identity(root),
        "root9 ownership root binding",
    )
    selected = decoded(value["nativePins"])
    cache.require(isinstance(selected, bytes), "owned root9 selected pins missing")
    protocol.validate_lock(
        value["portable"], cache.decode(selected), root, current=False
    )
    original_state(value["originalState"], root, native_protocol=native_protocol)
    cache.require(
        value["projection"]
        == projection(value["portable"], root, native_protocol=native_protocol),
        "root9 owned projection binding",
    )
    cache.require(
        value["contentHash"] == protocol.content_hash(value), "root9 ownership hash"
    )
    return value


def existing(root, hash_tree, *, native_protocol=protocols.DEPENDENCIES_V3):
    """Validate the complete owned set before planning any mutation."""
    native_protocol = protocols.checked(native_protocol)
    protocol = codec(native_protocol)
    root = layout.canonical(root)
    packages = root / "Packages"
    if not os.path.lexists(packages):
        return None
    cache.directory(packages)
    marker = native_protocol.ownership_marker
    raw = cache.regular_bytes(packages, marker, MARKER_LIMIT)
    value = validate_marker(cache.decode(raw), root, native_protocol=native_protocol)
    cache.require(
        raw == protocol.canonical(value) + b"\n", "owned root9 marker changed"
    )
    pins = value["portable"]["pins"]
    names = {
        protocols.installation_name(native_protocol, key, pin)
        for key, pin in pins.items()
    }
    cache.require(
        {path.name for path in packages.iterdir()} == names | {marker},
        "root9 owned package set changed",
    )
    for key, pin in pins.items():
        path = packages / protocols.installation_name(native_protocol, key, pin)
        cache.source_tree(path)
        cache.require(not os.path.lexists(path / ".git"), "root9 owned VCS metadata")
        cache.require(hash_tree(path) == pin["hash"], "root9 owned source changed")
    state = native_state(root, native_protocol=native_protocol)
    cache.require(state is not None, "root9 owned native state missing")
    projection_matches(
        decoded(state["bytes"]),
        value["projection"],
        root,
        native_protocol=native_protocol,
    )
    return value


def prepare_tree(
    root,
    lock,
    selected_raw,
    sources,
    destination,
    original,
    hash_tree,
    *,
    native_protocol=protocols.DEPENDENCIES_V3,
):
    """Build external staging only; the durable committer owns publication."""
    native_protocol = protocols.checked(native_protocol)
    protocol = codec(native_protocol)
    from dependencies_v1_transaction import encoded

    root, destination = layout.canonical(root), layout.canonical(destination)
    protocol.validate_lock(lock, cache.decode(selected_raw), root)
    original_state(original, root, native_protocol=native_protocol)
    cache.require(
        not destination.is_relative_to(root) and not os.path.lexists(destination),
        "root9 staging must be new and external",
    )
    cache.require(
        isinstance(sources, dict) and set(sources) == set(lock["pins"]),
        "root9 fetched source closure",
    )
    checked = {}
    for key, pin in lock["pins"].items():
        source = layout.canonical(sources[key])
        cache.directory(source)
        cache.require(
            store_root(str(source)) == str(source),
            "root9 immutable fetched source required",
        )
        cache.require(
            hash_tree(source) == pin["hash"], "root9 fetched source NAR mismatch"
        )
        checked[key] = source
    value = {
        "version": 1,
        "protocol": native_protocol.name,
        "rootIdentity": identity(root),
        "portable": lock,
        "nativePins": encoded(selected_raw),
        "originalState": original,
        "projection": projection(lock, root, native_protocol=native_protocol),
    }
    value["contentHash"] = protocol.content_hash(value)
    validate_marker(value, root, native_protocol=native_protocol)
    destination.mkdir(mode=0o700)
    for key, source in checked.items():
        target = destination / protocols.installation_name(
            native_protocol, key, lock["pins"][key]
        )
        writable_source(source, target)
        cache.require(
            hash_tree(target) == lock["pins"][key]["hash"],
            "root9 staged source NAR mismatch",
        )
    (destination / native_protocol.ownership_marker).write_bytes(
        protocol.canonical(value) + b"\n"
    )
    (destination / native_protocol.ownership_marker).chmod(0o600)
    return value
