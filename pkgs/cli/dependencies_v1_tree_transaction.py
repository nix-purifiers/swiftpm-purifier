# SPDX-License-Identifier: MIT
"""Bounded physical inventories for recoverable owned-directory publication.

Only transaction-owned staged/backup trees may be passed to cleanup. The caller
must bind this inventory to its durable reservation before any rename or removal.
Missing entries are permitted during cleanup recovery; changed or added entries
are refused before further removal. This module does not grant tree ownership.
"""

import hashlib
import os
from pathlib import Path
import stat

import dependencies_v1_cache as cache
import dependencies_v1_layout as layout
from dependencies_v1_transaction import sync

MAX_ENTRIES = 100000
MAX_FILE = 64 * 1024 * 1024
MAX_BYTES = 1024 * 1024 * 1024


def inventory(root):
    root = layout.canonical(root)
    cache.directory(root)
    result = {}
    total = 0

    def visit(path, depth):
        nonlocal total
        cache.require(depth <= 256, "owned tree depth")
        facts = path.lstat()
        mode = stat.S_IMODE(facts.st_mode)
        cache.require(mode <= 0o777, "owned tree special permission bits")
        relative = str(path.relative_to(root))
        if stat.S_ISDIR(facts.st_mode):
            result[relative] = {"kind": "directory", "mode": mode}
            cache.require(len(result) <= MAX_ENTRIES, "owned tree entry bound")
            for child in sorted(path.iterdir()):
                encoded = child.name.encode("utf-8", errors="strict")
                cache.require(
                    len(encoded) <= 255
                    and not any(ord(c) < 32 or ord(c) == 127 for c in child.name),
                    "owned tree filename",
                )
                visit(child, depth + 1)
        else:
            cache.require(
                stat.S_ISREG(facts.st_mode) and facts.st_nlink == 1,
                "owned tree link, alias or special file",
            )
            cache.require(facts.st_size <= MAX_FILE, "owned tree file bound")
            total += facts.st_size
            cache.require(total <= MAX_BYTES, "owned tree total byte bound")
            raw = cache.regular_bytes(root, relative, limit=MAX_FILE)
            result[relative] = {
                "kind": "regular",
                "mode": mode,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            }
            cache.require(len(result) <= MAX_ENTRIES, "owned tree entry bound")

    visit(root, 0)
    return result


def validate(record):
    cache.require(
        isinstance(record, dict) and 0 < len(record) <= MAX_ENTRIES,
        "owned tree inventory shape",
    )
    total = 0
    for name, entry in record.items():
        cache.require(isinstance(name, str), "owned inventory path type")
        parts = Path(name).parts
        cache.require(
            len(parts) <= 256
            and all(
                len(part.encode("utf-8", errors="strict")) <= 255
                and not any(ord(c) < 32 or ord(c) == 127 for c in part)
                for part in parts
            ),
            "owned inventory path bound",
        )
        cache.require(
            name == "."
            or (
                parts
                and not Path(name).is_absolute()
                and str(Path(name)) == name
                and all(part not in (".", "..") for part in parts)
            ),
            "owned inventory confined path",
        )
        cache.require(isinstance(entry, dict), "owned inventory entry type")
        cache.require(
            type(entry.get("mode")) is int and 0 <= entry["mode"] <= 0o777,
            "owned inventory mode",
        )
        if entry.get("kind") == "directory":
            cache.require(set(entry) == {"kind", "mode"}, "owned directory fields")
        else:
            cache.require(
                entry.get("kind") == "regular"
                and set(entry) == {"kind", "mode", "sha256", "size"}
                and isinstance(entry["sha256"], str)
                and len(entry["sha256"]) == 64
                and all(c in "0123456789abcdef" for c in entry["sha256"])
                and type(entry["size"]) is int
                and 0 <= entry["size"] <= MAX_FILE,
                "owned regular file fields",
            )
            total += entry["size"]
            cache.require(total <= MAX_BYTES, "owned inventory total byte bound")
        if name != ".":
            parent = str(Path(name).parent)
            cache.require(
                parent in record and record[parent].get("kind") == "directory",
                "owned inventory parent",
            )
    cache.require(record.get(".", {}).get("kind") == "directory", "owned tree root")
    return record


def verify(root, record, partial=False):
    validate(record)
    if not os.path.lexists(root):
        cache.require(partial, "owned tree missing")
        return {}
    current = inventory(root)
    cache.require(
        all(
            name in record and entry == record[name] for name, entry in current.items()
        ),
        "owned tree changed or contains foreign data",
    )
    cache.require(partial or current == record, "owned tree incomplete")
    return current


def cleanup(root, record, guard, callback=None):
    """Resume validated deletion; guard must check durable control ownership."""
    root = layout.canonical(root)
    guard()
    current = verify(root, record, partial=True)
    # Children precede parents; inventory is revalidated after every interruption.
    for name in sorted(
        current, key=lambda item: (len(Path(item).parts), item), reverse=True
    ):
        if name == ".":
            continue
        guard()
        verify(root, record, partial=True)
        path = root / name
        if current[name]["kind"] == "directory":
            path.rmdir()
        else:
            path.unlink()
        sync(path.parent)
        if callback is not None:
            callback("tree-remove-" + name)
    guard()
    remaining = verify(root, record, partial=True)
    if remaining:
        cache.require(set(remaining) == {"."}, "owned cleanup incomplete")
        root.rmdir()
        sync(root.parent)
        if callback is not None:
            callback("tree-remove-root")
