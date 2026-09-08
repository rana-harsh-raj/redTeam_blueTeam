# xperience — route/client/domain audit (read-only)

Clone: `/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture/xperience` (module `github.com/razorpay/xperience`, `xperience/go.mod:1`; go 1.26 `go.mod:3`). Git depth = 1 commit (`8872956 repoowners-file-manager[bot] 2026-06-11`), so no history evidence.

Evidence tags: **OBS** = observed literally in clone; **INF** = inferred (AI-generated skill docs, naming); **UNK** = unknown.

## 0. Key structural facts (OBS)

| Fact | Evidence |
|---|---|
| Server = gRPC + grpc-gateway REST via `goutils/grpcserver`; single `runtime.ServeMux`; no gin/chi/gorilla | `xperience/internal/boot/handlers.go:337-345` `createGrpcServer`, `:347-457` `registerGrpcHttpHandlers`, `:459-485` `registerGrpcHandlers` |
| Listen: gRPC `0.0.0.0:9080`, HTTP `0.0.0.0:9081`, Internal `0.0.0.0:9082` | `xperience/config/default.toml:6-9` `[app.network]` |
| **Proto & generated `rpc/` are NOT in clone** — `.gitignore` excludes `proto` and `rpc`; protos fetched from `https://github.com/razorpay/proto` sparse-checkout module `xperience/` at commit `f7a8c2c5117d10a611a33aa1502f22c13807c31f` | `xperience/.gitignore:28-33`; `xperience/Makefile:30,38,87-97`; `xperience/scripts/proto_modules:12`; `xperience/buf.yaml:2` (`buf.build/razorpay/xperience`) |
| buf plugins: go, go-grpc, grpc-gateway, twirp, openapiv2 (docs/ also gitignored, empty) | `xperience/buf.gen.yaml:11-34`; `xperience/.gitignore:16` |
| Twirp generated but **no twirp mux registered** (grep `twirp` in boot/controller = 0 hits) | `xperience/internal/boot/handlers.go` (absence) |
| Only literal HTTP paths in Go: `/v1/health`, `/v1/ping` (log-skip list), `/commit.txt`, workflow callback URIs `/v1/bulk-payouts/callbacks/workflow-state` & `/v1/bulk-payouts/callbacks/workflow-action` | `xperience/internal/interceptors/logging_interceptor.go:19-20`; `xperience/internal/boot/handlers.go:365`; `xperience/internal/app/client/workflows/constants.go:24-25`; `xperience/internal/tests/e2e/health_test.go:22` |
| Auth dispatch is by **gRPC full-method name → route key → auth class** (`DirectAuth`/`MerchantAuth`/`InternalAuth`/`ProxyAuth`/`AdminAuth`) | `xperience/internal/interceptors/authz_interceptor.go:55-79`; `xperience/internal/routes/routes.go:157-301` `RpcRouteMap`, `:304` `DirectAuth`, `:311` `ProxyAuth`, `:353` `MerchantAuth`, `:358` `AdminAuth`, `:367` `AdminAuthWithAuthzEnforcement` (EMPTY), `:369` `InternalAuth`, `:458` `InternalAppRouteMap` |
| Ownership: `@razorpay/xperience-devs`; BU RazorpayX / Business Banking / pod **Payouts Platform**; EM abhishek.sk@razorpay.com | `xperience/.github/CODEOWNERS:47`; `xperience/.github/repo_owners.json:2-11` |
| Image registry `c.rzp.io` (Harbor); devspace image `c.rzp.io/razorpay/xperience:api-devstack`, namespace `xperience` | `xperience/.github/workflows/ci.yml:120-125`; `xperience/devspace.yaml:6,44` |

### Auth classes (OBS, `xperience/internal/interceptors/authz_interceptor.go`)
| Class | Mechanism | Lines |
|---|---|---|
| DirectAuth | none (health only) | `:64-65`; routes `routes.go:304-308` |
| MerchantAuth | passport must yield merchant id (`helpers.GetMerchantID`) | `:254-257`; `xperience/internal/common/helpers/passport.go:66` |
| InternalAuth | Basic auth against `ApiAuth`/`WorkflowsAuth`/`VendorPaymentsAuth`/`BatchAuth`/`WebhookCallbackAuth` creds, then per-app allow-list `InternalAppRouteMap[originService]` | `:223-252`; creds `xperience/config/default.toml:48-67` (usernames `api`,`workflows`,`vendor_payments`,`batch`,`webhooks`) |
| ProxyAuth | Edge passport JWT (`HeaderKeyPassportJWTV1`) → AuthZ enforcer `Enforce(roles, sub=user id, org="razorpayx", originService="x_platform", resource=permission, action="post")` | `:163-221`; `:26-27`; JWT extraction `xperience/internal/boot/handlers.go:505`; `xperience/internal/interceptors/passport_interceptor.go:40` |
| AdminAuth | passport consumer type must be admin (`GetAdminID`); AuthZ enforcement only if route in `AdminAuthWithAuthzEnforcement` — **which is empty**, so admin routes have no permission check | `:89-100`; `xperience/internal/common/helpers/passport.go:125-136`; `routes.go:367` |
| AuthZ config | `[AuthZ] Environment="stage" Mock=true` in default | `xperience/config/default.toml:91-94` |

Permission strings (`xperience/internal/routes/permission.go:18-32`): `create_payout`, `view_all_payouts`, `view_payout`, `approve_payout`, `reject_payout`, `self_serve_workflow_config`, `view_cost_center`, `create_cost_center`, `upload_merchant_details`, `paywall_upload_admin`. Route→permission map `permission.go:36-77`.

## 1. Route table

Because proto is absent, the authoritative column is the **gRPC full method** (OBS from `RpcRouteMap`). HTTP method/path column is **INF** from two AI-generated docs that *disagree*:
- Doc A `xperience/.agents/skills/repo-skill/modules/integration/apis/*.md` → all `POST /v1/<pkg>/<snake_method>` (e.g. `POST /v1/bulkpayouts/create`, `POST /v1/budgets/fetch_by_id`).
- Doc B `xperience/.agents/skills/repo-skill/modules/technical/api/grpc.md:80-235` → REST-style (`GET /v1/bulk-payouts/{id}`, `POST /v1/bulk-payouts/callbacks/workflow-state`, `PATCH /v1/budgets/{id}`, `POST /v1/petty-cash`, `GET /v1/cost-centers`).
Doc B is corroborated by code for the two workflow callbacks (`client/workflows/constants.go:24-25`) and by the collection-agent test CSV (`/v1/collection-agent/callback`, `GET /v1/collection-agent/conversations?skip=&count=` — `xperience/collection_agent_test_cases.csv:2`). Doc B also lists methods that do **not** exist in `RpcRouteMap` (e.g. `BatchUploadCallback`, `AddToHierarchy`, `ProcessWebhook`), so treat it as partially stale. True paths = UNK until `razorpay/proto` `xperience/` is inspected.

Column key: Auth = class from `routes.go`; Perm = `RoutePermissionMap`; App = allowed basic-auth origin app(s) for InternalAuth (`routes.go:458-561`). Handler file:line = controller method. HTTP handler registration for every service: `xperience/internal/boot/handlers.go:381-455`; gRPC server registration `:463-482`.

### 1.1 Health (DirectAuth)
| gRPC method (OBS) | HTTP (INF) | Handler | Auth | routes.go |
|---|---|---|---|---|
| `/rzp.xperience.health.v1.HealthCheckAPI/Check` | `GET /v1/health` (OBS via e2e `health_test.go:22`) | `pkg/health.Server` `handlers.go:463` | Direct | `:158` |
| `/rzp.xperience.health.v1.HealthCheckAPI/Ping` | `GET /v1/ping` (OBS `logging_interceptor.go:20`) | same | Direct | `:159` |
| — | `GET /commit.txt` (OBS) | inline `handlers.go:364-369` | none | — |

### 1.2 Bulk payouts (domain: payouts / bulk uploads / approvals)
Internal API `BulkPayoutAPI` (InternalAuth, App=`api` except callbacks=`workflows`) and Edge API `BulkPayoutEdgeAPI` (ProxyAuth w/ permission). Controller: `xperience/internal/app/controller/bulkpayouts/controller.go` (internal) and `edge-controller.go` (edge). Service `xperience/internal/app/service/bulkpayouts/service.go`.

| Method (both `BulkPayoutAPI` and `BulkPayoutEdgeAPI`) | HTTP (INF, Doc B) | Internal Auth/App | Edge Perm | routes.go (int/edge) | Controller line (int/edge) |
|---|---|---|---|---|---|
| MetaSummary | `GET /v1/bulk-payouts/_meta/summary` | Internal/api | view_payout | `:161`/`:267` | `controller.go:36`/`edge-controller.go:37` |
| WorkflowSummary | `GET /v1/bulk-payouts/workflow/summary` | Internal/api | approve_payout | `:162`/`:268` | `:441`/— |
| OwnerBulkReject | `POST /v1/bulk-payouts/reject/owner` | Internal/api | self_serve_workflow_config | `:163`/`:269` | `:280` |
| FetchPending | `POST /v1/bulk-payouts/pending` | Internal/api | self_serve_workflow_config | `:164`/`:270` | `:306` |
| Create | `POST /v1/bulk-payouts` (Doc A: `POST /v1/bulkpayouts/create`) | Internal/api | create_payout | `:165`/`:271` | `:248` / edge calls `CreateV2` `edge-controller.go:239`, `service.go:1459` |
| GetRows | — | Internal/api | create_payout | `:166`/`:272` | `:363` |
| BulkApprove (OTP) | `POST /v1/bulk-payouts/approve` | Internal/api | approve_payout | `:167`/`:273` | `:415` |
| BulkReject | `POST /v1/bulk-payouts/reject` | Internal/api | reject_payout | `:168`/`:274` | `:387` |
| Process | — | Internal/api | create_payout | `:169`/`:275` | `:465` |
| FetchAll | `GET /v1/bulk-payouts/fetch/all` | Internal/api | view_all_payouts | `:170`/`:276` | `:98` |
| FetchMy | `GET /v1/bulk-payouts/fetch/me` | Internal/api | view_all_payouts | `:171`/`:277` | `:132` |
| FetchMyApprovals | `GET /v1/bulk-payouts/fetch/my-approvals` | Internal/api | approve_payout | `:172`/`:278` | `:168` |
| FetchMultiple | `GET /v1/bulk-payouts` | Internal/api | view_all_payouts | `:173`/`:279` | `:208` |
| FindByID | `GET /v1/bulk-payouts/{id}` | Internal/api | view_payout | `:174`/`:280` | `:62` |
| WorkflowActionCallback | `POST /v1/bulk-payouts/callbacks/workflow-action` (OBS as client const) | Internal/**workflows** (both int & edge variants) | — (edge variant in InternalAppRouteMap[workflows], not ProxyAuth) | `:175`/`:281`; app map `:539-546` | `:488` |
| WorkflowStateCallback | `POST /v1/bulk-payouts/callbacks/workflow-state` (OBS as client const) | Internal/workflows | — | `:176`/`:282` | `:338` |
| Migrate | `POST /v1/bulk-payouts/migrate` | **Internal (api) AND AdminAuth** (`BulkPayoutsMigrate` listed in both `:362` and `:386`; interceptor checks InternalAuth first `authz_interceptor.go:68`) | self_serve_workflow_config (mapped, unenforced) | `:177`/`:283` | `:508` |
| MigrateRaw | `POST /v1/bulk-payouts/migrate-raw` | Internal/api | (edge: none in map → ProxyAuth would fail "permission unknown") | `:178`/`:284` | `:533` |

Note (OBS): `BulkPayoutsMigrateRawEdge`, `BulkPayoutsWorkflowStateCallbackEdge`, `BulkPayoutsWorkflowActionCallbackEdge` are in `ProxyAuth` (`routes.go:318,323,329`) but have no `RoutePermissionMap` entry → `handleProxyAuth` returns "permission unknown for RPC" (`authz_interceptor.go:164-170`). The two callback edges are *also* in `InternalAppRouteMap[workflows]` but not in `InternalAuth` list, so the interceptor never reaches that branch. INF: edge callback/migrate-raw routes are effectively dead.

### 1.3 Paywall (domain: merchant subscription / plan billing via Razorpay Payments)
Controller `xperience/internal/app/controller/paywall/controller.go` + `edge-controller.go`; service `xperience/internal/app/service/paywall/service.go`.

| gRPC method | HTTP (INF Doc A) | Auth | Perm | routes.go | Controller |
|---|---|---|---|---|---|
| `PaywallAPI/GetMerchantDetails` | `POST /v1/paywall/merchant_details/{merchant_id}` | Proxy | view_payout | `:180` | `controller.go:36` |
| `PaywallAPI/GetMerchantPlanStatus` | `POST /v1/paywall/merchant_plan_status/{merchant_id}` | Proxy | view_payout | `:181` | `:89` |
| `PaywallAPI/GetMerchantPlanInfo` | `POST /v1/paywall/merchant_plan_info/{merchant_id}` | Proxy | view_payout | `:182` | `:131` |
| `PaywallAPI/GetPaymentStatus` | `POST /v1/paywall/payment_status` | Proxy **and** InternalAppRouteMap[api] (`:530`) but not in `InternalAuth` list → Proxy | view_payout | `:183` | `:225` |
| `PaywallEdgeAPI/GetMerchantDetails` | same | Proxy | view_payout | `:185` | `edge-controller.go:32` |
| `PaywallEdgeAPI/GetMerchantPlanStatus` | same | Proxy | view_payout | `:186` | `:57` |
| `PaywallEdgeAPI/GetMerchantPlanInfo` | same | Proxy | view_payout | `:187` | `:83` |
| `PaywallEdgeAPI/UploadMerchantDetailsAdmin` | `POST /v1/paywall/upload_merchant_details` | Admin (no authz enforcement) | paywall_upload_admin (unenforced) | `:188`; `:363` | `:109`; also `controller.go:173` |
| `PaywallEdgeAPI/GetPaymentStatus` | — | Proxy | view_payout | `:189` | `:148` |
| `PaywallEdgeAPI/UpdatePaymentStatus` | `PATCH /v1/paywall/payment-status` (Doc B) | Admin | — | `:190`; `:364` | `:177` |

Doc A mentions `POST /v1/paywall/webhook_subscription` / `webhook_payment` (`paywall-v1.md:339,396`) — **no such RPC in `RpcRouteMap`** → INF stale/unimplemented.

### 1.4 Cost centers (domain: expense allocation)
Controller `xperience/internal/app/controller/costcenters/` (internal + edge); service `service/costcenters/service.go`.

| gRPC method | HTTP (INF) | Auth/App | Perm | routes.go |
|---|---|---|---|---|
| `CostCenterAPI/Create` | `POST /v1/costcenters/create` (A) / `POST /v1/cost-centers` (B) | Internal/api | — | `:192` |
| `CostCenterAPI/FetchMultiple` | `.../fetch_multiple` / `GET /v1/cost-centers` | Internal/api | — | `:193` |
| `CostCenterAPI/FetchMultipleInternal` | `.../fetch_multiple_internal` | Internal/**vendor_payments** | — | `:194`; `:548-552` |
| `CostCenterAPI/FindByID` | `.../find_by_id` / `GET /v1/cost-centers/{id}` | Internal/api | — | `:195` |
| `CostCenterAPI/Update` | `.../update` / `PATCH /v1/cost-centers/{id}` | Internal/api | — | `:196` |
| `CostCenterAPI/Disable` | `.../disable` / `POST /v1/cost-centers/{id}/disable` | Internal/api | — | `:197` |
| `CostCenterEdgeAPI/Create` | — | Proxy | create_cost_center | `:286` |
| `CostCenterEdgeAPI/FetchMultiple` | — | Proxy | view_cost_center | `:287` |
| `CostCenterEdgeAPI/FindByID` | — | Proxy | view_cost_center | `:288` |
| `CostCenterEdgeAPI/Update` | — | Proxy | create_cost_center | `:289` |
| `CostCenterEdgeAPI/Disable` | — | Proxy | create_cost_center | `:290` |
| `CostCenterEdgeAPI/CreateAdmin` | — | Admin | — | `:291`; `:359` |
| `CostCenterEdgeAPI/UpdateAdmin` | — | Admin | — | `:292`; `:360` |
| `CostCenterEdgeAPI/FetchMultipleAdmin` | — | Admin | — | `:293`; `:361` |

### 1.5 Reports
| gRPC method | HTTP | Auth/App | routes.go | Handler |
|---|---|---|---|---|
| `/rzp.xperience.reports.v1.ReportsAPI/SendPendingEntitiesEmail` | UNK (`//TODO: change http route in proto` `routes.go:199`) | Internal/api | `:199` | `controller/reports/controller.go:26`; service `service/reports/service.go:55,86,210,238` (apiservice settings+users, workflows ListWorkflow, stork SendEmail) |

### 1.6 Groups / Group types / User details / User-group mapping (domain: team & org hierarchy, Postgres ltree)
Repos on **Postgres** (`handlers.go:333-337` ugm/gt/grp/ud repos). Controllers under `internal/app/controller/{groups,grouptypes,userdetails,usergroupmapping}`.

| gRPC method | HTTP (INF Doc A) | Auth/App | Audit | routes.go |
|---|---|---|---|---|
| `groups.v1.GroupsAPI/Create` | `POST /v1/groups/create` | Internal/api | Middleware | `:201` |
| `GroupsAPI/FetchMultiple` | `.../fetch_multiple` | Internal/api | | `:202` |
| `GroupsAPI/FetchMultipleInternal` | `.../fetch_multiple_internal` | Internal/vendor_payments | | `:203` |
| `GroupsAPI/FetchById` | `.../fetch_by_id` | Internal/api | | `:204` |
| `GroupsAPI/FetchByIdInternal` | `.../fetch_by_id_internal` | Internal/workflows | | `:205` |
| `GroupsAPI/Update` | `.../update` | Internal/api | Middleware | `:206` |
| `GroupsAPI/CreateInternal` | `.../create_internal` | Internal/**batch** | Service | `:207`; `:554-557` |
| `GroupsAPI/UpdateGroupHierarchy` | `.../update_hierarchy` | Internal/api | | `:208` |
| `grouptypes.v1.GroupTypesAPI/Create` | UNK | Internal/api | Middleware | `:210` |
| `GroupTypesAPI/FetchMultiple` | UNK | Internal/api | | `:211` |
| `GroupTypesAPI/FetchMultipleInternal` | UNK | Internal/vendor_payments | | `:212` |
| `userdetails.v1.UserDetailsAPI/DeleteUserFromMerchant` | UNK | Internal/api | Middleware | `:214` |
| `UserDetailsAPI/EditUserDetails` | UNK | Internal/api | Middleware | `:215` |
| `UserDetailsAPI/FetchMultipleUserDetails` | UNK | Internal/api | | `:216` |
| `UserDetailsAPI/GetUserDetails` | UNK | Internal/api | | `:217` |
| `UserDetailsAPI/GetUserDetailsInternal` | UNK | Internal/workflows | | `:218` |
| `UserDetailsAPI/CreateUserDetails` | UNK | Internal/api | Middleware | `:219` |
| `UserDetailsAPI/CreateUserDetailsInternal` | UNK | Internal/api + batch | Service | `:220`; `:495`,`:556` |
| `UserDetailsAPI/BulkCreateUserDetails` / `BulkCreateUserDetailsRaw` | UNK | Internal/api | Middleware | `:221-222` |
| `UserDetailsAPI/SyncUserDetails` | UNK | Internal/api | Middleware | `:223` |
| `UserDetailsAPI/UserInviteAccepted` | UNK | Internal/api | Service | `:224` |
| `UserDetailsAPI/FetchUserReporteesDetails` | UNK | Internal/api | | `:225` |
| `UserDetailsAPI/ResendUserInvite` / `CancelUserInvite` | UNK | Internal/api | Middleware | `:226-227` |
| `usergroupmapping.v1.UserGroupMappingAPI/AddUserToGroup` / `RemoveUserFromGroup` | UNK | Internal/api | Middleware | `:229-230` |
| `UserGroupMappingAPI/ListUsersOfGroup` / `ListGroupsOfUser` | UNK | Internal/api | | `:231-232` |

User-details routes call the API monolith for invites/users (`apiservice` `xperience_user_invitation`, `users_internal/%s`, `internal/merchants/%s/users` — see §2) and Raven OTP verify (`service/userdetails/service.go`).

### 1.7 Budgets / Petty cash / Expense categories (domain: expense management)
| gRPC method | HTTP (INF A / B) | Auth/App | Audit | routes.go |
|---|---|---|---|---|
| `budgets.v1.BudgetsAPI/FetchMultiple` | `POST /v1/budgets/fetch_multiple` / `GET /v1/budgets-all` | Internal/api | | `:234` |
| `BudgetsAPI/FetchMultipleExpense` | `.../fetch_multiple_expense` / `GET /v1/budgets-expense` | Internal/api | | `:235` |
| `BudgetsAPI/FetchBugdetExpenseByID` (sic) | `.../fetch_budget_expense_by_id` | Internal/api | | `:236` |
| `BudgetsAPI/FetchByID` | `.../fetch_by_id` / `GET /v1/budgets-all/{id}` | Internal/api | | `:237` |
| `BudgetsAPI/FetchByIDOwner` | `.../fetch_by_id_owner` / `GET /v1/budgets/{id}` | Internal/api | | `:238` |
| `BudgetsAPI/FetchMultipleOwner` | `.../fetch_multiple_owner` / `GET /v1/budgets` | Internal/api | | `:239` |
| `BudgetsAPI/GetBalanceID` | `.../get_balance_id` / `GET /v1/petty-cash-balance` | Internal/api | | `:240` |
| `BudgetsAPI/UpdateBalanceID` | `.../update_balance_id` / `PATCH /v1/petty-cash-balance` | Internal/api | Middleware | `:241` |
| `BudgetsAPI/GetSummary` / `GetSummaryAll` | `.../get_summary[_all]` | Internal/api | | `:242-243` |
| `BudgetsAPI/Create` | `.../create` / `POST /v1/budgets` | Internal/api | Middleware | `:244` |
| `BudgetsAPI/Update` | `.../update` / `PATCH /v1/budgets/{id}` | Internal/api | Middleware | `:245` |
| `BudgetsAPI/ProcessActivationCron` / `ProcessExpiryCron` / `ProcessRecurringCron` | `.../process_*_cron` | Internal/api | Service | `:246-248` |
| `pettycash.v1.PettyCashAPI/Create` (OTP) | `POST /v1/pettycash/create` / `POST /v1/petty-cash` | Internal/api | Middleware | `:249` |
| `PettyCashAPI/UpdateByID` / `UpdateBulk` | `.../update_by_id`, `.../update_bulk` | Internal/api | Middleware | `:250-251` |
| `PettyCashAPI/FetchMultipleSelf` / `FetchByIDOwner` / `FetchMultipleBudgetOwner` / `FetchMultiple` / `FetchByID` | `.../fetch_*` | Internal/api | | `:252-256` |
| `PettyCashAPI/StatusCallback` (payout status) | `.../status_callback` / `POST /v1/petty-cash/callbacks/status` | Internal/api | Service | `:257` |
| `PettyCashAPI/HardUpdateStatus` | `.../hard_update_status` | Internal/api | Middleware | `:258` |
| `expensecategories.v1.ExpenseCategoriesAPI/Create` / `FetchMultiple` / `Update` / `Delete` | UNK | Internal/api | Middleware (C/U/D) | `:260-263` |

Petty cash creates payouts via monolith `composite_payout_internal` and attaches files via `payouts_internal/attachments` (`xperience/internal/app/core/pettycash/core.go`; client consts `client/apiservice/create_petty_cash_payout.go:15`, `update_payout_attachments.go:15`). Budgets send email via Stork (`service/budgets/service.go`).

### 1.8 Payout downtimes (MerchantAuth; edge-only API)
| gRPC method | HTTP | Auth | routes.go | Handler | Downstream |
|---|---|---|---|---|---|
| `payoutdowntime.v1.PayoutDowntimeEdgeAPI/GetAllDowntimes` | UNK | Merchant (`routes.go:353-356`) | `:295` | `controller/payoutdowntime/controller.go:33` | banking-account `business/%s/banking_accounts` + FTS `v1/downtimes` (`service/payoutdowntime/service.go:63,109`) |
| `PayoutDowntimeEdgeAPI/GetDowntimeById` | UNK | Merchant | `:296` | `:72` | FTS `v1/downtimes/%s` (`service.go:180`) |

### 1.9 Statements (account statements / ledger view)
| gRPC method | HTTP | Auth/App | routes.go | Handler | Downstream |
|---|---|---|---|---|---|
| `statements.v1.StatementsSearchAPI/SearchStatements` | `POST /v1/statements/search` (both docs agree) | Internal/api | `:297` | `controller/statements/controller.go:28` | Elasticsearch alias `x_account_statements_source_events` (`service/statements/service.go:24,151`) **or** XAS `v1/account_statements` (`service.go:265-266`), enriched with monolith `payouts_internal/%s` and CFA `v1/contacts/%s`, `v1/fund_accounts/%s` |

### 1.10 Collection agent (AI/voice collections conversations — INF from `transcript_data`)
| gRPC method | HTTP (OBS from test CSV) | Auth/App | Perm | routes.go | Handler |
|---|---|---|---|---|---|
| `collectionagent.v1.CollectionAgentAPI/Callback` | `POST /v1/collection-agent/callback` | Internal/**webhooks** (`routes.go:558-560`) | — | `:299` | `controller/collectionagent/controller.go:28`; service enriches invoice via monolith `invoices_internal/%s` (`service/collectionagent/service.go:80-92`); table `entity_conversations` (`repository/collectionagent/model.go:11`), id prefix `ec_conv_` |
| `CollectionAgentAPI/ListConversations` | `GET /v1/collection-agent/conversations?skip&count` | Proxy | view_payout | `:300`; perm `permission.go:76` | `controller.go:39` |
| `CollectionAgentAPI/ListConversationsInternal` | UNK | Internal/api | — | `:301` | `controller.go:57` |

Who posts the callback (which "collection agent" vendor/service) = UNK; only basic-auth user `webhooks` (`default.toml:65-67`).

### 1.11 Not present (OBS absence in `RpcRouteMap`)
No routes for: payout links, contacts/fund-account CRUD (only read-through), balances API (client exists, unused), cards, payroll, tax payments, vendor payments (only as *caller* app), invoices (only enrichment), onboarding/current account, merchant settings, accounting integrations, capital, developer/API keys. `.cursorrules_telemetry_autonomous.md` mentions `/v1/payouts`, `/v1/transfer` but is a generic org-wide telemetry playbook, not xperience routes (`xperience/.cursorrules_telemetry_autonomous.md:1-20`).

## 2. Downstream clients

All HTTP clients built through `pkg/httpClient.NewClient(&cfg.<Key>, name)` with Heimdall+Hystrix (`xperience/pkg/httpClient/client.go:40`; config struct `xperience/internal/config/config.go:32-45,51`). Providers registered in `xperience/internal/provider/*_client.go`; getters `xperience/internal/provider/getter.go:124-249`. Instantiation `xperience/internal/boot/handlers.go:200-224` (`batch.NewApiCaller` called twice `:208,:219`).

| Client pkg (`xperience/internal/app/client/…`) | Config key | default.toml host | stage.toml | prod.toml | Paths called (OBS) | Used by routes/services |
|---|---|---|---|---|---|---|
| `apiservice` (Razorpay API monolith, basic auth user `rzp_live`) | `[ApiClient]` `default.toml:96-115`; `InternalBaseURL` used by `pkg/httpClient/base.go:91,108` | `https://api-web.dev.razorpay.in/v1` | `https://api-web.dev.razorpay.in/v1`, internal `https://api-web.int.dev.razorpay.in/v1` (`stage.toml:77-78`) | `https://api-graphql.razorpay.com/v1`, internal `https://prod-api-int.razorpay.com/v1` (`prod.toml:60-61`) | `users_internal/%s`, `merchants/%s/internal-users`, `internal/merchants/%s/users`, `xperience_user_invitation[/%s[/resend]]`, `composite_payout_internal`, `payouts_internal/attachments`, `payouts_internal/%s`, `contacts_internal/%s`, `fund_accounts_internal/%s`, `invoices_internal/%s`, `settings_internal/%s/%s` (files `client/apiservice/*.go` const lines 15-27) | userdetails, pettycash, bulkpayouts (SearchMerchantUsers), costcenters, paywall, statements, collectionagent, reports, `common/helpers/helpers.go` |
| `razorpaypayment` (public Razorpay Payments API) | `[RazorpayPaymentClient]` `default.toml:117-136` | `https://api.razorpay.com/v1` | same (`stage.toml:99`) | same (`prod.toml:308`) | `plans`, `orders`, `orders/%s/payments`, `payments/%s`, `subscriptions[/%s]`, `invoices?subscription_id=%s` (`client/razorpaypayment/*.go:16-18`) | paywall service (`service/paywall/service.go`) |
| `raven` (OTP) | `[RavenClient]` `default.toml:138-158` | `env\|RAVENCLIENT_BASEURL` | `https://raven.dev.razorpay.in` (`stage.toml:120`) | env | `v1/otp/generate`, `v1/otp/verify` (`client/raven/generate_otp.go:15`, `verify_otp.go:16`) | bulkpayouts BulkApprove, pettycash Create, userdetails |
| `stork` (email/SMS/message, twirp) | `[StorkClient]` `default.toml:160-179` | env `STORKCLIENT_BASEURL` | `https://stork.dev.razorpay.in` (`stage.toml:142`) | env | `twirp/rzp.stork.email.v1.EmailAPI/Send`, `twirp/rzp.stork.sms.v1.SMSAPI/Send`, `twirp/rzp.stork.message.v1.MessageAPI/Create` (`client/stork/send_*.go:16`) | bulkpayouts, budgets, reports |
| `workflows` (approval workflows, twirp) | `[WorkflowsClient]` `default.toml:181-200` | env `WORKFLOWSCLIENT_BASEURL` | `https://workflows.int.dev.razorpay.in` (`stage.toml:163`) | env | `twirp/rzp.workflows.workflow.v1.WorkflowAPI/{Create,Get,ListPending}`, `twirp/rzp.workflows.action.v1.ActionAPI/{Create,CreateDirectOnWorkflow}`, `twirp/rzp.workflows.config.v1.ConfigAPI/List`; callback URIs `/v1/bulk-payouts/callbacks/workflow-{state,action}`; service ids `rx_live`,`xperience`; config type `payout-approval` (`client/workflows/constants.go:15-25,43-44`, `create_workflow.go:14`, `fetch_workflow.go:15`, `list_workflow.go:14`, `get_configs.go:14`) | bulkpayouts, reports |
| `batch` (batch/file processing) | `[BatchClient]` `default.toml:202-221` | env `BATCHCLIENT_BASEURL` | `https://batch.dev.razorpay.in` (`stage.toml:184`) | env | `batch`, `batch/%s`, `batch-entry?batchId=…`, process (`client/batch/create_batch.go:15`, `fetch_batch.go:17`, `fetch_rows.go:15`, `process_batch.go:25`); headers `X-Entity-Id`,`X-Creator-Id`,`X-Creator-Type` (`constants.go:16-19`) | bulkpayouts |
| `ufh` (file hub) | `[UfhClient]` `default.toml:223-242` | `https://ufh.dev.razorpay.in` | — | `https://ufh.razorpay.com` (`prod.toml:85`) | `v1/files/upload`, `v1/files/%s/signed_url` (`client/ufh/upload_file.go:16`, `get_signed_url.go:17`) | bulkpayouts |
| `splitz` (experiments, twirp) | `[SplitzClient]` (stage/prod only) + `[Splitz.ExperimentIds].BulkPayoutsExperiment="PEVIqVHIxBF77O"` `default.toml:261-264` | — | `https://splitz.dev.razorpay.in` (`stage.toml:210`) | `https://splitz.razorpay.com` (`prod.toml:200`) | `twirp/rzp.splitz.evaluate.v1.EvaluateAPI/Evaluate` (`client/splitz/experiment_evaluator.go:13`) | bulkpayouts `service.go:2847` |
| `banking` (banking-account service) | `[BankingAccountClient]` `default.toml:266-284` (ApiToken auth) | `https://banking-account.dev.razorpay.in/v0.2` | not overridden | **not in prod.toml** (UNK prod host) | `merchant/%s/banking_account_by_account_number/%s`, `business/%s/banking_accounts` (`client/banking/constants.go:7,10`) | payoutdowntime |
| `fts` (fund transfer service) | `[FTSLiveClient]` `default.toml:308-327` | `https://fts-live.dev.razorpay.in` | — | `https://fts-live.razorpay.com` (`prod.toml:221`) | `v1/downtimes`, `v1/downtimes/%s` (`client/fts/constants.go:4-5`) | payoutdowntime |
| `xaccountstatements` (XAS) | `[XASClient]` `default.toml:329-348` | `https://x-account-statements.dev.razorpay.in` | same (`stage.toml:251`) | `https://x-account-statements-ext.razorpay.com` (`prod.toml:242`) | `v1/account_statements` (`client/xaccountstatements/constants.go:3`) | statements |
| `cfa` (contacts & fund accounts) | `[CFAClient]` (stage/prod/slit only; absent from default.toml) | — | `https://cfa.dev.razorpay.in` (`stage.toml:293`) | `https://cfa-live-int.razorpay.com` (`prod.toml:284`) | `v1/contacts/%s`, `v1/fund_accounts/%s` (`client/cfa/constants.go:6,9`) | statements |
| `payouts` (payouts service, "PSClient") | `[PSClient]` `default.toml:350-372` (typo section `[PSClientt.ConnPoolConfig]` `:353`) | `https://payouts.dev.razorpay.in` | same (`stage.toml:272`) | `https://payouts.razorpay.com` (`prod.toml:263`) | `v1/payouts/xperience_get_payout/%s` (`client/payouts/constants.go:5`) | **no call sites** in service/core (OBS: grep `payouts.GetApiCaller` = 0) → built but unused |
| `xbalances` | `[XBalancesClient]` `default.toml:286-305` | `https://x-balances.dev.razorpay.in` | same (`stage.toml:230`) | **not in prod.toml** | `%s/v1/balances/%s` (`client/xbalances/constants.go:3`) | **no call sites** (OBS) → unused |
| `dcs` (dynamic config, `goutils/dcs`) | `[DcsConfig]` user `xperience`, `Env` (`default.toml:248-251`); provider `internal/provider/dcs_client.go:30-59` | env-based, no host in TOML | | | key `rzp/x/merchant/xperience/BulkPayoutSettings`, feature `bulk_payout_approval` (`client/dcs/features/features.go:12,15`) | bulkpayouts |
| Elasticsearch (`olivere/elastic/v7`) | `[ESConfig].Addresses` | — | `https://vpc-common-es7-fd5jblmsbmhirqdm6xdqmremjy.ap-south-1.es.amazonaws.com` (`stage.toml:315`) | `https://vpc-prod-razorpayx-es-a4zsnxxvhoxdgc22zmutxnh4je.ap-south-1.es.amazonaws.com` (`prod.toml:305`) | alias `x_account_statements_source_events` | statements |
| Passport JWKS (`goutils/passport/v4`) | `[PassportConfig].JwksHost`; identifiers `apiv1`, `edgev1` (`default.toml:82-89`) | `https://edge-base.dev.razorpay.in/` | same | `https://edge-admin-internal.razorpay.com` (`prod.toml:51`) | — | all Proxy/Admin/Merchant routes |
| AuthZ (`goutils/authz` enforcer) | `[AuthZ]` ConsulToken/Environment/Mock (`default.toml:91-94`) | — | | | org `razorpayx`, originService `x_platform` | Proxy routes |
| Email links | `[EmailConfig].RxBaseUrl` `https://x.razorpay.com/` (`default.toml:246`; stage `https://x.dev.razorpay.in/` `stage.toml:74`) | | | | | notifications |
| Datastores | MySQL `xperience` db (`default.toml:16-30`), Postgres `xperience` (`:32-46`), Redis (`:75-80`) | | | | | |

## 3. Product domains served (evidence strength)

| Domain | Routes (gRPC methods) | Clients | Strength |
|---|---|---|---|
| Bulk payouts (upload/approve/reject/workflow, edge+internal) | 36 (18×2) | batch, workflows, ufh, raven, stork, splitz, dcs, apiservice | **Strong** (core of repo) |
| Expense mgmt: budgets / petty cash / expense categories | 15 + 10 + 4 = 29 | apiservice (composite payout), raven, stork | Strong |
| Org/team: groups, group types, user details, user-group mapping | 8 + 3 + 14 + 4 = 29 | apiservice (users/invites), raven | Strong |
| Cost centers (internal + edge + admin) | 6 + 8 = 14 | apiservice | Medium-strong |
| Paywall / merchant subscription billing | 10 | razorpaypayment (plans/orders/subscriptions/payments) | Medium |
| Account statements search (ES/XAS) | 1 | elasticsearch, xaccountstatements, cfa, apiservice | Medium (heavy service: `service/statements/service.go` ~900 lines) |
| Payout downtimes (merchant-facing) | 2 | fts, banking | Weak-medium |
| Collection agent conversations (AI collections, invoice-linked) | 3 | apiservice (invoices) | Weak-medium (new; test CSV present) |
| Reports (pending-entities email) | 1 | stork, workflows, apiservice | Weak |
| Balances / payouts-service passthrough | 0 | xbalances, payouts clients (unused) | Vestigial |

INF: README says the service exists "for providing experience on RazorpayX Dashboard" (`xperience/README.md:2-3`) — inbound consumer is the X dashboard via edge (ProxyAuth) and the API monolith/workflows/vendor-payments/batch via basic auth.

## 4. Referenced repos/services not in clone + owner hints

| Name | Evidence | Hint |
|---|---|---|
| `razorpay/proto` (protos, module `xperience/`) | `Makefile:30`, `scripts/proto_modules:12`, `buf.yaml:2` | needed for true HTTP paths |
| `razorpay/goutils` (authz, dcs, grpcserver, passport/v4, spine, telemetry, tracing, itf/slit) | `go.mod:23-37` | platform lib |
| `razorpay/error-mapping-module`, `razorpay/config-proto` | `go.mod:21-22` | |
| API monolith (`api-web`, `api-graphql`, `prod-api-int`) | `config/prod.toml:60-61` | Razorpay core API |
| `payouts` service (`payouts.razorpay.com`) | `prod.toml:263` | Payouts Platform pod |
| `fts` (`fts-live.razorpay.com`) | `prod.toml:221` | |
| `x-account-statements` (`x-account-statements-ext.razorpay.com`; comment "follows x-account-statements pattern" `handlers.go:340,437`) | `prod.toml:242` | sibling X repo |
| `cfa` (`cfa-live-int.razorpay.com`) | `prod.toml:284` | |
| `banking-account` service (`banking-account.dev.razorpay.in/v0.2`) | `default.toml:267` | |
| `x-balances` | `default.toml:287` | |
| `workflows`, `batch`, `raven`, `stork`, `splitz`, `ufh`, `dcs` | stage.toml hosts §2 | |
| `vendor-payments` (caller app `vendor_payments`) | `routes.go:11`, `default.toml:57-59` | consumer of cost-center/group internal APIs |
| `edge` (`edge-base`, `edge-admin-internal`) | `default.toml:83`, `prod.toml:51` | API gateway/passport |
| Owning team | `@razorpay/xperience-devs` (`CODEOWNERS:47`); pod Payouts Platform, EM abhishek.sk@razorpay.com (`repo_owners.json`) | Slack channels: **none found** in code/docs (grep `slack` = 0 hits outside vendor) |
| Telemetry namespace | `serviceNamespace = "payouts"` (`default.toml:378`) | confirms Payouts org placement |

## 5. Unknowns
1. **Exact HTTP verbs/paths** for all routes except health/ping/commit.txt/workflow callbacks/collection-agent — requires `razorpay/proto` `xperience/*.proto` (`google.api.http` annotations) at commit `f7a8c2c…`.
2. Prod hosts for Raven/Stork/Workflows/Batch (env vars `*_BASEURL`), and prod hosts for banking-account, x-balances (absent from `prod.toml`).
3. Identity of the "collection agent" caller (voice/AI vendor?) posting `Callback` under basic-auth user `webhooks`.
4. Whether `PSClient`/`XBalancesClient` are wired anywhere outside grep scope (no call sites found in `internal/app/{service,core,controller}`, `internal/common`).
5. Whether edge variants of `MigrateRaw`/workflow callbacks are reachable (no permission mapping → appear dead).
6. AuthZ policy content (Casbin/consul-backed via `goutils/authz`) — only permission *names* visible.
7. `docs/` openapiv2 output (gitignored) — would give definitive REST table if regenerated.
