# SPDX-License-Identifier: MIT
"""Closed format8 structural parity; synthetic cases are not native evidence."""

from pathlib import Path
import argparse
import copy
import hashlib
import json
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
source = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(source / "pkgs/cli"))
import dependencies_v1 as protocol
from test_dependencies_v1 import fixture

work = args.output.resolve()
work.mkdir()
cases = []


def check(name, lock, expected):
    try:
        protocol.validate(lock)
        python = True
    except protocol.StateError:
        python = False
    path = work / (name + ".json")
    path.write_text(json.dumps(lock, ensure_ascii=False))
    expression = (
        "import "
        + str(source / "lib-overlays/default/dependencies-v1-codec.nix")
        + " { lock = builtins.fromJSON (builtins.readFile "
        + str(path)
        + "); }"
    )
    x = subprocess.run(
        ["nix", "eval", "--impure", "--json", "--expr", expression],
        capture_output=True,
        timeout=30,
    )
    (work / (name + ".stdout")).write_bytes(x.stdout)
    (work / (name + ".stderr")).write_bytes(x.stderr)
    assert x.returncode == 0, (name, x.stderr)
    nix = json.loads(x.stdout)["valid"]
    assert python == nix == expected, (name, python, nix, expected)
    cases.append(
        {
            "name": name,
            "valid": nix,
            "inputSHA256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "stdoutSHA256": hashlib.sha256(x.stdout).hexdigest(),
            "stderrSHA256": hashlib.sha256(x.stderr).hexdigest(),
        }
    )


check("baseline", fixture(), True)
for name, change, expected in [
    ("present-native", lambda x: x["nativeToolLock"].update(sha256="3" * 64), True),
    (
        "prerelease",
        lambda x: x["pins"]["Leaf"].update(version="1.0.0-beta.1+build"),
        True,
    ),
    ("zero-prerelease", lambda x: x["pins"]["Leaf"].update(version="1.0.0-01"), False),
    ("unknown-SDK", lambda x: x.update(nativeSDK={}), False),
    ("old-version", lambda x: x.update(version=7), False),
    ("bool-version", lambda x: x.update(version=True), False),
    ("bool-native-version", lambda x: x["nativeState"].update(version=True), False),
    ("unknown-native-field", lambda x: x["nativeState"].update(other=1), False),
    ("empty-pins", lambda x: x.update(pins={}), False),
    ("pin-branch", lambda x: x["pins"]["Leaf"].update(branch="main"), False),
    ("null-version", lambda x: x["pins"]["Leaf"].update(version=None), False),
    ("wrong-revision", lambda x: x["pins"]["Leaf"].update(revision="v1.0.0"), False),
    (
        "git-hash-field",
        lambda x: x["pins"]["Leaf"].update(gitHash="sha256-" + "A" * 43 + "="),
        False,
    ),
    (
        "duplicate-source",
        lambda x: x["pins"].update(Other=dict(x["pins"]["Leaf"], name="Other")),
        False,
    ),
    (
        "duplicate-product",
        lambda x: x["products"].append(copy.deepcopy(x["products"][0])),
        False,
    ),
    ("unknown-product", lambda x: x["products"][0].update(other=1), False),
    (
        "duplicate-module",
        lambda x: x["products"][0].update(modules=["One", "One"]),
        False,
    ),
    (
        "empty-targets",
        lambda x: x["products"][0]["declaration"].update(targets=[]),
        False,
    ),
    (
        "unknown-declaration",
        lambda x: x["products"][0]["declaration"].update(kind="guessed"),
        False,
    ),
    ("explicit-static", lambda x: x["products"][0].update(linkage="static"), True),
    ("explicit-dynamic", lambda x: x["products"][0].update(linkage="dynamic"), True),
    ("empty-products", lambda x: x.update(products=[]), True),
    ("unicode-name", lambda x: x.update(package="Éclair"), True),
    ("control-name", lambda x: x.update(package="Bad\nname"), False),
]:
    lock = fixture()
    change(lock)
    lock["contentHash"] = protocol.content_hash(lock)
    check(name, lock, expected)
for name, value in [("null", None), ("list", []), ("integer", 8), ("string", "8")]:
    check(name, value, False)
(work / "receipt.json").write_text(
    json.dumps(
        {
            "complete": True,
            "scope": "Synthetic format8 structural Python/Nix parity only.",
            "cases": cases,
        },
        indent=2,
    )
    + "\n"
)
print("PASS", len(cases), "Python/Nix closed codec parity cases")
