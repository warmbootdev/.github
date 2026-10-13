#!/usr/bin/env bash
# Conventional Commits on every subject line.
#   check-commits.sh [RANGE...]      commits in each RANGE (git log revision arguments)
#   check-commits.sh --file MSG      one message file (the commit-msg hook)
#   check-commits.sh --subject TEXT  one subject (a pull request title)
set -euo pipefail

TYPES='feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert'
PATTERN="^($TYPES)(\([a-z0-9._/-]+\))?!?: [^ ].*[^.]$"
MAX=100

check() {
  local subject="$1" where="$2"
  if [[ ! "$subject" =~ $PATTERN ]]; then
    echo "$where: not a conventional commit: $subject" >&2
    echo "  expected <type>(<scope>)?: <summary>, type one of ${TYPES//|/, }, no trailing period" >&2
    return 1
  fi
  if ((${#subject} > MAX)); then
    echo "$where: subject is ${#subject} characters (max $MAX): $subject" >&2
    return 1
  fi
}

case "${1:-}" in
  --file)
    subject="$(sed -e '/^#/d' -e '/^[[:space:]]*$/d' "$2" | head -n1)"
    # Let git's own merge and fixup messages through; history rewriting removes them.
    [[ "$subject" =~ ^(Merge |fixup! |squash! |amend! ) ]] && exit 0
    check "$subject" "commit message"
    exit
    ;;
  --subject)
    check "$2" "title"
    exit
    ;;
esac

status=0
for range in "$@"; do
  read -ra revs <<< "$range"
  while IFS=$'\t' read -r sha subject; do
    check "$subject" "${sha:0:9}" || status=1
  done < <(git log --no-merges --format='%H%x09%s' "${revs[@]}")
done
exit "$status"
