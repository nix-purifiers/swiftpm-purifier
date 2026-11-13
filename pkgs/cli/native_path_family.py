# SPDX-License-Identifier: MIT
"""Select a closed codec from a literal descriptor, never from discovery."""

import dependencies_v3
import native_path_protocols as protocols
import workspace_v4_path


def codec(protocol):
    protocol = protocols.checked(protocol)
    if protocol is protocols.DEPENDENCIES_V3:
        return dependencies_v3
    return workspace_v4_path


def ownership(protocol):
    protocol = protocols.checked(protocol)
    if protocol is protocols.DEPENDENCIES_V3:
        import dependencies_v3_ownership

        return dependencies_v3_ownership
    import workspace_v4_path_ownership

    return workspace_v4_path_ownership


def installation(protocol):
    protocol = protocols.checked(protocol)
    if protocol is protocols.DEPENDENCIES_V3:
        from dependencies_v3_install_transaction import Installation
    else:
        from workspace_v4_path_install_transaction import Installation
    return Installation


def bridge(protocol):
    protocol = protocols.checked(protocol)
    if protocol is protocols.DEPENDENCIES_V3:
        from dependencies_v3_transaction import Bridge
    else:
        from workspace_v4_path_transaction import Bridge
    return Bridge
