# SPDX-License-Identifier: MIT
"""Two fixed owned roles for workspace7/LOCATION; no cross-protocol conversion."""

from dependencies_v3_install_transaction import Installation as SharedInstallation
from native_protocols import WORKSPACE_V7_PINS2


class Installation(SharedInstallation):
    NATIVE_PROTOCOL = WORKSPACE_V7_PINS2
    PROTOCOL = WORKSPACE_V7_PINS2.name
    INSTALL_OPERATION = "owned-install-workspace-v7-location-pins2"
