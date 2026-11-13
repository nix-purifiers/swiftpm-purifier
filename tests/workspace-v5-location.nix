# SPDX-License-Identifier: MIT
# Codec-only fixture: synthetic hashes must never be used as build evidence.
{ pkgs }:
let
  src = ./fixtures/dependencies-v3;
  original = builtins.fromJSON (builtins.readFile (src + "/swiftpm-purifier.lock.json"));
  base = rehash (
    original
    // {
      version = 11;
      workspaceStateVersion = 5;
      pins = builtins.mapAttrs (_: pin: pin // { branch = null; }) original.pins;
      localDependencies = map (
        row:
        row
        // {
          packageRef = (builtins.removeAttrs row.packageRef [ "path" ]) // {
            kind = "fileSystem";
            location = row.packageRef.path;
          };
          state = {
            name = "fileSystem";
            path = row.packageRef.path;
          };
        }
      ) original.localDependencies;
    }
  );
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
      lockFile = file "portable11.json" value;
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
  duplicate = builtins.replaceStrings [ ''"version":11'' ] [ ''"version":11,"version":11'' ] (
    builtins.toJSON base
  );
  duplicateRead = import ../lib-overlays/default/lock.nix {
    inherit src;
    lockFile = builtins.toFile "duplicate-root11.json" duplicate;
    packageResolved = pinsFile;
  };
  remote = builtins.elemAt valid.workspaceState.object.dependencies 1;
  selectedState =
    state: native // { object.pins = map (pin: pin // { inherit state; }) native.object.pins; };
  branchPins = selectedState {
    branch = "main";
    revision = base.pins.alpha.revision;
    version = null;
  };
  revisionPins = selectedState {
    branch = null;
    revision = base.pins.alpha.revision;
    version = null;
  };
  branchValue = rehash (alteredPin {
    branch = "main";
    version = null;
  });
  revisionValue = rehash (alteredPin {
    branch = null;
    version = null;
  });
  branchRead = read branchValue (file "branch-pins1.json" branchPins);
  revisionRead = read revisionValue (file "revision-pins1.json" revisionPins);
  remoteOf = value: builtins.elemAt value.workspaceState.object.dependencies 1;
  longCase =
    length:
    let
      prefix = "https://example.test/";
      suffix = "/Alpha.git";
      location =
        prefix
        + builtins.concatStringsSep "" (
          builtins.genList (_: "x") (length - builtins.stringLength prefix - builtins.stringLength suffix)
        )
        + suffix;
      selected = native // {
        object.pins = map (pin: pin // { repositoryURL = location; }) native.object.pins;
      };
    in
    read (rehash (alteredPin {
      sourceLocation = location;
    })) (file "long-native-pins1.json" selected);
  checks = {
    branchAccepted = branchRead.lock.problems == [ ];
    branchNullVersionOmitted =
      builtins.attrNames (remoteOf branchRead).basedOn.state.checkoutState == [
        "branch"
        "revision"
      ];
    revisionAccepted = revisionRead.lock.problems == [ ];
    revisionNullFieldsOmitted =
      builtins.attrNames (remoteOf revisionRead).basedOn.state.checkoutState == [ "revision" ];
    tagNullBranchOmitted =
      builtins.attrNames remote.basedOn.state.checkoutState == [
        "revision"
        "version"
      ];
    location4096Accepted = (longCase 4096).lock.problems == [ ];
    location4097Refused = (longCase 4097).lock.problems != [ ];
    nullLocalLocationRefused = refuses (alteredLocalRef {
      location = null;
    });
    integerLocalLocationRefused = refuses (alteredLocalRef {
      location = 42;
    });
    localStatePathMismatchRefused = refuses (alteredLocal {
      state = {
        name = "fileSystem";
        path = "@root@/Other";
      };
    });
    branchVersionConflictRefused = refuses (alteredPin {
      branch = "main";
    });
    branchWrongTypeRefused = refuses (alteredPin {
      branch = 42;
    });

    valid = valid.lock.problems == [ ];
    artifactsEmpty = valid.workspaceState.object.artifacts == [ ];
    nativeVersion = valid.workspaceState.version == 5;
    correctFile = valid.workspaceStateFile == "workspace-state.json";
    actualNativeVersion = valid.workspaceState.version == 5;
    noInventedArtifacts =
      builtins.attrNames valid.workspaceState.object == [
        "artifacts"
        "dependencies"
      ];
    exactReference =
      builtins.attrNames remote.packageRef == [
        "identity"
        "kind"
        "location"
        "name"
      ];
    editedName = remote.subpath == "alpha";
    originalCheckoutPath = remote.basedOn.subpath == "Alpha";
    actualKind = remote.packageRef.kind == "remoteSourceControl";
    localRecordPreserved =
      builtins.head valid.workspaceState.object.dependencies == builtins.head base.localDependencies;
    v2Refused = refuses (base // { workspaceStateVersion = 2; });
    booleanNativeRefused = refuses (base // { workspaceStateVersion = true; });
    booleanRootRefused = refuses (base // { version = true; });
    extraSDKRefused = refuses (base // { nativeSDK = { }; });
    unknownRootRefused = refuses (base // { version = 999; });
    portable10LocationRefused = refuses (base // { version = 10; });
    native3Refused = refuses (base // { workspaceStateVersion = 3; });
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
      location = "@root@/../escape";
    });
    localPathSchemaRefused = refuses (alteredLocalRef {
      path = "@root@/LocalPkg";
    });
    localWrongKindRefused = refuses (alteredLocalRef {
      kind = "local";
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
    pkgs.runCommand "swiftpm-workspace-v5-location-codec" { } "touch $out";
}
