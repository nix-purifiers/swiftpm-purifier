# SPDX-License-Identifier: MIT
"""Read-only capture bound to workspace7/LOCATION/pins3, before typed publication."""

import dependencies_v1_selectors as selectors
import dependencies_v3_capture as shared
from native_protocols import WORKSPACE_V7_PINS3


def native_graph(root, environment=None):
    return shared.native_graph(root, environment, native_protocol=WORKSPACE_V7_PINS3)


def prepare(
    root,
    temporary,
    api,
    update=None,
    *,
    native=selectors.DEFAULT,
    portable=selectors.DEFAULT,
):
    return shared.prepare(
        root,
        temporary,
        api,
        update,
        native=native,
        portable=portable,
        native_protocol=WORKSPACE_V7_PINS3,
    )
