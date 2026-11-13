# SPDX-License-Identifier: MIT
# Original 5.2.5 binary SDK; ordinary loader/header relocation only.
{ pkgs }:
let
  original = import ../swift515/default.nix { inherit pkgs; };
in
original.overrideAttrs (old: {
  version = "5.2.5";
  src = pkgs.fetchurl {
    url = "https://download.swift.org/swift-5.2.5-release/ubuntu1604/swift-5.2.5-RELEASE/swift-5.2.5-RELEASE-ubuntu16.04.tar.gz";
    hash = "sha256-5XaKWX0y99yN+/3g14UL42pmCGDvH6OKCAQewJqsH6k=";
  };
  # This original SDK has 81 absolute header directives. Keep the exact
  # reverse-byte and actual-header guards used by the earlier SDK fixture.
  installPhase =
    builtins.replaceStrings [ "${../swift424/relocate-modulemap.py}" ] [ "${./relocate-modulemap.py}" ]
      old.installPhase;
})
