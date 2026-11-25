# SPDX-License-Identifier: MIT
"""Observe an admitted native7/pins pair, without granting write authority.

This private selector is deliberately separate from public CLI dispatch. It
never chooses by compiler version and does not convert old portable formats.
"""

import dependencies_v1_cache as cache
import native_path_family as family
import native_protocols as protocols


def candidate(value, selected_pins, *, root):
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value["version"] != 7
    ):
        return None
    body = value.get("object")
    cache.require(
        isinstance(body, dict) and isinstance(body.get("dependencies"), list),
        "workspace7 candidate requires a complete native dependency list",
    )
    # Preserve the prior profile's envelope; this observer adds no authority
    # for a native7 body without the separately observed prebuilt surface.
    if set(value) == {"version", "object"} and set(body) == {
        "dependencies",
        "artifacts",
    }:
        return None
    if not body["dependencies"]:
        return None
    # The retained native633-v1 corpus belongs to the existing format4
    # profile. Observing it must not claim a new format or reject that path.
    if (
        isinstance(selected_pins, dict)
        and type(selected_pins.get("version")) is int
        and selected_pins["version"] == 1
        and "object" in selected_pins
    ):
        return None
    cache.require(
        isinstance(selected_pins, dict)
        and type(selected_pins.get("version")) is int
        and selected_pins["version"] in (2, 3),
        "workspace7 selected pin format is not admitted",
    )
    descriptor = (
        protocols.WORKSPACE_V7_PINS3
        if selected_pins["version"] == 3
        else protocols.WORKSPACE_V7_PINS2
    )
    codec = family.codec(descriptor)
    records = codec.native_index(value, root)
    pins = codec.pins_index(selected_pins)
    if descriptor is protocols.WORKSPACE_V7_PINS3:
        codec.attest_pins(selected_pins, root)
    remotes = {
        row["packageRef"]["location"]: row
        for row in records.values()
        if row["packageRef"]["kind"] in codec.CHECKOUT_KINDS
    }
    cache.require(set(remotes) == set(pins), "workspace7 complete source/pin closure")
    cache.require(
        all(
            codec.selected_pin_matches(pins[source], row)
            for source, row in remotes.items()
        ),
        "workspace7 selected identity/source/state join",
    )
    return descriptor
