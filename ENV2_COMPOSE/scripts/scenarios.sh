#!/usr/bin/env bash
# Run supplemental route or bank scenarios with the same runtime isolation.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
audit=0
if [ "${1:-}" = --with-egress-audit ]; then audit=1; shift; fi
family="${1:-route}"; if [ "$#" -gt 0 ]; then shift; fi
case "$family" in
  route) script=/verifier/route_scenarios.py ;;
  bank) script=/verifier/bank_scenarios.py ;;
  # kafka: real-FTS-producer Kafka status transport (I70/I71). Fails closed unless the
  # generated fixture index reports route_profile=kafka, i.e. an ARENA_ROUTE_PROFILE=kafka boot.
  kafka) script=/verifier/kafka_scenarios.py ;;
  *) echo 'Usage: scenarios.sh [--with-egress-audit] {route|bank|kafka} [runner options]' >&2; exit 2 ;;
esac
compose=(docker compose --env-file .env.arena -f docker-compose.yml --profile datastores --profile migrations --profile substitutes --profile core --profile verify)
python3 preflight/preflight.py
run_dir="${ARENA_RUN_DIR:-$ROOT/../reports/implementation/runs/$family-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
mkdir -p "$run_dir"; run_dir="$(cd "$run_dir" && pwd)"
# Bind the run to the boot that produced it (see scripts/fingerprint.py).
cp .runtime/arena-fingerprint.json "$run_dir/arena-fingerprint.json"
container="twin-$family-$(date +%s)-$$"
command=("${compose[@]}" run --name "$container" --no-deps --entrypoint python3 -v "$run_dir:/results" -e ARENA_TRACE_DIR=/results -e PYTHONDONTWRITEBYTECODE=1 verifier "$script" "$@")
if [ "$audit" = 1 ]; then
  exec python3 network/egress_audit.py --duration "${ARENA_TEST_TIMEOUT:-1200}" --output "$run_dir" --command-container "$container" -- "${command[@]}"
fi
trap 'docker rm -f "$container" >/dev/null 2>&1 || true' EXIT INT TERM
"${command[@]}"
