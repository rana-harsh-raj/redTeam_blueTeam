# Substitute contract — API monolith (`monolith-stub`)

Tier target: **CONTRACT-FAITHFUL SUBSTITUTE** for the PS/FTS-facing surface. The real `razorpay/api`
(PHP/Laravel, 23k-line route table, second live DB connection into PS's own database, Splitz/DCS/Raven/Credits
dependencies) is not worth running for this twin (lane 01 §5, lane 02 §5). Everything below is derived from
`api@2d665f9` and `payouts@4bf3dbf`; line references are in `reports/fidelity/raw/01_*.md` and `02_*.md`.

## A. Identity and lookup model (replaces the brute-force merchant probe)

| Production | Contract |
|---|---|
| Monolith reads the PS payout row over a second MySQL connection (`Connection::PAYOUT_SERVICE_DATABASE`), `SELECT * FROM payouts WHERE id=?` (`api/app/Models/Payout/Repository.php:5313-5323`); merchant = row.merchant_id (`Core.php:8461-8530`) | Stub MUST read `mysql-payouts` directly with a read-only DSN (same container the PS binary uses). Lookup by primary key. Remove `_ps_get_payout()`'s passport loop. Unknown id → HTTP 200 `{"status": null, "error": "payout not found"}` for create_fta (mirrors the monolith's outer catch shape), 404 object-shaped error elsewhere. |
| PS → monolith auth: plain HTTP Basic with one static service credential, no merchant header (`payouts/pkg/api/base.go:29-33`) | Accept Basic `[auth.api]` pair from `/run/secrets`. Ignore any merchant header. |
| Monolith → PS: Basic `payout_key/secret` + passport JWT (`Base.php:80-88,541-579`); relay clients (Status/Details/QueuedInitiate) send no actor headers (INFERRED) | Stub calls PS with Basic `[auth.api]`; passport optional on internal routes (PS internal routes are Basic-only). |

## B. Routes PS calls (all HTTP 200 with body-level `error`/`status` on failure unless stated)

| Route | Request (required fields) | Behaviour | Response |
|---|---|---|---|
| `POST /v1/payouts_service/create_fta/{payout_id}` | no body | Guard: payout status ∈ {created, initiated}, no FTA yet, and (`transaction_id` set OR balance account_type == direct); else `{"status": "<status>", "error": "Ledger entry not found in payout."}`. Mutex per payout. Create local `fund_transfer_attempts` row (see §D). Build FTS body (§C). POST fts `/v1/transfer` with `api_monolith` credential and **1 s timeout**; on timeout/any error swallow, enqueue async retry (stub: retry loop with delay, env `FTS_CREATE_MODE=swallow` default, `=error` to surface). | `{"status": "<payout status>", "error": null}`; on exception `{"status": null, "error": "<msg>"}` |
| `POST /v1/update_fts_fund_transfer` (fts credential only) | FTS webhook body (see route-matrix `fts_status_propagation.body_fields`) | 1) find FTA by `fund_transfer_id` else `(source_id, source_type)`; 2) `isValidStateTransition` else 200 `{"message":"webhook update skipped due to invalid state transition"}`; 3) save FTA; 4) POST PS `/v1/payouts/update_payouts_details_with_fts` then PATCH PS `/v1/payouts/update_payouts_with_fts` (details before status, always); 5) status via `$ftaToPayoutStatusMap` (state-machines.yaml) — CREATED/INITIATED no PS status call; unknown status → no call, log; terminal-repeat guard; no retry (FTS retries). Env `PS_RELAY_MODE=drop` keeps the lost-callback fault hook. | FTA JSON or `{"message": ...}` |
| `POST /v1/payouts_service/fetch_pricing_info` (route name `create_pricing_for_payout_service`) | `payout_id, merchant_id, balance_id, amount>=1, method, mode, channel` required; `purpose, user_id, fee_type` optional | Look up plan by (merchant pricing_plan_id, method, mode, channel, amount slab, fee_type); `fee_type=free_payout` → fees 0. Plan fixtures per synthetic-data-model.yaml `pricing_plan`. | `{"fees": int, "tax": int, "pricing_rule_id": "<14 chars>"}` (exact shape beyond these three fields not traced — mark ASSUMED) |
| `POST /v1/payouts_service/deduct_credits` | `payout_id, fees, tax, status, merchant_id, balance_id` | Stateful and idempotent per `(payout_id, source_type=payout)`: first call may reduce fees/tax from reward/fee credits (fixture per balance), repeat calls return original fees, `tax` 0, `credits_used:true`. | `{"fees": int, "tax": int, "credits_used": bool}` |
| `POST /v1/payouts_service/reverse_credits` | `entity_type in (payout, reversal), payout_id, merchant_id, balance_id, reversal_id (if reversal), fee_type` | Idempotent per source; validate fields (400 object error on missing). | `{"success": true}` (real shape not fully traced) |
| `POST /v1/payouts_service/decrement_free_payouts` | `merchant_id, balance_id, payout_id` | Counter keyed by **balance_id**; return null/no-op when balance.type != banking. | counter object (shape ASSUMED) |
| `POST /v1/payouts_service/free_payout_rollback` (`rollback_free_payouts`) | migration tool | accept, log | `{}` |
| `POST /v1/payouts_service/source_update` (`payouts_source_update`) | `payout_id, source_details[], previous_status, expected_current_status` | log (queue dispatch in prod) | `{"sources_updated": true}` |
| `POST /v1/payouts_service/status_details_source_update` | `payout_id, status_details{}, source_details[]` | return list of notified subscriber **types** (fixture list per merchant, e.g. `["workflow","webhook"]`), never echo request ids | `{"sources_updated": [<type>...]}` |
| `POST /v1/payouts_service/mail_and_sms` | `entity in (payout, transaction), type, entity_id (14), metadata{}`; for transaction also `payout_id, amount, created_at` | validate, log | `{}` |
| `POST /v1/payouts_service/dual_write` | `payout_id (14), timestamp (epoch)` (+`entity_type` branch) | log; `PS_DUAL_WRITE_MODE=fail` → 500 (reproduces 2026-09-03 incident) | `{"status": "success"}` (twin currently returns "queued" — INCORRECT) |
| `POST /v1/payouts_service/create_ledger` | `id (14), queue_if_low_balance?` | accept, log (creates monolith-side transaction in prod) | `{}` (ASSUMED) |
| `POST /v1/payouts_service/create` (`create_payout_entry`) | untraced | accept, log | `{}` (UNKNOWN) |
| `GET /v1/internal/merchants/{id}` | — | Return only what PS parses (`payouts/pkg/api/merchant_config.go:18-42`): `merchant{id,name,email,activated,live,billing_label,business_banking,hold_funds,feature[],org_id,created_at,category,country_code,category2,pricing_plan_id,purpose_code}`, `merchant_detail{business_type,business_name,company_pan,business_registered_address[,_l2,_city,_state,_country,_pin],iec_code}`. Do not expose `account_type/channel/balance_id` in the response (arena bookkeeping only). | as left |
| `GET /v1/fund_accounts_internal/fa_{id}` | — | shape per `payouts/pkg/api/fetch_fund_account.go` DTO (INFERRED from Go struct, not from PHP serializer) | fund account object |
| `GET /v1/internal_balances_queued` | `{balance_ids: [...]}` | Prod reads local `balance` table (synced from ledger by a journal-created consumer). Stub: read ledger MerchantBalance and **also update `mysql-apidb-stub.balance.balance/updated_at`** so PS's 6-hour freshness filter sees it. | `{"balances": {"<balance_id>": <amount>}}` |
| `POST /v1/merchant/on_hold_slas_internal` | merchant ids | fixture | `{"merchant_slas": {...}}` |
| `GET /v1/actor_info_internal/{user_id}` | — | derive `actor_property_key/value` from merchant_users fixture (role), not a static row | `{actor_id, actor_type, actor_property_key, actor_property_value}` (field names from PS DTO, unconfirmed against PHP) |
| `POST /v1/users_internal` | `user_ids|user_emails|user_contacts` | keyed map of full user entities + `is_password_set` | `{"<id>": {...user...}}` |
| `POST /v1/payouts/banking_account_statement/payout_update` → in prod `bas_recon_payout_update` on the monolith: | `bas_id, entity_id, entity_type in (payout, payout_reversal), merchant_id, transaction_date; converted_from_external, utr, grn, cms_ref_no` | for PS-owned payouts forward to PS `POST /v1/payouts/banking_account_statement/payout_update` | `{}` |

## C. FTS create body the stub must send (`api/app/Services/FTS/FundTransfer.php:313-356,424-551`)

```
product: payout | payout_refund | ES_ON_DEMAND | customer_wallet | penny_testing
merchant_id
transfer: { preferred_mode, amount, narration, source_id (bare payout id), source_type: "payout", initiate_at,
            preferred_channel (YESBANK→ICICI for non-DIRECT; AMAZONPAY→amazon_pay_fts),
            preferred_source_account_id (int, only when shouldFetchSourceFtsFundAccountId),
            request_meta{...} (conditional), is_batch }
merchant_category: { category, mcc, onboarded_time }
account: { bank_account | vpa | card | wallet | fund_account_id }
2fa: { otp } (ICICI CA only)
```
`shouldFetchSourceFtsFundAccountId`: `[true,false]` when balance DIRECT and channel in non-transaction channels;
`[true,true]` when SHARED and (sub-merchant on direct master OR sub-account payout); else no field.
No `X-Origin` header, no `origin_service` marker (FTS therefore records origin `API`).
Auth to FTS: static `api_monolith` Basic pair.

## D. Local tables the stub owns (API-DB stub, corrected DDL in `TWIN_SPEC/schema/apidb-subset.mysql.sql`)

`fund_transfer_attempts` (plural), `payouts_details` (PK `payout_id`, no `beneficiary_bank_code`), `payouts_status_details`,
`reversals` (polymorphic `entity_id/entity_type`, `fee` singular, no `payout_id`), `balance` (no `primary` column;
`type='primary'|'banking'`), `features` (`UNIQUE(name, entity_id)`), `workflow_entity_map`, `idempotency_keys`,
`banking_accounts`, `merchants`, `merchant_users`, `keys`.

## E. Ingress surface (currently MISSING; needed for dashboard/approval/admin families)

Implement only when those families are in scope; contracts in `reports/fidelity/raw/02_*.md` §1-§5:
- `POST /v1/payouts` classic path: middleware order, `MerchantIdempotencyHandler` (table `idempotency_keys`, mutex 1200 s, hash compare, cached replay, fail-closed), Splitz direct gate (three hard refusals + configurable divert), forward to PS with passport + `X-Payout-Actor-*` (classic) or none (direct), `X-Payouts-Service-Proxy` 1|0|absent.
- `payouts_with_otp`, `payouts/{id}/approve|reject`, `payouts/approve|reject/bulk`: OTP sentinel (`000000` success), role check via `merchant_users.role` → `bankingRoutePermissions`, PS calls `POST /v1/payouts/payouts_internal/{id}/approve {queue_if_low_balance}` / `reject`.
- `payouts/bulk_approve` (Batch): `X-Batch-Id` required, ≤15 rows, `payout_update_action A|R`, no OTP, per-row `Core::approvePayout` → WFS `ActionAPI` when `workflow_entity_map` row exists else legacy.
- Admin: `admin/payouts/cancel`, `payouts/manual_action` (forward 3 actions to PS `/v1/payouts_internal/manual_action`), `payouts/{id}/manual/status` (PROCESSED/REVERSED/FAILED; shared guard needs `fts_fund_account_id`+`fts_account_type`), `payouts/{id}/retry`, 3-month auto-cancel cron.
- `wf-service/state/callback` → forward `POST PS /v1/workflow/state`.

## F. Explicit non-goals
Rate limiting (none in prod), loop guard at monolith (logs only), FAV freshness gate (does not exist).
