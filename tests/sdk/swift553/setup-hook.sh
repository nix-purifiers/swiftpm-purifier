# shellcheck shell=bash
originalSwift553BuildPhase() {
    runHook preBuild
    local flags=(-c "${swiftpmBuildConfig-release}" -j "${NIX_BUILD_CORES-1}")
    concatTo flags swiftpmFlags swiftpmFlagsArray
    swift build --disable-sandbox "${flags[@]}"
    runHook postBuild
}

originalSwift553CheckPhase() {
    runHook preCheck
    local flags=(-c "${swiftpmBuildConfig-release}" -j "${NIX_BUILD_CORES-1}")
    concatTo flags swiftpmFlags swiftpmFlagsArray
    swift test "${flags[@]}"
    runHook postCheck
}

originalSwift553InstallPhase() {
    runHook preInstall
    local products
    products=$(swift build -c "${swiftpmBuildConfig-release}" --show-bin-path)
    swift package dump-package > "$TMPDIR/original-swift553-products.json"
    while IFS= read -r -d '' name; do
        install -D -m 755 "$products/$name" "$out/bin/$name"
    done < <(@python@ -c 'import json,sys; p=json.load(open(sys.argv[1])); sys.stdout.write("".join(x["name"]+"\0" for x in p["products"] if "executable" in x["type"]))' "$TMPDIR/original-swift553-products.json")
    while IFS= read -r -d '' name; do
        if [ -e "$products/lib$name.so" ]; then
            install -D -t "$out/lib" "$products/lib$name.so"
            for module in "$products"/*.swiftmodule "$products"/Modules/*.swiftmodule; do
                [ ! -e "$module" ] || install -D -t "$out/lib/swift/linux" "$module"
            done
        fi
    done < <(@python@ -c 'import json,sys; p=json.load(open(sys.argv[1])); sys.stdout.write("".join(x["name"]+"\0" for x in p["products"] if "library" in x["type"]))' "$TMPDIR/original-swift553-products.json")
    runHook postInstall
}

if [ -z "${buildPhase-}" ]; then buildPhase=originalSwift553BuildPhase; fi
if [ -z "${checkPhase-}" ]; then checkPhase=originalSwift553CheckPhase; fi
if [ -z "${installPhase-}" ]; then installPhase=originalSwift553InstallPhase; fi
