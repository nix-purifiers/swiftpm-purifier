# SPDX-License-Identifier: MIT
# Concrete realized components from the running CLI, without rebuilding a
# default SDK or pretending store paths can encode arbitrary stdenv functions.
{
  lib,
  symlinkJoin,
  writeShellScriptBin,
}:
{
  cli,
  swift,
  swiftpm,
  editor ? null,
}:
let
  paths = map (path: builtins.storePath (/. + path)) (
    [
      cli
      swift
      swiftpm
    ]
    ++ lib.optional (editor != null) editor
  );
  driver = writeShellScriptBin "swift" ''
    exec ${lib.escapeShellArg cli}/bin/swiftpm-purifier swift "$@"
  '';
in
symlinkJoin {
  name = "swiftpm-selected-toolchain";
  paths = [ driver ] ++ paths;
}
