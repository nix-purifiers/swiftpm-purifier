# SPDX-License-Identifier: MIT
"""Closed, immutable identities for the two native path-reference protocols."""

from dataclasses import dataclass

from dependencies_v1_cache import Reject


@dataclass(frozen=True)
class NativePathProtocol:
    portable_format: int
    native_version: int
    state_basename: str
    object_fields: frozenset[str]
    name: str
    ownership_marker: str


DEPENDENCIES_V3 = NativePathProtocol(
    9,
    3,
    "dependencies-state.json",
    frozenset({"dependencies"}),
    "dependencies-state-v3",
    ".swiftpm-purifier-dependencies-v3-owned.json",
)
WORKSPACE_V4_PATH = NativePathProtocol(
    10,
    4,
    "workspace-state.json",
    frozenset({"dependencies", "artifacts"}),
    "workspace-state-v4-path",
    ".swiftpm-purifier-workspace-v4-path-owned.json",
)


def checked(protocol):
    """Only literal allocated identities can select paths or authority domains."""
    if protocol is DEPENDENCIES_V3 or protocol is WORKSPACE_V4_PATH:
        return protocol
    raise Reject("unknown native path protocol descriptor")


def native_value(records, protocol):
    protocol = checked(protocol)
    body = {"dependencies": records}
    if protocol is WORKSPACE_V4_PATH:
        body["artifacts"] = []
    return {"object": body, "version": protocol.native_version}
