{ pkgs }:
pkgs.stdenvNoCC.mkDerivation {
  pname = "original-ubuntu-icu55";
  version = "55.1-4ubuntu1";
  src = pkgs.fetchurl {
    url = "https://old-releases.ubuntu.com/ubuntu/pool/main/i/icu/libicu55_55.1-4ubuntu1_amd64.deb";
    hash = "sha256-7LrYbkXAMB+ZdlEuCf3EYJrxH2l8yXQBC1pRyNDvknw=";
  };
  nativeBuildInputs = [ pkgs.dpkg ];
  dontUnpack = true;
  dontConfigure = true;
  dontBuild = true;
  dontFixup = true;
  installPhase = ''
    dpkg-deb -x "$src" "$TMPDIR/icu55"
    mkdir -p "$out/lib"
    cp -a "$TMPDIR/icu55/usr/lib/x86_64-linux-gnu/." "$out/lib/"
  '';
}
