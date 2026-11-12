# SPDX-License-Identifier: MIT
# Explicit original binary SDK fixture. No default toolchain reconstruction.
{
  sourcePath,
  selector ? "default",
  nativePath ? "",
  configuration ? "release",
}:
let
  pkgs = import /nix/store/cm5m4hjjh37s6z82rwa0vhkbcga3m8bq-source { };
  purifier = import /nix/store/iirdlbmpdxf9539j0h0ww94rd69n5n4r-source/nix/lib {
    inherit (pkgs) lib;
  };
  swiftPackages = import ./sdk/swift403/package-set.nix { inherit pkgs; };
  builder = pkgs.callPackage ../pkgs/build-swiftpm-project { inherit swiftPackages purifier; };
  src = builtins.path {
    path = builtins.toPath sourcePath;
    name = "swift403-pure-source";
  };
  selection =
    if selector == "default" then
      { }
    else if selector == "null" then
      { packageResolved = null; }
    else if selector == "root" then
      { packageResolved = src + "/Package.resolved"; }
    else if selector == "external" then
      { packageResolved = builtins.toPath nativePath; }
    else
      throw "unknown pure fixture selector";
  result = builder (
    {
      inherit src;
      buildConfig = configuration;
      # The source hash/revision is untouched; a distinct consumer fetch name
      # makes the live controlled origin observable independently of capture.
      fetchers.fetchgit =
        args: pkgs.fetchgit (args // { name = (args.name or "source") + "-swift403-pure-consumer"; });
    }
    // selection
  );
in
result // { inherit swiftPackages; }
