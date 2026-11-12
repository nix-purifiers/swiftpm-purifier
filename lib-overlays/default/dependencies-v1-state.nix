# SPDX-License-Identifier: MIT
# Pure immutable-input checks. Host inode/ownership authority stays in Python.
{
  lock,
  raw,
  src,
  lockFile,
  storeName,
  packageResolved ? null,
}@args:
let
  names = builtins.attrNames;
  closed =
    value: fields: builtins.isAttrs value && names value == builtins.sort builtins.lessThan fields;
  textPath = value: builtins.isPath value || builtins.isString value;
  split = value: builtins.filter builtins.isString (builtins.split "/" value);
  spelling =
    value:
    textPath value
    && builtins.substring 0 1 (toString value) == "/"
    && (
      toString value == "/"
      || builtins.all (part: part != "" && part != "." && part != "..") (
        builtins.tail (split (toString value))
      )
    );
  # A declared Nix store object is the immutable evaluation boundary. Reading
  # ambient / or /nix/store would make a flake evaluation impure; links below
  # (and at) the object are still rejected. Non-store inputs use full ancestry.
  pathInfo =
    value:
    if !spelling value then
      {
        valid = false;
        kind = "invalid";
      }
    else
      let
        path = toString value;
        storeMatch = builtins.match "(/nix/store/[^/]+)(/.*)?" path;
        boundary = if storeMatch == null then "/" else builtins.head storeMatch;
        kind = if boundary == "/" then "directory" else builtins.readFileType boundary;
        suffix = builtins.substring (builtins.stringLength boundary) (builtins.stringLength path) path;
        parts =
          if boundary == "/" then
            builtins.tail (split path)
          else
            builtins.filter (part: part != "") (split suffix);
      in
      builtins.foldl'
        (
          parent: part:
          let
            children = if parent.valid && parent.kind == "directory" then builtins.readDir parent.path else { };
            kind = children.${part} or "missing";
          in
          {
            path = parent.path + (if parent.path == "/" then "" else "/") + part;
            inherit kind;
            valid =
              parent.valid
              && parent.kind == "directory"
              && builtins.elem kind [
                "regular"
                "directory"
                "missing"
              ];
          }
        )
        {
          path = boundary;
          inherit kind;
          valid = builtins.elem kind [
            "regular"
            "directory"
          ];
        }
        parts;
  sourceInfo = pathInfo src;
  sourceValid = sourceInfo.valid && sourceInfo.kind == "directory";
  source = toString src;
  outside =
    path:
    builtins.substring 0 (builtins.stringLength source + 1) path != source + "/" && path != source;
  destination =
    value: default:
    let
      facts = pathInfo value;
      path = if textPath value then toString value else "";
    in
    {
      inherit path;
      valid =
        facts.valid
        && builtins.elem facts.kind [
          "regular"
          "missing"
        ]
        && (path == default || outside path);
      kind = facts.kind;
    };
  output = destination lockFile (source + "/swiftpm-purifier.lock.json");
  selected = if !(args ? packageResolved) then src + "/Package.resolved" else packageResolved;
  native =
    if selected == null then
      {
        path = null;
        valid = true;
        kind = "missing";
      }
    else
      destination selected (source + "/Package.resolved");
  selectorValid =
    sourceValid
    && output.valid
    && output.kind == "regular"
    && native.valid
    && (native.path == null || native.path != output.path);
  selectedRaw =
    if selectorValid && native.kind == "regular" then builtins.readFile native.path else null;
  rootEntries = if sourceValid then builtins.readDir src else { };
  logicalRaw =
    if (rootEntries."Package.resolved" or null) == "regular" then
      builtins.readFile (src + "/Package.resolved")
    else
      null;
  byteBound = value: value == null || builtins.stringLength value <= 1048576;
  # The JSON decoder discards duplicate keys; retain key-occurrence parity.
  noDuplicates =
    bytes: value:
    let
      tokens = builtins.split "\"(([^\"\\\\]|\\\\.)*)\"" bytes;
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
        item:
        if builtins.isAttrs item then
          names item ++ builtins.concatLists (map (name: objectKeys item.${name}) (names item))
        else if builtins.isList item then
          builtins.concatLists (map objectKeys item)
        else
          [ ];
    in
    builtins.sort builtins.lessThan keys == builtins.sort builtins.lessThan (objectKeys value);
  bom = builtins.fromJSON ''"\ufeff"'';
  rawValid = byteBound raw && builtins.substring 0 3 raw != bom && noDuplicates raw lock;
  codec = import ./dependencies-v1-codec.nix { inherit lock; };
  layout =
    if sourceValid then
      import ./dependencies-v1-layout.nix { inherit src; }
    else
      {
        valid = false;
        hash = null;
      };
  manifestNames =
    if sourceValid then
      builtins.filter (name: builtins.match "Package(@swift-[0-9.]+)?\\.swift" name != null) (
        names rootEntries
      )
    else
      [ ];
  manifestsValid = builtins.all (name: rootEntries.${name} == "regular") manifestNames;
  manifestHash = builtins.hashString "sha256" (
    builtins.toJSON (
      map (name: [
        name
        (builtins.hashFile "sha256" (src + "/${name}"))
      ]) manifestNames
    )
  );
  pins = if codec.valid then lock.pins else { };
  nativeValue =
    if selectedRaw == null then
      null
    else
      builtins.addErrorContext "swiftpm-purifier: invalid selected format-8 Package.resolved JSON" (
        builtins.fromJSON selectedRaw
      );
  nativePins = if nativeValue == null then [ ] else (nativeValue.object.pins or [ ]);
  pinValid =
    pin:
    closed pin [
      "package"
      "repositoryURL"
      "state"
    ]
    && builtins.isString pin.package
    && pins ? ${pin.package}
    && pin.repositoryURL == pins.${pin.package}.sourceLocation
    && closed pin.state [
      "branch"
      "revision"
      "version"
    ]
    &&
      pin.state == {
        branch = null;
        inherit (pins.${pin.package}) revision version;
      };
  nativeValid =
    selectedRaw == null
    || (
      byteBound selectedRaw
      && builtins.substring 0 3 selectedRaw != bom
      && noDuplicates selectedRaw nativeValue
      && closed nativeValue [
        "object"
        "version"
      ]
      && builtins.isInt nativeValue.version
      && nativeValue.version == 1
      && closed nativeValue.object [ "pins" ]
      && builtins.isList nativeValue.object.pins
      && builtins.length nativePins <= 1024
      && builtins.all pinValid nativePins
      && builtins.sort builtins.lessThan (map (pin: pin.package) nativePins) == names pins
    );
  binding = if selectedRaw == null then null else builtins.hashString "sha256" selectedRaw;
  problem =
    if !selectorValid then
      "invalid format-8 source or selected lock path/type"
    else if !rawValid || !codec.valid then
      "invalid closed format-8 lock or content hash"
    else if !byteBound selectedRaw || !byteBound logicalRaw then
      "format-8 native file exceeds byte bound"
    else if !layout.valid || layout.hash != lock.sourceLayoutHash then
      "stale or invalid format-8 source layout"
    else if !manifestsValid || manifestHash != lock.manifestHash then
      "stale format-8 manifest hash"
    else if binding != lock.nativeToolLock.sha256 || !nativeValid then
      "stale or invalid selected format-8 native pin bytes/graph"
    else
      null;
  usable = problem == null;
  products = if usable then lock.products else [ ];
  entries = map (name: {
    path = "Packages/${name}";
    inherit (pins.${name}) hash;
    git = {
      url = pins.${name}.sourceLocation;
      rev = pins.${name}.revision;
      submodules = true;
      leaveDotGit = false;
      commitObject = null;
    };
  }) (names pins);
in
{
  lock = {
    purifier = "swiftpm-purifier";
    problems =
      if usable then [ ] else [ "swiftpm-purifier: ${problem}. Run `swiftpm-purifier relock`." ];
    entries = if usable then entries else [ ];
    outputs = builtins.listToAttrs (
      map (product: {
        name = product.name;
        value = product;
      }) products
    );
  };
  inherit products storeName;
  package = if usable then lock.package else null;
  rootDirectory = if usable then lock.rootDirectory else null;
  lockVersion = 8;
  nativeSDK = null;
  preWorkspace = null;
  toolLockName = "Package.resolved";
  toolLockFile = if usable && selectedRaw != null then selected else null;
  toolLockBytes = if usable then selectedRaw else null;
  entryName = entry: storeName entry.path;
  workspaceStateFile = "dependencies-state.json";
  workspaceState = {
    version = 1;
    object.dependencies =
      if usable then
        map (name: {
          basedOn = null;
          inherit name;
          repositoryURL = pins.${name}.sourceLocation;
          subpath = name;
          state = {
            name = "edited";
            path = "@root@/Packages/${name}";
          };
        }) (names pins)
      else
        [ ];
  };
}
