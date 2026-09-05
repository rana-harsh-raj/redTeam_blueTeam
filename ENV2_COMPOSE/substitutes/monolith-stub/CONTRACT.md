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
