#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Preserve CI diagnostics, including negative filenames and hidden native state.

The outer artifact uses two ordinary filenames. Tar members retain the exact
fixture names and symlink/FIFO metadata without following or opening them.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import tarfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
root = args.root.resolve(strict=True)
names = [
    "dependencies-v1-runtime-build.json",
    "dependencies-v1-sdk-metadata",
    "dependencies-v1-prefetch-prefix",
    "dependencies-v1-cli-prefix",
    "dependencies-v1-codec",
    "dependencies-v1-layout",
    "dependencies-v1-readers.json",
    "dependencies-v1-native",
]
included = [name for name in names if os.path.lexists(root / name)]
archive = root / "dependencies-v1-evidence.tar.gz"
with tarfile.open(archive, "w:gz", dereference=False) as output:
    for name in included:
        output.add(root / name, arcname=name)
digest = hashlib.sha256()
with archive.open("rb") as stream:
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
manifest = {
    "version": 1,
    "archive": archive.name,
    "size": archive.stat().st_size,
    "sha256": digest.hexdigest(),
    "included": included,
    "scope": "Diagnostic retention only; does not change native outcomes or assertions.",
}
(root / "dependencies-v1-evidence-manifest.json").write_text(
    json.dumps(manifest, indent=2) + "\n"
)
print(json.dumps(manifest))
