# Twin v1.0: Milestone 1 completion report

Date: 2026-09-05. Lead: Claude (technical and implementation lead, Project RedGrid).
Primary input: `reports/claude-review/TWIN_V1_AUDIT_AND_NEXT_STEP.md`.
Scope: the bounded "correct and re-accept" milestone. No product family was added, no core service source was changed,
no autonomous red-agent work was started, and no Daytona action occurred.

## 1. Verdict: READY TO FREEZE

The generated acceptance gate passes: `reports/implementation/local-acceptance.json` (schema 2) reports
`status: passed`, 14 of 14 required checks true, zero unexplained results, computed from the artifacts named in
`reports/implementation/acceptance-manifest.json`. No boolean in that file is hand-set.

| Item | Value |
|---|---|
| Freeze commit | `FREEZE_COMMIT_SHA` |
| Tag | `twin-v1.0` (annotated; points at the commit that records the freeze SHA in this report, `TAG_COMMIT_SHA`) |
| Accepted run root | `reports/implementation/runs/m1-20260905T165255Z/` (git-ignored by evidence policy; every file the gate read is SHA-256 listed in `local-acceptance.json`, 139 entries) |
| Current-boot audit | `reports/implementation/audits/m1-current-boot-20260905T165742Z/` (passed with the documented caveats; boot identity matches replay A) |
| Build evidence | `.local/twin-repos/build-evidence-20260905T160213Z/` (payouts image `sha256:915299abaaf234dd7d8be73da9ec18c6b0554703184c6510c8516f3f6698838b`) |
| Tree config digest | `afb1aa02eb3112e9…` over 122 fingerprinted input files; identical in every run directory the gate consumed |
| Daytona | No sandbox created, no upload, no API call. The Daytona runner was updated to consume schema 2 and remains gated. |

## 2. What was corrected (twin side only)

Every item is registered in `ENV2_COMPOSE/config/declared-deviations.yaml` (status `fixed_in_m1`) and, where it caused a
retained failure, in `reports/implementation/fixed-twin-defects.json`.

| Id | Correction | Where | Deployed reference |
|---|---|---|---|
| DEV-005 / FD-001 | Payouts Kafka producer brokers `["kafka:9092"]` instead of the upstream placeholder pair that never resolved; this is what crashed the consumer after any failed message | `config/templates/base/payouts/arena.toml` | consumer config parity with `payouts/config/prod.toml:312-346`; producer target is the arena broker |
| DEV-007 | `restart: on-failure` on both Kafka consumer containers | `docker-compose.yml` | live Deployment `restartPolicy: Always` (kube-manifests drift report, revision 418) |
| DEV-001 / FD-002 | ledger-gate forwards every request header except hop-by-hop and transport headers, so `idempotency-key`, `Request-ID`, `Trace-ID`, `Country-Code` reach Ledger | `substitutes/ledger-gate/server.py`, `CONTRACT.md`, `test_contract.py` (6 tests) | ledger `pkg/idempotency/interceptor.go:115-118` short-circuits on an empty key; production sends it |
| DEV-002 | Ledger `[mutex] ttl = 60000` | `config/templates/base/ledger/arena.toml` | `ledger/config/prod-live.toml:326` |
| DEV-003 | Payouts `features.ikey_auto_enforcement = true` | `config/templates/base/payouts/arena.toml` | `payouts/config/prod.toml:664`. Enforcement is additionally gated per merchant by Splitz `ikey_auto_enforcement_ramp_up`, which the seeded fixtures return `off`; the flip is therefore inert against current fixtures and is recorded as such |
| DEV-004 | `files/error/{payout_error,fav_error}.json` staged into the payouts image at `/app/files/error/` (0644, gitleaks clean); image rebuilt through the new `build/record-rebuild.py`, which writes a build-evidence directory in the existing schema | `build/build-host.sh`, `build/assets.Dockerfile.md`, `build/record-rebuild.py` | `payouts/build/docker/prod/Dockerfile.api:80` copies `files/`; `internal/helpers/read_file.go` returned nil silently |
| DEV-006 | FTS `[payouts_service.update_fts_fund_transfer].TIMEOUT = 300` | `config/routes.py` | `fts/config/env.prod-live.toml:269-273` |
| FD-003 | Generated arena passwords no longer start with `-` or `_`; a leading `-` made mongosh parse the CFA seed password as an option and aborted one boot in roughly 32 | `secrets/gen-secrets.sh` | twin-only defect; the failed boot is retained at `runs/m1-20260905T165255Z/profile-kafka-failed-boot/` |

Explicitly not changed, by instruction: Kafka job registration, Kafka DTO translation, FAILED/REVERSED handling over
Kafka, the unchecked nil producer and unrecovered consumer goroutine, and every other documented production-source gap.
The admitted source copies under `.local/twin-repos/accepted/` and the pristine clones are byte-identical to the audit state.

## 3. Acceptance gate

`ENV2_COMPOSE/scripts/local-acceptance.py <manifest>` replaces the hand-authored file. It derives every check from named
artifacts and fails closed on missing, stale or contradictory evidence:

- **Staleness.** `scripts/up.sh` now writes `.runtime/arena-fingerprint.json` (sha256 of the compose file, config
  templates and generators, seeds, substitutes, verifier, cron driver; the route profile; the `rzp-arena/*` image ids
  resolved with the tag from `.env.arena`; boot id; git HEAD). Every run-producing command copies it into its run
  directory. The gate requires all consumed runs to carry the same config digest as the current tree and the audit's
  boot identity to match a replay boot.
- **`route_fidelity`** (required): 19 profile/scenario rows, all PASS. Transport observed for each, and the ending state
  equals either the normal state or a cited expected failure whose predicted state matches the DB snapshot.
- **`product_route_health`** (informational): true for 14 rows; false with a citation for the five Kafka rows that are
  source-predicted failures (see section 4).
- **`expected_failures`**: `TWIN_SPEC/expected-failures.yaml` entries EF-001, EF-002, EF-003 matched by run artifacts.
- **`declared_deviations`**: 76 entries embedded (7 `fixed_in_m1`, 69 `declared`).
- **`fixed_twin_defects`**: FD-001, FD-002, FD-003 with fix and evidence run; any placeholder text blocks the gate.
- **`executed`**: boots and fingerprints for monolith, direct, direct-create-monolith-status and kafka profiles.
- **`unexplained_results`**: empty; any failed, timed out or skipped case not mapped to an expected failure blocks.

Offline tests: `test_local_acceptance.py` 35, `DAYTONA/tests` 17, `test_compare_replays.py` 29, `preflight/audits`
6, ledger-gate contract 6, monolith-stub contract 18. All pass.

## 4. Fresh-run results (accepted root `runs/m1-20260905T165255Z`)

| Run | Result |
|---|---|
| Replay A full suite (monolith, empty volumes, resource-profiled, egress-audited) | 26 passed, 0 failed, 0 skipped; suite time 50.1 s; egress passed, exit 0, 0 outside packets |
| Replay B full suite (separate empty-volume boot) | 26 passed, 0 failed, 0 skipped; suite time 50.1 s; egress passed, exit 0, 0 outside packets |
| Logical replay comparison A vs B | passed, both inputs valid, zero differences (`logical-replay-m1.json`) |
| Six route cases (monolith) | all passed; per-case `route-evidence-<case>.json` transport observed |
| Six bank cases (monolith) | all passed; bank snapshots now carry the monolith relay layer; transport observed |
| Golden via Kong (monolith) | passed; resource phase `successful_payout` sampled |
| Direct profile golden | passed; `direct_origin_metadata` and `direct_status_http` observed |
| Mixed profile golden | passed; `direct_create_response` and `monolith_status` observed |
| Kafka profile Shared golden | failed as predicted (EF-001): payout `initiated`, FTS `PROCESSED`, no processed journal, no terminal webhook; egress exit 1 accepted only because the registry declares it |
| Kafka `shared_source_failure` | expected_failure, matches prediction (EF-001) |
| Kafka `direct_after_shared` (run after the Shared failure in the same boot) | passed: `processed`, one signed receipt; consumer containers `running`, restart count 0; retry topic consumed 2/2 |
| Kafka `failed_dropped_direct`, `failed_dropped_shared` | expected_failure, match prediction (EF-002): FTS FAILED published over Kafka, payout stays `initiated`, no reversal, no journal, no webhook |
| Kafka `reversed_dropped_direct` | expected_failure, matches prediction (EF-003): FTS REVERSED over Kafka, payout stays `processed`, no reversal |
| Current-boot audit on replay A | passed with caveats; 65 service identities and image ids match; zero unresolved findings |
| Resource profile | idle 1.02 cores / 4.11 GiB, successful payout 1.08 / 4.03, full suite 1.21 / 4.13; disk 4.42 GiB |

Every command above ran under the same-window egress audit with zero outside packets and zero dropped capture packets.

### Route fidelity by profile

| Profile | Scenarios | Fidelity |
|---|---|---|
| monolith | 6 route + 6 bank + golden | 13/13 PASS |
| direct | golden | PASS |
| direct-create-monolith-status | golden | PASS |
| kafka | golden-shared, direct_after_shared, failed_dropped_direct, failed_dropped_shared, reversed_dropped_direct | 5/5 PASS |

### Product-route health by profile

| Profile | Healthy | Not healthy (expected, cited) |
|---|---|---|
| monolith | 13 | none |
| direct | 1 | none |
| direct-create-monolith-status | 1 | none |
| kafka | 1 (`direct_after_shared`) | `golden-shared` (EF-001), `failed_dropped_direct` and `failed_dropped_shared` (EF-002), `reversed_dropped_direct` (EF-003) |

### Expected source failures

| Id | Behaviour | Source |
|---|---|---|
| EF-001 | Shared PROCESSED over Kafka never reaches the Ledger processed journal; payout stays `initiated` | `payouts internal/boot/handler.go:154` (RegisterJobs absent from `boot_kafka_consumer.go:17-29`), `pkg/worker/manager.go:186-189`, `core.go:2472-2475, 2589-2595` |
| EF-002 | FAILED over Kafka is acknowledged and dropped | `internal/taskHandlers/fts_status_updates.go:56-62`, retry `:58-64` |
| EF-003 | REVERSED over Kafka is acknowledged and dropped | same |

All three are deployed-relevant: the consumer runs in production (3 live replicas), the FTS producer is enabled in
prod-live, and the Splitz rollout of the Kafka route is not determinable from repositories (owner request P1).

## 5. Verifier changes

- V04 guards the index catalogue non-empty (and the journal primary key present) before asserting no unique index.
- V06 asserts I21's named clauses: `fts_fund_account_id` and lower-cased `fts_account_type` as stored by Payouts from the
  relay, cross-checked against the relay payload, and the credited Ledger `account_details.fund_account_type` under the
  matching Nodal/Current parent. Ledger persists no identifier column, so the identifier clause is asserted on what Payouts
  received and stored; the docstring says so.
- V16 is now stronger than production: after the dropped relay it drives all eight cron routes and asserts payout state,
  `payout_logs`, `updated_at`, FTS attempt chain, reversal absence and webhook absence unchanged over a 15 s window.
- New `verifier/kafka_scenarios.py` (family `kafka` in `scenarios.sh`), five cases driven only by real bank outcomes and
  the real FTS producer; no Kafka client was added. `route-evidence.py` gained `--bank-case` and `--kafka-case`, writes
  `route-evidence-<case>.json`, and records consumer liveness, group offsets and unfiltered crash markers for every
  kafka-profile run.
- `bank_scenarios.py` passes the monolith capture client so bank snapshots carry the relay layer.
- Replay comparator: four documented rules were added after the new observations exposed non-material differences
  (suite-wide capture logs scoped to the reading test, simulator `gateway_ref_no` aliased, V16's clock snapshot treated
  as a clock, reservation inspect items unordered because Ledger lists them from a Redis hash). See
  `LOGICAL_REPLAY_REVIEW.md`.

## 6. Retained attempts and what they showed

| Root | Outcome | Cause |
|---|---|---|
| `runs/m1-20260905T161523Z` | all workloads passed; comparison differed on V06 and V16 | new tests read suite-wide capture logs; comparator rules added |
| `runs/m1-20260905T163551Z` | all workloads passed; gate blocked | fingerprint resolved image tags with the compose default `:local` instead of `.env.arena`'s tag (fixed in `fingerprint.py`); one entropy-heuristic scanner hit in the payouts MySQL dump that did not recur (a table/column classifier now runs automatically on any future block) |
| `runs/m1-20260905T165255Z` | accepted | Kafka-profile boot failed once on a `-`-leading generated password (FD-003), retained as `profile-kafka-failed-boot/`; rerun passed |

Historical run directories from the Codex candidate and the audit are untouched.

## 7. Remaining declared deviations (69, all in `declared-deviations.yaml`)

Highest-impact ones a reader should know: mozart circuit-breaker timeout 220 s versus 10 s (the 30 s bank-timeout case
relies on it); payouts `source_update_topics` collapsed to one topic and two `stage-` prefixed job names; ledger
`appMode=test`, split accounts off, caches off; x-balances in-memory queue and loopback gRPC; FTS worker concurrency map
undeclared; all Splitz variants ASSUMED; the two arena source patches (`ARENA_DCS_URL`, `ARENA_STORK_JSON`); schema
extensions (`features.name` 255, `beneficiary_bank_code`); the monolith-side Kafka consumer not modelled (DEV-150);
production `restartPolicy: Always` represented by `on-failure` on the two consumers only.

## 8. Scope exclusions declared for Twin v1.0

Full Workflow/approval implementation; XAS/BAS/statement reconciliation; bulk caller and bulk approval; full production
Stork lifecycle; broad bank/channel fleet (RBL only, all channels UP); the missing worker fleet (11 of 25 payouts
workers); complete dashboard/admin/impersonation behaviour; Shared Kafka product-route health. Route fidelity for the
declared v1.0 profiles is unaffected by these exclusions.

## 9. Commit contents and hygiene

The freeze commit adds the twin package (`ENV2_COMPOSE/` minus generated config, generated seeds, secrets and runtime
state), `TWIN_SPEC/`, `DAYTONA/`, `ARCHITECTURE_EXPLORER/` (minus `dist/`), `reports/` (minus `runs/` and baseline logs),
and the two `reports/claude-review/` documents. A gitleaks scan of the staged set was run before committing; the only
hits are synthetic placeholders already present in the scaffold commit, upstream library test fixtures, a Splitz
experiment id, a commit SHA and a UUID in a unit test. No credentials, generated runtime secrets, private source copies,
database volumes or raw run directories are committed; retained evidence is hash-referenced from `local-acceptance.json`.

## 10. Owner requests

`reports/claude-review/OWNER_REQUEST_PACKET.md` lists three priority requests (Kafka experiment rollout, PS-direct plus
Kafka combination, sanitized 30-day counts of the two Kafka dispatch errors) and nine secondary ones. None blocks this
freeze.
