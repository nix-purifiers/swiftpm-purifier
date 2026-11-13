# SPDX-License-Identifier: MIT
"""Closed native dependency-state-v3 records for portable format 9.

Native version 3 keeps ``kind`` and ``path`` references. It is neither the
version-2 boolean reference nor the later ``location`` reference protocol.
This module never changes caller files or admits a different native version.
"""

import copy
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

import dependencies_v1_cache as cache
import legacy_state as legacy

FORMAT = 9
NATIVE_VERSION = 3
LIMIT = 1024
RECORD_FIELDS = {"basedOn", "packageRef", "state", "subpath"}
REF_FIELDS = {"identity", "kind", "name", "path"}
Reject = cache.Reject
require = cache.require


def utf8_bound(value, limit):
    if not isinstance(value, str):
        return False
    try:
        return 0 < len(value.encode("utf-8")) <= limit
    except UnicodeError:
        return False


def safe_name(value):
    return legacy.safe_name(value) and utf8_bound(value, 255)


def reference(value):
    require(isinstance(value, dict) and set(value) == REF_FIELDS, "v3 reference fields")
    require(
        safe_name(value["identity"]) and safe_name(value["name"]),
        "v3 reference identity/name",
    )
    require(value["kind"] in ("remote", "local"), "v3 reference kind")
    path = value["path"]
    require(
        utf8_bound(path, 4096) and not any(ord(c) < 32 or ord(c) == 127 for c in path),
        "v3 reference path",
    )
    return value


def checkout_subpath(location):
    require(legacy.git_location(location), "v3 remote Git location")
    if "://" in location:
        try:
            parsed = urlsplit(location)
        except ValueError as error:
            raise Reject("v3 Git URL") from error
        require(not parsed.query and not parsed.fragment, "v3 Git URL suffix")
        path = parsed.path
    else:
        path = location.split(":", 1)[1]
    name = path.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    require(safe_name(name), "v3 checkout subpath")
    return name


def local_directory(root, path, *, physical=True):
    """Require the actual lexical and physical in-tree local package.

    The owner must bind these observations to its source/currentness checks.
    A retained native state file alone never authorizes later mutation.
    """
    require(root is not None, "v3 local package requires an owned source root")
    root = Path(root)
    require(root.is_absolute() and not root.is_symlink(), "v3 source root")
    root = root.resolve(strict=True)
    candidate = Path(path)
    require(candidate.is_absolute() and str(candidate) == path, "v3 local spelling")
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise Reject("v3 local path escapes source") from error
    require(
        relative.parts
        and all(safe_name(part) for part in relative.parts)
        and relative.parts[0]
        not in (".build", ".git", "Packages", ".swiftpm-purifier"),
        "v3 local source path",
    )
    if not physical:
        # Historical local names are lexical data, never paths to inspect or
        # mutate. Only current source validation follows confined descriptors.
        return relative.as_posix(), str(candidate)
    current = root
    for part in relative.parts:
        current = current / part
        facts = current.lstat()
        require(stat.S_ISDIR(facts.st_mode), "v3 local directory must not be a link")
    require(current.resolve(strict=True) == candidate, "v3 local physical path")
    cache.regular_bytes(root, relative / "Package.swift", 2 * 1024 * 1024)
    cache.directory(current)
    require(current.resolve(strict=True) == candidate, "v3 local path changed")
    facts = current.stat()
    return relative.as_posix(), (facts.st_dev, facts.st_ino)


def checkout(record):
    require(
        isinstance(record, dict) and set(record) == RECORD_FIELDS,
        "v3 checkout fields",
    )
    ref = reference(record["packageRef"])
    require(ref["kind"] == "remote" and record["basedOn"] is None, "v3 checkout kind")
    require(record["subpath"] == checkout_subpath(ref["path"]), "v3 checkout path join")
    state = record["state"]
    require(
        isinstance(state, dict)
        and set(state) == {"name", "checkoutState"}
        and state["name"] == "checkout",
        "v3 checkout state fields",
    )
    value = state["checkoutState"]
    require(
        isinstance(value, dict)
        and set(value) == {"branch", "revision", "version"}
        and value["branch"] is None
        and isinstance(value["revision"], str)
        and legacy.REVISION.fullmatch(value["revision"]) is not None
        and legacy.version_valid(value["version"]),
        "v3 semantic-version checkout",
    )
    return record


def native_index(value, root=None, *, allow_edited=False, physical_locals=True):
    require(
        isinstance(value, dict)
        and set(value) == {"object", "version"}
        and type(value["version"]) is int
        and value["version"] == NATIVE_VERSION,
        "v3 native discriminator",
    )
    body = value["object"]
    require(
        isinstance(body, dict)
        and set(body) == {"dependencies"}
        and isinstance(body["dependencies"], list)
        and len(body["dependencies"]) <= LIMIT,
        "v3 native dependency envelope",
    )
    result, names, locations, local_paths = {}, set(), set(), set()
    installed_paths = set()
    for record in body["dependencies"]:
        require(
            isinstance(record, dict) and set(record) == RECORD_FIELDS,
            "v3 dependency fields",
        )
        ref = reference(record["packageRef"])
        identity, name = ref["identity"], ref["name"]
        require(
            identity not in result and name not in names, "v3 duplicate identity/name"
        )
        if ref["kind"] == "local":
            require(
                record["basedOn"] is None
                and record["state"] == {"name": "local"}
                and record["subpath"] == identity,
                "v3 local record",
            )
            _, physical = local_directory(root, ref["path"], physical=physical_locals)
            require(physical not in local_paths, "v3 local directory alias")
            local_paths.add(physical)
        else:
            require(ref["path"] not in locations, "v3 duplicate source location")
            locations.add(ref["path"])
            if record["state"] == {"name": "edited", "path": None}:
                require(allow_edited, "v3 caller checkout must not be edited")
                based = checkout(record["basedOn"])
                require(
                    based["packageRef"] == ref and record["subpath"] == name,
                    "v3 edited reference/path join",
                )
            else:
                checkout(record)
            require(name not in installed_paths, "v3 installed path alias")
            installed_paths.add(name)
        names.add(name)
        result[identity] = record
    return result


def edited_state(value, root=None, *, physical_locals=True):
    """Derive exactly the original v3 edited geometry, preserving local records."""
    records = native_index(value, root, physical_locals=physical_locals)
    projected = []
    for identity in sorted(records):
        original = records[identity]
        ref = original["packageRef"]
        if ref["kind"] == "local":
            projected.append(copy.deepcopy(original))
        else:
            projected.append(
                {
                    "basedOn": copy.deepcopy(original),
                    "packageRef": copy.deepcopy(ref),
                    "state": {"name": "edited", "path": None},
                    "subpath": ref["name"],
                }
            )
    result = {"object": {"dependencies": projected}, "version": NATIVE_VERSION}
    native_index(result, root, allow_edited=True, physical_locals=physical_locals)
    return result


TOP = {
    "version",
    "package",
    "rootDirectory",
    "manifestHash",
    "products",
    "workspaceStateVersion",
    "pins",
    "binaryTargets",
    "localDependencies",
    "contentHash",
}
PIN_FIELDS = {"name", "sourceLocation", "revision", "version", "hash"}


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def content_hash(value):
    try:
        raw = canonical(
            {key: item for key, item in value.items() if key != "contentHash"}
        )
    except (UnicodeError, TypeError, ValueError) as error:
        raise Reject("portable9 canonical JSON") from error
    return "sha256-" + base64.b64encode(hashlib.sha256(raw).digest()).decode("ascii")


def pins_index(value):
    """Join the selected original pins by their explicit source location."""
    require(
        isinstance(value, dict)
        and set(value) == {"version", "object"}
        and type(value["version"]) is int
        and value["version"] == 1,
        "v3 requires selected native pins v1",
    )
    body = value["object"]
    require(
        isinstance(body, dict)
        and set(body) == {"pins"}
        and isinstance(body["pins"], list)
        and len(body["pins"]) <= LIMIT,
        "v3 selected pin envelope",
    )
    result, names = {}, set()
    for pin in body["pins"]:
        require(
            isinstance(pin, dict) and set(pin) == {"package", "repositoryURL", "state"},
            "v3 selected pin fields",
        )
        name, location, state = pin["package"], pin["repositoryURL"], pin["state"]
        require(safe_name(name) and name not in names, "v3 selected pin package")
        checkout_subpath(location)
        require(location not in result, "v3 duplicate selected pin location")
        require(
            isinstance(state, dict)
            and set(state) == {"branch", "revision", "version"}
            and state["branch"] is None
            and isinstance(state["revision"], str)
            and legacy.REVISION.fullmatch(state["revision"]) is not None
            and legacy.version_valid(state["version"]),
            "v3 selected semantic-version pin",
        )
        result[location] = pin
        names.add(name)
    return result


def local_records(lock, root, *, physical=True):
    result = []
    for dependency in lock["localDependencies"]:
        require(
            isinstance(dependency, dict) and set(dependency) == RECORD_FIELDS,
            "v3 portable local fields",
        )
        ref = reference(dependency["packageRef"])
        path = ref["path"]
        require(
            ref["kind"] == "local"
            and path.startswith("@root@/")
            and dependency["basedOn"] is None
            and dependency["state"] == {"name": "local"}
            and dependency["subpath"] == ref["identity"],
            "v3 portable local record",
        )
        relative = path[len("@root@/") :]
        require(
            relative and all(safe_name(p) for p in relative.split("/")),
            "v3 local template",
        )
        row = copy.deepcopy(dependency)
        row["packageRef"]["path"] = str(Path(root) / relative)
        local_directory(root, row["packageRef"]["path"], physical=physical)
        result.append(row)
    return result


def checkout_records(lock, root, *, physical_locals=True):
    result = []
    for identity, pin in lock["pins"].items():
        result.append(
            {
                "basedOn": None,
                "packageRef": {
                    "identity": identity,
                    "kind": "remote",
                    "name": pin["name"],
                    "path": pin["sourceLocation"],
                },
                "state": {
                    "name": "checkout",
                    "checkoutState": {
                        "branch": None,
                        "revision": pin["revision"],
                        "version": pin["version"],
                    },
                },
                "subpath": checkout_subpath(pin["sourceLocation"]),
            }
        )
    result.extend(local_records(lock, root, physical=physical_locals))
    return {"object": {"dependencies": result}, "version": NATIVE_VERSION}


def manifest_hash(root, locals_):
    """Format-9 domain: root and every selected in-tree local manifest."""
    root = Path(root)
    packages = [("", root)]
    for row in locals_:
        relative, _ = local_directory(root, row["packageRef"]["path"])
        packages.append((relative + "/", root / relative))
    records = []
    for prefix, directory in packages:
        require(not directory.is_symlink(), "v3 manifest directory link")
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            names = sorted(os.listdir(fd))
            require("Package.swift" in names, "v3 package manifest missing")
            for name in names:
                if re.fullmatch(r"Package(@swift-[0-9.]+)?\.swift", name) is None:
                    continue
                raw = cache.regular_bytes(root, prefix + name, 2 * 1024 * 1024)
                records.append([prefix + name, hashlib.sha256(raw).hexdigest()])
        finally:
            os.close(fd)
    return hashlib.sha256(canonical(sorted(records))).hexdigest()


def validate_lock(lock, selected_pins, root, *, current=True):
    require(isinstance(lock, dict) and set(lock) == TOP, "closed portable9 root fields")
    require(
        type(lock["version"]) is int
        and lock["version"] == FORMAT
        and type(lock["workspaceStateVersion"]) is int
        and lock["workspaceStateVersion"] == NATIVE_VERSION,
        "portable9/native3 discriminator",
    )
    require(
        safe_name(lock["package"]) and safe_name(lock["rootDirectory"]),
        "portable9 root/package name",
    )
    require(lock["contentHash"] == content_hash(lock), "portable9 content hash")
    require(
        isinstance(lock["pins"], dict)
        and len(lock["pins"]) <= LIMIT
        and isinstance(lock["localDependencies"], list)
        and len(lock["localDependencies"]) <= LIMIT
        and lock["binaryTargets"] == [],
        "portable9 graph fields",
    )
    require(
        isinstance(lock["products"], list) and len(lock["products"]) <= LIMIT,
        "portable9 product list",
    )
    products = set()
    for product in lock["products"]:
        require(isinstance(product, dict), "portable9 product record")
        kind = product.get("type")
        fields = {"name", "type", "linkage"} if kind == "library" else {"name", "type"}
        require(
            set(product) == fields
            and kind in ("library", "executable")
            and safe_name(product["name"])
            and product["name"] not in products
            and (
                kind != "library"
                or product["linkage"] in ("static", "dynamic", "automatic")
            ),
            "portable9 product schema",
        )
        products.add(product["name"])
    selected = pins_index(selected_pins)
    locations = set()
    for identity, pin in lock["pins"].items():
        require(safe_name(identity) and isinstance(pin, dict), "portable9 pin identity")
        require(
            set(pin) in (PIN_FIELDS, PIN_FIELDS | {"url"})
            and safe_name(pin["name"])
            and isinstance(pin["hash"], str)
            and legacy.HASH.fullmatch(pin["hash"]) is not None,
            "portable9 pin fields/hash",
        )
        location = pin["sourceLocation"]
        checkout_subpath(location)
        require(location not in locations, "portable9 source alias")
        locations.add(location)
        if "url" in pin:
            checkout_subpath(pin["url"])
            require(pin["url"] != location, "portable9 redundant mirror")
        native = selected.get(location)
        require(
            native is not None
            and native["package"] == pin["name"]
            and native["state"]["revision"] == pin["revision"]
            and native["state"]["version"] == pin["version"],
            "portable9 selected pin join",
        )
    require(locations == set(selected), "portable9 selected pin closure")
    graph = checkout_records(lock, root, physical_locals=current)
    index = native_index(graph, root, physical_locals=current)
    require(
        lock["package"] not in {row["packageRef"]["name"] for row in index.values()},
        "portable9 root/dependency name alias",
    )
    require(
        isinstance(lock["manifestHash"], str)
        and re.fullmatch(r"[0-9a-f]{64}", lock["manifestHash"]) is not None
        and (
            not current
            or lock["manifestHash"] == manifest_hash(root, local_records(lock, root))
        ),
        "portable9 root/local manifest currency",
    )
    return lock


def validate_raw(raw, selected_raw, root):
    require(
        isinstance(raw, bytes)
        and len(raw) <= 1024 * 1024
        and isinstance(selected_raw, bytes)
        and len(selected_raw) <= 1024 * 1024,
        "portable9 selected byte bounds/presence",
    )
    return validate_lock(cache.decode(raw), cache.decode(selected_raw), root)
