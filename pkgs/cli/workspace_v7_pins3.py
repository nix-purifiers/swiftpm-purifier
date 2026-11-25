# SPDX-License-Identifier: MIT
"""Closed portable12 / native workspace7 LOCATION records with pins3.

Private scope follows independently reviewed actual native context geometry. No older codec is
reinterpreted. Origin hashes attest one ordinary root manifest; additional
native variants require genuine controls before their admission.
"""

import copy
import hashlib
from pathlib import Path
import re

import dependencies_v1_cache as cache
import dependencies_v3 as common
import legacy_state as legacy
import store_git_sources as store_git

FORMAT = 12
NATIVE_VERSION = 7
LIMIT = common.LIMIT
TOP = common.TOP | {"originHash"}
RECORD_FIELDS = common.RECORD_FIELDS
REF_FIELDS = {"identity", "kind", "location", "name"}
PIN_FIELDS = {"name", "sourceLocation", "revision", "branch", "version", "hash"}
Reject = cache.Reject
require = cache.require
safe_name = common.safe_name
canonical = common.canonical
content_hash = common.content_hash
local_directory = common.local_directory
CHECKOUT_KINDS = ("remoteSourceControl", store_git.KIND)


def checkout_subpath(location):
    if isinstance(location, str) and location.startswith("/nix/store/"):
        return store_git.checkout_subpath(location)
    if isinstance(location, str) and location.startswith("file:"):
        return store_git.checkout_subpath(store_git.uri_location(location))
    return common.checkout_subpath(location)


def reference(value):
    require(
        isinstance(value, dict) and set(value) == REF_FIELDS,
        "v7 LOCATION reference fields",
    )
    require(
        safe_name(value["identity"]) and safe_name(value["name"]), "v7 identity/name"
    )
    require(value["kind"] in CHECKOUT_KINDS + ("fileSystem",), "v7 reference kind")
    location = value["location"]
    require(
        common.utf8_bound(location, 4096)
        and not any(ord(c) < 32 or ord(c) == 127 for c in location),
        "v7 reference location",
    )
    if value["kind"] == store_git.KIND:
        store_git.split_location(location)
    elif value["kind"] == "remoteSourceControl":
        common.checkout_subpath(location)
    return value


def checkout_state(value, *, selected=False):
    require(isinstance(value, dict), "v7 checkout state object")
    if selected:
        require(
            set(value) == {"revision", "branch", "version"},
            "portable12 canonical checkout fields",
        )
    else:
        require(
            set(value)
            in ({"revision"}, {"revision", "branch"}, {"revision", "version"}),
            "v7 native optional checkout fields",
        )
    require(
        isinstance(value["revision"], str)
        and legacy.REVISION.fullmatch(value["revision"]) is not None,
        "v7 checkout revision",
    )
    branch, version = value.get("branch"), value.get("version")
    require(
        branch is None
        or (
            common.utf8_bound(branch, 4096)
            and not any(ord(c) < 32 or ord(c) == 127 for c in branch)
        ),
        "v7 checkout branch",
    )
    require(version is None or legacy.version_valid(version), "v7 checkout version")
    require(branch is None or version is None, "v7 branch/version conflict")
    if not selected:
        require(
            all(item is not None for item in value.values()),
            "v7 native checkout must omit absent fields",
        )
    result = {key: item for key, item in value.items() if item is not None}
    return result


def checkout(record):
    require(
        isinstance(record, dict) and set(record) == RECORD_FIELDS, "v7 checkout fields"
    )
    ref = reference(record["packageRef"])
    require(
        ref["kind"] in CHECKOUT_KINDS and record["basedOn"] is None,
        "v7 checkout kind",
    )
    require(
        record["subpath"] == checkout_subpath(ref["location"]), "v7 checkout subpath"
    )
    state = record["state"]
    require(
        isinstance(state, dict)
        and set(state) == {"name", "checkoutState"}
        and state["name"] == "sourceControlCheckout",
        "v7 checkout discriminator",
    )
    checkout_state(state["checkoutState"])
    if ref["kind"] == store_git.KIND:
        require(
            store_git.REVISION.fullmatch(state["checkoutState"]["revision"])
            is not None,
            "store Git SHA1 checkout",
        )
    return record


def native_value(records):
    return {
        "object": {"dependencies": records, "artifacts": [], "prebuilts": []},
        "version": NATIVE_VERSION,
    }


def native_index(value, root=None, *, allow_edited=False, physical_locals=True):
    require(
        isinstance(value, dict)
        and set(value) == {"object", "version"}
        and type(value["version"]) is int
        and value["version"] == NATIVE_VERSION,
        "v7 native discriminator",
    )
    body = value["object"]
    require(
        isinstance(body, dict)
        and set(body) == {"dependencies", "artifacts", "prebuilts"}
        and body["artifacts"] == []
        and body["prebuilts"] == []
        and isinstance(body["dependencies"], list)
        and len(body["dependencies"]) <= LIMIT,
        "v7 native envelope",
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
            "v7 dependency fields",
        )
        ref = reference(record["packageRef"])
        identity, name, location = ref["identity"], ref["name"], ref["location"]
        require(
            identity not in result and name not in names and location not in locations,
            "v7 dependency alias",
        )
        if ref["kind"] == "fileSystem":
            require(
                record["basedOn"] is None
                and record["state"] == {"name": "fileSystem", "path": location}
                and record["subpath"] == identity,
                "v7 local location/state.path join",
            )
            _, physical = local_directory(root, location, physical=physical_locals)
            require(physical not in local_paths, "v7 local directory alias")
            local_paths.add(physical)
        else:
            if record["state"] == {"name": "edited", "path": None}:
                require(allow_edited, "v7 caller checkout must not be edited")
                based = checkout(record["basedOn"])
                require(
                    based["packageRef"] == ref and record["subpath"] == identity,
                    "v7 edited reference/identity subpath",
                )
            else:
                checkout(record)
            require(identity not in installed_paths, "v7 installed path alias")
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


def origin_hash(root):
    """SwiftPM's attested ordinary single-root/no-extra-input digest.

    swift-6.4.2-RELEASE Workspace+Dependencies.computeResolvedFileOriginHash
    concatenates selected root manifest text and explicit root dependency
    inputs. Both actual fixtures attest Package.swift SHA256. Versioned or
    additional-input contexts remain refused pending their native controls.
    """
    root = Path(root)
    require(root.is_absolute() and root == root.resolve(), "v7 canonical manifest root")
    cache.directory(root)
    require(
        not any(
            re.fullmatch(r"Package@swift-[0-9.]+\.swift", p.name)
            for p in root.iterdir()
        ),
        "v7 version-selected manifest context awaits native controls",
    )
    raw = cache.regular_bytes(root, "Package.swift", 2 * 1024 * 1024)
    try:
        raw.decode("utf-8")
    except UnicodeError as error:
        raise Reject("v7 manifest UTF-8") from error
    return hashlib.sha256(raw).hexdigest()


def attest_pins(value, root):
    pins_index(value)
    require(
        value["originHash"] == origin_hash(root), "v7 native originHash/manifest join"
    )
    return value


def selected_pin_matches(pin, record):
    ref = record["packageRef"]
    return (
        pin["identity"] == ref["identity"]
        and pin["kind"] == ref["kind"]
        and ref["kind"] in CHECKOUT_KINDS
        and pin["location"] == ref["location"]
        and checkout_state(pin["state"]) == record["state"]["checkoutState"]
    )


def pins_index(value):
    require(
        isinstance(value, dict)
        and set(value) == {"version", "pins", "originHash"}
        and type(value["version"]) is int
        and value["version"] == 3,
        "portable12 requires native pins3",
    )
    require(
        isinstance(value["originHash"], str)
        and re.fullmatch(r"[0-9a-f]{64}", value["originHash"]) is not None
        and isinstance(value["pins"], list)
        and len(value["pins"]) <= LIMIT,
        "v7 pins3 envelope/originHash",
    )
    result, identities = {}, set()
    for pin in value["pins"]:
        require(
            isinstance(pin, dict)
            and set(pin) == {"identity", "kind", "location", "state"}
            and safe_name(pin["identity"])
            and pin["identity"] not in identities
            and pin["kind"] in CHECKOUT_KINDS,
            "v7 pins3 identity/kind fields",
        )
        location = pin["location"]
        if pin["kind"] == store_git.KIND:
            store_git.split_location(location)
            require(
                isinstance(pin["state"], dict)
                and isinstance(pin["state"].get("revision"), str)
                and store_git.REVISION.fullmatch(pin["state"]["revision"]) is not None,
                "store Git SHA1 selected revision",
            )
        else:
            common.checkout_subpath(location)
        require(location not in result, "v7 pins3 source alias")
        checkout_state(pin["state"])
        identities.add(pin["identity"])
        result[location] = pin
    return result


def local_records(lock, root, *, physical=True):
    records = []
    for dependency in lock["localDependencies"]:
        require(
            isinstance(dependency, dict) and set(dependency) == RECORD_FIELDS,
            "portable12 local fields",
        )
        ref = reference(dependency["packageRef"])
        location = ref["location"]
        require(
            ref["kind"] == "fileSystem"
            and location.startswith("@root@/")
            and dependency["basedOn"] is None
            and dependency["state"] == {"name": "fileSystem", "path": location}
            and dependency["subpath"] == ref["identity"],
            "portable12 local template join",
        )
        relative = location[len("@root@/") :]
        require(
            relative and all(safe_name(part) for part in relative.split("/")),
            "portable12 local template",
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
        source = store_git.pin_source(pin)
        location = source.location if source is not None else pin["sourceLocation"]
        kind = store_git.KIND if source is not None else "remoteSourceControl"
        state = checkout_state(
            {key: pin[key] for key in ("revision", "branch", "version")}, selected=True
        )
        records.append(
            {
                "basedOn": None,
                "packageRef": {
                    "identity": identity,
                    "kind": kind,
                    "name": pin["name"],
                    "location": location,
                },
                "state": {"name": "sourceControlCheckout", "checkoutState": state},
                "subpath": checkout_subpath(location),
            }
        )
    records.extend(local_records(lock, root, physical=physical_locals))
    return native_value(records)


def manifest_hash(root, locals_):
    origin_hash(root)
    paths = [{"packageRef": {"path": row["packageRef"]["location"]}} for row in locals_]
    return common.manifest_hash(root, paths)


def validate_lock(lock, selected_pins, root, *, current=True):
    require(isinstance(lock, dict) and set(lock) == TOP, "portable12 root fields")
    require(
        type(lock["version"]) is int
        and lock["version"] == FORMAT
        and type(lock["workspaceStateVersion"]) is int
        and lock["workspaceStateVersion"] == NATIVE_VERSION,
        "portable12/native7 discriminator",
    )
    require(
        safe_name(lock["package"]) and safe_name(lock["rootDirectory"]),
        "portable12 package/root name",
    )
    require(lock["contentHash"] == content_hash(lock), "portable12 content hash")
    require(
        isinstance(lock["pins"], dict)
        and len(lock["pins"]) <= LIMIT
        and isinstance(lock["localDependencies"], list)
        and len(lock["localDependencies"]) <= LIMIT
        and lock["binaryTargets"] == [],
        "portable12 graph fields",
    )
    require(
        isinstance(lock["products"], list) and len(lock["products"]) <= LIMIT,
        "portable12 products",
    )
    names = set()
    for product in lock["products"]:
        require(isinstance(product, dict), "portable12 product object")
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
            "portable12 product schema",
        )
        names.add(product["name"])
    selected = pins_index(selected_pins)
    require(
        isinstance(lock["originHash"], str)
        and re.fullmatch(r"[0-9a-f]{64}", lock["originHash"]) is not None
        and lock["originHash"] == selected_pins["originHash"]
        and (not current or lock["originHash"] == origin_hash(root)),
        "portable12 selected originHash/root manifest join",
    )
    locations = set()
    for identity, pin in lock["pins"].items():
        require(
            safe_name(identity) and isinstance(pin, dict), "portable12 pin identity"
        )
        source = store_git.pin_source(pin)
        fields_valid = (
            set(pin) == PIN_FIELDS | store_git.PIN_FIELDS
            if source is not None
            else set(pin) in (PIN_FIELDS, PIN_FIELDS | {"url"})
        )
        require(
            fields_valid
            and safe_name(pin["name"])
            and isinstance(pin["hash"], str)
            and legacy.HASH.fullmatch(pin["hash"]) is not None,
            "portable12 pin fields/hash",
        )
        location = source.location if source is not None else pin["sourceLocation"]
        if source is not None:
            store_git.split_location(location)
        else:
            common.checkout_subpath(location)
        require(location not in locations, "portable12 source alias")
        locations.add(location)
        if "url" in pin:
            common.checkout_subpath(pin["url"])
            require(pin["url"] != location, "portable12 redundant mirror")
        state = {key: pin[key] for key in ("revision", "branch", "version")}
        checkout_state(state, selected=True)
        native = selected.get(location)
        require(
            native is not None
            and native["identity"] == identity
            and native["kind"]
            == (store_git.KIND if source is not None else "remoteSourceControl")
            and native["location"] == location
            and checkout_state(native["state"]) == checkout_state(state, selected=True),
            "portable12 selected pin join",
        )
    require(locations == set(selected), "portable12 selected pin closure")
    graph = checkout_records(lock, root, physical_locals=current)
    index = native_index(graph, root, physical_locals=current)
    require(
        lock["package"] not in {row["packageRef"]["name"] for row in index.values()},
        "portable12 root/dependency name alias",
    )
    require(
        isinstance(lock["manifestHash"], str)
        and re.fullmatch(r"[0-9a-f]{64}", lock["manifestHash"]) is not None
        and (
            not current
            or lock["manifestHash"] == manifest_hash(root, local_records(lock, root))
        ),
        "portable12 manifest currency",
    )
    return lock


def validate_raw(raw, selected_raw, root):
    require(
        isinstance(raw, bytes)
        and len(raw) <= 1024 * 1024
        and isinstance(selected_raw, bytes)
        and len(selected_raw) <= 1024 * 1024,
        "portable12 selected byte bounds/presence",
    )
    return validate_lock(cache.decode(raw), cache.decode(selected_raw), root)
