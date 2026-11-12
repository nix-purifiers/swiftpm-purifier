# Ordinary header path relocation only; preserve every module/API directive.
from pathlib import Path
import hashlib, json, os, re

root = Path(os.environ["out"])
p = root / "lib/swift/linux/x86_64/glibc.modulemap"
before = p.read_bytes()
rows = []


def relocate(match):
    old = match.group(2).decode()
    relative = (
        ("/" + old.lstrip("/"))
        .removeprefix("/usr/include/")
        .removeprefix("x86_64-linux-gnu/")
    )
    base = (
        Path(os.environ["sdkCUuidHeaders"])
        if relative.startswith("uuid/")
        else Path(os.environ["sdkGlibcHeaders"])
    )
    target = base / relative
    if not target.is_file():
        raise ValueError("missing actual native header " + str(target))
    rows.append(
        {
            "original": old,
            "packaged": str(target),
            "headerSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }
    )
    return match.group(1) + str(target).encode() + match.group(3)


after = re.sub(rb'(?m)^([ \t]*header ")(/+usr/include/[^"\n]+)(")', relocate, before)
assert len(rows) == 77
reverse = after
for row in rows:
    reverse = reverse.replace(
        ('header "' + row["packaged"] + '"').encode(),
        ('header "' + row["original"] + '"').encode(),
    )
assert reverse == before
p.write_bytes(after)
(root / "original-header-relocations.json").write_text(
    json.dumps(
        {
            "path": str(p.relative_to(root)),
            "originalSha256": hashlib.sha256(before).hexdigest(),
            "packagedSha256": hashlib.sha256(after).hexdigest(),
            "mappings": rows,
            "reverseByteExact": True,
        },
        indent=2,
    )
    + "\n"
)
