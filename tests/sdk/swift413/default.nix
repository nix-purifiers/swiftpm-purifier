# SPDX-License-Identifier: MIT
# Original binary SDK; ordinary loader/header relocation only.
{ pkgs }:
let
  original = import ../swift403/default.nix { inherit pkgs; };
  relocator = builtins.toFile "relocate-swift413-modulemap.py" (
    builtins.replaceStrings [ "assert len(rows) == 77" ] [ "assert len(rows) == 78" ] (
      builtins.readFile ../swift403/relocate-modulemap.py
    )
  );
in
original.overrideAttrs (old: {
  version = "4.1.3";
  src = pkgs.fetchurl {
    url = "https://download.swift.org/swift-4.1.3-release/ubuntu1604/swift-4.1.3-RELEASE/swift-4.1.3-RELEASE-ubuntu16.04.tar.gz";
    hash = "sha256-zbPQhQ0DRh7GpwRx9fI70jjkYli23oDXk5W3gTawyNE=";
  };
  installPhase = ''
        runHook preInstall
        mkdir -p "$out"
        cp -a usr/. "$out/"
        python3 ${relocator}
        python3 ${../swift403/wrap-original.py}
        test ! -e "$out/bin/git"
        cat > "$out/bin/git" <<'GIT_WRAPPER'
    #!${pkgs.bash}/bin/bash
    unset LD_LIBRARY_PATH
    exec ${pkgs.git}/bin/git "$@"
    GIT_WRAPPER
        chmod +x "$out/bin/git"
        runHook postInstall
  '';
})
