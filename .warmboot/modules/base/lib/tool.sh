#!/usr/bin/env bash
# Sourced by checks. tool NAME IMAGE ARGS...: the local NAME if installed, else
# the pinned IMAGE with the repository mounted read-only at /repo.
tool() {
  local name="$1" image="$2"
  shift 2
  if command -v "$name" > /dev/null; then
    "$name" "$@"
  elif command -v docker > /dev/null; then
    docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/repo:ro" -w /repo "$image" "$@"
  else
    echo "$name is not installed and docker is not available" >&2
    return 127
  fi
}

# tracked PATHSPEC...: files git tracks or would track, minus std's own.
tracked() {
  git ls-files --cached --others --exclude-standard -- "$@" ':!:.warmboot/**'
}
