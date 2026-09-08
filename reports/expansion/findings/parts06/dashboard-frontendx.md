# dashboard + frontend-x: how RazorpayX is wired into the merchant dashboard

Scope: read-only survey of `dashboard` (Laravel PHP + Nx/Rspack MF monorepo) and `frontend-x` (lerna). Clone root: `/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture/`. All paths below are `repo/relative/path:line`. Tags: **OBS** = observed in source, **INF** = inferred, **UNK** = unknown.

---

## 1. How `/banking/*` (X) is hosted inside `dashboard`

### 1.1 Mechanism (OBS): runtime Module Federation, cross-repo remote `x`

| Layer | Evidence | What it says |
|---|---|---|
| Client router | `dashboard/apps/shell/src/client/components/ShellSSRLayout/SSRProductRouter.tsx:42` `const XDashboard = lazy(() => loadRemote('@federated/cross-repo/x/connectedX'))`; `:99` `<Route path="banking/*" element={<WrappedXDashboard />} />` | `/app/banking/*` renders a federated remote, not an iframe |
| Legacy (non-SSR) router | `dashboard/apps/shell/src/client/components/ProductRouter/ProductRouter.tsx:34,150` (same `loadRemote` + `banking/*`) | same mechanism on both router variants |
| MF runtime init | `dashboard/apps/shell/src/client/services/module-federation/federatedRuntimeImport/federatedRuntimeImport.ts:2-14` `init({ name:'shell', remotes:[{ alias:'@federated/cross-repo/x', entry: window.X_BANKING_REMOTE_ENTRY, name:'x' }] })` | `@module-federation/enhanced/runtime`; remote name `x` |
| Remote entry URL (server env) | `dashboard/apps/shell/src/env.ts:129` `X_BANKING_REMOTE_ENTRY`; prod `dashboard/apps/shell/environment/.env.production:136` `https://x.razorpay.com/dist/x.remoteEntry.js`; canary `.env.canary:145` `https://x.razorpay.com/canary/dist/x.remoteEntry.js`; devstack `.env.devstack:125` `https://x.dev.razorpay.in/dist/x.remoteEntry.js`; dev `.env.development:122` `https://localhost:8880/dist/x.remoteEntry.js` | the X bundle is served from `x.razorpay.com`, not from the dashboard origin |
| Injected into page | `dashboard/apps/shell/src/server/services/generator/scripts/generateEnvironmentKeysGetterScriptElement.tsx:78` `window.X_BANKING_REMOTE_ENTRY = "..."`; typed at `dashboard/libs/shared-types/global.d.ts:100` | SSR template exposes the env to the browser |
| X's own asset URLs | `dashboard/apps/shell/src/server/services/generator/scripts/generateXRemoteUrlsScript.tsx:42-46` `xOrigin = https://x.razorpay.com` (prod/canary) / `https://x.dev.razorpay.in`; `window.xUrl`, `window.xVendorPortalUrl` (`{xOrigin}/federated-bundles/x-vendor-portal`), `window.coreBundlesUrl` (`https://dashboard-assets.razorpay.com/dashboard/core-bundles`), plus capital URLs (§2) | header comment `:10` says it must mirror `X: src/js/bootstrap.ts` |
| Top-nav actions from X | `dashboard/apps/shell/src/client/components/Navigation/HeaderActionsLoader/index.tsx:129` `loadRemote('@federated/cross-repo/x/BankingTopNavItems')`; `:149-151` shell wraps it because "Banking's cross-repo remote hasn't shipped its own TopNavActions wrapper yet" | two exposed modules consumed: `connectedX`, `BankingTopNavItems` |
| Producer side (x repo, in clone root, corroboration only) | `x/scripts/webpack.dashboard.config.js:464-466` `name:'x'`, `filename:'x.remoteEntry.js'`; `:481` `'./connectedX': './src/js/exposed/ConnectedXApp/index'`; `:519` `'./BankingTopNavItems': ...` ; `x/src/js/bootstrap.ts:20-22` sets `window.xUrl`, `window.xVendorPortalUrl` | confirms the contract end-to-end |
| Router basename | `dashboard/apps/shell/src/client/contexts/ShellBrowserProvider.tsx:40` `<BrowserRouter basename="/app">`; `dashboard/apps/shell/src/server/configs/routes.server.ts:11` `STRICT_FRONTEND_APPS: '/app'` | public path is `/app/banking/*` |
| E2E path constants | `dashboard/libs/shared-qsuite/src/playwright/constants/index.ts:544` `X_BANKING: '/app/razorpayx'`, `:550` `BANKING: '/app/banking'` | both paths exist |

**Verdict (OBS):** X is hosted in the dashboard as a **cross-repo Module Federation remote** (`x@https://x.razorpay.com/dist/x.remoteEntry.js`, exposes `./connectedX` and `./BankingTopNavItems`), mounted under `/app/banking/*`. No iframe, no nginx proxy, no PHP route for `/banking`.

### 1.2 Things that are NOT the mechanism (OBS negative results)

| Checked | Result |
|---|---|
| PHP routes for `/banking`, `/x/`, `/app/banking` | `dashboard/app/Http/routes.php` — no match (rg). Only SPA catch-alls `razorx/{all?}` `:353`, `admin/capital-los/{all?}` `:354`, `admin/{all}` `:355` |
| nginx / k8s proxying to x | `dashboard/dockerconf/prod-default.conf:102-104` `location / { try_files ... /index.php }` — no `proxy_pass`, no `banking`; `dashboard/k8s/{beta,echo,qa}` no `banking`/`x.razorpay` matches |
| `dashboard-core.config.js` | `dashboard/dashboard-core.config.js:14-44` only lists E2E `baseDependencies` (edge, frontend-graphql, ufh, wallet, gcoms, asv, rize, pgos, gimli, reminders, scrooge, ui-config-service, care, partnerships, no-code-apps, splitz, subscriptions, terminals, pg-router, api, settlements, capital-es, recon-saas). No X/banking service. |
| `frozen-legacy-modules.js` | `dashboard/frozen-legacy-modules.js:22-51` — PG legacy modules → MFE map; includes `Capital → apps/capital` `:39`, `Payroll → apps/payroll-app` `:47`; no X/banking entry |

### 1.3 Reverse direction: dashboard loaded *inside* X (OBS, legacy)

`dashboard/resources/views/merchant/index1.blade.php:14-31`: if `window.parent !== window` and parent URL contains `config('app.banking_service_url')` (= `https://x.razorpay.com`, `dashboard/environment/.env.production:15`), the PG dashboard page `document.write`s `<script src="{banking_service_url}/dist/pgClient.js">`, sets `window.RZP.appHost = banking_service_url`, `window.RZP.appName = "businessbanking"`. **INF:** X embeds PG dashboard pages in an iframe for some flows (older "PG inside X" pattern); `pgClient.js` lives in the `x` repo (UNK — not verified there).

### 1.4 The PHP backend as X's BFF: origin-based product switch (OBS)

| Item | Evidence |
|---|---|
| Origin check | `dashboard/app/Http/ApiUrl.php:146-171` `isBankingOriginRequest($v2=false)`: compares request `Origin` host (and, in v2, request host) against `app.banking_service_url` (`https://x.razorpay.com`), `app.bank_lms_banking_service_url` (`https://partner-lms.razorpay.com`, default in `dashboard/config/app.php:294`), and v2 list `app.banking_service_url_v2` |
| v2 host list | `dashboard/environment/.env.production:14` `BANKING_SERVICE_URL_V2="https://x.razorpay.com, https://hdfc.razorpay.com, https://giga.razorpay.com, https://hdfcbank.razorpay.com, https://axis.razorpay.com, https://axiseasypay.razorpay.com, https://bankingprograms.razorpay.com, https://iob..., indusindbank..., bankofbaroda..., kotak..., idfcbank..., icicibank..., americanexpress..., yesbankltd..., hsbc..., sib..., dbms..."` (bank co-branded X/VAS hosts) |
| CORS allow-list | `dashboard/app/Http/Middleware/Cors.php:39-45` `banking_domain → app.banking_service_url`, `bank_lms_banking_domain → app.bank_lms_banking_service_url`; `:472-475` echoes `Access-Control-Allow-Origin: $originDomain` + `Allow-Credentials: true` |
| Role selection by product | `dashboard/app/User/Helper.php:73-86` `selectMerchantToLogin`: `productRole = isBanking ? 'banking_role' : 'role'`; same in `dashboard/app/User/Service.php:1255-1263, 1371-1379, 2676-2680, 2921-2924` |
| Banking-specific payload | `dashboard/app/User/Service.php:4374-4404` `appendBankingDetails()` adds `banking_details.is_test_payout_created / is_live_payout_created` via `Merchant\Service::getPayoutCount` → `dashboard/app/Merchant/Service.php:1452` `payouts?count=1&product=banking` (API monolith) |
| Metrics / tracing product label | `dashboard/app/Metrics/Constants.php:174` `BANKING='banking'`; `dashboard/app/Constants/Tracing.php:118`; `dashboard/app/Edge/SessionMismatchRecorder.php:85`; `dashboard/app/Admin/ApiRequestAny.php:1350` |
| Node BFF mirror | `dashboard/apps/shell/src/server/utils/index.ts:33-39` `isBankingOriginRequest(req)`; `dashboard/apps/shell/src/server/services/redirection/ShellRedirectionService.ts:353-377,483-486,536-545` and `USLRedirectionService.ts:383-386,434-441` — banking-origin requests are excluded from Easy/USL/FTUX redirection |
| ABAC blacklist | `dashboard/app/Http/Middleware/ABACDashboardAccess.php:50-56` `$blackListOrgHost = ["admin-dashboard.razorpay.com","x.razorpay.com","partner-lms.razorpay.com","x.dev.razorpay.in","admin-dashboard-int.razorpay.com"]` |
| Legacy blade flag | `dashboard/resources/views/merchant/index2.blade.php:15` `window.is_banking_request = {!! $is_banking_request !!}` |
| Banking demo user | `dashboard/app/User/Service.php:1004-1005` `Constants::BANKING_DEMO_USER_EMAIL` + `app.banking_demo_user_password`; `dashboard/app/Merchant/Constants.php:272-273` demo banking merchant ids (Beta/Prod) |

**INF:** the X SPA at `x.razorpay.com` calls `dashboard.razorpay.com` PHP endpoints (`/user`, `/merchant/api/{mode}/...`) with credentials; the PHP layer switches to `banking_role` when `Origin` is an X host. `partner-lms` (frontend-x) does exactly this (§4).

### 1.5 Navigation / product-switch wiring for Banking (OBS)

| Item | Evidence |
|---|---|
| Shell nav alias → href | `dashboard/apps/shell/src/shared/navigationShellConfig.ts:49-52` `banking_top_navigation_item: { icon:'RazorpayXIcon', href:'/banking' }`; `:100-101` `banking_navigate`, `bankingplus_navigate` → `/banking`; `:202` `x_banking → /banking` |
| Alias constants | `dashboard/apps/shell/src/client/components/Navigation/constants.ts:5` `BANKING: 'banking_top_navigation_item'`; `:30` `PRODUCT_PATH_MAP_FOR_INTERNAL_NAVIGATION[BANKING] = '/banking'` |
| Self-managed side-nav | `dashboard/apps/shell/src/client/components/ShellSSRLayout/sideNavRegistry/registry.ts:11,65` `banking_top_navigation_item: { kind:'self-managed' }`; `dashboard/apps/shell/src/shared/utils/navUtils.ts:132,143-144` pathname `/razorpayx` or `/banking` → active product `banking_top_navigation_item` |
| Full-screen (nav-hidden) banking flows | `dashboard/apps/shell/src/client/components/Navigation/constants.ts:44-64` `FULL_SCREEN_FLOWS_REGISTRY.BANKING` = `/banking/createPayout/*`, `/banking/invoice/*`, `/banking/create-invoice/*`, `/banking/createAdvance/*`, `/banking/createPurchaseOrder/*`, `/banking/createGrn/*`, `/banking/create-role/*`, `/banking/add-team-member*/*`, `/banking/update-team-member-role/*`, `/banking/workflow/*`, `/banking/payout-links/*`, `/banking/vendor-payments/vendor-application/*`, `/banking/vendor-payments/vendors-onboarding/*`, `/banking/bulk-payout/create/*`, `/banking/bulk-upload/create/*`, `/banking/bulk-vendors/create/*`, `/banking/demo/*`, `/banking/contacts/create/*` |
| Growth / access-denied page | `dashboard/apps/shell/src/shared/navigationPageConfig.ts:48-66` "Business Banking supercharged for disruptors" → CTA `https://razorpay.com/x/`; access-denied copy |
| Server nav config (UCS mock) | `dashboard/apps/shell/src/server/components/config.js:693-745` section `banking_products` with items `x_banking` (rule: splitz `J299iRPbuBRWeb` ∧ org `100000razorpay` ∧ IN ∧ ¬`primary.dispute_manager`), `x_corporate_cards` (`'cards_los' IN req.Features`), `x_payroll` (splitz `KmBlZ2iUatsoAB`…), `cash_advance` (`loc`/`cash_on_card` + `withdraw_loc` + owner/admin roles), `line_of_credit` (`loc_emi` + `withdraw_loc`) |
| Mobile product switcher | `dashboard/apps/shell/src/client/components/Navigation/TopNavigation/ProductSwitcherBottomSheet.tsx:51-66`, `hooks/utils.ts:142-148` (excluded aliases set) |
| Legacy `/razorpayx` widget route | `dashboard/web/js/merchant/routes/Content.js:2537-2545` `<Route path="razorpayx/*">` guarded by `user.isShowRazorpayXWidgetEnabled && user.isOrgRZP` → `RazorpayXWidget` (`dashboard/web/js/merchant/views/RazorpayXWidget/RazorpayXWidget.js:2` → `@libs/web-nexus/common/ui/XBankingWidget`); CTA `dashboard/web/js/common/ui/XBankingWidget/fallbackViewData.js:44-46` `https://x.razorpay.com/welcome?campaign=pg_x_widget&intent=current_account` |
| Legacy sidebar | `dashboard/web/js/merchant/components/Sidebar/MerchantNavLinks.js:336-349` "Banking" → `/razorpayx` (cond `getIsBankingEnabled`), "Payroll" → `/payroll`; `dashboard/web/js/merchant/components/Sidebar/helpers.js:1-6` |
| Legacy header links to X | `dashboard/web/js/merchant/components/HeaderNav/ProfileDropdownV2.tsx:247-249` "Go to RazorpayX" `https://x.razorpay.com`; `dashboard/web/js/merchant/utils/redirect.ts:4-17` `redirectToXBanking()` → `X_BANKING_PROD_URL='https://x.razorpay.com'` / `X_BANKING_DEV_URL='https://x.dev.razorpay.in'` (`dashboard/web/js/merchant/constants/urls.js:16-17`); `HeaderNav/VasPayoutsMiniNav.js:87` (shown when `user.isVasXBankingTopNavEnabled`, `HeaderNav/index.js:239`) |
| Legacy app switcher | `dashboard/web/js/merchant/components/HeaderNav/AppSwitcher.js:20-45` "Business Banking" list → `https://razorpay.com/x/current-accounts/`, `/x/payout-links/`, `/x/vendor-payments/`, `/x/payouts/` (marketing pages) |

---

## 2. Other RazorpayX-family products referenced in `dashboard`

| Product | Dashboard route(s) | Where wired | Backend / external target | Gate |
|---|---|---|---|---|
| **X Banking** | `/app/banking/*` (new), `/app/razorpayx/*` (legacy widget) | §1 | `https://x.razorpay.com/dist/x.remoteEntry.js` (MF); PHP BFF with `banking_role` | splitz `show_razorpayx_widget_exp` (`dashboard/web/js/merchant/models/User.js:874-875`), splitz `J299iRPbuBRWeb` (config.js:705) |
| **X Payroll** | `/app/payroll/*` | `dashboard/web/js/merchant/routes/Content.js:3013-3019` → `merchant/views/Payroll` (`Content.js:1113-1115`); shell alias `payroll_top_navigation_item`/`x_payroll` → `/payroll` (`navigationShellConfig.ts:54-57,102,204`) | Marketing widget only: `dashboard/web/js/merchant/views/Payroll/index.js:111` `https://payroll.razorpay.com/signup?utm_source=payroll_widget&utm_medium=pgdashboard...`. SSO: `dashboard/config/auth.php:39-42` `service_provider.opfin` (`OPFIN_JWT_SIGNING_KEY`, `OPFIN_REDIRECT_URL`); prod `OPFIN_REDIRECT_URL='https://payroll.razorpay.com/authenticate'` (`dashboard/environment/.env.production:71`, `apps/shell/environment/.env.production:95`); dev `https://opfin.np.razorpay.in/authenticate` (`apps/shell/environment/.env.development:84`) | splitz `show_payroll_widget_exp` (`User.js:1959-1960`), `KmBlZ2iUatsoAB` (config.js:726); `dashboard/config/splitz.php:22-25` `RAZORPAYX_PAYROLL_EXPERIMENT_*` |
| `apps/payroll-app` MFE | (none yet) | `dashboard/apps/payroll-app/src/App.tsx:7-14` is a dashboard-cli scaffold placeholder ("This app is generated by dashboard-cli"); alias registered `dashboard/tsconfig.base.json:64` | none | — |
| **Capital: Corporate Cards** | `/app/capital/corporate-cards/*` (alias `x_corporate_cards`, shell href `/corporate-cards` `navigationShellConfig.ts:203`; sidebar href `/capital/corporate-cards/` `apps/shell/src/client/widgets/Sidebar/href.ts:56`) | legacy `Content.js:3000-3006`; MFE `dashboard/apps/capital/src/App.tsx:63-66` (`prefix:'/capital/corporate-cards'`) gated by splitz `capital_mfe_migration` (`dashboard/web/js/merchant/routes/constants.ts:33-39`, `Content.js:2946-2952`) | `dashboard/apps/capital/src/views/corporate-cards/api.ts:4-7` via `merchantFetch` (PHP proxy `/merchant/api/live/...`): `los/service/twirp/rzp.capital.los.admin.v1.ProductAPI/GetProducts`, `los/service/twirp/rzp.capital.los.origination.v1.ApplicationAPI/ListOrSearch`. Card token: `dashboard/app/Http/routes.php:207` `GET /cards/token` → `GenerateTokenController.php:30,37` `capital_cards/token` then redirect `{checkout}/virtual-card?token=`. Remote bundles: `generateXRemoteUrlsScript.tsx:65-77` `https://cdn.razorpay.com/capital/federated-bundles/{cards-dashboard,cash-advance-emi,bnpl-dashboard}`, `https://cdn.razorpay.com/capital-onboarding/application/application.{modern,legacy}.remoteEntry.js` (`window.capital*Url`) | feature `cards_los` (config.js:719), `capital_cards`, `capital_cards_eligible` (`User.js:1636,1640`) |
| **Capital: Cash Advance / LOC** | `/app/capital/cash-advance`, `/app/capital/line-of-credit` (`href.ts:77-78`); shell `/cash-advance`, `/line-of-credit` (`navigationShellConfig.ts:205-206`) | legacy `Content.js:2958-2981` → `CashAdvanceRedirectToX` (`views/Capital/CashAdvance/withEDIMigration.tsx:58-67,127`); MFE `apps/capital/src/App.tsx:69-72` | **Redirects to X**: `dashboard/web/js/merchant/views/Capital/CashAdvanceV2/constants.ts:230-231` `NEW_CASH_ADVANCE_DASHBOARD='https://x.razorpay.com/capital/cash-advance?from=dashboard'` | features `loc`, `cash_on_card`, `withdraw_loc`, `loc_emi` (`User.js:1620,1628`; `views/Capital/utils/index.js:266-290`) |
| **Capital: Working-capital / non-FLDG loans, loans collections** | `/app/capital/non-fldg-loans/*`, `/app/capital/loans/*` (`href.ts:57,79`; `apps/capital/src/constants/loans.ts:33`) | `apps/capital/src/App.tsx:51-58` | `dashboard/apps/capital/src/views/loans-collections/api.js:11` `capital_collections/service/v1/{plans,installments,repayments,...}` via merchantFetch | `isLoansEnabled` (`User.js:1587`); `disable_loans_post_dpd`, `disable_loc_post_dpd` (`User.js:838,842`) |
| Capital admin (LOS) | `/admin/capital-los/*` | `dashboard/app/Http/routes.php:354` `capital_catchall` → `AdminController@getIndex` | admin SPA (`web/js/razorx`?) UNK | admin |
| **Current Account (CA) onboarding / X onboarding** | no dashboard route; CTA to `https://x.razorpay.com/welcome?...intent=current_account` (§1.5) | `dashboard/web/js/merchant/components/AnnouncementBar.js:35-52` "Get a current Account with RazorpayX"; `dashboard/web/js/common/components/Carousel/CarouselModal.tsx:15,27` | PHP: `dashboard/app/User/Service.php:2610` `current_account_waitlist_number`; `:2727-2735,4172-4240` RazorX exps `rx_ca_self_serve_flow`, `rx_ca_self_serve_flow_neo`, `rx_non_self_serve_ca_flow`, `merchant.business_banking_signup_at`, `from_ca_page`; Salesforce lead `x_onboarding_category='self_serve'` (`:2733`); route team `dashboard/app/Http/RouteTeamMap.php:14,51-70` `TEAM_RAZORPAYX_ONBOARDING='razorpayx_onboarding'` owns `oauth_user*`, `oauth_merchant*`, `oauth_get_org` routes (`routes.php:415-444`) | RazorX flags `dashboard/app/Razorx/Constants.php:85-86` |
| **Vendor payments / vendor portal** | inside X: `/banking/vendor-payments/...` (§1.5 full-screen list) | `window.xVendorPortalUrl` set by shell (`generateXRemoteUrlsScript.tsx:48-53`) → `https://x.razorpay.com/federated-bundles/x-vendor-portal` | PHP admin proxy whitelists `vendor-payments` route prefix (`dashboard/app/Admin/ApiRequestAny.php:136-137`) | — |
| **Tax payments** | none found as a dashboard route (only marketing copy "Automated Tax payments." `AppSwitcher.js:39`) | — | — | UNK |
| **Insights / InsightX** | `/app/insights/*` (PG insights MFE `apps/insights`) — *not* X | `INSIGHTX_HOST_URL=https://insightx-web.de.razorpay.com` (`dashboard/environment/.env.production:297`); PHP `routes.php:200-204` `/merchant/insightx/generate_superset_token`, `/method_aggregate` | Superset-based PG analytics; **not** RazorpayX (name collision) | — |
| **Invoices / accounting** | `/app/invoices` = PG invoices MFE (`frozen-legacy-modules.js:33`) — not X | — | — | — |
| **Partner LMS (bank partners)** | external host `https://partner-lms.razorpay.com` (`config/app.php:294`) treated as banking origin (§1.4) | — | frontend-x `partner-lms` (§4) | — |
| **Bank co-branded X / VAS "banking programs"** | external hosts | `dashboard/app/User/Constants.php:364-405` `DOMAIN_REDIRECT_MAP` (`hdfc.razorpay.com` → `hdfc-accounts.razorpay.com`, `bankingprograms.razorpay.com`, `bankingpartnerships.razorpay.com`, `kotak`, `icicibank`, `hsbc`, … id `BANKING_REDIRECTION_ENABLED`); `config/app.php:334-339` `bankingprograms_*_url`, `bankingpartnerships_*_url`; `.env.production:334-379` `*_ACCOUNTS_URL` | splitz `BANKING_REDIRECTION_ENABLED` (`config/splitz.php:270`) | — |

Hostname hits summary (OBS): `x.razorpay.com`, `x.dev.razorpay.in`, `x.np.razorpay.in` (`.env.beta:15`, shell `.env.development:44`), `payroll.razorpay.com`, `opfin.np.razorpay.in`, `partner-lms.razorpay.com` / `partner-lms.np.razorpay.in`, `cdn.razorpay.com/capital/...`, `dashboard-assets.razorpay.com`. **No** hits for `x-api`, `xperience`, `banking-accounts`, `razorpayx.` as hostnames in dashboard.

---

## 3. Permissions / roles / flags relevant to X in `dashboard`

| Kind | Name | Evidence |
|---|---|---|
| Role field | `merchant.banking_role` (vs `role`) | `dashboard/app/User/Helper.php:76-86`; `dashboard/app/User/Service.php:2234` (`banking_role !== 'owner'`), `dashboard/app/MerchantDetails/Service.php:404`; types `dashboard/libs/shared-types/src/common/razorpay-user/RazorpayUser.ts:505`, `RazorpayUserMerchant.ts:78`, `razorpay-user-v2/RzpUserV2.ts:30-31` (`banking_role`, `banking_role_name`); legacy `dashboard/web/js/merchant/models/User.js:165`; test fixture `dashboard/Tests/Functional/UserSessionTest.php:300` |
| Role names seen | `owner` (banking); PG-side `primary.owner|pseudo_owner|admin|merchant_view_only|manager|finance|dispute_manager` in nav rules (`config.js:705,733,741`) | X-side role catalogue (e.g. `banking.*`) **UNK** in dashboard |
| Product enum | `DashboardGraphQLProductTypeEnum.RAZORPAYX` | `dashboard/libs/shared-types/src/common/dashboard-graphql/DashboardGraphQLProductTypeEnum.ts:3` |
| Team | `DASHBOARD_TEAMS.RAZORPAY_X='RazorpayX'` | `dashboard/libs/shared-types/src/common/DASHBOARD_TEAMS.ts:25` |
| Org/feature flags | `show_top_nav_razorpayx` (org feature), `vas_razorpayx` (merchant feature) → `isVasXBankingTopNavEnabled` | `dashboard/web/js/merchant/models/User.js:2278-2279` |
| Capital features | `cards_los`, `capital_cards`, `capital_cards_eligible`, `loc`, `cash_on_card`, `withdraw_loc`, `loc_emi`, `disable_loc_post_dpd`, `disable_loans_post_dpd` | `config.js:719,733,741`; `User.js:838,842,1620,1628,1636,1640` |
| Splitz experiments | `show_razorpayx_widget_exp`, `show_payroll_widget_exp`, `RAZORPAYX_PAYROLL_EXPERIMENT_*`, `BANKING_REDIRECTION_ENABLED`, `PARTNERSHIP_CAPITAL`, `CAPITAL_ES_BLOCKED_SPLITZ`, `CAPITAL_ISPLUSPLUS_SPLITZ`, `capital_mfe_migration`; nav ids `J299iRPbuBRWeb` (X Banking), `KmBlZ2iUatsoAB` (X Payroll) | `dashboard/config/splitz.php:22-25,50,134,151,206,237,270`; `dashboard/web/js/merchant/utils/abExperimentsMap.js:141,316`; `dashboard/web/js/merchant/routes/constants.ts:39`; `config.js:705-727` |
| RazorX (legacy) flags | `rx_ca_self_serve_flow`, `rx_non_self_serve_ca_flow`, `mob_welcome_ca_card` | `dashboard/app/Razorx/Constants.php:80,85-86`; `config/razorx.php:10` `REQUEST_ORIGIN => 'banking'` |
| Merchant attrs | `business_banking_signup_at` | `dashboard/app/User/Service.php:4196` |
| i18n hide tags | `i18_hide_add_new_razorpay_x_merchant`, `i18_hide_razorpay_x_affiliate_account` | `dashboard/web/js/merchant/constants/tags.ts:51-52,215,221` |
| Announcement tag | `announcement_razorpayx` | `User.js:967` |
| Legacy gating helpers | `getIsBankingEnabled` = `isShowRazorpayXWidgetEnabled && isOrgRZP && !isDisputeManager && !isFunctionScopedRole`; `getIsPayrollWidgetEnabled` | `dashboard/web/js/merchant/components/Sidebar/helpers.js:1-6` |
| Nav rule context | `req.Experiments`, `req.Features`, `req.Roles`, `req.Merchant.OrgID=='100000razorpay'`, `req.Merchant.CountryCode=='IN'` | `config.js:705-741` (UCS-style expressions) |

---

## 4. `frontend-x` packages

Root: `frontend-x/package.json:3-5` name `frontend-x`, "Suite of RazorpayX mobile and web apps"; workspaces `:11-15`; lerna 3.22.1 (`:40`); `@razorpay/universe-cli` 11.1.0 (`:43`). CODEOWNERS `frontend-x/CODEOWNERS:1-11`: `/frontend-x/ @sumit-gupta91`; `/x-customer-payout-links/ @sumit-gupta91 @Anmay5525`; `/x-accouts-receivables/ @parichay28 @sumit-gupta91` (sic — path typo, won't match `x-accounts-receivable`); `/partner-ms/ @anilpal6795 @kaushal007adi` (sic — no such dir); `/x-vendor-portal/ @burhanuday @sumit-gupta91 @Anmay5525`; fallback `* @razorpay/mandatoryxreviewers`. No entry for `x-invoice-approval` (falls to `*`).

| Package | Purpose (OBS) | Framework / deploy shape | Routes | API base + endpoints | Hostname |
|---|---|---|---|---|---|
| `x-accounts-receivable` v4.0.0 | "Customer facing page for accounts receivables" (`package.json`) — hosted invoice view + OTP | universe-cli SSR (Express `src/entryServer.ts:29-34` identity/ab/render middleware); `Dockerfile`, `devspace.yaml` | `frontend-x/x-accounts-receivable/src/shared/routes.tsx:20-21` `v1/:invoiceid/view/`, `v1/:invoiceid/redacted/` | `frontend-x/x-accounts-receivable/src/shared/globals.js:8-14` `https://accounts-receivable.razorpay.com/v1/` (prod) / `https://accounts-receivable.concierge.stage.razorpay.in/v1/` (dev/beta); `src/env.ts:13-14` hosted invoices base `.../v1/invoices/hosted`; `src/shared/api/invoice.ts:14-37` `POST {id}/generateOTP`, `POST {id}/verifyOTP`, `GET {id}/redacted`, `GET {id}/view?token=`, `POST {id}/purchaserMarkAsPaid`; checkout `src/app.tsx:22` `checkout.razorpay.com/v1/checkout.js`; devstack `UNIVERSE_PUBLIC_API_HOST='https://api-web.dev.razorpay.in'` (`environment/.env.devstack:9`) | assets `https://invoices-x.razorpay.com` (`environment/.env.production:3`); Sentry project `x-accounts-receivable` (`:7`) |
| `x-customer-payout-links` v2.2.0 | "customer facing payout links" — payee claims a payout link | universe-cli SSR (`src/entryServer.ts:29-37`); Dockerfile, `devspace.yml` | `src/shared/routes.tsx:18-21` `/`, `/about`; server accepts `^/v1/payout-links/poutlk_{14}/view/$` and `^/v1/demo/payout-links/poutlk_{14}/view/$` (`src/shared/services/renderMiddleware.js:158-159`) | `UNIVERSE_PUBLIC_API_HOST=https://api.razorpay.com/v1/` (`environment/.env.production:4`); `src/shared/globals.js:8-14` prod `https://api.razorpay.com/v1/`, beta `https://beta-api.stage.razorpay.in/v1/`, func `https://api-web.func.razorpay.in/v1/`; SSR fetch `payout-links/{id}/view-data`, `demo/payout-links/{id}/view-data` (`renderMiddleware.js:47,53`); `src/shared/api/plApi.js:3-25` `POST {id}/generate-customer-otp`, `verify-customer-otp`, `fund-accounts`, `initiate`, `GET {id}/status`, `GET https://ifsc.razorpay.com/{code}`; signup link `https://x.razorpay.com/auth/signup` (`src/shared/constants.js:152`) | assets `https://payout-links-assets.razorpay.com` (`.env.production:3`); Sentry `x-customer-payout-links` |
| `x-invoice-approval` v1.0.9 | "invoice approval public page" — approve/reject vendor-payment invoice from email link | universe-cli CSR (`src/bootstrap/Wrapper/Wrapper.tsx:26` BrowserRouter); no Dockerfile (static) | single page; action parsed from URL `params[2].split('action=')` (`src/Approval/Approval.tsx:20`) | `UNIVERSE_PUBLIC_API_HOST=https://api.razorpay.com/v1` (`environment/.env.production:4`), beta `https://api-web.dev.razorpay.in/v1` (`.env.beta:5`, `src/shared/api.ts:5`); `POST {API_HOST}/vendor-payments/approve-reject` (`src/Approval/data.ts:19`, `Approval.tsx:23,29-30`) | assets `https://invoices-x.razorpay.com/approval` (`.env.production:3`), beta `https://invoices-x.np.razorpay.in/approval`; Sentry `x-invoice-approval` |
| `x-vendor-portal` v10.2.1 | "Vendor Portal federated module" — vendor onboarding/bills UI loaded *inside X* | Webpack 5 `ModuleFederationPlugin` (`webpack.config.js:183-201`): `name:'x_vendor_portal'`, `filename:'x_vendor_portal.remoteEntry.js'`, `remotes: { x: 'x@[xUrl]/x.remoteEntry.js' }`, exposes `./Main`, `./duck`, `./saga`, `./utils`, `./VendorPortalDetailsView`, `./ga`; shares X's scope (`:13-14`); imports `x/api`, `x/apiTypes`, `x/store`, `x/modules` (`src/VendorPortalV2/store/apiSliceBaseQuery.ts:3-5`, `src/App.tsx:5,8`) | V2 `src/VendorPortalV2/constants.ts:3-13` `/vendor-portal/`, `/vendor-portal/onboarding/`, `/vendor-portal/bills/` (`Routes.tsx:12-14`); V1 `VendorPortalV1/VendorPortalListView/VendorPortalListViewWrapper.js:10-11` `settings`, `*` | via X's `fetch` (dashboard PHP proxy `/merchant/api/{mode}/`; test base `https://beta-dashboard.stage.razorpay.in/merchant/api/live` `constants.ts:47`); `BASE_PATH='/vendor_experience/vendor'` (`constants.ts:46`): `vendor_applications`, `vendor_application/{id}[/metadata|/action|/fund_account_validate]`, `document/{fileId}/get_signed_url`, `vendor_bills`, `vendor_bill[/{id}]` (`services/vendorApiSlice.ts:22-77`, `vendorBillsApiSlice.ts:18-47`, `apiSlice.ts:19` `{BASE_PATH}/create`); `/ufh/files/upload` (`vendorApiSlice.ts:69`); `/vendor-payments/tds-categories` (`:84`) | served from `{xOrigin}/federated-bundles/x-vendor-portal[/canary]` (dashboard `generateXRemoteUrlsScript.tsx:48-53`; `x/src/js/bootstrap.ts:22`); env detection by host `x.razorpay.com` / `x.np.razorpay.in` / `x-func.np.razorpay.in` / `x.dev.razorpay.in` (`src/VendorPortalV1/utils.js:198-210`) |
| `partner-lms` v3.0.4 | "Dashboard for bank partners for lead management" — bank POCs track CA activation leads | universe-cli CSR + `devstack/Dockerfile`, `config/devstack.json` (deps `api`, `dashboard`, `master-onboarding`) | `src/App/App.js:31-35` `auth/*`, `/`, `lead-details/:id`; `src/views/auth/Auth.js:29-33` `auth/signin`, `auth/invitation` (`src/shared/routes/paths.js:1-8`) | **Dashboard PHP as API**: `UNIVERSE_PUBLIC_API_HOST=https://dashboard.razorpay.com/` (`environment/.env.production:6`), devstack `https://dashboard.dev.razorpay.in/`; `src/api/user.js:9-66` `GET user/?tags=0&...`, `POST user/signin`, `user/logout`, `user/register`, `GET user/api/live/invitations/token/{t}`; `src/api/merchant.js:68-139` `merchant/api/live/partner_lms/banking_account[/{id}]`, `.../partner_lms/activation/{id}/activity`, `/comments`, `/bank_poc`, `.../partner_lms/bank_branches`, `.../partner_lms/bank_pocs`, `merchant/api/live/merchants-users` | `https://partner-lms.razorpay.com` (`.env.production:3`; `webpack.config.js:14-17` also `partner-lms.dev.razorpay.in`, `partner-lms-func.np.razorpay.in`, `partner-lms.np.razorpay.in`); matches dashboard `bank_lms_banking_service_url` (§1.4) |

Owning team (OBS): CODEOWNERS individuals above; org-level `@razorpay/mandatoryxreviewers`. Team names beyond that: **UNK**.

---

## 5. Domain signal strength, probable external repos/services, unknowns

### 5.1 Signal strength (evidence density in these two repos)

| Domain | Strength | Basis |
|---|---|---|
| X shell integration (MF remote `x`, `/app/banking`) | **Strong** | 10+ files in `apps/shell` + producer config in `x` repo |
| PHP BFF product switch (`banking_role`, origin check, CORS) | **Strong** | `ApiUrl.php`, `Cors.php`, `User/{Helper,Service}.php`, shell redirection services |
| Vendor payments / vendor portal | **Strong** | `x-vendor-portal` full API surface + shell full-screen routes |
| Payout links (customer side) | **Strong** | `x-customer-payout-links` endpoints against `api.razorpay.com/v1/payout-links` |
| Accounts receivable / hosted invoices | **Medium** | `x-accounts-receivable` → `accounts-receivable.razorpay.com` (separate service) |
| Invoice approval (vendor-payments workflow) | **Medium** | one endpoint `/v1/vendor-payments/approve-reject` |
| Partner LMS / CA activation | **Medium** | `partner-lms` endpoints `merchant/api/live/partner_lms/*` on dashboard PHP; controllers not inspected |
| Capital (cards/LOC/loans) | **Medium** | `apps/capital` + legacy views; cash-advance UI **redirects into X** |
| Payroll (Opfin) | **Weak** | marketing widget + JWT SSO config only; `apps/payroll-app` is a scaffold |
| CA onboarding / X onboarding | **Weak-Medium** | PHP experiment plumbing + Salesforce lead; UI lives in X |
| Corporate cards | **Medium** | LOS twirp endpoints + CDN bundles |
| Tax payments, accounting, insights (X), "business banking" module | **None/Weak** | only marketing strings; InsightX is PG analytics |

### 5.2 Probable repos / services referenced but not in these two repos

| Name (probable) | Signal source | Confidence |
|---|---|---|
| `x` (RazorpayX SPA; exposes `connectedX`, `BankingTopNavItems`, `x.remoteEntry.js`, `pgClient.js`) | `federatedRuntimeImport.ts:9-12`, `generateXRemoteUrlsScript.tsx:10` ("X file: src/js/bootstrap.ts"), `index1.blade.php:23` — present in clone root as `x/` | OBS |
| `frontend-universe` / `@razorpay/universe-cli` | READMEs, `package.json:43` | OBS (github.com/razorpay/frontend-universe) |
| Opfin / payroll (`payroll.razorpay.com`, `opfin.np.razorpay.in`) | `config/auth.php:39-42`, env `OPFIN_REDIRECT_URL` | OBS host; repo name INF `opfin` |
| Capital LOS (`rzp.capital.los.*` twirp), `capital_collections`, `capital_cards`, capital-onboarding, capital-es | `apps/capital/.../api.ts:5-7`, `loans-collections/api.js:11`, `GenerateTokenController.php:30`, `generateXRemoteUrlsScript.tsx:70-80`, `dashboard-core.config.js:42` | OBS paths; repo names INF |
| `accounts-receivable` service (`accounts-receivable.razorpay.com`, `*.concierge.stage.razorpay.in`) | `x-accounts-receivable/src/shared/globals.js:8-14` | OBS host |
| `vendor-experience` (`/vendor_experience/vendor/*`) and `vendor-payments` (`/vendor-payments/*`) | `x-vendor-portal` BASE_PATH; `x-invoice-approval/data.ts:19`; both dirs exist in clone root | OBS path; mapping INF |
| `ufh` (`/ufh/files/upload`) | `vendorApiSlice.ts:69`; `dashboard-core.config.js:19` | OBS |
| UCS / ui-config-service (navigation_shell) | `dashboard/CLAUDE.md:184`, `dashboard-core.config.js:32` | OBS |
| Splitz | `config/splitz.php` | OBS |
| Bank co-branded X hosts (`hdfc|kotak|icicibank|...razorpay.com`) and `*-accounts.razorpay.com` (USL) | `.env.production:14,334-379`, `User/Constants.php:364-405` | OBS hosts; service INF (accounts/USL) |
| `insightx` (PG analytics, Superset) | `.env.production:297` | OBS host; not X |

### 5.3 Unknowns

- X-side role catalogue and permission model behind `banking_role` (only `owner` observed in dashboard).
- Which dashboard PHP controllers implement `partner_lms/*` and whether they proxy to `api` or `banking-accounts` (not inspected).
- Whether `pgClient.js` / iframe embedding of PG inside X (`index1.blade.php`) is still live or dormant.
- Actual UCS `navigation_shell` production rules (only the mock/config in `apps/shell/src/server/components/config.js` was read).
- Teams for `x-invoice-approval`, `x-accounts-receivable` (CODEOWNERS paths are misspelled → only fallback owners apply).
- Tax-payments product routing (no code path found in either repo).
- `apps/payroll-app` intended scope (scaffold only).
