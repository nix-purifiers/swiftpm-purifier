# SPDX-License-Identifier: MIT
"""Real filesystem tests of the actual install loops; no SDK or module ABI claim."""

import argparse, json, os, subprocess, tempfile
from pathlib import Path


def loop(path):
    lines = path.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if "for module in " in line)
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "done")
    value = "\n".join(lines[start : end + 1]).replace(
        "${stdenv.hostPlatform.parsed.kernel.name}", "linux"
    )
    assert "${stdenv." not in value
    return value


def check(source, bash):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        products = root / "products"
        out = root / "output"
        products.mkdir()
        # Synthetic copy-I/O bytes, never represented as compiler output.
        (products / "Flat.swiftmodule").write_bytes(
            b"legacy flat module copy fixture\n"
        )
        bundled = products / "Modules/Bundle.swiftmodule/Project"
        bundled.mkdir(parents=True)
        (bundled.parent / "triple.swiftmodule").write_bytes(
            b"directory module copy fixture\n"
        )
        (bundled / "triple.swiftsourceinfo").write_bytes(
            b"nested source-info copy fixture\n"
        )
        linked = root / "link-target"
        linked.mkdir()
        (linked / "triple.swiftmodule").write_bytes(b"symlink-directory copy fixture\n")
        (products / "Linked.swiftmodule").symlink_to(linked, target_is_directory=True)
        member = root / "member-target"
        member.write_bytes(b"member symlink copy fixture\n")
        (bundled.parent / "triple.swiftdoc").symlink_to(member)
        result = subprocess.run(
            [bash, "-e", "-c", loop(source)],
            env=dict(os.environ, products=str(products), out=str(out)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        assert result.returncode == 0, (source, result.stderr.decode())
        expected = {
            "Flat.swiftmodule": b"legacy flat module copy fixture\n",
            "Bundle.swiftmodule/triple.swiftmodule": b"directory module copy fixture\n",
            "Bundle.swiftmodule/Project/triple.swiftsourceinfo": b"nested source-info copy fixture\n",
            "Linked.swiftmodule/triple.swiftmodule": b"symlink-directory copy fixture\n",
            "Bundle.swiftmodule/triple.swiftdoc": b"member symlink copy fixture\n",
        }
        destination = out / "lib/swift/linux"
        import shutil

        shutil.rmtree(products)
        shutil.rmtree(linked)
        member.unlink()
        assert {
            str(p.relative_to(destination))
            for p in destination.rglob("*")
            if p.is_file()
        } == set(expected)
        assert not any(p.is_symlink() for p in destination.rglob("*"))
        for name, raw in expected.items():
            assert (destination / name).read_bytes() == raw
        return {
            "source": str(source),
            "files": len(expected),
            "regularDirectoryAndSymlinkDirectory": True,
            "scope": "copy I/O only/no SDK",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--builder", type=Path, required=True)
    parser.add_argument("--hook", type=Path, required=True)
    parser.add_argument("--bash", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            [check(path, args.bash) for path in [args.builder, args.hook]], indent=2
        )
    )
