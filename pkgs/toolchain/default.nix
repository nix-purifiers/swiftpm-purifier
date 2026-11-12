# SPDX-License-Identifier: MIT
#
# A Swift toolchain whose `swift` runs through `swiftpm-purifier swift`:
# in a package with an up-to-date lockfile, SwiftPM builds, tests and runs
# on the locked dependencies, offline. The rest of the toolchain (swiftc,
# the SwiftPM subcommands, and sourcekit-lsp, the language server editors
# use) comes from nixpkgs unchanged; sourcekit-lsp reads the locked
# dependencies from the same links.
{
  lib,
  symlinkJoin,
  writeShellScriptBin,
  swiftPackages,
  cli,
}:
let
  selectedCLI =
    if lib.isDerivation cli && cli ? override then
      cli.override { inherit swiftPackages; }
    else
      throw "swiftpm-purifier: toolchain CLI has no source-owned SDK override recipe";
  swift = writeShellScriptBin "swift" ''
    exec ${selectedCLI}/bin/swiftpm-purifier swift "$@"
  '';
in
assert selectedCLI.nativeSDKDescriptor.components.swift.path == toString swiftPackages.swift;
assert selectedCLI.nativeSDKDescriptor.components.swiftpm.path == toString swiftPackages.swiftpm;
assert selectedCLI.nativeSDKDescriptor.components.stdenv.recipe == swiftPackages.stdenv.drvPath;
symlinkJoin {
  name = "swiftpm-purifier-toolchain";
  paths = [
    swift
    selectedCLI
    swiftPackages.swift
    swiftPackages.swiftpm
  ]
  ++ lib.optional (
    swiftPackages ? sourcekit-lsp && swiftPackages.sourcekit-lsp != null
  ) swiftPackages.sourcekit-lsp;
  meta.mainProgram = "swift";
}
