# SPDX-License-Identifier: MIT
"""Data-only native4 source/plan/artifact product projection.

The bounded lexer is separate from the format7 target grammar. Plan command
strings are observations and are never executed by this module.
"""

from pathlib import Path
import re

import pre_workspace_plan as lexer
import dependencies_v1_layout as layout

Reject = lexer.Reject
require = lexer.require
decode = lexer.decode
scalar = lexer.scalar


def plan(raw):
    require(len(raw) <= 4 * 1024 * 1024, "plan bound")
    result = {}
    section = None
    command = None
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError:
        raise Reject("plan encoding")
    for line in lines:
        if not line.strip():
            continue
        require(
            "\t" not in line
            and not line.lstrip().startswith(("#", "!", "&", "*", "---", "...")),
            "unsupported YAML syntax",
        )
        depth = len(line) - len(line.lstrip(" "))
        require(depth in (0, 2, 4), "indentation")
        text = line[depth:]
        if text.startswith('"'):
            match = re.fullmatch(r'("(?:[^"\\]|\\.)*"):(?: (.*))?', text)
            require(match is not None, "quoted key")
            key = decode(match[1])
            value = match[2]
        else:
            match = re.fullmatch(r"([a-z][a-z-]*):(?: ?(.*))?", text)
            require(match is not None, "key")
            key = match[1]
            value = match[2]
        if depth == 0:
            target = result
            section = key
            command = None
        elif depth == 2:
            require(section in result and isinstance(result[section], dict), "section")
            target = result[section]
            command = key
        else:
            require(
                section == "commands"
                and command in result[section]
                and isinstance(result[section][command], dict),
                "command",
            )
            target = result[section][command]
        require(key not in target, "duplicate key")
        target[key] = {} if value in (None, "") else scalar(value)
    require(
        set(result) == {"client", "tools", "targets", "default", "commands"}, "top keys"
    )
    require(
        result["client"] == {"name": "swift-build"}
        and result["tools"] == {}
        and result["default"] == "main",
        "client",
    )
    require(
        isinstance(result["targets"], dict)
        and {"main", "test"} <= set(result["targets"])
        and len(result["targets"]) <= 20000,
        "native4 target roots and count",
    )
    require(
        isinstance(result["commands"], dict) and len(result["commands"]) <= 20000,
        "commands bound",
    )
    for values in result["targets"].values():
        require(
            isinstance(values, list)
            and all(isinstance(v, str) for v in values)
            and len(values) == len(set(values)),
            "target list",
        )
    return result


parse = plan


def safe_name(value):
    return (
        isinstance(value, str)
        and value not in ("", ".", "..")
        and len(value.encode("utf-8")) <= 255
        and not any(c in value for c in "/\\")
        and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )


def path_in_root(value, root, regular=False):
    require(isinstance(value, str) and "\0" not in value, "path string")
    path = Path(value)
    require(
        path.is_absolute() and str(path) == value and ".." not in path.parts,
        "canonical absolute path",
    )
    require(path.is_relative_to(root), "path outside owned snapshot")
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        require(not current.is_symlink(), "unmeasured path alias")
    if regular:
        require(path.is_file(), "missing native artifact/source")
    return path


def project(root, dump, description, plan_bytes, compiler):
    root = layout.canonical(root)
    require(root.is_dir(), "physical native4 product root")
    require(description.get("path") == str(root), "native root identity")
    require(
        safe_name(dump.get("name")) and description.get("name") == dump.get("name"),
        "native root name join",
    )
    require(
        isinstance(dump.get("products"), list),
        "legacy root API needs separate evidence",
    )
    parsed = parse(plan_bytes)
    require(
        "main" in parsed["targets"] and "test" in parsed["targets"],
        "native target roots",
    )
    modules, module_paths, object_modules, phony, links = {}, {}, {}, {}, {}
    for key, command in parsed["commands"].items():
        tool = command.get("tool")
        require(
            tool in ("phony", "swift-compiler", "shell", "archive"),
            "unmodelled native command kind",
        )
        if tool == "swift-compiler":
            name = command.get("module-name")
            require(safe_name(name) and name not in modules, "unique compiler module")
            require(command.get("executable") == compiler, "actual selected compiler")
            output = path_in_root(command.get("module-output-path"), root)
            require(
                output.name == name + ".swiftmodule"
                and output.is_relative_to(root / ".build"),
                "native module output",
            )
            require(output not in module_paths, "unique module path")
            for field in ("sources", "objects", "inputs", "outputs"):
                require(
                    lexer.string_list(command.get(field))
                    and len(command[field]) == len(set(command[field])),
                    "unique native compiler lists",
                )
            require(
                command["sources"] and command["objects"],
                "nonempty native compiler lists",
            )
            for source in command["sources"]:
                path_in_root(source, root, regular=True)
            for obj in command["objects"]:
                path = path_in_root(obj, root)
                require(
                    path.is_relative_to(output.parent) and path not in object_modules,
                    "unique native object ownership",
                )
                object_modules[path] = name
            require(
                set(command["outputs"]) == set(command["objects"]) | {str(output)},
                "native compiler output join",
            )
            modules[name] = command
            module_paths[output] = name
        elif tool == "phony":
            require(
                lexer.string_list(command.get("inputs"))
                and len(command["inputs"]) == 1,
                "phony input",
            )
            require(
                lexer.string_list(command.get("outputs"))
                and len(command["outputs"]) == 1,
                "phony output",
            )
            token = command["outputs"][0]
            require(
                token.startswith("<")
                and token.endswith(">")
                and safe_name(token[1:-1]),
                "phony token",
            )
            require(token not in phony, "unique phony token")
            phony[token] = path_in_root(command["inputs"][0], root)
        else:
            require(
                lexer.string_list(command.get("outputs"))
                and len(command["outputs"]) == 1,
                "link output",
            )
            output = path_in_root(command["outputs"][0], root)
            require(
                output.is_relative_to(root / ".build") and output not in links,
                "unique owned link output",
            )
            require(lexer.string_list(command.get("inputs")), "link inputs")
            if tool == "shell":
                require(
                    lexer.string_list(command.get("args"))
                    and command["args"]
                    and command["args"][0] == compiler,
                    "non-compiler shell command",
                )
            links[output] = command
    require(modules, "native modules")
    for name, command in modules.items():
        require(
            phony.get("<" + name + ".module>") == Path(command["module-output-path"]),
            "compiler module to native phony path join",
        )
    for name, values in parsed["targets"].items():
        require(
            safe_name(name) and all(value in phony for value in values),
            "target-to-phony join",
        )
        if name not in ("main", "test"):
            require(values == ["<" + name + ">"], "named native target")
    graph = {}
    for name, command in modules.items():
        dependencies = set()
        for value in command["inputs"]:
            path = path_in_root(value, root)
            if path in module_paths:
                dependencies.add(module_paths[path])
            else:
                require(value in command["sources"], "unknown native compiler input")
        graph[name] = dependencies
    reachable_modules, visited_outputs, active_outputs = set(), set(), set()

    def visit_output(path):
        require(path not in active_outputs, "native output cycle")
        if path in visited_outputs:
            return
        active_outputs.add(path)
        if path in module_paths or path in object_modules:
            name = module_paths[path] if path in module_paths else object_modules[path]
            reachable_modules.add(name)
            for value in modules[name]["inputs"]:
                visit_output(Path(value))
        elif path in links:
            for value in links[path]["inputs"]:
                visit_output(Path(value))
        active_outputs.remove(path)
        visited_outputs.add(path)

    for token in parsed["targets"]["main"]:
        visit_output(phony[token])
    targets, c99 = {}, {}
    for target in description.get("targets", []):
        name, module = target.get("name"), target.get("c99name")
        require(
            safe_name(name)
            and name not in targets
            and safe_name(module)
            and module not in c99,
            "unique root target/module",
        )
        require(
            target.get("module_type") == "SwiftTarget"
            and target.get("type") in ("library", "executable", "test"),
            "measured native target type",
        )
        require(module in modules, "root target/compiler module join")
        folder = path_in_root(target.get("path"), root)
        relative = folder.relative_to(root)
        require(
            not relative.parts
            or relative.parts[0] not in (".build", ".git", "Packages"),
            "root source collides with metadata",
        )
        require(lexer.string_list(target.get("sources")), "root sources")
        files = []
        for source in target["sources"]:
            require(
                not Path(source).is_absolute()
                and ".." not in Path(source).parts
                and str(Path(source)) == source,
                "relative target source",
            )
            actual_source = path_in_root(str(folder / source), root, regular=True)
            require(
                actual_source.relative_to(root).parts[0]
                not in (
                    ".build",
                    ".git",
                    "Packages",
                    "Package.resolved",
                    "swiftpm-purifier.lock.json",
                ),
                "source collides with metadata",
            )
            files.append(str(actual_source))
        require(
            set(files) == set(modules[module]["sources"]),
            "native root source membership",
        )
        targets[name], c99[module] = target, name

    def closure(names):
        result, active = set(), set()

        def visit(name):
            require(name not in active, "module cycle")
            if name in result:
                return
            active.add(name)
            for child in graph[name]:
                visit(child)
            active.remove(name)
            result.add(name)

        for name in names:
            visit(name)
        return result

    closure(modules)
    results, used_names, covered = [], set(), set()

    def emit(name, type_name, linkage, declared, inferred=None):
        require(
            safe_name(name) and name not in used_names, "duplicate/unsafe product name"
        )
        require(
            declared
            and len(declared) == len(set(declared))
            and all(value in targets for value in declared),
            "declared native targets",
        )
        roots = [targets[value]["c99name"] for value in declared]
        needed = closure(roots)
        require(
            needed <= reachable_modules,
            "product module closure unreachable from selected main",
        )
        require(
            all(
                Path(modules[value]["module-output-path"]).is_file()
                and all(Path(obj).is_file() for obj in modules[value]["objects"])
                for value in needed
            ),
            "successful native product module/object evidence",
        )
        if type_name == "library":
            require(
                all(targets[value]["type"] == "library" for value in declared),
                "library source targets",
            )
        else:
            require(
                sum(targets[value]["type"] == "executable" for value in declared) == 1,
                "one executable root",
            )
        possible = (
            {name}
            if type_name == "executable"
            else {"lib" + name + ".a"}
            if linkage == "static"
            else (
                {"lib" + name + ".a", "lib" + name + ".so", "lib" + name + ".dylib"}
                if linkage == "automatic"
                else {"lib" + name + ".so", "lib" + name + ".dylib"}
            )
        )
        artifacts = [
            path
            for path in links
            if path.name in possible
            and path in {phony[token] for token in parsed["targets"]["main"]}
        ]
        if linkage == "automatic":
            require(not artifacts, "automatic native module-only product")
        else:
            require(len(artifacts) == 1, "exact native product artifact")
            require(
                artifacts[0].is_file(), "successful native product artifact evidence"
            )
            command = links[artifacts[0]]
            require(
                command["tool"] == ("archive" if linkage == "static" else "shell"),
                "native artifact tool",
            )
            require(
                all(Path(value) in object_modules for value in command["inputs"]),
                "native object input join",
            )
            expected_objects = {
                Path(value) for module in needed for value in modules[module]["objects"]
            }
            require(
                set(map(Path, command["inputs"])) == expected_objects
                and len(command["inputs"]) == len(expected_objects),
                "exact transitive artifact object coverage",
            )
        declaration = (
            {"kind": "inferred-executable", "target": inferred}
            if inferred is not None
            else {"kind": "explicit", "targets": list(declared)}
        )
        value = {
            "name": name,
            "type": type_name,
            "modules": sorted(needed),
            "declaration": declaration,
        }
        if type_name == "library":
            value["linkage"] = linkage
        results.append(value)
        used_names.add(name)

    for product in dump["products"]:
        require(isinstance(product, dict), "product object")
        type_name = product.get("product_type")
        require(type_name in ("library", "executable"), "native product type")
        fields = {"name", "product_type", "targets"} | (
            {"type"} if type_name == "library" else set()
        )
        require(
            set(product) == fields and lexer.string_list(product.get("targets")),
            "closed native declaration",
        )
        linkage = (
            ("automatic" if product["type"] is None else product["type"])
            if type_name == "library"
            else None
        )
        require(linkage in (None, "automatic", "static", "dynamic"), "native linkage")
        emit(product.get("name"), type_name, linkage, product["targets"])
        if type_name == "executable":
            covered.update(product["targets"])
    for name, target in targets.items():
        if target["type"] == "executable" and name not in covered:
            emit(name, "executable", None, [name], inferred=name)
    return {"products": sorted(results, key=lambda row: row["name"])}
