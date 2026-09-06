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

generator_args=()
if [ -n "${ARENA_SOURCE_REPOS_ROOT:-}" ]; then generator_args=(--repos-root "$ARENA_SOURCE_REPOS_ROOT"); fi
python3 seeds/generator/generate.py --epoch "${ARENA_SEED_EPOCH:-$(date +%s)}" ${generator_args[@]+"${generator_args[@]}"}

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

# Preserve host 0600 files while giving UID 10001 only its own generated inputs.
python3 secrets/materialize.py

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
# All credentials below are read explicitly. Sourcing an optional file can
# terminate macOS bash even with `|| true`, and is unnecessary here.

MYSQL_PAYOUTS_ROOT_PW="$(cat secrets/mysql_payouts_root_password.txt)"
MYSQL_FTS_ROOT_PW="$(cat secrets/mysql_fts_root_password.txt)"
MYSQL_XBALANCES_ROOT_PW="$(cat secrets/mysql_xbalances_root_password.txt)"
MONGO_CFA_ROOT_PW="$(cat secrets/mongo_cfa_root_password.txt)"

echo "  -- payouts banking_accounts/fund_accounts/counters (seeds/generated/s4/payouts.sql)"
compose exec -T mysql-payouts mysql -uroot -p"$MYSQL_PAYOUTS_ROOT_PW" payouts < seeds/generated/s4/payouts.sql

echo "  -- fts source accounts / routing (seeds/generated/s4/fts.sql)"
compose exec -T mysql-fts mysql -uroot -p"$MYSQL_FTS_ROOT_PW" fts < seeds/generated/s4/fts.sql

echo "  -- x-balances balance rows (seeds/generated/s4/xbalances.sql)"
compose exec -T mysql-xbalances mysql -uroot -p"$MYSQL_XBALANCES_ROOT_PW" rx_balances_local < seeds/generated/s4/xbalances.sql

echo "  -- ledger accounts + ledger_config (seeds/generated/s4/ledger.sql; tenant X schema from rx_migrations)"
LEDGER_PW="$(cat secrets/postgres_ledger_password.txt)"
compose exec -T -e PGPASSWORD="$LEDGER_PW" postgres-ledger psql -v ON_ERROR_STOP=1 -q -U ledger -d ledger < seeds/generated/s4/ledger.sql

echo "  -- cfa contacts/fund_accounts/hash_lookup (seeds/generated/s4/cfa.js, Mongo)"
compose exec -T mongo-cfa mongosh cfa --quiet --file /dev/stdin \
  --username cfa_root --password "$MONGO_CFA_ROOT_PW" --authenticationDatabase admin \
  < seeds/generated/s4/cfa.js

echo "  -- generated API balance mirrors and feature rows"
MYSQL_APIDB_ROOT_PW="$(cat secrets/mysql_apidb_root_password.txt)"
compose exec -T mysql-apidb-stub mysql -uroot -p"$MYSQL_APIDB_ROOT_PW" api_local < seeds/generated/s4/apidb.sql
bash scripts/provision-monolith-db.sh

echo "############################################################"
echo "# 7/8  substitutes profile"
echo "############################################################"
if [ "${ARENA_SKIP_BUILD:-0}" != "1" ]; then
  compose --profile datastores --profile substitutes build
fi
compose --profile datastores --profile substitutes up -d
./scripts/healthcheck.sh kong-lite monolith-stub dcs-stub splitz-stub shield-stub \
  pricing-stub asv-stub stork-capture merchant-webhook-sink xas-sim --timeout 120

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
  docker run --rm --pull never --network "rzp-arena${ARENA_SUFFIX:-}" curlimages/curl:latest -fsS -o /dev/null -w "     $ident -> HTTP %{http_code}\n" \
    -X POST -H "Authorization: Basic $LEDGER_AUTH_B64" -H "Ledger-Tenant: X" -H "Content-Type: application/json" \
    -d "{\"ledger_config_data_identifier\":\"$ident\"}" \
    http://ledger-api:8080/twirp/rzp.ledger.ledger_config.v1.LedgerConfigAPI/CreateInBulk
done

echo "  -- (post-core) ledger Direct-account (DA) parent accounts: AccountAPI/CreateInBulk{direct_account_x} (7 parents, ledger internal/account/seed_data/direct_account_x.go), then Activate (CreateInBulk leaves them IN_REVIEW; Shared parents in the SQL seed are ACTIVATED). Idempotent: a re-run answers non-2xx on the UNIQUE account_name and is tolerated. Merchant DA sub-accounts are created per Direct merchant by RED_LOOP/red_loop/ledger_da.py onboard_da_merchant (CreateOnEvent direct_merchant_onboarding)."
DA_HTTP="$(docker run --rm --pull never --network "rzp-arena${ARENA_SUFFIX:-}" curlimages/curl:latest -sS -o /dev/null -w "%{http_code}" \
    -X POST -H "Authorization: Basic $LEDGER_AUTH_B64" -H "Ledger-Tenant: X" -H "Content-Type: application/json" \
    -d '{"account_data_identifier":"direct_account_x"}' \
    http://ledger-api:8080/twirp/rzp.ledger.account.v1.AccountAPI/CreateInBulk || echo 000)"
echo "     direct_account_x parent accounts -> HTTP $DA_HTTP $([ "$DA_HTTP" = "200" ] && echo '(created)' || echo '(non-2xx: already present or ledger unavailable -- verified below)')"
LEDGER_PW_DA="$(cat secrets/postgres_ledger_password.txt)"
for acc_id in $(compose exec -T -e PGPASSWORD="$LEDGER_PW_DA" postgres-ledger psql -U ledger -d ledger -tA -c \
    "SELECT a.id FROM accounts a JOIN account_details d ON d.account_id=a.id WHERE d.tenant='X' AND d.deleted_at IS NULL AND (d.parent_account_id IS NULL OR d.parent_account_id='') AND d.account_name LIKE 'Direct %' AND a.status<>'ACTIVATED'"); do
  docker run --rm --pull never --network "rzp-arena${ARENA_SUFFIX:-}" curlimages/curl:latest -sS -o /dev/null -w "     activate $acc_id -> HTTP %{http_code}\n" \
    -X POST -H "Authorization: Basic $LEDGER_AUTH_B64" -H "Ledger-Tenant: X" -H "Content-Type: application/json" \
    -d "{\"id\":\"$acc_id\"}" http://ledger-api:8080/twirp/rzp.ledger.account.v1.AccountAPI/Activate || true
done
DA_PARENTS="$(compose exec -T -e PGPASSWORD="$LEDGER_PW_DA" postgres-ledger psql -U ledger -d ledger -tA -c \
    "SELECT count(*) FROM accounts a JOIN account_details d ON d.account_id=a.id WHERE d.tenant='X' AND d.deleted_at IS NULL AND (d.parent_account_id IS NULL OR d.parent_account_id='') AND d.account_name LIKE 'Direct %' AND a.status='ACTIVATED'")"
echo "     direct_account_x ACTIVATED parent accounts in ledger: ${DA_PARENTS:-?} (expected 7)"

echo "  -- (post-core) initial reservation reconciliation and trusted-store readiness"
# cron-driver starts before the API and may miss its first tick. The source gate
# intentionally queues until a healthy pass writes its heartbeat. Run the real
# endpoint after health, then check trust: HTTP200 alone does not prove a pass.
compose --profile substitutes exec -T cron-driver python3 /app/driver.py --once reservation_reconcile
python3 scripts/reservation-readiness.py


echo "############################################################"
echo "# LocalStack queues: $(docker compose --env-file .env.arena logs localstack 2>/dev/null | grep -cE '(queue|topic) ') created by seeds/localstack/init-queues.sh"
if [ "${ARENA_HOST_BRIDGE:-1}" = "1" ]; then python3 scripts/ingress.py start --port "${KONG_LITE_HOST_PORT:-18080}"; fi
# Boot identity: input digests, image ids, running container ids, route profile,
# git HEAD, boot id. Written to .runtime/ (git-ignored, no secrets or rendered
# config) and copied into every run directory so the acceptance gate can prove
# which tree and which boot produced each retained run.
python3 scripts/fingerprint.py
echo "# Env 2 arena is up. Entrypoint: http://localhost:${KONG_LITE_HOST_PORT:-18080}"
echo "# Run scripts/golden-run.sh to exercise the verifier."
echo "############################################################"
