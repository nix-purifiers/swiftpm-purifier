# SPDX-License-Identifier: MIT
"""Format-7-only structural source inventory; no native tool or SDK selection."""

import hashlib
import json
import os
from pathlib import Path
import stat

DEFAULT_LOCK = "swiftpm-purifier.lock.json"
ROOT_DIRECTORIES = {".build", "Packages", ".git"}
MAX_ENTRIES = 100000
MAX_DEPTH = 256


class LayoutError(ValueError):
    pass


def canonical(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise LayoutError("format 7 requires an absolute canonical source/lock path")
    for current in [*reversed(path.parents), path]:
        if current.is_symlink():
            raise LayoutError("format 7 source/lock ancestors must not be symlinks")
        if current != path and os.path.lexists(current) and not current.is_dir():
            raise LayoutError("format 7 source/lock ancestor is not a directory")
    return path


def lock_destination(root, lock_file=None):
    root = canonical(root)
    selected = canonical(root / DEFAULT_LOCK if lock_file is None else lock_file)
    if selected != root / DEFAULT_LOCK and selected.is_relative_to(root):
        raise LayoutError(
            "format 7 supports the default root purifier lock or a fully external lock; "
            "other in-tree lock destinations are not supported"
        )
    if os.path.lexists(selected) and not selected.is_file():
        raise LayoutError("format 7 purifier lock must be a regular file or absent")
    return selected


def source_layout(root, lock_file=None):
    root = canonical(root)
    lock_destination(root, lock_file)
    if not root.is_dir():
        raise LayoutError("format 7 source root must be a directory")
    result = []

    def visit(directory, prefix, depth):
        if depth > MAX_DEPTH:
            raise LayoutError("format 7 source layout exceeds its depth bound")
        for item in sorted(os.scandir(directory), key=lambda x: os.fsencode(x.name)):
            name = item.name
            try:
                name.encode("utf-8")
            except UnicodeError as error:
                raise LayoutError("format 7 source paths must be UTF-8") from error
            if any(ord(c) < 32 or ord(c) == 127 for c in name):
                raise LayoutError(
                    "format 7 source paths must not contain control characters"
                )
            mode = item.stat(follow_symlinks=False).st_mode
            if stat.S_ISLNK(mode):
                raise LayoutError("format 7 source layout must not contain symlinks")
            kind = (
                "directory"
                if stat.S_ISDIR(mode)
                else "regular"
                if stat.S_ISREG(mode)
                else None
            )
            if kind is None:
                raise LayoutError("format 7 source layout contains a nonregular input")
            if not prefix and name in ROOT_DIRECTORIES | {DEFAULT_LOCK}:
                allowed = (
                    {"regular", "directory"}
                    if name == ".git"
                    else {"regular"}
                    if name == DEFAULT_LOCK
                    else {"directory"}
                )
                if kind not in allowed:
                    raise LayoutError("format 7 owned root entry has an invalid type")
                continue
            relative = f"{prefix}/{name}" if prefix else name
            result.append([relative, kind])
            if len(result) > MAX_ENTRIES:
                raise LayoutError("format 7 source layout exceeds its entry bound")
            if kind == "directory":
                visit(Path(directory) / name, relative, depth + 1)

    visit(root, "", 0)
    result.sort(key=lambda x: x[0].encode("utf-8"))
    payload = json.dumps(
        {"version": 1, "entries": result},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return {"entries": result, "hash": hashlib.sha256(payload).hexdigest()}
