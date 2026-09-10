#!/usr/bin/env bash
# network/dns-check.sh — confirm DNS on rzp-arena resolves ONLY arena service
# names, by running a one-shot lookup container attached to rzp-arena and
# trying to resolve both an arena name (must succeed) and a real external
# name (must fail, since rzp-arena is `internal: true` and has no path to
# any real DNS resolver -- Docker's embedded DNS on an internal network only
# ever answers for containers on that same network).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$COMPOSE_ROOT"

ARENA_NAME_TO_CHECK="${1:-redis}"
EXTERNAL_NAME_TO_CHECK="${2:-example.com}"

echo "==> checking that '$ARENA_NAME_TO_CHECK' resolves on rzp-arena (should succeed)"
if docker run --rm --network rzp-arena nicolaka/netshoot:latest \
    getent hosts "$ARENA_NAME_TO_CHECK" >/dev/null 2>&1; then
  echo "OK: $ARENA_NAME_TO_CHECK resolved"
else
  echo "FAIL: $ARENA_NAME_TO_CHECK did NOT resolve on rzp-arena -- is the datastores profile up?" >&2
  exit 1
fi

echo "==> checking that '$EXTERNAL_NAME_TO_CHECK' does NOT resolve on rzp-arena (should fail)"
if docker run --rm --network rzp-arena nicolaka/netshoot:latest \
    getent hosts "$EXTERNAL_NAME_TO_CHECK" >/dev/null 2>&1; then
  echo "FAIL: $EXTERNAL_NAME_TO_CHECK resolved on rzp-arena -- this network has an egress/DNS path it should not have. Investigate immediately." >&2
  exit 1
else
  echo "OK: $EXTERNAL_NAME_TO_CHECK did not resolve (expected -- rzp-arena is internal: true)"
fi

echo "==> dns-check.sh: all checks passed"
