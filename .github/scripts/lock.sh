#!/usr/bin/env bash
# Regenerate the three hash-locked requirement files with the exact command in each file's header.
#
#   .github/scripts/lock.sh             re-lock, keeping the current pins (CI job "locks" checks for no diff)
#   .github/scripts/lock.sh --upgrade   move every pin to the newest allowed version (workflow lock-refresh)
#
# Order matters: packaging/requirements-build.txt is constrained by requirements.txt.
set -euo pipefail
cd "$(dirname "$0")/../.."

for file in requirements.txt requirements-dev.txt packaging/requirements-build.txt; do
  command=$(sed -n '2s/^#[[:space:]]*//p' "$file")
  case "$command" in
    "uv pip compile "*) ;;
    *) echo "::error file=$file::line 2 is not a 'uv pip compile' command: $command"; exit 1 ;;
  esac
  echo "$command $*"
  # Word splitting is intended: the header holds plain arguments without quotes or spaces in paths
  # shellcheck disable=SC2086
  $command --quiet "$@"
done
