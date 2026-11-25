# SPDX-License-Identifier: MIT
# Source the actual selected SDK environment with absent, canonical, and
# foreign caller store values. No compiler or caller configuration changes.
{ pkgs, environment }:
let
  otherStore = "/declared-other-store";
  # Exercise the recipe with another declared store without realizing an
  # SDK or pretending the host's physical store moved.
  customRecipe =
    builtins.scopedImport
      {
        builtins = builtins // {
          storeDir = otherStore;
        };
      }
      ../pkgs/swift-sdk/swift-environment.nix
      {
        inherit (pkgs) lib;
        swiftPackages = {
          stdenv = {
            mkDerivation = attrs: attrs;
            hostPlatform.isDarwin = false;
          };
          swift = "/owned-unused-swift";
          swiftpm = "/owned-unused-swiftpm";
        };
        git = "/owned-unused-git";
        unzip = "/owned-unused-unzip";
      };
in
pkgs.runCommand "swift-sdk-store-environment"
  {
    customInstallPhase = customRecipe.installPhase;
    passAsFile = [ "customInstallPhase" ];
  }
  ''
    for mode in absent canonical foreign; do
      if [ "$mode" = absent ]; then
        env -u NIX_STORE ${pkgs.bash}/bin/bash --noprofile --norc -c '
          source "$1"
          test "$NIX_STORE" = "$2"
        ' owned-sdk ${environment} ${pkgs.lib.escapeShellArg builtins.storeDir}
      else
        initial=${pkgs.lib.escapeShellArg builtins.storeDir}
        if [ "$mode" = foreign ]; then initial=/owned-foreign-store; fi
        NIX_STORE="$initial" ${pkgs.bash}/bin/bash --noprofile --norc -c '
          source "$1"
          test "$NIX_STORE" = "$2"
        ' owned-sdk ${environment} ${pkgs.lib.escapeShellArg builtins.storeDir}
      fi
    done
    # This is synthetic exporter coverage, not a nondefault physical store.
    env out="$TMPDIR/custom-environment" NIX_STORE=/foreign-caller-store \
      ${pkgs.bash}/bin/bash --noprofile --norc "$customInstallPhasePath"
    ${pkgs.bash}/bin/bash --noprofile --norc -c '
      source "$1"
      test "$NIX_STORE" = "$2"
    ' owned-sdk "$TMPDIR/custom-environment" ${pkgs.lib.escapeShellArg otherStore}
    touch "$out"
  ''
