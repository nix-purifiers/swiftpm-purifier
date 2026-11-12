# SPDX-License-Identifier: MIT
"""Isolated format8 native capture; output publication is transactional elsewhere."""

import hashlib
import os
from pathlib import Path
import tempfile
import time
import shutil

import dependencies_v1 as protocol
import dependencies_v1_cache as cache
import dependencies_v1_layout as layout
import dependencies_v1_manifest as manifest
import dependencies_v1_plan as plan
import dependencies_v1_projection as projection
import dependencies_v1_selectors as selectors
import dependencies_v1_snapshot as snapshot
import pre_workspace as common
import legacy_manifest
from native_sdk import store_root


class NativeSession(common.NativeSession):
    """Bound observations outside caller source even with an in-project TMPDIR."""

    def __init__(self, cwd, environment, evidence_parent):
        self.cwd = Path(cwd)
        self.env = dict(environment)
        self.evidence = Path(
            tempfile.mkdtemp(
                prefix="swiftpm-purifier-dependencies-v1-", dir=evidence_parent
            )
        )
        self.logs = self.evidence / "logs"
        self.logs.mkdir()
        self.receipt = {"complete": False, "commands": []}
        self.deadline = time.monotonic() + 720
        self.save()


def native_pin_records(raw):
    """Read only measured semantic-version pin records, including before fetch."""
    if raw is None:
        return None
    value = cache.decode(raw)
    cache.require(
        isinstance(value, dict)
        and set(value) == {"version", "object"}
        and type(value["version"]) is int
        and value["version"] == 1,
        "native pin protocol",
    )
    cache.require(
        isinstance(value["object"], dict)
        and set(value["object"]) == {"pins"}
        and isinstance(value["object"]["pins"], list)
        and len(value["object"]["pins"]) <= 1024,
        "native pin fields",
    )
    actual = {}
    locations = set()
    for pin in value["object"]["pins"]:
        cache.require(
            isinstance(pin, dict) and set(pin) == {"package", "repositoryURL", "state"},
            "native pin record",
        )
        cache.require(
            protocol.legacy.safe_name(pin["package"])
            and len(pin["package"].encode("utf-8")) <= 255
            and pin["package"] not in actual
            and protocol.legacy.git_location(pin["repositoryURL"])
            and pin["repositoryURL"] not in locations,
            "native pin identity",
        )
        checkout = pin["state"]
        cache.require(
            isinstance(checkout, dict)
            and set(checkout) == {"branch", "revision", "version"}
            and checkout["branch"] is None
            and isinstance(checkout["revision"], str)
            and protocol.legacy.REVISION.fullmatch(checkout["revision"]) is not None
            and protocol.legacy.version_valid(checkout["version"]),
            "native semantic pin checkout shape",
        )
        actual[pin["package"]] = {"URL": pin["repositoryURL"], "state": pin["state"]}
        locations.add(pin["repositoryURL"])
    return actual


def native_pins(raw, state, allow_stale=False):
    """Bind native exact bytes semantically to actual checkout identities."""
    actual = native_pin_records(raw)
    if actual is None:
        return
    expected = {
        dep["name"]: {
            "URL": dep["repositoryURL"],
            "state": dep["state"]["checkoutState"],
        }
        for dep in state["object"]["dependencies"]
    }
    if allow_stale:
        cache.require(
            set(actual) == set(expected)
            and all(actual[name]["URL"] == expected[name]["URL"] for name in actual),
            "native selected pin/cache package identity join",
        )
    else:
        cache.require(actual == expected, "native pin/checkouts identity join")


def capture_basis(root, environment, hash_tree=None):
    """Distinguish an absent cache from an existing, fully validated cache."""
    cache.validate_environment(environment)
    if os.path.lexists(root / "Packages"):
        from dependencies_v1_ownership import existing, native_state, MARKER

        cache.directory(root / "Packages")
        cache.require(
            os.path.lexists(root / "Packages" / MARKER), "unrecognized Packages state"
        )
        cache.require(
            callable(hash_tree), "owned capture requires verified source hashes"
        )
        return {"owned": existing(root, hash_tree), "native": native_state(root)}
    if not os.path.lexists(root / ".build"):
        cache.require(
            not os.path.lexists(root / "Packages"), "unrecognized Packages state"
        )
        cache.require(
            not os.path.lexists(root / "Package.pins"), "mixed native pin format"
        )
        return None
    cache.validate(root, environment)
    return [
        cache.regular_bytes(root, ".build/dependencies-state.json"),
        cache.regular_bytes(root, ".build/repositories/checkouts-state.json"),
    ]


def verify_basis(root, environment, expected, hash_tree=None):
    cache.require(
        capture_basis(root, environment, hash_tree) == expected,
        "caller native metadata changed during capture",
    )


def candidate(api, temporary, environment):
    """Observe the native cache protocol using only a fresh owned local fixture.

    Local Git here is probe input, never an admitted portable source location.
    No project manifest, pin bytes or cache is read by this discriminator.
    """
    cache.require(not api.sdk_mode(), "format8 has no SDK-bound CLI capture")
    temporary = layout.canonical(temporary)
    cache.directory(temporary)
    cache.validate_environment(environment)
    leaf = temporary / "leaf"
    (leaf / "Sources/Leaf").mkdir(parents=True)
    (leaf / "Package.swift").write_text(
        '// swift-tools-version:4.0\nimport PackageDescription\nlet package = Package(name: "ProtocolLeaf", products: [.library(name: "Leaf", targets: ["Leaf"])], targets: [.target(name: "Leaf")])\n'
    )
    (leaf / "Sources/Leaf/Leaf.swift").write_text(
        "public func leaf() -> Int { return 42 }\n"
    )
    root = temporary / "root"
    root.mkdir()
    location = leaf.as_uri()
    import json

    (root / "Package.swift").write_text(
        '// swift-tools-version:4.0\nimport PackageDescription\nlet package = Package(name: "ProtocolRoot", dependencies: [.package(url: '
        + json.dumps(location)
        + ', from: "1.0.0")])\n'
    )
    (root / "Sources").mkdir()
    (root / "Sources/main.swift").write_text("print(42)\n")
    (temporary / "tmp").mkdir()
    env = dict(
        environment,
        TMPDIR=str(temporary / "tmp"),
        CLANG_MODULE_CACHE_PATH=str(temporary / "clang-module-cache"),
    )
    session = NativeSession(root, env, temporary.parent)
    git = [
        api.tool("git"),
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "tag.gpgsign=false",
    ]
    session.run("protocol-git-init", [*git, "init", str(leaf)])
    session.run("protocol-git-add", [*git, "-C", str(leaf), "add", "."])
    session.run(
        "protocol-git-commit",
        [
            *git,
            "-c",
            "user.name=Purifier protocol fixture",
            "-c",
            "user.email=protocol@example.invalid",
            "-C",
            str(leaf),
            "commit",
            "-m",
            "Native cache protocol observation",
        ],
    )
    session.run("protocol-git-tag", [*git, "-C", str(leaf), "tag", "1.0.0"])
    revision = (
        session.run("protocol-git-head", [*git, "-C", str(leaf), "rev-parse", "HEAD"])
        .stdout.decode()
        .strip()
    )
    result = session.run(
        "protocol-native-fetch",
        [*api.native_command("swift-package"), "fetch"],
        success=False,
        timeout=60,
    )
    accepted = False
    state_path = root / ".build/dependencies-state.json"
    if result.returncode == 0 and os.path.lexists(state_path):
        state = cache.decode(
            cache.regular_bytes(root, ".build/dependencies-state.json")
        )
        if (
            isinstance(state, dict)
            and type(state.get("version")) is int
            and state["version"] == 1
        ):
            body = state.get("object")
            dependencies = (
                body.get("dependencies", []) if isinstance(body, dict) else []
            )
            if (
                isinstance(dependencies, list)
                and len(dependencies) == 1
                and isinstance(dependencies[0], dict)
            ):
                dep = dependencies[0]
                accepted = (
                    dep.get("name") == "ProtocolLeaf"
                    and dep.get("repositoryURL") == location
                    and dep.get("state")
                    == {
                        "name": "checkout",
                        "checkoutState": {
                            "branch": None,
                            "revision": revision,
                            "version": "1.0.0",
                        },
                    }
                    and not os.path.lexists(root / ".build/workspace-state.json")
                )
    session.receipt.update(
        complete=True,
        candidate=accepted,
        scope="Owned local-Git native cache protocol discriminator only; not portable local-source support",
    )
    session.save()
    return accepted


def prepare(
    root,
    temporary,
    api,
    update=None,
    native=selectors.DEFAULT,
    portable=selectors.DEFAULT,
):
    """Return a verified lock and selected pin bytes without writing caller outputs."""
    cache.require(not api.sdk_mode(), "format8 SDK-bound CLI capture is unavailable")
    root = layout.canonical(root)
    temporary = layout.canonical(temporary)
    cache.require(not temporary.is_relative_to(root), "capture work must be external")
    pin, output, logical = selectors.pair(root, native, portable)
    initial_layout = layout.source_layout(root, output.path)
    initial_manifest = common.manifest_hash(root)
    env = dict(os.environ)
    cache.require("SWIFTPM_BUILD_DIR" not in env, "external native build directory")
    (temporary / "tmp").mkdir()
    env.update(
        TMPDIR=str(temporary / "tmp"),
        CLANG_MODULE_CACHE_PATH=str(temporary / "clang-module-cache"),
    )
    session = None
    hash_counter = 0

    def owned_hash(path):
        nonlocal hash_counter
        hash_counter += 1
        return common.hash_path(
            session, api, "caller-owned-source-" + str(hash_counter), path
        )

    if os.path.lexists(root / "Packages"):
        session = NativeSession(root, env, temporary.parent)
    initial_basis = capture_basis(
        root, env, owned_hash if session is not None else None
    )
    native_pin_records(pin.raw)
    package = temporary / root.name
    if initial_basis is None or isinstance(initial_basis, dict):
        shutil.copytree(
            root,
            package,
            symlinks=True,
            ignore=lambda parent, names: (
                [name for name in names if name in layout.ROOT_DIRECTORIES]
                if Path(parent) == root
                else []
            ),
        )
    else:
        native_pins(pin.raw, cache.decode(initial_basis[0]), allow_stale=True)
        snapshot.snapshot(root, package, env)
    # Native pin projection is confined to the owned copy; the logical caller
    # file and external selected file remain untouched throughout observation.
    projected = package / "Package.resolved"
    if pin.raw is None:
        projected.unlink(missing_ok=True)
    else:
        projected.write_bytes(pin.raw)
    if session is None:
        session = NativeSession(package, env, temporary.parent)
    else:
        session.cwd = package
        session.env = dict(env)
    package_command = api.native_command("swift-package")
    if isinstance(initial_basis, list):
        cache.validate(package, session.env)
    session.run(
        "native-resolution",
        [*package_command, *(["fetch"] if update is None else ["update", *update])],
    )
    cache.validate(package, session.env)
    state = cache.decode(cache.regular_bytes(package, ".build/dependencies-state.json"))
    new_native = (
        cache.regular_bytes(package, "Package.resolved")
        if (package / "Package.resolved").exists()
        else None
    )
    native_pins(new_native, state)
    validated = session.run(
        "root-validation", [*package_command, "--verbose", "dump-package"]
    )
    compiler, description = manifest.invocation(validated.stdout, package)
    observed = session.run("root-protocol", compiler, protocol_flag="-fileno")
    cache.require(not observed.stdout, "native manifest unexpected stdout")
    graph = legacy_manifest.json_value(
        session.run(
            "root-description", [*package_command, "describe", "--type", "json"]
        ).stdout
    )
    manifest.inventory(description, graph, observed.protocol, package)
    session.run("native-build", api.native_command("swift-build"))
    products = plan.project(
        package,
        description,
        graph,
        cache.regular_bytes(package, ".build/debug.yaml", 4 * 1024 * 1024),
        compiler[0],
    )["products"]
    pins = {}
    sources = {}
    inventories = {}
    for index, dep in enumerate(state["object"]["dependencies"]):
        label = "dependency-" + str(index)
        checkout = package / ".build/checkouts" / dep["subpath"]
        cache.validate(package, session.env)
        head = (
            session.run(
                label + "-HEAD",
                [
                    api.tool("git"),
                    "--no-optional-locks",
                    "-C",
                    str(checkout),
                    "rev-parse",
                    "--verify",
                    "HEAD^{commit}",
                ],
            )
            .stdout.decode()
            .strip()
        )
        cache.require(
            head == dep["state"]["checkoutState"]["revision"],
            "native checkout actual HEAD",
        )
        session.run(
            label + "-fsck",
            [
                api.tool("git"),
                "--no-optional-locks",
                "-C",
                str(checkout),
                "fsck",
                "--full",
            ],
        )
        fetched = legacy_manifest.json_value(
            session.run(
                label + "-prefetch",
                [
                    api.tool("nix-prefetch-git"),
                    "--quiet",
                    "--fetch-submodules",
                    "--url",
                    dep["repositoryURL"],
                    "--rev",
                    head,
                ],
            ).stdout
        )
        cache.require(
            isinstance(fetched, dict)
            and fetched.get("rev") == head
            and common.digest(fetched.get("hash"))
            and isinstance(fetched.get("path"), str),
            "immutable Git fetch result",
        )
        source = Path(fetched["path"])
        cache.require(
            store_root(str(source)) == str(source)
            and source.is_dir()
            and not source.is_symlink(),
            "immutable fetched source",
        )
        actual = temporary / (label + "-actual")
        common.plain_copy(checkout, actual)
        cache.require(
            common.hash_path(session, api, label + "-actual-source", actual)
            == fetched["hash"]
            and common.hash_path(session, api, label + "-fetched-source", source)
            == fetched["hash"],
            "native source/fetched Git revision NAR join",
        )
        pins[dep["name"]] = {
            "name": dep["name"],
            "sourceLocation": dep["repositoryURL"],
            "revision": head,
            "version": dep["state"]["checkoutState"]["version"],
            "hash": fetched["hash"],
        }
        sources[dep["name"]] = source
    for index, dep in enumerate(state["object"]["dependencies"]):
        label = "dependency-" + str(index)
        probe = temporary / (label + "-manifest")
        projection.writable_source(sources[dep["name"]], probe)
        allowed = projection.prepare_probe(probe, pins, sources, dep["name"])
        validation = session.run(
            label + "-validation",
            [*package_command, "--verbose", "dump-package"],
            cwd=probe,
        )
        invocation, declared = manifest.invocation(validation.stdout, probe, allowed)
        cache.require(
            invocation[0] == compiler[0], "native manifest selected compiler join"
        )
        fd = session.run(
            label + "-protocol", invocation, cwd=probe, protocol_flag="-fileno"
        )
        cache.require(not fd.stdout, "dependency manifest unexpected stdout")
        dependency_graph = legacy_manifest.json_value(
            session.run(
                label + "-description",
                [*package_command, "describe", "--type", "json"],
                cwd=probe,
            ).stdout
        )
        projection.verify_probe(probe, pins, dep["name"], declared)
        inventory = manifest.inventory(declared, dependency_graph, fd.protocol, probe)
        cache.require(
            inventory["package"] == dep["name"],
            "native state/manifest package name join",
        )
        inventories[dep["repositoryURL"]] = inventory
    joins = manifest.join_references(description, graph, inventories)
    # The same rule applies to each dependency's own direct references.
    # Legacy API3 implicit targets are bound by the successful native graph;
    # nonempty explicit legacy target-reference forms remain unsupported.
    for url, inventory in inventories.items():
        declared = inventory["description"]
        if "products" not in declared:
            cache.require(
                not declared["targets"],
                "legacy explicit target references require native evidence",
            )
        else:
            dependency_graph = {
                "targets": [{"name": target["name"]} for target in declared["targets"]]
            }
            manifest.join_references(declared, dependency_graph, inventories)
    cache.require(
        cache.decode(cache.regular_bytes(package, ".build/dependencies-state.json"))
        == state,
        "native checkout graph changed during observations",
    )
    final_native = (
        cache.regular_bytes(package, "Package.resolved")
        if (package / "Package.resolved").exists()
        else None
    )
    cache.require(final_native == new_native, "native pins changed during observations")
    cache.require(
        common.manifest_hash(package) == initial_manifest
        and layout.source_layout(package)["hash"] == initial_layout["hash"],
        "native observations changed captured source currency",
    )
    selected_native = None if pin.path is None else new_native
    lock = {
        "version": 8,
        "nativeProtocol": "dependencies-state-v1",
        "nativeState": {"file": ".build/dependencies-state.json", "version": 1},
        "package": description["name"],
        "rootDirectory": root.name,
        "manifestHash": initial_manifest,
        "sourceLayoutHash": initial_layout["hash"],
        "nativeToolLock": {
            "file": "Package.resolved",
            "sha256": None
            if selected_native is None
            else hashlib.sha256(selected_native).hexdigest(),
        },
        "pins": pins,
        "products": products,
        "localDependencies": [],
        "binaryTargets": [],
    }
    lock["contentHash"] = protocol.content_hash(lock)
    protocol.validate(lock)
    pin.verify()
    output.verify()
    logical.verify()
    cache.require(
        layout.source_layout(root, output.path) == initial_layout
        and common.manifest_hash(root) == initial_manifest,
        "caller source currency changed during capture",
    )
    verify_basis(
        root,
        env,
        initial_basis,
        owned_hash if isinstance(initial_basis, dict) else None,
    )
    session.receipt.update(
        complete=True,
        protocol="dependencies-state-v1",
        declarationJoins=joins,
        portableContentHash=lock["contentHash"],
    )
    session.save()
    return lock, selected_native, session


def capture_and_publish(
    root,
    temporary,
    api,
    registry,
    update=None,
    native=selectors.DEFAULT,
    portable=selectors.DEFAULT,
):
    """Recover before native observation, then publish the prepared file pair."""
    from dependencies_v1_transaction import Transaction, canonical
    from dependencies_v1_lifecycle import recover_locked

    cache.require(not api.sdk_mode(), "format8 SDK-bound CLI capture is unavailable")
    with Transaction(root, registry, native, portable).locked() as transaction:
        recovery = recover_locked(transaction)
        expected = transaction.pair()
        lock, new_native, session = prepare(
            root, temporary, api, update, native, portable
        )
        raw = canonical(lock) + b"\n"
        protocol.decode(raw)
        cache.require(
            common.manifest_hash(root) == lock["manifestHash"]
            and layout.source_layout(root, transaction.paths[1])["hash"]
            == lock["sourceLayoutHash"],
            "source currency changed before capture publication",
        )
        transaction.publish([new_native, raw], expected)
        session.receipt.update(
            publication="committed",
            priorRecovery=recovery,
            selectedPinSHA256=lock["nativeToolLock"]["sha256"],
        )
        session.save()
        return lock, session
