# Payouts Service — inbound contract (payouts@4bf3dbf)

## Auth
| Mechanism | Detail | Source |
|---|---|---|
| Passport | header `X-Passport-JWT-V1`, RS256, `kid` ∈ `[passport.api] apiv1`, `[passport.edge] edgev2` (prod) / `edgev1` (default) / `arena-passport-1`; missing → 401, unparsable → 500; legacy auth types private/proxy/privilege/admin | `internal/routing/middleware/passport.go:37-58,142-155`; `config/prod.toml:190-198` |
| Service Basic `[auth.*]` | api, workflow (`rzp_live`), fts, fastcron (`fast_cron`), xperience, vendorpayments (`vp_user`), settlements, irctc, bankingaccounts (`banking_accounts_user`), xbalances (`x_balances`), merchantconfiguration; dashboard/reminder unwired | `config/default.toml:93-132`; `internal/routing/router/*.go` |
| Identity precedence | merchant: passport consumer (private) or impersonation.Consumer (others) → header `x-merchant-id`/`X-Entity-Id` fallback; user: consumer(type user) → `App-User-ID` → `x-creator-id` when `x-creator-type=user`; actor: `X-Payout-Actor-Id/Type` | `internal/auth/authHelper.go:12-25,173-208`; `request_context.go:47-116`; `idempotency_key.go:80-93` |
| Idempotency | `X-Payout-Idempotency`; Redis mutex `key+merchant` 20 min; SHA-256 canonical body; enforcement gated `Features.IKeyAutoEnforcement` + Splitz `IKeyAutoEnforcementRampUp` | `internal/routing/middleware/idempotency_key.go:34-93` |

## Route groups
| Group | Auth | Routes (selection) |
|---|---|---|
| `/v1/payouts` (merchant) | Basic api/workflow + Passport | `POST /` create (DTO `FundAccountPayoutRequest`: purpose ≤30, amount, currency ∈ INR MYR USD EUR GBP SGD AED, merchant_id 14, account_number?, reference_id ≤40, narration alnum ≤30, idempotency_key?, fund_account_id | fund_account, source_details[], scheduled_at?, batch_id?, skip_workflow?, tds?, attachments?, remitter_details?, subtotal_amount?), `GET /:id`, cancel, `GET /schedule/timeslots`, `POST /bulk` (+Passport, ≤15 rows) |
| `/v1/payouts_internal` + internal `/v1/payouts/*` | Basic api/workflow/xperience/fts/vendorpayments/settlements/irctc (no passport) | `POST /manual_action`, `PATCH /update_payouts_with_fts` (`StatusUpdateRequest{source_id,status,failure_reason,bank_status_code,fts_fund_account_id,fts_account_type,fts_status}`), `POST /update_payouts_details_with_fts` (`DetailsUpdateRequest{utr,remarks,failure_reason,channel,return_utr,source_id,mode,fts_transfer_id(req),beneficiary_name,bank_status_code,fta_status,status_details{beneficiary_bank,reason,processed_by_time,mode},cms_ref_number,gateway_ref_number,bank_processed_time}`), `PATCH /update_credit_transfer_payout`, `POST /transfer_status_webhook` (`TransferStatusWebhookForPayoutRequest` incl. source_account_id, bank_account_type), `POST /scheduled/process`, `POST /balance_update_event {balance_id, balance}`, `POST /retry {payout_ids}`, `POST /retry/source_update`, `POST /payouts_internal/:id/approve {queue_if_low_balance}` / `reject`, `GET /analytics`, `POST /bene_bank_status_update`, `POST /on_hold/process`, `POST /free_payout_migration`, `POST /consistency_checker`, `POST /batch/process`, `POST /shield/evaluate`, `POST /duplicate_payout_evaluate`, `POST /banking_account_statement/payout_update`, `POST /rzp_fees_payout`, `POST /set_pricing_rule_info`, `GET /mapped_vpa/:upi_number`, `GET /fetch_multiple`, `GET /payouts_internal/:id`, `POST /payout_internal`, `GET /inflight_reservations` |
| `/v1/notify` | Basic fts | `POST /health/update` (partner bank health → Redis) |
| `/v1/workflow` | Basic workflow/api | `POST /state`, `PATCH /state/:id` (write-only) |
| `/v1/admin`, `/v1/admin/internal_actions` | Basic api + Passport(admin) | entity reads, free payout attrs, `PATCH /payouts_sync`, `POST /query_db` |
| `/v1/cron` | Basic fastcron | 24 routes incl. process_queued_payouts, process_batch_submitted_payouts, process_beneficiary_bank_on_hold_payouts, process_queued_low_balance_payouts, process_inflight_reservation_reconciliation, process_scheduled_payouts, payouts_dual_write_failure_processing, reverse_dual_write, payouts_sla_breach_monitor, fund_management_payouts/check |
| `/v1/internal` (xbalances) | Basic xbalances | x-balances callbacks |
| `/v1/merchant/*`, `/v1/admin/merchant-configuration` | Basic api/fastcron/merchantconfiguration | merchant onboarding, config |

Sources: `internal/routing/router/{payout_routes,payout_internal_routes,payout_internal_routes_with_passport,workflow_routes,payout_admin_routes,cron_routes,internal_routes}.go`; DTOs `internal/app/dtos/{payoutCreate,payoutUpdate,transfer_status_webhook_request,manual_action,payout_update_bas_entity_request}.go`.

## Outbound clients (prod.toml timeouts)
FTS 1000 ms r3; Ledger 200 ms r3 (closed); CFA 10 s (masked-open); x-balances 30 s; banking-account 2 s; Stork 30 s; Shield 200 ms (open); Splitz 200 ms (open); Governor 1 s / CC 2 s (closed); UPS 3 s; Workflows 100 ms r3; ASV 30 s (closed); XAS 30 s. Source `payouts/config/prod.toml` (lane 04 §8).
