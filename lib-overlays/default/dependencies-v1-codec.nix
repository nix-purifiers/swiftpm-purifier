# SPDX-License-Identifier: MIT
# Closed format8 structure only; source/selectors/ownership are separate gates.
{ lock }:
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
  closed =
    value: fields: builtins.isAttrs value && names value == builtins.sort builtins.lessThan fields;
  stringList =
    predicate: value: builtins.isList value && value != [ ] && all predicate value && unique value;
  topFields = [
    "version"
    "nativeProtocol"
    "nativeState"
    "package"
    "rootDirectory"
    "manifestHash"
    "sourceLayoutHash"
    "nativeToolLock"
    "pins"
    "products"
    "localDependencies"
    "binaryTargets"
    "contentHash"
  ];
  pinValid =
    identity:
    let
      pin = lock.pins.${identity};
    in
    safeName identity
    && closed pin [
      "name"
      "sourceLocation"
      "revision"
      "version"
      "hash"
    ]
    && pin.name == identity
    && gitLocation pin.sourceLocation
    && revision pin.revision
    && semver pin.version
    && hash pin.hash;
  declarationValid =
    product:
    let
      declaration = product.declaration;
    in
    builtins.isAttrs declaration
    && (
      if (declaration.kind or null) == "explicit" then
        closed declaration [
          "kind"
          "targets"
        ]
        && stringList safeName declaration.targets
      else
        product.type == "executable"
        && closed declaration [
          "kind"
          "target"
        ]
        && declaration.kind == "inferred-executable"
        && safeName declaration.target
        && declaration.target == product.name
    );
  productValid =
    product:
    builtins.isAttrs product
    && builtins.elem (product.type or null) [
      "library"
      "executable"
    ]
    && closed product (
      [
        "name"
        "type"
        "modules"
        "declaration"
      ]
      ++ (if product.type == "library" then [ "linkage" ] else [ ])
    )
    && safeName product.name
    && stringList (
      value: builtins.isString value && builtins.match "[A-Za-z_][A-Za-z0-9_]*" value != null
    ) product.modules
    && declarationValid product
    && (
      product.type != "library"
      || builtins.elem product.linkage [
        "automatic"
        "static"
        "dynamic"
      ]
    );
  contentHash = builtins.convertHash {
    hash = builtins.hashString "sha256" (builtins.toJSON (builtins.removeAttrs lock [ "contentHash" ]));
    hashAlgo = "sha256";
    toHashFormat = "sri";
  };
  valid =
    closed lock topFields
    && builtins.isInt lock.version
    && lock.version == 8
    && lock.nativeProtocol == "dependencies-state-v1"
    && closed lock.nativeState [
      "file"
      "version"
    ]
    && lock.nativeState.file == ".build/dependencies-state.json"
    && builtins.isInt lock.nativeState.version
    && lock.nativeState.version == 1
    && safeName lock.package
    && safeName lock.rootDirectory
    && sha256 lock.manifestHash
    && sha256 lock.sourceLayoutHash
    && lock.localDependencies == [ ]
    && lock.binaryTargets == [ ]
    && closed lock.nativeToolLock [
      "file"
      "sha256"
    ]
    && lock.nativeToolLock.file == "Package.resolved"
    && (lock.nativeToolLock.sha256 == null || sha256 lock.nativeToolLock.sha256)
    && builtins.isAttrs lock.pins
    && names lock.pins != [ ]
    && builtins.length (names lock.pins) <= 1024
    && all pinValid (names lock.pins)
    && unique (map (name: lock.pins.${name}.sourceLocation) (names lock.pins))
    && builtins.isList lock.products
    && builtins.length lock.products <= 1024
    && all productValid lock.products
    && unique (map (product: product.name) lock.products)
    && hash lock.contentHash
    && lock.contentHash == contentHash;
in
{
  inherit valid;
}
