# SPDX-License-Identifier: MIT
"""Declaration identity controls independent of native version strings."""

import unittest
import dependencies_v1_manifest as manifest


class Manifest(unittest.TestCase):
    def setUp(self):
        self.alpha = "https://example.invalid/A.git"
        self.beta = "https://example.invalid/B.git"
        self.dependencies = {
            self.alpha: {
                "package": "AlphaPkg",
                "products": [
                    {"name": "Common", "type": "library", "targets": ["Alpha"]}
                ],
            },
            self.beta: {
                "package": "BetaPkg",
                "products": [
                    {"name": "Common", "type": "library", "targets": ["Beta"]}
                ],
            },
        }
        self.graph = {"targets": [{"name": "One"}]}

    def description(self, reference):
        return {
            "dependencies": [{"url": self.alpha}, {"url": self.beta}],
            "targets": [{"name": "One", "dependencies": [reference]}],
        }

    def test_package_name_qualifies_instead_of_repository_basename(self):
        reference = {"type": "product", "name": "Common", "package": "AlphaPkg"}
        result = manifest.join_references(
            self.description(reference), self.graph, self.dependencies
        )
        self.assertEqual(result[0]["candidate"]["URL"], self.alpha)
        reference["package"] = "A"
        with self.assertRaises(manifest.plan.Reject):
            manifest.join_references(
                self.description(reference), self.graph, self.dependencies
            )

    def test_unqualified_and_byname_ambiguity_refuse(self):
        for reference in (
            {"type": "product", "name": "Common", "package": None},
            {"type": "byname", "name": "Common"},
        ):
            with self.assertRaises(manifest.plan.Reject):
                manifest.join_references(
                    self.description(reference), self.graph, self.dependencies
                )

    def test_transitive_inventory_does_not_supply_undeclared_product(self):
        description = self.description(
            {"type": "product", "name": "OnlyBeta", "package": None}
        )
        description["dependencies"] = [{"url": self.alpha}]
        self.dependencies[self.beta]["products"][0]["name"] = "OnlyBeta"
        with self.assertRaises(manifest.plan.Reject):
            manifest.join_references(description, self.graph, self.dependencies)

    def test_unknown_reference_fields_and_local_targets_refuse(self):
        for reference in (
            {"type": "target", "name": "Missing"},
            {
                "type": "product",
                "name": "Common",
                "package": "AlphaPkg",
                "guessed": True,
            },
        ):
            with self.assertRaises(manifest.plan.Reject):
                manifest.join_references(
                    self.description(reference), self.graph, self.dependencies
                )

    def test_local_reference_uses_actual_local_target_namespace(self):
        for kind in ("target", "byname"):
            self.assertEqual(
                manifest.join_references(
                    self.description({"type": kind, "name": "One"}),
                    self.graph,
                    self.dependencies,
                ),
                [],
            )


if __name__ == "__main__":
    unittest.main()
