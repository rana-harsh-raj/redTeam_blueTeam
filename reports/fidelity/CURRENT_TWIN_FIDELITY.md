# CURRENT_TWIN_FIDELITY — how faithfully ENV2_COMPOSE reproduces production Payouts (2026-09-05)

Verdict scale: **REAL** (actual code + schema), **CONTRACT-FAITHFUL** (validated against source/fixtures), **REPRESENTATIVE** (shape right, behaviour partly inferred), **INCORRECT** (contradicts source), **MISSING**, **UNKNOWN-BLOCKED**. Row-level detail: `PRODUCTION_VS_TWIN_MATRIX.csv`. Corrections to earlier reports: `ARCHITECTURE_CORRECTIONS.md`. Evidence: `raw/01..12_*.md`.

> Package state caveat: `ENV2_COMPOSE/` was being edited by a separate process during this pass (132 files changed
> 16:23–17:10, 2026-09-05; not by this investigation, which was read-only on the package). Verdicts reflect the files
> as read by the lanes; some fixes (cron paths, a `monolith_reader` DSN) were already present by 17:10. See
> `TWIN_SPEC/CODEX_HANDOFF.md` "Note on package state".

## 1. What the twin gets right

| Area | Verdict | Why |
|---|---|---|
| Payouts Service binary (api, 14 workers, 2 Kafka consumers), state machine, idempotency, validation, scheduled slots, passport verification (v3 static kid) | REAL | same compiled code; V01/V02/V03/V05/V09/V11/V14/V24b pass on real mechanisms |
| Ledger (binary, migrations, `ledger_config` via `LedgerConfigAPI/CreateInBulk` from the repo's own Go seed) | REAL | `payout_initiated` authorization, balance debit, top-ups via `positive_adjustment_processed` are the production mechanisms |
| FTS (binary, migrations, route selection code, FSM, webhook code, 3×200 s retry) | REAL | seeds are the only synthetic part |
| CFA, x-balances | REAL | 1:1 topology for CFA; hash algorithm reproduced exactly |
| Monolith FTS-webhook relay (`update_fts_fund_transfer`: details-before-status, exact PS bodies, `$ftaToPayoutStatusMap`) | CONTRACT-FAITHFUL | byte-level match with `api Attempt/Core.php`, `Status.php` |
| `GET /internal/merchants/{id}` (fields PS actually parses) | CONTRACT-FAITHFUL | matches `payouts/pkg/api/merchant_config.go` |
| Kong-lite passport for merchant API keys; `[auth.*]` service credentials; Stork capture + HMAC receiver; Shield stub; DCS stub wire bytes (+ documented arena patch); Kafka broker + consumers | CONTRACT-FAITHFUL | verified against `goutils/passport`, `stork/internal/webhook/core.go`, `shield-sdk`, `config-proto` |
| Safety envelope (internal network, preflight, egress audit, generated secrets, no credentials in images) | n/a | reproduced and audited |

## 2. Largest fidelity errors (ordered by blast radius)

| # | Gap | Effect on findings | Fix source |
|---|---|---|---|
| 1 | **Splitz**: arena `client_side_eval = true` (prod `false`) + stub `FetchDecisionContext` empty + unseeded default `on` | every payouts experiment silently takes the code default; pricing source, merchant-config source, bulk concurrency, workflow naming, direct cutover cannot be exercised; verifier results on Splitz-gated paths are meaningless | repo-only (config + stub) |
| 2 | **Monolith stub identity model**: brute-force passport probe instead of reading PS DB; `create_fta` hard-fails instead of 1 s timeout + async requeue; INCORRECT responses (`dual_write`, `status_details_source_update`, `deduct_credits`, `fetch_pricing_info` ignoring channel/method/amount, `decrement_free_payouts` key) | masks cross-merchant isolation bugs; hides production's "FTS slow ⇒ success-shaped response"; fees never vary | repo-only |
| 3 | **Ledger M2 seed**: FTS Current account seeded as `nodal`, Current parents missing | any Direct `payout_processed`/`payout_reversed`/FAV journal fails discovery | repo-only (fix SQL in lane 07) |
| 4 | **Cron driver**: 3 wrong `/v1/cron/*` paths, `process_batch_submitted_payouts` and `fund_management_payouts/check` missing; cadence assumed | reservation reconciler heartbeat never set ⇒ gate fails closed (V12/V13); on-hold cancels never run; bulk NEFT/RTGS never dispatch | repo-only (cadence needs FastCron export) |
| 5 | **Workflow Service absent** (`[workflow] host` dead) | any workflow-applicable create fails with 400; approval family untestable | REAL WFS (Cassandra+Cadence) or substitute |
| 6 | **Ingress layer absent** (monolith `POST /v1/payouts` middleware, dashboard proxy, OTP, role checks, Batch) | public-API classic validations, idempotency replay semantics, dashboard/approval/bulk/admin families untestable | substitutes (contracts written) |
| 7 | **Mozart-sim** ignores gateway/version, 6 of ~140 bank codes, no pending-hold knob | bank-error semantics, ambiguous states, per-bank routing untested; pre-terminal verifiers skip | repo-only (contract written); real Mozart blocked by private modules |
| 8 | **API-DB stub DDL drift** (`fund_transfer_attempt`, `payout_status_details`, `banking_account` names; `payouts_details.id`; `reversals.payout_id/fees`; `balance.primary`; no `features` unique) and dead seed files | direct-DB fallbacks and dual-write reads hit wrong shapes | repo-only |
| 9 | **Kafka producer leg off** (fts `enabled=false`, Splitz off) | Kafka status path (drops reversed/failed) untested end-to-end | repo-only (config) |
| 10 | **Queued low-balance**: stale `balance.updated_at`, no `balance_update_event` caller | V24a fails; dequeue semantics untested | repo-only (stub updates API-DB balance + posts event) |
| 11 | **Statements/recon/repair absent** (XAS, BAS `payout_update`, ART routes caller, retry-preprocessor gate, admin routes) | return-credit reversals and Env 4 repair untestable | substitutes (contracts written); `workflow_configs` rows external |
| 12 | **Topology coverage**: 10 of 24 payouts workers, 12 of ~157 FTS workers, 1 of 5 x-balances bank workers, one monolith-stub for ~45 monolith worker roles | fund-management, data-consistency, bulk batch dispatch, non-RBL banks untested | repo-only (mechanical) |
| 13 | **Passport shapes**: only merchant API-key shape; keys embed merchant id | partner/sub-merchant, admin, dashboard-proxy identity paths untestable | repo-only |

## 3. Verifier re-reading against source

| Verifier | Original reading | Corrected reading |
|---|---|---|
| V04 | ledger dedupe | prod has **no** unique index; rewrite to assert app-level single row + document race (I03) |
| V06 | timing/headroom | `payout_processed` is async (job on `payout_update_failure_handling`) and Shared-only; wait for the job; fix M2 seed for Direct |
| V07/V18 | timing | stimulus must traverse the monolith remap (or mozart `RETURNED`/`INSUFFICIENT_FUND`), not a synthetic PS webhook |
| V10 | expected failed payout | production rejects at create with 400 "not enough balance" when `queue_if_low_balance=false` (correct behaviour) |
| V12/V13 | DCS key shape | DCS bytes and reachability are fine; reconciler heartbeat missing (cron path) ⇒ gate fails closed; Splitz path also affects merchant-config source |
| V16 | stuck-initiated must not self-heal | production has no repair for `initiated`; the twin should test "lost callback ⇒ stays initiated" (`PS_RELAY_MODE=drop`) and label auto-heal expectations as stronger-than-prod |
| V21 | blocked by fidelity gap | stale endpoint path; stork-capture already captures `/_captured/events` |
| V24a | balance freshness | needs API-DB `balance.updated_at` refresh + `balance_update_event` (transitional in prod) |

## 4. Fidelity tier summary by component

REAL: payouts, ledger, fts, cfa, x-balances, Kafka broker/consumers, PS passport verification.
CONTRACT-FAITHFUL: monolith relay + merchants route, kong-lite (API-key shape), stork-capture, merchant sink, shield-stub, dcs-stub, ledger_config load, M1/M3 ledger seeds, CFA seeds.
REPRESENTATIVE: monolith-stub (other routes), mozart-sim, pricing, cron-driver, banking-accounts stub, FTS/x-balances worker subsets, FTS seeds (RBL only).
INCORRECT: splitz-stub for payouts, ledger M2 FTS account seed, API-DB DDL details, cron paths, `[workflow]` host, several monolith-stub responses.
MISSING: monolith ingress, dashboard, OTP, WFS, Batch, XAS/recon/repair, UPS, Kafka producer leg, 10 payouts workers.
UNKNOWN-BLOCKED: real Mozart, Splitz/DCS runtime values, FastCron cadence, out-of-band prod DDL, Cadence/engine versions.
