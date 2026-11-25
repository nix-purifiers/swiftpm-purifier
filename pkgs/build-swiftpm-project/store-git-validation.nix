# SPDX-License-Identifier: MIT
# The validator reads only its declared bare source input, never the fetched checkout.
{
  runCommand,
  python3,
  git,
  nix,
}:
binding:
runCommand "swiftpm-purifier-store-git-validated"
  {
    declaration = builtins.toJSON binding.declaration;
    passAsFile = [ "declaration" ];
    nativeBuildInputs = [ python3 ];
    PYTHONPATH = ../cli;
  }
  ''
    python3 ${../cli/store_git_validator.py} \
      --declaration "$declarationPath" --git ${git}/bin/git --nix ${nix}/bin/nix > "$out"
  ''
