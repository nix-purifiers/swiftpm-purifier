# SPDX-License-Identifier: MIT
"""Closed portable11 / native workspace5 LOCATION records with pins1.

Native checkout fields omit absent branch/version values. Selected pins keep
their original null fields. Neither representation is a PATH-family record.
"""

import copy
from pathlib import Path
import re

import dependencies_v1_cache as cache
import dependencies_v3 as common
import legacy_state as legacy

FORMAT = 11
NATIVE_VERSION = 5
LIMIT = common.LIMIT
TOP = common.TOP
RECORD_FIELDS = common.RECORD_FIELDS
REF_FIELDS = {"identity", "kind", "location", "name"}
PIN_FIELDS = {"name", "sourceLocation", "revision", "branch", "version", "hash"}
Reject = cache.Reject
require = cache.require
safe_name = common.safe_name
canonical = common.canonical
content_hash = common.content_hash
local_directory = common.local_directory
checkout_subpath = common.checkout_subpath


def reference(value):
    require(
        isinstance(value, dict) and set(value) == REF_FIELDS,
        "v5 LOCATION reference fields",
    )
    require(
        safe_name(value["identity"]) and safe_name(value["name"]), "v5 identity/name"
    )
    require(value["kind"] in ("remoteSourceControl", "fileSystem"), "v5 reference kind")
    location = value["location"]
    require(
        common.utf8_bound(location, 4096)
        and not any(ord(c) < 32 or ord(c) == 127 for c in location),
        "v5 reference location",
    )
    return value


def checkout_state(value, *, selected=False):
    require(isinstance(value, dict), "v5 checkout state object")
    if selected:
        require(
            set(value) == {"revision", "branch", "version"}, "v5 pins1 checkout fields"
        )
    else:
        require(
            set(value)
            in ({"revision"}, {"revision", "branch"}, {"revision", "version"}),
            "v5 native optional checkout fields",
        )
    require(
        isinstance(value["revision"], str)
        and legacy.REVISION.fullmatch(value["revision"]) is not None,
        "v5 checkout revision",
    )
    branch, version = value.get("branch"), value.get("version")
    require(
        branch is None
        or (
            common.utf8_bound(branch, 4096)
            and not any(ord(c) < 32 or ord(c) == 127 for c in branch)
        ),
        "v5 checkout branch",
    )
    require(version is None or legacy.version_valid(version), "v5 checkout version")
    require(branch is None or version is None, "v5 branch/version conflict")
    if not selected:
        require(
            all(item is not None for item in value.values()),
            "v5 native checkout must omit absent fields",
        )
    return {key: item for key, item in value.items() if item is not None}


def checkout(record):
    require(
        isinstance(record, dict) and set(record) == RECORD_FIELDS, "v5 checkout fields"
    )
    ref = reference(record["packageRef"])
    require(
        ref["kind"] == "remoteSourceControl" and record["basedOn"] is None,
        "v5 checkout kind",
    )
    require(
        record["subpath"] == checkout_subpath(ref["location"]), "v5 checkout subpath"
    )
    state = record["state"]
    require(
        isinstance(state, dict)
        and set(state) == {"name", "checkoutState"}
        and state["name"] == "sourceControlCheckout",
        "v5 checkout discriminator",
    )
    checkout_state(state["checkoutState"])
    return record


def native_value(records):
    return {
        "object": {"dependencies": records, "artifacts": []},
        "version": NATIVE_VERSION,
    }


def native_index(value, root=None, *, allow_edited=False, physical_locals=True):
    require(
        isinstance(value, dict)
        and set(value) == {"object", "version"}
        and type(value["version"]) is int
        and value["version"] == NATIVE_VERSION,
        "v5 native discriminator",
    )
    body = value["object"]
    require(
        isinstance(body, dict)
        and set(body) == {"dependencies", "artifacts"}
        and body["artifacts"] == []
        and isinstance(body["dependencies"], list)
        and len(body["dependencies"]) <= LIMIT,
        "v5 native envelope",
    )
    result, names, locations, local_paths, installed_paths = (
        {},
        set(),
        set(),
        set(),
        set(),
    )
    for record in body["dependencies"]:
        require(
            isinstance(record, dict) and set(record) == RECORD_FIELDS,
            "v5 dependency fields",
        )
        ref = reference(record["packageRef"])
        identity, name, location = ref["identity"], ref["name"], ref["location"]
        require(
            identity not in result and name not in names and location not in locations,
            "v5 dependency alias",
        )
        if ref["kind"] == "fileSystem":
            require(
                record["basedOn"] is None
                and record["state"] == {"name": "fileSystem", "path": location}
                and record["subpath"] == identity,
                "v5 local location/state.path join",
            )
            _, physical = local_directory(root, location, physical=physical_locals)
            require(physical not in local_paths, "v5 local directory alias")
            local_paths.add(physical)
        else:
            if record["state"] == {"name": "edited", "path": None}:
                require(allow_edited, "v5 caller checkout must not be edited")
                based = checkout(record["basedOn"])
                require(
                    based["packageRef"] == ref and record["subpath"] == identity,
                    "v5 edited reference/identity subpath",
                )
            else:
                checkout(record)
            require(identity not in installed_paths, "v5 installed path alias")
            installed_paths.add(identity)
        names.add(name)
        locations.add(location)
        result[identity] = record
    return result


def edited_state(value, root=None, *, physical_locals=True):
    records = native_index(value, root, physical_locals=physical_locals)
    result = []
    for identity, record in sorted(records.items()):
        if record["packageRef"]["kind"] == "fileSystem":
            result.append(copy.deepcopy(record))
        else:
            result.append(
                {
                    "basedOn": copy.deepcopy(record),
                    "packageRef": copy.deepcopy(record["packageRef"]),
                    "state": {"name": "edited", "path": None},
                    "subpath": identity,
                }
            )
    value = native_value(result)
    native_index(value, root, allow_edited=True, physical_locals=physical_locals)
    return value


def pins_index(value):
    require(
        isinstance(value, dict)
        and set(value) == {"version", "object"}
        and type(value["version"]) is int
        and value["version"] == 1,
        "portable11 requires native pins1",
    )
    body = value["object"]
    require(
        isinstance(body, dict)
        and set(body) == {"pins"}
        and isinstance(body["pins"], list)
        and len(body["pins"]) <= LIMIT,
        "v5 pins1 envelope",
    )
    result, names = {}, set()
    for pin in body["pins"]:
        require(
            isinstance(pin, dict) and set(pin) == {"package", "repositoryURL", "state"},
            "v5 pins1 record",
        )
        name, location = pin["package"], pin["repositoryURL"]
        require(safe_name(name) and name not in names, "v5 pins1 name")
        checkout_subpath(location)
        require(location not in result, "v5 pins1 location alias")
        checkout_state(pin["state"], selected=True)
        names.add(name)
        result[location] = pin
    return result


def local_records(lock, root, *, physical=True):
    records = []
    for dependency in lock["localDependencies"]:
        require(
            isinstance(dependency, dict) and set(dependency) == RECORD_FIELDS,
            "portable11 local fields",
        )
        ref = reference(dependency["packageRef"])
        location = ref["location"]
        require(
            ref["kind"] == "fileSystem"
            and location.startswith("@root@/")
            and dependency["basedOn"] is None
            and dependency["state"] == {"name": "fileSystem", "path": location}
            and dependency["subpath"] == ref["identity"],
            "portable11 local template join",
        )
        relative = location[len("@root@/") :]
        require(
            relative and all(safe_name(part) for part in relative.split("/")),
            "portable11 local template",
        )
        record = copy.deepcopy(dependency)
        absolute = str(Path(root) / relative)
        record["packageRef"]["location"] = absolute
        record["state"]["path"] = absolute
        local_directory(root, absolute, physical=physical)
        records.append(record)
    return records


def checkout_records(lock, root, *, physical_locals=True):
    records = []
    for identity, pin in lock["pins"].items():
        state = checkout_state(
            {key: pin[key] for key in ("revision", "branch", "version")}, selected=True
        )
        records.append(
            {
                "basedOn": None,
                "packageRef": {
                    "identity": identity,
                    "kind": "remoteSourceControl",
                    "name": pin["name"],
                    "location": pin["sourceLocation"],
                },
                "state": {"name": "sourceControlCheckout", "checkoutState": state},
                "subpath": checkout_subpath(pin["sourceLocation"]),
            }
        )
    records.extend(local_records(lock, root, physical=physical_locals))
    return native_value(records)


def manifest_hash(root, locals_):
    # Hashing is shared; the reference adapter is explicit and owns no paths.
    paths = [{"packageRef": {"path": row["packageRef"]["location"]}} for row in locals_]
    return common.manifest_hash(root, paths)


def validate_lock(lock, selected_pins, root, *, current=True):
    require(isinstance(lock, dict) and set(lock) == TOP, "portable11 root fields")
    require(
        type(lock["version"]) is int
        and lock["version"] == FORMAT
        and type(lock["workspaceStateVersion"]) is int
        and lock["workspaceStateVersion"] == NATIVE_VERSION,
        "portable11/native5 discriminator",
    )
    require(
        safe_name(lock["package"]) and safe_name(lock["rootDirectory"]),
        "portable11 package/root name",
    )
    require(lock["contentHash"] == content_hash(lock), "portable11 content hash")
    require(
        isinstance(lock["pins"], dict)
        and len(lock["pins"]) <= LIMIT
        and isinstance(lock["localDependencies"], list)
        and len(lock["localDependencies"]) <= LIMIT
        and lock["binaryTargets"] == [],
        "portable11 graph fields",
    )
    require(
        isinstance(lock["products"], list) and len(lock["products"]) <= LIMIT,
        "portable11 products",
    )
    names = set()
    for product in lock["products"]:
        require(isinstance(product, dict), "portable11 product object")
        kind = product.get("type")
        fields = {"name", "type", "linkage"} if kind == "library" else {"name", "type"}
        require(
            set(product) == fields
            and kind in ("library", "executable")
            and safe_name(product["name"])
            and product["name"] not in names
            and (
                kind != "library"
                or product["linkage"] in ("static", "dynamic", "automatic")
            ),
            "portable11 product schema",
        )
        names.add(product["name"])
    selected = pins_index(selected_pins)
    locations = set()
    for identity, pin in lock["pins"].items():
        require(
            safe_name(identity) and isinstance(pin, dict), "portable11 pin identity"
        )
        require(
            set(pin) in (PIN_FIELDS, PIN_FIELDS | {"url"})
            and safe_name(pin["name"])
            and isinstance(pin["hash"], str)
            and legacy.HASH.fullmatch(pin["hash"]) is not None,
            "portable11 pin fields/hash",
        )
        location = pin["sourceLocation"]
        checkout_subpath(location)
        require(location not in locations, "portable11 source alias")
        locations.add(location)
        if "url" in pin:
            checkout_subpath(pin["url"])
            require(pin["url"] != location, "portable11 redundant mirror")
        state = {key: pin[key] for key in ("revision", "branch", "version")}
        checkout_state(state, selected=True)
        native = selected.get(location)
        require(
            native is not None
            and native["package"] == pin["name"]
            and native["state"] == state,
            "portable11 selected pin join",
        )
    require(locations == set(selected), "portable11 selected pin closure")
    graph = checkout_records(lock, root, physical_locals=current)
    index = native_index(graph, root, physical_locals=current)
    require(
        lock["package"] not in {row["packageRef"]["name"] for row in index.values()},
        "portable11 root/dependency name alias",
    )
    require(
        isinstance(lock["manifestHash"], str)
        and re.fullmatch(r"[0-9a-f]{64}", lock["manifestHash"]) is not None
        and (
            not current
            or lock["manifestHash"] == manifest_hash(root, local_records(lock, root))
        ),
        "portable11 manifest currency",
    )
    return lock


def validate_raw(raw, selected_raw, root):
    require(
        isinstance(raw, bytes)
        and len(raw) <= 1024 * 1024
        and isinstance(selected_raw, bytes)
        and len(selected_raw) <= 1024 * 1024,
        "portable11 selected byte bounds/presence",
    )
    return validate_lock(cache.decode(raw), cache.decode(selected_raw), root)
