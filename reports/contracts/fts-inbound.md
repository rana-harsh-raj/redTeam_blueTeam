# FTS — inbound contract (fts@2a09e76)

## Auth
HTTP Basic; app derived from username prefix before the first `_` (`ps_payouts` → PS, `api_monolith` → API, `art_*` → ART, `alert_*` → ALERT); credentials from `[users.<app>]` (`USERS_<APP>_USERNAME/PASSWORD`). 401 with `WWW-Authenticate: Basic`. Source `internal/routing/middleware/auth.go:16-96`.

## Routes
| Route | Apps | Request | Response |
|---|---|---|---|
| `POST /v1/transfer` | API, SETTLEMENT, VALIDX, PS, WALLET | `{product, merchant_id (RZP id), transfer{amount int>0, preferred_mode, source_id ≤14, source_type, transfer_by?, mode?, preferred_channel?, preferred_source_account_id? (int ⇒ DIRECT), narration alpha, initiate_at?, is_batch?, request_meta?}, account: oneOf{bank_account{account_type saving|current|nodal, ifsc_code, account_number, beneficiary_*}, vpa, card, non_saved_card, wallet, fund_account_id}, 2fa{otp}?, merchant_category{category, sub_category, mcc, onboarded_time}?}`; header `X-Origin` (API|payouts) → `transfer_meta.origin_service` when Splitz `CreateTransferMetaRollout` | `{fund_transfer_id, fund_account_id, status:"CREATED"}` (200 for existing (source_type, source_id), 201 new) |
| `GET /v1/transfer` | same | filters id, product, status, merchant_id, source_id, source_type, fund_account_id, preferred_channel, count, skip, from, to, bank_status_code, action, channel | transfers |
| `PUT /v1/transfer` | same | no-op | 200 |
| `PATCH/POST /v1/attempts/:action`, `PUT /v1/attempts/reconcile` | API, ART | keyed by `gateway_ref_no`: `{return_utr?, remarks (req), bank_status_code (req), utr?, status?, failure_reason?, mozart_meta{processed|failed|reversed}}`; actions update, safe_update, verify, process, reconcile; batch cap `Meta.AttemptsPatchActionLimit` | per-key `{status, message}` |
| `POST /v1/alert_manager` | API, ALERT | Alertmanager webhook → `channel_information_status` | 200 |
| `/v1/channel_health_events/*` | API, ALERT | alert_action, schedules, manual_override, fail_fast_status/manual_update, test_transaction/status | 200 |
| status/verify routes (`/v1/transfer/:id/...`) | API/PS | status check, verify | — |

Sources: `internal/routing/router/route_list.go:148-197`; `internal/controllers/{validation.go:33-333, transfer.go:139-179, attempt_bulk_action.go:57-140}`; `internal/transfer/{service.go:322-436, transfer_meta.go:9-34}`.

## Outbound (config)
`[webhook.<product>.transfer_status]` (monolith `/v1/update_fts_fund_transfer`, 3×200 s), `[payouts_service.update_fts_fund_transfer]` (PS `/v1/payouts_internal/transfer_status_webhook`), `[payouts_service.notify_channel_status]` / `[webhook.payout.notify_channel_status]` (downtime `PartnerBankHealthPayload{mode, channel, begin, account_type, status, source, instrument, include_merchants, exclude_merchants}`, 3×10 s), `[kafka_producers.fire_transfer_status]` (topic `rx-fts-status-update-events`, key source_id), `[mozart]` (`POST /fts/{gateway}/{version}/{action}`), `[xas]` (statement lookups for retry preprocessor), ledger `AccountAPI/CreateOnEvent` (`<lower(bank_account_type)>_pool_account_onboarding`).
