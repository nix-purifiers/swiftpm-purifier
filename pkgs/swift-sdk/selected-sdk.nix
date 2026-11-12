# SPDX-License-Identifier: MIT
# Identifies the actual package set supplied through the existing override.
# These are concrete derivations and executable paths, never serialized
# mkDerivation functions or a reconstructed default Swift package set.
{
  lib,
  writeText,
  swiftPackages,
  git,
  unzip,
}:
let
  environment = import ./swift-environment.nix {
    inherit
      lib
      swiftPackages
      git
      unzip
      ;
  };
  component =
    name: value:
    if lib.isDerivation value then
      {
        path = toString value;
        # Retain the recipe itself without asking Nix to realize every output
        # (including unrelated documentation/debug/build-time outputs).
        recipe = builtins.unsafeDiscardOutputDependency value.drvPath;
      }
    else
      throw "swiftpm-purifier: selected ${name} has no concrete Nix derivation recipe; this SDK environment cannot be reproduced";
  descriptor = {
    version = 1;
    system = swiftPackages.stdenv.hostPlatform.system;
    environmentShell = swiftPackages.stdenv.shell;
    components = {
      swift = component "swift" swiftPackages.swift;
      swiftpm = component "swiftpm" swiftPackages.swiftpm;
      stdenv = component "stdenv" swiftPackages.stdenv;
      environment = component "environment" environment;
    };
    executables = {
      swift = "${swiftPackages.swift}/bin/swift";
      swiftc = "${swiftPackages.swift}/bin/swiftc";
      package = "${swiftPackages.swiftpm}/bin/swift-package";
      build = "${swiftPackages.swiftpm}/bin/swift-build";
      test = "${swiftPackages.swiftpm}/bin/swift-test";
      run = "${swiftPackages.swiftpm}/bin/swift-run";
    };
  };
in
{
  inherit environment descriptor;
  roots = [
    swiftPackages.swift
    swiftPackages.swiftpm
    swiftPackages.stdenv
    environment
  ]
  ++ [
    (builtins.appendContext (builtins.head (builtins.match "(/nix/store/[^/]+)(/.*)?" descriptor.environmentShell)) (
      builtins.getContext descriptor.environmentShell
    ))
  ];
  file = writeText "swiftpm-selected-sdk.json" (builtins.toJSON descriptor);
}
