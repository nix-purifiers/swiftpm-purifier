# SPDX-License-Identifier: MIT
#
# The swiftpm-purifier command, a Python script with the tools it runs
# (swift, nix-prefetch-git and nix) named by environment variables. Its
# unit tests run in the build.
{
  lib,
  stdenvNoCC,
  makeWrapper,
  writeText,
  python3,
  swiftPackages,
  runtimeMode ? "local",
  nix,
  nix-prefetch-git,
  git,
  unzip,
}:
let
  # The environment nixpkgs' setup hooks give a build with the Swift
  # toolchain: the compiler and linker flags its wrappers read, and PATH.
  # SwiftPM compiles and links manifests with that toolchain, so the
  # command runs it in this environment, as `nix develop` would.
  selectedSDK = import ../../nix/lib/selected-sdk.nix {
    inherit
      lib
      writeText
      swiftPackages
      git
      unzip
      ;
  };
  swiftEnvironment = selectedSDK.environment;
  editorArguments =
    lib.optionals (swiftPackages ? sourcekit-lsp && swiftPackages.sourcekit-lsp != null)
      [
        "--set"
        "SWIFTPM_PURIFIER_EDITOR_RECIPE"
        (builtins.unsafeDiscardOutputDependency swiftPackages.sourcekit-lsp.drvPath)
        "--set"
        "SWIFTPM_PURIFIER_EDITOR_OUTPUT"
        (swiftPackages.sourcekit-lsp.outputName or "out")
        "--set"
        "SWIFTPM_PURIFIER_EDITOR_PATH"
        (builtins.unsafeDiscardStringContext (toString swiftPackages.sourcekit-lsp))
      ];

in
assert builtins.elem runtimeMode [
  "local"
  "sdk"
];
stdenvNoCC.mkDerivation {
  pname = "swiftpm-purifier";
  version = "0.1.0";

  src = lib.fileset.toSource {
    root = ../..;
    fileset = lib.fileset.unions [
      ./swiftpm_purifier.py
      ./bounded_native.py
      ./native_sdk.py
      ./host_runtime.py
      ./test_swiftpm_purifier.py
      ./fixtures/native553
    ];
  };

  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [ python3 ];

  doCheck = true;
  checkPhase = ''
    runHook preCheck
    (cd pkgs/cli && SWIFTPM_PURIFIER_RUNTIME_MODE=sdk python3 -m unittest -v test_swiftpm_purifier)
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    mkdir -p $out/libexec/swiftpm-purifier
    cp pkgs/cli/swiftpm_purifier.py $out/libexec/swiftpm-purifier/
    cp pkgs/cli/bounded_native.py $out/libexec/swiftpm-purifier/
    cp pkgs/cli/native_sdk.py $out/libexec/swiftpm-purifier/
    cp pkgs/cli/host_runtime.py $out/libexec/swiftpm-purifier/
    # SWIFTPM_PURIFIER_FLAKE is this flake's source, whose package set the
    # interactive mode and `publish` build with, so they use the builder
    # and the purifier library a Nix build uses.
  ''
  + (
    if runtimeMode == "sdk" then
      ''
        makeWrapper ${lib.getExe python3} $out/bin/swiftpm-purifier \
          --set SWIFTPM_PURIFIER_RUNTIME_MODE sdk \
          --add-flags $out/libexec/swiftpm-purifier/swiftpm_purifier.py \
          --set SWIFTPM_PURIFIER_FLAKE ${../..} \
          --set SWIFTPM_PURIFIER_SWIFT ${swiftPackages.swift}/bin/swift \
          --set SWIFTPM_PURIFIER_SWIFT_PACKAGE ${swiftPackages.swiftpm}/bin/swift-package \
          --set SWIFTPM_PURIFIER_SWIFT_BUILD ${swiftPackages.swiftpm}/bin/swift-build \
          --set SWIFTPM_PURIFIER_SWIFT_TEST ${swiftPackages.swiftpm}/bin/swift-test \
          --set SWIFTPM_PURIFIER_SWIFT_RUN ${swiftPackages.swiftpm}/bin/swift-run \
          --set SWIFTPM_PURIFIER_NATIVE_ENVIRONMENT ${swiftEnvironment} \
          --set SWIFTPM_PURIFIER_NATIVE_SDK ${selectedSDK.file} \
          --set SWIFTPM_PURIFIER_CLI_ROOT "$out" \
          ${lib.escapeShellArgs editorArguments} \
          --set SWIFTPM_PURIFIER_NIX_PREFETCH_GIT ${lib.getExe nix-prefetch-git} \
          --set SWIFTPM_PURIFIER_NIX ${lib.getExe nix} \
          --run 'source ${swiftEnvironment}'
      ''
    else
      ''
        makeWrapper ${lib.getExe python3} $out/libexec/local-launcher \
          --add-flags $out/libexec/swiftpm-purifier/swiftpm_purifier.py \
          --set SWIFTPM_PURIFIER_RUNTIME_MODE local \
          --set SWIFTPM_PURIFIER_FLAKE ${../..} \
          --set SWIFTPM_PURIFIER_CLI_ROOT "$out" \
          --set SWIFTPM_PURIFIER_NIX_PREFETCH_GIT ${lib.getExe nix-prefetch-git} \
          --set SWIFTPM_PURIFIER_NIX ${lib.getExe nix} \
          --suffix PATH : ${
            lib.makeBinPath [
              git
              unzip
            ]
          }
        mkdir -p "$out/bin"
        printf '%s\n' '#!${stdenvNoCC.shell}' \
          'export SWIFTPM_PURIFIER_HOST_PATH="''${PATH-}"' \
          "exec \"$out/libexec/local-launcher\" \"\$@\"" > "$out/bin/swiftpm-purifier"
        chmod +x "$out/bin/swiftpm-purifier"
      ''
  )
  + ''
    runHook postInstall
  '';

  meta = {
    description = "Nix as the dependency backend of SwiftPM packages";
    mainProgram = "swiftpm-purifier";
    license = lib.licenses.mit;
  };
  passthru = {
    nativeSDKDescriptor = if runtimeMode == "sdk" then selectedSDK.descriptor else null;
    nativeSDKDescriptorFile = if runtimeMode == "sdk" then selectedSDK.file else null;
    nativeEnvironment = if runtimeMode == "sdk" then swiftEnvironment else null;
  };
}
