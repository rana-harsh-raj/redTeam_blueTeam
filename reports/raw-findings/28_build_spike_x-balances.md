# Build spike: x-balances

Repo: `$SPIKE/rzp-payouts-architecture/x-balances`
Working area: `$SPIKE/env2-spike/x-balances/` (`bin/`, `config/`, `logs/`, `boot.log`, `worker_boot.log`)
Findings file: this file (`findings/28_build_spike_x-balances.md`)

## TL;DR

- Compiles cleanly, no proto/buf generation needed (generated `rpc/` code is already checked into git).
- Migrations run cleanly against a local MySQL 8 — 2 tables (`balance`, `goose_db_version`).
- **`cmd/worker` boots fully and passes `/live` and `/ready` (HTTP 200, `SERVING_STATUS_SERVING`).**
- **`cmd/server` cannot boot in this sandbox.** It has a *hard-coded, unconditional, fatal* dependency on a real DCS (Dynamic Configuration Service) login call at construction time. `Mock=true` does **not** suppress this call — it is a library-level (`goutils/dcs@v1.5.2`) behavior, not something x-balances' own config exposes a bypass for. See "Blockers" below.
- **Incident**: before this was understood, one x-balances server boot attempt made a real outbound HTTPS POST to `https://dcs-live.dev.razorpay.in/v1/auth/login` and got back a live HTTP 404 (i.e. egress to `*.razorpay.in` was **not** blocked at the network level in this sandbox). All subsequent boot attempts were protected with a process-local `HTTPS_PROXY=http://127.0.0.1:1` "blackhole" so any further attempt fails closed (`connection refused`) instead of reaching a real host. No credentials or data were sent past a 404-returning auth endpoint; nothing was written anywhere. See "Incident" section.

```
SUMMARY: compile=✔ migrate=✔ boot=✔(worker only, server=✘) health=✔(worker only)
```

---

## Build

Go toolchain: `/opt/homebrew/bin/go` 1.26.6 darwin/arm64, `GOPRIVATE=github.com/razorpay/*` exported.

`go mod download` completed in <1s (already warm from a concurrent background download by the coordinator — verified clean exit 0 on my own run too).

No buf/protoc generation was required: `rpc/` is **tracked in git** (not gitignored — confirmed via `git ls-files rpc | wc -l` → 32 files, and `git check-ignore -v rpc` → no match). `.gitignore` explicitly documents this: *"rpc -- gRPC code is checked in to improve readability and searchability on Github"*. `proto/` is gitignored but present anyway (sourced from the shared proto repo per the same comment). No repo files were modified; nothing was copied to a `repo-copy/` (not needed since no generation happened).

Binaries built directly from the pristine clone to the external bin dir:

```bash
export GOPRIVATE=github.com/razorpay/*
cd $SPIKE/rzp-payouts-architecture/x-balances
go build -o $SPIKE/env2-spike/x-balances/bin/server    ./cmd/server/...      # 17.4s wall
go build -o $SPIKE/env2-spike/x-balances/bin/worker    ./cmd/worker/...      # 7.5s wall
go build -o $SPIKE/env2-spike/x-balances/bin/migration ./cmd/migration/...   # 5.0s wall
```

All three exit 0, no errors/warnings. `cmd/` layout (verified, not assumed): `cmd/server`, `cmd/worker`, `cmd/migration` — matches the task brief.

## Datastores

Docker containers, **not** on the `rzp-arena` (`--internal`) network at creation time — see important caveat below — then joined to it afterward:

```bash
MYSQL_PW=$(openssl rand -hex 16)
REDIS_PW=$(openssl rand -hex 16)
APP_PW=$(openssl rand -hex 16)   # shared by both mysql app users

docker run -d --name xbal-mysql -p 127.0.0.1:23408:3306 \
  -e MYSQL_ROOT_PASSWORD="$MYSQL_PW" mysql:8      # -> MySQL 8.4.11

docker run -d --name xbal-redis -p 127.0.0.1:26481:6379 \
  redis:7 redis-server --requirepass "$REDIS_PW"  # -> Redis 7

docker network connect rzp-arena xbal-mysql
docker network connect rzp-arena xbal-redis
```

**Caveat / gotcha found**: `rzp-arena` is created `--internal`. Containers attached to *only* an internal network **cannot have host ports published** — `docker run --network rzp-arena -p 127.0.0.1:PORT:CONTAINER_PORT ...` silently creates the container but drops the port publish (no error, no non-zero exit; `docker ps`/`docker port` simply show no host mapping). Fix: create the container on the default bridge network (where `-p` works normally), then `docker network connect rzp-arena <container>` afterward to also attach it to the internal network. Both networks coexist fine; the host-published port keeps working.

**Second gotcha**: my first choice of host ports (MySQL `23308`, Redis `26381`, per the brief) collided with another parallel spike agent's containers (`cfa-mysql` already had `23308`), and — combined with the `--internal`-network port-publish bug above — resulted in `xbal-mysql`/`xbal-redis` silently having *no* published port at all, which surfaced as a confusing "Access denied for user ...@172.17.0.1" MySQL auth error (the auth was fine; the port simply routed to the wrong/no container). Re-picked free ports and confirmed with `lsof` before recreating: **MySQL → 127.0.0.1:23408**, **Redis → 127.0.0.1:26481**.

App-level MySQL users/databases (both DBs on the same MySQL instance, matching x-balances' `Store`/`APIStore` split):

```sql
CREATE DATABASE rx_balances_local CHARACTER SET utf8mb4;
CREATE DATABASE api_local CHARACTER SET utf8mb4;
CREATE USER 'rx_balances_rw'@'%' IDENTIFIED BY '<APP_PW>';
GRANT ALL PRIVILEGES ON rx_balances_local.* TO 'rx_balances_rw'@'%';
CREATE USER 'api_rx'@'%' IDENTIFIED BY '<APP_PW>';
GRANT ALL PRIVILEGES ON api_local.* TO 'api_rx'@'%';
```

Connectivity verified from outside the container (`docker run --rm mysql:8 mysql -h host.docker.internal -P 23408 ...` and `redis-cli ... PING` → `PONG`) before running the app.

## Migrations

`cmd/migration` is a `goose`-based (Go-migration, not `.sql` files) runner. Only **one** migration exists in the repo: `internal/database/migrations/20250127224555_balance.go`.

The binary's default `-dir` flag (`/internal/database/migrations`, an absolute path baked in as a flag default) does not exist outside a container filesystem, so it must be overridden to the repo's actual migrations directory (read-only reference into the clone, never written to):

```bash
export WORKDIR=$SPIKE/env2-spike/x-balances   # config/ base for configloader
export APP_ENV=arena
XB=$SPIKE/rzp-payouts-architecture/x-balances

$SPIKE/env2-spike/x-balances/bin/migration -dir "$XB/internal/database/migrations" status
# Pending -- 20250127224555_balance.go

$SPIKE/env2-spike/x-balances/bin/migration -dir "$XB/internal/database/migrations" up
# OK   20250127224555_balance.go (15.21ms)
# goose: successfully migrated database to version: 20250127224555
```

**Outcome**: success. `SHOW TABLES` on `rx_balances_local`:

```
Tables_in_rx_balances_local
balance
goose_db_version
```

Table count: **2**. `goose_db_version` shows one applied row (`version_id=20250127224555`).

Note: only `Store` (the main `rx_balances_local` DB) is migrated by `cmd/migration` — `APIStore`/`api_local` is created empty (no migration targets it; the app only ever does a dual-write to a pre-existing `credit_transfers` table there in the real API monolith DB, which is out of scope for this spike and not exercised at boot).

## Config

Config loader (`pkg/configloader`, viper-based) always loads `config/default.toml` first, then overlays `config/<APP_ENV>.toml` on the same struct (values present in the second file win; anything absent is inherited from the first — confirmed by reading `pkg/configloader/configloader.go` and its test). `APP_ENV` defaults to `"dev"` if unset (`pkg/env/env.go`); there is no `dev.toml` checked in (gitignored, dev-machine-local). `WORKDIR` env var controls where `./config` is resolved from when unset it's relative-to-cwd.

Built `config/arena.toml` as a full overlay (every section re-specified, not just diffs) at `$SPIKE/env2-spike/x-balances/config/arena.toml`, run with `APP_ENV=arena WORKDIR=$SPIKE/env2-spike/x-balances`. Full file is committed there; key rewrites:

| Config key | Old (default.toml) | New (arena.toml) | Notes |
|---|---|---|---|
| `App.HostName` | `https://x-balances.dev.razorpay.in` | `http://127.0.0.1:8081` | cosmetic only, used in `/commit.txt` |
| `Server.ServerAddresses.Grpc/Http/Internal` | `:8080` / `:8081` / `:8082` (no host) | `127.0.0.1:8080` / `:8081` / `:8082` | **required** — see "grpc-gateway self-dial via proxy" below |
| `Store.Sql.Port` | `3306` | `23408` | local docker MySQL |
| `Store.Sql.Url` | `localhost` | `127.0.0.1` | |
| `Store.Sql.Password` | `rx_balances_rw_pw` (placeholder) | `<openssl rand hex 16>` | |
| `APIStore.Sql.Port/Url/Password` | same pattern | same pattern | second local DB `api_local` |
| `Queue.Driver` | `sqs` | `inmemory` | avoids AWS entirely — see "SQS avoided" below |
| `APIService.Mock` | `false` | `true` | avoids real call to `api-web.dev.razorpay.in` |
| `APIService.BaseURL` | `https://api-web.dev.razorpay.in` | `http://127.0.0.1:19301` (unused, Mock=true) | |
| `PayoutService.Mock` | `false` | `true` | avoids real call to `payouts.dev.razorpay.in` |
| `PayoutService.BaseURL` | `https://payouts.dev.razorpay.in` | `http://127.0.0.1:19302` (unused) | |
| `BankingAccountsConfig` (whole block) | *absent from default.toml* | added, `Mock=true`, `BaseURL=http://127.0.0.1:19304` | present in stage/prod/e2e but not default |
| `MozartConfig` (whole block) | *absent from default.toml* | added, `Mock=true`, `BaseURL=http://127.0.0.1:19305` | present in stage/prod/e2e but not default |
| `Cache.Redis.Addr` | `localhost:6379` | `127.0.0.1:26481` | local docker Redis |
| `Cache.Redis.Password` | `""` | `<openssl rand hex 16>` | |
| `RateLimiter.Redis.Addr/Password` | same pattern | same pattern | |
| `DCSConfig.Mock` | `false` | `true` | **does not actually prevent egress — see Blockers** |
| `AccountServiceConfig.Host` | `asv.grpc.conc.dev.razorpay.in:443` | `127.0.0.1:19306` (unused, Mock=true) | ASV client returns a stub when Mock=true (verified in code, no ping at construction) |
| `AccountServiceConfig.Mock` | `false` | `true` | |
| `LedgerConfig.Host` | `https://ledger-live.dev.razorpay.in` | `http://127.0.0.1:19303` (unused) | Ledger has **no Mock flag** (mandatory dependency) — safe because `pkg/ledger.NewClient()` only builds an SDK config + `http.Client`, verified no dial/ping at construction |
| `Telemetry.exporterHost` | `localhost` | `127.0.0.1` | already local in default.toml |
| Various `Server.Auth.*` basic-auth creds | `env\|SERVER_AUTH_*` placeholders (unresolved unless matching env var set — x-balances' own configloader does **not** special-case the `env\|` prefix, unlike some sibling repos) | dummy literal strings | cosmetic, only affects incoming request auth, irrelevant to boot/health |

Mock flags summarized: `APIService`, `PayoutService`, `BankingAccountsConfig`, `MozartConfig`, `AccountServiceConfig`, `DCSConfig` all set `Mock = true`. `LedgerConfig` has no Mock flag in the schema at all (by design — "mandatory dependency" per code comment) but is safe to boot with a fake host since its client is lazy.

Telemetry: OTLP exporter still points at `127.0.0.1:4318` — nothing is actually listening there, but the telemetry lib's exporter is async/non-fatal on connection failure (confirmed: boot proceeds past `telemetry.NewTelemetry` regardless of exporter reachability).

## Residual grep hits

```
grep -nE 'razorpay\.(com|in|vpc)|amazonaws|arn:aws|10\.[0-9]+\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.' config/arena.toml
```
```
4:## APP_ENV=arena. Every external (*.razorpay.in / *.razorpay.com / VPC) host
213:# Pointed at an unused local port so nothing here can reach *.razorpay.in. ----
```
Both hits are inside `#`/`##` comments documenting the rewrite — **not** live config values. Judged safe. No IP-literal hits (no 10.x/172.16-31.x/192.168.x anywhere).

## Startup dependencies observed

| Config key | External host it would otherwise hit | What a stub would need to return |
|---|---|---|
| `DCSConfig` | `https://dcs-live.dev.razorpay.in` (via `goutils/dcs@v1.5.2`, env `dev`→`Live` mode) | A `200` JSON login/token response from `POST /v1/auth/login` for the client construction to succeed at all — **`Mock=true` does not skip this call** (see Blockers). No config-level `ServerURL` override reachable from x-balances' `DCSConfig` struct (it only exposes `Env`/`Mock`/`Auth`, and the SDK's env→URL mapping has no "local" option). |
| `AccountServiceConfig` (ASV) | `asv.grpc.conc.dev.razorpay.in:443` | Not needed — `pkg/account.NewClient` returns `NewStub()` immediately when `Mock=true`, before touching `Host` at all. |
| `APIService`, `PayoutService`, `BankingAccountsConfig`, `MozartConfig` | `*.razorpay.in` / `*.razorpay.com` hosts | Not needed at boot — these gateway `NewService()` constructors are lazy HTTP client wrappers (Hystrix/heimdall-based), no ping/dial until a real API call is made. Mock flags additionally short-circuit call sites in these gateways. |
| `LedgerConfig` | `https://ledger-live.dev.razorpay.in` | Not needed at boot — `pkg/ledger.NewClient()` only assembles config + a plain HTTP client (verified by reading the code), no network call at construction. Would need a real 200 response once any ledger-touching RPC is exercised. |
| `Queue.Sqs` (if `Driver=sqs`) | `sqs.<region>.amazonaws.com` (per a same-family sibling repo's finding — the SQS driver ignores the `Endpoint` override) | Avoided entirely by setting `Queue.Driver = "inmemory"`, which is a real, first-class driver option in `pkg/worker/queue` (`pkg/worker/queue/broker/inmemory`) — no AWS SDK path touched at all. |
| `Telemetry` (OTLP exporter) | `127.0.0.1:4318` (already local) | Nothing listening; exporter failures are non-fatal/async. |

## Boot & health results

### `cmd/server` — does NOT boot (fatal)

`cmd/server/main.go` calls `pkgdcs.NewClient(...)` unconditionally and treats any error as `log.Fatalf` (`cmd/server/main.go:127`, `"DCS client init failed"`), **before** the HTTP/gRPC server is even constructed. Every attempt — with `DCSConfig.Mock=true` — dies here. `boot.log` (final, protected run):

```
{"level":"FATAL",...,"caller":"server/main.go:127","message":"DCS client init failed",
 "config":{"error":"Post \"https://dcs-live.dev.razorpay.in/v1/auth/login\": proxyconnect tcp: dial tcp 127.0.0.1:1: connect: connection refused"}}
```
(`connection refused` here is *our* blackhole proxy working as intended — see Incident — not a real network response.)

No health endpoint was ever reachable for `cmd/server` in this environment.

### `cmd/worker` — boots successfully, health checks pass

`cmd/worker/main.go`'s DCS init failure is **non-fatal** (`log.Errorf`, continues with `dcsSvc == nil`, only disables the payouts-balance-refresh event publisher). Boot command:

```bash
source $SPIKE/env2-spike/x-balances/config/boot_env.sh   # sets APP_ENV, WORKDIR, proxy blackhole
$SPIKE/env2-spike/x-balances/bin/worker > $SPIKE/env2-spike/x-balances/worker_boot.log 2>&1 &
```

Startup completed in well under 1s after the (blocked, safe) DCS attempt; servers came up immediately:
```
{"level":"INFO",...,"message":"grpc_server addr: 127.0.0.1:8080"}
{"level":"INFO",...,"message":"http_server addr: 127.0.0.1:8081"}
{"level":"INFO",...,"message":"internal_server addr: 127.0.0.1:8082"}
{"level":"INFO",...,"message":"worker running"}
{"level":"INFO",...,"message":"starting worker: pocworker"}
```

Health endpoints (found by reading `internal/server/http_handler.go` + `proto/common/health/v2/*.proto`: gRPC-gateway registers `GET /live` and `GET /ready`, **not** `/health` or `/` as the task brief guessed):

```bash
$ curl -s -o /dev/stdout -w "\nHTTP %{http_code}\n" http://127.0.0.1:8081/live
{"status":"SERVING_STATUS_SERVING", "statusChecks":[{"name":"database","status":"ok"},{"name":"max-go-routines","status":"ok"},{"name":"max-gc-pause","status":"ok"}]}
HTTP 200

$ curl -s -o /dev/stdout -w "\nHTTP %{http_code}\n" http://127.0.0.1:8081/ready
{"status":"SERVING_STATUS_SERVING", "statusChecks":[{"name":"max-go-routines","status":"ok"},{"name":"max-gc-pause","status":"ok"},{"name":"database","status":"ok"}]}
HTTP 200
```

`database: ok` in both confirms the MySQL connection (via `Store`) is live and healthy. Full `worker_boot.log` shows no other host contacted besides the single blocked DCS attempt (`grep -oE '"[a-zA-Z0-9._-]*razorpay[a-zA-Z0-9._-]*"|amazonaws...'` over the log after the proxy fix → zero matches besides the one already-blocked DCS URL string in the error message itself).

Process shut down gracefully via `SIGTERM` (`worker/main.go` graceful-shutdown path, `bigcache cleanup routine` stopped cleanly too).

### grpc-gateway self-dial via proxy — a config fix, documented for reproducibility

The internal HTTP→gRPC gateway (`grpc-gateway`'s `runtime.ServeMux`) dials the gRPC server using `Server.ServerAddresses.Grpc` verbatim (`goutils/grpcserver@v0.5.2.../server.go:307`, `r(mux, cfg.ServerAddresses.Grpc)`). With the default `":8080"` (empty host), Go's `http.ProxyFromEnvironment`/gRPC's proxy dialer could not match it against `NO_PROXY=127.0.0.1,...` (empty host doesn't match any NO_PROXY entry), so with the `HTTPS_PROXY` blackhole active, the health-check curl itself got proxied and failed. Fix: set `Server.ServerAddresses.Grpc = "127.0.0.1:8080"` explicitly in `arena.toml` (done above) so the NO_PROXY exclusion matches. This is purely an artifact of running under an egress-blackhole proxy for safety — not a real bug in x-balances.

## Incident — one unintended real outbound call

Before the `HTTPS_PROXY` blackhole was in place, the **first** `cmd/server` boot attempt (with `DCSConfig.Mock=true`, `Env="dev"`) made a real `POST https://dcs-live.dev.razorpay.in/v1/auth/login` request and received back a live **HTTP 404** response (i.e. DNS resolved and a TCP+TLS connection to a real `*.razorpay.in` host succeeded from this sandbox — egress was not blocked at the network level). The process errored on the 404 and fatally exited immediately afterward; no request body/credentials of consequence were sent beyond the login attempt itself (dummy username `arena_dcs` / dummy password), no response body was processed beyond a 404, and nothing was written anywhere as a result.

Root cause (confirmed by reading `goutils/dcs@v1.5.2/dcs.go` `Client.New()`): the DCS SDK **always** performs a login call during `New()` construction regardless of the `Mock` config flag — `Mock` is stored but never checked before the login step; it only affects `Get/Put/Patch` call sites *after* a successful client construction. x-balances' own `pkg/dcs.NewClient()` wraps this unconditionally and has no way to skip it. There is also no config-level way to redirect the login target to localhost: the URL is resolved from a hardcoded `env×mode → URL` table in the SDK (`config/uri.go`), and the `ServerURL` override field is only consulted when `len(Modes) != 1`, but x-balances always sets exactly one mode (`WithModes([]config.Mode{config.Live})`), so that branch is unreachable.

**Mitigation applied for all subsequent attempts**: exported `HTTP_PROXY=HTTPS_PROXY=http://127.0.0.1:1` (a closed, unused local port) plus `NO_PROXY=127.0.0.1,localhost,127.0.0.0/8,::1` before every binary invocation (see `config/boot_env.sh`). Go's default `http.Transport` honors these env vars; any outbound call to a non-excluded host — including the DCS login — now fails immediately and locally with `connection refused` instead of reaching a real host. Verified via log output on every subsequent run (`dial tcp 127.0.0.1:1: connect: connection refused`, not a real HTTP status).

**Relayed to the coordinator/other spike agents**: the `cfa` leg independently hit the identical `goutils/dcs` unconditional-login issue (and a separate SQS-ignores-Endpoint issue) and messaged this agent a warning mid-task — this finding is mutually confirmed across two independently-built services in the same monorepo family, so it likely affects any other service that eagerly constructs a `goutils/dcs` client at boot with `Env` set to a real environment name. x-balances additionally avoided the SQS trap by using `Queue.Driver="inmemory"` before ever building/running the binaries, so it was never exposed to the AWS-dial issue.

## Worker boot notes

Covered above under "cmd/worker — boots successfully". No separate consumer/other binary exists beyond `server`, `worker`, `migration` (verified via `find cmd -maxdepth 3`).

## Blockers

1. **`cmd/server` cannot boot without a working (or code-patched) DCS login.** This is the single blocker preventing a full server + health-check validation for the primary binary. Not fixable via config alone within this spike's constraints (no repo edits allowed, no `/etc/hosts` override attempted — deemed out of scope/too invasive for a sandboxed spike and not requested). Two real fixes exist outside this spike's scope:
   - Upstream: patch `cmd/server/main.go` to make DCS init non-fatal (matching `cmd/worker/main.go`'s existing pattern) for local/arena environments, or
   - Add a genuine "local" DCS env option to `goutils/dcs` config's env→URL table that points at a stub, and honor `Mock` before the login call.
2. Only `cmd/worker` was validated end-to-end (boot + `/live` + `/ready` = 200, `database: ok`). `cmd/server`'s HTTP routes (`/v1/accounts`, `/v1/balances`, etc.) were never exercised since the process never reaches `server.Run()`.
3. `APIStore`/`api_local` DB exists but is empty (no migrations target it) — any code path that dual-writes to `credit_transfers` there was not exercised (out of scope; boot-only spike).

```
SUMMARY: compile=✔ migrate=✔ boot=✔(worker)/✘(server) health=✔(worker)/✘(server)
```
Explanation: `cmd/server` fails to boot due to a hard-coded fatal dependency on a real DCS login call, which cannot be satisfied locally without either a code change (out of scope for this spike) or a genuine live connection to `*.razorpay.in` (forbidden — see Incident). `cmd/worker` — built from the same repo, same config, same datastores — boots cleanly and reports healthy on both `/live` and `/ready`, confirming the MySQL/Redis/config wiring itself is correct; the blocker is specific to `cmd/server`'s stricter (fatal) error handling around DCS.
