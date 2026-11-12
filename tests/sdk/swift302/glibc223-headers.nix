{ pkgs }:
pkgs.stdenvNoCC.mkDerivation {
  pname = "original-ubuntu-glibc-headers";
  version = "2.23-0ubuntu11.3";
  src = pkgs.fetchurl {
    url = "https://security.ubuntu.com/ubuntu/pool/main/g/glibc/libc6-dev_2.23-0ubuntu11.3_amd64.deb";
    hash = "sha256-tbkQLI30wP/3cKI2wP65qy1DDr3bkQNZkno6FyXJOjc=";
  };
  nativeBuildInputs = [ pkgs.dpkg ];
  dontUnpack = true;
  dontConfigure = true;
  dontBuild = true;
  dontFixup = true;
  installPhase = ''
    dpkg-deb -x "$src" "$TMPDIR/original"
    mkdir -p "$out/include"
    cp -a "$TMPDIR/original/usr/include/." "$out/include/"
    cp -a "$TMPDIR/original/usr/include/x86_64-linux-gnu/." "$out/include/"
  '';
}
