# shellcheck shell=bash
originalSwift424BuildPhase() {
    runHook preBuild
    local flags=(-c "${swiftpmBuildConfig-release}")
    concatTo flags swiftpmFlags swiftpmFlagsArray
    swift build "${flags[@]}"
    runHook postBuild
}

originalSwift424CheckPhase() {
    runHook preCheck
    local flags=(-c "${swiftpmBuildConfig-release}")
    concatTo flags swiftpmFlags swiftpmFlagsArray
    swift test "${flags[@]}"
    runHook postCheck
}

originalSwift424InstallPhase() {
    runHook preInstall
    local products
    products=$(swift build -c "${swiftpmBuildConfig-release}" --show-bin-path)
    swift package dump-package > "$TMPDIR/original-swift424-products.json"
    while IFS= read -r -d '' name; do
        install -D -m 755 "$products/$name" "$out/bin/$name"
    done < <(@python@ -c 'import json,sys; p=json.load(open(sys.argv[1])); sys.stdout.write("".join(x["name"]+"\0" for x in p["products"] if x.get("product_type") == "executable"))' "$TMPDIR/original-swift424-products.json")
    while IFS= read -r -d '' name; do
        if [ -e "$products/lib$name.so" ]; then
            install -D -t "$out/lib" "$products/lib$name.so"
            for module in "$products"/*.swiftmodule "$products"/Modules/*.swiftmodule; do
                [ ! -e "$module" ] || install -D -t "$out/lib/swift/linux" "$module"
            done
        fi
    done < <(@python@ -c 'import json,sys; p=json.load(open(sys.argv[1])); sys.stdout.write("".join(x["name"]+"\0" for x in p["products"] if x.get("product_type") == "library"))' "$TMPDIR/original-swift424-products.json")
    runHook postInstall
}

if [ -z "${buildPhase-}" ]; then buildPhase=originalSwift424BuildPhase; fi
if [ -z "${checkPhase-}" ]; then checkPhase=originalSwift424CheckPhase; fi
if [ -z "${installPhase-}" ]; then installPhase=originalSwift424InstallPhase; fi
