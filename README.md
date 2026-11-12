# swiftpm-purifier

swiftpm-purifier keeps SwiftPM's development workflow on locked,
Nix-resolved dependencies. Use the normal build and test commands in your
working tree; building the project itself in the Nix sandbox is an
optional next step.

## Quickstart

From the project root:

```zsh
nix shell 'github:nix-purifiers/swiftpm-purifier#cli'
swiftpm-purifier lock
swiftpm-purifier swift build
swiftpm-purifier swift test
```

Commit the lockfiles that the lock command writes, including the build
tool's own lockfile when it has one. Keep using the project's usual build,
test and editor commands with this setup. Nix provides the locked
dependencies; the development commands above run outside the Nix sandbox.
No project Nix files are needed for this workflow.

Sandboxed builds of the whole project are an optional
[next step](#building-the-project-with-nix).

The CLI preserves the Swift runtime already selected by your PATH and
`SWIFT_EXEC`. Use `swiftpm-purifier swift` for locked development and to
preserve `Package.resolved` while SwiftPM works with edited dependencies.
With no purifier lockfile, it delegates to that same installed runtime.

For persistent use, add these packages through your usual Nix configuration.


## Development loop

`swiftpm-purifier swift build`, `swiftpm-purifier swift test` and
`swiftpm-purifier swift run` delegate to the installed runtime on the
locked dependencies. Prefetch links them into `Packages/` and
`.build/artifacts/` from the Nix store. The pre-workspace Git protocol
instead installs verified, owned copies with their real Git metadata.
The dependencies-state version-1 protocol installs verified, owned copies
without Git metadata and projects the native edited-dependency state.

If you intentionally want a packaged runtime,
`nix shell 'github:nix-purifiers/swiftpm-purifier#toolchain'` provides an
explicit SDK and a `swift` wrapper for these commands. That is an optional
alternative to your installed runtime.

`swiftpm-purifier` is a standalone command that runs SwiftPM, not a SwiftPM
package plugin. `swiftpm-purifier lock` records the downloads and keeps the
pins of `Package.resolved` as they are. It relocks when `Package.swift` or the pins
changed since the lockfile was made, and `relock` relocks regardless. A
relock with unchanged native metadata reproduces the lockfile byte for byte.

`swiftpm-purifier update [<package>...]` updates the pins through the
`swift package update` command of SwiftPM, for every package or the named
ones, and then relocks the lockfile against the result.

`swiftpm-purifier publish <scope.name> <version>` builds the `release` with
Nix and uploads it (SE-0391) to the registry configured for the scope, or
to `--registry <url>`. `SWIFTPM_PURIFIER_REGISTRY_TOKEN` is sent as a
bearer token.

For an external build, every command takes `--lockfile <path>` and
`--tool-lockfile <path>`, or reads `SWIFTPM_PURIFIER_LOCKFILE` and
`SWIFTPM_PURIFIER_TOOL_LOCKFILE`. Either one takes precedence over the
default location. The dev shell of a flake that calls the builder can set
both variables to the paths it passes as `lockFile` and `packageResolved`.

## Building the project with Nix

Once the native development workflow is working, you can also build and
test the whole project in the Nix sandbox. For this step, keep a flake or
another Nix build definition in the project's repository, or maintain an
external packaging repository that points at its sources and lockfiles.
The builder consumes the same lockfiles as the development workflow.

The flake adds one attribute to the package set: the scope
`pkgs.swiftpm-purifier`, which holds the builder `buildSwiftPMProject`, the
`swiftpm-purifier` command as `cli`, and the Swift `toolchain`.

A project whose authors want a flake can package the project with one. The
flake can live in the repository of the project or in another one, and it
calls the builder directly:

```nix
{
  inputs.swiftpm-purifier.url =
    "github:nix-purifiers/swiftpm-purifier";
  inputs.nixpkgs.follows = "swiftpm-purifier/nixpkgs";

  outputs =
    {
      self,
      nixpkgs,
      swiftpm-purifier,
      ...
    }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        overlays = [
          swiftpm-purifier.overlays.default
        ];
      };
      project =
        pkgs.swiftpm-purifier.buildSwiftPMProject {
        src = self;
      };
    in
    {
      packages.${system}.default =
        project.packages.hello;
      checks.${system}.tests = project.tests;
    };
}
```

A package in an overlay that takes the scope as `{ swiftpm-purifier }:` calls
`swiftpm-purifier.buildSwiftPMProject` the same way.

`buildSwiftPMProject` takes these arguments:

- `src`, the root of the package.
- `lockFile`, which defaults to `swiftpm-purifier.lock.json` in `src` and
  may lie outside it.
- `packageResolved`, which defaults to `Package.resolved` in `src`. A copy
  kept outside the repository replaces the one in `src` in the build.
- `fetchers`, which goes unchanged to `fetchEntry` of the purifier library,
  so a company mirror or credentials go into `fetchers.fetchurl`.
- `buildConfig`, `release` (the default) or `debug`.
- `artifactBuildInputs`, libraries that the prebuilt files of binary
  targets need beyond the C and C++ runtime, curl, libxml2, zlib, OpenSSL,
  SQLite and ICU.
- `nativeProducts`, an optional list of locked dynamic-library product names
  whose public C ABI the caller explicitly selects. Defaults to `[]`.
- `nativeIncludeDirectories`, optional relative directories in the installed
  project that hold public C headers. Install those headers with `postInstall`
  when the native SwiftPM install hook does not install them.
- Any argument of `stdenv.mkDerivation`.

It returns:

- `packages.<product>`, one per product of the package. An executable is
  its binary under `bin/`. A dynamic or static library is its library file
  under `lib/` with its Swift modules. SwiftPM publishes an automatic
  library, a plugin and a macro as source, so their package is the source
  archive of the package.
- `tests`, the build with `swift test` run in the sandbox, on Linux only
  (nixpkgs' Swift has no XCTest runner on macOS).
- `release`, the source archive of the package, which a package registry
  serves and `swiftpm-purifier publish` uploads.
- `fetches`, the purifier library entries that the build fetches.
- `project`, the build with every product installed, and `tree`, the locked
  dependencies laid out for SwiftPM.

Selected `nativeProducts` expose `.projections.native`, `.python-ffi`, and
`.java-ffi` on both the build result and `packages.<product>` (the latter
contains only that product). The native catalog gives exact library paths,
headers, target system and producer identity. Use `purifier_native.load(name)`
with that view on `PYTHONPATH`, or pass its catalog library path to Java FFM;
the caller supplies compatible function signatures and lifetime rules.
The projection retains the installed producer and its runtime closure.

For example, `nativeProducts = [ "Proof" ];` selects the installed dynamic
product `Proof` from the checked lock. Missing, duplicate, static and automatic
products are rejected. Ordinary Swift libraries keep their Swift modules and
source distribution behavior; a Swift ABI is not inferred to be a C ABI.
The check builds a C target and its dependency through SwiftPM, calls the
exported symbol from ctypes, cffi and Java 21 FFM, and checks `dladdr` on the
tested function pointer against the selected library. This proves that
fixture and selected SDK, rather than arbitrary Swift APIs or cross targets.

The builder is a package for `callPackage` in that scope. It takes
`swiftPackages`, `runCommand`, `zip`, `unzip`, `zstd`, `python3`,
`writeText`, `fetchurl`, `fetchgit` and the purifier library by name.
`swiftpm-purifier.buildSwiftPMProject.override` replaces any of them, and
`swiftpm-purifier.overrideScope` replaces what everything in the scope
shares.

The standalone CLI uses the Swift runtime already selected by your PATH and
`SWIFT_EXEC`. It keeps that runtime during capture, dependency prefetch and
`swiftpm-purifier swift` commands. Management tools are added after the
original PATH. No packaged Swift SDK is required for this local workflow.
Local locks use portable format 4, format 6 for original workspace-state
version 1 with `Package.pins`, format 7 for the pre-workspace Git
protocol, or format 8 for `.build/dependencies-state.json` version 1.
Runtime observations are diagnostic and do not bind the lock
to a host binary or SDK version.
Native manifest and dependency requirements are checked by the selected
SwiftPM and compiler.

An explicit packaged toolchain remains available. Select a project's
compiler and SwiftPM together through `swiftPackages`, using
`toolchain.override { inherit swiftPackages; }` and
`buildSwiftPMProject.override { inherit swiftPackages; }`. To capture with
that immutable SDK explicitly, use
`cli.override { inherit swiftPackages; runtimeMode = "sdk"; }`.
The set supplies concrete `swift`, `swiftpm` and `stdenv` derivations;
`sourcekit-lsp` is optional.

Explicit SDK capture writes format 5, recording the compiler, package
manager, environment, derivation recipes and physical runtime closure.
The SDK mode and sandboxed builder reject a different SDK before native
build commands run. Those identities are specific to the host system;
separate external locks can use `--lockfile`. Host runtime observations
never serve as an immutable SDK attestation.

Native `Package.resolved` formats 1, 2 and 3 are understood. For format 1,
the native workspace supplies package identities; the purifier preserves
package names and source locations SwiftPM actually recorded. Native
option probes select available cache and prebuilt controls. Older native
dependency-state layouts still need their own replay adapters; a native
protocol error does not select a replacement compiler.

Original workspace-state version 1 uses a separate portable format 6. Its
resolved Git checkouts come from the native workspace; `Package.pins` may
contain only some of those packages. The lock binds the exact native pin
file bytes, including manual pin reasons and `autoPin`. Unchanged metadata
keeps its original formatting. A missing native pin file is accepted only
for an empty dependency graph, and remains absent during local commands.
The default native filename is `Package.pins`; `packageResolved` and
`--tool-lockfile` can select an external file. Ambiguous native filenames,
local path dependencies in this protocol, and SDK-mode format 6 capture or
consumption are rejected. Existing format 4 and 5 readers reject format 6.

The original Swift 3.1.1 compiler and bundled SwiftPM 3.1.0 are a fixture
point for this protocol. Pure builds can select that immutable SDK
explicitly. The tested fixture uses generated `@testable` imports that
require a debug build; release program builds are checked separately.
These fixture versions do not restrict the
runtime selected for local commands.

The pre-workspace protocol has no native dependency-state file. Portable
format 7 captures the actual native dependency graph, Git revisions,
source trees and full Git metadata. Capture preserves an existing native
`Packages` cache in its owned temporary copy; `update` explicitly asks
the native package manager to change that selection. A bounded native
build supplies the authoritative product and module plan before the
purifier writes the lock. Compiler and Git failures remain native failures.

Format 7 requires `Package.pins` and `Package.resolved` to remain absent;
native lockfile overrides and mixed workspace metadata are rejected. It
binds manifest bytes and the structural source layout, including empty
directories, so adding or renaming an implicit module requires relocking.
Use the default root purifier lock or a fully external `--lockfile`.
External/shared Git object stores and aliased build outputs are rejected.

Prefetch installs full, owned Git checkouts transactionally. It refuses
to replace an unowned or edited `Packages` cache; move an existing native
cache aside first. Repeated prefetch preserves unchanged owned bytes,
and uninstall removes only verified owned dependencies. Actual Git
constructs each checkout's index from its verified commit. Normal index
stat-cache refreshes are accepted after checking index entries against
the commit; staged content or mode changes are refused. Ownership travels
with the dependency cache, so native build-artifact cleaning preserves it.
Local commands
keep the selected developer runtime. Format 7 has no SDK-bound capture
mode; pure builds can explicitly select an immutable SDK separately.
Older format 4, 5 and 6 readers reject format 7.

The original Swift 3.0.2 compiler and package manager are a Linux fixture
point for this protocol, including debug XCTest, release programs,
multi-module static/dynamic libraries and executable aliases. This is
an individual fixture point, not a claim about every historical release
or Darwin runtime.

Portable format 8 represents a separate native protocol: dependencies-state
version 1 with version-1 `Package.resolved` bytes or their explicit absence.
It is not workspace-state version 1 (format 6) or dependencies-state version 2.
Capture reads actual native state and manifest protocols, Git revisions,
source hashes and compiler build plans. A local fixture detects the selected
package manager's native state geometry without reading the project's pins
or choosing an adapter from its release number. Fresh projects and verified
native or purifier-owned caches are resolved in an owned source copy.

This protocol currently supports nonempty remote Git graphs with semantic
versions and Swift targets. Native declarations bind explicit product targets
separately from the transitive modules they export. Pure builds preserve
automatic libraries as their real object files and modules; they do not
invent a static archive. Branch-only or revision-only pins, local or binary
dependencies, unrecognized legacy declaration forms, and mixed native state
are rejected rather than interpreted as another lock format.

Use the default root lock and pins, or independently select fully external
files with `--lockfile` and `--tool-lockfile`. The pure reader also distinguishes
an omitted `packageResolved` argument from explicit `null`. Pin bytes and
absence are bound independently of any unselected logical-root file. Source
layout and manifest changes require relocking. Symlink, hardlink and in-tree
selector aliases are refused by the host mutators.

Prefetch validates the selected runtime's actual manifest protocols, direct
dependency references, product/module plan, edited-state behavior and output
geometry in a separate owned arena before installation. It installs verified
VCS-free sources transactionally, preserving the original native cache.
Relock and update can operate while those dependencies remain installed.
Uninstall removes only verified owned sources and restores the original
native-state bytes or absence. Dirty or unowned dependencies are preserved
and refused. The local `swift` wrapper holds the root lock while projecting
selected pin bytes or absence and recoverably restoring the logical-root
file; detected foreign edits are preserved for inspection. Recovery precedes
subsequent format-8 mutations.

The original Swift 4.0.3 compiler with SwiftPM 4.0.0-dev is a measured Linux
fixture point, including a transitive remote graph, native build/clean/rebuild,
pure XCTest and installed product consumers. Default and read-only external
selectors are exercised through the installed local CLI. Explicit-null pins
are exercised through the pure reader and host lifecycle helper. These are
individual measured domains, not a version interval or Darwin claim.
Format 8 has no SDK-bound CLI capture or consumption mode; pure builds may
select an explicit immutable SDK. Formats 4–7 retain their own readers.

`lib.swiftpm-purifier.readLock { src, lockFile ? ..., packageResolved ? ... }`
gives the lock in memory of the purifier contract:
`{ purifier, entries, outputs, problems }`. `problems` is empty for a usable
lock.

We recommend committing the purifier lock and any native lockfile the
project's protocol uses to the repository of the project.
The builder and the commands look for them there by default.

A flake can instead keep a build external: the flake that calls the builder
keeps the lockfiles outside the repository and passes them as `lockFile` and
`packageResolved`. This builds a project whose author commits neither
lockfile, or whose committed `Package.resolved` is stale or comes from
another version of SwiftPM. The commands take `--lockfile <path>` and
`--tool-lockfile <path>` for the two files, or the variables
`SWIFTPM_PURIFIER_LOCKFILE` and `SWIFTPM_PURIFIER_TOOL_LOCKFILE`. With
`--tool-lockfile`, `lock` and `update` write the `Package.resolved` there and
leave the copy in the tree as it is, and `lock` starts from the copy in the
tree when the outside one does not exist yet.

### One-off builds without a project Nix file

For an ad hoc build, the builder can also be evaluated directly from the
project root. Replace the example package name with one from your project:

```zsh
nix build --impure --expr 'let
  purifier = builtins.getFlake "github:nix-purifiers/swiftpm-purifier";
  pkgs = import purifier.inputs.nixpkgs {
    system = builtins.currentSystem;
    overlays = [ purifier.overlays.default ];
  };
in
(pkgs.swiftpm-purifier.buildSwiftPMProject { src = ./.; })
.packages.hello'
```

This chooses the packaged SDK from the purifier's pinned nixpkgs for a
sandboxed build.
To build with ambient `<nixpkgs>` from your Nix search path instead,
replace `import purifier.inputs.nixpkgs` with `import <nixpkgs>`.
Both forms need no Nix files in your project. The selected SDK must meet
the project's native language and dependency requirements. A format 5 lock
additionally requires the exact immutable SDK used for explicit SDK capture;
pass that `swiftPackages` set to the builder. Local prefetch and development
reject format 5 rather than ignoring its SDK binding. To adopt that project
with an installed runtime, deliberately run local `relock` to write portable
format 4, or keep using explicit SDK mode.

## Working offline

`swiftpm-purifier prefetch` realizes the locked dependencies in the Nix
store, links them into the package, and writes the native workspace state.
A garbage-collector root under `.build/swiftpm-purifier` keeps those
artifacts alive. Local mode keeps your installed runtime and language
server. Explicit SDK mode also retains its selected packaged tools.
After prefetch, `swiftpm-purifier swift build`, `test` and `run` replay the
locked dependencies with the origin offline. `swiftpm-purifier uninstall`
removes the links. The default `.gitignore` that SwiftPM writes already
ignores `/Packages`.

## Editor

Local mode uses your existing language server. `prefetch` also writes the
native workspace state into `.build/index-build` for its background index.
The optional packaged toolchain includes sourcekit-lsp when that SDK
provides one. The pinned nixpkgs sourcekit-lsp 6.3.3 has an unresolved
SourceKit plugin lookup problem and cannot create its build server;
that packaged-editor limitation does not select or replace a local editor.

## Troubleshooting

A lockfile that is stale, changed by hand, malformed or incomplete fails
in the command and at Nix evaluation, with a message that names the command
that fixes it.

| Message | Cause | Fix |
|---|---|---|
| `no Swift runtime on the original host PATH` | No installed Swift driver was available before adding management tools. | Select your existing Swift installation on PATH. |
| `is out of date with Package.resolved` or `with Package.swift` | The pins or the manifests changed since the lock run. | `swiftpm-purifier lock` |
| `does not match its contentHash` | The lockfile was edited after the lock run wrote it. | `swiftpm-purifier relock` |
| `lacks <field>` or `is not valid JSON` | The lockfile is incomplete or damaged. | `swiftpm-purifier relock` |
| `has lock format version N` | The lockfile is from another version of swiftpm-purifier. | `swiftpm-purifier relock` with the version that the flake pins |
| `hash mismatch` | The content of a download differs from the recorded hash. | Check the source, then `swiftpm-purifier relock` |

`swiftpm-purifier swift` stops with a stale lockfile, where SwiftPM would
resolve and download what the lock does not hold.

## Supported language repositories

| What SwiftPM fetched | What the lockfile records | Status |
|---|---|---|
| A package from git (`.package(url:)`) | the content hash by `nix-prefetch-git`, with submodules; the fetch URL when a mirror replaces the location | supported |
| A package from a registry (`.package(id:)`) | the URL of the source archive that the registry serves, and its hash | supported |
| A remote binary target (`.binaryTarget(url:checksum:)`) | the URL, with the checksum of the manifest | supported |
| A package by path (`.package(path:)`) | where it is | part of the source |
| A local binary target (`.binaryTarget(path:)`) | where it is | part of the source |
| A git repository on the local disk | nothing | refused, because it exists on one machine only; depend on it by path or by its remote URL |
| Prebuilt swift-syntax libraries | nothing | refused; the build and `swift` pass `--disable-experimental-prebuilts` and build swift-syntax from source |

The lockfile entries follow the entry shapes of
[purifier](https://github.com/nix-purifiers/purifier), the library that the
nix-purifiers share, and that library fetches them. The lock run refuses a
dependency whose SwiftPM workspace state it cannot derive from
`Package.resolved`. Binary targets ship prebuilt files for other systems,
so on Linux the build patches their ELF executables and libraries with
`autoPatchelfHook` to run under Nix.

x86_64-linux and aarch64-darwin are the supported systems, so the flake's
checks run there and not on aarch64-linux; CI runs on x86_64-linux. nixpkgs
marks its Swift 6.3 compiler broken on aarch64-linux, where it fails to
link with the gold linker, and aarch64-linux joins the flake's systems once
nixpkgs updates to Swift 6.4, which links with LLD. On macOS the builds use
nixpkgs' Swift and Apple SDK rather than Xcode, and `tests` doesn't run
there: nixpkgs' Swift has no XCTest runner on macOS.

## Examples

For day-to-day work on these projects, use the native-tool setup and
commands in [Quickstart](#quickstart) and [Development loop](#development-loop).
The Nix expressions below show optional sandboxed packaging of those
projects.

### vapor/toolbox

[vapor/toolbox](https://github.com/vapor/toolbox) is the command-line tool
of the Vapor web framework. It commits `Package.resolved` and no purifier
lockfile.

Lock the tool at a release, and build it from the clone:

```zsh
nix shell 'github:nix-purifiers/swiftpm-purifier#toolchain'
git clone --branch 20.0.2 https://github.com/vapor/toolbox
cd toolbox
swiftpm-purifier lock
nix build --impure --expr 'let
  purifier = builtins.getFlake "github:nix-purifiers/swiftpm-purifier";
  pkgs = import purifier.inputs.nixpkgs {
    system = builtins.currentSystem;
    overlays = [ purifier.overlays.default ];
  };
in
(pkgs.swiftpm-purifier.buildSwiftPMProject { src = ./.; })
.packages.vapor'
./result/bin/vapor --help
```

To keep the purifier lockfile outside the clone, write it into a flake of
your own with `--lockfile`, and let that flake build the tool from its
source:

```zsh
nix shell 'github:nix-purifiers/swiftpm-purifier#toolchain'
swiftpm-purifier --lockfile \
  ../vapor-toolbox/swiftpm-purifier.lock.json lock
```

```nix
{
  inputs.swiftpm-purifier.url =
    "github:nix-purifiers/swiftpm-purifier";
  inputs.nixpkgs.follows = "swiftpm-purifier/nixpkgs";
  inputs.toolbox = {
    url = "github:vapor/toolbox/20.0.2";
    flake = false;
  };

  outputs =
    {
      nixpkgs,
      swiftpm-purifier,
      toolbox,
      ...
    }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        overlays = [
          swiftpm-purifier.overlays.default
        ];
      };
      project =
        pkgs.swiftpm-purifier.buildSwiftPMProject {
        src = toolbox;
        lockFile = ./swiftpm-purifier.lock.json;
      };
    in
    {
      packages.${system}.default =
        project.packages.vapor;
    };
}
```

To move to a newer release, change the tag in the flake input, and run
`swiftpm-purifier update` in a checkout of that release with the same
`--lockfile`.

## Credits

The purifier idea was inspired by nixcloud's
[cargo (libnix)](https://github.com/nixcloud/cargo).

## License

MIT
