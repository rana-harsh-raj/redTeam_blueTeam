#!/usr/bin/env bash
# scripts/golden-run.sh — run the verifier against a fully-up arena.
# Placeholder orchestration: wait for kong-lite (the cross-profile ordering
# depends_on couldn't express, see docker-compose.yml's comment), optionally
# wrap the run in network/egress-audit.sh, then invoke the verifier.
#
# verify/ itself (the actual golden-flow assertions) is owned by a separate
# workstream (VERIFIER_SPEC.md-driven, see findings/29_env2_scaffold.md) --
# this script only wires up WHEN/HOW it runs, not what it asserts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$COMPOSE_ROOT"

WITH_EGRESS_AUDIT=0
if [ "${1:-}" = "--with-egress-audit" ]; then
  WITH_EGRESS_AUDIT=1
fi

compose() {
  # all profiles are always enabled here so ps/exec/logs can address every service (depends_on crosses profiles)
  docker compose --env-file .env.arena -f docker-compose.yml --profile datastores --profile migrations --profile substitutes --profile core --profile verify "$@"
}

echo "==> waiting for kong-lite (arena entrypoint)"
./scripts/healthcheck.sh kong-lite --timeout 60

if [ "$WITH_EGRESS_AUDIT" = "1" ]; then
  echo "==> starting egress audit in the background for the duration of this run"
  ./network/egress-audit.sh 120 &
  AUDIT_PID=$!
fi

echo "==> building + running verifier"
compose --profile verify build verifier
set +e
compose --profile verify run --rm verifier
RESULT=$?
set -e

if [ "$WITH_EGRESS_AUDIT" = "1" ]; then
  wait "$AUDIT_PID" || true
  echo "==> see EGRESS_AUDIT.md for the result"
fi

if [ "$RESULT" -eq 0 ]; then
  echo "==> golden-run.sh: verifier PASSED"
else
  echo "==> golden-run.sh: verifier FAILED (exit $RESULT)" >&2
fi
exit "$RESULT"
