# TWIN_SPEC — RazorpayX Payouts Twin, authoritative specification (2026-09-05)

This directory is the evidence-backed specification for a production-representative Payouts Twin. It supersedes the
planning-era documents in `reports/` (PAYOUTS_ARCHITECTURE.md, PAYOUTS_FLOW_CATALOG.md, ARCHITECTURE_DELTA.md,
SYNTHETIC_FIXTURE_SPEC.md, EFFECTIVE_CONFIG_GAPS.md) wherever they disagree; the disagreements are listed in
`reports/fidelity/ARCHITECTURE_CORRECTIONS.md`. Nothing here was written from the earlier reports alone: every claim
was re-read in the repositories at the commits recorded in `architecture.yaml`.

| File | What it is |
|---|---|
| `architecture.yaml` | Production architecture per family (public API, dashboard, shared/ledger, direct rails, approval, queued, scheduled/on-hold, success/failure/return/reversal, PS→monolith→FTS, direct paths, Kafka, webhooks, statements/recon, cutover, bulk, admin/repair) with confidence tags |
| `components.yaml` | Every component: what it controls, tier today, target tier, real-vs-substitute decision |
| `runtime-topology.yaml` | Production deployments/workers/queues/topics/crons vs the twin's compose services |
| `route-matrix.yaml` | Which path a payout takes and the exact selectors (config keys, DCS/Splitz flags) |
| `state-machines.yaml` | Payout, FTS transfer/attempt, monolith FTA map, ledger journals, workflow, reservation |
| `configuration-manifest.yaml` | Effective production config keys that change behaviour, the twin's values, and arena generation rules |
| `synthetic-data-model.yaml` | Merchant archetypes and every record family with type/constraints/generation rule/exactness |
| `acceptance-invariants.yaml` | Invariants with production provenance and the verifier that proves each |
| `substitute-contracts/` | Exact protocol + behavioural contract for each substitute (monolith, kong-lite, mozart-sim, WFS, Batch, Stork, XAS/recon/repair, DCS/Splitz/pricing/Shield) |
| `schema/` | DDL/JSON schema per datastore derived from migrations and models (no rows) |
| `CODEX_HANDOFF.md` | Ordered implementation plan for Codex |

Companion reports: `reports/fidelity/` (CURRENT_TWIN_FIDELITY.md, PRODUCTION_VS_TWIN_MATRIX.csv, SCHEMA_PROVENANCE.md,
CONFIG_PROVENANCE.md, SYNTHETIC_DATA_PROVENANCE.md, REMAINING_INFORMATION_REQUESTS.md, ARCHITECTURE_CORRECTIONS.md,
`raw/01..12_*.md` lane evidence) and `reports/contracts/` (service API contracts).

Fidelity tiers used everywhere: **REAL**, **CONTRACT-FAITHFUL SUBSTITUTE**, **REPRESENTATIVE SUBSTITUTE**, **UNKNOWN-BLOCKED** (+ **INCORRECT**/**MISSING** for the current twin).

## Milestone 4 update (2026-09-07)

The Direct / current-account, statement-ingestion, reconciliation and Direct-Ledger sections were re-derived from source by
the M4 Phase-1 handoffs (`reports/implementation/m4-subagent-handoffs/T02..T07.md`) and consolidated in
`reports/implementation/m4-source-map.md`. Corrections applied here (ids from `reports/implementation/m4-contradictions.md`):

| Where | Correction |
|---|---|
| `route-matrix.yaml` `merchant_class_selectors`, new `direct_source_selection` | FTS Direct selector is `transfer_processor.go:964/1025` → `account/service.go:5314-5330` matching `source_accounts.fund_account_id` (C-006); a routing rule is only a tie-breaker among >1 source accounts, never required with one (C-003); monolith `banking_accounts.account_type` is `CURRENT`, `balance.account_type` carries `direct` (T02 #11); DCS `direct_accounts/Configs` holds only `in_flight_reservation_enabled` (T02 #4); x-balances enum/status vs seed (C-007/C-008, open) |
| `route-matrix.yaml` `ps_direct_route`, `ps_fts_direct`, `kafka`, `reservation_reconcile` | stale twin notes replaced: `[payouts_service.update_fts_fund_transfer]` is injected for every profile, the gate is the `arena_fts_meta` Splitz variant (T05 #2); Kafka FAILED/REVERSED drop root cause is the consumer (T02 #3); reconciler cron path is correct (T04) |
| `route-matrix.yaml` new `direct_status_transports` (A–I) and `direct_reconciliation_routes` (R1–R15) | every Direct status transport and every BAS/XAS/ledger-DA route with a D-006 fidelity label `real | substitute | expected-failure | missing` |
| `state-machines.yaml` | `fts_to_payout_status_map` scope (direct-webhook path only; unlisted-channel fallback; SLICE divergence, T02 #5/#6/#12); `ledger_journal.events_direct_account_x` filled (PS posts nothing — C-004/C-010); `inflight_reservation` full transition table + both release triggers (T04, C-009); new `bas_statement_linking` and `da_journal_events` machines |
| `synthetic-data-model.yaml` | M2 identity corrections (T07-C1), `fts.source_accounts.account_type` lowercase (C-005 open), routing-rule requirement (C-003), x-balances `account_type`/`status` (C-007/C-008 open), monolith `banking_accounts.account_type` (T02 #11); new `direct_merchant_record_set` and `direct_statement_and_reconciliation_records` |
| `expected-failures.yaml` | EF-004 (no DA journal for PS-recon merchants), EF-005 (`event_created_timestamp` vs `event_create_timestamp`), EF-006 (Kafka drop cross-reference), EF-007 (BalanceRefreshEvent unreachable), EF-008 (FTS health emitter missing); EF-001 attribution note corrected |
| `components.yaml`, `architecture.yaml` | XAS is one of three matchers and consumes one PS-only queue (C-011/C-013); ledger DA accounts and monolith-substitute emitter; mozart-sim/bankingaccounts-stub M4 routes; Direct rails path rewritten |

Not changed by M4: `substitute-contracts/xas-recon-repair.md` §1 and `ENV2_COMPOSE/substitutes/xas-sink/CONTRACT.md` (the
latter is owned by the T11 xas-sim work; its "payouts/ledger/FTS enqueue" claim is superseded by C-011 as recorded in
`components.yaml`). Open contradictions C-005/C-007/C-008/C-009 are marked OPEN inline and are resolved by runtime observation
(T09/T10), not by this spec. Known limits: `reports/implementation/m4-known-limits.md`.

Constraints preserved: no real customer data; no production/staging/DevStack credentials; no runtime dependency on shared
infrastructure; repositories were read only (shallow clones, `gh api` GET for history); nothing was pushed.
