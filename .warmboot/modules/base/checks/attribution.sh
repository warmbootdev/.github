#!/usr/bin/env bash
# No AI attribution in the commits being pushed or reviewed, or in the pull
# request's title and description (squash merges make them the commit message).
set -euo pipefail
here="$(dirname "$0")/.."
IFS=';' read -ra ranges <<< "${STD_RANGE:-}"
status=0
if ((${#ranges[@]})); then
  bash "$here/bin/check-attribution.sh" "${ranges[@]}" || status=1
fi
for text in "${STD_SUBJECTS_EXTRA:-}" "${STD_PR_BODY:-}"; do
  [[ -n "$text" ]] && { bash "$here/bin/check-attribution.sh" --text "$text" || status=1; }
done
exit "$status"
