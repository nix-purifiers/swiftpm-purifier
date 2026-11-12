# SPDX-License-Identifier: MIT
# Repo-local format 6 only. No selected SDK or runtime is inspected here.
{ lock, nativeFile }:
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
  sha256 = value: builtins.isString value && builtins.match "[0-9a-f]{64}" value != null;
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
  binding = lock.nativeToolLock or null;
  products = lock.products or null;
  pinNames = if builtins.isAttrs pins then names pins else [ ];
  requiredPin = [
    "hash"
    "name"
    "revision"
    "sourceLocation"
    "version"
  ];
  pinValid =
    identity:
    let
      pin = pins.${identity};
    in
    safeName identity
    && builtins.isAttrs pin
    && all (name: builtins.hasAttr name pin) requiredPin
    && all (name: builtins.elem name (requiredPin ++ [ "url" ])) (names pin)
    && pin.name == identity
    && gitLocation pin.sourceLocation
    && revision pin.revision
    && semver pin.version
    && hash pin.hash
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
    && all safeName product.modules
    && unique product.modules;
  expectedFields = [
    "binaryTargets"
    "contentHash"
    "localDependencies"
    "manifestHash"
    "nativeToolLock"
    "package"
    "pins"
    "products"
    "rootDirectory"
    "version"
    "workspaceStateVersion"
  ];
  lockShape =
    builtins.isAttrs lock
    && names lock == expectedFields
    && builtins.isInt lock.version
    && lock.version == 6
    && builtins.isInt lock.workspaceStateVersion
    && lock.workspaceStateVersion == 1
    && lock.localDependencies == [ ]
    && lock.binaryTargets == [ ]
    && safeName lock.package
    && safeName lock.rootDirectory
    && sha256 lock.manifestHash
    && hash lock.contentHash
    && builtins.isAttrs pins
    && all pinValid pinNames
    && unique (map (name: pins.${name}.sourceLocation) pinNames)
    && builtins.isList products
    && all productValid products
    && unique (map (product: product.name) products);
  bindingShape =
    builtins.isAttrs binding
    &&
      names binding == [
        "file"
        "sha256"
      ]
    && binding.file == "Package.pins"
    && (binding.sha256 == null || sha256 binding.sha256);
  parent = builtins.dirOf (toString nativeFile);
  entries = if builtins.pathExists parent then builtins.readDir parent else { };
  fileType =
    entries.${builtins.unsafeDiscardStringContext (builtins.baseNameOf (toString nativeFile))}
    or "missing";
  raw = if fileType == "regular" then builtins.readFile nativeFile else "";
  native =
    if fileType == "regular" && builtins.stringLength raw <= 1048576 then
      builtins.fromJSON raw
    else
      null;
  nativePinValid =
    pin:
    builtins.isAttrs pin
    &&
      names pin == [
        "package"
        "reason"
        "repositoryURL"
        "version"
      ]
    && safeName pin.package
    && gitLocation pin.repositoryURL
    && semver pin.version
    && (pin.reason == null || builtins.isString pin.reason);
  # A closed top-level object plus closed pin objects lets us detect duplicate
  # JSON keys without canonicalizing or changing the original native bytes.
  # Strings are tokenized first so JSON-looking text inside reasons is inert.
  tokens = builtins.split "\"(([^\"\\\\]|\\\\.)*)\"" raw;
  indices = builtins.genList (i: i) (builtins.length tokens);
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
    ) indices
  );
  keyCount = name: builtins.length (builtins.filter (key: key == name) keys);
  noDuplicateKeys =
    let
      count = builtins.length native.pins;
    in
    builtins.length keys == 3 + 4 * count
    && keyCount "autoPin" == 1
    && keyCount "pins" == 1
    && keyCount "version" == 1 + count
    && all (name: keyCount name == count) [
      "package"
      "reason"
      "repositoryURL"
    ];
  nativeShape =
    builtins.isAttrs native
    &&
      names native == [
        "autoPin"
        "pins"
        "version"
      ]
    && builtins.isInt native.version
    && native.version == 1
    && builtins.isBool native.autoPin
    && builtins.isList native.pins
    && all nativePinValid native.pins
    && unique (map (pin: pin.package) native.pins)
    && unique (map (pin: pin.repositoryURL) native.pins)
    && noDuplicateKeys;
  joined = all (
    pin:
    let
      checkout = pins.${pin.package} or null;
    in
    checkout != null && checkout.sourceLocation == pin.repositoryURL && checkout.version == pin.version
  ) native.pins;
  fileValid =
    if fileType == "missing" then
      binding.sha256 == null && pins == { }
    else
      fileType == "regular"
      && nativeShape
      && binding.sha256 != null
      && builtins.hashString "sha256" raw == binding.sha256
      && joined;
  resolvedPins = map (identity: {
    inherit identity;
    kind = "remote"; # Explicit Git dispatch, never inferred from version.
    location = pins.${identity}.sourceLocation;
    state = { inherit (pins.${identity}) revision version; };
  }) pinNames;
  record = identity: pin: {
    basedOn = {
      basedOn = null;
      currentRevision = pin.revision;
      currentVersion = pin.version;
      repositoryURL = pin.sourceLocation;
      subpath = identity;
    };
    currentRevision = null;
    currentVersion = null;
    repositoryURL = pin.sourceLocation;
    subpath = identity;
  };
in
{
  valid = lockShape && bindingShape && fileValid;
  inherit resolvedPins;
  workspaceState = {
    dependencies = map (name: record name pins.${name}) pinNames;
    version = 1;
  };
}
