# SPDX-License-Identifier: MIT
# Repo-local portable 7 only; native files remain absent, no selected SDK.
{
  lock,
  raw,
  src,
  lockFile,
  packageResolved ? null,
}:
let
  names = builtins.attrNames;
  all = builtins.all;
  text =
    value: builtins.isString value && value != "" && builtins.match "[^[:cntrl:]]+" value != null;
  safeName =
    value:
    text value
    && !(builtins.elem value [
      "."
      ".."
    ])
    && builtins.match "[^/\\\\[:cntrl:]]+" value != null;
  moduleName =
    value: builtins.isString value && builtins.match "[A-Za-z_][A-Za-z0-9_]*" value != null;
  gitLocation =
    value:
    text value
    && (
      builtins.match "(https?|ssh|git)://[^/]+(/.*)?" value != null
      || builtins.match "[^/ @:]+@[^/ :]+:.+" value != null
    );
  identifier = "(0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)";
  semver =
    value:
    builtins.isString value
    &&
      builtins.match "(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)(-${identifier}(\\.${identifier})*)?(\\+[0-9A-Za-z-]+(\\.[0-9A-Za-z-]+)*)?" value
      != null;
  revision =
    value: builtins.isString value && builtins.match "[0-9a-f]{40}([0-9a-f]{24})?" value != null;
  hex = value: builtins.isString value && builtins.match "[0-9a-f]{64}" value != null;
  hash =
    value:
    builtins.isString value
    && builtins.match "sha256-[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=" value != null;
  unique =
    values:
    builtins.length values == builtins.length (
      names (
        builtins.listToAttrs (
          map (name: {
            inherit name;
            value = true;
          }) values
        )
      )
    );
  pins = lock.pins or null;
  pinNames = if builtins.isAttrs pins then names pins else [ ];
  pinFields = [
    "gitHash"
    "hash"
    "name"
    "revision"
    "sourceLocation"
    "version"
  ];
  pinValid =
    name:
    let
      pin = pins.${name};
    in
    safeName name
    && builtins.isAttrs pin
    && all (field: builtins.hasAttr field pin) pinFields
    && all (field: builtins.elem field (pinFields ++ [ "url" ])) (names pin)
    && pin.name == name
    && gitLocation pin.sourceLocation
    && revision pin.revision
    && semver pin.version
    && hash pin.hash
    && hash pin.gitHash
    && (!(pin ? url) || gitLocation pin.url);
  productValid =
    product:
    builtins.isAttrs product
    && safeName (product.name or null)
    && builtins.elem (product.type or null) [
      "executable"
      "library"
    ]
    &&
      names product == (
        if product.type == "executable" then
          [
            "modules"
            "name"
            "type"
          ]
        else
          [
            "linkage"
            "modules"
            "name"
            "type"
          ]
      )
    && (
      product.type != "library"
      || builtins.elem product.linkage [
        "static"
        "dynamic"
      ]
    )
    && builtins.isList product.modules
    && product.modules != [ ]
    && all moduleName product.modules
    && unique product.modules;
  expectedFields = builtins.sort builtins.lessThan [
    "version"
    "nativeProtocol"
    "package"
    "rootDirectory"
    "manifestHash"
    "sourceLayoutHash"
    "pins"
    "products"
    "localDependencies"
    "binaryTargets"
    "nativeLockFiles"
    "contentHash"
  ];
  shape =
    builtins.isAttrs lock
    && names lock == expectedFields
    && builtins.isInt lock.version
    && lock.version == 7
    && lock.nativeProtocol == "pre-workspace-git"
    && safeName lock.package
    && safeName lock.rootDirectory
    && hex lock.manifestHash
    && hex lock.sourceLayoutHash
    && hash lock.contentHash
    && lock.localDependencies == [ ]
    && lock.binaryTargets == [ ]
    && builtins.isAttrs lock.nativeLockFiles
    &&
      names lock.nativeLockFiles == [
        "Package.pins"
        "Package.resolved"
      ]
    && all (name: lock.nativeLockFiles.${name} == null) (names lock.nativeLockFiles)
    && builtins.isAttrs pins
    && all pinValid pinNames
    && unique (map (name: pins.${name}.sourceLocation) pinNames)
    && builtins.isList lock.products
    && all productValid lock.products
    && unique (map (product: product.name) lock.products);
  # Compare decoded key occurrences with the parsed object tree. Duplicate
  # keys cannot hide behind escapes or JSON-looking string values.
  tokens = builtins.split "\"(([^\"\\\\]|\\\\.)*)\"" raw;
  keys = builtins.concatLists (
    map (
      i:
      let
        token = builtins.elemAt tokens i;
        next = if i + 1 < builtins.length tokens then builtins.elemAt tokens (i + 1) else "";
      in
      if
        builtins.isList token && builtins.isString next && builtins.match "[[:space:]]*:.*" next != null
      then
        [ (builtins.fromJSON ("\"" + builtins.elemAt token 0 + "\"")) ]
      else
        [ ]
    ) (builtins.genList (i: i) (builtins.length tokens))
  );
  objectKeys =
    value:
    if builtins.isAttrs value then
      names value ++ builtins.concatLists (map (name: objectKeys value.${name}) (names value))
    else if builtins.isList value then
      builtins.concatLists (map objectKeys value)
    else
      [ ];
  noDuplicates =
    builtins.sort builtins.lessThan keys == builtins.sort builtins.lessThan (objectKeys lock);
  rawValid =
    builtins.stringLength raw <= 1048576
    && builtins.substring 0 3 raw != builtins.fromJSON ''"\ufeff"'';
  rootEntries = builtins.readDir src;
  stateName =
    name:
    builtins.match ".*([Ss][Tt][Aa][Tt][Ee]|[Ww][Oo][Rr][Kk][Ss][Pp][Aa][Cc][Ee]).*" name != null
    || builtins.match ".*\\.json" name != null;
  buildType = rootEntries.".build" or "missing";
  buildEntries = if buildType == "directory" then builtins.readDir (src + "/.build") else { };
  indexType = buildEntries."index-build" or "missing";
  indexEntries =
    if indexType == "directory" then builtins.readDir (src + "/.build/index-build") else { };
  buildTree =
    path: depth:
    let
      entries = builtins.readDir path;
      children = map (
        name:
        if entries.${name} == "directory" && depth < 256 then
          buildTree (path + "/${name}") (depth + 1)
        else
          {
            valid = entries.${name} == "regular";
            count = 0;
          }
      ) (names entries);
      count =
        builtins.length (names entries) + builtins.foldl' (sum: child: sum + child.count) 0 children;
    in
    {
      inherit count;
      valid = count <= 100000 && all (child: child.valid) children;
    };
  buildSafe = buildType != "directory" || (buildTree (src + "/.build") 0).valid;
  absence =
    packageResolved == null
    && !(rootEntries ? "Package.pins")
    && !(rootEntries ? "Package.resolved")
    && builtins.elem buildType [
      "missing"
      "directory"
    ]
    && builtins.elem indexType [
      "missing"
      "directory"
    ]
    &&
      all
        (
          name:
          builtins.elem (buildEntries.${name} or "missing") [
            "directory"
            "missing"
          ]
        )
        [
          "debug"
          "release"
        ]
    && buildSafe
    && !(builtins.any stateName (names buildEntries))
    && !(builtins.any stateName (names indexEntries));
  layout = import ./pre-workspace-layout.nix { inherit src lockFile; };
  manifestNames = builtins.sort builtins.lessThan (
    builtins.filter (name: builtins.match "Package(@swift-[0-9.]+)?\\.swift" name != null) (
      names rootEntries
    )
  );
  regularManifests =
    (rootEntries."Package.swift" or null) == "regular"
    && all (name: rootEntries.${name} == "regular") manifestNames;
  manifestHash = builtins.hashString "sha256" (
    builtins.toJSON (
      map (name: [
        name
        (builtins.hashFile "sha256" (src + "/${name}"))
      ]) manifestNames
    )
  );
  contentHash = builtins.convertHash {
    hash = builtins.hashString "sha256" (builtins.toJSON (builtins.removeAttrs lock [ "contentHash" ]));
    hashAlgo = "sha256";
    toHashFormat = "sri";
  };
  valid =
    rawValid
    && shape
    && noDuplicates
    && absence
    && layout.valid
    && regularManifests
    && lock.contentHash == contentHash
    && lock.manifestHash == manifestHash
    && lock.sourceLayoutHash == layout.hash;
  resolvedPins =
    if valid then
      map (name: {
        identity = name;
        location = pins.${name}.sourceLocation;
        kind = "remote";
        state = {
          revision = pins.${name}.revision;
          version = pins.${name}.version;
          branch = null;
        };
      }) pinNames
    else
      [ ];
  entries =
    if valid then
      map (name: {
        path = "Packages/${name}-${pins.${name}.version}";
        hash = pins.${name}.hash;
        git = {
          url = pins.${name}.url or pins.${name}.sourceLocation;
          rev = pins.${name}.revision;
          submodules = true;
          leaveDotGit = false;
          commitObject = null;
        };
      }) pinNames
    else
      [ ];
in
{
  inherit valid entries resolvedPins;
  pins = if valid then pins else { };
  metadataName =
    name:
    "preworkspace-"
    + builtins.substring 0 24 (
      builtins.hashString "sha256" (
        builtins.toJSON [
          name
          pins.${name}.revision
        ]
      )
    );
}
