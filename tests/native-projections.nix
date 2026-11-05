# SPDX-License-Identifier: MIT
{ pkgs, build }:
let
  args = {
    src = ./fixtures/native-proof;
    nativeProducts = [ "Proof" ];
    nativeIncludeDirectories = [ "include" ];
    postInstall = ''
      install -Dm644 Sources/Proof/include/proof.h "$out/include/proof.h"
    '';
  };
  built = build args;
  projection = built.packages.Proof.projections;
  ordinary = import ./native-consumer.nix {
    inherit pkgs projection;
    library = "Proof";
    symbol = "proof_answer";
  };
  closure = pkgs.closureInfo { rootPaths = [ projection.native ]; };
  default = build { src = ./fixtures/native-proof; };
  invalid =
    products:
    !(builtins.tryEval (build (args // { nativeProducts = products; })).project.drvPath).success;
in
assert default.projections == { };
assert !(default.packages.Proof ? projections);
assert invalid [ "Missing" ];
assert invalid [ "BaseStatic" ];
assert !(built.packages.BaseStatic ? projections);
assert invalid [
  "Proof"
  "Proof"
];
assert invalid "Proof";
# An automatic Swift library is a source distribution, never inferred C ABI.
assert
  !(builtins.tryEval
    (build {
      src = ./fixtures/hello;
      nativeProducts = [ "Greeting" ];
    }).project.drvPath
  ).success;
pkgs.runCommand "swiftpm-native-ordinary-consumers"
  {
    nativeBuildInputs = [
      (pkgs.python3.withPackages (ps: [ ps.cffi ]))
      pkgs.stdenv.cc
    ];
  }
  ''
    test -e ${ordinary}
    grep -Fx '${built.project}' ${closure}/store-paths
    test -e ${built.project}/lib/libProof${pkgs.stdenv.hostPlatform.extensions.sharedLibrary}
    PYTHONPATH=${projection.python-ffi} python - <<'PY'
    import cffi, json, pathlib, subprocess, purifier_native
    catalog = json.load(open('${projection.native.catalog}'))
    ffi = cffi.FFI()
    include, = catalog['includeDirectories']
    ffi.cdef(subprocess.check_output(['cc', '-E', '-P', str(pathlib.Path(include) / 'proof.h')], text=True))
    assert ffi.dlopen(catalog['libraries']['Proof']).proof_answer() == 42
    PY
    touch $out
  ''
