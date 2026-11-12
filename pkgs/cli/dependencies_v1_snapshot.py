# SPDX-License-Identifier: MIT
"""Isolated native cache snapshots for dependencies-state version 1."""

import configparser
import os
from pathlib import Path
import shutil

import dependencies_v1_cache as cache
import dependencies_v1_layout as layout


def snapshot(root, destination, environment):
    """Copy validated source/cache data without executing Git or Swift.

    The destination is a new owned directory. Only the validated checkout
    alternate and origin locator change. Caller metadata is never rewritten.
    Native consumers must validate the returned copy again before execution.
    """
    root = layout.canonical(root)
    destination = layout.canonical(destination)
    cache.require(
        not destination.is_relative_to(root), "snapshot must be outside caller"
    )
    cache.require(not os.path.lexists(destination), "snapshot destination exists")
    cache.directory(destination.parent)
    layout.source_layout(root)
    records = cache.validate(root, environment)
    # Bound and retain native control bytes before the first destination write.
    state = cache.regular_bytes(root, ".build/dependencies-state.json")
    registry = cache.regular_bytes(root, ".build/repositories/checkouts-state.json")
    locator_bytes = {}
    for record in records:
        subpath = record["subpath"]
        prefix = ".build/checkouts/" + subpath + "/.git/"
        locator_bytes[subpath] = (
            cache.regular_bytes(root, prefix + "config"),
            cache.regular_bytes(root, prefix + "objects/info/alternates"),
        )
    shutil.copytree(
        root,
        destination,
        symlinks=True,
        ignore=lambda parent, names: (
            [name for name in names if name in {".build", "Packages", ".git"}]
            if Path(parent) == root
            else []
        ),
    )
    (destination / ".build/checkouts").mkdir(parents=True)
    (destination / ".build/repositories").mkdir()
    for record in records:
        subpath = record["subpath"]
        old_bare = root / ".build/repositories" / subpath
        new_bare = destination / ".build/repositories" / subpath
        old_checkout = root / ".build/checkouts" / subpath
        new_checkout = destination / ".build/checkouts" / subpath
        shutil.copytree(old_bare, new_bare, symlinks=True)
        shutil.copytree(old_checkout, new_checkout, symlinks=True)
        config, alternate = locator_bytes[subpath]
        # These copied files must still match the preflight observation.
        relative = ".build/checkouts/" + subpath + "/.git/"
        cache.require(
            cache.regular_bytes(destination, relative + "config") == config,
            "checkout config changed during snapshot",
        )
        cache.require(
            cache.regular_bytes(destination, relative + "objects/info/alternates")
            == alternate,
            "checkout alternate changed during snapshot",
        )
        cache.require(
            alternate == (str(old_bare / "objects") + "\n").encode(),
            "native alternate spelling",
        )
        # Do not parse and regenerate Git configuration. Replace only the
        # single measured URL value, retaining all other original bytes.
        old = str(old_bare).encode()
        new = str(new_bare).encode()
        cache.require(config.count(old) == 1, "ambiguous checkout origin encoding")
        updated = config.replace(old, new)
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.read_string(updated.decode())
        cache.require(
            parser['remote "origin"']["url"] == str(new_bare), "owned origin rewrite"
        )
        (new_checkout / ".git/config").write_bytes(updated)
        (new_checkout / ".git/objects/info/alternates").write_bytes(
            (str(new_bare / "objects") + "\n").encode()
        )
    (destination / ".build/dependencies-state.json").write_bytes(state)
    (destination / ".build/repositories/checkouts-state.json").write_bytes(registry)
    cache.require(
        cache.regular_bytes(root, ".build/dependencies-state.json") == state
        and cache.regular_bytes(root, ".build/repositories/checkouts-state.json")
        == registry,
        "caller native metadata changed during snapshot",
    )
    cache.require(
        cache.validate(root, environment) == records,
        "caller cache changed during snapshot",
    )
    cache.require(
        cache.validate(destination, environment) == records, "snapshot cache identity"
    )
    cache.require(
        layout.source_layout(root) == layout.source_layout(destination),
        "snapshot source layout",
    )
    return records
