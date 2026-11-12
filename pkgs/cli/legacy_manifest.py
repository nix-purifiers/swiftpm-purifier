# SPDX-License-Identifier: MIT
"""Read the original native manifest protocol; never infer custom products."""

import json
import os
from pathlib import Path
import re
import shlex


class ManifestError(ValueError):
    pass


def json_value(raw):
    if len(raw) > 1024 * 1024:
        raise ManifestError("native manifest JSON exceeds its bound")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ManifestError("duplicate native manifest JSON key")
            result[key] = value
        return result

    def constant(value):
        raise ManifestError("invalid native manifest JSON constant: " + value)

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError) as error:
        raise ManifestError("invalid native manifest JSON: " + str(error)) from error


def invocation(verbose_stdout, package):
    """Recover only the compiler invocation actually emitted by SwiftPM.

    The caller must first establish native SwiftPM exit success. Replay uses
    the same cwd/environment and replaces only the inherited file descriptor.
    No SDK prefix or manifest compiler override is guessed here.
    """
    if len(verbose_stdout) > 1024 * 1024:
        raise ManifestError("native verbose manifest output exceeds its bound")
    try:
        first, description = verbose_stdout.decode("utf-8").split("\n", 1)
        argv = shlex.split(first)
    except (ValueError, UnicodeError) as error:
        raise ManifestError("native manifest invocation is not unambiguous") from error
    if (
        len(argv) < 10
        or not os.path.isabs(argv[0])
        or not os.access(argv[0], os.X_OK)
        or not Path(argv[0]).is_file()
        or argv[-2] != "-fileno"
        or re.fullmatch(r"[0-9]+", argv[-1]) is None
        or argv.count("-fileno") != 1
        or argv.count("-I") != 1
        or argv.count("-L") != 1
        or argv.count("-lPackageDescription") != 1
    ):
        raise ManifestError("unsupported native manifest compiler invocation")
    try:
        includes = Path(argv[argv.index("-I") + 1])
        libraries = Path(argv[argv.index("-L") + 1])
    except IndexError as error:
        raise ManifestError("incomplete native manifest library invocation") from error
    if (
        not includes.is_absolute()
        or includes != libraries
        or not includes.is_dir()
        or not (includes / "PackageDescription.swiftmodule").exists()
        or not any(
            (includes / name).is_file()
            for name in ("libPackageDescription.so", "libPackageDescription.dylib")
        )
    ):
        raise ManifestError("native manifest compiler/library binding is unavailable")
    package = package.resolve()
    manifests = [
        Path(value)
        for value in argv[1:-2]
        if Path(value).is_absolute()
        and Path(value).parent.resolve() == package
        and re.fullmatch(r"Package(@swift-[0-9.]+)?\.swift", Path(value).name)
        and Path(value).is_file()
    ]
    if len(manifests) != 1:
        raise ManifestError(
            "native manifest invocation does not name one project manifest"
        )
    description = json_value(description)
    if (
        not isinstance(description, dict)
        or not isinstance(description.get("name"), str)
        or not description["name"]
    ):
        raise ManifestError("unsupported native package description")
    return argv[:-2], description


def protocol(raw, description):
    data = json_value(raw)
    if not isinstance(data, dict) or set(data) != {"errors", "package", "products"}:
        raise ManifestError("unsupported native manifest protocol shape")
    if not isinstance(data["errors"], list) or data["errors"]:
        raise ManifestError("native manifest reported errors")
    if data["package"] != description:
        raise ManifestError(
            "native manifest protocol disagrees with SwiftPM validation"
        )
    if not isinstance(data["products"], list):
        raise ManifestError("invalid native manifest products")
    products = []
    seen = set()
    for product in data["products"]:
        if (
            not isinstance(product, dict)
            or set(product) != {"name", "type", "modules"}
            or not isinstance(product["name"], str)
            or not product["name"]
            or product["name"] in (".", "..")
            or any(char in product["name"] for char in ("/", "\\", "\0"))
            or product["name"] in seen
            or product["type"] not in ("a", "dylib", "exe")
            or not isinstance(product["modules"], list)
            or not product["modules"]
            or not all(isinstance(name, str) and name for name in product["modules"])
            or len(set(product["modules"])) != len(product["modules"])
        ):
            raise ManifestError("unsupported or ambiguous native product")
        seen.add(product["name"])
        products.append(product)
    return products


def product_view(explicit, graph, package_name):
    """Explicit native products plus native implicit executable products.

    Old library modules alone produce module files, not an installable archive
    or shared library. They must not be advertised as inferred products.
    """
    if (
        not isinstance(graph, dict)
        or graph.get("name") != package_name
        or not isinstance(graph.get("modules"), list)
    ):
        raise ManifestError("invalid native module graph")
    modules = {}
    for module in graph["modules"]:
        if (
            not isinstance(module, dict)
            or not isinstance(module.get("name"), str)
            or not module["name"]
            or module["name"] in (".", "..")
            or any(char in module["name"] for char in ("/", "\\", "\0"))
            or module["name"] in modules
            or type(module.get("is_test")) is not bool
            or module.get("type") not in ("library", "executable")
        ):
            raise ManifestError("unsupported or ambiguous native module graph")
        modules[module["name"]] = module
    result = []
    by_name = {}
    for product in explicit:
        if any(
            name not in modules or modules[name]["is_test"]
            for name in product["modules"]
        ):
            raise ManifestError("native product names an unavailable or test module")
        entry = {
            "name": product["name"],
            "type": "executable" if product["type"] == "exe" else "library",
            "modules": product["modules"],
        }
        if entry["type"] == "library":
            entry["linkage"] = "static" if product["type"] == "a" else "dynamic"
        result.append(entry)
        by_name[entry["name"]] = entry
    for name, module in modules.items():
        if module["is_test"] or module["type"] != "executable":
            continue
        implicit = {"name": name, "type": "executable", "modules": [name]}
        if name in by_name:
            if by_name[name] != implicit:
                raise ManifestError(
                    "native explicit and implicit products have ambiguous names"
                )
        else:
            result.append(implicit)
    return result


def capture(package, package_command, environment, evidence):
    """Observe original manifest products through the selected native tools.

    Native validation precedes replay. All observations are private evidence;
    the caller decides whether and where a portable dependency lock is written.
    """
    import bounded_native

    evidence.mkdir()
    logs = evidence / "logs"
    logs.mkdir()
    receipt = {"complete": False, "commands": []}

    def save():
        (evidence / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    def command(label, argv, protocol_flag=None):
        row = {"label": label}
        receipt["commands"].append(row)
        result = bounded_native.run_recorded(
            argv,
            record=row,
            logs=logs,
            save=save,
            cwd=package,
            env=environment,
            timeout=60,
            output_limit=1024 * 1024,
            protocol_fd_flag=protocol_flag,
        )
        if result.returncode:
            raise ManifestError(
                "selected native tool rejected legacy manifest; evidence at "
                + str(evidence)
            )
        return result

    validated = command(
        "native-validation", [*package_command, "--verbose", "dump-package"]
    )
    compiler, description = invocation(validated.stdout, package)
    replayed = command("native-protocol", compiler, "-fileno")
    if replayed.stdout:
        raise ManifestError(
            "native manifest emitted unexpected stdout; evidence at " + str(evidence)
        )
    explicit = protocol(replayed.protocol, description)
    graph = command(
        "native-module-graph", [*package_command, "describe", "--type", "json"]
    )
    products = product_view(explicit, json_value(graph.stdout), description["name"])
    receipt.update(complete=True, package=description["name"], products=products)
    save()
    return products, description["name"]
