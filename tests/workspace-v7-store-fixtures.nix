# SPDX-License-Identifier: MIT
# Daemon-free build check: preserve even empty directories and executable bits.
{ pkgs }:
pkgs.runCommand "workspace-v7-store-fixture-nars"
  {
    nativeBuildInputs = [
      pkgs.gnutar
      pkgs.gzip
    ];
    archive = ./fixtures/workspace7-store-git/immutable-inputs.tar.gz;
    dontFixup = true;
  }
  ''
    mkdir unpacked "$TMPDIR/no-nix-config"
    tar --no-same-owner -xzf "$archive" -C unpacked
    NIX_USER_CONF_FILES= NIX_CONF_DIR="$TMPDIR/no-nix-config" ${pkgs.nix}/bin/nix \
      --extra-experimental-features nix-command --offline --store dummy:// hash path --type sha256 unpacked/alpha > alpha.hash
    NIX_USER_CONF_FILES= NIX_CONF_DIR="$TMPDIR/no-nix-config" ${pkgs.nix}/bin/nix \
      --extra-experimental-features nix-command --offline --store dummy:// hash path --type sha256 unpacked/beta > beta.hash
    test "$(cat alpha.hash)" = "sha256-HldBR3cqy5qFCgQUS9U8WvviWcFw6BXdw13QasiaqL4="
    test "$(cat beta.hash)" = "sha256-rjISdpUjdGmUSfjreLZeUfXNfO6us24D6ZSKLwOfEI0="
    mkdir "$out"
    cp alpha.hash beta.hash "$out/"
  ''
