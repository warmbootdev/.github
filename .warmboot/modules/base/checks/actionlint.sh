#!/usr/bin/env bash
# GitHub Actions workflows: syntax, expressions, and shellcheck on run: steps.
set -euo pipefail
source "$(dirname "$0")/../lib/tool.sh"
mapfile -t workflows < <(tracked '.github/workflows/*.yml' '.github/workflows/*.yaml')
if ((${#workflows[@]} == 0)); then
  echo "no workflows"
  exit 0
fi
tool actionlint rhysd/actionlint:1.7.12 "${workflows[@]}"
