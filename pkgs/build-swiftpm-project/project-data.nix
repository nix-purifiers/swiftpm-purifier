# SPDX-License-Identifier: MIT
# Dependency/source data only. No Swift package set or mkDerivation function
# is recreated by CLI fetch/publish operations.
{
  runCommand,
  zip,
  unzip,
  zstd,
  python3,
  writeText,
  fetchurl,
  fetchgit,
  callPackage,
  purifier,
}:
{
  src,
  platform,
  selectedSDK ? null,
  fetchers ? { },
  lockFile ? src + "/swiftpm-purifier.lock.json",
  packageResolved ? null,
  artifactBuildInputs ? [ ],
}:
let
  read = import ../../lib-overlays/default/lock.nix {
    inherit
      src
      lockFile
      packageResolved
      selectedSDK
      ;
  };
  current = builtins.seq (purifier.checkLock read.lock) true;
  tree =
    assert current;
    import ./tree.nix {
      inherit read runCommand;
      entries = purifier.entriesFor platform read.lock;
      fetchEntry = purifier.fetchEntry (
        {
          inherit
            runCommand
            unzip
            zstd
            python3
            writeText
            fetchurl
            fetchgit
            ;
        }
        // fetchers
      );
      patchArtifact = callPackage ./patch-artifact.nix { buildInputs = artifactBuildInputs; };
    };
  release =
    assert current;
    import ./source-archive.nix { inherit runCommand zip; } {
      inherit src;
      name = read.package;
    };
in
{
  inherit
    read
    current
    tree
    release
    ;
}
