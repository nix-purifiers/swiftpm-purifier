# SPDX-License-Identifier: MIT
# Original 5.6.3 binary SDK; ordinary loader/header relocation only.
{ pkgs }:
let
  original = import ../swift553/default.nix { inherit pkgs; };
  originalLibedit = import ../swift424/libedit2.nix { inherit pkgs; };
  originalZ3 = import ../swift533/libz3.nix { inherit pkgs; };
in
original.overrideAttrs (
  old:
  let
    runtime =
      old.sdkRuntimeLibraries
      + ":"
      + pkgs.lib.makeLibraryPath [
        originalLibedit
        originalZ3
        pkgs.ncurses5
        pkgs.libbsd
      ];
  in
  {
    version = "5.6.3";
    src = pkgs.fetchurl {
      url = "https://download.swift.org/swift-5.6.3-release/ubuntu2004/swift-5.6.3-RELEASE/swift-5.6.3-RELEASE-ubuntu20.04.tar.gz";
      hash = "sha256-gagdRvUXtIXxbr5Bt88UEf1lLxKQJo1pgwYrkc867mc=";
    };
    sdkRuntimeLibraries = runtime;
    sdkCUuidHeaders = "${pkgs.lib.getDev pkgs.libuuid}/include";
    installPhase = ''
      runHook preInstall
      mkdir -p "$out"
      cp -a usr/. "$out/"
      python3 ${./relocate-cuuid.py}
      python3 ${./wrap-original.py}
      runHook postInstall
    '';
    sdkClangArguments =
      builtins.replaceStrings [ old.sdkRuntimeLibraries ] [ runtime ]
        old.sdkClangArguments;
  }
)
