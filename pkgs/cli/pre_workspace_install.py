# SPDX-License-Identifier: MIT
"""Transactional ownership of format7's real native Packages directory."""

import json
import os
from pathlib import Path
import re
import shutil
import tempfile

import pre_workspace as protocol
import pre_workspace_git as git_metadata

STATE = ".swiftpm-purifier-owned.json"
LEGACY_STATE = Path(".build/swiftpm-purifier/preworkspace-owned.json")


def require(condition, message):
    if not condition:
        raise protocol.StateError(message)


def hash_path(path, nix_hash):
    from pre_workspace_git import command

    return command([nix_hash, "--type", "sha256", str(path)])


def regular_directory(path):
    return path.is_dir() and not path.is_symlink()


def existing_state(root, nix_hash, git="git"):
    """Verify ownership bytes and actual index semantics before any write."""
    cache = root / "Packages"
    build = root / ".build"
    private = build / "swiftpm-purifier"
    for ancestor in (build, private):
        require(
            not ancestor.is_symlink()
            and (not os.path.lexists(ancestor) or ancestor.is_dir()),
            "legacy ownership ancestor must be a real directory or absent",
        )
    require(
        not os.path.lexists(root / LEGACY_STATE),
        "old private build ownership records require explicit removal; no automatic migration",
    )
    state = cache / STATE
    if not os.path.lexists(cache):
        return None
    require(regular_directory(cache), "native Packages must be a real directory")
    require(os.path.lexists(state), "refusing an unowned native Packages directory")
    require(
        state.is_file() and not state.is_symlink(), "ownership marker must be regular"
    )
    raw = state.read_bytes()
    require(len(raw) <= 1024 * 1024, "ownership marker byte bound")
    old = protocol.plan.decode(raw.decode("utf-8"))
    require(
        isinstance(old, dict)
        and set(old) == {"version", "packages"}
        and type(old["version"]) is int
        and old["version"] == 2,
        "invalid ownership state",
    )
    canonical = (json.dumps(old, sort_keys=True, separators=(",", ":")) + "\n").encode()
    require(raw == canonical, "ownership marker bytes are not canonical")
    require(isinstance(old["packages"], dict), "invalid owned packages map")
    require(
        regular_directory(cache),
        "owned native Packages directory is missing or replaced",
    )
    require(
        set(old["packages"]) | {STATE} == {entry.name for entry in cache.iterdir()},
        "owned checkout set is missing, stale, or has extra paths",
    )
    for name, digest in old["packages"].items():
        require(
            protocol.legacy.safe_name(name)
            and name != STATE
            and isinstance(digest, str)
            and re.fullmatch("[0-9a-f]{64}", digest),
            "invalid owned checkout identity",
        )
        checkout = cache / name
        require(regular_directory(checkout), "owned checkout was replaced")
        require(
            git_metadata.normalized_hash(checkout, nix_hash, git) == digest,
            "refusing to replace an edited owned checkout",
        )
    return raw


def install(root, records, nix_hash, git="git"):
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
        require(
            name != STATE and name not in destinations,
            "duplicate or reserved verified native cache path",
        )
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
    old_raw = existing_state(root, nix_hash, git)
    cache = root / "Packages"
    # A sibling transaction is on the destination filesystem and never
    # appears in the project's structural source inventory.
    transaction = Path(
        tempfile.mkdtemp(prefix=".swiftpm-purifier-install-", dir=root.parent)
    )
    staged = transaction / "Packages"
    backup = transaction / "previous-Packages"
    staged.mkdir()
    moved_old = moved_new = False
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
            hashes[destination.name] = git_metadata.normalized_hash(
                destination, nix_hash, git
            )
        new_raw = (
            json.dumps(
                {"version": 2, "packages": hashes},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        # Recheck after potentially expensive copies; do not overwrite a
        # user's concurrent edit while preparing the transaction.
        require(
            existing_state(root, nix_hash, git) == old_raw,
            "ownership state changed during staging",
        )
        protocol.native_absence(root)
        if old_raw == new_raw:
            return False
        (staged / STATE).write_bytes(new_raw)
        if cache.exists():
            os.replace(cache, backup)
            moved_old = True
        os.replace(staged, cache)
        moved_new = True
        require(
            (cache / STATE).read_bytes() == new_raw,
            "ownership marker changed during installation",
        )
        return True
    except BaseException:
        if moved_new:
            shutil.rmtree(cache)
        if moved_old:
            os.replace(backup, cache)
        raise
    finally:
        shutil.rmtree(transaction)


def uninstall(root, nix_hash, git="git"):
    """Remove only the byte-verified owned cache and its ownership record."""
    root = protocol.layout.canonical(root)
    old = existing_state(root, nix_hash, git)
    if old is None:
        return
    cache = root / "Packages"
    transaction = Path(
        tempfile.mkdtemp(prefix=".swiftpm-purifier-remove-", dir=root.parent)
    )
    backup = transaction / "Packages"
    moved = False
    try:
        os.replace(cache, backup)
        moved = True
        require(
            (backup / STATE).read_bytes() == old,
            "ownership marker changed during uninstall",
        )
    except BaseException:
        if moved:
            os.replace(backup, cache)
        raise
    finally:
        shutil.rmtree(transaction)
