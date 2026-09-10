#!/usr/bin/env bash
# scripts/snapshot.sh — dump every datastore's contents to
# ./snapshots/<label>/ so scripts/restore.sh can bring an arena back to
# exactly this point (e.g. "right after seeding, before any scenario ran").
# Snapshots are plain SQL/BSON dumps -- no credentials are embedded in the
# dump files themselves (mysqldump/pg_dump/mongodump write data, not the
# connection password used to fetch it).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$COMPOSE_ROOT"

LABEL="${1:-$(date -u +%Y%m%dT%H%M%SZ)}"
OUT_DIR="$COMPOSE_ROOT/snapshots/$LABEL"
mkdir -p "$OUT_DIR"

compose() {
  # all profiles are always enabled here so ps/exec/logs can address every service (depends_on crosses profiles)
  docker compose --env-file .env.arena -f docker-compose.yml --profile datastores --profile migrations --profile substitutes --profile core --profile verify "$@"
}

echo "==> snapshotting to $OUT_DIR"

MYSQL_PAYOUTS_PW="$(cat secrets/mysql_payouts_root_password.txt)"
MYSQL_FTS_PW="$(cat secrets/mysql_fts_root_password.txt)"
MYSQL_XBALANCES_PW="$(cat secrets/mysql_xbalances_root_password.txt)"
MYSQL_APIDB_PW="$(cat secrets/mysql_apidb_root_password.txt)"
LEDGER_PW="$(cat secrets/postgres_ledger_password.txt)"
MONGO_PW="$(cat secrets/mongo_cfa_root_password.txt)"

compose exec -T mysql-payouts mysqldump -uroot -p"$MYSQL_PAYOUTS_PW" payouts > "$OUT_DIR/mysql-payouts.sql"
compose exec -T mysql-fts mysqldump -uroot -p"$MYSQL_FTS_PW" fts > "$OUT_DIR/mysql-fts.sql"
compose exec -T mysql-xbalances mysqldump -uroot -p"$MYSQL_XBALANCES_PW" rx_balances_local > "$OUT_DIR/mysql-xbalances.sql"
compose exec -T mysql-apidb-stub mysqldump -uroot -p"$MYSQL_APIDB_PW" api_local > "$OUT_DIR/mysql-apidb.sql"
compose exec -T -e PGPASSWORD="$LEDGER_PW" postgres-ledger pg_dump -U ledger -d ledger > "$OUT_DIR/postgres-ledger.sql"
compose exec -T mongo-cfa mongodump --username cfa_root --password "$MONGO_PW" \
  --authenticationDatabase admin --db cfa --archive > "$OUT_DIR/mongo-cfa.archive"

echo "==> snapshot complete: $OUT_DIR"
ls -lh "$OUT_DIR"
