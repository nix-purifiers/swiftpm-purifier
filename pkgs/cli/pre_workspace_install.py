# SPDX-License-Identifier: MIT
"""Transactional ownership of format7's real native Packages directory."""

import json
import os
from pathlib import Path
import re
import shutil
import tempfile

import pre_workspace as protocol

STATE = "preworkspace-owned.json"


def require(condition, message):
    if not condition:
        raise protocol.StateError(message)


def hash_path(path, nix_hash):
    from pre_workspace_git import command

    return command([nix_hash, "--type", "sha256", str(path)])


def regular_directory(path):
    return path.is_dir() and not path.is_symlink()


def existing_state(root, nix_hash):
    """Read and verify every previously owned byte before any write."""
    cache = root / "Packages"
    build = root / ".build"
    private = build / "swiftpm-purifier"
    state = private / STATE
    for directory in (build, private):
        require(
            not directory.is_symlink(), "owned metadata ancestors must not be symlinks"
        )
        require(
            not os.path.lexists(directory) or directory.is_dir(),
            "owned metadata ancestor is not a directory",
        )
    if not os.path.lexists(state):
        require(
            not os.path.lexists(cache), "refusing an unowned native Packages directory"
        )
        return None
    require(
        state.is_file() and not state.is_symlink(), "ownership state must be regular"
    )
    raw = state.read_bytes()
    require(len(raw) <= 1024 * 1024, "ownership state byte bound")
    old = protocol.plan.decode(raw.decode("utf-8"))
    require(
        isinstance(old, dict)
        and set(old) == {"version", "packages"}
        and type(old["version"]) is int
        and old["version"] == 1,
        "invalid ownership state",
    )
    require(isinstance(old["packages"], dict), "invalid owned packages map")
    require(
        regular_directory(cache),
        "owned native Packages directory is missing or replaced",
    )
    require(
        set(old["packages"]) == {entry.name for entry in cache.iterdir()},
        "owned checkout set is missing, stale, or has extra paths",
    )
    for name, digest in old["packages"].items():
        require(
            protocol.legacy.safe_name(name)
            and isinstance(digest, str)
            and re.fullmatch("[0-9a-f]{64}", digest),
            "invalid owned checkout identity",
        )
        checkout = cache / name
        require(regular_directory(checkout), "owned checkout was replaced")
        require(
            hash_path(checkout, nix_hash) == digest,
            "refusing to replace an edited owned checkout",
        )
    return raw


def install(root, records, nix_hash):
    """Replace only a verified owned cache; rollback includes ownership bytes.

    All fetching and native runtime checks belong to the caller and precede
    this operation. Records come from the verified data derivation, never
    arbitrary lockfile shell fragments. Inner source symlinks retain bytes.
    """
    root = protocol.layout.canonical(root)
    protocol.native_absence(root)
    require(isinstance(records, list), "invalid verified source records")
    destinations = set()
    for record in records:
        require(
            isinstance(record, dict) and set(record) == {"name", "path", "source"},
            "closed verified source record",
        )
        require(
            protocol.legacy.safe_name(record["name"]), "unsafe verified source name"
        )
        path = record["path"]
        require(
            isinstance(path, str)
            and path.startswith("Packages/")
            and path.count("/") == 1,
            "unsafe verified native cache path",
        )
        name = path[len("Packages/") :]
        require(
            protocol.legacy.safe_name(name)
            and name.startswith(record["name"] + "-")
            and protocol.legacy.version_valid(name[len(record["name"]) + 1 :]),
            "native cache path does not match its observed name/version",
        )
        require(name not in destinations, "duplicate verified native cache path")
        destinations.add(name)
        source = Path(record["source"])
        require(
            source.is_absolute()
            and str(source).startswith("/nix/store/")
            and regular_directory(source),
            "verified immutable checkout required",
        )
        require(
            regular_directory(source / ".git"),
            "verified checkout must carry real Git metadata",
        )
    old_raw = existing_state(root, nix_hash)
    cache = root / "Packages"
    build = root / ".build"
    private = build / "swiftpm-purifier"
    state = private / STATE
    build_existed, private_existed = build.exists(), private.exists()
    # A sibling transaction is on the destination filesystem and never
    # appears in the project's structural source inventory.
    transaction = Path(
        tempfile.mkdtemp(prefix=".swiftpm-purifier-install-", dir=root.parent)
    )
    staged = transaction / "Packages"
    backup = transaction / "previous-Packages"
    staged.mkdir()
    moved_old = moved_new = state_replaced = False
    try:
        hashes = {}
        for record in records:
            source = Path(record["source"])
            destination = staged / Path(record["path"]).name
            expected = hash_path(source, nix_hash)
            protocol.writable_copy(source, destination)
            require(
                hash_path(destination, nix_hash) == expected,
                "owned copy changed verified Git/source bytes",
            )
            hashes[destination.name] = expected
        new_raw = (
            json.dumps(
                {"version": 1, "packages": hashes},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        # Recheck after potentially expensive copies; do not overwrite a
        # user's concurrent edit while preparing the transaction.
        require(
            existing_state(root, nix_hash) == old_raw,
            "ownership state changed during staging",
        )
        protocol.native_absence(root)
        if old_raw == new_raw:
            return False
        private.mkdir(parents=True, exist_ok=True)
        pending = transaction / "next-state"
        pending.write_bytes(new_raw)
        if cache.exists():
            os.replace(cache, backup)
            moved_old = True
        os.replace(staged, cache)
        moved_new = True
        os.replace(pending, state)
        state_replaced = True
        return True
    except BaseException:
        if moved_new:
            shutil.rmtree(cache)
        if moved_old:
            os.replace(backup, cache)
        if state_replaced:
            if old_raw is None:
                state.unlink()
            else:
                rollback = transaction / "rollback-state"
                rollback.write_bytes(old_raw)
                os.replace(rollback, state)
        raise
    finally:
        shutil.rmtree(transaction)
        if not private_existed and private.is_dir() and not any(private.iterdir()):
            private.rmdir()
        if not build_existed and build.is_dir() and not any(build.iterdir()):
            build.rmdir()


def uninstall(root, nix_hash):
    """Remove only the byte-verified owned cache and its ownership record."""
    root = protocol.layout.canonical(root)
    old = existing_state(root, nix_hash)
    if old is None:
        return
    cache = root / "Packages"
    state = root / ".build/swiftpm-purifier" / STATE
    transaction = Path(
        tempfile.mkdtemp(prefix=".swiftpm-purifier-remove-", dir=root.parent)
    )
    moved_cache = moved_state = False
    try:
        os.replace(cache, transaction / "Packages")
        moved_cache = True
        os.replace(state, transaction / STATE)
        moved_state = True
    except BaseException:
        if moved_cache:
            os.replace(transaction / "Packages", cache)
        if moved_state:
            os.replace(transaction / STATE, state)
        raise
    finally:
        shutil.rmtree(transaction)
