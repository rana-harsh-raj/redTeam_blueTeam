#!/usr/bin/env bash
# Build is completed before the runtime audit starts. Additional args go to pytest.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
AUDIT=0
if [ "${1:-}" = --with-egress-audit ]; then AUDIT=1; shift; fi
compose=(docker compose --env-file .env.arena -f docker-compose.yml --profile datastores --profile migrations --profile substitutes --profile core --profile verify)
./scripts/healthcheck.sh kong-lite --timeout 60
if [ "${ARENA_SKIP_BUILD:-0}" != 1 ]; then "${compose[@]}" build verifier; fi
python3 preflight/preflight.py
RUN_DIR="${ARENA_RUN_DIR:-$ROOT/../reports/implementation/runs/$(date -u +%Y%m%dT%H%M%SZ)-$$}"
mkdir -p "$RUN_DIR"
RUN_DIR="$(cd "$RUN_DIR" && pwd)"
# Bind the run to the boot that produced it; the acceptance gate refuses a run
# with no fingerprint or with a fingerprint that disagrees with the tree.
cp .runtime/arena-fingerprint.json "$RUN_DIR/arena-fingerprint.json"
# Verifier writes sanitized HTTP/state traces and JUnit into this local directory.
CONTAINER="twin-verifier-$(date +%s)-$$"
command=("${compose[@]}" run --name "$CONTAINER" --no-deps -v "$RUN_DIR:/results" -e ARENA_TRACE_DIR=/results verifier --junitxml=/results/junit.xml "$@")
if [ "$AUDIT" = 1 ]; then
  exec python3 network/egress_audit.py --duration "${ARENA_TEST_TIMEOUT:-2400}" --output "$RUN_DIR" --command-container "$CONTAINER" -- "${command[@]}"
fi
trap 'docker rm -f "$CONTAINER" >/dev/null 2>&1 || true' EXIT INT TERM
"${command[@]}"
