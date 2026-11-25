# SPDX-License-Identifier: MIT
{ pkgs }:
pkgs.runCommand "swiftpm-module-install-layouts"
  {
    nativeBuildInputs = [
      pkgs.python3
      pkgs.coreutils
      pkgs.bash
    ];
  }
  ''
    python3 ${./module-layout.py} \
      --builder ${../pkgs/build-swiftpm-project/default.nix} \
      --hook ${./sdk/swift642/setup-hook.sh} --bash ${pkgs.bash}/bin/bash > "$out"
  ''
