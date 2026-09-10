# Lane D4 — identity, ingress, approval-workflow, bulk (M6)

Part file: `reports/domain/parts/identity-ingress.json` — **263 nodes, 342 edges, 6 families**,
0 schema violations and 0 dangling endpoints under
`python3 scripts/domain/build_graph.py --parts reports/domain/parts --out /tmp/gcheck --strict`.

Pinned READ-ONLY clones under
`/private/tmp/claude-502/-Users-rana-singh/cca012eb-.../scratchpad/rzp-payouts-architecture/`:
`api` 2d665f91 · `edge` da9ff5b2 · `terraform-kong` 8ffe965b · `authz` 8687f48f · `goutils` efc7941 ·
`workflows` 080d71a · `batch` 3839dd7 · `xperience` 8872956 · `x` 40b093f · `admin-dashboard` e29bdf9 ·
`payouts` 4bf3dbf9. Every file:line below is relative to its repo root.

---

## 1. Identity end-to-end

### 1.1 The credential classes (`identity:*`)

| id | What it is | Verified by |
|---|---|---|
| `identity:merchant-api-key` | `rzp_{live,test}_<14>` + secret, HTTP Basic | Kong `basic-auth-x` (`edge/kong-plugins/kong-plugin-basic-auth-x/kong/plugins/basic-auth-x/access.lua:366-420`); monolith `privateAuth` (`api/app/Http/BasicAuth/BasicAuth.php:896`) |
| `identity:dashboard-session` | cookie + `X-CSRF-TOKEN`, path-rewritten to `/merchant/api/{mode}/*` | SPA `x/src/js/api/api.js:63-69,170-179`; Kong `user-auth` (`edge/.../user-auth/access.lua:335-460`); monolith `proxyAuth` (`BasicAuth.php:1220,1547-1580`) |
| `identity:dashboard-otp` | OTP/2FA factor on create + approve | `api/app/Models/Payout/Service.php:1832-1840` (`create_payout`), `:1219-1226` (`approve_payout`), `:1919` (ICICI 2FA) |
| `identity:partner-oauth-token` | `Authorization: Bearer` on the `/oauth` base URL | `api/app/Http/Middleware/Authenticate.php:209-221,357-370`; Kong `jwt-x` (`edge/.../jwt-x/handler.lua:83-136`) |
| `identity:admin-token` | `X-Admin-Token` | `api/app/Http/RequestHeader.php:92`; `BasicAuth.php:1279-1334,2563-2572`; Kong admin branch `edge/.../user-auth/access.lua:366,377-410` |
| `identity:passport-jwt` | `X-Passport-JWT-V1`, RS256, kid `edgev2`, **exp = now + 300 s hardcoded** | mint `edge/.../upstream-jwt/access.lua:60-80,86-205`; verify `goutils/passport/handler.go:57-86` |
| `identity:passport-jwt-minted-by-api` | the monolith's own passport for outbound calls (issuer `api`, kid `apiv1`, 300 s) | `api/app/Http/BasicAuth/BasicAuth.php:3846-3871` |
| `identity:service-basic-auth:{batch,workflows,payouts-service,xperience,fts,vendor-payments,payout-links,merchant-dashboard,admin-dashboard}` | internal-app Basic creds toward the monolith | `api/config/applications.php:1168-1751`; allow-lists `api/app/Http/Route.php:12922-19423`; verifier `BasicAuth.php:1653-1796` |
| `identity:workflows-inbound-basic` | the WFS allow-list (12 credentials, **one shared list for all four Twirp services**) | `workflows/internal/boot/hooks/auth.go:26-82`; `workflows/config/prod.toml:185-221` |
| `identity:batch-inbound-basic` | Batch's in-memory Spring Basic users (`apiUser`, `xperienceUser`, …) | `batch/src/main/java/com/razorpay/batch/security/BasicAuthenticationAdapter.java:23-127` |
| `identity:xperience-inbound-basic` | xperience `InternalAuth` creds + per-app route allow-list | `xperience/internal/interceptors/authz_interceptor.go:223-252`; `xperience/internal/routes/routes.go:458-475` |
| `identity:workflow-actor` | `actor_id` / `actor_type` / `actor_property_key` / `actor_property_value` | monolith `api/app/Models/Workflow/Service/Client.php:342-416`; WFS `workflows/internal/entities/action/interactor.go:300-333` |
| `identity:ps-cred-api`, `identity:ps-cred-workflow`, `identity:ps-cred-fastcron`, `identity:service-basic-auth:api-to-ps` | the payouts-service credential set | `payouts/internal/config/config.go:231-245`; `payouts/internal/routing/router/*.go` |

### 1.2 Passport claim schema (authoritative)

`edge/.../upstream-jwt/access.lua:86-205` builds: `nbf`, `exp` (`now+300`, `:90`), `jti`, `payloadhash`
(SHA-256 of the body, `:92`), `iat`/`iss` (=`edge`), `aud` (upstream host), `mode`, `org`+`product`,
`domain`, `identified`, `authenticated`, `credential.{username,public_key,secret_ref_id}`,
`merchant.id` (legacy, merchant consumers only), `consumer.{id,type}`,
`impersonation.{type,consumer.{id,type}}`, `oauth.*`, `roles`, `additional_identities[]`.

There is **no `authType` claim** — `auth_flow` is Kong-internal ctx only. The Go SDK
(`goutils/passport/passport.go:30-45`) has **no field for `merchant.id` or `payloadhash`**, so both are
silently dropped downstream. `goutils/passport/helpers.go:55-67` `GetResourceOwnerID` resolves the
effective merchant as **impersonation.Consumer.ID → oauth.OwnerID → consumer.ID**.

Three tiers: `basic-auth-x`/`jwt-x`/`user-auth` set both flags true; `keyless-identifier` sets
`identified` only; `basic-auth-x` falling back to the anonymous consumer leaves **both false** — that
last case is what the merchant-dashboard payout routes actually run on
(`terraform-kong/prod/api-dashboard/templates/merchant-dashboard.tf:6347-6352`).

### 1.3 Who enforces what

| Layer | Authentication | Authorization |
|---|---|---|
| **Kong / edge** | resolves the consumer (basic-auth-x, jwt-x, user-auth, admin token) and mints the passport | `authz-enforcer` (priority 975) POSTs `{claims:{subject,org,roles}, resource, action}` as `gateway:$KONG_AUTHZ_ENFORCER_PASSWORD` (`edge/.../authz-enforcer/handler.lua:102-176`). **`shadow_mode=true` AND route-whitelisted on prod `api.razorpay.com/v1/payouts`** → never blocks (`terraform-kong/base/api/api.tf:5190-5204`). **`shadow_mode=false` on `api-dashboard-merchant`** → enforced (`.../merchant-dashboard.tf:7175-7182`). No rate-limiting plugin is bound to any payouts/contacts/fund_accounts route (`terraform-kong/prod/edge/global-plugins.tf:46-60` is global-only) |
| **api monolith** | 7 auth types dispatched at `api/app/Http/Middleware/Authenticate.php:287-341` | Merchant-dashboard: `Route::$bankingRoutePermissions` (`Route.php:11966`) enforced **locally, DB-backed** in `app/Http/Middleware/UserAccess.php:358,413,518`. Admin: `Route::$routePermission` (`Route.php:10221`) enforced in `app/Http/Middleware/AdminAccess.php:285-328` (route→permission → admin roles → tenant check → merchant access-group). **No production remote AuthZ client exists** — the only `AuthzEnforcerClient` lives under `app/Services/Mock` |
| **xperience** | 4 planes per RPC (`internal/interceptors/authz_interceptor.go:68-73`) | ProxyAuth routes call the **real goutils authz enforcer** (`authz_interceptor.go:195-218`) with `Resource=RoutePermissionMap[route]`, `Action="post"`. InternalAuth routes check `InternalAppRouteMap[originService]` |
| **payouts service** | `middleware.BasicAuth(cred.*)` per route group + optional `PassportAuthentication(supportedAuths)` | **No role model at all.** Merchant resolution is passport-first, then the plain `X-Entity-Id` / `x-merchant-id` header (`internal/auth/authHelper.go:13-25` → `internal/auth/headers.go:27-38`) |
| **workflows (WFS)** | HTTP Basic, plain `==`, one 12-credential allow-list shared by all four services (`internal/boot/hooks/auth.go:47-72`) | **None per caller or per RPC.** `provider.ContextKeyCaller` is written (`auth.go:64`) and never read. Action-level checks are `AllowedActions[actor_type]` plus a **string match of caller-asserted `actor_property_key/value`** against the pending state's stored rules (`internal/entities/action/interactor.go:300-333,361-410`) |
| **batch** | Spring HTTP Basic, in-memory users | **None.** Any authenticated user may call any endpoint; the only merchant binding is the caller-declared `X-Entity-Id` header (`service/BatchService.java:92-108`) |

### 1.4 Roles

X banking roles are defined in `api/app/Models/User/BankingRole.php:20-48` and mapped to permissions in
`api/app/Models/User/UserRolePermissionsMap.php`. The authz policy set
`authz/scripts/policies/xplatform.csv:2-46` grants the payout **write** path
(create / approve / reject / cancel / bulk / bulk_approve) to exactly
**owner, admin, finance_l1, finance_l2, finance_l3**; `operations` gets contacts-write and payout-read;
`view_only` gets reads. `chartered_accountant` and `vendor` appear nowhere in `xplatform.csv`.

`maker`, `maker_admin`, `checker_l1/l2/l3` exist as monolith roles (`BankingRole.php:40-45`) and as authz
roles, but a full sweep of every payout policy row found **zero maker/checker grants** — X payout
maker-checker is expressed through the permission bundles `payout_create_view` vs
`payout_approve_reject_view` (`authz/scripts/policies/30_06_22_rx_xplatform_1.csv:2-15`,
`authz/scripts/policies/01_03_2024_rx_xplateform_cac_update.csv:2-9`) and through the workflow engine,
not through role names.

---

## 2. Ingress

### 2.1 The two monolith → payouts-service proxy paths

* **Direct (Splitz-gated, business-logic-skipping)** — `PayoutController.php:126,162,196` →
  `Models/Payout/DirectToPayoutsServiceGate.php::shouldDivert()`. False for test mode, false for internal
  apps (`:81-102`), false when the body `merchant_id` differs from the authenticated merchant (`:104-124`);
  otherwise a Splitz experiment keyed on `merchant_id`. Sends the untouched public input to PS `/v1/payouts`
  with header **`X-Payouts-Service-Proxy: 0`** (`Services/PayoutService/Create.php:208-217`).
* **Classic (business-logic-first)** — `Models/Payout/Service.php:561,8169,8202` → `Processor/Base.php:597`,
  after validation/balance/pricing; header **`X-Payouts-Service-Proxy: 1`**.

Every PS call carries Basic `config('applications.payouts_service')[mode]` +
`X-Passport-JWT-V1` + `X-Payout-Actor-Id`/`-Type` + `X-Payout-Idempotency`
(`Services/PayoutService/Base.php:80-88,356,541-596`; `Create.php:188-217`), **60 s timeout, no retry**
(`Base.php:93,95,145`).

### 2.2 Kong

Prod payout routes all sit on the `prod-api` service in the `authenticated_paths` bucket
(`terraform-kong/base/api/api.tf:900-1057`, prod `terraform-kong/prod/api/api.tf:655-725`), upstream
`api.api.svc.cluster.local` 960 / `api-canary` 20 / `api-baseline` 20.

The **payouts-proxy-cutover** template (`terraform-kong/templates/payouts-proxy-cutover/`) duplicates those
routes at `regex_priority=2` and diverts via the `upstream-override` plugin. It is registered in Atlantis
**only for devstack** (`terraform-kong/atlantis.yaml:326-333`; no prod/stage wrapper exists). The live
devstack cohort is **one merchant** (`C0uw3CXseZPwmJ`, auth flow `basic-auth-x:merchant`) for
`POST /v1/payouts`, plus 100 % of `GET /v1/payouts/schedule/timeslots`
(`terraform-kong/base/payouts-proxy-cutover/config.tf:12-28,48-69`). **There is no Splitz integration in
edge or terraform-kong** — per-merchant cohorting for the dashboard route is pushed into the PS shadow
gateway, as the config file itself explains (`config.tf:31-47`).

Anti-spoof: a `request-transformer` strips client-supplied `X-Payouts-Service-Proxy`, `X-Merchant-Id`,
`X-Entity-Id` on the diverted `payout_create` route (`payout-create.tf:203-215`) — but the equivalent strip
is **off by default on the dashboard and admin-dashboard shadow routes**
(`payouts-proxy-cutover/variables.tf:215-222,241-244`) and the devstack wrapper enables it for none of them.

### 2.3 Monolith payout route inventory

48 `route:api-monolith/*` nodes are in the part file, each with its `Route.php` definition line, auth-group
lines, and `$bankingRoutePermissions` / `$routePermission` line. The load-bearing ones:

| route | auth groups | permission |
|---|---|---|
| `POST payouts` (2311) | `private:5780` | `CREATE_PAYOUT` (12051) |
| `POST payouts_with_otp` (2320) | `private:5595`, `proxy:8002`, `userWhitelist:7350` | `CREATE_PAYOUT` (12054) |
| `POST payouts/{id}/approve` (2330) | `private:5594`, `proxy:8018`, `userWhitelist:7360` | `APPROVE_PAYOUT` (12078) |
| `POST payouts/{id}/reject` (2332) | `private:5593`, `proxy:8020` | `REJECT_PAYOUT` (12080) |
| `POST payouts/approve/bulk` (2328) | `proxy:8016` | `APPROVE_PAYOUT` (12076) |
| **`POST payouts/bulk`** (2318) | **`proxy:8034` ONLY** | `CREATE_PAYOUT_BULK` (12072) |
| **`POST payouts/bulk_approve`** (2319) | **`proxy:8035` ONLY** | `APPROVE_PAYOUT_BULK` (12074) |
| `POST xperience/bulk-payouts/approve` (2154) | `proxy:8625` | `APPROVE_PAYOUT` (12522) |
| `POST payouts_internal/{id}/approve` (2420) | `internal:6632` | — (allow-listed to `workflows`, `Route.php:19098`) |
| `POST wf-service/state/callback` (2448) | `internal:6722` | — (allow-listed to `workflows` `:19100` and `payouts_service` `:19414`) |
| `POST payouts/manual_action` (2345) | `admin:10208` | `PAYOUT_MANUAL_ACTION` (11409) |
| `ANY service/batch/{path?}` (3557) | `admin:9332` | `BATCH_API_CALL` (11057) |

`Route::$twoFactorAuthRequiredRoutes` (`Route.php:21545-21551`) contains **no payout route** — the OTP gate
is inside the controller, not the middleware.

### 2.4 The xperience BFF — a whole second bulk pipeline

`xperience` owns a `bulk_payouts` aggregate with its own state machine
(`xperience/internal/app/repository/bulkpayouts/state_machine.go:5-23`):
`created → validating → {validated, validation_failed}`; `validated → {processing, pending}`;
`pending → {rejected, processing}`; `processing → {processed, failed}`. `pending` is the maker-checker wait.

It calls **Batch directly** (`POST /batch` multipart, `PUT /batch` to process, `GET /batch/{id}`,
`GET /batch-entry`) and **WFS directly** (`WorkflowAPI/Create` with `entity_type="bulk_payouts"`,
`config_type="payout-approval"`, callbacks registered at `/v1/bulk-payouts/callbacks/workflow-state` and
`/workflow-action` with success codes `[200,201]` —
`xperience/internal/app/client/workflows/mapper.go:7-95`). Its 14 bulk-payout RPCs each exist twice: an
`*_edge` variant under passport + goutils-authz-enforcer, and a non-edge variant under Basic auth
allow-listed to the `api` app.

⚠️ `xperience/internal/app/client/workflows/create_workflow.go:36-40` logs the outbound Basic-auth
**username and password at info level**.

---

## 3. REAL Workflow Service vs the M5 `wfengine` reconstruction

### 3.1 Shape

| | REAL (`workflows` @ 080d71a) | wfengine (`ENV2_COMPOSE/substitutes/workflow-engine`) |
|---|---|---|
| Deployables | `cmd/api` (Twirp, 4 services), `cmd/workers` (**Cadence workers — the actual engine**), `cmd/migration` | one Python process, `run_engine.py` |
| Engine | Uber Cadence; every approval is a live workflow execution; the whole `Workflow` + compiled `Config` is snapshotted into the workflow input (`internal/entities/workflow/starter.go:89`) | in-process decision core over SQLite WAL (`wfengine/core.py`, `schema.sql`) |
| Store | MySQL (`workflows`, `states`, `actions`, `configs`, `comments`, `assignee`, `state_change_logs`) | SQLite (`orgs`, `actors`, `policies`, `workflows`, `approvals`, `audit_log`, `callbacks`) |
| Surface | 26 Twirp RPCs over `/twirp/rzp.workflows.{config,workflow,action,comment}.v1.*` | 1 Twirp RPC (`WorkflowAPI/Create`) + a REST actor plane + an admin plane + `/_arena/*` |
| Inbound auth | Basic, plain `==`, one shared 12-credential allow-list, no per-RPC scoping | Basic (service plane) + Bearer actor tokens + admin token, **strictly separated** (`server.py:100-123`) |
| Cache/queue | `pkg/cache` (Redis) and `pkg/worker/queue` are **dead code** — zero non-test importers | none |

### 3.2 Semantic-by-semantic fidelity table

| # | Maker-checker semantic | REAL WFS behaviour (file:line) | wfengine behaviour (file:line) | Verdict |
|---|---|---|---|---|
| 1 | **Create** | `WorkflowAPI.Create` (`internal/entities/workflow/server.go:30-75`) inserts a `workflows` row + a `state_change_logs` row, **no `states` rows**, then starts a Cadence execution (`starter.go:37-107`) whose `START_STATE` is auto-approved into the first checker state (`internal/client/types/approval/workflow.go:64-112,183-192`) | `POST /twirp/…/WorkflowAPI/Create` (`server.py:161-197`) parks one `workflows` row in `pending` and returns `id`+`config_id`+`status`+`domain_status`, mirroring `CreateHttpResponse` (`server.py:44-60`) | **partial** — wire-compatible with payouts' `WfCreate`, but no Cadence execution, no `states` rows, no `pkg/transition` status FSM |
| 2 | **Duplicate create / idempotency** | `interactor.go:341-377`: an existing workflow on `(config_id, entity_id, entity_type)` in `created|initiated` is **returned** instead of creating a second. Read-then-write, **no unique index** — two simultaneous creates can both succeed. Cadence's `WorkflowExecutionAlreadyStartedError` is swallowed (`starter.go:95-99`) | `core.py:86-100`: returns the existing row on `(org, idempotency_key)` or `(org, entity_id, state='pending')`, inside a single SQLite transaction | **partial** — same observable outcome, stronger (transactional) guarantee; wfengine also honours an explicit `idempotency_key` the real service ignores |
| 3 | **N-of-M approvals** | `StateDataRules.Count` per checker state; each approval calls `ReduceCount()` (`internal/dto/state_model.go:176-178`) **in Cadence memory only — `states.rules` is never rewritten**; complete when `Count <= 0` (`workflow.go:523-566`) | one flat `policies.required_approvals` counted as `COUNT(*)` of distinct rows in `approvals` (`core.py:74-77,175-178`), persisted on every act | **divergent** — real is a per-step counter invisible to the DB; wfengine is a single workflow-wide distinct-approver count that is always durable |
| 4 | **Multi-step / AND-OR graph** | `Template.ConvertToTemplate` (`internal/dto/config_model.go:362-486`) compiles `range → steps → roles` into a named-node DAG: `between` slab nodes (min-exclusive, max-inclusive, `workflow.go:490-493`), `checker` nodes named `<role_name>_<role_id>_<n>_approval` with `group_name` = the step ordinal, and a synthetic `merge_states` join per `AND` step; `OR` steps fan out as parallel branches | **nothing** — wfengine has no config compiler, no steps, no AND/OR, no amount slabs, no multi-level chaining | **not-in-reconstruction** |
| 5 | **Eligible approvers / roles** | `validateActionOnState` (`internal/entities/action/interactor.go:300-333`) string-matches the **caller-asserted** `actor_property_key`/`actor_property_value` against the pending `State.Rules`; `validateIfActionIsAllowed` (`:361-410`) gates by `actor_type` against `Config.Template.AllowedActions`. WFS **never re-derives the actor's real role** | server-side: the Bearer token resolves to an actor row with `(org_id, roles)` (`identity.py:109-119`); `core.py:144-151` requires the `approver` role and, when configured, membership of `policies.eligible_approvers` | **divergent** — wfengine is *stricter* and moves the trust boundary. The real trust boundary (C52) is that any of the 12 credentials may claim any role |
| 6 | **maker ≠ checker** | **Not enforced anywhere.** There is no comparison of `workflow.creator_id` against the acting `actor_id` on any approval path. The only creator-vs-checker logic is the DCS flag `skip_approval_if_creator_is_checker` (`internal/dcs/features/features.go:22`, `workflow.go:225-289`), which does the **opposite**: when the creator's role equals the checker role it **auto-approves** the state on the creator's behalf, on *every* role-keyed checker state | `core.py:153-156` (`# [CHECK:separation]`): `if wf["separation"] and actor==creator → 403 separation_violation` | **divergent — the single biggest fidelity gap.** wfengine enforces an invariant the real engine does not have, and does not model the flag that inverts it |
| 7 | **Duplicate approval by the same actor** | `verifyAlreadyOpenActionsOnStateGroup` (`interactor.go:335-355`) → 400 `ILLEGAL_ACTION_ACTOR_HAS_ALREADY_TAKEN_ACTED_ON_STATE_GROUP`, scoped to the **state group (step)** and keyed on `(actor_id, actor_type)` — so the same human acting as `user` and as `admin` counts twice. It is a non-transactional read-then-write across two tables with **no unique index on `actions`**, so concurrent duplicates can both pass; and `ReduceCount()` is called unconditionally inside Cadence (`workflow.go:367`) | `approvals` PK `(workflow_id, approver_id)` (`schema.sql:80-86`) makes a repeat **idempotent**: `core.py:164-174` skips the insert and re-counts, so the distinct count does not move | **divergent** — real returns an error, wfengine returns success idempotently; real is racy at the storage layer, wfengine is not |
| 8 | **Reject** | any single `rejected` action anywhere terminates the whole workflow: `processTerminalActionOnState` → `processEndState(ctx,"rejected")` (`workflow.go:308-332,387-396,455-469`). Available to `user`, `owner`, `admin`, `service` subject to `AllowedActions` — where the default matrix grants `approved` **only to `user`** (`config_model.go:555-581`) | `core.py:197-228`: terminal, bumps `version`, audits, fires the reject callback. Same actor checks as approve (org, `approver` role, eligibility, separation) | **partial** — same terminal effect; wfengine has no `AllowedActions` matrix and applies approve-style eligibility to reject |
| 9 | **Cancel / terminate** | `WorkflowAPI.Terminate` (`server.go:276-295` → `interactor.go:914-1004`): legal only from `created|initiated|init_failed`; in one transaction every state → `terminated`, workflow → `terminated`, then Cadence `TerminateWorkflow`. **Fires no callback** — the calling domain is never notified. The `actions` state machine has no `terminated` state, so in-flight actions are orphaned | `core.py:231-258`: creator or `operator` role, terminal, **fires a reject-side callback with `reason="cancelled"`** | **divergent** — wfengine notifies the payout side on cancel; the real service does not |
| 10 | **Expiry** | No timer of any kind (`grep NewTimer\|CronSchedule\|Await` over `internal/`+`pkg/` → zero). The only bound is Cadence `ExecutionStartToCloseTimeout` from `Meta.WorkflowExpireTime`; **the v2 compiler never sets that field** (`config_model.go:547-553`), so every `CreateV2` config gets the **10-year default** (`starter.go:176-183`). On a Cadence timeout **nothing observes it** — no callback, no DB update; the row stays `initiated` forever. (The real 3-month auto-reject is a *monolith* cron, `api/app/Models/Payout/Service.php:2530-2540`) | `expiry_seconds` per policy, default 86400 (`schema.sql:45`); `_maybe_expire` runs lazily before every decision and on read (`core.py:56-72`), plus an admin `/admin/expire-sweep`; on expiry it moves to `expired`, audits, and **fires the reject callback** so the payout leaves `pending` | **divergent** — day-scale, self-firing expiry vs a 10-year silent Cadence timeout |
| 11 | **Stale version / concurrency** | **No optimistic locking at all.** No `version`/`lock_version` column on `workflows`, `states` or `actions`; no `SELECT … FOR UPDATE`. The guards are (a) the `pkg/transition` FSM, which requires an open transaction and rejects illegal `from` states (`transition.go:81-183`), (b) `RowsAffected == 0` → `"no rows have been updated"` (`internal/common/repo.go:79-87`), (c) three copies of `checkIfRepeatStateTransitionForActivity` that tolerate a repeat by **substring-sniffing the error text `"failed To perform Event"`** | monotonic `workflows.version` bumped on every transition, with an `expected_version` guard → 409 `stale_version` (`core.py:158-162,216-218,246-248`; `schema.sql:61`) | **not-in-real** — wfengine adds an optimistic-concurrency contract the real engine does not expose. Callers must not depend on it |
| 12 | **Callback delivery: transport** | Cadence activity `MakeApiCallAndProcessResponseActivity` (`internal/client/types/common/callback.go:121-196`). URL = `boot.Config.Clients[cb.Service].Host + cb.UrlPath` — **host and credentials come from WFS config, only the path from the payload** (`callback.go:69-99`). HTTP Basic via a custom RoundTripper (`:203-206`), `Content-Type: application/json`, **`POST` and `PATCH` only** (`:155-163`) | `callbacks.py:107-149`: uses `callback_details.workflow_callbacks.processed.domain_status.{approved,rejected}.{url_path,method,headers}` verbatim, joined onto `PS_API_URL`; Basic `rzp_live` + the shared secret; falls back to `/v1/payouts/payouts_internal/{id}/{approve,reject}` | **match** on shape and identity; **partial** on host resolution (real derives it from its own client table, wfengine from `PS_API_URL`) |
| 13 | **Callback retry policy** | Inherits `DefaultActivityOptions` (`internal/client/types/approval/workflow.go:24-45`): `ScheduleToStart` 365 d, **`StartToClose` 30 s**, `InitialInterval` 2 s, `BackoffCoefficient` **5.0**, `MaximumInterval` 1 h, `ExpirationInterval` **30 days**, **no `MaximumAttempts`**. The per-callback HTTP timeout defaults to **60 s** (`callback.go:40,142`) — i.e. **longer than the 30 s Cadence attempt**, so a slow receiver produces duplicate callbacks. A permanent 4xx is retried for the full 30 days because `NewCustomError("this is not an successful response")` is not in `NonRetriableErrorReasons` | `callbacks.py:179-200`: `max_attempts=5`, linear `backoff*attempt` (default 0.5 s), 5 s per-request timeout, then `callback_exhausted` in the audit log | **divergent** — 5 bounded attempts over seconds vs unlimited attempts over 30 days |
| 14 | **Callback success codes** | Entirely payload-driven: `HandleCallbackResponse` (`callback.go:101-113`) matches `response_handler.success_status_codes`; anything else is a **retryable** Cadence error. Payouts registers `[200, 201, 409]` (`payouts/pkg/workflow/workflow_create.go:320-325`); the WFS e2e fixture ships `[200,201,408]` | hardcoded `SUCCESS_CODES = (200, 201, 409)` (`callbacks.py:26`) | **match** for the payouts path; **partial** in general (real is config-driven) |
| 15 | **Callback idempotency** | **None.** No idempotency key is generated or sent. `actions.idempotency_key CHAR(30)` exists in SQL (`migrations/20200702202525_create_actions.go:28`) but is absent from the Go model and never read or written. De-duplication is entirely the receiver's problem — which matters because of #13's timeout mismatch | persists a `callbacks` row keyed `cbk_<wf>_<kind>` with `UNIQUE(workflow_id, kind)` **before** the first attempt, sends it as an **`Idempotency-Key` header**, and re-queues undelivered rows on startup (`callbacks.py:63-84,164-175`; `schema.sql:101-111`) | **not-in-real** — wfengine sends a header the real engine never sends. A receiver built against wfengine must not rely on it |
| 16 | **Audit trail** | `state_change_logs` written inside the caller's transaction on **every** status change of a workflow/action/state (`pkg/transition/transition.go:159-177`), plus the `actions` and `comments` rows. `triggered_by` comes **verbatim from the unauthenticated `X-User-Email` header** (`hooks/auth.go:15,35` → `internal/common/utils.go:20-27`); an *empty* header still yields `mode=MANUAL` with an empty `triggered_by`, which fails `StateChangeLog.Validate()` and **aborts the enclosing transaction** | append-only `audit_log(seq, workflow_id, org_id, actor_id, action, detail, at)` written in the same transaction as every mutation (`schema.sql:89-97`; `core.py:63-66,118-122,184-189,222-225,252-255`), plus `callback_delivered` / `callback_exhausted` events; readable at `GET /v1/workflows/{id}/audit` | **partial** — same append-only property, different schema; wfengine's actor attribution is authenticated, the real one is a free-text header |
| 17 | **Tenant isolation** | The only ownership check is `validateOwnerDetails` (`interactor.go:556-568`), which compares the request's `owner_id`/`owner_type`/`service` against the **config's** — not against the caller's identity. Any of the 12 credentials can act for any merchant | `core.py:139-142,206-207,240-241,267-269` (`# [CHECK:org]`): the actor's `org_id` must equal the workflow's, on approve, reject, cancel **and read** | **divergent** — wfengine enforces a tenant boundary the real engine does not |

### 3.3 Verdict

`sub:workflow-engine` is classified **`high_fidelity_replacement`**, and that classification is correct
*for the purpose it was built for* (M5: exercising maker-checker invariants against a real decision
engine). It is **not** a drop-in stand-in for the real Workflow Service at the M6 architecture level.

It reproduces faithfully: payouts' `WfCreate` wire contract, the callback target/identity/headers/success
codes, terminal-state freeze, and durable at-least-once callback delivery with restart recovery.

**Concrete divergences to fix (ordered by impact):**

1. **No config compiler.** The real engine's entire configuration surface — `ConfigAPI.CreateV2`'s
   `range → steps → roles` DSL and `ConvertToTemplate`'s compilation into a `between`/`checker`/`merge_states`
   DAG — has no analogue. wfengine has a single flat policy per `(org, entity_type)`. Without this there is
   no way to exercise amount slabs, multi-level approvals, or AND/OR steps.
2. **maker ≠ checker is inverted.** wfengine hard-enforces separation; the real engine has no such check
   and ships a DCS flag (`skip_approval_if_creator_is_checker`) that **auto-approves** when maker role ==
   checker role. Any assurance result that depends on separation being enforced is not transferable.
3. **Actor trust model is inverted.** The real engine trusts caller-asserted `actor_property_key/value`
   from any of 12 equally-privileged credentials; wfengine resolves the actor server-side from a bearer
   token. wfengine cannot express the real trust boundary (C52), so it cannot surface defects that live
   there.
4. **Duplicate approval semantics differ in kind** (real: 400 error scoped to the state group; wfengine:
   idempotent success), and the real one is racy where wfengine is transactional.
5. **Expiry is day-scale and self-firing** in wfengine vs a 10-year silent Cadence timeout in the real
   engine that notifies nobody.
6. **`expected_version` optimistic concurrency does not exist** in the real engine at all.
7. **The `Idempotency-Key` callback header does not exist** in the real engine.
8. **Retry policy is off by orders of magnitude** (5 attempts / seconds vs unlimited / 30 days, backoff
   coefficient 0 vs 5.0) and the real engine's 30 s-activity-vs-60 s-HTTP timeout mismatch — a genuine
   duplicate-callback generator — is not modelled.
9. **Missing RPC surface**: 25 of 26 Twirp RPCs are absent, including `ListPending` (the approval-queue
   read), `Terminate`, `ActionAPI.CreateWithEntityId` and `CreateDirectOnWorkflow`.
10. **No `state_change_logs`-equivalent per-entity FSM**, no `states` rows, no `comments`, no assignee.

Also note that `sub:workflow-engine` is **not yet a compose service** — `ENV2_COMPOSE/docker-compose.yml`
still only wires `workflow-sim` (`:1965-1994`).

---

## 4. The exact bulk contract — Batch → monolith → payouts service

This section is the build spec for a contract-faithful batch substitute.

### 4.1 Chain overview

```
dashboard --(POST /batches, multipart)--> monolith BatchController@createBatch
   monolith --(Basic applications.batch, POST /batch multipart)--> Batch service
Batch service, per 5-row group:
   --(Basic rzp_<mode>_<X-Entity-Id> : BATCH_API_SECRET, POST {api.basepath}payouts/bulk)--> monolith
       monolith Service::createBulkPayout
           shared-account rows --(POST {PS}/v1/payouts/bulk)--> payouts service
           direct-account rows --> stay on the monolith
   --(POST {api.basepath}payouts/bulk_approve)--> monolith Service::approveBulkPayout  [never reaches PS]
```

### 4.2 Batch → monolith `POST {api.basepath}payouts/bulk`

**URL.** `getBasePath()` falls through every special case to `appConfiguration.getApiBasePath()`
(`batch/src/main/java/com/razorpay/batch/batchengine/item/processor/BulkApiCallDataProcessorImpl.java:211-281`,
return at `:280`) = config `api.basepath` ← env `API_BASE_PATH`, which **already ends in `/v1/`**
(`batch/src/main/resources/application-prod-rzpx.properties:27`, `.env.slit-ci:49`). Final URL is a plain
concatenation: `https://api.razorpay.com/v1/payouts/bulk`. No query string, no path params.

**Method / timeouts / retry.** `POST` only (`BulkApiCallDataProcessorImpl.java:127,131`). Connect and read
timeouts both **60 000 ms** (`application.properties:198-199`). Retry is Spring `RetryTemplate` with a
**fixed** backoff (payout configs set no `retryStrategy`): **5 total attempts, 5 000 ms apart**
(`ApiCallDataProcessorHelper.java:84,90`; `application.properties:201-202`). Retryable:
`SocketTimeoutException`, `ResourceAccessException`, `HttpRetryException`, `ThrottledClientException` (429),
plus any configured retryable status (`ApiCallDataProcessorHelper.java:66-78`).

**Auth.** `RestCallUtils.computeAuthHeader` (`batch/src/main/java/com/razorpay/batch/utils/RestCallUtils.java:55-69`):

```
authType = "proxy"   (class default, BulkApiCallDataProcessorImpl.java:84; payout configs never override it)
username = "rzp_" + mode + "_" + entityId          // e.g. rzp_live_FioOVkSj6ULJt3
password = api.secret  ← env BATCH_API_SECRET       // ONE service-wide secret, not a per-merchant key
Authorization: Basic base64(username + ":" + password)
```

`mode` = `Batch.mode` = the inbound `mode` header (default `test`,
`commons/RequestTokenInterceptor.java:44`); `entityId` = `Batch.entity_id` = the inbound `X-Entity-Id`
header. On the monolith this lands on **`proxyAuth`** (`api/app/Http/BasicAuth/BasicAuth.php:1220`,
`verifyInternalAppAsProxy` `:1547-1580`): Basic user = merchant id, Basic pass = the `batch` internal-app
secret, then the route allow-list `Route::$internalApps['batch']` (`api/app/Http/Route.php:18735`, which
lists `payout_bulk_create` `:18748` and `payout_bulk_approve` `:18749`).
(`payout_link_bulk*` instead sets `authType:"internal"` → username `rzp_<mode>` with no merchant segment.)

**Headers** — `RestCallUtils.setDefaultHeaders` (`RestCallUtils.java:38-53`), constants in
`constants/HeaderConstant.java:9-19`:

| header | value |
|---|---|
| `Authorization` | `Basic base64("rzp_<mode>_<entityId>:<BATCH_API_SECRET>")` |
| `Content-Type` | `application/json` |
| `X-Batch-Id` | `jobExecutionService.getJobId()` = **`batch.id`**, the raw 14-char id, **no `batch_` prefix** |
| `X-Creator-Id` | `batch.creator_id`, from the inbound `X-Creator-Id` header, verbatim |
| `X-Creator-Type` | `batch.creator_type`, from the inbound `X-Creator-Type` header, verbatim |
| `X-Entity-Id` | `batch.entity_id` = merchant id |
| `rzpctx-dev-serve-user` | only when `batch.settings["rzpctx-dev-serve-user"]` is non-empty (`BulkApiCallDataProcessorImpl.java:297-302`) |

**Negatives that matter:** there is **no idempotency header** — the line is commented out
(`RestCallUtils.java:47-49`); the key travels in the body. There is no `X-Dashboard*`, no
`X-Razorpay-Account`, no `mode` header, no `X-Request-Id` on this call.

**Idempotency-key derivation.** For `payout` and `payout_approval`
(`BulkApiCallDataProcessorImpl.java:106,163-166`):

```
idempotency_key = "batch_" + <batch_entry.id>       // idempotentKeyPrefix "batch_" + the 14-char entry PK
```

injected into each body element under the config's `idempotentKey` name (`"idempotency_key"`) by
`BaseApiProcessor.buildRequestBody` (`.../apiprocessor/BaseApiProcessor.java:235-253`, line 241).
**No Redis mutex, no content hash, no cross-attempt coordination.** By contrast the six
`v2/payouts_*_process` types and `payout_link_bulk*` use
`CacheIdempotencyEnabledBulkApiCallDataProcessorImpl` (`:104-172`): SHA-1 over
`PayoutConstant.getPayoutIdempotencyColumns()` → Redis key `payouts:<merchant>:<hash>` guarded by a 300 s
`RedisMutexLock` `payouts:mtx:<m>:<h>` with a 24 h TTL, and a cached `<key>:<payoutId>` short-circuits the
HTTP call entirely as a duplicate (`constants/PayoutConstant.java:48-59`). This two-tier split is the
concrete "known race on duplicate idempotency keys" (control C53).

**Chunking / concurrency.** `bulkSize: 5` → `AggregateItemReader` groups 5 `batch_entry` rows per outbound
call (`batchengine/step/BaseStepProcessor.java:81-88`); `chunkSize: 1`; `maxThreads: 10` for `payout`
(`payout.json:49-68`) and **6** for `payout_approval` (`payout_approval.json:45-67`). Rows are read in
`sequence_number` order (`BatchEntryDataReaderImpl.java:55-79`, `pageSize 1000`) but dispatched across the
thread pool, so **response ordering is not guaranteed across calls**; within one request the array order is
the read order.

**Request body — a bare top-level JSON array**, no envelope key
(`BulkApiCallDataProcessorImpl.java:498,581-582`). This is the single most likely thing to get wrong.

```json
[
  {
    "idempotency_key": "batch_Q9xK2mZbT1a4Rc",
    "razorpayx_account_number": "2323230041626905",
    "payout": {
      "amount": "10000", "amount_in_rupees": "", "currency": "INR",
      "mode": "IMPS", "purpose": "payout", "narration": "Acme Salary",
      "scheduled_at": null, "reference_id": "INV-2201"
    },
    "fund": {
      "id": "fa_Jk3mQ1pXyZ8aBc", "account_type": "", "account_name": "",
      "account_IFSC": "", "account_number": "", "account_vpa": "",
      "account_phone_number": "9876543210", "account_email": "bene@example.com"
    },
    "contact": {
      "type": "employee", "name": "Asha Rao", "email": "asha@example.com",
      "mobile": "9876543210", "reference_id": "EMP-0042"
    },
    "notes": { "dept": "eng" }
  }
]
```

Template at `batch/src/main/resources/payout.json:125-155`; `##Column Header##` tokens are substituted from
the CSV row and JSON-escaped (`BaseApiProcessor.java:175-186`). `payout.scheduled_at` comes from
`batch.settings.scheduled_at`; the flat optional-param keys are folded into their nested homes and removed
from the top level (`apiprocessor/PayoutApiProcessorImpl.java:22-105`). `notes` is assembled from any input
column whose header *contains* the `notesColumn` string, with the marker and brackets stripped
(`BaseApiProcessor.java:60-80,146-152`) — a header `notes[dept]` yields `"notes": {"dept": …}`.

**Response Batch expects** (`BaseApiProcessor.processBulkApiResponse`, `.../BaseApiProcessor.java:327-376`):

```json
{ "items": [
    { "idempotency_key": "batch_Q9xK2mZbT1a4Rc", "http_status_code": 200,
      "id": "pout_Jk3mQ1pXyZ8aBc", "entity": "payout", "status": "processing" },
    { "idempotency_key": "batch_Lp7bV0nWtQ2eYd", "http_status_code": 400,
      "error": { "code": "BAD_REQUEST_ERROR", "description": "The ifsc provided is invalid" } }
] }
```

Rules a substitute **must** honour:

1. envelope key is `items` (`bulkApiResponseKey` default, `BaseApiProcessor.java:49`);
2. each item **must echo the exact `idempotency_key`** it was sent — missing or unknown throws
   `LogicException("UNHANDLED FLOW")` and fails the whole 5-row chunk (`:351-358`);
3. per-entry status is `item.http_status_code`, defaulting to the outer HTTP status (`:346`);
4. success = code ∈ `successStatusCode` = `[200]` for both payout types;
5. the **entire item map** becomes `Record.responseColumns` (`:345`), which is what makes the output
   columns `id`, `error.code`, `error.description` resolve
   (`batchengine/item/mapper/FlatFileFieldExtractor.java:26-47`);
6. an item with `http_status_code == 500` throws `HttpRetryException` and retries the **whole group**
   (`:385-402`);
7. the v2 `payoutV2ApiProcessor` additionally requires `id` on a successful item, else
   `LogicException("Payout id not present in response")` (`PayoutV2ApiProcessorImpl.java:222-225`).

**Transport-error handling after the 5 attempts** (`BulkApiCallDataProcessorImpl.getRecoveryContext`,
`:380-463`): socket/resource-access errors write `error = {code:408, description:<msg>}`; a 5xx with
`failOnServerError:false` (payout, v2 payouts) writes `error = {code:<status>, description:<msg>}` and the
job continues; a 5xx with `failOnServerError:true` — **which is `payout_approval`, because its config omits
the field and the default is `true` (`BulkApiCallDataProcessorImpl.java:88`)** — throws
`JobFailureException(API_SERVER_DOWN)` and **fails the whole batch job**.

**What is written back per row** (`batchengine/item/writer/BatchEntryDataWriterImpl.java:63-131`):
`batch_entry.row_data` = the input columns as JSON, `batch_entry.response_data` = the response item as JSON,
and `batch_entry.status = "PROCESSED"` — **for successes and failures alike**. There are **no
`error_code`/`error_description` columns**; per-row outcome lives only inside `response_data`. Counters are
flushed with an additive native UPDATE (`repository/BatchRepository.java:31-36`):
`success_count += (status_code ∈ [200])`, else `failure_count += 1`, `processed_count += 1`.

### 4.3 Monolith `POST payouts/bulk` → payouts service `POST /v1/payouts/bulk`

Controller `api/app/Http/Controllers/PayoutController.php:718-727` → `Models/Payout/Service.php:3051-3075`
`createBulkPayout`:

* merchant not migrated → `createBulkPayoutForAPI` (`:3302`), everything stays on the monolith;
* migrated → `handleBulkCreationForPayoutServiceEnabledCurrentAccountMerchant` (`:3099-3125`), which splits
  the array by the balance's account type: **shared → payouts service, direct → monolith**
  (`getPayoutServiceAndApiInput`, `:3101-3135`).

The PS call is `Services/PayoutService/BulkPayout.php:50-84`:

```
POST {PAYOUTS_URL}/v1/payouts/bulk          (path const :16 '/payouts/bulk'; the /v1 prefix is Base.php:52,350-354)
  X-Passport-JWT-V1 : <minted by the monolith for the PS base URL>    (:62)
  x-batch-id        : <forwarded verbatim from Batch's X-Batch-Id>     (:63)
  X-Entity-Id       : <forwarded verbatim>                             (:64)
  x-creator-id      : <forwarded verbatim>                             (:65)
  x-creator-type    : <forwarded verbatim>                             (:66)
  + Basic config('applications.payouts_service')[mode] and the standard actor/idempotency headers (Base.php:80-88,356,582-596)
```

Pre-flight: `POST {PAYOUTS_URL}/v1/payouts/bulk/validate` (`BulkPayout.php:14,27-44`), passport only.

**PS side** (`payouts/internal/routing/router/payout_internal_routes_with_passport.go:13-35`):

* auth = `middleware.BasicAuth(cred.API, cred.Workflow)` (`:16`) **plus**
  `middleware.PassportAuthentication([]string{})` (`:18`) — the **empty** `supportedAuths` slice disables the
  auth-type check entirely (`internal/routing/middleware/passport.go:157-162`), so any legacy auth type is
  accepted. There is **no** `middleware.IdempotencyKey` wrapper on this route.
* `x-batch-id` is **mandatory** (`internal/controllers/bulkPayoutsController.go:73-80` → 400).
* max **15** entries (`internal/app/bulkPayoutsProcessor/constants.go:6`, enforced `service.go:48-62` → 400).
  ⚠️ Batch sends 5 per call, so this ceiling is never reached from Batch — but the monolith's own
  `approveBulkPayout` enforces the same 15 (`api/app/Models/Payout/Validator.php:1521-1531`).
* per-entry required fields (`internal/app/bulkPayoutsProcessor/validation.go:20-58`):
  `razorpayx_account_number` (5–22), `idempotency_key`, `payout.{amount|amount_in_rupees, currency=INR,
  mode, purpose≤30}`, `fund.{id | account_type + details}`; `payout.narration ≤30`,
  `payout.reference_id ≤40`, `contact.email` must be an email. `amount` and `amount_in_rupees` are mutually
  exclusive (`:114-128`).
* dedupe: `bulk_idempotency_keys` UNIQUE `(idempotency_key, merchant_id)` —
  **`batch_id` is stored but is NOT part of the uniqueness key**
  (`internal/database/migrations/20240830164301_bulk_idempotency_keys.go:13-30`). Guarded by a **180 s**
  mutex `batch_payout_<batchID>_<ikey>` (`core.go:289`; `appConstants/constants.go:293,295`). A replay
  returns the existing payout (`core.go:354-388`); a row whose `source_id` was never set (a crash between
  insert and update) **permanently poisons** that `(merchant, ikey)` pair with a 500 (`:373-381`).
* response (`internal/app/dtos/bulkPayoutsAPIResponse.go:9-13`, built `service.go:127-146`):
  `{"entity":"collection","count":N,"items":[…]}` — successes first, then failures. Success items are
  `PayoutApiResponse` (carrying `idempotency_key`, `batch_id`, `id`, `status`, …); failure items are
  `{idempotency_key, batch_id, http_status_code, error:{description, code}}`
  (`internal/app/dtos/bulkPayoutErrorResponse.go:9-19`). Per-entry code mapping: `BAD_REQUEST_*` → 400,
  `DuplicatePayoutEvaluateFoundExistingHash` → 409, everything else → 500 (`core.go:527-558`).
* **HTTP 200 is returned even when every entry failed** (`bulkPayoutsController.go:113`).
* ⚠️ merchant resolution falls back to the **plain `X-Entity-Id` / `x-merchant-id` headers** when the
  passport yields none (`internal/auth/authHelper.go:13-25` → `internal/auth/headers.go:27-38`), gated only
  by the shared service Basic credentials.
* ⚠️ `POST /v1/payouts/bulk/validate` currently returns a **hard-coded sample payload**
  (`internal/app/payouts/service.go:1189-1210`, `// TODO: Remove this before prod release!!!!`) and takes
  `merchant_id` from the **body**, not the passport.

### 4.4 Monolith `POST payouts/bulk_approve` (never reaches PS)

`PayoutController.php:778-785` → `Models/Payout/Service.php:3539-3620`:

* max **15** rows (`validateBulkPayoutCount`, `Validator.php:1521-1531`);
* `x-batch-id` header **mandatory** (`validateBatchId`, `api/app/Base/Validator.php:83-89` — non-empty only,
  no prefix requirement);
* per row: `idempotency_key`, `payout.id` and `payout_update_action` must all be present
  (`Service.php:4656-4669` → `Base/Validator.php:105-113,183-196`);
* `payout_update_action` uppercased: `A` → `approvePayoutsFromBatchService` (validator ruleset
  `batch_approve`, `findByPublicId`, `validatePayoutStatusForApproveOrReject`, `Core::approvePayout`);
  `R` → `rejectPayoutFromBatchService`; anything else → `BadRequestValidationFailureException`
  (`Service.php:3629-3680`);
* each row runs in its own `repo->transaction`; per-row exceptions are caught and pushed as
  `{batch_id, idempotency_key, error:{description, public_error_code}, http_status_code}`;
* the response is `PublicCollection::toArrayWithItems()` = `{entity, count, items}` — matching Batch's
  `items[]`-keyed-by-`idempotency_key` expectation;
* **no forwarding to the payouts service exists on this path.**

Batch's request element (`batch/src/main/resources/payout_approval.json:68-88`,
`apiprocessor/PayoutApprovalProcessorImpl.java:32-51`):

```json
[{
  "idempotency_key": "batch_<batch_entry.id>",
  "razorpayx_account_number": "<account_number (do not edit)>",
  "payout_update_action": "A",
  "user_comment": "Bulk approved",
  "payout":  { "id": "pout_…", "amount": "100.00", "currency": "INR",
               "mode": "IMPS", "purpose": "payout", "narration": "…", "status": "pending" },
  "fund":    { "id": "fa_…" },
  "contact": { "name": "Asha Rao", "id": "cont_…" }
}]
```

`user_comment` comes from `batch.settings.user_comment`, defaulting to the literal `"Bulk approved"`
(`PayoutApprovalProcessorImpl.java:22,43`). The input CSV for this batch type is the **output of a prior
export** — a pending-approvals report whose columns are all suffixed `(do not edit)` except the single
editable `Approve (A) / Reject (R) payout` column (`payout_approval.json:13-30`).

### 4.5 Batch's own API and Postgres schema

**`POST /batch`** (`api/BatchController.java:48-59`), multipart, fields bound by **Java property name**
(`domain/BatchCreateDTO.java:24-68`): `multipartFile`, `batchTypeId` (required, must exist in `batch_type`),
`name`, `settings` (valid JSON, ≤4999 chars), `version` (≥`2.0` activates the `/v2/` config dir and the
`_validate`/`_process` split), `schedule` (epoch ms), `storeHandler`, `s3SignedUrl`.
`entityId`/`mode`/`creatorId`/`creatorType` come **from headers only** and overwrite any form value
(`service/BatchService.java:92-108`); a missing `X-Entity-Id` throws `X_ENTITY_ID_NOT_PRESENT`.

Other routes: `PUT /batch` (v2 second leg, `ProcessBatchDTO{previousBatchId, batch_type_id, name,
settings}`), `GET /batch/{id}`, `GET /batch/getBatches/{ids}`, `GET /batch/validateFileName`,
`GET /batch/pageable`, `GET /batch/{id}/download`, `POST /batch/{id}/{action}` (202, action ∈
`start,restart,pause,fail,cancel,complete`), `GET /batch-entry?batchId=&sequence_numbers=`,
`POST /direct/validate`, `GET /direct/batch`, `GET /health_check`.

**`GET /batch/{id}` response** — the `Batch` entity with `@JsonProperty` names: `id`, `entity_id`, `name`,
`batch_type_id`, `mode`, `creator_id`, `creator_type`, `is_scheduled`, `upload_count`, `processed_count`,
`failure_count`, `total_count`, `success_count`, `attempts`, `status`, `settings`, `amount`,
`processed_amount`, `schedule_time`, `created_at`/`updated_at` (**seconds on the wire, milliseconds in the
column** — `entity/BaseEntity.java:27-46`). **There is no `processed_percentage` field**; callers derive it.
`attempts` and `upload_count` are never written by the service.

**Postgres** (`src/main/resources/schema.sql`): `batch_type(id, name)` `:96-100`;
`batch(...)` `:102-123,156,158,176-178` with index `(entity_id, created_at DESC)`;
`batch_entry(id, created_at, updated_at, batch_id, response_data TEXT, row_data TEXT, sequence_number,
status)` `:126-136` with index `(batch_id, sequence_number ASC)`;
`file_store(...)` `:138-154,160`; `configuration(...)` `:162-169`; plus the Spring Batch metadata tables
`:16-94`. Seed `data.sql` has 426 `batch_type` rows including `payout` `:25`, `fund_account` `:23`,
`payout_link_bulk` `:95`, `tally_payout` `:119`, the v2 `payouts_*` triplets `:191-225` — but
**`payout_approval` has no row**, so `@BatchTypeIdConstraint` would reject `POST /batch` for it.

**Status machine** (`enums/BatchStatus.java:6-11`,
`batchengine/listener/BatchJobExecutionListener.java:88-146`): `CREATED`/`SCHEDULED` at insert; per-step
`STAGING → PROCESSING → OUTPUT` (v1) or `STAGING → VALIDATING → OUTPUT_CREATION` (v2 validate); at job end
`COMPLETED` / `FAILED` / `PAUSED` (Spring `STOPPED`) / `CANCELLED` (a completed `<id>_output` job). Entry
statuses (`enums/BatchEntryStatus.java:3-5`): `CREATED`, `VALIDATED`, `FAILED`, `PROCESSED`.

**Dispatch.** `BatchCore.handleBatchProcessing` (`core/BatchCore.java:237-263`) pushes to SQS only when the
merchant id or batch type is in `QueueValidation` — and **no payout type is in that allow-list**
(`validation/queue/QueueValidation.java:7-11`), so **payout batches run inline on the web pod at request
time**, and only if the `enable_batch_processing` configuration row is true.

**Inbound auth.** Spring HTTP Basic against in-memory users (`security/BasicAuthenticationAdapter.java:23-127`):
`apiUser` (`BATCH_BASIC_AUTH_USERNAME/PASSWORD`, this is the monolith), `xperienceUser`, `remindersUser`,
`abacusUser`, `accountingIntegrationsUser`, `vendorExperienceUser`, `vendorPaymentsUser`, `emandateUser`.
A second `WebSecurityConfigurerAdapter` handles requests carrying `X-Passport-JWT-V1`
(`security/PassportSecurityConfig.java:152-205`). **Authorization is coarse — any authenticated user may
call any endpoint**, and the only merchant binding is the caller-declared `X-Entity-Id`.

### 4.6 PS-side batch-submitted release

`batch_submitted_merchants` is **not a table** — it is a worker queue
(`payouts/internal/app/common/appConstants/worker.go:8`; job `internal/job/batch_submitted_merchants.go:15-58`,
`MaxRetries 5`, `Timeout 120 s`). The persisted state is `payouts.status = 'batch_submitted'`
(`appConstants/states.go:8`), entered on event `batch_summit` from
`create_request_submitted | pending | scheduled` (`state_machine.go:330`) whenever a payout has a
`batch_id` **and** mode ∈ {NEFT, RTGS} (`bulk_payout_helper.go:415-460`, `constants.go:22-25`), and again on
workflow approval of a batch payout (`core.go:5124-5135`, set directly, bypassing the state machine).

`POST /v1/cron/process_batch_submitted_payouts` (`cron_routes.go:40-49`, **`BasicAuth(cred.FastCron)` only** —
no passport, no DB middleware; documented hourly) runs the fan-out
(`bulk_payout_helper.go:25-85`): `SELECT DISTINCT merchant_id FROM payouts WHERE status='batch_submitted'`
(`repo.go:436-445`, **unbounded, no time window**) then one job per merchant. ⚠️ **The first enqueue failure
aborts the whole loop**, silently skipping every later merchant for that tick (`:64-77`).

The consumer takes a 30 s merchant mutex `merchant_batch_submitted_<merchantID>`, reads up to the Redis
`batch_payouts_fetch_limit` (default **300**) payouts for that merchant with no `FOR UPDATE` and no ordering
(`bulk_payout_helper.go:87-127`, `repo.go:447-458`), then per payout takes a 30 s `payout_<id>` mutex and
hands it to the normal post-create pipeline. A payout no longer in `batch_submitted` is swallowed as success
so duplicate queue messages dequeue rather than retry (`:301-350`).

A second queue `bulk_payouts` handles the non-delayed modes (`internal/job/bulk_payouts.go:18-95`).

---

## 5. Admin / ops actions

| Monolith admin route (Route.php) | Permission (`$routePermission`) | Onward |
|---|---|---|
| `POST payouts/manual_action` (2345, admin:10208) | `PAYOUT_MANUAL_ACTION` (11409) | PS `POST /v1/payouts/manual_action` (`payouts/internal/routing/router/payout_internal_routes.go:20-25`) — body `{action, bulk_input:[{payout_id}], reason, queue_if_low_balance}` → `{ps_success_payout_ids[], ps_failed_payout_ids[], api_payout_ids[]}`; actions `processed_to_processing`, `approve_workflow_payouts`, `reject_workflow_payouts` (`internal/controllers/adminClientController.go:294+`) |
| `PATCH payouts/{id}/manual/status` (4570, admin:9873) | `PAYOUT_STATUS_UPDATE_MANUALLY` (11591) | local `payouts` table or PS, branching on `getIsPayoutService()` |
| `PATCH payouts/manual/status_update/batch` (4571, admin:9874) | `PAYOUT_STATUS_UPDATE_MANUALLY` (11592) | `Service.php:5030` `findMany` then per-payout branch |
| `POST payouts/{id}/retry` (2357, admin:9037) | `RETRY_SETTLEMENT` (10669) | PS `POST /v1/payouts/retry` |
| `POST admin/payouts/cancel` (2342, admin:8889) | `REJECT_PAYOUT_BULK` (10400) | bulk cancel |
| `POST admin/payouts/workflow_retry` (2419, admin:9797) | `RETRY_PAYOUT_WORKFLOW_BULK` (10401) | re-drives the workflow create |
| `PATCH fund_transfer_attempts` (816, admin:9146) | `SETTLEMENT_BULK_UPDATE` (10866) | FTA bulk update |
| `POST payouts/fee_recovery` (2772, admin:9718) | `PROCESS_FEE_RECOVERY` (11488) | PS `POST /v1/payouts/rzp_fees_payout` |
| `POST banking_account_statement/source/update` (4227, admin:9907) | `MANUALLY_LINK_RBL_ACCOUNT_STATEMENT` (11626) | BAS relink |
| `ANY service/batch/{path?}` (3557, admin:9332) | `BATCH_API_CALL` (11057) | **arbitrary passthrough to the Batch service** (`api/app/Services/BatchMicroService.php`) |
| `POST wf-service/action` (2452, admin:9785) | — | WFS `ActionAPI` from the admin dashboard |

Enforcement is `AdminAccess::policyChecker` (`api/app/Http/Middleware/AdminAccess.php:285-328`): route →
permission → admin roles+permissions from the monolith's **own DB** → tenant role check → merchant
access-group check. No remote AuthZ call.

The admin SPA posts to `/admin/api/{mode}/<path>` (`admin-dashboard/js/common/fetch.js:88-96`) with the
browser session; it **never sets `X-Admin-Token` itself** (grep-confirmed absent) — the admin-dashboard
backend/edge mints it before the monolith sees it.

Notable PS-side asymmetries found while tracing these:
`POST /v1/cron/query_db` executes arbitrary SELECTs behind **fast-cron Basic auth alone**
(`payouts/internal/routing/router/cron_routes.go:130-135`) while its twin
`/v1/admin/internal_actions/query_db` requires an admin passport
(`internal_action_routes.go:16,20-25`); and `payouts_internal/{id}/approve|reject` carry **no passport and
no merchant scoping** — any of seven internal Basic credentials can approve or reject any payout id
(`payout_internal_routes.go:14-18,69-80`).

---

## 6. Twin gaps this lane records

> Snapshot taken while sibling M6 lanes were landing changes; the rows below reflect the working tree at
> the time this part file was generated.

| Component | Twin state | Evidence |
|---|---|---|
| `svc:batch` | a contract-faithful **`batch-sim`** substitute now exists for the *payout* batch types, but its compose block is **staged, not pasted** — `batch-sim` is not yet a running service. The Spring Batch engine, Postgres, S3 file store, output-file writing and the completion-email tasklet are out of scope by design | `ENV2_COMPOSE/substitutes/batch-sim/CONTRACT.md`, `.../compose-block.yml` (which states "This lane does NOT edit docker-compose.yml") |
| the 11 non-payout batch types (`payout_link_bulk*`, `fund_account*`, `tally_payout`, the six `v2/payouts_*`) | **absent** — including the entire hardened SHA-1 + `RedisMutexLock` idempotency tier, so control C53's two-tier split cannot be exercised | `ENV2_COMPOSE/substitutes/batch-sim/CONTRACT.md` scope statement |
| `svc:xperience` | **absent** — the whole bulk-payouts BFF, its `bulk_payouts` state machine and its direct WFS integration | not in `ENV2_COMPOSE/docker-compose.yml` |
| `svc:api-monolith` ingress | `monolith-stub` serves **only** PS→monolith internal callbacks; **zero** payout ingress routes, no batch proxy, no `wf-service/*` routes, and **no `/v1/workflow/state` receiver** | `ENV2_COMPOSE/substitutes/monolith-stub/server.py:1080-1112` |
| `identity:passport-jwt` | `kong-lite` mints a reduced passport: no `payloadhash`, `credential`, `merchant.id`, `aud` or `impersonation`, and a synthetic `iss` instead of `edge` | `ENV2_COMPOSE/substitutes/kong-lite/server.py:150-174` vs `edge/.../upstream-jwt/access.lua:86-205` |
| dashboard / OAuth / admin ingress | **absent** — no cookie session, no CSRF, no OTP, no bearer, no `X-Admin-Token` anywhere in the twin | `kong-lite` implements merchant-key Basic auth only (`server.py:177-193`) |
| `sub:workflow-engine` | **now a compose service** (port 8093, SQLite at `/data/wfe.db`, `WFE_TENANT_KEY=owner_id` so the engine's org is the payouts merchant, `WFE_WORKFLOW_ID_LEN=14` to fit payouts' `CHAR(14)` `workflow_id` columns). Still carries the 10 divergences in §3.3 | `ENV2_COMPOSE/docker-compose.yml:2385-2440`; `substitutes/workflow-engine/ARENA.md` |
| `queue:batch_submitted_merchants`, `queue:bulk_payouts` | **now driven by the real payouts worker binary** | `ENV2_COMPOSE/docker-compose.yml:968` (`PAYOUTS_WORKER_NAME=batch_submitted_merchants`), `:932` (`bulk_payouts`) |
| role / permission model | **absent** — no `role:*` enforcement anywhere; `kong-lite` passes `seeds/merchants.json` roles through verbatim | `ENV2_COMPOSE/substitutes/kong-lite/server.py:123,171-172` |
