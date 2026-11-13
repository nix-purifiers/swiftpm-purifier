# SPDX-License-Identifier: MIT
# Codec-only fixture: synthetic hashes must never be used as build evidence.
{ pkgs }:
let
  src = ./fixtures/dependencies-v3;
  base = builtins.fromJSON (builtins.readFile (src + "/swiftpm-purifier.lock.json"));
  native = builtins.fromJSON (builtins.readFile (src + "/Package.resolved"));
  rehash =
    value:
    value
    // {
      contentHash = builtins.convertHash {
        hash = builtins.hashString "sha256" (
          builtins.toJSON (builtins.removeAttrs value [ "contentHash" ])
        );
        hashAlgo = "sha256";
        toHashFormat = "sri";
      };
    };
  file = name: value: builtins.toFile name (builtins.toJSON value);
  read =
    value: selected:
    import ../lib-overlays/default/lock.nix {
      inherit src;
      lockFile = file "portable9.json" value;
      packageResolved = selected;
    };
  pinsFile = file "selected-pins.json" native;
  valid = read base pinsFile;
  refuses = value: (read (rehash value) pinsFile).lock.problems != [ ];
  alteredPin = change: base // { pins.alpha = base.pins.alpha // change; };
  alteredLocal =
    change:
    base
    // {
      localDependencies = [ ((builtins.head base.localDependencies) // change) ];
    };
  alteredLocalRef =
    change:
    alteredLocal {
      packageRef = (builtins.head base.localDependencies).packageRef // change;
    };
  selectedRefuses = value: (read base (file "wrong-selected-pins.json" value)).lock.problems != [ ];
  duplicate = builtins.replaceStrings [ ''"version":9'' ] [ ''"version":9,"version":9'' ] (
    builtins.toJSON base
  );
  duplicateRead = import ../lib-overlays/default/lock.nix {
    inherit src;
    lockFile = builtins.toFile "duplicate-root9.json" duplicate;
    packageResolved = pinsFile;
  };
  remote = builtins.elemAt valid.workspaceState.object.dependencies 1;
  checks = {
    valid = valid.lock.problems == [ ];
    correctFile = valid.workspaceStateFile == "dependencies-state.json";
    actualNativeVersion = valid.workspaceState.version == 3;
    noInventedArtifacts = builtins.attrNames valid.workspaceState.object == [ "dependencies" ];
    exactReference =
      builtins.attrNames remote.packageRef == [
        "identity"
        "kind"
        "name"
        "path"
      ];
    editedName = remote.subpath == "AlphaPkg";
    originalCheckoutPath = remote.basedOn.subpath == "Alpha";
    actualKind = remote.packageRef.kind == "remote";
    localRecordPreserved =
      builtins.head valid.workspaceState.object.dependencies == builtins.head base.localDependencies;
    v2Refused = refuses (base // { workspaceStateVersion = 2; });
    booleanNativeRefused = refuses (base // { workspaceStateVersion = true; });
    booleanRootRefused = refuses (base // { version = true; });
    extraSDKRefused = refuses (base // { nativeSDK = { }; });
    unknownRootRefused = refuses (base // { version = 999; });
    old4State3Refused = refuses (base // { version = 4; });
    old5State3Refused = refuses (base // { version = 5; });
    wrongPinNameRefused = refuses (alteredPin {
      name = "OtherPkg";
    });
    wrongPinRevisionRefused = refuses (alteredPin {
      revision = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    });
    wrongPinVersionRefused = refuses (alteredPin {
      version = "1.0.1";
    });
    wrongPinURLRefused = refuses (alteredPin {
      sourceLocation = "https://example.invalid/Other.git";
    });
    extraPinKindRefused = refuses (alteredPin {
      kind = "remote";
    });
    invalidHashRefused = refuses (alteredPin {
      hash = "not-a-hash";
    });
    localEscapeRefused = refuses (alteredLocalRef {
      path = "@root@/../escape";
    });
    localModernSchemaRefused = refuses (alteredLocalRef {
      location = "@root@/LocalPkg";
    });
    localWrongKindRefused = refuses (alteredLocalRef {
      kind = "fileSystem";
    });
    localWrongSubpathRefused = refuses (alteredLocal {
      subpath = "LocalPkg";
    });
    combinedIdentityAliasRefused = refuses (alteredLocalRef {
      identity = "alpha";
    });
    combinedNameAliasRefused = refuses (alteredLocalRef {
      name = "AlphaPkg";
    });
    localManifestDomainBound = refuses (base // { localDependencies = [ ]; });
    nullPinsRefused = (read base null).lock.problems != [ ];
    wrongPinsVersionRefused = selectedRefuses (native // { version = 2; });
    booleanPinsVersionRefused = selectedRefuses (native // { version = true; });
    duplicateRootRefused = duplicateRead.lock.problems != [ ];
  };
in
{
  inherit checks;
  test =
    assert builtins.all (name: checks.${name}) (builtins.attrNames checks);
    pkgs.runCommand "swiftpm-dependencies-v3-codec" { } "touch $out";
}
