# ENV2_COMPOSE — Env 2 runnable implementation scaffold

For Twin v1, use [the current setup and execution guide](../TWIN_V1_README.md).
It covers admitted fresh source copies, generated fixtures, scoped secret volumes,
both internal networks, the loopback bridge and audited verification. The original
scaffold description below is retained as background; its earlier topology and
seed instructions are superseded by that guide.

A docker-compose scaffold for **Env 2: public API payout with idempotency
and webhooks** (`INITIAL_PAYOUTS_ENVIRONMENT_BOM.md` §2), the first
recommended sandbox environment in the Payouts architecture closure. Real
services (payouts, ledger, fts, cfa, x-balances) built from their own repo
clones; everything else (`api` monolith, DCS, Splitz, Shield, Stork, Mozart,
ASV, Kong) is a small F2 substitute under `substitutes/`.

This scaffold does **not** modify or vendor any repo clone. It builds
images FROM external clone paths you provide, and generates all runtime
config from scratch per arena.

## Safety model (read this before running anything)

- **`rzp-arena`** (docker network) is `internal: true` — no container on it
  can reach the real internet or any real `razorpay.{com,in,vpc}` host, by
  construction (Docker enforces this at the network level, not just by
  convention).
- **`rzp-ingress`** is the only network with a path to the host. **`kong-lite`**
  is the only container a developer's shell/test runner talks to from
  outside; `verifier` also joins both networks (it's a container-internal
  test harness with its own datastore-assertion helpers, not the host — see
  `docker-compose.yml`'s comment on that service).
- **No credentials in any image.** `build/*.Dockerfile` build stages take a
  git credential only via a BuildKit `--secret` mount, gone before the
  runtime stage starts. Runtime images contain compiled binaries + CA certs
  only.
- **Config generated from scratch per arena.** `config/generate.py` renders
  `generated/<svc>/*.toml` from `config/templates/*.tmpl` +
  `config/arena.yaml` + `secrets/*.txt`, wiping `generated/` first. Every
  `generated/` file is bind-mounted **read-only** into its container — never
  baked into an image.
- **Secrets generated per arena, destroyed at teardown.** `secrets/gen-secrets.sh`
  mints everything fresh (`openssl rand`); `secrets/destroy.sh` (called by
  `scripts/down.sh`) shreds it all, including `generated/` (which had
  credentials inlined).
- **Preflight is a hard gate.** `preflight/preflight.py` scans every
  effective config value (rendered TOML + docker-compose.yml + .env.arena +
  the process environment) for `razorpay\.(com|in|vpc)`, `amazonaws\.com`,
  `arn:aws`, `169\.254\.`, and RFC1918 addresses outside the arena's own
  subnets. `scripts/up.sh` runs it right after config generation and treats
  a non-zero exit as a hard stop.
- **No host home/docker-socket/credential mounts anywhere** — every
  `volumes:` entry in `docker-compose.yml` is either a named Docker volume,
  a path inside this scaffold's own tree, or a `generated`/`secrets` file.

## Layout

```
ENV2_COMPOSE/
  docker-compose.yml       4 profiles (datastores/core/substitutes/verify)
                            + a 5th `migrations` profile for one-shot runs
  .env.arena                ports/tags/subnets -- NO credentials
  build/                    Dockerfiles for the 5 real services + mozart-mock,
                             build.sh (BuildKit secret), build-host.sh (host build)
  config/
    arena.yaml               topology + DCS flags + Splitz variants (NO credentials)
    _miniyaml.py              stdlib-only YAML-SUBSET parser (arena.yaml only, not general YAML)
    generate.py               renders generated/<svc>/*.toml from templates + secrets
    templates/*.toml.tmpl     one per service, {{TOKEN}} placeholders
  secrets/                  gen-secrets.sh, destroy.sh (gitignored output)
  preflight/preflight.py    the hard gate described above
  network/                  egress-audit.sh, dns-check.sh
  seeds/                    M1/M2/M3 synthetic-merchant seed skeletons (SQL/JS/JSON)
  substitutes/               10 Python-stdlib F2 stubs + kong-lite + mozart-sim,
                             each substitutes/<name>/CONTRACT.md + server.py
  verifier/                  golden-flow pytest suite (separate workstream, see below)
  scripts/                   up/down/reset/snapshot/restore/healthcheck/golden-run
  generated/                 config/generate.py's output (gitignored)
```

## Quickstart

```bash
cd ENV2_COMPOSE

# 1. BUILD phase (host holds the GitHub credential; runtime images get only binaries).
#    REPOS_ROOT = dir with payouts, ledger, fts, cfa, x-balances (payouts/cfa/x-balances as the
#    patched copies described in reports/ENV2_BUILD_RUNBOOK.md §2). Proven path: build-host.sh.
REPOS_ROOT=/path/to/repos-root GOFLAGS=-mod=mod bash build/build-host.sh all

# 2. Bring the whole arena up (secrets -> config -> preflight -> datastores
#    -> migrations -> seeds -> substitutes -> core -> health wait). Re-runnable; keeps secrets.
bash scripts/up.sh

# 3. Run the golden-flow verifier (24 verifiers) with the egress capture running
bash scripts/golden-run.sh --with-egress-audit

# 4. Tear down (destroys secrets + generated config + volumes)
./scripts/down.sh
```

Milestone 1 additions (see [TWIN_V1_README.md](../TWIN_V1_README.md) for full detail):

```bash
# Kafka status-transport scenarios (I70/I71) -- kafka route profile only
bash scripts/scenarios.sh --with-egress-audit kafka [--case shared_source_failure|direct_after_shared|failed_dropped_direct|failed_dropped_shared|reversed_dropped_direct]

# Per-run boot fingerprint (also invoked by up.sh; copied into every run dir)
python3 scripts/fingerprint.py

# Generated machine-readable acceptance gate (schema 2), replacing the hand-authored one
python3 scripts/local-acceptance.py MANIFEST.json [--output reports/implementation/local-acceptance.json] [--summary reports/implementation/LOCAL_ACCEPTANCE_GENERATED.md]

# Rebuild one core image and refresh its build-evidence directory
python3 build/record-rebuild.py <service> --base-evidence <dir> --repos-root <dir>
```

The arena's only host-reachable entrypoint is
`http://localhost:${KONG_LITE_HOST_PORT:-18080}` (kong-lite).

## Process model actually running (after the golden run)

- payouts: `payouts-api`, 14 `payouts-worker-<job>` (one job per process, `PAYOUTS_WORKER_NAME`), 2 Kafka consumers (`PAYOUTS_CONSUMER_TASK_NAME`).
- fts: `fts-web` + 13 `fts-worker-*` machinery workers, one per `[queue.worker_queue_map]` key (`-command=<key>`, e.g. `rbl::imps::initiate_transfer`).
- cfa: `cfa-server`, `cfa-worker-contact`, `cfa-worker-fa` (`CFA_WORKER_QUEUE_NAME`).
- ledger: `ledger-api`, `ledger-worker`, `ledger-scheduler` (one-shot). x-balances: server + worker.
- substitutes: kong-lite (+ `/_arena/mint`), monolith-stub (merchant/fund-account fixtures, pricing, `create_fta` relay to fts, FTS-webhook relay to PS with the monolith status map, `internal_balances_queued` from ledger), bankingaccounts-stub, dcs/splitz/shield/pricing/asv stubs, stork-capture, sinks, mozart-sim, cron-driver.
- Verifier: `bash scripts/golden-run.sh --with-egress-audit`; waits scaled by `ARENA_WAIT_SCALE` (default 6).

## Two things this scaffold does NOT do for you

1. **Mozart's real binary is CONFIRMED unbuildable in this environment.**
   `go build` of a real `../mozart` clone fails on the private module
   `github.com/razorpay/integrations-utils` (404 for the build identity
   available here) — not merely "unconfirmed," actually tried and blocked.
   `mozart-sim` (Python, stdlib-only) is therefore the **PRIMARY** Mozart
   substitute (`config/generate.py`'s `ARENA_MOZART_IMPL` defaults to
   `mozart-sim`; see `substitutes/mozart-sim/CONTRACT.md`), grounded
   against 6 scenarios cross-checked directly against real
   `mozart/app/testdata/fts/**` fixtures and `fts/internal/providers/
   mozart/error_code.go`'s status mapping. `mozart-mock` (the real binary)
   is kept defined under an opt-in `mozart-real` compose profile — not
   started by `substitutes` or `core` — in case a working
   `integrations-utils` credential becomes available later.
2. **A handful of DB schemas are best-effort skeletons, not verified
   migrations.** `seeds/mysql/apidb-ddl/00_init.sql`,
   `seeds/mysql/fts_seed.sql`, `seeds/postgres/ledger_seed.sql`,
   `seeds/mongo/cfa_seed.js` are all explicitly marked `TODO: confirm
   against the real migration files` — the underlying repos' migration
   directories weren't fully read in this pass. `scripts/up.sh`'s seed step
   will fail loudly (not silently) if a table name is wrong; fix the
   specific seed file, not the orchestration script. A separate,
   better-grounded seed set (`payouts.sql`/`fts.sql`/`ledger.sql`/
   `xbalances.sql`, keyed on the correct `ARENAM0000000N` merchant ids —
   see next section) landed from a parallel fixtures workstream at
   `/private/tmp/.../scratchpad/env2-seeds/` during this pass; reconciling
   it with the skeletons above is a follow-up, not done here.
3. **DCS is effectively inert against the pristine payouts-api/cfa-server
   binaries — a confirmed, not assumed, gap.** Both `payouts/pkg/dcs/
   client.go` and `cfa/internal/dcsservice/client.go` build their
   `goutils/dcs` client with a single `Mode`, which makes the SDK derive
   the login host from a hardcoded env→real-hostname map, **never**
   consulting the `[dcs] ServerURL`/`[Dcs] ServerURL` key this scaffold
   renders pointing at `dcs-stub`. The safe, confirmed workaround (used by
   both `findings/28_build_spike_payouts.md` and
   `findings/28_build_spike_cfa.md`, and baked into
   `config/templates/{payouts,cfa}.toml.tmpl`) sets `Env` to a string the
   SDK doesn't recognize, so the DCS client fails at construction *before*
   any network call — DCS-driven feature flags resolve to their Go
   zero-value (`false`) rather than the arena's seeded fixture values.
   `dcs-stub` (`seeds/dcs/merchants.json`) is still fully built to spec —
   useful for the verifier, manual testing, or a future payouts/cfa binary
   patched with `dcs.SetContextUrl()` — see its `CONTRACT.md` for the full
   grounding. `asv-stub` has an analogous but differently-caused gap
   (payouts' real ASV client is gRPC, this stub is plain HTTP/JSON) — see
   `substitutes/asv-stub/CONTRACT.md`.

## Collision notes (for whoever picks this up next)

Two other agent workstreams wrote directly into this same directory tree
while this scaffold was being built (see
`findings/29_env2_scaffold.md` in the audit scratchpad for the full blow-by-blow):

- **`substitutes/kong-lite/`, `substitutes/monolith-stub/`,
  `substitutes/Dockerfile`** — originally written by a research fork that
  exceeded its read-only brief. **Both were substantially rewritten in the
  Env 2 substitutes pass** (this file's "Validation performed" section
  above and each stub's own `CONTRACT.md` have the details) — `kong-lite`
  now does real Basic-Auth + RS256 passport-JWT minting (it previously did
  no auth at all), `monolith-stub` now serves the routes
  `findings/20_api_monolith.md` actually confirmed (it previously served
  `/v1/contacts`/`/v1/fund_accounts`/`/v1/merchants`, none of which are
  real monolith routes). `substitutes/Dockerfile` (the shared runtime
  image) is unchanged.
- **`verifier/`** (the entire pytest suite: `conftest.py`, `helpers/*.py`,
  `verifiers/test_v*.py`, `Dockerfile`, `run.sh`, `requirements.txt`) — a
  separate, `VERIFIER_SPEC.md`-driven workstream this pass has no visibility
  into. This scaffold only wires it into `docker-compose.yml` (network,
  volume, the `secrets:` bridge described below) — do not add verification
  logic to `verifier/` from this workstream.

`verifier/helpers/creds.py` expects Basic-Auth credentials as `"user:pass"`
files under `/run/secrets/<slug>` (slugs: `ledger`, `fts`, `monolith`,
`ps-fastcron`, `ps-service`) — a different convention than the rest of this
scaffold's `SECRET.<name>` → inlined-into-TOML approach.
`secrets/gen-secrets.sh`'s `verifier-bridge/` step derives these from the
same underlying `auth_*` secrets so both conventions share one source of
randomness; the exact prefix→credential mapping is a best-effort guess
(commented in `gen-secrets.sh`), not confirmed against `VERIFIER_SPEC.md`.

## Validation performed on this scaffold

```bash
docker compose --env-file .env.arena -f docker-compose.yml \
  --profile datastores --profile substitutes config -q   # exit 0

docker compose --env-file .env.arena -f docker-compose.yml \
  --profile datastores --profile substitutes --profile core \
  --profile verify --profile migrations config -q         # exit 0

python3 -m py_compile <every .py file>                     # clean
bash -n <every .sh file>                                   # clean
bash secrets/gen-secrets.sh && python3 config/generate.py \
  && python3 preflight/preflight.py                        # PASS, end to end
python3 -c "import tomllib; ..." on every generated/**/*.toml  # all parse
```

No service was actually started (`docker compose up`) as part of building
this scaffold — that was explicitly out of scope (a separate build-spike
workstream owns that). Everything above is static/config-generation
validation only.

The Env 2 substitutes pass (see `substitutes/*/CONTRACT.md` and
`/private/tmp/.../scratchpad/rzp-payouts-architecture/findings/
30_env2_substitutes.md` for the full log) additionally ran each stub's
`server.py` directly on a free local port (`python3 <stub>.py` + `curl`,
no containers) — including a real end-to-end test between `stork-capture`
and `merchant-webhook-sink` (HMAC signature verification), `kong-lite`
against a throwaway mock `payouts-api` (Basic-Auth + RS256 passport
minting, independently re-verified against the real generated keypair),
and `monolith-stub`'s FTS-relay in both `relay` and `drop` modes against a
throwaway mock `payouts-api`. Also re-ran the three `docker compose ...
config -q` commands (now including `--profile mozart-real`) and the full
`gen-secrets.sh && generate.py && preflight.py` chain after every
substantive change in this pass — all still pass.
