# SPDX-License-Identifier: MIT
"""Fresh-process selected-file publication/recovery, including external parents."""

from pathlib import Path
import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys

source = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(source / "pkgs/cli"))
import dependencies_v1_transaction as transaction
import dependencies_v1_selectors as selectors


def paths(folder, config):
    native = (
        None
        if config["domain"] == "null"
        else folder / "native/pins"
        if config["domain"] in ("external-native", "external-both")
        else selectors.DEFAULT
    )
    portable = (
        folder / "portable/lock"
        if config["domain"] in ("external-portable", "external-both")
        else selectors.DEFAULT
    )
    return native, portable


def instance(folder, config):
    return transaction.Transaction(
        folder / "root", folder / "registry", *paths(folder, config)
    )


if len(sys.argv) > 1 and sys.argv[1] == "worker":
    try:
        folder = Path(sys.argv[2])
        config = json.loads((folder / "config.json").read_text())
        operation = sys.argv[3]
        point = sys.argv[4]
        trace = []

        def callback(label):
            trace.append(label)
            if point == label:
                os._exit(75)

        service = instance(folder, config)
        with service.locked():
            if operation == "publish":
                assert service.recover() == "clean"
                service.publish(
                    [transaction.decoded(value) for value in config["new"]],
                    [transaction.decoded(value) for value in config["old"]],
                    callback,
                )
                result = "published"
            else:
                result = service.recover(callback)
        print(json.dumps({"result": result, "trace": trace}))
        sys.exit(0)
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"refused": str(error)}))
        sys.exit(3)

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
work = args.output.resolve()
work.mkdir()
(work / "logs").mkdir()
report = {
    "complete": False,
    "scope": "Production selected-file transaction process-interruption and foreign-write controls; no power-loss/full directory installer/native runtime/support claim.",
    "sourceSHA256": hashlib.sha256(
        (source / "pkgs/cli/dependencies_v1_transaction.py").read_bytes()
    ).hexdigest(),
    "commands": [],
    "cases": [],
}


def save():
    (work / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")


def call(label, folder, operation, point="none", expected=0):
    argv = [
        sys.executable,
        str(Path(__file__).resolve()),
        "worker",
        str(folder),
        operation,
        point,
    ]
    x = subprocess.run(argv, capture_output=True, timeout=5)
    (work / "logs" / (label + ".stdout")).write_bytes(x.stdout)
    (work / "logs" / (label + ".stderr")).write_bytes(x.stderr)
    report["commands"].append(
        {
            "label": label,
            "argv": argv,
            "exit": x.returncode,
            "stdoutSHA256": hashlib.sha256(x.stdout).hexdigest(),
            "stderrSHA256": hashlib.sha256(x.stderr).hexdigest(),
        }
    )
    save()
    assert x.returncode == expected, (label, x.returncode, x.stdout, x.stderr)
    return json.loads(x.stdout) if x.stdout else None


def setup(label, domain, old_native, new_native, old_portable=b"old portable"):
    folder = work / label
    folder.mkdir()
    for name in ("root", "native", "portable"):
        (folder / name).mkdir()
    (folder / "registry").mkdir(mode=0o700)
    root = folder / "root"
    (root / "unrelated").write_bytes(b"caller unrelated data")
    config = {"domain": domain}
    native, portable = paths(folder, config)
    native_path = root / "Package.resolved" if native is selectors.DEFAULT else native
    portable_path = (
        root / "swiftpm-purifier.lock.json"
        if portable is selectors.DEFAULT
        else portable
    )
    if native_path != root / "Package.resolved":
        (root / "Package.resolved").write_bytes(b"logical root preserved")
    if portable_path != root / "swiftpm-purifier.lock.json":
        (root / "swiftpm-purifier.lock.json").write_bytes(
            b"unselected default lock preserved"
        )
    old_native = None if native is None else old_native
    new_native = None if native is None else new_native
    for path, raw, mode in (
        (native_path, old_native, 0o751),
        (portable_path, old_portable, 0o640),
    ):
        if path is not None and raw is not None:
            path.write_bytes(raw)
            path.chmod(mode)
    config.update(
        old=list(map(transaction.encoded, [old_native, old_portable])),
        new=list(map(transaction.encoded, [new_native, b"new portable"])),
    )
    (folder / "config.json").write_text(json.dumps(config))
    return folder, config


def verify(folder, config, committed):
    service = instance(folder, config)
    with service.locked():
        expected = [
            transaction.decoded(value)
            for value in config["new" if committed else "old"]
        ]
        assert service.pair() == expected
        old = [transaction.decoded(value) for value in config["old"]]
        modes = [
            None
            if raw is None
            else (0o751 if index == 0 else 0o640)
            if old[index] is not None
            else 0o644
            for index, raw in enumerate(expected)
        ]
        assert service.modes() == modes, (service.modes(), modes)
        assert service.recover() == "clean"
    root = folder / "root"
    assert (root / "unrelated").read_bytes() == b"caller unrelated data"
    if config["domain"] in ("null", "external-native", "external-both"):
        assert (root / "Package.resolved").read_bytes() == b"logical root preserved"
    if config["domain"] in ("external-portable", "external-both"):
        assert (
            root / "swiftpm-purifier.lock.json"
        ).read_bytes() == b"unselected default lock preserved"
    for parent in [root, folder / "native", folder / "portable"]:
        assert not any(
            path.name.startswith(transaction.PREFIX) or path.name == transaction.JOURNAL
            for path in parent.iterdir()
        )
    assert all(path.suffix == ".lock" for path in (folder / "registry").iterdir())


variants = [
    (domain, old, new, b"old portable")
    for domain in ("default", "external-both")
    for old in (None, b"old native")
    for new in (None, b"new native")
]
variants += [
    ("null", None, None, b"old portable"),
    ("external-native", b"old native", b"new native", b"old portable"),
    ("external-portable", b"old native", b"new native", b"old portable"),
]
variants += [
    (domain, None, b"new native", None) for domain in ("default", "external-both")
] + [("null", None, None, None)]
for variant_index, variant in enumerate(variants):
    folder, config = setup("baseline-" + str(variant_index), *variant)
    trace = call("baseline-" + str(variant_index), folder, "publish")["trace"]
    verify(folder, config, True)
    commit = trace.index("commit-renamed")
    for point in trace:
        label = "boundary-" + str(len(report["cases"]))
        folder, config = setup(label, *variant)
        call(label + "-publish", folder, "publish", point, 75)
        (folder / "root/.build").mkdir()
        (folder / "root/.build/output").write_text("temporary output")
        shutil.rmtree(folder / "root/.build")
        call(label + "-recover", folder, "recover")
        committed = trace.index(point) >= commit
        verify(folder, config, committed)
        report["cases"].append(
            {
                "name": label,
                "domain": variant[0],
                "point": point,
                "committed": committed,
                "exactPairAndModes": True,
                "unrelatedPreserved": True,
            }
        )
        save()
    # Interruption in recovery itself, including every cleanup boundary.
    interrupted, config = setup("rollback-baseline-" + str(variant_index), *variant)
    call(
        "rollback-baseline-" + str(variant_index) + "-publish",
        interrupted,
        "publish",
        "output-1-renamed",
        75,
    )
    recovery_trace = call(
        "rollback-baseline-" + str(variant_index) + "-recover", interrupted, "recover"
    )["trace"]
    verify(interrupted, config, False)
    for point in recovery_trace:
        label = "rollback-" + str(len(report["cases"]))
        folder, config = setup(label, *variant)
        call(label + "-publish", folder, "publish", "output-1-renamed", 75)
        call(label + "-first-recover", folder, "recover", point, 75)
        call(label + "-second-recover", folder, "recover")
        verify(folder, config, False)
        report["cases"].append(
            {
                "name": label,
                "domain": variant[0],
                "recoveryPoint": point,
                "exactOldPairAndModes": True,
                "unrelatedPreserved": True,
            }
        )
        save()
report["complete"] = True
save()
print(
    "PASS",
    len(report["cases"]),
    "fresh-process publication/rollback/cleanup boundary cases",
)
