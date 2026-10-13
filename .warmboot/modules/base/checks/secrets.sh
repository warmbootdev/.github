#!/usr/bin/env bash
# Secrets in the commits being pushed or reviewed (gitleaks). Without a range
# (nothing pushed yet), the working tree.
set -euo pipefail
source "$(dirname "$0")/../lib/tool.sh"
config=.gitleaks.toml
[[ -f "$config" ]] || config=.warmboot/modules/base/gitleaks.toml
IFS=';' read -ra ranges <<< "${STD_RANGE:-}"
if ((${#ranges[@]} == 0)); then
  # The working tree as git sees it: tracked and new files, not ignored ones
  # (build output, rendered manifests, local credentials that never leave).
  tree="$(mktemp -d)"
  trap 'rm -rf "$tree"' EXIT
  git ls-files -z --cached --others --exclude-standard | xargs -0 -r cp --parents -t "$tree" 2> /dev/null || true
  cp --parents "$config" "$tree" 2> /dev/null || true
  (cd "$tree" && tool gitleaks zricethezav/gitleaks:v8.30.1 dir --no-banner --redact --config "$config" .)
  exit
fi
status=0
for range in "${ranges[@]}"; do
  tool gitleaks zricethezav/gitleaks:v8.30.1 git --no-banner --redact --config "$config" --log-opts="$range" . || status=1
done
exit "$status"
