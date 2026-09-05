#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Python owns the finally sequence. A shell EXIT trap retries cleanup if Python
# is terminated; the provider TTL still covers host crash or SIGKILL of both.
state="${DAYTONA_STATE_FILE:-$script_dir/../runs/$(date -u +%Y%m%dT%H%M%SZ)-$$/state.json}"
for argument in "$@"; do
  case "$argument" in --state|--state=*)
    echo 'Set DAYTONA_STATE_FILE to customize state; --state is reserved by the cleanup controller.' >&2
    exit 2 ;;
  esac
done
finish() {
  result=$?
  trap - EXIT INT TERM
  if [ "${DAYTONA_APPROVED:-}" = 1 ] && [ -f "$state" ]; then
    if ! python3 "$script_dir/daytona_runner.py" cleanup --state "$state"; then
      printf 'Cleanup remains unverified. Retry with --state %s\n' "$state" >&2
      result=1
    fi
  fi
  exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
python3 "$script_dir/daytona_runner.py" run --state "$state" "$@"
