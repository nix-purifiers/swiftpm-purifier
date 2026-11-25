# SPDX-License-Identifier: MIT
"""Store-input identity and closure refusals; no SDK or subprocess responses."""

import copy
import json
from unittest.mock import patch
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

import dependencies_v1_cache as cache
import store_git_sources as store

BETA = {
    "path": "/nix/store/8rf90rkvqmj48s1290w5m54hv0bp6nai-purifier-beta-git-fixture",
    "narHash": "sha256-rjISdpUjdGmUSfjreLZeUfXNfO6us24D6ZSKLwOfEI0=",
    "subdirectory": "beta.git",
}
ALPHA = {
    "path": "/nix/store/krp7vr3lh5yv37z0s2lk8nmwjqf3ksis-purifier-alpha-git-fixture",
    "narHash": "sha256-HldBR3cqy5qFCgQUS9U8WvviWcFw6BXdw13QasiaqL4=",
    "subdirectory": "alpha.git",
}


class Identity(unittest.TestCase):
    def test_actual_nix_original_and_counter_name_vectors(self):
        # These are actual205/219 outputs, not expected values generated here.
        for value, counter in [
            (
                BETA,
                "/nix/store/42q6faxwsmf65hx8slccc43mmlvz2npm-purifier-beta-git-fixture-countercontrol",
            ),
            (
                ALPHA,
                "/nix/store/3yh25dqrknb9qz1gwnxjkdld6xay9c2q-purifier-alpha-git-fixture-countercontrol",
            ),
        ]:
            with self.subTest(value=value["path"]):
                source = store.Source.decode(value)
                self.assertEqual(
                    store.content_addressed_root(source.nar_hash, source.name),
                    source.path,
                )
                name = Path(counter).name[33:]
                self.assertEqual(
                    store.content_addressed_root(source.nar_hash, name), counter
                )
                self.assertNotEqual(counter, source.path)
                self.assertEqual(store.uri_location(source.uri), source.location)

    def test_wrong_nar_name_digest_mode_encoding_and_store_refuse(self):
        changes = [
            {"narHash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="},
            {"path": BETA["path"] + "-different-name"},
            {"path": BETA["path"].replace("8rf90", "9rf90")},
            {"path": BETA["path"].replace("/nix/store/", "/gnu/store/")},
            {"path": "../" + BETA["path"].lstrip("/")},
            {"narHash": "sha512-" + "A" * 86 + "=="},
            {"narHash": "sha256:" + "0" * 52},
            {"narHash": "sha256-" + "0" * 64},
            {"subdirectory": "../beta.git"},
            {"subdirectory": "/beta.git"},
            {"subdirectory": "nested/beta.git"},
            {"subdirectory": ".git"},
            {"subdirectory": "beta.git/"},
        ]
        # Actual125 flat archive identity is not a recursive source identity.
        changes.append(
            {
                "path": "/nix/store/z99y75ya3zcig8jq1xg2kc47zanl64d3-swift-6.4.2-RELEASE-ubuntu22.04.tar.gz",
                "narHash": "sha256-wwgbGBcRfGbkdk0qFINX2ZcgAEiSLHeLDwjHlYM64G0=",
            }
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(cache.Reject):
                store.Source.decode(BETA | change)
        with self.assertRaises(cache.Reject):
            store.Source.decode(BETA | {"future": None})

    def test_uri_is_exact_and_cannot_add_host_traversal_or_aliases(self):
        uri = store.Source.decode(BETA).uri
        for bad in [
            uri.replace("file:///", "file://localhost/"),
            uri.replace("file:///", "FILE:///"),
            uri + "/",
            uri + "?query=x",
            uri + "#fragment",
            uri.replace("/beta.git", "/./beta.git"),
            uri.replace("/beta.git", "/nested/../beta.git"),
            uri.replace("/beta.git", "/beta%2Egit"),
            uri.replace("/beta.git", "/beta%00.git"),
            uri.replace("/nix/store/", "/nix//store/"),
            "file:///tmp/beta.git",
        ]:
            with self.subTest(bad=bad), self.assertRaises(cache.Reject):
                store.uri_location(bad)

    def test_store_name_constraints_and_canonical_uri_encoding(self):
        for name in ["", ".", "..", ".-x", "..-x", "x/y", "x%y", "x y", "x" * 212]:
            with self.subTest(name=name):
                self.assertFalse(store.valid_store_name(name))
        for name in [".hidden", "...", "x" * 211, "fixture+question?equal=1"]:
            with self.subTest(name=name):
                self.assertTrue(store.valid_store_name(name))
        name = "fixture+question?equal=1"
        root = store.content_addressed_root(BETA["narHash"], name)
        source = store.Source.decode(BETA | {"path": root})
        self.assertIn("%2Bquestion%3Fequal%3D1", source.uri)
        self.assertEqual(store.uri_location(source.uri), source.location)
        with self.assertRaises(cache.Reject):
            store.uri_location(source.uri.replace("%2B", "%2b"))

    def test_file_pin_requires_both_discriminator_and_bound_metadata(self):
        source = store.Source.decode(BETA)
        pin = {
            "sourceKind": store.KIND,
            "storeSource": BETA,
            "sourceLocation": source.uri,
            "revision": "a" * 40,
        }
        self.assertEqual(store.pin_source(pin), source)
        self.assertIsNone(
            store.pin_source({"sourceLocation": "https://example.invalid/beta.git"})
        )
        for change in [
            {"sourceKind": "remoteSourceControl"},
            {"sourceKind": True},
            {"sourceLocation": source.location},
            {"revision": "a" * 64},
            {"revision": None},
            {"url": source.uri},
        ]:
            with self.subTest(change=change), self.assertRaises(cache.Reject):
                store.pin_source(pin | change)
        for key in ["sourceKind", "storeSource"]:
            partial = copy.deepcopy(pin)
            del partial[key]
            with self.assertRaises(cache.Reject):
                store.pin_source(partial)
        with self.assertRaises(cache.Reject):
            store.pin_source([])

    def test_direct_dataclass_cannot_bypass_boundary_before_tools(self):
        session = SimpleNamespace(run=lambda *a, **k: self.fail("tool must not run"))
        source = store.Source("/tmp/foreign", BETA["narHash"], "beta.git")
        with self.assertRaises(cache.Reject):
            store.inspect(
                source, "a" * 40, None, None, session, "git", "nix", label="outside"
            )


class MetadataPolicy(unittest.TestCase):
    def make_repository(self, base):
        root = Path(base).resolve()
        repo = root / "repo.git"
        repo.mkdir()
        for directory in ["objects", "objects/info", "objects/pack", "refs", "hooks"]:
            (repo / directory).mkdir(exist_ok=True)
        (repo / "HEAD").write_text("ref: refs/heads/master\n")
        (repo / "config").write_text(
            "[core]\n repositoryformatversion = 0\n filemode = true\n bare = true\n"
        )
        return store.Source(str(root), BETA["narHash"], "repo.git"), repo

    def test_metadata_context_restores_sdk_policy_even_on_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            original = {
                "HOME": "/caller-home",
                "PATH": "/caller-path",
                "GIT_CONFIG_GLOBAL": "/caller-global",
                "GIT_CONFIG_PARAMETERS": "caller-policy",
                "GIT_CONFIG_COUNT": "2",
                "GIT_CONFIG_KEY_0": "include.path",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/caller-alt",
                "NIX_REMOTE": "caller-store",
                "NIX_CONFIG": "caller-config",
                "LD_PRELOAD": "caller-loader",
            }
            session = SimpleNamespace(env=original, evidence=Path(directory))
            with self.assertRaises(KeyboardInterrupt):
                with store.metadata_environment(
                    session, Path(BETA["path"]) / "beta.git"
                ):
                    self.assertEqual(session.env["HOME"], original["HOME"])
                    self.assertEqual(session.env["GIT_CONFIG_NOSYSTEM"], "1")
                    self.assertEqual(session.env["GIT_NO_REPLACE_OBJECTS"], "1")
                    self.assertEqual(session.env["GIT_ALLOW_PROTOCOL"], "")
                    self.assertNotIn("GIT_CONFIG_PARAMETERS", session.env)
                    self.assertNotIn("GIT_CONFIG_COUNT", session.env)
                    self.assertNotIn("GIT_ALTERNATE_OBJECT_DIRECTORIES", session.env)
                    self.assertNotIn("NIX_REMOTE", session.env)
                    self.assertNotIn("LD_PRELOAD", session.env)
                    config = Path(session.env["GIT_CONFIG_GLOBAL"])
                    self.assertEqual(config.stat().st_mode & 0o777, 0o600)
                    self.assertIn(
                        str(Path(BETA["path"]) / "beta.git"), config.read_text()
                    )
                    raise KeyboardInterrupt()
            self.assertIs(session.env, original)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_branch_revision_expressions_refuse_before_layout_or_tools(self):
        source = store.Source.decode(BETA)
        invalid = [
            "",
            "master~1",
            "master^",
            "master@{0}",
            "master:other",
            "topic?",
            "topic*",
            "topic[",
            "topic\\x",
            "/topic",
            "topic/",
            "topic//fix",
            "topic/.hidden",
            "topic/fix.lock",
            "topic..fix",
            "topic.",
            "topic\t",
        ]
        session = SimpleNamespace(deadline=time.monotonic() + 5)
        with patch.object(store, "_check_layout") as layout:
            for branch in invalid:
                with self.subTest(branch=branch), self.assertRaises(ValueError):
                    store.inspect(
                        source,
                        "a" * 40,
                        branch,
                        None,
                        session,
                        "/never/git",
                        "/never/nix",
                        label="refusal",
                    )
            layout.assert_not_called()
        # Source-only positive: a literal slash-containing ref reaches the physical gate.
        with patch.object(
            store, "_check_layout", side_effect=RuntimeError("physical gate")
        ) as layout:
            with self.assertRaisesRegex(RuntimeError, "physical gate"):
                store.inspect(
                    source,
                    "a" * 40,
                    "topic/literal-fix",
                    None,
                    session,
                    "/never/git",
                    "/never/nix",
                    label="literal",
                )
            layout.assert_called_once()

    def test_capture_metadata_isolates_nix_and_restores_caller_on_failure(self):
        for fail in (None, "registration", "canonical"):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory:
                original = {
                    "NIX_REMOTE": "foreign://caller",
                    "GIT_CONFIG_COUNT": "invalid",
                }
                source = store.Source.decode(BETA)
                session = SimpleNamespace(env=original, evidence=Path(directory))
                observed = []

                def run(label, argv):
                    self.assertNotIn("NIX_REMOTE", session.env)
                    self.assertNotIn("GIT_CONFIG_COUNT", session.env)
                    observed.append(label)
                    if label.endswith(str(fail)):
                        raise KeyboardInterrupt()
                    data = (
                        {source.path: {"narHash": source.nar_hash, "references": []}}
                        if label.endswith("registration")
                        else {
                            "path": source.path,
                            "context": {source.path: {"path": True}},
                        }
                    )
                    return SimpleNamespace(stdout=json.dumps(data).encode())

                session.run = run
                api = SimpleNamespace(tool=lambda name: "/owned/" + name)
                with patch.object(store, "inspect", return_value=source):
                    if fail is None:
                        self.assertEqual(
                            store.capture(
                                source.location,
                                "a" * 40,
                                None,
                                None,
                                session,
                                api,
                                label="control",
                            ),
                            source,
                        )
                    else:
                        with self.assertRaises(KeyboardInterrupt):
                            store.capture(
                                source.location,
                                "a" * 40,
                                None,
                                None,
                                session,
                                api,
                                label="control",
                            )
                self.assertIs(session.env, original)
                self.assertEqual(len(observed), 1 if fail == "registration" else 2)
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_closed_layout_refuses_callbacks_indirection_and_partial_objects(self):
        changes = [
            lambda repo: (repo / "config").write_bytes(b" " * (64 * 1024 + 1)),
            lambda repo: (repo / "config").write_text(
                "[core]\nrepositoryformatversion=0\nbare=true\nhooksPath=/foreign\n"
            ),
            lambda repo: (repo / "config").write_text(
                "[core]\nrepositoryformatversion=0\nbare=true\n[include]\npath=/foreign\n"
            ),
            lambda repo: (repo / "config").write_text(
                '[core]\nrepositoryformatversion=0\nbare=true\n[remote "origin"]\npromisor=true\n'
            ),
            lambda repo: (repo / "objects/info/alternates").write_text("/foreign\n"),
            lambda repo: (repo / "objects/pack/pack.promisor").write_text(""),
            lambda repo: (repo / "shallow").write_text("a" * 40 + "\n"),
            lambda repo: (repo / "hooks/pre-commit").write_text("foreign callback\n"),
            lambda repo: (repo / "objects/escape").symlink_to("/foreign"),
            lambda repo: (repo / "commondir").write_text("/foreign\n"),
        ]
        for change in changes:
            with (
                self.subTest(change=change),
                tempfile.TemporaryDirectory() as directory,
            ):
                source, repo = self.make_repository(directory)
                self.assertEqual(
                    store._check_layout(source, time.monotonic() + 5), repo
                )
                change(repo)
                with self.assertRaises(cache.Reject):
                    store._check_layout(source, time.monotonic() + 5)

    def test_git_tree_refuses_unbound_inputs_and_malformed_records(self):
        oid = b"a" * 40
        store.validate_tree(b"100644 blob " + oid + b"\tSources/main.swift\0")
        store.validate_tree(b"100755 blob " + oid + b"\tbuild.sh\0")
        for bad in [
            b"160000 commit " + oid + b"\tSubmodule\0",
            b"120000 blob " + oid + b"\tlink\0",
            b"100644 blob " + oid + b"\t.gitmodules\0",
            b"100644 blob " + oid + b"\tNested/.GIT/config\0",
            b"100644 blob " + oid + b"\t../outside\0",
            b"100644 blob " + oid + b"\t/absolute\0",
            b"100644 blob " + oid + b"\tpath//alias\0",
            b"bad record\0",
        ]:
            with self.subTest(bad=bad), self.assertRaises(cache.Reject):
                store.validate_tree(bad)


class AuthenticCodecs(unittest.TestCase):
    def test_observed_local_kind_tag_branch_revision_and_identity_refusals(self):
        import workspace_v7_pins2 as pins2
        import workspace_v7_pins3 as pins3

        fixture = (
            Path(__file__).resolve().parents[2] / "tests/fixtures/workspace7-store-git"
        )
        sources = {"alpha": ALPHA, "beta": BETA}
        hashes = {
            "alpha": "sha256-IhLx2ZVsduMMSGadNgJZ+bH7djWu8u3j0Ue6UCKnrGg=",
            "beta": "sha256-Csx98D7OFnUzpptxvyzM8kHvDIJtEMkwTmfPp4xFRfo=",
        }
        for case in sorted(p for p in fixture.iterdir() if p.is_dir()):
            codec = pins3 if case.name.startswith("60") else pins2
            with (
                self.subTest(case=case.name),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                (root / "Package.swift").write_bytes(
                    (case / "Package.swift").read_bytes()
                )
                state = json.loads((case / "workspace-state.json").read_text())
                selected = json.loads((case / "Package.resolved").read_text())
                pins = {}
                for row in state["object"]["dependencies"]:
                    ref = row["packageRef"]
                    revision = row["state"]["checkoutState"]
                    identity = ref["identity"]
                    source = store.Source.decode(sources[identity])
                    pins[identity] = {
                        "name": ref["name"],
                        "sourceKind": store.KIND,
                        "storeSource": source.encode(),
                        "sourceLocation": source.uri,
                        "revision": revision["revision"],
                        "branch": revision.get("branch"),
                        "version": revision.get("version"),
                        "hash": hashes[identity],
                    }
                lock = {
                    "version": 12 if codec is pins3 else 13,
                    "workspaceStateVersion": 7,
                    "package": "PureApp",
                    "rootDirectory": "caller",
                    "manifestHash": codec.manifest_hash(root, []),
                    "pins": pins,
                    "localDependencies": [],
                    "binaryTargets": [],
                    "products": [{"name": "PureApp", "type": "executable"}],
                }
                if codec is pins3:
                    lock["originHash"] = selected["originHash"]
                lock["contentHash"] = codec.content_hash(lock)
                codec.validate_lock(lock, selected, root)
                self.assertEqual(
                    codec.native_index(codec.checkout_records(lock, root), root),
                    codec.native_index(state, root),
                )
                for field, value in [
                    ("sourceKind", "remoteSourceControl"),
                    ("url", pins["alpha"]["sourceLocation"]),
                    ("storeSource", ALPHA | {"path": BETA["path"]}),
                ]:
                    bad = copy.deepcopy(lock)
                    bad["pins"]["alpha"][field] = value
                    bad["contentHash"] = codec.content_hash(bad)
                    with self.assertRaises(ValueError):
                        codec.validate_lock(bad, selected, root)
                wrong = copy.deepcopy(selected)
                wrong["pins"][0]["kind"] = "remoteSourceControl"
                with self.assertRaises(ValueError):
                    codec.validate_lock(lock, wrong, root)


if __name__ == "__main__":
    unittest.main()
