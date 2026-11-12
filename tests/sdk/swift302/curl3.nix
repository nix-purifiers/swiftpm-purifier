{ pkgs }:
pkgs.stdenvNoCC.mkDerivation {
  pname = "original-ubuntu-curl3-runtime";
  version = "7.47.0-1ubuntu2.19";
  srcs = [
    (pkgs.fetchurl {
      url = "https://archive.ubuntu.com/ubuntu/pool/main/c/curl/libcurl3_7.47.0-1ubuntu2.19_amd64.deb";
      hash = "sha256-VHhiXi9tOPRMXDNq0Ms/g+gO9vPijlFmW7n1aATlnjg=";
    })
    (pkgs.fetchurl {
      url = "https://archive.ubuntu.com/ubuntu/pool/main/o/openssl/libssl1.0.0_1.0.2g-1ubuntu4.20_amd64.deb";
      hash = "sha256-gAP5hmKX3Q/nfMdxRwoGgD0dG4YDUK2R3SEuOMNqINI=";
    })
    (pkgs.fetchurl {
      url = "https://archive.ubuntu.com/ubuntu/pool/main/o/openldap/libldap-2.4-2_2.4.42+dfsg-2ubuntu3.13_amd64.deb";
      hash = "sha256-kyguwYdYb24t4SeuPysXF8Z0BboK98EhE4xkjOtau3g=";
    })
    (pkgs.fetchurl {
      url = "https://archive.ubuntu.com/ubuntu/pool/main/libi/libidn/libidn11_1.32-3ubuntu1.2_amd64.deb";
      hash = "sha256-/2SJDXOMOIX0XXGCwssJZr4sQf/xCDvT2meWP7b7PO8=";
    })
    (pkgs.fetchurl {
      url = "https://archive.ubuntu.com/ubuntu/pool/main/c/cyrus-sasl2/libsasl2-2_2.1.26.dfsg1-14ubuntu0.2_amd64.deb";
      hash = "sha256-yVfKJGiuR3DObBca8RLGFs/qPCbyecvGBbeLyZiz948=";
    })

  ];
  nativeBuildInputs = [ pkgs.dpkg ];
  dontUnpack = true;
  dontConfigure = true;
  dontBuild = true;
  dontFixup = true;
  installPhase = ''
    mkdir -p "$out/lib"
    for deb in $srcs; do
     mkdir "$TMPDIR/curl-deb"
     dpkg-deb -x "$deb" "$TMPDIR/curl-deb"
     for directory in "$TMPDIR/curl-deb/lib/x86_64-linux-gnu" "$TMPDIR/curl-deb/usr/lib/x86_64-linux-gnu"; do
      if [ -d "$directory" ]; then cp -a "$directory/." "$out/lib/"; fi
     done
     rm -r "$TMPDIR/curl-deb"
    done
  '';
}
