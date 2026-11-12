# SPDX-License-Identifier: MIT
"""Verified VCS-free package staging and native-state ownership for format8."""

import base64
import os
from pathlib import Path
import shutil

import dependencies_v1 as protocol
import dependencies_v1_cache as cache
import dependencies_v1_layout as layout
from dependencies_v1_projection import edited_state as projection
from dependencies_v1_transaction import canonical
from native_sdk import store_root

MARKER = ".swiftpm-purifier-dependencies-v1-owned.json"
MARKER_LIMIT = 16 * 1024 * 1024
FIELDS = {
    "version",
    "protocol",
    "root",
    "lockHash",
    "originalState",
    "sources",
    "projection",
    "contentHash",
}


def original_state(raw):
    if raw is None:
        return None
    cache.require(
        isinstance(raw, bytes) and len(raw) <= 1024 * 1024, "original state byte bound"
    )
    state = cache.decode(raw)
    cache.require(
        isinstance(state, dict)
        and set(state) == {"version", "object"}
        and type(state["version"]) is int
        and state["version"] == 1,
        "original native state protocol",
    )
    cache.require(
        isinstance(state["object"], dict)
        and set(state["object"]) == {"dependencies"}
        and isinstance(state["object"]["dependencies"], list)
        and 0 < len(state["object"]["dependencies"]) <= 1024,
        "original native dependency graph",
    )
    names = set()
    urls = set()
    subpaths = set()
    for dep in state["object"]["dependencies"]:
        cache.require(
            isinstance(dep, dict)
            and set(dep) == {"basedOn", "name", "repositoryURL", "state", "subpath"}
            and dep["basedOn"] is None,
            "original checkout fields",
        )
        cache.require(
            protocol.legacy.safe_name(dep["name"])
            and dep["name"] not in names
            and protocol.legacy.git_location(dep["repositoryURL"])
            and dep["repositoryURL"] not in urls,
            "original checkout identity",
        )
        subpath = dep["subpath"]
        cache.require(
            protocol.legacy.safe_name(subpath)
            and len(subpath.encode()) <= 255
            and subpath not in subpaths,
            "original opaque checkout subpath",
        )
        value = dep["state"]
        cache.require(
            isinstance(value, dict)
            and set(value) == {"name", "checkoutState"}
            and value["name"] == "checkout",
            "original checkout kind",
        )
        checkout = value["checkoutState"]
        cache.require(
            isinstance(checkout, dict)
            and set(checkout) == {"branch", "revision", "version"}
            and checkout["branch"] is None
            and isinstance(checkout["revision"], str)
            and protocol.legacy.REVISION.fullmatch(checkout["revision"]) is not None
            and protocol.legacy.version_valid(checkout["version"]),
            "original checkout state",
        )
        names.add(dep["name"])
        urls.add(dep["repositoryURL"])
        subpaths.add(subpath)
    return raw


def native_state(root):
    root = layout.canonical(root)
    cache.require(not os.path.lexists(root / "Package.pins"), "mixed native pin format")
    build = root / ".build"
    if not os.path.lexists(build):
        return None
    cache.directory(build)
    cache.require(
        not os.path.lexists(build / "workspace-state.json"), "mixed native state"
    )
    return (
        cache.regular_bytes(build, "dependencies-state.json")
        if os.path.lexists(build / "dependencies-state.json")
        else None
    )


def validate_marker(value):
    cache.require(
        isinstance(value, dict) and set(value) == FIELDS, "closed owned marker"
    )
    cache.require(len(canonical(value)) + 1 <= MARKER_LIMIT, "owned marker byte bound")
    cache.require(
        type(value["version"]) is int
        and value["version"] == 1
        and value["protocol"] == "dependencies-state-v1",
        "owned marker protocol",
    )
    old_root = value["root"]
    # This is a lexical comparison anchor, never a filesystem path to follow.
    cache.require(
        isinstance(old_root, str)
        and Path(old_root).is_absolute()
        and str(Path(old_root)) == old_root
        and "//" not in old_root
        and ".." not in Path(old_root).parts,
        "owned root spelling",
    )
    pins = value["sources"]
    cache.require(
        isinstance(pins, dict) and 0 < len(pins) <= 1024, "owned source graph"
    )
    urls = set()
    for name, pin in pins.items():
        cache.require(
            protocol.legacy.safe_name(name)
            and len(name.encode()) <= 255
            and isinstance(pin, dict)
            and set(pin) == {"name", "sourceLocation", "revision", "version", "hash"}
            and pin["name"] == name,
            "owned pin fields",
        )
        cache.require(
            protocol.legacy.git_location(pin["sourceLocation"])
            and pin["sourceLocation"] not in urls
            and isinstance(pin["revision"], str)
            and protocol.legacy.REVISION.fullmatch(pin["revision"]) is not None
            and protocol.legacy.version_valid(pin["version"])
            and protocol.portable.digest(pin["hash"]),
            "owned pin identity",
        )
        urls.add(pin["sourceLocation"])
    projection_matches(canonical(value["projection"]), projection(old_root, pins))
    cache.require(
        protocol.portable.digest(value["lockHash"])
        and value["contentHash"] == protocol.content_hash(value),
        "owned marker content hash",
    )
    original = value["originalState"]
    cache.require(
        original is None or isinstance(original, str), "original state encoding"
    )
    raw = None if original is None else base64.b64decode(original, validate=True)
    original_state(raw)
    return value


def projection_matches(raw, expected):
    value = cache.decode(raw)
    cache.require(
        isinstance(value, dict)
        and set(value) == {"version", "object"}
        and type(value["version"]) is int
        and value["version"] == 1,
        "owned native state version",
    )
    cache.require(
        isinstance(value["object"], dict)
        and set(value["object"]) == {"dependencies"}
        and isinstance(value["object"]["dependencies"], list),
        "owned native state object",
    )
    actual = {}
    for dep in value["object"]["dependencies"]:
        cache.require(
            isinstance(dep, dict)
            and isinstance(dep.get("name"), str)
            and dep["name"] not in actual,
            "owned native dependency identity",
        )
        actual[dep["name"]] = dep
    wanted = {dep["name"]: dep for dep in expected["object"]["dependencies"]}
    cache.require(actual == wanted, "owned native projection changed")


def existing(root, hash_tree, allow_missing_state=False):
    root = layout.canonical(root)
    packages = root / "Packages"
    if not os.path.lexists(packages):
        return None
    cache.directory(packages)
    marker = cache.regular_bytes(packages, MARKER, limit=MARKER_LIMIT)
    value = validate_marker(cache.decode(marker))
    cache.require(marker == canonical(value) + b"\n", "owned marker canonical bytes")
    cache.require(
        {path.name for path in packages.iterdir()} == set(value["sources"]) | {MARKER},
        "owned package set changed",
    )
    for name, pin in value["sources"].items():
        path = packages / name
        cache.directory(path)
        cache.require(
            not os.path.lexists(path / ".git"), "owned sources must be VCS-free"
        )
        cache.source_tree(path)
        cache.require(hash_tree(path) == pin["hash"], "owned dependency source changed")
    current = native_state(root)
    if current is None:
        cache.require(allow_missing_state, "owned native state missing")
    else:
        projection_matches(current, value["projection"])
    return value


def prepare_tree(root, lock, sources, destination, original, hash_tree):
    """Assemble verified owned sources outside caller space; never install here."""
    root = layout.canonical(root)
    destination = layout.canonical(destination)
    protocol.validate(lock)
    original_state(original)
    cache.require(
        not destination.is_relative_to(root) and not os.path.lexists(destination),
        "owned staging must be a new external directory",
    )
    cache.require(
        isinstance(sources, dict) and set(sources) == set(lock["pins"]),
        "owned fetched source set",
    )
    checked = {}
    for name, pin in lock["pins"].items():
        cache.require(len(name.encode()) <= 255, "owned source component bound")
        path = layout.canonical(sources[name])
        cache.directory(path)
        cache.require(
            store_root(str(path)) == str(path), "immutable fetched source required"
        )
        cache.require(hash_tree(path) == pin["hash"], "fetched source NAR mismatch")
        checked[name] = path
    value = {
        "version": 1,
        "protocol": "dependencies-state-v1",
        "root": str(root),
        "lockHash": lock["contentHash"],
        "originalState": None
        if original is None
        else base64.b64encode(original).decode(),
        "sources": cache.decode(canonical(lock["pins"])),
        "projection": projection(root, lock["pins"]),
    }
    value["contentHash"] = protocol.content_hash(value)
    validate_marker(value)
    destination.mkdir(mode=0o700)
    for name, path in checked.items():
        target = destination / name
        shutil.copytree(path, target, symlinks=True)
        cache.require(
            not os.path.lexists(target / ".git"), "fetched source contains Git metadata"
        )
        cache.source_tree(target)
        for directory, names, files in os.walk(target):
            Path(directory).chmod(Path(directory).stat().st_mode | 0o700)
            for filename in files:
                leaf = Path(directory) / filename
                leaf.chmod(leaf.stat().st_mode | 0o200)
        cache.require(
            hash_tree(target) == lock["pins"][name]["hash"],
            "staged source NAR mismatch",
        )
    (destination / MARKER).write_bytes(canonical(value) + b"\n")
    return value


def installation_basis(root, hash_tree):
    """Read the replaceable roles before staging; never create caller paths.

    A first install preserves native checkout bytes. A subsequent install carries
    forward the first install's backup, including after project relocation.
    Directory publication must recheck this basis under the root transaction lock.
    """
    root = layout.canonical(root)
    cache.directory(root)
    owned = existing(root, hash_tree)
    current = native_state(root)
    if owned is None:
        original = original_state(current)
    else:
        original = (
            None
            if owned["originalState"] is None
            else base64.b64decode(owned["originalState"], validate=True)
        )
        original_state(original)
    build = root / ".build"
    return {
        "rootIdentity": list(cache_identity(root)),
        "buildIdentity": list(cache_identity(build))
        if os.path.lexists(build)
        else None,
        "packagesIdentity": list(cache_identity(root / "Packages"))
        if owned is not None
        else None,
        "currentState": current,
        "originalState": original,
        "owned": owned,
    }


def cache_identity(path):
    facts = path.lstat()
    return facts.st_dev, facts.st_ino
