# SPDX-License-Identifier: MIT
"""Verify real Git/source NARs; no synthesized repository or native SDK."""

import argparse
import base64
import json
import os
from pathlib import Path
import shutil

import bounded_native


def command(argv):
    result = bounded_native.run(argv, timeout=120, output_limit=8 * 1024 * 1024)
    if result.returncode:
        raise ValueError(
            "real Git/source verification command failed: "
            + result.stderr.decode(errors="replace")
        )
    return result.stdout.decode().strip()


def hex_hash(value):
    if not isinstance(value, str) or not value.startswith("sha256-"):
        raise ValueError("SHA-256 SRI required")
    digest = base64.b64decode(value[7:], validate=True)
    if len(digest) != 32 or "sha256-" + base64.b64encode(digest).decode() != value:
        raise ValueError("canonical SHA-256 SRI required")
    return digest.hex()


def verify(source, output, revision, source_hash, metadata_hash, origin, git, nix_hash):
    source, output = Path(source), Path(output)
    if (
        source.is_symlink()
        or not source.is_dir()
        or not (source / ".git").is_dir()
        or (source / ".git").is_symlink()
    ):
        raise ValueError("a real immutable Git checkout is required")
    if command([nix_hash, "--type", "sha256", str(source)]) != hex_hash(metadata_hash):
        raise ValueError("actual real Git NAR differs from its lock")
    prefix = [git, "-c", "safe.directory=" + str(source), "-C", str(source)]
    if command([*prefix, "rev-parse", "--verify", "HEAD^{commit}"]) != revision:
        raise ValueError("actual Git HEAD differs from the captured revision")
    command([*prefix, "fsck", "--full"])
    metadata = Path(command([*prefix, "rev-parse", "--absolute-git-dir"])).resolve()
    if not metadata.is_relative_to(source.resolve()) or any(
        path.is_symlink() for path in metadata.rglob("*")
    ):
        raise ValueError("real Git metadata is not confined to its fetched checkout")
    common = Path(command([*prefix, "rev-parse", "--git-common-dir"]))
    if not common.is_absolute():
        common = source / common
    if not common.resolve().is_relative_to(source.resolve()):
        raise ValueError("real Git common metadata points outside its fetched checkout")
    if (
        Path(command([*prefix, "rev-parse", "--show-toplevel"])).resolve()
        != source.resolve()
    ):
        raise ValueError("real Git worktree points outside its fetched checkout")
    plain = Path(os.environ["TMPDIR"]) / "plain-source"
    shutil.copytree(source, plain, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    if command([nix_hash, "--type", "sha256", str(plain)]) != hex_hash(source_hash):
        raise ValueError(
            "real Git checkout does not reproduce the locked plain source NAR"
        )
    shutil.copytree(source, output, symlinks=True)
    for directory, names, files in os.walk(output):
        Path(directory).chmod(Path(directory).stat().st_mode | 0o700)
        for name in names + files:
            path = Path(directory) / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | 0o200)
    # Restore only the native observed origin, with actual Git configuration.
    command([git, "-C", str(output), "config", "remote.origin.url", origin])
    if (
        command([git, "-C", str(output), "config", "--get", "remote.origin.url"])
        != origin
    ):
        raise ValueError("actual Git origin was not restored")
    receipt = {
        "revision": revision,
        "sourceHash": source_hash,
        "gitHash": metadata_hash,
        "origin": origin,
        "metadataSource": str(source),
    }
    (output / ".git" / "swiftpm-purifier-verified.json").write_text(
        json.dumps(receipt, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in (
        "source",
        "output",
        "revision",
        "source-hash",
        "metadata-hash",
        "origin",
        "git",
        "nix-hash",
    ):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    verify(
        args.source,
        args.output,
        args.revision,
        args.source_hash,
        args.metadata_hash,
        args.origin,
        args.git,
        args.nix_hash,
    )
