# SPDX-License-Identifier: MIT
#
# The `swiftpm-purifier` library namespace. It holds `readLock { src,
# lockFile ? <in src>, packageResolved ? <in src> }`, the lock in memory of
# the purifier contract:
# `{ purifier, entries, outputs, problems }` (lib-overlays/default/lock.nix). The
# builder needs a package set, so it is the package `buildSwiftPMProject`
# that the default package overlay adds.
{ ... }:
{

  overlay = final: prev: {
    swiftpm-purifier = (prev.swiftpm-purifier or { }) // {
      readLock =
        args@{
          src,
          lockFile ? src + "/swiftpm-purifier.lock.json",
          packageResolved ? src + "/Package.resolved",
        }:
        let
          parsed = builtins.addErrorContext "swiftpm-purifier: ${toString lockFile} is not valid JSON. Run `swiftpm-purifier relock` to write it again." (
            builtins.fromJSON (builtins.readFile lockFile)
          );
          preWorkspace =
            builtins.isAttrs parsed && builtins.isInt (parsed.version or null) && parsed.version == 7;
        in
        (import ./lock.nix {
          inherit src lockFile;
          # Preserve existing defaults for formats 4/5/6. Format 7 has no
          # native pin file; an explicitly supplied selector still rejects.
          packageResolved = if preWorkspace && !(args ? packageResolved) then null else packageResolved;
        }).lock;
    };
  };

}
