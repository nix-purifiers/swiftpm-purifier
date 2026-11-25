# SPDX-License-Identifier: MIT
"""Strict selection of the existing registry-capable profile, not a codec.

Registry-free graphs still belong to native-pair discovery. A successful
classification grants no ownership or publication authority: the established
capture path must resolve the real graph and verify every derived record.
"""

import re

import dependencies_v1_cache as cache
import legacy_state as legacy
import workspace_v7_pins3 as git_codec

require = cache.require


def candidate(value, selected_pins, *, root):
    # Older concrete native versions and the already-retained location
    # envelope keep their prior routing before this new classification.
    if (
        isinstance(value, dict)
        and type(value.get("version")) is int
        and value["version"] != 7
    ):
        return False
    body = value.get("object") if isinstance(value, dict) else None
    if (
        isinstance(value, dict)
        and type(value.get("version")) is int
        and value["version"] == 7
        and set(value) == {"version", "object"}
        and isinstance(body, dict)
        and set(body) == {"dependencies", "artifacts"}
    ):
        return False
    records = body.get("dependencies") if isinstance(body, dict) else None
    if isinstance(records, list):
        require(len(records) <= git_codec.LIMIT, "legacy registry native bound")
    if not isinstance(records, list) or not any(
        isinstance(row, dict)
        and isinstance(row.get("packageRef"), dict)
        and row["packageRef"].get("kind") == "registry"
        for row in records
    ):
        return False
    require(
        set(value) == {"version", "object"}
        and type(value["version"]) is int
        and value["version"] == 7
        and set(body) == {"dependencies", "artifacts", "prebuilts"}
        and body["artifacts"] == []
        and body["prebuilts"] == [],
        "legacy registry complete native7 envelope",
    )
    require(
        isinstance(selected_pins, dict)
        and type(selected_pins.get("version")) is int
        and selected_pins["version"] in (2, 3),
        "legacy registry selected pin version",
    )
    fields = {"version", "pins"}
    if selected_pins["version"] == 3:
        fields.add("originHash")
        require(
            isinstance(selected_pins.get("originHash"), str)
            and re.fullmatch("[0-9a-f]{64}", selected_pins["originHash"]) is not None,
            "legacy registry native origin syntax",
        )
    # This is the retained profile's native field, not format-12 attestation.
    # Preserve genuine pins; never compute or overwrite their originHash.
    require(set(selected_pins) == fields, "legacy registry selected pin fields")
    require(isinstance(selected_pins["pins"], list), "legacy registry pin array")
    require(len(selected_pins["pins"]) <= git_codec.LIMIT, "legacy registry pin bound")
    pins = {}
    for pin in selected_pins["pins"]:
        require(
            isinstance(pin, dict)
            and set(pin) == {"identity", "kind", "location", "state"}
            and git_codec.safe_name(pin["identity"])
            and pin["identity"] not in pins,
            "legacy registry unique closed pin",
        )
        require(
            pin["kind"] in ("registry", "remoteSourceControl"),
            "legacy registry unsupported selected kind",
        )
        pins[pin["identity"]] = pin
    identities, names, locations, local_paths, pinned = (
        set(),
        set(),
        set(),
        set(),
        set(),
    )
    for row in records:
        require(
            isinstance(row, dict)
            and set(row) == git_codec.RECORD_FIELDS
            and row["basedOn"] is None,
            "legacy registry unedited closed record",
        )
        ref = row["packageRef"]
        require(
            isinstance(ref, dict)
            and set(ref) == git_codec.REF_FIELDS
            and git_codec.safe_name(ref["identity"])
            and git_codec.safe_name(ref["name"])
            and isinstance(ref["location"], str),
            "legacy registry closed reference",
        )
        identity, name, location = ref["identity"], ref["name"], ref["location"]
        require(
            identity not in identities
            and name not in names
            and location not in locations,
            "legacy registry native alias",
        )
        identities.add(identity)
        names.add(name)
        locations.add(location)
        if ref["kind"] == "fileSystem":
            # Reuse the independently observed native local grammar; it grants
            # no source-copy/deletion authority here.
            git_codec.native_index(git_codec.native_value([row]), root)
            _, physical = git_codec.local_directory(root, location)
            require(physical not in local_paths, "legacy registry local alias")
            local_paths.add(physical)
            continue
        require(identity in pins, "legacy registry missing selected identity")
        pin = pins[identity]
        pinned.add(identity)
        if ref["kind"] == "registry":
            require(
                re.fullmatch(r"[a-z0-9][a-z0-9_-]*[.][a-z0-9][a-z0-9_-]*", identity)
                is not None
                and location == identity
                and name == identity,
                "legacy registry canonical identity/location/name",
            )
            state = row["state"]
            require(
                isinstance(state, dict)
                and set(state) == {"name", "version"}
                and state["name"] == "registryDownload"
                and legacy.version_valid(state["version"]),
                "legacy registry download state",
            )
            version = state["version"]
            scope, package = identity.split(".")
            require(
                row["subpath"] == f"{scope}/{package}/{version}"
                and pin["kind"] == "registry"
                and pin["location"] == ""
                and isinstance(pin["state"], dict)
                and set(pin["state"]) == {"version"}
                and pin["state"]["version"] == version,
                "legacy registry pin/download/subpath join",
            )
        else:
            require(
                ref["kind"] == "remoteSourceControl",
                "legacy registry unsupported native kind",
            )
            git_codec.checkout(row)
            require(
                pin["kind"] == ref["kind"]
                and pin["location"] == location
                and git_codec.checkout_state(pin["state"])
                == row["state"]["checkoutState"],
                "legacy registry remote pin/source/state join",
            )
    require(pinned == set(pins), "legacy registry complete selected closure")
    return True
