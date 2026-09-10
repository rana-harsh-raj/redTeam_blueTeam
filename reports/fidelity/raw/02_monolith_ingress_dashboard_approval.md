# Lane 02 — Payout INGRESS through the API monolith and the dashboard

Verifies/corrects `reports/raw-findings/20_api_monolith.md` §2,§3,§5, `12_payouts_approval_queue_bulk.md`,
`23_workflows_batch.md` §B, `07_frontend_bff.md`, `ARCHITECTURE_DELTA.md` §(d) against source. Investigation
combined direct reads (this agent) and six deep parallel re-verification passes (general-purpose subagents,
each independently re-reading source, not trusting the prior reports) — all citations below were re-derived
from source in this pass, not copied from the earlier drafts.

## Scope and sources read

Repos (shallow clones, read-only), commit ids:

| Repo | Commit | Role |
|---|---|---|
| `api` | `2d665f918b60e917ec92be648fb5816d247f72b1` | PHP/Laravel monolith — ingress, dashboard proxy, admin, PS-proxy |
| `payouts` | `4bf3dbf9239feadea6d65ca90c893a988e116173` | Go "Payouts Service" (PS) |
| `batch` | `3839dd77d061422c144b7409af401415d598c54b` | Java/Spring bulk-upload engine |
| `workflows` | `080d71a51b777c89af586c92ac4b5f683a473a7c` | Go Workflow Service (WFS), Cadence |
| `dashboard` | `60ed3c781ded186ad38c41ea8f9fc90b7f3cd7c3` | Razorpay PG merchant dashboard (BE proxy) |
| `x` | `40b093f97ec1b8162e7f46ecee223af04982d5ba` | RazorpayX merchant dashboard SPA |
| `frontend-x` | `efe5df3d26ec5bfaef69b8cb66fb558674cd960f` | Payee-facing payout-links app |
| `xperience` | `88729560e52b35aa9d5768d67ff807f847537ee7` | Go BFF (bulk payouts, petty-cash) |
| `admin-dashboard` | `e29bdf988410704c3053417e91051a0ec88cebbb` | RZP internal ops SPA |
| `terraform-kong` / `edge` | `8ffe965b…` / `da9ff5b2…` | Kong config / Lua plugins |
| `dcs` | `fca59e3e…` | Dynamic Config Service |
| `kube-manifests` | `9226a892…` | k8s manifests |
| `vendor-payments` | `20c4f4d5…` | one confirmed internal caller of `internalContactPayout` |

Files opened in `api` (non-exhaustive, load-bearing ones): `app/Http/Route.php` (23,164-line route table),
`app/Http/Controllers/PayoutController.php`, `app/Models/Payout/{Service,Core,Validator,Repository,Entity,
DirectToPayoutsServiceGate,CreateFlowObserver}.php`, `app/Models/Payout/Processor/Base.php`,
`app/Services/PayoutService/{Base,Create,Workflow,ManualAction,PayoutShadowService}.php`,
`app/Http/Middleware/{Authenticate,MerchantIpFilter,MerchantIdempotencyHandler,UserAccess,AdminAccess}.php`,
`app/Http/{BasicAuth/BasicAuth,AccessAuthorizationService,RequestHeader,Kernel}.php`,
`app/Models/User/Core.php`, `app/Http/Controllers/WorkflowServiceController.php`,
`app/Models/Workflow/Service/StateMap/{Service,Core}.php`, `app/Jobs/{ApprovedPayoutProcessor,
ApprovedPayoutDistribution}.php`, `database/migrations/2020_03_13_162700_create_idempotency_keys_table.php`,
`app/Admin/Permission/Name.php`. In `dashboard`: `app/Http/Controllers/GenericController.php`,
`app/Admin/ApiRequestAny.php`, `app/Http/ApiUrl.php`, `app/Http/routes.php`,
`apps/shell/src/server/utils/decryptPassportTokenFromHeader.ts`. In `admin-dashboard`: `js/admin/adminActions/
actionModals/{RejectPayout,ClearPendingPayouts,ForceUpdatePayoutStatus,ForceUpdatePayoutStatusBulk,
RetryPayout,FreePayoutMigration}.js`, `js/admin/payouts/PayoutsManualActions.js`. In `x`: `src/js/api/{outflow.js,
payoutsApiSlice.ts,bulkPayoutsApiSlice.ts,batch.js,workflow.ts}.js`, `nginx/nginx.conf.template`,
`package.json`. In `xperience`: `internal/interceptors/passport_interceptor.go`, `internal/app/client/
apiservice/create_petty_cash_payout.go`, `config/default.toml`. In `batch`: `src/main/resources/{payout,
payout_approval}.json`, `src/main/java/com/razorpay/batch/batchengine/item/processor/{BulkApiCallDataProcessorImpl,
BaseApiProcessor}.java`, `src/main/java/com/razorpay/batch/utils/RestCallUtils.java`. In `payouts`:
`internal/routing/router/{payout_internal_routes_with_passport,payout_internal_routes}.go`,
`internal/app/bulkPayoutsProcessor/{core,validation,service}.go`, `internal/app/tidb/mock.go`,
`internal/app/payouts/fetch_orchestrator/strategies/fetch_by_id_strategy.go`, `internal/helpers/apidb.go`,
`config/{prod,default,devstack,e2e}.toml`. In `terraform-kong`: `base/payouts-proxy-cutover/*.tf`,
`templates/payouts-proxy-cutover/*.tf`, `prod/api/api.tf`, `prod/api/variables.tf`. In `edge`:
`kong-plugins/kong-plugin-upstream-jwt/kong/plugins/upstream-jwt/access.lua`,
`kong-utils/kong-utils-common/kong/utils/common.lua`. Plus `ENV2_COMPOSE/substitutes/kong-lite/server.py`,
`ENV2_COMPOSE/substitutes/monolith-stub/{server.py,CONTRACT.md}`, `ENV2_COMPOSE/docker-compose.yml`,
`reports/ENV2_BUILD_STATUS.md`, `reports/raw-findings/31_env2_bringup_notes.md`,
`reports/{ARCHITECTURE_DELTA,UNRESOLVED_QUESTIONS,SYNTHETIC_FIXTURE_SPEC,EFFECTIVE_CONFIG_GAPS}.md`.

Not opened this pass (named as gaps below): `authz` (repo exists in scratchpad but was out of this lane's
assigned scope), PS's own internal validation/create-side source beyond what's cited from the `payouts`
findings already in the corpus, `config-proto`.

---

## Production behaviour

### 1. `POST /v1/payouts` — public API create pipeline, in order

**Route.** `payout_create` = `['post','payouts','PayoutController@postFundAccountPayout']`
(`api/app/Http/Route.php:2311`). **CORRECTION to `20_api_monolith.md` §1**: the route is `$private`-only
(`Route.php:5780`, inside the `$private` array 5577–6027). It is **not** in `$admin` (8727–10221) — a
Razorpay-admin-authenticated caller cannot hit `POST /v1/payouts` directly. (`$private` membership also
enables Bearer/OAuth auth for this route via `Authenticate.php:364`, `$bearerAuthRoutes = merge($private,
OAUTH_SPECIFIC_ROUTES)`.)

**Middleware order** (`api/app/Http/Kernel.php:55-93`, `$middlewarePriority`, CONFIRMED — not in the prior
report at all): `Authenticate → BusinessAuth → SDKMetric → AdminAccess → UserAccess → SubscriptionProxy →
ExcelStoreProxy → FailureEventsInterceptor → Workflow → MerchantIpFilter → EventTracker → P2p →
HttpEncryptionMiddleware → MerchantIdempotencyHandler → RequestContextHandler`.

**Auth-type determination** (`Authenticate::handle()`, `app/Http/Middleware/Authenticate.php:159-224`): if an
Edge passport is present, auth context (merchant, app identity, consumer type) comes entirely from passport
claims, bypassing key/secret. Else, Bearer token (if route ∈ `$bearerAuthRoutes`) → `authenticateBearerAuth()`;
else Basic-Auth dispatched by `$this->routeType`: `Route::PRIVATE → ba->privateAuth()`, `Route::PROXY →
ba->proxyAuth()`, `Route::INTERNAL`/`ADMIN → ba->appAuth()`.

**IP filter** (`app/Http/Middleware/MerchantIpFilter.php:46-69`): runs (a) for strict-private-auth (key/secret,
not passport-app-auth — the inline comment at line 50 says "application auth via EDGE is treated as private
auth, we do not require IP filter") → `authenticateIpForPrivateAuth()`; or (b) for proxy-auth (dashboard) →
`authenticateIpForProxyAuth($clientRequestIp)`, reading `X-Dashboard-IP`, checked against
`merchant->getMerchantDashboardWhitelistedIpsLive/Test()`; empty list ⇒ no check. A newer, Splitz/feature-gated
per-service Redis whitelist also exists for private-auth (`applyNewWhitelistIfApplicable`, lines 176-296),
keyed by `Route::getServiceMappingForIpWhitelist()` (`payout_create` → service `api_payouts`, `Route.php:20626`).
IP filtering, being Laravel middleware, runs **before** the Splitz gate and balance validation (both live inside
the controller/service layer).

**Idempotency.** Client-facing header is **`X-Payout-Idempotency`** (`RequestHeader::X_PAYOUT_IDEMPOTENCY`,
`app/Http/RequestHeader.php:88`) — config `$idempotentRoutesConfig['payout_create']` (`Route.php:20527-20533`)
`{SOURCE_TYPE: PAYOUT, HEADER_KEY: X_PAYOUT_IDEMPOTENCY, IKEY_MANDATORY: true, FEATURE_FLAG_FOR_MANDATORY_IKEY:
Feature::PAYOUT_IDEM_KEY_REQUIRED}`. Enforced by `MerchantIdempotencyHandler.php:84-210`, gated on
strict-private-auth or a named-internal-app list, and `Route::isApplicableForMerchantIdempotency()`. Mandatory
flag is layered: Splitz ramp-up experiment (`ikey_auto_enforcement_ramp_up_experiment_id`) + merchant feature
`PAYOUT_IDEM_KEY_REQUIRED`; on any internal error it **fails closed** (blocks the payout) —
`isIdempotencyKeyMandatory()`, lines 503-556. **Table**: `idempotency_keys`
(`database/migrations/2020_03_13_162700_create_idempotency_keys_table.php`), columns id,
`idempotency_key`(255), `merchant_id`, `source_id`, `source_type`, `request_hash`, timestamps.
**Uniqueness scope: composite unique `(idempotency_key, merchant_id)`** — per-merchant, not global.
Lookup: `IdempotencyKey\Repository::findByIdempotencyKeyAndMerchant()`. Per-key mutex (TTL 1200s). On
retry with an already-associated response: SHA-256 request-body-hash comparison — mismatch throws
`BAD_REQUEST_SAME_IDEM_KEY_DIFFERENT_REQUEST`; match → returns the **cached response** (200, no
re-execution) without re-running any business logic. Lock contention on the same in-flight key → error
`SERVER_ERROR_ANOTHER_OPERATION_PROGRESS_SAME_IDEM_KEY` (not a 409 — 409 `BAD_REQUEST_CONFLICT_*` is
reserved for a separate route list, `$httpStatusCodeConflictIdempotentRoutes`, which lists only
`transfer_create`). **Inbound vs outbound key are the SAME value**: `Create::addIdempotencyKeyToHeaders()`
(`app/Services/PayoutService/Create.php:188-206`) re-fetches the same `idempotency_key` row (via
`basicauth->getIdempotencyKeyId()`, set by the middleware) and forwards `X-Payout-Idempotency: <the original
client value>` to PS — not independently generated.

**Splitz "direct" cutover gate** (`app/Models/Payout/DirectToPayoutsServiceGate.php`, invoked from
`PayoutController::decideServeFromPayoutsService()`, `PayoutController.php:163-189`):
- Experiment id from config `app.payout_create_direct_ps_splitz_experiment_id` (`PayoutController.php:165`);
  gate returns false immediately if empty or merchant unresolved.
- `shouldDivert()` decision order (`DirectToPayoutsServiceGate.php:59-136`): (1) false if not live mode; (2)
  false if `isAppAuth===true` (internal-app traffic stays classic — PS's root path runs public-create
  validation internal callers were never subject to); (3) false if body `merchant_id` names a different
  merchant than the caller (classic flow honours it for balance scoping; PS's direct path derives merchant
  from auth only — logged via `CreateFlowObserver::observe(FLOW_BODY_MID_MISMATCH, …)`); (4) else calls the
  injected experiment closure — `(new Merchant\Core())->isSplitzExperimentEnable({id: merchant_id,
  experiment_id, request_data: json_encode(payload)}, 'enable', TraceCode::PAYOUT_CREATE_DIRECT_PS_SPLITZ_ERROR)`
  — **variant string checked is literally `'enable'`**; any Splitz-layer failure is caught and treated as
  `false` (fail-safe to classic).
- If diverted: `createPayoutOnPayoutsService()` (`PayoutController.php:198-209`) calls
  `PayoutServiceCreate::createPayoutDirect($input, $merchantId)` and returns the PS response **verbatim**
  (see "Response translation" below) — no monolith business logic runs at all.

**Loop-trace instrumentation (new, not in prior report)**: every hit to `postFundAccountPayout()` first logs
`TraceCode::API_PAYOUT_CONTROLLER_ENTRY` including `loop_header_present: X-Payouts-Service-Proxy header !=
null` and `x_forwarded_for` (`PayoutController.php:88-96`, comment: *"Pairs with the Payouts Service's
PROXY_CUTOVER_DECISION / PS_PAYOUT_CONTROLLER_ENTRY logs to trace the Edge -> PS -> API -> PS path of a
single client request"*), then best-effort mirrors the raw request to a **shadow router**
(`PayoutShadowService::mirrorRequest('POST', path, input, headers)`, errors swallowed). `mirrorRequest()`
(`app/Services/PayoutService/PayoutShadowService.php:60-160+`) reads config
`applications.payouts_shadow_router` (`payouts_shadow_router_url`, `kill_switch`,
`*_connect_timeout`/`*_timeout`), resolves `merchant_id` from body/basicauth/`X-Shadow-Merchant-Id` header,
and — unless `kill_switch` is on — additionally gates the actual mirror send on a per-merchant Splitz
experiment (`shouldMirrorRequest()`). **This is a previously-undocumented migration/canary mechanism**:
production payout-create traffic (for enrolled merchants) is being mirrored to a second endpoint for
comparison, independent of and prior to the direct-diversion gate. Not covered by `20_api_monolith.md` at
all.

**Classic-path pre-proxy validations, in order** (`Service::fundAccountPayout()`, `Service.php:562-884`):
1. `isStrictPrivateAuth()` or allowed internal app, else `BAD_REQUEST_FORBIDDEN` (579-583). *Monolith-only.*
2. `processAccountNumber($input,true)` (587) → `Merchant\Validator::validateAndTranslateAccountNumberForBanking()`
   locally, unless merchant flag `isTranslateAccountNumberToBalanceIdInPayoutsService` delegates this to PS
   itself. *Monolith-only unless delegated.*
3. `Validator::BEFORE_CREATE_FUND_ACCOUNT_PAYOUT` rules (589-590, `Validator.php:370-383`):
   `fund_account_id`/`fund_account` required-without-each-other, `source_details.*` shape,
   `enable_workflow_for_internal_contact`, TDS/attachments/subtotal_amount/remitter_details shape.
   *Monolith-only.*
4. `validateAndUpdateCardMode()` (592). *Monolith-only.*
5. Composite-payout / mobile-number-payout classification (594-601) — flags only, no side effects.
6. **Gate**: `!isMobileNumberPayout && isMonolithProxyForPayoutServiceMerchant($balance)` (631, body
   8169-8196: live-mode + `PayoutsBankingAccount\Core::merchantMigratedToPayoutServiceByMerchantIdAndBalanceIdOnMonolithProxy()`
   + excludes partner-OAuth callers). **If true, diverts to `monolithProxyForPayoutsServiceMerchant()` (8201)
   and returns immediately — every step below (7-9) is skipped.**
7. (Non-diverted only) Mobile-number-payout Splitz check + `BAD_REQUEST_MOBILE_NUMBER_PAYOUT_NOT_ALLOWED`
   (647-676).
8. `checkIfPayoutIsAllowed()` (678, body 5665-5709): Lite-account block, CA-active check
   (`checkIfDirectAccountIsActive`, 5711-5742), ICICI-direct-account 2FA/BaaS gating, tokenised-card
   mode-mismatch check under `ALLOW_NON_SAVED_CARDS`. *Monolith-only, and already skipped when step 6
   diverts.*
9. Composite-payout branch: high-TPS/async ingress checks, contact/fund-account creation.
10. `createPayoutToFundAccount()` (783) → `Processor\Base::createPayout()` (`Processor/Base.php:286+`):
    `preValidations()` (290, body 3599-3626 — funds-on-hold check gated by `SKIP_HOLD_FUNDS_ON_PAYOUT`,
    then `blockBankingVaPayoutsIfApplicable()` at 3632-3646 gated by `BLOCK_VA_PAYOUTS` feature + shared-VA
    balance type + Lite-block), workflow applicability, then a **separate, older "V1" migration check**
    (`createPayoutViaMicroservice()`, line 315→5147, `isPayoutServiceIfApplicable()`) that can *also* proxy
    to PS after locally fetching pricing/fund-account/credits/VA-info first; if not migrated, falls to
    genuinely local creation — Shield fraud evaluation (`shieldEvaluatePayoutsRequest()`,
    `Processor/Base.php:5451`/called at 2745) runs here, post-entity-build, in `Processor/Base.php` only
    (no "shield" string anywhere in `Service.php`/`Core.php`).

**No FAV gate, no pricing computation, and no fund-account/contact-ownership check appear as discrete
pre-proxy steps for the V2/direct/direct-diversion paths** — pricing and fund-account/contact fetch happen
only inside the legacy V1 proxy path's `createPayoutViaMicroservice()` (5147-5245); the V2 balance-based
path passes the input through unenriched (`Create::createRequestBodyV2`, `Create.php:385-393`), and the
controller-level direct-diversion path does zero enrichment at all (`Create.php:221-234` docblock: *"No
enrichment, no pricing push... PS owns all of it"*).

**Exact PS request** (`app/Services/PayoutService/Create.php`):
- URIs: root `/payouts` (:30), `/payouts/payouts_internal` (:31, app-authed calls), `/payouts/
  internal_contact_payout` (:32, explicit internal-contact param), `/payouts/rzp_fees_payout` (:36, purpose
  override). Selection: explicit `$isInternal` flag → internal-contact; else `basicauth->isAppAuth()` →
  `payouts_internal`; else root; `purpose===RZP_FEES` overrides regardless. **No `payout_type` field drives
  this** — `Entity::PAYOUT_TYPE` exists but is used only for `single`-vs-`bulk` analytics tagging in
  `Processor/Base.php` (7 sites), unrelated to URI selection.
- Auth: BasicAuth `key`/`secret` from `config('applications.payouts_service')[mode]['payout_key'/'payout_secret']`
  (`Base.php:80-88`).
- Passport JWT: `Passport::PASSPORT_JWT_V1` header (`getHeadersWithJwt()`, `Base.php:541-579`), with a
  privilege-auth special case re-signed as an application-consumer JWT.
- Actor headers (`addActorsToHeaders()`, `Base.php:582-597`): merchant call → `X-Payout-Actor-Id=merchantId`,
  `X-Payout-Actor-Type=merchant`; internal-app call (`isInternalApp()`, an OR of ~12 specific app flags,
  `BasicAuth.php:2107-2126`, distinct from the simpler `isAppAuth()`) → `X-Payout-Actor-Id=<app name or
  'external'>`, `X-Payout-Actor-Type=application`. **CORRECTION**: actor headers are added on the classic
  V1/V2 proxy paths only — the controller-level **direct-diversion path deliberately omits them**
  (`Create.php:235-258` docblock: *"Actor identity is deliberately NOT sent — PS resolves it from the
  passport"*).
- `X-Payout-Idempotency`: see above — passed-through client value.
- `X-Payouts-Service-Proxy` marker (`addProxyCutoverHeader()`, `Create.php:208-219`): set to `1` by the
  legacy V1 return-pass proxy, `0` by the controller-level direct-diversion call. **CORRECTION**: the V2
  balance-based smart-routing proxy (`Processor\Base::createPayoutViaPayoutsService` →
  `Create::createPayoutViaPayoutsService`, `Create.php:133-186`) **does not call `addProxyCutoverHeader` at
  all** — no such header on that third path. There are three distinct proxy mechanisms (legacy-V1
  enrichment-based, V2 pass-through, controller-level direct), only two of which set this header.
- Timeouts 60s / 10s connect (`Base.php:93,95`); no retry — one `Requests::request()` call, re-thrown on any
  failure, documented as unsafe to retry (risk of double-pay).

**Response translation.**
- **Direct-diversion**: `response($psResponse['body'], $psResponse['status_code'], $psResponse['headers'])`
  (`PayoutController.php:198-209`) — exact PS status/body relayed; headers filtered by
  `Create::relayableResponseHeaders()` (`Create.php:268-291`, strips hop-by-hop headers, defaults
  `content-type` to `application/json`).
- **Classic/V2-proxy**: **not** relayed raw. `Base::makeRequestAndGetContentV2()`→`checkResponseForError()`
  (`Base.php:433-539`) JSON-decodes PS's error body and **re-throws monolith-native exceptions**
  (`BAD_REQUEST_VALIDATION_FAILURE`, duplicate-create → payout-id-bearing message,
  `BAD_REQUEST_PAYOUT_NOT_ENOUGH_BALANCE_BANKING`/`BAD_REQUEST_SUSPICIOUS_TRANSACTION`, else generic
  `ServerErrorException`). On success, `Service::fundAccountPayout()` returns the monolith's own
  `toArrayPublic()`/`payoutServiceResponse` shape via `ApiResponse::json($data)` — the monolith's own
  response envelope/status, not PS's raw HTTP status.

**What a bypass loses** (a caller hitting PS's `/v1/payouts` directly with only a passport, as the twin does):
- `MerchantIpFilter` (dashboard/private IP allowlist) — genuinely monolith-only middleware; never runs
  against PS.
- `MerchantIdempotencyHandler`'s client-facing hash-mismatch/mutex/cache-response semantics
  (`idempotency_keys` table) — PS may have an independent idempotency store, but the monolith's specific
  409/mutex/replay behaviour above does not exist there (UNKNOWN whether PS's is equivalent — out of this
  lane's repo-read scope for PS internals).
- `Validator::BEFORE_CREATE_FUND_ACCOUNT_PAYOUT` + card-mode validation (Service.php:589-592).
- `isMonolithProxyForPayoutServiceMerchant()`/`DirectToPayoutsServiceGate::shouldDivert()` — decision points
  that simply never execute; whether PS runs an equivalent internal gate is UNKNOWN from this repo.
- `checkIfPayoutIsAllowed()` (Lite-block, CA-active, ICICI-2FA/BaaS gating, tokenised-card check) — note this
  is *already* skipped on the monolith's own balance-proxy path for migrated merchants, so its absence is
  not unique to full bypass for those merchants specifically.
- `preValidations()` (funds-on-hold, `BLOCK_VA_PAYOUTS`) — monolith-only as implemented in
  `Processor/Base.php`.
- Shield fraud evaluation as wired in `Processor/Base.php` — runs only on the fully-local/legacy-V1 creation
  path; whether PS has its own independent Shield integration is UNKNOWN (out of scope for this lane; the
  companion `payouts` lane's report should be checked).
- Balance resolution semantics: monolith honours a differing body `merchant_id` (scoped lookup); PS's direct
  path derives merchant purely from auth — this is exactly why `DirectToPayoutsServiceGate` refuses to
  divert such requests, but a caller that skips the monolith entirely gets PS's (narrower) behaviour
  unconditionally.
- The shadow-router mirror and `CreateFlowObserver`/trace-code observability — pure telemetry, no
  behavioural loss, but relevant to note the twin also has none of this migration instrumentation.

---

### 2. OTP variants, `payouts_internal`, `payouts_internal_direct`, `internalContactPayout`

**Route/auth-group table (re-verified; CORRECTS `20_api_monolith.md` §1's "auth group(s)" column for every
row below)**:

| Route name | URI | Actual auth-group membership | NOT in |
|---|---|---|---|
| `payout_create_with_otp` | `payouts_with_otp` | `$private`(5595), `$userWhitelist`(7350), `$proxy`(8002) | `$internal`, `$admin` |
| `composite_payout_create_with_otp` | `composite_payout_with_otp` | `$userWhitelist`(7351), `$proxy`(8003) | `$private`, `$internal`, `$admin` |
| `payout_create_2FA` | `payouts/2fa/create` | `$private`(5596), `$userWhitelist`(7352), `$proxy`(8004) | `$internal`, `$admin` |
| `payout_send_2FA_otp` | `payouts/2fa/send_otp` | `$userWhitelist`(7353), `$proxy`(8005) | `$private`, `$admin` |
| `composite_payout_internal` | `composite_payout_internal` | `$internal`(6203) | `$admin` |
| `payout_create_internal` | `payouts_internal` | `$internal`(6593) | `$admin` |
| `payout_create_internal_direct` | `payouts_internal_direct` | `$internal`(6594) | `$admin` |
| `payout_create_on_internal_contact` | `internalContactPayout` | `$internal` (same block) | `$admin` |
| `payout_approve`/`payout_reject` | `payouts/{id}/approve\|reject` | `$private`, `$userWhitelist`, `$proxy` | `$internal`, `$admin` |
| `payout_approve_bulk`/`reject_bulk` | `payouts/approve\|reject/bulk` | `$userWhitelist`, `$proxy` | `$private`, `$admin` |

**Net correction**: none of these ten routes appears in `$admin` (8727–10213) or `$direct` (12582–12834) at
all — a Razorpay-admin-authenticated caller cannot hit any create/approve/reject/OTP route directly, contrary
to the prior report's blanket "(…, admin)" tagging. Feature gate `Feature::PAYOUT` is additionally required
for the OTP/2FA family (`Route.php:20313-20315`, `$routeNameToFeaturesMap`).

**Real callers found (grepped `x`, `frontend-x`, `xperience`, `dashboard`, `workflows`, `vendor-payments`)**:
- `payouts_with_otp` — **`x/src/js/api/outflow.js:88`**, `fetch.post('/payouts_with_otp', …)` — the
  RazorpayX dashboard SPA's payout-create flow (attaches `X-Payout-Idempotency` when an experiment is on).
  No caller found in PG `dashboard` or `frontend-x`.
- `payouts/{id}/approve`/`reject`/`approve/bulk`/`reject/bulk` — **`x/src/js/api/payoutsApiSlice.ts:186-220`**
  — mutations post `otp`, `token`, `payout_ids`, `queue_if_low_balance`, `user_comment`.
- `composite_payout_internal` — **`xperience/internal/app/client/apiservice/create_petty_cash_payout.go:15`**
  (`CreatePettyCashPayoutUrl = "composite_payout_internal"`) — xperience's own petty-cash payout creation,
  app-authed.
- `internalContactPayout` — **`vendor-payments/internal/payout/core.go:84`**
  (`CreatePayoutOnInternalContact = "v1/internalContactPayout/"`) — confirmed real caller.
- `payouts_internal` (create) — no caller found in `x`/`frontend-x`/`xperience`/`dashboard`; only appears in
  `workflows/tests/e2e/constants/routes/route_list.go:17` (test-harness seeding, not production evidence).
  Consistent with `20_api_monolith.md`'s separately-confirmed `$internalApps` allowlist (`cross_border_import_
  service`, `accounts_receivable`, `vendor_payments`, `payout_links`, `capital_early_settlements`, `scrooge`,
  `xpayroll`, `settlements_service` — none of these repos are in this scratchpad's clone set, so their call
  sites can't be directly confirmed here; the allowlist itself is the strongest evidence of real callers).
- `payouts/2fa/create[_internal]`, `payouts/2fa/send_otp`, `payouts_internal_direct` — **no caller found**
  in any repo read this pass outside mock/test fixtures. UNKNOWN — likely a repo outside this scratchpad
  (an ICICI-specific frontend, or `scrooge`/`settlements-service`).

**Required roles/permissions and where enforced — CORRECTION to `20_api_monolith.md` §3**: dashboard-side
enforcement is `UserAccess::getRoutePermission()` (`app/Http/Middleware/UserAccess.php:518-527`), which reads
**`Route::$bankingRoutePermissions`** (`Route.php:11966-12581`) — **not** `Route::$routePermission` as the
prior report stated; `$routePermission` (`Route.php:10221-11950`) is instead consumed by `AdminAccess.php:117`
(RZP-admin permission check), `Workflow.php:229` (workflow-engine rule lookup), and API-docs generation. Both
tables happen to carry the same `payout_create_with_otp`/`payout_approve`/etc. → `Permission::CREATE_PAYOUT`/
`APPROVE_PAYOUT` mappings, but gate different auth surfaces. A **second, live enforcement path** exists,
selected by whether the merchant has custom-access-control (CAC) roles enabled
(`UserAccess::validateBankingUserAccess`, lines 257-307): CAC merchants → `validateBankingUserRoutePolicyV2`
→ unconditionally calls `AccessAuthorizationService::hasAccessAllowedV2()`; legacy-role merchants →
`validateBankingUserRoutePolicy` → local role-permission map, falling to `AccessAuthorizationService
::hasAccessAllowed()` when feature `AUTHORIZE_VIA_AUTHZ` is on. `AccessAuthorizationService`
(`app/Http/AccessAuthorizationService.php:56-102`) maps route names to resource paths (`payout_create_with_otp`
→ `/v1/payouts_with_otp`, `payout_approve` → `/v1/payouts/{id}/approve`) and calls
`AuthzEnforcer\Service::enforcerAPIEnforce()` (`app/Models/AuthzEnforcer/Service.php:33-58`) — a generated
Swagger client, mock-vs-real selected at runtime by `config('applications.authzXPlatformEnforcer.mock')` =
`env('AUTHZ_XPLATFORM_ENFORCER_MOCK', true)` — **defaults to mock**; real client requires an explicit env
override. **CORRECTION**: this is a fully-wired, env-toggled live integration point, not dead/mock-only code
as the prior report implied — but the actual prod toggle value is UNKNOWN from source.

**PS-proxy request differences**: see §1's "Exact PS request" for URI/actor-header logic — same mechanism
applies uniformly to these variants (URI keyed on `isInternal`/`isAppAuth`/purpose, not a `payout_type`
field; actor headers = merchant vs internal-app-name).

**OTP verification mechanism** — two genuinely distinct systems:
- **Generic OTP** (`payouts_with_otp`, `payout_approve`, `payout_approve_bulk`): `User\Core::verifyOtp()`
  (`app/Models/User/Core.php:6344-6372`), builds a context/receiver/source payload via
  `getTokenAndRavenOtpReqParams()` (6402-6473) and calls `$this->app->raven->verifyOtp($payload, $mock)`
  (6371) — **Razorpay's internal Raven notification/OTP service**, not Kong/Edge, not a local OTP table.
  Create-time: `fundAccountPayoutWithOtp()` (`Service.php:1834-1840`), action=`create_payout`. Approve-time:
  `Service.php:1219-1226`, action=`approve_payout`, mandatory for non-partner approvals; partner-OAuth
  approvals instead run `validatePayoutForApprovalViaOAuth()` (1213), no OTP.
- **ICICI 2FA** (`payouts/2fa/*`): `Core::triggerIciciOtpForPayoutViaFts()` (`Core.php:649`), builds
  `{source_id, source_type=PAYOUT, source_account_id, mode, amount}` and calls
  `fts_fund_transfer->requestOtpCreate($input)` (668) — **owned by FTS**, not Raven. Called from
  `createPayoutAndTriggerIciciOtp()` (606-647, on create, failures swallowed) and
  `Service::otpSendForIciciCa2fa()` (1949-2009, the `payout_send_2FA_otp` handler — additionally requires
  `payout.status===PENDING` and `checkIfMerchantIsAllowedForIciciDirectAccountPayoutWith2Fa()`, feature
  `ICICI_2FA`).

---

### 3. Approval/rejection pipeline and Workflow Service interaction

**Single approve/reject** (`Service.php:1165-1231`, `approveFundAccountPayout()`): local lookup
`repo->payout->findByPublicIdAndMerchant()` (1179); on **any** exception, falls back to
`handlePayoutServicePayoutForWorkflowAction($id)` (1185, body 1233-1292). **CORRECTION**: this fallback is
**not** an HTTP call to PS to perform the approval — it is a Splitz-gated
(`RazorxTreatment::WORKFLOW_ACTION_WITH_DB_DUAL_WRITE_PAYOUTS_SERVICE`) dual-write: `Payout\Core
::processDualWrite()` (1310) pulls/mirrors the PS row into the local `payouts` table, then re-queries
locally. OTP mandatory for non-partner approvals (see §2); partner-OAuth approvals skip it.

**PS route for approve/reject** — found and read in full, `app/Services/PayoutService/Workflow.php`:
`WORKFLOW_PAYOUT_SERVICE_URI = '/payouts/payouts_internal/%s'` (:13); `approvePayoutViaMicroservice()` = `POST
.../approve/`, body `{queue_if_low_balance}` (:28-47); `rejectPayoutViaMicroservice()` = `POST .../reject/`,
empty body (:111-128). Both send only the Passport JWT — **no actor headers on this call**. Dispatched from
`Payout\Core::processWorkflowActionOnPayout()`→`processWorkflowActionOnPayoutBase()` (`Core.php:3232-3410`):
if `payout->getIsPayoutService()===true`, call the microservice methods; else run the legacy local workflow
(`Workflow\Action\Checker\Core::create`, 3319, then local `processApprovePayout`/`processRejectPayout`).

**`wf-service/state/callback`** (`WorkflowServiceController::createWorkflowStateMap()`,
`app/Http/Controllers/WorkflowServiceController.php:125-159`) — read in full. This is **purely state-map
bookkeeping**, not the terminal approve/reject action: delegates to `Workflow\Service\StateMap\Service
::create()` (checks duplicate `state_id` → 409 `BAD_REQUEST_WORKFLOW_STATE_CALLBACK_DUPLICATE`), which
persists a `workflow_state_map` row locally via `StateMap\Core::create()` (58-165) and — if the payout is
PS-owned and the caller isn't itself PS — **additionally forwards the same state callback to PS** via
`payoutWorkflowServiceClient->createStateCallbackViaMicroservice($input)` (97, `POST /workflow/state` on PS,
`Workflow.php:54-74`), gated by Splitz `NON_TERMINAL_MIGRATION_HANDLING` — a dual-write/mirror so
role-based payout-list filters see the state (inline comment, 99-104). The **actual terminal action** is a
**separate** WFS→API callback, to distinct routes `payout_approve_internal`/`payout_reject_internal`
(`Route.php:2420-2421`, `payouts_internal/{id}/approve|reject`), allowlisted only for the `workflows`
internal-app credential (`Route.php:19096-19099`), landing on
`Service::processActionOnFundAccountPayoutInternal()` (1320-1353).

**Bulk approve/reject via the dashboard — CORRECTED, most significant discrepancy found this pass**: the
dashboard-facing `payout_approve_bulk`/`payout_reject_bulk` routes call
`Service::bulkApproveFundAccountPayouts()` (1507-1552) / `bulkRejectFundAccountPayout()` (1610), both
**synchronous**: `verifyOtp` once (1515, action=`approve_payout_bulk`), then a plain `foreach` loop calling
`Core::approvePayout($payout,$input)` per payout (1534) — **no Splitz gate, no queue, no
`ApprovedPayoutProcessor`/`ApprovedPayoutDistribution` involvement at all**. `shouldProcessBulkApproveAsync()`
(6802-6811, checks `RazorxTreatment::PAYOUT_BULK_APPROVE_ASYNC`) is actually referenced from a **different**
method — `processActionOnFundAccountPayoutInternal()` (the single-payout WFS-callback handler from above),
not the dashboard's bulk endpoint. When that flag is enabled, it dispatches
`ApprovedPayoutDistribution::dispatch()` (1341, queue `approved_payout_distribute`,
`app/Jobs/ApprovedPayoutDistribution.php:17`), whose `handle()` in turn dispatches
`ApprovedPayoutProcessor::dispatch()` (`ApprovedPayoutDistribution.php:84`, queue
`approved_payout_processor`) — a **two-hop** queue chain (Distribution → Processor), not the single dispatch
the prior report implied. **The dashboard's synchronous bulk-approve UI action and the async
`ApprovedPayoutProcessor` pipeline are triggered by two entirely different route families**
(`payout_approve_bulk` vs. the WFS-callback `payout_approve_internal`), not "same mechanism sync vs async."

**Batch service's own `payouts/bulk_approve` — CORRECTION to `ARCHITECTURE_DELTA.md` Confirmed-1 (see
Corrections table below)**: this is a **third, distinct** bulk-approval mechanism, and it targets the
**monolith**, not Payouts Service, and not via the WFS-callback routes above — see §5.

**`ApprovedPayoutProcessor`** (`app/Jobs/ApprovedPayoutProcessor.php`, full read): `queueConfigKey =
'approved_payout_processor'`; no Laravel `$tries`/`$backoff` — hand-rolled `MAX_RETRIES=2`,
`RETRY_INTERVAL=50s` (lines 35,38). `handle()` (68-139): `repoManager->payout->findByPublicId($this->payoutId)`
(80, local-`payouts`-table query) → `validatePayoutStatusForApproveOrReject()` → rate-limit check → `Core
::processActionOnPayout($isApproved,$payout,$data)` (94) → `delete()`. `processActionOnPayout` branches on
`getIsPayoutService()`: true → `payoutWorkflowServiceClient->approvePayoutViaMicroservice()`/
`rejectPayoutViaMicroservice()` (same PS routes as above); false → local processing. **On the reported prod
issue** (local `payouts` table absent): the first statement (`findByPublicId`) fails with a generic
`\Throwable`; `$payout` remains `null`; `isPayoutInTerminalState()` short-circuits `false` (payout is null) at
line 173; falls to `checkRetry()` (181-218) — re-dispatches with 50s delay up to 2 retries, then on
exhaustion **pushes to a raw SQS dead-letter queue** (`config('queue.approved_payout_processor_dlq.<mode>')`)
and deletes the job. **Not an infinite retry storm** — bounded (~100s) then DLQ'd.

---

### 4. Admin/repair endpoints

Re-verified route/permission table (all line numbers CONFIRMED, all inside `$admin` 8727–10213 and
`$routePermission` 10221–11951, all inside `admin_dashboard` internal-app block 14874–17600):

| Route | URI | Line | Permission (line) |
|---|---|---|---|
| `payout_reject_admin_bulk` | `admin/payouts/cancel` | 2342 | `REJECT_PAYOUT_BULK` (10400) |
| `payouts_dashboard_manual_actions` | `payouts/manual_action` | 2345 | `PAYOUT_MANUAL_ACTION` (11409) |
| `update_payout_status` | `payouts/{id}/manual/status` | 4570 | `PAYOUT_STATUS_UPDATE_MANUALLY` (11591) |
| `update_payout_status_batch` | `payouts/manual/status_update/batch` | 4571 | `PAYOUT_STATUS_UPDATE_MANUALLY` (11592) |
| `payout_retry` | `payouts/{id}/retry` | 2357 | `RETRY_SETTLEMENT` (10669) |
| `payout_workflow_retry_admin_bulk` | `admin/payouts/workflow_retry` | 2419 | `RETRY_PAYOUT_WORKFLOW_BULK` (10401) — **correction: not `RETRY_SETTLEMENT`**, that's shared only by `setl_retry`/`payout_retry` |

`payouts_auto_cancel_on_expiry` (6516) is inside `$internal` (6027-7327), not `$admin` — it's a cron-triggered
internal route, allowlisted for the `cron` app (17913).

**Real callers confirmed** (`admin-dashboard` repo, RZP-internal ops SPA):
`js/admin/adminActions/actionModals/RejectPayout.js:27` → `POST live/admin/payouts/{id}/reject` (note: a
distinct per-id reject exists, addressed separately from the bulk-cancel route below);
`ClearPendingPayouts.js:27` → `POST live/admin/payouts/cancel`;
`ForceUpdatePayoutStatusBulk.js:23` → `PATCH live/payouts/manual/status_update/batch`;
`ForceUpdatePayoutStatus.js:42` → `PATCH live/payouts/{id}/manual/status`;
`RetryPayout.js:27` → `POST {mode}/payouts/retry`;
`PayoutsManualActions.js:153` → `POST live/payouts/manual_action`;
`FreePayoutMigration.js:35` → `POST live/admin/payouts/free_payout_migration`;
`FeeRecoveryRetry.js:21` → `POST live/payouts/fee_recovery_retry/manual`.
This confirms `admin-dashboard` (not `dashboard` or `x`) is the real caller of the entire admin/repair
surface, via `Auth::guard('api')` RZP-staff session (see §7).

**Method bodies** (all read in full this pass):
- **`bulkRejectFundAccountPayouts`** (`admin/payouts/cancel`, `PayoutController.php:465` →
  `Service.php:1610`): body `payout_ids[]` (public_id, size 19), `force_reject`(bool, optional),
  `user_comment`(optional, max 255). Fetches all requested payouts from local `payouts` in one query
  (`findManyByPublicIds`/…`AndMerchant`), validates `validatePayoutStatusForApproveOrReject()` per row, then
  **loops, calling `Core::rejectPayout($payout,$input)` once per payout** — **not a single PS bulk-reject
  call**. `rejectPayout`→`processWorkflowActionOnPayout` branches on `getIsPayoutService()`: PS-owned rows
  fan out to `rejectPayoutViaMicroservice()` (one PS call per PS row); non-PS rows mutate the local table
  directly. Response: `{total_count, processed_ids: [], failed_ids: ["{public_id} - {msg}", …]}`.
- **`payoutManualActions`** (`payouts/manual_action`, `Service.php:7423`): body `{action, bulk_input[max
  MAX_COUNT_PAYOUTS_BULK_MANUAL_ACTION], reason}`, `action` constrained to an allowlist
  (`PAYOUTS_MANUAL_ACTIONS`, `Validator.php:177-190`): `dual_write, processed_to_processing,
  approve_workflow_payouts, reject_workflow_payouts, process_bank_transfer, redis_get,
  generate_merchant_invoice, manual_smart_collect_entity_creation, onboard_collectx_merchant,
  smart_collect_va_credited_webhook, source_status_update_retry, onboard_slice_va` — a general admin
  dispatcher, not payout-specific only. For `processed_to_processing`/`approve_workflow_payouts`/
  `reject_workflow_payouts`, calls `payoutServiceManualActionClient->forwardManualActionToPayoutsMicroservice()`
  (7486-7533) → **PS route `POST /payouts/manual_action`** (same path string as the monolith route, a
  distinct outbound call), body `{action, bulk_input, reason, queue_if_low_balance}`. PS's response
  segregates `ps_success_payout_ids`/`ps_failed_payout_ids`/`api_payout_ids`; only `api_payout_ids` are then
  processed locally.
- **`updatePayoutStatusManually`** (`payouts/{id}/manual/status`, `Service.php:4626`): tries local
  `findOrFail($id)` first; on **any** `\Throwable` (including a SQL "table doesn't exist" error, not just
  not-found) falls back to `getAPIModelPayoutFromPayoutService($id)` (4635) — a PS-fetched in-memory model.
  Body: `status`(required, must be a final status per `Status::isFinalState` — PROCESSED/REVERSED/REJECTED/
  CANCELLED/FAILED), `failure_reason`/`fts_account_type`/`fts_fund_account_id`(conditionally required). The
  actual switch in `Core::updatePayoutStatusManually` (6987-7013) only implements PROCESSED/REVERSED/FAILED
  — REJECTED/CANCELLED hit a no-op `default:` branch (just logs `UNKNOWN_STATUS_SENT_TO_PAYOUT`). Transition
  guard (`Validator::validatePayoutStatusUpdateManually`, 1533-1570) is **skipped entirely for non-shared
  (CA/direct) balances**; for shared balances, `INITIATED→PROCESSED` or `PROCESSED→REVERSED` require
  `fts_fund_account_id`+`fts_account_type` or throw; `INITIATED→REVERSED` without those fires a Slack alert
  (`x-payouts-core-alerts`) but is still allowed.
- **`updatePayoutStatusManuallyInBatch`** (`payouts/manual/status_update/batch`, `Service.php:5023`,
  line drifted +6 from a new `fav_failed` special case): `repo->payout->findMany($payoutIds)` (5036), loops,
  splitting PS ids out (`continue`) for a second pass processed via `getAPIModelPayoutFromPayoutService()`
  then the same `updatePayoutAndFTAManually()` handler used for local rows.
- **`postPayoutRetry`** (`payouts/{id}/retry`, `PayoutController.php:604`) → **`Service::processReversedPayout($id)`**
  (not `bulkRetryWorkflowOnPayout` as the prior report implied): `repo->payout->findByPublicId($id)` — **local
  only, no PS fallback** — → `Core::retryReversedPayout($payout)` (855). A PS-only payout id here has no
  fallback path, a materially different resilience profile from the manual-status endpoints.
- **`bulkRetryWorkflowOnPayout`** (`admin/payouts/workflow_retry`, `Service.php:1740`): body `payout_ids[]`;
  requires admin auth explicitly (1746-1748); loops, `Core::retryPayoutWorkflow($payout,$input)` (3675) →
  `workflowService->createWorkflow($payout,$input)` — targets **WFS**, not PS; no `is_payout_service` branch.

**Admin auth model** (`app/Http/Middleware/AdminAccess.php::policyChecker()`, full read): (1)
`getRoutePermission()` (115-126) — `Route::$routePermission[$routeName]`, throws if unmapped; (2)
`admin->getRolesAndPermissionsList()` (`Models/Admin/Admin/Entity.php:689-708`) —
`$this->roles()->with('permissions')->get()` — **local DB-backed** admin→role→permission; (3)
`checkTenantRoleAllowed()` (346-361) — `RouteRoleScope::getRoles($routeName)`; null ⇒ pass; else intersect
with admin's roles; (4) `checkPermissionAllowed()` (363-383) — explicit wildcard-permission denial, then
`in_array` membership; (5) `Admin\Group\Core::groupCheck($admin,$merchant)` (328) — only if a merchant is in
scope and the route isn't in `Route::$skipMerchantAccessCheckOnSpecificAdminAuthRoutes`.

**3-month auto-cancel cron** (`processDispatchForPayoutsAutoExpiry`, `Service.php:2530-2557`): threshold
`Carbon::now(IST)->startOfDay()->subMonths(3)`; fetches `Status::PENDING`(2538) and `Status::QUEUED`(2540)
ids separately, merges, dispatches one `PayoutsAutoExpire` job per id (queue `payouts_auto_expire`).
**RESOLVED (was medium-confidence in the prior report)**: the reject-vs-fail branching happens one level
deeper, in `Core::processAutoExpiryOfPayouts($payoutId)` (4482-4525): current status re-read under mutex,
`PENDING → forceRejectPayout()` (rejected); `QUEUED` or `CREATE_REQUEST_SUBMITTED → handlePayoutFailed()`
(failed); anything else → logged no-op.

**`payouts_service/*` PS-caller-only confirmation**: `BasicAuth::verifyInternalApp()`
(`app/Http/BasicAuth/BasicAuth.php:1662,1680-1684`) reads `Route::$internalApps[$this->internalApp]` and
rejects any route not in that app's allowlist. The `payouts_service` block (`Route.php:19379-19426`) is
confirmed to allowlist `create_payout_entry`, `payouts_service_dual_write`, `bas_recon_payout_update`,
`payout_fetch_by_id_internal`, `create_FTA_payout_service`, `create_ledger_payout_service`,
`deduct_credits_via_payout_service`, `reverse_credits_via_payout_service`, `create_pricing_for_payout_service`,
`decrement_free_payouts_payouts_service`, `rollback_free_payouts`, `payouts_service_mail_and_sms`,
`payouts_source_update`, `status_details_source_update`, `pricing_fetch_plan`. **Minor string corrections**
to the task's shorthand names: `fetch_pricing_info`→actual `create_pricing_for_payout_service`;
`decrement_free_payouts`→`decrement_free_payouts_payouts_service`; `free_payout_rollback`→`rollback_free_payouts`;
`mail_and_sms`→`payouts_service_mail_and_sms`; `source_update`→`payouts_source_update`;
`dual_write`→`payouts_service_dual_write`; `delete_card_metadata`→`delete_card_meta_data_and_vault_token`;
`renameAttachments`→`rename_attachments_for_payouts`. Substance confirmed: these are all PS→monolith
callback-only routes.

---

### 5. Bulk (`payouts/bulk` and `payouts/bulk_approve`)

**`payout.json`** (`batch/src/main/resources/payout.json`, full read, 261 lines, 4 steps): `DataStaging`
(genericFileReader, 20 named CSV columns, `linesToSkip:1`, `useFileHeaderName:true`) → `DataProcessing`
(`maxThreads:10, chunkSize:1, enableBulk:true, bulkSize:5`; `dataReader: batchEntryDataReader,
batchEntryStatusList:["CREATED"], saveState:false, pageSize:1000`; `dataProcessor.type:
bulkApiCallDataProcessor, apiProcessor.type: payoutApiProcessor, idempotentKey:"idempotency_key"`;
`endpoint:"payouts/bulk", httpMethod:"post", failOnServerError:false`) → `OutputCreation` (24-column CSV incl.
`id`, `error.code`, `error.description`) → `Notification` (`batchCompletionEmailTasklet`, `authType:internal`).

**Idempotency-key derivation — weak, no cross-attempt coordination**:
`BulkApiCallDataProcessorImpl.java:106,163` — `idempotentKeyPrefix="batch_"`, key = `"batch_" +
record.getId()` (the `BatchEntry` row's own Hibernate-generated PK). **No Redis mutex, no cross-row/
cross-file dedup** at the Batch layer for this batch type. Contrast: `payout_link_bulk[.json/_v2.json]` and
six `v2/payouts_*_bene_*_process.json` types use `CacheIdempotencyEnabledBulkApiCallDataProcessorImpl` — SHA-1
hash of business columns, Redis lookup, `RedisMutexLock`(300s TTL) + 24h cache — a materially stronger
mechanism not applied to the classic `payout.json` path.

**Outbound target/auth/headers**: `getBasePath()` (`BulkApiCallDataProcessorImpl.java:211-281`) falls through
to `appConfiguration.getApiBasePath()` = `https://api.razorpay.com/v1/` (**the public API-monolith edge
domain**, not a direct PS host) for both `payout.json` and `payout_approval.json` (no special-case override
for either). Auth: HTTP Basic, `RestCallUtils.computeAuthHeader()` (`:55-69`) — username
`"rzp_" + mode + "_" + entityId"` (or `"rzp_" + mode"` only, for `authType=internal`), password =
single shared `api.secret` config (`${BATCH_API_SECRET:secret}` — **not** the merchant's real key/secret).
Default `authType = PROXY` (`BulkApiCallDataProcessorImpl.java:84`) — the merchant-impersonation form.
Headers (`HeaderConstant.java:9-18`, all set in `RestCallUtils.setDefaultHeaders`): `Authorization`,
`Content-Type: application/json`, `X-Batch-Id`, `X-Creator-Id`, `X-Creator-Type`, `X-Entity-Id`.

**Request/response contract**: body is a **raw JSON array**, one element per row of the 5-row bulk group
(`BaseApiProcessor.buildRequestBody()`, 235-253, injecting the `idempotentKey` field), built from
`payout.json`'s `##Column##`-templated parameters. Response: single JSON object, array field `items`
(configurable, default `"items"`), matched back to request rows by `idempotency_key`
(`BaseApiProcessor.processBulkApiResponse()`, 328-376); non-2xx overall fails the whole group; any
per-item `http_status_code` in `retryAbleStatusCode` (default `[500]`) throws `HttpRetryException`
(`handleBulkApiFailure()`, 386-404), retrying the **entire 5-row group** via Spring `RetryTemplate` — fixed
backoff, **5 attempts, 5000ms** (`application.properties:201-202`; exponential alternative exists but
`payout.json` doesn't opt in).

**PS side** (`payouts` repo): route `POST /v1/payouts/bulk` (`payout_internal_routes_with_passport.go:21-27`,
`BasicAuth(cred.API,cred.Workflow) + PassportAuthentication`) → `BulkPayoutsProcessorService
::CreateBulkPayouts` → `Service` layer, which itself Splitz-gates (merchant-level,
`SplitzExperimentList.BulkPayoutConcurrencyExperiment`) between `Core.CreateBulkPayoutsConcurrent`
(worker-pool, 5-10 workers) and `Core.CreateBulkPayouts` (sequential) — **RESOLVED (was "unconfirmed which
is wired"): both are live, chosen per-merchant at runtime, not one dead path.** Both converge on
`HandleBulkPayoutInputForSinglePayout`. Idempotency: `IdempotencyKey` is `required` per-row
(`validation.go:22`), uniqueness `(merchant_id, idempotency_key)` in table `bulk_idempotency_keys`
(`repo.go:28-35`), per-`(batch_id, idempotency_key)` mutex (`core.go:289-295`).

**`payout_approval.json` (bulk approve/reject) — MAJOR CORRECTION** (see Corrections table): this does
**not** call PS's `/v1/payouts/bulk_approve` (no such PS route exists — grepped the whole `payouts` repo,
zero hits beyond unrelated SMS-copy strings). It calls **the API monolith's own** `payouts/bulk_approve`
(`payout_approval.json:66`, `endpoint:"payouts/bulk_approve", httpMethod:post,
apiProcessor.type:payoutApprovalProcessor`), same base-path/auth/header mechanism as `payout.json`
(`PayoutApprovalProcessorImpl` only overrides `passSettingsIfRequired` to inject `user_comment`/`email`).
On the monolith: `Route.php:2319` `'payout_bulk_approve' => ['post','payouts/bulk_approve',
'PayoutController@approvePayoutBulk']` → `PayoutController.php:778-785` →
`Service::approveBulkPayout()` (`Service.php:3539-3599+`), which **validates `X-Batch-Id` is present**
(`validateBatchId`, 3552, comment "bad request if not from batch service"), then per-item calls
`processEntryForBulkPayoutApproval()` (3629) → `approvePayoutsFromBatchService()`/`rejectPayoutFromBatchService()`
(3652-3668) → `repo->payout->findByPublicId(...)` → `Core::approvePayout()`/`rejectPayout()` — **the same
Core methods used by ordinary single-payout approval**, running fully inside the monolith. `findByPublicId`
here (`Repository.php:411-427`) is the general PS-first/local-fallback override (see §6), so PS-owned
payouts are correctly resolved despite the "local" framing of the lookup. This endpoint therefore runs
**inside the monolith's own business logic** (per-row DB transaction, `Core::approvePayout`), not a
proxy/pass-through to PS, and **does not obviously go through WFS either** for the actual state check — it
reuses the same `Core::approvePayout()` any direct dashboard approve would use.

**Dashboard's own bulk-upload path** — three upload endpoints, effectively 4 API generations
(`x/src/js/api/bulkPayoutsApiSlice.ts`, `batch.js`): (1) legacy `POST /batches/validate`, multipart
`FormData{file,type}`; (2) v1 `POST /payouts/batch/validate`, multipart `FormData{file}`; (3) v2, **JSON not
multipart** — `POST /xperience-edge/bulk-payouts` with `{ufh_file_id}` (pre-uploaded file handle) when flag
`isXpsEdgeBulkPayoutsMigrationEnabled` is on, or `POST /xperience/bulk-payouts/validate` with
`{file: base64, file_name, mime_type}` when off. **Correction to prior report**: v2 paths are JSON with a
file-handle-reference or base64 payload, not multipart — only legacy/v1 use multipart. Flags
(`isXpsEdgeMigrationExpEnabled`, `isXpsEdgeBulkPayoutsMigrationEnabled`) are resolved **client-side once per
session** via `abService.getVariants([...])` keyed to `merchantId` (`x/src/js/modules/SplitzService/
fetchBulkSplitz.ts`), cached in Redux, threaded through as booleans — not a per-response server flag. URL
swap is a trivial string substitution (`updateXperienceHostname.ts`: `apiEndpoint.replace('xperience',
'xperience-edge')`).

---

### 6. Migration/proxy-cutover state

**`payouts-proxy-cutover` (terraform-kong) — CONFIRMED devstack-only, with the exact diverted route set**:
`terraform-kong/base/payouts-proxy-cutover/` is the sole instantiation (`atlantis.yaml:326-333`, one project
`base-payouts-proxy-cutover`). `main.tf` backend key `terraform-kong/dev-serve/base/payouts-proxy-cutover/
terraform.tfstate`. No `prod/`/`stage/`/`sg/`/`us/` wrapper exists anywhere, though
`templates/payouts-proxy-cutover/variables.tf:1-6` already validates `env ∈ {prod, devstack}` — the module
is prod-ready in template form but not instantiated for prod. **Only two routes are actually diverted**
(non-empty `upstream-override` conditions in `config.tf`): `payout_create-payouts-proxy-cutover` (POST
`/v1/payouts`, cohort-gated: consumer `C0uw3CXseZPwmJ`, `auth_flows=["basic-auth-x:merchant"]`, rollout=1)
and `payouts_scheduled_time_slots-payouts-proxy-cutover` (GET `/v1/payouts/schedule/timeslots`, rollout=1,
both live/test). `admin_dashboard_override_conditions`/`api_override_conditions` are unset (default `{}}`) —
so shadow GET routes (fetch_by_id, fetch_multiple, purposes, summary) exist as Kong route definitions but
stay on the monolith default upstream. No `contacts`/`fund_account` route appears anywhere in this module.
`payouts_with_otp` is absent from all three route-definition files in the module — **correction to phrasing**:
it isn't excluded by any blocklist, it was simply never added to this module's route set (it's defined
elsewhere, `base/api/api.tf`/`base/api-dashboard/*`, and stays on the monolith by omission).

**Loop-guard — CORRECTION to `ARCHITECTURE_DELTA.md` W6/C45 ("confirmed absent")**: real loop-guards exist
on both hops. (a) PS-side: `payouts/internal/routing/shadowgateway/middleware.go:14-18` defines
`HeaderPSProxied = "X-PS-Proxied"`, enforced at `:91-95` — a request already carrying this header short-
circuits the shadow-gateway middleware (`metric.ShadowGatewaySkippedTotal.WithLabelValues("loop_guard")`);
set on every PS→monolith hop by `monolith_client.go:177-183`. (b) Monolith-side: `X-Payouts-Service-Proxy`
(`api/app/Http/RequestHeader.php:90`, set by `Create::addProxyCutoverHeader()`, `Create.php:208-219`) is
logged for loop-tracing at ingress (`PayoutController.php:88-95`, `loop_header_present` in
`API_PAYOUT_CONTROLLER_ENTRY`), explicitly commented as pairing with PS's `PROXY_CUTOVER_DECISION`/
`PS_PAYOUT_CONTROLLER_ENTRY` logs to trace the full "Edge → PS → API → PS" path. Neither loop-guard actively
rejects a looped request at the monolith's ingress (the PS-side one does, at PS); this nuance (monolith logs
but doesn't reject; PS's shadow-gateway middleware short-circuits) should replace the blanket "absent" claim.
`anti-spoof-strip` (strips a client-supplied `X-Payouts-Service-Proxy`/`X-Merchant-Id`/`X-Entity-Id` before
reaching PS) is wired for `payout_create` but **off by default** for the dashboard/admin-dashboard shadow
routes (`dashboard_anti_spoof_strip_routes`/`admin_dashboard_anti_spoof_strip_routes` default `[]`).

**`is_payout_service` semantics and read-path fallback for legacy rows — RESOLVED (closes
UNRESOLVED_QUESTIONS #7)**. Two independent, complementary mechanisms:
- **Monolith side** (`api/app/Models/Payout/Repository.php`): every read method (`find`, `findByPublicId`,
  `findByIdAndMerchantId`, `findByIdAndMerchant`, `findByPublicIdAndMerchant`) follows the identical pattern
  — `shouldSkipApiFallback()` (7474-7491): **returns `true` by default** in live mode (config
  `app.payout_repository_no_fallback_kill_switch` unset/false ⇒ skip-fallback is ON — i.e. **PS-authoritative,
  no local DB at all** is the default steady state); the "kill switch," despite its name, is a **revert** —
  turning it on restores DB fallback. When skip-fallback is on: query PS only, find-or-fail (no DB touch at
  all). When off (kill-switch on, or non-live mode): **PS-first, DB-fallback-on-null** — call
  `fetchViaPayoutServiceByID()`; if PS returns a row, optionally shadow-compare against the local DB (gated
  by Splitz `PAYOUT_REPOSITORY_DATA_COMPARISON_ENABLED`, exception-safe) and return it; **only if PS returns
  null does it fall through to `parent::find…()` against the local `payouts` table** — this is the concrete
  legacy (`is_payout_service=0`, PS-unaware) row fallback. `forceFetchFromAPIDB=true` (used by e.g. the
  dual-write mechanism) skips PS entirely and forces the local-DB read.
- **PS side** (`payouts/internal/app/payouts/fetch_orchestrator/strategies/fetch_by_id_strategy.go:21-53`):
  "Fallback order: AppDB → ApiDB → TiDB." The ApiDB leg is gated by
  `helpers.IsApiDbEnabledForPayouts` (`internal/helpers/apidb.go:23-25`), whose docstring describes a
  Splitz experiment (`use_api_db_for_payouts_experiment`, fail-safe true) but whose **body is hardcoded
  `return false`** — the direct-DB leg to the monolith's `payouts` table is **unconditionally skipped in
  current code**, consistent with that table's prod deletion; legacy rows are served exclusively via
  AppDB→TiDB today. (Contrast: the sibling `IsApiDbEnabledForPayoutStatusDetails` *does* implement the real
  Splitz-gated check — the payouts variant looks deliberately stubbed off post-deletion.)
  `payouts/internal/app/tidb/mock.go:57-58`'s "1234=legacy(is_payout_service=0), 5678=PS(is_payout_service=1)"
  comment is confirmed to be **test-mock-only** (`core.go:228-230`, `if t.Mock {...}`), documenting an RCA
  dated 2026-09-02 that motivated SLIT test coverage — not production logic itself.
- **Practical implication for W4/C42** (ARCHITECTURE_DELTA's "`ApprovedPayoutProcessor` fails with `Table
  'api.payouts' doesn't exist`"): reads through the standard `Repository` overrides are safe by default
  (PS-authoritative, DB never touched); the job's own `findByPublicId()` call *does* go through this same
  override and would succeed via PS. The likely break point is downstream **writes** (`$payout->save()`/
  `saveOrFail()`), which target the local connection unconditionally and are not covered by any read-fallback
  override — this reconciles "reads are fine, the specific write-path job fails" without contradiction. Not
  independently confirmed by tracing the exact `save()` call in this pass — flagged for the payouts-repo lane.

**`PAYOUTS_FEATURES_FORWARD_DUAL_WRITE`/`REVERSE_DUAL_WRITE` — CONFIRMED-FOUND definition (was reported
"not found" in `UNRESOLVED_QUESTIONS.md` #6 / `EFFECTIVE_CONFIG_GAPS.md`)**: both exist verbatim in
`payouts/config/`. `prod.toml:665-666`: `reverse_dual_write = "env|PAYOUTS_FEATURES_REVERSE_DUAL_WRITE"`,
`forward_dual_write = "env|PAYOUTS_FEATURES_FORWARD_DUAL_WRITE"` (env-templated placeholders, resolved only
at deploy time — outside any cloned repo). `devstack.toml:681-682`/`e2e.toml:627-628`: `reverse_dual_write`
env-templated, `forward_dual_write` hardcoded `true`. `default.toml:715-716` baseline:
`reverse_dual_write=true`, `forward_dual_write=false`. `slit.toml:192`: `reverse_dual_write=false`. Type:
`internal/config/config.go:472-473`, `ReverseDualWrite bool`/`ForwardDualWrite bool` under a `Features`
struct (`mapstructure` tags matching the TOML keys), alongside sibling flags `skip_ca_dual_write`,
`db_route_label_enabled` on the same `"env|PAYOUTS_FEATURES_*"` convention.
`config_test.go:33-41` confirms these placeholders are "only resolvable via real process env vars at deploy
time." **Actual prod runtime value remains genuinely unresolved** — searched `kube-manifests` (no
`PAYOUTS_FEATURES_*`/dual-write env var set anywhere) and `dcs` (only its own unrelated
`dcs_kv_dual_write_enabled` KV-store mechanism) — the value is injected by something outside all cloned
repos (Vault/Confd/Helm-values not present in this clone-set). Best available signal is the `default.toml`
baseline (reverse=true, forward=false), not proof of the prod value.

**AuthZ shadow-mode confirmation**: `terraform-kong/prod/api/api.tf:6204-6218`, service-level `authz_url:
"https://authz-enforcer-internal.razorpay.com/v1/enforce", shadow_mode: true, whitelisted_routes:
local.whitelisted_routes_api`; `prod/api/variables.tf:1920-1927` lists `payout_create-prod-api`,
`payout_fetch_by_id-prod-api`, `fund_account_*-prod-api`, `contact_*-prod-api` all inside
`whitelisted_routes_api`. The cutover module's own comment confirms: *"payout_create-prod-api sits in
base/api's authz-enforcer whitelist (shadow mode, enforcement skipped)."* `watchResourceGroups`
excluding `x_platform` — zero hits for that string in `terraform-kong`/`edge`; this almost certainly lives
in the `authz` service's own repo, which was **out of this lane's assigned scope** (present in the
scratchpad but not read this pass) — UNKNOWN, needs a targeted follow-up read of `authz`.

**Rate-limiting — reconfirmed absent on payout routes**: `rate-limiter-contextual`/`rate-limiter-non-contextual`
plugins exist (`base/api/api.tf:7171-7190`) but are wired only to a dummy test route
(`feature_dummy-prod-api`), not to any payout/contact/fund_account route.

---

### 7. Dashboard repos — which is the BFF, and how login becomes an identity the monolith trusts

**Definitive finding, read directly from source (not delegated to a subagent)**: `x` is a **pure static
SPA** with no server-side BFF logic of its own — `x/nginx/nginx.conf.template` proxies every non-federated
path straight to an S3 bucket (`proxy_pass https://$bucket/x/${COMMIT_ID}/beta$uri`); `package.json`'s build
scripts (`start`/`build`/`production`) are pure webpack bundling with no server entrypoint. The reverse-proxy
layer sitting behind `apiHost = dashboard.razorpay.com` (`x/config.js:289`) is the **PG `dashboard` repo's own
PHP backend** — CORRECTS `07_frontend_bff.md` claim #34 ("no dashboard-proxy… mechanism found"): it exists,
and it's the primary one.

- `dashboard/app/Http/routes.php:186,422`: `Route::any('/merchant/api/{mode}/{path}',
  'GenericController@handleAny')` — this is exactly the path shape `x`'s own axios client constructs
  (`config.url = '/merchant/api/${mode}${config.url}'`, per the prior lane's finding).
- `GenericController::handleAny()` (`app/Http/Controllers/GenericController.php:141-` ) forwards a
  whitelisted header subset and delegates to `new App\Admin\ApiRequestAny([...])->send($path,$method)`.
- **`ApiRequestAny` is the real identity-minting proxy** (`app/Admin/ApiRequestAny.php`, read in full):
  for `clientType==='merchant'` (the `x` dashboard's route class) it resolves the Laravel session
  (`Auth::guard('user')->user()`, `$user->currentMerchant()`) and sets, per outbound request:
  `X-Dashboard-User-Role = $currentMerchant->role` (this is exactly the finance_l1/l2/l3 role the monolith's
  `UserAccess::getRoutePermission()` reads for banking-route permission checks), `X-Dashboard-User-Id`,
  `X-Dashboard-User-Email`, `X-Dashboard-User-2FA-Verified`, and forwards `X-Razorpay-Account` **only if the
  client itself sent it** (constant `RAZORPAY_ACCOUNT_HEADER`, line 65) — consistent with the prior finding
  that `x`'s own frontend code never sets this header (so it is effectively always absent for the merchant
  dashboard flow). **BasicAuth to the monolith**: `$baUser = $this->mode . '_' . $currentMerchantId`,
  `$pass = Config::get('api.auth_pass')` — assembled at the call site as `'rzp_' . $baUser` — i.e. username
  `rzp_<mode>_<merchantId>`, password a **single shared internal secret** (`api.auth_pass`), **not** the
  merchant's real API key/secret. **This is the "identification-only passport" mechanism**: the monolith's
  `$proxy` auth-group trusts whoever presents this Basic-Auth pair (mode+merchantId identifies the merchant;
  the password merely proves "I am the trusted dashboard backend," not "I am this specific merchant") plus
  the `X-Dashboard-User-*` headers for role/user context — there is **no signed, cryptographically-bound
  JWT** in this path at all, contrary to a "Passport JWT" framing.
- For `clientType==='admin'` (RZP-staff/admin-dashboard traffic via `/admin/api/{mode}/{path}`,
  `dashboard/app/Http/routes.php:346`): `Auth::guard('api')->user()` (a **separate**, RZP-internal-SSO
  guard) resolves the admin; headers `X-Dashboard-Admin-Username`, `X-Dashboard-Admin-Email`,
  `X-Admin-Token`, `X-Admin-Id`; BasicAuth username = **just `$this->mode`** (no merchant id — `rzp_live`/
  `rzp_test`), password `Config::get('api.admin_auth_pass')` — a second, distinct shared secret. This
  confirms `admin-dashboard`'s traffic reaches the monolith's `$admin` auth group through this same
  `ApiRequestAny` proxy, under RZP's own SSO session (`Auth::guard('api')`), not a merchant session.
- `clientType==='extension_merchant'` (a third path, `processExtensionAuthHeaders()`, lines 604-650) parses
  an actual **JWT** (`Lcobucci\JWT`) from a custom header, extracts `merchant_id`/`user_id` claims, and
  builds `baUser = mode.'_'.merchantId` with a *different* shared secret (`api.auth_pass`, same variable
  name but a distinct code path) — a genuinely separate, JWT-based identity mechanism for a narrower
  "extension" integration surface, not the mainstream `x` dashboard flow.
- **A separate, newer Passport-JWT mechanism does exist**, but in a different, newer part of `dashboard`:
  `apps/shell/src/server/utils/decryptPassportTokenFromHeader.ts` uses `@razorpay/passport-node`'s
  `passport.initHandler(JWKS_HOST)` to verify an inbound `X-Passport-JWT-V1` header via JWKS — this is the
  Node/TypeScript "shell" app (the newer "One Dashboard" federation host that mounts X's own remote bundle
  for `/banking/*`, per the prior lane's finding), a genuinely different, JWKS-verified, Edge-minted-JWT
  auth model, coexisting with the legacy PHP session→BasicAuth-impersonation model above for the classic
  `/merchant/api/*` surface. Two different auth models genuinely coexist in the same repo, gated by which
  app (`apps/shell` Node vs. legacy `app/` PHP) handles the request.
- **`xperience`** (Go BFF) is a **separate, second** BFF, used specifically for the newer bulk-payouts and
  petty-cash flows (`/xperience/*`, `/xperience-edge/*` relative paths on the same `dashboard.razorpay.com`
  host) — it authenticates via genuine Passport-JWT verification with two configured identities,
  `PassportConfig.Api` (`identifier="apiv1"`) and `PassportConfig.Edge` (`identifier="edgev1"`), both
  verified against `JwksHost = "https://edge-base.dev.razorpay.in/"` (`xperience/config/default.toml:82-89`)
  — i.e. xperience **verifies** a JWT that Edge (or a caller holding an Edge-issued key) minted; it does not
  mint its own from a session cookie the way the PHP `ApiRequestAny` path does.
  `passport_interceptor.go:23-79` (full read): reads `X-Passport-JWT-V1` from gRPC metadata, decodes via
  the passport SDK, tags context with merchant/user id from `ConsumerClaims`/`ResourceOwnerID`. So for the
  `/xperience*` surface, the identity really is a signed passport; for the classic `/merchant/api/*` surface
  it is the Basic-Auth-plus-headers scheme above. Both ultimately originate from the same dashboard login
  session, just translated differently depending on which BFF handles the specific route.

**Answer to "which repo is the RazorpayX merchant-dashboard BFF for payouts"**: there is no single answer —
it's **two BFFs**: (1) the legacy PG **`dashboard` PHP backend** (`GenericController`/`ApiRequestAny`),
fronting the classic `/merchant/api/{mode}/*` surface (create/approve/reject/bulk-approve/cancel, i.e. most
of §1-§4 above) with session→BasicAuth-impersonation+identity-headers, and (2) **`xperience`** (Go),
fronting the newer `/xperience[-edge]/*` bulk-payout-batch and petty-cash surface with genuine Passport-JWT
verification. `x` itself is UI-only (no BFF logic); `admin-dashboard` is the caller for the `$admin`
auth-group (RZP-ops) surface, proxied through the same PG `dashboard` backend under a separate RZP-SSO
guard; `frontend-x`'s only payout-relevant app (`x-customer-payout-links`) is payee-facing and unrelated to
the merchant BFF question.

---

## Corrections to existing reports

| Report | Claim | What source says | Evidence |
|---|---|---|---|
| `20_api_monolith.md` §1 | `payout_create`, `payout_create_with_otp`, `payouts_internal`, `payouts_internal_direct`, `payout_approve`, `payout_reject`, `payout_approve_bulk`/`reject_bulk` all include `admin` in their auth-group tag | None of these ten routes appear in `Route::$admin` (8727-10213) at all — a Razorpay-admin-authenticated caller cannot hit any of them directly | `Route.php` array-boundary grep, this pass |
| `20_api_monolith.md` §3 | Dashboard role permission enforced via `UserAccess::getRoutePermission()` reading `Route::$routePermission` | It reads `Route::$bankingRoutePermissions` (11966-12581); `$routePermission` (10221-11950) is consumed by `AdminAccess`/`Workflow`/API-docs instead | `UserAccess.php:518-527`; `AdminAccess.php:117`; `Workflow.php:229` |
| `20_api_monolith.md` §3 | `AuthzEnforcerClient` under `Services/Mock` implies no live production integration was found | A live, env-toggled integration path (`AccessAuthorizationService`→`AuthzEnforcer\Service::enforcerAPIEnforce()`) is fully wired for CAC-enabled/`AUTHORIZE_VIA_AUTHZ` merchants; mock-vs-real is an env flag (`AUTHZ_XPLATFORM_ENFORCER_MOCK`, defaults true) | `AccessAuthorizationService.php:56-102`; `AuthzEnforcer/Service.php:33-58`; `config/applications.php:2205-2212` |
| `20_api_monolith.md` §2 / `ARCHITECTURE_DELTA.md` W6/C45 | "No loop-guard (`X-PS-Proxied` or equivalent) exists… confirmed absent, not merely unfound" | `X-PS-Proxied` is a real, enforced loop-guard header in the `payouts` (PS) repo's shadow-gateway middleware; the monolith separately logs (not rejects) `X-Payouts-Service-Proxy` presence at ingress for loop-tracing | `payouts/internal/routing/shadowgateway/middleware.go:14-18,91-95`; `api/app/Http/Controllers/PayoutController.php:88-95` |
| `ARCHITECTURE_DELTA.md` Confirmed-1 / `23_workflows_batch.md` §B | "Batch's `payout_approval` batch type calls `POST payouts/bulk_approve` directly on Payouts Service, bypassing WFS entirely" | No `bulk_approve` route exists anywhere in the `payouts` (PS) repo. `payout_approval.json` calls the same `https://api.razorpay.com/v1/` base as `payout.json`, landing on the **API monolith's own** `payouts/bulk_approve` route (`Route.php:2319` → `PayoutController::approvePayoutBulk` → `Service::approveBulkPayout` → `Core::approvePayout`, the same core method ordinary approvals use) | `batch/src/main/resources/payout_approval.json:66`; `api/app/Models/Payout/Service.php:3539-3670`; repo-wide grep of `payouts` for `bulk_approve` (zero hits) |
| `UNRESOLVED_QUESTIONS.md` #7 / `EFFECTIVE_CONFIG_GAPS.md` | Read-path fallback for legacy (`is_payout_service=0`) rows "not traced" | Fully traced on both sides: monolith `Repository` PS-first-with-DB-fallback pattern (default: PS-only, no DB, unless a kill-switch or comparison-experiment triggers DB access); PS's own `AppDB→ApiDB→TiDB` chain has the `ApiDB` leg hardcoded off (`IsApiDbEnabledForPayouts` returns `false` unconditionally) | `api/app/Models/Payout/Repository.php:216-256,411-427,7474-7491,7555-7580`; `payouts/internal/helpers/apidb.go:23-25`; `payouts/internal/app/payouts/fetch_orchestrator/strategies/fetch_by_id_strategy.go:21-53` |
| `UNRESOLVED_QUESTIONS.md` #6 / `EFFECTIVE_CONFIG_GAPS.md` | `PAYOUTS_FEATURES_FORWARD_DUAL_WRITE`/`REVERSE_DUAL_WRITE` "not found as static config anywhere" | Both exist verbatim as `env|`-templated TOML keys in `payouts/config/{prod,default,devstack,e2e,slit}.toml`, typed `ReverseDualWrite bool`/`ForwardDualWrite bool` in `internal/config/config.go:472-473`. The *runtime value* (injected at deploy time) is still genuinely not in any cloned repo — that narrower claim survives | `payouts/config/prod.toml:665-666`; `default.toml:715-716`; `config.go:472-473` |
| `07_frontend_bff.md` claim #34 | "No dashboard-proxy forwarding to Payouts Service with identification-only passport mechanism was found in this repo" | It exists and is the primary mechanism: `dashboard/app/Admin/ApiRequestAny.php` mints `rzp_<mode>_<merchantId>` Basic-Auth + `X-Dashboard-User-*` identity headers from the Laravel session, forwarded to the monolith's `$proxy` auth group | `ApiRequestAny.php:141-503` (full method read, this pass) |
| `12_payouts_approval_queue_bulk.md` §A.7 (payouts-repo lane) | "Bulk-approval triggering must live outside this repo… presumably calling this repo's existing single-payout `/payouts_internal/{id}/approve` per payout" | Confirmed as one of *three* coexisting mechanisms, but the Batch-triggered one (`payout_approval.json`) does not call PS's per-payout approve endpoint either — it calls the monolith's `payouts/bulk_approve`, which itself calls the *local* `Core::approvePayout()`, which *for PS-owned rows* then calls PS's single-payout `payouts_internal/{id}/approve` — three hops, not one | See §5 above |
| `12_payouts_approval_queue_bulk.md` §A.7 (WFS lane) | `shouldProcessBulkApproveAsync()`/`ApprovedPayoutProcessor` described alongside the dashboard's bulk-approve UI as "the async bulk-approve path" | These are triggered from a *different* route family (`payout_approve_internal`, the WFS single-payout callback) than the dashboard's own `payouts/approve/bulk` (which is always synchronous, no queue at all) | `api/app/Models/Payout/Service.php:1320-1353,1507-1552,6802-6811` |
| `20_api_monolith.md` §2 | Blanket "1=classic,0=direct" framing for `X-Payouts-Service-Proxy` implies exactly two proxy mechanisms | Three coexist: legacy-V1 enrichment proxy (sets header=1), V2 balance-based pass-through proxy (**sets no such header at all**), and controller-level direct-diversion (sets header=0) | `api/app/Services/PayoutService/Create.php:133-186,208-219` |

---

## Twin comparison

| Component | Production mechanism | Twin mechanism (file) | Verdict | What differs | Controls |
|---|---|---|---|---|---|
| Monolith ingress (`POST /v1/payouts`) | `PayoutController::postFundAccountPayout` — auth, IP filter, idempotency, Splitz direct-gate, classic validations, proxy to PS | `ENV2_COMPOSE/substitutes/kong-lite/server.py` mints a passport JWT directly from a merchant-key Basic-Auth lookup and routes straight to `payouts-api:9400` | **MISSING** | No middleware stack at all: no IP filter, no `idempotency_keys` table/mutex/replay, no Splitz gate, no classic validations, no Shield, no fund-account/contact/pricing enrichment | auth, idempotency, balance, routing |
| Public-API idempotency | `idempotency_keys` table, `(idempotency_key, merchant_id)` unique, mutex+hash-compare, cached-response replay | None — kong-lite has no idempotency concept; PS's own `bulk_idempotency_keys`-equivalent for single-create is untested by the twin's public-API path | **MISSING** | Twin relies entirely on whatever idempotency PS itself enforces on `/v1/payouts` root; the monolith's client-facing dedup/replay semantics are untested | idempotency |
| Splitz direct-cutover gate | `DirectToPayoutsServiceGate::shouldDivert()` — live-mode/app-auth/body-mid/Splitz checks | None — twin always goes "direct," unconditionally, for every request | **MISSING** | Twin cannot exercise the classic (business-logic-first) path at all; only the always-diverted shape is tested | routing |
| Dashboard-originated create/approve/reject/bulk (`payouts_with_otp`, `payouts/{id}/approve`, `/reject`, `/approve\|reject/bulk`) | PG `dashboard` PHP proxy (`ApiRequestAny`) mints `rzp_<mode>_<mid>`+shared-secret Basic-Auth and `X-Dashboard-User-*` headers from a Laravel session; monolith enforces `finance_l1/l2/l3` role, mandatory OTP (Raven) | None | **MISSING** | No dashboard, no session, no role enforcement, no OTP at all | auth, tenant, approval |
| ICICI 2FA (`payouts/2fa/*`) | FTS-issued OTP, pending-then-approve state machine | None | **MISSING** | — | approval |
| Admin/repair (`admin/payouts/cancel`, `manual_action`, `manual/status[/batch]`, `retry`, `workflow_retry`) | `admin-dashboard` SPA → PG proxy (RZP-SSO, `Auth::guard('api')`) → `AdminAccess::policyChecker` (local role→permission DB) → monolith `Core`/PS-fallback per-route | None | **MISSING** | No admin auth model, no repair tooling, no local↔PS split logic exercised | admin, repair, payout/transfer state |
| Bulk create (`payouts/bulk` via Batch) | Batch `payout.json` → monolith-edge-domain Basic-Auth-proxy call → PS `CreateBulkPayouts[Concurrent]`, `bulk_idempotency_keys` table | None — twin's verifier calls PS directly, single-payout only; no batch/chunking/idempotency-key-derivation path exercised | **MISSING** | No Batch service, no CSV staging, no chunked bulk call, no per-row idempotency-key derivation (`"batch_"+BatchEntry.id`) | idempotency, routing |
| Bulk approve (`payout_approval.json`→monolith `payouts/bulk_approve`) | Monolith-internal, `X-Batch-Id`-gated, reuses `Core::approvePayout` | None | **MISSING** | — | approval |
| Workflow Service (approval state machine) | Cadence-backed WFS, Twirp `ActionAPI`, callbacks to `payouts_internal/{id}/approve\|reject` | None | **MISSING** | Twin has no multi-step/maker-checker workflow at all; the verifier's payouts are never workflow-gated | approval, routing |
| PS-callback surface (`payouts_service/*`, `internal/merchants/{id}`, `fund_accounts_internal`, `update_fts_fund_transfer`, `create_fta`) | Monolith routes, `payouts_service`-credential-allowlisted | `ENV2_COMPOSE/substitutes/monolith-stub/server.py` — implements exactly this subset (confirmed by direct read: `fetch_pricing_info`, `dual_write`, `source_update`, `mail_and_sms`, `create_fta`, `update_fts_fund_transfer`, `internal_merchants`, `fund_accounts_internal`, `on_hold_slas_internal`, `internal_balances_queued`) | **CONTRACT-FAITHFUL** (for this subset only) | Field names/behaviour sourced directly from `payouts/pkg/api/*.go` DTOs per the stub's own `CONTRACT.md`; two routes (`actor_info_internal`, `users_internal`) are explicitly marked not-confirmed-real by the stub's own docs | routing, balance (partial) |
| `is_payout_service`/read-fallback migration state | Monolith PS-first-with-local-fallback `Repository` pattern; PS's `AppDB→ApiDB(off)→TiDB` chain | Not modeled — twin has no local monolith `payouts` table at all, and PS's fetch-orchestrator fallback chain is untested | **MISSING** | — | routing, payout state |

---

## Recommendation: real vs substitute

**Monolith ingress layer (public create/OTP/2FA/approve/reject/bulk-approve/admin, i.e. `api/app/Http/
Controllers/PayoutController.php` + `Payout\{Service,Core,Validator}` + `Http/Middleware/*`)**: **SUBSTITUTE.**
The full monolith is a 23k-line-route-table PHP/Laravel application with deep coupling to its own local
databases (`balance`, `features`, `merchant_users`, admin permission tables), Splitz, DCS, Raven, FTS, and a
`payouts_service` database connection distinct from PS's own — running it for real inside an isolated arena
is disproportionate to the value for this investigation's twin. If a substitute is built, it must implement,
at minimum: (a) `POST /v1/payouts` with the exact middleware order (auth→IP-filter→idempotency) and the
`idempotency_keys` `(key,merchant_id)`-unique replay/mutex contract; (b) the Splitz direct-gate's three
hard-coded refusal conditions (non-live, app-auth, body-mid-mismatch) plus a configurable "divert" boolean in
place of the real experiment; (c) `payouts_with_otp`/`payouts/{id}/approve` with an OTP stub that always
succeeds/fails on a fixed sentinel value rather than calling Raven/FTS; (d) the three admin/repair routes'
distinct local-vs-PS branching (`findByPublicId`/`findMany` local-first with a PS-shaped fallback) so
approve/reject/manual-status semantics against PS-owned payouts are exercised; (e) the `payouts_service/*`
callback surface already built (`monolith-stub`) should be kept and extended, not replaced.
Full protocol/behavioural contract for a substitute: routes and bodies per §1-§4 above; status codes —
200/`payout` JSON on success, monolith-native error codes (`BAD_REQUEST_VALIDATION_FAILURE`,
`BAD_REQUEST_PAYOUT_NOT_ENOUGH_BALANCE_BANKING`, `BAD_REQUEST_SAME_IDEM_KEY_DIFFERENT_REQUEST`,
`SERVER_ERROR_ANOTHER_OPERATION_PROGRESS_SAME_IDEM_KEY`) on failure; no retry semantics of its own (fails
closed on idempotency-mandatory errors); side effects — one `X-Payout-Idempotency`-keyed row per accepted
create, workflow-state-map rows on approval callbacks.

**Dashboard BFF (`dashboard` PHP `ApiRequestAny`/`GenericController`, and `xperience` for bulk/petty-cash)**:
**SUBSTITUTE.** Both are themselves thin proxies once identity is established — a substitute needs only:
(a) a fake login endpoint that sets a session cookie and, given a role, returns
`X-Dashboard-User-Role/Id/Email/2FA-Verified` header values verbatim (no real Laravel session/DB needed);
(b) BasicAuth assembly `rzp_<mode>_<merchantId>` : `<shared secret>` toward whatever plays the monolith's
role; (c) for `xperience`'s surface, a minimal Passport-JWT mint/verify pair (RS256, `edgev1`/`apiv1`
identifiers) — the twin's existing `kong-lite` passport minting can be reused/extended for this rather than
building a second JWT mechanism. Full contract: routes/bodies per §7; response passthrough is otherwise
transparent (BFF adds only headers, doesn't transform bodies on this path per `GenericController::handleAny`).

**Batch service**: **SUBSTITUTE.** Real Batch is a 7-k8s-deployment Spring Boot app with Postgres+S3+SQS; not
worth running for this investigation. Substitute contract: `POST /payouts/bulk` (monolith-edge-domain-shaped)
accepting a JSON array of ≤5 rows with an `idempotency_key` per row (format `"batch_"+<opaque id>`, NOT
required to be globally unique — no mutex expected at this layer), `X-Batch-Id`/`X-Creator-Id`/
`X-Creator-Type`/`X-Entity-Id` headers, Basic-Auth `rzp_<mode>_<entityId>`:`<shared secret>`; response
`{items: [{idempotency_key, id, http_status_code, error.code, error.description}, …]}`; retry-worthy only on
overall non-2xx or a per-item `http_status_code==500`, fixed-backoff 5×5s at the caller (not the substitute's
concern, but the substitute should be resilient to exact-duplicate retries of the same 5-row body).

**OTP service**: **SUBSTITUTE.** Two independent OTP concepts: (a) Raven, for `create_payout`/`approve_payout`
actions — substitute contract: an endpoint accepting `{action, otp, token, payout_id?}` returning success/fail
on a fixed sentinel (e.g. `otp=="000000"`→success); (b) FTS-issued ICICI 2FA — substitute contract:
`requestOtpCreate({source_id, source_type=PAYOUT, source_account_id, mode, amount})` returning an opaque
token, verified later by the same sentinel scheme. Neither needs SMS delivery or real Raven/FTS state.

---

## Synthetic data

| Family/table | Field | Source evidence | Type+length | Constraints | Allowed values | FK/relationships | State rules | Distribution matters? | Generation rule | EXACT/REPRESENTATIVE/ASSUMED |
|---|---|---|---|---|---|---|---|---|---|---|
| `idempotency_keys` (monolith) | `idempotency_key` | `api/database/migrations/2020_03_13_162700_create_idempotency_keys_table.php` | varchar(255) | unique with `merchant_id` | client-chosen string | — | one row per accepted create | no | client-supplied or `uuid()` | EXACT (migration) |
| `idempotency_keys` | `merchant_id` | same migration | CHAR(14)-shaped | composite unique w/ key | — | → `merchants.id` | — | no | reuse merchant fixture id | EXACT |
| `idempotency_keys` | `source_id`/`source_type` | same migration | varchar | set once entity created | `source_type=payout` for this flow | → `payouts.id` | null until entity created | no | set post-create | EXACT |
| `idempotency_keys` | `request_hash` | same migration | varchar(64)-ish (SHA-256 hex) | recomputed on retry-compare | hex | — | mismatch ⇒ reject | no | `sha256(json_encode(sorted-body))` | EXACT (mechanism), REPRESENTATIVE (exact hash function name not fully confirmed) |
| `merchant_users`/role | role | `x/src/js/modules/constants.ts:378-425` (from prior lane, reconfirmed by `ApiRequestAny.php:448` `$currentMerchant->role`) | enum-like string | — | `finance_l1`,`finance_l2`,`finance_l3`,`owner`,`admin` (WFS config example) | user↔merchant | maker=L1, checker=L2/L3 by convention (not enforced by a literal maker/checker field) | yes — approval routing depends on role | seed 3 users, one per role, per merchant | REPRESENTATIVE (role string set confirmed; the underlying `merchant_users` table schema itself was not read in this pass — monolith repo scope didn't include it) |
| Dashboard identity headers | `X-Dashboard-User-Role/Id/Email/2FA-Verified` | `ApiRequestAny.php:448-457` | strings/bool-as-string | 2FA-verified is `'true'`/`'false'` literal strings | — | user id → arbitrary | — | no | mint directly, no backing DB needed for a substitute | EXACT |
| OTP (Raven) | action | `api/app/Models/User/Core.php:6344-6473` | string | — | `create_payout`,`approve_payout`,`approve_payout_bulk` | — | verified once per action attempt | no | fixed sentinel value | EXACT (action-name strings), ASSUMED (sentinel-otp convention for a substitute) |
| ICICI 2FA (FTS) | otp request fields | `Core.php:649-668` | `{source_id,source_type=PAYOUT,source_account_id,mode,amount}` | — | — | source_id → `payouts.id` | pending until approved via `payouts/approve/2fa` | no | opaque token per request | EXACT (field names), ASSUMED (token format) |
| Batch `payout.json` idempotency | `idempotency_key` | `BulkApiCallDataProcessorImpl.java:106,163` | string | `"batch_"+BatchEntry.id` | — | → `BatchEntry.id` (Hibernate PK) | stable across retries of the same row | no | `"batch_" + sequential int` | EXACT |
| Batch headers | `X-Batch-Id`,`X-Creator-Id`,`X-Creator-Type`,`X-Entity-Id` | `HeaderConstant.java:9-18` | strings | — | — | batch id → arbitrary opaque | — | no | mint per synthetic batch | EXACT |
| `bulk_idempotency_keys` (PS) | `(merchant_id, idempotency_key)` | `payouts/internal/app/bulkPayoutsProcessor/repo.go:28-35` | composite unique | — | — | → merchant, → payout | — | no | client-supplied per row | EXACT |
| workflow config (WFS) | roles/steps | `12_payouts_approval_queue_bulk.md` A.3 (payouts-repo lane, cross-referenced), `SYNTHETIC_FIXTURE_SPEC.md` §2 | JSON template | — | `finance_l1`/`admin`/`owner` step roles | — | multi-level AND/OR | yes (approval routing) | reuse the concrete example config already in the corpus | EXACT (already-confirmed example JSON) |
| Admin permission tables | admin→role→permission | `Admin/Admin/Entity.php:689-708` (Eloquent relation, read this pass) | relational | — | `Permission::*` constants | admin↔role↔permission | — | no | seed one admin with all payout-admin permissions for golden-path testing | REPRESENTATIVE (relation confirmed; full schema/migration not read this pass) |

---

## Cannot be derived from repositories

1. **`authz` repo itself** (present in the scratchpad but out of this lane's assigned scope) — needed to
   resolve `watchResourceGroups`/`x_platform` exclusion (item 6) and to confirm whether the "real" (non-mock)
   `AuthzEnforcerClient` toggle (`AUTHZ_XPLATFORM_ENFORCER_MOCK`) is actually flipped on in any prod
   deployment. Likely owner: AuthZ platform team. Minimal request: sanitized `authz` service config export
   (env-var values, not source) for the `x_platform` resource group and for the monolith's authz-enforcer
   feature-flag deployment values.
2. **`PAYOUTS_FEATURES_FORWARD_DUAL_WRITE`/`REVERSE_DUAL_WRITE` runtime values** — confirmed as
   `env|`-templated placeholders in `payouts/config/*.toml`, but the actual deployed value is injected by a
   Vault/Confd/Helm-values layer not present in any cloned repo. Likely owner: Payouts platform/SRE. Minimal
   request: the env-var value as deployed in prod (a single boolean pair, safe to share as sanitized config).
3. **`merchant_users` table schema** — the monolith itself does not check merchant-user↔role membership in
   `api`'s own code (confirmed absence, `20_api_monolith.md`); this pass confirms role is instead carried
   from the dashboard's *own* session (`$currentMerchant->role`) as an `X-Dashboard-User-Role` header, so the
   authoritative table backing "which user has which role for which merchant" is a `dashboard`-repo-owned
   table not read in this pass. Likely owner: Dashboard/growth-banking team. Minimal request: schema-only DDL
   for the user↔merchant↔role table(s).
4. **The exact `save()`/write path for `ApprovedPayoutProcessor` and other admin-repair jobs** — this pass
   confirms the *read* side is safely PS-first (no local DB touch by default), but did not trace the specific
   write call that the reported prod incident (`Table 'api.payouts' doesn't exist`) implies is failing.
   Likely owner: API-monolith/Payouts platform team. Minimal request: a stack trace or `ExternalServiceCallResponse`/
   exception-log sample from the actual incident, or a sanitized read of `Payout\Core::handlePayoutProcessed`/
   `handlePayoutFailed`'s save path.
5. **Real (non-mock) production toggle values** for `applications.authzXPlatformEnforcer.mock` and
   `applications.payouts_shadow_router.kill_switch`/experiment id — both confirmed to exist as live,
   env-toggled mechanisms in code, but their actual prod settings weren't retrievable from static config in
   any cloned repo. Likely owner: API-monolith platform team. Minimal request: sanitized deployment env-var
   values for these two flags.
6. **Callers of `payouts/2fa/create[_internal]`, `payouts/2fa/send_otp`, `payouts_internal_direct`** — no
   caller was found in any of the 13 repos read across this lane's parallel passes. Likely owner: unclear —
   possibly an ICICI-specific frontend or `scrooge`/`settlements-service`/`xpayroll`, none of which are in
   this scratchpad's clone set. Minimal request: a repo-name confirmation from the owning team, or an APM
   trace of inbound callers by route name.
7. **PS's own internal validation/idempotency/Shield-integration source** — this lane confirmed what the
   *monolith* loses when bypassed, but could not independently confirm from PS's own source whether PS
   re-implements equivalents (its own idempotency store, its own fraud/Shield call, its own
   `RequirePublicCreateCaller` parity with the monolith's private-auth allowlist). Likely owner: Payouts
   Service team / the companion "payouts core" investigation lane. Minimal request: cross-reference against
   that lane's existing findings, or a targeted read of `payouts/internal/app/payouts/processor/base.go`'s
   create-time validation list.

---

## Fidelity tier verdicts

- **Monolith ingress (create/OTP/2FA/approve/reject/admin/bulk-approve)** — UNKNOWN-BLOCKED as a *live*
  component in the twin (it doesn't exist at all today); as a documentation target it is now
  CONTRACT-FAITHFUL-DERIVABLE — every route, body, header, and state transition needed to build a substitute
  is confirmed from source this pass. Most important reason: the twin bypasses this layer entirely by design
  (kong-lite mints its own passport and calls PS directly), so today it is simply MISSING, not degraded.
- **Dashboard BFF (`dashboard`/`xperience`)** — UNKNOWN-BLOCKED (absent from the twin). Most important
  reason: same as above — no dashboard exists in Env 2 at all; the identity-minting contract (Basic-Auth
  impersonation + header identity, or Passport-JWT for xperience) is now fully confirmed and buildable as a
  REPRESENTATIVE SUBSTITUTE.
- **Batch service** — UNKNOWN-BLOCKED (absent). Contract fully confirmed (this pass + prior lane); a
  CONTRACT-FAITHFUL SUBSTITUTE is buildable without running real Batch.
- **OTP (Raven/FTS)** — UNKNOWN-BLOCKED (absent). Both mechanisms' exact trigger points and field shapes are
  confirmed; a sentinel-based REPRESENTATIVE SUBSTITUTE is sufficient since neither is business-logic-load-bearing
  beyond gating.
- **Migration/proxy-cutover state (Kong, dual-write flags, `is_payout_service` fallback)** — CONTRACT-FAITHFUL
  as documentation (both the Kong-side and monolith/PS-side mechanisms are now traced with source citations);
  the twin does not need to model this at all since it bypasses the monolith and Kong entirely by construction
  — most important reason: this is an ingress-layer concern and the twin's ingress bypass makes it moot for
  the twin's own fidelity, but material for anyone deciding whether to re-introduce a monolith substitute.
- **PS-callback surface (`monolith-stub`)** — REAL-shaped SUBSTITUTE, CONFIRMED CONTRACT-FAITHFUL for the
  routes it implements (`fetch_pricing_info`, `dual_write`, `source_update`, `mail_and_sms`, `create_fta`,
  `update_fts_fund_transfer`, `internal_merchants`, `fund_accounts_internal`, `on_hold_slas_internal`,
  `internal_balances_queued`); two routes (`actor_info_internal`, `users_internal`) are explicitly
  UNKNOWN/best-effort per the stub's own `CONTRACT.md`. Most important reason: this is the one component in
  this lane's scope that the twin actually implements, and it does so with direct DTO-level grounding.
