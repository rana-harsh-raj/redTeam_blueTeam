# Build Spike: ledger

Scope: `ledger` only, part of a 5-service Go buildability spike (payouts, ledger, fts, cfa, x-balances). Repo clone: `$SPIKE/rzp-payouts-architecture/ledger`. Working area: `$SPIKE/env2-spike/ledger/`.

Status: DONE — compile ✔, migrate ✔, boot ✔, health ✔. See SUMMARY at the end.

## Recon summary

- `cmd/` entrypoints found (16 total): `api`, `worker`, `worker_kafka`, `migration`, `makeshift_migration`, `scheduler`, `outbox_relay`, `idempotency_scheduler`, `idempotency_partition_manager`, `merchant_offboard`, `data_backfill`, `data_comparator`, `data_comparator_rx_da`, `worker_makeshift_txn`.
  - Server binary: `cmd/api/main.go` → `boot.InitApi`.
  - Worker binary: `cmd/worker/main.go` → `boot.InitWorker`.
  - Migration binary: `cmd/migration/main.go`, uses `github.com/pressly/goose`. NOT an "rx tool" per se — it's a goose-based CLI; dispatches to RX or PG Postgres dialect based on the `TENANT` env var (`X` = RazorpayX/RX DB, `PG` = payments-gateway DB). Also has Mirror/Makeshift modes gated by config flags.
- Config directory: `ledger/config/*.toml` (naoina-toml style, `env|VAR|default|type` interpolation). Env selection: `APP_ENV` (default `"dev"` via `boot.GetEnv()`), loaded by `config_reader.NewDefaultConfig().Load(env, &Config)`. We use a new `config/arena.toml` and run with `APP_ENV=arena`.
- Repo already ships a `config/dev_docker.toml` (mostly-localhost profile intended for local docker-compose dev) which was used as the base/reference for the rewritten arena config, alongside `config/default.toml` (the canonical full key set incl. `mock` flags).

## Build

- Go: `/opt/homebrew/bin/go` 1.26.6 darwin/arm64. `export GOPRIVATE=github.com/razorpay/*` required and was set for every step.
- `go mod download` (in `ledger/`) completed clean, ~66s, no checksum/revision errors.

### Twirp/gRPC generated code (buf)

`buf.gen.yaml` outputs to `rpc` (+ swagger to `rpc/swagger`); proto sources expected under `proto/`. Both `rpc` and `proto` ARE gitignored in this repo (`git check-ignore -v` confirms lines 33/36 of `.gitignore`), so generation happened **directly inside the repo clone** (rule 1 permits this).

Installed pinned toolchain (from `Makefile`) into `$SPIKE/env2-spike/ledger/bin` via `GOBIN=$SPIKE/env2-spike/ledger/bin go install ...`:
```
go install github.com/bufbuild/buf/cmd/buf@v1.28.1
go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.26.0
go install github.com/twitchtv/twirp/protoc-gen-twirp@v8.0.0
go install github.com/elliots/protoc-gen-twirp_swagger@v0.0.0-20200502013400-f21ef47d69e3
```

Proto sources: rather than the CI's `proto-fetch` (sparse git checkout of `github.com/razorpay/proto`), copied the modules listed in `ledger/scripts/proto_modules` from the already-cloned `$SPIKE/rzp-payouts-architecture/proto` repo into `ledger/proto/` (gitignored dest):
```
mkdir -p ledger/proto
for m in common/health/v1 common/dashboard/v1 platform/ledger platform/art/dual_write/v1 platform/nss/transaction/v1; do
  mkdir -p ledger/proto/$(dirname $m)
  cp -R $SPIKE/rzp-payouts-architecture/proto/$m ledger/proto/$m
done
```
Then, from inside `ledger/` with the installed tools on `PATH`:
```
buf generate --path ./proto
```
Ran clean, no errors. `buf mod` deps (`googleapis`, `grpc-ecosystem/grpc-gateway`) resolved from the public `buf.build` registry — not a razorpay/internal/AWS host, so not a rule-3 violation; this is buf's own public module registry used only at generate time, not at app runtime. Output: 28 generated files under `ledger/rpc/{common,ledger,art,platform,swagger}/...`, matching the import paths used in `internal/boot/handler.go` (e.g. `github.com/razorpay/ledger/rpc/common/health/v1`, `.../rpc/ledger/account/v1`, etc.) — confirmed by inspection before building.

### Migration files directory

`cmd/migration/main.go` imports `_ "github.com/razorpay/ledger/internal/database/migrations"` and defaults `-dir` to `internal/database/migrations`, but the repo only ships `internal/database/pg_migrations/` and `internal/database/rx_migrations/`. `internal/database/migrations/*.*` is gitignored (`.gitignore` line 65: "internal migration files"). Found the missing step in `.github/workflows/review_ci.yml`:
```
mkdir -p internal/database/migrations
cp -r internal/database/pg_migrations/* internal/database/migrations/
```
Replicated this exactly (PG tenant/dialect = plain Postgres, matches our single-Postgres datastore) directly inside the repo clone (gitignored target, permitted):
```
mkdir -p ledger/internal/database/migrations
cp ledger/internal/database/pg_migrations/*.go ledger/internal/database/migrations/   # 51 files
```

### Compile

All three required binaries built with `GOPRIVATE=github.com/razorpay/*` and `go build -o <external path> ./cmd/...` (external `bin/` = `$SPIKE/env2-spike/ledger/bin/`, never `ledger/bin`):
```
go build -o $SPIKE/env2-spike/ledger/bin/api      ./cmd/api/       # ~19.4s wall (cold), success
go build -o $SPIKE/env2-spike/ledger/bin/worker   ./cmd/worker/    # ~6.3s wall, success
go build -o $SPIKE/env2-spike/ledger/bin/migration ./cmd/migration/ # success (after migrations dir fix above)
```
No compile errors for any of the three. `ledger-sdk` (`$SPIKE/rzp-payouts-architecture/ledger-sdk`) is NOT imported by `ledger`'s own `go.mod`/build graph (it's a client SDK for consumers of ledger, not a ledger dependency) — not built, per instructions.

## Datastore

- No `CREATE EXTENSION` statements found anywhere under `internal/database/pg_migrations/` (grep for `CREATE EXTENSION|uuid_generate|gen_random_uuid|pgcrypto|uuid-ossp` returned nothing). No version-pinning SQL found either. Defaulted to **Postgres 16** per instructions.
- Docker network `rzp-arena` pre-exists as `--internal` (no egress). **Important finding**: an `--internal` network does NOT support host port publishing — `docker run --network rzp-arena -p 127.0.0.1:25432:5432 ...` silently drops the `-p` mapping (`NetworkSettings.Ports` came back `{"5432/tcp": null}`, connection refused from host). Fix: create the container on the default bridge network (where `-p` works normally), then `docker network connect rzp-arena` to it as a second network. Container ends up dual-homed: reachable at `127.0.0.1:25432` from the host, and reachable by other `rzp-arena` containers via its `rzp-arena` IP (`172.18.0.10` in this run) — no egress gained since `rzp-arena` itself stays internal.

Exact commands used:
```
PGPASS=$(openssl rand -hex 16)   # synthetic local password, not echoed here
docker run -d --name ledger-postgres \
  -p 127.0.0.1:25432:5432 \
  -e POSTGRES_USER=ledger \
  -e POSTGRES_PASSWORD="$PGPASS" \
  -e POSTGRES_DB=ledger \
  postgres:16
docker network connect rzp-arena ledger-postgres
```
Verified: `PGPASSWORD=$PGPASS psql -h 127.0.0.1 -p 25432 -U ledger -d ledger -c "select 1;"` → `1` row returned.

Also stood up a local Redis (required — see Config section below): `docker run -d --name ledger-redis -p 127.0.0.1:26382:6379 redis:7` (bridge network, then `docker network connect rzp-arena ledger-redis`; port 26379/26380/26381 were already taken by sibling agents' payouts/fts/x-balances redis containers). `docker exec ledger-redis redis-cli ping` → `PONG`.

## Migrations

`cmd/migration` is goose-based (`github.com/pressly/goose`). `TENANT=PG` selected (plain Postgres dialect — `common.TenantPG`; the other option, `TENANT=X`, is RazorpayX/`rx_migrations`, a different migration set for a logically-separate DB the spike doesn't need). Ran from the built `migration` binary with an explicit `-dir` (the default `internal/database/migrations` is relative to CWD, not the binary's location):
```
export WORKDIR=$SPIKE/env2-spike/ledger      # so pkg/config finds config/{default,arena}.toml
export APP_ENV=arena
export LEDGER_ARENA_PG_PASSWORD=<pgpass>
export TENANT=PG
$SPIKE/env2-spike/ledger/bin/migration -dir $SPIKE/rzp-payouts-architecture/ledger/internal/database/migrations status
$SPIKE/env2-spike/ledger/bin/migration -dir $SPIKE/rzp-payouts-architecture/ledger/internal/database/migrations up
```
Outcome: all 51 migrations applied cleanly (`OK` for each), final line `goose: no migrations to run. current version: 20260619120000`. No errors, no manual extension/version workarounds needed (confirms the earlier grep finding of zero `CREATE EXTENSION` statements).

`\dt` (public schema) → **56 relations** (51 migrations create more tables than 1:1 because several `CREATE TABLE ... PARTITION BY` migrations also create a `..._default` partition in the same migration): `accounts`, `account_details`, `journal`(+`_default`), `ledger_entries`(+`_default`), `ledger_entry_details`(+`_default`), `ledger_config`, `state_change_logs`, `idempotency`(+`_default`), `idempotency_partition_info`, `journal_rejected_events`(+`_default`), `onboarding_events`, `split_accounts`, `outbox_jobs` and 12 per-consumer `outbox_jobs_<consumer>` partitioned tables each with a `_default` partition (api, cps, settlements, growth, ups, nbs, pcp, emandate, sync, optimizer, route, affordability, cbimport, disputeservice, bts, offers, apmservice→`outbox_jobs_apm`, partnerships), plus `goose_db_version`. Full `\dt` output saved at `$SPIKE/env2-spike/ledger/logs/dt_output.txt`; full migration-up log at `$SPIKE/env2-spike/ledger/logs/migration_up.log`.

## Config

Config loader: `pkg/config` (viper-backed, `naoina`-style `env|VAR|default|type` tokens). `boot.Initialize` calls `config_reader.NewDefaultConfig().Load(env, &Config)`, which **always** loads `config/default.toml` first, then `config/<env>.toml`, from `$WORKDIR/config` (or 2 dirs up from `pkg/config/config.go` if `$WORKDIR` unset). Because `config/default.toml` is always loaded as the base layer regardless of env, and it is NOT gitignored (only `config/dev.*`/`config/test.*` are), we could not write our env file into the repo's own `config/` dir — instead set `WORKDIR=$SPIKE/env2-spike/ledger` and populated `$SPIKE/env2-spike/ledger/config/` with:
- `default.toml` — **byte-for-byte copy** of the repo's `config/default.toml` (read-only base layer; the loader requires a file literally named `default.toml` in the search path, so this had to be present, but it was not edited).
- `arena.toml` — a full rewritten copy of `default.toml` (per the instruction to copy-then-rewrite, not the terser override style the repo's own `dev_docker.toml` uses), with every external host pointed at localhost or a mock flipped on.

Env vars used at run time: `WORKDIR`, `APP_ENV=arena`, `LEDGER_ARENA_PG_PASSWORD=<synthetic pg password>`.

### Keys rewritten (old → new)

| Key | Old (default.toml) | New (arena.toml) | Why |
|---|---|---|---|
| `app.appEnv` | `default` | `arena` | env identity |
| `app.hostname` | `ledger.razorpay.com` | `ledger.arena.local` | identity string only (not dialed), but kept off real domains |
| `db.rx.writer.ConnectionConfig.{url,port,password}` | `127.0.0.1:5432` / `ledger` | `127.0.0.1:25432` / env-sourced synthetic password | point at local Postgres container |
| `db.pg.writer.ConnectionConfig.{url,port,password}` | `127.0.0.1:5432` / `ledger` | `127.0.0.1:25432` / env-sourced synthetic password | same, PG tenant |
| `tracing.host` | `localhost` (already `enabled=false`) | `127.0.0.1` | belt-and-braces; was already inert |
| `telemetry.exporterHost` default | `localhost` | `127.0.0.1` | OTLP exporter is lazy (never dials at boot per code comment in `boot.go`) but kept explicit |
| `authz.mock` | `false` | **`true`** | `false` would call `authz.NewEnforcer` against a real Consul cluster (`env="stage"`). Mocking short-circuits to `EnforcerMock{}`, no dial. **Also flips Passport to mock** — `boot.go` calls `passportProvider.InitPassportHandler(Config.Passport, Config.AuthZ.Mock)`, sharing this one flag. |
| `passport.host` | `https://edge-base.dev.razorpay.in` | `http://127.0.0.1:1` (placeholder, unused) | dead value now that authz.mock=true forces `HandlerPassportMock{}` |
| `apiService.baseUrl` | `https://api.razorpay.com` | `http://127.0.0.1:1` (placeholder, unused) | already `mock=true` in default.toml; rewritten defensively |
| `redis.{host,port}` / `elasticache.{host,port}` | `localhost:6379` | `127.0.0.1:26382` | **required, not optional** — `boot.Initialize` calls `redis.NewV8ElasticacheUniversalClient` which does a synchronous `Ping()` and fails boot on error; stood up `ledger-redis` container for this |
| `queue.driver` | `sqs` | **`sqs_local`** | `sqs` (plain dialect) ignores the `Endpoint` field and always builds a client against real AWS regardless of `Prefix`/`Region` (confirmed by reading `goutils/worker/v3/queue/broker/sqs`: only `NewWithEndpoint`, used by the `sqs_local` dialect, calls `.WithEndpoint()`). The `api` binary never calls `Worker.Start()` (only `worker` does, via `registerWorkerHandler`), so this was inert for api boot either way, but `sqs_local` was used throughout for correctness before booting `worker` too. |
| `queue.sqs.{prefix,endpoint,useAnonymousCredentials}` | `https://sqs.ap-south-1.amazonaws.com/...` | `http://127.0.0.1:1` + `useAnonymousCredentials=true` | remove AWS host; anonymous creds skip the AWS credential-provider chain (env/shared-config/EC2-IMDS lookups) entirely |
| `wda.defaultHttpClientUrl` | `https://wda-service.concierge.stage.razorpay.in` | `http://127.0.0.1:1` (placeholder, unused) | already `mock=true` in default.toml |
| `splitz.{endpoint,mock}` | `https://splitz.dev.razorpay.in` / `mock=false` | `http://127.0.0.1:1` / **`mock=true`** | default.toml ships this integration live (`mock=false`); `InitSplitz` only builds an http client at boot (no dial), but a real `GetVariant` call at runtime would have hit the real endpoint, so mocked it |
| `snsOutboxer.../endpoint`, `pubSub.../endpoint` | `http://localhost:4100` | `http://127.0.0.1:4100` | already local; no SNS emulator stood up (not needed — both paths are inert, see below) |
| `events.kafka.brokers`, `queueKafka.kafka.brokers`, `queueMakeshiftTxn.kafka.brokers` | `localhost:*` | `127.0.0.1:*` | already local; `events.enabled=false` so kafka producer is never constructed; the two `queueKafka`/`queueMakeshiftTxn` configs only matter to `cmd/worker_kafka`/`cmd/worker_makeshift_txn`, not booted here |

### Mock flags (final state)

| Flag | Value | Dials real host if `false`/unset? |
|---|---|---|
| `authz.mock` | `true` (flipped) | yes — Consul |
| `passport` (via `authz.mock`) | mocked | yes — `edge-base.dev.razorpay.in` |
| `apiService.mock` | `true` (already) | yes — `api.razorpay.com` |
| `slack.mock` | `true` (already) | n/a (webhookUrl empty anyway) |
| `pagerDuty.mock` | `true` (already) | n/a |
| `wda.mock` | `true` (already) | no — `InitWda` never dials at boot regardless, only builds an http client |
| `splitz.mock` | `true` (flipped) | no dial at boot either way, but real `GetVariant` calls would hit the endpoint |
| `events.enabled` | `false` (already) | yes — kafka producer construction skipped entirely |

Telemetry has no boot-time enable/disable — `initTelemetry` always runs, but the OTLP HTTP exporter is documented in `boot.go` as lazy ("does not dial the collector at startup"), so left pointed at localhost with no separate kill-switch needed.

### Residual grep hits

```
grep -nE 'razorpay\.(com|in|vpc)|amazonaws|arn:aws|10\.[0-9]+\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.' $SPIKE/env2-spike/ledger/config/arena.toml
```
5 hits, all judged safe:
1. `outboxJob.x_journal_created_sns = "arn:aws:sns:us-east-1:000000000000:journal-created"` — identifier string only (test/local placeholder account `000000000000`), not a network target; the actual network target for this same feature is `snsOutboxer.pubSub.journal_created.sns.endpoint = http://127.0.0.1:4100`. Also unreachable at boot: `InitOutboxer` (the only consumer of this whole `[outbox]`/`[outboxJob]`/`[snsOutboxer]` tree) is gated on `Config.App.AppMode == "default"` in `boot.go`, and `arena.toml` sets `appMode = "test"`.
2. `snsOutboxer.pubSub.journal_created.sns.topicName = "arn:aws:sns:...:journal-created"` — same, identifier only, same inert gate.
3. `pubSub.journal_created.sns.topicArn = "arn:aws:sns:us-east-1:000000000000"` — same pattern; `pubsub.New()` (called unconditionally in `InitApi`/`InitWorker`) only builds a lazy per-topic publisher map and does not dial at construction — confirmed by reading `pkg/pubsub/manager.go`.
4. Comment on `wda.defaultHttpClientUrl` documenting the real hostname it replaced (`https://wda-service.concierge.stage.razorpay.in`) — comment text, not a config value; `wda.mock=true`.
5. Comment on `splitz.endpoint` documenting the real hostname it replaced (`https://splitz.dev.razorpay.in`) — comment text, not a config value; `splitz.mock=true`.

No live connection-string/host hit remains. Proceeding to boot.

### Note: `goutils/dcs` trap (raised by sibling legs) does not apply to ledger

Two sibling legs (cfa, x-balances) reported `github.com/razorpay/goutils/dcs`'s `dcs.New()` making a real outbound login POST at construction, ignoring `Mock=true`. Checked: `grep -rn "goutils/dcs" ledger/go.mod ledger/go.sum` and `grep -rln 'goutils/dcs"' ledger --include='*.go'` both return nothing — **ledger does not import `goutils/dcs` at all**, so this trap is not applicable here.

### Note: `queue` driver trap — plain `"sqs"` dialect ignores `Endpoint` and always targets real AWS

A sibling leg separately flagged that the goutils SQS queue driver ignores a configured `Endpoint` under the plain `"sqs"` dialect. Confirmed by reading `goutils/worker/v3/queue/broker/sqs/queue.go`: `sqs.New()` (dialect `"sqs"`) never calls `.WithEndpoint()`, so it always targets real AWS regardless of `Prefix`/`Endpoint` config; only `sqs.NewWithEndpoint()` (dialect `"sqs_local"`) calls `awsConfig.WithEndpoint(conf.Endpoint)`. Switched `queue.driver` to `sqs_local` in `arena.toml` for exactly this reason (see Config section above) — this was caught and fixed *before* booting anything, not after an incident.

## Boot & health results

Ran with `HTTP_PROXY=http://127.0.0.1:1`, `HTTPS_PROXY=http://127.0.0.1:1`, `NO_PROXY=127.0.0.1,localhost,127.0.0.0/8,::1` exported as a defense-in-depth net (per a sibling leg's finding that the sandbox does NOT block real internet egress from a host-run Go binary — `rzp-arena --internal` only isolates docker containers from each other) — Go's default `http.Transport` honors these, so anything that missed our config rewrite would fail closed instead of reaching a real host.

```
export WORKDIR=$SPIKE/env2-spike/ledger
export APP_ENV=arena
export LEDGER_ARENA_PG_PASSWORD=<pgpass>
export HTTP_PROXY=http://127.0.0.1:1
export HTTPS_PROXY=http://127.0.0.1:1
export NO_PROXY=127.0.0.1,localhost,127.0.0.0/8,::1
$SPIKE/env2-spike/ledger/bin/api > $SPIKE/env2-spike/ledger/boot.log 2>&1 &
```
- Listening within ~6-8s: `lsof -iTCP:8080 -sTCP:LISTEN` → `api ... TCP localhost:http-alt (LISTEN)`.
- `boot.log` was **empty** on every run (0 lines, no panics, no errors) — the goutils zap logger apparently doesn't emit INFO-level boot lines to stdout in this config; absence of panic/error output plus a listening socket plus a passing health check was taken as the success signal.
- `lsof -p <api-pid> -iTCP` while running showed only local connections: `localhost:*->localhost:26382` (redis) and two `localhost:*->localhost:25432` (postgres, writer handles for both RX and PG tenant configs pointing at the same instance) — **no connections to any other host**, confirmed with the proxy blackhole active (if anything had tried to reach a real host via HTTP(S), it would have failed against `127.0.0.1:1`; a raw TCP dial bypassing the proxy would have shown up in `lsof` as a non-local destination, and none did).
- Health check:
```
$ curl -sS -i -X POST http://127.0.0.1:8080/twirp/rzp.common.health.v1.HealthCheckAPI/Check -H "Content-Type: application/json" -d '{}'
HTTP/1.1 200 OK
Content-Length: 43
Content-Type: application/json

{"serving_status":"SERVING_STATUS_SERVING"}
```
  (Plain `GET /health` → `404 page not found`, as expected — this service only exposes the Twirp health RPC at `/twirp/rzp.common.health.v1.HealthCheckAPI/Check`, registered in `internal/boot/handler.go:RegisterDefaultHandler`.)
- Repeated the boot → health → kill cycle twice (once without the proxy blackhole, once with) to cross-check; identical clean result both times.
- Process killed with `kill <pid>` (graceful SIGTERM, triggers `shutDown()` in `internal/boot/server.go`); confirmed no longer listening on 8080.

## Worker boot notes

`cmd/worker` → `boot.InitWorker`. Booting it concurrently with `api` fails immediately (`listen tcp4 :8081: bind: address already in use` — both binaries share the same `metric.port=":8081"` from config, not configurable per-binary in this env file), so stopped `api` first, then booted `worker` alone for ~14s:

```
2026-09-04T18:06:25 INFO  "starting worker consumer for queue account_create"
2026-09-04T18:06:25 ERROR "failed to fetch message from queue: RequestError: send request failed
  caused by: Post \"http://127.0.0.1:1/\": dial tcp 127.0.0.1:1: connect: connection refused"
2026-09-04T18:06:25 ERROR "failed to construct job RequestError: send request failed ..." (same cause)
```
Findings:
- Worker boots past `boot.Initialize` (DB/redis/mutex/authz-mock/etc. all fine, same as `api`) and immediately calls `Worker.Start()` → begins polling the configured SQS queue at startup (unlike `api`, which only constructs the queue client but never starts polling — see Config section note on `registerWorkerHandler`).
- Confirms our `sqs_local` driver fix works correctly: the failed dial target is our own local placeholder `http://127.0.0.1:1` (connection refused — nothing listens there), **not** a real AWS endpoint. Had we left `driver = "sqs"`, this step would have made a real outbound call to AWS SQS.
- The retry loop has **no backoff on this error path** — it hammered the failed local endpoint continuously, producing ~860k log lines in ~14s (`worker_boot.log` was truncated to head/tail 30-40 lines for the findings; full tight-loop behavior noted here instead of preserved verbatim). This is worth flagging to the ledger team independent of the spike: a genuinely-unreachable/misconfigured queue backend in a real environment would produce a very large, tight-error-log volume rather than backing off.
- Killed with `kill -9` (had to, given the tight loop) after ~14s. To actually exercise a working local worker, one would need a functioning `sqs_local`-compatible emulator (e.g. elasticmq) listening at the configured endpoint with the expected queue provisioned — out of scope for this spike (task only asked to observe startup demands).
- Re-booted `api` alone afterward and re-confirmed clean health (see Boot & health results) before final shutdown.

## Blockers

None outstanding for `api`. Everything in scope (compile, migrate, boot, health) succeeded. Two things worth flagging upstream, not blockers for this spike:
1. `cmd/migration`'s default `-dir` (`internal/database/migrations`) points at a directory that doesn't exist in a fresh checkout — it's populated only by a CI step (`.github/workflows/review_ci.yml`) copying `pg_migrations/*` into it, undocumented anywhere else (no README/Makefile target for local dev). Reproduced that CI step manually.
2. `queue.driver="sqs"` (the config value ledger ships with in `default.toml`) silently ignores any `Endpoint` override and always targets real AWS SQS — there is no config-only way to fully localize it short of switching to the `sqs_local` dialect (undocumented alternative discovered by reading vendor source, not any ledger doc).

## SUMMARY

`compile=✔ migrate=✔ boot=✔ health=✔`

All four steps succeeded cleanly for the `ledger` service: 3 binaries built (`api`, `worker`, `migration`) after generating Twirp code (buf, gitignored `rpc`/`proto` dirs) and replicating a CI-only step to materialize `internal/database/migrations`; Postgres 16 migrated cleanly (51/51 migrations, 56 tables, no extensions needed); `api` booted against a fully-localized `arena.toml` (Postgres + Redis containers, every other integration mocked or pointed at an unreachable local placeholder) and passed its Twirp health check (`SERVING_STATUS_SERVING`), confirmed with zero non-local network activity even under an HTTP(S) proxy blackhole; `worker` was booted briefly and confirmed to reach `Worker.Start()`/queue-poll without ever touching a real host, thanks to switching off the plain `sqs` dialect (which does dial real AWS regardless of config) in favor of `sqs_local`.


