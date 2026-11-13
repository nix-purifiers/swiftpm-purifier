# SPDX-License-Identifier: MIT
# Closed portable11 / native5 LOCATION+pins1 reader. No host ownership authority.
{
  lock,
  raw,
  src,
  lockFile,
  storeName,
  packageResolved ? null,
}@args:
let
  descriptor = {
    portable = 11;
    native = 5;
    stateFile = "workspace-state.json";
    artifacts = true;
  };
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
    && native.kind == "regular"
    && native.path != null
    && native.path != output.path;
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
  all = builtins.all;
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
  safeName =
    value:
    builtins.isString value
    && value != ""
    && builtins.stringLength value <= 255
    && !(builtins.elem value [
      "."
      ".."
    ])
    && builtins.match "[^/\\[:cntrl:]]+" value != null;
  gitLocation =
    value:
    builtins.isString value
    && builtins.match "[^[:cntrl:]?#]+" value != null
    && (
      builtins.match "(https?|ssh|git)://[^/]+/.+" value != null
      || builtins.match "[^/ @:]+@[^/ :]+:.+" value != null
    );
  checkoutPath =
    value:
    if !gitLocation value then
      null
    else
      let
        scp = builtins.match "[^/ @:]+@[^/ :]+:(.+)" value;
        location = if scp == null then value else builtins.head scp;
        trimmed = builtins.head (builtins.match "(.*[^/])/*" location);
        matched = builtins.match "(.*/)?([^/]+)" trimmed;
        leaf = if matched == null then "" else builtins.elemAt matched 1;
        suffix = builtins.match "(.*)\\.git" leaf;
        result = if suffix == null then leaf else builtins.head suffix;
      in
      if safeName result then result else null;
  identifier = "(0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)";
  semver =
    value:
    builtins.isString value
    &&
      builtins.match "(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)(-${identifier}(\\.${identifier})*)?(\\+[0-9A-Za-z-]+(\\.[0-9A-Za-z-]+)*)?" value
      != null;
  branch =
    value:
    builtins.isString value
    && builtins.stringLength value > 0
    && builtins.stringLength value <= 4096
    && builtins.match "[^[:cntrl:]]+" value != null;
  revision =
    value: builtins.isString value && builtins.match "[0-9a-f]{40}([0-9a-f]{24})?" value != null;
  sri =
    value:
    builtins.isString value
    && builtins.match "sha256-[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=" value != null;
  topFields = [
    "version"
    "package"
    "rootDirectory"
    "manifestHash"
    "products"
    "workspaceStateVersion"
    "pins"
    "binaryTargets"
    "localDependencies"
    "contentHash"
  ];
  pinFields = [
    "name"
    "sourceLocation"
    "revision"
    "branch"
    "version"
    "hash"
  ];
  pins = if builtins.isAttrs (lock.pins or null) then lock.pins else { };
  locals = if builtins.isList (lock.localDependencies or null) then lock.localDependencies else [ ];
  products = if builtins.isList (lock.products or null) then lock.products else [ ];
  localInfo =
    dependency:
    let
      ref = dependency.packageRef or { };
      matched =
        if builtins.isString (ref.location or null) then
          builtins.match "@root@/(.+)" ref.location
        else
          null;
      relative = if matched == null then "" else builtins.head matched;
      parts = split relative;
      facts = pathInfo (source + "/" + relative);
    in
    {
      inherit relative facts;
      valid =
        closed dependency [
          "basedOn"
          "packageRef"
          "state"
          "subpath"
        ]
        && closed ref [
          "identity"
          "kind"
          "name"
          "location"
        ]
        && safeName ref.identity
        && safeName ref.name
        && matched != null
        && builtins.stringLength ref.location <= 4096
        && builtins.stringLength (source + "/" + relative) <= 4096
        && ref.kind == "fileSystem"
        && dependency.basedOn == null
        &&
          dependency.state == {
            name = "fileSystem";
            path = ref.location;
          }
        && dependency.subpath == ref.identity
        && all safeName parts
        && !(builtins.elem (builtins.head parts) [
          ".build"
          ".git"
          "Packages"
          ".swiftpm-purifier"
        ])
        && facts.valid
        && facts.kind == "directory"
        && ((builtins.readDir facts.path)."Package.swift" or null) == "regular";
    };
  pinValid =
    identity:
    let
      pin = pins.${identity};
    in
    safeName identity
    && (closed pin pinFields || closed pin (pinFields ++ [ "url" ]))
    && safeName pin.name
    && checkoutPath pin.sourceLocation != null
    && builtins.stringLength pin.sourceLocation <= 4096
    && revision pin.revision
    && (pin.version == null || semver pin.version)
    && (pin.branch == null || branch pin.branch)
    && (pin.branch == null || pin.version == null)
    && sri pin.hash
    && (!(pin ? url) || checkoutPath pin.url != null && pin.url != pin.sourceLocation);
  productValid =
    product:
    builtins.isAttrs product
    && safeName (product.name or null)
    && (
      (
        closed product [
          "name"
          "type"
        ]
        && product.type == "executable"
      )
      || (
        closed product [
          "name"
          "type"
          "linkage"
        ]
        && product.type == "library"
        && builtins.elem product.linkage [
          "static"
          "dynamic"
          "automatic"
        ]
      )
    );
  shape =
    closed lock topFields
    && builtins.isInt lock.version
    && lock.version == descriptor.portable
    && builtins.isInt lock.workspaceStateVersion
    && lock.workspaceStateVersion == descriptor.native
    && safeName lock.package
    && safeName lock.rootDirectory
    && builtins.isAttrs lock.pins
    && builtins.length (names pins) <= 1024
    && builtins.isList lock.localDependencies
    && builtins.length locals <= 1024
    && builtins.isList lock.products
    && builtins.length products <= 1024
    && lock.binaryTargets == [ ]
    && all pinValid (names pins)
    && all (row: (localInfo row).valid) locals
    && all productValid products
    && unique (map (row: row.name) products)
    && unique ((names pins) ++ map (row: row.packageRef.identity) locals)
    && unique (
      (map (identity: pins.${identity}.name) (names pins)) ++ map (row: row.packageRef.name) locals
    )
    && unique (map (identity: pins.${identity}.sourceLocation) (names pins))
    && unique (map (row: (localInfo row).relative) locals)
    && !(builtins.elem lock.package (
      (map (identity: pins.${identity}.name) (names pins)) ++ map (row: row.packageRef.name) locals
    ));
  contentHash = builtins.convertHash {
    hash = builtins.hashString "sha256" (builtins.toJSON (builtins.removeAttrs lock [ "contentHash" ]));
    hashAlgo = "sha256";
    toHashFormat = "sri";
  };
  selectedValue = if selectedRaw == null then null else builtins.fromJSON selectedRaw;
  nativePinValid =
    pin:
    closed pin [
      "package"
      "repositoryURL"
      "state"
    ]
    && safeName pin.package
    && checkoutPath pin.repositoryURL != null
    && closed pin.state [
      "branch"
      "revision"
      "version"
    ]
    && (pin.state.branch == null || branch pin.state.branch)
    && revision pin.state.revision
    && (pin.state.version == null || semver pin.state.version)
    && (pin.state.branch == null || pin.state.version == null);
  nativeShape =
    closed selectedValue [
      "version"
      "object"
    ]
    && builtins.isInt selectedValue.version
    && selectedValue.version == 1
    && closed selectedValue.object [ "pins" ]
    && builtins.isList selectedValue.object.pins
    && builtins.length selectedValue.object.pins <= 1024
    && all nativePinValid selectedValue.object.pins
    && unique (map (row: row.package) selectedValue.object.pins)
    && unique (map (row: row.repositoryURL) selectedValue.object.pins);
  nativeJoin =
    nativeShape
    && builtins.length selectedValue.object.pins == builtins.length (names pins)
    && all (
      identity:
      let
        pin = pins.${identity};
        matches = builtins.filter (row: row.repositoryURL == pin.sourceLocation) selectedValue.object.pins;
      in
      builtins.length matches == 1
      && (builtins.head matches).package == pin.name
      && (builtins.head matches).state.branch == pin.branch
      && (builtins.head matches).state.revision == pin.revision
      && (builtins.head matches).state.version == pin.version
    ) (names pins);
  packages = [
    {
      prefix = "";
      path = source;
    }
  ]
  ++ map (
    row:
    let
      info = localInfo row;
    in
    {
      prefix = info.relative + "/";
      path = info.facts.path;
    }
  ) locals;
  manifestNames =
    directory:
    builtins.filter (name: builtins.match "Package(@swift-[0-9.]+)?\\.swift" name != null) (
      names (builtins.readDir directory)
    );
  manifestsValid =
    shape
    && all (
      package:
      let
        entries = builtins.readDir package.path;
        manifests = manifestNames package.path;
      in
      builtins.elem "Package.swift" manifests
      && all (
        name:
        entries.${name} == "regular"
        && builtins.stringLength (builtins.readFile (package.path + "/" + name)) <= 2097152
      ) manifests
    ) packages;
  manifestRecords = builtins.concatLists (
    map (
      package:
      map (name: [
        (package.prefix + name)
        (builtins.hashFile "sha256" (package.path + "/" + name))
      ]) (manifestNames package.path)
    ) packages
  );
  manifestHash = builtins.hashString "sha256" (
    builtins.toJSON (
      builtins.sort (a: b: builtins.lessThan (builtins.head a) (builtins.head b)) manifestRecords
    )
  );
  problem =
    if !selectorValid then
      "invalid portable${toString descriptor.portable} source or selected pin/lock path/type"
    else if !rawValid || !shape then
      "invalid closed portable${toString descriptor.portable}/native${toString descriptor.native} schema"
    else if lock.contentHash != contentHash then
      "portable${toString descriptor.portable} content hash mismatch"
    else if
      !byteBound selectedRaw
      || !byteBound logicalRaw
      || !noDuplicates selectedRaw selectedValue
      || !nativeJoin
    then
      "invalid portable${toString descriptor.portable} selected native pins v1/graph"
    else if !manifestsValid || lock.manifestHash != manifestHash then
      "stale portable${toString descriptor.portable} root/local manifest currency"
    else
      null;
  usable = problem == null;
  reference = identity: {
    inherit identity;
    kind = "remoteSourceControl";
    name = pins.${identity}.name;
    location = pins.${identity}.sourceLocation;
  };
  checkoutRecord = identity: {
    basedOn = null;
    packageRef = reference identity;
    subpath = checkoutPath pins.${identity}.sourceLocation;
    state = {
      name = "sourceControlCheckout";
      checkoutState = builtins.listToAttrs (
        builtins.concatLists (
          map
            (
              key:
              if pins.${identity}.${key} == null then
                [ ]
              else
                [
                  {
                    name = key;
                    value = pins.${identity}.${key};
                  }
                ]
            )
            [
              "branch"
              "revision"
              "version"
            ]
        )
      );
    };
  };
  editedRecord = identity: {
    basedOn = checkoutRecord identity;
    packageRef = reference identity;
    subpath = identity;
    state = {
      name = "edited";
      path = null;
    };
  };
  entries = map (identity: {
    path = "Packages/${identity}";
    inherit (pins.${identity}) hash;
    git = {
      url = pins.${identity}.url or pins.${identity}.sourceLocation;
      rev = pins.${identity}.revision;
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
    outputs =
      if usable then
        builtins.listToAttrs (
          map (product: {
            inherit (product) name;
            value = product;
          }) products
        )
      else
        { };
  };
  package = if usable then lock.package else null;
  rootDirectory = if usable then lock.rootDirectory else null;
  products = if usable then products else [ ];
  inherit storeName;
  entryName = entry: storeName entry.path;
  lockVersion = descriptor.portable;
  nativeSDK = null;
  preWorkspace = null;
  toolLockName = "Package.resolved";
  toolLockFile = if usable then selected else null;
  toolLockBytes = if usable then selectedRaw else null;
  workspaceStateFile = descriptor.stateFile;
  workspaceState =
    if usable then
      {
        version = descriptor.native;
        object = {
          dependencies = locals ++ map editedRecord (names pins);
        }
        // (if descriptor.artifacts then { artifacts = [ ]; } else { });
      }
    else
      null;
}
