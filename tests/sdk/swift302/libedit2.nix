# Original archived Ubuntu libedit ABI2. Payload copied without rewrites.
{ pkgs }:
pkgs.stdenvNoCC.mkDerivation {
  pname = "original-ubuntu-libedit2";
  version = "3.1-20170329-1";
  src = pkgs.fetchurl {
    url = "https://old-releases.ubuntu.com/ubuntu/pool/main/libe/libedit/libedit2_3.1-20170329-1_amd64.deb";
    hash = "sha256-QUzCi+rEVreBQLCgdVgDTEophyErNHQB27bh03XxHTI=";
  };
  nativeBuildInputs = [ pkgs.dpkg ];
  dontUnpack = true;
  dontConfigure = true;
  dontBuild = true;
  dontFixup = true;
  installPhase = ''
    dpkg-deb -x "$src" "$TMPDIR/original-libedit2"
    mkdir -p "$out/lib" "$out/share/doc/libedit2"
    cp -a "$TMPDIR/original-libedit2/usr/lib/x86_64-linux-gnu/." "$out/lib/"
    cp "$TMPDIR/original-libedit2/usr/share/doc/libedit2/copyright" "$out/share/doc/libedit2/"
  '';
}
