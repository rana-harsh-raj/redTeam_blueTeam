# Payouts Service → API Monolith: Dependency & Routing State Inventory

Repo: `razorpay/payouts` @ `4bf3dbf9` (read-only clone). Cross-checked against `cfa` and `x-balances` clones. The `razorpay/api` monolith repo itself was not accessible — every claim about the monolith side is inferred from client code, config, and route auth wiring in these repos.

Confidence key: **High** = direct code evidence with call sites; **Medium** = code evidence but behaviour depends on an external system (Splitz rollout %, Credstash env var) not visible in this repo; **Low** = inferred/config-only, no runtime evidence available.

---

## 1. `pkg/api/` client inventory — every request type

`config/prod.toml:111-116`:
```
[api]
    internalIngressHost = "https://prod-api-int.razorpay.com/v1"
    host = "https://api.razorpay.com/v1"
    [api.auth]
        username = "rzp_live"
        password = "env|PAYOUTS_API_AUTH_PASSWORD"
```
Two client instances are wired (`GetAPIClient` = `host`, `GetAPIClientV2` = presumably `internalIngressHost`); `provider.GetVersionedApiClient()` (`internal/provider/getter.go:328-337`) picks V2 when `ApiInternalIngressFeature.Enable` is set — a routing-only toggle (which monolith frontdoor to hit), not a feature-scope toggle.

| File (pkg/api) | Method + path (on monolith) | Purpose | Called from (flow) | Critical path | Lifecycle |
|---|---|---|---|---|---|
| `fetch_pricing.go` | POST `/payouts_service/fetch_pricing_info` | Pricing/fee calc when Redis-cached rule misses or `fee_type=free_payout` | `processor/payout_pricing.go:290` (`FetchPricing`) — CREATE/PROCESS | **Yes** — default fallback tier for every merchant not on Charge-Collections pricing experiment | **Current** |
| `deduct_credits.go` | POST `/payouts_service/deduct_credits` | Deduct payout fee credits | `processor/payoutCredits.go:127` (`DeductCreditsAtAPI`) — CREATE | **Yes** | **Current** |
| `reverse_credits.go` | POST `/payouts_service/reverse_credits` | Reverse deducted credits on failure/reversal | `reversals/reverseCredits.go:55` — REVERSAL | **Yes** | **Current** |
| `fts_create.go` | POST `/payouts_service/create_fta/{id}` | Create FTA (fund transfer attempt) via monolith‑fronted FTS call | `processor/payoutFTS.go` `CreateFTS`/`makeFTSRequest` — PROCESS | **Fallback only** — `fundAccountPayoutDirect.go:226` calls it only when the async `FtsAsyncProcessing` queue push fails, and `core.go:5877` (`FtsAsyncProcessing` job) falls back to it only when `IsFTSRequestFromPayoutsService` (merchant-list config `fts_request_from_ps`, else Splitz `fts_request_from_payouts_service_experiment`) is **false** for the merchant | **Current (fallback path)** — primary path is now `pkg/fts` direct client (`fts-live.razorpay.com`, see `fund_transfer_service.go`) |
| `credit_transfer_create.go` | POST `/credit_transfer/create_async` | VA→VA payout via credit-transfer instead of FTS | `processor/payoutCreditTransfer.go:28` — PROCESS (VA-to-VA subset) | **Yes**, for the VA-to-VA subset | **Current** |
| `merchant_banking_account.go` (fn 1) | GET `/banking_accounts/{accountNumber}/{merchantID}` | Fetch banking account by account+merchant | `bankingAccount/core.go:286` — CREATE/PROCESS | **Fallback** — primary is XBalances (`isFetchFromXBalanceEnabled`, config `fetch_balance_from_x_balance` / Splitz) | **Current (fallback)** |
| `merchant_banking_account.go` (fn 2) | GET `/banking_accounts_balance_id/{balanceId}` | Fetch banking account by balance ID | `bankingAccount/core.go:603` (`fetchBankingAccountFromApiUsingBalanceId`) | **Fallback**, same gating as above | **Current (fallback)** |
| `merchant_config.go` | GET `/internal/merchants/{id}` | Full merchant config (features, pricing plan, business details) | `merchant/core.go:189` (`fetchMerchantConfigFromApi`), gated by `merchant/core.go:1077` `IsMerchantConfigViaAsvAndDcsEnabled` (Splitz exp `merchant_config_via_asv_and_dcs_experiment` = `Qnc6b4fg1Hi36k` in `config/prod.toml:479`) | **Yes** — merchant config is read on essentially every CREATE/APPROVE/PROCESS call; this is the **default** path unless the Splitz variant routes the merchant to ASV+DCS | **Current** (Medium confidence on rollout %: Splitz variant weight not visible in repo) |
| `fetch_fund_account.go` | GET `/fund_accounts_internal/fa_{id}` | Fetch a fund account by ID | `fundAccountCache/core.go:1114` (`FetchFundAccountFromAPIMonolithAndStoreInCache`) | **Yes, as fallback** — called from ~9 call sites in `payouts/core.go` (CREATE/PROCESS/webhook/retry paths) whenever `GetCfaFundAccountWithCache` (CFA-first) misses | **Current (fallback, exercised often)** |
| `create_fund_account.go` | POST `/fund_accounts_internal` | Create a fund account (legacy path) | `fundAccountCache/core.go:251` (`CreateFundAccountAtAPI`) — used only by `bulkPayoutsProcessor/core.go:226` | **Yes, for BULK only** — single-payout creation now goes through CFA (`CreateFundAccountOnContactAndFundAccountService`, `payouts/core.go:892`) | **Current for BULK; migration-lagging for single-create** |
| `create_contact.go` | POST `/contacts_internal` | Create a contact (legacy path) | `contact/core.go:44` (`CreateContactAtAPI`), invoked from `fundAccountCache/core.go:235` inside the API-based fund-account-creation flow | **Yes, for BULK only** (same gating as `create_fund_account.go`) | **Current for BULK** |
| `delete_card_data.go` | DELETE `/payouts_service/delete_card_metadata/{cardID}` | Delete stored card metadata/vault token | `fundAccountCache/core.go:717` | Admin/cleanup adjacent to CANCEL/fund-account delete | **Current** |
| `fetch_actor_info.go` | GET `/actor_info_internal/{userId}` | Fetch actor (dashboard user) info for audit/attribution | `actorInfo/core.go:161`, `fetch_orchestrator/enricher.go:1039` — used in payout GET/fetch-multiple response enrichment | Not on write path; read/enrichment | **Current** |
| `fetch_balances.go` | GET `/internal_balances_queued` | Bulk balance fetch for queued-payout dispatch | `payouts/helperQueuedPayouts.go:94` (`FetchBalancesFromApi`) — used in queued-payout dispatch (`core.go:280,366`) | **Yes** — PROCESS (queued dispatch) | **Current** |
| `fetch_merchant_slas.go` | POST `/merchant/on_hold_slas_internal` | Fetch on-hold SLA config per merchant | `merchant/core.go:715` | On-hold/SLA evaluation, PROCESS-adjacent | **Current** |
| `payout_purpose.go` | GET `/payouts/purposes/{merchantId}` | Fetch merchant's configured payout purposes | `payoutPurpose/core.go:94` (`fetchPayoutPurposeFromApi`) | CREATE (purpose validation) | **Current** |
| `user_fetch_multiple.go` | POST `/users_internal` | Bulk user detail fetch | `userDetails/core.go:312` (`fetchUsersFromBulkAPI`) | Fetch-multiple / bulk enrichment | **Current** |
| `fetch_user_details.go` | GET `/users_internal/{userId}` | Single user detail fetch | `userDetails/core.go:109` (`fetchUserDetailsFromApi`) | CREATE/APPROVE (actor attribution) | **Current** |
| `dual_write.go` | POST `/payouts_service/dual_write` | Legacy HTTP push of an entity (payout, BAS, etc.) to the monolith after every DB write | `payouts/model.go:1518` `Payout.DualWrite()` → `common/dualWrite/payout.go` `ProcessForPayout` → async job → `payouts/core.go:3759` `Core.DualWrite` → this client. Hooked unconditionally into **every** `Create`/`Update` via `internal/app/base/repo/repo.go:41,55` (generic repo wrapper used by `Payout`, `BankingAccountStatement`, `PayoutDetails`/`APIPayoutDetails`) | **Yes — fires on every CREATE/PROCESS/STATUS-UPDATE write**, no feature flag gate found in code (only a Splitz experiment `for_dual_write_direct_push` that *redirects* the same push to SQS instead of HTTP, doesn't disable it) | **Legacy — likely broken since ~12 Aug 2026** (monolith `payouts` table deleted; this HTTP endpoint plausibly 404s/errors silently now — errors here are logged and swallowed, so failure is invisible to callers). Distinct from the config-gated dataSync-based reverse dual-write below. **Needs live verification** (see Unresolved Questions). |
| `status_details_source_update.go` | POST `/payouts_service/status_details_source_update` | Notify monolith of a new payout status-detail row (multi status-detail case on `StateInitiated`) | `payouts/core.go:2745` | STATUS-UPDATE | **Current** |
| `source_update.go` | POST `/payouts_service/source_update` | "Use existing HTTP call to API monolith" (code comment) — notifies monolith payout `source` on every status change | `payouts/PayoutSourceHelper.go:108` (`UpdatePayoutSource`) | STATUS-UPDATE (fires on every payout status transition) | **Current** |
| `payout_status_update.go` | POST `/payout/status/update` | Update payout status at monolith (old settlement-era pattern) | **No callers found anywhere in `internal/`**; `GetPayoutStatusUpdateRequest` is not even part of the `api.IClient` interface in `pkg/api/client.go` | — | **Legacy-dead** |
| `bas_recon_payout_update.go` | POST `/banking_account_statement/payout_update` | Notify monolith when a Bank Account Statement (BAS) row is reconciled against a payout/reversal | `bankingAccountStatement/core.go:448` (`SendUpdatesToPayoutSource`), via `GetAPIClientV2` | REVERSAL/STATUS-UPDATE (BAS recon) | **Current** |
| `rollback_free_payouts.go` | POST `/payouts_service/free_payout_rollback` | Roll back a merchant's free-payout counter at the monolith | `freePayout/core.go:539` (`rollbackFreePayoutAPIRequest`), reached via admin route `/v1/payouts/free_payout_migration` | Admin/migration | **Migration-only** |
| `update_api_free_payouts.go` | POST `/payouts_service/decrement_free_payouts` | Decrement the monolith-side free-payout counter for a merchant not yet migrated | `freePayout/freePayoutApi.go:33`, reached from `freePayout/core.go` when `payout_service_enabled`/counter-migrated is false | CREATE (fee_type=free_payout, legacy merchants only) | **Current for un-migrated merchants; shrinking as migration completes** |
| `workflow_state_map_create.go` | POST `/wf-service/state/callback` | Forward a new workflow-state-map row to the monolith's workflow callback | `workflow/stateMap/core.go:190` (`ForwardStateCreateCallbackToAPI`) | APPROVE/REJECT (workflow) | **Current** |
| `workflow_state_map_update.go` | PATCH `/wf-service/state/{id}/callback` | Forward a workflow-state-map update | `workflow/stateMap/core.go:253` (`ForwardStateUpdateCallbackToAPI`) | APPROVE/REJECT | **Current** |
| `mail_and_sms.go` | POST `/payouts_service/mail_and_sms` | Trigger payout/transaction mail+SMS via monolith notification pipeline | `common/mailAndSms/mail_and_sms.go` (`SendPayoutMailAndSmsViaApiAsync`), called after FTS status update route | STATUS-UPDATE (async, non-blocking) | **Current** |
| `rename_attachments.go` | POST `/payouts_service/renameAttachments/{id}` | Rename payout-detail attachments at monolith after upload | `payoutDetails/core.go:248` (`ProcessPayoutDetailsToRenameAttachments`) | CREATE-adjacent (attachment upload) | **Current** |
| `dcc_payout_details_fetch.go` | POST `/consistency_checker/fetch` | Fetch payout details for the (now-removed) Data Consistency Checker | `payouts/fetch_payouts_details_from_api.go` (`GetPayoutsDetailsFromApi`/`fetchPayoutsDetails`) — **zero callers found anywhere else in the repo** | — | **Legacy-dead** — consistent with Slack: DCC for payouts table removed via PR #1901 |
| `fetch_fav_fund_account.go` | GET `/fund_accounts_internal/fa_{id}` | Fund-account fetch, FAV (Fund Account Validation) variant | `favFundAccount/core.go:138` (`FetchFundAccountFromApi`) | FAV flow (pre-payout validation) | **Current** |
| `fetch_fav_pricing.go` | POST `/fund_accounts/validations_internal/pricing_info` | Pricing for FAV | `fundAccountValidation/core.go:233` | FAV flow | **Current** |
| `validate_vpa.go` | POST `/fund_accounts/validations_internal/vpa` | VPA validation | `fundAccountValidation/processor/base.go:56` | FAV flow | **Current** |
| `fav_fts.go` | POST `/fund_accounts/validations_internal/pennydrop` | Penny-drop bank account validation via FTS-fronted monolith route | `fundAccountValidation/processor/bank_account.go:205` | FAV flow | **Current** |

Note: `config.go`, `base.go`, `client.go`, `constants.go` are shared plumbing (auth, HTTP wrapper, error parsing), not individual endpoints.

---

## 2. Merchant migration flags

| Flag / column | Source | Where read | What it branches |
|---|---|---|---|
| `payout_service_enabled` (`appConstants.PayoutServiceEnabled = "payout_service_enabled"`) | **Two layers, both must be true.** (1) DCS feature, read via `merchantConfigCore.IsFeatureEnabled(ctx, merchantID, PayoutServiceEnabled)`. (2) A boolean column on PS's own `banking_accounts` table (`internal/app/bankingAccount/model.go:19`, `GetIsPayoutsServiceEnabled()`), set at onboarding (`merchantOnboarding/core.go:110-158`) and pushed back to DCS via `dcsClient.PatchConfig` (`merchantOnboarding/core.go:268`) | `freePayout/core.go:838` `MerchantOnPayoutsService(ctx, payout)`: `return featureEnabled && balanceIdMigrated` (`:877`) — i.e. **both** the DCS feature flag AND the per-balance `banking_accounts.payout_service_enabled` column must be true for the merchant/balance to be considered "on Payouts Service." A separate `MerchantCounterOnPayoutsService` (`:880`) checks a third, independent column `GetIsCounterMigrated()` specifically for free-payout-counter ownership. | Free-payout counter source of truth (PS vs. monolith `update_api_free_payouts.go`/`rollback_free_payouts.go`); onboarding write-path |
| `is_payout_service` | Column on the payout row itself (0 = legacy monolith-owned payout, 1 = PS-native), read directly off the `Payout`/`payouts_temp` model — `payouts/model.go:952` `IsPayoutServicePayout()`, `payouts/repo.go:1191` `GetIsPayoutServiceAndTypeByIDs` (queries PS's own DB, not the monolith) | `fetch_orchestrator/enricher.go:806,828` and all 4 `response_builders/*.go` (admin/proxy/privilege/private) | Whether GET/fetch-multiple responses report a payout as "payout service" vs legacy in their JSON; **not** a routing flag — it's a provenance marker persisted per-row, useful for identifying pre-migration rows in a mixed dataset |
| `merchant_config_via_asv_and_dcs_experiment` (Splitz `Qnc6b4fg1Hi36k`) | Splitz | `merchant/core.go:1077` `IsMerchantConfigViaAsvAndDcsEnabled` | Whether `merchant/core.go:Get()` fetches merchant config from monolith API (`fetchMerchantConfigFromApi`) or from ASV+DCS (`fetchMerchantConfigFromAsvAndDcs`) — **default is API unless the Splitz variant routes the merchant off** |
| `fts_request_from_ps` (config) / `fts_request_from_payouts_service_experiment` (Splitz) | Config merchant-list (`config/prod.toml:508-511`, env-driven whitelist/blacklist) with Splitz fallback | `payouts/core.go:5931` / `reversals/core.go:341` `IsFTSRequestFromPayoutsService` | Whether FTA creation goes direct to FTS (`pkg/fts`, `fts-live.razorpay.com`) or via monolith `create_fta` (`pkg/api`) |
| `fetch_balance_from_x_balance` (config) / `FetchBalanceEntityFromXBalance` (Splitz) | Config merchant-list with Splitz fallback | `bankingAccount/core.go:152` `isFetchFromXBalanceEnabled` | Whether banking-account/balance lookups hit XBalances or the monolith |

`IsPayoutServicePayout`/`is_payout_service` and `payout_service_enabled` are **not the same flag** — the task description conflates them; code treats them as distinct (row-provenance marker vs merchant-onboarding feature).

---

## 3. `payouts_temp` / TiDB / dual-write state after PR #1946 / #1901

Two **separate, parallel** dual-write mechanisms coexist in this commit:

**A. Legacy HTTP dual-write (`pkg/api/dual_write.go`, POST `/payouts_service/dual_write`)**
- Hooked unconditionally into `internal/app/base/repo/repo.go:41,55` — fires on every `Create`/`Update` of any model implementing a `DualWrite(ctx)` method: `Payout` (`payouts/model.go:1518`), `BankingAccountStatement` (`bankingAccountStatement/model.go:202`), `PayoutDetails`/`APIPayoutDetails` (`payoutDetails/model.go:254,258`).
- Dispatch: `common/dualWrite/payout.go` `ProcessForPayout` → checks Splitz `for_dual_write_direct_push` (`ForDualWriteDirectPushToAPI`) — if on, pushes to `ApiQueueForAsyncDualWriteDirectPushJob` (SQS, presumably consumed by the monolith itself); otherwise (or additionally, if dedup variant) queues `AsyncDualWrite` job (`internal/job/async_dual_write.go`) which calls `Core.DualWrite` → the HTTP client above.
- **No feature flag disables this path outright** in the code read. Given the monolith's `payouts` table was deleted ~12 Aug 2026, this HTTP endpoint plausibly errors on the monolith side now; errors are logged (`trace.PayoutDualWriteDispatchToQueueFailure`) and swallowed, not surfaced. **Flagged as likely-broken-but-still-firing; needs live confirmation** (see §9).

**B. dataSync-framework reverse dual-write (writes directly to TiDB `payouts_temp`, via the `[db.api]` connection pool — see §4)**
- `internal/app/reverseDualWrite/config/config.go` registers 8 entity types (`ps_payout`, `payout`, `fund_transfer_attempt`, `payouts_details`, `payout_source`, `payouts_status_details`, `workflow_entity_map`, `workflow_state_map`) each mapped `SourceTableName` (PS's own table) → `DestinationTableName` (`payouts_temp` etc. on TiDB, per `models/payout.go:79-80`: `TablePayoutSource = "payouts"`, `TablePayoutDestination = "payouts_temp"`). These writes physically go through the same 5-connection `[db.api]` mysql/TiDB pool documented in §4 — there is no separate `[tidb]` client.
- Explicit call site: `payouts/repo.go` `Create`/`Update`/`UpdatePayoutWithoutWebhookTrigger` call `PushPSPayoutForDualWrite(ctx, payout.ID, payout.MerchantID)` (`repo.go:580,610,660`), gated by `internal/app/payouts/forward_dual_write.go:26` `isForwardDualWriteEnabled` → `provider.GetConfig(ctx).Features.ForwardDualWrite`.
- `config/prod.toml:666`: `forward_dual_write = "env|PAYOUTS_FEATURES_FORWARD_DUAL_WRITE"` — **value is env/Credstash-driven, not visible in this repo**; default in `config/default.toml:716` is `false`.
- A companion flag `reverse_dual_write = "env|PAYOUTS_FEATURES_REVERSE_DUAL_WRITE"` (`config/prod.toml:665`) exists but no call site referencing `Features.ReverseDualWrite` was found in `internal/` grep — possibly consumed inside the `pkg/dataSync` package itself (not inspected in depth) or in the cron/consumer entrypoint for the reverse-sync job, whose cadence/trigger was **not located** in this pass (matches the task's expectation that cron cadence is unresolved).
- This is the mechanism most consistent with Slack's "reverse dual-write PS→monolith/TiDB (`payouts_temp`) chronically broken, RZPX-144" — it is config-flag gated (can be toggled off cleanly) and its target table name matches exactly.

**Forward dual-write (API→PS), per Slack, was removed** — confirmed no `pkg/api` client method or route accepts an inbound "create/update payout from API" call that writes into PS's `payouts` table as a dual-write target (the closest inbound writes are the FTS/workflow/BAS callback routes in §6, which are legitimate PS-owned mutations, not a monolith-sourced payout dual-write).

---

## 4. `[db.api]` — direct monolith DB read from `payouts`

**Correction (this section was wrong in a prior pass of this file and has been re-verified directly against the repo three times, including a hard `grep`/`sed` read of the raw toml — see below): a live `[db.api]` connection pool DOES exist and IS used.**

`config/prod.toml:35-45`:
```toml
[db.api]
    dialect               = "mysql"
    protocol              = "tcp"
    url                   = "env|PAYOUTS_DB_API_URL"
    port                  = "3306"
    name                  = "env|PAYOUTS_DB_API_NAME"
    username              = "env|PAYOUTS_DB_API_USERNAME"
    password              = "env|PAYOUTS_DB_API_PASSWORD"
    sslMode               = "require"
    maxOpenConnections    = 5
    maxIdleConnections    = 2
    connectionMaxLifetime = 60
    debug                 = true
```
Two consumers, confirmed in code:
1. **`payouts` table reads — hard-disabled.** `internal/helpers/apidb.go:23-25` `IsApiDbEnabledForPayouts` is hardcoded `return false` (its own doc-comment describes Splitz gating via `use_api_db_for_payouts_experiment` that is no longer wired up). Guards `FindMultipleWithPaginationAPI` (`payouts/core.go:9236-9253`) and `GetIsPayoutServiceAndTypeByIDs` (`core.go:9307`, `non_terminal_payouts.go:56,74`). Consistent with the monolith `payouts` table being deleted ~12 Aug 2026 — these paths are effectively dead-but-present.
2. **`payout_status_details` table reads — genuinely live.** `internal/helpers/apidb.go:41-53` `IsApiDbEnabledForPayoutStatusDetails` is real Splitz-gated (experiment `use_api_db_for_payout_status_details_experiment`, id `TMp5VcIFYOHK2m`, `config/prod.toml:490`), **fail-open to `true`** if Splitz is unreachable or the experiment id is unset. Consumed by `payoutStatusDetails/core.go:199` and by `ApiDBFetcher` (`internal/app/payoutStatusDetails/fetch_orchestrator/fetchers/api_db_fetcher.go`), part of an `AppDB → ApiDB → TiDB` fetch-orchestrator fallback chain for historical (pre-cutover) payout-status-detail reads, bounded by a data-age-range config.
3. The dataSync reverse-dual-write engine (§3-B) also **writes** into `payouts_temp` and its `_temp` siblings through this same `[db.api]` pool — TiDB is reached exclusively as "the monolith's DB" via this pool; there is no separately-configured TiDB client anywhere in `config/*.toml`.

So: payouts service is **not** fully weaned off direct API-DB access — the `payouts` table path is dead-but-wired, while `payout_status_details` reads and the reverse-dual-write writes are live, both riding the same 5-connection `[db.api]` pool. Whether this is more or less complete than `cfa`/`x-balances`'s own `[APIStore.Sql]` connections (§8) is a matter of degree (payouts' own core `payouts` table read is disabled; cfa/x-balances still actively read filter fields from the monolith DB), not a clean "payouts has none, they do."

---

## 5. Shadow gateway — production state

`config/prod.toml:857-871`:
```toml
[shadow_gateway]
    enabled             = false
    monolith_base_url   = "https://prod-api-int.razorpay.com"
    proxy_timeout       = "30s"
    shadow_timeout      = "10s"
    splitz_timeout      = "100ms"
    max_shadow_routines = 16
    # Route onboarding is config-only; keys are lowercase "method path" ...
    # (all route examples are commented out — no live [shadow_gateway.routes.*] entries)
```
`config/default.toml:976-995` mirrors this (`enabled = false`, `monolith_base_url = ""`, no routes). **`devstack.toml:708-720` is the only environment with it live**: `enabled = true`, `monolith_base_url = "https://api-web.int.dev.razorpay.in"`, one pilot route `get /v1/payouts/schedule/timeslots` on Splitz experiment `TUnTsUB8Os2kX0` (project `RQDeYaM0u0QPxe`), default variant weight 100% `proxy` (inert passthrough).

**Conclusion: in production, the Edge→PS proxy/shadow-gateway cutover is fully wired but switched off, with zero routes onboarded.** The Kong-side `payouts-proxy-cutover` template referenced in Slack sits upstream of this and wasn't visible in this repo.

**`internal_consumer` bypass logic** (`internal/routing/shadowgateway/internal_consumer.go:12-54`): any request presenting Basic-auth whose username matches a configured internal consumer — `auth.API` (monolith), `Dashboard`, `Reminder`, `FTS`, `Workflow`, `FastCron`, `MerchantConfiguration`, `Xperience`, `VendorPayments`, `BankingAccounts`, `XBalances`, `Settlements`, `Irctc` — is **always routed native**, never proxied/shadowed, because (per code comment) the monolith can't authenticate PS-internal credentials and would otherwise bounce its own traffic back to itself.

---

## 6. Inbound: PS routes evidenced as called BY the monolith

No direct evidence file states "monolith calls this" explicitly (the monolith repo is inaccessible), but every route below is Basic-auth-gated with `cred.API` in its middleware chain (`internal/routing/router/*.go`), meaning it is designed to accept monolith-authenticated calls. Grouped by flow (auth sets from `internal/routing/router/*.go`):

| Flow | Route(s) | Auth set includes cred.API + | File |
|---|---|---|---|
| CREATE | `POST /v1/payouts/payout_internal` (idempotent create) | Workflow, Xperience, FTS, VendorPayments, Settlements, Irctc | `payout_internal_routes.go:184` |
| APPROVE/REJECT | `POST /v1/payouts/payouts_internal/:id/approve`, `/reject` | (same set) | `payout_internal_routes.go:69-80` |
| STATUS-UPDATE | `PATCH /v1/payouts/update_payouts_with_fts`, `POST /update_payouts_details_with_fts`, `POST /bene_bank_status_update`, `POST /transfer_status_webhook` | (same set) | `payout_internal_routes.go:26-37,87-92,153-158` |
| REVERSAL/CANCEL adjacent | `POST /v1/payouts/banking_account_statement/payout_update` (BAS recon inbound), `POST /manual_action` | (same set) | `payout_internal_routes.go:20-25,130-134` |
| BULK | `POST /v1/payouts/batch/process`, `POST /v1/payouts/scheduled/process` | (same set) | `payout_internal_routes.go:44-49,111-116` |
| Admin/migration | `POST /v1/payouts/free_payout_migration`, `/consistency_checker`, `/elasticsearch/backfill_index` | (same set) | `payout_internal_routes.go:99-110,171-176` |
| Retry | `POST /v1/payouts/retry`, `/retry/source_update` | (same set) | `payout_internal_routes.go:57-68` |
| Workflow callback | `workflow_routes.go` — `cred.Workflow, cred.API` | — | `workflow_routes.go:15` |
| Fund management admin | `fund_management_payout_admin_routes.go` — explicit comment: `// Auth: cred.API (Basic Auth with API credentials — API monolith → PS internal call).` | FastCron | `fund_management_payout_admin_routes.go:13,17` — **only route file with an explicit code comment confirming monolith-origin traffic** |
| Merchant/BAS/FAV internal | `merchant_routes.go`, `bas_internal_routes.go`, `bas_dev_admin_routes.go`, `fund_account_validation_internal_routes.go`, `fund_account_validation_routes.go`, `elasticsearch_internal_routes.go`, `ikey_excusions_routes.go`, `internal_routes.go`, `internal_action_routes.go`, `payout_admin_routes.go`, `payout_bulk_routes.go`, `payout_proxy_routes.go`, `payout_routes.go`, `payout_routes_v2.go`, `payout_status_details.go`, `payout_internal_routes_with_passport.go` | all include `cred.API` | (see grep list above) |

**Caveat (Medium/Low confidence):** `cred.API` gating proves the route *accepts* monolith credentials; it does not prove the monolith *currently* calls it, especially post `payouts` table deletion. Only `fund_management_payout_admin_routes.go` carries an explicit code comment asserting monolith origin. The rest should be treated as "candidate inbound surface, needs live traffic/APM confirmation."

---

## 7. Production hostnames — `config/prod.toml` outbound clients

| Client | Host |
|---|---|
| `api` | `internalIngressHost = https://prod-api-int.razorpay.com/v1`, `host = https://api.razorpay.com/v1` |
| `ledger` | `https://ledger-live.razorpay.com` |
| `fts` | `https://fts-live.razorpay.com/v1` |
| `cfa` | `https://cfa-live-int.razorpay.com` |
| `x_balances` (`x-balances` client) | `https://x-balances-ext.razorpay.com` |
| `account_service` (ASV) | `asv-grpc.razorpay.com:443` |
| `stork` | `https://stork.razorpay.com` |
| `shield` | `https://shield-payout-int.razorpay.com` |
| `splitz` | `https://splitz.razorpay.com` |
| `dcs` | no explicit `host` key (`Username=payouts`, `Env=prod`) — resolved via internal service discovery, not a static host in this file |
| `mozart` | `https://mozart.razorpay.com` |
| `workflow` | `https://workflows.razorpay.com` |
| `banking_account_service` | `https://banking-account.razorpay.in/v0.2` |
| `razorx` | `https://prod-razorx.razorpay.com/v1` |
| `wda` | `https://wda-service.razorpay.com` |
| `hvault` | `https://tax-compliance-vault.razorpay.com` |
| `vault` | `https://vault.razorpay.com/v1` |
| `ups` (payments-upi) | `https://payments-upi.razorpay.com` |
| `ccsdk.charge_collections` | `https://charge-collections-live.razorpay.com` (HTTP), `prod-charge-collections-service.razorpay.vpc` (secondary/internal) |
| `x_account_statements` | `https://x-account-statements-ext.razorpay.com` |
| `raven` | `https://raven.razorpay.com` |
| kafka | `prod-noncde-kafka.razorpay.com:9090` (multiple consumer/producer blocks) |
| es | `https://vpc-prod-razorpayx-es-a4zsnxxvhoxdgc22zmutxnh4je.ap-south-1.es.amazonaws.com` |
| tidb / api-db | **no separate `[tidb]` section** — TiDB (and the monolith's own DB) is reached exclusively via `[db.api]` (`config/prod.toml:35-45`, `url=env|PAYOUTS_DB_API_URL`, 5 max-open conns — see §4 for full detail and usage). A distinct, unrelated key `payouts_db_query_permission.tidb = "env|PAYOUTS_DB_QUERY_PERMISSION_TIDB"` is a query-permission flag, not a connection. |
| cache | `payouts.cache.razorpay.vpc` |
| sns | `[sns]` section present, endpoint not captured in this pass |

---

## 8. `cfa` and `x-balances` — their own monolith dependencies

**cfa** (contact/fund-account service, `cfa/config/prod.toml`): still carries a **live direct MySQL connection to the monolith DB** — `[APIStore.Sql]` (`cfa/config/prod.toml:19-31`), consumed via `internal/apidbservice/repo/repo.go` and wired into `contacts/service.go:41` and `fund_accounts/service.go:69` as `apiRepo`. Both `contacts/service.go:1108` `fetchMultipleFromAPIDB` and `fund_accounts/service.go:1473` `fetchFundAccountsFromAPIDB` fall back to this direct read for filter fields not indexed in Elasticsearch, and fund-account fetch-multiple explicitly three-way-merges `apiResults`, `cfaResults`, and `tidbResults` (`fund_accounts/service.go:1455` `MergeAndPaginateFundAccounts`). Separately, cfa has an HTTP client `[APIService]` → `https://prod-api-int.razorpay.com` (`internal/apiservice/client.go`) used only for `RegisterBeneForCardFundAccount` (`POST /v1/fund_accounts/card/register`), called from `fund_accounts/card/service.go:115,339` — this is on the card-fund-account creation path. cfa also still runs **its own forward dual-write and lazy-load queues** to the monolith for both contacts and fund accounts (`config/prod.toml:44-63`: `prod-api-rx-contact-dual-write-live`, `prod-api-rx-fund-account-dual-write-live`, `ContactLazyLoad`, `FundAccountLazyLoad`), meaning cfa's own migration off the monolith is less complete than PS's.

**x-balances** (balance service, `x-balances/config/prod.toml`): also has a live `[APIStore.Sql]` direct MySQL connection to the monolith DB (`config/prod.toml:22-33`), plus an `[APIService]` HTTP client → `https://prod-api-int.razorpay.com` (`internal/gateway/api_service/service.go`, explicitly commented `"Package api_service provides a client for making internal calls to the API monolith"`). Two call sites: `DualWriteSubBalanceLimit` (best-effort, fire-and-forget POST `/v1/credit_transfer/sub_balance_limit`, called from `sub_balance_limits/service.go`) and `CloseSubMerchantVirtualAccounts` (`POST /v1/internal/merchant/{id}/virtual_accounts/close`, called from `sub_balances/service.go:189` as one of several parallel goroutines during **SubBalance CREATE onboarding**). The direct-DB `apiRepo` fallback pattern (`internal/balances/service.go:26,51`, `internal/accounts/service.go:31,73`) mirrors cfa's: used as a fallback read when the primary balance repo returns `RecordNotFound`, and merged into fetch-multiple results. **Balance creation itself was not confirmed to depend on the monolith** in this pass beyond the `CloseSubMerchantVirtualAccounts` best-effort side-call during SubBalance onboarding — worth a follow-up look at `internal/balances/service.go` Create path specifically if that matters for the audit.

---

## Note on this file's provenance

This file was independently (and concurrently) produced/overwritten by three parallel research passes within the same session — the lead investigation plus two background sub-agents that were asked to report back in text only but instead wrote directly to this path, racing each other. The version below is the coordinator's final reconciliation: it keeps the more detailed/accurate content from each pass and explicitly corrects one factual conflict that surfaced during reconciliation — §4 (`[db.api]`) had been asserted **not to exist** by the last writer, which is incorrect; it was re-verified directly against `config/prod.toml` and the consuming Go code and corrected in place. Everything else was spot-checked (4 additional load-bearing claims re-verified against source) and found accurate. Treat §4 and its cross-references (§3-B, §7) as the authoritative versions; treat the rest of the document as cross-checked but not exhaustively re-verified line-by-line by the coordinator.

---

## 9. Unresolved questions / needs live verification

1. **Is the legacy HTTP dual-write (`dual_write.go`, §3-A) actually failing in prod right now?** It fires unconditionally on every payout write with no visible disable flag; if the monolith's `/payouts_service/dual_write` handler depended on the now-deleted `payouts` table, every one of these calls is silently erroring (swallowed, logged only). Needs an APM/error-rate check on this endpoint from the monolith or PS's outbound-call metrics (`metric.ExternalServiceCallResponse`, labeled by host+path).
2. **`Features.ForwardDualWrite` and `Features.ReverseDualWrite` actual values in prod** — both are `env|...`/Credstash-sourced, not visible in this repo. Needs a Credstash/env lookup to know whether §3-B (the dataSync TiDB write, matching RZPX-144) is currently on or off.
3. **Rollout weight of Splitz experiment `Qnc6b4fg1Hi36k`** (`merchant_config_via_asv_and_dcs_experiment`) — this determines what fraction of merchant-config reads still hit the monolith by default. Needs a Splitz dashboard check.
4. **Reverse-dual-write cron/consumer cadence** — the dataSync framework registers entity configs but the actual trigger (cron schedule vs continuous consumer) was not located in this pass; likely in a `cmd/` entrypoint or `pkg/dataSync` internals not yet read.
5. **`is_payout_service=0` legacy rows** — since the monolith's own `payouts` table is deleted, it's unclear where `is_payout_service=0` payouts (identified in `payouts/repo.go:1191`) are actually served from now; `internal/app/tidb/mock.go:57-58` comment references an RCA dated 2026-09-02 that may have the answer but wasn't fully read in this pass.
6. **§6 inbound surface confidence** — only one route file (`fund_management_payout_admin_routes.go`) has an explicit "called by API monolith" code comment; the rest is inferred from `cred.API` auth gating, which proves acceptance, not active traffic. A traffic/APM cross-check against the monolith's outbound call list (not accessible) would firm this up.
