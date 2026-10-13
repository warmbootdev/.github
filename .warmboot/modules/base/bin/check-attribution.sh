#!/usr/bin/env bash
# Refuse AI attribution in commit messages and pull request text: co-author
# trailers naming an AI tool, session links, "Generated with" footers.
#   check-attribution.sh [RANGE...]     every commit in each RANGE (git log revision arguments)
#   check-attribution.sh --file MSG     one message file (the commit-msg hook)
#   check-attribution.sh --text TEXT    a pull request title or description
set -euo pipefail

PATTERN='^[[:space:]]*(co-authored-by:.*(claude|anthropic|copilot|openai|chatgpt|gemini|cursor|sisyphus|opencode|devin|codex|aider)|claude-session:|(🤖[[:space:]]*)?generated (with|by) \[?(claude|copilot|chatgpt|cursor|codex)|ultraworked with)'

found() { # text where
  local hits
  hits="$(grep -inE "$PATTERN" <<< "$1" || true)"
  [[ -z "$hits" ]] && return 1
  echo "$2: AI attribution is not allowed; remove:" >&2
  while IFS= read -r line; do echo "  $line" >&2; done <<< "$hits"
}

case "${1:-}" in
  --file)
    ! found "$(sed '/^#/d' "$2")" "commit message"
    exit
    ;;
  --text)
    ! found "$2" "pull request"
    exit
    ;;
esac

status=0
for range in "$@"; do
  read -ra revs <<< "$range"
  while read -r sha; do
    found "$(git log -1 --format=%B "$sha")" "${sha:0:9}" && status=1
  done < <(git rev-list "${revs[@]}")
done
exit "$status"
