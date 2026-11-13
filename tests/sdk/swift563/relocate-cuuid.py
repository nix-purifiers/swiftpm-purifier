# SPDX-License-Identifier: MIT
"""Relocate only the two original CUUID header paths, with byte reversal."""

from pathlib import Path
import hashlib, json, os

root = Path(os.environ["out"])
target = Path(os.environ["sdkCUuidHeaders"]) / "uuid/uuid.h"
assert target.is_file() and target.is_absolute()
old = b'header "/usr/include/uuid/uuid.h"'
new = ('header "' + str(target) + '"').encode()
rows = []
for relative in [
    "lib/swift/linux/x86_64/glibc.modulemap",
    "lib/swift_static/linux/x86_64/glibc.modulemap",
]:
    p = root / relative
    before = p.read_bytes()
    assert (
        hashlib.sha256(before).hexdigest()
        == "ff0fc39839e671441cbef0869a8c987d31a815783341705acc8c612d6af221fe"
    )
    assert before.count(old) == 1
    after = before.replace(old, new, 1)
    assert after.replace(new, old, 1) == before
    p.write_bytes(after)
    rows.append(
        {
            "path": relative,
            "originalSHA256": hashlib.sha256(before).hexdigest(),
            "packagedSHA256": hashlib.sha256(after).hexdigest(),
            "originalHeader": "/usr/include/uuid/uuid.h",
            "packagedHeader": str(target),
            "headerSHA256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "changedDirectives": 1,
            "reverseByteExact": True,
        }
    )
(root / "original-header-relocations.json").write_text(
    json.dumps(
        {
            "scope": "Ordinary single CUUID header path relocation in each original dynamic/static map; no API/macro/directive edits",
            "maps": rows,
        },
        indent=2,
    )
    + "\n"
)
