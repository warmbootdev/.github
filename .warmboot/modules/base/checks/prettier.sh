#!/usr/bin/env bash
# Prettier, at the version the standards pin (.warmboot/prettier-packages), over
# everything .prettierignore leaves in. Through npx, never a devDependency: adding
# one regenerates lockfiles, and a lockfile resolved on arm64 drops the amd64
# native packages images need.
set -euo pipefail
mapfile -t pkgs < <(grep -v '^[[:space:]]*$' .warmboot/prettier-packages)
args=()
for p in "${pkgs[@]}"; do args+=(--package "$p"); done
npx --yes "${args[@]}" -- prettier --check --log-level warn .
