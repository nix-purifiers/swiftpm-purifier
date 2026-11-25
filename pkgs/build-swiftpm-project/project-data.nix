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
  git ? null,
  nix ? null,
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
}@args:
let
  read = import ../../lib-overlays/default/lock.nix (
    {
      inherit
        src
        lockFile
        selectedSDK
        ;
    }
    // (if args ? packageResolved then { inherit packageResolved; } else { })
  );
  current = builtins.seq (purifier.checkLock read.lock) true;
  storeSources = read.storeGitSources or [ ];
  validatedSources = map (binding: {
    inherit binding;
    validation =
      assert git != null && nix != null;
      (import ./store-git-validation.nix {
        inherit
          runCommand
          python3
          git
          nix
          ;
      })
        binding;
  }) storeSources;
  # File inputs always use the real fetcher. Caller fetcher overrides still apply
  # to the existing remote kinds; neither an override nor a cached FOD skips validation.
  boundFetchgit =
    arguments:
    let
      matches = builtins.filter (row: row.binding.uri == arguments.url) validatedSources;
      ordinary = fetchers.fetchgit or fetchgit;
    in
    if matches == [ ] then
      ordinary arguments
    else
      assert builtins.length matches == 1;
      (fetchgit arguments).overrideAttrs (_: {
        storeGitValidation = (builtins.head matches).validation;
      });

  tree =
    assert current;
    if read.lockVersion == 7 then
      assert git != null && nix != null;
      import ./pre-workspace-tree.nix {
        inherit
          read
          fetchgit
          fetchers
          runCommand
          python3
          git
          nix
          ;
      }
    else
      import (if read.lockVersion == 8 then ./dependencies-v1-tree.nix else ./tree.nix) (
        {
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
            // (if storeSources == [ ] then { } else { fetchgit = boundFetchgit; })
          );
          patchArtifact = callPackage ./patch-artifact.nix { buildInputs = artifactBuildInputs; };
        }
        // (if read.lockVersion == 8 then { inherit python3; } else { })
        // (
          if storeSources == [ ] then
            { }
          else
            {
              storeGitValidations = map (row: row.validation) validatedSources;
              storeGitBindings = map (binding: binding.declaration) storeSources;
            }
        )
      );
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
