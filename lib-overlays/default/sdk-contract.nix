# SPDX-License-Identifier: MIT
# Pure metadata validation. The native build/CLI additionally measure the
# selected physical closure; this reader does not reconstruct SDK functions.
{
  identity,
  selected ? null,
}:
let
  keys =
    value: expected:
    builtins.isAttrs value && builtins.attrNames value == builtins.sort builtins.lessThan expected;
  text = value: builtins.isString value && value != "";
  rootOf =
    path:
    if !builtins.isString path then
      null
    else
      let
        match = builtins.match "(/nix/store/[0-9a-z]{32}-[A-Za-z0-9+._?=-]{1,211})(/[^/]+)*" path;
        parts = builtins.filter builtins.isString (builtins.split "/" path);
      in
      if
        match == null
        || builtins.any (
          part:
          builtins.elem part [
            "."
            ".."
          ]
        ) parts
      then
        null
      else
        builtins.head match;
  root = path: rootOf path != null && rootOf path == path;
  hash = value: builtins.isString value && builtins.match "sha256-[A-Za-z0-9+/]{43}=" value != null;
  roles = [
    "swift"
    "swiftc"
    "package"
    "build"
    "test"
    "run"
  ];
  descriptor = identity.descriptor or { };
  components = descriptor.components or { };
  commands = descriptor.executables or { };
  closure = identity.closure or { };
  executables = identity.executables or { };
  environment = identity.environment or { };
  capabilities = identity.capabilities or { };
  versions = identity.versions or { };
  componentValid =
    component:
    keys component [
      "path"
      "recipe"
    ]
    && root component.path
    && root component.recipe
    && builtins.match ".*\\.drv" component.recipe != null;
  descriptorValid =
    keys descriptor [
      "version"
      "system"
      "environmentShell"
      "components"
      "executables"
    ]
    && builtins.isInt descriptor.version
    && descriptor.version == 1
    && text descriptor.system
    && rootOf descriptor.environmentShell != null
    && keys components [
      "swift"
      "swiftpm"
      "stdenv"
      "environment"
    ]
    && builtins.all componentValid (builtins.attrValues components)
    && keys commands roles
    && builtins.all (
      role:
      commands.${role} == components.${
        if
          builtins.elem role [
            "swift"
            "swiftc"
          ]
        then
          "swift"
        else
          "swiftpm"
      }.path
      + "/bin/"
      + (
        if
          builtins.elem role [
            "swift"
            "swiftc"
          ]
        then
          role
        else
          "swift-${role}"
      )
    ) roles;
  closureValid =
    builtins.isAttrs closure
    && builtins.length (builtins.attrNames closure) > 0
    && builtins.length (builtins.attrNames closure) <= 4096
    && builtins.all (path: root path && hash closure.${path}) (builtins.attrNames closure)
    && builtins.all (component: closure ? ${component.path}) (builtins.attrValues components)
    && closure ? ${rootOf descriptor.environmentShell};
  executablesValid =
    keys executables roles
    && builtins.all (
      role:
      let
        executable = executables.${role};
        resolvedRoot = rootOf (executable.resolved or null);
      in
      keys executable [
        "path"
        "resolved"
        "sha256"
      ]
      && executable.path == commands.${role}
      && resolvedRoot != null
      && closure ? ${resolvedRoot}
      && builtins.isString executable.sha256
      && builtins.match "[0-9a-f]{64}" executable.sha256 != null
    ) roles;
  environmentValid =
    keys environment [
      "variables"
      "pathPrefix"
    ]
    && builtins.isAttrs environment.variables
    && builtins.all builtins.isString (builtins.attrValues environment.variables)
    && text environment.pathPrefix
    && (environment.variables.SWIFT_EXEC or null) == commands.swiftc;
  optionValid =
    value: builtins.isString value && builtins.match "--[a-z0-9]+(-[a-z0-9]+)*" value != null;
  optionsValid =
    values:
    builtins.isList values
    && builtins.all optionValid values
    &&
      values == builtins.sort builtins.lessThan (
        builtins.attrNames (
          builtins.listToAttrs (
            map (name: {
              inherit name;
              value = true;
            }) values
          )
        )
      );
  capabilitiesValid =
    keys capabilities [
      "package"
      "build"
      "test"
      "run"
    ]
    && builtins.all optionsValid (builtins.attrValues capabilities);
  versionsValid =
    keys versions [
      "compiler"
      "swiftpm"
    ]
    && builtins.all (value: text value && builtins.stringLength value <= 4096) (
      builtins.attrValues versions
    );
  valid =
    keys identity [
      "version"
      "descriptor"
      "closure"
      "executables"
      "environment"
      "capabilities"
      "versions"
    ]
    && builtins.isInt identity.version
    && identity.version == 1
    && descriptorValid
    && closureValid
    && executablesValid
    && environmentValid
    && capabilitiesValid
    && versionsValid;
in
{
  inherit valid;
  problem =
    if !valid then
      "native SDK identity is malformed or incomplete"
    else if selected != null && descriptor != selected then
      "selected compiler, SwiftPM, environment or derivation recipe differs from the lock"
    else
      null;
}
