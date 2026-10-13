#!/usr/bin/env bash
# Prettier at the versions the standards pin (.warmboot/prettier-packages), over
# the files git tracks, minus .prettierignore. Installed into a cache, never
# a devDependency: adding one regenerates lockfiles, and a lockfile resolved on
# arm64 drops the amd64 native packages images need.
set -euo pipefail
mapfile -t pkgs < <(grep -v '^[[:space:]]*$' .warmboot/prettier-packages)
key=$(printf '%s\n' "${pkgs[@]}" | sha256sum | cut -c1-16)
cache="${XDG_CACHE_HOME:-$HOME/.cache}/warmboot-std/prettier-$key"
if [[ ! -x "$cache/node_modules/.bin/prettier" ]]; then
  mkdir -p "$cache"
  npm install --silent --no-audit --no-fund --prefix "$cache" "${pkgs[@]}" > /dev/null
fi
# Plugins named in .prettierrc.json resolve from the working directory, where they
# are not installed: hand prettier the config without them, and each plugin by path.
mapfile -t plugin_args < <(CACHE="$cache" node -e '
  const fs = require("fs"), path = require("path");
  const cfg = JSON.parse(fs.readFileSync(".prettierrc.json", "utf8"));
  for (const p of cfg.plugins || []) console.log("--plugin=" + require.resolve(p, { paths: [process.env.CACHE] }));
  delete cfg.plugins;
  fs.writeFileSync(path.join(process.env.CACHE, "config.json"), JSON.stringify(cfg));
')
# The files git would track -- every .gitignore, nested ones included, which
# prettier's own --ignore-path cannot see -- then .prettierignore on top.
mapfile -d '' files < <(git ls-files -z --cached --others --exclude-standard | grep -zv '^\.warmboot/' || true)
if ((${#files[@]} == 0)); then
  echo "no files"
  exit 0
fi
printf '%s\0' "${files[@]}" | xargs -0 "$cache/node_modules/.bin/prettier" --config "$cache/config.json" \
  "${plugin_args[@]}" --ignore-path .prettierignore --ignore-unknown --check --log-level warn
