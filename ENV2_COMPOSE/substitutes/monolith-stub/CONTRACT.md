# monolith-stub substitute contract

Stands in for the legacy `api` monolith for the payouts-service-facing
surface (`/internal/merchants/{id}`, the `payouts_service/*` callback
group, `fund_accounts_internal`, `merchant/on_hold_slas_internal`,
`update_fts_fund_transfer`). Routes and their controller/allowlist
grounding come from `findings/20_api_monolith.md` §1 (route table, confirmed
via `Route.php` grep with line numbers) and §2/§4 (proxy + FTS-relay
mechanics). Exact request/response field names come from a direct read of
`payouts/pkg/api/*.go` (the payouts-side client DTOs) — see each seed
file's `_source` note for which `.go` file backs which endpoint.

Rewritten in this pass from an earlier placeholder (`/v1/contacts`,
`/v1/fund_accounts`, `/v1/merchants` — none of which are real monolith
routes per findings/20) to the confirmed route set below.

## Endpoints

| Method | Path | Route name (findings/20) | Seed source |
|---|---|---|---|
| GET | `/internal/merchants/{id}` | `internal_merchant_fetch` | `seeds/monolith/merchants.json` |
| POST | `/payouts_service/fetch_pricing_info` | `create_pricing_for_payout_service` | `seeds/pricing.json` |
| POST | `/payouts_service/deduct_credits` | `deduct_credits_via_payout_service` | sink (200, echoes fees/tax) |
| POST | `/payouts_service/reverse_credits` | `reverse_credits_via_payout_service` | sink (200, `success:true`) |
| POST | `/payouts_service/source_update` | `payouts_source_update` | sink (200, `sources_updated:true`) |
| POST | `/payouts_service/status_details_source_update` | `status_details_source_update` | sink (200, echoes source ids updated) |
| POST | `/payouts_service/mail_and_sms` | `payouts_service_mail_and_sms` | sink (200, `{}`) |
| POST | `/payouts_service/dual_write` | `payouts_service_dual_write` | sink (200, `{status:"queued"}`) |
| POST | `/payouts_service/decrement_free_payouts` | `decrement_free_payouts_payouts_service` | `seeds/pricing.json` `free_payout_counters` (mutated in-memory) |
| GET | `/fund_accounts_internal/fa_{id}` | `fund_account_get_internal` | `seeds/monolith/fund_accounts.json` |
| POST | `/merchant/on_hold_slas_internal` | `on_hold_merchant_slas_internal` | `seeds/monolith/misc.json` |
| GET | `/actor_info_internal/{user_id}` | **not confirmed** (see below) | `seeds/monolith/misc.json` |
| POST | `/users_internal` | **not confirmed** (see below) | `seeds/monolith/misc.json` |
| POST | `/update_fts_fund_transfer` | `update_fts_fund_transfer` | relay, see below |
| GET | `/_arena/log` | stub-only | in-memory sink log (dual_write/source_update/status_details_source_update/mail_and_sms/decrement_free_payouts/update_fts_fund_transfer inbound bodies) |

Every route above except `/_arena/log` and `GET /health` requires HTTP
Basic-Auth matching the configured credential (`STUB_BASIC_AUTH_FILE`,
mounted from `secrets/auth_monolith_shared.txt`, username `rzp_live` per
`payouts.toml.tmpl`'s `[api.auth]`).

## Not-confirmed routes (`actor_info_internal`, `users_internal`)

`findings/20_api_monolith.md` traced `Route.php`'s route table
programmatically and did **not** find route names matching these two paths
— only the payouts-side Go client DTOs are confirmed real
(`payouts/pkg/api/fetch_actor_info.go` `ActorInfo`,
`user_fetch_multiple.go` `UserDetailsMultipleResponse`, the latter a raw
`map[string]interface{}` so any JSON object is structurally valid against
it). This stub answers both with best-effort fixture rows
(`seeds/monolith/misc.json`, ids matching `passport_fixtures.json`'s M3
finance_l1/l2/owner users) rather than omitting them, per the task's
explicit ask — flagged here as unconfirmed, not silently presented as
verified.

## `fetch_pricing_info` — special-case overrides

Mirrors the real ccSdk mock's own override precedence
(`seeds/pricing.json` `special_case_overrides`): `purpose == "rzp_fees"` or
`"refund"` → fee/tax `0`; `fee_type == "free_payout"` → fee/tax `0`;
otherwise looked up by `(merchant_id, mode)` in `seeds/pricing.json`
`plans` — flat Rs 2 (200 paisa) + 18% GST (36 paisa) for every
mode/merchant per the task's explicit requirement.

## `update_fts_fund_transfer` relay

Per `findings/20_api_monolith.md` §4: the real monolith commits the FTA row
update FIRST (not modeled here — this stub commits nothing, matching the
task spec), then makes exactly **one** HTTP call to PS
(`Status.updatePayoutStatusViaFTS`, no retry — "PS may already have created
the payout, retrying could pay the beneficiary twice") — reproduced as a
single-attempt `PATCH {PAYOUTS_API_HOST}:{PAYOUTS_API_PORT}/v1/payouts/update_payouts_with_fts`
with a Basic-Auth header (`PS_RELAY_AUTH_USER` + a password read from
`PS_RELAY_AUTH_PASS_FILE`). `PS_RELAY_MODE` env (`relay` default, or
`drop`) simulates a lost relay for testing FTS/payouts' own reconciliation
paths — `drop` swallows the inbound call and never sends the PATCH.
`GET /_arena/log` records the inbound body either way. Validated with a
throwaway local HTTP server standing in for `payouts-api` on
`127.0.0.1:19400`: `relay` mode produced a real PATCH (confirmed via
`relay_status_code: 200` in the stub's own response, i.e. `urlopen`
actually round-tripped to the mock server) with the configured
Basic-Auth header; `drop` mode produced `{"relayed": false}` with no
outbound call.

## Health

`GET /health` → 200 (unauthenticated, per shared `_common/base_stub.py`).

## Validated

`python3 -m py_compile`; ran `server.py` on port 18804/18805, curled every
route above (merchant fetch, pricing incl. `rzp_fees` override,
`decrement_free_payouts` x2 confirming the in-memory counter increments,
fund-account fetch for the `fa_` + suffix-stripped id incl. the
9999-suffix/inactive account, `on_hold_slas`, `actor_info`, `users_internal`,
`dual_write`/`mail_and_sms` sinks, `_arena/log` showing all sink calls) and
both `PS_RELAY_MODE` values against a throwaway mock `payouts-api`.

## M4 (T11): `banking_account_statement/payout_update` relay + Direct-account (DA) ledger emitter

Routes: `POST /v1/banking_account_statement/payout_update` (api `Route.php:4837`
`bas_recon_payout_update`, `payouts_service` group — the path XAS's gateway posts to,
`x-account-statements/internal/gateway/api/service/service.go:34`) and the earlier
`/v1/payouts/banking_account_statement/payout_update` alias. Handler = `Payout/Core.php:10305-10318`
`payoutUpdateByBASRecon`: the 9-field XAS `EnrichmentUpdateRequest` `{bas_id, entity_id, entity_type,
merchant_id, transaction_date, converted_from_external, utr, grn, cms_ref_no}` is forwarded **verbatim** to the
REAL PS `POST /v1/payouts/banking_account_statement/payout_update` (Basic `api` credential; payouts
`dtos.PayoutUpdateBASEntityRequest` reads the same 9 names) and PS's status/body is returned. Validation kept
from the earlier pass: the 5 mandatory fields and `entity_type ∈ {payout, payout_reversal}`.

**DA ledger emitter (substitute)** — `send_to_ledger_post_source_entity_processing`, reproducing
`BankingAccountStatement/Core.php:3081-3091` (gate), `:5175-5228` (`sendToLedgerPostSourceEntityProcessing`),
`:5243-5278` (`processLedgerPayoutForDirect`) and `Transaction/Processor/Ledger/Payout.php:235-425,717-756`
(`pushTransactionToLedgerForDirect`, `getDefaultPayloadForDirectPayout`) + `Ledger/Base.php:180`
(`pushToLedgerSns`). Runs after a 2xx relay, i.e. at the moment a Direct payout becomes linked.

Gates, in order (each skip is recorded in `GET /_arena/ledger_emits` with `skipped=<reason>`):
`relay_not_2xx` · `ledger_disabled` (`LEDGER_ENABLED=false` = `applications.ledger.enabled`) ·
`entity_type_not_payout` · `payout_not_found` (PS row) · `shared_or_primary_balance` (merchants.json
`account_type != direct`) · `high_tps_composite_payout` · **`da_ledger_skipped_ps_recon`** (feature
`payout_service_txn_recon` on AND `entity_type == payout`: the monolith defers to PS "which already does this",
but PS has that call commented out — `payouts/core.go:7284`; the preserved production gap) ·
`da_ledger_journal_writes_off` · `charge_collections_not_modelled` · `basd_not_found` ·
`reversal_not_found` (payout_reversal without a PS `reversals` row).

Journals published: `payout` → `da_payout_processed` then `da_payout_processed_recon` (`rzp_fees` purpose →
`da_fee_payout_processed`); `payout_reversal` → `da_payout_reversed` + `da_payout_reversed_recon`
(transactor `rvrsl_<reversal id>`); `converted_from_external=true` → single `da_ext_payout_processed` /
`da_ext_payout_reversed` (`Payout/Core.php:10003-10016`). Payload = the ledger `Journal` struct
(`ledger/internal/job/job_sqs/journal_create.go:56-75`): `tenant X, mode live, idempotency_key (uuid1 per
publish, as Uuid::uuid1 — env DA_LEDGER_IKEY_MODE=deterministic switches to a sha-derived key), merchant_id,
currency, amount/base_amount (payout.amount), commission (fees), tax, identifiers {"banking_account_stmt_detail_id":
"basd_<id>"[, product_id]}, additional_params {[fee_accounting: reward][, account_type]}, notes {"balance_id":
"bal_<id>", "transaction_id": "bas_<bas id>"}, transactor_id pout_/rvrsl_, transactor_event, transaction_date,
api_transaction_id (processed/reversed only)`. The BASD id comes from the PS
`banking_account_statement_details` row for the payout's balance (`monolith_reader` now has SELECT on that
table and on `reversals`, `scripts/provision-monolith-db.sh`).

Transport: SNS topic `api-ledger-journal-create-live` (payouts `appConstants/constants.go:381`; the monolith's
`LEDGER_TRANSACTION_CREATE`) created idempotently on LocalStack at first use and subscribed to SQS
`journal_create` with `RawMessageDelivery=false`, so the REAL `ledger-worker-journal-create` receives the
production SNS envelope and unwraps `Message` (`job_sqs/base.go extractRawContent`). If SNS fails three
times, a direct `SendMessage` of the raw JSON is used and recorded as `transport=sqs_direct_fallback`.

Feature source: merchants.json `merchant.feature` (boot) + runtime overrides `POST /_arena/merchant_features
{merchant_id, features:{name: bool}}` / `GET /_arena/merchant_features?merchant_id=` (lost on restart).
Evidence: `GET /_arena/ledger_emits[?merchant_id=]` (payload, transport, SNS message id, skips) and the
`da_ledger_emitter` entries in `GET /_arena/log`.

Declared deviations (`config/declared-deviations.yaml` DEV-163..166): trigger point relocated from the
monolith's own statement-fetch linking to the XAS `payout_update` arrival; `notes.transaction_id` is
`bas_<id>` (PS mirror form) instead of the monolith's `txn_<api transaction>`; `transaction_date` for
`da_payout_processed` is PS `payouts.updated_at` (no `processed_at` column in PS); `payout_service_txn_recon`
is a per-merchant stub feature, not a Splitz evaluation.
