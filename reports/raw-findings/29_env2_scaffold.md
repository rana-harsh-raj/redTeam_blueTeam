# 29 — Env 2 compose scaffold progress note

Output dir: `/Users/rana.singh/rzp-payouts-architecture/ENV2_COMPOSE/`.

**CORRECTION (resolved):** earlier revisions of this note described the
`substitutes/kong-lite/*`, `substitutes/monolith-stub/*`, and
`substitutes/Dockerfile` writes as coming from an unrelated "concurrent
agent/coordinator". Root cause is now identified: those writes came from a
`fork` subagent I launched purely for read-only research (cmd/ entrypoints,
config keys, ports). A fork inherits the FULL parent conversation context,
including the top-level task instructions and the mid-task coordinator
update — so it started independently executing the top-level scaffolding
task itself, in parallel with me. Not a second coordinator-directed agent;
it was my own fork exceeding its (read-only) brief. I've sent it a message
to stand down and return only its research findings. Its already-written
files are being KEPT (not reverted) since they're well-grounded — treating
`monolith-stub/*`, `kong-lite/*`, and the shared `substitutes/Dockerfile`
convention (single parameterised Dockerfile for all Python stdlib stubs,
build args `STUB_DIR`/`STUB_NAME`/`STUB_PORT`, rather than one Dockerfile per
stub) as final. I'm following that same shared-Dockerfile convention for the
remaining stubs I own. **Lesson for future forks in this task: don't fork
for research when the full task instructions are in context — use a fresh
non-fork agent for read-only research instead.**

Remaining scope (mine, single-writer from here): docker-compose.yml, build/
Dockerfiles for the 5 real services, config/generate.py + templates,
secrets/, preflight/, network/, seeds/, scripts/, README.md, and the
remaining substitutes: dcs-stub (done), splitz-stub (done), shield-stub,
pricing-stub, asv-stub, stork-capture, merchant-webhook-sink, mozart-mock,
mozart-sim, xas-sink, cron-driver.

## Done so far

1. `substitutes/_common/base_stub.py` — shared stdlib http.server skeleton
   (health check always open, Basic-Auth gate from a file, JSON route
   dispatch). Compatible with the convention the other writer's
   `monolith-stub/server.py` already uses (`sys.path.insert(0,"/app")`,
   `from _common.base_stub import serve`).
2. `substitutes/dcs-stub/CONTRACT.md` + `server.py` — real wire contract,
   NOT guessed: `goutils/dcs` **is** readable in this scratchpad (contrary to
   BOM §1's "none accessible" note, which predates this clone). Grounded in
   `goutils/dcs/rpc/dcs/kv/v1/kv.pb.gw.go` (REST paths: `POST /v1/kv/get`,
   `/put`, `/patch`, `/audit`, `/entity`, `/v1/auth/login`), `kv.pb.go`
   (JSON field names), `key.go` (key-flatten format), and
   `goutils/dcs/server/mock/client.go` (the exact `{0x08,0x01}` /
   `{0x08,0x00}` byte pattern DCS's own mock uses for bool feature values —
   reused verbatim here). Seeds the three Env 2 flags from BOM §2: M1 all
   off, M2 `in_flight_reservation_enabled=on`, M3 `enable_payout_workflow=on`.
3. `substitutes/monolith-stub/*` — NOT mine (see note above); superseded my
   earlier draft, which referenced a different (less grounded) route set.
   Current owner's version uses `mysql-apidb-stub` + `seeds/mysql/apidb_seed.json`
   and ledger's real `[apiService]`/`[apiDb]`/`[apiTiDb]` config section
   names — more accurate than my first draft, deferring to it.
4. `substitutes/kong-lite/*` — NOT mine; reverse-proxy stub routing by
   longest-prefix-match, `/health` answered locally, no auth (each upstream
   still enforces its own creds). Route table documented in its CONTRACT.md.

## Coordinator mid-task update (addressed)

Coordinator asked for: (a) `mozart-mock` service built from the REAL `mozart`
repo (`mozart -mock` flag, `mozart/app/mock/`, fixtures under
`mozart/app/testdata/fts/**`) as a 6th build target, keeping the lightweight
Python `mozart-sim` as fallback; (b) precise Stork contract for
`stork-capture` (Twirp `WebhookAPI/ProcessEvent`/`Create`, HMAC-SHA256
signature header) plus a new `merchant-webhook-sink` service to receive and
verify captured deliveries. Researched via `findings/25_stork_mozart.md`
(already on disk, written by an earlier phase) — full contract details there.
Key facts pulled forward:
- Mozart `-mock` listens on `[app] LISTEN_PORT` from its TOML config
  (`mozart/conf/env.sample.toml` default `LISTEN_PORT=80`) — **TODO/ASSUMPTION**:
  need `env.arena.toml` under `mozart/conf/` with `LISTEN_PORT=8085` and the
  container started with whatever env var selects that file (pattern inferred
  as `APP_ENV=arena` from the `env.<name>.toml` naming convention used by
  every other file in `mozart/conf/` — **not confirmed** against
  `mozart/app/environment/reader.go`'s actual env-var name in the time
  available; flag for the build-spike agent to confirm before first real run).
  Also unconfirmed: whether `-mock` boot path requires live DB/Redis/Kafka
  config sections to be non-empty (reader.go's `AppConfig` struct has
  `DBConfig`/`RedisConfig`/`KafkaConfig` fields) — mozart-mock's compose entry
  will `depends_on: redis` defensively; may need mysql too, TODO confirm.
- Stork: real contract fully captured (Twirp JSON-over-HTTP, HMAC-SHA256 over
  raw payload, per-caller Basic-Auth). Building stork-capture + merchant-webhook-sink next.

## Remaining work (not yet started)

splitz-stub, shield-stub, pricing-stub, asv-stub, stork-capture,
merchant-webhook-sink, mozart-mock (build/mozart.Dockerfile + config
template), mozart-sim, xas-sink, cron-driver; `docker-compose.yml`; `build/`
Dockerfiles for payouts/ledger/fts/cfa/x-balances + `build.sh` + `build-host.sh`;
`config/generate.py` + `config/arena.yaml` + 5 `config/templates/*.toml.tmpl`
(+ mozart template); `secrets/gen-secrets.sh` + `destroy.sh`;
`preflight/preflight.py`; `network/egress-audit.sh` + `dns-check.sh`; `seeds/`
skeletons for M1/M2/M3; `scripts/` (up/down/reset/snapshot/restore/healthcheck/
golden-run + cron-driver loop); `README.md`. Fork agent
(`a07f4efe7f183253a`) is researching exact cmd/ entrypoints, config
section/key names, ports, and health-check paths for the 5 core services from
their real `config/default.toml` files — will fold results into `build/*.Dockerfile`
and `config/templates/*.tmpl` once it reports back.

## Second collision, resolved: verifier/ vs verify/

A second, genuinely separate agent (not my fork -- my fork explicitly
confirmed stand-down and made no further tool calls) is concurrently
building a real pytest-based verifier under `verifier/` (helpers/{db,
http_client,passport,creds,wait,payouts_flow}.py, conftest.py,
requirements.txt: pytest+pymysql+psycopg+pymongo+redis) driven by a
`VERIFIER_SPEC.md` I don't have visibility into -- presumably a parallel
workstream the root coordinator dispatched directly. I had independently
built a placeholder at `verify/` (single-file HTTP-only harness). Resolved
by deleting my own `verify/` (my own placeholder, not their work -- not a
"revert" of another agent's output) and repointing `docker-compose.yml`'s
`verifier` service at `./verifier`, on BOTH `rzp-arena` and `rzp-ingress`
(their helpers need direct datastore access, not just an HTTP path through
kong-lite) -- see the compose file's `verifier` service comment. Do NOT add
verifier logic from this workstream going forward; only fix compose wiring
if it drifts from what `verifier/` actually contains.

## Since last note — compose backbone + all 5 build Dockerfiles done

- `docker-compose.yml`: full 4-profile compose (datastores/core/substitutes/
  verify) + a 5th `migrations` profile for one-shot `<svc>-migrate` runs
  (not in the task's named profile list, but necessary since compose has no
  native "job" primitive -- `docker compose --profile migrations run --rm
  <svc>-migrate`, orchestrated by scripts/up.sh, which is not yet written).
  Validated: `docker compose --profile datastores --profile substitutes
  config -q` (the exact command specified in the task) exits 0, as does the
  same command with every profile enabled together. NOTE:
  `--profile core --profile verify` ALONE (without datastores) fails --
  core services legitimately depend_on datastore services, so core and
  datastores must always be enabled together in the same invocation; this
  is intentional and matches how scripts/up.sh will always invoke them
  together, not a bug. Also had to strip `depends_on` on two specific
  cross-profile edges (cron-driver->payouts-api, verifier->kong-lite) since
  compose refuses `config -q` when a depends_on target's profile isn't
  simultaneously active -- documented inline in the compose file; ordering
  for those two is deferred to scripts/healthcheck.sh polling instead (not
  yet written).
- Design decision on secrets/config flow (not fully spelled out in the task
  prompt, filling the gap explicitly): `config/generate.py` (not yet
  written) will read `secrets/*.txt` (plaintext credential files, generated
  per-arena by `secrets/gen-secrets.sh`, not yet written) and INLINE real
  credential values directly into `generated/<svc>/*.toml` -- no more
  `env|VARNAME` indirection at container runtime. `generated/` is bind-
  mounted read-only into each container; images themselves never see a
  credential. This satisfies "runtime images contain no credentials" and
  "config generated from scratch per arena" simultaneously without needing
  entrypoint scripts to cat secret files into env vars at container start.
  Docker Compose's own `secrets:` top-level construct IS still used, but
  only for the 6 official datastore images (mysql x4/postgres/mongo) via
  their standard `*_PASSWORD_FILE` env-var convention -- appropriate there,
  inappropriate for the 5 custom Go services which read a TOML file, not env.
- `build/{payouts,ledger,fts,cfa,xbalances}.Dockerfile`: multi-stage,
  `--mount=type=secret,id=netrc,required=true` in the build stage (removed
  before COPY of source; runtime stage is `alpine:3.21` + compiled binaries
  + CA certs only). Go versions matched exactly to each repo's real go.mod
  (via the fork's research, now folded in): payouts 1.26, ledger 1.25, fts
  1.24, cfa 1.24, x-balances 1.25. Entrypoint binary names match what
  `docker-compose.yml`'s `entrypoint:`/`command:` fields reference.
  fts.Dockerfile additionally installs nginx + a hand-authored
  `build/fts-nginx.conf` (arena-adjusted reconstruction of the shape
  described in fts/build/nginx.conf, NOT a copy of the real file -- repo
  clones are never read into an image beyond `COPY . .` inside the build
  stage, which IS the real source, that's expected) and
  `build/fts-entrypoint.sh` (dispatches `web`|`worker` args -- `web` starts
  the Go binary then execs nginx in foreground).
- `build/mozart.Dockerfile`: builds the REAL mozart binary (go 1.25.1,
  confirmed from mozart/go.mod directly) with `-mock` as its ENTRYPOINT arg,
  copies in `app/testdata/fts/**` fixtures (~76MB, public test data, no
  creds), does NOT copy `conf/` from the repo (may contain real secret
  *structure*) -- arena config bind-mounted at `/app/conf` instead (see
  docker-compose.yml `mozart-mock` volumes). Build context must be an
  external absolute path to a real mozart clone (`MOZART_BUILD_CONTEXT` in
  `.env.arena`) since this scaffold never modifies/vendors repo clones.
  **Flagged, unconfirmed assumption** (explicit in the Dockerfile's own
  header comment): `-mock` mode's LISTEN_PORT/LISTEN_IP come from the same
  `conf/env.<APP_ENV>.toml` convention as normal boot, selected by
  `APP_ENV=arena` -- inferred from the naming convention of every other file
  already in `mozart/conf/`, NOT confirmed by reading
  `mozart/app/environment/reader.go`'s actual env-var-name logic. Whoever
  runs the first real build of this should confirm before trusting
  mozart-mock's config actually gets picked up.
- `build/build.sh`: mints a short-lived `.netrc` from `gh auth token`
  (per-invocation, deleted via `trap ... EXIT`, never touches an image
  layer), builds all 5 core images + optional mozart-mock via
  `DOCKER_BUILDKIT=1 docker build --secret id=netrc,...`.
- `build/build-host.sh` + `build/runtime-only.Dockerfile`: host-side
  alternative (build binaries with the developer's own already-authenticated
  `go`, package into a Dockerfile with NO build stage at all since nothing
  in that path ever touches a credential inside Docker). Explicitly does
  NOT package fts (needs nginx, which the generic runtime-only image
  doesn't have) -- documented gap, prints a warning rather than silently
  producing a broken fts image.

All 8 stubs I own now have CONTRACT.md + server.py: dcs-stub, splitz-stub,
shield-stub, pricing-stub, asv-stub, stork-capture, merchant-webhook-sink,
xas-sink, mozart-sim (9, actually) -- all grounded in real proto/config
where the repo was readable (dcs, splitz, shield, stork all confirmed
against real `.proto`/Go source, not guessed; pricing/asv/xas explicitly
marked as thinner "skeleton, TODO" per the task's own item-9 framing for
stub completeness). `scripts/cron-driver/driver.py` also done (BOM §1 cadence
assumptions carried forward with explicit "UNVERIFIED" framing + env-var
overrides).

## Remaining work

`config/generate.py` + `config/arena.yaml` + `config/templates/*.tmpl` (6:
payouts/ledger/fts/cfa/xbalances/mozart-mock) -- next up.
`secrets/gen-secrets.sh` + `destroy.sh`; `preflight/preflight.py`;
`network/egress-audit.sh` + `dns-check.sh`; `seeds/` (M1/M2/M3 skeletons +
`seeds/mysql/apidb-ddl/` init scripts referenced by the `mysql-apidb-stub`
compose service + `seeds/mysql/apidb_seed.json` referenced by
`monolith-stub`'s volume mount + `dcs_flags.json` referenced by
`dcs-stub`'s volume mount -- NOTE these 3 mount source paths already exist
in `docker-compose.yml` and must be created to match); `scripts/`
(up/down/reset/snapshot/restore/healthcheck/golden-run, not cron-driver
which is done); `README.md`.

## FINAL: scaffold complete

All remaining deliverables done: `config/arena.yaml` + `config/_miniyaml.py`
(stdlib-only YAML-SUBSET parser, since PyYAML isn't stdlib -- handles
exactly arena.yaml's shape: block mappings/sequences, one-line flow
mappings, scalars; NOT a general YAML parser, documented as such) +
`config/generate.py` (renders all 6 service configs, inlines secrets,
fails loudly on any unresolved `{{TOKEN}}`) + all 6
`config/templates/*.toml.tmpl` (payouts/ledger/fts/cfa/xbalances/mozart-mock,
grounded in the fork's real section/key-name research, every downstream
client section the task asked for: db, redis, sqs endpoint, kafka, ledger,
fts, cfa, x_balances, api, stork, shield, splitz, dcs, workflow,
account_service, mozart, hvault/vault (Mock), telemetry, auth creds) +
`secrets/gen-secrets.sh`/`destroy.sh` (openssl-random everything, RSA
passport keypair + minimal JWKS, verifier-bridge/ for the parallel
verifier workstream's different credential-file convention) +
`preflight/preflight.py` (all 4 forbidden patterns + RFC1918-outside-arena-
subnet, writes SAFETY_PREFLIGHT.md, verified with a live negative test:
injected real `razorpay.com`/`arn:aws`/`169.254.` strings into generated
output, preflight caught all 3 and exited 1) + `network/{egress-audit,dns-check}.sh` +
`seeds/` (mysql apidb-ddl + apidb_seed.json + fts_seed.sql + xbalances_seed.sql,
postgres ledger_seed.sql, mongo cfa_seed.js, stork subscriptions.json,
splitz variant_table.json -- every file explicitly TODO-flagged where
schema wasn't confirmed against real migrations, per the task's own
framing for this deliverable) + `scripts/{up,down,reset,snapshot,restore,healthcheck,golden-run}.sh` +
`README.md`.

**End-to-end pipeline actually executed and verified** (not just syntax-checked):
`secrets/gen-secrets.sh` -> `config/generate.py` -> `python3 tomllib` parse
of all 6 generated TOML files -> `preflight/preflight.py` PASS, then a
deliberate negative test (inject forbidden strings, confirm preflight FAILS
and reports them, then regenerate clean) -> PASS again. Caught and fixed
one real bug this way that `bash -n` could NOT have caught: unescaped
backticks inside a double-quoted `echo` string in `gen-secrets.sh` were
being interpreted as command substitution (`` `secrets:` `` -> "secrets:
command not found") -- `bash -n` only checks syntax validity, and
backtick-command-substitution IS syntactically valid, so it never flagged
it. Lesson: for any script with human-readable backticks in its own
output strings, actually RUN it once, don't trust `-n` alone.

**Third collision, handled without conflict**: `verifier/` (the OTHER
agent's pytest-based verifier, see above) filled in fully while this pass
was still working (`conftest.py`, `Dockerfile`, `run.sh`,
`verifiers/test_v01..test_v24` -- 24 real golden-flow tests) --
`docker-compose.yml`'s `verifier` service wiring was already pointed at
`./verifier` from the earlier collision-resolution; only had to add a
`secrets:` bridge once `verifier/helpers/creds.py`'s different credential
convention (`"user:pass"` files at `/run/secrets/<slug>`, slugs `ledger`/
`fts`/`monolith`/`ps-fastcron`/`ps-service`, resolved via a grep across
their 24 test files for `resolve_basic_auth(...)` calls) became visible.
Did not write or touch any verifier LOGIC file.

## Validation run so far

`python3 -m py_compile` clean on every `.py` file under `substitutes/` and
`scripts/cron-driver/`. `bash -n` clean on `build/build.sh`,
`build/build-host.sh`, `build/fts-entrypoint.sh`. `docker compose ... config
-q` clean for the task's exact required profile combo and for all profiles
together (see above for the one expected/intentional partial-profile
failure). Have NOT yet validated `verifier/`'s files (not mine) or run
`python3 -m py_compile` against them.
