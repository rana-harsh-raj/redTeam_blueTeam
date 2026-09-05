# Buildability Spike — payouts, ledger, fts, cfa, x-balances

Env2 buildability spike across five Go services from the payouts/ledger monorepo family. Each service was compiled, given a local datastore (MySQL/Postgres/Mongo + Redis as needed) on a dedicated docker network, migrated, and booted against a from-scratch, fully-localized "arena" config, then health-checked. Work was parallelized across five independent sub-agents (one per service, isolated repo/working dirs, distinct docker ports) coordinated by one root agent; this file merges their per-service findings (`28_build_spike_<svc>.md`, kept intact alongside this file) into one report.

**No repo clone was modified.** All generated code went either into an already-gitignored directory inside the clone (payouts `rpc/`, ledger `rpc/`+`proto/`) or was pre-committed already (cfa, x-balances ship generated `rpc/` in git). One repo (payouts) had a scoped, clearly-labeled bonus experiment applied to a **copy** of the repo, never the clone. `git status` on every clone is clean.

## Summary table

| Service | Compile | Migrate | Boot | Health | Notes |
|---|:---:|:---:|:---:|:---:|---|
| **payouts** | ✔ | ✔ (34/34, 32 tables) | ✔* | ✔* | Baseline (pristine clone) panics deterministically at an unconditional DCS login step — zero network calls, boot incomplete. A scoped bonus experiment in a **repo copy** (one-line legitimate SDK override + local stub) achieved a full boot and green health check. *(✔ reflects the bonus-experiment result; baseline alone would be boot=✘/health=✘.)* |
| **ledger** | ✔ | ✔ (51/51, 56 tables) | ✔ | ✔ | Clean end-to-end. `api` passes Twirp health (`SERVING_STATUS_SERVING`); `worker` reaches its poll loop safely (local placeholder only) after switching the SQS driver. |
| **fts** | ✔ | ✔ (50/50, 46 tables) | ✔ | ✔ | Clean end-to-end, no incidents. `/status` returns `db:1, mutex:1`. Widest env-var surface of the five (~65 `env\|VAR` placeholders to resolve). |
| **cfa** | ✔ | ✔ (4/4 Mongo migrations, 3 collections + tracking) | ✔ | ✔ | Clean final boot, but **two real outbound calls to real Razorpay/AWS infrastructure occurred mid-spike** before being caught (see Cross-cutting findings). Also required an out-of-brief MySQL datastore (`APIStore`) as a hard boot dependency alongside Mongo. |
| **x-balances** | ✔ | ✔ (1/1, 2 tables) | ✔ (worker) / ✘ (server) | ✔ (worker) / ✘ (server) | `cmd/worker` boots clean and healthy. `cmd/server` has a **fatal, unconditional** DCS-login dependency with no config-level bypass — cannot boot without a code change (out of scope) or a real network call (forbidden). One real outbound call to `dcs-live.dev.razorpay.in` occurred before this was understood (got a live 404). |

All five: compile ✔ (5/5), migrate ✔ (5/5). Boot/health: 4/5 fully clean (ledger, fts, cfa, payouts-via-bonus-experiment); x-balances partially (worker only); payouts' own *baseline* boot is a documented, safe, zero-network-call failure at the same class of blocker that fully blocked x-balances' server.

## Cross-cutting findings (apply to more than one service)

### 1. `goutils/dcs` ignores `Mock=true` and logs in for real at client construction — hit by 3 of 5 services

`github.com/razorpay/goutils/dcs` (seen at v1.5.2 in cfa/x-balances, v1.7.1 in cfa, v1.7.3 in payouts) performs a real `POST .../v1/auth/login` against a hardcoded, env-derived real Razorpay host **inside `dcs.New()`/`Client.New()`, unconditionally, regardless of the `Mock` config flag** — `Mock` is stored but genuinely never read at this step in every version encountered. There is no config-level way to redirect the login URL for the common case (single `Mode`) — the SDK's only override path (`ServerURL`, consulted when `len(Modes) != 1`) is unreachable when a service sets exactly one mode, which all three did.

Confirmed real outbound calls (both caught within seconds, both got non-2xx responses, no data of consequence exchanged):
- **cfa**: `POST https://dcs-live.dev.razorpay.in/v1/auth/login` → live `403`.
- **x-balances**: `POST https://dcs-live.dev.razorpay.in/v1/auth/login` → live `404`.
- **payouts**: avoided by deliberately setting an *invalid* `dcs.Env` value so the SDK's own `GetEnv()` fails locally before ever reaching the login step (zero network calls) — this is what "boot" means for payouts' baseline: a documented, safe, non-booting failure, not a clean pass.

Per-service outcome once understood:
- **cfa**: set `Dcs.env` to an unrecognized value → SDK fails inside `GetEnv()` before dialing; cfa's own caller treats this as non-fatal, server continues booting and reaches a clean health check.
- **payouts**: same technique for the documented baseline; a bonus repo-copy experiment instead called the SDK's own exported `SetContextUrl()` override before `dcs.New()`, paired with a local stub, to get a full green boot.
- **x-balances**: `cmd/server` calls `dcs.NewClient` unconditionally and `log.Fatalf`s on any error — including the deliberately-invalid-env trick, since x-balances' own code doesn't expose a way to make `GetEnv()`'s failure non-fatal without a code change. `cmd/worker` treats the same failure as non-fatal (`log.Errorf`, continues with a nil DCS client) and boots clean.
- **ledger**: does not import `goutils/dcs` at all — confirmed via grep, not applicable.

**This is the report's single most important finding**: a `Mock=true`/similar-looking config flag does not reliably prevent real outbound calls from shared internal SDKs. Static config review (grepping for `Mock`/`razorpay.in` strings) is not sufficient on its own to guarantee no egress — live boot-log inspection, ideally under a defense-in-depth network block, is required.

### 2. The sandbox does NOT block real internet egress from a host-run process

`rzp-arena` was created with `docker network create --internal`, but that only removes egress from **containers placed on that network** — it does nothing to a Go binary run directly on the host (which is how every service's app binary ran in this spike, per the brief's instruction that binaries run on the host for now). x-balances' first `cmd/server` boot attempt proved this empirically: a real TLS connection to `dcs-live.dev.razorpay.in` succeeded and returned a real HTTP 404. Once this was discovered, all five agents added a process-local proxy blackhole as defense-in-depth before further boot attempts:
```
export HTTP_PROXY=http://127.0.0.1:1
export HTTPS_PROXY=http://127.0.0.1:1
export NO_PROXY=127.0.0.1,localhost,127.0.0.0/8,::1
```
Go's default `http.Transport` honors these; any call that missed a config rewrite now fails closed (`connection refused`) instead of reaching a real host. Gotcha hit by both payouts (avoided) and x-balances (hit and fixed): if a service's own grpc-gateway self-dials its own grpc server using a bare `:PORT` address (no host), `NO_PROXY`'s host-matching doesn't exclude it and the service's own health-check traffic gets blackholed too — fix by setting an explicit `127.0.0.1:PORT` in whatever config controls that self-dial, not by dropping the proxy.

**Recommendation for any future/longer-running version of this spike**: don't rely on `--internal` docker networks or config review alone. Run host binaries under a real egress-blocking mechanism from the start (the proxy blackhole above, a pf/nftables rule, or a network namespace) rather than adding it reactively after a real call has already gone out.

### 3. The plain `"sqs"` queue driver in `goutils/worker` ignores the `Endpoint` config field and always dials real AWS

Confirmed independently by ledger and cfa (both read the vendor source): `goutils/worker/v2` and `/v3`'s plain `"sqs"` dialect never calls `.WithEndpoint()`, so a locally-pointed `Queue.Sqs.Endpoint` is silently ignored and the client always targets a real regional AWS endpoint. Only the separate `"sqs_local"` dialect honors `Endpoint`. cfa's worker actually made repeated real `ReceiveMessage` calls to `sqs.us-east-1.amazonaws.com` (403/AccessDenied each time) before this was caught and fixed with a `QUEUE_DRIVER=sqs_local` override. ledger caught and fixed this proactively (by reading the source before booting) and never made a live call. cfa additionally found that its own `cmd/server` code hardcodes rejection of any driver other than the literal `"sqs"` (`"unsupported queue driver"` fatal), so the fix could only be applied to the worker binary, not the server config — safe for the server because it never actively polls SQS at boot. x-balances sidestepped the whole class of bug by using the first-class `"inmemory"` driver instead of SQS entirely; payouts has no `pkg/queue` package at all, so it's not applicable there.

### 4. `docker network create --internal` silently drops host port publishing — hit by all five services independently

`docker run --network rzp-arena -p 127.0.0.1:PORT:CONTAINER_PORT ...` (or `--internal`-only networks in general) accepts the `-p` flag with no error but never actually binds it — `NetworkSettings.Ports` comes back empty/null, no `docker-proxy` process starts, and the host can't reach the container at all. Root cause: an `--internal` network has no gateway configured, and Docker's host-port DNAT path requires one. Every agent independently found the same fix: create the datastore container on an ordinary bridge network first (where `-p` publishing works normally), then `docker network connect rzp-arena <container>` afterward so it's still reachable from other arena containers per the spike's convention. This doesn't grant any extra egress — the official MySQL/Postgres/Mongo/Redis images make no outbound calls at runtime regardless of which networks they're attached to.

### 5. Config loaders across this service family share a "default-then-env-overlay" pattern, not "one file wins"

payouts, ledger, cfa, and x-balances all load `config/default.toml` first unconditionally, then overlay `config/<ENV>.toml` on the same struct — anything the env file doesn't repeat is silently inherited from `default.toml`. This mattered concretely: cfa's and ledger's `default.toml` each still contain 1-2 real `razorpay.in`/`razorpay.com` hosts (`App.HostName`, `Wda.default_http_client_url`) that would leak through underneath an env file that didn't also override them. Every agent copied `default.toml` byte-for-byte (never edited) alongside its new `arena.toml` and verified functionally (not just by inspection) that the overlay actually wins for every host-bearing key. fts's loader is closer to true override behavior but still requires its own `env.default.toml`+`holiday.toml` base files to co-exist with the env override.

### 6. GOPRIVATE / go.sum module-download notes (reconciled)

- **Root cause / fix confirmed**: `GOPRIVATE` is not set in the ambient shell in this sandbox. Every agent exported `export GOPRIVATE=github.com/razorpay/*` explicitly per command/session; with it set, `go mod download`/`go build` succeeded cleanly for all five repos, resolving via direct git + the pre-authenticated `gh` credential helper.
- **payouts / `github.com/razorpay/ifsc/v2@v2.0.43` checksum mismatch**: an early background `go mod download` run *without* `GOPRIVATE` hit `checksum mismatch` for this module (downloaded hash vs. go.sum hash disagreed, reproducing even via `proxy.golang.org` directly — i.e. a genuinely stale go.sum entry, likely an upstream tag re-point, not just a missing-GOPRIVATE artifact). The documented, rule-compliant fallback (never applied to the pristine clone) is: `rsync -a --exclude=.git` the repo into a scratch copy, delete the two `github.com/razorpay/ifsc/v2 v2.0.43` lines from that copy's `go.sum`, and rebuild with `GOFLAGS=-mod=mod` so Go re-fetches and re-records a fresh hash. In practice, the payouts agent's own `GOPRIVATE`-exported `go build` run against the shared, already-warmed module cache resolved `ifsc/v2` cleanly without needing this fallback — flagged here as a real (if minor) upstream go.sum-hygiene concern for payouts' CI, not as something this spike had to work around.
- **fts / "missing revision" goutils and relay errors**: an early background download (again, without `GOPRIVATE`) failed hard with dozens of `missing go.mod at revision ...`/`unknown revision refs/tags/...` errors for various `goutils/*` and `relay/*` submodules, plus a module-cache `.lock` error suggesting a race from a concurrent/interrupted download. The fts agent's own clean, `GOPRIVATE`-exported `go mod download` succeeded on the first try and never reproduced the failure, so it cannot fully confirm whether the original cause was purely missing-GOPRIVATE or a genuine transient cache race (most likely both contributed — GOPRIVATE gets the fetch onto the correct direct-git path, and the interrupted concurrent download left a stale lock/partial state that a clean retry doesn't hit). Either way: **a clean `GOPRIVATE`-exported retry resolved it**, no repo/go.sum changes were needed.
- ledger, cfa, x-balances: `go mod download` was clean on first try in every agent's own run, no checksum or revision issues encountered.

## Datastores stood up (final state, all containers left running on `rzp-arena` + a per-service bridge network)

| Service | Datastore | Image | Host port | Container |
|---|---|---|---|---|
| payouts | MySQL 8 | `mysql:8` | `127.0.0.1:23306` | `payouts-mysql` |
| payouts | Redis 7 | `redis:7` | `127.0.0.1:26379` | `payouts-redis` |
| ledger | Postgres 16 | `postgres:16` | `127.0.0.1:25432` | `ledger-postgres` |
| ledger | Redis 7 | `redis:7` | `127.0.0.1:26382` | `ledger-redis` |
| fts | MySQL 8.0 | `mysql:8.0` | `127.0.0.1:23307` | `fts-mysql` |
| fts | Redis 7 | `redis:7` | `127.0.0.1:26380` | `fts-redis` |
| cfa | MongoDB 7.0 | `mongo:7.0` | `127.0.0.1:27117` | `cfa-mongo` |
| cfa | MySQL 8.0 (out-of-brief, hard boot dep) | `mysql:8.0` | `127.0.0.1:23308` | `cfa-mysql` |
| x-balances | MySQL 8 | `mysql:8` | `127.0.0.1:23408` | `xbal-mysql` |
| x-balances | Redis 7 | `redis:7` | `127.0.0.1:26481` | `xbal-redis` |

No LocalStack or Kafka containers were needed for any service — every agent confirmed (by reading boot-preload code, not assuming) that SQS/Kafka are either not dialed eagerly at boot, gated off via a disabled-by-default flag, or (payouts) not present in the codebase (`pkg/queue` doesn't exist there) or (x-balances) avoided entirely via the `inmemory` queue driver. Final port allocations deviated slightly from the pre-assigned plan in `env2-spike/PORT_ALLOCATION.txt` (x-balances moved from 23308/26381 to 23408/26481, cfa added an unplanned 23308 MySQL) due to a couple of real-time collisions between parallel agents — actual ports are the table above.

## Startup dependencies observed (per service — config key → external host → what a stub would need to return)

**payouts** (full table in `28_build_spike_payouts.md` "Startup dependencies observed"): `db`/`redis` need real local instances (used); `dcs` needs a `POST /v1/auth/login` → `{"access_token": "..."}` response (stubbed in the bonus experiment only); `elasticsearch`/`ccsdk` need `Mock=true` (set); `mozart`/`xas`/`x_balances`/`cfa`/`raven`/`hvault`/`stork`/`ledger`/`shield`/`ups`/`splitz` are all preloaded but construct only lazy HTTP clients — no stub needed at boot; `account_service` gRPC dial is non-blocking; `events.kafka` needs `enabled=false` (set) to avoid a real Sarama producer construction; `queue.sqs` is entirely unused (no `pkg/queue` package exists).

**ledger**: `db`/`redis` real local instances (used, both pinged synchronously at boot); `authz` needs `mock=true` (also flips Passport); `apiService`/`wda`/`splitz` need `mock=true`; `queue.driver` needs `sqs_local` (not plain `sqs`) plus an unreachable local `Endpoint`; SNS/pubsub ARNs are inert identifier strings gated off by `appMode="test"`; telemetry OTLP exporter is non-blocking.

**fts**: `database`/`mutex`(redis)/`queue`(redis) need real local instances; `beam` needs `MOCK=true`; `feature.POLL_DATA_FROM_RELAY_SERVICE` needs `false` (else a Panic-on-error relay bootstrap call fires at boot); `kafka_producers.fire_transfer_status` needs `enabled=false`; `xas`/`splitz`/`razorx`/`lumberjack`/`sentry` all lazy/non-fatal, no stub needed; 8 webhook URLs rewritten for hygiene (never dialed at boot).

**cfa**: `Store.MongoDB`/`APIStore.Sql` need real local instances (both hard, unconditional `log.Fatalf`-on-error deps for `cmd/server`/`cmd/worker`); `Dcs` needs an invalid `env` value (documented safe-fail, since `Mock=true` alone does **not** prevent a real login call — see Cross-cutting #1); `Queue.Sqs` needs `sqs_local` driver + `Endpoint` override for the worker only (server hardcodes rejecting anything but literal `"sqs"`, but never actively polls, so is safe left as-is); `BinService`/`VaultService`/`TokenService`/`ApiService` need `Mock=true` (all but `VaultService`'s client actually check it before dialing — `VaultService` is safe only because its BaseURL is unreachable, not because Mock gates it); `ElasticSearch` needs `Enabled=false`.

**x-balances**: `Store.Sql`/`Cache.Redis`/`RateLimiter.Redis` need real local instances; `APIService`/`PayoutService`/`BankingAccountsConfig`/`MozartConfig`/`AccountServiceConfig` need `Mock=true` (all verified to actually gate the real HTTP/gRPC construction, unlike DCS); `LedgerConfig` has no Mock flag at all but is safe because its client is lazy; `Queue.Driver` needs `inmemory` (avoids the SQS-ignores-Endpoint trap entirely); `DCSConfig.Mock=true` does **not** prevent `cmd/server`'s fatal unconditional login call (see Blockers) — `cmd/worker` treats the same failure as non-fatal.

## Blockers

1. **`goutils/dcs`'s `Mock` flag is non-functional at the client-construction step, across every SDK version encountered (v1.5.2, v1.7.1, v1.7.3)** — real login calls were made (and caught) from cfa and x-balances before this was understood; payouts avoided it only via a deliberate invalid-env trick. This is a shared-library issue affecting at least 3 of 5 services in this family and worth escalating to whoever owns `goutils/dcs`, independent of this spike.
2. **`x-balances cmd/server` cannot fully boot in this spike without a code change or a real network call** — its DCS init is `log.Fatalf`-on-error with no non-fatal path (unlike `cmd/worker`, which already has the safe pattern). `cmd/worker` (same repo, same config, same datastores) boots and passes health cleanly, proving everything else about the wiring is correct.
3. **`payouts`' baseline (pristine, unmodified) boot does not reach a listener** — it panics safely and deterministically at the same DCS step, for the same underlying reason as #1/#2. A bonus, clearly-labeled repo-copy experiment (not counted as "the" baseline result) showed that a one-line legitimate SDK override is sufficient to get a full green boot once DCS is out of the way, suggesting the rest of payouts' boot path is otherwise sound.
4. **`goutils/worker`'s plain `"sqs"` dialect ignores the `Endpoint` override and always dials real AWS** — hit for real by cfa's worker (caught within ~12s), proactively avoided by ledger and x-balances once understood. Worth flagging to whoever owns that shared queue library: `Endpoint` looking like a working local override while silently doing nothing is a footgun.
5. **cfa needed an out-of-brief MySQL datastore** (`APIStore`) as a hard, unconditional boot dependency alongside its documented Mongo dependency — the task brief's datastore list for cfa (Mongo/Redis only) was incomplete relative to what `internal/server` actually requires to boot.
6. **payouts production code (`enrichment_strategy.go`) imports `e2e/utils`**, whose `init()` chain requires `config/e2e.toml` to exist even for a normal `cmd/api` boot — worked around by copying that file in, but looks like an accidental prod/test coupling worth reporting upstream.
7. **ledger's `cmd/migration` default `-dir` points at a directory that doesn't exist in a fresh checkout** — it's populated only by a CI-only step (`.github/workflows/review_ci.yml` copying `pg_migrations/*` into `internal/database/migrations`), undocumented anywhere else. Reproduced manually for this spike.
8. Minor: cfa's own `slit.toml` has `ElasticSearch.Mock` typed as the TOML string `"true"` against a Go `bool` field — likely a latent typo in that file, not something this spike needed to fix (`Enabled=false` makes it moot either way).

## Deliverables

- Per-service detailed findings (build commands, full config diff tables, exact docker/migration/boot commands, boot logs referenced): `findings/28_build_spike_payouts.md`, `28_build_spike_ledger.md`, `28_build_spike_fts.md`, `28_build_spike_cfa.md`, `28_build_spike_x-balances.md` (kept as-is alongside this merged summary).
- Arena config files, stub scripts, and boot logs: `env2-spike/<svc>/config/` and `env2-spike/<svc>/*.log` for each of the five services.
- Built binaries: `env2-spike/<svc>/bin/`.
- This file and `env2-spike/README-spike.md` (environment overview + how to reproduce/tear down).
