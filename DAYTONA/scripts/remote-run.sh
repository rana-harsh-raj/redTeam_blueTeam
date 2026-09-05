#!/usr/bin/env bash
# Executed only inside the approved, outbound-blocked Daytona sandbox.
set -euo pipefail
case "${1:-}" in smoke|full) profile="$1" ;; *) exit 2 ;; esac
root=/tmp/payouts-twin
cd "$root/ENV2_COMPOSE"
out="$root/results/$profile"
mkdir -p "$out"
exec >"$out/run.log" 2>&1
finish() {
  result=$?
  trap - EXIT INT TERM
  set +e
  for evidence in SAFETY_PREFLIGHT.md EGRESS_AUDIT.md DNS_CHECK.md; do
    [ ! -f "$evidence" ] || cp "$evidence" "$out/"
  done
  # Only designated synthetic verifier artifacts; no databases, volumes, source, or secrets.
  if [ -d verifier/results ]; then cp -R verifier/results "$out/verifier-results"; fi
  test_result=$result
  bash scripts/down.sh
  teardown=$?
  if [ "$result" -eq 0 ] && [ "$teardown" -ne 0 ]; then result=$teardown; fi
  printf '{"profile":"%s","test_exit_code":%s,"teardown_exit_code":%s,"exit_code":%s}\n' \
    "$profile" "$test_result" "$teardown" "$result" > "$out/result.json"
  exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
export ARENA_SKIP_BUILD=1
export ARENA_HOST_BRIDGE=0
export ARENA_RUN_DIR="$out/artifacts"
export COMPOSE_PROJECT_NAME=payouts-twin
export COMPOSE_PULL_POLICY=never
docker info >/dev/null
docker compose version
# Every image, helper, migration mount, and executable must already have passed
# the local offline rehearsal. No registry or package download is allowed here.
bash scripts/down.sh
bash scripts/up.sh
python3 preflight/preflight.py
bash network/dns-check.sh
if [ "$profile" = smoke ]; then
  bash scripts/golden-run.sh --with-egress-audit -k 'v01 or v06'
else
  bash scripts/golden-run.sh --with-egress-audit
fi
python3 - "$ARENA_RUN_DIR/junit.xml" <<'PY'
import sys
import xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
cases = list(root.iter('testcase'))
if not cases:
    raise SystemExit('No smoke/full test cases were recorded')
if any(case.find(tag) is not None for case in cases for tag in ('skipped', 'failure', 'error')):
    raise SystemExit('Remote acceptance rejects failed, errored, or skipped cases')
PY
