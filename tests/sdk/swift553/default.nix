# Local candidate for the original SDK test fixture. Not a support claim.
{ pkgs }:
let
  inherit (pkgs) lib;
  gcc = pkgs.stdenv.cc.cc;
  gccDirectory = "${gcc}/lib/gcc/x86_64-unknown-linux-gnu/${gcc.version}";
  runtimeLibraries = lib.makeLibraryPath [
    gcc.lib
    pkgs.ncurses
    pkgs.sqlite
    pkgs.libxml2_13
    pkgs.curl
    pkgs.libedit
    pkgs.libuuid
    pkgs.zlib
  ];
in
assert pkgs.stdenv.hostPlatform.system == "x86_64-linux";
pkgs.stdenvNoCC.mkDerivation {
  pname = "original-swift-sdk";
  version = "5.5.3";
  src = pkgs.fetchurl {
    url = "https://download.swift.org/swift-5.5.3-release/ubuntu2004/swift-5.5.3-RELEASE/swift-5.5.3-RELEASE-ubuntu20.04.tar.gz";
    hash = "sha256-aMadeXj/kDMlgMXeSJr/lt+EvAz2fZTs70H2hI9o25E=";
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
    gcc
    pkgs.git
    pkgs.unzip
  ];
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
    python3 ${./wrap-original.py}
    runHook postInstall
  '';
  meta.platforms = [ "x86_64-linux" ];
}
