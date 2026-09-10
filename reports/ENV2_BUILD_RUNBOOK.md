# ENV2_BUILD_RUNBOOK — building and starting the Payouts Env 2 arena

Package: `/Users/rana.singh/rzp-payouts-architecture/ENV2_COMPOSE/` (docker-compose; profiles `datastores`, `migrations`, `substitutes`, `core`, `verify`). Runtime: Docker inside a Colima Linux/arm64 VM. Status of the last run is in `ENV2_BUILD_STATUS.md`; safety evidence in `SAFETY_PREFLIGHT.md`, `EGRESS_AUDIT.md`, `SECRET_SCAN.md`.

## 0. Prerequisites (host)

| Item | How | Notes |
|---|---|---|
| Go 1.26 | `brew install go` | builds happen on the host (BUILD phase); private modules via `GOPRIVATE=github.com/razorpay/*` + `gh auth setup-git` |
| Docker runtime | `brew install colima docker docker-compose docker-buildx && colima start --cpu 4 --memory 12 --disk 60 --vm-type vz` | the Colima VM is the disposable Linux host the safety gate asks for |
| Repo clones | shallow clones of payouts, ledger, fts, cfa, x-balances (+ mozart optional) | never modified; payouts/cfa/x-balances are built from **copies** carrying two documented patches (see §2) |
| Python 3 | stock | stubs and tooling are stdlib-only |

## 1. Secret scan of inputs (once per clone set)

```
brew install gitleaks
gitleaks dir <clones-root> --redact=100 --report-format json --report-path <scratch>/gitleaks.json --exit-code 0
```
Quarantine anything that looks real (prod/stage env files) outside the clone tree; see `SECRET_SCAN.md`.

## 2. BUILD phase (credentials only here)

Binaries are compiled on the host (which holds the GitHub credential) and copied into credential-free runtime images (`build/runtime-only.Dockerfile`: alpine + ca-certificates + binaries, non-root `appuser`).

```
cd ENV2_COMPOSE
export REPOS_ROOT=<staging dir with symlinks: payouts→<copy>, ledger, fts, cfa→<copy>, x-balances→<copy>>
export GOFLAGS=-mod=mod
bash build/apply-arena-patches.sh "$REPOS_ROOT"   # go.mod replace -> build/arena-patches/goutils-dcs-<ver>, DCS/stork source patches (idempotent)
bash build/build-host.sh all          # GOOS=linux GOARCH=<host arch> CGO_ENABLED=0; payouts cmds need -tags boot (handled)
```
Deviations applied in repo **copies** (never in the clones):
1. `payouts/go.sum`: the two `github.com/razorpay/ifsc/v2 v2.0.43` lines re-recorded (upstream tag re-pointed; pristine go.sum fails checksum).
2. `payouts/pkg/dcs/client.go`, `cfa/internal/dcsservice/client.go`, `x-balances/pkg/dcs/client.go`: 3-line patch — if `ARENA_DCS_URL` is set, call the SDK's exported `dcs.SetContextUrl(ctx, url)` before `dcs.New`. Reason: `goutils/dcs` ignores `Mock` and derives its login host from a hardcoded env→hostname map (`goutils/dcs/config/uri.go`), so pristine binaries dial a real host at construction. Unset in production ⇒ no behaviour change.
   **Plus** a build-time `replace github.com/razorpay/goutils/dcs => <scratch>/env2-spike/arena-patches/goutils-dcs-<ver>` in each copy's `go.mod` (v1.7.3 payouts, v1.7.1 cfa, v1.5.2 x-balances): the module copy's `GetContextUrl` returns `ARENA_DCS_URL` when the request context carries no URL. Reason: the context override only covers the constructor; every later call (e.g. payouts' HTTP-encryption feature check, `v1/kv/get`) fell back to the real hostname (stopped only by the proxy blackhole). No-op when the variable is unset.
3. Runtime asset files are staged by `build-host.sh` (`stage_assets_payouts`, `stage_assets_cfa`): payouts `/app/github.com/razorpay/ifsc/v2@<ver>/src/*.json`; cfa `/app/github.com/razorpay/cfa/pkg/ifsc/*.json` and `/app/cfa-entry.sh` (socat Mongo forward; `socat` is in the runtime image). Services resolve them relative to `WORKDIR=/app`.
3b. Schema patch: `seeds/schema-patches/payouts.sql` (applied by `up.sh` after migrations) adds `payout_details.beneficiary_bank_code`, which the code writes but no repo migration creates (ARCHITECTURE_DELTA W10).
4. FTS runs its Go binary directly on 8080 (production fronts it with nginx; not needed here).

Mozart: `mozart -mock` would be the preferred bank simulator but cannot be built — its private deps `integrations-utils`, `integrations-go`, `orchestrator` are not readable. The Python `mozart-sim` (real envelope, real `bank_status_code` values, 6 scenarios from `mozart/app/testdata`) is the primary substitute; `mozart-mock` remains an opt-in profile.

Verify: `docker run --rm --network none -w /app -e WORKDIR=/app -e APP_ENV=arena rzp-arena/payouts:local /app/payouts-api` must fail only on missing `/app/config` (not exec-format or asset errors).

## 3. RUNTIME phase

```
cd ENV2_COMPOSE
bash scripts/up.sh
```
`up.sh` performs, in order: `secrets/gen-secrets.sh` (per-arena DB passwords, service Basic-Auth pairs with the real usernames, RSA passport keypair, synthetic merchant keys `rzp_test_ARENA…`) → `config/generate.py` (renders `generated/<svc>/{default,arena[,e2e]}.toml` from templates; every URL is an arena service name; secrets inlined into read-only mounted files, never into images) → `preflight/preflight.py` (hard gate; writes `SAFETY_PREFLIGHT.md`) → `docker compose --profile datastores up -d` (MySQL ×4 incl. an API-DB DDL stub, Postgres, Mongo, Redis, LocalStack SQS/SNS, Kafka KRaft) → `--profile migrations run --rm <svc>-migrate` (goose/rx/Mongo migrations from the mounted repo migration dirs) → seeds (`seeds/` SQL/JS/JSON for merchants ARENAM00000001..3) → `--profile substitutes up -d` (kong-lite, monolith-stub, dcs-stub, splitz-stub, shield-stub, pricing-stub, asv-stub, stork-capture, merchant-webhook-sink, xas-sink, mozart-sim, cron-driver) → `--profile core up -d` (payouts-api, 14 `payouts-worker-<job>` processes each pinned to one job via `PAYOUTS_WORKER_NAME`, two `payouts-kafka-fts-status-updates[-retry]-consumer` processes pinned via `PAYOUTS_CONSUMER_TASK_NAME`, ledger api/worker/scheduler(one-shot), fts web/workers, cfa server/worker, xbalances server/worker) → `scripts/healthcheck.sh`.

`up.sh` is re-runnable: it keeps existing secrets (so datastore volumes stay reachable) unless `REGEN_SECRETS=1`; a from-empty rebuild is `bash scripts/down.sh && bash scripts/up.sh`. Manual equivalents (all from `ENV2_COMPOSE`; always pass `--env-file .env.arena`, and pass the profiles together because `depends_on` crosses profiles):
```
docker compose --env-file .env.arena --profile datastores up -d
docker compose --env-file .env.arena --profile datastores --profile migrations run --rm payouts-migrate   # and ledger-, fts-, cfa-, xbalances-migrate
docker compose --env-file .env.arena --profile datastores --profile substitutes up -d
docker compose --env-file .env.arena --profile datastores --profile substitutes --profile core up -d
bash scripts/healthcheck.sh <service...>
```
Queues: `seeds/localstack/init-queues.sh` (LocalStack `ready.d` hook) creates the 32 SQS queues + 1 SNS topic named in the generated configs. Payouts talks to LocalStack through its own vendored SQS client (`[queue.sqs].endpoint`, signed with the synthetic arena AWS env so LocalStack resolves region `ap-south-1`); cfa/x-balances use the `goutils/worker` `sqs_local` dialect.
Network model: `rzp-arena` (explicit `name:`, subnet `172.28.16.0/24`) is `internal: true` (no egress, default-deny by construction); `rzp-ingress` connects only `kong-lite` (and the verifier) to the host. Every core container also carries `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:9` and a `NO_PROXY` list of arena names as defense-in-depth against SDK paths that ignore config. Core containers set `WORKDIR=/app`, `APP_ENV=arena`, `ARENA_DCS_URL=http://dcs-stub:8080`, run as `appuser`, and mount only `./generated/<svc>` read-only.

## 4. Verification and audit

Result snapshot (2026-09-04): boot from empty volumes to 31 healthy core processes via `bash scripts/up.sh`; golden run 8 passed / 6 failed / 12 skipped (see `ENV2_BUILD_STATUS.md` for the per-verifier reading); egress audit and DNS check PASS.


```
bash network/dns-check.sh            # arena names resolve to arena IPs only
bash network/egress-audit.sh 60      # tcpdump on the Docker-host namespace, BPF "src arena and not dst arena" → EGRESS_AUDIT.md
bash scripts/golden-run.sh --with-egress-audit   # runs verifier/ (24 verifiers, pytest) inside the arena with the capture running
```
Any external connection attempt observed by the egress audit terminates the run; the originating service and config key are recorded in `EGRESS_AUDIT.md`.

## 5. Reset, snapshot, teardown

```
bash scripts/reset.sh      # flush redis, purge queues, re-apply the idempotent seeds/s4 set (schema kept; use down+up for from-empty)
bash scripts/snapshot.sh   # tar named volumes
bash scripts/restore.sh <snapshot>
bash scripts/down.sh       # compose down -v + secrets/destroy.sh (shreds secrets/ and generated/)
```

## 6. Known gaps / where it can stop

- Third-party image pulls need Docker Hub (BUILD-phase egress only); flaky TLS handshakes were seen — `docker pull` with retries.
- `xbalances-server` boots only with the DCS patch (its DCS init is fatal); `payouts-api` additionally needs `config/e2e.toml` present (prod code imports a test package) — generated and sanitized by `generate.py`.
- Credentials are one generated secret per caller→callee pair; every callee's inbound auth section is bound to the same token (finding 31). FTS usernames must be `<app>_<x>` (`ps_payouts`, `api_monolith`) because fts derives the app from the prefix; payouts validates passports by `kid` = `[passport.edge].identifier` (`arena-passport-1`, per-arena key).
- fts-web listens on `0.0.0.0:8080` in the arena (upstream default binds `127.0.0.1` behind nginx). fts' payout status webhook goes to `monolith-stub`, which relays to payouts-api exactly as the monolith does.
- The verifier speaks the post-Kong contract to payouts-api directly (`PS_PUBLIC_URL`) with passports minted by kong-lite's arena-only `POST /_arena/mint`; kong-lite's own API-key path is exercised with the seeded merchant keys.
- Safety-gate note: `preflight/preflight.py` refuses every `arn:aws` except the LocalStack synthetic account `000000000000` (arena queues/topics); `config/generate.py` rewrites all other ARNs to `arn:local`.
- Balances in tests must be changed through the ledger (verifier `helpers.payouts_flow.ledger_topup`, a `positive_adjustment_processed` journal); SQL edits on `accounts` are invisible to ledger-api's balance cache.
- Direct-account merchants (M2) need: fts `direct_account_routing_rules`, `channel_information_status` UP rows, and `transfer.preferred_source_account_id` in the FTS create request (sent by the monolith relay).
- Cron cadence is assumed (`config/arena.yaml`); production cadence lives in the FastCron SaaS.
- Ledger accounts: seeds create accounts via SQL and reuse ledger's own hardcoded parent-account ids (see `SYNTHETIC_FIXTURE_SPEC.md` §19).
