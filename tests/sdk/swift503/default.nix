# SPDX-License-Identifier: MIT
# Original binary SDK; ordinary loader/header relocation only.
{ pkgs }:
let
  original = import ../swift403/default.nix { inherit pkgs; };
  # Interpreter arguments after the script path belong to the manifest.
  # Keep its leading driver-mode selector first and insert compiler flags
  # before the script; ordinary compiler/frontend dispatch stays unchanged.
  wrapper = builtins.toFile "wrap-original-swift503.py" (
    builtins.replaceStrings
      [
        ''tool.name == "clang-10"''
        ''in swiftc:*|swift:-frontend) set -- "$@" ''
        ''+ " ;; esac\n"''
      ]
      [
        ''tool.name == "clang-7"''
        "in swiftc:--driver-mode=swift) shift; set -- --driver-mode=swift "
        ''
          + ' "$@" ;; swiftc:*|swift:-frontend) set -- "$@" '
          + " ".join(map(shlex.quote, ["-Xcc", "-nostdinc", "-Xcc", "-isystem", "-Xcc", str(root / "lib/swift/clang/include")]))
          + " ;; esac\n"
        ''
      ]
      (builtins.readFile ../swift403/wrap-original.py)
  );
in
original.overrideAttrs (old: {
  version = "5.0.3";
  src = pkgs.fetchurl {
    url = "https://download.swift.org/swift-5.0.3-release/ubuntu1604/swift-5.0.3-RELEASE/swift-5.0.3-RELEASE-ubuntu16.04.tar.gz";
    hash = "sha256-Eu+AWVb5Tomxx31Veo+j8MoTcPGpdPMPDhWnoyW5j74=";
  };
  installPhase = ''
        runHook preInstall
        mkdir -p "$out"
        cp -a usr/. "$out/"
        python3 ${../swift424/relocate-modulemap.py}
        python3 ${wrapper}
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
