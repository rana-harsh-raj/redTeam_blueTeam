# Build Spike: fts

Status: COMPLETE

SUMMARY: compile=✔ migrate=✔ boot=✔ health=✔

## Build

No proto/buf/protoc codegen needed — `grep -rl "^package main"` under `fts/` and a repo-wide search for `*.proto` / `buf.yaml` / `buf.gen.yaml` found nothing. fts vendors its generated dependents (ledger client, etc.) as regular Go modules, not in-repo codegen. So the whole build was a plain `go build`, straight out of the pristine clone (`$SPIKE/rzp-payouts-architecture/fts`), no repo copy needed.

### GOPRIVATE fix — before/after

The task brief flagged that an earlier background `go mod download` on this repo (without `GOPRIVATE` set) failed hard with dozens of errors like:
```
go: missing github.com/razorpay/goutils/itf/slit/go.mod at revision itf/slit/v0.1.2-alpha.1
go: reading github.com/razorpay/goutils/kafka/go.mod at revision kafka/v1.2.1: unknown revision refs/tags/kafka/v1.2.1
```
**Note:** partway through this spike the coordinator relayed a message saying the root agent had since diagnosed this as a transient module-cache race (concurrent/interrupted background download), not a real GOPRIVATE/tag-resolution problem. In my own run I never actually hit the failure at all — `go mod download` succeeded cleanly on the first try:
```
export GOPRIVATE=github.com/razorpay/*
export GOFLAGS=-mod=mod
cd $SPIKE/rzp-payouts-architecture/fts
go mod download    # exit 0, ~0.2s (module cache already warm from other agents' downloads)
```
So for fts specifically I can't confirm which of the two explanations (GOPRIVATE vs cache race) was the original root cause — only that with `GOPRIVATE=github.com/razorpay/*` set, download and every subsequent build were clean.

### Binary builds

`cmd/` layout (verified, task brief's guess was right about `cmd/migration` but incomplete otherwise): `cmd/web` (server), `cmd/worker` (worker/cron commands), `cmd/migration` (goose-based migrator), `cmd/mock-server` (a local stub server for the bank/gateway mocks used in tests — not needed for this spike).

```
export GOPRIVATE=github.com/razorpay/*
cd $SPIKE/rzp-payouts-architecture/fts
OUT=$SPIKE/env2-spike/fts/bin
go build -v -o $OUT/fts         ./cmd/web/...          # exit 0, ~10s wall (cold), 135MB
go build    -o $OUT/worker      ./cmd/worker/...       # exit 0, ~3.4s wall, 133MB
go build    -o $OUT/migrate     ./cmd/migration/...    # exit 0, ~2.6s wall, 83MB
go build    -o $OUT/mock-server ./cmd/mock-server       # exit 0, ~2.2s wall, 77MB
```
Note: `./cmd/mock-server/...` (with the ellipsis) fails with `go: cannot write multiple packages to non-directory` because that subtree also contains non-main helper packages (`testdata`, `utils`, `controllers`); building the bare directory (`./cmd/mock-server`, no `...`) works since it's the only `package main` in that tree.

All four binaries produced cleanly at `$SPIKE/env2-spike/fts/bin/{fts,worker,migrate,mock-server}`, darwin/arm64, no CGO.

## Datastores

```
docker network create fts-bridge   # non-internal bridge, needed so -p publish actually works
                                    # (rzp-arena is --internal; docker silently drops -p publish
                                    # for a container whose ONLY network is an internal one —
                                    # NetworkSettings.Ports stayed null with PortBindings set.
                                    # Fix used by the other build-spike agents too: attach the
                                    # container to your own bridge network for -p, then also
                                    # `docker network connect rzp-arena <name>` as a secondary
                                    # network for the no-egress internal segment.)

DBPASS=$(openssl rand -hex 16)

docker run -d --name fts-mysql --network fts-bridge -p 127.0.0.1:23307:3306 \
  -e MYSQL_ROOT_PASSWORD="$DBPASS" -e MYSQL_DATABASE=fts \
  -e MYSQL_USER=fts -e MYSQL_PASSWORD="$DBPASS" \
  mysql:8.0 --default-authentication-plugin=mysql_native_password

docker run -d --name fts-redis --network fts-bridge -p 127.0.0.1:26380:6379 redis:7

docker network connect rzp-arena fts-mysql
docker network connect rzp-arena fts-redis
```

- MySQL 8.0.46 (`mysql:8.0` image), reachable at `127.0.0.1:23307`, DB `fts`, user `fts`.
- Redis 7 (`redis:7` image), reachable at `127.0.0.1:26380`, no auth (`requirepass` not set).
- Both containers also joined to `rzp-arena` (the pre-existing `--internal` no-egress network) as a secondary network, per the spike's shared-network convention, but the host-reachable path the fts binaries actually use is the `fts-bridge`-published ports above.

fts needs Redis for two roles that matter at boot: `mutex` (distributed lock, pinged by `/status`) and `queue` (machinery job queue/broker, used by `worker`) and `cache`. All three point at the same single Redis container/database in this spike (fine for a build spike; a real env would likely split them).

## Migrations

`cmd/migration/main.go` is a goose wrapper. Two quirks discovered by reading it rather than assuming:
- It has **no `-base_path` flag** (only `-dir`, `-env`, `-mode`) — `config.LoadConfig` is called with `constants.DefaultBasePath` (`"."`) hardcoded, so the working directory at invocation time IS the base path. Had to `cd` into `$SPIKE/env2-spike/fts` (where the arena config lives) before invoking `./bin/migrate`.
- `-dir` defaults to `internal/migrations` (relative), so it must be pointed explicitly at the real repo's migrations directory (read-only, not copied) since the binary's CWD is the scratch dir, not the repo.

```
cd $SPIKE/env2-spike/fts
source config/arena.env.sh
./bin/migrate -env=arena -dir="$SPIKE/rzp-payouts-architecture/fts/internal/migrations" status
./bin/migrate -env=arena -dir="$SPIKE/rzp-payouts-architecture/fts/internal/migrations" up
```
Outcome: all 50 Go-based goose migrations (`00001_bank_accounts_table_create.go` … `00050_rename_jpmc_channel_enum.go`) applied cleanly in well under a second (`goose: no migrations to run. current version: 50`).

`SHOW TABLES;` (46 rows — 45 app tables + `goose_db_version`):
```
account_type_mappings, attempts, attempts_unique_keys, audit_logs, bank_account_meta,
bank_accounts, batches, bene_health, beneficiary_status, cards, channel_health_events,
channel_information_status, channel_information_status_logs, channel_metrics, cron_jobs,
direct_account_routing_rules, downtime_state_change_logs, downtimes, entity_meta,
event_tags, fail_fast_status, fail_fast_status_logs, fund_accounts, goose_db_version,
key_value_store, key_value_store_logs, merchant_communication_details,
merchant_configurations, multi_account_routing_rules, non_saved_cards, otp_information,
otp_request_logs, otp_requests, otp_verification_logs, preferred_routing_weights,
remitter_accounts, schedules, source_account_mappings, source_accounts,
state_change_logs, transfer_meta, transfers, trigger_status, trigger_status_logs,
vpas, wallets
```

## Config

fts's config loader (`internal/config/config.go` + `helper.go`) always loads, in order: `config/env.default.toml` → `config/holiday.toml` → `config/env.<ENV>[-migration].toml` (the last one optional, only applied if present) → then walks the **entire merged struct** looking for `"env|VARNAME"` string placeholders and Panics if any remaining one has no matching OS env var (`internal/config/env.go: LoadEnvironmentVariables`). This matters because it means every `env|X` placeholder ANYWHERE in the ~10,300-line default config must resolve — even for config sections the app never touches at boot (e.g. per-bank webhook auth secrets) — or the process panics before the router starts.

Filenames actually present (task brief's `env.default.toml` + `env.dev.toml` guess was half right — `env.dev.toml` is gitignored and doesn't exist in the clone; the closest present template is `env.dev_local.toml`, which I used as the base pattern):
```
config/env.default.toml          # ~10,336 lines, loaded first, always
config/holiday.toml              # loaded first, always
config/env.dev_local.toml        # template used as the base for env.arena.toml (web/worker)
config/env.dev_local-migration.toml  # template used as the base for env.arena-migration.toml
```

Per hard rule 1 (never modify a file inside a repo clone) and the fact that `cmd/migration/main.go` hardcodes `basePath="."`, I did NOT create `env.arena*.toml` inside the repo's own `config/` dir. Instead I set `basePath` = `$SPIKE/env2-spike/fts` (via `-base_path` for the web/worker binaries, and via `cd` for migration, since it has no such flag) and populated `$SPIKE/env2-spike/fts/config/` with:
- `env.default.toml`, `holiday.toml` — verbatim copies from the repo (config loader requires these to exist under `<basePath>/config/`, and copying is not "modifying the clone").
- `env.arena.toml` — new file, web/worker overrides.
- `env.arena-migration.toml` — new file, migration overrides.
- `arena.env.sh` — shell script exporting the ~65 `env|VARNAME` OS env vars the merged config still references after the two override files are applied (see below).

### Key rewrites (env.arena.toml, web/worker)

| Section | Old (env.default.toml) | New (env.arena.toml) |
|---|---|---|
| `database.master/replica` HOST/PORT/NAME | `127.0.0.1:3306` (master) / `env\|DATABASE_LIVE_REPLICA_HOST` (replica) | `127.0.0.1:23307`, `NAME="fts"`, both master+replica |
| `mutex` HOST/PORT/PASSWORD | `env\|REDIS_MUTEX_HOST` / `env\|REDIS_MUTEX_PORT` | `127.0.0.1` / `26380` / `""` (no-auth redis) |
| `cache.redis` HOST/PORT/PASSWORD | `env\|REDIS_CACHE_HOST` / `env\|REDIS_CACHE_PORT` | `127.0.0.1` / `26380` / `""` |
| `queue.redis` HOST/PORT/PASSWORD | `env\|REDIS_QUEUE_HOST` / `env\|REDIS_QUEUE_PORT` | `127.0.0.1` / `26380` / `""` |
| `feature.POLL_DATA_FROM_RELAY_SERVICE` | (default true-ish/unset) | `false` (skips `relay.GetProvider().LoadBootstrapConfigurations()`, a real network call at boot) |
| `beam.BASE_URL` | `https://beam.razorpay.com/push` | `http://127.0.0.1:9/mock/beam`, `MOCK=true` |
| `spinnaker.WEBHOOK_URL` | `https://deploy-github-actions.razorpay.com/webhooks/webhook/uptime` | `http://127.0.0.1:9/mock/spinnaker` |
| `razorx.BASE_URL` | `https://razorx.int.stage.razorpay.in/v1/evaluate` | `http://127.0.0.1:9/mock/razorx/v1/evaluate` |
| `ledger.BASE_URL` | `https://ledger-test.dev.razorpay.in` | `http://127.0.0.1:9/mock/ledger` |
| `splitz.config.endpoint` | `https://splitz.dev.razorpay.in` | `http://127.0.0.1:9/mock/splitz` |
| `xas.host` | `https://x-account-statements.dev.razorpay.in` | `http://127.0.0.1:9/mock/xas` (`enabled` left `true` — confirmed `NewClient` does not dial synchronously, see below) |
| `kafka_producers.fire_transfer_status.enabled` | `true`, `brokers=["stage-kafka.razorpay.in:9090"]` | `false` (no local Kafka broker stood up for this spike — see Blockers) |
| `webhook.{refund,settlement,payout,customer_wallet,customer_payout,payout_refund,penny_testing,es_on_demand,ca_payout}.transfer_status.URL` | mostly `htTPS://test124.free.beeceptor.com/` (public sandbox, not a razorpay host but still external), one real `https://wallet.dev.razorpay.in/...` | all → `http://127.0.0.1:9/mock/webhook` |

Everything else (mozart URL *parts* like `"rbl/v1"`, `api_service`/`payouts_service` base URLs which were already `http://localhost:8080/`, `mozart.BASE_URL` already `http://localhost:8085/fts`, JPMC S3 bucket name/region) was left as inherited from `env.default.toml`/`env.dev_local.toml` patterns since none of it resolves to a real external host, or (JPMC S3) is only touched by file-integration jobs never exercised in this spike.

`Mock`/disable flags exercised: `beam.MOCK=true`, `feature.POLL_DATA_FROM_RELAY_SERVICE=false`, `kafka_producers.fire_transfer_status.enabled=false`. `sentry.DSN` was left as a dummy string (see Residual grep hits / Boot log) — sentry-go logs a parse error and disables itself, non-fatal, so no dedicated MOCK flag was needed there. Telemetry (OTEL) is NOT disableable via config (`telemetry.Initialize()` is hard-`Fatal` on error) — pointed `TELEMETRY_EXPORTERHOST=127.0.0.1` instead; the OTLP HTTP exporter constructor doesn't dial synchronously so this is safe (confirmed by reading `goutils/telemetry@.../telemetry.go`).

### `env.arena-migration.toml`

Only overrides `database.master` (HOST/PORT/NAME → arena MySQL; USERNAME/PASSWORD kept as `env|LIVE_MIGRATION_DB_USERNAME`/`env|LIVE_MIGRATION_DB_PASSWORD`, matching the `env.dev_local-migration.toml` template) plus the same JPMC S3 literal overrides (needed only because `LoadEnvironmentVariables` walks the whole struct, not because migration touches S3).

### `arena.env.sh` — the ~65 required env vars

Rather than hand-editing every `env|SOME_BANK_AUTH_SECRET` placeholder still left in the ~10,300-line default file after the overrides above (SPINNAKER_*, RAZORX_AUTH_*, LEDGER_AUTH_*, MOZART_AUTH_*, all 8 `WEBHOOK_*_AUTH_*` pairs, `USERS_*` basic-auth pairs for admin routes, JPMC crypto keys, etc. — none of which point at a *host*, they're opaque secret strings for code paths never exercised in this spike), I set all of them to a fixed `spike-dummy` string via a small exported-env script, with real values only for the things that matter: `DATABASE_USERNAME`/`DATABASE_PASSWORD`/`LIVE_MIGRATION_DB_USERNAME`/`LIVE_MIGRATION_DB_PASSWORD` (the arena MySQL creds) and `TELEMETRY_EXPORTERHOST`/`JAEGER_HOSTNAME` (`127.0.0.1`). No real secrets are echoed anywhere in this doc or the script (the MySQL password is read from a local gitignored-scratch file, `config/.mysql_root_pass`, not embedded in the script).

## Residual grep hits

```
grep -nE 'razorpay\.(com|in)|amazonaws|arn:aws|10\.[0-9]+\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.' \
  $SPIKE/env2-spike/fts/config/env.arena.toml $SPIKE/env2-spike/fts/config/env.arena-migration.toml
# (no output — zero hits in the files this spike actually wrote/controls)
```
Zero hits — confirms every host the task's regex cares about was rewritten in the override files. (The regex only checks `env.arena*.toml`, which is correct: `env.default.toml`/`holiday.toml` are untouched upstream copies and still contain the original `razorpay.in`/`.com` strings for every field the arena overrides don't touch — e.g. all the `MOZART_AUTH_KEY` etc. auth secrets and any `[integration.api.*]` gateway base config not itself a host. Those fields are never read by anything invoked in this spike.)

Manually verified (not caught by the regex, but still real external hosts per hard rule 3, so rewritten anyway): the `htTPS://test124.free.beeceptor.com/` webhook URLs (a public sandbox service, not `*.razorpay.*`/AWS/VPC, so outside the letter of the grep pattern but still non-local) — all 8 rewritten to `http://127.0.0.1:9/mock/webhook` per hard rule 3 ("every config value must point at 127.0.0.1/localhost or a local stub").

## Startup dependencies observed

| Config key | External host (pre-rewrite) | What a stub would need to return | Boot-blocking? |
|---|---|---|---|
| `database.master`/`replica` | internal MySQL | MySQL protocol handshake + our 46 tables | **Yes** — `database.Initialize()` opens a real connection at boot |
| `mutex` (redis) | internal Redis | Redis protocol | **Yes** — `/status` pings it; `mutex.Provider.Ping()` |
| `cache.redis` | internal Redis | Redis protocol | No hard fail observed at boot, but used broadly |
| `queue.redis` | internal Redis | Redis protocol (machinery broker) | **Yes for worker** — `queue.Initialize()` connects synchronously; web also calls `queue.Initialize()` but doesn't launch consumers |
| `telemetry.exporterHost` | (env var, unset in prod default) | OTLP/HTTP `/v1/traces` endpoint | No — `otlptracehttp.New()` doesn't dial synchronously; `telemetry.Initialize()` only Fatals on a *construction* error, not on unreachability |
| `xas.host` | `x-account-statements.dev.razorpay.in` | XAS REST API | No — `xas.NewClient` only builds a hystrix-wrapped HTTP client, no dial |
| `splitz.config.endpoint` | `splitz.dev.razorpay.in` | Splitz REST API | No — same pattern, lazy client construction (`splitz.NewClient`) |
| `razorx.BASE_URL` | `razorx.int.stage.razorpay.in` | Razorx REST API | No — `razorx.Initialize()` just sets a struct, no client/dial at all |
| `lumberjack.BASE_URL` | already `http://localhost:8000/v1/track` | Lumberjack log-sink API | No — `lumberjack.NewClient` spins up async worker goroutines against a buffered channel, no dial |
| `sentry.DSN` | (env var) | Sentry ingest endpoint | No — sentry-go logs `DsnParseError: invalid scheme` on a bad DSN and disables itself, non-fatal |
| `kafka_producers.fire_transfer_status` | `stage-kafka.razorpay.in:9090` | Kafka broker | **Yes if enabled** — `initializeProducer()` calls `NewProducer()` synchronously when `Enabled=true`; disabled for this spike instead of standing up a broker |
| `relay` (feature-flagged) | internal relay config service | Relay bootstrap-config API | **Yes if `feature.POLL_DATA_FROM_RELAY_SERVICE=true`** — `bootstrap.go` calls `relay.GetProvider().LoadBootstrapConfigurations()` directly (Panics on error); left `false` |
| `beam.BASE_URL` | `beam.razorpay.com` | Beam push API | No — `MOCK=true` short-circuits the client in-process |
| `webhook.*.transfer_status` (8 configs) | mostly beeceptor.com sandbox, one `wallet.dev.razorpay.in` | Webhook receiver | No — only fired on-demand by specific transfer-status-change events, never at boot |
| `jaeger.HOST` | `env\|JAEGER_HOSTNAME` | Jaeger agent (UDP, port 6831) | No — legacy Jaeger tracer is a `NoopTracer` now (OTEL replaced it per code comments); pointed at `127.0.0.1` anyway |

## Boot & health results

Router routes (from `internal/routing/router/route_list.go` + `router.go` — task brief's `/` guess for health was incomplete; there are two distinct health-ish endpoints):
- `GET /` → static welcome message, no dependency checks (`controllers.App.Get`)
- `GET status` (registered outside the versioned route groups, alongside `/metrics`) → real liveness/readiness check: pings DB and Redis mutex (`controllers.Status.Get` → `doHealthCheck`)
- `GET metrics` → Prometheus scrape endpoint

```
$ curl -sS -w "\nHTTP_STATUS:%{http_code}\n" http://127.0.0.1:8080/
{"message":"Get to FTS :)"}
HTTP_STATUS:200

$ curl -sS -w "\nHTTP_STATUS:%{http_code}\n" http://127.0.0.1:8080/status
{"app_status":"App Running","commit_id":"","container_id":"","db":1,"mutex":1}
HTTP_STATUS:200

$ curl -sS http://127.0.0.1:8080/metrics | head -5
# HELP fts_api_response_time Time taken for api to respond
# TYPE fts_api_response_time histogram
fts_api_response_time_bucket{instance_type="default",method="GET",url="/",le="0"} 0
...
```
`db:1, mutex:1` in the `/status` response confirms both the arena MySQL and arena Redis were reachable and healthy from the live process, not just at connection-open time.

Boot command:
```
cd $SPIKE/env2-spike/fts
source config/arena.env.sh
./bin/fts -env=arena -base_path="$SPIKE/env2-spike/fts" > boot.log 2>&1 &
```
Startup to listening: ~0.3s (13:53:43.938 telemetry initialized → 13:53:43.976 "INITIALIZING_ROUTER", process was already accepting connections by the time the first curl landed ~3s later — the delay was my `sleep`, not app startup).

Log scan for outbound-to-non-local: `grep -niE 'razorpay\.(com|in)|amazonaws|beeceptor|dial tcp|connection refused|no such host|timeout' boot.log | grep -v '127.0.0.1'` → **zero hits**. Only one warning-level line in the whole 69-line boot log: the expected `sentry.go:26 Failed to init sentry — [Sentry] DsnParseError: invalid scheme` (from the dummy `SENTRY_DSN=spike-dummy`), which is non-fatal by design (sentry-go swallows this and just runs with reporting disabled).

Process killed cleanly with `kill <pid>` after health checks (`graceful.RunWithErr` handles SIGTERM but a plain `kill` was sufficient for this spike).

## Worker/consumer boot notes

```
./bin/worker -env=arena -base_path="$SPIKE/env2-spike/fts" -command=default > worker.log 2>&1 &
```
`-command` defaults to `commands.Default` (`"default"`), which is a registered command (verified in `internal/commands/commands.go`) so no extra discovery needed. Worker went through the same provider bootstrap as web (DB, telemetry, queue, xas, sentry — same dummy-DSN warning, same clean log) then launched a machinery worker against the arena Redis broker:
```
INFO: Launching a worker with the following settings:
INFO: - Broker: redis://127.0.0.1:26380
INFO: - CustomQueue: fts_default
INFO: - ResultBackend: redis://127.0.0.1:26380
INFO: [*] Waiting for messages. To exit press CTRL+C
```
Registered 32 task names (`process_transfer.v2`, `verify_transfer_status.v2`, `initiate_transfer.v2`, etc. — the `.v2` suffix comes from `queue.task_version` in `env.default.toml`). Ran for ~10s idle-waiting, no outbound-to-non-local hits in the log, killed cleanly. Did not attempt to actually enqueue/process a task (out of scope — no upstream producer wired up in this spike).

## Blockers

None that prevented compile/migrate/boot/health. Two things intentionally scoped out rather than worked around:
1. **Kafka** — `kafka_producers.fire_transfer_status` was disabled (not pointed at a local broker) since the task brief's datastore list only called for MySQL + Redis for fts. If a future pass wants a fully-wired boot, standing up a local single-broker Kafka (e.g. `bitnami/kafka` or `confluentinc/cp-kafka` on the same `fts-bridge`/`rzp-arena` pattern) and re-enabling that producer with `brokers=["127.0.0.1:<port>"]` would be the next step.
2. **Mock/stub server unused** — `cmd/mock-server` was built but not run; none of fts's outbound HTTP clients (razorx/ledger/splitz/xas/beam/webhooks) are exercised at boot or by hitting `/`, `status`, `/metrics`, so a running stub wasn't needed to reach a healthy state. The `http://127.0.0.1:9/mock/...` URLs written into `env.arena.toml` are placeholders (nothing listens on port 9) — safe because every one of those clients is confirmed lazy-dial (see Startup dependencies table), but a deeper spike exercising actual transfer/beneficiary flows would need `cmd/mock-server` (or similar) actually running on those ports.

SUMMARY: compile=✔ migrate=✔ boot=✔ health=✔
