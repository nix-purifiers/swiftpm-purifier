# SPDX-License-Identifier: MIT
"""Cache metadata rejection controls; synthetic inputs make no Git object claim."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import dependencies_v1_cache as cache


class Cache(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.subpath = "Leaf.git--opaque"
        self.bare = self.root / ".build/repositories" / self.subpath
        self.metadata = self.root / ".build/checkouts" / self.subpath / ".git"
        (self.bare / "objects").mkdir(parents=True)
        (self.metadata / "objects/info").mkdir(parents=True)
        (self.metadata / "objects/info/alternates").write_text(
            str(self.bare / "objects") + "\n"
        )
        url = "https://example.invalid/Leaf.git"
        state = {
            "version": 1,
            "object": {
                "dependencies": [
                    {
                        "basedOn": None,
                        "name": "Leaf",
                        "repositoryURL": url,
                        "state": {
                            "name": "checkout",
                            "checkoutState": {
                                "branch": None,
                                "revision": "a" * 40,
                                "version": "1.0.0",
                            },
                        },
                        "subpath": self.subpath,
                    }
                ]
            },
        }
        registry = {
            "version": 1,
            "object": {
                "repositories": {
                    url: {
                        "repositoryURL": url,
                        "status": "available",
                        "subpath": self.subpath,
                    }
                }
            },
        }
        (self.root / ".build/dependencies-state.json").write_text(json.dumps(state))
        (self.root / ".build/repositories/checkouts-state.json").write_text(
            json.dumps(registry)
        )
        (self.metadata / "config").write_text(
            '[core]\n repositoryformatversion = 0\n bare = false\n[remote "origin"]\n url = '
            + str(self.bare)
            + "\n fetch = +refs/heads/*:refs/remotes/origin/*\n"
        )
        (self.bare / "config").write_text(
            '[core]\n repositoryformatversion = 0\n bare = true\n[remote "origin"]\n url = '
            + url
            + "\n fetch = +refs/*:refs/*\n"
        )

    def test_native_config_shape(self):
        self.assertEqual(len(cache.validate(self.root, {})), 1)

    def test_config_commands_and_pointer_sections_refuse(self):
        config = self.metadata / "config"
        original = config.read_text()
        for fragment in (
            "\n[include]\n path = /outside\n",
            "\n[credential]\n helper = !caller-command\n",
            '\n[remote "extra"]\n url = /outside\n',
            "\n[DEFAULT]\n sshcommand = caller-command\n",
        ):
            with self.subTest(fragment=fragment):
                config.write_text(original + fragment)
                with self.assertRaises(cache.Reject):
                    cache.validate(self.root, {})
        for key in ("sshCommand", "fsmonitor", "hooksPath", "worktree"):
            with self.subTest(key=key):
                config.write_text(
                    original.replace("[core]\n", "[core]\n " + key + " = /outside\n")
                )
                with self.assertRaises(cache.Reject):
                    cache.validate(self.root, {})

    def test_implicit_environment_is_actual_process_environment(self):
        with patch.dict(os.environ, {"GIT_OBJECT_DIRECTORY": ""}):
            with self.assertRaises(cache.Reject):
                cache.validate(self.root)
        self.assertEqual(len(cache.validate(self.root, {})), 1)

    def test_unsupported_branch_and_nonsemver_are_not_inferred(self):
        path = self.root / ".build/dependencies-state.json"
        original = json.loads(path.read_text())
        for key, value in [
            ("branch", "main"),
            ("version", None),
            ("version", "latest"),
        ]:
            data = json.loads(json.dumps(original))
            data["object"]["dependencies"][0]["state"]["checkoutState"][key] = value
            path.write_text(json.dumps(data))
            with self.subTest(key=key, value=value):
                with self.assertRaises(cache.Reject):
                    cache.validate(self.root, {})


if __name__ == "__main__":
    unittest.main()
