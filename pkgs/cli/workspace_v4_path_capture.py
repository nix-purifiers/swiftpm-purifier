# SPDX-License-Identifier: MIT
"""Read-only capture bound to workspace4/PATH, before typed publication."""

import dependencies_v1_selectors as selectors
import dependencies_v3_capture as shared
from native_path_protocols import WORKSPACE_V4_PATH


def native_graph(root, environment=None):
    return shared.native_graph(root, environment, native_protocol=WORKSPACE_V4_PATH)


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
        native_protocol=WORKSPACE_V4_PATH,
    )
