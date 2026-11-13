# SPDX-License-Identifier: MIT
# Original 5.3.3 binary SDK; ordinary loader/header relocation only.
{ pkgs }:
let
  original = import ../swift553/default.nix { inherit pkgs; };
  originalLibedit = import ../swift424/libedit2.nix { inherit pkgs; };
  originalZ3 = import ./libz3.nix { inherit pkgs; };
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
    version = "5.3.3";
    src = pkgs.fetchurl {
      url = "https://download.swift.org/swift-5.3.3-release/ubuntu2004/swift-5.3.3-RELEASE/swift-5.3.3-RELEASE-ubuntu20.04.tar.gz";
      hash = "sha256-4mJPK1bNYwEaoaGF6j+prt8Vfv6G5rIbPqzFaalI514=";
    };
    sdkRuntimeLibraries = runtime;
    sdkGlibcHeaders = "${pkgs.glibc.dev}/include";
    sdkCUuidHeaders = "${pkgs.lib.getDev pkgs.libuuid}/include";
    installPhase = ''
      runHook preInstall
      mkdir -p "$out"
      cp -a usr/. "$out/"
      python3 ${./relocate-modulemap.py}
      python3 ${../swift553/wrap-original.py}
      runHook postInstall
    '';
    sdkClangArguments =
      builtins.replaceStrings [ old.sdkRuntimeLibraries ] [ runtime ]
        old.sdkClangArguments;
  }
)
