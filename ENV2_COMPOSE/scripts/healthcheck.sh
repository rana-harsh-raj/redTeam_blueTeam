#!/usr/bin/env bash
# scripts/healthcheck.sh — wait for one or more compose services to report
# `healthy` (per their own `healthcheck:` block in docker-compose.yml).
#
# Exists because several depends_on edges were deliberately removed from
# docker-compose.yml to keep partial-profile `config -q` validation working
# (see docker-compose.yml's comments on the cron-driver and verifier
# services) -- this script is how scripts/up.sh and scripts/golden-run.sh
# recover that ordering without compose's own depends_on.
#
# Usage: ./scripts/healthcheck.sh <service> [service...] [--timeout SECONDS]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$COMPOSE_ROOT"

TIMEOUT=120
SERVICES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --timeout) TIMEOUT="$2"; shift 2 ;;
    *) SERVICES+=("$1"); shift ;;
  esac
done

if [ ${#SERVICES[@]} -eq 0 ]; then
  echo "usage: $0 <service> [service...] [--timeout SECONDS]" >&2
  exit 1
fi

compose() {
  # all profiles are always enabled here so ps/exec/logs can address every service (depends_on crosses profiles)
  docker compose --env-file .env.arena -f docker-compose.yml --profile datastores --profile migrations --profile substitutes --profile core --profile verify "$@"
}

for svc in "${SERVICES[@]}"; do
  echo "==> waiting up to ${TIMEOUT}s for $svc to become healthy"
  elapsed=0
  while true; do
    cid="$(compose ps -q "$svc" 2>/dev/null || true)"
    if [ -z "$cid" ]; then
      status="not_created"
    else
      status="$(docker inspect --format='{{.State.Health.Status}}' "$cid" 2>/dev/null || echo "no_healthcheck")"
    fi
    if [ "$status" = "healthy" ]; then
      echo "    $svc: healthy (after ${elapsed}s)"
      break
    fi
    if [ "$status" = "no_healthcheck" ] && [ -n "$cid" ]; then
      running="$(docker inspect --format='{{.State.Running}}' "$cid" 2>/dev/null || echo false)"
      if [ "$running" = "true" ]; then
        echo "    $svc: no healthcheck defined but container is running -- treating as ready (after ${elapsed}s)"
        break
      fi
    fi
    if [ "$elapsed" -ge "$TIMEOUT" ]; then
      echo "ERROR: $svc did not become healthy within ${TIMEOUT}s (last status: $status)" >&2
      compose logs --tail=50 "$svc" >&2 || true
      exit 1
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
done

echo "==> all requested services healthy"
