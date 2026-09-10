# Tentative replication workflow — primary domain: Source-to-Pay (Vendor Payments + Tax Payments/TDS)

Status: **design only** (task scope). No domain code was built. The one bootstrap component that
is cheap and validates the integration seam is listed at the end as an optional first step.
Base twin: `ENV2_COMPOSE` arena at tag `assurance-m5-autonomous-discovery` (26 real services + 15
substitutes; monolith substitute already serves `payouts_internal`, `internalContactPayout`,
contacts/fund-accounts internal routes; workflow-engine reconstruction from M5).

## 1. Repositories to ingest (pinned commits from `clone_results.log`)

| Repo | Commit | Tier | Role |
|---|---|---|---|
| razorpay/vendor-payments | `20c4f4d59970` | F3 (real) | invoices, PO/GRN, TDS/tax payments, vendor advances/settlements, accounting-payouts |
| razorpay/vendor-experience | `df90df21bee5` | F3 (real, Cadence) or F2 (portal stub) | vendor portal/onboarding |
| razorpay/accounting-integrations | `fc13a0b2a382` | F3 (real) | accounting leg (Zoho/Tally stubbed) |
| razorpay/proto | `52682577d79d` | contracts | Twirp/gRPC for vendor-payments, tax-payments, vendor-portal, accounting-payouts, callback |
| razorpay/workflows | `080d71a51b77` | already F2/F3 (M5 engine) | 4 new workflow config types |
| razorpay/api | `2d665f918b60` | reference for monolith substitute | routes `tax-payments/*`, `vendor-payments/*`, `payouts_internal/{id}/tax-payment-id`, SourceUpdater |
| razorpay/payouts, ledger, fts, cfa, x-balances | as pinned | already F3 | unchanged |
| razorpay/x | `40b093f97ec1` | optional frontend | VendorPayouts, TaxPayments views |

## 2. Build order

1. **Contracts**: regenerate Twirp/gRPC stubs from `proto/` with `rpc/buf.gen.twirp.yaml` (rpc clone failed earlier; buf regeneration avoids needing it). Verify `vendor-payments` compiles against them (`go build ./...`; module already vendors stubs — confirm).
2. **Substitutes first** (all loopback-only, in `ENV2_COMPOSE/substitutes/`):
   - `icici-directtax-sim` (mozart-style fixture server for `/api/v1/directTaxTin2/{paymentProcess,verification,debitAdvice}` + token; scenario selection by exact field match, like `mozart -mock`).
   - `gst-ocr-stub` (MastersIndia + Veryfi + Sandbox.co.in canned responses).
   - `abacus-stub`, `metro-stub`, `ufh-stub`, `reminders-stub`, `gimli-stub`, `mailgun-sink` (HTTP 200 + capture).
   - `bvs-stub` (from `business-verification-service-sdk-go` contract; deterministic verified/failed).
   - `tax-compliance-sink` (SQS queue `tax-compliance-payout-events` on the existing LocalStack + Vault dev server for payouts `[hvault]`).
   - `zoho-tally-sink` for accounting-integrations.
   - Extend `monolith-stub` with: `tax-payments/*` Twirp proxy → vendor-payments; `PATCH payouts_internal/{id}/tax-payment-id`; `SourceUpdater` fan-out for `payout-updates-vendor-payments` → `PayoutStatusChange`; `tds_categories`; `vendor-payments/*` proxy.
   - Extend `workflow-engine` with config types `vendor-payment-v2-approval`, `purchase-order-approval`, `goods-received-note-approval`, `vendor-onboarding-approval` and the `CallbackApi.WorkflowStateCallback` Twirp callback.
3. **Data plane**: MySQL schema for vendor-payments from `internal/migrations` (88), Elasticsearch single node (or the in-repo `elasticservice` fallback if one exists — verify), Kafka topics (reuse arena Kafka): `add-tds-entry`, `prod-tds`, `prod-email-int`, `prod-auto-invoice-processing`, `prod-po-status-update`, `prod-grn-status-update`, `prod-vp-status-update`, `prod-fetch-gstr-2a/2b`, `prod-vp-gstr-2b-recon`, `prod-ocr`, `prod-fund-account-verification`, `prod-elastic-data-ingestion`, `x.vendor-payments.accounting-payouts.status-update`.
4. **vendor-payments** web + the 11 worker entrypoints (`internal/tasks/*.go RegisterTask`); config from `config/default.toml` with hosts rewritten to arena names (api → monolith-stub, workflows → workflow-engine, stork → stork-capture, bvs/abacus/metro/ufh → stubs, ICICI → sim).
5. **vendor-experience** (Cadence) — first pass as F2 portal stub exposing `VendorApi.BulkInvite`/`DcsAPI` and OTP; second pass real with a Cadence container if memory allows (M5 notes: arena is memory-constrained).
6. **accounting-integrations** with Kafka consumer on the status topic and sink for Zoho/Tally.
7. **SNS wiring**: add `payout-updates-vendor-payments` topic to payouts config `[source_update_topics]` and subscribe the monolith-stub HTTP endpoint (mirrors prod: SNS → monolith → service).
8. **Frontend (optional)**: `x` VendorPayouts/TaxPayments views behind kong-lite routes `/v1/vendor-payments/**`, `/v1/tax-payments/*`.

## 3. Service graph (arena additions)

```
x (VendorPayouts/TaxPayments) ─┐
                               ▼
kong-lite ──/v1/vendor-payments/**──► vendor-payments ──POST v1/payouts_internal/──► monolith-stub ──► payouts ──► ledger/fts/cfa
           ──/v1/tax-payments/*──► monolith-stub (Twirp proxy) ──► vendor-payments ──POST v1/internalContactPayout/ (rzp_tax_pay)──► monolith-stub ──► payouts
vendor-payments ──workflow create──► workflow-engine ──WorkflowStateCallback──► vendor-payments
payouts ──SNS payout-updates-vendor-payments──► monolith-stub (SourceUpdater) ──PayoutStatusChange──► vendor-payments
vendor-payments ──Kafka accounting-payouts.status-update──► accounting-integrations ──► zoho-tally-sink
vendor-payments ──► icici-directtax-sim | gst-ocr-stub | bvs-stub | abacus-stub | metro-stub | ufh-stub | stork-capture | vendor-experience(stub)
kafka add-tds-entry (fixture publisher) ──► vendor-payments initiate-tds worker
payouts ──[hvault]──► vault-dev (tax-compliance-vault substitute); payouts/monolith ──SQS──► tax-compliance-sink
```

## 4. Minimum synthetic data

- 2 merchants from the existing Direct-account fixture (`reports/SYNTHETIC_FIXTURE_SPEC.md`), one with `skip_wf_for_vendor_payments`-style flags off (workflow on), one with workflow off.
- Roles: owner, finance_l1 (maker), finance_l2 (checker), operations, view_only, plus `vendor` portal user; permissions from authz `xplatform*.csv` for vendor payments/PO/GRN/tax.
- 3 vendors (contact type `vendor`) with bank fund accounts (one failing FAV), 1 internal `rzp_tax_pay` contact + fund account auto-created by `internal_tax_contact.go`.
- 6 invoices (2 with OCR fixture files), 2 purchase orders, 2 GRNs, 1 vendor advance, 1 vendor settlement.
- TDS: 3 `tds_categories` (194C, 194J, 194H), `entity_tax` rows per invoice, 1 monthly batch; 2 `add-tds-entry` fixture messages.
- GST: 1 GSTR-2B fixture, 2 input-tax-credit rows.
- Control policies (`controlpolicy`) for approval thresholds; 1 custom field.
- Accounting: 1 Zoho org mapping; expected sync_status rows.

## 5. Critical journeys (acceptance gates)

| # | Journey | Gate |
|---|---|---|
| J1 | Invoice → maker submits → checker approves (workflow-engine) → payout created via `payouts_internal` → FTS processed → SNS → `PayoutStatusChange` → invoice `paid` | end-to-end status parity + idempotency on retry |
| J2 | Invoice rejected by checker → no payout | negative control |
| J3 | Monthly TDS: publish `add-tds-entry` → `InitiateMonthlyPayouts` → `internalContactPayout` (`rzp_tax_pay`) → tag-back `tax-payment-id` → challan via `icici-directtax-sim` | `payouts_details.tax_payment_id` set; contact-type gate rejects wrong app |
| J4 | Internal-app authorization: app `xpayroll`-style caller attempts `rzp_tax_pay` payout → rejected by `type.go:35-48` | boundary test |
| J5 | PO → GRN → invoice 3-way match with `purchase-order-approval` + `goods-received-note-approval` | state machine parity |
| J6 | Vendor invite → portal OTP → BVS verified → fund account created → first payout | identity provenance |
| J7 | Vendor advance + vendor settlement source types share the SNS topic; monolith fan-out routes each correctly | source-type routing |
| J8 | Accounting: status Kafka → accounting-integrations → sink shows synced invoice/payout | downstream leg |
| J9 | Cancel queued tax payout (`CancelQueuedPayoutCron`) → `payouts_internal/{id}/cancel` | cancel path (tax payouts are cancellable, `Payout/Core.php:3087-3090`) |
| J10 | Restart-recovery of workers mid-batch (Kafka offsets; idempotent create) | durability |

## 6. External dependencies requiring replacements

| Dependency | Replacement | Fidelity |
|---|---|---|
| ICICI direct tax (`apibankingone.icicibank.com`) | `icici-directtax-sim` fixtures | F2 (needs prod samples for byte parity) |
| MastersIndia, Veryfi, Sandbox.co.in | canned JSON | F1 |
| BVS | contract stub from SDK | F2 |
| abacus, metro, ufh, reminders, gimli, mailgun | HTTP stubs/sinks | F1 |
| Zoho/Tally/QuickBooks | sinks | F1 |
| tax-compliance (+vault) | SQS sink + Vault dev | F2 |
| Cadence (vendor-experience) | stub first; real Cadence optional | F2→F3 |
| Elasticsearch | single node | F3 |
| External cron scheduler | local cron container | F2 |

## 7. Contract tests

- Twirp `razorpay.vendorpayments.taxpayments` and `vendorpayments` request/response shapes generated from `proto/` (golden JSON per rpc used by J1–J9).
- Monolith substitute routes: `POST v1/payouts_internal/`, `POST v1/internalContactPayout/`, `PATCH v1/payouts_internal/{id}/tax-payment-id`, `POST v1/payouts_internal/{id}/cancel`, `GET v1/banking_accounts_internal/`, `v1/contacts_internal/`, `v1/fund_accounts_internal/` — compare against `api/app/Http/Route.php` handlers (same method as `reports/implementation/CONTRACT_FIXES.md`).
- SNS message schema from `payouts/pkg/sourceupdater/event.go` vs monolith `SourceUpdater` parser.
- Workflow callback `CallbackApi.WorkflowStateCallback` payload vs `workflows` callback builder.
- Kafka `add-tds-entry` message vs `internal/tasks/initiatetds.go` decoder; accounting status message vs accounting-integrations decoder.
- Auth: basic-auth credential names from `vendor-payments/internal/boot/helpers.go:187-269` and payouts `[auth.*]`.

## 8. Clean-boot test

Extend `ENV2_COMPOSE/scripts/local-acceptance.py` manifest with an `s2p` profile:
`down -v → up (profile s2p) → wait healthy (vendor-payments /health, workers registered, ES green, topics created) → seed → J1..J10 with `--with-egress-audit` → audit.py (loopback-only; the only permitted external host remains the model gateway) → compare-replays → gates`. Target: two consecutive clean boots with identical gate JSON (as in M3.1).

## 9. Daily snapshot and incremental refresh

See `SNAPSHOT_REFRESH_PLAN.md` (inputs: 3 repos + proto + monolith route/updater files + config-proto; refresh = pinned-commit diff, contract regeneration, migration replay, gate rerun).

## 10. Optional bootstrap component (small, validates the seam)

A `vp-caller-sim` in `ENV2_COMPOSE/substitutes/` that replays J3's three HTTP calls
(`internalContactPayout` with `rzp_tax_pay`, `tax-payment-id` tag-back, cancel) against the live
arena using the `vendor_payments` internal credential. It proves the monolith-stub → payouts →
contact-type gate seam before vendor-payments itself is ingested. Not built in this task.

## 11. Effort estimate

| Phase | Effort |
|---|---|
| Contracts + substitutes | 3–4 days |
| vendor-payments boot + workers + data | 3–4 days |
| Journeys J1–J10 + gates + clean boot | 3 days |
| vendor-experience real (Cadence) + accounting | 2–3 days |
| Total | ~2.5 weeks, one engineer, assuming no new access |
