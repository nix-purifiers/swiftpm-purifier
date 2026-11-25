# SPDX-License-Identifier: MIT
"""Select a closed codec from a literal descriptor, never from discovery."""

import dependencies_v3
import native_protocols as protocols
import workspace_v4_path


def codec(protocol):
    protocol = protocols.checked(protocol)
    if protocol is protocols.DEPENDENCIES_V3:
        return dependencies_v3
    if protocol is protocols.WORKSPACE_V5_LOCATION:
        import workspace_v5_location

        return workspace_v5_location
    if protocol is protocols.WORKSPACE_V7_PINS3:
        import workspace_v7_pins3

        return workspace_v7_pins3
    if protocol is protocols.WORKSPACE_V7_PINS2:
        import workspace_v7_pins2

        return workspace_v7_pins2
    return workspace_v4_path


def ownership(protocol):
    protocol = protocols.checked(protocol)
    if protocol is protocols.WORKSPACE_V7_PINS2:
        import workspace_v7_pins2_ownership

        return workspace_v7_pins2_ownership
    if protocol is protocols.WORKSPACE_V7_PINS3:
        import workspace_v7_pins3_ownership

        return workspace_v7_pins3_ownership
    if protocol is protocols.DEPENDENCIES_V3:
        import dependencies_v3_ownership

        return dependencies_v3_ownership
    if protocol is protocols.WORKSPACE_V5_LOCATION:
        import workspace_v5_location_ownership

        return workspace_v5_location_ownership
    import workspace_v4_path_ownership

    return workspace_v4_path_ownership


def installation(protocol):
    protocol = protocols.checked(protocol)
    if protocol is protocols.DEPENDENCIES_V3:
        from dependencies_v3_install_transaction import Installation
    elif protocol is protocols.WORKSPACE_V5_LOCATION:
        from workspace_v5_location_install_transaction import Installation
    elif protocol is protocols.WORKSPACE_V7_PINS3:
        from workspace_v7_pins3_install_transaction import Installation
    elif protocol is protocols.WORKSPACE_V7_PINS2:
        from workspace_v7_pins2_install_transaction import Installation
    else:
        from workspace_v4_path_install_transaction import Installation
    return Installation


def bridge(protocol):
    protocol = protocols.checked(protocol)
    if protocol is protocols.DEPENDENCIES_V3:
        from dependencies_v3_transaction import Bridge
    elif protocol is protocols.WORKSPACE_V5_LOCATION:
        from workspace_v5_location_transaction import Bridge
    elif protocol is protocols.WORKSPACE_V7_PINS3:
        from workspace_v7_pins3_transaction import Bridge
    elif protocol is protocols.WORKSPACE_V7_PINS2:
        from workspace_v7_pins2_transaction import Bridge
    else:
        from workspace_v4_path_transaction import Bridge
    return Bridge
