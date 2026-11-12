# SPDX-License-Identifier: MIT
# Shared source-owned base environment for the selected Swift package set.
{
  lib,
  swiftPackages,
  git,
  unzip,
}:
swiftPackages.stdenv.mkDerivation {
  name = "swiftpm-purifier-swift-environment";
  nativeBuildInputs = [
    swiftPackages.swift
    swiftPackages.swiftpm
    git
    # SwiftPM unpacks binary-target archives with unzip.
    unzip
  ];
  buildInputs = [
    swiftPackages.swift
    swiftPackages.swiftpm
  ];
  dontUnpack = true;
  dontBuild = true;
  installPhase = ''
    for name in $(printf '%s\n' "''${!NIX_@}" | grep -vE '^NIX_(BUILD_[A-Z_]+|LOG_FD|STORE|ATTRS_[A-Z_]+|SSL_CERT_FILE|ENFORCE_PURITY|PATH)$'); do
      value=''${!name}
      # The rpath to this derivation's own lib/ does not belong in the
      # command's builds.
      value=''${value//-rpath $out\/lib/}
      printf 'export %s=%q\n' "$name" "$value"
    done > $out
    printf 'export PATH=%q"''${PATH:+:$PATH}"\n' "$PATH" >> $out
    printf 'export SWIFT_EXEC=%q\n' ${lib.escapeShellArg "${swiftPackages.swift}/bin/swiftc"} >> $out
  ''
  # On macOS the SDK's setup hook names the SDK in DEVELOPER_DIR and
  # SDKROOT, where SwiftPM and xcrun look for it.
  + lib.optionalString swiftPackages.stdenv.hostPlatform.isDarwin ''
    for name in "''${!DEVELOPER_DIR@}" "''${!SDKROOT@}"; do
      printf 'export %s=%q\n' "$name" "''${!name}"
    done >> $out
  '';
}
