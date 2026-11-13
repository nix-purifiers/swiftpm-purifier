# SPDX-License-Identifier: MIT
#
# Reads a swiftpm-purifier lockfile (written by `swiftpm-purifier lock`)
# together with the manifests and the Package.resolved in `src`.
#
# Package.resolved names each remote dependency and the revision or
# version it is pinned to. The lockfile adds what Package.resolved lacks:
# the content hash of each pin, the package name that the manifest of each
# dependency declares, the URL a mirror or a registry serves it from, and
# what the manifests declare beyond the pins (binary targets, path
# dependencies and products).
#
# The result's `lock` is the lock in memory of the purifier contract:
# `entries`, the purifier library entries to fetch; `outputs`, the
# products by name; and `problems`, one message for a lockfile from
# another format version, with a missing field, changed by hand, or out of
# date with the manifests or the pins, each naming the command that fixes
# it. The other attributes are what the build needs besides: the SwiftPM
# workspace state that marks every pinned dependency as edited, and the
# package names from the manifest. They hold values only when `problems`
# is empty.
#
# Needs no nixpkgs. `lockFile` is a path, or an absolute path as a string.
{
  lockFile,
  src,
  # SwiftPM's lockfile. A flake that keeps its own copy outside `src` passes
  # it here, and the build uses it in place of the one in `src`.
  packageResolved ? null,
  selectedSDK ? null,
}@args:
let
  relock = "Run `swiftpm-purifier relock` to write it again.";

  # Nix cannot catch a parse error, so a lockfile that is not valid JSON
  # fails evaluation, and the context names the command that fixes it.
  lock = builtins.addErrorContext "swiftpm-purifier: ${toString lockFile} is not valid JSON. ${relock}" (
    builtins.fromJSON (builtins.readFile lockFile)
  );

  supportedVersion = import ./lock-version.nix;
  sdk = if (lock.version or null) == supportedVersion then lock.nativeSDK or null else null;
  sdkContract = import ./sdk-contract.nix {
    identity = sdk;
    selected = selectedSDK;
  };

  fields = [
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
  missingFields = builtins.filter (field: !(lock ? ${field})) fields;

  # The lock run writes a hash of the lockfile without this field, of the
  # JSON swiftpm-purifier and builtins.toJSON both write: keys in order, no
  # spaces. A lockfile changed by hand no longer matches it.
  contentHash = builtins.convertHash {
    hash = builtins.hashString "sha256" (builtins.toJSON (builtins.removeAttrs lock [ "contentHash" ]));
    hashAlgo = "sha256";
    toHashFormat = "sri";
  };

  # The hash of the manifests, computed the way swiftpm-purifier computes
  # it: the SHA-256 of the JSON list of [name, hex SHA-256] of each
  # manifest, in name order.
  manifests = builtins.sort builtins.lessThan (
    builtins.filter (name: builtins.match "Package(@swift-[0-9.]+)?\\.swift" name != null) (
      builtins.attrNames (builtins.readDir src)
    )
  );
  manifestHash = builtins.hashString "sha256" (
    builtins.toJSON (
      map (name: [
        name
        (builtins.hashFile "sha256" (src + "/${name}"))
      ]) manifests
    )
  );

  # SwiftPM writes no Package.resolved for a package without remote
  # dependencies, and such a package has no pins.
  preWorkspaceFormat = (lock.version or null) == 7;
  preWorkspace = import ./pre-workspace-state.nix {
    inherit
      lock
      src
      lockFile
      packageResolved
      ;
    raw = builtins.readFile lockFile;
  };
  legacyFormat = (lock.version or null) == 6;
  toolLockName =
    if preWorkspaceFormat then
      null
    else if legacyFormat then
      "Package.pins"
    else
      "Package.resolved";
  resolvedFile =
    if preWorkspaceFormat then
      null
    else if packageResolved != null then
      packageResolved
    else
      src + "/${toolLockName}";
  legacy = import ./legacy-state.nix {
    inherit lock;
    nativeFile = resolvedFile;
  };
  logicalLegacyType = (builtins.readDir src)."Package.pins" or "missing";
  resolved =
    if preWorkspaceFormat then
      null
    else if builtins.pathExists resolvedFile then
      builtins.fromJSON (builtins.readFile resolvedFile)
    else
      {
        version = 3;
        pins = [ ];
      };
  # Format 1 records a package name rather than its native identity. The
  # lock run joins source locations to identities from the selected SDK's
  # actual workspace, preserving Unicode and custom identity behavior.
  legacyPin =
    pin:
    let
      name = pin.package or null;
      location = pin.repositoryURL or null;
      state = pin.state or null;
      optionalText = value: value == null || (builtins.isString value && value != "");
      valid =
        builtins.isAttrs pin
        &&
          builtins.attrNames pin == [
            "package"
            "repositoryURL"
            "state"
          ]
        && builtins.isString name
        && name != ""
        && builtins.isString location
        && location != ""
        && builtins.isAttrs state
        && builtins.all (
          field:
          builtins.elem field [
            "branch"
            "revision"
            "version"
          ]
        ) (builtins.attrNames state)
        && builtins.isString (state.revision or null)
        && builtins.match "[0-9a-f]{40}([0-9a-f]{24})?" state.revision != null
        && optionalText (state.branch or null)
        && optionalText (state.version or null)
        && !((state.branch or null) != null && (state.version or null) != null);
      matches = builtins.filter (identity: (lock.pins.${identity}.sourceLocation or null) == location) (
        builtins.attrNames lock.pins
      );
      identity = builtins.head matches;
    in
    if !valid then
      throw "swiftpm-purifier: invalid version-1 pin"
    else if builtins.length matches != 1 then
      throw "swiftpm-purifier: missing or ambiguous native source identity"
    else if (lock.pins.${identity}.name or null) != name then
      throw "swiftpm-purifier: version-1 package name disagrees with native identity"
    else
      {
        inherit identity location;
        kind = if lock.workspaceStateVersion == 7 then "remoteSourceControl" else "remote";
        state = {
          branch = state.branch or null;
          revision = state.revision;
          version = state.version or null;
        };
      };
  resolvedPins =
    if preWorkspaceFormat then
      preWorkspace.resolvedPins
    else if legacyFormat then
      legacy.resolvedPins
    else if (resolved.version or null) == 1 then
      if
        !(builtins.isAttrs (resolved.object or null) && builtins.isList (resolved.object.pins or null))
      then
        throw "swiftpm-purifier: invalid version-1 pins"
      else
        map legacyPin resolved.object.pins
    else
      resolved.pins;

  # The revision, or for a registry pin the version, that a pin and the
  # lockfile's record of it name.
  pinnedAt = pin: pin.state.revision or pin.state.version;
  lockedAt = locked: locked.revision or locked.version;
  isRegistry = pin: pin.kind == "registry";

  covered = builtins.all (
    pin: lock.pins ? ${pin.identity} && lockedAt lock.pins.${pin.identity} == pinnedAt pin
  ) resolvedPins;
  sameSet =
    builtins.sort builtins.lessThan (map (pin: pin.identity) resolvedPins)
    == builtins.attrNames lock.pins;

  uniqueStrings =
    values:
    builtins.length (
      builtins.attrNames (
        builtins.listToAttrs (
          map (name: {
            inherit name;
            value = true;
          }) values
        )
      )
    ) == builtins.length values;
  legacyNames =
    map (identity: lock.pins.${identity}.name) (builtins.attrNames lock.pins)
    ++ map (dependency: dependency.packageRef.name) lock.localDependencies;
  legacyIdentities =
    builtins.attrNames lock.pins
    ++ map (dependency: dependency.packageRef.identity) lock.localDependencies;

  legacyLocalValid =
    dependency:
    let
      ref = dependency.packageRef or { };
      matched =
        if builtins.isString (ref.path or null) then builtins.match "@root@/(.+)" ref.path else null;
      relative = if matched == null then "" else builtins.head matched;
      components = builtins.filter builtins.isString (builtins.split "/" relative);
      directory =
        builtins.foldl'
          (
            current: part:
            let
              entries = if current.valid then builtins.readDir current.path else { };
            in
            {
              path = current.path + "/${part}";
              valid = current.valid && (entries.${part} or null) == "directory";
            }
          )
          {
            path = src;
            valid = true;
          }
          components;
    in
    builtins.isAttrs dependency
    &&
      builtins.attrNames dependency == [
        "basedOn"
        "packageRef"
        "state"
        "subpath"
      ]
    && dependency.basedOn == null
    && dependency.state == { name = "local"; }
    && builtins.isAttrs ref
    &&
      builtins.attrNames ref == [
        "identity"
        "isLocal"
        "name"
        "path"
      ]
    && ref.isLocal == true
    && builtins.isString ref.identity
    && ref.identity != ""
    && builtins.isString ref.name
    && ref.name != ""
    && dependency.subpath == ref.identity
    && matched != null
    && builtins.all (part: part != "" && part != "." && part != "..") components
    && directory.valid
    && ((builtins.readDir directory.path)."Package.swift" or null) == "regular"
    && builtins.isString (builtins.readFile (directory.path + "/Package.swift"));

  # The first problem in the order the checks depend on each other: a later
  # check reads what an earlier one verified.
  problem =
    if
      !(
        lock ? version
        && builtins.isInt lock.version
        && builtins.elem lock.version [
          4
          supportedVersion
          6
          7
        ]
      )
    then
      "swiftpm-purifier: ${toString lockFile} has lock format version ${
        toString (lock.version or "none")
      }; this library reads versions 4, ${toString supportedVersion}, 6 and 7. Run `swiftpm-purifier relock` with the swiftpm-purifier revision of this library."
    else if (lock.workspaceStateVersion or null) == 3 then
      "swiftpm-purifier: native dependency-state 3 requires portable format 9; formats 4 and 5 cannot describe this protocol. ${relock}"
    else if preWorkspaceFormat && !preWorkspace.valid then
      "swiftpm-purifier: invalid pre-workspace format-7 data, source layout, native absence, or lock destination. ${relock}"
    else if !preWorkspaceFormat && missingFields != [ ] then
      "swiftpm-purifier: ${toString lockFile} lacks ${builtins.concatStringsSep ", " missingFields}. ${relock}"
    else if lock.contentHash != contentHash then
      "swiftpm-purifier: ${toString lockFile} does not match its contentHash, so it was changed after the lock run wrote it. ${relock}"
    else if (lock.workspaceStateVersion or null) == 1 && !legacyFormat then
      "swiftpm-purifier: native workspace format 1 requires portable lock format 6. ${relock}"
    else if legacyFormat && !legacy.valid then
      "swiftpm-purifier: invalid portable legacy format-6 data or native Package.pins binding. ${relock}"
    else if
      legacyFormat
      && !(builtins.elem logicalLegacyType [
        "regular"
        "missing"
      ])
    then
      "swiftpm-purifier: logical Package.pins must be a regular file or absent. ${relock}"
    else if
      legacyFormat
      && packageResolved == null
      && builtins.pathExists resolvedFile
      && builtins.pathExists (src + "/Package.resolved")
    then
      "swiftpm-purifier: both native pin file formats are present; select packageResolved explicitly."
    else if lock.version == supportedVersion && sdkContract.problem != null then
      "swiftpm-purifier: ${sdkContract.problem}. ${relock}"
    else if
      !preWorkspaceFormat
      && lock.workspaceStateVersion == 2
      && (
        (builtins.pathExists resolvedFile && (resolved.version or null) != 1)
        || !(builtins.all legacyLocalValid lock.localDependencies)
        || lock.binaryTargets != [ ]
        || !(builtins.all (
          identity:
          let
            name = lock.pins.${identity}.name or null;
          in
          builtins.isString name && builtins.match "[A-Za-z0-9_][A-Za-z0-9_.-]*" name != null
        ) (builtins.attrNames lock.pins))
        || !uniqueStrings legacyNames
        || !uniqueStrings legacyIdentities
      )
    then
      "swiftpm-purifier: unsupported or unsafe dependency-state format-2 lock. ${relock}"
    else if lock.manifestHash != manifestHash then
      "swiftpm-purifier: ${toString lockFile} is out of date with Package.swift in ${toString src}. Run `swiftpm-purifier lock`."
    else if
      !preWorkspaceFormat
      && !legacyFormat
      && (
        !builtins.isInt (resolved.version or null)
        || !builtins.elem (resolved.version or null) [
          1
          2
          3
        ]
      )
    then
      "swiftpm-purifier: ${toString resolvedFile} has format version ${
        toString (resolved.version or "none")
      }, which this library does not support. Run `swiftpm-purifier lock` with the project's toolchain."
    else if !(covered && sameSet) then
      "swiftpm-purifier: ${toString lockFile} is out of date with ${toString resolvedFile}. Run `swiftpm-purifier lock`."
    else
      null;
  problems = if problem == null then [ ] else [ problem ];
  usable = problems == [ ];

  # SwiftPM keeps a source control checkout under the last component of the
  # URL without its `.git` suffix, which keeps the case of the URL where
  # the identity is lowercase, and a registry download under
  # scope/name/version. The lock run checks that the workspace state
  # SwiftPM wrote uses these paths.
  subpathOf =
    pin:
    if isRegistry pin then
      let
        parts = builtins.match "([^.]+)\\.(.+)" pin.identity;
      in
      "${builtins.elemAt parts 0}/${builtins.elemAt parts 1}/${pin.state.version}"
    else
      let
        base = builtins.elemAt (builtins.match ".*/([^/]+)/*" pin.location) 0;
        unsuffixed = builtins.match "(.*)\\.git" base;
      in
      if unsuffixed == null then base else builtins.head unsuffixed;

  packageRefOf =
    pin:
    if lock.workspaceStateVersion == 2 then
      {
        inherit (pin) identity;
        isLocal = false;
        path = pin.location;
        inherit (lock.pins.${pin.identity}) name;
      }
    else
      {
        inherit (pin) identity kind;
        location = if isRegistry pin then pin.identity else pin.location;
        inherit (lock.pins.${pin.identity}) name;
      };

  checkoutStateOf =
    pin:
    if isRegistry pin then
      {
        name = "registryDownload";
        inherit (pin.state) version;
      }
    else
      {
        name = if pin.kind == "remote" then "checkout" else "sourceControlCheckout";
        checkoutState =
          if lock.workspaceStateVersion == 7 then
            builtins.removeAttrs pin.state (
              builtins.filter (key: pin.state.${key} == null) (builtins.attrNames pin.state)
            )
          else
            pin.state;
      };

  recordOf =
    pin:
    let
      subpath = if lock.workspaceStateVersion == 2 then lock.pins.${pin.identity}.name else subpathOf pin;
      packageRef = packageRefOf pin;
    in
    {
      basedOn = {
        basedOn = null;
        inherit packageRef subpath;
        state = checkoutStateOf pin;
      };
      inherit packageRef subpath;
      state = {
        name = "edited";
        path = null;
      };
    };

  entryOf =
    pin:
    let
      locked = lock.pins.${pin.identity};
    in
    {
      path = "Packages/${
        if legacyFormat || lock.workspaceStateVersion == 2 then locked.name else subpathOf pin
      }";
      inherit (locked) hash;
    }
    // (
      if isRegistry pin then
        {
          archive = {
            inherit (locked) url;
            mirrors = [ ];
            stripTop = true;
          };
        }
      else
        {
          # SwiftPM uses an edited dependency as a plain source tree, so the
          # checkout needs no .git.
          git = {
            url = locked.url or pin.location;
            rev = pin.state.revision;
            submodules = true;
            leaveDotGit = false;
            commitObject = null;
          };
        }
    );

  # A remote binary target is fetched into the directory above its bundle.
  # Each bundle path starts with "@root@/", which is 7 characters, and its
  # hash is the checksum the manifest declares.
  binaryEntryOf = target: {
    path = dirOf (builtins.substring 7 (builtins.stringLength target.path) target.path);
    archive = {
      inherit (target.source) url;
      mirrors = [ ];
      stripTop = false;
    };
    hash = builtins.convertHash {
      hash = target.source.checksum;
      hashAlgo = "sha256";
      toHashFormat = "sri";
    };
  };

  # A store path name from a tree path. Store path names allow
  # [A-Za-z0-9+._?=-], must not start with a dot, and are at most 211
  # characters; every other character becomes "_", and a long name keeps
  # its end. The swift wrapper and the sandboxed build both name their
  # fetches with this function, so they share store paths.
  storeName =
    path:
    let
      # builtins.split alternates the text between matches with a list per
      # match.
      replaced = builtins.concatStringsSep "" (
        map (part: if builtins.isList part then "_" else part) (builtins.split "[^A-Za-z0-9+._?=-]" path)
      );
      length = builtins.stringLength replaced;
      tail = if length > 200 then builtins.substring (length - 200) 200 replaced else replaced;
      undotted = builtins.head (builtins.match "\\.*(.*)" tail);
    in
    if undotted == "" then "entry" else undotted;
in
if builtins.isAttrs lock && (lock.version or null) == 9 then
  import ./dependencies-v3-state.nix (
    {
      inherit
        lock
        src
        lockFile
        storeName
        ;
      raw = builtins.readFile lockFile;
    }
    // (if args ? packageResolved then { inherit packageResolved; } else { })
  )
else if builtins.isAttrs lock && (lock.version or null) == 8 then
  import ./dependencies-v1-state.nix (
    {
      inherit
        lock
        src
        lockFile
        storeName
        ;
      raw = builtins.readFile lockFile;
    }
    // (if args ? packageResolved then { inherit packageResolved; } else { })
  )
else
  {
    lock = {
      purifier = "swiftpm-purifier";
      inherit problems;

      entries =
        if usable then
          if preWorkspaceFormat then
            preWorkspace.entries
          else
            map entryOf resolvedPins
            ++ map binaryEntryOf (builtins.filter (target: target.source.type == "remote") lock.binaryTargets)
        else
          [ ];

      # Each product of the package by name, with its type and, for a
      # library, its linkage.
      outputs =
        if usable then
          builtins.listToAttrs (
            map (product: {
              inherit (product) name;
              value = product;
            }) lock.products
          )
        else
          { };
    };

    inherit (lock) package rootDirectory products;
    inherit toolLockName;
    preWorkspace = if preWorkspaceFormat then preWorkspace else null;
    toolLockFile = resolvedFile;
    lockVersion = lock.version;
    nativeSDK = sdk;
    inherit storeName;

    # The fetch name of an entry: its tree path, made a valid store name.
    entryName = entry: storeName entry.path;

    workspaceState =
      if preWorkspaceFormat then
        null
      else if legacyFormat then
        legacy.workspaceState
      else
        {
          object = {
            dependencies = lock.localDependencies ++ map recordOf resolvedPins;
          }
          // (if lock.workspaceStateVersion == 2 then { } else { artifacts = lock.binaryTargets; })
          // (
            if lock.workspaceStateVersion == 2 then
              { }
            else if lock.workspaceStateVersion == 7 || (resolved.version or null) != 1 then
              { prebuilts = [ ]; }
            else
              { }
          );
          version = lock.workspaceStateVersion;
        };
    workspaceStateFile =
      if preWorkspaceFormat then
        null
      else if lock.workspaceStateVersion == 2 then
        "dependencies-state.json"
      else
        "workspace-state.json";
  }
