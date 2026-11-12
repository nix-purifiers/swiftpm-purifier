# SPDX-License-Identifier: MIT
"""Bind format8 physical selectors before any native command or write."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path

import dependencies_v1_cache as cache
import dependencies_v1_layout as layout

DEFAULT = object()


def identity(path):
    facts = path.lstat()
    return facts.st_dev, facts.st_ino


@dataclass(frozen=True)
class Selection:
    path: Path | None
    raw: bytes | None
    parent_identity: tuple | None
    file_identity: tuple | None

    @property
    def sha256(self):
        return None if self.raw is None else hashlib.sha256(self.raw).hexdigest()

    def verify(self):
        if self.path is None:
            return
        cache.require(
            identity(self.path.parent) == self.parent_identity,
            "selector parent changed",
        )
        raw = (
            cache.regular_bytes(self.path.parent, self.path.name)
            if os.path.lexists(self.path)
            else None
        )
        current_identity = identity(self.path) if raw is not None else None
        cache.require(
            raw == self.raw and current_identity == self.file_identity,
            "selected file changed",
        )


def select(root, value=DEFAULT, *, portable=False):
    root = layout.canonical(root)
    cache.directory(root)
    if value is None:
        cache.require(not portable, "portable selector cannot be null")
        return Selection(None, None, None, None)
    default = "swiftpm-purifier.lock.json" if portable else "Package.resolved"
    path = layout.canonical(root / default if value is DEFAULT else value)
    cache.require(
        path == root / default or not path.is_relative_to(root),
        "selector must be default root or fully external",
    )
    cache.directory(path.parent)
    parent = identity(path.parent)
    raw = cache.regular_bytes(path.parent, path.name) if os.path.lexists(path) else None
    selected = Selection(path, raw, parent, identity(path) if raw is not None else None)
    selected.verify()
    return selected


def pair(root, native=DEFAULT, portable=DEFAULT):
    pin = select(root, native)
    lock = select(root, portable, portable=True)
    cache.require(
        pin.path is None or pin.path != lock.path,
        "native and portable selectors coincide",
    )
    cache.require(
        pin.file_identity is None or pin.file_identity != lock.file_identity,
        "native and portable selector aliases",
    )
    # External selection must never alias the logical-root role. The bounded
    # reader rejects hardlinks even when the second name lies outside root.
    logical = select(root)
    for chosen in (pin, lock):
        if chosen.path is not None and chosen.path != logical.path:
            cache.require(
                chosen.file_identity is None
                or chosen.file_identity != logical.file_identity,
                "logical-root selector alias",
            )
    pin.verify()
    lock.verify()
    logical.verify()
    return pin, lock, logical
