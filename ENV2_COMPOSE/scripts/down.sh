#!/usr/bin/env bash
# scripts/down.sh — tear down every profile and destroy secrets/generated
# config. Volumes are removed too by default (-v) since "secrets are
# generated per arena and destroyed at teardown" implies datastore contents
# (which may embed those secrets, e.g. mysql's own user table) shouldn't
# outlive the arena either -- pass --keep-volumes to opt out (e.g. to
# snapshot first via scripts/snapshot.sh, which you should do BEFORE
# calling this, not after).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$COMPOSE_ROOT"

KEEP_VOLUMES=0
if [ "${1:-}" = "--keep-volumes" ]; then
  KEEP_VOLUMES=1
fi

compose() {
  docker compose --env-file .env.arena -f docker-compose.yml "$@"
}

echo "==> stopping every profile"
if [ "$KEEP_VOLUMES" = "1" ]; then
  compose --profile datastores --profile substitutes --profile core --profile verify --profile migrations down
else
  compose --profile datastores --profile substitutes --profile core --profile verify --profile migrations down -v
fi

echo "==> destroying secrets + generated config"
./secrets/destroy.sh

echo "==> removing SAFETY_PREFLIGHT.md / EGRESS_AUDIT.md (regenerated on next run)"
rm -f "$COMPOSE_ROOT/SAFETY_PREFLIGHT.md" "$COMPOSE_ROOT/EGRESS_AUDIT.md"

echo "==> down.sh complete"
