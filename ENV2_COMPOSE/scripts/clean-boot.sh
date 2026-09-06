#!/usr/bin/env bash
# Milestone 3.1 Workstream C -- disposable empty-volume clean-boot orchestrator.
#
# Boots a fully isolated arena instance from EMPTY volumes in a separate working
# copy (so host-side generated secrets/config/.runtime never touch the live tree),
# runs the original 26-test verifier in frozen-baseline mode (route policy OFF) with
# the same-window egress audit, and leaves the run evidence for inspection. Teardown
# is a separate step (scripts/instance-cleanup.py) so results can be examined first.
#
# Usage: scripts/clean-boot.sh <instance-id> [workdir]
# Env:   FROZEN_BASELINE=1 (default) sets KONG_ENFORCE_ROUTE_POLICY=0.
set -euo pipefail

INSTANCE="${1:?usage: clean-boot.sh <instance-id> [workdir]}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${2:-$SRC/../.local/m31-clean/${INSTANCE}/ENV2_COMPOSE}"
LOG_ROOT="${CLEAN_BOOT_LOG_ROOT:-$SRC/../RED_LOOP/runs/cleanboot-${INSTANCE}}"
mkdir -p "$LOG_ROOT"

echo "############################################################"
echo "# clean-boot ${INSTANCE}"
echo "#   src=${SRC}"
echo "#   work=${WORK}"
echo "#   evidence=${LOG_ROOT}"
echo "############################################################"

# 1. Preflight: refuse to launch if the plan overlaps any existing arena.
echo "== preflight (instance-plan, fail-closed on overlap) =="
python3 "$SRC/scripts/instance-plan.py" "$INSTANCE" --json > "$LOG_ROOT/instance-plan.json"
python3 "$SRC/scripts/instance-plan.py" "$INSTANCE"
if ! python3 -c "import json,sys; sys.exit(0 if json.load(open('$LOG_ROOT/instance-plan.json'))['safe_to_launch'] else 1)"; then
  echo "ABORT: plan overlaps an existing arena." >&2; exit 2
fi

# 2. Prepare a clean, isolated working copy (no generated secrets/config/runs).
echo "== preparing isolated working copy =="
rm -rf "$WORK"; mkdir -p "$WORK"
rsync -a --delete \
  --exclude '.git' --exclude 'generated/' --exclude 'snapshots/' \
  --exclude 'secrets/*.txt' --exclude 'secrets/jwks.json' --exclude 'secrets/.env.secrets' \
  --exclude 'secrets/merchant-keys/' --exclude '__pycache__/' --exclude '*.pyc' \
  --exclude '.runtime/' "$SRC/" "$WORK/"

# 3. Derive the instance env.
eval "$(python3 "$SRC/scripts/instance-plan.py" "$INSTANCE" --emit-env)"
export ARENA_SUFFIX COMPOSE_PROJECT_NAME ARENA_SUBNET INGRESS_SUBNET KONG_LITE_HOST_PORT

# 3a. Write the instance subnets/port into the COPY's .env.arena so the safety
# preflight (which reads its allowed arena subnets from .env.arena) matches the
# process-env subnets this instance actually uses. Only the copy is touched.
sed -i.bak -E \
  -e "s|^ARENA_SUBNET=.*|ARENA_SUBNET=${ARENA_SUBNET}|" \
  -e "s|^INGRESS_SUBNET=.*|INGRESS_SUBNET=${INGRESS_SUBNET}|" \
  -e "s|^KONG_LITE_HOST_PORT=.*|KONG_LITE_HOST_PORT=${KONG_LITE_HOST_PORT}|" \
  "$WORK/.env.arena"
echo "  patched copy .env.arena: ARENA_SUBNET=${ARENA_SUBNET} INGRESS_SUBNET=${INGRESS_SUBNET} PORT=${KONG_LITE_HOST_PORT}"

# 3b. The safety preflight also scans docker-compose.yml literally and flags the
# hard-coded default subnet fallbacks (${ARENA_SUBNET:-172.28.16.0/24}) as outside
# this instance's arena subnets. Rewrite those DEFAULT literals in the copy to the
# instance subnets so the file agrees with the instance. Only the copy is touched.
sed -i.bak2 -E \
  -e "s|(ARENA_SUBNET:-)172\.28\.16\.0/24|\1${ARENA_SUBNET}|" \
  -e "s|(INGRESS_SUBNET:-)172\.28\.17\.0/24|\1${INGRESS_SUBNET}|" \
  "$WORK/docker-compose.yml"
echo "  patched copy docker-compose.yml default subnet literals to instance subnets"
export KONG_ENFORCE_ROUTE_POLICY="${KONG_ENFORCE_ROUTE_POLICY:-0}"   # frozen baseline
export REGEN_SECRETS=1                                               # fresh synthetic secrets
export ARENA_HOST_BRIDGE="${ARENA_HOST_BRIDGE:-0}"                   # no host bridge needed for in-network verifier
echo "  project=$COMPOSE_PROJECT_NAME suffix=$ARENA_SUFFIX port=$KONG_LITE_HOST_PORT enforce=$KONG_ENFORCE_ROUTE_POLICY"
echo "  subnets=$ARENA_SUBNET / $INGRESS_SUBNET"

# 4. Boot from empty volumes.
echo "== boot (up.sh) =="
cd "$WORK"
( ARENA_SKIP_BUILD=0 bash scripts/up.sh ) 2>&1 | tee "$LOG_ROOT/up.log" | tail -40 || {
  echo "BOOT FAILED -- see $LOG_ROOT/up.log" >&2; exit 3; }

# 5. Verifier (26 tests, frozen baseline) + same-window egress audit.
echo "== verifier + egress audit =="
export ARENA_RUN_DIR="$LOG_ROOT/verifier-run"
( bash scripts/golden-run.sh --with-egress-audit ) 2>&1 | tee "$LOG_ROOT/verifier.log" | tail -30 || {
  echo "VERIFIER RUN returned nonzero -- inspect $LOG_ROOT/verifier.log" >&2; }

echo "############################################################"
echo "# clean-boot ${INSTANCE} complete. Evidence: $LOG_ROOT"
echo "#   junit: $ARENA_RUN_DIR/junit.xml"
echo "#   teardown: python3 $SRC/scripts/instance-cleanup.py $INSTANCE"
echo "############################################################"
