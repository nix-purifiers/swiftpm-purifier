# SPDX-License-Identifier: MIT
# Genuine SDK binaries with ordinary source-owned Nix build/install hooks.
{
  pkgs,
  packagerCPU ? null,
}:
let
  sdk = import ./default.nix { inherit pkgs packagerCPU; };
  hook = pkgs.makeSetupHook {
    name = "original-swift642-build-hook";
    substitutions = {
      python = pkgs.lib.getExe pkgs.python3;
      clang = "${sdk}/bin/clang";
    };
  } ./setup-hook.sh;
  swiftpm =
    pkgs.runCommand "original-swiftpm-6.4.2-with-build-hook"
      {
        passthru.version = sdk.version;
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
