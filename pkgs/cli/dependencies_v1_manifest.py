# SPDX-License-Identifier: MIT
"""Native manifest inventories and unambiguous dependency product joins."""

from pathlib import Path
import re
import shlex

import dependencies_v1_layout as layout

import legacy_manifest
import dependencies_v1_plan as plan

require = plan.require


def invocation(stdout, root, dependency_parents=None):
    """Recover the root invocation amid measured native dependency invocations."""
    require(
        isinstance(stdout, bytes) and len(stdout) <= 1024 * 1024,
        "native verbose manifest bound",
    )
    root = layout.canonical(root)
    allowed = None
    if dependency_parents is not None:
        allowed = {layout.canonical(parent) for parent in dependency_parents}
        require(
            len(allowed) <= 1024
            and all(
                parent.parent == root / "Packages" and plan.safe_name(parent.name)
                for parent in allowed
            ),
            "explicit owned manifest parents",
        )
    try:
        lines = stdout.decode("utf-8").splitlines(keepends=True)
    except UnicodeError as error:
        raise plan.Reject("native verbose UTF8") from error
    if lines and lines[0] == "which clang\n":
        lines = lines[1:]
    boundary = next(
        (index for index, line in enumerate(lines) if line.rstrip("\r\n") == "{"), None
    )
    require(
        boundary is not None and 0 < boundary <= 1025,
        "native compiler invocation sequence",
    )
    description = "".join(lines[boundary:])
    invocations = []
    parents = set()
    for line in lines[:boundary]:
        try:
            argv = shlex.split(line)
        except ValueError as error:
            raise plan.Reject("native compiler invocation quoting") from error
        manifests = [
            Path(value)
            for value in argv
            if Path(value).is_absolute()
            and re.fullmatch(r"Package(@swift-[0-9.]+)?\.swift", Path(value).name)
        ]
        require(len(manifests) == 1, "one native compiler manifest")
        parent = layout.canonical(manifests[0].parent)
        require(
            parent == root
            or (
                parent in allowed
                if allowed is not None
                else parent.parent == root / ".build/checkouts"
            ),
            "native compiler manifest outside owned graph",
        )
        require(parent not in parents, "duplicate native compiler manifest")
        parents.add(parent)
        compiler, decoded = legacy_manifest.invocation(
            (line + description).encode(), parent
        )
        invocations.append((parent, compiler, decoded))
    require(invocations[0][0] == root, "root native compiler invocation first")
    selected = invocations[0][1][0]
    require(
        all(compiler[0] == selected for _, compiler, _ in invocations),
        "native manifest selected compiler identity",
    )
    return invocations[0][1], invocations[0][2]


def inventory(description, graph, raw_protocol, root):
    """Join successful native validation, inherited-FD protocol and sources.

    Callers execute native validation only in an isolated source copy. An
    absent legacy products field is never treated as an explicit empty list.
    """
    data = legacy_manifest.json_value(raw_protocol)
    require(
        isinstance(data, dict) and data.get("errors") == [], "native manifest errors"
    )
    require(data.get("package") == description, "native manifest protocol package join")
    require(
        graph.get("name") == description.get("name") and graph.get("path") == str(root),
        "native inventory package/source join",
    )
    require(plan.safe_name(description.get("name")), "native inventory package name")
    require(isinstance(graph.get("targets"), list), "native inventory targets")
    targets = {}
    for target in graph["targets"]:
        require(
            isinstance(target, dict)
            and plan.safe_name(target.get("name"))
            and target["name"] not in targets,
            "native inventory target identity",
        )
        require(
            target.get("type") in ("library", "executable", "test")
            and target.get("module_type") == "SwiftTarget",
            "native inventory target kind",
        )
        folder = plan.path_in_root(target.get("path"), root)
        require(
            folder.relative_to(root).parts
            and folder.relative_to(root).parts[0] not in (".build", ".git", "Packages"),
            "native inventory target metadata collision",
        )
        require(
            isinstance(target.get("sources"), list) and target["sources"],
            "native inventory sources",
        )
        for name in target["sources"]:
            require(
                isinstance(name, str)
                and not Path(name).is_absolute()
                and ".." not in Path(name).parts
                and str(Path(name)) == name,
                "native inventory relative source",
            )
            plan.path_in_root(str(folder / name), root, regular=True)
        targets[target["name"]] = target
    products = []
    if set(data) == {"errors", "package"}:
        require(
            isinstance(description.get("products"), list),
            "native4 explicit product inventory",
        )
        for product in description["products"]:
            require(
                isinstance(product, dict)
                and product.get("product_type") in ("library", "executable"),
                "native4 inventory product type",
            )
            expected = {"name", "product_type", "targets"} | (
                {"type"} if product["product_type"] == "library" else set()
            )
            require(
                set(product) == expected and plan.safe_name(product["name"]),
                "native4 inventory product fields",
            )
            if product["product_type"] == "library":
                require(
                    product["type"] in (None, "static", "dynamic"),
                    "native4 inventory linkage",
                )
            products.append(
                {
                    "name": product["name"],
                    "type": product["product_type"],
                    "targets": product["targets"],
                }
            )
    else:
        # Measured legacy API3 no-explicit-product domain. The native4 graph
        # creates a library product named after the package, covering its
        # library targets, rather than products named after individual modules.
        require(
            set(data) == {"errors", "package", "products"}
            and "products" not in description,
            "native legacy manifest protocol",
        )
        explicit = legacy_manifest.protocol(raw_protocol, description)
        require(
            not explicit, "legacy explicit products require additional native evidence"
        )
        libraries = sorted(
            name for name, target in targets.items() if target["type"] == "library"
        )
        if libraries:
            products.append(
                {"name": description["name"], "type": "library", "targets": libraries}
            )
    seen = set()
    for product in products:
        require(product["name"] not in seen, "duplicate dependency product name")
        seen.add(product["name"])
        declared = product["targets"]
        require(
            isinstance(declared, list)
            and declared
            and all(isinstance(name, str) and name in targets for name in declared)
            and len(declared) == len(set(declared)),
            "dependency product targets",
        )
        if product["type"] == "library":
            require(
                all(targets[name]["type"] == "library" for name in declared),
                "dependency library target types",
            )
    return {
        "package": description["name"],
        "products": products,
        "description": description,
    }


def join_references(description, graph, dependencies):
    """Reject missing/ambiguous external product identities after native success.

    `dependencies` maps actual direct dependency URLs to native inventories;
    transitive products cannot satisfy a reference to an undeclared package.
    """
    require(
        isinstance(description.get("dependencies"), list), "native direct dependencies"
    )
    direct = []
    for record in description["dependencies"]:
        require(
            isinstance(record, dict) and isinstance(record.get("url"), str),
            "native dependency URL",
        )
        url = record["url"]
        require(
            url in dependencies and url not in direct,
            "native dependency inventory join",
        )
        direct.append(url)
    local = {target["name"] for target in graph["targets"]}
    joins = []
    require(
        isinstance(description.get("targets"), list), "native reference target list"
    )
    for target in description["targets"]:
        require(
            isinstance(target, dict) and isinstance(target.get("dependencies"), list),
            "native target references",
        )
        for reference in target["dependencies"]:
            require(
                isinstance(reference, dict)
                and reference.get("type") in ("target", "byname", "product"),
                "native target reference kind",
            )
            kind = reference["type"]
            name = reference.get("name")
            require(plan.safe_name(name), "native target reference name")
            fields = (
                {"type", "name", "package"} if kind == "product" else {"type", "name"}
            )
            require(set(reference) == fields, "native target reference fields")
            if kind == "target" or (kind == "byname" and name in local):
                require(name in local, "missing local target")
                continue
            package = reference.get("package")
            require(
                package is None or plan.safe_name(package),
                "native product package qualifier",
            )
            candidates = [
                {
                    "URL": url,
                    "package": dependencies[url]["package"],
                    "product": product["name"],
                    "targets": product["targets"],
                }
                for url in direct
                for product in dependencies[url]["products"]
                if product["name"] == name
                and (package is None or package == dependencies[url]["package"])
            ]
            require(
                len(candidates) == 1, "ambiguous or missing dependency product identity"
            )
            joins.append({"reference": reference, "candidate": candidates[0]})
    return joins
