# SPDX-License-Identifier: MIT
"""Synthetic journal inputs based on the measured LOCATION/pins1 geometry.

The source digest is a unit-test placeholder, never native NAR evidence.
Retained predecessor envelopes test exact byte rollback, not old-codec admission.
"""

from pathlib import Path
import tempfile
import workspace_v5_location as protocol
import test_workspace_v5_location as model_fixtures


class WireFixture:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "Package.swift").write_text("// root model fixture\n")
        (self.root / "Local").mkdir()
        (self.root / "Local/Package.swift").write_text("// local model fixture\n")
        fixture = model_fixtures.Workspace5Location()
        fixture.root = self.root
        self.lock, self.selected = fixture.lock("tag")
        self.local_record = protocol.local_records(self.lock, self.root)[0]

    def rehash(self, value):
        value["contentHash"] = protocol.content_hash(value)


def path_lock(value, root, version=9):
    """Build independently valid older PATH model inputs for foreign journals."""
    import copy
    import dependencies_v3
    import workspace_v4_path

    old = dependencies_v3 if version == 9 else workspace_v4_path
    value = copy.deepcopy(value)
    value.update(version=version, workspaceStateVersion=old.NATIVE_VERSION)
    for pin in value["pins"].values():
        assert pin.pop("branch") is None and pin["version"] is not None
    for record in value["localDependencies"]:
        ref = record["packageRef"]
        ref["path"] = ref.pop("location")
        ref["kind"] = "local"
        record["state"] = {"name": "local"}
    value["manifestHash"] = old.manifest_hash(root, old.local_records(value, root))
    value["contentHash"] = old.content_hash(value)
    return value
