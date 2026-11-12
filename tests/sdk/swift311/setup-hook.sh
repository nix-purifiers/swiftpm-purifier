# shellcheck shell=bash
originalSwift311BuildPhase() {
    runHook preBuild
    local flags=(-c "${swiftpmBuildConfig-release}")
    concatTo flags swiftpmFlags swiftpmFlagsArray
    swift build "${flags[@]}"
    runHook postBuild
}
originalSwift311CheckPhase() {
    runHook preCheck
    local flags=(-c "${swiftpmBuildConfig-release}")
    concatTo flags swiftpmFlags swiftpmFlagsArray
    swift test "${flags[@]}"
    runHook postCheck
}
originalSwift311InstallPhase() {
    runHook preInstall
    local products="$PWD/.build/${swiftpmBuildConfig-release}"
    mkdir -p "$out"
    while IFS= read -r -d '' name; do
        install -D -m 755 "$products/$name" "$out/bin/$name"
    done < <(@python@ -c 'import json,sys; p=json.load(open(sys.argv[1])); sys.stdout.write("".join(x["name"]+"\0" for x in p if x["type"] == "executable"))' "$swiftpmPurifierProducts")
    while IFS= read -r -d '' name && IFS= read -r -d '' suffix; do
        install -D -t "$out/lib" "$products/lib$name.$suffix"
    done < <(@python@ -c 'import json,sys; p=json.load(open(sys.argv[1])); sys.stdout.write("".join(x["name"]+"\0"+("so" if x["linkage"] == "dynamic" else "a")+"\0" for x in p if x["type"] == "library"))' "$swiftpmPurifierProducts")
    for module in "$products"/*.swiftmodule "$products"/*.swiftdoc "$products"/Modules/*.swiftmodule; do
        [ ! -e "$module" ] || install -D -t "$out/lib/swift/linux" "$module"
    done
    runHook postInstall
}
if [ -z "${buildPhase-}" ]; then buildPhase=originalSwift311BuildPhase; fi
if [ -z "${checkPhase-}" ]; then checkPhase=originalSwift311CheckPhase; fi
if [ -z "${installPhase-}" ]; then installPhase=originalSwift311InstallPhase; fi
