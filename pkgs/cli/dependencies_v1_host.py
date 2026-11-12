# SPDX-License-Identifier: MIT
"""Host format8 currency checks and actual native capability observations."""

import hashlib
import os
from pathlib import Path
import shutil

import dependencies_v1 as protocol
import dependencies_v1_cache as cache
import dependencies_v1_capture as capture
import dependencies_v1_layout as layout
import dependencies_v1_manifest as manifest
import dependencies_v1_ownership as ownership
import dependencies_v1_plan as plan
import dependencies_v1_projection as projection
import dependencies_v1_selectors as selectors
import legacy_manifest
import pre_workspace as common
from dependencies_v1_transaction import canonical


def validate_source(root, raw, native=selectors.DEFAULT, portable=selectors.DEFAULT):
    """Bind portable bytes to the current source and independently selected pins."""
    root = layout.canonical(root)
    lock = protocol.decode(raw)
    pin, output, logical = selectors.pair(root, native, portable)
    cache.require(output.raw == raw, "selected portable bytes changed")
    cache.require(
        common.manifest_hash(root) == lock["manifestHash"], "manifest currency"
    )
    cache.require(
        layout.source_layout(root, output.path)["hash"] == lock["sourceLayoutHash"],
        "source layout currency",
    )
    cache.require(
        pin.sha256 == lock["nativeToolLock"]["sha256"],
        "selected native pin byte binding",
    )
    state = {
        "object": {
            "dependencies": [
                {
                    "name": name,
                    "repositoryURL": value["sourceLocation"],
                    "state": {
                        "checkoutState": {
                            "branch": None,
                            "revision": value["revision"],
                            "version": value["version"],
                        }
                    },
                }
                for name, value in sorted(lock["pins"].items())
            ]
        }
    }
    capture.native_pins(pin.raw, state)
    pin.verify()
    output.verify()
    logical.verify()
    return lock


def runtime(
    root,
    raw,
    sources,
    temporary,
    api,
    hash_tree,
    native=selectors.DEFAULT,
    portable=selectors.DEFAULT,
    evidence_parent=None,
):
    """Exercise the caller-selected runtime in an external, source-only arena.

    No release/version label selects this adapter. The actual manifest FD,
    native edited state, compiler plan and output geometry must agree. Fetched
    sources are independently checked by prepare_tree before native execution.
    """
    cache.require(not api.sdk_mode(), "format8 has no SDK-bound CLI runtime contract")
    root = layout.canonical(root)
    temporary = layout.canonical(temporary)
    cache.directory(temporary)
    cache.require(not temporary.is_relative_to(root), "runtime arena must be external")
    cache.validate_environment(os.environ)
    cache.require(
        "SWIFTPM_BUILD_DIR" not in os.environ, "external native build directory"
    )
    lock = validate_source(root, raw, native, portable)
    probe = temporary / root.name
    shutil.copytree(
        root,
        probe,
        symlinks=True,
        ignore=lambda parent, names: (
            [name for name in names if name in layout.ROOT_DIRECTORIES]
            if Path(parent) == root
            else []
        ),
    )
    staged = temporary / "owned-sources"
    marker = ownership.prepare_tree(probe, lock, sources, staged, None, hash_tree)
    staged.rename(probe / "Packages")
    (probe / ".build").mkdir()
    (probe / ".build/dependencies-state.json").write_bytes(
        canonical(marker["projection"])
    )
    # The selected pin file may be external, explicitly absent, or defaulted.
    # Only the corresponding bytes are projected into this owned native root.
    pin, _, _ = selectors.pair(root, native, portable)
    projected = probe / "Package.resolved"
    if pin.raw is None:
        projected.unlink(missing_ok=True)
    else:
        projected.write_bytes(pin.raw)
    (temporary / "tmp").mkdir()
    env = dict(
        os.environ,
        TMPDIR=str(temporary / "tmp"),
        CLANG_MODULE_CACHE_PATH=str(temporary / "clang-module-cache"),
    )
    session = capture.NativeSession(
        probe, env, temporary.parent if evidence_parent is None else evidence_parent
    )
    package = api.native_command("swift-package")
    result = session.run("runtime-manifest", [*package, "--verbose", "dump-package"])
    compiler, description = manifest.invocation(
        result.stdout, probe, {probe / "Packages" / name for name in lock["pins"]}
    )
    observed = session.run(
        "runtime-manifest-protocol", compiler, protocol_flag="-fileno"
    )
    cache.require(not observed.stdout, "runtime manifest unexpected stdout")
    graph = legacy_manifest.json_value(
        session.run(
            "runtime-description", [*package, "describe", "--type", "json"]
        ).stdout
    )
    manifest.inventory(description, graph, observed.protocol, probe)
    cache.require(description["name"] == lock["package"], "runtime package identity")
    inventories = {}
    for index, (name, pin_record) in enumerate(lock["pins"].items()):
        dependency = temporary / ("dependency-" + str(index))
        projection.writable_source(sources[name], dependency)
        dependency_manifest = common.manifest_hash(dependency)
        dependency_layout = layout.source_layout(dependency)["hash"]
        allowed = projection.prepare_probe(dependency, lock["pins"], sources, name)
        label = "runtime-dependency-" + str(index)
        validation = session.run(
            label + "-validation",
            [*package, "--verbose", "dump-package"],
            cwd=dependency,
        )
        invocation, declared = manifest.invocation(
            validation.stdout, dependency, allowed
        )
        cache.require(
            invocation[0] == compiler[0], "runtime dependency compiler identity"
        )
        fd = session.run(
            label + "-protocol", invocation, cwd=dependency, protocol_flag="-fileno"
        )
        cache.require(not fd.stdout, "runtime dependency manifest unexpected stdout")
        dependency_graph = legacy_manifest.json_value(
            session.run(
                label + "-description",
                [*package, "describe", "--type", "json"],
                cwd=dependency,
            ).stdout
        )
        projection.verify_probe(dependency, lock["pins"], name, declared)
        inventory = manifest.inventory(
            declared, dependency_graph, fd.protocol, dependency
        )
        cache.require(
            inventory["package"] == name, "runtime dependency package identity"
        )
        cache.require(
            common.manifest_hash(dependency) == dependency_manifest
            and layout.source_layout(dependency)["hash"] == dependency_layout,
            "runtime dependency observations changed source currency",
        )
        inventories[pin_record["sourceLocation"]] = inventory
    joins = manifest.join_references(description, graph, inventories)
    for inventory in inventories.values():
        declared = inventory["description"]
        if "products" not in declared:
            cache.require(
                not declared["targets"],
                "legacy explicit target references require native evidence",
            )
        else:
            manifest.join_references(
                declared,
                {
                    "targets": [
                        {"name": target["name"]} for target in declared["targets"]
                    ]
                },
                inventories,
            )
    ownership.existing(probe, hash_tree)
    session.run("runtime-build", api.native_command("swift-build"))
    actual_products = plan.project(
        probe,
        description,
        graph,
        cache.regular_bytes(probe, ".build/debug.yaml", 4 * 1024 * 1024),
        compiler[0],
    )["products"]
    cache.require(
        actual_products == lock["products"],
        "runtime product declaration/module closure",
    )
    geometry = session.run(
        "runtime-output-geometry",
        [*api.native_command("swift-build"), "--show-bin-path"],
    )
    lines = geometry.stdout.decode("utf-8", errors="strict").splitlines()
    cache.require(len(lines) == 1, "runtime output geometry lines")
    binary = Path(lines[0])
    cache.require(
        binary.is_absolute() and binary.resolve().is_relative_to(probe / ".build"),
        "runtime output geometry confinement",
    )
    cache.directory(binary.resolve())
    ownership.existing(probe, hash_tree)
    cache.require(
        common.manifest_hash(probe) == lock["manifestHash"]
        and layout.source_layout(probe)["hash"] == lock["sourceLayoutHash"],
        "runtime observations changed root source currency",
    )
    cache.require(
        projected.read_bytes() == pin.raw
        if pin.raw is not None
        else not projected.exists(),
        "runtime changed selected native pins",
    )
    validate_source(root, raw, native, portable)
    session.receipt.update(
        complete=True,
        protocol="dependencies-state-v1",
        scope="Actual selected runtime manifest/state/build-plan/output capability in owned arena; no immutable SDK or ABI attestation",
        portableContentHash=lock["contentHash"],
        compiler=compiler[0],
        declarationJoins=joins,
        outputDirectory=str(binary),
        selectedNativeSHA256=None
        if pin.raw is None
        else hashlib.sha256(pin.raw).hexdigest(),
    )
    session.save()
    return session
