# SPDX-License-Identifier: MIT
# Portable 10 is workspace4/PATH, distinct from workspace4/LOCATION.
args:
import ./dependencies-v3-state.nix (args // { nativePathProtocol = "workspace-state-v4-path"; })
