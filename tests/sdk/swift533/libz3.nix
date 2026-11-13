# SPDX-License-Identifier: MIT
# Archived original Ubuntu 20.04 libz3 ABI4; copied without payload rewrites.
{ pkgs }:
pkgs.stdenvNoCC.mkDerivation {
  pname = "original-ubuntu-libz3-4";
  version = "4.8.7-4build1";
  src = pkgs.fetchurl {
    url = "https://archive.ubuntu.com/ubuntu/pool/universe/z/z3/libz3-4_4.8.7-4build1_amd64.deb";
    hash = "sha256-P4I2GuQesp9MCclPD36I9U3br2jblMJpVxUo2Ik5TZQ=";
  };
  nativeBuildInputs = [ pkgs.dpkg ];
  dontUnpack = true;
  dontConfigure = true;
  dontBuild = true;
  dontFixup = true;
  installPhase = ''
    dpkg-deb -x "$src" "$TMPDIR/original-z3"
    mkdir -p "$out/lib" "$out/share/doc/libz3-4"
    cp -a "$TMPDIR/original-z3/usr/lib/x86_64-linux-gnu/." "$out/lib/"
    cp "$TMPDIR/original-z3/usr/share/doc/libz3-4/copyright" "$out/share/doc/libz3-4/"
  '';
}
