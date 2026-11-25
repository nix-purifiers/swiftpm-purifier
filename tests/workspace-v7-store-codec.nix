# SPDX-License-Identifier: MIT
# Generated portable data; native pins and full owned projections are original SDK225 bytes.
# This checks schema/context only, not a compiler, Git fetch, or SDK release interval.
{ pkgs }:
let
  fixture = import ./workspace-v7-store-inputs.nix;
  roots = fixture.roots;
  expectedContext = builtins.listToAttrs (
    map
      (name: {
        name = builtins.unsafeDiscardStringContext (toString roots.${name});
        value.path = true;
      })
      [
        "alpha"
        "beta"
      ]
  );
  hashes = {
    alpha = "sha256-IhLx2ZVsduMMSGadNgJZ+bH7djWu8u3j0Ue6UCKnrGg=";
    beta = "sha256-Csx98D7OFnUzpptxvyzM8kHvDIJtEMkwTmfPp4xFRfo=";
  };
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
  # Captured JSON is data, not a source-context capability; reader reconstructs roots.
  file =
    name: value: builtins.toFile name (builtins.unsafeDiscardStringContext (builtins.toJSON value));
  normalized =
    value:
    value
    // {
      object = value.object // {
        dependencies = builtins.listToAttrs (
          map (row: {
            name = row.packageRef.identity;
            value = row;
          }) value.object.dependencies
        );
      };
    };
  one =
    name:
    let
      src = ./fixtures/workspace7-store-git + "/${name}";
      native = builtins.fromJSON (builtins.readFile (src + "/Package.resolved"));
      state = builtins.fromJSON (builtins.readFile (src + "/workspace-state.json"));
      owned = builtins.fromJSON (builtins.readFile (src + "/owned-edited-state.json"));
      format = if native.version == 3 then 12 else 13;
      pins = builtins.listToAttrs (
        map (
          row:
          let
            identity = row.packageRef.identity;
            source = fixture.inputs.${identity};
            checkout = row.state.checkoutState;
          in
          {
            name = identity;
            value = {
              name = row.packageRef.name;
              sourceKind = "localSourceControl";
              sourceLocation = "file://${roots.${identity}}/${identity}.git";
              storeSource = {
                path = toString roots.${identity};
                narHash = source.narHash;
                subdirectory = "${identity}.git";
              };
              revision = checkout.revision;
              branch = checkout.branch or null;
              version = checkout.version or null;
              hash = hashes.${identity};
            };
          }
        ) state.object.dependencies
      );
      base = rehash (
        {
          version = format;
          workspaceStateVersion = 7;
          package = "PureApp";
          rootDirectory = "caller";
          manifestHash = builtins.hashString "sha256" (
            builtins.toJSON [
              [
                "Package.swift"
                (builtins.hashFile "sha256" (src + "/Package.swift"))
              ]
            ]
          );
          inherit pins;
          localDependencies = [ ];
          binaryTargets = [ ];
          products = [ ];
        }
        // (if format == 12 then { originHash = native.originHash; } else { })
      );
      read =
        value: selected:
        import ../lib-overlays/default/lock.nix {
          inherit src;
          lockFile = file "workspace7-portable.json" value;
          packageResolved = selected;
        };
      valid = read base (src + "/Package.resolved");
      altered =
        change:
        rehash (
          base
          // {
            pins = base.pins // {
              alpha = base.pins.alpha // change;
            };
          }
        );
      refuses = change: (read (altered change) (src + "/Package.resolved")).lock.problems != [ ];
      changedMeta = change: { storeSource = base.pins.alpha.storeSource // change; };
      joinedUpper = builtins.mapAttrs (
        identity: pin:
        if identity == "alpha" then pin // { revision = pkgs.lib.toUpper pin.revision; } else pin
      ) base.pins;
      upperNative = native // {
        pins = map (
          pin:
          if pin.identity == "alpha" then
            pin
            // {
              state = pin.state // {
                revision = pkgs.lib.toUpper pin.state.revision;
              };
            }
          else
            pin
        ) native.pins;
      };
      wrongKind = native // {
        pins = map (pin: pin // { kind = "remoteSourceControl"; }) native.pins;
      };
    in
    assert valid.lock.problems == [ ];
    assert valid.toolLockBytes == builtins.readFile (src + "/Package.resolved");
    assert normalized valid.workspaceState == normalized owned;
    assert builtins.getContext (builtins.toJSON valid.workspaceState) == expectedContext;
    assert builtins.getContext (builtins.toJSON valid.lock.entries) == expectedContext;
    assert
      builtins.getContext (builtins.toJSON (map (s: s.declaration) valid.storeGitSources))
      == expectedContext;
    assert refuses { sourceKind = "remoteSourceControl"; };
    assert refuses { url = base.pins.alpha.sourceLocation; };
    assert refuses (changedMeta {
      narHash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
    });
    assert refuses (changedMeta {
      path = base.pins.alpha.storeSource.path + "-other";
    });
    assert refuses (changedMeta {
      subdirectory = "../alpha.git";
    });
    assert refuses (changedMeta {
      future = null;
    });
    assert refuses { future = null; };
    assert
      (read (rehash (base // { pins = joinedUpper; })) (file "uppercase-pins.json" upperNative))
      .lock.problems != [ ];
    assert (read base (file "wrong-kind-pins.json" wrongKind)).lock.problems != [ ];
    true;
  all = map one [
    "59-tag"
    "59-branch"
    "59-revision"
    "60-tag"
    "60-branch"
    "60-revision"
  ];
in
assert builtins.all (value: value) all;
pkgs.runCommand "workspace-v7-store-codec" { } "touch $out"
