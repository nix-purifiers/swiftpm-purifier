# Local candidate for the original SDK test fixture. Not a support claim.
{ pkgs }:
let
  inherit (pkgs) lib;
  gcc = pkgs.stdenv.cc.cc;
  originalLibedit = import ./libedit2.nix { inherit pkgs; };
  gccDirectory = "${gcc}/lib/gcc/x86_64-unknown-linux-gnu/${gcc.version}";
  runtimeLibraries = lib.makeLibraryPath [
    gcc.lib
    pkgs.ncurses5
    pkgs.sqlite
    pkgs.libxml2_13
    pkgs.curl
    originalLibedit
    pkgs.libuuid
    pkgs.zlib
    pkgs.icu60
    pkgs.libbsd
  ];
in
assert pkgs.stdenv.hostPlatform.system == "x86_64-linux";
pkgs.stdenvNoCC.mkDerivation {
  pname = "original-swift-sdk";
  version = "4.2.4";
  src = pkgs.fetchurl {
    url = "https://download.swift.org/swift-4.2.4-release/ubuntu1804/swift-4.2.4-RELEASE/swift-4.2.4-RELEASE-ubuntu18.04.tar.gz";
    hash = "sha256-LEitmnHddBidQzjAWcwEfqYNTOzTiBJuFLMRKgzKGqA=";
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
    pkgs.which
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
    python3 ${../swift553/wrap-original.py}
    runHook postInstall
  '';
  meta.platforms = [ "x86_64-linux" ];
}
