# SPDX-License-Identifier: MIT
# Original SDK plus source-owned ordinary Nix build hooks. No compiler,
# SwiftPM implementation, feature answers, or release strings are changed.
{ pkgs }:
let
  sdk = import ./default.nix { inherit pkgs; };
  hook = pkgs.makeSetupHook {
    name = "original-swift525-build-hook";
    substitutions = {
      python = pkgs.lib.getExe pkgs.python3;
      clang = "${sdk}/bin/clang";
    };
  } ./setup-hook.sh;
  swiftpm =
    pkgs.runCommand "original-swiftpm-5.2.0-with-build-hook"
      {
        passthru.version = "5.2.0";
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
