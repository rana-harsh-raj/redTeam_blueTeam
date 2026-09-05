# Build Spike: cfa

Repo: `$SPIKE/rzp-payouts-architecture/cfa`
Working area: `$SPIKE/env2-spike/cfa/`

## Build

Go: `/opt/homebrew/bin/go` 1.26.6 darwin/arm64. `GOPRIVATE=github.com/razorpay/*` exported (no other GOFLAGS/GOSUMDB overrides needed).

```
export GOPRIVATE=github.com/razorpay/*
cd $SPIKE/rzp-payouts-architecture/cfa
go mod download          # exit 0, ~0.9s (module cache already warm)
```

`go mod download` succeeded cleanly on first try (no checksum-mismatch or missing-revision errors seen for ifsc/goutils/relay submodules that affected sibling repos).

Binaries built directly from the pristine clone with `-o <external path>` — no source files in the clone were modified.

```
go build -o $SPIKE/env2-spike/cfa/bin/cfa-server    ./cmd/server/...      # exit 0, ~19s
go build -o $SPIKE/env2-spike/cfa/bin/cfa-worker    ./cmd/worker/...      # exit 0, ~10s
go build -o $SPIKE/env2-spike/cfa/bin/cfa-migration ./cmd/migration/...  # exit 0, ~9s
```

Binaries: `$SPIKE/env2-spike/cfa/bin/{cfa-server,cfa-worker,cfa-migration}` (~100-140MB each, darwin/arm64).

### Generated code

None needed. `cfa/cmd/` has three entrypoints: `server`, `worker`, `migration`. The `rpc/` directory (gRPC/protobuf generated Go code for `common/health`, `tokens`, `x-cfa`) is **checked into the repo** (`git ls-files rpc/...` lists the `.pb.go` files, not gitignored) — buf/protoc generation was not required to build. The top-level `.gitignore` has an entry `proto` (singular) with the comment "This will be sourced from the proto repo when needed" — that directory doesn't exist in this clone and wasn't needed since `rpc/` is pre-generated and committed.

## cmd/ layout (verified, not assumed)

- `cmd/server/main.go` — main API server: boots grpc+http+metrics server (`internal/server`), storage (Mongo `Store` + MySQL `APIStore`), event system (SQS), Elasticsearch client, BinService/VaultService/TokenService/ApiService SDK clients, DCS feature-flag client, and an in-process worker instance (job enqueue only, web pod pattern).
- `cmd/worker/main.go` — job worker: boots the same `internal/server` (health+metrics only, no grpc handlers registered) plus a `gworker` manager that actually runs job handlers (`contact_lazy_load`, `fund_account_lazy_load`).
- `cmd/migration/main.go` — standalone Mongo migration CLI (`up`, `up-to VERSION`, `down`, `down-all`, `status`), talks **only** to `config.Store.MongoDB` (no MySQL/APIStore dependency). Migrations are Go functions registered via `internal/database/migrations.RegisterMigrations()`, tracked in a `migrations` collection (`{id, version, name, applied_at}`), not raw SQL files.

## Health endpoints (verified, not assumed)

`/live` and `/ready` are real, on the **HTTP port** (`:8081` by default, from `Server.ServerAddresses.Http`), served via grpc-gateway proxying to a gRPC `HealthService` — traced through:
- `internal/server/grpc_handler.go` registers `healthrpc.RegisterHealthServiceServer(..., health.NewServer(opt.HealthService))`
- `internal/server/http_handler.go` registers `healthrpc.RegisterHealthServiceHandlerFromEndpoint(...)` on the gateway mux
- `rpc/common/health/v2/health_check_api.swagger.json` confirms the HTTP paths are literally `/live` and `/ready`
- `internal/health/service/service.go` (`NewService`) wires only two checks: `max-go-routines`, `max-gc-pause` (always), plus a `mongodb` ping check **only if** the storage is `*mongodb.Repo` (it is, here). **APIStore/MySQL is never part of the health check**, even though it's mandatory at boot (see Datastore/Blockers below).

## Datastore

CFA needs **two** datastores to boot the server/worker binaries, not just Mongo — discovered via recon, not assumed:
1. **MongoDB** — `config.Store` (`pkg/storage/mongodb`), the only thing `cmd/migration` touches.
2. **MySQL** — `config.APIStore` (`pkg/storage/sql`, gorm `mysql` driver). `storage.New` unconditionally switches on `Config.Choice` (`"mongodb"` or `"sql"`); there is no no-op/mock choice. `cmd/server/main.go` and `cmd/worker/main.go` both call `storage.New(ctx, config.APIStore)` unconditionally and `log.Fatalf` on error — so **without a real, reachable MySQL, cfa-server/cfa-worker cannot boot at all**, regardless of health-check semantics. The task brief only anticipated Mongo(+Redis); MySQL was out of the brief's enumerated datastore list, but is a hard boot-time requirement discovered in `internal/server` wiring, so a MySQL container was added (127.0.0.1-only, on `rzp-arena`) to get past process boot — flagged here for the coordinator rather than silently expanding scope.

No Redis: `grep -rn redis internal/ pkg/ cmd/ config/` in cfa found **zero** direct usage — `github.com/go-redis/redis` only appears as an indirect (transitive) `go.mod` dependency. No Redis container was started.

Chose versions by finding cfa's own SLIT (their local integration-test) reference at `cfa/docker-compose.slit.yml`, which pins `mongo:7.0` and `mysql:8.0` — consistent with `go.mongodb.org/mongo-driver v1.17.3` and `gorm.io/driver/mysql v1.6.0` in `go.mod`. Followed the same image versions and auth-plugin/charset flags.

```
# both created on the default bridge network first (so -p publishing works),
# then attached to rzp-arena — --network rzp-arena --internal at `docker run`
# time silently drops all -p host-port publishing (verified: NetworkSettings.Ports
# came back {} when created directly on the internal network), so containers must
# be created on bridge (or any non-internal network) and connected to rzp-arena
# afterward with `docker network connect`.

docker run -d --name cfa-mongo \
  -p 127.0.0.1:27117:27017 \
  -e MONGO_INITDB_ROOT_USERNAME=root \
  -e MONGO_INITDB_ROOT_PASSWORD=<openssl rand -hex 16> \
  -e MONGO_INITDB_DATABASE=cfa_arena \
  mongo:7.0
docker network connect rzp-arena cfa-mongo

docker run -d --name cfa-mysql \
  -p 127.0.0.1:23308:3306 \
  -e MYSQL_ROOT_PASSWORD=<openssl rand -hex 16> \
  -e MYSQL_DATABASE=api \
  -e MYSQL_USER=cfa \
  -e MYSQL_PASSWORD=<openssl rand -hex 16> \
  mysql:8.0 \
  --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci --default-authentication-plugin=mysql_native_password
docker network connect rzp-arena cfa-mysql
```

Port notes: 27017/3306 were the "natural" host ports per the brief/slit-compose, but 3306's usual local alias (23306) was already claimed by a sibling agent's `payouts-mysql` container — used **23308** for `cfa-mysql` instead (23307 was also taken, by `fts-mysql`). Mongo's 27117 (brief-specified, to avoid the 27017 default) was free.

An application-level Mongo user was created (root user is never referenced by the app config):
```
docker exec cfa-mongo mongosh --quiet -u root -p <root_pass> --authenticationDatabase admin --eval "
db.getSiblingDB('admin').createUser({
  user: 'cfa', pwd: '<app_pass>',
  roles: [ { role: 'readWrite', db: 'cfa_arena' }, { role: 'dbAdmin', db: 'cfa_arena' } ]
})"
```
(MySQL needed no equivalent step — `MYSQL_USER=cfa`/`MYSQL_PASSWORD` env vars create the app user directly with access to `MYSQL_DATABASE=api`.)

Both containers confirmed healthy via `mongosh --eval "db.adminCommand('ping').ok"` and `mysqladmin ping` before building the config.

## Migrations

```
export WORKDIR=$SPIKE/env2-spike/cfa
export APP_ENV=arena
$SPIKE/env2-spike/cfa/bin/cfa-migration status   # all 4 Pending
$SPIKE/env2-spike/cfa/bin/cfa-migration -v up    # applies all 4
$SPIKE/env2-spike/cfa/bin/cfa-migration status   # all 4 Applied
```

Outcome: **all 4 migrations applied successfully**, no errors. These are Go-coded Mongo "migrations" — collection creation with JSON-schema validators, index creation, and (for 2 of them) a single sample-document seed — not SQL DDL:

| Version | Name | What it does |
|---|---|---|
| 1715709457 | create_contacts_collection | creates `contacts` w/ schema validator + 4 indexes (`merchant_id`, `hash`, composite `reference_id+merchant_id`, `batch_id`) + inserts 1 sample doc |
| 1715709459 | create_fund_accounts_collection | creates `fund_accounts` w/ schema validator + 8 indexes (`bank_account.id`, `vpa.id`, `wallet.id`, `card.id`, `contact_id`, `hash`, `batch_id`, `merchant_id`) + inserts 1 sample doc |
| 1715709458 | create_hash_lookup_collection | creates `hash_lookup` + inserts 2 sample entries |
| 1739361599 | identify_duplicates | read-only scan for duplicate `id`/`hash` values across the 3 collections above (no duplicates found — expected, DB was freshly seeded); does not itself create anything, just prints a report |

`cfa-migration` also self-creates a 5th collection, `migrations` (the tracking table, `{id, version, name, applied_at}`), on first `up`.

Verified via `docker exec cfa-mongo mongosh -u cfa -p ... cfa_arena --eval "db.getCollectionNames()"`:
```
['contacts', 'hash_lookup', 'fund_accounts', 'migrations']
```
Counts: `contacts`=1, `hash_lookup`=2, `fund_accounts`=1, `migrations`=4 (one doc per applied migration, matching `status` output).

## Config

CFA's config loader (`pkg/configloader`) is **not** "one env file wins" — `Loader.Load(env, cfg)` always parses `config/default.toml` into the struct **first**, then parses `config/<APP_ENV>.toml` into the **same struct instance** (viper `Unmarshal` only overwrites fields present in the second file's keys; anything default.toml set and the env file doesn't repeat survives). So a clean arena config requires copying `default.toml` unmodified (verbatim, `diff` confirmed identical) into the working area alongside a new `arena.toml` override layer — **not** just writing one override file — otherwise `default.toml`'s two hardcoded `razorpay.in` hosts (`App.HostName`, `Wda.default_http_client_url`) would silently take effect underneath it.

Files, both under `$SPIKE/env2-spike/cfa/config/` (config dir resolved via `WORKDIR` env var, per `pkg/configloader.NewDefaultOptions`):
- `default.toml` — **byte-identical copy** of `cfa/config/default.toml` (never edited).
- `arena.toml` — new override layer, based on the structure of `cfa/config/slit.toml` (cfa's own closest-to-local, all-`Mock=true` env), then every external host rewritten.

Launch env vars: `WORKDIR=$SPIKE/env2-spike/cfa`, `APP_ENV=arena` (selects `arena.toml`; `APP_MODE` left **unset** — if set to `test`, the loader looks for `arena_test.toml` instead, which doesn't exist). `QUEUE_DRIVER=sqs_local` is added only for the worker launch (see Startup dependencies / Blockers).

Key rewrites (old → new), verified against both `default.toml` and `slit.toml` as the two possible "old" sources:

| Key | Old (default.toml / slit.toml) | New (arena.toml) |
|---|---|---|
| `App.HostName` | `https://cfa.dev.razorpay.in` (both files) | `http://127.0.0.1:8081` |
| `Store.MongoDB.Endpoint/Port/Username/Password/DatabaseName` | `env|STORE_MONGO_*` (default) / `localhost:27017 cfa/password123 e2e-test` (dev.toml, unused base) | `127.0.0.1:27117`, `cfa`/`<mongo app pass>`, `cfa_arena` |
| `APIStore.Sql.Url/Port/Username/Password/Name` | *(absent from default.toml — no APIStore section at all)*; slit: `localhost:3306 root/root api` | `127.0.0.1:23308`, `cfa`/`<mysql app pass>`, `api` |
| `ElasticSearch.Host` / `Enabled` / `Mock` | slit: `localhost:9200`, `Enabled=false`, `Mock="true"` (string, not bool) | `127.0.0.1:9200`, `Enabled=false`, `Mock=true` (proper bool) |
| `BinService.BaseURL` | slit: `https://bin-service.dev.razorpay.in` | `http://127.0.0.1:1` (unreachable stub; client already short-circuits on `Mock=true` before any HTTP call — see Startup dependencies) |
| `VaultService.BaseURL` | slit: `https://vault-web.dev.razorpay.in/v1` | `http://127.0.0.1:1` |
| `TokenService.BaseURL` | slit: `https://tokens-test.dev.razorpay.in` | `http://127.0.0.1:1` |
| `ApiService.BaseURL` | slit: `https://api-web.dev.razorpay.in` | `http://127.0.0.1:1` |
| `Wda.default_http_client_url` | default: `https://wda-service.concierge.stage.razorpay.in`; slit: same | `http://127.0.0.1:1`, `mock=true` |
| `Server.Auth.Dev[0].Username` | slit: `test.dev@razorpay.com` (a login string, not a network host, but matched the grep pattern) | `local.dev` |
| `Queue.Sqs.Region/Endpoint/Prefix` | `env|QUEUE_SQS_REGION` / `env|QUEUE_SQS_ENDPOINT` / `env|QUEUE_SQS_PREFIX` (both files — inert placeholders unless the literal env var is exported, which it wasn't) | `us-east-1` / `http://127.0.0.1:1` / `cfa-arena`, `UseAnonymousCredentials=true` (see DCS/SQS incidents below — this override is necessary but **not sufficient** for the worker binary) |
| `Dcs.env` | `dev` (both files, `Mock=true` in default/slit but see incident) | `arena-disabled` (deliberately invalid — see incident) |
| `Telemetry.exporterHost` | `localhost` (default, already safe) | `127.0.0.1` (kept explicit) |

Mock flags left/set `true`: `Dcs.mock`, `ElasticSearch.Mock`, `BinService.Mock`, `VaultService.Mock`, `TokenService.Mock`, `ApiService.Mock`, `Wda.mock`. `ElasticSearch.Enabled=false` in addition (so its health-check goroutine never starts — see `pkg/elasticsearch/elasticsearch.go`: `Enabled && !Mock` gates real init, `Start()` returns immediately if `!Enabled`).

## Residual grep hits — and a real incident, not just a static grep

Static grep against the final config file is clean:
```
$ grep -nE 'razorpay\.(com|in)|amazonaws|arn:aws|10\.[0-9]+\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.' $SPIKE/env2-spike/cfa/config/arena.toml
10:# repeats them with the same razorpay.in values - all instances are rewritten below.
174:# at construction time -> a real HTTPS POST to https://dcs-live.dev.razorpay.in/
```
Both hits are **comment lines** documenting the incident below (kept deliberately, for auditability) — zero hits in any live TOML value. `default.toml` (the pristine copy, loaded first) does still contain its original 2 hits (`App.HostName`, `Wda.default_http_client_url`) — both are explicitly overridden by `arena.toml`'s later load pass (verified functionally, see Config section above and Boot log below, not just by inspection).

**This static/config-level check was not sufficient on its own — two live outbound calls to real hosts were actually made and caught, during boot testing, from config values that *looked* safe (`Mock=true`) on paper:**

1. **DCS login call** (`internal/dcsservice`, wraps `github.com/razorpay/goutils/dcs`). With `Dcs.env="dev"`, `Dcs.mock=true`, the **first boot attempt made a real `POST https://dcs-live.dev.razorpay.in/v1/auth/login`** and got back `HTTP 403`. Root cause: `goutils/dcs@v1.7.1`'s `dcs.New()` unconditionally calls `c.creds.Get()` (a real login HTTP call) during client construction — the `Mock` flag passed via `config.WithMock()` does not gate this step in this SDK version; it's a client-construction-time login independent of mock mode. **The server process was killed within ~1s of that log line appearing.** Fix: `Dcs.env` set to `"arena-disabled"` (not a value goutils/dcs's `config.GetEnv()` switch recognizes) so cfa's own `internal/dcsservice/client.go:newDCSServer()` errors out at `config.GetEnv(cfg.Env)` **before** ever calling `dcs.New()` — confirmed by re-running: log now shows only `"dcs invalid environment"` / `"invalid env provided"`, immediately, no network attempt, and cfa's own caller treats this as non-fatal (server continues booting, DCS-driven flags just resolve false).
2. **Worker SQS polling** (`cmd/worker` only, via `pkg/worker.InitWorker` → `goutils/worker/v2/queue`). With `Queue.Driver="sqs"`, `Queue.Sqs.Endpoint="http://127.0.0.1:1"`: the **worker binary's active poll loop made repeated real `ReceiveMessage` calls to `https://sqs.us-east-1.amazonaws.com/`** (got `AccessDenied`/403 each time, ~every 750ms) — because the plain `"sqs"` driver in `goutils/worker/v2/queue/broker/sqs` **ignores the `Endpoint` config field entirely** and always builds a real regional AWS endpoint from `Region` alone; only the separate `"sqs_local"` driver honors `Endpoint` (calls `awsConfig.WithEndpoint(...)`). **The worker process was killed within ~12s** (it was let run briefly per the boot-probe instructions, then killed once the pattern was clear). Fix: relaunch the worker with env override `QUEUE_DRIVER=sqs_local` — re-verified clean, log now shows only `dial tcp 127.0.0.1:1: connect: connection refused` (fully local). Note `Queue.Driver` **cannot** be set to `"sqs_local"` in the config file itself for `cfa-server`, because `internal/eventsystem/service.go`'s `NewServiceWithSQS()` hardcodes a check that rejects any driver string other than the literal `"sqs"` (confirmed: doing so makes `cfa-server` fail fast at boot with `"unsupported queue driver: sqs_local"`, fatal). Since `cfa-server` never actively calls SQS at boot or during health checks (`mgr.Start()`/polling is only invoked in `cmd/worker`, not `cmd/server` — confirmed by a clean `cfa-server` boot log with `Driver="sqs"`), the config file keeps `Driver="sqs"` for the server and the worker is launched with the env-var override instead.

Both incidents are recorded here in full because they are exactly the class of thing this spike exists to surface: **a `Mock=true` flag or a locally-scoped hostname/endpoint field in TOML does not guarantee no real network call — some SDKs perform privileged/credentialed setup at client-construction time regardless of a mock flag, and some queue drivers silently ignore an `Endpoint` override.** No merchant/production data was exchanged in either case (login got 403, SQS got AccessDenied/403) and both were caught and killed within seconds, but both were real, unauthorized-per-brief calls to real Razorpay/AWS infrastructure from this sandbox — flagging prominently for the coordinator, not burying it in a footnote.

## Startup dependencies observed

| Config key | External host (before rewrite) | What a stub would need to return | Status here |
|---|---|---|---|
| `Store.MongoDB.*` | (internal DocumentDB in prod) | Mongo wire protocol, auth | Real local `mongo:7.0` container — fully functional |
| `APIStore.Sql.*` | (internal Aurora/RDS MySQL in prod) | MySQL wire protocol, `api` schema | Real local `mysql:8.0` container — connects; app never queries a real table during boot/health (no schema/tables created — out of scope, migrations only touch Mongo) |
| `Dcs.*` (via goutils/dcs) | `https://dcs-live.dev.razorpay.in` | `POST /v1/auth/login` → token JSON | **Not stubbed** — disabled instead (invalid `env`), see incident #1. cfa treats absence as non-fatal (flags resolve `false`) |
| `Queue.Sqs.*` (event system, server only) | `sqs.<region>.amazonaws.com` | SQS `SendMessage`/queue-URL semantics | Points at `127.0.0.1:1` (nothing listens); safe because server never actively sends at boot |
| `Queue.Sqs.*` (worker poll loop) | `sqs.<region>.amazonaws.com` | SQS `ReceiveMessage` long-poll | **Needed `Driver=sqs_local` + `Endpoint`**, see incident #2 — even then, no real listener, so worker just loops on "connection refused" (would need an actual SQS-compatible stub, e.g. ElasticMQ/localstack, to go further) |
| `BinService/VaultService/TokenService/ApiService.BaseURL` | `*.dev.razorpay.in` | REST APIs (IIN lookup, card vault, tokens, API monolith) | `Mock=true` — cfa's own client code returns a mock client before any HTTP client is even constructed (`internal/{binservice,tokenservice,apiservice}/client.go`: `if config.Mock { return New*MockClient() }`) — verified no BaseURL is ever dialed for these three. **Exception:** `internal/vaultservice/client.go`'s `NewCardVaultClient` does **not** check `Mock` at construction — it always builds a real `http.Client` pointed at `BaseURL`; it's only safe here because `BaseURL` is rewritten to an unreachable local stub and no code path exercises `GetCardNumber`/vault calls during boot or `/live`/`/ready` |
| `ElasticSearch.*` | (internal ES cluster) | ES REST API | `Enabled=false` — `NewElasticsearch()`/`Start()` no-op entirely, confirmed by code read, no container needed |
| `Telemetry.exporterHost/Port` | (internal OTel collector) | OTLP HTTP ingest | `127.0.0.1:4318`, nothing listening — logs show periodic `connect: connection refused`, harmless, fully local |

## Boot & health results

`cfa-server`, `WORKDIR`/`APP_ENV=arena`, config as above (final, incident-free run):

```
$ curl -sS -w '\nHTTP_STATUS=%{http_code}\n' http://127.0.0.1:8081/live
{"status":"SERVING_STATUS_SERVING","statusChecks":[{"name":"mongodb","status":"ok"},{"name":"max-go-routines","status":"ok"},{"name":"max-gc-pause","status":"ok"}]}
HTTP_STATUS=200

$ curl -sS -w '\nHTTP_STATUS=%{http_code}\n' http://127.0.0.1:8081/ready
{"status":"SERVING_STATUS_SERVING","statusChecks":[{"name":"mongodb","status":"ok"},{"name":"max-go-routines","status":"ok"},{"name":"max-gc-pause","status":"ok"}]}
HTTP_STATUS=200
```

Also checked: `GET /commit.txt` → `200`, empty body (no `APP_GITCOMMIT` set, expected for a local build). `internal_server` metrics port (`:8082`, Prometheus) responded `200`.

Time-to-ready: server logged `"server(s) running"` well under 1s after MongoDB/MySQL connect (both pre-warmed local containers) — the 60s wait budget in the brief was unnecessary here.

Boot log fully grepped for `razorpay\.(com|in)|amazonaws|arn:aws|10\.[0-9]+\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.` — zero hits in the final run (the two incidents above happened in *earlier* runs, both caught and fixed before this final clean run).

Process was `kill`ed (SIGTERM) after verification; log shows a clean graceful-shutdown sequence (`sigterm received` → `cancel() context` → `gracefully shutdown`).

## Worker boot notes

`cfa-worker` requires the same `Store`/`APIStore`/`Dcs`-adjacent config (it doesn't init DCS or Elasticsearch/Bin/Vault/Token/Api clients — narrower dependency surface than `cfa-server` — but does init `Store`, and both `Store`+`APIStore` are still mandatory since `storage.New` is called for `config.Store` only in worker, actually — **correction**: re-checked `cmd/worker/main.go`, it only calls `storage.New(ctx, config.Store)` (Mongo), **not** `config.APIStore` — the worker binary does not need MySQL at all, only the server does).

With `QUEUE_DRIVER=sqs_local` override: worker boots (`grpc`/`http`/`internal` listeners up, `"worker running"` logged), immediately starts polling its (empty-named) queue, and loops on `dial tcp 127.0.0.1:1: connect: connection refused` roughly every 50-60ms — this is expected/by-design since no SQS-compatible stub is running locally; it demonstrates the worker's only real runtime dependency beyond Mongo is a reachable SQS endpoint. Let it run ~6-12s (well within the 10-15s budget), confirmed log stayed 100% local, then killed with `SIGKILL` (the poll loop doesn't have a clean shutdown path worth waiting on for this probe).

## Blockers

- **MySQL was not in the brief's authorized datastore list** (only Mongo/Redis were named) but is a hard, unconditional boot-time dependency for `cfa-server` (`config.APIStore`, `storage.New` has no no-op choice and `log.Fatalf`s on error). Added a local `mysql:8.0` container to get a full boot+health result; flagging this as a scope note for the coordinator rather than a silent expansion — worth confirming this is acceptable before treating it as "the" cfa local-dev recipe.
- **`Dcs.mock=true` does not prevent a real network call** in `goutils/dcs@v1.7.1` (see incident #1) — worth a heads-up to whoever owns that shared library, since any service using this DCS SDK version with `Mock=true` in a sandboxed/offline environment will make the same unintended login call.
- **`Queue.Driver="sqs"` ignores `Endpoint`** in `goutils/worker/v2/queue` (see incident #2) — same heads-up; `Endpoint` looks like a working override in the TOML/struct but silently does nothing unless `Driver="sqs_local"`, which cfa's own `eventsystem` service explicitly disallows for the server binary.
- No SQS-compatible stub (e.g. ElasticMQ) was stood up, so the worker's actual message-consume path was not exercised end-to-end — only proven safe/local, not proven functional. Out of scope per the brief (Mongo/Redis only); flagging as the next step if worker functionality (not just "does it boot without touching AWS") needs validating.
- `ElasticSearch.Mock` is typed `bool` in `pkg/elasticsearch/config.go` but cfa's own `slit.toml` sets it as the TOML string `"true"` (not `true`) — likely a latent bug/typo in that file (not something this spike needed to fix, since `arena.toml` used a proper bool and `Enabled=false` makes `Mock` irrelevant anyway either way).
- `vaultservice.NewCardVaultClient` doesn't check `Mock` at construction (unlike bin/token/api services) — noted above; not a blocker here (BaseURL is a safe local stub) but worth knowing if this client is ever exercised on a request path during a deeper spike.

---

SUMMARY: compile=✔ migrate=✔ boot=✔ health=✔

All four target outcomes succeeded. Two real-network-call incidents occurred and were caught mid-spike (DCS login to `dcs-live.dev.razorpay.in`, worker SQS polling to `sqs.us-east-1.amazonaws.com`) — both root-caused, fixed via config-only changes (no repo edits), and reverified clean before the final boot/health run reported above. MySQL (`APIStore`) was added as an out-of-brief but load-bearing local dependency to reach a real boot+health result; without it, `boot`/`health` would both be ✘ (`log.Fatalf` on `storage.New(config.APIStore)`).
