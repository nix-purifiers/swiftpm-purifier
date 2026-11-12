# SPDX-License-Identifier: MIT
import hashlib
import copy
import os
import sys
import time
import shlex
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import swiftpm_purifier as sp
import legacy_manifest as lm
import legacy_state as ls


class LegacyWorkspaceCodec(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "Package.swift").write_text(
            '// swift-tools-version:3.1\nimport PackageDescription\nlet package=Package(name:"Project")\n'
        )
        fixture = Path(__file__).parent / "fixtures/native311"
        raw = (fixture / "Package.pins").read_bytes()
        (self.root / "Package.pins").write_bytes(raw)
        native = json.loads(raw)["pins"][0]
        checkout = json.loads((fixture / "workspace-state.json").read_text())[
            "dependencies"
        ][0]
        self.lock = {
            "version": 6,
            "workspaceStateVersion": 1,
            "package": "Project",
            "rootDirectory": "Project",
            "pins": {
                native["package"]: {
                    "name": native["package"],
                    "sourceLocation": native["repositoryURL"],
                    "revision": checkout["currentRevision"],
                    "version": checkout["currentVersion"],
                    "hash": "sha256-" + "A" * 43 + "=",
                }
            },
            "localDependencies": [],
            "binaryTargets": [],
            "products": [],
            "nativeToolLock": {
                "file": "Package.pins",
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
            "manifestHash": sp.manifest_hash(self.root),
        }
        self.save()
        self.override = mock.patch.object(sp, "TOOL_LOCK_OVERRIDE", None)
        self.override.start()
        self.addCleanup(self.override.stop)
        self.lock_override = mock.patch.object(sp, "LOCK_OVERRIDE", None)
        self.lock_override.start()
        self.addCleanup(self.lock_override.stop)

    def save(self):
        self.lock["contentHash"] = sp.content_hash(self.lock)
        (self.root / sp.LOCK_NAME).write_text(json.dumps(self.lock))

    def test_actual_native_metadata_and_git_version_are_accepted(self):
        self.assertEqual(sp.read_lock(self.root), self.lock)
        self.assertTrue(sp.is_current(self.root, self.lock))
        self.assertEqual(
            ls.workspace(self.lock)["dependencies"][0]["basedOn"]["currentRevision"],
            self.lock["pins"]["LegacyLeaf"]["revision"],
        )

    def test_changed_native_bytes_rejected_before_runtime_or_tree(self):
        with (self.root / "Package.pins").open("ab") as stream:
            stream.write(b" ")
        with (
            mock.patch.object(
                sp,
                "observe_host_runtime",
                side_effect=AssertionError("runtime must not be probed"),
            ),
            mock.patch.object(
                sp,
                "nix_build",
                side_effect=AssertionError("tree must not be built"),
            ),
        ):
            with self.assertRaises(sp.PurifierError):
                sp.prefetch(self.root)
        self.assertFalse((self.root / ".build").exists())

    def test_current_legacy_lock_does_not_convert_back_to_four(self):
        before = (self.root / sp.LOCK_NAME).read_bytes()
        with (
            mock.patch.object(sp, "sdk_mode", return_value=False),
            mock.patch.object(sp, "require_runtime"),
            mock.patch.object(
                sp,
                "relock",
                side_effect=AssertionError("idempotent lock must not recapture"),
            ),
        ):
            sp.lock_command(self.root, False)
        self.assertEqual((self.root / sp.LOCK_NAME).read_bytes(), before)

    def test_sdk_binding_is_rejected_before_measurement(self):
        with mock.patch.object(
            sp,
            "native_sdk_identity",
            side_effect=AssertionError("no invented SDK binding"),
        ):
            with self.assertRaisesRegex(sp.PurifierError, "no SDK-bound"):
                sp.require_native_sdk(self.lock)

    def test_empty_graph_native_absence_is_preserved(self):
        (self.root / "Package.pins").unlink()
        self.lock["pins"] = {}
        self.lock["nativeToolLock"]["sha256"] = None
        self.save()
        self.assertEqual(sp.read_lock(self.root), self.lock)
        self.assertFalse((self.root / "Package.pins").exists())
        (self.root / "Package.pins").write_text(
            '{"version":1,"autoPin":true,"pins":[]}'
        )
        with self.assertRaises(sp.PurifierError):
            sp.read_lock(self.root)

    def test_renderer_matches_actual_edited_native_record(self):
        fixture = (
            Path(__file__).parent / "fixtures/native311/edited-workspace-state.json"
        )
        native = json.loads(fixture.read_text())
        expected = copy.deepcopy(native)
        checkout = native["dependencies"][0]["basedOn"]
        expected["dependencies"][0]["basedOn"]["subpath"] = "LegacyLeaf"
        lock = {
            "pins": {
                "LegacyLeaf": {
                    "revision": checkout["currentRevision"],
                    "version": checkout["currentVersion"],
                    "sourceLocation": checkout["repositoryURL"],
                }
            }
        }
        self.assertEqual(ls.workspace(lock), expected)


class LegacyManifestProtocol(unittest.TestCase):
    fixture = Path(__file__).parent / "fixtures/native311"

    def test_actual_native_multimodule_products(self):
        raw = (self.fixture / "multimodule-protocol.json").read_bytes()
        data = json.loads(raw)
        products = lm.protocol(raw, data["package"])
        graph = {
            "name": "multi",
            "modules": [
                {"name": name, "is_test": False, "type": "library"}
                for name in ("One", "Two")
            ],
        }
        self.assertEqual(
            lm.product_view(products, graph, "multi"),
            [
                {
                    "name": "Combo",
                    "type": "library",
                    "linkage": "dynamic",
                    "modules": ["One", "Two"],
                },
                {
                    "name": "StaticCombo",
                    "type": "library",
                    "linkage": "static",
                    "modules": ["One", "Two"],
                },
            ],
        )

    def test_actual_error_protocol_never_accepts_fallback_version(self):
        raw = (self.fixture / "invalid-version-protocol.json").read_bytes()
        with self.assertRaisesRegex(lm.ManifestError, "reported errors"):
            lm.protocol(raw, json.loads(raw)["package"])

    def test_library_modules_and_tests_are_not_inferred_products(self):
        graph = {
            "name": "library",
            "modules": [
                {"name": "library", "is_test": False, "type": "library"},
                {"name": "libraryTests", "is_test": True, "type": "library"},
            ],
        }
        self.assertEqual(lm.product_view([], graph, "library"), [])

    def test_alias_keeps_implicit_native_executable(self):
        graph = {
            "name": "library",
            "modules": [{"name": "library", "is_test": False, "type": "executable"}],
        }
        self.assertEqual(
            lm.product_view(
                [{"name": "Alias", "type": "exe", "modules": ["library"]}],
                graph,
                "library",
            ),
            [
                {"name": "Alias", "type": "executable", "modules": ["library"]},
                {"name": "library", "type": "executable", "modules": ["library"]},
            ],
        )

    def test_unavailable_module_and_ambiguous_names_rejected(self):
        graph = {
            "name": "library",
            "modules": [{"name": "library", "is_test": False, "type": "executable"}],
        }
        for product in (
            {"name": "Alias", "type": "a", "modules": ["Missing"]},
            {"name": "library", "type": "a", "modules": ["library"]},
        ):
            with self.assertRaises(lm.ManifestError):
                lm.product_view([product], graph, "library")

    def test_unsafe_implicit_product_names_rejected(self):
        for name in ("../escape", "..", "a\\b", "bad\0name"):
            graph = {
                "name": "Project",
                "modules": [{"name": name, "is_test": False, "type": "executable"}],
            }
            with self.subTest(name=name), self.assertRaises(lm.ManifestError):
                lm.product_view([], graph, "Project")

    def test_duplicate_keys_and_non_json_constants_rejected(self):
        for raw in (b'{"name":1,"name":2}', b'{"version":NaN}'):
            with self.assertRaises(lm.ManifestError):
                lm.json_value(raw)
        with self.assertRaisesRegex(lm.ManifestError, "bound"):
            lm.json_value(b" " * (1024 * 1024 + 1))

    def test_protocol_package_shape_and_product_mutations(self):
        baseline = json.loads((self.fixture / "multimodule-protocol.json").read_bytes())
        cases = []
        bad = copy.deepcopy(baseline)
        bad["package"]["name"] = "Other"
        cases.append(bad)
        bad = copy.deepcopy(baseline)
        bad["products"][0]["modules"] = ["One", "One"]
        cases.append(bad)
        bad = copy.deepcopy(baseline)
        bad["products"][0]["type"] = "automatic"
        cases.append(bad)
        bad = copy.deepcopy(baseline)
        bad["products"][1]["name"] = "Combo"
        cases.append(bad)
        bad = copy.deepcopy(baseline)
        bad["products"][0]["name"] = "../escape"
        cases.append(bad)
        bad = copy.deepcopy(baseline)
        bad["errors"] = None
        cases.append(bad)
        for data in cases:
            with self.subTest(data=data), self.assertRaises(lm.ManifestError):
                lm.protocol(json.dumps(data), baseline["package"])

    def test_authoritative_invocation_spaces_and_rejection_controls(self):
        # Contract-shaped filesystem data exercises framing only. No fake
        # compiler or library is executed, probed, or claimed as a real SDK.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "project with spaces"
            package.mkdir()
            manifest = package / "Package.swift"
            manifest.write_text("// framing fixture")
            compiler = root / "compiler with spaces"
            compiler.write_text("unused")
            compiler.chmod(0o755)
            library = root / "library with spaces"
            library.mkdir()
            for name in ("PackageDescription.swiftmodule", "libPackageDescription.so"):
                (library / name).touch()
            argv = [
                str(compiler),
                "--driver-mode=swift",
                "-I",
                str(library),
                "-L",
                str(library),
                "-lPackageDescription",
                str(manifest),
                "-fileno",
                "5",
            ]
            output = (shlex.join(argv) + '\n{"name":"Project"}').encode()
            command, description = lm.invocation(output, package)
            self.assertEqual(command, argv[:-2])
            self.assertEqual(description, {"name": "Project"})
            for candidate in (
                output + b"noise",
                output.replace(b"-fileno", b"-unknown-fd"),
                output.replace(b"Package.swift", b"Other.swift"),
            ):
                with self.assertRaises(lm.ManifestError):
                    lm.invocation(candidate, package)
            (library / "libPackageDescription.so").unlink()
            with self.assertRaisesRegex(lm.ManifestError, "binding"):
                lm.invocation(output, package)


class NativeProtocolBoundaries(unittest.TestCase):
    def native(self, script, **options):
        return sp.bounded_native.run(
            [sys.executable, "-c", script],
            protocol_fd_flag="--protocol-fd",
            timeout=1,
            **options,
        )

    def test_protocol_is_separate_and_status_is_preserved(self):
        result = self.native(
            "import os,sys; fd=int(sys.argv[-1]); "
            "os.write(1,b'noise'); os.write(2,b'diagnostic'); "
            "os.write(fd,b'{\"answer\":42}'); sys.exit(17)"
        )
        self.assertEqual(result.returncode, 17)
        self.assertEqual(result.stdout, b"noise")
        self.assertEqual(result.stderr, b"diagnostic")
        self.assertEqual(json.loads(result.protocol), {"answer": 42})
        self.assertEqual(result.args[-2], "--protocol-fd")

    def test_protocol_shares_combined_output_bound(self):
        with self.assertRaisesRegex(ValueError, "output bound") as caught:
            self.native(
                "import os,sys; os.write(1,b'1234'); os.write(int(sys.argv[-1]),b'56789')",
                output_limit=8,
            )
        self.assertGreater(
            len(caught.exception.stdout) + len(caught.exception.protocol), 8
        )

    def test_descendant_holding_only_protocol_pipe_hits_deadline(self):
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "deadline") as caught:
            self.native(
                "import os,sys,time; fd=int(sys.argv[-1]); pid=os.fork(); "
                "os.write(fd,b'partial') if pid else None; "
                "os._exit(0) if pid else None; "
                "os.close(1); os.close(2); time.sleep(30)"
            )
        self.assertEqual(caught.exception.protocol, b"partial")
        self.assertLess(time.monotonic() - started, 5)

    def test_closed_pipes_do_not_hide_live_parent(self):
        with self.assertRaisesRegex(TimeoutError, "deadline"):
            self.native(
                "import os,sys,time; os.close(1); os.close(2); os.close(int(sys.argv[-1])); time.sleep(30)"
            )

    def test_existing_two_stream_call(self):
        result = sp.bounded_native.run(
            [sys.executable, "-c", "print('unchanged')"], timeout=1
        )
        self.assertEqual(result.stdout, b"unchanged\n")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.protocol, b"")

    @unittest.skipUnless(Path("/proc/self/fd").is_dir(), "Linux descriptor accounting")
    def test_failed_spawn_closes_protocol_descriptors(self):
        before = set(os.listdir("/proc/self/fd"))
        with self.assertRaises(FileNotFoundError):
            sp.bounded_native.run(
                ["/nonexistent/purifier-protocol-command"], protocol_fd_flag="-fileno"
            )
        self.assertEqual(set(os.listdir("/proc/self/fd")), before)


def pin(identity, location, kind="remoteSourceControl"):
    """A pin of Package.resolved."""
    return {
        "identity": identity,
        "kind": kind,
        "location": location,
        "state": {"revision": "a" * 40, "version": "1.0.0"},
    }


def checkout(identity, location, kind="remoteSourceControl", name=None):
    """SwiftPM's workspace-state record of a checked-out dependency."""
    return {
        "basedOn": None,
        "packageRef": {
            "identity": identity,
            "kind": kind,
            "location": location,
            "name": name or identity,
        },
        "state": {
            "checkoutState": {"revision": "a" * 40, "version": "1.0.0"},
            "name": "sourceControlCheckout",
        },
        "subpath": identity,
    }


class LegacyLocalState(unittest.TestCase):
    fixture = Path(__file__).parent / "fixtures/native424"

    def test_actual_native_local_metadata_is_current(self):
        self.assertTrue(
            sp.is_current(
                self.fixture, json.loads((self.fixture / sp.LOCK_NAME).read_text())
            )
        )

    def test_escape_or_unknown_local_protocol_is_rejected(self):
        original = json.loads((self.fixture / sp.LOCK_NAME).read_text())
        for field, value in (
            ("path", "@root@/../Local"),
            ("path", "@root@/Missing"),
            ("isLocal", False),
            ("identity", "leaf"),
            ("name", "LegacyLeaf"),
        ):
            with self.subTest(field=field, value=value):
                lock = copy.deepcopy(original)
                lock["localDependencies"][0]["packageRef"][field] = value
                with self.assertRaises(sp.PurifierError):
                    sp.is_current(self.fixture, lock)

    def test_actual_symlink_and_directory_manifests_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            outside = base / "outside"
            shutil.copytree(self.fixture / "Local", outside)
            for kind in ("directory-link", "manifest-link", "directory-manifest"):
                with self.subTest(kind=kind):
                    root = base / kind
                    shutil.copytree(self.fixture, root)
                    manifest = root / "Local/Package.swift"
                    if kind == "directory-link":
                        shutil.rmtree(root / "Local")
                        (root / "Local").symlink_to(outside, target_is_directory=True)
                    elif kind == "manifest-link":
                        manifest.unlink()
                        manifest.symlink_to(outside / "Package.swift")
                    else:
                        manifest.unlink()
                        manifest.mkdir()
                    with self.assertRaises(sp.PurifierError):
                        sp.is_current(
                            root, json.loads((root / sp.LOCK_NAME).read_text())
                        )


class ManifestHash(unittest.TestCase):
    def test_matches_the_nix_formula(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Package.swift").write_text("// a\n")
            (root / "Package@swift-5.9.swift").write_text("// b\n")
            (root / "Other.swift").write_text("// ignored\n")
            listing = [
                ["Package.swift", hashlib.sha256(b"// a\n").hexdigest()],
                ["Package@swift-5.9.swift", hashlib.sha256(b"// b\n").hexdigest()],
            ]
            # builtins.toJSON writes no spaces.
            expected = hashlib.sha256(
                json.dumps(listing).replace(", ", ",").encode()
            ).hexdigest()
            self.assertEqual(sp.manifest_hash(root), expected)


class LegacyResolved(unittest.TestCase):
    fixtures = Path(__file__).parent / "fixtures/native553"

    def setUp(self):
        self.resolved = json.loads((self.fixtures / "Package.resolved").read_text())
        self.dependency = json.loads(
            (self.fixtures / "workspace-state.json").read_text()
        )["object"]["dependencies"][0]
        ref = self.dependency["packageRef"]
        self.identities = {ref["location"]: ref["identity"]}

    def read(self, value=None, identities=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Package.resolved"
            path.write_text(json.dumps(self.resolved if value is None else value))
            return sp.resolved_pins(
                path, self.identities if identities is None else identities
            )

    def test_uses_native_identity_instead_of_lowercasing_package_name(self):
        actual = self.read()[0]
        self.assertEqual(actual["identity"], "leaf")
        self.assertEqual(actual["legacyPackage"], "LegacyLeaf")
        self.assertEqual(sp.subpath_of(actual), "Leaf")

    def test_derives_original_workspace_checkout_exactly(self):
        actual = sp.record_of(self.read()[0], "LegacyLeaf")
        self.assertEqual(actual["basedOn"], self.dependency)

    def test_preserves_source_identity_join_in_written_pin(self):
        actual = self.read()[0]
        with (
            mock.patch.object(sp, "mirrors", return_value={}),
            mock.patch.object(
                sp, "prefetch_git", return_value="sha256-test"
            ) as prefetch,
        ):
            locked = sp.lock_pin(actual, self.dependency, Path("."), Path("config"))
        self.assertEqual(locked["sourceLocation"], actual["location"])
        self.assertEqual(locked["name"], "LegacyLeaf")
        prefetch.assert_called_once_with(
            actual["location"], actual["state"]["revision"]
        )

    def test_v1_pins_use_modern_workspace_protocol_when_native_state_is_7(self):
        fixture = self.fixtures.parent / "native633-v1"
        dependency = json.loads((fixture / "workspace-state.json").read_text())[
            "object"
        ]["dependencies"][0]
        ref = dependency["packageRef"]
        actual = sp.resolved_pins(
            fixture / "Package.resolved", {ref["location"]: ref["identity"]}
        )[0]
        record = sp.record_of(actual, "LegacyLeaf", 7)
        self.assertEqual(record["basedOn"], dependency)
        self.assertEqual(record["packageRef"]["kind"], "remoteSourceControl")
        self.assertEqual(record["basedOn"]["state"]["name"], "sourceControlCheckout")
        self.assertNotIn("branch", record["basedOn"]["state"]["checkoutState"])
        with (
            mock.patch.object(sp, "mirrors", return_value={}),
            mock.patch.object(sp, "prefetch_git", return_value="sha256-test"),
        ):
            locked = sp.lock_pin(actual, dependency, Path("."), Path("config"), 7)
        self.assertEqual(locked["sourceLocation"], actual["location"])

    def test_missing_native_identity_is_rejected(self):
        with self.assertRaises(sp.PurifierError):
            self.read(identities={})

    def test_duplicate_native_identity_is_rejected(self):
        value = copy.deepcopy(self.resolved)
        value["object"]["pins"] *= 2
        with self.assertRaises(sp.PurifierError):
            self.read(value)

    def test_invalid_legacy_states_are_rejected(self):
        for state in [
            None,
            {"revision": True},
            {"revision": "short"},
            {"revision": "a" * 40, "extra": 1},
            {"revision": "a" * 40, "branch": "main", "version": "1.0.0"},
        ]:
            with self.subTest(state=state):
                value = copy.deepcopy(self.resolved)
                value["object"]["pins"][0]["state"] = state
                with self.assertRaises(sp.PurifierError):
                    self.read(value)

    def test_native_name_must_match_workspace(self):
        actual = self.read()[0]
        actual["legacyPackage"] = "Other"
        with self.assertRaises(sp.PurifierError):
            sp.lock_pin(actual, self.dependency, Path("."), Path("config"))


class NativeSDKMetadata(unittest.TestCase):
    """Pure contract checks on a physically measured original SDK snapshot."""

    def setUp(self):
        self.identity = json.loads(
            (LegacyResolved.fixtures / "sdk-identity.json").read_text()
        )

    def test_actual_measured_identity_is_accepted(self):
        self.assertIs(sp.native_sdk.validate_identity(self.identity), self.identity)

    def test_missing_selected_closure_root_is_rejected(self):
        path = self.identity["descriptor"]["components"]["swift"]["path"]
        self.identity["closure"].pop(path)
        with self.assertRaises(sp.native_sdk.SDKError):
            sp.native_sdk.validate_identity(self.identity)

    def test_wrong_compiler_command_is_rejected(self):
        self.identity["descriptor"]["executables"]["swiftc"] = self.identity[
            "descriptor"
        ]["executables"]["swift"]
        with self.assertRaises(sp.native_sdk.SDKError):
            sp.native_sdk.validate_identity(self.identity)

    def test_missing_stdenv_recipe_is_rejected(self):
        self.identity["descriptor"]["components"]["stdenv"].pop("recipe")
        with self.assertRaises(sp.native_sdk.SDKError):
            sp.native_sdk.validate_identity(self.identity)

    def test_nonstring_capability_is_rejected(self):
        self.identity["capabilities"]["package"] = [{}]
        with self.assertRaises(sp.native_sdk.SDKError):
            sp.native_sdk.validate_identity(self.identity)

    def test_environment_cannot_select_another_compiler(self):
        self.identity["environment"]["variables"]["SWIFT_EXEC"] = self.identity[
            "descriptor"
        ]["executables"]["swift"]
        with self.assertRaises(sp.native_sdk.SDKError):
            sp.native_sdk.validate_identity(self.identity)

    def test_physical_closure_mismatch_is_rejected(self):
        lock = {"version": sp.LOCK_VERSION, "nativeSDK": copy.deepcopy(self.identity)}
        path = self.identity["descriptor"]["components"]["swift"]["path"]
        lock["nativeSDK"]["closure"][path] = "sha256-" + "A" * 43 + "="
        with mock.patch.object(sp, "native_sdk_identity", return_value=self.identity):
            with self.assertRaises(sp.PurifierError):
                sp.require_native_sdk(lock)

    def test_active_command_bindings_are_checked_even_with_measured_identity(self):
        descriptor = self.identity["descriptor"]
        variables = dict(
            self.identity["environment"]["variables"],
            PATH=self.identity["environment"]["pathPrefix"],
        )
        for role, name in {
            "swift": "SWIFTPM_PURIFIER_SWIFT",
            "package": "SWIFTPM_PURIFIER_SWIFT_PACKAGE",
            "build": "SWIFTPM_PURIFIER_SWIFT_BUILD",
            "test": "SWIFTPM_PURIFIER_SWIFT_TEST",
            "run": "SWIFTPM_PURIFIER_SWIFT_RUN",
        }.items():
            variables[name] = descriptor["executables"][role]
        with mock.patch.dict(sp.os.environ, variables, clear=True):
            sp.native_sdk.check_active(self.identity)
            sp.os.environ["SWIFTPM_PURIFIER_SWIFT_PACKAGE"] = descriptor["executables"][
                "swift"
            ]
            with self.assertRaises(sp.native_sdk.SDKError):
                sp.native_sdk.check_active(self.identity)


class IsCurrent(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "Package.swift").write_text("// a\n")
        self.pins = [pin("x", "https://example/x.git")]
        (self.root / "Package.resolved").write_text(
            json.dumps({"pins": self.pins, "version": 3})
        )
        self.lock = {
            "manifestHash": sp.manifest_hash(self.root),
            "pins": {"x": {"revision": "a" * 40}},
        }

    def tearDown(self):
        self.temporary.cleanup()

    def test_a_lock_covering_every_pin_is_current(self):
        self.assertTrue(sp.is_current(self.root, self.lock))

    def test_a_moved_pin_is_out_of_date(self):
        self.lock["pins"]["x"]["revision"] = "b" * 40
        self.assertFalse(sp.is_current(self.root, self.lock))

    def test_an_extra_pin_is_out_of_date(self):
        self.lock["pins"]["y"] = {"revision": "a" * 40}
        self.assertFalse(sp.is_current(self.root, self.lock))

    def test_a_lockfile_of_swiftpm_kept_outside_is_the_one_checked(self):
        outside = self.root / "outside.resolved"
        outside.write_text(json.dumps({"pins": [], "version": 3}))
        self.lock["pins"] = {}
        with mock.patch.object(sp, "TOOL_LOCK_OVERRIDE", str(outside)):
            self.assertEqual(sp.resolved_path(self.root), outside.resolve())
            self.assertTrue(sp.is_current(self.root, self.lock))
        self.assertFalse(sp.is_current(self.root, self.lock))

    def test_no_package_resolved_means_no_pins(self):
        (self.root / "Package.resolved").unlink()
        self.assertEqual(sp.resolved_pins(self.root / "Package.resolved"), [])
        self.lock["pins"] = {}
        self.assertTrue(sp.is_current(self.root, self.lock))


class ReadLock(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, text):
        (self.root / sp.LOCK_NAME).write_text(text)

    def assert_refused(self, *phrases):
        with self.assertRaises(sp.PurifierError) as caught:
            sp.read_lock(self.root)
        message = str(caught.exception)
        self.assertIn("swiftpm-purifier relock", message)
        for phrase in phrases:
            self.assertIn(phrase, message)

    def test_no_lockfile_is_none(self):
        self.assertIsNone(sp.read_lock(self.root))

    def test_invalid_json_names_the_relock(self):
        self.write("{ not json")
        self.assert_refused("not valid JSON")

    def test_a_missing_field_is_named(self):
        lock = {key: None for key in sp.LOCK_KEYS if key != "pins"}
        lock["version"] = sp.LOCK_VERSION
        self.write(json.dumps(lock))
        self.assert_refused("lacks pins")

    def test_another_format_version_is_refused(self):
        lock = {key: None for key in sp.LOCK_KEYS}
        lock["version"] = (
            max(sp.LOCK_VERSION, sp.legacy_state.FORMAT, sp.pre_workspace.FORMAT) + 1
        )
        self.write(json.dumps(lock))
        self.assert_refused("lock format version")

    def test_a_lockfile_changed_by_hand_is_refused(self):
        lock = {key: [] for key in sp.LOCK_KEYS}
        lock["version"] = sp.LEGACY_LOCK_VERSION
        lock["contentHash"] = sp.content_hash(lock)
        self.write(json.dumps(lock))
        self.assertEqual(sp.read_lock(self.root)["version"], sp.LEGACY_LOCK_VERSION)
        lock["pins"] = {"x": {"hash": "sha256-x"}}
        self.write(json.dumps(lock))
        self.assert_refused("contentHash")

    def test_lock_replaces_an_unreadable_lockfile(self):
        self.write("{ not json")
        with mock.patch.object(sp, "relock") as relock:
            sp.lock_command(self.root, force=False)
        relock.assert_called_once_with(self.root)

    def test_lock_upgrades_an_unbound_version_four_record(self):
        with (
            mock.patch.object(sp, "read_lock", return_value={"version": 4}),
            mock.patch.object(sp, "relock") as relock,
        ):
            sp.lock_command(self.root, force=False)
        relock.assert_called_once_with(self.root)


class Template(unittest.TestCase):
    def test_replaces_the_package_directory(self):
        package = Path("/tmp/work/package")
        value = {
            "path": "/tmp/work/package/.build/artifacts/x",
            "list": ["/tmp/work/package"],
            "n": 1,
        }
        self.assertEqual(
            sp.template(value, package),
            {"path": "@root@/.build/artifacts/x", "list": ["@root@"], "n": 1},
        )


class LockPin(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.package = Path(self.temporary.name) / "package"
        self.config = Path(self.temporary.name) / "config"
        self.package.mkdir()
        self.config.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def test_a_remote_pin_records_its_hash_and_package_name(self):
        url = "https://github.com/swiftlang/swift-cmark.git"
        with mock.patch.object(sp, "prefetch_git", return_value="sha256-x") as prefetch:
            locked = sp.lock_pin(
                pin("swift-cmark", url),
                checkout("swift-cmark", url, name="cmark-gfm"),
                self.package,
                self.config,
            )
        prefetch.assert_called_once_with(url, "a" * 40)
        self.assertEqual(
            locked, {"name": "cmark-gfm", "revision": "a" * 40, "hash": "sha256-x"}
        )

    def test_a_checkout_keeps_the_case_of_the_url(self):
        url = "https://github.com/jpsim/Yams.git"
        self.assertEqual(sp.subpath_of(pin("yams", url)), "Yams")
        self.assertEqual(sp.subpath_of(pin("x", "https://example/x/")), "x")

    def test_the_derived_record_is_swiftpms_record_marked_as_edited(self):
        url = "https://example/x.git"
        record = sp.record_of(pin("x", url), "x")
        self.assertEqual(record["basedOn"], checkout("x", url))
        self.assertEqual(record["state"], {"name": "edited", "path": None})

    def test_a_record_that_differs_from_the_derived_one_is_refused(self):
        dependency = checkout("x", "https://example/x.git")
        dependency["subpath"] = "elsewhere"
        with self.assertRaises(sp.PurifierError) as caught:
            sp.lock_pin(
                pin("x", "https://example/x.git"), dependency, self.package, self.config
            )
        self.assertIn("does not derive", str(caught.exception))

    def test_a_mirror_is_recorded_as_the_fetch_url(self):
        mirrors = {
            "object": [
                {
                    "original": "https://a.example/x.git",
                    "mirror": "https://b.example/x.git",
                }
            ],
            "version": 1,
        }
        (self.config / "mirrors.json").write_text(json.dumps(mirrors))
        with mock.patch.object(sp, "prefetch_git", return_value="sha256-x") as prefetch:
            locked = sp.lock_pin(
                pin("x", "https://a.example/x.git"),
                checkout("x", "https://a.example/x.git"),
                self.package,
                self.config,
            )
        prefetch.assert_called_once_with("https://b.example/x.git", "a" * 40)
        self.assertEqual(locked["url"], "https://b.example/x.git")

    def test_a_local_repository_is_refused(self):
        with self.assertRaises(sp.PurifierError):
            sp.lock_pin(
                pin("local", "/home/someone/local", kind="localSourceControl"),
                checkout("local", "/home/someone/local", kind="localSourceControl"),
                self.package,
                self.config,
            )

    def test_a_registry_pin_records_its_archive_url(self):
        registries = {
            "registries": {"[default]": {"url": "https://registry.example/"}},
            "version": 1,
        }
        (self.config / "registries.json").write_text(json.dumps(registries))
        registry_pin = {
            "identity": "nix.greeter",
            "kind": "registry",
            "location": "",
            "state": {"version": "1.2.0"},
        }
        dependency = {
            "basedOn": None,
            "packageRef": {
                "identity": "nix.greeter",
                "kind": "registry",
                "location": "nix.greeter",
                "name": "nix.greeter",
            },
            "state": {"name": "registryDownload", "version": "1.2.0"},
            "subpath": "nix/greeter/1.2.0",
        }
        with mock.patch.object(
            sp, "prefetch_file", return_value="sha256-y"
        ) as prefetch:
            locked = sp.lock_pin(registry_pin, dependency, self.package, self.config)
        prefetch.assert_called_once_with(
            "https://registry.example/nix/greeter/1.2.0.zip"
        )
        self.assertEqual(
            locked,
            {
                "name": "nix.greeter",
                "version": "1.2.0",
                "url": "https://registry.example/nix/greeter/1.2.0.zip",
                "hash": "sha256-y",
            },
        )


class RelockFailureWrites(unittest.TestCase):
    """Later validation failures must preserve both user-owned outputs."""

    def exercise(self, failure):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Package.swift").write_text("// swift-tools-version:5.5\n")
            resolved = root / "Package.resolved"
            lock = root / sp.LOCK_NAME
            old_resolved = b'{"version":3,"pins":[]}\n'
            old_lock = b'{"previous":"user-owned-output"}\n'
            resolved.write_bytes(old_resolved)
            lock.write_bytes(old_lock)

            def resolve(command, **kwargs):
                package = kwargs["cwd"]
                (package / "Package.resolved").write_text(
                    json.dumps(
                        {"version": 3, "pins": [pin("x", "https://example/x.git")]}
                    )
                )
                (package / ".build").mkdir()
                dependencies = (
                    []
                    if failure == "workspace"
                    else [checkout("x", "https://example/x.git")]
                )
                (package / ".build/workspace-state.json").write_text(
                    json.dumps(
                        {
                            "version": 6,
                            "object": {"dependencies": dependencies, "artifacts": []},
                        }
                    )
                )

            products = mock.Mock(return_value=([], "Root"))
            if failure == "products":
                products.side_effect = sp.PurifierError(
                    "native product inspection failed"
                )
            if failure == "serialization":
                products.return_value = ([], {"not JSON serializable"})
            with (
                mock.patch.object(sp, "LOCK_OVERRIDE", None),
                mock.patch.object(sp, "TOOL_LOCK_OVERRIDE", None),
                mock.patch.object(sp, "swiftpm_isolation", return_value=[]),
                mock.patch.object(sp, "native_sdk_identity", return_value={}),
                mock.patch.object(sp, "run", side_effect=resolve),
                mock.patch.object(sp, "lock_pin", return_value={"revision": "a" * 40}),
                mock.patch.object(sp, "products", products),
            ):
                with self.assertRaises((sp.PurifierError, TypeError)):
                    sp.relock(root)
            self.assertEqual(resolved.read_bytes(), old_resolved)
            self.assertEqual(lock.read_bytes(), old_lock)

    def test_missing_workspace_pin_preserves_both_outputs(self):
        self.exercise("workspace")

    def test_failed_native_product_inspection_preserves_both_outputs(self):
        self.exercise("products")

    def test_serialization_failure_preserves_both_outputs(self):
        self.exercise("serialization")


class UnlockedWrapperTest(unittest.TestCase):
    def test_unlocked_delegation_preserves_arguments_streams_and_status(self):
        import json
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native = root / "native"
            native.write_text(
                "#!" + sys.executable + "\n"
                "import json, sys\n"
                "print(json.dumps(sys.argv[1:]))\n"
                "print('native stderr', file=sys.stderr)\n"
                "sys.exit(23)\n"
            )
            native.chmod(0o755)
            environment = dict(os.environ)
            environment["SWIFTPM_PURIFIER_SWIFT"] = str(native)
            pass
            arguments = ["build", "argument with spaces", "--example=a=b"]
            command = [
                sys.executable,
                str(Path(__file__).with_name("swiftpm_purifier.py")),
                *["swift"],
                *arguments,
            ]
            result = subprocess.run(
                command, cwd=root, env=environment, capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 23, result.stderr)
            self.assertEqual(json.loads(result.stdout), arguments)
            self.assertIn("warning:", result.stderr)
            self.assertIn("impurely", result.stderr)
            self.assertIn("native stderr", result.stderr)
            self.assertEqual([p.name for p in root.iterdir()], ["native"])

            # An existing malformed lock must never use this fallback.
            for name, contents in {
                "Package.swift": "// swift-tools-version: 6.0\n"
            }.items():
                (root / name).write_text(contents)
            (root / "swiftpm-purifier.lock.json").write_text("{broken json")
            result = subprocess.run(
                command, cwd=root, env=environment, capture_output=True, text=True
            )
            self.assertNotEqual(result.returncode, 23)
            self.assertNotIn("native stderr", result.stderr)


if __name__ == "__main__":
    unittest.main()
