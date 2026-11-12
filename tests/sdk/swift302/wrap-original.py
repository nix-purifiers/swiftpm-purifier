"""Package original ELF tools with ordinary loader/C environment wrappers.

ELF interpreter/runtime-path packaging may relocate loader tables. Allocated
payloads must match except .interp, .dynstr, and verified .dynamic metadata.
Only compiler header-search flags isolate the explicit test SDK from host
headers. No SDK APIs, versions, or feature answers are edited.
Each native tool retains argv[0], including swiftc and autolink dispatch.
"""

import hashlib
import json
import os
from pathlib import Path
import shlex
import struct
import subprocess


def elf_facts(path):
    with path.open("rb") as stream:
        header = stream.read(64)
        if header[:6] != b"\x7fELF\x02\x01":
            raise ValueError("unsupported original ELF class/endian")
        program_offset = struct.unpack_from("<Q", header, 32)[0]
        program_width, program_count = struct.unpack_from("<HH", header, 54)
        section_offset = struct.unpack_from("<Q", header, 40)[0]
        width, count, names_index = struct.unpack_from("<HHH", header, 58)
        if width < 64 or count > 8192 or names_index >= count or program_count > 1024:
            raise ValueError("ELF table bound")
        stream.seek(program_offset)
        programs = stream.read(program_width * program_count)
        interpreter = any(
            struct.unpack_from("<I", programs, program_width * i)[0] == 3
            for i in range(program_count)
        )
        stream.seek(section_offset)
        table = stream.read(width * count)
        sections = [
            struct.unpack_from("<IIQQQQIIQQ", table, width * i) for i in range(count)
        ]
        names = sections[names_index]
        if names[5] > 1024 * 1024:
            raise ValueError("ELF name table bound")
        stream.seek(names[4])
        names = stream.read(names[5])
        result = {}
        addresses = {}
        dynamic = []
        strings = b""
        for section in sections:
            name = names[section[0] :].split(b"\0", 1)[0].decode("ascii")
            addresses[name] = section[3]
            if name == ".dynstr":
                if section[5] > 16 * 1024 * 1024:
                    raise ValueError("dynamic strings bound")
                stream.seek(section[4])
                strings = stream.read(section[5])
            if name == ".dynamic":
                if section[5] > 1024 * 1024 or section[5] % 16:
                    raise ValueError("dynamic table bound")
                stream.seek(section[4])
                raw_dynamic = stream.read(section[5])
                dynamic = [
                    struct.unpack_from("<qQ", raw_dynamic, i)
                    for i in range(0, len(raw_dynamic), 16)
                ]
            if not section[2] & 2 or name == ".interp":
                continue
            sha = hashlib.sha256()
            if section[1] != 8:
                stream.seek(section[4])
                remaining = section[5]
                while remaining:
                    data = stream.read(min(1024 * 1024, remaining))
                    if not data:
                        raise ValueError("truncated ELF section")
                    sha.update(data)
                    remaining -= len(data)
            result[name] = {
                "type": section[1],
                "flags": section[2],
                "size": section[5],
                "sha256": None if section[1] == 8 else sha.hexdigest(),
            }
        paths = []
        for tag, value in dynamic:
            if tag in (15, 29):
                if value >= len(strings) or b"\0" not in strings[value:]:
                    raise ValueError("invalid runtime path string")
                paths.append([tag, strings[value:].split(b"\0", 1)[0].decode("utf-8")])
        return (
            interpreter,
            result,
            {"addresses": addresses, "dynamic": dynamic, "runtimePaths": paths},
        )


root = Path(os.environ["out"])
binary = root / "bin"
runtime = ":".join(
    [str(root / p) for p in ["lib/swift/linux", "lib/swift/host", "lib"]]
    + [os.environ["sdkRuntimeLibraries"]]
)
paths = str(binary) + ":" + os.environ["sdkToolPath"]
headers = os.environ["sdkCHeaders"]
clang_arguments = json.loads(os.environ["sdkClangArguments"])
facts = []
runtime_facts = []
# Runtime libraries must resolve their own transitive dependencies without an
# ambient LD_LIBRARY_PATH. Only ordinary loader strings/tables may change.
for library in sorted(root.rglob("*.so*")):
    if library.is_symlink() or not library.is_file():
        continue
    with library.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            continue
    original_sha = hashlib.sha256(library.read_bytes()).hexdigest()
    _, before, old = elf_facts(library)
    subprocess.run(
        [
            os.environ["sdkPatchelf"],
            "--no-sort",
            "--force-rpath",
            "--add-rpath",
            runtime,
            str(library),
        ],
        check=True,
    )
    _, after, new = elf_facts(library)
    changed = {
        name
        for name in before.keys() | after.keys()
        if before.get(name) != after.get(name)
    }
    if changed - {".dynamic", ".dynstr"}:
        raise ValueError("runtime-library code/payload changed: " + str(library))
    pointer_sections = {4: ".hash", 5: ".dynstr", 6: ".dynsym", 0x6FFFFEF5: ".gnu.hash"}
    ignored = {0, 10, 15, 29} | pointer_sections.keys()
    if [item for item in old["dynamic"] if item[0] not in ignored] != [
        item for item in new["dynamic"] if item[0] not in ignored
    ]:
        raise ValueError(
            "unexpected runtime-library dynamic metadata change: " + str(library)
        )
    for metadata, sections in [(old, before), (new, after)]:
        for tag, value in metadata["dynamic"]:
            if tag in pointer_sections and value != metadata["addresses"].get(
                pointer_sections[tag]
            ):
                raise ValueError("invalid loader table relocation: " + str(tag))
            if tag == 10 and value != sections[".dynstr"]["size"]:
                raise ValueError("invalid DT_STRSZ")
    if len(old["runtimePaths"]) > 1:
        raise ValueError("ambiguous original runtime paths")
    original_path = old["runtimePaths"][0][1] if old["runtimePaths"] else ""
    expected = original_path + (":" if original_path else "") + runtime
    if new["runtimePaths"] != [[15, expected]]:
        raise ValueError("unexpected runtime-library RPATH")
    runtime_facts.append(
        {
            "path": str(library.relative_to(root)),
            "originalSha256": original_sha,
            "packagedSha256": hashlib.sha256(library.read_bytes()).hexdigest(),
            "originalAllocatedSections": before,
            "packagedAllocatedSections": after,
            "originalLoaderMetadata": old,
            "packagedLoaderMetadata": new,
        }
    )
for tool in sorted(binary.iterdir()):
    if tool.is_symlink() or not tool.is_file():
        continue
    with tool.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            continue
    sha = hashlib.sha256(tool.read_bytes()).hexdigest()
    interpreter, original_sections, original_metadata = elf_facts(tool)
    if interpreter:
        subprocess.run(
            [
                os.environ["sdkPatchelf"],
                "--no-sort",
                "--set-interpreter",
                os.environ["sdkInterpreter"],
                str(tool),
            ],
            check=True,
        )
    _, packaged_sections, packaged_metadata = elf_facts(tool)
    changed = {
        name
        for name in original_sections.keys() | packaged_sections.keys()
        if original_sections.get(name) != packaged_sections.get(name)
    }
    if changed - {".dynamic"}:
        raise ValueError(
            "original allocated ELF payload changed: " + tool.name + " " + repr(changed)
        )
    relocations = []
    if ".dynamic" in changed:
        before = original_metadata["dynamic"]
        after = packaged_metadata["dynamic"]
        if len(before) != len(after):
            raise ValueError("dynamic table size changed")
        for index, (old, new) in enumerate(zip(before, after)):
            if old == new:
                continue
            if not (
                old[0] == new[0] == 6
                and old[1] == original_metadata["addresses"].get(".dynsym")
                and new[1] == packaged_metadata["addresses"].get(".dynsym")
            ):
                raise ValueError("unexpected dynamic metadata change: " + tool.name)
            relocations.append(
                {
                    "index": index,
                    "tag": 6,
                    "old": old[1],
                    "new": new[1],
                    "targetSection": ".dynsym",
                }
            )
    packaged_sha = hashlib.sha256(tool.read_bytes()).hexdigest()
    raw = tool.with_name("." + tool.name + "-original")
    tool.rename(raw)
    prefix = (
        "#!" + os.environ["sdkBash"] + "\n"
        "export LD_LIBRARY_PATH=" + shlex.quote(runtime) + "\n"
        "export PATH=" + shlex.quote(paths) + "\n"
        "export CPATH=" + shlex.quote(headers) + "\n"
    )
    arguments = clang_arguments if tool.name == "clang-10" else []
    header_search = ""
    if tool.name == "swift":
        header_search = (
            'case "${0##*/}" in swift|swiftc) set -- '
            + " ".join(
                map(
                    shlex.quote,
                    [
                        "-Xcc",
                        "-nostdinc",
                        "-Xcc",
                        "-isystem",
                        "-Xcc",
                        str(root / "lib/swift/clang/include"),
                    ],
                )
            )
            + ' "$@" ;; esac\n'
        )
    tool.write_text(
        prefix
        + header_search
        + 'exec -a "$0" '
        + shlex.quote(str(raw))
        + " "
        + " ".join(map(shlex.quote, arguments))
        + ' "$@"\n'
    )
    tool.chmod(0o755)
    if hashlib.sha256(raw.read_bytes()).hexdigest() != packaged_sha:
        raise ValueError("packaged tool bytes changed")
    facts.append(
        {
            "tool": tool.name,
            "originalSha256": sha,
            "packagedSha256": packaged_sha,
            "interpreterChanged": interpreter,
            "originalAllocatedSectionsExceptInterpreter": original_sections,
            "packagedAllocatedSectionsExceptInterpreter": packaged_sections,
            "verifiedLoaderRelocations": relocations,
            "raw": str(raw),
            "wrapperSha256": hashlib.sha256(tool.read_bytes()).hexdigest(),
        }
    )
(root / "original-tool-bytes.json").write_text(
    json.dumps(facts, indent=2, sort_keys=True) + "\n"
)
(root / "original-runtime-bytes.json").write_text(
    json.dumps(runtime_facts, indent=2, sort_keys=True) + "\n"
)
