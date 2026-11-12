# SPDX-License-Identifier: MIT
# Real metadata is an independent NAR. Do not use the shared reconstructed
# commitObject .git feature; the public entries remain plain Git sources.
{
  read,
  fetchgit,
  fetchers,
  runCommand,
  python3,
  git,
  nix,
}:
let
  realFetch = fetchers.fetchgit or fetchgit;
  packages = map (
    name:
    let
      pin = read.preWorkspace.pins.${name};
      metadata = realFetch {
        # Separate producer and consumer cache identities. A nonce revision
        # fixture can prove a cold consumer fetch without deleting store data.
        name = read.preWorkspace.metadataName name + "-replay";
        url = pin.url or pin.sourceLocation;
        rev = pin.revision;
        hash = pin.gitHash;
        fetchSubmodules = true;
        leaveDotGit = true;
      };
      verified =
        runCommand (read.preWorkspace.metadataName name + "-verified")
          {
            nativeBuildInputs = [
              python3
              git
              nix
            ];
            PYTHONPATH = ../../pkgs/cli;
            inherit metadata;
            revision = pin.revision;
            sourceHash = pin.hash;
            metadataHash = pin.gitHash;
            nativeOrigin = pin.sourceLocation;
          }
          ''
            python3 ${../../pkgs/cli/pre_workspace_git.py} \
              --source "$metadata" --output "$out" --revision "$revision" \
              --source-hash "$sourceHash" --metadata-hash "$metadataHash" \
              --origin "$nativeOrigin" --git ${git}/bin/git --nix-hash ${nix}/bin/nix-hash
          '';
    in
    {
      inherit name;
      path = "Packages/${name}-${pin.version}";
      source = toString verified;
    }
  ) (builtins.attrNames read.preWorkspace.pins);
in
runCommand "swiftpm-purifier-preworkspace-tree"
  {
    sources = builtins.toJSON packages;
    passAsFile = [ "sources" ];
    nativeBuildInputs = [ python3 ];
  }
  ''
    mkdir -p "$out"
    python3 - "$sourcesPath" "$out" <<'PY'
    import json,sys
    from pathlib import Path
    packages=json.load(open(sys.argv[1]));root=Path(sys.argv[2])
    for package in packages:
        path=root/package['path'];path.parent.mkdir(parents=True,exist_ok=True)
        path.symlink_to(package['source'])
    (root/'pre-workspace-sources.json').write_text(json.dumps(packages,sort_keys=True)+'\n')
    PY
  ''
