# SPDX-License-Identifier: MIT
"""Literal transaction identities; each codec keeps its own record domain.

The PATH registry remains closed to its original two identities. This registry
selects shared transaction mechanics without expanding either PATH codec.
"""

from dataclasses import dataclass

from dependencies_v1_cache import Reject
from native_path_protocols import DEPENDENCIES_V3, WORKSPACE_V4_PATH


@dataclass(frozen=True)
class NativeLocationProtocol:
    portable_format: int
    native_version: int
    state_basename: str
    object_fields: frozenset[str]
    name: str
    ownership_marker: str


WORKSPACE_V5_LOCATION = NativeLocationProtocol(
    11,
    5,
    "workspace-state.json",
    frozenset({"dependencies", "artifacts"}),
    "workspace-state-v5-location-pins1",
    ".swiftpm-purifier-workspace-v5-location-owned.json",
)

WORKSPACE_V7_PINS3 = NativeLocationProtocol(
    12,
    7,
    "workspace-state.json",
    frozenset({"dependencies", "artifacts", "prebuilts"}),
    "workspace-state-v7-location-pins3",
    ".swiftpm-purifier-workspace-v7-pins3-owned.json",
)


WORKSPACE_V7_PINS2 = NativeLocationProtocol(
    13,
    7,
    "workspace-state.json",
    frozenset({"dependencies", "artifacts", "prebuilts"}),
    "workspace-state-v7-location-pins2",
    ".swiftpm-purifier-workspace-v7-pins2-owned.json",
)


def checked(protocol):
    if (
        protocol is DEPENDENCIES_V3
        or protocol is WORKSPACE_V4_PATH
        or protocol is WORKSPACE_V5_LOCATION
        or protocol is WORKSPACE_V7_PINS3
        or protocol is WORKSPACE_V7_PINS2
    ):
        return protocol
    raise Reject("unknown native transaction protocol descriptor")


def installation_name(protocol, identity, pin):
    protocol = checked(protocol)
    return identity if is_location(protocol) else pin["name"]


def is_location(protocol):
    protocol = checked(protocol)
    return protocol in (WORKSPACE_V5_LOCATION, WORKSPACE_V7_PINS3, WORKSPACE_V7_PINS2)
