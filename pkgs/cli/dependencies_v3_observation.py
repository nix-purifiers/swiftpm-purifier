# SPDX-License-Identifier: MIT
"""Bounded physical source currency for an owned native observation arena."""

import hashlib
import os
from pathlib import Path
import stat

import dependencies_v1_cache as cache
import dependencies_v3 as protocol
import legacy_runtime

MAX_ENTRIES = 100000
MAX_DEPTH = 256
MAX_FILE = 64 * 1024 * 1024
MAX_BYTES = 1024 * 1024 * 1024


def geometry(root):
    """Walk owned source descriptors, never following directory or file links."""
    result = {}
    total = 0
    descriptor = legacy_runtime.directory_fd(root)

    def visit(parent, relative, depth):
        nonlocal total
        cache.require(depth <= MAX_DEPTH, "owned probe source depth")
        facts = os.fstat(parent)
        result[relative or "."] = [
            "directory",
            facts.st_dev,
            facts.st_ino,
            stat.S_IMODE(facts.st_mode),
        ]
        cache.require(len(result) <= MAX_ENTRIES, "owned probe source entry bound")
        ignored = {".build", "Packages", ".git"} if not relative else {".build"}
        for name in sorted(os.listdir(parent)):
            if name in ignored:
                continue
            cache.require(
                len(name.encode("utf-8", errors="strict")) <= 255,
                "owned probe filename bound",
            )
            path = relative + "/" + name if relative else name
            facts = os.stat(name, dir_fd=parent, follow_symlinks=False)
            prefix = [facts.st_dev, facts.st_ino, stat.S_IMODE(facts.st_mode)]
            if stat.S_ISDIR(facts.st_mode):
                child = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=parent,
                )
                try:
                    actual = os.fstat(child)
                    cache.require(
                        (actual.st_dev, actual.st_ino) == (facts.st_dev, facts.st_ino),
                        "owned probe directory changed",
                    )
                    visit(child, path, depth + 1)
                finally:
                    os.close(child)
            elif stat.S_ISREG(facts.st_mode):
                cache.require(
                    facts.st_nlink == 1 and facts.st_size <= MAX_FILE,
                    "owned probe source alias or byte bound",
                )
                total += facts.st_size
                cache.require(total <= MAX_BYTES, "owned probe total source byte bound")
                result[path] = ["regular", *prefix, facts.st_size]
            elif stat.S_ISLNK(facts.st_mode):
                result[path] = ["symlink", *prefix, os.readlink(name, dir_fd=parent)]
            else:
                raise cache.Reject("owned probe source special file")
            cache.require(len(result) <= MAX_ENTRIES, "owned probe source entry bound")

    try:
        visit(descriptor, "", 0)
    finally:
        os.close(descriptor)
    return result


def fingerprint(root, entries):
    rows = []
    for name, entry in sorted(entries.items()):
        row = [name, entry]
        if entry[0] == "regular":
            digest = hashlib.sha256()
            legacy_runtime.regular_content(root, name, MAX_FILE, digest=digest)
            row.append(digest.hexdigest())
        rows.append(row)
    return hashlib.sha256(protocol.canonical(rows)).hexdigest()


def observe(root, selected, expected=None):
    """Only logical pin absence or exact projected bytes are intentional changes."""
    entries = geometry(root)
    pin = entries.pop("Package.resolved", None)
    if pin is not None:
        cache.require(
            pin[0] == "regular" and pin[-1] <= 1024 * 1024,
            "owned probe logical pins type or bound",
        )
    if expected is not None:
        cache.require(
            entries == expected["geometry"], "owned probe source geometry changed"
        )
    if pin is not None:
        cache.require(
            cache.regular_bytes(root, "Package.resolved") == selected,
            "owned probe logical pins changed",
        )
    else:
        cache.require(
            expected is not None, "owned probe projected pins missing initially"
        )
    result = {"geometry": entries, "fingerprint": fingerprint(root, entries)}
    if expected is not None:
        cache.require(
            result["fingerprint"] == expected["fingerprint"],
            "owned probe source content changed",
        )
    final = geometry(root)
    final_pin = final.pop("Package.resolved", None)
    cache.require(
        final == entries and final_pin == pin,
        "owned probe source changed while reading",
    )
    return result


def snapshot(root):
    """Caller source has no intentionally mutable pin role during observation."""
    entries = geometry(root)
    result = {"geometry": entries, "fingerprint": fingerprint(root, entries)}
    cache.require(geometry(root) == entries, "caller source changed while reading")
    return result


def verify(root, expected):
    entries = geometry(root)
    cache.require(
        entries == expected["geometry"],
        "caller source geometry changed during observation",
    )
    cache.require(
        fingerprint(root, entries) == expected["fingerprint"],
        "caller source content changed during observation",
    )
    cache.require(geometry(root) == entries, "caller source changed while reading")


def project_pins(root, raw):
    """Project data only into an owned copy; never truncate through an alias."""
    root = Path(root)
    if os.path.lexists(root / "Package.resolved"):
        cache.regular_bytes(root, "Package.resolved")
        (root / "Package.resolved").unlink()
    if raw is None:
        return
    cache.require(
        isinstance(raw, bytes) and len(raw) <= 1024 * 1024,
        "owned projected pin byte bound",
    )
    parent = legacy_runtime.directory_fd(root)
    try:
        fd = os.open(
            "Package.resolved",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=parent,
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
    finally:
        os.close(parent)


def capture_source(root, expected=None):
    """Capture permits native pin resolution, with all other source fixed."""
    entries = geometry(root)
    pin = entries.pop("Package.resolved", None)
    if pin is not None:
        cache.require(
            pin[0] == "regular" and pin[-1] <= 1024 * 1024,
            "capture logical pin type or bound",
        )
    if expected is not None:
        cache.require(
            entries == expected["geometry"], "capture source geometry changed"
        )
    result = {"geometry": entries, "fingerprint": fingerprint(root, entries)}
    if expected is not None:
        cache.require(
            result["fingerprint"] == expected["fingerprint"],
            "capture source content changed",
        )
    final = geometry(root)
    final_pin = final.pop("Package.resolved", None)
    cache.require(
        final == entries and final_pin == pin, "capture source changed while reading"
    )
    return result
