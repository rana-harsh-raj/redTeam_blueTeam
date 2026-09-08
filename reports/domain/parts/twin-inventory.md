# twin-inventory (lane D6) — what the Payouts twin ALREADY has

**Subject:** `/Users/rana.singh/rzp-payouts-architecture`, branch `milestone-6-complete-payouts-domain`.
Runtime facts come from the live arena (compose project `env2_compose`) read **read-only** at
`2026-09-07T17:04:12Z`; compose facts come from `git show HEAD:ENV2_COMPOSE/docker-compose.yml`
(byte-identical to the working tree at read time). Machine-readable form:
`reports/domain/parts/twin-inventory.json` (199 nodes, 215 edges, 13 families, `runtime_snapshot`).
Validated: `python3 scripts/domain/build_graph.py --parts reports/domain/parts --out /tmp/gcheck --strict`
→ **0 schema violations, 0 dangling edge endpoints**.

Other lanes are concurrently editing `substitutes/workflow-engine`, `scripts/snapshot`, `RED_LOOP/m6`
and `RED_LOOP/surface/m6_acceptance.py`. Those are listed as **pending** at the end and are not
inventoried as existing capability.

---

## 1. Headline numbers

| | |
|---|---|
| compose services defined at HEAD | **75** |
| containers present in `env2_compose` | **68** (67 running, 1 exited) |
| running and reporting **healthy** | **66** |
| running with **no healthcheck defined** | 1 (`cron-driver`) |
| **not running** | `ledger-scheduler` — Exited (0) 18 h ago |
| never created (by design) | 5 migration one-shots, `verifier` (profile `verify`, `run --rm`), `mozart-mock` (profile `mozart-real`, image unbuildable) |
| memory used by the arena | **4 276 MiB** of **11 934 MiB** Docker VM → **7 658 MiB headroom** (4 CPU, Docker 29.5.2) |
| real Go binaries running | 5 services + 38 worker processes (17 payouts, 6 ledger, 13 fts, 2 cfa, 1 x-balances) |
| substitutes on disk | **18** (`_common` + 17), of which **15 run as compose services** |
| executable journeys already present | **73** (61 in-arena, 12 standalone) across all 13 canonical families |
| host entrypoint | `http://127.0.0.1:18080` → `kong-lite` — `GET /_arena/health` = 200, `routes:8`, `merchants_loaded:137` |

---

## 2. Compose services (with live state)

Profiles: `datastores` → `migrations` → `substitutes` → `core` → `verify` (+ opt-in `mozart-real`).
`$TAG` = `ARENA_TAG` = `v1-candidate`. Named volumes only (bind mounts omitted).

| compose service | profile | image | entrypoint / worker selector | healthcheck | networks | depends_on | volumes | env var names | state |
|---|---|---|---|---|---|---|---|---|---|
| `mysql-payouts` | datastores | mysql:8.0 | `—` | `CMD mysqladmin ping -h 127.0.0.1 -u root -p$$(cat /run/secrets/mysql_p` | rzp-arena | — | mysql-payouts-data | MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD_FILE, MYSQL_ROOT_PASSWORD_FILE | running (healthy) · 378 MiB |
| `mysql-fts` | datastores | mysql:8.0 | `—` | `CMD mysqladmin ping -h 127.0.0.1 -u root -p$$(cat /run/secrets/mysql_f` | rzp-arena | — | mysql-fts-data | MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD_FILE, MYSQL_ROOT_PASSWORD_FILE | running (healthy) · 371 MiB |
| `mysql-xbalances` | datastores | mysql:8.0 | `—` | `CMD mysqladmin ping -h 127.0.0.1 -u root -p$$(cat /run/secrets/mysql_x` | rzp-arena | — | mysql-xbalances-data | MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD_FILE, MYSQL_ROOT_PASSWORD_FILE | running (healthy) · 353 MiB |
| `mysql-apidb-stub` | datastores | mysql:8.0 | `—` | `CMD mysqladmin ping -h 127.0.0.1 -u root -p$$(cat /run/secrets/mysql_a` | rzp-arena | — | mysql-apidb-data | MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD_FILE, MYSQL_ROOT_PASSWORD_FILE | running (healthy) · 367 MiB |
| `postgres-ledger` | datastores | postgres:15-alpine | `—` | `CMD-SHELL pg_isready -U ledger -d ledger` | rzp-arena | — | postgres-ledger-data | POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD_FILE | running (healthy) · 37 MiB |
| `mongo-cfa` | datastores | mongo:6.0 | `—` | `CMD mongosh --quiet --eval db.adminCommand('ping')` | rzp-arena | — | mongo-cfa-data | MONGO_INITDB_ROOT_USERNAME, MONGO_INITDB_ROOT_PASSWORD_FILE, MONGO_INITDB_DATABASE | running (healthy) · 74 MiB |
| `redis` | datastores | redis:7-alpine | `redis-server --save  --appendonly no` | `CMD redis-cli ping` | rzp-arena | — | — |  | running (healthy) · 5 MiB |
| `localstack` | datastores | localstack/localstack:3.8 | `—` | `CMD curl -sf http://127.0.0.1:4566/_localstack/health` | rzp-arena | — | localstack-data | SERVICES, DEFAULT_REGION, AWS_DEFAULT_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, EAGER_SERVICE_LOADING, PERSISTENCE | running (healthy) · 143 MiB |
| `kafka` | datastores | apache/kafka:3.8.0 | `—` | `CMD-SHELL /opt/kafka/bin/kafka-topics.sh --bootstrap-server 127.0.0.1:` | rzp-arena | — | kafka-data | KAFKA_NODE_ID, KAFKA_PROCESS_ROLES, KAFKA_LISTENERS, KAFKA_ADVERTISED_LISTENERS, KAFKA_CONTROLLER_LISTENER_NAMES, KAFKA_LISTENER_SECURITY_PROTOCOL_MAP, KAFKA_CONTROLLER_QUORUM_VOTERS, KAFKA_AUTO_CREATE_TOPICS_ENABLE, KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR, KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR, KAFKA_TRANSACTION_STATE_LOG_MIN_ISR, KAFKA_LOG_DIRS (+1) | running (healthy) · 965 MiB |
| `payouts-migrate` | migrations | rzp-arena/payouts:$TAG | `/app/payouts-migration up` | `—` | rzp-arena | mysql-payouts, mysql-apidb-stub | config-payouts, ${PAYOUTS_REPO_MIGRATIONS_DIR} | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY | not_created |
| `ledger-migrate` | migrations | rzp-arena/ledger:$TAG | `/app/ledger-migration -dir=/app/internal/database/migrations up` | `—` | rzp-arena | postgres-ledger | config-ledger, ${LEDGER_REPO_MIGRATIONS_DIR | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, TENANT, LEDGER_ARENA_PG_PASSWORD | not_created |
| `fts-migrate` | migrations | rzp-arena/fts:$TAG | `/app/fts-migrate -env=arena -dir=/app/internal/migrations up` | `—` | rzp-arena | mysql-fts | config-fts, ${FTS_REPO_MIGRATIONS_DIR | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY | not_created |
| `cfa-migrate` | migrations | rzp-arena/cfa:$TAG | `/app/cfa-entry.sh /app/cfa-migration -v up` | `—` | rzp-arena | mongo-cfa | config-cfa | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY | not_created |
| `xbalances-migrate` | migrations | rzp-arena/xbalances:$TAG | `/app/xbalances-migration -dir=/app/internal/database/migrations up` | `—` | rzp-arena | mysql-xbalances, mysql-apidb-stub | config-xbalances, ${XBALANCES_REPO_MIGRATIONS_DIR} | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY | not_created |
| `payouts-api` | core | rzp-arena/payouts:$TAG | `/app/payouts-api` | `CMD wget -q -O /dev/null http://127.0.0.1:9400/status` | rzp-arena | mysql-payouts, mysql-apidb-stub, redis, localstack, kafka | config-payouts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, ARENA_DCS_URL, ARENA_STORK_JSON | running (healthy) · 43 MiB |
| `payouts-worker-webhook-event` | core | rzp-arena/payouts:$TAG | `/app/payouts-workers` | `CMD wget -q -O /dev/null http://127.0.0.1:9400/status` | rzp-arena | mysql-payouts, redis, localstack | config-payouts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, PAYOUTS_WORKER_NAME, PAYOUTS_WORKER_MAXCONCURRENCY, ARENA_DCS_URL (+1) | running (healthy) · 42 MiB |
| `payouts-worker-queued-payout` | core | rzp-arena/payouts:$TAG | `/app/payouts-workers` | `CMD wget -q -O /dev/null http://127.0.0.1:9400/status` | rzp-arena | mysql-payouts, redis, localstack | config-payouts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, PAYOUTS_WORKER_NAME, PAYOUTS_WORKER_MAXCONCURRENCY, ARENA_DCS_URL (+1) | running (healthy) · 41 MiB |
| `payouts-worker-schedule-payout` | core | rzp-arena/payouts:$TAG | `/app/payouts-workers` | `CMD wget -q -O /dev/null http://127.0.0.1:9400/status` | rzp-arena | mysql-payouts, redis, localstack | config-payouts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, PAYOUTS_WORKER_NAME, PAYOUTS_WORKER_MAXCONCURRENCY, ARENA_DCS_URL (+1) | running (healthy) · 41 MiB |
| `payouts-worker-on-hold-payout` | core | rzp-arena/payouts:$TAG | `/app/payouts-workers` | `CMD wget -q -O /dev/null http://127.0.0.1:9400/status` | rzp-arena | mysql-payouts, redis, localstack | config-payouts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, PAYOUTS_WORKER_NAME, PAYOUTS_WORKER_MAXCONCURRENCY, ARENA_DCS_URL (+1) | running (healthy) · 41 MiB |
| `payouts-worker-fts-async-processing` | core | rzp-arena/payouts:$TAG | `/app/payouts-workers` | `CMD wget -q -O /dev/null http://127.0.0.1:9400/status` | rzp-arena | mysql-payouts, redis, localstack | config-payouts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, PAYOUTS_WORKER_NAME, PAYOUTS_WORKER_MAXCONCURRENCY, ARENA_DCS_URL (+1) | running (healthy) · 41 MiB |
| `payouts-worker-fts-async-hv-processing` | core | rzp-arena/payouts:$TAG | `/app/payouts-workers` | `CMD wget -q -O /dev/null http://127.0.0.1:9400/status` | rzp-arena | mysql-payouts, redis, localstack | config-payouts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, PAYOUTS_WORKER_NAME, PAYOUTS_WORKER_MAXCONCURRENCY, ARENA_DCS_URL (+1) | running (healthy) · 41 MiB |
| `payouts-worker-payout-create-failure-handling` | core | rzp-arena/payouts:$TAG | `/app/payouts-workers` | `CMD wget -q -O /dev/null http://127.0.0.1:9400/status` | rzp-arena | mysql-payouts, redis, localstack | config-payouts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, PAYOUTS_WORKER_NAME, PAYOUTS_WORKER_MAXCONCURRENCY, ARENA_DCS_URL (+1) | running (healthy) · 41 MiB |
| `payouts-worker-payout-update-failure-handling` | core | rzp-arena/payouts:$TAG | `/app/payouts-workers` | `CMD wget -q -O /dev/null http://127.0.0.1:9400/status` | rzp-arena | mysql-payouts, redis, localstack | config-payouts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, PAYOUTS_WORKER_NAME, PAYOUTS_WORKER_MAXCONCURRENCY, ARENA_DCS_URL (+1) | running (healthy) · 41 MiB |
| `payouts-worker-transaction-create` | core | rzp-arena/payouts:$TAG | `/app/payouts-workers` | `CMD wget -q -O /dev/null http://127.0.0.1:9400/status` | rzp-arena | mysql-payouts, redis, localstack | config-payouts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, PAYOUTS_WORKER_NAME, PAYOUTS_WORKER_MAXCONCURRENCY, ARENA_DCS_URL (+1) | running (healthy) · 41 MiB |
| `payouts-worker-generic-processing` | core | rzp-arena/payouts:$TAG | `/app/payouts-workers` | `CMD wget -q -O /dev/null http://127.0.0.1:9400/status` | rzp-arena | mysql-payouts, redis, localstack | config-payouts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, PAYOUTS_WORKER_NAME, PAYOUTS_WORKER_MAXCONCURRENCY, ARENA_DCS_URL (+1) | running (healthy) · 41 MiB |
| `payouts-worker-async-dual-write` | core | rzp-arena/payouts:$TAG | `/app/payouts-workers` | `CMD wget -q -O /dev/null http://127.0.0.1:9400/status` | rzp-arena | mysql-payouts, redis, localstack | config-payouts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, PAYOUTS_WORKER_NAME, PAYOUTS_WORKER_MAXCONCURRENCY, ARENA_DCS_URL (+1) | running (healthy) · 42 MiB |
| `payouts-worker-x-balances-balance-refresh` | core | rzp-arena/payouts:$TAG | `/app/payouts-workers` | `CMD wget -q -O /dev/null http://127.0.0.1:9400/status` | rzp-arena | mysql-payouts, redis, localstack | config-payouts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, PAYOUTS_WORKER_NAME, PAYOUTS_WORKER_MAXCONCURRENCY, ARENA_DCS_URL (+1) | running (healthy) · 41 MiB |
| `payouts-worker-rbl-banking-account-statement` | core | rzp-arena/payouts:$TAG | `/app/payouts-workers` | `CMD wget -q -O /dev/null http://127.0.0.1:9400/status` | rzp-arena | mysql-payouts, redis, localstack | config-payouts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, PAYOUTS_WORKER_NAME, PAYOUTS_WORKER_MAXCONCURRENCY, ARENA_DCS_URL (+1) | running (healthy) · 53 MiB |
| `payouts-worker-payout-source-updater` | core | rzp-arena/payouts:$TAG | `/app/payouts-workers` | `CMD wget -q -O /dev/null http://127.0.0.1:9400/status` | rzp-arena | mysql-payouts, redis, localstack | config-payouts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, PAYOUTS_WORKER_NAME, PAYOUTS_WORKER_MAXCONCURRENCY, ARENA_DCS_URL (+1) | running (healthy) · 41 MiB |
| `payouts-worker-partner-bank-hold-payouts` | core | rzp-arena/payouts:$TAG | `/app/payouts-workers` | `CMD wget -q -O /dev/null http://127.0.0.1:9400/status` | rzp-arena | mysql-payouts, redis, localstack | config-payouts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, PAYOUTS_WORKER_NAME, PAYOUTS_WORKER_MAXCONCURRENCY, ARENA_DCS_URL (+1) | running (healthy) · 41 MiB |
| `payouts-kafka-fts-status-updates-consumer` | core | rzp-arena/payouts:$TAG | `/app/payouts-kafka-consumer` | `CMD pgrep -f payouts-kafka-consumer` | rzp-arena | mysql-payouts, kafka | config-payouts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, PAYOUTS_CONSUMER_TASK_NAME, PAYOUTS_CONSUMER_TASK_MAXCONCURRENCY, ARENA_DCS_URL (+1) | running (healthy) · 38 MiB |
| `payouts-kafka-fts-status-updates-retry-consumer` | core | rzp-arena/payouts:$TAG | `/app/payouts-kafka-consumer` | `CMD pgrep -f payouts-kafka-consumer` | rzp-arena | mysql-payouts, kafka | config-payouts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, PAYOUTS_CONSUMER_TASK_NAME, PAYOUTS_CONSUMER_TASK_MAXCONCURRENCY, ARENA_DCS_URL (+1) | running (healthy) · 38 MiB |
| `ledger-api` | core | rzp-arena/ledger:$TAG | `/app/ledger-api` | `CMD wget -q -O /dev/null --post-data={"service":""} --header=Content-T` | rzp-arena | postgres-ledger, redis, localstack | config-ledger | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY | running (healthy) · 23 MiB |
| `ledger-worker` | core | rzp-arena/ledger:$TAG | `/app/ledger-worker` | `CMD pgrep -f ledger-worker` | rzp-arena | postgres-ledger, redis, localstack | config-ledger | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, LEDGER_WORKER_QUEUENAME, LEDGER_WORKER_MAXCONCURRENCY | running (healthy) · 23 MiB |
| `ledger-worker-balance-update` | core | rzp-arena/ledger:$TAG | `/app/ledger-worker` | `CMD pgrep -f ledger-worker` | rzp-arena | postgres-ledger, redis, localstack | config-ledger | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, LEDGER_WORKER_QUEUENAME, LEDGER_WORKER_MAXCONCURRENCY | running (healthy) · 24 MiB |
| `ledger-worker-journal-create` | core | rzp-arena/ledger:$TAG | `/app/ledger-worker` | `CMD pgrep -f ledger-worker` | rzp-arena | postgres-ledger, redis, localstack | config-ledger | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, LEDGER_WORKER_QUEUENAME, LEDGER_WORKER_MAXCONCURRENCY | running (healthy) · 24 MiB |
| `ledger-worker-entry-details-create` | core | rzp-arena/ledger:$TAG | `/app/ledger-worker` | `CMD pgrep -f ledger-worker` | rzp-arena | postgres-ledger, redis, localstack | config-ledger | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, LEDGER_WORKER_QUEUENAME, LEDGER_WORKER_MAXCONCURRENCY | running (healthy) · 23 MiB |
| `ledger-worker-entry-details-create-pg` | core | rzp-arena/ledger:$TAG | `/app/ledger-worker` | `CMD pgrep -f ledger-worker` | rzp-arena | postgres-ledger, redis, localstack | config-ledger | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, LEDGER_WORKER_QUEUENAME, LEDGER_WORKER_MAXCONCURRENCY | running (healthy) · 24 MiB |
| `ledger-scheduler` | core | rzp-arena/ledger:$TAG | `/app/ledger-scheduler` | `CMD pgrep -f ledger-scheduler` | rzp-arena | postgres-ledger, redis, localstack | config-ledger | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, LEDGER_SCHEDULER_COMMAND | exited |
| `fts-web` | core | rzp-arena/fts:$TAG | `/app/fts-web -env=arena -base_path=/app` | `CMD nc -z 127.0.0.1 8080` | rzp-arena | mysql-fts, redis | config-fts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY | running (healthy) · 25 MiB |
| `fts-worker-default` | core | rzp-arena/fts:$TAG | `/app/fts-worker -env=arena -base_path=/app -command=default` | `CMD pgrep -f fts-worker` | rzp-arena | mysql-fts, redis | config-fts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY | running (healthy) · 24 MiB |
| `fts-worker-initiate-transfer` | core | rzp-arena/fts:$TAG | `/app/fts-worker -env=arena -base_path=/app -command=initiate_transfer` | `CMD pgrep -f fts-worker` | rzp-arena | mysql-fts, redis | config-fts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY | running (healthy) · 29 MiB |
| `fts-worker-check-transfer-status` | core | rzp-arena/fts:$TAG | `/app/fts-worker -env=arena -base_path=/app -command=check_transfer_status` | `CMD pgrep -f fts-worker` | rzp-arena | mysql-fts, redis | config-fts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY | running (healthy) · 25 MiB |
| `fts-worker-fire-transfer-status-webhook` | core | rzp-arena/fts:$TAG | `/app/fts-worker -env=arena -base_path=/app -command=fire_transfer_status_webhook` | `CMD pgrep -f fts-worker` | rzp-arena | mysql-fts, redis | config-fts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY | running (healthy) · 25 MiB |
| `fts-worker-retry-transfer` | core | rzp-arena/fts:$TAG | `/app/fts-worker -env=arena -base_path=/app -command=retry_transfer` | `CMD pgrep -f fts-worker` | rzp-arena | mysql-fts, redis | config-fts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY | running (healthy) · 25 MiB |
| `fts-worker-retry-transfer-preprocessor` | core | rzp-arena/fts:$TAG | `/app/fts-worker -env=arena -base_path=/app -command=retry_transfer_preprocessor` | `CMD pgrep -f fts-worker` | rzp-arena | mysql-fts, redis | config-fts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY | running (healthy) · 24 MiB |
| `fts-worker-retry-transfer-source-update` | core | rzp-arena/fts:$TAG | `/app/fts-worker -env=arena -base_path=/app -command=retry_transfer_source_update` | `CMD pgrep -f fts-worker` | rzp-arena | mysql-fts, redis | config-fts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY | running (healthy) · 24 MiB |
| `fts-worker-rbl-initiate-transfer` | core | rzp-arena/fts:$TAG | `/app/fts-worker -env=arena -base_path=/app -command=rbl::initiate_transfer` | `CMD pgrep -f fts-worker` | rzp-arena | mysql-fts, redis | config-fts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY | running (healthy) · 25 MiB |
| `fts-worker-rbl-check-transfer-status` | core | rzp-arena/fts:$TAG | `/app/fts-worker -env=arena -base_path=/app -command=rbl::check_transfer_status` | `CMD pgrep -f fts-worker` | rzp-arena | mysql-fts, redis | config-fts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY | running (healthy) · 25 MiB |
| `fts-worker-rbl-imps-initiate-transfer` | core | rzp-arena/fts:$TAG | `/app/fts-worker -env=arena -base_path=/app -command=rbl::imps::initiate_transfer` | `CMD pgrep -f fts-worker` | rzp-arena | mysql-fts, redis | config-fts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY | running (healthy) · 25 MiB |
| `fts-worker-rbl-imps-check-transfer-status` | core | rzp-arena/fts:$TAG | `/app/fts-worker -env=arena -base_path=/app -command=rbl::imps::check_transfer_status` | `CMD pgrep -f fts-worker` | rzp-arena | mysql-fts, redis | config-fts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY | running (healthy) · 24 MiB |
| `fts-worker-rbl-direct-imps-initiate-transfer` | core | rzp-arena/fts:$TAG | `/app/fts-worker -env=arena -base_path=/app -command=rbl::direct::imps::initiate_transfer` | `CMD pgrep -f fts-worker` | rzp-arena | mysql-fts, redis | config-fts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY | running (healthy) · 25 MiB |
| `fts-worker-rbl-direct-imps-check-transfer-status` | core | rzp-arena/fts:$TAG | `/app/fts-worker -env=arena -base_path=/app -command=rbl::direct::imps::check_transfer_status` | `CMD pgrep -f fts-worker` | rzp-arena | mysql-fts, redis | config-fts | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY | running (healthy) · 25 MiB |
| `cfa-server` | core | rzp-arena/cfa:$TAG | `/app/cfa-entry.sh /app/cfa-server` | `CMD nc -z 127.0.0.1 8081` | rzp-arena | mongo-cfa, localstack | config-cfa | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, ARENA_DCS_URL, ARENA_STORK_JSON | running (healthy) · 39 MiB |
| `cfa-worker-contact` | core | rzp-arena/cfa:$TAG | `/app/cfa-entry.sh /app/cfa-worker` | `CMD pgrep -f cfa-worker` | rzp-arena | mongo-cfa, localstack | config-cfa | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, CFA_WORKER_QUEUE_NAME, QUEUE_DRIVER, ARENA_DCS_URL (+1) | running (healthy) · 34 MiB |
| `cfa-worker-fa` | core | rzp-arena/cfa:$TAG | `/app/cfa-entry.sh /app/cfa-worker` | `CMD pgrep -f cfa-worker` | rzp-arena | mongo-cfa, localstack | config-cfa | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, CFA_WORKER_QUEUE_NAME, QUEUE_DRIVER, ARENA_DCS_URL (+1) | running (healthy) · 34 MiB |
| `xbalances-server` | core | rzp-arena/xbalances:$TAG | `/app/xbalances-server` | `CMD nc -z 127.0.0.1 8081` | rzp-arena | mysql-xbalances, mysql-apidb-stub, redis, localstack | config-xbalances | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, ARENA_DCS_URL, ARENA_STORK_JSON | running (healthy) · 22 MiB |
| `xbalances-worker` | core | rzp-arena/xbalances:$TAG | `/app/xbalances-worker` | `CMD pgrep -f xbalances-worker` | rzp-arena | mysql-xbalances, redis, localstack | config-xbalances | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_DEFAULT_REGION, WORKDIR, APP_ENV, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, ARENA_DCS_URL, ARENA_STORK_JSON | running (healthy) · 15 MiB |
| `kong-lite` | substitutes | rzp-arena/kong-lite:$TAG | `—` | `CMD wget -qO- http://127.0.0.1:8080/_arena/health` | rzp-arena,rzp-ingress | — | secrets-kong | STUB_PORT, KONG_MERCHANTS_FILE, KONG_MERCHANT_SECRETS_DIR, PASSPORT_PRIVATE_KEY_FILE, PASSPORT_IDENTIFIER, PASSPORT_ISS, PS_API_AUTH_USER, PS_API_AUTH_PASS_FILE, KONG_ENFORCE_ROUTE_POLICY | running (healthy) · 15 MiB |
| `ledger-gate` | substitutes | rzp-arena/ledger-gate:$TAG | `—` | `CMD wget -qO- http://127.0.0.1:8080/health` | rzp-arena | — | — |  | running (healthy) · 13 MiB |
| `monolith-stub` | substitutes | rzp-arena/monolith-stub:$TAG | `—` | `CMD wget -qO- http://127.0.0.1:8080/health` | rzp-arena | — | secrets-monolith | STUB_PORT, STUB_BASIC_AUTH_FILE, MONOLITH_MERCHANTS_FILE, MONOLITH_FUND_ACCOUNTS_FILE, MONOLITH_MISC_FILE, MONOLITH_PRICING_FILE, PS_RELAY_MODE, PAYOUTS_API_HOST, PAYOUTS_API_PORT, PS_RELAY_AUTH_USER, PS_RELAY_AUTH_PASS_FILE | running (healthy) · 17 MiB |
| `dcs-stub` | substitutes | rzp-arena/dcs-stub:$TAG | `—` | `CMD wget -qO- http://127.0.0.1:8080/health` | rzp-arena | — | — | STUB_PORT, DCS_SEED_FILE | running (healthy) · 12 MiB |
| `splitz-stub` | substitutes | rzp-arena/splitz-stub:$TAG | `—` | `CMD wget -qO- http://127.0.0.1:8080/health` | rzp-arena | — | — | STUB_PORT, SPLITZ_SEED_FILE | running (healthy) · 12 MiB |
| `shield-stub` | substitutes | rzp-arena/shield-stub:$TAG | `—` | `CMD wget -qO- http://127.0.0.1:8080/health` | rzp-arena | — | — | STUB_PORT, SHIELD_RULES_FILE, SHIELD_LATENCY_MS | running (healthy) · 12 MiB |
| `pricing-stub` | substitutes | rzp-arena/pricing-stub:$TAG | `—` | `CMD wget -qO- http://127.0.0.1:8080/health` | rzp-arena | — | — | STUB_PORT, PRICING_SEED_FILE | running (healthy) · 12 MiB |
| `asv-stub` | substitutes | rzp-arena/asv-stub:$TAG | `—` | `CMD wget -qO- http://127.0.0.1:8080/health` | rzp-arena | — | — | STUB_PORT | running (healthy) · 11 MiB |
| `bankingaccounts-stub` | substitutes | rzp-arena/bankingaccounts-stub:$TAG | `—` | `CMD wget -qO- http://127.0.0.1:8080/health` | rzp-arena | — | — | STUB_PORT | running (healthy) · 12 MiB |
| `stork-capture` | substitutes | rzp-arena/stork-capture:$TAG | `—` | `CMD wget -qO- http://127.0.0.1:8080/health` | rzp-arena | — | — | STUB_PORT, STORK_SEED_FILE, STORK_EVENTS_LOG_FILE, STORK_REORDER, STORK_DUPLICATE_TERMINAL | running (healthy) · 13 MiB |
| `merchant-webhook-sink` | substitutes | rzp-arena/merchant-webhook-sink:$TAG | `—` | `CMD wget -qO- http://127.0.0.1:8080/health` | rzp-arena | — | — | STUB_PORT, WEBHOOK_SECRET, DELIVERIES_LOG_FILE | running (healthy) · 12 MiB |
| `xas-sim` | substitutes | rzp-arena/xas-sim:$TAG | `—` | `CMD wget -qO- http://127.0.0.1:8080/health` | rzp-arena | — | secrets-monolith | STUB_PORT, STUB_NAME, XAS_SQS_ENDPOINT, XAS_SOURCE_EVENT_QUEUE, XAS_API_BASE_URL, XAS_API_AUTH_FILE | running (healthy) · 16 MiB |
| `mozart-mock` | mozart-real | rzp-arena/mozart:$TAG | `—` | `CMD nc -z 127.0.0.1 8085` | rzp-arena | — | config-mozart-mock | APP_ENV | not_created |
| `mozart-sim` | substitutes | rzp-arena/mozart-sim:$TAG | `—` | `CMD wget -qO- http://127.0.0.1:8085/health` | rzp-arena | — | — | STUB_PORT | running (healthy) · 17 MiB |
| `workflow-sim` | substitutes | rzp-arena/workflow-sim:$TAG | `—` | `CMD wget -qO- http://127.0.0.1:8092/_arena/health` | rzp-arena | — | secrets-kong | STUB_PORT, WORKFLOW_INBOUND_USER, WORKFLOW_INBOUND_PASS, PS_API_URL, WORKFLOW_CALLBACK_USER, WORKFLOW_CALLBACK_PASS_FILE | running (healthy) · 14 MiB |
| `cron-driver` | substitutes | python:3.12-alpine | `python3 /app/driver.py` | `—` | rzp-arena | — | — | PAYOUTS_BASE_URL, CRON_BASIC_AUTH_USER | running · 13 MiB |
| `verifier` | verify | rzp-arena/verifier:$TAG | `—` | `—` | rzp-arena | — | — | ARENA_ENTRYPOINT, PASSPORT_SIGNER_URL, KONG_LITE_URL, PS_PUBLIC_URL, MOZART_MOCK_URL, FTS_WEB_URL, ARENA_M1_MERCHANT_ID, ARENA_M1_BALANCE_ID, ARENA_M1_FUND_ACCOUNT_ID, ARENA_M1_ACCOUNT_NUMBER, ARENA_M2_MERCHANT_ID, ARENA_M2_BALANCE_ID (+6) | not_created |

**Networks.** `rzp-arena` (172.28.16.0/24, `internal: true` — no egress) carries every container;
`rzp-ingress` (172.28.17.0/24) carries only `kong-lite`, which is therefore the sole path from the host.

**Named volumes (17).** `config-{payouts,ledger,fts,cfa,xbalances,mozart-mock}`,
`secrets-{kong,monolith}`, `mysql-{payouts,fts,xbalances,apidb}-data`, `postgres-ledger-data`,
`mongo-cfa-data`, `redis-data`, `localstack-data`, `kafka-data`.

**Worker selectors actually wired.**
`PAYOUTS_WORKER_NAME` ∈ {webhook_event, queued_payout, schedule_payout, on_hold_payout,
fts_async_processing, fts_async_hv_processing, payout_create_failure_handling,
payout_update_failure_handling, transaction_create, generic_processing, async_dual_write,
x_balances_balance_refresh, rbl_banking_account_statement, payout_source_updater,
partner_bank_hold_payouts} (15 of 24 real workers);
`PAYOUTS_CONSUMER_TASK_NAME` ∈ {fts_status_updates, fts_status_updates_retry};
`LEDGER_WORKER_QUEUENAME` ∈ {account_create, balance_update, journal_create,
ledger_entry_details_create, ledger_entry_details_create_pg};
`fts-worker -command=` ∈ {default, initiate_transfer, check_transfer_status,
fire_transfer_status_webhook, retry_transfer, retry_transfer_preprocessor,
retry_transfer_source_update, rbl::initiate_transfer, rbl::check_transfer_status,
rbl::imps::{initiate_transfer,check_transfer_status},
rbl::direct::imps::{initiate_transfer,check_transfer_status}} (13 of ~157);
`CFA_WORKER_QUEUE_NAME` ∈ {contact-lazy-load, fund-account-lazy-load}; x-balances runs one
undifferentiated worker (1 of 5 real bank workers).

---

## 3. Substitutes → implements → fidelity

`sub:* implements svc:*` edges are in the JSON. Control-plane routes are the `/_arena/*` surface;
**every** stub built on `_common/base_stub.py` also serves `POST|GET /_arena/faults`
(scoped delay / status / connection-drop fault injection) and `GET /health`.

| substitute | implements (real service) | business routes | control plane (`/_arena/*`) | verdict |
|---|---|---|---|---|
| `kong-lite` | `svc:edge-kong` (Kong + edge) | 8 prefix routes: `/v1/payouts`, `/v1/contacts`, `/v1/fund_accounts`, `/twirp/rzp.payouts` → payouts-api:9400; `/twirp/ledger` → ledger-api; `/v1/fts` → fts-web; `/twirp/cfa` → cfa-server; `/v1/balances` → xbalances-server | `/_arena/health`, `POST /_arena/mint` | **CONTRACT-FAITHFUL** for the merchant API-key → passport shape (RS256, v3 static kid). **MISSING** the monolith ingress `POST /v1/payouts` middleware, public-API `idempotency_keys` replay, and the Splitz `DirectToPayoutsServiceGate`. `route_policy.py` compiles a fail-closed DENY/ALLOW table from `payouts/internal/routing/router/*.go`, gated by `KONG_ENFORCE_ROUTE_POLICY`. |
| `ledger-gate` | *(none — twin-only)* | verbatim reverse proxy of every path to `ledger-api:8080` | `POST /_arena/faults` (scope = merchant/transactor id; path prefix, status, `delay_ms` 0–30000, drop; `{clear:true}`), `GET /_arena/faults` | **Twin-only transport gate.** Forwards every header except hop-by-hop, so Ledger's `idempotency-key` request-idempotency stays live on the payouts leg (DEV-001, `fixed_in_m1`). FTS and monolith reach `ledger-api` directly. Offline proof: `test_contract.py`, 6 cases. |
| `monolith-stub` | `svc:api-monolith` | 22 routes: `GET /internal/merchants/{id}`, the `payouts_service/*` group (fetch_pricing_info, deduct_credits, reverse_credits, source_update, status_details_source_update, mail_and_sms, dual_write, decrement_free_payouts, create_ledger, free_payout_rollback, create, `create_fta/{id}`), `GET /fund_accounts_internal/fa_{id}`, `POST /merchant/on_hold_slas_internal`, `GET /actor_info_internal/{id}`, `POST /users_internal`, `POST /update_fts_fund_transfer`, `GET /internal_balances_queued`, 3 BAS `payout_update` aliases | `balance-sync`, `relay-control`, `relay`, `relay/release`, `GET log`, `GET ledger_emits`, `GET/POST merchant_features` | **MIXED.** CONTRACT-FAITHFUL for `update_fts_fund_transfer` (byte-level match with `api Attempt/Core.php` + `Status.php`) and `GET /internal/merchants/{id}`. **INCORRECT** for `create_fta` timeout semantics, `dual_write`, `status_details_source_update`, `deduct_credits`, `fetch_pricing_info` (ignores channel/method/amount), `decrement_free_payouts` key, queued low-balance dequeue. **MISSING** `payouts/bulk_approve`. (CURRENT_TWIN_FIDELITY gap #2.) |
| `dcs-stub` | `svc:dcs` | `POST /v1/auth/login`, `/v1/kv/{get,evaluate,put,patch,audit,entities}` | `GET /_arena/legacy/{merchant_id}` | **CONTRACT-FAITHFUL** — real protobuf wire encoder verified against `goutils/dcs` generated gateway + `config-proto`. Documented arena patch: payouts is pointed at it via `ARENA_DCS_URL` because real endpoint resolution is environment-derived. |
| `splitz-stub` | `svc:splitz` | `Evaluate`, `EvaluateBulk`, `FetchDecisionContext`, `AllowCors` (Twirp JSON) | — | **INCORRECT for payouts** and the single largest fidelity error (gap #1): arena config sets `client_side_eval=true` (prod `false`) and `FetchDecisionContext` returns empty, so every payouts experiment silently takes the code default. Pricing source, merchant-config source, bulk concurrency, workflow naming and the Direct cutover cannot be exercised. |
| `shield-stub` | `svc:shield` | `POST /v1/rules/evaluate/payout` | — | **CONTRACT-FAITHFUL** (shield-sdk `rule_evaluation/service.go`, `payout_evaluate.go`); allow/block/review, payouts branches only on `block`. V22 drives a scoped delay to prove fail-open. |
| `pricing-stub` | *(alias only)* | `POST /fetch_pricing_info` | — | **UNUSED.** No rendered client config points at it; payouts `[ccSdk]` is mock and no `[ccSdk]` section is rendered. The real arena pricing source is `monolith-stub`. **Runs healthy, receives zero traffic.** |
| `asv-stub` | `svc:asv` (Account Service) | `POST /account/fetch` | — | **UNREACHABLE BY DESIGN.** Payouts' ASV client is real gRPC (`goutils/account-service` `EstablishConnectionWithConfig`, unconditional at construction); an HTTP/JSON stub cannot be its peer. Documented placeholder. **Runs healthy, receives zero traffic.** |
| `bankingaccounts-stub` | `svc:banking-accounts` | `GET /payouts/shield/merchant/{mid}/details`, `GET /merchant/{mid}/banking_account_by_account_number/{acct}/credentials` | `POST /_arena/reload` | **REPRESENTATIVE** — only the 2 routes payouts calls; others return object-shaped 404. RBL credentials are synthetic and `mozart-sim` ignores them (DEV-168 / L-003). The credentials route feeds the **real** `rbl_banking_account_statement` worker. |
| `stork-capture` | `svc:stork` | `WebhookAPI/{Create,List,ProcessEvent}`, `SMSAPI/Send`, `EmailAPI/Send` (Twirp JSON) | `GET /_captured/events`, `GET /_arena/events?merchant=` | **CONTRACT-FAITHFUL** wire contract + HMAC-SHA256 signing; **REPRESENTATIVE** for ordering (real Stork is queued/async, this delivers inline). `STORK_REORDER` / `STORK_DUPLICATE_TERMINAL` simulate out-of-order and duplicate terminal delivery. |
| `merchant-webhook-sink` | `ext:merchant-webhook-endpoint` | `POST /webhook/{merchant_id}` | `GET /_received/events`, `GET /_arena/deliveries` | **CONTRACT-FAITHFUL** receiver: verifies `X-Razorpay-Signature == hex(HMAC-SHA256(secret, raw_body))`, records `X-Razorpay-Event-Id` / `Request-Id`. Every webhook assertion in every journey reads `/_arena/deliveries`. |
| `xas-sim` | `svc:x-account-statements` | `GET /v1/account_statements`, `GET .../fetch_multiple_by_reference_numbers`, `POST /v1/statement/dual_write`, `POST /v1/statement/enrich_statements`, `POST /v1/source_event/fetch` | `POST accounts`, `POST statements/sync`, `POST source_events`, `POST source_events/enqueue`, `GET state`, `POST reset` | **SUBSTITUTE** (m4-fidelity-matrix: production reachability **unknown**, deviations L-005;C-011;C-013;C-014). Grounded in the pristine `x-account-statements@e73fd5a` clone; consumes the **real** payouts `x_account_statement_source_event` SQS queue. Replaced the M1 health-only `xas-sink`. |
| `mozart-sim` | `svc:mozart` | `POST /{ns}/{gateway}/{version}/{transfer_init,transfer_status,gateway_auth,gateway_session,account_statement}` | `GET/POST /_arena/scenario`, `GET /_arena/scenarios`, `GET/POST /_arena/statement` | **REPRESENTATIVE and PRIMARY.** 6 grounded scenarios (`arena_processed_100100` … `arena_reversed_100600`) cross-checked against `mozart/app/testdata/fts/rbl/v1` and `fts error_code.go`, but it ignores gateway/version, covers 6 of ~140 bank codes and has no pending-hold knob (gap #7). |
| `mozart-mock` | `svc:mozart` (real binary) | — | — | **BLOCKED.** `go build` of the mozart clone 404s on private module `github.com/razorpay/integrations-utils`. `rzp-arena/mozart:v1-candidate` is the single `images_unresolved` entry in the boot fingerprint. Service block + Dockerfile + `ARENA_MOZART_IMPL` switch all exist; the image does not. |
| `workflow-sim` | `svc:workflows` | `POST /twirp/rzp.workflows.workflow.v1.WorkflowAPI/Create`; **outbound** `POST {PS}/v1/payouts/payouts_internal/{id}/approve|reject` | `GET /_arena/pending`, `POST /_arena/decide`, `GET /_arena/health` | Create is **byte-compatible** with payouts `WfCreate`; callbacks hit the **real** payouts internal routes with the real Basic `rzp_live` identity. **But OFF BY DEFAULT** — `ARENA_WORKFLOW_HOST` defaults to the frozen dead address `http://127.0.0.1:1`. Container is healthy and idle. |
| `workflow-engine` | `svc:workflows` | Create (byte-compatible) + actor API (create/get/approve/reject/cancel, bearer tokens) + real approve/reject callbacks | admin API for orgs / actors / policies | **M5 RECONSTRUCTION, explicitly labelled** — the real engine repo was unavailable. Interfaces byte-compatible / real-config; the decision engine (orgs, roles, N-of-M, maker/checker separation, expiry, optimistic concurrency, durable SQLite WAL, restart recovery) is reconstructed. Cadence timers and production RBAC deliberately out of scope. **Not a compose service** — runs standalone under `RED_LOOP/m5/scenario_suite.py`. |
| `xas-sink` | `svc:x-account-statements` | — (health only) | `GET /health` | **SUPERSEDED but still declared.** No longer a compose service, yet the directory exists, `config/arena.yaml` still declares `services.xas_sink`, and `.runtime/arena-fingerprint.json` still records `rzp-arena/xas-sink:v1-candidate` instead of `xas-sim`. |
| `cron-driver` | `ext:fastcron` | 9 outbound `POST /v1/cron/*` on fixed intervals | — | **REPRESENTATIVE.** Routes corrected in M1 (3 were wrong; `process_batch_submitted_payouts` and `fund_management_payouts/check` were missing) but the **cadence is assumed** — the FastCron export is an external artefact this identity does not have (gap #4). No healthcheck on the container. |
| `verifier` | *(test harness)* | — | — | pytest image (profile `verify`, `run --rm`), carries the 26-test suite, the 8 M4 boundary gates and the route/bank/kafka scenario runners. |

Aggregate fidelity of the whole twin (`reports/fidelity/PRODUCTION_VS_TWIN_MATRIX.csv`, 138 rows):
REAL 22 · CONTRACT-FAITHFUL 25 · REPRESENTATIVE 34 · INCORRECT 17 · MISSING 37 · UNKNOWN-BLOCKED 3;
114 of 138 rows are fixable from the repo alone. Note that
`reports/fidelity/CURRENT_TWIN_FIDELITY.md` is dated **2026-09-05** and predates the M4/M5
substitutes (`ledger-gate`, `xas-sim`, `workflow-sim`, `workflow-engine`, `asv-stub`), so its gaps
#5 (WFS absent) and #11 (XAS/recon absent) are now partly closed but the document was not re-issued.
The current per-component labels are in `reports/implementation/m4-fidelity-matrix.csv` (43 rows)
and `m5-fidelity-matrix.csv` (18 rows).

---

## 4. Config generation and arena switches

`ENV2_COMPOSE/config/generate.py` renders `generated/<svc>/` from `config/templates/base/<svc>/*`
(13 template files across payouts/ledger/fts/cfa/xbalances + `mozart-mock.toml.tmpl`) using
`config/arena.yaml` (topology, no credentials) plus `secrets/*.txt`. It fails closed on any
unresolved `{{TOKEN}}`, re-applies the preflight forbidden-hostname sanitizer to everything it
writes, and wipes `generated/` on every run. One hard-coded rewrite worth knowing:
`generate.py:427` rewrites `http://ledger-api:8080` → `http://ledger-gate:8080` **for payouts only**,
which is why payouts' live `[ledger] host` is the gate while FTS and the monolith reach Ledger direct.

| switch | default | what it selects |
|---|---|---|
| `ARENA_ROUTE_PROFILE` | `monolith` | The PS→FTS create leg, the FTS→PS status leg and the Kafka producer. `config/routes.py:10` — `monolith{direct_create:F, direct_status:F, kafka:F}`, `direct{T,T,F}`, `direct-create-monolith-status{T,F,F}`, `kafka{T,F,T}`. Applied by rewriting payouts `[configs.fts_request_from_ps]` (whitelist ⇄ `__no_synthetic_merchant__`) and fts `[splitz.experiments]` / `[kafka_producers.fire_transfer_status]`; the rendered TOML is re-parsed before write. |
| `ARENA_MOZART_IMPL` | `mozart-sim` | Resolves `{{MOZART.url}}` to `mozart-sim:8085` or `mozart-mock:8085`. `mozart-mock` also needs `--profile mozart-real` and a buildable image (there is none). |
| `ARENA_WORKFLOW_HOST` | `http://127.0.0.1:1` | payouts `[workflow] host`. The default is a **frozen dead address** so workflow-applicable creates fail 400 exactly as in twin-v1.0. Set `http://workflow-sim:8092` to activate the approval family in-arena. |
| `ARENA_XAS_SOURCE_EVENT_WHITELIST` | *(empty)* | payouts `[configs.account_statement_source_event]` merchant whitelist; empty = every Direct merchant. |
| `ARENA_DCS_URL` | `http://dcs-stub:8080` | Build patch (`build/apply-arena-patches.sh`): DCS routing uses this instead of the real environment-derived endpoint. Can hide DCS endpoint-resolution failures. |
| `ARENA_STORK_JSON` | `1` | Build patch: payouts uses Stork's JSON client instead of protobuf. Can hide protobuf/JSON compatibility differences. |
| `ARENA_TAG` | `v1-candidate` | Image tag on every `rzp-arena/<svc>` image. |
| `ARENA_SUBNET` / `INGRESS_SUBNET` | `172.28.16.0/24` / `172.28.17.0/24` | `rzp-arena` is `internal: true` (no egress); `rzp-ingress` is the only host path. |
| `KONG_LITE_HOST_PORT` | `18080` | The single host-reachable port of the entire arena. |
| `KONG_ENFORCE_ROUTE_POLICY` | `1` (`0` in frozen-baseline clean boots) | Turns kong-lite's fail-closed merchant route policy on/off. The 8 M4 boundary gates require it **on**; the original 26-test suite is validated with it **off**. |
| `ARENA_COMPOSE_PROJECT` / `ARENA_SUFFIX` / `ARENA_NETWORK` | `env2_compose` / *(empty)* / `rzp-arena` | Let the RED_LOOP provisioners target a disposable instance (`env2c_<id>`, `rzp-arena-<id>`, `rzp-arena-secrets-kong-<id>`) instead of the live arena. |
| `ARENA_SKIP_BUILD` | `0` | `up.sh` skips `docker build` (used by clean-boot). |
| `PS_RELAY_MODE` | *(unset)* | monolith-stub relay: normal / drop / hold — lets V16 prove a lost FTS callback leaves the payout stuck in `initiated`. |
| `STORK_REORDER`, `STORK_DUPLICATE_TERMINAL` | *(unset)* | stork-capture out-of-order and duplicate-terminal delivery simulation (M4 G34/G45). |
| `MOZART_BUILD_CONTEXT` | *(unset)* | path to a mozart clone; only meaningful with `--profile mozart-real`. |
| `ARENA_TRACE_DIR`, `ARENA_RUN_DIR`, `ARENA_LIVE_RUN_DIR` | `/results` | where scenario runners write evidence. |

`config/declared-deviations.yaml` carries the numbered `DEV-*` register that pairs with these
(e.g. DEV-001 ledger-gate header drop → `fixed_in_m1`; DEV-024 balance refresh; DEV-168 RBL creds).

---

## 5. Seeds and provisioning — what a fresh synthetic merchant gets

**Static seeds.** `seeds/generator/generate.py` (with `evidence-lock.json`, `schema-evidence.json`,
`schema_compare.py`, `test_generate.py`) renders `seeds/generated/` from the hand-authored
`seeds/*` fixtures: `merchants.json`, `monolith/{merchants,fund_accounts,misc}.json`, `pricing.json`,
`dcs/merchants.json`, `splitz/experiments.json` (8 real experiment ids) + `variant_table.json`,
`shield/rules.json`, `stork/subscriptions.json`, `mozart_scenarios.json`, `cfa-entities.json`,
`pricing-id-reservations.json`, `scenario-index.json`, `provenance.json`, and the S4 datastore
loads `s4/{payouts,ledger,fts,xbalances,apidb}.sql` + `s4/cfa.js`. Schema patches live in
`seeds/schema-patches/{apidb,payouts}.sql`; the API-DB DDL itself is `seeds/mysql/apidb-ddl/00_init.sql`.
`seeds/localstack/init-queues.sh` creates **38** SQS queues / SNS topics (queued_payout,
schedule_payout, webhook_event, transaction_create, journal_create, balance_update,
rbl_banking_account_statement, x_account_statement_source_event, fmp-check/initiate, …).
Three fixture merchants exist: `ARENAM00000001` (Shared/ledger-backed), `ARENAM00000002`
(Direct/RBL current account, in-flight reservation on), `ARENAM00000003` (workflow-enabled).

**Dynamic provisioning (control plane only — the red agent never imports these).**

| capability | module | what a fresh merchant gets |
|---|---|---|
| Shared/pool merchant | `RED_LOOP/red_loop/provisioner.py::provision_funded_merchant` | apidb-stub `merchants` + `balance` (shared/banking); x-balances `balance` (pool/rbl/activated); payouts `banking_accounts` + `counters` + `fund_accounts` + `bank_accounts`; ledger 4 sub-accounts (merchant_va funded, rest 0); cfa contact/fund_account/hash_lookup; monolith-stub `merchants.json` + pricing plan (id reserved via `pricing_ids.reserve_plan_id`); kong-lite key + secret volume (+ restart). Deterministic hashed ids in a range disjoint from M1/M2/M3. **Self-verified by a real payout create before use**; on failure the caller falls back to a fixture attacker with a fresh idempotency namespace and records the fallback. |
| Direct/current-account merchant | `RED_LOOP/red_loop/provisioner_direct.py::provision_direct_merchant` | Everything above in Direct shape, plus apidb `features` + monolith current-account `banking_accounts`; payouts `banking_account_statement_details` (active); **FTS** `bank_accounts`, `fund_accounts`, `source_accounts(CURRENT/'direct')`, `source_account_mappings`, `account_type_mappings`, `preferred_routing_weights`, `direct_account_routing_rules`; ledger FTS-current receivable/payable pair keyed to the new FTS id + 4 merchant sub-accounts at 0 (Direct payouts post no journal); dcs-stub block copied from M2 (`in_flight_reservation_enabled=true`); splitz-stub every M2 variant; stork seed **and** a live `WebhookAPI/Create`; bankingaccounts-stub + kong-lite restarts. Idempotent, reports which rows already existed, and refuses foreign ids (`_refuse_foreign_id`). Has its own `self_check_direct_merchant`. |
| Judge-only canaries | `provisioner.py::mint_victim_canaries` / `provision_campaign` | Fresh unique canary strings in reference/notes/narration on victim-owned payouts, recorded in a judge-only manifest; a canary appearing in an attacker response is a confidentiality impact. Fresh per campaign, so no cross-campaign leakage. |
| Bank/statement fixtures | `red_loop/bas_fixtures.py`, `provisioner_direct.set_mozart_scenario`, `run_reconciler_once` | Per-merchant/per-attempt mozart scenario selection, statement CSV injection, single reconciler tick. |

---

## 6. Executable journeys already present (73)

Fidelity is this lane's runtime judgement: `real_source_running` = the assertion lands on real
compiled binaries end-to-end; `behavioural_placeholder` = a substitute is load-bearing in the path.

| journey id | family | proves | fidelity | mode | source | evidence |
|---|---|---|---|---|---|---|
| `journey:accounting/v04` | accounting | Sequential real Ledger requests dedupe a complete journal DTO (app-level; prod has NO unique index) | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v04_ledger_dedupe.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:accounting/v05` | accounting | payout_initiated journal sums to zero and debits amount + fees (fees include tax) | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v05_ledger_accounting_initiated.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:accounting/v06` | accounting | Real FTS + bank success produces a balanced Shared-only payout_processed journal keyed to the FTS source account actually used | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v06_ledger_accounting_processed.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:accounting/v11` | accounting | MerchantBalance debit is synchronous with the API response (single immediate read, no retry) | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v11_merchant_balance_sync.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:accounting/v23` | accounting | A pricing 500 rejects the payout before dispatch or debit (fail-closed at create_request_submitted) | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v23_fee_fail_closed.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:approval-workflow/m5-approval-success` | approval-workflow | an approved workflow drives the real payout out of pending | high_fidelity_replacement | standalone | `RED_LOOP/m5/scenario_suite.py` | reports/implementation/m5-workflow-scenarios.json (12/12 passed); m5-acceptance.json |
| `journey:approval-workflow/m5-callback-identity` | approval-workflow | the approve/reject callback carries Basic rzp_live + x-creator-id + X-Razorpay-Account and 200/201/409 count as success | high_fidelity_replacement | standalone | `RED_LOOP/m5/scenario_suite.py` | reports/implementation/m5-workflow-scenarios.json (12/12 passed); m5-acceptance.json |
| `journey:approval-workflow/m5-cancellation` | approval-workflow | creator/operator cancellation of a pending workflow | high_fidelity_replacement | standalone | `RED_LOOP/m5/scenario_suite.py` | reports/implementation/m5-workflow-scenarios.json (12/12 passed); m5-acceptance.json |
| `journey:approval-workflow/m5-concurrent-decisions` | approval-workflow | a simultaneous approve and reject resolve to exactly one terminal state | high_fidelity_replacement | standalone | `RED_LOOP/m5/scenario_suite.py` | reports/implementation/m5-workflow-scenarios.json (12/12 passed); m5-acceptance.json |
| `journey:approval-workflow/m5-cross-org-denial` | approval-workflow | an actor from organisation B cannot decide organisation A's workflow | high_fidelity_replacement | standalone | `RED_LOOP/m5/scenario_suite.py` | reports/implementation/m5-workflow-scenarios.json (12/12 passed); m5-acceptance.json |
| `journey:approval-workflow/m5-duplicate-approval` | approval-workflow | a repeated approval by the same approver is idempotent (N-of-M counts distinct approvers) | high_fidelity_replacement | standalone | `RED_LOOP/m5/scenario_suite.py` | reports/implementation/m5-workflow-scenarios.json (12/12 passed); m5-acceptance.json |
| `journey:approval-workflow/m5-duplicate-callback` | approval-workflow | a retried approve/reject callback is idempotent (at-least-once delivery) | high_fidelity_replacement | standalone | `RED_LOOP/m5/scenario_suite.py` | reports/implementation/m5-workflow-scenarios.json (12/12 passed); m5-acceptance.json |
| `journey:approval-workflow/m5-expiry` | approval-workflow | lazy + swept expiry of a pending workflow | high_fidelity_replacement | standalone | `RED_LOOP/m5/scenario_suite.py` | reports/implementation/m5-workflow-scenarios.json (12/12 passed); m5-acceptance.json |
| `journey:approval-workflow/m5-maker-checker-separation` | approval-workflow | the requester cannot be their own approver | high_fidelity_replacement | standalone | `RED_LOOP/m5/scenario_suite.py` | reports/implementation/m5-workflow-scenarios.json (12/12 passed); m5-acceptance.json |
| `journey:approval-workflow/m5-rejection` | approval-workflow | reject is reachable only from pending and lands the payout in rejected | high_fidelity_replacement | standalone | `RED_LOOP/m5/scenario_suite.py` | reports/implementation/m5-workflow-scenarios.json (12/12 passed); m5-acceptance.json |
| `journey:approval-workflow/m5-restart-recovery` | approval-workflow | a genuine mid-flow process restart recovers durable state from SQLite WAL and resumes pending callbacks | high_fidelity_replacement | standalone | `RED_LOOP/m5/scenario_suite.py` | reports/implementation/m5-workflow-scenarios.json (12/12 passed); m5-acceptance.json |
| `journey:approval-workflow/m5-stale-decision` | approval-workflow | optimistic concurrency: a decision on a stale version is rejected, the fresh version succeeds | high_fidelity_replacement | standalone | `RED_LOOP/m5/scenario_suite.py` | reports/implementation/m5-workflow-scenarios.json (12/12 passed); m5-acceptance.json |
| `journey:balance-refresh-reservations/v12` | balance-refresh-reservations | Direct-rail in-flight reservation total equals live reservations | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v12_reservation_gate_total.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:balance-refresh-reservations/v13` | balance-refresh-reservations | processed holds the reservation for balance refresh; failed releases it immediately | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v13_reservation_release_on_terminal.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:bank-channel-routing/bank-ambiguous-with-utr` | bank-channel-routing | synthetic unmapped bank error classifies as pending with the attempt UTR preserved | real_source_running | arena | `ENV2_COMPOSE/verifier/bank_scenarios.py:22` | ENV2_COMPOSE/verifier/BANK_SCENARIOS.md; reports/implementation/bank-control-tests.txt |
| `journey:bank-channel-routing/bank-ambiguous-without-utr` | bank-channel-routing | same pending classification with no attempt UTR | real_source_running | arena | `ENV2_COMPOSE/verifier/bank_scenarios.py:22` | ENV2_COMPOSE/verifier/BANK_SCENARIOS.md; reports/implementation/bank-control-tests.txt |
| `journey:bank-channel-routing/bank-delayed-success` | bank-channel-routing | pending at init and first status check, processed after the second REAL FTS status-check request | real_source_running | arena | `ENV2_COMPOSE/verifier/bank_scenarios.py:22` | ENV2_COMPOSE/verifier/BANK_SCENARIOS.md; reports/implementation/bank-control-tests.txt |
| `journey:bank-channel-routing/bank-duplicate` | bank-channel-routing | DUPLICATE_TXN is pending (PBANK), not failed and not processed | real_source_running | arena | `ENV2_COMPOSE/verifier/bank_scenarios.py:22` | ENV2_COMPOSE/verifier/BANK_SCENARIOS.md; reports/implementation/bank-control-tests.txt |
| `journey:bank-channel-routing/bank-hold` | bank-channel-routing | INITIATED bank code -> initiated, one balanced initiated journal, debit held, no terminal webhook | real_source_running | arena | `ENV2_COMPOSE/verifier/bank_scenarios.py:22` | ENV2_COMPOSE/verifier/BANK_SCENARIOS.md; reports/implementation/bank-control-tests.txt |
| `journey:bank-channel-routing/bank-timeout` | bank-channel-routing | a delayed 504 without a valid Mozart envelope becomes pending MOZART_INDETERMINATE | real_source_running | arena | `ENV2_COMPOSE/verifier/bank_scenarios.py:22` | ENV2_COMPOSE/verifier/BANK_SCENARIOS.md; reports/implementation/bank-control-tests.txt |
| `journey:bank-channel-routing/kafka-direct-after-shared` | bank-channel-routing | Direct payout after a Shared one on the Kafka route | real_source_running | arena | `ENV2_COMPOSE/verifier/kafka_scenarios.py:49` | reports/implementation/KAFKA_ROUTE_EVIDENCE.md |
| `journey:bank-channel-routing/kafka-failed-dropped-direct` | bank-channel-routing | PS Kafka consumer DROPS a Direct FAILED status (fts_status_updates.go:56-62) — expected failure EF-002 | behavioural_placeholder | arena | `ENV2_COMPOSE/verifier/kafka_scenarios.py:49` | reports/implementation/KAFKA_ROUTE_EVIDENCE.md |
| `journey:bank-channel-routing/kafka-failed-dropped-shared` | bank-channel-routing | PS Kafka consumer drops a Shared FAILED status — expected failure | behavioural_placeholder | arena | `ENV2_COMPOSE/verifier/kafka_scenarios.py:49` | reports/implementation/KAFKA_ROUTE_EVIDENCE.md |
| `journey:bank-channel-routing/kafka-reversed-dropped-direct` | bank-channel-routing | PS Kafka consumer drops a Direct REVERSED status — expected failure EF-003 | behavioural_placeholder | arena | `ENV2_COMPOSE/verifier/kafka_scenarios.py:49` | reports/implementation/KAFKA_ROUTE_EVIDENCE.md |
| `journey:bank-channel-routing/kafka-shared-source-failure` | bank-channel-routing | Shared source failure over the Kafka status route (EXPECTED FAILURE: RegisterJobs absent from boot_kafka_consumer.go) | behavioural_placeholder | arena | `ENV2_COMPOSE/verifier/kafka_scenarios.py:49` | reports/implementation/KAFKA_ROUTE_EVIDENCE.md |
| `journey:bank-channel-routing/m4-route-r-remap` | bank-channel-routing | account_type/channel status remap: Direct rbl failed->failed vs Shared failed->reversed | behavioural_placeholder | arena | `RED_LOOP/surface/m4_direct_journeys.py` | reports/implementation/m4-route-coverage.json |
| `journey:bank-channel-routing/m4-route-r1` | bank-channel-routing | FTS -> PS direct transfer_status_webhook | behavioural_placeholder | arena | `RED_LOOP/surface/m4_direct_journeys.py` | reports/implementation/m4-route-coverage.json |
| `journey:bank-channel-routing/m4-route-r2` | bank-channel-routing | FTS -> monolith-stub update_fts_fund_transfer -> PS relay | behavioural_placeholder | arena | `RED_LOOP/surface/m4_direct_journeys.py` | reports/implementation/m4-route-coverage.json |
| `journey:bank-channel-routing/m4-route-r3` | bank-channel-routing | FTS -> Kafka rx-fts-status-update-events -> PS consumer (Direct FAILED/REVERSED dropped: EF-002/EF-003) | behavioural_placeholder | arena | `RED_LOOP/surface/m4_direct_journeys.py` | reports/implementation/m4-route-coverage.json |
| `journey:bank-channel-routing/v18` | bank-channel-routing | One real bank decline maps Shared -> reversed and Direct/RBL -> failed | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v18_shared_vs_direct_failed_remap.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:direct-payouts/m4-a` | direct-payouts | create -> hold/success -> reservation awaiting_balance_refresh -> REAL rbl_banking_account_statement worker ingest -> xas-sim match -> REAL UpdatePayoutAfterBASRecon -> monolith DA emitter - | behavioural_placeholder | arena | `RED_LOOP/surface/m4_direct_journeys.py` | reports/implementation/m4-direct-journeys.json (A-G all PASS) + m4-direct-e2e-acceptance.json + m4-xas-ledger. |
| `journey:direct-payouts/m4-b` | direct-payouts | mozart failure -> failed, no reversal row, no Shared reversal journal, reservation released, payout.failed webhook, duplicate failure idempotent | real_source_running | arena | `RED_LOOP/surface/m4_direct_journeys.py` | reports/implementation/m4-direct-journeys.json (A-G all PASS) + m4-direct-e2e-acceptance.json + m4-xas-ledger. |
| `journey:direct-payouts/m4-c` | direct-payouts | delayed_success (poll1 PENDING, poll2 SUCCESS) -> pending observed -> processed; statement recon once; final accounting once; replayed delayed status idempotent | behavioural_placeholder | arena | `RED_LOOP/surface/m4_direct_journeys.py` | reports/implementation/m4-direct-journeys.json (A-G all PASS) + m4-direct-e2e-acceptance.json + m4-xas-ledger. |
| `journey:direct-payouts/m4-d` | direct-payouts | hold then attempt failure -> failed, reservation released, no Shared reversal, correct webhook/accounting | real_source_running | arena | `RED_LOOP/surface/m4_direct_journeys.py` | reports/implementation/m4-direct-journeys.json (A-G all PASS) + m4-direct-e2e-acceptance.json + m4-xas-ledger. |
| `journey:direct-payouts/route-direct-success` | direct-payouts | Direct create -> bank success -> processed, on the selected route profile | real_source_running | arena | `ENV2_COMPOSE/verifier/route_scenarios.py:16` | reports/implementation/ROUTE_COVERAGE.md; reports/implementation/m3-verifier.json |
| `journey:direct-payouts/route-failed-direct` | direct-payouts | Direct bank failure -> failed with no Shared reversal | real_source_running | arena | `ENV2_COMPOSE/verifier/route_scenarios.py:16` | reports/implementation/ROUTE_COVERAGE.md; reports/implementation/m3-verifier.json |
| `journey:direct-payouts/v08` | direct-payouts | Real Direct bank failure creates NO reversal and NO ledger journal | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v08_ledger_accounting_failed_direct.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:failure-reversal-cancellation/route-failed-shared` | failure-reversal-cancellation | Shared bank failure -> reversed with a credit journal | real_source_running | arena | `ENV2_COMPOSE/verifier/route_scenarios.py:16` | reports/implementation/ROUTE_COVERAGE.md; reports/implementation/m3-verifier.json |
| `journey:failure-reversal-cancellation/route-returned` | failure-reversal-cancellation | A bank RETURNED result produces the reversal/credit path | real_source_running | arena | `ENV2_COMPOSE/verifier/route_scenarios.py:16` | reports/implementation/ROUTE_COVERAGE.md; reports/implementation/m3-verifier.json |
| `journey:failure-reversal-cancellation/v07` | failure-reversal-cancellation | Bank decline traverses FTS -> Shared reversal with an exact fee-inclusive credit and a reversal entity | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v07_ledger_accounting_reversed.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:failure-reversal-cancellation/v16` | failure-reversal-cancellation | A dropped terminal relay leaves the payout stuck in `initiated` and no cron route repairs it (drives all 8 FastCron routes) | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v16_stuck_initiated_no_repair.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:failure-reversal-cancellation/v17` | failure-reversal-cancellation | Payout/reversal rows commit while the real Ledger call is delayed or unavailable (2 cases, ledger-gate fault injection) | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v17_reversal_ordering.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:idempotency-retries/m4-g52` | idempotency-retries | G52 — concurrent creates with the same idempotency key yield one payout | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_m4_g52_concurrent_idempotency.py` | reports/implementation/m4-boundary-results.json (gates G34/G45/G46/G47/G48/G49/G50/G52 all pass; H-D3 xfail) |
| `journey:idempotency-retries/v01` | idempotency-retries | Same X-Payout-Idempotency key + same body -> same payout id | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v01_idempotency_same_key_same_body.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:idempotency-retries/v02` | idempotency-retries | Same idempotency key + different body -> 400 SameIdempotencyKeyDifferentRequest | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v02_idempotency_same_key_different_body.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:idempotency-retries/v03` | idempotency-retries | FTS /v1/transfer dedupes on (source_id, source_type) | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v03_fts_transfer_dedupe.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:internal-service-routes/m4-g46` | internal-service-routes | G46 — merchant A cannot read or change merchant B's resources through the hardened edge (both directions) | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_m4_g46_cross_merchant.py` | reports/implementation/m4-boundary-results.json (gates G34/G45/G46/G47/G48/G49/G50/G52 all pass; H-D3 xfail) |
| `journey:internal-service-routes/m4-g47` | internal-service-routes | G47 — request-body merchant_id/account_number cannot move a payout onto another tenant | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_m4_g47_body_identity_override.py` | reports/implementation/m4-boundary-results.json (gates G34/G45/G46/G47/G48/G49/G50/G52 all pass; H-D3 xfail) |
| `journey:internal-service-routes/m4-g48` | internal-service-routes | G48 — forged passports / service-shaped credentials confer no privilege | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_m4_g48_forged_service_headers.py` | reports/implementation/m4-boundary-results.json (gates G34/G45/G46/G47/G48/G49/G50/G52 all pass; H-D3 xfail) |
| `journey:internal-service-routes/m4-g49` | internal-service-routes | G49 — internal-only routes are unreachable from merchant credentials, and the same route IS reachable in-arena with a service credential | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_m4_g49_internal_routes_unreachable.py` | reports/implementation/m4-boundary-results.json (gates G34/G45/G46/G47/G48/G49/G50/G52 all pass; H-D3 xfail) |
| `journey:internal-service-routes/m4-g50` | internal-service-routes | G50 — the denial is at the gateway, not the absence of the route (broker fingerprint) | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_m4_g50_denial_layer_distinction.py` | reports/implementation/m4-boundary-results.json (gates G34/G45/G46/G47/G48/G49/G50/G52 all pass; H-D3 xfail) |
| `journey:internal-service-routes/m4-hd3` | internal-service-routes | H-D3 — free_payout ownership: the verified F-M4-001 IDOR (recorded XFAIL, i.e. the defect is still present by design of the frozen baseline) | behavioural_placeholder | arena | `ENV2_COMPOSE/verifier/verifiers/test_m4_hd3_free_payout_ownership.py` | reports/implementation/m4-boundary-results.json (gates G34/G45/G46/G47/G48/G49/G50/G52 all pass; H-D3 xfail) |
| `journey:internal-service-routes/v15` | internal-service-routes | FTS status webhook on a non-initiated (terminal) payout -> 200 with no state change | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v15_fts_webhook_state_guard.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:queued-low-balance/route-queued` | queued-low-balance | A queued payout is dequeued when balance allows | real_source_running | arena | `ENV2_COMPOSE/verifier/route_scenarios.py:16` | reports/implementation/ROUTE_COVERAGE.md; reports/implementation/m3-verifier.json |
| `journey:queued-low-balance/v09` | queued-low-balance | Insufficient balance with queue_if_low_balance=true -> queued | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v09_balance_auth_queued.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:queued-low-balance/v10` | queued-low-balance | Insufficient balance with queue_if_low_balance=false -> 400 at create, no debit | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v10_balance_auth_failed.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:queued-low-balance/v24` | queued-low-balance | Cron dequeue of queued and scheduled payouts via the real /v1/cron/* routes (2 cases: V24a queued, V24b scheduled) | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v24_cron_dequeue.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:scheduled-payouts/route-scheduled` | scheduled-payouts | A scheduled payout is dispatched at its slot by the real cron route | real_source_running | arena | `ENV2_COMPOSE/verifier/route_scenarios.py:16` | reports/implementation/ROUTE_COVERAGE.md; reports/implementation/m3-verifier.json |
| `journey:shared-payouts/golden-shared` | shared-payouts | One fixed Shared payout driven end-to-end through the REAL merchant API-key ingress at kong-lite (the only journey that exercises an actual merchant key rather than a minted passport); write | real_source_running | arena | `ARCHITECTURE_EXPLORER/run-golden.py -> ARCHITECTURE_EXPLORER/server.py:41 golden() -> ENV2_COMPOSE/verifier/scenario.py` | reports/implementation/EXPLORER_VERIFICATION.md; explorer-final-saved-verification.json; reports/implementatio |
| `journey:shared-payouts/v14` | shared-payouts | Observed transition log matches the pinned payouts state_machine.go transition table | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v14_state_machine_legality.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:shared-payouts/v20` | shared-payouts | Another merchant cannot fetch or cancel a payout (tenant isolation) | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v20_tenant_isolation.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:shared-payouts/v22` | shared-payouts | A scoped Shield delay is observed on a real create and fails open | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v22_shield_fail_open.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:statement-reconciliation/m4-e` | statement-reconciliation | link BEFORE terminal status: the REAL VerifyPayoutFailedTransaction refuses on the FTS-direct route (R1) while the monolith-relay route still moves it to failed (F-T10-1) | behavioural_placeholder | arena | `RED_LOOP/surface/m4_direct_journeys.py` | reports/implementation/m4-direct-journeys.json (A-G all PASS) + m4-direct-e2e-acceptance.json + m4-xas-ledger. |
| `journey:statement-reconciliation/m4-f` | statement-reconciliation | identical CSV twice -> no new BAS row (dedup key), no duplicate journal, no double reservation release | behavioural_placeholder | arena | `RED_LOOP/surface/m4_direct_journeys.py` | reports/implementation/m4-direct-journeys.json (A-G all PASS) + m4-direct-e2e-acceptance.json + m4-xas-ledger. |
| `journey:statement-reconciliation/m4-g` | statement-reconciliation | credit statement for a failed payout -> failed->reversed with a reversal transaction_id; wrong-amount statement -> external with no link/journal (negative control) | behavioural_placeholder | arena | `RED_LOOP/surface/m4_direct_journeys.py` | reports/implementation/m4-direct-journeys.json (A-G all PASS) + m4-direct-e2e-acceptance.json + m4-xas-ledger. |
| `journey:statement-reconciliation/v19` | statement-reconciliation | A set transaction_id blocks the failed transition (adapter-boundary guard) | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v19_direct_failed_verification_guard.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |
| `journey:webhooks/m4-g34` | webhooks | G34/G45 — duplicate terminal FTS webhook for a Direct payout does not change status and adds no merchant delivery | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_m4_g34_duplicate_terminal_webhook.py` | reports/implementation/m4-boundary-results.json (gates G34/G45/G46/G47/G48/G49/G50/G52 all pass; H-D3 xfail) |
| `journey:webhooks/v21` | webhooks | Exact terminal webhook delivery after an observed details-only update | real_source_running | arena | `ENV2_COMPOSE/verifier/verifiers/test_v21_webhook_completeness.py` | reports/implementation/VERIFIER_RESULTS.md (replay19/replay20: 26/0/0); reports/implementation/runs/replay20/f |

### Journey counts by family

| family | executable journeys | in-arena | standalone |
|---|---:|---:|---:|
| `family:accounting` | 5 | 5 | 0 |
| `family:approval-workflow` | 12 | 0 | 12 |
| `family:balance-refresh-reservations` | 2 | 2 | 0 |
| `family:bank-channel-routing` | 16 | 16 | 0 |
| `family:direct-payouts` | 7 | 7 | 0 |
| `family:failure-reversal-cancellation` | 5 | 5 | 0 |
| `family:idempotency-retries` | 4 | 4 | 0 |
| `family:internal-service-routes` | 7 | 7 | 0 |
| `family:queued-low-balance` | 4 | 4 | 0 |
| `family:scheduled-payouts` | 1 | 1 | 0 |
| `family:shared-payouts` | 4 | 4 | 0 |
| `family:statement-reconciliation` | 4 | 4 | 0 |
| `family:webhooks` | 2 | 2 | 0 |
| **total** | **73** | **61** | **12** |

### How they are actually run

| suite | how | count | arena? |
|---|---|---:|---|
| original verifier suite | `docker compose --profile verify run --rm verifier` → pytest `verifier/verifiers/test_v*.py` | 24 files / **26 test functions** (V17 and V24 each carry 2) | yes |
| M4 boundary gates | same image, `verifier/m4_boundary.py` + `verifiers/test_m4_*.py`, requires `KONG_ENFORCE_ROUTE_POLICY=1` | 8 files → 12 gate outcomes (G34, G45, G46×2, G47×2, G48, G49, G50, G52×2, H-D3 xfail) | yes |
| route scenarios | `verifier/route_scenarios.py --output /results/...` | 6 cases | yes |
| bank scenarios | `verifier/bank_scenarios.py --scenarios ...` (see `BANK_SCENARIOS.md`) | 6 cases | yes |
| kafka scenarios | `verifier/kafka_scenarios.py` (needs `ARENA_ROUTE_PROFILE=kafka`) | 5 cases, 4 of them **expected failures** | yes |
| M4 Direct journeys | `python3 RED_LOOP/surface/m4_direct_journeys.py` — self-provisions 2 fresh Direct merchants | 7 journeys (A–G) + 4-route matrix + 6 invariants | yes |
| M5 workflow scenarios | `python3 RED_LOOP/m5/scenario_suite.py` — boots a standalone `workflow-engine` + payout-sink, incl. a genuine mid-flow process restart | 12 scenarios | **no — standalone** |
| Explorer golden | `python3 ARCHITECTURE_EXPLORER/run-golden.py [--with-egress-audit]` | 1 golden Shared payout via a real merchant API key | yes |

The M4 driver also asserts 6 named invariants, all recorded PASS in
`reports/implementation/m4-direct-journeys.json`: `I-Direct-failure-no-shared-reversal(G31)`,
`I-Direct-success-no-duplicate-ledger(G54)`, `I-reservation-not-released-twice(G53)`,
`I-balance-not-decremented-twice(G28-adj)`, `I-conservation(G51)`, `I-reconciliation-correct-tuple(G26)`.

---

## 7. Boot / refresh tooling that exists

| tool | what it does |
|---|---|
| `ENV2_COMPOSE/scripts/up.sh` | gen secrets → generate config → preflight → datastores → migrations → seeds → substitutes → core → health wait → write the boot fingerprint. Assumes images already built. Honours `ARENA_SKIP_BUILD`, `REGEN_SECRETS`, `ARENA_SOURCE_REPOS_ROOT`. |
| `scripts/down.sh` | tears down every profile and **destroys secrets + generated config + volumes** by default; `--keep-volumes` opts out. |
| `scripts/clean-boot.sh <instance-id> [workdir]` | disposable empty-volume boot in a **separate working copy** (host secrets/config/.runtime untouched), runs the 26-test suite in frozen-baseline mode (`KONG_ENFORCE_ROUTE_POLICY=0`) with a same-window egress audit; teardown is a separate step (`scripts/instance-cleanup.py`) so results survive. Paired with `scripts/instance-plan.py`. |
| `scripts/fingerprint.py` | writes `.runtime/arena-fingerprint.json` (schema_version 1): `boot_id`, `compose_project`, `config_digest`, **126 `config_hashes`** (compose file, templates, generators, seeds, substitutes, verifier, cron driver), `git_head`, `images` (21 tags → image id), `images_available`, `images_unresolved`, `route_profile`, `recorded_at`, **67 `service_container_ids`**. Every run-producing command copies it beside its results. Non-secret by construction. |
| `scripts/snapshot.sh <label>` / `scripts/restore.sh <label>` | dump/reload every datastore to `snapshots/<label>/` (mysqldump / pg_dump / mongodump — data only, no credentials). Restore needs the datastores profile already up. |
| `scripts/reset.sh`, `scripts/healthcheck.sh`, `scripts/scenarios.sh`, `scripts/golden-run.sh` | reset state, wait-for-health, drive the scenario runners, drive the golden run. |
| `scripts/local-acceptance.py` | derives `reports/implementation/local-acceptance.json` (schema_version 2) purely from named run artifacts it reads and hashes; a check it cannot compute is `false` with a `reason`, never absent. Offline, touches no container. |
| `scripts/resource-profile.py`, `scripts/reservation-readiness.py`, `scripts/route-evidence.py`, `scripts/compare-replays.py`, `scripts/capture-command.py` | resource sampling, reservation-heartbeat readiness gate, route evidence extraction, logical-equivalence replay comparison, audited command capture. |
| `scripts/ingress.py` | host-side ingress helper (pid/log under `.runtime/`). |
| `build/prepare-repos.py` | extracts exact Git HEAD archives from source clones (never edits them), admits copies only after a Gitleaks 8.30.1 scan, writes a per-file provenance manifest incl. commits/dirty status, exclusions, generator versions/hashes and arena-patch hashes. |
| `build/check-inputs.py` | re-verifies the admitted input inventory + hashes + arena patch files/script, optional `go mod verify`; non-zero on anything missing/changed/unapproved. |
| `build/build-host.sh` | builds the 5 Go binaries on the host (`GOPROXY=off`, `GOFLAGS=-mod=readonly`, `GOTOOLCHAIN=local`, `CGO_ENABLED=0`, linux/arm64) then copies only binaries into credential-free runtime images. `build/build.sh` is the in-Docker alternative. |
| `build/record-rebuild.py` | rebuilds one core image and writes a fresh `.local/twin-repos/build-evidence-<UTC>` directory (build log, `build-results.json`, `image-assets.json`, asset-readability) that `preflight/audits/current_boot.py` reads. |
| `build/apply-arena-patches.sh` + `build/arena-patches/` | the only two behaviour-changing patches: `ARENA_DCS_URL` DCS routing and `ARENA_STORK_JSON` Stork JSON client. With the flags unset the original paths remain. |
| `preflight/preflight.py` | independent gate over `generated/`: forbidden hostnames / ARNs / link-local, run as a separate step from `generate.py`'s own sanitizer. |
| `network/egress_audit.py`, `network/egress-audit.sh`, `network/dns-check.sh` | same-window DNS/packet egress audit that wraps every acceptance-grade command. |
| `secrets/gen-secrets.sh`, `secrets/materialize.py`, `secrets/destroy.sh` | per-arena credential generation, materialization into volumes, destruction at teardown. 27 secret files + a passport RSA keypair + JWKS; none are in any image. |

**BUILD_PROVENANCE.md.** All 5 real services built exit 0 from pinned HEADs — payouts `4bf3dbf9`
(1425 files, 26.08 s), ledger `471ff4d5` (738, 22.29 s), fts `2a09e763` (1101, 14.15 s),
cfa `d488558e` (475, 14.87 s), x-balances `1a21c0f9` (539, 11.74 s). Go 1.26.6, linux/arm64,
`go mod verify` passed for all five, 0 unresolved secret findings in the admitted sources.
Generated RPC from `proto@52682577`. `.runtime/arena-fingerprint.json` records 21 image tags →
image ids, with `images_available: false` and exactly one unresolved image (`rzp-arena/mozart`).

---

## 8. Evidence: which artifact proves which journeys

| journeys | evidence artifact |
|---|---|
| V01–V24 (26 tests) | `reports/implementation/VERIFIER_RESULTS.md` — replay19 and replay20 both **26/0/0** with passing DNS/packet audits, 45.9 s / 46.3 s; `runs/replay19|replay20/full/junit.xml` + `egress.json` + traces; logical equivalence in `logical-replay-final.json`; corrections in `VERIFIER_REPAIR_NOTES.md` |
| M4 boundary gates G34/G45/G46/G47/G48/G49/G50/G52 + H-D3 | `reports/implementation/m4-boundary-results.json` (all pass; H-D3 recorded **xfail** = the verified `free_payout` IDOR F-M4-001 is still present in the frozen baseline) |
| M4 Direct journeys A–G, routes R1/R2/R3/R-remap, 6 invariants | `m4-direct-journeys.json` (A–G all PASS), `m4-route-coverage.json`, `m4-invariant-results.json`, `m4-direct-e2e-acceptance.json`, `m4-direct-e2e-evidence-manifest.json(.sha256)`, `m4-direct-e2e-replay.json`, `m4-direct-e2e-soak.json` |
| Direct provisioning + statement ingest + XAS/ledger | `m4-direct-provision.json`, `m4-bas-ingest.json` (`all_pass`), `m4-xas-ledger.json` (`all_green`) |
| M5 approval-workflow (12 scenarios) | `m5-workflow-scenarios.json` (**12/12 passed**), `m5-acceptance.json`, `m5-benchmark-integrity.json`, `m5-finding-replays.json` |
| Explorer golden Shared payout | `EXPLORER_VERIFICATION.md`, `explorer-final-saved-verification.json`, `runs/explorer-*/live-result.json` + `live-golden-shared.jsonl` + the copied `arena-fingerprint.json` |
| route / bank / kafka scenario runners | `ROUTE_COVERAGE.md`, `ROUTE_SOURCE_EVIDENCE.md`, `RETURNED_ROUTE_EVIDENCE.md`, `bank-control-tests.txt`, `KAFKA_ROUTE_EVIDENCE.md`, `m3-verifier.json` |
| clean-checkout / independent replay | `m41-clean-checkout-acceptance.json` (74/74), `m41-independent-audit.md`, `m41-gate-validation-matrix.csv`, `m4-lifecycle-validation.json` |
| build + boot integrity | `BUILD_PROVENANCE.md`, `acceptance-manifest.json`, `final-gate-verification.json`, `compose-materialization-validation.json`, `local-acceptance.json` |
| safety envelope | `SECRET_AND_ENDPOINT_AUDIT.md`, `expanded-secret-scan.json`, `remaining-volume-secret-scan.json`, `EGRESS_AUDIT.md`, `m5-safety-egress.json` |

---

## 9. Resource envelope (snapshot `2026-09-07T17:04:12Z`)

Docker VM: **11 934 MiB** total, 4 CPU, Docker 29.5.2. Arena in use: **4 275.9 MiB across 67
containers** → **7 658.4 MiB headroom (64 %)**. CPU is idle (nothing above 5.7 %).

| container | MiB |
|---|---:|
| `kafka` | 964.6 |
| `mysql-payouts` | 377.8 |
| `mysql-fts` | 371.2 |
| `mysql-apidb-stub` | 366.9 |
| `mysql-xbalances` | 352.8 |
| `localstack` | 142.7 |
| `mongo-cfa` | 74.2 |
| `payouts-worker-rbl-banking-account-statement` | 52.9 |
| `payouts-api` | 42.9 |
| every other payouts/fts/ledger/cfa/xbalances process | ≈ 20–42 each |
| every Python substitute | ≈ 12–17 each |

Practical reading: the datastores are **2 576 MiB (60 %)** of the whole arena and Kafka alone is
**23 %**. Adding another ~40 Go worker processes (~40 MiB each ≈ 1.6 GiB) or a second full arena
instance for a disposable clean boot (~4.3 GiB) both fit in the current headroom; a third
simultaneous arena would not.

---

## 10. The five biggest "runs but unverified" / "declared but missing" items

1. **`ARENA_WORKFLOW_HOST` defaults to the dead address `http://127.0.0.1:1`** — `workflow-sim` is
   healthy and idle, and the 12 approval-workflow scenarios are the **only** family with zero
   in-arena journeys. The approval path is proven against a standalone reconstruction
   (`substitutes/workflow-engine`, not a compose service) and never against the running arena.
2. **14 substitute containers are running image ids that no longer match their current
   `rzp-arena/*:v1-candidate` tags** (kong-lite, monolith-stub, dcs-stub, splitz-stub, shield-stub,
   pricing-stub, asv-stub, bankingaccounts-stub, stork-capture, merchant-webhook-sink, mozart-sim,
   workflow-sim, xas-sim, ledger-gate — the 5 real Go services do match). Nothing detects this:
   `fingerprint.py` records image ids only at boot, and no gate re-compares them afterwards. The
   fingerprint additionally still names **`rzp-arena/xas-sink`**, a service the compose file no
   longer has, and never names `xas-sim`.
3. **`asv-stub` and `pricing-stub` are healthy but provably receive zero traffic.** ASV's real
   client is gRPC so a JSON stub can never be its peer (its own CONTRACT.md says so); pricing has no
   client config pointing at it at all. Two green healthchecks that assert nothing.
4. **`ledger-scheduler` has been Exited (0) for 18 hours** — the only non-running service in the
   `core` profile. No restart policy brought it back, no journey asserts on it, and nothing in the
   acceptance chain notices. Either it is genuinely not needed in the arena, or a scheduled ledger
   path is silently untested.
5. **`splitz-stub` is INCORRECT for payouts and the whole arena runs on code defaults.**
   `client_side_eval=true` (prod `false`) plus an empty `FetchDecisionContext` means every payouts
   experiment silently takes its code default, so pricing source, merchant-config source, bulk
   concurrency, workflow naming and the Direct cutover cannot be exercised — yet 61 in-arena
   journeys run over this and none of them flags it.

Runners-up worth recording: `mozart-mock` is **declared but unbuildable** (`rzp-arena/mozart` is the
one unresolved image, private module `integrations-utils`); the **FTS→Kafka producer leg is off** in
the default `monolith` profile so the two real PS Kafka consumers run with no input (4 of the 5
kafka scenarios are recorded expected failures); and the **cron cadence is assumed** for
`process_batch_submitted_payouts` and `fund_management_payouts/check`.

---

## 11. Pending M6 work (other lanes — inventoried as pending, not as capability)

- `RED_LOOP/m6/clean_boot.py` (untracked) — down → assert empty volumes/secrets/generated → fresh
  `up.sh` → health → `RED_LOOP/m6/journeys/run.py`. **`RED_LOOP/m6/journeys/` is currently empty.**
- `RED_LOOP/surface/m6_acceptance.py` (untracked) — evaluates 10 M6 gates against
  `PAYOUTS_FUNCTIONAL_GRAPH.json`, `GRAPH_STATS.json`, `REPOSITORY_INVENTORY_M6.csv`,
  `SNAPSHOT_MANIFEST.json`, `m6-journeys.json`, `m6-clean-boot.json`, `m6-refresh-demo.json` —
  **none of which exist yet**.
- `scripts/snapshot/{capture,diff,affected,recipes,common}.py` (untracked) — domain snapshot/diff
  tooling that pins graph version, repo shas, recipe/config/schema/contract hashes and image digests.
- `ENV2_COMPOSE/substitutes/workflow-engine/wfengine/{callbacks,identity,server}.py` — modified in
  the working tree by another lane.
- `reports/domain/{README.md, REMAINING_ACCESS_MANIFEST.md, REPOSITORY_INVENTORY_M6.csv}` and
  `scripts/domain/` are untracked M6 scaffolding shared with the other discovery lanes.
