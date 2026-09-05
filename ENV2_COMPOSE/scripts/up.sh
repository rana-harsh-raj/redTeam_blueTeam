#!/usr/bin/env bash
# scripts/up.sh — bring up the full Env 2 arena, in the order the task
# brief specifies: gen secrets -> generate config -> preflight -> datastores
# -> migrations -> seeds -> substitutes -> core -> health wait.
#
# Assumes images are already built (build/build.sh or build/build-host.sh)
# and tagged rzp-arena/<svc>:${ARENA_TAG:-local}, and the substitutes/
# images will be built by `docker compose build` inline (they have a
# `build:` block; the 5 core services + mozart-mock do not, by design --
# see docker-compose.yml's header comment).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$COMPOSE_ROOT"

compose() {
  docker compose --env-file .env.arena -f docker-compose.yml "$@"
}

echo "############################################################"
echo "# 1/8  secrets/gen-secrets.sh"
echo "############################################################"
if [ -f secrets/postgres_ledger_password.txt ] && [ "${REGEN_SECRETS:-0}" != "1" ]; then
  echo "secrets already present (re-run); keeping them so existing datastore volumes stay reachable. Set REGEN_SECRETS=1 (after scripts/down.sh) to regenerate."
else
  ./secrets/gen-secrets.sh
fi

echo "############################################################"
echo "# 2/8  config/generate.py"
echo "############################################################"
python3 config/generate.py

echo "############################################################"
echo "# 3/8  preflight/preflight.py  (HARD GATE -- non-zero exit stops here)"
echo "############################################################"
python3 preflight/preflight.py

echo "############################################################"
echo "# 4/8  datastores profile"
echo "############################################################"
compose --profile datastores up -d
./scripts/healthcheck.sh mysql-payouts mysql-fts mysql-xbalances mysql-apidb-stub \
  postgres-ledger mongo-cfa redis localstack kafka --timeout 180

echo "############################################################"
echo "# 5/8  migrations (one-shot)"
echo "############################################################"
compose --profile datastores --profile migrations run --rm payouts-migrate
compose --profile datastores --profile migrations run --rm ledger-migrate
compose --profile datastores --profile migrations run --rm fts-migrate
compose --profile datastores --profile migrations run --rm cfa-migrate
compose --profile datastores --profile migrations run --rm xbalances-migrate

echo "  -- arena schema patches (columns the code writes but repo migrations never add; see seeds/schema-patches/*.sql headers)"
MYSQL_PAYOUTS_ROOT_PW_SP="$(cat secrets/mysql_payouts_root_password.txt)"
compose exec -T mysql-payouts mysql -uroot -p"$MYSQL_PAYOUTS_ROOT_PW_SP" payouts < seeds/schema-patches/payouts.sql
MYSQL_APIDB_ROOT_PW_SP="$(cat secrets/mysql_apidb_root_password.txt)"
compose exec -T mysql-apidb-stub mysql -uroot -p"$MYSQL_APIDB_ROOT_PW_SP" api_local < seeds/schema-patches/apidb.sql

echo "############################################################"
echo "# 6/8  seeds"
echo "############################################################"
# shellcheck disable=SC1091
source ./secrets/.env.secrets 2>/dev/null || true

MYSQL_PAYOUTS_ROOT_PW="$(cat secrets/mysql_payouts_root_password.txt)"
MYSQL_FTS_ROOT_PW="$(cat secrets/mysql_fts_root_password.txt)"
MYSQL_XBALANCES_ROOT_PW="$(cat secrets/mysql_xbalances_root_password.txt)"
MONGO_CFA_ROOT_PW="$(cat secrets/mongo_cfa_root_password.txt)"

echo "  -- payouts banking_accounts/fund_accounts/counters (seeds/s4/payouts.sql)"
compose exec -T mysql-payouts mysql -uroot -p"$MYSQL_PAYOUTS_ROOT_PW" payouts < seeds/s4/payouts.sql

echo "  -- fts source accounts / routing (seeds/s4/fts.sql)"
compose exec -T mysql-fts mysql -uroot -p"$MYSQL_FTS_ROOT_PW" fts < seeds/s4/fts.sql

echo "  -- x-balances balance rows (seeds/s4/xbalances.sql)"
compose exec -T mysql-xbalances mysql -uroot -p"$MYSQL_XBALANCES_ROOT_PW" rx_balances_local < seeds/s4/xbalances.sql

echo "  -- ledger accounts + ledger_config (seeds/s4/ledger.sql; tenant X schema from rx_migrations)"
LEDGER_PW="$(cat secrets/postgres_ledger_password.txt)"
compose exec -T -e PGPASSWORD="$LEDGER_PW" postgres-ledger psql -v ON_ERROR_STOP=1 -q -U ledger -d ledger < seeds/s4/ledger.sql

echo "  -- cfa contacts/fund_accounts/hash_lookup (seeds/s4/cfa.js, Mongo)"
compose exec -T mongo-cfa mongosh cfa --quiet \
  --username cfa_root --password "$MONGO_CFA_ROOT_PW" --authenticationDatabase admin \
  < seeds/s4/cfa.js

echo "  -- apidb: DDL applied automatically via docker-entrypoint-initdb.d on first boot;"
echo "     apidb_seed.json / dcs_flags.json are read directly by monolith-stub/dcs-stub at their own startup (bind-mounted), no extra step needed here."

echo "############################################################"
echo "# 7/8  substitutes profile"
echo "############################################################"
compose --profile datastores --profile substitutes build
compose --profile datastores --profile substitutes up -d
./scripts/healthcheck.sh kong-lite monolith-stub dcs-stub splitz-stub shield-stub \
  pricing-stub asv-stub stork-capture merchant-webhook-sink xas-sink --timeout 120

MOZART_IMPL="${ARENA_MOZART_IMPL:-mozart-mock}"
if [ "$MOZART_IMPL" = "mozart-mock" ]; then
  if compose ps -q mozart-mock >/dev/null 2>&1 && [ -n "$(compose ps -q mozart-mock)" ]; then
    ./scripts/healthcheck.sh mozart-mock --timeout 60 || {
      echo "WARNING: mozart-mock did not become healthy -- falling back to mozart-sim. " \
           "This is exactly the scenario build/mozart.Dockerfile's ASSUMPTION note warned about; " \
           "check 'docker compose logs mozart-mock' before relying on Mozart-dependent flows." >&2
      compose --profile datastores --profile substitutes up -d mozart-sim
      ./scripts/healthcheck.sh mozart-sim --timeout 60
    }
  else
    echo "mozart-mock was not built (MOZART_BUILD_CONTEXT unset?) -- using mozart-sim"
    compose --profile datastores --profile substitutes up -d mozart-sim
    ./scripts/healthcheck.sh mozart-sim --timeout 60
  fi
else
  compose --profile datastores --profile substitutes up -d mozart-sim
  ./scripts/healthcheck.sh mozart-sim --timeout 60
fi
compose --profile datastores --profile substitutes up -d cron-driver

echo "  -- Stork webhook subscriptions: stork-capture self-seeds from seeds/stork/subscriptions.json at boot (STORK_SEED_FILE); verifying"
compose --profile datastores --profile substitutes logs stork-capture 2>/dev/null | grep -E "loaded [0-9]+ webhook subscription" | tail -1 \
  || echo "WARNING: stork-capture did not report loaded webhook subscriptions; check 'docker compose logs stork-capture'"

echo "############################################################"
echo "# 8/8  core profile + final health wait"
echo "############################################################"
compose --profile datastores --profile substitutes --profile core up -d
# every core-profile service except ledger-scheduler (one-shot job: runs, exits 0)
CORE_SERVICES="$(compose --profile datastores --profile substitutes --profile core config --services \
  | grep -E '^(payouts|ledger|fts|cfa|xbalances)-' | grep -v '^ledger-scheduler$' | tr '
' ' ')"
# shellcheck disable=SC2086
./scripts/healthcheck.sh $CORE_SERVICES --timeout 240

echo "  -- (post-core) ledger accounting configs: ledger's own seed sets via LedgerConfigAPI/CreateInBulk (shared_account_x, direct_account_x)"
LEDGER_AUTH_B64="$(printf 'payouts_key:%s' "$(cat secrets/auth_payouts_ledger.txt)" | base64)"
for ident in shared_account_x direct_account_x; do
  docker run --rm --network rzp-arena curlimages/curl:latest -s -o /dev/null -w "     $ident -> HTTP %{http_code}\n" \
    -X POST -H "Authorization: Basic $LEDGER_AUTH_B64" -H "Ledger-Tenant: X" -H "Content-Type: application/json" \
    -d "{\"ledger_config_data_identifier\":\"$ident\"}" \
    http://ledger-api:8080/twirp/rzp.ledger.ledger_config.v1.LedgerConfigAPI/CreateInBulk || echo "WARNING: ledger config bulk create failed for $ident"
done


echo "############################################################"
echo "# LocalStack queues: $(docker compose --env-file .env.arena logs localstack 2>/dev/null | grep -cE '(queue|topic) ') created by seeds/localstack/init-queues.sh"
echo "# Env 2 arena is up. Entrypoint: http://localhost:${KONG_LITE_HOST_PORT:-18080}"
echo "# Run scripts/golden-run.sh to exercise the verifier."
echo "############################################################"
