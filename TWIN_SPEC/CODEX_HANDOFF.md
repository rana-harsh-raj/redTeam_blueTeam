# CODEX_HANDOFF — Payouts Twin implementation order

Scope for Codex: modify **ENV2_COMPOSE** (package only; never the repository clones, never company repos). Every item cites the source it must match. Do not guess where an item says UNKNOWN: label it in the artifact (`# ASSUMED` / `# UNKNOWN`) and keep it visible.

## Note on package state
`ENV2_COMPOSE/` was modified by another process while this specification was being written (132 files changed
between 16:23 and 17:10 on 2026-09-05, none by this investigation). The verdicts below describe the package as
read during the lanes. At 17:10 the concurrent edits had already applied: corrected cron paths for on-hold and
reservation reconcile (`scripts/cron-driver/driver.py:40-41`) and a `monolith_reader` DSN into `mysql-payouts`
(`substitutes/monolith-stub/server.py:284`). Still open at that time: `client_side_eval = true` (arena.toml:546),
splitz `DEFAULT_VARIANT = "on"`, `dual_write` → `"queued"`, `fund_transfer_attempt` singular table, `[workflow] host =
http://127.0.0.1:1`. **Re-verify every P0 item against the current file state before implementing.**

## 0. Non-negotiables
- No real customer data, credentials, or production/staging/DevStack hosts; keep preflight + egress audit gates.
- Where production behaviour is a gap (no unique index on ledger journal, no expiry callback from WFS, no stuck-initiated repair, no self-approval check), reproduce the gap; do not "fix" it in the twin.
- Every seed/config value that is not derivable from a repository stays labelled ASSUMED (see `reports/fidelity/REMAINING_INFORMATION_REQUESTS.md`).

## 1. Priority 0 — make existing verifiers meaningful (repo-only fixes)

| # | Change | Where | Source of truth | Acceptance |
|---|---|---|---|---|
| P0.1 | `client_side_eval = false` in the arena payouts template; splitz-stub default for unseeded experiments → `off`; seed `Evaluate` per (experiment, merchant) for all 38 payouts + ~30 fts + monolith gates, each labelled ASSUMED | `config/templates/base/payouts/arena.toml:546`, `substitutes/splitz-stub/server.py`, `seeds/s4/splitz_fixtures.json` | `payouts/config/prod.toml:442`; `payouts/pkg/splitz/splitz.go:91-98,149-161`; `internal/config/config.go:396-435` | PS logs show Evaluate calls; toggling `MerchantConfigViaAsvAndDcs` changes the merchant-config path |
| P0.2 | cron-driver: use `/process_beneficiary_bank_on_hold_payouts`, `/process_inflight_reservation_reconciliation`, `/payouts_dual_write_failure_processing`; add `/process_batch_submitted_payouts`, `/fund_management_payouts/check`; keep cadence in `cron_schedule.yaml` marked ASSUMED | `scripts/cron-driver/driver.py`, `seeds/s4/cron_schedule.yaml` | `payouts/internal/routing/router/cron_routes.go` | zero 404s in driver log; heartbeat key present in Redis; V12/V13 reach a live reservation |
| P0.3 | Ledger seed fix for M2: `fund_account_type=current`, add Current Receivable/Payable parents, use `current_pool_account_onboarding` in the API seeder | `seeds/s4/ledger.sql`, `seeds/s4/ledger_accounts_via_api.sh:53-62` | `fts/internal/account/source_account.go:99-101`; `ledger/internal/account/seed_data/shared_account_x.go:199-231`; SQL in `reports/fidelity/raw/07_*.md` §3 | Direct `payout_processed` journal discovery succeeds |
| P0.4 | monolith-stub: read the payout from `mysql-payouts` (read-only DSN) instead of the passport probe; `FTS_CREATE_MODE=swallow|error` with 1 s timeout + async retry; fix responses (`dual_write` → `{"status":"success"}`, `status_details_source_update` → subscriber types, stateful idempotent `deduct_credits`, `decrement_free_payouts` keyed by balance, `fetch_pricing_info` keyed by channel/method/mode/amount slab/fee_type with `error` shape); add `create_ledger`, `free_payout_rollback`, `banking_account_statement/payout_update`; drop `account_type/channel/balance_id` from the `internal/merchants` response | `substitutes/monolith-stub/server.py`, `seeds/monolith/*.json`, `docker-compose.yml` | `TWIN_SPEC/substitute-contracts/monolith-stub.md` | contract tests per route; V18 via relay path passes |
| P0.5 | API-DB stub DDL: apply `TWIN_SPEC/schema/apidb-subset.mysql.sql` (plural table names, `payouts_details` PK, `reversals` columns, `balance.type`, `features` unique); delete `seeds/mysql/apidb_seed.json`, `seeds/mysql/xbalances_seed.sql` | `seeds/schema-patches/apidb.sql`, `seeds/mysql/apidb-ddl/00_init.sql` | `api/app/Constants/Table.php`; migrations cited in `reports/fidelity/SCHEMA_PROVENANCE.md` | `SHOW TABLES` matches the schema file |
| P0.6 | Queued low-balance: monolith-stub `internal_balances_queued` also updates `balance.balance/updated_at` in the API-DB stub and POSTs `POST /v1/payouts_internal/balance_update_event {balance_id, balance}` after a ledger top-up | `substitutes/monolith-stub/server.py`, `verifier/helpers/payouts_flow.py` | `payouts/internal/app/balance/repo.go:26-51`; `payout_internal_routes.go:52-53`; `api QueuedInitiate.php:10-30` | V24a passes |
| P0.8 | Ledger processes: set `LEDGER_SCHEDULER_COMMAND=split-account-balance-update` on `ledger-scheduler` (prod CronJob override; default `[scheduler].command` is `journal-created-sns-publish`) and run one `ledger-worker` per queue via `LEDGER_WORKER_QUEUENAME` (account_create, balance_update, journal_create, ledger_entry_details_create[_pg]) instead of a single hardcoded `account_create` consumer | `docker-compose.yml` ledger services | `ledger/internal/boot/boot.go:693`; `kube-manifests/templates/ledger/templates/{split-account-balance-update-cronjob-live.yaml,balance-update-live-worker.yaml:57-65}`; `reports/fidelity/CONFIG_PROVENANCE.md` §2.6-2.7 | non-MerchantBalance ledger_entries balances update after journals |
| P0.7 | Verifier corrections: V04 (assert no unique index + single row), V06 (wait for `payout_update_failure_handling`), V07/V18 (mozart scenarios / relay path), V10 (expect 400), V16 (`PS_RELAY_MODE=drop` lost-callback semantics; label stronger checks), V21 (`/_captured/events`), V20 fetch-half | `verifier/verifiers/*` | `TWIN_SPEC/acceptance-invariants.yaml` | golden run: 0 INCORRECT expectations |

## 2. Priority 1 — bank leg and status paths

| # | Change | Source |
|---|---|---|
| P1.1 | mozart-sim: branch on `{gateway}/{version}`, real mock scenario selectors, ≥1 code per FTS classification bucket, pending-hold and latency knobs, `transfer_status` pending→terminal sequences, `RETURNED`/`return_utr` | `TWIN_SPEC/substitute-contracts/mozart-sim.md` |
| P1.2 | Kafka leg: fts `[kafka_producers.fire_transfer_status].enabled=true`, Splitz `FireStatusUpdateKafka=on` for a designated merchant; verifier injects a message to prove reversed/failed drop | `fts/config/env.prod-live.toml:315-331`; `payouts/internal/taskHandlers/fts_status_updates.go:56-113` |
| P1.3 | Direct FTS→PS leg: add `[payouts_service.update_fts_fund_transfer]` to fts arena config, send `X-Origin: payouts` from a PS-direct create (Splitz `ExperimentForFtsRequestFromPayoutsService` on) for one merchant | `fts/internal/transfer/service.go:1146-1208,1889-1910` |
| P1.4 | FTS seeds: second channel (ICICI) + DOWN `channel_information_status` rows + default (`merchant_id NULL`) direct routing rule + matching workers | `fts/internal/account/service.go:5413-5439`; `channel/service.go:590-622` |
| P1.5 | Add the 10 missing payouts worker services (mechanical copies with the real `PAYOUTS_WORKER_NAME`) | `kube-manifests/cde/payouts/values.yaml`; `payouts/internal/job/*.go` |
| P1.6 | Stork fault knobs (`STORK_DUPLICATE`, `STORK_DELAY_MS`, drop); shield `X-Test-Delay-Ms`/`X-Test-Fault`; monolith-stub `X-Test-Fault` for pricing | contracts in `substitute-contracts/` |
| P1.7 | kong-lite: impersonation (`X-Razorpay-Account` → `impersonation.Consumer`, capitalised keys), admin and identification-only shapes; keys-table indirection | `TWIN_SPEC/substitute-contracts/kong-lite.md` |

## 3. Priority 2 — promote to real / add families

| # | Change | Decision |
|---|---|---|
| P2.1 | Workflow Service **REAL**: Cassandra + Cadence server (from `uber/cadence` compose) + MySQL + `cmd/api` + `cmd/workers -WORKFLOW_DOMAINS=payouts`; `[clients.payouts_live]` → payouts-api; seed `configs` (type `payout-approval`, `enabled="true"`); point arena `[workflow] host` at it | `substitute-contracts/workflow-service.md`; Cadence version UNKNOWN |
| P2.2 | Monolith ingress module (substitute): `POST /v1/payouts` middleware order + idempotency table semantics + direct gate; `payouts_with_otp`, approve/reject (+bulk), `bulk_approve` (Batch), admin routes, `wf-service/state/callback`; dashboard fake-login → headers; OTP sentinel | `substitute-contracts/monolith-stub.md` §E, `raw/02_*.md` |
| P2.3 | Batch thin driver | `substitute-contracts/batch-service.md` |
| P2.4 | XAS substitute + BAS `payout_update` + FTS retry-preprocessor read API; recon/ART driver for Env 4; `workflow_configs` rows ASSUMED | `substitute-contracts/xas-recon-repair.md` |
| P2.5 | Split monolith-stub into role-differentiated substitutes (dual-write, post-create, source-updater) when consistency tests are in scope | `raw/12_*.md` |
| P2.6 | UPS stub or real `payments-upi` when UPI beneficiary flows are tested; ASV gRPC mock only if `MerchantConfigViaAsvAndDcs` is turned on | `substitute-contracts/dcs-splitz-pricing-shield.md` |

## 4. Acceptance tests (must pass before a run is called "production-representative")
1. All P0 verifiers pass with the corrected expectations (`acceptance-invariants.yaml` I01–I24, I30, I40–I41).
2. Per-mode fee assertion (I90) shows different fees for IMPS vs NEFT vs UPI from the seeded plan.
3. Splitz toggle test: flipping `ChargeCollectionsCallExperiment` changes the pricing source observed in PS logs.
4. Direct merchant `payout_processed` journal exists after the async job (I21) and V08 still passes.
5. Lost-callback test: `PS_RELAY_MODE=drop` leaves the payout `initiated` with no repair (I70/I80).
6. Kafka injection test proves reversed/failed drop (I71).
7. Preflight + egress audit PASS in the same window as the golden run.

## 5. Items that must remain visibly labelled, never guessed
Splitz variants (all), DCS values per real segment, FastCron cadence, `payout_details.beneficiary_bank_code` type, `sub_balances` shape, ledger merchant-side onboarding caller, bene-bank-down Redis writer, `PAYOUTS_FEATURES_*DUAL_WRITE` runtime values, `AUTHZ_XPLATFORM_ENFORCER_MOCK`, shadow-router toggles, Cadence server version, engine versions, SQS visibility/DLQ, XAS app-name credential, `workflow_configs` rows, whether Batch `bulk_approve` works in prod today.
