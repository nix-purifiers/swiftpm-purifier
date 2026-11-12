# SPDX-License-Identifier: MIT
"""Strict data-only original pre-workspace native build-plan projection.

This reads successful native observations; it never executes plan commands.
"""

from pathlib import Path
import json, re, os


class Reject(ValueError):
    pass


def require(value, message):
    if not value:
        raise Reject(message)


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "duplicate nested JSON key")
        result[key] = value
    return result


def constant(value):
    raise Reject("nonfinite JSON")


def decode(raw):
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError, UnicodeError) as e:
        raise Reject("invalid JSON data: " + str(e))


def string_list(value):
    return isinstance(value, list) and all(
        isinstance(x, str) and "\0" not in x for x in value
    )


def scalar(raw):
    if raw.startswith(('"', "[", "{")):
        try:
            return decode(raw)
        except ValueError:
            raise Reject("non-JSON scalar/collection")
    if raw in ("true", "false"):
        return raw == "true"
    require(bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", raw)), "non-data scalar")
    return raw


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
        set(result["targets"]) <= {"main", "test"} and "main" in result["targets"],
        "targets",
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


def safe_path(path, root, file=False):
    require(isinstance(path, str), "native path type")
    path = Path(path)
    root = Path(root)
    require(path.is_absolute(), "absolute native path")
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        raise Reject("escaping native path")
    require(".." not in parts and "." not in parts, "unsafe lexical path")
    for ancestor in [*reversed(root.parents), root]:
        require(not ancestor.is_symlink(), "symlink native root ancestor")
    current = root
    for part in parts:
        current = current / part
        require(not current.is_symlink(), "symlink native path")
    if file:
        require(parts and path.is_file(), "missing/nonregular native file")
    return parts


def regular(path, root):
    return safe_path(path, root, True)


def link_operands(args, compiler, output, build, objects):
    require(
        string_list(args) and args and args[0] == compiler,
        "observed link compiler identity",
    )
    result = []
    i = 1
    while i < len(args):
        value = args[i]
        if value in ("-g", "-emit-library", "-emit-executable"):
            i += 1
            continue
        if value == "-o":
            require(i + 1 < len(args) and args[i + 1] == output, "link output operand")
            regular(args[i + 1], build)
            i += 2
            continue
        if value == "-Xlinker":
            require(
                i + 1 < len(args) and args[i + 1] == "-rpath=$ORIGIN",
                "unsupported linker argument",
            )
            i += 2
            continue
        if value in ("-L", "-I"):
            require(i + 1 < len(args), "missing directory operand")
            safe_path(args[i + 1], build)
            i += 2
            continue
        if value.startswith(("-L", "-I")) and len(value) > 2:
            safe_path(value[2:], build)
            i += 1
            continue
        require(
            value in objects and not value.startswith("@"),
            "unsupported/unjoined link operand",
        )
        regular(value, build)
        require(value not in result, "duplicate link operand")
        result.append(value)
        i += 1
    return result


def products(parsed, root, explicit, compiler):
    root = Path(root)
    build = root / ".build/debug"
    commands = parsed["commands"]
    modules = {}
    objects = {}
    names = set()
    compiler_keys = {
        "tool",
        "executable",
        "module-name",
        "module-output-path",
        "inputs",
        "outputs",
        "import-paths",
        "temps-path",
        "objects",
        "other-args",
        "sources",
        "is-library",
        "enable-whole-module-optimization",
        "num-threads",
    }
    for marker, row in commands.items():
        require(isinstance(row, dict), "command mapping")
        tool = row.get("tool")
        if tool == "swift-compiler":
            require(set(row) == compiler_keys, "compiler keys")
            require(
                isinstance(compiler, str)
                and row["executable"] == compiler
                and Path(compiler).is_absolute(),
                "observed compiler identity",
            )
            for field in (
                "inputs",
                "outputs",
                "import-paths",
                "objects",
                "other-args",
                "sources",
            ):
                require(string_list(row[field]), "compiler list type")
            for field in (
                "module-name",
                "module-output-path",
                "temps-path",
                "num-threads",
            ):
                require(isinstance(row[field], str), "compiler scalar type")
            require(
                type(row["is-library"]) is bool
                and type(row["enable-whole-module-optimization"]) is bool,
                "compiler boolean type",
            )
            require(
                re.fullmatch(r"[1-9][0-9]*", row["num-threads"]) is not None
                and int(row["num-threads"]) <= 4096,
                "compiler thread scalar",
            )
            safe_path(row["temps-path"], build)
            for directory in row["import-paths"]:
                safe_path(directory, build)
            name = row["module-name"]
            require(
                isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name),
                "module name",
            )
            require(name not in names, "module collision")
            names.add(name)
            require(marker == "<" + name + ".module>", "native module marker identity")
            require(
                Path(row["module-output-path"]).name == name + ".swiftmodule",
                "module output identity",
            )
            require(
                row["outputs"] == [marker, *row["objects"]], "module object/output join"
            )
            require(
                isinstance(row["sources"], list)
                and row["sources"]
                and len(row["sources"]) == len(set(row["sources"])),
                "module sources",
            )
            owners = []
            for source in row["sources"]:
                parts = regular(source, root)
                owners.append(
                    "dependency"
                    if parts[0] == "Packages"
                    else "test"
                    if parts[0] == "Tests"
                    else "root"
                )
            require(len(set(owners)) == 1, "mixed module source ownership")
            if owners[0] != "test":
                regular(row["module-output-path"], build)
            modules[marker] = {
                "name": name,
                "owner": owners[0],
                "isLibrary": row["is-library"],
                "inputs": row["inputs"],
                "objects": row["objects"],
            }
            require(isinstance(row["objects"], list) and row["objects"], "objects")
            for obj in row["objects"]:
                if owners[0] != "test":
                    regular(obj, build)
                require(obj not in objects, "object collision")
                objects[obj] = marker
        elif tool == "shell":
            require(
                set(row) == {"tool", "description", "inputs", "outputs", "args"},
                "shell keys",
            )
        elif tool == "archive":
            require(set(row) == {"tool", "inputs", "outputs"}, "archive keys")
        else:
            raise Reject("unknown tool")
        if tool in ("shell", "archive"):
            require(
                string_list(row["inputs"])
                and string_list(row["outputs"])
                and row["outputs"],
                "link/archive list types",
            )
            if tool == "shell":
                require(
                    isinstance(row["description"], str)
                    and string_list(row["args"])
                    and row["args"]
                    and row["args"][0] == compiler,
                    "shell scalar/compiler type",
                )
    for marker, module in modules.items():
        require(all(x in modules for x in module["inputs"]), "module dependency join")
    visiting = set()
    done = set()

    def visit(marker):
        require(marker not in visiting, "module graph cycle")
        if marker in done:
            return
        visiting.add(marker)
        for dependency in modules[marker]["inputs"]:
            visit(dependency)
        visiting.remove(marker)
        done.add(marker)

    for marker in modules:
        visit(marker)
    normalized_explicit = []
    require(isinstance(explicit, list), "explicit products list")
    for item in explicit:
        require(
            isinstance(item, dict)
            and set(item) == {"name", "type", "modules"}
            and item["type"] in ("exe", "dylib", "a"),
            "explicit shape/type",
        )
        require(
            isinstance(item["name"], str)
            and string_list(item["modules"])
            and item["modules"]
            and len(item["modules"]) == len(set(item["modules"])),
            "explicit name/module identity",
        )
        normalized = {
            "name": item["name"],
            "type": "executable" if item["type"] == "exe" else "library",
            "modules": sorted(item["modules"]),
        }
        if item["type"] != "exe":
            normalized["linkage"] = "dynamic" if item["type"] == "dylib" else "static"
        normalized_explicit.append(normalized)
    require(
        len({p["name"] for p in normalized_explicit}) == len(normalized_explicit),
        "explicit product collision",
    )
    result = []
    output_names = set()
    for marker in parsed["targets"]["main"]:
        require(marker in commands, "missing main command")
        if marker in modules:
            require(modules[marker]["owner"] != "test", "test in main")
            continue
        match = re.fullmatch(r"<([A-Za-z_][A-Za-z0-9_-]*)\.(exe|dylib|a)>", marker)
        require(match is not None, "unknown/test main product")
        name, kind = match.groups()
        row = commands[marker]
        require(
            row["tool"] == ("archive" if kind == "a" else "shell"), "product tool/type"
        )
        require(
            isinstance(row["outputs"], list)
            and len(row["outputs"]) == 2
            and row["outputs"][0] == marker,
            "output shape",
        )
        regular(row["outputs"][1], build)
        require(Path(row["outputs"][1]).parent == build, "nested output")
        expected = (
            name
            if kind == "exe"
            else "lib" + name + (".so" if kind == "dylib" else ".a")
        )
        require(Path(row["outputs"][1]).name == expected, "output identity/type")
        require(expected not in output_names, "output collision")
        output_names.add(expected)
        if kind != "a":
            args = row["args"]
            require(string_list(args), "args")
            require(args and args[0] == compiler, "observed link compiler identity")
            require(
                args.count("-o") == 1
                and args.index("-o") + 1 < len(args)
                and args[args.index("-o") + 1] == row["outputs"][1],
                "link output",
            )
            require(
                args.count("-emit-executable") == (1 if kind == "exe" else 0)
                and args.count("-emit-library") == (1 if kind == "dylib" else 0),
                "exclusive link type",
            )
        require(string_list(row["inputs"]), "inputs")
        linked = []
        for item in row["inputs"]:
            module = item if item in modules else objects.get(item)
            require(module is not None, "unjoined product input")
            if module not in linked:
                linked.append(module)
        require(
            linked and all(modules[m]["owner"] != "test" for m in linked),
            "test/empty product modules",
        )
        require(
            all(
                set(modules[marker]["objects"]) <= set(row["inputs"])
                for marker in linked
            ),
            "complete linked module objects",
        )
        if kind != "a":
            require(
                link_operands(row["args"], compiler, row["outputs"][1], build, objects)
                == [item for item in row["inputs"] if item in objects],
                "link object/input join",
            )
        owned = [modules[m]["name"] for m in linked if modules[m]["owner"] == "root"]
        if not owned:
            continue
        product = {
            "name": name,
            "type": "executable" if kind == "exe" else "library",
            "modules": sorted(owned),
        }
        if kind != "exe":
            product["linkage"] = "dynamic" if kind == "dylib" else "static"
        if kind != "exe" or name not in owned:
            require(product in normalized_explicit, "undeclared library/alias")
        else:
            require(
                len(owned) == 1
                and any(
                    modules[x]["name"] == name and not modules[x]["isLibrary"]
                    for x in linked
                ),
                "implicit executable module identity",
            )
        result.append(product)
    require(len({p["name"] for p in result}) == len(result), "product name collision")
    for item in normalized_explicit:
        require(item in result, "explicit product join")
    return result


def native_absence(root, override=None):
    root = Path(root)
    require(override is None, "external native lock override")
    for name in ("Package.pins", "Package.resolved"):
        require(not os.path.lexists(root / name), "native lock file presence")
    state = root / ".build"
    require(
        not state.is_symlink() and not (state / "debug").is_symlink(),
        "symlink build layout",
    )
    if os.path.lexists(state):
        require(
            state.is_dir() and not any(state.glob("*state*.json")),
            "workspace/layout state present",
        )
    return True
