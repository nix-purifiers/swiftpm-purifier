# SPDX-License-Identifier: MIT
"""Owned pure-build projection and actual native format-8 product installation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import re
import shlex


def require(condition, message):
    if not condition:
        raise ValueError("swiftpm-purifier format8: " + message)


def decode(path):
    import dependencies_v1
    return dependencies_v1.decode(Path(path).read_bytes())


def pins_unchanged(root, lock):
    path = root / "Package.resolved"
    raw = path.read_bytes() if path.exists() else None
    digest = None if raw is None else hashlib.sha256(raw).hexdigest()
    require(digest == lock["nativeToolLock"]["sha256"], "selected native bytes/absence changed")


def edited_state(root, lock):
    return {"version": 1, "object": {"dependencies": [
        {"basedOn": None, "name": name, "repositoryURL": pin["sourceLocation"],
         "state": {"name": "edited", "path": str(root / "Packages" / name)}, "subpath": name}
        for name, pin in sorted(lock["pins"].items())
    ]}}


def prepare(args):
    import dependencies_v1_cache as cache
    import dependencies_v1_capture as capture
    import dependencies_v1_layout as layout
    import pre_workspace
    root = layout.canonical(args.root)
    lock = decode(args.lock)
    require(root.name == lock["rootDirectory"], "root directory identity")
    require(layout.source_layout(root)["hash"] == lock["sourceLayoutHash"], "source inventory")
    require(pre_workspace.manifest_hash(root) == lock["manifestHash"], "manifest bytes")
    # These paths belong to the unpacked sandbox copy, never the caller.
    for name in (".build", ".git", "Packages"):
        path = root / name
        require(not path.is_symlink(), "unexpected staged metadata link")
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    native = root / "Package.resolved"
    native.unlink(missing_ok=True)
    raw = Path(args.native_file).read_bytes() if args.native_file else None
    expected = {"object": {"dependencies": [
        {"name": name, "repositoryURL": pin["sourceLocation"], "state": {
            "name": "checkout", "checkoutState": {"branch": None, "revision": pin["revision"], "version": pin["version"]}
        }} for name, pin in sorted(lock["pins"].items())
    ]}}
    capture.native_pins(raw, expected)
    if raw is not None:
        native.write_bytes(raw)
    pins_unchanged(root, lock)
    for name, pin in sorted(lock["pins"].items()):
        source = Path(args.tree) / "Packages" / name
        require(source.is_dir(), "missing immutable dependency")
        destination = root / "Packages" / name
        destination.parent.mkdir(exist_ok=True)
        shutil.copytree(source, destination, symlinks=True)
        require(not (destination / ".git").exists(), "dependency must be VCS-free")
        for path in [destination, *destination.rglob("*")]:
            require(not path.is_symlink(), "dependency source link")
            path.chmod(path.stat().st_mode | (0o700 if path.is_dir() else 0o200))
        cache.source_tree(destination)
    (root / ".build").mkdir()
    (root / ".build/dependencies-state.json").write_text(json.dumps(edited_state(root, lock), sort_keys=True) + "\n")


def root_invocation(raw, root, pin_names, expected_compiler):
    """Admit only actual compiler observations for this owned edited graph."""
    import dependencies_v1_layout as layout
    import legacy_manifest
    require(isinstance(raw, bytes) and len(raw) <= 1024 * 1024, "verbose manifest bound")
    lines = raw.decode("utf-8").splitlines(keepends=True)
    if lines and lines[0] == "which clang\n":
        lines = lines[1:]
    boundary = next((i for i, line in enumerate(lines) if line.rstrip("\r\n") == "{"), None)
    require(boundary is not None and 0 < boundary <= len(pin_names) + 1, "bounded manifest invocation sequence")
    description = "".join(lines[boundary:])
    allowed = {root} | {root / "Packages" / name for name in pin_names}
    observed = []
    parents = set()
    for line in lines[:boundary]:
        argv = shlex.split(line)
        manifests = [Path(value) for value in argv if Path(value).is_absolute() and re.fullmatch(r"Package(@swift-[0-9.]+)?\.swift", Path(value).name)]
        require(len(manifests) == 1, "one native manifest per invocation")
        parent = layout.canonical(manifests[0].parent)
        require(parent in allowed and parent not in parents, "unique owned edited manifest parent")
        compiler, decoded = legacy_manifest.invocation((line + description).encode(), parent)
        require(compiler[0] == expected_compiler, "explicit selected compiler identity")
        observed.append((parent, compiler, decoded))
        parents.add(parent)
    require(observed[0][0] == root, "root manifest invocation first")
    return observed[0][1], observed[0][2]


def install(args):
    import dependencies_v1_capture as capture
    import dependencies_v1_cache as cache
    import dependencies_v1_layout as layout
    import pre_workspace
    import dependencies_v1_manifest as manifest
    import dependencies_v1_plan as plan
    import legacy_manifest
    root = Path(args.root).resolve()
    lock = decode(args.lock)
    pins_unchanged(root, lock)
    session = capture.NativeSession(root, os.environ, root.parent)
    observed = session.run("pure-root-validation", [args.package_command, "--verbose", "dump-package"])
    print("format8 native manifest observation", flush=True)
    print(observed.stdout.decode("utf-8"), flush=True)
    compiler, dump = root_invocation(observed.stdout, root, lock["pins"], args.compiler)
    protocol = session.run("pure-root-protocol", compiler, protocol_flag="-fileno")
    require(not protocol.stdout, "unexpected manifest stdout")
    graph = legacy_manifest.json_value(session.run("pure-root-description", [args.package_command, "describe", "--type", "json"]).stdout)
    manifest.inventory(dump, graph, protocol.protocol, root)
    inventories = {}
    for index, (name, pin) in enumerate(sorted(lock["pins"].items())):
        probe = session.evidence / ("dependency-" + str(index))
        shutil.copytree(root / "Packages" / name, probe, symlinks=True)
        validation = session.run("pure-dependency-" + str(index) + "-validation", [args.package_command, "--verbose", "dump-package"], cwd=probe)
        invocation, declared = root_invocation(validation.stdout, probe, [], args.compiler)
        fd = session.run("pure-dependency-" + str(index) + "-protocol", invocation, cwd=probe, protocol_flag="-fileno")
        require(not fd.stdout, "unexpected dependency manifest stdout")
        dependency_graph = legacy_manifest.json_value(session.run("pure-dependency-" + str(index) + "-description", [args.package_command, "describe", "--type", "json"], cwd=probe).stdout)
        inventory = manifest.inventory(declared, dependency_graph, fd.protocol, probe)
        require(inventory["package"] == name, "actual dependency package name")
        inventories[pin["sourceLocation"]] = inventory
    manifest.join_references(dump, graph, inventories)
    for inventory in inventories.values():
        declared = inventory["description"]
        if "products" not in declared:
            require(not declared["targets"], "legacy explicit dependency references require evidence")
        else:
            manifest.join_references(declared, {"targets": [{"name": target["name"]} for target in declared["targets"]]}, inventories)
    raw = (root / ".build" / (args.configuration + ".yaml")).read_bytes()
    observed_products = plan.project(root, dump, graph, raw, compiler[0])["products"]
    require(observed_products == lock["products"], "actual native declarations/module/object closure differs from lock")
    pins_unchanged(root, lock)
    require(cache.decode(cache.regular_bytes(root, ".build/dependencies-state.json")) == edited_state(root, lock), "native edited graph changed")
    require(pre_workspace.manifest_hash(root) == lock["manifestHash"] and layout.source_layout(root)["hash"] == lock["sourceLayoutHash"], "native observations changed source currency")
    parsed = plan.plan(raw)
    modules = {row["module-name"]: row for row in parsed["commands"].values() if row["tool"] == "swift-compiler"}
    phony = {row["outputs"][0]: Path(row["inputs"][0]) for row in parsed["commands"].values() if row["tool"] == "phony"}
    main_outputs = {phony[token] for token in parsed["targets"]["main"]}
    links = {Path(row["outputs"][0]): row for row in parsed["commands"].values() if row["tool"] in ("shell", "archive")}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    installed = []

    def copy(source, relative):
        source = plan.path_in_root(str(source), root, regular=True)
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return str(relative)

    for product in lock["products"]:
        module_files, object_files = [], []
        for name in product["modules"]:
            command = modules[name]
            path = Path(command["module-output-path"])
            module_files.append(copy(path, Path("lib/swift/linux") / path.name))
            doc = path.with_suffix(".swiftdoc")
            if doc.exists():
                module_files.append(copy(doc, Path("lib/swift/linux") / doc.name))
            for obj in command["objects"]:
                relative = Path(obj).relative_to(path.parent)
                object_files.append(copy(Path(obj), Path("lib/swiftpm-purifier") / product["name"] / "objects" / name / relative))
        artifact = None
        if product.get("linkage") != "automatic":
            expected = {product["name"]} if product["type"] == "executable" else {"lib" + product["name"] + suffix for suffix in (".a", ".so", ".dylib")}
            candidates = [p for p in links if p in main_outputs and p.name in expected]
            require(len(candidates) == 1, "unique actual native artifact")
            artifact = copy(candidates[0], Path("bin" if product["type"] == "executable" else "lib") / candidates[0].name)
        installed.append({**product, "moduleFiles": module_files, "objectFiles": object_files, "artifact": artifact})
    metadata = out / "share/swiftpm-purifier"
    metadata.mkdir(parents=True, exist_ok=True)
    (metadata / "products.json").write_text(json.dumps(installed, sort_keys=True) + "\n")
    # Actual observations go to the retained Nix build log, not nondeterministic
    # temporary paths/timestamps in the installed output.
    print("format8 actual native evidence: " + json.dumps(session.receipt, sort_keys=True))
    for path in sorted(session.logs.iterdir()):
        print("format8 observation " + path.name)
        print(path.read_text(errors="replace"))
    shutil.rmtree(session.evidence)


def package(args):
    project = Path(args.project)
    products = json.loads((project / "share/swiftpm-purifier/products.json").read_text())
    selected = [p for p in products if p["name"] == args.product]
    require(len(selected) == 1, "selected installed product")
    selected = selected[0]
    out = Path(args.out)
    for relative in selected["moduleFiles"] + selected["objectFiles"] + ([selected["artifact"]] if selected["artifact"] else []):
        path = Path(relative)
        require(not path.is_absolute() and ".." not in path.parts, "installed product path")
        destination = out / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(project / path)
    metadata = out / "share/swiftpm-purifier"
    metadata.mkdir(parents=True, exist_ok=True)
    (metadata / "products.json").write_text(json.dumps([selected], sort_keys=True) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("prepare", "install"):
        p = sub.add_parser(action)
        p.add_argument("--root", required=True)
        p.add_argument("--lock", required=True)
        if action == "prepare":
            p.add_argument("--tree", required=True)
            p.add_argument("--native-file", required=True)
        else:
            p.add_argument("--out", required=True)
            p.add_argument("--configuration", required=True, choices=("release", "debug"))
            p.add_argument("--package-command", required=True)
            p.add_argument("--compiler", required=True)
    p = sub.add_parser("package")
    p.add_argument("--project", required=True)
    p.add_argument("--product", required=True)
    p.add_argument("--out", required=True)
    args = parser.parse_args()
    {"prepare": prepare, "install": install, "package": package}[args.action](args)
