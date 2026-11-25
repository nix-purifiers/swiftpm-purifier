# SPDX-License-Identifier: MIT
# Private12/13 immutable source binding, not Git-policy or host ownership authority.
# Physical Git/NAR checks run in the normal validation derivation before fetching.
let
  names = builtins.attrNames;
  closed =
    value: fields: builtins.isAttrs value && names value == builtins.sort builtins.lessThan fields;
  sri =
    value:
    builtins.isString value
    && builtins.match "sha256-[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=" value != null;
  validName =
    value:
    builtins.isString value
    && builtins.stringLength value > 0
    && builtins.stringLength value <= 211
    && builtins.match "[A-Za-z0-9+._?=-]+" value != null
    && builtins.match "\\.\\.?(-.*)?" value == null;
  rootParts =
    value:
    if builtins.isString value then
      builtins.match "/nix/store/([0123456789abcdfghijklmnpqrsvwxyz]{32})-([A-Za-z0-9+._?=-]+)" value
    else
      null;
  rootValid =
    value:
    let
      parts = rootParts value;
    in
    parts != null && validName (builtins.elemAt parts 1);
  leafValid =
    value:
    builtins.isString value
    && builtins.stringLength value <= 255
    && builtins.match "[A-Za-z0-9][A-Za-z0-9._+-]*" value != null;
  split = value: builtins.filter builtins.isString (builtins.split "/" value);
  nativeLocationValid =
    value:
    if !builtins.isString value then
      false
    else
      let
        parts = split value;
      in
      builtins.length parts == 5
      && builtins.elemAt parts 0 == ""
      && builtins.elemAt parts 1 == "nix"
      && builtins.elemAt parts 2 == "store"
      && rootValid ("/nix/store/" + builtins.elemAt parts 3)
      && leafValid (builtins.elemAt parts 4);
  # Paths are ASCII Nix names plus one restricted leaf. This matches Python quote(safe="/").
  escapeURI = builtins.replaceStrings [ "+" "?" "=" ] [ "%2B" "%3F" "%3D" ];
  canonicalURI = location: "file://" + escapeURI location;
  subpath =
    leaf:
    let
      match = builtins.match "(.*)\\.git" leaf;
    in
    if match == null then leaf else builtins.head match;
  hex = "0123456789abcdef";
  hexIndex = builtins.listToAttrs (
    builtins.genList (i: {
      name = builtins.substring i 1 hex;
      value = i;
    }) 16
  );
  alphabet = "0123456789abcdfghijklmnpqrsvwxyz";
  powers = [
    1
    2
    4
    8
    16
    32
    64
    128
    256
  ];
  # https://nix.dev/manual/nix/2.34/protocols/store-path
  # Recursive SHA256 source identity with NO references. No store query or physical check.
  contentAddressedRoot =
    narHash: name:
    let
      inner = builtins.convertHash {
        hash = narHash;
        hashAlgo = "sha256";
        toHashFormat = "base16";
      };
      digest = builtins.hashString "sha256" "source:sha256:${inner}:/nix/store:${name}";
      byte =
        index:
        16 * hexIndex.${builtins.substring (2 * index) 1 digest}
        + hexIndex.${builtins.substring (2 * index + 1) 1 digest};
      compressed = builtins.genList (
        index: if index + 20 < 32 then builtins.bitXor (byte index) (byte (index + 20)) else byte index
      ) 20;
      encoded = builtins.concatStringsSep "" (
        builtins.genList (
          index:
          let
            group = 31 - index;
            bit = group * 5;
            offset = builtins.div bit 8;
            shift = bit - offset * 8;
            low = builtins.div (builtins.elemAt compressed offset) (builtins.elemAt powers shift);
            high =
              if offset + 1 < 20 then
                (builtins.elemAt compressed (offset + 1)) * (builtins.elemAt powers (8 - shift))
              else
                0;
          in
          builtins.substring (builtins.bitAnd 31 (low + high)) 1 alphabet
        ) 32
      );
    in
    assert sri narHash && validName name;
    "/nix/store/${encoded}-${name}";
  present = pin: builtins.isAttrs pin && (pin ? sourceKind || pin ? storeSource);
  binding =
    pin:
    let
      meta = if builtins.isAttrs pin then pin.storeSource or null else null;
      shape =
        builtins.isAttrs pin
        && (pin.sourceKind or null) == "localSourceControl"
        && !(pin ? url)
        && closed meta [
          "path"
          "narHash"
          "subdirectory"
        ]
        && rootValid meta.path
        && sri meta.narHash
        && leafValid meta.subdirectory
        && subpath meta.subdirectory != "";
      path = if shape then builtins.unsafeDiscardStringContext meta.path else null;
      narHash = if shape then builtins.unsafeDiscardStringContext meta.narHash else null;
      leaf = if shape then builtins.unsafeDiscardStringContext meta.subdirectory else null;
      name = if shape then builtins.elemAt (rootParts path) 1 else null;
      locationText = if shape then path + "/" + leaf else null;
      valid =
        shape
        && contentAddressedRoot narHash name == path
        && builtins.isString (pin.sourceLocation or null)
        && pin.sourceLocation == canonicalURI locationText;
      # Raw JSON supplies only data. Reconstruct the single declared Nix context explicitly.
      input =
        if valid then
          let
            result = builtins.path {
              path = builtins.toPath path;
              inherit name;
              sha256 = narHash;
            };
          in
          assert toString result == path;
          result
        else
          null;
      location = if valid then "${input}/${leaf}" else null;
    in
    {
      inherit
        valid
        locationText
        input
        location
        ;
      kind = "localSourceControl";
      checkoutSubpath = if shape then subpath leaf else null;
      uri = if valid then canonicalURI location else null;
      storeSource =
        if valid then
          {
            path = toString input;
            inherit narHash;
            subdirectory = leaf;
          }
        else
          null;
      declaration =
        if valid then
          {
            version = 1;
            sourceKind = "localSourceControl";
            storeSource = {
              path = toString input;
              inherit narHash;
              subdirectory = leaf;
            };
            revision = pin.revision;
            branch = pin.branch;
            packageVersion = pin.version;
          }
        else
          null;
    };
in
{
  kind = "localSourceControl";
  inherit
    present
    binding
    nativeLocationValid
    canonicalURI
    contentAddressedRoot
    validName
    ;
}
