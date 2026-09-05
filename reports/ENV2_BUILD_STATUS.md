# ENV2_BUILD_STATUS — 2026-09-04 (final for this pass)

Host: macOS arm64, Go 1.26, Docker 29.5 inside Colima (Linux/arm64 VM, 4 CPU / 12 GB). Package: `ENV2_COMPOSE/`. Evidence: `raw-findings/28_build_spike*.md` (host spike), `raw-findings/30_env2_substitutes.md` (stubs + config derivation), `raw-findings/31_env2_bringup_notes.md` (arena bring-up), `SAFETY_PREFLIGHT.md`, `EGRESS_AUDIT.md`, `SECRET_SCAN.md`.

**Bottom line: Env 2 boots end-to-end from the documented start command.** From empty volumes: secrets → config → preflight → 9 datastores → 5 migration sets → seeds → 12 substitutes → 30 core containers, all reporting healthy, with zero error lines in a 40 s steady-state scan and LocalStack answering only HTTP 200. Golden-run/verifier and egress results are in the last two rows.

## Stage results

| Stage | payouts | ledger | fts | cfa | x-balances | Notes |
|---|---|---|---|---|---|---|
| Private modules download | ✔ | ✔ | ✔ | ✔ | ✔ | `GOPRIVATE=github.com/razorpay/*`; payouts `go.sum` ifsc lines re-recorded in a repo copy |
| Cross-compile linux/arm64 + runtime image | ✔ (`-tags boot`, ifsc assets) | ✔ | ✔ (Go binary on 8080, no nginx) | ✔ (ifsc assets, socat Mongo forward) | ✔ | alpine + binaries, non-root, no credentials |
| Migrations in the arena (from empty volumes, repo migration dirs mounted ro) | ✔ 34 goose | ✔ 51 (tenant X, `rx_migrations`) | ✔ 50 goose | ✔ 4 Mongo | ✔ 1 | `scripts/up.sh` step 5 |
| Seeds (`seeds/s4/*`, idempotent) | ✔ 3 banking accounts / 4 fund accounts / 3 counters | ✔ 18 accounts, 3 ledger_config, 2 journals | ✔ | ✔ 4 contacts / 4 fund accounts / 8 hash rows | ✔ | step 6 |
| Substitutes | ✔ kong-lite, monolith-stub, dcs-stub, splitz-stub, shield-stub, pricing-stub, asv-stub, stork-capture (3 webhook subscriptions self-seeded), merchant-webhook-sink, xas-sink, mozart-sim, cron-driver | | | | | real `mozart -mock` still unbuildable (private deps) |
| Core boot + health | ✔ api + 14 per-job workers + 2 Kafka consumers | ✔ api, worker, scheduler (one-shot, exit 0) | ✔ web, workers | ✔ server, worker-contact, worker-fa | ✔ server, worker | 30/30 healthy |
| Steady state (40 s scan) | 0 error lines | 0 | 0 | 0 | 0 | LocalStack: `ReceiveMessage => 200` only; both Kafka consumers claimed their partitions |
| DNS isolation | ✔ `network/dns-check.sh`: arena names resolve, `example.com` does not | | | | | |
| Golden run (24 verifiers) + egress audit | see §Golden run below | | | | | |

## What it took after the config derivation (all repo-derived, none guessed)

1. Payouts runs **one worker process per job** (`PAYOUTS_WORKER_NAME`), 25 jobs in production manifests; the arena runs the 14 payout-path jobs. The `[worker]` block must exist in TOML for the env override to apply.
2. Payouts Kafka consumers are **two processes** selected by `PAYOUTS_CONSUMER_TASK_NAME`; their `[kafka_consumers]/[task]/[topics]` config exists only in `config/devstack.toml`.
3. LocalStack resolves a path-style queue URL's region from the SigV4 signature: anonymous requests fall back to `us-east-1` and every queue "does not exist". Fixed with synthetic signing creds in the shared env, `ap-south-1` everywhere, anonymous flags off, full-URL `Prefix` for the goutils v1 client (cfa).
4. cfa's worker takes its queue from `CFA_WORKER_QUEUE_NAME`; production runs `cfa-worker-contact` and `cfa-worker-fa`, mirrored in the arena.
5. LocalStack needs a `ready.d` hook to create the 34 SQS queues and 2 SNS topics the services name.
6. Compose: explicit network `name:`s, all profiles passed together, `up.sh` re-runnable (keeps secrets), reset uses the s4 seed set.
7. Every inter-service credential pair had to be bound on the **callee** side too (payouts/ledger `[auth.*]`, cfa/x-balances `Server.Auth.*`, fts `[users.*]`); fts usernames must be `<app>_…`; payouts validates passports by `kid` (`arena-passport-1`).
8. payouts' `[fts]` client block exists only in env-specific TOMLs; fts' payout status webhook goes to the monolith (`monolith-stub` relay); fts-web must bind `0.0.0.0`.
9. Substitutes must answer `/v1/<route>` paths with object-shaped errors; the monolith identity is `rzp_live`.
10. The DCS context override does not cover post-construction calls → build-time module replace (`arena-patches/goutils-dcs-*`).
11. **Schema drift**: `payout_details.beneficiary_bank_code` is written by code but created by no migration → `seeds/schema-patches/payouts.sql` (W10).
12. Verifier fixes from real contracts: `merchant_id` in the create body (from the passport), monolith fund-account ids (`ARENAFAX…`), min amount 100 paise, real scheduled-payout slots, fts `transfers.source_id` is 14 chars and responds with `fund_transfer_id`.

## Safety findings (unchanged, must be read)

1. `goutils/dcs` ignores `Mock=true` and logs in to a hardcoded per-env host at construction (2 real outbound calls to a dev DCS host during the host spike, before mitigations). Arena mitigation: internal-only network, proxy blackhole, and the transparent `ARENA_DCS_URL` patch in repo copies.
2. `goutils/worker` plain `sqs` dialect ignores `Endpoint` and dials real AWS (cfa's worker hit `sqs.us-east-1.amazonaws.com` for ~12 s during the host spike). Arena mitigation: `sqs_local` dialect / vendored client endpoint / inmemory.
3. Host-run binaries are not covered by `docker network --internal`; every arena process is a container.

## Deviations applied (copies only, never the clones)

payouts `go.sum` ifsc hash re-recorded; payouts/cfa/x-balances DCS wrappers patched with `ARENA_DCS_URL`; IFSC asset files added to images; FTS run without nginx; Kafka image `apache/kafka:3.8.0`; migration jobs mount the clone migration dirs read-only; cfa Mongo reached through an in-container `socat` forward (client forces TLS unless the endpoint is localhost).

## Golden run

Command: `cd ENV2_COMPOSE && bash scripts/golden-run.sh --with-egress-audit` (verifier container on `rzp-arena`; 24 verifiers in `ENV2_COMPOSE/verifier/`; log archived at `raw-findings/env2-logs/verifier-run28.log`).

Result of the final run (2026-09-04, golden-run.sh --with-egress-audit, log `raw-findings/env2-logs/golden-run-final.log`): **8 failed, 9 passed, 9 skipped** (28 runs of the suite were needed to reach this; each earlier failure class was a wiring or contract gap now recorded in `raw-findings/31_env2_bringup_notes.md`). The **end-to-end payout path is proven** for both merchant types: create → ledger journal (4 entries) → `initiated` → monolith relay → FTS (per-queue workers) → Mozart simulator → FTS status webhook → monolith relay → PS `processed` (with utr), including the Direct/RBL merchant (`preferred_source_account_id`, direct routing rules).

| Verifier | Result | Reading |
|---|---|---|
| V01 idempotency same key/body, V02 different body rejected | PASS | real PS idempotency table |
| V03 FTS transfer dedupe | PASS | fts `attempts_unique_keys` |
| V05 payout_initiated journal balanced + merchant debited | PASS | ledger X config via `CreateInBulk`, entities with signed ids |
| V09 low balance → queued | PASS | ledger authorization |
| V11 merchant balance debited synchronously | PASS | |
| V08 direct-account failed: no reversal, no ledger journal | PASS | direct/RBL merchant through fts direct routing + mozart-sim + monolith status map (FAILED→failed) |
| V14 no illegal transitions in payout_logs | PASS | |
| V24 scheduled payout dequeues after slot + cron | PASS | real slots {9,13,17,21} IST + arena time travel |
| V04 ledger journal dedupe | FAIL | reads by `transactor_id` for a payout that ended `created` in the wait window; the invariant itself (no unique index, C18) is unchanged |
| V06 processed accounting, V18 shared failed→reversed remap, V10 insufficient balance fails | FAIL | verifier races the live bank path (payouts reach `processed` in seconds) or exhausts the shared merchant's ledger balance across runs; needs per-test balance headroom and mozart-sim scenario amounts (100200 = bank failure, 100600 = returned) instead of synthetic PS webhooks |
| V12 direct-rail reservation total | FAIL | in-flight reservation flag is read from dcs-stub for M2; PS did not register a live reservation — open question whether the DCS key/value shape matches (finding 26) |
| V24 queued low-balance dequeue | FAIL | payout stays `queued` after a real ledger top-up + cron; PS's dequeue reads the **monolith** VA balance (stub → ledger) and API-DB `balance.updated_at`; remaining gap is documented in finding 31 |
| V07/V13/V15/V16/V17/V19/V20/V21 | SKIP | pre-terminal-state preconditions (`initiated` with no bank outcome) cannot be held because mozart-sim answers immediately; needs the `100300` pending scenario wired per test |
| V17 network fault, V22 shield latency, V23 pricing `X-Test-Fault` | SKIP | fault-injection controls do not exist in the substitutes yet (spec gaps carried over) |

Isolation evidence for the same window (capture 2026-09-04T17:58:48Z, 120 s, inside the final golden run): `EGRESS_AUDIT.md` PASS (host-namespace capture, filter `src arena and not dst arena`, control sample 200 packets), `network/dns-check.sh` PASS, `SAFETY_PREFLIGHT.md` PASS. During the whole pass no packet left the arena; the only outbound attempt ever observed (DCS `kv/get` to a real hostname) was stopped by the proxy blackhole and then removed at build time.

## What the golden run still needs (in priority order)

1. Per-test balance headroom: seed/top-up before each accounting verifier (helper `ledger_topup` exists), and use mozart-sim scenario amounts for bank outcomes.
2. A "hold in initiated" bank scenario (mozart-sim `100300`) for the pre-terminal verifiers.
3. Fault-injection hooks in shield-stub / monolith-stub / network for V17/V22/V23.
4. The queued low-balance dequeue path: confirm what production's monolith `balance` sync provides that the arena lacks.

## Independent audit (raw-findings/32_independent_audit_env2.md) and what was done about it

| Audit finding | Action in this pass |
|---|---|
| Egress audit and golden run were not captured in the same window | golden run re-executed with `--with-egress-audit`; `EGRESS_AUDIT.md` now carries the timestamp of that run |
| The DCS module patch lived only in the scratch tree; a fresh engineer could not rebuild | copied into `ENV2_COMPOSE/build/arena-patches/` with `build/apply-arena-patches.sh` (go.mod replace + the two source patches, idempotent); runbook §2 updated |
| `SAFETY_PREFLIGHT.md` hostname table silently capped at 200 rows | a `truncated: N more` row is now appended when the cap applies |
| A third-party domain (beeceptor.com) survived sanitization in an upstream default TOML | generator rewrites public third-party URLs to the blackhole; preflight refuses any `https?://…\.(com|in|io|net|org)` outside LocalStack |
| `generated/` configs were 0644 while `secrets/` are 0600 | generator writes rendered configs 0600 |
| `monolith-stub` never fails the dual-write like the 2026-09-03 production incident | `PS_DUAL_WRITE_MODE=fail` returns 500 on `/payouts_service/dual_write` (default `ok`) |
| Mozart substitute is hand-built, verifier confidence tiers not updated | recorded here and in VERIFIER_SPEC.md: results on the bank leg are "simulator-grounded" (envelopes from `mozart/app/testdata`), not "real Mozart" |
| V04/V06/V12/V24 failures are genuine findings about the target system | kept as failures, not masked |
