#!/usr/bin/env bash
# scripts/reset.sh — reset in-place BETWEEN scenarios without a full
# teardown: flush redis, purge SQS/SNS queues, re-run the ledger
# accounts/ledger_config seed (BOM §1: "Ledger balances must be re-seeded"),
# re-run the reservation reconcile job, leave everything else (containers,
# migrations already applied) as-is. Much faster than down.sh + up.sh for
# iterating on a single golden flow.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$COMPOSE_ROOT"

compose() {
  # all profiles are always enabled here so ps/exec/logs can address every service (depends_on crosses profiles)
  docker compose --env-file .env.arena -f docker-compose.yml --profile datastores --profile migrations --profile substitutes --profile core --profile verify "$@"
}

echo "==> flushing redis"
compose exec -T redis redis-cli FLUSHALL

echo "==> purging localstack SQS queues (best-effort -- lists queues then purges each)"
compose exec -T localstack sh -c '
  awslocal sqs list-queues --output json 2>/dev/null | \
  python3 -c "import json,sys; data=json.load(sys.stdin); [print(u) for u in data.get(\"QueueUrls\", [])]"
' | while read -r queue_url; do
  [ -z "$queue_url" ] && continue
  echo "    purging $queue_url"
  compose exec -T localstack awslocal sqs purge-queue --queue-url "$queue_url" || true
done

echo "==> re-applying the idempotent seed set (seeds/s4/*): data only; schema stays. For a from-empty rebuild use scripts/down.sh && scripts/up.sh"
MYSQL_PAYOUTS_ROOT_PW="$(cat secrets/mysql_payouts_root_password.txt)"
MYSQL_FTS_ROOT_PW="$(cat secrets/mysql_fts_root_password.txt)"
MYSQL_XBALANCES_ROOT_PW="$(cat secrets/mysql_xbalances_root_password.txt)"
MONGO_CFA_ROOT_PW="$(cat secrets/mongo_cfa_root_password.txt)"
LEDGER_PW="$(cat secrets/postgres_ledger_password.txt)"
compose exec -T mysql-payouts mysql -uroot -p"$MYSQL_PAYOUTS_ROOT_PW" payouts < seeds/s4/payouts.sql
compose exec -T mysql-fts mysql -uroot -p"$MYSQL_FTS_ROOT_PW" fts < seeds/s4/fts.sql
compose exec -T mysql-xbalances mysql -uroot -p"$MYSQL_XBALANCES_ROOT_PW" rx_balances_local < seeds/s4/xbalances.sql
compose exec -T -e PGPASSWORD="$LEDGER_PW" postgres-ledger psql -v ON_ERROR_STOP=1 -q -U ledger -d ledger < seeds/s4/ledger.sql
compose exec -T mongo-cfa mongosh cfa --quiet --username cfa_root --password "$MONGO_CFA_ROOT_PW" --authenticationDatabase admin < seeds/s4/cfa.js
echo "==> triggering payouts-api's reservation-reconcile cron job (BOM §1: 'code constant' cadence, normally on a 5 min loop via cron-driver)"
compose exec -T cron-driver python3 -c "
import os, sys
sys.path.insert(0, '/app')
import driver
driver._hit('/v1/cron/reservation_reconcile')
" || echo "WARNING: could not trigger reservation_reconcile directly -- cron-driver's own loop will pick it up within its normal interval anyway"

echo "==> reset.sh complete"
