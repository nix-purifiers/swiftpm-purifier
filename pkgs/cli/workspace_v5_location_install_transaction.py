# SPDX-License-Identifier: MIT
"""Two fixed owned roles for workspace5/LOCATION; no cross-protocol conversion."""

from dependencies_v3_install_transaction import Installation as SharedInstallation
from native_protocols import WORKSPACE_V5_LOCATION


class Installation(SharedInstallation):
    NATIVE_PROTOCOL = WORKSPACE_V5_LOCATION
    PROTOCOL = WORKSPACE_V5_LOCATION.name
    INSTALL_OPERATION = "owned-install-workspace-v5-location-pins1"
