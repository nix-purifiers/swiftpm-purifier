# SPDX-License-Identifier: MIT
# Format-8 source layout inventory. Control artifacts require recovery first.
{ src }:
let
  root = builtins.readDir src;
  names = builtins.attrNames root;
  artifact =
    name:
    name == ".swiftpm-purifier-transaction.json"
    || builtins.match "\\.swiftpm-purifier-stage-[0-9a-f]{64}" name != null;
  metadata = {
    ".git" = [
      "directory"
      "regular"
    ];
    ".build" = [ "directory" ];
    "Packages" = [ "directory" ];
    "Package.resolved" = [ "regular" ];
    "swiftpm-purifier.lock.json" = [ "regular" ];
  };
  nameValid =
    name: builtins.stringLength name <= 255 && builtins.match ".*[[:cntrl:]].*" name == null;
  hasArtifact = builtins.any artifact names;
  metadataValid = builtins.all (
    name: !(root ? ${name}) || builtins.elem root.${name} metadata.${name}
  ) (builtins.attrNames metadata);
  walk =
    path: prefix: depth:
    if depth > 256 then
      {
        valid = false;
        entries = [ ];
      }
    else
      let
        children = builtins.readDir path;
        results = map (
          name:
          let
            kind = children.${name};
            relative = prefix + name;
            nested =
              if kind == "directory" then
                walk (path + "/${name}") (relative + "/") (depth + 1)
              else
                {
                  valid = kind == "regular";
                  entries = [ ];
                };
          in
          if !nameValid name then
            {
              valid = false;
              entries = [ ];
            }
          else if prefix == "" && metadata ? ${name} then
            {
              valid = true;
              entries = [ ];
            }
          else
            {
              inherit (nested) valid;
              entries = [
                [
                  relative
                  kind
                ]
              ]
              ++ nested.entries;
            }
        ) (builtins.attrNames children);
      in
      {
        valid = builtins.all (result: result.valid) results;
        entries = builtins.concatLists (map (result: result.entries) results);
      };
  result =
    if hasArtifact || !metadataValid || root ? "Package.pins" then
      {
        valid = false;
        entries = [ ];
      }
    else
      walk src "" 0;
  valid = result.valid && builtins.length result.entries <= 100000;
  inventory = builtins.sort (
    left: right: builtins.lessThan (builtins.elemAt left 0) (builtins.elemAt right 0)
  ) result.entries;
in
{
  inherit valid;
  paths = if valid then inventory else null;
  hash =
    if valid then
      builtins.hashString "sha256" (
        builtins.toJSON [
          "swiftpm-purifier/dependencies-state-v1/source-layout"
          1
          inventory
        ]
      )
    else
      null;
}
