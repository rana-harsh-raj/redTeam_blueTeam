#!/usr/bin/env bash
# scripts/restore.sh — restore datastores from a snapshot written by
# scripts/snapshot.sh. Datastores profile must already be up (this does NOT
# start containers, only loads data into already-running ones).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$COMPOSE_ROOT"

LABEL="${1:?usage: $0 <snapshot-label> (see ls snapshots/)}"
IN_DIR="$COMPOSE_ROOT/snapshots/$LABEL"

if [ ! -d "$IN_DIR" ]; then
  echo "ERROR: no snapshot at $IN_DIR" >&2
  exit 1
fi

compose() {
  # all profiles are always enabled here so ps/exec/logs can address every service (depends_on crosses profiles)
  docker compose --env-file .env.arena -f docker-compose.yml --profile datastores --profile migrations --profile substitutes --profile core --profile verify "$@"
}

MYSQL_PAYOUTS_PW="$(cat secrets/mysql_payouts_root_password.txt)"
MYSQL_FTS_PW="$(cat secrets/mysql_fts_root_password.txt)"
MYSQL_XBALANCES_PW="$(cat secrets/mysql_xbalances_root_password.txt)"
MYSQL_APIDB_PW="$(cat secrets/mysql_apidb_root_password.txt)"
LEDGER_PW="$(cat secrets/postgres_ledger_password.txt)"
MONGO_PW="$(cat secrets/mongo_cfa_root_password.txt)"

echo "==> restoring from $IN_DIR"

[ -f "$IN_DIR/mysql-payouts.sql" ] && compose exec -T mysql-payouts mysql -uroot -p"$MYSQL_PAYOUTS_PW" payouts < "$IN_DIR/mysql-payouts.sql"
[ -f "$IN_DIR/mysql-fts.sql" ] && compose exec -T mysql-fts mysql -uroot -p"$MYSQL_FTS_PW" fts < "$IN_DIR/mysql-fts.sql"
[ -f "$IN_DIR/mysql-xbalances.sql" ] && compose exec -T mysql-xbalances mysql -uroot -p"$MYSQL_XBALANCES_PW" rx_balances_local < "$IN_DIR/mysql-xbalances.sql"
[ -f "$IN_DIR/mysql-apidb.sql" ] && compose exec -T mysql-apidb-stub mysql -uroot -p"$MYSQL_APIDB_PW" api_local < "$IN_DIR/mysql-apidb.sql"
[ -f "$IN_DIR/postgres-ledger.sql" ] && compose exec -T -e PGPASSWORD="$LEDGER_PW" postgres-ledger psql -U ledger -d ledger < "$IN_DIR/postgres-ledger.sql"
if [ -f "$IN_DIR/mongo-cfa.archive" ]; then
  compose exec -T mongo-cfa mongorestore --username cfa_root --password "$MONGO_PW" \
    --authenticationDatabase admin --db cfa --archive --drop < "$IN_DIR/mongo-cfa.archive"
fi

echo "==> restore complete"
