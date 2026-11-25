# Local candidate for the original SDK test fixture. Not a support claim.
{
  pkgs,
  packagerCPU ? null,
}:
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
    (import ../swift424/libedit2.nix { inherit pkgs; })
  ];
in
assert pkgs.stdenv.hostPlatform.system == "x86_64-linux";
assert packagerCPU == null || (builtins.isInt packagerCPU && packagerCPU >= 0);
pkgs.stdenvNoCC.mkDerivation {
  pname = "original-swift-sdk";
  version = "6.4.2";
  src = pkgs.fetchurl {
    name = "swift-6.4.2-RELEASE-ubuntu22.04.tar.gz";
    url = "https://download.swift.org/swift-6.4.2-release/ubuntu2204/swift-6.4.2-RELEASE/swift-6.4.2-RELEASE-ubuntu22.04.tar.gz";
    hash = "sha256-wwgbGBcRfGbkdk0qFINX2ZcgAEiSLHeLDwjHlYM64G0=";
  };
  preUnpack =
    lib.optionalString (packagerCPU != null) ''
      ${pkgs.util-linux}/bin/taskset --pid --cpu-list ${toString packagerCPU} "$$"
      python3 -c 'import os; actual=sorted(os.sched_getaffinity(0)); print("OWNED_SWIFT642_PACKAGER_AFFINITY="+str(actual),flush=True); assert actual == [${toString packagerCPU}]'
    ''
    + ''
      swift642ArchiveDigest=$(sha256sum "$src")
      test "''${swift642ArchiveDigest%% *}" = "c3081b1817117c66e4764d2a148357d997200048922c778b0f08c795833ae06d"
    '';
  nativeBuildInputs = [
    pkgs.python3
    pkgs.patchelf
    pkgs.util-linux
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
    mkdir -p "$out/usr"
    cp -a usr/. "$out/usr/"
    python3 ${./wrap-original.py}
    for sdkDirectory in bin include lib libexec local share; do
      test -d "$out/usr/$sdkDirectory"
      ln -s "usr/$sdkDirectory" "$out/$sdkDirectory"
    done
    python3 -c 'import json,os; from pathlib import Path; Path(os.environ["out"],"original-packager-resource.json").write_text(json.dumps({"cpuAffinity":sorted(os.sched_getaffinity(0))})+"\n")'
    runHook postInstall
  '';
  meta.platforms = [ "x86_64-linux" ];
}
