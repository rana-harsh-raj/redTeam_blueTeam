# RazorpayX Payouts — Merchant-Facing Entrypoints, BFF, and Session/Auth Model

Lane: frontend/BFF for payouts (single payout, bulk upload, approvals/maker-checker, payout links, scheduled, cancel), the API paths they call, and session/auth/merchant-context.

Repos investigated (all read-only, shallow clones under
`/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture/`):

- `x` @ `40b093f97ec1b8162e7f46ecee223af04982d5ba` — RazorpayX merchant dashboard SPA
- `frontend-x` @ `efe5df3d26ec5bfaef69b8cb66fb558674cd960f` — suite of RazorpayX customer-facing/vendor micro-apps
- `xperience` @ `88729560e52b35aa9d5768d67ff807f847537ee7` — Core Banking Experience Go microservice (BFF)
- `dashboard` @ `60ed3c781ded186ad38c41ea8f9fc90b7f3cd7c3` — Razorpay PG merchant dashboard (payout-relevant parts only)

---

## Per-repo summary

### `x` (RazorpayX dashboard) — the primary merchant-facing entrypoint

- SPA, package name `business-banking`. React 17, react-router 6, Redux + `@reduxjs/toolkit` (RTK Query for newer API calls), redux-saga, axios ^0.25, `@razorpay/blade` design system, styled-components. No GraphQL/Apollo anywhere.
- Build: Webpack 5 (+ Rollup for a `pgclient` sub-bundle), env-selected via `X_ENV` (dev/prod/beta/charlie/delta/echo/zeta/func2-7/devstack/automation) baked into `config.js`.
- Deploy: **no app Dockerfile at repo root** — only `Dockerfile.e2e` (Playwright runner image) and `nginx/Dockerfile` (static asset server). Deploy model is bundle-to-S3 by commit-id; `nginx/nginx.conf.template` proxies `/x/${COMMIT_ID}/...` to S3. CI via GitHub Actions (`beta-deploy.yml`, `canary-deploy.yml`, `deploy.yml`, `hotfix-deploy.yml`, e2e workflows, `chromatic.yml` for visual regression).
- CODEOWNERS is minimal: only `@razorpay/mandatoryxreviewers` globally, plus `@razorpay/tech-banking` on the RBAC permission file (`src/js/views/xtra/Can/permission.js`) and SideNav. No team explicitly owns the payouts UI directories.
- `devstack.json` is an empty `{}` — devstack config injected at CI/deploy time.
- All payout-mutating HTTP calls in this repo target a single `apiHost` (`dashboard.razorpay.com` in prod) via axios with `withCredentials: true` (cookie session) + `X-CSRF-TOKEN`; requests are routed server-side (not client-side) to backend services. Relative path prefixes `xperience` / `xperience-edge` are used for the newer bulk-payouts flows; there is **no distinct xperience host constant in the frontend config** — that indirection happens server-side, outside this repo.

### `xperience` — confirmed BFF for the `x` dashboard, and a genuine payout-mutation path

- Go microservice, gRPC + grpc-gateway (REST surface auto-generated from `.proto` `google.api.http` annotations, protos pulled from BSR at build time — not checked into this shallow clone). Uses Twirp deps too but grpc-gateway is the REST bridge.
- Passport-JWT auth with **two configured identities**: `apiv1` (internal API monolith) and **`edgev1`** — the edge identity is the one used by the X dashboard's BFF traffic, strongly corroborating that xperience's "Edge" API family (`*EdgeAPI` services) is the dashboard-facing surface, while the plain (`*API`, non-Edge) family serves internal-app-to-app calls (api, workflows, vendor_payments, batch, webhooks).
- **It is not merely a core-banking/read service** — the `bulkpayouts.v1.BulkPayoutEdgeAPI` (Create/Process/BulkApprove/BulkReject/FetchPending/FetchAll/FetchMy/FetchMyApprovals) owns the entire bulk-payout lifecycle: upload → validate → workflow approval → `batch.GetApiCaller().ProcessBatch()` → disbursal. It also directly creates single "petty cash" payouts against the API monolith (`apiservice.ApiCaller.CreatePettyCashPayout` → `composite_payout_internal` on the API monolith, with `X-Razorpay-Account: merchantId` set outbound) and reads/updates payout state (`FetchPayoutByID`, `UpdatePayoutAttachments`, and a direct Payouts-Service client `PSClient` at `payouts.dev.razorpay.in` for `GetPayoutByID`).
- Downstream backends: API monolith (`ApiClient`), Payouts Service (`PSClient`), Batch service (`BatchClient`), Workflows engine (`WorkflowsClient`), Banking Accounts (`BankingAccountClient`), X Balances (`XBalancesClient`), X Account Statements (`XASClient`), FTS (`FTSLiveClient`), Razorpay Payment API (`RazorpayPaymentClient`, i.e. api.razorpay.com), UFH file service, Raven/Stork notification services, Splitz/DCS config. No client to a service literally named "ledger" or "authz" — AuthZ is enforced in-process via Casbin (`goutils/authz/enforcer`, org `razorpayx`, origin `x_platform`).
- Every outbound client config carries Hystrix circuit-breaker + Heimdall retry settings (`MaxConcurrentRequests=100`, `RequestVolumeThreshold=20`, `CircuitBreakerSleepWindow=5000ms`, `ErrorPercentThreshold=50`; retries generally `false`/domain-specific — e.g. `RetriesConfig.PettyCashCreation=3`, `PayoutCreation=3`).
- Build/deploy: multi-stage Docker (`build/docker/prod/Dockerfile.api`) pulls proto via `make proto-deps proto-refresh`; `devspace.yaml` targets namespace `xperience`; no Helm chart present in this checkout (likely lives in a separate infra repo). CODEOWNERS: `* @razorpay/xperience-devs` (single team owns whole repo).

### `frontend-x` — customer-facing payout-link app is the payout-relevant piece

- Monorepo (yarn workspaces + lerna) with five apps: `x-customer-payout-links`, `x-accounts-receivable`, `x-invoice-approval`, `x-vendor-portal`, `partner-lms`. **No literal `frontend-x/` subdir and no mobile/React Native app exist** in this checkout (contrary to the task brief's assumption) — all apps are React web apps, SSR via Node/webpack, except `x-vendor-portal` which is a Webpack Module Federation **remote** consumed by the main `x` app (imports `x/VendorPayouts`, `x/ui` from a federated host, and itself makes no direct payout API calls).
- `x-customer-payout-links` is the **payee-facing page** opened from a payout link URL (`/v1/payout-links/poutlk_<14 chars>/view/`). Auth for the payee is **OTP-based, keyless** (no Razorpay account/login) via `generate-customer-otp` / `verify-customer-otp`, then `fund-accounts` selection and `initiate`. Server-side rendering fetches link view-data using HTTP **Basic Auth** (`rzp_live` + an env secret password) against `UNIVERSE_PUBLIC_API_HOST` (`https://api.razorpay.com/v1/` in prod).
- CODEOWNERS at repo root has stale/typo'd path entries (`/x-accouts-receivables/`, `/partner-ms/`) that don't match the real directory names, meaning those two apps currently have no directory-specific reviewers enforced beyond the global fallback team.
- Notable process-risk (not a live leaked credential): `x-customer-payout-links/README.md` instructs developers to hardcode a fallback password literal into `renderMiddleware.js` for local dev, linking to an internal "secure paste" for the real value.

### `dashboard` (PG merchant dashboard) — cross-sell/federation only, no native payout initiation

- Payouts cannot be created from the PG dashboard's own code. The one PHP "payout" call in this repo (`Merchant/Service.php::getPayoutCount`) is a read-only GET used to populate an onboarding checklist flag; the equivalent JS middleware is explicitly commented **"Not used by PG, kept for reference"**.
- Real payout-creation routes under `/banking/*` (e.g. `/banking/payouts/create`) are served by mounting **X's own federated remote bundle** in-page (`loadRemote('@federated/cross-repo/x/connectedX')`, sourced from `x.razorpay.com`) inside the PG dashboard's "shell" app — i.e. this repo frames/hosts X's app rather than implementing payout creation itself. Legacy dashboard code has simpler cross-sell (`VasPayoutsMiniNav`, `RxCaInterest`, `XBankingWidget` — `window.open('https://x.razorpay.com')`).
- **No "dashboard-proxy" forwarding to a Payouts Service with an identification-only passport was found in this repo.** Only two unrelated `dashboard_proxy`-named entries exist, both for a customer-care service (`app/Http/RouteTeamMap.php:99-100`). The Slack-referenced "dashboard-proxy" mechanism most likely lives in a separate edge/gateway repo not present in this checkout — flagged as an unresolved question below.
- A general-purpose API proxy does exist (`GenericController::handleAny` → `Route::any('/merchant/api/{mode}/{path}', ...)`, `app/Admin/ApiRequestAny.php`), forwarding arbitrary methods/paths (including an `X-Payout-Idempotency` header) to the Razorpay API — architecturally capable of carrying a payout-create call, but no PG frontend code was found that actually issues one.
- Session cookie `rzp_usr_session` is host-only (`domain: null` in `config/session.php`), not shared cross-domain by default. Cross-domain cookies that do exist are analytics/AB-test only (`.razorpay.com` domain on `SESSION_UID_COOKIE_KEY`, Segment `ajs_anonymous_id`). A separate Passport JWT model (`X-Passport-JWT-V1` header, `@razorpay/passport-node`) is present and more likely to be the real cross-product identity mechanism, but its distribution (edge/gateway-injected) is outside this repo's code.

---

## Findings

| # | Claim | Repo | File path + symbol | Evidence excerpt | Confidence | Capability |
|---|---|---|---|---|---|---|
| 1 | Single-payout create is `POST /payouts_with_otp`, with an idempotency header behind a feature flag | x | `src/js/api/outflow.js:83-90` | `if (isIdempotencyForCreatePayoutsExpEnabled) config.headers['X-Payout-Idempotency'] = ...; axios.post('/payouts_with_otp', ...)` | high | persist |
| 2 | Create-payout route is permission-gated at the router level | x | `src/js/views/Routes/Routes.js:1324-1330` | `<Route path="${paths.createPayout.root}*" element={<RestrictedRoute requiredPermissions={CREATE_PAYOUT}>...` | high | authorize / route |
| 3 | Approve / reject single payout | x | `src/js/api/outflow.js:155-173` | `POST /payouts/{id}/approve`, `POST /payouts/{id}/reject` | high | authorize / reject |
| 4 | Bulk approve/reject (individual payout entities selected in bulk) | x | `src/js/api/outflow.js:174-194` | `POST /payouts/approve/bulk`, `POST /payouts/reject/bulk` | high | authorize / reject |
| 5 | Owner "reject all pending" bulk action spans payouts, payout-links, and bulk-payout batches | x | `src/js/api/workflow.ts:27-42` | `POST /payouts/reject/bulk/owner`, `POST /payout-links/reject/bulk/owner`, `POST /xperience/bulk-payouts/reject/owner` | high | reject |
| 6 | Cancel payout | x | `src/js/api/outflow.js:117-119`; `src/js/views/Payouts/PayoutsModalActions/CancelPayout/CancelPayout.js` | `POST /payouts/{payoutId}/cancel` | high | reverse |
| 7 | Bulk payout upload/validate has 4 coexisting API generations (migration in progress: legacy batch → v1 → v2/Xperience) | x | `src/js/api/batch.js:14-22`; `src/js/api/bulkPayoutsApiSlice.ts:60-103` | legacy `POST /batches/validate`; v1 `POST /payouts/batch/validate`; v2 `POST /xperience/bulk-payouts/validate` or `/xperience-edge/bulk-payouts` if `isXpsEdgeBulkPayoutsMigrationEnabled` | high | transform / persist |
| 8 | Bulk-payout batch processing (v2) is routed to xperience via a literal string host-swap, not distinct frontend config | x | `src/js/api/outflow.js:293-307`; `src/js/modules/util/updateXperienceHostname.ts` | `updateXperienceHostname` replaces `"xperience"` → `"xperience-edge"` in the relative path when flag enabled | high | route |
| 9 | Bulk-payout-batch approve/reject (as opposed to individual-payout bulk actions) goes to xperience paths | x | `src/js/api/bulkPayoutsApiSlice.ts:221-255` | `POST /xperience/bulk-payouts/approve`, `POST /xperience/bulk-payouts/reject` | high | authorize / reject |
| 10 | ICICI bank-side 2FA is a separate approval path from Razorpay OTP | x | `src/js/api/outflow.js:242-283`; `src/js/views/Payouts/PayoutsModalActions/WorkflowActions/ApprovePayout/ApprovePayoutWithBank2FA/` | `POST /payouts/2fa/create`, `POST /payouts/2fa/send_otp`, `POST /payouts/approve/2fa` | high | authorize |
| 11 | Merchant/role context comes from Redux store (`app.user.role`, `app.user.permissions`, `app.merchant`), not React context or a request header | x | `src/js/views/Routes/RestrictedRoute.js:91-96` | `mapStateToProps = ({ app, appConfig }) => ({ userRole: app.user.role, merchant: app.merchant, permissions: app.user.permissions, ...})` | high | observe |
| 12 | RazorpayX role model is Finance L1/L2/L3 (not literal "maker/checker" strings), functionally maker(L1)/checker(L2/L3) | x | `src/js/modules/constants.ts:378-425` | `['Finance L1','finance_l1','Create and issue payouts...']`, `['Finance L3','finance_l3','Verify and authorise payments at level 3']` | high | observe |
| 13 | No `X-Razorpay-Account` / account-switch header exists anywhere in the `x` SPA — account context is session/cookie + URL-path `mode` (`live`/`test`), not a per-request account header | x | repo-wide grep (zero hits); `src/js/api/api.js:173` | `config.url = '/merchant/api/${mode}${config.url}'` | medium (negative grep result) | observe |
| 14 | Auth is cookie session (`withCredentials`) + CSRF header + region header; no bearer token in normal flow (bearer only for OAuth-style integrations) | x | `src/js/api/api.js:66-74,183-191`; `src/js/modules/bootConstants.js:1` | `withCredentials: true`; `csrfHeaderName = 'X-CSRF-TOKEN'`; `headers['x-razorpay-user-merchant-region']='IN'`; `headers['X-Origin-Product']` when One Dashboard | high | authorize |
| 15 | Permission constants for the payouts domain are centralized and enforced via a `<Can I={...}>` wrapper, not inline role checks | x | `src/js/views/xtra/Can/permission.js:244-268`; `src/js/views/xtra/Can/index.js` | `CREATE_PAYOUT`, `CREATE_PAYOUT_BULK`, `APPROVE_PAYOUT`, `APPROVE_PAYOUT_BULK`, `REJECT_PAYOUT`, `VIEW_PAYOUT`, `VIEW_ALL_PAYOUTS`, `CANCEL_PAYOUT`, `APPROVE_PAYOUT_LINKS`, `REJECT_PAYOUT_LINKS` | high | authorize |
| 16 | Splitz (Razorpay's own experimentation service) drives feature exposure, not LaunchDarkly (absent from repo) | x | `src/js/modules/splitz.js:1-24`; `config.js` (`rxPayoutsRTK`, `rxRTKSplitzId`) | `abService.init({id, apiBaseUrl: __CONFIG__.coreApiDomain})`; `rxPayoutsRTK: 'KvMe67nh6Mngcw'` | high | route |
| 17 | No `graphify-out/graph.json` present anywhere in the `x` repo | x | repo-wide `find` (zero hits) | — | high | observe |
| 18 | E2E is Playwright (not Cypress); dedicated payout specs exist and assert real API paths | x | `playwright.config.ts`; `e2e/payouts.spec.ts:32-37`; `e2e/bulk-payouts/{v1Flow,v2Flow}.spec.ts` | test waits for `/payouts_with_otp` success response with `data.id` | high | observe |
| 19 | xperience uses two Passport identities: `apiv1` (internal API monolith) and `edgev1` (dashboard-facing edge) | xperience | `config/default.toml:83-87` | `[PassportConfig.Api] identifier="apiv1"` / `[PassportConfig.Edge] identifier="edgev1"`, `JwksHost="https://edge-base.dev.razorpay.in/"` | high | authorize |
| 20 | xperience's `BulkPayoutEdgeAPI.Process` actually triggers payout disbursal via the Batch service | xperience | `internal/app/service/bulkpayouts/service.go:2351` (`Process`), `:2887` (`initiateProcessing` → `batch.GetApiCaller().ProcessBatch`) | validates request, `IsProcessingAllowed()`, calls Batch service to process | high | persist / route |
| 21 | xperience directly creates single "petty cash" payouts against the API monolith | xperience | `internal/app/client/apiservice/create_petty_cash_payout.go:18` | calls `composite_payout_internal` on `ApiClient.BaseUrl`, header `X-Razorpay-Account: merchantId` | high | persist |
| 22 | Route-level auth classes: `DirectAuth`/`ProxyAuth`/`MerchantAuth`/`AdminAuth`/`InternalAuth`, looked up per gRPC method | xperience | `internal/routes/routes.go` (`RpcRouteMap`); `internal/interceptors/authz_interceptor.go:46-59` | `route, ok := routes.RpcRouteMap[rpcMethod]` dispatches auth class | high | authorize |
| 23 | Merchant/user id extracted from Passport JWT and injected into context as structured logging fields (`x-merchant-id`, `x-user-id`), re-serialized outbound as `X-Razorpay-Account` | xperience | `internal/interceptors/passport_interceptor.go:53-79`; `internal/common/helpers/passport.go:98,189` | `helpers.SetTag(ctx, constants.MerchantIdHeaderKey, resourceOwnerId)`; outbound `constants.HeaderXRzpAccount: merchantId` | high | route / observe |
| 24 | Every outbound xperience client has Hystrix + Heimdall resiliency config | xperience | `config/default.toml:96-116` (`ApiClient.HystrixResiliencyConfig`, `.HeimdallConfig`) | `MaxConcurrentRequests=100`, `CircuitBreakerSleepWindow=5000`, `ShouldRetry=false`, `Retries=3` | high | observe |
| 25 | AuthZ is Casbin-based, in-process, fixed org `razorpayx` / origin `x_platform` (no separate "authz" service client found) | xperience | `internal/interceptors/authz_interceptor.go:12,25-27` | `AuthzOrganisation="razorpayx"`; `OriginService="x_platform"` | high | authorize |
| 26 | Payout-link customer page auth is OTP-based/keyless — no merchant login involved for the payee | frontend-x | `x-customer-payout-links/src/shared/api/plApi.js:2-13` | `generateOtp`→`POST /payout-links/{id}/generate-customer-otp`; `verifyOtp`→`POST /payout-links/{id}/verify-customer-otp`; `addFundAccounts`→`POST /payout-links/{id}/initiate` | high | authorize / persist |
| 27 | Payout-link URL shape is fixed by a server-side regex, non-matching paths 404 | frontend-x | `x-customer-payout-links/src/shared/services/renderMiddleware.js:158-159` | `validRequestRegex = /^\/v1\/payout-links\/poutlk_([a-zA-Z0-9]{14})\/view\/$/` | high | route |
| 28 | SSR view-data fetch uses HTTP Basic Auth with a fixed username and an env-sourced secret password | frontend-x | `x-customer-payout-links/src/shared/services/renderMiddleware.js:32-42` | `Authorization: Basic base64(rzp_live:${PAYOUT_LINKS_APP_AUTH_PASS})` | high | authorize |
| 29 | `x-vendor-portal` has no direct payout API calls of its own; it consumes payout UI/data from the main `x` app via Webpack Module Federation | frontend-x | `x-vendor-portal/webpack.config.js:183-190`; `src/VendorPortalV1/data.js:17` | `remotes: { x: 'x@[xUrl]/x.remoteEntry.js' }`; `import ... from 'x/VendorPayouts'` | high | route / observe |
| 30 | frontend-x CODEOWNERS has two typo'd/stale paths matching no real directory | frontend-x | repo root `CODEOWNERS` | `/x-accouts-receivables/` and `/partner-ms/` vs. actual dirs `x-accounts-receivable`, `partner-lms` | high | observe |
| 31 | PG dashboard cannot natively create payouts; its own "payout" code path is a read-only count check | dashboard | `app/Merchant/Service.php:1441-1466` (`getPayoutCount`) | `GET /payouts?count=1&product=banking`, used only to set an onboarding-checklist flag | high | observe |
| 32 | PG dashboard's JS middleware for the same endpoint is explicitly dead/legacy | dashboard | `apps/shell/src/server/configs/routes.server.ts:25-26` | comment: "Not used by PG, kept for reference" | high | observe |
| 33 | Real payout-creation UI under `/banking/*` on the PG dashboard is X's own federated remote, not PG-owned code | dashboard | `apps/shell/src/client/components/ProductRouter/ProductRouter.tsx:34,150` | `const XDashboard = lazy(() => loadRemote('@federated/cross-repo/x/connectedX')); <Route path="banking/*" element={<WrappedXDashboard />} />` | high | route |
| 34 | No "dashboard-proxy → Payouts Service with identification-only passport" mechanism found in this repo; only unrelated care-service proxy entries share the `dashboard_proxy` name | dashboard | `app/Http/RouteTeamMap.php:99-100` | `'care_service_dashboard_proxy' => [self::TEAM_PAYMENTS_CARE]` | high (absence in this repo) | observe |
| 35 | A generic, largely unrestricted API proxy exists in the PG dashboard backend, capable of carrying a payout-create call if a frontend issued one (none found) | dashboard | `app/Http/Controllers/GenericController.php`; `app/Admin/ApiRequestAny.php`; `app/Http/routes.php:186` | `Route::any('/merchant/api/{mode}/{path}', 'GenericController@handleAny')`, forwards `X-Payout-Idempotency`, blocks only `admin/payout` GET for one merchant (SBB-622) | high | route |
| 36 | PG dashboard's primary session cookie is host-only, not shared cross-domain; cross-domain cookies present are analytics-only | dashboard | `config/session.php:181,207` | `'cookie' => 'rzp_usr_session'`, `'domain' => null` | high | observe |
| 37 | A Passport-JWT identity model (`X-Passport-JWT-V1` header) exists in the PG dashboard, likely the real cross-product identity carrier, but its distribution mechanism is outside this repo | dashboard | `apps/shell/src/server/utils/decryptPassportTokenFromHeader.ts`; `app/Admin/GraphRequestAny.php:27` | header-based JWT read, `@razorpay/passport-node` package | medium | authorize |

---

## Frontend route → API call table (`x` dashboard)

| UI route | Action | Method + API path | Host / config key | Auth headers | Role gating (permission const) |
|---|---|---|---|---|---|
| `/createPayout/*` | Create single payout | `POST /payouts_with_otp` | `apiHost` (`dashboard.razorpay.com`), rewritten server-side to `/merchant/api/{mode}/payouts_with_otp` | Cookie session + `X-CSRF-TOKEN` + optional `X-Payout-Idempotency` + `x-razorpay-user-merchant-region: IN` | `CREATE_PAYOUT` (route-level) |
| `/payouts` | List / view payouts, incl. "my approvals" tab | `GET /payouts`, `GET /payouts/_meta/summary` | `apiHost` | same | `VIEW_PAYOUT` (route); `myApprovals` tab needs `APPROVE_PAYOUT` |
| `/payouts` (detail panel) | Approve payout | `POST /payouts/{id}/approve` | `apiHost` | same + OTP/token in body | `<Can I={APPROVE_PAYOUT}>` (component) |
| `/payouts` (detail panel) | Reject payout | `POST /payouts/{id}/reject` | `apiHost` | same | `<Can I={REJECT_PAYOUT}>` |
| `/payouts` (list, multi-select) | Bulk approve payouts | `POST /payouts/approve/bulk` | `apiHost` | same + OTP/token | `<Can I={APPROVE_PAYOUT_BULK}>` |
| `/payouts` (list, multi-select) | Bulk reject payouts | `POST /payouts/reject/bulk` | `apiHost` | same | `<Can I={REJECT_PAYOUT}>` |
| `/payouts` (detail panel) | Cancel payout | `POST /payouts/{id}/cancel` | `apiHost` | same | `CANCEL_PAYOUT` |
| `/bulk-payout/create/*` | Upload bulk payout file (validate) | `POST /xperience/bulk-payouts/validate` (or `/xperience-edge/bulk-payouts`, or legacy `/batches/validate`, `/payouts/batch/validate`) | `apiHost` + Xperience path prefix (server-routed) | same | `CREATE_PAYOUT_BULK` + feature flag `payoutFeatureFlag` |
| `/bulk-payout/create/*` | Process bulk payout batch | `POST /xperience/bulk-payouts/{batch_id}/process` (or `POST /payouts/batch/{batch_id}/process` v1) | `apiHost` | same | `CREATE_PAYOUT_BULK` |
| `/payouts/bulk` | List bulk payout batches | `GET /xperience/bulk-payouts/fetch/{me\|my-approvals\|all}` | `apiHost` | same | `VIEW_PAYOUT` / `APPROVE_PAYOUT` for approvals tab |
| `/payouts/bulk` | Approve/reject bulk-payout batch | `POST /xperience/bulk-payouts/approve` / `/reject` | `apiHost` | same + OTP | `APPROVE_PAYOUT_BULK` / `REJECT_PAYOUT_BULK` |
| `/payout-links` | List / view payout links | `GET /payout-links` | `apiHost` | same | `VIEW_PAYOUT_LINKS` |
| `/payout-links/create` | Create payout link | `POST /payout-links` | `apiHost` | same | `CREATE_PAYOUT_LINKS` |
| `/payout-links/bulk` | Bulk create payout links | `POST /payout-links/batch-create` | `apiHost` | same | `CREATE_PAYOUT_LINKS` |
| `/payout-links` (detail) | Approve/reject payout link | `POST /payout-links/{id}/approve` (reject analogous) | `apiHost` | same + OTP | `APPROVE_PAYOUT_LINKS` / `REJECT_PAYOUT_LINKS` |
| `/payout-links` (detail) | Cancel payout link | `POST /payout-links/{id}/cancel` | `apiHost` | same | `CANCEL_PAYOUT_LINKS` |
| CreatePayout wizard (embedded) | Schedule / queue-if-low-balance | payload fields `scheduled_at`, `queue_if_low_balance` on `POST /payouts_with_otp`; timeslots via `GET /payouts/schedule/timeslots` | `apiHost` | same | `CREATE_PAYOUT` |
| `views/Workflow/*` | Configure maker-checker approval rules (self-serve) | `POST/PUT/DELETE /workflow/config` | `apiHost` | same | self-serve workflow config permission |

### Frontend-x public/customer entrypoint

| UI route | Action | Method + API path | Host / config key | Auth |
|---|---|---|---|---|
| `/v1/payout-links/poutlk_<id>/view/` (SSR, `x-customer-payout-links`) | Render payout link landing page | `GET {UNIVERSE_PUBLIC_API_HOST}payout-links/{id}/view-data` | `UNIVERSE_PUBLIC_API_HOST` (prod: `https://api.razorpay.com/v1/`) | HTTP Basic Auth (`rzp_live` + `PAYOUT_LINKS_APP_AUTH_PASS`) — server-side only |
| Same app, browser-side wizard | Generate/verify payee OTP | `POST /payout-links/{id}/generate-customer-otp`, `POST /payout-links/{id}/verify-customer-otp` | same base | none (keyless) — OTP is the auth |
| Same app | Fetch/select fund account, submit payout | `POST /payout-links/{id}/fund-accounts`, `POST /payout-links/{id}/initiate` | same base | OTP-derived token |
| Same app | Poll status | `GET /payout-links/{id}/status` | same base | none (link id only) |
| Same app | IFSC lookup | `GET https://ifsc.razorpay.com/{code}` | hardcoded external host | none |

---

## Backend host / base-URL table

| Config key | Prod value | Repo / file |
|---|---|---|
| `apiHost` | `https://dashboard.razorpay.com` | x — `config.js:289` |
| `adminApiHost` | `https://admin-dashboard.razorpay.com` | x — `config.js:294` |
| `axisApiHost` | `https://axis.razorpay.com` | x — `config.js:290` |
| `coreApiDomain` | `https://api.razorpay.com` | x — `config.js:306` |
| `xpayrollHost` | `https://payroll.razorpay.com` | x — `config.js:302` |
| `uslLoginRedirectUrl` | `https://accounts.razorpay.com/auth?product=banking` | x — `config.js:303` |
| `ljMetricsHost` | `https://lumberjack-metrics.razorpay.com/v1/frontend-metrics` | x — `config.js:298` |
| `UNIVERSE_PUBLIC_API_HOST` | `https://api.razorpay.com/v1/` | frontend-x (`x-customer-payout-links`) — `environment/.env.production` |
| `UNIVERSE_PUBLIC_ASSETS_URL` | `https://payout-links-assets.razorpay.com` | frontend-x — `x-customer-payout-links/environment/.env.production` |
| `ApiClient.BaseUrl` | `https://api-web.dev.razorpay.in/v1` (dev; API monolith) | xperience — `config/default.toml` |
| `PSClient.BaseUrl` | `https://payouts.dev.razorpay.in` (dev; Payouts Service) | xperience — `config/default.toml` |
| `BatchClient.BaseUrl` | `env\|BATCHCLIENT_BASEURL` | xperience — `config/default.toml` |
| `WorkflowsClient.BaseUrl` | `env\|WORKFLOWSCLIENT_BASEURL` | xperience — `config/default.toml` |
| `BankingAccountClient.BaseUrl` | `https://banking-account.dev.razorpay.in/v0.2` | xperience — `config/default.toml` |
| `XBalancesClient.BaseUrl` | `https://x-balances.dev.razorpay.in` | xperience — `config/default.toml` |
| `XASClient.BaseUrl` | `https://x-account-statements.dev.razorpay.in` | xperience — `config/default.toml` |
| `FTSLiveClient.BaseUrl` | `https://fts-live.dev.razorpay.in` | xperience — `config/default.toml` |
| `RazorpayPaymentClient.BaseUrl` | `https://api.razorpay.com/v1` | xperience — `config/default.toml` |
| `PassportConfig.Edge.JwksHost` | `https://edge-base.dev.razorpay.in/` | xperience — `config/default.toml` |
| `EmailConfig.RxBaseUrl` | `https://x.razorpay.com/` (link-back in notification emails) | xperience — `config/default.toml` |

Note: the `x` SPA has **no separate literal "xperience host"** — `/xperience/...` and `/xperience-edge/...` are relative path prefixes on the single `apiHost`, resolved server-side (routing from `dashboard.razorpay.com` to the xperience service happens outside the `x` repo).

---

## xperience route / backend table (selected, payout-relevant + representative)

| gRPC method (`routes.go`) | Handler | Downstream backend(s) | Auth class |
|---|---|---|---|
| `bulkpayouts.v1.BulkPayoutEdgeAPI/Create` | `internal/app/controller/bulkpayouts/edge-controller.go` → `Service.Create` | `batch` client | `ProxyAuth` |
| `bulkpayouts.v1.BulkPayoutEdgeAPI/Process` | edge-controller → `Service.Process` (`internal/app/service/bulkpayouts/service.go:2351`) | `batch.GetApiCaller().ProcessBatch` (`:2887`) | `ProxyAuth` |
| `bulkpayouts.v1.BulkPayoutEdgeAPI/BulkApprove` / `BulkReject` | edge-controller | `workflows` client, `batch` client | `ProxyAuth` |
| `bulkpayouts.v1.BulkPayoutEdgeAPI/FetchPending`/`FetchAll`/`FetchMy`/`FetchMyApprovals`/`GetRows`/`FindByID` | edge-controller | `bulkpayouts` MySQL repo, `batch.GetApiCaller().FetchBatch/FetchRows` | `ProxyAuth` |
| `bulkpayouts.v1.BulkPayoutAPI/*` (non-Edge dupes) | `internal/app/controller/bulkpayouts/controller.go` | same | `InternalAuth` (callers: api, workflows, batch) |
| `payoutdowntime.v1.PayoutDowntimeEdgeAPI/GetAllDowntimes`, `/GetDowntimeById` | `internal/app/controller/payoutdowntime/controller.go` | `payoutdowntime` repo | `MerchantAuth` |
| `paywall.v1.PaywallEdgeAPI/GetMerchantDetails` | `internal/app/controller/paywall/edge-controller.go` | `razorpaypayment` client (api.razorpay.com) | `ProxyAuth` |
| `costcenters.v1.CostCenterEdgeAPI/*` | `internal/app/controller/costcenters/edge-controller.go` | mysql/postgres repo | `ProxyAuth` / `AdminAuth` for `*AdminEdge` |
| `statements.v1.StatementsSearchAPI/SearchStatements` | `internal/app/controller/statements/controller.go` | XAS client, `banking` client | `InternalAuth` |
| `health.v1.HealthCheckAPI/Check`, `/Ping` | `pkg/health` | — | `DirectAuth` |
| (client-only, not a route) `apiservice.CreatePettyCashPayout` | `internal/app/client/apiservice/create_petty_cash_payout.go:18` | API monolith `composite_payout_internal` | outbound, header `X-Razorpay-Account` |
| (client-only) `payouts.GetPayoutByID` | `internal/app/client/payouts/api_caller.go` | Payouts Service (`PSClient`) | outbound |

---

## Feature flag table

| Flag / mechanism | Repo | Effect | Evidence |
|---|---|---|---|
| Splitz experiment `rxPayoutsRTK` | x | Payouts RTK-Query migration exposure | `config.js` (`rxPayoutsRTK: 'KvMe67nh6Mngcw'`) |
| Splitz experiment `rxRTKSplitzId` | x | Broader RTK migration exposure | `config.js` |
| `payoutFeatureFlag` | x | Merchant-level gate on Bulk Payout create route (`allowedFeatures`) | `Routes.js:1472` |
| `isXpsEdgeMigrationExpEnabled` | x | Swaps `/xperience/...` → `/xperience-edge/...` for bulk payout processing | `src/js/api/outflow.js:293-307` |
| `isXpsEdgeBulkPayoutsMigrationEnabled` | x | Selects v2/Xperience-Edge upload+list endpoints vs. legacy/v1 | `src/js/api/bulkPayoutsApiSlice.ts:79-103` |
| `isIdempotencyForCreatePayoutsExpEnabled` | x | Gates the `X-Payout-Idempotency` header on single payout create | `src/js/api/outflow.js:50,83-90` |
| Splitz (`abService` / `SplitzService`) | x | General experimentation SDK, `apiBaseUrl = coreApiDomain` | `src/js/modules/splitz.js:1-24` |
| Splitz client (`Splitz` config section) | xperience | Backend-side experiment resolution (used inside shadow/route-mode-style decisions in sibling `payouts` repo per other lane's findings; present as a config dependency here too) | `config/default.toml` |
| No LaunchDarkly anywhere | x, xperience, frontend-x, dashboard | repo-wide negative grep in all four repos | — |

---

## Unresolved questions

1. **Where does the Slack-referenced "dashboard-proxy → Payouts Service, identification-only passport" mechanism actually live?** Not found in `dashboard` (PG) or `x` (RazorpayX) repos in this checkout. Candidates not investigated: a separate API-gateway/edge repo, or the `xperience` "Proxy"/"Api" Passport identity distinction may be the same concept under a different name — worth a targeted follow-up grep across an edge-gateway or "passport" repo if available.
2. **Server-side routing from `dashboard.razorpay.com` (the `x` SPA's `apiHost`) to `xperience` / `xperience-edge` / the Payouts Service is invisible from the frontend repos.** The `x` repo shows only relative path prefixes; actual reverse-proxy/service-mesh routing rules live outside all four repos investigated here (likely an edge/API-gateway repo, or the `payouts` repo's own shadow-gateway per the sibling `01_payouts_core.md` findings — cross-reference recommended).
3. **Exact backend behind `PSClient`/Payouts-Service vs. the `payouts` repo investigated in another lane** — xperience's `PSClient.BaseUrl = https://payouts.dev.razorpay.in` almost certainly is the `payouts` Go service from lane 01, but this was not cross-confirmed by comparing service names/ports directly.
4. **Whether `X-Razorpay-Account` account-switching exists at all client-side in `x`** — confirmed absent via grep (negative evidence only); worth confirming via live network-tab capture since the codebase may set it via a mechanism not textually greppable (e.g. dynamically constructed header name) or the multi-account switch may not be a feature of this dashboard at all (single account per login/cookie).
5. **`xperience`'s actual REST path strings** are not visible in this shallow clone because `.proto` files (hence `google.api.http` REST path annotations) are gitignored and fetched from a separate Buf Schema Registry repo (`buf.build/razorpay/xperience`) at build time — only gRPC method names and Go handler wiring were confirmed, not the literal REST URLs merchants' browsers would hit (though the `x` SPA's `/xperience/...` relative-path convention strongly suggests a 1:1 gateway mapping).
6. **`x-vendor-portal`'s payout UI surface** (imports `x/VendorPayouts`) was not traced further into the `x` repo's `VendorPayouts` module in this lane — cross-reference with the main `x` findings above (§ Create/Bulk Payout modules) if vendor-portal-specific payout flows need deeper coverage.
7. **CODEOWNERS gaps** — both `frontend-x` (`x-accounts-receivable`, `partner-lms` effectively unowned due to typos) and `x` (no team-specific ownership of payouts UI directories beyond the global fallback) show weaker-than-expected ownership enforcement for payout-adjacent code; not verified whether this is intentional or an oversight.
8. **PG dashboard's Passport-JWT distribution mechanism** (`X-Passport-JWT-V1`, presumably edge/gateway-injected) was not traced to its source — unclear whether this is the same cross-product identity system referenced by xperience's `PassportConfig.Api`/`Edge` identities, which would materially clarify the cross-domain session/identity story between PG dashboard and X dashboard.
