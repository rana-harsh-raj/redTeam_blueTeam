# Substitute contracts — DCS, Splitz, pricing, Shield, ASV, UPS

Sources: `reports/fidelity/raw/10_*.md`, `04_*.md` §3, `01_*.md` §3.

## DCS (`dcs-stub`) — CONTRACT-FAITHFUL, keep
- Wire: grpc-gateway JSON `POST /v1/kv/{get,evaluate,put,patch,audit,entities}`, `POST /v1/auth/login` (JWT bearer, ~6 h). `KeyValue.value` = base64 of protobuf-wire bytes (`goutils/dcs/dcs.go:281`). Key = `namespace/entity/entity_id/domain/object_name`.
- Payouts keys (`payouts/pkg/dcs/features/features.go`): `rzp/x/merchant/payouts/{ApiInterface,FundTransfer,Workflows,IntelligentPayouts,SubAccountRolesPayouts,direct_accounts/Configs,direct_accounts/PayoutModeConfig}`, `rzp/x/merchant/accounting/IntegrationSettings`. `in_flight_reservation_enabled` = field 1 of `direct_accounts.Configs` (bytes `08 01` when true; absent when false). Enabled feature names = proto field TextName of every populated bool; DCS overrides API-DB features on collision (`merchant/core.go:458-469`).
- Client cache 15 s / 8 MB BigCache; `Mock` flag is dead in the SDK; `WithMock(false)` hardcoded in payouts.
- Reachability: arena build patch `GetContextUrl` → `ARENA_DCS_URL` fallback covers login and per-request calls (`build/arena-patches/goutils-dcs-v1.7.3/dcs.go:111,180-186,192`). Keep the patch documented as arena-only.
- Dual naming: 10 flags have different API-DB vs DCS names (see `04_*.md` §3); seed BOTH spellings where a test depends on a flag (only `payout_workflows`/`enable_payout_workflow` is OR-reconciled by Splitz `PayoutWorkflowsDcsNameExperiment`).

## Splitz (`splitz-stub`) — must become CONTRACT-FAITHFUL
- Production payouts: `[splitz] client_side_eval = false` (`payouts/config/prod.toml:442`) → server-side `POST /twirp/rzp.splitz.evaluate.v1.EvaluateAPI/Evaluate|EvaluateBulk`. Arena template copied `true` from default.toml → payouts uses `FetchDecisionContext`, which the stub answers `{"experiments": []}` → every experiment resolves to code default. **Fix: set `client_side_eval = false` in the arena template** (and optionally implement `FetchDecisionContext` returning full experiment definitions).
- Request `{id (merchant), experiment_id, experiment_name, request_data, track_impression}`; response `variant{name, variables[{key:"result", value:"on"|"off"}]}`; payouts treats error/nil as off (`payouts/pkg/splitz/splitz.go:91-98,149-161`); some callers check `{enable, action}` variables (duplicate payout).
- Unseeded experiment default must be **off** (today `"on"`).
- Experiments to seed (payouts 38 in `internal/config/config.go:396-435`; fts ~30 in `fts/config/env.prod-live.toml:356-385`; monolith gates `payout_create_direct_ps`, `NON_TERMINAL_MIGRATION_HANDLING`, `PAYOUTS_SERVICE_CRON_TRIGGER_FOR_QUEUED_PAYOUTS`, `WORKFLOW_ACTION_WITH_DB_DUAL_WRITE_PAYOUTS_SERVICE`, `PAYOUT_BULK_APPROVE_ASYNC`, `PAYOUT_REPOSITORY_DATA_COMPARISON_ENABLED`). Rollout values are UNKNOWN (Splitz MySQL only) — label every seeded variant ASSUMED until an export is obtained.
- Bucketing: MurmurHash seeds 113/919, order whitelisting → sampler → exclusion → audience → bucket (only needed if `FetchDecisionContext` is implemented).

## Pricing — CONTRACT-FAITHFUL target
- Decision tree (`payouts/internal/app/payouts/processor/payout_pricing.go:138-355`): skip if fee set; Splitz `ChargeCollectionsCallExperiment` → Governor/CC SDK; else Redis cached rule (`MerchantId_BalanceId_Channel_Mode_Purpose_Method_Amount_FeeType_UserId`, set by monolith `set_pricing_rule_info`) when `PerformanceOptimizationExperiment` or Direct; else monolith `POST /v1/payouts_service/fetch_pricing_info` (fail-closed). `free_payout` always monolith.
- Contract: request `{payout_id, merchant_id, balance_id, amount ≥1, method, mode, channel, purpose?, fee_type?, user_id?}` → `{fees, tax, pricing_rule_id}` or `{error, public_error_code}` (`api Processor/Base.php:4996-5069`).
- Plan model = single `pricing` table (`api/database/migrations/2014_07_22_114552_create_pricing.php`): rows grouped by `plan_id`, filtered by `account_type, channel, payouts_filter, payment_method(+type/subtype)`, `amount_range_active/min/max`, fees `percent_rate (scale 100), fixed_rate, min_fee, max_fee`, `fee_bearer`, `type pricing|commission|buy_pricing`. Seed at least: IMPS/NEFT/RTGS/UPI/amazonpay slabs, a free-payout plan, an rzp_fees zero rule, one absent-rule error case.
- TDS is separate (Kafka `KafkaMessageTDS`), not part of fees/tax.

## Shield (`shield-stub`) — CONTRACT-FAITHFUL, keep
`POST /v1/rules/evaluate/payout` → action `allow|block|review`; PS branches on `block` only; 200 ms timeout, fail-open. Add `X-Test-Delay-Ms`/`X-Test-Fault` knobs for V22-style tests.

## ASV — only if `MerchantConfigViaAsvAndDcs` is turned on
Native gRPC (`goutils/account-service`, `accountv1.AccountService/GetByID` with 25-path field mask, `payouts/internal/app/merchant/core.go:34-58`); an HTTP stub cannot be dialed. Build a Go gRPC mock using the SDK's generated types, or leave the experiment off (legacy monolith path).

## UPS — MISSING
`POST /v1/payments/validate/account` `{entity:"vpa", value}` → `{vpa, success, customer_name}` (`payouts/pkg/upiService/vpa_mapper.go:17-77`); real `payments-upi` has a dev compose. Needed only for `FetchMappedVpa`.
