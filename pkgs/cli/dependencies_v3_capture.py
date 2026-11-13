# SPDX-License-Identifier: MIT
"""Read-only checks on an owned native-v3 capture, before publication."""

import os
from pathlib import Path

import dependencies_v1_cache as cache
import dependencies_v3 as protocol
import native_protocols as protocols
from native_path_family import codec
import dependencies_v3_observation as observation
import dependencies_v1_selectors as selectors


def native_graph(root, environment=None, *, native_protocol=protocols.DEPENDENCIES_V3):
    """Bind complete original native pins to unedited dependency records.

    This is an observation of data, never recovery or deletion authority.
    The caller must own the staging root and bound every native command.
    """
    native_protocol = protocols.checked(native_protocol)
    protocol = codec(native_protocol)
    root = Path(root)
    cache.require(
        root.is_absolute() and root == root.resolve(), "canonical capture root"
    )
    cache.directory(root)
    cache.validate_environment(environment)
    cache.directory(root / ".build")
    other = (
        "workspace-state.json"
        if native_protocol is protocols.DEPENDENCIES_V3
        else "dependencies-state.json"
    )
    for relative in (".build/" + other, "Package.pins"):
        cache.require(
            not os.path.lexists(root / relative), "mixed native capture state"
        )
    state_raw = cache.regular_bytes(root, ".build/" + native_protocol.state_basename)
    pin_raw = cache.regular_bytes(root, "Package.resolved")
    records = protocol.native_index(cache.decode(state_raw), root)
    pins = protocol.pins_index(cache.decode(pin_raw))
    remote = {
        record["packageRef"][
            "location" if native_protocol is protocols.WORKSPACE_V5_LOCATION else "path"
        ]: record
        for record in records.values()
        if record["packageRef"]["kind"]
        == (
            "remoteSourceControl"
            if native_protocol is protocols.WORKSPACE_V5_LOCATION
            else "remote"
        )
    }
    cache.require(set(remote) == set(pins), "native-v3 complete pin/source join")
    paths = set()
    for location, record in remote.items():
        pin = pins[location]
        cache.require(
            pin["package"] == record["packageRef"]["name"]
            and (
                protocol.checkout_state(pin["state"], selected=True)
                if native_protocol is protocols.WORKSPACE_V5_LOCATION
                else pin["state"]
            )
            == record["state"]["checkoutState"],
            "native-v3 pin/name/revision/version join",
        )
        subpath = record["subpath"]
        cache.require(subpath not in paths, "native-v3 checkout path alias")
        paths.add(subpath)
        for relative in (".build/checkouts", ".build/checkouts/" + subpath):
            cache.directory(root / relative)
        cache.source_tree(root / ".build/checkouts" / subpath)
    return records, pin_raw


def checkout_heads(
    root, records, session, api, *, native_protocol=protocols.DEPENDENCIES_V3
):
    """Verify Git uses the owned checkout and its actual commit matches state."""
    native_protocol = protocols.checked(native_protocol)
    root = Path(root)
    cache.validate_environment(session.env)
    result = {}
    for index, (identity, record) in enumerate(sorted(records.items())):
        if record["packageRef"]["kind"] != (
            "remoteSourceControl"
            if native_protocol is protocols.WORKSPACE_V5_LOCATION
            else "remote"
        ):
            continue
        checkout = root / ".build/checkouts" / record["subpath"]
        cache.require(checkout == checkout.resolve(), "native-v3 checkout link")
        git = [api.tool("git"), "--no-optional-locks", "-C", str(checkout)]
        label = "native-v3-checkout-" + str(index)
        gitdir = Path(
            session.run(label + "-git-dir", [*git, "rev-parse", "--absolute-git-dir"])
            .stdout.decode("utf-8")
            .strip()
        )
        cache.require(
            gitdir.is_absolute()
            and gitdir == gitdir.resolve()
            and gitdir.is_relative_to(root / ".build"),
            "native-v3 Git metadata escapes owned cache",
        )
        common_dir = Path(
            session.run(
                label + "-common-dir",
                [*git, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            )
            .stdout.decode("utf-8")
            .strip()
        )
        cache.require(
            common_dir.is_absolute()
            and common_dir == common_dir.resolve()
            and common_dir.is_relative_to(root / ".build"),
            "native-v3 shared Git metadata escapes owned cache",
        )
        head = (
            session.run(
                label + "-HEAD", [*git, "rev-parse", "--verify", "HEAD^{commit}"]
            )
            .stdout.decode("ascii")
            .strip()
        )
        cache.require(
            head == record["state"]["checkoutState"]["revision"],
            "native-v3 actual checkout HEAD",
        )
        result[identity] = head
    return result


def immutable_sources(
    root,
    temporary,
    records,
    session,
    api,
    mirrors=None,
    *,
    native_protocol=protocols.DEPENDENCIES_V3,
):
    """Join original native checkout content to a fixed-revision Nix fetch.

    ``mirrors`` is a measured source-location mapping supplied by the owned
    capture session. This helper publishes no caller files or ownership state.
    """
    import legacy_manifest
    import pre_workspace as common
    from dependencies_v1_projection import writable_source
    from native_sdk import store_root

    native_protocol = protocols.checked(native_protocol)
    root, temporary = Path(root), Path(temporary)
    cache.require(
        temporary.is_absolute()
        and temporary == temporary.resolve()
        and not temporary.is_relative_to(root),
        "native-v3 immutable source work must be external",
    )
    mirrors = {} if mirrors is None else mirrors
    cache.require(isinstance(mirrors, dict), "native-v3 mirror mapping")
    heads = checkout_heads(root, records, session, api, native_protocol=native_protocol)
    pins, sources = {}, {}
    for index, (identity, record) in enumerate(sorted(records.items())):
        if record["packageRef"]["kind"] != (
            "remoteSourceControl"
            if native_protocol is protocols.WORKSPACE_V5_LOCATION
            else "remote"
        ):
            continue
        location = record["packageRef"][
            "location" if native_protocol is protocols.WORKSPACE_V5_LOCATION else "path"
        ]
        url = mirrors.get(location, location)
        protocol.checkout_subpath(url)
        label = "native-v3-source-" + str(index)
        fetched = legacy_manifest.json_value(
            session.run(
                label + "-fetch",
                [
                    api.tool("nix-prefetch-git"),
                    "--quiet",
                    "--fetch-submodules",
                    "--url",
                    url,
                    "--rev",
                    heads[identity],
                ],
            ).stdout
        )
        cache.require(
            isinstance(fetched, dict)
            and fetched.get("rev") == heads[identity]
            and common.digest(fetched.get("hash"))
            and isinstance(fetched.get("path"), str),
            "native-v3 fixed Git fetch result",
        )
        source = Path(fetched["path"])
        cache.require(
            store_root(str(source)) == str(source)
            and source.is_dir()
            and not source.is_symlink(),
            "native-v3 fetched source must be an immutable store root",
        )
        # Store optimisation may hardlink immutable file bodies. Validate a
        # private copied inventory, then bind both trees by their NAR hashes.
        inspected = temporary / (label + "-fetched-inventory")
        cache.require(
            not os.path.lexists(inspected), "native-v3 inventory work already exists"
        )
        writable_source(source, inspected)
        cache.require(
            not os.path.lexists(source / ".git"), "native-v3 fetched VCS metadata"
        )
        checkout = root / ".build/checkouts" / record["subpath"]
        cache.source_tree(checkout)
        actual = temporary / (label + "-actual")
        cache.require(
            not os.path.lexists(actual), "native-v3 source work already exists"
        )
        common.plain_copy(checkout, actual)
        cache.require(
            common.hash_path(session, api, label + "-inventory-hash", inspected)
            == fetched["hash"]
            and common.hash_path(session, api, label + "-checkout-hash", actual)
            == fetched["hash"]
            and common.hash_path(session, api, label + "-fetch-hash", source)
            == fetched["hash"],
            "native-v3 checkout/fetched revision NAR join",
        )
        pin = {
            "name": record["packageRef"]["name"],
            "sourceLocation": location,
            "revision": heads[identity],
            "version": record["state"]["checkoutState"].get("version"),
            "hash": fetched["hash"],
        }
        if native_protocol is protocols.WORKSPACE_V5_LOCATION:
            pin["branch"] = record["state"]["checkoutState"].get("branch")
        if url != location:
            pin["url"] = url
        pins[identity], sources[identity] = pin, source
    return pins, sources


def prepare(
    root,
    temporary,
    api,
    update=None,
    *,
    native=selectors.DEFAULT,
    portable=selectors.DEFAULT,
    native_protocol=protocols.DEPENDENCIES_V3,
):
    """Observe a fresh owned native graph and return validated portable bytes.

    No caller output is written here. Publication must use the typed Capture
    transaction under the shared root lock, followed by owned installation.
    Omitted selectors mean their default files; explicit null is unsupported.
    """
    import copy
    import legacy_manifest
    import dependencies_v1_layout as layout
    from dependencies_v1_capture import NativeSession

    native_protocol = protocols.checked(native_protocol)
    protocol = codec(native_protocol)
    cache.require(not api.sdk_mode(), "portable9 SDK-bound CLI capture is unavailable")
    root, temporary = layout.canonical(root), layout.canonical(temporary)
    cache.require(
        not temporary.is_relative_to(root), "portable9 capture work must be external"
    )
    if native_protocol in (
        protocols.WORKSPACE_V4_PATH,
        protocols.WORKSPACE_V5_LOCATION,
    ):
        from native_path_family import ownership

        ownership(native_protocol).require_same_family(
            root, getattr(api, "dependencies_v1_hash", None)
        )
    pin, output, logical = selectors.pair(root, native, portable)
    cache.require(pin.path is not None, "portable9 null native selector is unsupported")
    if pin.raw is not None:
        protocol.pins_index(cache.decode(pin.raw))
    initial = observation.snapshot(root)
    package = temporary / root.name
    api.copy_package(root, package)
    observation.project_pins(package, pin.raw)
    source_before = observation.capture_source(package)
    (temporary / "tmp").mkdir()
    environment = dict(
        os.environ,
        TMPDIR=str(temporary / "tmp"),
        CLANG_MODULE_CACHE_PATH=str(temporary / "clang-module-cache"),
    )
    cache.require(
        "SWIFTPM_BUILD_DIR" not in environment, "external native build directory"
    )
    session = NativeSession(package, environment, temporary.parent)
    command = api.native_command("swift-package")
    isolation = api.swiftpm_isolation(temporary)
    session.receipt.update(
        sourceBefore=source_before["fingerprint"],
        sourceAfter=None,
        callerBefore=initial["fingerprint"],
        callerAfter=None,
    )
    session.save()
    # Only the native parser selects the toolchain protocol. No version range
    # or portable-version rewrite substitutes for observing the produced state.
    session.run(
        "native-resolution",
        [
            *command,
            *isolation,
            *(["resolve"] if update is None else ["update", *update]),
        ],
    )
    records, new_native = native_graph(
        package, environment, native_protocol=native_protocol
    )
    immutable_work = temporary / "immutable-sources"
    immutable_work.mkdir()
    pins, sources = immutable_sources(
        package,
        immutable_work,
        records,
        session,
        api,
        api.mirrors(package, temporary / "config", legacy=True),
        native_protocol=native_protocol,
    )
    description = legacy_manifest.json_value(
        session.run("root-description", [*command, "dump-package"]).stdout
    )
    source_after = observation.capture_source(package, source_before)
    observation.verify(root, initial)
    session.receipt.update(
        sourceAfter=source_after["fingerprint"], callerAfter=initial["fingerprint"]
    )
    session.save()
    products, name = api.product_description(description)
    locals_ = []
    for identity, record in sorted(records.items()):
        if record["packageRef"]["kind"] == (
            "fileSystem"
            if native_protocol is protocols.WORKSPACE_V5_LOCATION
            else "local"
        ):
            relative, _ = protocol.local_directory(
                package,
                record["packageRef"][
                    "location"
                    if native_protocol is protocols.WORKSPACE_V5_LOCATION
                    else "path"
                ],
            )
            row = copy.deepcopy(record)
            row["packageRef"][
                "location"
                if native_protocol is protocols.WORKSPACE_V5_LOCATION
                else "path"
            ] = "@root@/" + relative
            if native_protocol is protocols.WORKSPACE_V5_LOCATION:
                row["state"]["path"] = "@root@/" + relative
            locals_.append(row)
    lock = {
        "version": protocol.FORMAT,
        "package": name,
        "rootDirectory": root.name,
        "manifestHash": protocol.manifest_hash(
            package,
            [
                row
                for row in records.values()
                if row["packageRef"]["kind"]
                == (
                    "fileSystem"
                    if native_protocol is protocols.WORKSPACE_V5_LOCATION
                    else "local"
                )
            ],
        ),
        "products": products,
        "workspaceStateVersion": protocol.NATIVE_VERSION,
        "pins": pins,
        "binaryTargets": [],
        "localDependencies": locals_,
    }
    lock["contentHash"] = protocol.content_hash(lock)
    protocol.validate_lock(lock, cache.decode(new_native), root)
    final_records, final_native = native_graph(
        package, environment, native_protocol=native_protocol
    )
    cache.require(
        final_records == records and final_native == new_native,
        "portable9 native graph changed during capture",
    )
    observation.verify(root, initial)
    pin.verify()
    output.verify()
    logical.verify()
    if native_protocol in (
        protocols.WORKSPACE_V4_PATH,
        protocols.WORKSPACE_V5_LOCATION,
    ):
        ownership(native_protocol).require_same_family(
            root, getattr(api, "dependencies_v1_hash", None)
        )
    session.receipt.update(
        complete=True,
        protocol=native_protocol.name,
        portableContentHash=lock["contentHash"],
        sources={key: str(path) for key, path in sources.items()},
    )
    session.save()
    return lock, new_native, session
