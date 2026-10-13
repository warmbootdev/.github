#!/usr/bin/env bash
# Conventional commit subjects for the commits being pushed or reviewed, and
# the pull request title (squash merges use it as the subject).
set -euo pipefail
here="$(dirname "$0")/.."
IFS=';' read -ra ranges <<< "${STD_RANGE:-}"
status=0
if ((${#ranges[@]})); then
  bash "$here/bin/check-commits.sh" "${ranges[@]}" || status=1
else
  echo "no commits to check"
fi
if [[ -n "${STD_SUBJECTS_EXTRA:-}" ]]; then
  bash "$here/bin/check-commits.sh" --subject "$STD_SUBJECTS_EXTRA" || status=1
fi
exit "$status"
