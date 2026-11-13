# SPDX-License-Identifier: MIT
"""Two fixed owned roles for workspace4/PATH; no cross-protocol conversion."""

from dependencies_v3_install_transaction import Installation as SharedInstallation
from native_path_protocols import WORKSPACE_V4_PATH


class Installation(SharedInstallation):
    NATIVE_PROTOCOL = WORKSPACE_V4_PATH
    PROTOCOL = WORKSPACE_V4_PATH.name
    INSTALL_OPERATION = "owned-install-workspace-v4-path"
