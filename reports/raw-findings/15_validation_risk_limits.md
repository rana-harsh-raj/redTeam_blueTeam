# 15 — Validation, Risk, Limits, Fees, Compliance controls on a Payout

Scope: every control that can REJECT or TRANSFORM a payout other than approval workflow and balance.
Repos (read-only clones): `ValidX` (40f181b6), `payouts` (4bf3dbf9), `cfa` (d488558e), `fts`, `xperience`.
All paths below are relative to the repo named in the row. No secrets recorded; only env-var / config-key names.

---

## 1. ValidX repo summary

**What it is.** A standalone Go 1.24 gRPC + grpc-gateway service that executes bank-account / VPA verification (penny drop ₹1, paisa drop, Citi penniless, Citi RPD/UPI-intent, Slice penniless) on behalf of the **API monolith**. It is *not* called by the Payouts service (zero `validx` references in `payouts`), and it does not expose the public `POST /v1/fund_accounts/validations` itself; every ValidX route is `/v1/internal/...`.

| Aspect | Evidence |
|---|---|
| Entrypoints | `cmd/server` (gRPC `:8080`, HTTP gw `:8081`, metrics/health `:8082` — `config/default.toml:11-13`), `cmd/worker` (SQS job consumer), `cmd/migration` (goose). `README.md:56-62` |
| Routes (grpc-gateway) | `POST /v1/internal/validations` (CreateValidation), `POST /v2/internal/validations` (CreateValidationV2), `GET /v1/internal/validations/{id}`, `POST /v1/internal/validations/webhook/fts`, `POST /v1/internal/validations/webhook/citi`, `.../webhook/citi/payment-acceptance`, `.../webhook/citi/refund`, `POST /v1/internal/validations/manual-action`, plus `RoutingAPI` CRUD at `/v1/internal/routing/rules[/{id}]` — `rpc/x/validx/.../*.pb.gw.go:751-765, 562-570`; handlers `internal/api_controller/validation/server/server.go:42,208,390,536,706,848` |
| Inbound auth | Basic auth with two rotating passwords per caller, then a per-username gRPC-method ACL. Only three ingress identities exist: **API monolith, Cron, Admin** — `internal/config/config.go:226-240`, `internal/server/interceptors.go:17-70`, `internal/server/interceptor/basicauth.go:29-97`, `authz.go:77-95`. |
| Callers | API monolith (owner of the public FAV API) → ValidX. FTS holds a dedicated `users.validx` credential (`fts/config/env.default.toml:5530-5532`), CFA has `Server.Auth.Validx` (`cfa/config/default.toml:35-38`), x-balances has a Validx ingress user (see findings 06). i.e. ValidX is an *outbound* caller of FTS, CFA, x-balances, ledger, charge-collections, Stork, DCS, Splitz, ASV, banking-accounts, Mozart. |
| Strategy → executor | Factory key `gateway:method` (`internal/validation/strategy/factory/factory.go:198-247`). Method resolved from request mode (`optimized`→penniless if eligible else pennydrop, `rpd`, `paisadrop`) at `factory.go:250-265`. Gateway resolved **DB-first** from `gateway_routing_rules` (merchant_id nullable, fund_account_type, validation_method, gateway, priority, enabled — `internal/database/model/gateway_routing_rule.go:19-40`) via `gatewayRoutingService.ResolveGateway`, falling back to hardcoded defaults `factory.go:269-316`; defaults: bank-account penniless→**citi**, bank-account pennydrop/paisadrop→**fts**, VPA pennydrop/paisadrop→**fts**, RPD→**citi** (`internal/constant/validation.go:223-230`). Routing rules cached in Redis (`internal/constant/cache.go:28,36`). |
| FTS penny drop | `internal/gateway/fts/service/service.go:56-80` `DoTransfer` → `BaseURL + "/transfer"` (`internal/gateway/fts/params.go:4`), i.e. FTS `POST /v1/transfer` (FTS route list `fts/internal/routing/router/route_list.go:148`, with VALIDX as an allowed caller per findings 03). Result arrives via FTS webhook → `POST /v1/internal/validations/webhook/fts` → job `validation_webhook_process`. |
| Citi penniless / RPD | via **Mozart** generic URL `/{namespace}/{gateway}/{version}/{action}` (`internal/gateway/mozart/service/service.go:30-31,68`); polling jobs `validation_citi_penniless_inquiry`, `validation_citi_rpd_inquiry` with back-off (`internal/validation/strategy/citi/response_handler.go:176-206`). |
| "api" pennydrop | Legacy path that asks the **API monolith** to run the penny drop: `PennydropInitiateURI = "/v1/fund_accounts/validations_internal/pennydrop"` (`internal/gateway/api/service/service.go:36,166`). |
| Cache strategy | Dedup of repeat validations for the same account: `validation_cache_info(cache_key, validation_id)`; lookup is `FindLatestByCacheKey` ordered by `created_at DESC` with **no age predicate** (`internal/validation_cache_info/repo/repo.go:38-46`); Redis validation_id→cache_key kept 1h (`internal/validation_cache_info/service/service.go:197`). Skippable per merchant via DCS `skip_cache` (`internal/gateway/dcs/features/features.go:23`). |
| Write-back of outcome | (a) **Stork** webhook events `fund_account.validation.completed` / `.failed` (`internal/constant/stork.go:32-34`) if DCS `enable_stork` (`features.go:26`); **else** POST to API monolith `SendValidationWebhookURI = "/v1/fund_accounts/validations_internal/webhook"` (`internal/gateway/api/service/service.go:34,117`; chosen at `internal/validation/statemanager/manager.go:132-133`, and `internal/validation/core/core.go:960`). (b) **Dual-write** SQS queue `prod-api-validation-dual-write-live` on create and on every state change (`config/prod.toml:149-150`, `internal/publisher/dual_write/publisher.go:81`, `statemanager/manager.go:120`, `internal/validation/service/service.go:214`) — the queue name says the API monolith consumes it, so the monolith's own FAV tables are kept in sync. (c) **Ledger** journals for the ₹1 debit/credit with events `fav_initiated / fav_processed / fav_failed / fav_reversed` (`internal/integrations/ledger/transformer/params.go:6-15`, `internal/integrations/ledger/service/service.go:151`, `internal/gateway/ledger/service/service.go:149-178`; insufficient-balance detection `:271`). RPD credits tracked in `rpd_ledger` (UTR, debit/credit — `internal/database/model/rpd_ledger.go:20-31`) with `rpd_refund_inquiry` job. (d) **CFA**: ValidX fetches the fund account, or **creates** one for composite requests (`internal/integrations/cfa/service/service.go:39-67,155`; gateway `internal/gateway/cfa/service/service.go:76,153,231,312`). It never writes a "verified" flag to CFA — CFA has none (findings 05, row "no verified state"). |
| Fees | Charge-collections `CalculateFees` on create; an error is returned to the caller, i.e. **fee failure fails validation creation** (`internal/validation/service/service.go:864-875`; gateway `internal/gateway/charge_collections/service/service.go:200`). Merchant fee metadata cached 24h (`service.go:989-1025`). |
| State machine | `created → initiated → completed | failed` (`internal/constant/validation.go:10-18`). Timeout job marks `failed` with timeout code and still emits the webhook (`internal/validation/statemanager/manager.go:70-133`); late webhooks are still processed. Manual action route can force completion but "don't allow fallback for manual completion" (`internal/validation/core/core.go:1270`). |
| Datastores | MySQL tables: `validations`, `validation_attempts`, `validation_beneficiary_data` (PII, AES-encrypted via `ENCRYPTION_AESENCRYPTIONKEY`, `config.go:255-259`), `validation_cache_info`, `validation_status_details`, `rpd_ledger`, `ifsc_bank_mapping`, `gateway_routing_rules` (`internal/database/model/*.go`). Redis: routing cache, locks (3-min mutex in cache strategy `cache.go:72`), fee-info cache. SQS: 9 job queues under `internal/job/*`. |
| Config flags | DCS: `skip_cache`, `enable_stork`. Splitz experiment list (`config.go:77-81, 269-290`). Redis-driven `config:validation:gateway` / `config:validation:fallback:gateway`. No per-mode/amount rules exist in ValidX. |
| Tests | `internal/api_controller/validation/server/server_test.go:81` (suite covering create/webhook/manual-action contracts), `internal/job/validation_webhook_process/validation_webhook_process_test.go`, `internal/validation/core/core_test.go`. |

**Authoritative source for "fund account is valid".** In code, no service *gates* on validity:
- ValidX persists a validation *event* (`validations.status`) with **no expiry/TTL/freshness field** anywhere in `internal/validation*`, `internal/database/model`, or `internal/constant` (grep for expir/ttl/valid_till returns only Redis-cache TTLs).
- CFA has only `active` (soft delete), no verified flag (findings 05).
- FTS `beneficiary_status` is bank-side beneficiary *registration* for channels that need it (`fts/internal/account/beneficiary_status.go:12-53`, tasks `register_beneficiary.go`, `verify_beneficiary.go`), not FAV.
- Payouts has **no** synchronous FAV gate (see §2). Its `internal/app/fundAccountValidation` is a *separate* FAV entity flow that calls the **API monolith** (`/fund_accounts/validations_internal/pennydrop`, `pkg/api/fav_fts.go:43`), not ValidX.
- Therefore the only place a merchant-visible FAV result is aggregated is the **API monolith's FAV entity**, fed by ValidX webhooks and the dual-write queue. The KNOWN statement "Payouts Service gates payouts on FAV freshness" is **not supported by the payouts code at 4bf3dbf9** (see unresolved Q1).

---

## 2. Control catalog (payout create → dispatch)

Order follows `BasePayoutProcessor.CreatePayout` (`payouts/internal/app/payouts/processor/base.go:83`) and `Core.FetchDependenciesAndCreatePayout` (`payouts/internal/app/payouts/core.go:1064`). "Closed" = error/timeout blocks the payout; "Open" = payout proceeds.

| # | Control | Owner | Enforced at (file:line) | Inputs | Outcome on failure | Fail-open/closed | Config / flag | Conf. |
|---|---|---|---|---|---|---|---|---|
| 1 | Dependency fetch: fund account, contact, purpose, banking account, merchant config | payouts | `core.go:4754-4818` (errgroup) | fund_account_id, purpose, account_number | any error → payout create rejected | **Closed**, but CFA outage is masked: under `IsFetchFromMicroservicesBulkExperimentEnabled`, CFA fetch failure falls back to API monolith (`core.go:4785-4795`); otherwise fund account comes from request payload / API monolith (`core.go:4797-4804`, `internal/app/fundAccountCache/core.go` Get) | Splitz "fetch from microservices bulk" experiment | High |
| 2 | Business Banking enabled | payouts (merchant config from API monolith) | `core.go:1087-1094` (also `:627-634`) | `merchantConfig.IsBusinessBankingEnabled()` | `BusinessBankingNotEnabled` | Closed | — | High |
| 3 | Amount tiers (min ₹1; max ₹10 cr; ₹50 cr with `IncreasePayoutLimit`; ₹300 cr for settlements/cross-border apps; sub-rupee only with `BelowRupeePayouts`) | payouts | `validation.go:171-262` via `ValidatePayoutCreateInput` `:155-168` | amount, merchant feature flags, app name | `MaximumAmountLimit`/`MinimumAmountLimit`/`BelowRupeeMinimumAmountLimit` (400) | Closed | merchant_configurations flags `IncreasePayoutLimit`, `BelowRupeePayouts` (`appConstants/features.go:18`) | High |
| 4 | Currency ↔ channel (non-INR/MYR only on RBL) | payouts | `validation.go:264-282` | currency, banking-account channel | `InvalidCurrencyForChannel` (400) | Closed | — | High |
| 5 | Per-mode amount caps: RTGS ≥ ₹2 L, IMPS ≤ ₹5 L, UPI ≤ ₹1 L, AmazonPay ≤ ₹10 k | payouts | `mode.go:639-659`, called from entity `Payout.Validate()` `validation.go:29-41` | mode, amount | `PayoutAmountModeMismatch` (400) | Closed | hardcoded `constants.go:56-66` | High |
| 6 | Channel × destination × mode × account-type support matrix; country-aware mode/channel validity | payouts | `mode.go:604-636` from `RunValidations` `base.go:830-848` | channel, fund-account type, mode, banking-account type, country | `PayoutModeNotSupported` family | Closed | static maps in `mode.go` | High |
| 7 | Fund account `active` and contact `active` (data owned by CFA / API monolith) | payouts | `base.go:850-872` | `payout.FundAccount.IsActive()`, contact active | `PayoutToInactiveFundAccountNotAllowed` / `PayoutToInactiveContactNotAllowed` | Closed | — | High |
| 8 | Card issuer/network ↔ mode | payouts | `base.go:1017-1040` (`ValidateModeOfIssuer`) | card issuer, network, mode | `PayoutModeNotSupportedForIssuer` | Closed | — | High |
| 9 | Internal-contact protection: non-privileged callers cannot pay internal contact types (except `rzp_fees`); privileged apps need explicit permission | payouts | `internal/app/contact/type.go:50-85` | passport auth type, app name, contact type, purpose | `PayoutToInternalFundAccountNotPermitted` / `FundAccountIDNotFound` | Closed | — | High |
| 10 | UPI VPA blacklist regexes (per merchant, cached) | payouts | `base.go:1297-1362` | VPA address, merchant regex list | `PayoutToBlacklistedVPAError` (400) | **Open** on regex-fetch error (`:1317-1324`) and on malformed regex (`:1336-1343`) | regex list from cache | High |
| 11 | Merchant eligibility per mode (e.g. `DisableXAmazonpay`) | payouts | `base.go:884+` | merchant feature flags, mode | mode-specific error | Closed | merchant_configurations flags | Med |
| 12 | Direct-account extras: UPI on RBL direct, block AmazonPay from direct, DCS UPI channel config | payouts | `fundAccountPayoutDirect.go:89-126` | channel, mode, account type | `PayoutModeNotSupported` | Closed | Splitz `check_upi_mode_config_via_dcs_experiment` | High |
| 13 | Merchant funds on hold (`hold_funds` from API monolith merchant config) — shared/sub-balance accounts, non-privileged callers | payouts | `fundAccountPayoutShared.go:122-152`; source `internal/app/merchant/core.go:203,355` | `merchantConfig.HoldFunds`, `SkipHoldFundsOnPayout` flag, auth type | `MerchantFundOnHold` | Closed | flag `SkipHoldFundsOnPayout` | High |
| 14 | VA payout allowed check | payouts | `fundAccountPayoutShared.go:128-131` (`CheckIfVAPayoutIsAllowed`) | banking account, merchant config | error | Closed | — | Med |
| 15 | Duplicate-payout detection (hash of mode, amount, merchant, reference_id, notes, narration…; custom interval) | payouts | `base.go:199-237`; `core.go:7027-7090` | payout fields, fund account | if Splitz `action=true`: rejected with `DuplicatePayoutEvaluateFoundExistingHash`; else **shadow** (logged + datalake event only) | **Open** (no concrete decision → metric only, `:234`) | Splitz `for_duplicate_payout_evaluate` → `{enable:"on", action:bool}` (`base.go:1540-1590`) | High |
| 16 | **Shield risk evaluation** | payouts → Shield | `base.go:239-253`; `core.go:6858-6885` | see §3 | action `block` → payout persisted with `BadRequestSuspiciousTransaction`, moved to terminal state, API error `suspicious_transaction` (`base.go:1445-1470`) | **Open** on error/timeout (`core.go:6858-6867` returns false) | HTTP timeout 200 ms (`config/prod.toml:410,424`); exclusion map `shield_payout_evaluate.exclude_payout.parameter` — prod excludes `purpose=["rzp_fees"]` (`prod.toml:426-429`, `config.go:437-443`) | High |
| 17 | Approval workflow | payouts | `base.go:256-316` | — | out of scope | — | — | — |
| 18 | **Beneficiary bank down → on_hold** | payouts (signal from FTS) | `base.go:318,530` → `onHoldHelper.go:16-140`; ingress `internal_routes.go:10-22` (`POST /v1/notify/health/update`, BasicAuth FTS; FTS side `fts/config/env.prod-live.toml:254-256`), `payout_internal_routes.go:90` (`POST /payouts_internal/bene_bank_status_update`) → `core.go:5223-5271` writes IFSC-prefix status to Redis | mode = IMPS, fund account = bank account, Redis bank-down key, random traffic split | payout `queued_reason=BeneBankDown`, state `on_hold`; **auto** re-dispatch via cron `POST /process_beneficiary_bank_on_hold_payouts` (`cron_routes.go:57` → `core.go:5282`) | Open (no Redis entry → proceed) | feature `PayoutsOnHold`; split % in `GenerateRandomNumberAndCheckIfPayoutToHold` (`onHoldHelper.go:118`) | High |
| 19 | Queue-if-required (balance) | payouts | `fundAccountPayoutDirect.go:149-160` | — | out of scope | — | — | — |
| 20 | Free-payout counter (monthly, per balance) | payouts | `base.go:1403-1414` → `internal/app/counters/core.go:185-232,287-354` | `free_payouts_consumed`, reset IST monthly | **never rejects**; sets `fee_type=free_payout` else normal fee | Open | `counters` table (`migrations/20220526144631_create_counters_table.go`) | High |
| 21 | **Fee/tax calculation** (legacy API-monolith pricing or CC-SDK/Governor) | payouts → API monolith / Governor+charge-collections | `payout_pricing.go:138` from `fundAccountPayoutDirect.go:161`, `fundAccountPayoutShared.go:76` | purpose, mode, amount, fee_type, account type | error → `Process()` fails → payout **rejected** (`payout_pricing.go:294-308`, `:368-378`, `:567-576`) | **Closed** (Redis pre-fetch miss only falls through to blocking API call `:252-259`) | Splitz `charge_collections_call_experiment` selects primary source; `charge_collections_call_canary_experiment` runs a fire-and-forget shadow call (`:584-642`) | High |
| 22 | `rzp_fees` (fee-recovery) payouts | payouts | purpose const `appConstants/constants.go:112`; fees forced to 0 `payout_pricing.go:347-350, 409-413`; skips Shield via exclusion (row 16); internal-contact exemption (row 9) | purpose | zero fee/tax | n/a | — | High |
| 23 | Credits deduction | payouts | `fundAccountPayoutDirect.go:~165` | — | out of scope (balance) | — | — | — |
| 24 | **TDS entry** | payouts → Kafka `add-tds-entry` | `core.go:8212-8272`; fired from FTS status webhook on PROCESSED/REVERSED `fts_transfer_status_webhook.go:402,406` | payout, TDS data | return value discarded → **never affects payout** | Open (fire-and-forget) | `constants.go:41` | High |
| 25 | KYC / merchant activation | — | **none in payouts**: `MerchantConfig.Activated` is populated (`internal/app/merchant/core.go:199`) but never checked | — | — | — | — | High (absence) |
| 26 | Mobile-number payouts / partner OAuth | — | **no code**; only deprecated card-mode comments (`processor/fund_transfer_service.go:379`, `pkg/fts/transfer_init_create.go:251`) | — | — | — | — | High (absence) |
| 27 | Payload encryption (AES, whole body) | payouts | `internal/routing/middleware/request_encrypt_decrypt.go:59,264`; `internal/app/payloadcrypt/service.go:168,236,341` | `X-Payload-Encrypted` header, auth type | undecryptable body → 4xx | Closed for opted-in merchants | applied per-route on `GET/POST /v1/payouts`, `/v1/payouts/:id`, `cancel_payout`, `POST /v2/payouts` (`payout_routes.go:35-75`, `payout_routes_v2.go:30-31`); **only** for Private (key/secret) auth — Proxy/dashboard & Privilege skip (`request_encrypt_decrypt.go:63-67`); DCS flag + Vault key presence (`service.go:434`); key = HashiCorp Vault path `merchant/aes_key/<mode>/<merchant_id>` (`service.go:31,345,367`), Vault token from env `PAYOUTS_HVAULT_TOKEN` (`config/prod.toml:720`) | High |
| 28 | Bulk-file structural validation | xperience | `internal/app/validator/bulkpayouts.go:30-89` | extension (.csv/.xlsx), ≤ 50 000 rows, single sheet, mandatory headers per template | whole file rejected before batch creation (`service/bulkpayouts/service.go:1355-1409`) | Closed | `BulkFileRowCountLimit=50000` | High |
| 29 | Bulk row-level validation | external "Batch" service (not xperience, not ValidX/CFA) | `service/bulkpayouts/service.go:2661-2782` (`CreateBatch`, `BatchTypeId="<template>_validate"`) | file | per-row partial success (counts only in `repository/bulkpayouts/model.go:125-133`) | unknown (outside cloned repos) | — | Med |
| 30 | Structural fund-account validation (IFSC lookup etc.) | CFA | findings 05 (`internal/fund_accounts/validate.go`) | IFSC, account no. | reject at fund-account create | Closed | DCS `skip_ifsc_lookup` | High |
| 31 | FAV freshness gate on payout | — | **not found in payouts** (no `IsFavRequired`, `ValidateFundAccount`, fav status field) | — | — | — | — | High (absence) |

Velocity: **no daily/aggregate merchant limit exists in payouts.** The only windowed limit is inside FTS for NEFT-24x7 second-slot routing, stored in Redis per merchant (`fts/internal/merchants/second_slot.go:26-120`, `config.go:19-21`); breach changes slot routing, it does not reject. `payouts.merchant_configurations` is a boolean feature-flag table (`internal/app/merchantConfiguration/model.go:8-15`, migration `20250311053125`), unrelated to FTS's `merchant_configurations` KV table (`fts/internal/migrations/00019_merchant_configuration.go:14-27`). `LiteBalanceThreshold`/`GatewayBalanceThreshold` belong to Fund-Management top-ups (`internal/app/fundManagement/core.go:156-670`), not to payout gating.

---

## 3. Findings with excerpts

### 3.1 Payouts has no FAV gate; its own FAV module is a client of the API monolith
`payouts/pkg/api/fav_fts.go:43`
```go
FavFts = "/fund_accounts/validations_internal/pennydrop"
```
`c.Host` is the `[api]` monolith host (`config/prod.toml:111-113`). Same internal path ValidX's "api" strategy uses (`ValidX/internal/gateway/api/service/service.go:36`). `grep -rni validx payouts` → 0 hits. The FAV FSM in payouts is `Created → Completed|Failed` only (`internal/app/fundAccountValidation/state_machine.go:19-94`), with `fav_ledger.go:17-197` posting `fav_*` journals (retry on `ErrorLedgerRetryableFailure`, `FavNotEnoughBalanceViaLedger` on insufficient balance). Unreachable monolith/FTS → FAV request errors out (fail-closed for the FAV call; `pkg/api/client.go:279-281`, default 30 s timeout `client.go:23`).

### 3.2 Shield is binary and fail-open
`payouts/internal/app/payouts/core.go:6858-6885`
```go
res, err := req.Create()
if err != nil { metric...; log...; return false }          // proceed
if res.GetStatusCode() == 200 && res.GetAction() == ValueBlock { return true }
return false
```
`ValueBlock = "block"` (`core.go:223`); no review/hold branches. Request (`pkg/shield/shield_payout_rule_evaluate.go:40-68`) carries payout id/amount/mode/narration/currency/purpose, source account type/channel/number, merchant id/created_at/MCC/type/constitution, beneficiary name/email/mobile/VPA/account/IFSC, fund_account created_at, user id, source IP. Timeout 200 ms (`config/prod.toml:410`, wired `internal/provider/shield_client.go:37`). No admin bypass endpoint; only `POST /shield/evaluate` evaluate-only route (`payout_internal_routes.go:119-120`). Dead constant `PayoutShieldEvaluateExperiment` (`processor/base.go:41`) is unreferenced.

### 3.3 Fee calculation is on the critical path and fail-closed
`payouts/internal/app/payouts/processor/payout_pricing.go:294-308`
```go
res, err := b.FetchPricingRuleInfo(payout, "api")
if err != nil { ...; return err }
```
CC-SDK path `:567-576` returns the wrapped Governor/charge-collections error. Order inside `Process()` (`fundAccountPayoutDirect.go:149-168`): `QueuePayoutIfRequired → HandleFreePayout → FetchPricing → DeductCredits → Created`. So fees are computed **after** the payout row exists and after Shield.

### 3.4 Beneficiary-bank-down hold
`payouts/internal/app/payouts/processor/onHoldHelper.go:59-74` — only IMPS + bank account + feature `PayoutsOnHold`; reads Redis key set by FTS notification (`core.go:5262-5271`); holds a random fraction and lets the rest through "to detect uptime"; cron re-dispatches (`cron_routes.go:57`). Manual reject remains available at `POST /payouts_internal/:payout_id/reject` (`payout_internal_routes.go:76`).

### 3.5 Duplicate detection is Splitz-controlled shadow-or-enforce
`processor/base.go:1577-1585`: Splitz variable `result` = `{enable:"on", action:true|false}`; only `action=true` rejects (`base.go:229-231`).

### 3.6 TDS is post-hoc and non-blocking
`fts_transfer_status_webhook.go:402,406` call `ProcessTdsForPayout` and discard the error; topic `add-tds-entry` (`constants.go:41`); gated on money-moved statuses (`core.go:8214`).

### 3.7 ValidX outcome fan-out
`ValidX/internal/validation/statemanager/manager.go:120-133`
```go
_, _ = m.dualWritePublisher.PublishValidationEvent(...)
if m.storkService == nil || !m.storkService.TrySendWebhook(ctx, m.dcsService, validationDTO, attempt) {
    _ = m.apiService.SendValidationWebhook(ctx, validationDTO, attempt)   // API monolith
}
```
Errors are ignored (`_ =`), so a lost webhook is not retried here (unresolved Q4).

---

## 4. Dependencies (this lane)

| From | To | Purpose | Failure effect on payout |
|---|---|---|---|
| payouts | API monolith (`[api]`) | merchant config (activated, hold_funds, features), fund-account fallback, legacy pricing, FAV penny drop | reject (closed) |
| payouts | CFA (`[cfa]`) | fund account fetch (experiment) | masked by monolith fallback |
| payouts | Shield | risk block | proceed (open) |
| payouts | Governor + charge-collections (CC-SDK) | fee/tax | reject (closed) when primary |
| payouts | Splitz | dup-eval, pricing source, DCS-UPI experiments | experiment off → default path |
| payouts | Redis | bene-bank-down flags, pricing cache, VPA regex cache | open (falls through) |
| payouts | Vault (`PAYOUTS_HVAULT_TOKEN`) | per-merchant AES keys | encrypted merchants fail to decrypt |
| payouts | Kafka `add-tds-entry` | TDS | none |
| FTS | payouts `/v1/notify/health/update`, `/v1/notify/downtime` | bank/channel health | hold IMPS payouts |
| API monolith | ValidX `/v1/internal/validations*` | FAV execution | FAV API error |
| ValidX | FTS `/v1/transfer`, Mozart (Citi), API monolith, CFA, ledger, charge-collections, Stork, x-balances, banking-accounts, ASV, DCS, Splitz | see §1 | FAV fails; fee error fails FAV create |
| ValidX | SQS `prod-api-validation-dual-write-live` | sync to API monolith | drift if dropped |
| xperience | "Batch" service | bulk row validation | file-level only in xperience |

---

## 5. Unresolved questions

1. **Where is "payouts gated on FAV freshness" actually enforced?** Not in `payouts` at 4bf3dbf9, not in CFA, not in ValidX. Candidates: API monolith (`/v1/payouts` front door, owner of the FAV entity and the `validations_internal/*` routes) or dashboard/xperience UX. Needs the monolith repo (`x` clone appears to be the frontend, not the PHP API).
2. **Is the ValidX cache strategy age-bounded?** `FindLatestByCacheKey` has no time predicate; either an age filter lives in `validation_cache_info/service` (not seen) or repeat validations are deduped indefinitely. Verify before relying on "freshness".
3. **Bulk row validation rules** live in the external Batch service (`<template>_validate`), not in any cloned repo.
4. **ValidX webhook durability**: errors from Stork/API webhook and dual-write are discarded in the state manager; confirm whether the `validation_webhook_process` job or the monolith reconciles misses.
5. **Shield semantics beyond `block`**: the shield-sdk module is not vendored; whether Shield can return `review`/`hold` is unknown, and payouts would treat anything but `block` as allow.
6. **Duplicate-detection live mode**: production Splitz value of `for_duplicate_payout_evaluate.action` (enforce vs shadow) is not in repo config.
7. **Per-merchant Shield exclusion**: the `exclude_payout.parameter` map is keyed by `PayoutInput` field; only `purpose=rzp_fees` is set in prod. Any merchant allowlist would have to be added there; none exists today.
