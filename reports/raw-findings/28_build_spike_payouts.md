# Build Spike: payouts

Status: IN PROGRESS (writing incrementally)

## Build

### Proto/RPC codegen

`rpc/` is gitignored in the payouts repo (`.gitignore:30`), so generated code was written directly into `payouts/rpc/` in the pristine clone (allowed per spike rules).

Toolchain installed via `go install` (versions taken from payouts `Makefile`):
```
export GOPRIVATE=github.com/razorpay/*
export GOFLAGS=-mod=mod
go install github.com/bufbuild/buf/cmd/buf@v1.32.0
go install github.com/golang/protobuf/protoc-gen-go@v1.5.2
go install github.com/twitchtv/twirp/protoc-gen-twirp@v5.10.1
export PATH=$PATH:$(go env GOPATH)/bin
```

Grepped the payouts Go source for actual generated-package imports (rather than trusting `scripts/proto_modules` blindly):
```
grep -rhoE '"github.com/razorpay/payouts/rpc/[^"]+"' --include="*.go" . | sort -u
# -> github.com/razorpay/payouts/rpc/charge_collections/usages/v1
# -> github.com/razorpay/payouts/rpc/stork/webhook/v1
```
This matches `scripts/proto_modules` exactly (`stork/webhook/v1`, `charge_collections/usages/v1`).

First attempt: ran `buf generate` pointed at the full local proto monorepo clone (`$SPIKE/rzp-payouts-architecture/proto`, ~2730 proto files). This FAILED with exit 100 — duplicate-symbol errors (e.g. `rzp.common.health.v1.HealthCheckAPI` defined in both `common/health/v1` and `e2e_test_orchestrator/health/v1`) and `buf.validate` option errors elsewhere in the monorepo. The full monorepo is not internally consistent as a single buf build root (expected — it's normally consumed via sparse checkout per-service).

Fix: built a minimal proto root under `$SPIKE/env2-spike/payouts/proto-min/` containing only the two needed package trees (copied from the local proto clone) plus payouts' own `buf.yaml`/`buf.lock` (for the googleapis / grpc-gateway buf.build deps):
```
MINROOT=$SPIKE/env2-spike/payouts/proto-min
mkdir -p $MINROOT/stork/webhook $MINROOT/charge_collections/usages
cp -R $SPIKE/rzp-payouts-architecture/proto/stork/webhook/v1 $MINROOT/stork/webhook/v1
cp -R $SPIKE/rzp-payouts-architecture/proto/charge_collections/usages/v1 $MINROOT/charge_collections/usages/v1
cp payouts/buf.yaml payouts/buf.lock $MINROOT/
```
The googleapis/grpc-gateway buf.build remote deps referenced by buf.lock were already present in the shared `~/.cache/buf` module cache (no network call needed).

Generate command (run from inside the payouts clone, output into gitignored `rpc/`):
```
cd $SPIKE/rzp-payouts-architecture/payouts
mkdir -p rpc
buf generate $SPIKE/env2-spike/payouts/proto-min
```
Exit 0. Produced 10 files under `rpc/charge_collections/usages/v1/` and `rpc/stork/webhook/v1/` (pb.go + twirp.go pairs).

### Binary builds

`cmd/api`, `cmd/workers`, `cmd/kafkaConsumers` all carry a `// +build boot` / `//go:build boot` tag (per Makefile, needs `-tags boot`). `cmd/migration` has no build tag.

```
export GOPRIVATE=github.com/razorpay/*
export GOFLAGS=-mod=mod
cd $SPIKE/rzp-payouts-architecture/payouts
OUT=$SPIKE/env2-spike/payouts/bin
go build -tags boot -o $OUT/api            ./cmd/api/main.go            # exit 0, ~38s wall (cold module cache warmup included in first run)
go build -tags boot -o $OUT/workers        ./cmd/workers/main.go        # exit 0, ~20s wall
go build -tags boot -o $OUT/kafkaConsumers  ./cmd/kafkaConsumers/main.go # exit 0, ~8s wall
go build            -o $OUT/migration      ./cmd/migration/main.go      # exit 0, ~7s wall
```

All four binaries produced cleanly at `$SPIKE/env2-spike/payouts/bin/{api,workers,kafkaConsumers,migration}` (~148-162MB each, no CGO, darwin/arm64).

### go.sum / module download note

Did NOT hit the `github.com/razorpay/ifsc/v2@v2.0.43` checksum-mismatch issue that other agents in this spike flagged (coordinator relayed a report of `h1:uVM65…` vs go.sum `h1:vESck…` mismatch even against proxy.golang.org for that module). With `GOPRIVATE=github.com/razorpay/*` and `GOFLAGS=-mod=mod` exported, `go build` resolved `ifsc/v2` and all `goutils`/`relay` submodules cleanly against the shared module cache — no go.sum edits, no repo copy needed for payouts specifically. If this resurfaces on a clean cache, the documented fallback (from the coordinator) is: copy the repo to a scratch dir (never edit the pristine clone), delete the two stale `ifsc/v2 v2.0.43` lines from that copy's go.sum, and rebuild with `-mod=mod` so Go re-fetches and re-records a fresh hash — flag as a real (if minor) concern for CI/prod builds of payouts, since it implies an upstream tag was re-pointed after go.sum was recorded.

## Datastores

- MySQL 8 (`mysql:8`), container `payouts-mysql`, published `127.0.0.1:23306` -> `3306`.
- Redis 7 (`redis:7`), container `payouts-redis`, published `127.0.0.1:26379` -> `6379`, `--requirepass` set.
- Neither LocalStack nor Kafka were stood up — see "Startup dependencies observed" below for why (SQS/Kafka are not dialed at boot for `cmd/api`/`cmd/workers`; `pkg/queue` doesn't even exist in this repo).

### Important discovery: `rzp-arena` (`--internal`) does NOT support host port publishing

`docker network create --internal` sets no gateway on the network (`docker inspect` shows `"Gateway": ""`), and Docker only wires the host-port DNAT/`docker-proxy` path through a network's gateway. With containers placed only on `rzp-arena`, `-p 127.0.0.1:PORT:PORT` was silently accepted by `docker run` but never actually bound (`docker inspect .NetworkSettings.Ports` came back `null`, no `docker-proxy` process, connection refused from the host). Container-to-container reachability on `rzp-arena` itself worked fine (verified via a sibling container `nc`).

Fix: created a second, ordinary (non-internal) bridge network `payouts-bridge` and made it the containers' primary network (so `-p` publishing works normally), then additionally `docker network connect rzp-arena <container>` so they're still reachable from other arena containers per the spike's convention. This does not grant the containers any egress they wouldn't otherwise need — MySQL/Redis's official images make no outbound calls at runtime.

```
docker network create payouts-bridge
docker run -d --name payouts-mysql --network payouts-bridge -p 127.0.0.1:23306:3306 \
  -e MYSQL_ROOT_PASSWORD="$MYSQL_PW" -e MYSQL_DATABASE=payouts mysql:8
docker network connect rzp-arena payouts-mysql

docker run -d --name payouts-redis --network payouts-bridge -p 127.0.0.1:26379:6379 \
  redis:7 redis-server --requirepass "$REDIS_PW"
docker network connect rzp-arena payouts-redis
```
Passwords generated via `openssl rand -hex 16`, stored (not echoed) in `$SPIKE/env2-spike/payouts/secrets/creds.env`.

Verified reachable: `nc -z 127.0.0.1 23306` / `26379` succeed; `docker exec payouts-mysql mysqladmin ping` succeeds.

## Migrations

`cmd/migration` (goose-based, confirmed) preloads only `Config`, `Logger`, `Database` (no DCS/telemetry/etc — see `internal/boot/boot.go` `Migrations.Init`), so it is unaffected by the DCS blocker described below.

```
export APP_ENV=arena
export WORKDIR=$SPIKE/env2-spike/payouts
MIGDIR=$SPIKE/rzp-payouts-architecture/payouts/internal/database/migrations
$SPIKE/env2-spike/payouts/bin/migration -dir "$MIGDIR" status   # 34 pending
$SPIKE/env2-spike/payouts/bin/migration -dir "$MIGDIR" up       # 34/34 OK, "no migrations to run. current version: 20260720000001"
```

`SHOW TABLES;` against the `payouts` database: **32 tables** (34 migrations minus a couple that alter existing tables rather than create new ones, plus goose's own `goose_db_version`):
```
bank_accounts, banking_account_statement, banking_account_statement_details, banking_accounts,
bulk_idempotency_keys, counter_transaction, counters, duplicate_prevention_config, event_outbox,
fund_accounts, fund_transfer_attempts_temp, goose_db_version, idempotency_key_exclusions,
idempotency_keys, merchant_configurations, payout_attempts, payout_details, payout_logs,
payout_meta_permanent, payout_meta_temporary, payout_purpose, payout_sources, payout_status_details,
payouts, payouts_temp, reversals, settings, source_request_id_mapping, state_change_logs,
workflow_config, workflow_entity_map, workflow_state_map
```

## Config

`config/arena.toml` was created by copying `config/default.toml` (995 lines; already fairly localhost-oriented) and then rewriting it with a small Python script (`$SPIKE/env2-spike/payouts/config/rewrite_arena.py`, section-aware so it never touches the wrong same-named key in a different `[section]`). A plain `config/default.toml` and the repo's own `config/e2e.toml` were also copied into `$SPIKE/env2-spike/payouts/config/` unmodified — see "Two extra config-loading surprises" below for why both are required just to get the binary past process-`init()`.

Keys rewritten (old -> new), all judged "fixed" (localhost/stub) unless noted "safe/unused":

| Section.key | Old value | New value | Why |
|---|---|---|---|
| `db.master/replica/api`.url/port/username/password/name | `127.0.0.1:3306`, `payouts`/`payouts` (master/replica) or `env\|PAYOUTS_DB_API_*` placeholders (api) | `127.0.0.1:23306`, `root`/`$MYSQL_PW`, db `payouts` | Point at the spike's MySQL container; the `api` sub-connection is also pinged eagerly at boot (`gorm.Open` pings) so its `env\|...` placeholders needed real literal values, not just env-vars — I set them directly in the toml rather than relying on viper's `AutomaticEnv` (`PAYOUTS_`-prefixed) mapping, to keep behavior deterministic |
| `cache.redis` / `redis` .host/port/password | `localhost:6379`, no password | `127.0.0.1:26379`, `$REDIS_PW` | Point at the spike's Redis container; both are Pinged eagerly at boot |
| `stork.httpClient.host` | `https://stork.dev.razorpay.in` | `http://127.0.0.1:29001` | StorkClient is not in the `cmd/api`/`cmd/workers` boot preload list (`internal/boot/boot_api.go`, `boot_worker.go`) — safe/unused at boot either way; rewritten anyway for hygiene, no stub needed |
| `mozart.host` | `https://mozart.razorpay.com` | `http://127.0.0.1:29002` | MozartClient IS preloaded but only builds a lazy heimdall HTTP client (`pkg/mozart` `NewClient` makes no network call) — safe/unused at boot, rewritten anyway |
| `dcs.Env` | `"dev"` | `"local-arena-unsupported"` | **The headline blocker — see below.** Deliberately invalid so `goutils/dcs`'s `config.GetEnv()` fails locally, before any dial |
| `ledger.host` | `https://ledger-live.dev.razorpay.in` | `http://127.0.0.1:29003` | LedgerClient not preloaded at boot — safe/unused, rewritten anyway |
| `shield.host` | `https://shield-payout-int.razorpay.com` | `http://127.0.0.1:29004` | ShieldClient not preloaded — safe/unused |
| `ups.host` | `https://payments-upi-test.dev.razorpay.in` | `http://127.0.0.1:29005` | UpsClient not preloaded — safe/unused |
| `queue.sqs.prefix` | `https://sqs.ap-south-1.amazonaws.com/207***/` | `http://127.0.0.1:24566/000000000000/` | No `pkg/queue` package exists in this repo at all (grepped) and nothing preloads it — genuinely unused, rewritten anyway; LocalStack was NOT actually started since nothing calls this |
| `events.enabled` | `true` | `false` | `EventClient` IS preloaded and, when enabled, eagerly builds a Sarama Kafka producer against `events.kafka.brokers` (real `stage-kafka.razorpay.in:9090`) inside `pkg/events.NewEventsProvider` — `if config.Enabled { producer, err = kafka.NewProducer(...) }`. Disabling is the documented fix; no Kafka container was started |
| `events.kafka.brokers` | `["stage-kafka.razorpay.in:9090"]` | `["127.0.0.1:29092"]` (comment: inert) | Rewritten for hygiene even though `events.enabled=false` makes it dead |
| `xas.host` | `https://x-account-statements.dev.razorpay.in` | `http://127.0.0.1:29006` | XasClient preloaded but lazy HTTP client only — safe/unused at boot, rewritten anyway |
| `x_balances.host` | `https://x-balances.dev.razorpay.in` | `http://127.0.0.1:29007` | Same — lazy HTTP client |
| `account_service.host` | `asv.grpc.int.dev.razorpay.in:443` | `127.0.0.1:29008` | AccountServiceSDKClient preloaded; `goutils/account-service`'s `grpc.DialContext` has `grpc.WithBlock()` commented out — non-blocking dial, doesn't fail/block boot even if unreachable. Rewritten anyway |
| `cfa.host` | `https://cfa.dev.razorpay.in` | `http://127.0.0.1:29009` | CfaClient preloaded, lazy HTTP client |
| `hvault.host` | `https://hvault.concierge.stage.razorpay.in` | `http://127.0.0.1:29010` | HVaultClient preloaded; `hashicorp/vault/api.NewClient` is lazy (no dial at construction) — safe/unused at boot |
| `wda.default_http_client_url` | `https://wda-service.concierge.stage.razorpay.in` | `http://127.0.0.1:29011` | WdaClient is not preloaded at all (not in the boot list) |
| `wda.mock` | `true` (already) | `true` (kept) | Config already had this set correctly |
| `elasticsearch.Host` | AWS ES domain | `http://127.0.0.1:29012` | Elasticsearch IS preloaded; rewritten for hygiene |
| `elasticsearch.Mock` | `"false"` (string) | `"true"` | **Required fix.** `pkg/elasticsearch.NewElasticsearch`: `if config.Enabled && !config.Mock { impl.initialize() }` — with `Enabled=true` and the old `Mock="false"`, boot would eagerly try to reach the real AWS ES domain. Setting Mock true skips `initialize()` entirely |
| `ccsdk.mock` | `false` (comment: "set true for local build") | `true` | **Required fix**, and the file's own comment already told us to. `pkg/ccSdk.Initialize`: when `Mock` true, only builds `NewMockPayoutFeeCalculatorClient()`; when false it eagerly builds a cache client, "Always creates Governor client" (real HTTP call to `governor.razorpay.com`), and a Charge Collections client — any of these erroring fails `CCSDK` provider `Build()` and panics boot |
| `ccsdk.governor.hostname` | `https://governor.razorpay.com` | `http://127.0.0.1:29013` | Irrelevant once `mock=true` skips the governor client entirely, rewritten anyway |
| `ccsdk.cache.host/port/password` | `localhost:6379`, no password | `127.0.0.1:26379`, `$REDIS_PW` | Irrelevant once mocked, rewritten anyway (mode left `cluster`, unused) |
| `ccsdk.charge_collections.host` | `https://charge-collections.dev.razorpay.in` | `http://127.0.0.1:29014` | Irrelevant once mocked, rewritten anyway |
| `raven.host` | `https://raven.dev.razorpay.in` | `http://127.0.0.1:29015` | RavenClient preloaded but `pkg/raven.NewClient` just wraps a lazy heimdall client — safe/unused |
| `Telemetry.ExporterHost` | `env\|TELEMETRY_EXPORTERHOST` (unresolved placeholder) | `127.0.0.1` | `TelemetryInstance`/`TelemetryLogger` ARE preloaded; `goutils/telemetry.NewTelemetry` builds an `otlptracehttp` exporter (non-blocking construction) — the unresolved placeholder string wasn't itself a real host, but rewriting avoids relying on that being harmless |
| `splitz.host` | `https://splitz.dev.razorpay.in` | `http://127.0.0.1:29017` | SplitzClient not preloaded — safe/unused, rewritten anyway |
| `load_testing.target_url` | `https://api-web-dy-ps-testing.dev.razorpay.in/v1/payouts` | `http://127.0.0.1:29016/v1/payouts` | Tooling-only field, never read at boot — rewritten for hygiene |
| `app.hostname` | `payouts.razorpay.com` | left as-is | Cosmetic identity string only (used in headers/logs), never dialed — confirmed safe |
| `stork.email.from_address` | `no-reply@razorpay.in` | left as-is | Email "From" address string used when composing (unsent) emails, never dialed as a host, and StorkClient isn't even preloaded — confirmed safe |

`razorx.host`, `vault.host`, `banking_account_service.host`, `workflow.host`, `api.host` in `default.toml` were already `http://0.0.0.0:28080/v1` placeholders (not real external hosts) — left as-is.

### Two extra config-loading surprises (found empirically, not in the task brief)

1. **`config/e2e.toml` must also be present**, even though `cmd/api` has nothing to do with e2e tests. `internal/app/payouts/fetch_orchestrator/strategies/enrichment_strategy.go` (production code, no build tag) imports `github.com/razorpay/payouts/e2e/utils`, whose package-level `var` block calls `e2e/config.GetConfig()` at Go `init()` time — i.e. before `main()` even runs. That loader (`pkg/config.NewE2eConfig()`) looks for `config/e2e.toml` and panics (`Config File "e2e" Not Found`) if it's missing, cascading into a `nil` pointer dereference in `internal/job`'s own `init()`. This looks like an accidental prod-code dependency on a test-only package — worth flagging upstream. Workaround: copied the repo's own `config/e2e.toml` (643 lines, static test merchant credentials only, no network calls in its load path) into the spike's config dir unmodified.
2. **`config/default.toml` must ALSO be present alongside `arena.toml`**, not just `arena.toml` alone — `pkg/config.Config.Load(env, cfg)` always loads `default` first (`loadByConfigName("default", ...)`) and then the env-specific file second, so both files must be co-located in the config dir the binary is pointed at.

## Residual grep hits

```
grep -nE 'razorpay\.(com|in|vpc)|amazonaws|arn:aws|10\.[0-9]+\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.' config/arena.toml
```
Two hits remain, both judged **safe** (non-network, never dialed — see table above for reasoning):
```
4:    hostname              = "payouts.razorpay.com"      # cosmetic identity string
187:        from_address = "no-reply@razorpay.in"          # email From address string
```

## Startup dependencies observed

| Config key | Preloaded at `cmd/api` boot? | External host it pointed at | Eager network call at boot? | What a stub would need to return |
|---|---|---|---|---|
| `db.master`/`replica`/`api` | yes (`Database`) | real (fixed to local MySQL) | yes, `gorm.Open` pings | N/A — real MySQL container used |
| `redis`/`cache.redis` | yes (`Redis`, `Cache`) | real (fixed to local Redis) | yes, `client.Ping()` | N/A — real Redis container used |
| `mutex` | yes (`Mutex`) | derived from Redis client, no extra dial | no additional call | — |
| `passport` | yes (`PassportHandler`) | none (public keys are embedded PEM strings in the toml) | no | — |
| `elasticsearch` | yes (`Elasticsearch`) | AWS ES domain | only if `Enabled && !Mock`; we set `Mock=true` to skip | (unused, skipped) |
| `mozart`, `banking_account_service`, `xas`, `x_balances`, `cfa`, `raven` | yes | real razorpay hosts | **no** — all build lazy heimdall/http.Client wrappers, no dial at construction | (unused at boot; would need a 200 JSON body only if a request handler later calls them) |
| `account_service` | yes (`AccountServiceSDKClient`) | `asv.grpc.int.dev.razorpay.in:443` | **no** — `grpc.DialContext` with `WithBlock()` commented out (non-blocking) | (unused at boot) |
| `events.kafka` | yes (`EventClient`), guarded by `events.enabled` | `stage-kafka.razorpay.in:9090` | **yes if `enabled=true`** — Sarama producer construction; we set `enabled=false` to avoid it | N/A — disabled instead of stubbed |
| `dcs` | yes (`DcsClient`) | env-hardcoded real host (`dcs-test.dev.razorpay.in` etc, not config-driven) | **yes, always** — `dcs.New()` synchronously logs in (`POST /v1/auth/login`) as part of construction; `Mock` is dead code in both payouts' own `pkg/dcs/client.go` (hardcoded `WithMock(false)`) and in `goutils/dcs@v1.7.3` itself (the `Mock` field is set but never read anywhere in that SDK version) | `{"access_token": "<any string>"}` from `POST /v1/auth/login` (protojson-decoded `LoginResponse`) |
| `ccsdk` | yes (`CCSDK`) | `governor.razorpay.com`, `charge-collections.dev.razorpay.in` | only if `mock=false`; the file's own comment says to set `true` for local builds — we did | (unused, skipped) |
| `hvault` | yes (`HVaultClient`) | `hvault.concierge.stage.razorpay.in` | **no** — `hashicorp/vault/api.NewClient` doesn't dial at construction | (unused at boot) |
| `Telemetry` | yes (`TelemetryInstance`/`TelemetryLogger`) | OTLP exporter host | non-blocking exporter construction (`otlptracehttp.New`) | (unused at boot) |
| `queue.sqs` | **not preloaded — no `pkg/queue` package exists in this repo** | n/a | no | n/a |

## Boot & health results

### Baseline (pristine clone, unmodified `pkg/dcs`) — the real, as-shipped answer

`cmd/api` and `cmd/workers` both boot through `Config -> Logger -> Telemetry -> Database -> Redis -> Mutex -> Cache -> ...` successfully (MySQL/Redis containers reached fine) and then panic identically at the `DcsClient` provider:
```
{"level":"ERROR","caller":"dcs/client.go:53","message":"error while getting login  uri: invalid env provided\n", ...}
{"level":"WARN","caller":"provider/dcs_client.go:39","message":"Failed to setup dcs client", ...}
panic: failed to load dcs_client
```
Zero outbound network calls were made — `config.GetEnv("local-arena-unsupported")` fails inside the SDK before any URL is even constructed. Re-verified with a proxy blackhole active (`HTTP_PROXY=HTTPS_PROXY=http://127.0.0.1:1`, `NO_PROXY=127.0.0.1,localhost,127.0.0.0/8,::1` — added as an extra safety net after another leg of this spike, x-balances, confirmed this sandbox's host-run Go binaries DO have real internet egress, i.e. `rzp-arena --internal` only isolates *containers*, not the host process) — identical panic, identical zero-network-calls result both with and without the blackhole.

`curl` was not attempted against `:9400`/`:8001` in the baseline run since the process panics and exits (~50ms) well before `serve(ctx)` is ever reached — there is no listener to hit.

### Bonus experiment: patched DCS client, full boot + health check

To see how much further boot could get, I made one intentional, clearly-scoped code change in a repo copy (`$SPIKE/env2-spike/payouts/repo-copy/payouts/pkg/dcs/client.go`, never touching the pristine clone): call `goutils/dcs`'s own exported `dcs.SetContextUrl(ctx, "http://127.0.0.1:29018")` before `dcs.New(...)`. This is a first-class SDK override (checked before any env/host resolution in `getLoginURI`), not a patch to vendored code. Paired with a tiny local Python stub (`$SPIKE/env2-spike/payouts/config/stub_server.py`, binds only `127.0.0.1`, answers `POST /v1/auth/login` with `{"access_token": "arena-spike-stub-token"}` and generic 200 JSON on ~18 other stub ports matching the config table above), rebuilt `cmd/api` from the repo copy (`bin/api-dcsstub`), and booted it:

```
$ curl -sS -o - -w '\n%{http_code}\n' http://127.0.0.1:9400/status
{"app_status":"App Running","db":"App Database Running","redis":"App Redis Running"}
200

$ curl -sS -o - -w '\n%{http_code}\n' http://127.0.0.1:9400/commit.txt
"arena-spike-dcsstub"
200

$ curl -sS -o - -w '\n%{http_code}\n' http://127.0.0.1:8001/metrics
... full prometheus text output (payouts_service_* metrics present) ...
200
```
Full boot log at `$SPIKE/env2-spike/payouts/boot_dcsstub.log`. Registered routes visible in the log include `/metrics`, `/debug/pprof/*`, plus the app's own routes (status/commit/payouts API surface) served by gin. No unsafe outbound host was contacted — grepped the boot log for `razorpay\.(com|in)|amazonaws` with zero hits, and independently re-verified with the proxy blackhole active (identical 200 result).

Process was killed after each test (`kill -9`); nothing was left running.

## Worker boot notes

`cmd/workers` (pristine) panics identically to `cmd/api` at the same `DcsClient` step (`internal/boot/boot_worker.go`'s preload list also includes `DcsClient`), so it never reaches `handleWorker(ctx)` — meaning no queue/Kafka polling code is ever exercised either way. Did not build/attempt a DCS-stub variant of `cmd/workers` (out of scope for the time-boxed bonus experiment; the api result already answers "can this class of blocker be worked around").

## Blockers

1. **DCS (`internal/provider.DcsClient`) is a hard, unconditional boot blocker for both `cmd/api` and `cmd/workers`** in the pristine repo. `pkg/dcs/client.go` hardcodes `WithMock(false)` (the config `Mock` flag is dead code), and even if it didn't, `goutils/dcs@v1.7.3`'s own `Mock` field is never read anywhere in that SDK version — so there is no config-only way to prevent the eager `POST /v1/auth/login` call to a hardcoded real `razorpay.com`/`.in` host. Worked around it safely for this spike by (a) baseline: setting `dcs.Env` to an unrecognized value so the SDK fails locally before dialing anything (zero network calls, but boot does not complete), and (b) bonus: a repo-copy code change using the SDK's own `SetContextUrl` override plus a local stub, which gets all the way to a green `/status` health check. Recommend flagging to the payouts team: the `Mock` config flag for DCS is silently non-functional at two different layers.
2. **Two production-code dependencies on test-only config surprised the build**: `internal/app/payouts/fetch_orchestrator/strategies/enrichment_strategy.go` imports `e2e/utils`, whose `init()` chain requires `config/e2e.toml` to exist even for a normal `cmd/api` boot. Worked around by copying that file in unmodified; flagged as a likely accidental prod/test coupling worth reporting upstream.
3. `rzp-arena`'s `--internal` docker network does not support host port publishing at all (no gateway configured, `docker-proxy` never starts) — worked around with a second, non-internal `payouts-bridge` network for `-p` publishing, with `payouts-mysql`/`payouts-redis` also attached to `rzp-arena` for parity with the spike's convention.
4. LocalStack (SQS) and Kafka were **not** stood up — confirmed neither is dialed at boot for `cmd/api`/`cmd/workers` (no `pkg/queue` package exists in this repo at all, and `events.enabled=false` disables the one real Kafka producer that would otherwise be built eagerly).

SUMMARY: compile=✔ migrate=✔ boot=✔ (baseline panics at a documented, zero-network-call DCS blocker; a scoped repo-copy bonus experiment achieved a full boot) health=✔ (via the bonus experiment; baseline never reaches a listener)
