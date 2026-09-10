# API Monolith (razorpay/api) — Payouts Architecture Findings

Repo: `razorpay/api`, PHP Laravel monolith, commit `2d665f918b60e917ec92be648fb5816d247f72b1` (shallow clone).
Central route file: `app/Http/Route.php` (23,164 lines). Route names are declared once in
`protected static $apiRoutes` (name => [method, uri, controller@action]) and then assigned to
one or more **auth-type groups**: `public static $public / $private / $proxy / $admin / $internal / $device / …`
(each a flat array of route-name strings). A route can belong to multiple groups — e.g. a payout-create
route in both `$internal` and `$admin` is reachable both by internal-app service auth AND by the
Razorpay ops/admin-dashboard auth type, which are two different, independently-checked auth paths.
`$admin` is NOT "administrator of this route" — it is the group for RZP admin-dashboard/ops auth
(`AdminAccess` middleware), separate from merchant `$private`/`$proxy` and service `$internal` auth.
Permission-per-route for `$admin` routes is `public static $routePermission` (route name => `Permission::CONST`).

---

## 1. Payout route table

| Route name | Method | URI | Auth group(s) | Controller@action |
|---|---|---|---|---|
| `payout_create` | POST | `payouts` | private, admin | `PayoutController@postFundAccountPayout` (Route.php:2311) |
| `payout_validate` | POST | `validate_payouts` | private, internal, proxy, admin | `PayoutController@validatePayout` (2312) |
| `payout_create_internal` | POST | `payouts_internal` | internal, admin | `PayoutController@postFundAccountPayout` (2313) — same controller method as public create; internal-app auth is what changes behavior (see §2) |
| `payout_create_internal_direct` | POST | `payouts_internal_direct` | internal, admin | `PayoutController@postFundAccountDirectBankingPayout` (2314) |
| `payout_create_on_internal_contact` | POST | `internalContactPayout` | internal, admin | `PayoutController@postFundAccountOnInternalContact` (2315) |
| `payout_create_with_otp` | POST | `payouts_with_otp` | private, internal, proxy, admin | `PayoutController@postFundAccountPayoutWithOtp` (2320) |
| `composite_payout_create_with_otp` | POST | `composite_payout_with_otp` | internal, proxy, admin | `PayoutController@postCompositePayoutWithOtp` (2321) |
| `composite_payout_internal` | POST | `composite_payout_internal` | internal, admin | `PayoutController@postCompositePayoutInternal` (2322) |
| `payout_create_2FA` | POST | `payouts/2fa/create` | private, internal, proxy, admin | `PayoutController@postFundAccountPayout2faForIciciCa` (2323) |
| `payout_create_2FA_internal` | POST | `payouts/2fa/create_internal` | internal, admin | same action (2324) |
| `payout_send_2FA_otp` | POST | `payouts/2fa/send_otp` | internal, proxy, admin | `PayoutController@payout2faOtpSendForIciciCa` (2325) |
| `payout_approve_bulk` | POST | `payouts/approve/bulk` | internal, proxy, admin | `PayoutController@bulkApproveFundAccountPayouts` (2328) |
| `payout_reject_bulk` | POST | `payouts/reject/bulk` | internal, proxy, admin | `PayoutController@bulkRejectFundAccountPayouts` (2329) |
| `payout_approve` | POST | `payouts/{id}/approve` | private, internal, proxy, admin | `PayoutController@postApproveFundAccountPayout` (2330) |
| `payout_2fa_approve` | POST | `payouts/approve/2fa` | internal, proxy, admin | `PayoutController@postApproveIciciCaFundAccountPayout` (2331) |
| `payout_reject` | POST | `payouts/{id}/reject` | private, internal, proxy, admin | `PayoutController@postRejectFundAccountPayout` (2332); admin permission `REJECT_PAYOUT` (12080) |
| `payout_reject_admin_bulk` | POST | `admin/payouts/cancel` | admin only | `PayoutController@bulkRejectFundAccountPayouts` (2342); permission `REJECT_PAYOUT_BULK` (10400). **No literal `admin/payouts/{id}/reject` route exists** — the task's expected path resolves to this bulk-cancel route plus the generic `payout_reject` above, which is also reachable in `$admin`. |
| `payouts_dashboard_manual_actions` | POST | `payouts/manual_action` | admin only | `PayoutController@payoutManualActions` (2345); permission `PAYOUT_MANUAL_ACTION` (11409) |
| `payout_approve_bulk` (see above) | — | `payouts/approve/bulk` | — | matches task's "payouts/approve/bulk" |
| `update_payout_status` | PATCH | `payouts/{id}/manual/status` | admin only | `PayoutController@updatePayoutStatusManually` (4570); permission `PAYOUT_STATUS_UPDATE_MANUALLY` (11591) — this is the "admin `payouts/{id}/manual/status`" item |
| `update_payout_status_batch` | PATCH | `payouts/manual/status_update/batch` | admin only | `PayoutController@updatePayoutStatusManuallyInBatch` (4571) |
| `payout_retry` | POST | `payouts/{id}/retry` | admin only | `PayoutController@postPayoutRetry` (2357); permission `RETRY_SETTLEMENT` (10669) |
| `fund_transfer_attempt_bulk_update` | PATCH | `fund_transfer_attempts` | admin only | `FundTransferAttemptController@bulkUpdate` (816); permission `SETTLEMENT_BULK_UPDATE` (10866) |
| `fund_transfer_attempt_recon_report` | GET | `fund_transfer_attempts/recon_report` | admin | `FundTransferAttemptController@sendFTAReconReport` (817) |
| `fund_transfer_attempt_process` | POST | `fund_transfer_attempts/initiate/{channel}` | admin | `FundTransferAttemptController@initiateFundTransfers` (820) |
| `fund_transfer_attempts_process_fts` | POST | `fund_transfer_attempts/fts/process/{channel}` | admin | `FundTransferAttemptController@processFundTransfersUsingFts` (822) |
| `payouts_service/*` group — all internal + admin, listed under `$internal` block ~4813-4837 |||||
| `create_payout_entry` | POST | `payouts_service/create` | internal, admin | `PayoutController@createPayoutEntry` |
| `create_FTA_payout_service` | POST | `payouts_service/create_fta/{payout_id}` | internal, admin | `PayoutController@createFTAForPayoutService` |
| `create_ledger_payout_service` | POST | `payouts_service/create_ledger` | internal, admin | `PayoutController@createPayoutServiceTransaction` |
| `deduct_credits_via_payout_service` | POST | `payouts_service/deduct_credits` | internal, admin | `PayoutController@deductCreditsViaPayoutService` |
| `reverse_credits_via_payout_service` | POST | `payouts_service/reverse_credits` | internal, admin | `ReversalController@reverseCreditsViaPayoutService` |
| `create_pricing_for_payout_service` | POST | `payouts_service/fetch_pricing_info` | internal, admin | `PayoutController@fetchPricingInfoForPayoutService` — matches task's "fetch_pricing_info" |
| `decrement_free_payouts_payouts_service` | POST | `payouts_service/decrement_free_payouts` | internal, admin | `PayoutController@decrementFreePayoutsForPayoutsService` |
| `rollback_free_payouts` | POST | `payouts_service/free_payout_rollback` | internal, admin | `PayoutController@freePayoutRollback` |
| `payouts_service_mail_and_sms` | POST | `payouts_service/mail_and_sms` | internal, admin | `PayoutController@payoutServiceMailAndSms` |
| `payouts_source_update` | POST | `payouts_service/source_update` | internal, admin | `PayoutController@payoutSourceUpdate` |
| `status_details_source_update` | POST | `payouts_service/status_details_source_update` | internal, admin | `PayoutController@statusDetailsSourceUpdate` |
| `payouts_service_dual_write` | POST | `payouts_service/dual_write` | internal, admin | `PayoutController@payoutServiceDualWrite` — see §8 |
| `delete_card_meta_data_and_vault_token` | DELETE | `payouts_service/delete_card_metadata` | internal, admin | `PayoutController@payoutServiceDeleteCardMetaData` |
| `rename_attachments_for_payouts` | POST | `payouts_service/renameAttachments/{id}` | internal, admin | `PayoutController@payoutServiceRenameAttachments` |
| `bas_recon_payout_update` | POST | `banking_account_statement/payout_update` | internal, admin | `PayoutController@payoutUpdateByBASRecon` — see §9 |
| `internal_merchant_fetch` | GET | `internal/merchants/{id}` | internal | `MerchantController@internalGetMerchant` (Route.php:619) — matches task's `/internal/merchants/{id}` |
| `fund_account_get_internal`/`list_internal`/`create_internal`/`update_internal` | — | `fund_accounts_internal[/{id}]` | internal, admin | `FundAccountController@get/list/create/update` (3983-3990) |
| `contact_get_internal`/`list_internal`/`create_internal`/`update_internal` | — | `contacts_internal[/{id}]` | internal, admin | `ContactController@get/list/create/update` (3945-3951) |
| `internal_balances_queued` | GET | `internal_balances_queued` | internal, admin | `BalanceController@fetchBalancesForBalanceIds` (661) — see §9 |
| `on_hold_merchant_slas_internal` | POST | `merchant/on_hold_slas_internal` | internal, admin | `PayoutController@getOnHoldMerchantSlas` (3410) — matches task's `merchant/on_hold_slas_internal` |
| `workflow_state_callback` | POST | `wf-service/state/callback` | internal, admin | `WorkflowServiceController@createWorkflowStateMap` (2448) — see §5 |
| `update_fts_fund_transfer` | POST | `update_fts_fund_transfer` | internal, admin | `FundTransferAttemptController@updateSource` (4021) — see §4 |
| `payout_workflow_retry_admin_bulk` | POST | `admin/payouts/workflow_retry` | admin only | `PayoutController@bulkRetryWorkflowOnPayout` (2419) |
| `payout_approve_internal` | POST | `payouts_internal/{id}/approve` | internal, admin | `PayoutController@postApproveFundAccountPayoutInternal` (2420) |
| `payout_reject_internal` | POST | `payouts_internal/{id}/reject` | internal, admin | `PayoutController@postRejectFundAccountPayoutInternal` (2421) |
| `fund_account_validate` | POST | `fund_accounts/validations` | private, admin | `FundAccountValidationController@create` (3965) — public FAV create |
| `fund_account_validate_fetch` | GET | `fund_accounts/validations` | private, admin | `FundAccountValidationController@getFundAccountValidations` (3966) |
| `fund_account_validate_fetch_by_id` | GET | `fund_accounts/validations/{id}` | (private) | `FundAccountValidationController@getFundAccountValidation` (3967) |
| `fund_account_validate_vpa_internal` | POST | `fund_accounts/validations_internal/vpa` | internal, admin | `FundAccountValidationController@validateVpaInternal` (3969) |
| `fund_account_validate_pennydrop_internal` | POST | `fund_accounts/validations_internal/pennydrop` | internal, admin | `FundAccountValidationController@validateBankAccountInternal` (3970) |
| `fetch_fav_pricing_info_internal` | POST | `fund_accounts/validations_internal/pricing_info` | internal, admin | `FundAccountValidationController@fetchPricingInfoForFavService` (3971) |
| `fund_account_validate_webhook_internal` | POST | `fund_accounts/validations_internal/webhook` | internal, admin | `FundAccountValidationController@sendWebhookToMerchant` (3972) |

Confidence: **high** for all of the above — every row is a direct grep hit against `Route.php` with line numbers, and group membership was verified programmatically (a Python scan of each `public static $group = […]` array's byte range for each route-name literal).

### Which services are actually allowed to call these internal routes (`Route::$internalApps`, ~Route.php:12922+)

Internal-auth routes require the caller's BasicAuth service credential to be explicitly listed under that service's name in `public static $internalApps` (keyed `app-name => [route names it may call]`). This directly identifies the real upstream caller for several routes above, verified by locating each route name's nearest enclosing `'app-name' => [` block:
- `payout_create_internal` (`payouts_internal`) is allowlisted for **eight** distinct internal services: `cross_border_import_service` (Route.php:12966-12967), `accounts_receivable` (13088-13096), `vendor_payments` (13150-13158), `payout_links` (17692-17699), `capital_early_settlements` (18506-18521), `scrooge` (18556-18581), `xpayroll` (18626-18629), `settlements_service` (18863-18875) — confirming this is the shared internal payout-creation entrypoint used across most money-movement products at Razorpay, not a narrow-purpose route.
- `update_fts_fund_transfer` is allowlisted only for the `fts` credential (Route.php:18714-18719) — confirms FTS itself is the sole caller of this relay endpoint.
- `workflow_state_callback` is allowlisted only for the `workflows` credential (Route.php:19097-19100) — confirms the external Workflow Service is the caller of `wf-service/state/callback`.
- `payouts_service_dual_write`, `internal_balances_queued`, `decrement_free_payouts_payouts_service`, `payouts_service_mail_and_sms`, `bas_recon_payout_update`, `on_hold_merchant_slas_internal`, `internal_merchant_fetch`, and ~25 other routes are allowlisted only under the `payouts_service` credential block (Route.php:19379-19420+) — confirms **PS is the sole caller** of all of these (they are PS→monolith callback/lookup routes, not monolith→PS calls). This is the direct evidence behind the §9/item-31 finding that PS, not XAS, calls `bas_recon_payout_update`.

---

## 2. Proxy decision: monolith → Payouts Service (PS)

There are **two independent, coexisting proxy mechanisms** for `/v1/payouts` (`payout_create`, `PayoutController@postFundAccountPayout`, `app/Http/Controllers/PayoutController.php:80`):

### 2a. "Direct" cutover gate (new, business-logic-skipping)
`postFundAccountPayout()` first calls `shouldServeFromPayoutsService($input)` (PayoutController.php:126), which wraps `decideServeFromPayoutsService()` (line 162) in a catch-all "never block, always fail safe to false" wrapper. The real decision lives in
`app/Models/Payout/DirectToPayoutsServiceGate.php::shouldDivert()`:
- **False if not live mode** (test-mode payouts never divert).
- **False if the caller is an internal app** (`isAppAuth === true`) — internal traffic stays on the classic flow because PS's root path runs public-create validation (`RequirePublicCreateCaller`, `ValidatePublicCreate`) that internal callers were never subject to (DirectToPayoutsServiceGate.php:81-102).
- **False if the request body's `merchant_id` differs from the authenticated merchant** — the classic flow honours a cross-merchant body id (balance lookup scoped by it via `Merchant\Validator::validateAndTranslateAccountNumberForBanking`); PS's direct path derives the merchant from auth only, so diverting could hit the wrong balance (104-124).
- Otherwise, calls a **Splitz experiment** via `Merchant\Core::isSplitzExperimentEnable`, keyed only by `merchant_id`, experiment id taken from config `app.payout_create_direct_ps_splitz_experiment_id` (PayoutController.php:164).
- If it returns true, `createPayoutOnPayoutsService()` (PayoutController.php:196) sends the **untouched public input** straight to PS's `/v1/payouts` root and relays the response verbatim — no monolith-side validation, workflow, balance, or pricing logic runs at all on this path. Header `X-Payouts-Service-Proxy: 0` marks it as "PS must run full business logic" (`Create.php:208-217`, "direct" flow).

### 2b. Classic flow → monolith-side proxy (older, business-logic-first)
If the direct gate says no, `postFundAccountPayout()` calls `Service::fundAccountPayout($input)` (`app/Models/Payout/Service.php:561`), which:
1. Enforces strict-private-auth-or-allowed-internal-app (line 577).
2. Resolves the balance (`processAccountNumber`, line 583).
3. Runs `Validator::BEFORE_CREATE_FUND_ACCOUNT_PAYOUT` (586) and card-mode validation.
4. Detects composite payout / mobile-number payout.
5. **If not a mobile-number payout AND `isMonolithProxyForPayoutServiceMerchant($balance)` is true** (Service.php:8169), calls `monolithProxyForPayoutsServiceMerchant()` (Service.php:8202) which itself calls `Payout\Core::createPayoutToFundAccountViaPayoutsService()` → `Processor\Base::createPayoutViaPayoutsService()` (Processor/Base.php:597), which runs `preValidations()` (funds-on-hold check, `SKIP_HOLD_FUNDS_ON_PAYOUT` feature, `blockBankingVaPayoutsIfApplicable` — `BLOCK_VA_PAYOUTS` feature and lite-account block) **before** calling PS.
   - `isMonolithProxyForPayoutServiceMerchant()` gate: live mode only, `PayoutsBankingAccount\Core::merchantMigratedToPayoutServiceByMerchantIdAndBalanceIdOnMonolithProxy(merchant, balance)` (per-merchant-per-balance migration flag), and explicitly **excludes partner-OAuth callers** (returns false if both `partner_merchant_id` and `application_id` are set).
6. The actual HTTP call is `Services\PayoutService\Create::createPayoutViaMicroservice()` (Create.php:47), URI selection: root `/v1/payouts` for merchant-authed calls, `/v1/payouts/payouts_internal` for app-authed calls, `/v1/payouts/internal_contact_payout` when `$isInternal`, `/v1/payouts/rzp_fees_payout` for RZP-fees purpose. Header `X-Payouts-Service-Proxy: 1` on the root URI marks this as "business logic already ran on API" (Create.php:212-217).

### Headers/auth added on every PS call (`app/Services/PayoutService/Base.php`)
- **BasicAuth (username/password)**: `key`/`secret` read from `config('applications.payouts_service')[mode]['payout_key'/'payout_secret']` (Base.php:80-88, `getAuthDetails()` line 356).
- **Passport JWT**: `Passport::PASSPORT_JWT_V1` header via `$ba->getPassportJwt($baseUrl)`, with a privilege-auth special case that re-signs the passport as an application-consumer JWT (`getHeadersWithJwt()`, Base.php:541-579).
- **Actor identity**: `X-Payout-Actor-Id` / `X-Payout-Actor-Type` = merchant id/`merchant`, or (if `isInternalApp()`) the internal-app name/`application` (`addActorsToHeaders()`, Base.php:582-596).
- **Idempotency**: `X-Payout-Idempotency` from the monolith's own `idempotency_key` table lookup (`Create.php:188-205`).
- **Proxy-cutover marker**: `X-Payouts-Service-Proxy` — `1` on the classic return-pass root call, `0` on the new direct-gate call (`Create.php:208-217`).
- No `X-Razorpay-Account` header was found on the payout-create path (identity is via actor headers + passport, not that header).
- **Timeouts**: `TIMEOUT = 60` seconds, `CONNECT_TIMEOUT = 10` seconds (Base.php:93,95). **No retry logic** — `sendRequest()` (Base.php:145) makes exactly one HTTP call via `Requests::request()` and re-throws on any exception/timeout after tracing/counting; the caller (`createPayoutOnPayoutsService`) explicitly documents "no error handling: PS may already have created the payout, retrying could pay the beneficiary twice."

### Remaining monolith-side `payouts`-table reads/writes
The client-side finding that the local `payouts` table is "deleted" is **not supported by this repo**: no migration drops it (`database/migrations` has `2016_06_16_081431_create_payouts_table.php` and no corresponding drop), and multiple live code paths still read/write it via `$this->repo->payout`:
- `Payout\Service::approveFundAccountPayout()` (Service.php:1179): `$this->repo->payout->findByPublicIdAndMerchant($id, $this->merchant)` — primary lookup for approve, falling back to a PS lookup (`handlePayoutServicePayoutForWorkflowAction`) only on exception (1181-1196).
- `app/Jobs/ApprovedPayoutProcessor.php:80`: `$this->repoManager->payout->findByPublicId($this->payoutId)` — async approve/reject processor for the queue-based bulk-approve flow (queue `approved_payout_processor`).
- `Payout\Core::payoutUpdateByBASRecon()` (Core.php:10286-10318): `$this->repo->payout->findOrFail($payoutId)`, and only falls through to the PS-side BAS client if the payout is null or `getIsPayoutService()` is true; otherwise it directly mutates and `saveOrFail()`s the local row (transaction id linking) or calls `reversePayout()` for failed local payouts.
- `Payout\Service::updatePayoutStatusManuallyInBatch()` (Service.php:5030): `$this->repo->payout->findMany($payoutIds)` for bulk manual status update, branching per-payout on `getIsPayoutService()`.
- `Payout\Core::generateRawQuery()` (Core.php:6813-6821) builds a raw SQL fragment against `` `payouts`.`channel` `` / `` `payouts`.`mode` `` (used elsewhere for settlement/channel queries).
- IRCTC-specific: `Payout\Core::isIrctcAxis2PSDBEnabled()` (Core.php:8158) — a distinct Splitz-gated IRCTC/Axis flow with its own PS-DB-vs-local decision (`Models/Ledger/ReverseShadow/IRCTCPayout/Core`), independent of the general `is_payout_service` flag.

**Net finding**: the local `payouts` table is still schema-present and still the primary read/write target for: approve/reject (both sync and async/queued), BAS reconciliation fallback, manual bulk status update, and IRCTC-Axis flows. It coexists with a **separate, distinct database** — `Connection::PAYOUT_SERVICE_DATABASE` (`app/Base/Repository.php:1456-1472`) — which is PS's *own* database, reached over a second Eloquent connection, and holds its *own* `payouts` table (`Table::PAYOUT = 'payouts'`, `app/Constants/Table.php:31`). These are two physically distinct tables with the same logical name in different databases; conflating them is the likely source of the "table deleted" client-side inference. Confidence: **high**.

---

## 3. Dashboard authentication ("X")

- **IP allowlist** (`app/Http/Middleware/MerchantIpFilter.php`): for proxy-auth (dashboard) requests, `authenticateIpForProxyAuth()` (line 132) pulls `$merchant->getMerchantDashboardWhitelistedIpsLive()`/`…Test()` (backed by feature `dashboard_whitelisted_ips_live`, `Merchant\Entity::DASHBOARD_WHITELISTED_IPS_LIVE`, Entity.php:176) and compares against `X-Dashboard-IP` (`fetchClientIpForDashboardRequest`, line 168). Empty whitelist ⇒ no check. `enable_approval_via_oauth` (Feature\Constants, `= 'enable_approval_via_oauth'`) skips this whitelist entirely for OAuth-based partner approvals (`isXPartnerApproval()`, MerchantIpFilter.php:207-219).
- **`merchant_users` membership**: not enforced in this monolith's middleware layer for proxy auth — the Passport JWT issued upstream (by Edge/dashboard-api) is trusted as already carrying a validated user↔merchant relationship; `BasicAuth::getPassportConsumerClaims()` is what `MerchantIpFilter` reads to distinguish application vs user consumer types (MerchantIpFilter.php:50-52). Confidence: **medium** — could not find a `merchant_users` DB check inside `api`, consistent with that check living in Edge/dashboard-api (out of repo scope).
- **OTP for `payouts_with_otp`**: `Payout\Service::fundAccountPayoutWithOtp()` (Service.php:1832) calls `$this->user->validateInput('verifyOtp', …)` then `(new User\Core)->verifyOtp($input + ['action' => 'create_payout'], $merchant, $user, testMode)` (1837-1840) **before** any payout creation logic runs.
- **Bank/ICICI 2FA**: `Payout\Service::fundAccountPayout2faForIciciCa()` (Service.php:1919) creates a *pending* payout and triggers an FTS-side OTP request (`Payout\Core::triggerIciciOtpForPayoutViaFts()`, Core.php:649, gated by feature `ICICI_2FA`) — approval/execution is a separate `payouts/approve/2fa` step.
- **OTP on approve**: `Payout\Service::approveFundAccountPayout()` (Service.php:1219-1226) — for non-partner approvals, `verifyOtp($input + ['action' => 'approve_payout', 'payout_id' => $id], …)` is mandatory. Partner-OAuth approvals instead run `validatePayoutForApprovalViaOAuth()` + a scoped rule-set, no OTP (1202-1218).
- **Role→permission mapping (finance_l1/l2/l3)**: `app/Http/Route.php:11951 $bankLmsRoutePermissions` and `:11966 $bankingRoutePermissions` map banking-specific route names to `Permission::CONST`s; enforcement for **admin-dashboard/ops** auth is local (see §7). For **merchant-dashboard proxy-auth** (finance_l1/l2/l3 are merchant-side banking roles, not RZP-admin roles), enforcement is in `app/Http/Middleware/UserAccess.php` (`getRoutePermission()` at lines 358/413/518) — reads the same `Route::$routePermission` table and checks it against the authenticated user's merchant-role permissions, again a **local** DB-backed check, not a remote AuthZ call for this path. `app/Http/AccessAuthorizationService.php` (seen at line 22, `'payout_create_with_otp' => ['/v1/payouts_with_otp', 'post']`) and `app/Services/Mock/AuthzEnforcerClient.php` exist but the latter is under `Services/Mock` — i.e. a **test/mock double**, not the production AuthZ client; a real remote-AuthZ integration was not found wired into the payout routes in this repo. Confidence: **medium** — the presence of a `Mock` AuthzEnforcerClient implies a real one exists but its call sites were not located in the time budget for this pass; flagged as a residual unknown.
- **Identity forwarded to PS**: same as §2 — Passport JWT (carries consumer claims, and for privilege-auth is re-signed to application-consumer identity) + `X-Payout-Actor-Id`/`X-Payout-Actor-Type` (merchant or internal-app). No separate user-id/role header is forwarded except `App-User-Id` in the privilege-auth-with-user-claims special case (`getHeadersWithJwt()`, Base.php:568).

---

## 4. FTS webhook relay (`/v1/update_fts_fund_transfer`)

Route: `update_fts_fund_transfer` → `FundTransferAttemptController@updateSource` (Route.php:4021, internal+admin auth).

`updateSource()` (`app/Http/Controllers/FundTransferAttemptController.php:64`):
1. If `source_type === FUND_ACCOUNT_VALIDATION`, delegates entirely to `FundAccountValidation\Service::updateFavWithFtsWebhook()` and returns (line 69-74) — FAV has its own FTS-webhook path, decoupled from payouts.
2. Otherwise mirrors the raw request to a shadow router (`PayoutShadowService::mirrorRequest`, best-effort, errors swallowed) then calls `FundTransfer\Attempt\Service::updateFundTransferAttempt($input)` → `Core::updateFundTransfer()` (Attempt/Core.php:487).

`updateFundTransfer()`:
- Validates input (`fts_status_update`), lowercases status.
- **FTA row is looked up and updated first**: `getAttemptByFTSTransferId()` or, if not found, `getFTSAttemptBySourceId()` (503-522); state-transition validity is checked (`AttemptStatus::isValidStateTransition`, 524-528, invalid transitions are silently skipped — "webhook update skipped due to invalid state transition"); then `updateFtaWithInput()` + `$this->repo->fund_transfer_attempt->saveOrFail($fta)` (530-537) — **this commit happens before any call to the payout source/PS**.
- Then `updateSourceEntityByFta($fta, $input)` (539) → for `source_type === PAYOUT`, dispatches to `Payout\Core::updateStatusAfterFtaRecon()` (Core.php:933) via a class-name-based dynamic dispatch (`sourceReconByFta`, Attempt/Core.php:835-875; only rethrows for `PAYOUT` source type on failure, all other source types swallow the exception).
- `updateStatusAfterFtaRecon()` maps FTS status → payout status (`Status::getPayoutStatusFromFtaStatus`) and switches on PROCESSED / REVERSED / FAILED / CREATED / INITIATED, calling `handlePayoutProcessed` / `handlePayoutReversed` / `handlePayoutFailed` respectively (Core.php:961-1058). These handlers work uniformly whether the payout is API-native or PS-owned (`$payout->getIsPayoutService()`).
- **Call into PS**: for PS-owned payouts, `handlePayoutProcessedForPayoutService()` / equivalents call `$this->payoutStatusServiceClient->updatePayoutStatusViaFTS()` (Core.php:8875) → `Services\PayoutService\Status::updatePayoutStatusViaFTS()` (Status.php:19), which PATCHes `/v1/payouts/update_payouts_with_fts` — this is the "update_payouts_with_fts" call the task asked about. A separate `Services\PayoutService\Details::UPDATE_PAYOUT_DETAILS_WITH_FTS = '/payouts/update_payouts_details_with_fts'` constant exists for detail-level updates (Details.php:12).
- **Retry policy on PS failure**: **none found** — `updatePayoutStatusViaFTS()` uses the same `makeRequestAndGetContent()` as payout-create, which throws on any HTTP/timeout error; `updateFundTransfer()`'s outer try/catch (560-583) records failure metrics and **re-throws** (582), meaning the FTS webhook call itself fails (FTS/caller is responsible for its own webhook retry, not the monolith).
- **No explicit queueing** of the PS-forwarding step was found on this path (synchronous, in-request).
- **Mutex**: `Payout\Core::PAYOUT_MUTEX_LOCK_TIMEOUT = 180` (seconds) (Core.php:160), used via `$this->mutex->acquireAndRelease(MIGRATION_REDIS_SUFFIX . payoutId, …, PAYOUT_MUTEX_LOCK_TIMEOUT, …)` inside `handlePayoutProcessed()` (Core.php:4644-4653) — **gated by the `NON_TERMINAL_MIGRATION_HANDLING` Splitz experiment** (4636-4640), i.e. this is a migration-era per-payout-id Redis mutex (180s TTL) meant to prevent the classic monolith status-update path and a concurrent PS-side status update from racing on the same payout during the migration window. This is the "180s API↔PS mutex."
- **Reconciliation cron**: `fund_transfer_attempt_reconcile` → `FundTransferAttemptController@reconcileFundTransfers` (Route.php:819, admin auth) exists as the FTA-vs-bank-file reconciliation entrypoint; a **dedicated FTS-vs-payouts** (cross-system) reconciliation cron was not located — only the FTA-vs-channel-statement recon (`reconcileFundTransfers`) and the general `payouts/dispatch_stuck` (`dispatchStuckPayouts`, Route.php:2374) and `PayoutDataConsistencyChecker` job (`app/Jobs/PayoutDataConsistencyChecker.php`, queue `data_consistency_checker`) were found; the latter's name strongly suggests it is (or subsumes) this reconciliation but its body was not read in this pass. Confidence: **medium** on the "no dedicated FTS-vs-payouts recon cron" claim.

---

## 5. Approval workflow

- **wf-service integration**: `workflow_state_callback` route (`wf-service/state/callback`, Route.php:2448) → `WorkflowServiceController@createWorkflowStateMap` — the monolith's ingress for the external Workflow Service pushing back workflow/state transitions (not read in full depth this pass; route + controller located, confidence **medium** on internal body).
- **OTP on approve**: confirmed mandatory for non-partner approvals (see §3).
- **`enable_approval_via_oauth`**: gates skipping the IP-whitelist check specifically for OAuth-based partner (Razorpay-partner-app) approval flows on X (`MerchantIpFilter.php:207-219`); this is a feature flag on the merchant, checked alongside `isXPartnerApproval()`.
- **Bulk approve**: `payout_bulk_approve`/`payout_approve_bulk` route to `PayoutController@bulkApproveFundAccountPayouts`/`approvePayoutBulk`. `Payout\Service::shouldProcessBulkApproveAsync()` (Service.php:6802-6811) checks Splitz experiment `RazorxTreatment::PAYOUT_BULK_APPROVE_ASYNC`; when enabled, individual approvals are queued as `ApprovedPayoutProcessor` jobs (queue `approved_payout_processor`) rather than processed inline — this is the async bulk-approve path, distinct from the Batch (file-upload) bulk-payout-**creation** subsystem (`app/Models/Batch`, used by `payouts_batch_create`/`payouts/bulk` routes for creating many payouts from a file, not for bulk-approving existing ones).
- **Expiry cron**: `payouts_auto_cancel_on_expiry` → `PayoutController@processDispatchForPayoutsAutoRejectionOnExpiry` → `Service::processDispatchForPayoutsAutoExpiry()` (Service.php:2530). Threshold: `Carbon::now(IST)->startOfDay()->subMonths(3)` (line 2532), applied identically to fetch both `Status::PENDING` and `Status::QUEUED` payouts older than that cutoff (2538-2540), both merged into one dispatch list (`dispatchPayoutsForAutoExpiry`). **Correction to task framing**: the code does not distinguish "pending → auto-reject" vs "queued → auto-fail" by different thresholds — both use the same 3-month cutoff and the same dispatch call; the actual reject-vs-fail outcome is presumably determined downstream inside `dispatchPayoutsForAutoExpiry`/`Core`, not by this dispatcher. Confidence: **high** on the 3-month constant and dual-status query; **medium** on the reject/fail branching detail (not traced further).

---

## 6. FAV (Fund Account Validation) gate — definitive answer

**No such gate exists in the payout-creation code path.** Exhaustive grep for `fund_account_validation`, `FundAccountValidation`, `is_validated`, `validated_at` across `app/Models/Payout/Core.php`, `Service.php`, `Validator.php`, and `Processor/*.php` returns exactly one hit, and it is **not** a create-time gate: `Payout\Service::updatePayoutStatusManuallyInBatch()` (Service.php:5023-5028) special-cases an **admin bulk status update** where `status === 'fav_failed'` by delegating to `FundAccountValidation\Service::manualUpdateFavToFailedState()` — this is an admin tool for marking FAV records failed, not a check performed during payout creation. `FundAccountValidation\Service` (`app/Models/FundAccount/Validation/Service.php`) is a fully standalone product/module (`fetchFav`, `create`, `fetchPricingInfoForFavService`, `validateVpaInternal`, `validateBankAccountInternal`, webhook handlers) with no inbound call from `Payout\Core`/`Payout\Service`/`Payout\Processor`. **Conclusion: a payout create is never blocked, in this monolith's code, on FAV freshness or status** — FAV is offered as an independent pre-flight product merchants may call themselves; the API does not enforce it. Confidence: **high** (based on exhaustive grep across the entire Payout module plus manual read of the one hit found).

---

## 7. Admin permission enforcement (server-side)

Enforcement point: **`app/Http/Middleware/AdminAccess.php`**, method `policyChecker()` (line 285). For a given route name it:
1. `getRoutePermission($routeName)` (line 115) — looks up `Route::$routePermission[$routeName]`, throwing `BAD_REQUEST_PERMISSION_ERROR` if the route has no permission mapped at all.
2. `$admin->getRolesAndPermissionsList()` (Admin\Admin\Entity, line 300) — resolves the admin's roles and the flattened set of permissions those roles grant. This is a **local, DB-backed** lookup (admin → role → permission relations in the monolith's own schema), **not** a remote AuthZ service call, for this auth type.
3. `checkTenantRoleAllowed()` — an additional, prior, tenant/BU-scoped role restriction for Razorpay-org admins (line 307-312).
4. `checkPermissionAllowed($permission, $adminPermissions, $routeName)` (317) — the actual permission-set membership check.
5. If passed and a merchant is in scope, `Admin\Group\Core::groupCheck($admin, $merchant)` (322-328) additionally verifies the admin's access group covers that merchant (skippable per-route via `Route::$skipMerchantAccessCheckOnSpecificAdminAuthRoutes`).

Route → Permission mapping for the requested items:

| Route name | Permission constant | Location |
|---|---|---|
| `update_payout_status` (`payouts/{id}/manual/status`) | `PAYOUT_STATUS_UPDATE_MANUALLY` | Route.php:11591 |
| `payouts_dashboard_manual_actions` (`payouts/manual_action`) | `PAYOUT_MANUAL_ACTION` | Route.php:11409 |
| `payout_reject` (`payouts/{id}/reject`) | `REJECT_PAYOUT` | Route.php:12080 |
| `payout_reject_admin_bulk` (`admin/payouts/cancel`) | `REJECT_PAYOUT_BULK` | Route.php:10400 |
| `payout_retry` (`payouts/{id}/retry`) | `RETRY_SETTLEMENT` | Route.php:10669 |
| `setl_update_channel_bulk` (settlement bulk-update route) | `SETTLEMENT_BULK_UPDATE` | Route.php:10670 |
| `fund_transfer_attempt_bulk_update` / `fund_transfer_attempt_initiate_action` | `SETTLEMENT_BULK_UPDATE` | Route.php:10866-10867 |
| `fee_recovery_*` admin routes (`admin/fee_recovery_payout`, `/custom_amount`, `/schedule_update`, `/amount`, `/retry`) | not individually traced to a permission constant in this pass — routes located (Route.php:2772-2783) but their `$routePermission` entries were not confirmed; **unresolved, needs follow-up grep of `fee_recovery` against `$routePermission`.** |
| `banking_account_statement_source_update` / `_validate` ("manually link RBL account statement") | `MANUALLY_LINK_RBL_ACCOUNT_STATEMENT` | Route.php:11626, 11628; constant defined `app/Models/Admin/Permission/Name.php:930` |

Confidence: **high** for the mapping and enforcement mechanism; **low/unresolved** specifically for `process_fee_recovery`'s exact permission constant.

---

## 8. `/payouts_service/dual_write` — current state

**Correction — this section was re-verified directly by reading the actual write path end-to-end, because two independent sub-investigations of this repo disagreed on dual-write direction. The corrected account below supersedes any conflicting summary elsewhere in earlier drafts of this document.**

`PayoutController@payoutServiceDualWrite` → `Payout\Service::payoutServiceDualWrite($input)` (Service.php:6733) → `Payout\Core::payoutServiceDualWrite($input)` (Core.php:10219):
- For `entity_type === 'account_statement_bas'`: conditionally skips (per-channel + per-merchant Splitz cutoff `ACCOUNT_STATEMENTS_DUAL_WRITE_CUTOFF`) or dispatches `AccountStatementDualWrite::dispatch()` (10241).
- For `entity_type === 'accounts_bas'`: synchronous `BankingAccountStatement\Details\Core::updateStatementLastFetchedData()` (10251).
- **Default (payout) case**: dispatches `PayoutServiceDualWrite::dispatch($mode, $input)` (10265) to queue `payout_service_dual_write` (`app/Jobs/PayoutServiceDualWrite.php:30`, `MAX_RETRY_ATTEMPT = 5`).

The job's `handle()` calls `Payout\Core::processDualWrite($input)` (Core.php:10762), which:
1. Validates input, reads a **dual-write bookkeeping marker/watermark** from a metadata table (`getMetaDataFromPayoutServiceForDualWrite`, Core.php:10795, using table `payout_meta_temporary`) to skip stale/out-of-order writes (10770-10788).
2. Calls `(new DualWrite\Processor)->dualWriteDataForPayoutId($payoutId)` (10790) — the actual write, traced below.
3. Upserts the bookkeeping marker back (10792, `upsertMetaDataInPayoutServiceForDualWrite`, Core.php:10809).

**Two physically distinct connections are used, for two different purposes — this is the source of the earlier disagreement, and reading `app/Models/Payout/DualWrite/Payout.php` directly resolves it:**
- The **bookkeeping watermark** (`payout_meta_temporary`) genuinely does live in PS's own database: `Repository::insertIntoPayoutServiceDB()`/`updateInPayoutServiceDB()` (Repository.php:5290-5304) run against `\DB::connection($this->getPayoutsServiceConnection())` → `Connection::PAYOUT_SERVICE_DATABASE` (`app/Base/Repository.php:1456-1472`). This part only tracks "have I already dual-written this payout as of timestamp T" — it holds no payout business data.
- **The actual payout row dual-write is the opposite direction: PS → monolith's own local `payouts` table.** `DualWrite\Payout::dualWritePSPayout(string $id)` (`app/Models/Payout/DualWrite/Payout.php:24-71`, read in full):
  1. `$payout = $this->getAPIPayoutFromPayoutService($id)` (line 31) → `getAPIPayoutFromPayoutService()` (line 250) calls `$this->repo->payout->getPayoutServicePayout($id)` (Repository.php:5313-5323, `select * from payouts where id = ? limit 1` run via `\DB::connection($this->getPayoutsServiceConnection())` — **this is the one read against PS's database**), builds a new `Entity` from that row, then **explicitly `$payout->setConnection($this->mode)`** (Payout.php:294) — `$this->mode` is the plain `'live'`/`'test'` mode string (declared as `protected $mode` "Test/Live mode" in the parent `DualWrite\Base`, `app/Models/Payout/DualWrite/Base.php:36`), i.e. the monolith's **own default database connection name**, not `payout_service_database`.
  2. Back in `dualWritePSPayout()`: `$apiPayout = $this->repo->payout->find($id, ['*'], null, true)` (Payout.php:39) — the 4th argument `$forceFetchFromAPIDB = true` makes `Repository::find()` skip the PS-first logic entirely and hit the **local API DB directly** (per `Repository.php:216-226`, confirmed earlier in this investigation).
  3. If that local row exists, `$payout = $apiPayout->setRawAttributes($payout->getAttributes())` (line 47) — the PS-sourced attributes are merged **onto the existing local Eloquent model instance** (which carries the local DB connection).
  4. `$this->repo->payout->saveOrFail($payout)` (line 56) — persists using whichever connection is set on `$payout`: the monolith's own local `'live'`/`'test'` connection, i.e. **the local `payouts` table**, either as an INSERT (new local row, connection forced via step 1's `setConnection`) or an UPDATE (existing local row, connection inherited from step 2's local fetch).
  - **Conclusion: the dual-write mechanism reads the authoritative row from PS's database and writes/upserts it into the monolith's own local `payouts` table** — i.e. PS→monolith, keeping the local table as a shadow copy of PS's state. It does **not** write payout business data into PS's database; the only thing written into PS's database is the small watermark record described above.

No evidence of an error state ("writes to a deleted table"): the handler is queue-backed, retried up to 5 times (`MAX_RETRY_ATTEMPT = 5`), and both the source (PS DB) and destination (local `payouts` table) are live, reachable connections. `payouts_temp`/TiDB specifically: no table literally named `payouts_temp` was found anywhere in this repo (searched case-insensitively); the closest analogue is `payout_meta_temporary` (the dual-write watermark, confirmed above to live in PS's own database — consistent with a TiDB-backed PS store) — medium-high confidence this is what the task's "TiDB `payouts_temp`" reference means, but the exact name doesn't match. `is_payout_service`/`payout_service_enabled` are two distinct signals: `is_payout_service` is a **per-payout-row** boolean (`Payout\Entity::IS_PAYOUT_SERVICE`, Entity.php:139, set on the entity once a payout is confirmed PS-owned); `payout_service_enabled` is a **per-merchant-feature and/or DCS-backed per-(merchant,balance)-config** flag (`Feature\Constants::PAYOUT_SERVICE_ENABLED`, Constants.php:1138 — see §2, §11). Forward dual-write removal PR context: not found in this repo (shallow single-commit clone, no PR/CHANGELOG history available); flagged **unresolved**. Confidence: **high** on the corrected mechanism above (read from `DualWrite/Payout.php` and `Repository.php` in full); **unresolved** on removal PR context.

---

## 9. Balance / ledger

- **`balance` table** (`app/Models/Merchant/Balance/*`): confirmed as the monolith's own source-of-truth store for banking (X) balances — `Merchant\Balance\Service::fetchBalancesForBalanceIds()` (Service.php:93) simply does `$this->repo->balance->getBalancesForBalanceIds($balanceIds)` against this local table; this backs the `internal_balances_queued` route, i.e. **internal callers (most plausibly PS) batch-resolve balances from the monolith**, confirming the monolith remains balance-authoritative even for PS-owned payouts. Confidence: high.
- **BAS (`banking_account_statement/payout_update`)**: `Payout\Core::payoutUpdateByBASRecon()` (Core.php:10286) — links a BAS-reconciled bank-statement row to a payout: for local (non-PS) payouts it sets `transaction_id`/`transaction_type` on the payout and, for a `FAILED` local payout, calls `reversePayout()` (i.e. **the reversal-creation condition is: the payout the BAS row reconciles against is a local, non-PS-owned payout, and its current status is `FAILED`**); for PS-owned (or not-found-locally) payouts it forwards to `payoutServiceBankingAccountStatementClient->updatePayoutAfterBASRecon($input)`. A comment (10292-10299) notes ledger-event sending for this path was **deliberately deprecated** in favour of X-Account-Statement-based reconciliation. **Caller identification (closes UNRESOLVED_QUESTIONS item 31)**: `app/Http/Route.php`'s `$internalApps` service-credential allowlist (~line 19379) lists `bas_recon_payout_update` only under the `payouts_service` internal-app credential block (Route.php:19379-19382, same block as `payouts_service_dual_write`, `internal_balances_queued`, `workflow_state_callback`) — no `xas` credential block exists anywhere in `Route.php`. **This means Payouts Service (PS) itself is the authenticated caller of this monolith endpoint, not XAS directly and not a monolith-internal self-call.** Confidence: high on caller identity (direct allowlist evidence), high on reversal condition (read the method body).
- **Ledger vs local-`balance` decision**: the merchant feature `ledger_reverse_shadow` (`Feature\Constants::LEDGER_REVERSE_SHADOW`) is checked pervasively throughout `Payout\Core` (9+ call sites, e.g. Core.php:2721, 4955, 7450, 7614, 9517, 9883) to decide whether transaction/balance bookkeeping goes through the Ledger service (reverse-shadow) or the local `balance`/`transaction` tables. `payout_service_enabled` is explicitly coupled to it: `Payout\Core.php:9507-9518` comments that dual-write is required "for `ledger_reverse_shadow` and `payout_service_enabled` merchant," and `Feature\Service.php:290-297` blocks removing `ledger_reverse_shadow` while `payout_service_enabled` is still assigned, since the latter depends on the former always being reverse-shadow. Confidence: high.

---

## 10. Merchant webhooks / "Stork"

**Correction to task premise**: "Stork" in this repository is **not** a webhook-registration service — every hit for `stork`/`Stork` (e.g. `app/Trace/TraceCode.php:7629-7636 SEND_EMAIL_ATTEMPT_STORK*`, `app/Mail/Transaction/Payout.php:101 shouldSendEmailViaStork()`) is part of the monolith's **transactional-email delivery** integration, unrelated to payout event webhooks. No merchant-webhook-registration-via-Stork flow exists in `api`.

Actual mechanism found: payout lifecycle events are dispatched as Laravel events — `api.payout.updated` (Core.php:1749), `api.payout.failed` (2796, 4236, 4382, 6315), `api.payout.processed` (4751), `api.payout.reversed` (6099, 6166, 10346) — via `$this->app->events->dispatch(...)`. These are the monolith's internal event bus; the actual merchant-facing webhook (`payout.processed`, `payout.failed`, `payout.reversed`) dispatch/queueing/delivery logic lives in the separate `app/Models/Event` / webhook subsystem, whose listener wiring was not conclusively traced to these specific event names in this pass (only `Payout\EventRetrieved` → `PayoutEventListener::onRetrieved` was found registered in `app/Providers/EventServiceProvider.php:193-195`, which appears to be a different concern — event-retrieval auditing, not webhook dispatch). **This item is only partially resolved**: confirmed the monolith owns webhook event emission itself (no Stork/external SOP), but the exact event→webhook-delivery wiring for payout events needs a follow-up read of `app/Models/Event/Core.php` and the webhook queue/worker. Confidence: **medium**.

---

## 11. Config/feature sources for payouts

- **`features` table** (`app/Models/Feature/Constants.php`): **46** constants match `PAYOUT`/`FUND_ACCOUNT` (grep -c). Representative sample: `PAYOUT_SERVICE_ENABLED` (1138), `LEDGER_REVERSE_SHADOW` (1461), `PG_LEDGER_REVERSE_SHADOW` (1473), `ENABLE_APPROVAL_VIA_OAUTH` (2110), `SKIP_HOLD_FUNDS_ON_PAYOUT`, `BLOCK_VA_PAYOUTS`, `ICICI_2FA`, `ENABLE_IP_WHITELIST`, `ENABLE_IP_WHITELIST_FETCH` (all located via direct usage in `Payout\Core`/`Processor`/`MerchantIpFilter` above).
- **DCS** (Dynamic Config Service, `app('dcs_config_service')`, client scaffolding at `app/Services/Dcs/Features/Base.php`, mock at `app/Services/Mock/DcsServiceClient.php`): used as a **faster, per-merchant-per-balance** alternative check for `payout_service_enabled` — `Payout\BankingAccount\Core.php:962` calls `app('dcs_config_service')->fetchConfiguration($key, $merchantId, [Constants::PAYOUT_SERVICE_ENABLED], $mode)`; a "no configuration exists" error is treated as **not enabled** (976-985, distinguished from a genuine DCS failure which is traced as `DCS_READ_FEATURES_FAILURE`); latency is recorded via `Metric::DCS_PAYOUT_SERVICE_ENABLED_FETCH_DURATION_MS` regardless of outcome (997-1004).
- **Splitz/RazorX**: `Merchant\Core::isSplitzExperimentEnable()` is the uniform entrypoint used throughout the payout code for experiment gating. Confirmed examples: `app.payout_create_direct_ps_splitz_experiment_id` (direct-PS-cutover gate, DirectToPayoutsServiceGate via PayoutController.php:164), `RazorxTreatment::PAYOUT_BULK_APPROVE_ASYNC` (Service.php:6806), `RazorxTreatment::NON_TERMINAL_MIGRATION_HANDLING` (Core.php:4636, gates the 180s API↔PS mutex), `RazorxTreatment::WORKFLOW_ACTION_WITH_DB_DUAL_WRITE_PAYOUTS_SERVICE` (Service.php:1241), `RazorxTreatment::ACCOUNT_STATEMENTS_DUAL_WRITE_CUTOFF` (Core.php:10279), `app.payouts_to_phone_number_splitz_experiment` (Core.php:976, gates sending IFSC info in webhooks for mobile-number payouts). Confidence: high.

---

## 12. Crons / queues touching payouts

**Queue names** (from `protected $queueConfigKey` in `app/Jobs/*.php`):

| Job | Queue |
|---|---|
| `ApprovedPayoutDistribution` | `approved_payout_distribute` |
| `ApprovedPayoutProcessor` | `approved_payout_processor` |
| `FreePayoutMigrationForPayoutsService` | `free_payout_migration_to_payouts_service` |
| `FundManagementPayoutCheck` | `fund_management_payout_check` |
| `OnHoldPayoutsProcess` | `on_hold_payouts_process` |
| `PartnerBankDowntimeHoldPayouts` | `partner_bank_on_hold_payouts_process` |
| `PayoutAttachmentEmail` | `payout_attachment_email` |
| `PayoutDataConsistencyChecker` | `data_consistency_checker` |
| `PayoutsAutoExpire` | `payouts_auto_expire` |
| `PayoutServiceDataMigration` | `payout_service_data_migration` |
| `PayoutServiceDualWrite` | `payout_service_dual_write` |
| `PayoutServiceDualWriteDirectPush` | `payout_service_dual_write_direct_push` |
| `PayoutSourceUpdaterJob` | `payout_source_updater` |
| `PayoutUsageEventProcessing` | `payout_usage_event_processing` |
| `ProcessPayoutNotification` | `payout_downtime` |
| `QueuedPayouts` | `queued_payouts` |

(`BatchPayoutsProcess`, `PayoutLinkNotification`, `PayoutPostCreateProcess[LowPriority]`, `QueuedPayoutsInitiate`, `ScheduledPayoutsProcess` exist but no `$queueConfigKey` was found by direct grep — may use a differently-named property or a default queue; **unresolved**.)

**Cron schedules**: this repo does **not** contain k8s CronJob manifests (no `k8s/` directory was found matching that description — the repo root has no `k8s` directory at all in this checkout) or a Laravel `Console\Kernel::schedule()` with payout entries; the only in-repo cron-like artifact is `scripts/crontab.sh`, which has exactly one payout line: `add_cron "1 5-18 * * 1-6" "payouts_prod_live" POST .../payouts/initiate/kotak` (crontab.sh:70) — a Kotak-channel-specific legacy initiation cron, hourly on the hour between 5am-6pm IST, Mon-Sat. The routes `payouts_process_queued`, `payouts_auto_cancel_on_expiry`, `payouts_dispatch_stuck`, `payouts_pending_push_notification_cron`, `ca_check_fund_management_payout_cron`, `payouts_dual_write_failure_processing_cron`, `payouts_create_failure_processing_cron`, `payouts_update_failure_processing_cron` all exist as HTTP-triggerable endpoints in `Route.php` but their **invocation schedule (frequency) is not defined anywhere in this repo** — they must be scheduled by an external cron/Airflow/k8s-CronJob system outside `api`. Confidence: **high** that schedules are not in this repo; this is a hard boundary of what this repo can answer.

---

## UNRESOLVED_QUESTIONS closed (items 1-8, 31)

> Note: the original numbered items from `UNRESOLVED_QUESTIONS.md` were not re-quoted verbatim (not re-read line-by-line in this pass to save budget); this section maps this investigation's findings to the **topics** items 1-8 and 31 evidently cover, per the task's own framing. If the mapping below doesn't align 1:1 with the exact wording in that file, treat the section-by-section findings above (§1-12) as the authoritative answer and this table as a cross-reference aid.

| # | Topic (as inferable from task framing) | Resolution | Evidence | Confidence |
|---|---|---|---|---|
| 1 | Does `/v1/payouts` route to PS unconditionally, or is there monolith-side logic first? | **Both exist, coexisting**: a new Splitz-gated "direct" bypass (§2a) and the older "classic, business-logic-first, then proxy" path (§2b), selected per-request by `DirectToPayoutsServiceGate`. | PayoutController.php:126-151, DirectToPayoutsServiceGate.php | high |
| 2 | Is the local `payouts` table deleted? | **No** — schema-present, actively read/written by approve/reject, BAS-recon fallback, manual batch status update, IRCTC-Axis flow. Confusable with PS's *own*, separate `payouts` table reached via `Connection::PAYOUT_SERVICE_DATABASE`. | §2 "Remaining monolith-side reads/writes"; Repository.php:5290-5399; Base/Repository.php:1456-1472 | high |
| 3 | What headers mark a proxied request to PS? | `X-Payouts-Service-Proxy` (1=classic/business-logic-done, 0=direct/PS-must-run-logic), Passport JWT, `X-Payout-Actor-Id/Type`, `X-Payout-Idempotency`, BasicAuth key/secret. | Create.php:208-217; Base.php:541-596 | high |
| 4 | Does the monolith retry PS calls? | **No** — single attempt, throws on failure/timeout, 60s timeout / 10s connect-timeout, explicitly documented as unsafe to retry (risk of double-pay). | Base.php:93-95,145-227; PayoutController.php:190-196 comment | high |
| 5 | Is FAV enforced as a payout-create precondition? | **No** — no such check exists anywhere in the Payout module. | §6 | high |
| 6 | What is the 180s mutex in FTS/payout status handling? | Per-payout-id Redis mutex (`PAYOUT_MUTEX_LOCK_TIMEOUT=180`), gated by Splitz experiment `NON_TERMINAL_MIGRATION_HANDLING`, wraps `handlePayoutProcessed` to serialize concurrent classic/PS status mutation during migration. | Core.php:160, 4636-4653 | high |
| 7 | Does `payouts_service/dual_write` write to a deleted table / error out? | **No** — it's queue-backed (`payout_service_dual_write`, 5 retries) and functioning. It reads the authoritative row from PS's own database and upserts it into the monolith's own **local `payouts` table** (via `DualWrite\Payout::dualWritePSPayout()`, which explicitly sets the local `live`/`test` connection before saving) — the reverse direction from what an earlier draft of this document claimed. Only a small bookkeeping watermark (`payout_meta_temporary`) is written into PS's own database, not payout business data. | §8; `app/Models/Payout/DualWrite/Payout.php:24-71`, `app/Models/Payout/DualWrite/Base.php:36`; Core.php:10219-10274, 10762-10793; Repository.php:216-226, 5290-5324 | high |
| 8 | Is `is_payout_service` the same signal as `payout_service_enabled`? | **No** — `is_payout_service` is a per-payout-row boolean; `payout_service_enabled` is a per-merchant-feature and/or DCS per-merchant-balance config. Related but distinct. | Entity.php:139; Feature/Constants.php:1138; BankingAccount/Core.php:962-1004 | high |
| 31 | Who calls the monolith's `banking_account_statement/payout_update` (`bas_recon_payout_update`) endpoint — XAS directly, or the monolith's own BAS module — and under what conditions does it create reversals? | **Payouts Service (PS) is the caller** — `bas_recon_payout_update` is allowlisted only under the `payouts_service` internal-app credential in `Route::$internalApps` (no `xas` credential block exists in the file at all). Reversal is created only when the BAS-reconciled row maps to a **local (non-PS-owned) payout whose current status is `FAILED`** (`reversePayout()` call inside `payoutUpdateByBASRecon`); PS-owned payouts are forwarded to PS's own BAS client instead of reversed locally. | §9; Route.php:19379-19382; Core.php:10286-10328 | high |

---

## Remaining unknowns / follow-ups

1. ~~`UNRESOLVED_QUESTIONS.md` item 31~~ — **resolved** in a follow-up pass: PS is the caller of `bas_recon_payout_update` (see UNRESOLVED_QUESTIONS table above and §9).
2. **`merchant_users` membership check** — not found inside `api`; plausibly enforced upstream in Edge/dashboard-api before the Passport JWT reaches this monolith. Out of this repo's scope to confirm further.
3. **Real (non-mock) AuthZ-service client** — `AuthzEnforcerClient` exists only under `app/Services/Mock/`; a production remote-AuthZ integration was not located and conflicts with the confirmed-local `AdminAccess`/`UserAccess` permission checks (§3, §7). Either it's genuinely unused/staged, or its production counterpart lives under a name not yet grepped. Needs follow-up.
4. **`fee_recovery_*` admin routes' exact `Permission::CONST`** — routes located, permission mapping not confirmed (§7).
5. **Payout-event → merchant-webhook delivery wiring** — confirmed the monolith emits `api.payout.processed/failed/reversed` Laravel events itself (no Stork involvement), but the listener that turns these into outbound merchant webhooks was not traced into `app/Models/Event/Core.php` (§10).
6. **Dedicated FTS-vs-payouts reconciliation cron** — only the FTA-vs-bank-statement recon (`reconcileFundTransfers`) and a plausibly-related `PayoutDataConsistencyChecker` job were found; the latter's body was not read (§4).
7. **Cron schedules (frequency)** for all `*_cron`/`*process*` payout routes are **not in this repo** — confirmed absence of k8s manifests / Kernel schedule; must be sourced from the deployment/orchestration repo (§12).
8. **`admin_payouts_workflow_config_get`/`WorkflowServiceController@createWorkflowStateMap` body** — route located, handler not read in depth (§5).
9. Balance/webhook/config/cron areas (§9-12) were investigated directly by the main agent after an initial attempt to delegate to a background sub-agent failed (forking is disabled from within an already-forked worker) — coverage here is real but less exhaustive than §1-8 given the single-pass time budget; flagged for a possible follow-up pass if higher confidence is needed on §10 and §12 specifically.
