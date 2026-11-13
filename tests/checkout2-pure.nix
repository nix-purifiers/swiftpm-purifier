# SPDX-License-Identifier: MIT
# Explicit original binary SDK fixture. No default toolchain reconstruction.
{
  sourcePath,
  selector ? "default",
  nativePath ? "",
  configuration ? "release",
  sdkEpoch ? "swift413",
}:
let
  fixture = builtins.getFlake ("path:" + toString ../.);
  pkgs = import fixture.inputs.nixpkgs { };
  purifier = import (fixture.inputs.purifier + "/nix/lib") {
    inherit (pkgs) lib;
  };
  swiftPackages =
    if sdkEpoch == "swift413" then
      import ./sdk/swift413/package-set.nix { inherit pkgs; }
    else if sdkEpoch == "swift503" then
      import ./sdk/swift503/package-set.nix { inherit pkgs; }
    else if sdkEpoch == "swift515" then
      import ./sdk/swift515/package-set.nix { inherit pkgs; }
    else if sdkEpoch == "swift525" then
      import ./sdk/swift525/package-set.nix { inherit pkgs; }
    else if sdkEpoch == "swift533" then
      import ./sdk/swift533/package-set.nix { inherit pkgs; }
    else
      throw "unknown checkout2 test SDK fixture";
  builder = pkgs.callPackage ../pkgs/build-swiftpm-project { inherit swiftPackages purifier; };
  src = builtins.path {
    path = builtins.toPath sourcePath;
    name = "${sdkEpoch}-pure-source";
  };
  selection =
    if selector == "default" then
      { }
    else if selector == "null" then
      { packageResolved = null; }
    else if selector == "root" then
      { packageResolved = src + "/Package.resolved"; }
    else if selector == "external" then
      {
        packageResolved = builtins.path {
          path = builtins.toPath nativePath;
          name = "${sdkEpoch}-selected-Package.resolved";
        };
      }
    else
      throw "unknown pure fixture selector";
  result = builder (
    {
      inherit src;
      swiftpmPurifierProducts = pkgs.writeText "${sdkEpoch}-native-products.json" (
        builtins.toJSON
          (builtins.fromJSON (builtins.readFile (src + "/swiftpm-purifier.lock.json"))).products
      );
      buildConfig = configuration;
      # The source hash/revision is untouched; a distinct consumer fetch name
      # makes the live controlled origin observable independently of capture.
      fetchers.fetchgit =
        args: pkgs.fetchgit (args // { name = (args.name or "source") + "-${sdkEpoch}-pure-consumer"; });
    }
    // selection
  );
in
result // { inherit swiftPackages; }
