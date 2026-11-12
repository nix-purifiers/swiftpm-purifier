# SPDX-License-Identifier: MIT
"""Owned edited-graph data for native dependency manifest probes."""

import os
from pathlib import Path
import shutil

import dependencies_v1_cache as cache
import dependencies_v1_layout as layout
import legacy_state as legacy
from dependencies_v1_transaction import canonical


def edited_state(root, pins):
    return {
        "version": 1,
        "object": {
            "dependencies": [
                {
                    "basedOn": None,
                    "name": name,
                    "repositoryURL": pin["sourceLocation"],
                    "state": {
                        "name": "edited",
                        "path": str(Path(root) / "Packages" / name),
                    },
                    "subpath": name,
                }
                for name, pin in sorted(pins.items())
            ]
        },
    }


def writable_source(source, destination):
    shutil.copytree(source, destination, symlinks=True)
    cache.require(
        not os.path.lexists(destination / ".git"),
        "manifest probe sources must be VCS-free",
    )
    cache.source_tree(destination)
    for directory, names, files in os.walk(destination):
        Path(directory).chmod(Path(directory).stat().st_mode | 0o700)
        for name in files:
            path = Path(directory) / name
            path.chmod(path.stat().st_mode | 0o200)


def prepare_probe(root, pins, sources, current_name):
    """Seed a fresh owned probe from the caller's verified complete locked set.

    All source paths must already be bound to their locked NAR by the caller.
    No source is fetched or inferred here; each other pin is copied and typed
    before native code can consume it. The current package is the probe root.
    """
    root = layout.canonical(root)
    cache.directory(root)
    cache.require(
        isinstance(pins, dict) and set(pins) == set(sources) and current_name in pins,
        "complete locked manifest probe graph",
    )
    cache.require(
        not os.path.lexists(root / "Packages") and not os.path.lexists(root / ".build"),
        "manifest probe metadata must start absent",
    )
    allowed = set()
    for name, pin in pins.items():
        cache.require(
            legacy.safe_name(name)
            and len(name.encode()) <= 255
            and pin.get("name") == name
            and legacy.git_location(pin.get("sourceLocation")),
            "manifest probe package identity",
        )
        if name == current_name:
            continue
        path = layout.canonical(sources[name])
        cache.directory(path)
        allowed.add(root / "Packages" / name)
    (root / "Packages").mkdir()
    for name in sorted(pins):
        if name != current_name:
            writable_source(Path(sources[name]), root / "Packages" / name)
    others = {name: pin for name, pin in pins.items() if name != current_name}
    (root / ".build").mkdir()
    (root / ".build/dependencies-state.json").write_bytes(
        canonical(edited_state(root, others)) + b"\n"
    )
    return allowed


def verify_probe(root, pins, current_name, description):
    """Native resolution may retain only known edited locked dependencies."""
    root = layout.canonical(root)
    expected = {
        dep["name"]: dep
        for dep in edited_state(
            root, {name: pin for name, pin in pins.items() if name != current_name}
        )["object"]["dependencies"]
    }
    path = root / ".build/dependencies-state.json"
    if not os.path.lexists(path):
        cache.require(
            description.get("dependencies") == [],
            "manifest probe lost dependency state",
        )
        return
    state = cache.decode(cache.regular_bytes(root, ".build/dependencies-state.json"))
    cache.require(
        isinstance(state, dict)
        and set(state) == {"version", "object"}
        and type(state["version"]) is int
        and state["version"] == 1
        and isinstance(state["object"], dict)
        and set(state["object"]) == {"dependencies"}
        and isinstance(state["object"]["dependencies"], list),
        "manifest probe native state",
    )
    actual = {}
    for dep in state["object"]["dependencies"]:
        cache.require(
            isinstance(dep, dict)
            and isinstance(dep.get("name"), str)
            and dep["name"] not in actual
            and dep["name"] in expected
            and dep == expected[dep["name"]],
            "manifest probe escaped edited locked graph",
        )
        actual[dep["name"]] = dep
    urls = {
        pin["sourceLocation"]: name
        for name, pin in pins.items()
        if name != current_name
    }
    cache.require(
        isinstance(description.get("dependencies"), list),
        "manifest probe declared dependencies",
    )
    for dependency in description["dependencies"]:
        cache.require(
            isinstance(dependency, dict)
            and dependency.get("url") in urls
            and urls[dependency["url"]] in actual,
            "manifest probe direct dependency state join",
        )
