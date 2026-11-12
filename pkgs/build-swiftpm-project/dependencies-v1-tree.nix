# SPDX-License-Identifier: MIT
# JSON transports native names as data, including spaces/quotes; never shell text.
{
  read,
  entries,
  fetchEntry,
  runCommand,
  python3,
  patchArtifact ? null,
}:
runCommand "swiftpm-purifier-dependencies-v1-tree"
  {
    sources = builtins.toJSON (
      map (entry: {
        inherit (entry) path;
        source = toString (fetchEntry entry);
      }) entries
    );
    state = builtins.toJSON read.workspaceState;
    passAsFile = [
      "sources"
      "state"
    ];
    nativeBuildInputs = [ python3 ];
  }
  ''
    python3 - "$sourcesPath" "$statePath" "$out" <<'PY'
    import json,sys
    from pathlib import Path
    out=Path(sys.argv[3]);out.mkdir()
    for entry in json.load(open(sys.argv[1])):
        path=Path(entry['path'])
        if path.is_absolute() or len(path.parts)!=2 or path.parts[0]!='Packages' or path.parts[1] in ('.','..'):
            raise ValueError('invalid format8 dependency destination')
        target=out/path;target.parent.mkdir(exist_ok=True);target.symlink_to(entry['source'])
    (out/'workspace-state.json.in').write_bytes(Path(sys.argv[2]).read_bytes())
    PY
  ''
