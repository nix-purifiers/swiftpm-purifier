# Local candidate for the original SDK test fixture. Not a support claim.
{ pkgs }:
let
  inherit (pkgs) lib;
  gcc = pkgs.stdenv.cc.cc;
  originalCurl = import ./curl3.nix { inherit pkgs; };
  originalIcu = import ./icu55.nix { inherit pkgs; };
  originalLibedit = import ./libedit2.nix { inherit pkgs; };
  gccDirectory = "${gcc}/lib/gcc/x86_64-unknown-linux-gnu/${gcc.version}";
  runtimeLibraries = lib.makeLibraryPath [
    gcc.lib
    pkgs.ncurses5
    pkgs.sqlite
    pkgs.libxml2_13
    originalCurl
    pkgs.libidn2
    pkgs.libkrb5
    pkgs.heimdal
    pkgs.cyrus_sasl
    pkgs.gnutls
    pkgs.rtmpdump
    pkgs.libssh2
    pkgs.nghttp2
    originalLibedit
    pkgs.libuuid
    pkgs.zlib
    originalIcu
    pkgs.libbsd
  ];
in
assert pkgs.stdenv.hostPlatform.system == "x86_64-linux";
pkgs.stdenvNoCC.mkDerivation {
  pname = "original-swift-sdk";
  version = "3.1.1";
  src = pkgs.fetchurl {
    url = "https://download.swift.org/swift-3.1.1-release/ubuntu1604/swift-3.1.1-RELEASE/swift-3.1.1-RELEASE-ubuntu16.04.tar.gz";
    hash = "sha256-vbG9Mb7TkeJ81q77ofyY4YwS32StA/JbPxQxs0SEHQk=";
  };
  nativeBuildInputs = [
    pkgs.python3
    pkgs.patchelf
  ];
  dontConfigure = true;
  dontBuild = true;
  dontFixup = true;
  sdkBash = "${pkgs.bash}/bin/bash";
  sdkPatchelf = "${pkgs.patchelf}/bin/patchelf";
  sdkInterpreter = "${pkgs.glibc}/lib/ld-linux-x86-64.so.2";
  sdkRuntimeLibraries = runtimeLibraries;
  sdkToolPath = lib.makeBinPath [
    pkgs.binutils-unwrapped
    pkgs.clang
    gcc
    pkgs.git
    pkgs.unzip
  ];
  sdkGlibcHeaders = "${pkgs.glibc.dev}/include";
  sdkCUuidHeaders = "${lib.getDev pkgs.libuuid}/include";
  sdkCHeaders = "${pkgs.glibc.dev}/include:${pkgs.linuxHeaders}/include";
  sdkClangArguments = builtins.toJSON [
    "-B${pkgs.glibc}/lib/"
    "-B${gccDirectory}/"
    "-L${gccDirectory}"
    "-L${pkgs.glibc}/lib"
    "-L${gcc.lib}/lib"
    "-Wl,-dynamic-linker,${pkgs.glibc}/lib/ld-linux-x86-64.so.2"
    # Swift's shared libraries need these runtime dependencies transitively.
    "-Wl,--disable-new-dtags"
    "-Wl,-rpath,${runtimeLibraries}"
    "-Wl,-rpath,${pkgs.glibc}/lib"
  ];
  installPhase = ''
        runHook preInstall
        mkdir -p "$out"
        cp -a usr/. "$out/"
        python3 ${./relocate-modulemap.py}
        python3 ${./wrap-original.py}
        test ! -e "$out/bin/git"
        cat > "$out/bin/git" <<'GIT_WRAPPER'
    #!${pkgs.bash}/bin/bash
    unset LD_LIBRARY_PATH
    exec ${pkgs.git}/bin/git "$@"
    GIT_WRAPPER
        chmod +x "$out/bin/git"
        runHook postInstall
  '';
  meta.platforms = [ "x86_64-linux" ];
}
