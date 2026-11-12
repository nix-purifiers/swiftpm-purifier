# SPDX-License-Identifier: MIT
# Format 7 only: paths/types and empty directories, never source contents.
{
  src,
  lockFile ? src + "/swiftpm-purifier.lock.json",
}:
let
  defaultLock = "swiftpm-purifier.lock.json";
  rootText = toString src;
  lockText = toString lockFile;
  typeAt =
    path:
    if builtins.pathExists path then
      builtins.readFileType path
    else
      let
        parent = builtins.dirOf (toString path);
        key = builtins.unsafeDiscardStringContext (builtins.baseNameOf (toString path));
        entries = if builtins.pathExists parent then builtins.readDir parent else { };
      in
      entries.${key} or "missing";
  partsOf =
    path:
    builtins.filter (part: builtins.isString part && part != "") (builtins.split "/" (toString path));
  canonical =
    path:
    let
      text = toString path;
      storePrefix = builtins.storeDir + "/";
      stored = builtins.substring 0 (builtins.stringLength storePrefix) text == storePrefix;
      parts = partsOf path;
      storeParts = builtins.length (partsOf builtins.storeDir) + 1;
      # Inspect the actual store object with its original reference context.
      # Reading / or /nix/store with a truncated object context is invalid and
      # would also escape the source boundary during pure evaluation.
      anchorParts = if stored then builtins.genList (i: builtins.elemAt parts i) storeParts else [ ];
      anchor = if stored then "/" + builtins.concatStringsSep "/" anchorParts else "";
      remaining =
        if stored then
          builtins.genList (i: builtins.elemAt parts (i + storeParts)) (builtins.length parts - storeParts)
        else
          parts;
      initial = {
        path = anchor;
        kind = if stored then builtins.readFileType anchor else "directory";
        valid = true;
      };
      ancestors = builtins.foldl' (
        state: part:
        let
          current = state.path + "/${part}";
          parentValid =
            state.valid
            && builtins.elem state.kind [
              "directory"
              "missing"
            ];
          kind = if parentValid && state.kind == "directory" then typeAt current else "missing";
        in
        {
          path = current;
          inherit kind;
          valid = parentValid && part != "." && part != ".." && kind != "symlink";
        }
      ) initial remaining;
    in
    builtins.substring 0 1 text == "/"
    && builtins.match ".*//.*" text == null
    && (text == "/" || builtins.match ".*/" text == null)
    && builtins.all (part: part != "." && part != "..") parts
    && initial.kind != "symlink"
    && ancestors.valid;
  inRoot = builtins.substring 0 (builtins.stringLength rootText + 1) lockText == rootText + "/";
  destinationValid =
    canonical lockFile
    && (lockText == rootText + "/${defaultLock}" || !inRoot)
    && builtins.elem (typeAt lockFile) [
      "regular"
      "missing"
    ];
  walk =
    path: prefix: depth:
    if depth > 256 then
      {
        valid = false;
        entries = [ ];
      }
    else
      let
        kinds = builtins.readDir path;
        children = map (
          name:
          let
            kind = kinds.${name};
            relative = if prefix == "" then name else "${prefix}/${name}";
            excluded =
              prefix == ""
              && builtins.elem name [
                ".build"
                "Packages"
                ".git"
                defaultLock
              ];
            validName = builtins.match "[^[:cntrl:]]+" name != null;
            validKind = builtins.elem kind [
              "regular"
              "directory"
            ];
            ownedType =
              if name == ".git" then
                validKind
              else
                kind == (if name == defaultLock then "regular" else "directory");
            below =
              if validName && validKind && !excluded && kind == "directory" then
                walk (path + "/${name}") relative (depth + 1)
              else
                {
                  valid = true;
                  entries = [ ];
                };
          in
          {
            valid = validName && validKind && (!excluded || ownedType) && below.valid;
            entries =
              if excluded then
                [ ]
              else
                [
                  [
                    relative
                    kind
                  ]
                ]
                ++ below.entries;
          }
        ) (builtins.attrNames kinds);
        entries = builtins.concatLists (map (child: child.entries) children);
      in
      {
        valid = builtins.all (child: child.valid) children && builtins.length entries <= 100000;
        inherit entries;
      };
  rootValid = canonical src && typeAt src == "directory" && destinationValid;
  inventory =
    if rootValid then
      walk src "" 0
    else
      {
        valid = false;
        entries = [ ];
      };
  valid = rootValid && inventory.valid;
  entries =
    if valid then
      builtins.sort (a: b: builtins.lessThan (builtins.head a) (builtins.head b)) inventory.entries
    else
      [ ];
in
{
  inherit valid entries;
  hash =
    if valid then
      builtins.hashString "sha256" (
        builtins.toJSON {
          version = 1;
          inherit entries;
        }
      )
    else
      null;
}
