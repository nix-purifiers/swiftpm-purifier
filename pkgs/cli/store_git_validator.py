# SPDX-License-Identifier: MIT
"""Daemon-free physical store-Git validation inside an ordinary Nix builder."""

import argparse
import json
import os
from pathlib import Path
import sys

import bounded_native
import dependencies_v1_cache as cache
from pre_workspace import NativeSession
import store_git_sources as store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--declaration", type=Path, required=True)
    parser.add_argument("--git", required=True)
    parser.add_argument("--nix", required=True)
    args = parser.parse_args(argv)
    with args.declaration.open("rb") as stream:
        raw = stream.read(64 * 1024 + 1)
    cache.require(len(raw) <= 64 * 1024, "store Git validation declaration bound")
    declaration = cache.decode(raw)
    cache.require(
        isinstance(declaration, dict)
        and set(declaration)
        == {
            "version",
            "sourceKind",
            "storeSource",
            "revision",
            "branch",
            "packageVersion",
        }
        and type(declaration["version"]) is int
        and declaration["version"] == 1
        and declaration["sourceKind"] == store.KIND,
        "closed store Git validation declaration",
    )
    source = store.Source.decode(declaration["storeSource"])
    session = NativeSession(Path.cwd(), dict(os.environ))
    try:
        store.inspect(
            source,
            declaration["revision"],
            declaration["branch"],
            declaration["packageVersion"],
            session,
            args.git,
            args.nix,
            label="store-source",
        )
        session.receipt.update(complete=True, physicalNarVerified=True)
        print(
            json.dumps(
                {
                    "version": 1,
                    "sourceKind": store.KIND,
                    "storeSource": source.encode(),
                    "revision": declaration["revision"],
                    "physicalNarVerified": True,
                },
                sort_keys=True,
            )
        )
        return 0
    except BaseException as error:
        session.receipt["aborted"] = type(error).__name__
        raise
    finally:
        session.save()


if __name__ == "__main__":
    with bounded_native.default_sigterm():
        try:
            sys.exit(main())
        except (ValueError, OSError) as error:
            print(
                "swiftpm-purifier: store Git validation failed: " + str(error),
                file=sys.stderr,
            )
            sys.exit(1)
