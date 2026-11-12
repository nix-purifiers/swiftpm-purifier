# SPDX-License-Identifier: MIT
"""Repo-local portable format 6 for actual legacy workspace-state format 1.

Git checkout revisions and versions come from native workspace records.
Package.pins is partial native metadata, never a fabricated complete graph.
"""

import hashlib
import re
from pathlib import Path

import legacy_manifest

FORMAT = 6
SEMVER = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?"
)
REVISION = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")
HASH = re.compile(r"sha256-[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=")


class StateError(ValueError):
    pass


def safe_name(value):
    return (
        isinstance(value, str)
        and bool(value)
        and value not in (".", "..")
        and not any(c in value for c in ("/", "\\"))
        and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )


def git_location(value):
    if (
        not isinstance(value, str)
        or not value
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        return False
    return (
        re.fullmatch(r"(https?|ssh|git)://[^/]+(/.*)?", value) is not None
        or re.fullmatch(r"[^/ @:]+@[^/ :]+:.+", value) is not None
    )


def version_valid(value):
    if not isinstance(value, str) or SEMVER.fullmatch(value) is None:
        return False
    prerelease = value.split("+", 1)[0].split("-", 1)
    return len(prerelease) == 1 or all(
        not item.isdigit() or item == "0" or not item.startswith("0")
        for item in prerelease[1].split(".")
    )


def native_pins(path):
    path = Path(path)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise StateError("legacy native pin metadata must be a regular file")
    if not path.exists():
        return None, None
    try:
        with path.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        data = legacy_manifest.json_value(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, legacy_manifest.ManifestError) as error:
        raise StateError("invalid legacy native pin metadata: " + str(error)) from error
    if (
        not isinstance(data, dict)
        or set(data) != {"version", "autoPin", "pins"}
        or type(data["version"]) is not int
        or data["version"] != 1
        or type(data["autoPin"]) is not bool
        or not isinstance(data["pins"], list)
    ):
        raise StateError("unsupported native Package.pins shape")
    names, locations = set(), set()
    for pin in data["pins"]:
        if (
            not isinstance(pin, dict)
            or set(pin) != {"package", "reason", "repositoryURL", "version"}
            or not safe_name(pin["package"])
            or pin["package"] in names
            or not git_location(pin["repositoryURL"])
            or pin["repositoryURL"] in locations
            or not version_valid(pin["version"])
            or (pin["reason"] is not None and not isinstance(pin["reason"], str))
        ):
            raise StateError("unsupported or ambiguous native Package.pins record")
        names.add(pin["package"])
        locations.add(pin["repositoryURL"])
    return data, hashlib.sha256(raw).hexdigest()


def validate(lock, native_file):
    """Reject before runtime probes, Nix realization, or native tree writes."""
    if (
        not isinstance(lock, dict)
        or set(lock)
        != {
            "version",
            "package",
            "rootDirectory",
            "manifestHash",
            "products",
            "workspaceStateVersion",
            "pins",
            "binaryTargets",
            "localDependencies",
            "contentHash",
            "nativeToolLock",
        }
        or lock.get("version") != FORMAT
        or type(lock.get("version")) is not int
        or lock.get("workspaceStateVersion") != 1
        or type(lock.get("workspaceStateVersion")) is not int
        or "nativeSDK" in lock
        or lock.get("localDependencies") != []
        or lock.get("binaryTargets") != []
        or not isinstance(lock.get("pins"), dict)
        or not isinstance(lock.get("manifestHash"), str)
        or re.fullmatch(r"[0-9a-f]{64}", lock["manifestHash"]) is None
        or not isinstance(lock.get("contentHash"), str)
        or HASH.fullmatch(lock["contentHash"]) is None
        or not safe_name(lock.get("package"))
        or not safe_name(lock.get("rootDirectory"))
    ):
        raise StateError("unsupported portable legacy format-6 lock")
    binding = lock.get("nativeToolLock")
    if (
        not isinstance(binding, dict)
        or set(binding) != {"file", "sha256"}
        or binding["file"] != "Package.pins"
        or (
            binding["sha256"] is not None
            and (
                not isinstance(binding["sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", binding["sha256"]) is None
            )
        )
    ):
        raise StateError("invalid legacy native pin metadata binding")
    locations = set()
    for identity, pin in lock["pins"].items():
        if (
            not safe_name(identity)
            or not isinstance(pin, dict)
            or set(pin)
            - {"name", "sourceLocation", "revision", "version", "hash", "url"}
            or not {"name", "sourceLocation", "revision", "version", "hash"} <= set(pin)
            or pin.get("name") != identity
            or not git_location(pin.get("sourceLocation"))
            or pin["sourceLocation"] in locations
            or not isinstance(pin.get("revision"), str)
            or REVISION.fullmatch(pin["revision"]) is None
            or not version_valid(pin.get("version"))
            or not isinstance(pin.get("hash"), str)
            or HASH.fullmatch(pin["hash"]) is None
            or ("url" in pin and not git_location(pin["url"]))
        ):
            raise StateError("invalid or ambiguous resolved legacy Git checkout")
        locations.add(pin["sourceLocation"])
    if not isinstance(lock.get("products"), list):
        raise StateError("invalid legacy product view")
    product_names = set()
    for product in lock["products"]:
        if (
            not isinstance(product, dict)
            or not safe_name(product.get("name"))
            or product["name"] in product_names
            or product.get("type") not in ("executable", "library")
            or set(product)
            != (
                {"name", "type", "modules"}
                if product.get("type") == "executable"
                else {"name", "type", "modules", "linkage"}
            )
            or (
                product["type"] == "library"
                and product.get("linkage") not in ("static", "dynamic")
            )
            or not isinstance(product.get("modules"), list)
            or not product["modules"]
            or not all(safe_name(name) for name in product["modules"])
            or len(set(product["modules"])) != len(product["modules"])
        ):
            raise StateError("invalid or ambiguous legacy product")
        product_names.add(product["name"])
    metadata, actual_hash = native_pins(native_file)
    if binding["sha256"] is None:
        if lock["pins"] or metadata is not None:
            raise StateError(
                "absent native metadata is supported only for an empty legacy graph"
            )
    elif actual_hash != binding["sha256"]:
        raise StateError("native Package.pins bytes differ from the legacy lock")
    if metadata is not None:
        for pin in metadata["pins"]:
            checkout = lock["pins"].get(pin["package"])
            if (
                checkout is None
                or checkout["sourceLocation"] != pin["repositoryURL"]
                or checkout["version"] != pin["version"]
            ):
                raise StateError(
                    "native pin name, source, or version disagrees with its Git checkout"
                )
    return metadata


def checkout_record(identity, pin):
    """Original edited schema; only the proven dormant subpath is normalized."""
    return {
        "basedOn": {
            "basedOn": None,
            "currentRevision": pin["revision"],
            "currentVersion": pin["version"],
            "repositoryURL": pin["sourceLocation"],
            "subpath": identity,
        },
        "currentRevision": None,
        "currentVersion": None,
        "repositoryURL": pin["sourceLocation"],
        "subpath": identity,
    }


def workspace(lock):
    return {
        "dependencies": [
            checkout_record(name, pin) for name, pin in sorted(lock["pins"].items())
        ],
        "version": 1,
    }


def capture(root, package, state, environment, isolation, api):
    """Capture only actual native workspace1 checkouts; never use pin versions as revisions."""
    import json
    import tempfile
    import bounded_native

    if api.sdk_mode():
        raise StateError(
            "legacy workspace1 SDK-bound capture is unavailable; use local mode; no format5 downgrade is permitted"
        )
    if (
        not isinstance(state, dict)
        or set(state) != {"dependencies", "version"}
        or type(state["version"]) is not int
        or state["version"] != 1
        or not isinstance(state["dependencies"], list)
    ):
        raise StateError("unsupported original workspace-state format1")
    evidence = Path(tempfile.mkdtemp(prefix="swiftpm-purifier-legacy-capture-"))
    logs = evidence / "logs"
    logs.mkdir()
    receipt = {"complete": False, "nativeWorkspace": state, "commands": []}

    def save():
        (evidence / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    command = [*api.native_command("swift-package"), *isolation]
    products, package_name = legacy_manifest.capture(
        package, command, environment, evidence / "manifest"
    )
    staged = {}
    locations = set()
    for index, dependency in enumerate(state["dependencies"]):
        if (
            not isinstance(dependency, dict)
            or set(dependency)
            != {
                "basedOn",
                "currentRevision",
                "currentVersion",
                "repositoryURL",
                "subpath",
            }
            or dependency["basedOn"] is not None
            or not isinstance(dependency["currentRevision"], str)
            or REVISION.fullmatch(dependency["currentRevision"]) is None
            or not version_valid(dependency["currentVersion"])
            or not git_location(dependency["repositoryURL"])
            or dependency["repositoryURL"] in locations
            or not safe_name(dependency["subpath"])
        ):
            raise StateError("unsupported or ambiguous original Git checkout")
        checkout = package / ".build/checkouts" / dependency["subpath"]
        manifest = checkout / "Package.swift"
        if (
            not checkout.is_dir()
            or not manifest.is_file()
            or not manifest.resolve().is_relative_to(checkout.resolve())
        ):
            raise StateError(
                "legacy checkout manifest is unavailable or escapes its source"
            )
        row = {"label": "checkout-manifest-" + str(index)}
        receipt["commands"].append(row)
        result = bounded_native.run_recorded(
            [*command, "--verbose", "dump-package"],
            record=row,
            logs=logs,
            save=save,
            cwd=checkout,
            env=environment,
            timeout=60,
            output_limit=1024 * 1024,
        )
        if result.returncode:
            raise StateError(
                "selected native tool rejected dependency manifest; evidence at "
                + str(evidence)
            )
        _, description = legacy_manifest.invocation(result.stdout, checkout)
        name = description["name"]
        if not safe_name(name) or name in staged:
            raise StateError(
                "legacy source has an unsafe or duplicate actual package name"
            )
        staged[name] = {
            "name": name,
            "sourceLocation": dependency["repositoryURL"],
            "revision": dependency["currentRevision"],
            "version": dependency["currentVersion"],
        }
        locations.add(dependency["repositoryURL"])
    native_file = package / "Package.pins"
    metadata, metadata_hash = native_pins(native_file)
    destination = api.legacy_native_path(root)
    original_metadata, original_hash = native_pins(destination)
    if metadata is not None and original_metadata == metadata:
        # Native fetch may reformat unchanged metadata. Bind the developer's
        # original bytes, including whitespace, rather than rewriting them.
        native_file.write_bytes(destination.read_bytes())
        metadata_hash = original_hash
    if (
        not api.TOOL_LOCK_OVERRIDE
        and native_file.exists()
        and (package / "Package.resolved").exists()
    ):
        raise StateError(
            "both native pin formats are present; select --tool-lockfile explicitly"
        )
    if metadata is None and staged:
        raise StateError(
            "native metadata absence is supported only for an empty original graph"
        )
    if metadata is not None:
        for pin in metadata["pins"]:
            checkout = staged.get(pin["package"])
            if (
                checkout is None
                or checkout["sourceLocation"] != pin["repositoryURL"]
                or checkout["version"] != pin["version"]
            ):
                raise StateError(
                    "actual native pin name/source/version disagrees with resolved checkout"
                )
    mirrors = api.mirrors(package, evidence / "config", legacy=True)
    for pin in staged.values():
        location = pin["sourceLocation"]
        url = mirrors.get(location, location)
        if not git_location(url):
            raise StateError("legacy Git mirror must be a portable remote location")
        if url != location:
            pin["url"] = url
        pin["hash"] = api.prefetch_git(url, pin["revision"])
    lock = {
        "version": FORMAT,
        "workspaceStateVersion": 1,
        "package": package_name,
        "rootDirectory": root.name,
        "manifestHash": api.manifest_hash(root),
        "pins": staged,
        "products": sorted(products, key=lambda product: product["name"]),
        "localDependencies": [],
        "binaryTargets": [],
        "nativeToolLock": {"file": "Package.pins", "sha256": metadata_hash},
    }
    lock["contentHash"] = api.content_hash(lock)
    validate(lock, native_file)
    lock_bytes = (json.dumps(lock, indent=2, ensure_ascii=False) + "\n").encode()
    native_bytes = native_file.read_bytes() if native_file.exists() else None
    # Both payloads are completely validated and serialized before user files
    # change. Preserve original bytes and absence when native fetch does so.
    if native_bytes is None:
        if destination.exists():
            destination.unlink()
    elif not destination.exists() or destination.read_bytes() != native_bytes:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(native_bytes)
    api.lock_path(root).write_bytes(lock_bytes)
    receipt.update(complete=True, lock=lock, nativeMetadataSha256=metadata_hash)
    save()
    print(
        "swiftpm-purifier: wrote portable legacy format6 with "
        + str(len(staged))
        + " actual Git checkouts",
        file=__import__("sys").stderr,
    )
    return lock
