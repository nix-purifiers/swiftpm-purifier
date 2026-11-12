# SPDX-License-Identifier: MIT
"""Verify a native build's selected SDK using its declared closure graph."""

import argparse
import json
from pathlib import Path

import native_sdk
import swiftpm_purifier


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--closure-paths", type=Path, required=True)
    parser.add_argument("--nix", required=True)
    args = parser.parse_args()
    expected = native_sdk.validate_identity(json.loads(args.expected.read_text()))
    descriptor = native_sdk.load_descriptor(args.descriptor)
    if descriptor != expected["descriptor"]:
        raise native_sdk.SDKError(
            "selected SDK derivations/environment differ from the lock"
        )
    native_sdk.check_build_bindings(descriptor)
    paths = args.closure_paths.read_text().splitlines()
    actual = native_sdk.measure(
        descriptor,
        args.nix,
        swiftpm_purifier.native_options,
        verify_active=False,
        closure_paths=paths,
    )
    if actual != expected:
        raise native_sdk.SDKError(
            "actual native compiler, SwiftPM, runtime closure or capabilities differ from the lock"
        )


if __name__ == "__main__":
    main()
