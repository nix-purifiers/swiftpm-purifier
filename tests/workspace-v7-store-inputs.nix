# SPDX-License-Identifier: MIT
# Data-only fixture delivery: no IFD, Git command or selected SDK is involved.
let
  evidence = builtins.fromJSON (
    builtins.readFile ./fixtures/workspace7-store-git/immutable-inputs-evidence.json
  );
  # NarHash of actual263 reconstructed alpha+beta directory, independently audited271.
  bundle = builtins.fetchTarball {
    # fetchTarball URL API requires no string context; payload identity stays pinned.
    url = builtins.unsafeDiscardStringContext "file://${./fixtures/workspace7-store-git/immutable-inputs.bundle.tar.gz}";
    sha256 = "sha256-iUmfulaotgbjNyywul82eOjtHbCq/keim9qg5sRBCOQ=";
  };
  roots = builtins.mapAttrs (
    name: row:
    builtins.path {
      path = bundle + "/${name}";
      name = builtins.substring 33 (builtins.stringLength (builtins.baseNameOf row.storeRoot)) (
        builtins.baseNameOf row.storeRoot
      );
      sha256 = row.narHash;
    }
  ) evidence.inputs;
in
{
  inherit roots;
  inputs = evidence.inputs;
}
