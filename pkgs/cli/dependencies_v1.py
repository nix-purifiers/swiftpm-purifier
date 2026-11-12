# SPDX-License-Identifier: MIT
"""Closed portable format 8 for native dependencies-state version 1.

Structural validation does not grant permission to execute a native command.
Source currency, selectors, cache confinement and ownership are separate gates.
"""

import re

import legacy_state as legacy
import pre_workspace as portable
import pre_workspace_plan as plan

FORMAT = 8
StateError = plan.Reject
require = plan.require
content_hash = portable.content_hash
TOP = {
    "version",
    "nativeProtocol",
    "nativeState",
    "package",
    "rootDirectory",
    "manifestHash",
    "sourceLayoutHash",
    "nativeToolLock",
    "pins",
    "products",
    "localDependencies",
    "binaryTargets",
    "contentHash",
}


def hex_digest(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def names(values, module=False):
    return (
        isinstance(values, list)
        and bool(values)
        and all(
            isinstance(value, str)
            and (
                re.fullmatch("[A-Za-z_][A-Za-z0-9_]*", value) is not None
                if module
                else legacy.safe_name(value)
            )
            for value in values
        )
        and len(values) == len(set(values))
    )


def validate(lock):
    """Validate a decoded lock without accessing a caller filesystem."""
    require(isinstance(lock, dict) and set(lock) == TOP, "closed format8 top schema")
    require(
        type(lock["version"]) is int
        and lock["version"] == FORMAT
        and lock["nativeProtocol"] == "dependencies-state-v1",
        "format8 discriminator",
    )
    state = lock["nativeState"]
    require(
        isinstance(state, dict)
        and set(state) == {"file", "version"}
        and state["file"] == ".build/dependencies-state.json"
        and type(state["version"]) is int
        and state["version"] == 1,
        "fixed native protocol descriptor",
    )
    require(
        legacy.safe_name(lock["package"]) and legacy.safe_name(lock["rootDirectory"]),
        "package/root name",
    )
    require(
        hex_digest(lock["manifestHash"]) and hex_digest(lock["sourceLayoutHash"]),
        "source hash types",
    )
    require(
        lock["localDependencies"] == [] and lock["binaryTargets"] == [],
        "unsupported local/binary graph",
    )
    native = lock["nativeToolLock"]
    require(
        isinstance(native, dict)
        and set(native) == {"file", "sha256"}
        and native["file"] == "Package.resolved"
        and (native["sha256"] is None or hex_digest(native["sha256"])),
        "native exact byte/absence binding",
    )
    require(
        isinstance(lock["pins"], dict) and 0 < len(lock["pins"]) <= 1024,
        "bounded nonempty Git graph",
    )
    locations = set()
    for name, pin in lock["pins"].items():
        require(
            isinstance(pin, dict)
            and set(pin) == {"name", "sourceLocation", "revision", "version", "hash"},
            "closed format8 pin",
        )
        require(legacy.safe_name(name) and pin["name"] == name, "pin name")
        require(
            legacy.git_location(pin["sourceLocation"])
            and pin["sourceLocation"] not in locations,
            "unique Git source",
        )
        locations.add(pin["sourceLocation"])
        require(
            isinstance(pin["revision"], str)
            and legacy.REVISION.fullmatch(pin["revision"]) is not None,
            "actual revision",
        )
        require(legacy.version_valid(pin["version"]), "semver checkout only")
        require(portable.digest(pin["hash"]), "source NAR hash")
    require(
        isinstance(lock["products"], list) and len(lock["products"]) <= 1024,
        "bounded product list",
    )
    seen = set()
    for product in lock["products"]:
        require(
            isinstance(product, dict)
            and product.get("type") in ("library", "executable"),
            "product type",
        )
        fields = {"name", "type", "modules", "declaration"}
        if product["type"] == "library":
            fields.add("linkage")
        require(set(product) == fields, "closed format8 product")
        require(
            legacy.safe_name(product["name"]) and product["name"] not in seen,
            "unique product name",
        )
        seen.add(product["name"])
        require(names(product["modules"], module=True), "actual export module closure")
        declaration = product["declaration"]
        require(isinstance(declaration, dict), "product declaration")
        if declaration.get("kind") == "explicit":
            require(
                set(declaration) == {"kind", "targets"}
                and names(declaration["targets"]),
                "explicit target declaration",
            )
        else:
            require(
                product["type"] == "executable"
                and set(declaration) == {"kind", "target"}
                and declaration["kind"] == "inferred-executable"
                and legacy.safe_name(declaration["target"])
                and declaration["target"] == product["name"],
                "inferred executable declaration",
            )
        if product["type"] == "library":
            require(
                product["linkage"] in ("automatic", "static", "dynamic"),
                "library linkage",
            )
    require(
        portable.digest(lock["contentHash"])
        and lock["contentHash"] == content_hash(lock),
        "content hash binding",
    )
    return lock


def decode(raw):
    require(
        isinstance(raw, bytes) and len(raw) <= 1024 * 1024, "portable lock byte bound"
    )
    try:
        text = raw.decode("utf-8")
    except UnicodeError as error:
        raise StateError("portable lock UTF8") from error
    return validate(plan.decode(text))
