#!/bin/sh
set -eu
. "$(dirname "$0")/_common.sh"
guard_project_ownership
compose up -d --build --wait
for topic in add-tds-entry prod.x.vendor-payments.accounting-payouts.status-update; do
  if ! compose exec -T kafka rpk topic describe "$topic" >/dev/null 2>&1; then
    compose exec -T kafka rpk topic create "$topic"
  fi
done
"$S2P_DIR/scripts/health.sh"
