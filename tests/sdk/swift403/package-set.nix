# SPDX-License-Identifier: MIT
# Original SDK plus source-owned ordinary Nix build hooks. No compiler,
# SwiftPM implementation, feature answers, or release strings are changed.
{ pkgs }:
let
  sdk = import ./default.nix { inherit pkgs; };
  existing = "/nix/store/1hnldmhza7zqfb6m12m9q3lmzki2z0x3-original-swift-sdk-4.0.3";
  hook = pkgs.makeSetupHook {
    name = "original-swift403-build-hook";
    substitutions = {
      python = pkgs.lib.getExe pkgs.python3;
      clang = "${pkgs.clang}/bin/clang";
    };
  } ./setup-hook.sh;
  swiftpm =
    pkgs.runCommand "original-swiftpm-4.0.0-dev-with-build-hook"
      {
        passthru.version = "4.0.0-dev";
      }
      ''
        mkdir -p "$out/nix-support"
        ln -s ${hook}/nix-support/setup-hook "$out/nix-support/setup-hook"
        ln -s ${sdk}/bin "$out/bin"
        ln -s ${sdk}/lib "$out/lib"
      '';
in
assert toString sdk == existing && builtins.pathExists existing;
{
  inherit (pkgs) stdenv;
  swift = sdk;
  inherit swiftpm;
}
