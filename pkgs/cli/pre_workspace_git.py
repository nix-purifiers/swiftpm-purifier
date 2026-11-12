# SPDX-License-Identifier: MIT
"""Verify real Git/source NARs; no synthesized repository or native SDK."""

import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import re
import tempfile

import bounded_native


OBJECT_ENV = (
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_DIR",
    "GIT_COMMON_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
)


def reject_git_environment(environment):
    if any(name in environment for name in OBJECT_ENV):
        raise ValueError(
            "pre-workspace Git object/worktree environment overrides are unsupported"
        )


def confine_git(source, environment=None):
    """Check physical metadata pointers before invoking any Git operation."""
    reject_git_environment(os.environ if environment is None else environment)
    source = Path(source)
    if (
        source.is_symlink()
        or not source.is_dir()
        or (source / ".git").is_symlink()
        or not (source / ".git").is_dir()
    ):
        raise ValueError("self-contained real Git metadata directory required")
    root = source.resolve()
    repositories = []
    for pointer in [
        source / ".git",
        *[path for path in source.rglob(".git") if path != source / ".git"],
    ]:
        if pointer.is_symlink():
            raise ValueError("Git metadata pointer must not be a symlink")
        if pointer.is_file():
            if pointer.stat().st_size > 8192:
                raise ValueError("Git metadata pointer byte bound")
            match = re.fullmatch(r"gitdir: ([^\r\n\x00]+)\n?", pointer.read_text())
            if match is None:
                raise ValueError("unsupported Git metadata pointer")
            metadata = Path(match[1])
            if not metadata.is_absolute():
                metadata = pointer.parent / metadata
        else:
            metadata = pointer
        try:
            metadata = metadata.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ValueError("missing or invalid Git metadata pointer") from error
        if not metadata.is_relative_to(root) or not metadata.is_dir():
            raise ValueError("Git metadata pointer escapes its owned checkout")
        if os.path.lexists(metadata / "commondir"):
            raise ValueError("shared Git common metadata is unsupported")
        if os.path.lexists(metadata / "index.lock") or any(
            path.name.startswith("sharedindex.") for path in metadata.iterdir()
        ):
            raise ValueError("locked or split Git indexes are unsupported")
        count = 0
        for item in metadata.rglob("*"):
            count += 1
            if count > 100000:
                raise ValueError("Git metadata entry bound")
            if item.is_symlink():
                raise ValueError("Git metadata contains symlinks")
            if (
                item.name in ("alternates", "http-alternates")
                and item.parent.name == "info"
                and item.parent.parent.name == "objects"
            ):
                raise ValueError("Git alternate object pointers are unsupported")
        repositories.append((pointer.parent, metadata))
    return repositories


def command_raw(argv):
    result = bounded_native.run(argv, timeout=120, output_limit=8 * 1024 * 1024)
    if result.returncode:
        raise ValueError(
            "real Git/source verification command failed: "
            + result.stderr.decode(errors="replace")
        )
    return result.stdout


def command(argv):
    return command_raw(argv).decode().strip()


def validate_indices(source, git):
    """Read actual index entries and flags without refreshing caller metadata."""
    repositories = confine_git(source)
    for repository, metadata in repositories:
        index = metadata / "index"
        if index.is_symlink() or not index.is_file():
            raise ValueError("a regular complete Git index is required")
        prefix = [
            git,
            "--no-optional-locks",
            "-c",
            "safe.directory=" + str(repository),
            "-C",
            str(repository),
        ]
        expected = {}
        for record in command_raw(
            [*prefix, "ls-tree", "-rz", "--full-tree", "HEAD"]
        ).split(b"\0"):
            if not record:
                continue
            header, path = record.split(b"\t", 1)
            mode, kind, oid = header.split()
            if (
                mode not in (b"100644", b"100755", b"120000", b"160000")
                or kind != (b"commit" if mode == b"160000" else b"blob")
                or path in expected
            ):
                raise ValueError("unsupported Git tree entry")
            expected[path] = (mode, oid)
        actual = {}
        for record in command_raw([*prefix, "ls-files", "--stage", "-z"]).split(b"\0"):
            if not record:
                continue
            header, path = record.split(b"\t", 1)
            mode, oid, stage = header.split()
            if stage != b"0" or path in actual:
                raise ValueError("unmerged or duplicate Git index entry")
            actual[path] = (mode, oid)
        if actual != expected:
            raise ValueError("Git index entries differ from the verified HEAD tree")
        raw = command_raw([*prefix, "ls-files", "--debug", "-z"])
        stat_record = re.compile(
            rb"  ctime: [0-9]+:[0-9]+\n  mtime: [0-9]+:[0-9]+\n  dev: [0-9]+\tino: [0-9]+\n  uid: [0-9]+\tgid: [0-9]+\n  size: [0-9]+\tflags: ([0-9a-fA-F]+)\n"
        )
        offset = 0
        paths = []
        while offset < len(raw):
            end = raw.find(b"\0", offset)
            if end < 0:
                raise ValueError("unsupported Git index debug output")
            paths.append(raw[offset:end])
            match = stat_record.match(raw, end + 1)
            if match is None or int(match[1], 16) != 0:
                raise ValueError("unsupported Git index flags or metadata")
            offset = match.end()
        if paths != list(actual):
            raise ValueError("Git index flag records disagree with index entries")
    return repositories


def initialize_indices(source, git):
    """Construct real indexes from verified objects only in owned staging."""
    for repository, metadata in confine_git(source):
        command([git, "-C", str(repository), "read-tree", "HEAD"])
    validate_indices(source, git)
    for repository, metadata in confine_git(source):
        if command_raw(
            [git, "--no-optional-locks", "-C", str(repository), "status", "--porcelain"]
        ):
            raise ValueError("materialized Git checkout is not clean")


def normalized_hash(source, nix_hash, git):
    """Hash all bytes except exact validated metadata/index stat-cache files."""
    source = Path(source)
    indices = {
        metadata / "index" for repository, metadata in validate_indices(source, git)
    }
    with tempfile.TemporaryDirectory(prefix="preworkspace-owned-hash-") as temporary:
        plain = Path(temporary) / "checkout"
        shutil.copytree(
            source,
            plain,
            symlinks=True,
            ignore=lambda directory, names: [
                name for name in names if Path(directory) / name in indices
            ],
        )
        return command([nix_hash, "--type", "sha256", str(plain)])


def hex_hash(value):
    if not isinstance(value, str) or not value.startswith("sha256-"):
        raise ValueError("SHA-256 SRI required")
    digest = base64.b64decode(value[7:], validate=True)
    if len(digest) != 32 or "sha256-" + base64.b64encode(digest).decode() != value:
        raise ValueError("canonical SHA-256 SRI required")
    return digest.hex()


def verify(source, output, revision, source_hash, metadata_hash, origin, git, nix_hash):
    source, output = Path(source), Path(output)
    confine_git(source)
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
    initialize_indices(output, git)
    materialized_plain = Path(os.environ["TMPDIR"]) / "materialized-plain"
    shutil.copytree(
        output, materialized_plain, symlinks=True, ignore=shutil.ignore_patterns(".git")
    )
    if command([nix_hash, "--type", "sha256", str(materialized_plain)]) != hex_hash(
        source_hash
    ):
        raise ValueError("Git index construction changed the locked source NAR")
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
