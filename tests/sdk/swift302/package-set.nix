# SPDX-License-Identifier: MIT
# Original SDK plus source-owned ordinary Nix build hooks. No compiler,
# SwiftPM implementation, feature answers, or release strings are changed.
{ pkgs }:
let
  sdk = import ./default.nix { inherit pkgs; };
  hook = pkgs.makeSetupHook {
    name = "original-swift302-build-hook";
    substitutions = {
      python = pkgs.lib.getExe pkgs.python3;
    };
  } ./setup-hook.sh;
  swiftpm =
    pkgs.runCommand "original-swiftpm-3.0.2-with-build-hook"
      {
        passthru.version = "3.0.2";
      }
      ''
        mkdir -p "$out/nix-support"
        ln -s ${hook}/nix-support/setup-hook "$out/nix-support/setup-hook"
        ln -s ${sdk}/bin "$out/bin"
        ln -s ${sdk}/lib "$out/lib"
      '';
in
{
  inherit (pkgs) stdenv;
  swift = sdk;
  inherit swiftpm;
}
