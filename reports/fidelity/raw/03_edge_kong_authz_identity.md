# Lane 03 — Edge / Kong / AuthZ / Passport / Identity Propagation

Verifies and corrects `reports/raw-findings/21_edge_kong_authz.md`, `24_shared_libs_proto.md` §1,
and `ARCHITECTURE_DELTA.md` W1/W6/C43–C46 against source. Those two raw findings are exhaustive
and are treated as CONFIRMED baseline where this pass re-derived the same facts independently; new
material below is a from-scratch read of `payouts` (routing/router, routing/middleware, internal/auth,
internal/boot, config), `api` (migrations), and the ENV2 twin.

## Scope and sources read

- `terraform-kong` (prod/api/api.tf, prod/api-dashboard/*, base/payouts-proxy-cutover/*, prod/payout-links/*)
- `edge` (kong-plugin-upstream-jwt/access.lua, kong-plugin-impersonation-grant/*, kong-plugin-authz-enforcer)
- `authz` (admin/repo/entities, admin/corepolicy/policy.go, scripts/policies/*.csv, config/*.toml)
- `goutils/passport` (passport.go, helpers.go, handler.go, config.go, mock/*) — v4 source
- `goutils/authz/v2` (enforcer/model.go, enforcer/enforcer.go, policystore, adapter/mock.go)
- `payouts`: `internal/routing/router/*.go` (all route files), `internal/routing/middleware/{auth,passport,request_context,idempotency_key}.go`,
  `internal/auth/{passport,authHelper,headers}.go`, `internal/boot/boot_api.go`, `internal/provider/passport_handler.go`,
  `pkg/passport/handler.go`, `internal/routing/shadowgateway/*.go`, `internal/config/config.go`, `internal/constants/headers.go`,
  `config/{default,devstack,sample}.toml`, `pkg/api/client.go`, `pkg/workflow/workflow_create.go`
- `api` (monolith) migrations: `2014_04_23_221828_create_keys.php`, `2017_03_28_104101_create_merchant_users_table.php`,
  `app/Models/Key/Entity.php`
- `batch/src/main/resources/payout_approval.json` (spot-check for `bulk_approve` caller)
- ENV2 twin: `ENV2_COMPOSE/substitutes/kong-lite/{server.py,CONTRACT.md}`, `ENV2_COMPOSE/secrets/gen-secrets.sh`,
  `ENV2_COMPOSE/config/templates/base/payouts/arena.toml`, `ENV2_COMPOSE/seeds/merchants.json`,
  `ENV2_COMPOSE/verifier/helpers/passport.py`

All claims below carry `repo/path:line` provenance. Where I re-derived a fact already in `21_edge_kong_authz.md`
or `24_shared_libs_proto.md` from source myself, I mark it CONFIRMED (re-verified) and cite my own read,
not just the prior report.

---

## Production behaviour

### 1. Kong route/plugin table

Baseline is `raw-findings/21_edge_kong_authz.md` §1 (CONFIRMED, re-verified spot checks below); it is
accurate and should be read as the primary route table for payouts/contacts/fund_accounts/payout-links.
Spot-checks performed this pass:

| Route | File:line | Auth bucket | Upstream | Notes |
|---|---|---|---|---|
| `POST/GET /v1/fund_accounts` | `terraform-kong/prod/api/api.tf:156-166` (`fund_account_create-prod-api`), `:993` | `authenticated_paths` | monolith | CONFIRMED — re-read, matches 21§1.1 |
| `POST/GET /v1/contacts` | `terraform-kong/prod/api/api.tf:141,164-165` (`contact_create-prod-api`) | `authenticated_paths` | monolith | CONFIRMED |
| `GET /v1/transactions/{id}` | `terraform-kong/prod/api/api.tf:2291-2296` (`transaction_statement_fetch-prod-api`) | same `authenticated_paths` bucket, `rollout=1, impersonation_rollout=1, passport_enabler_config = local.passport_enabler_global_config` as payout routes | monolith | **NEW — not enumerated in 21_edge_kong_authz.md.** This is the X banking "transactions" (statement-of-account) listing route the task named; it shares the exact same passport-enabler/rollout config as `/v1/payouts`, so everything in §2 below (claim schema, mode/impersonation derivation) applies identically. |
| `GET /v1/transactions_banking` | `terraform-kong/prod/api/api.tf:2968-2973` (`transaction_statement_fetch_multiple_for_banking-prod-api`) | same bucket | monolith | NEW, same note |
| `GET /v1/payout-links/{id}/view` | `terraform-kong/templates/payout-links/x-customer-payout-links.tf` on `payout-links.razorpay.com` | plain `paths`, customer-facing, no Kong auth wall | `x-customer-payout-links.razorpay.com` | CONFIRMED via `terraform-kong/prod/payout-links/config.tf:1-30` (module wiring, `payout-links_route_preserve_host=false`, target host `x-customer-payout-links.razorpay.com:443`) |

No `/v1/transactions*` route is diverted by `payouts-proxy-cutover` anywhere (grepped; only `payout_create`/`payout_fetch_by_id`/timeslots are in the cutover's whitelist per 21§1.3) — it stays monolith-only in every environment, same as payouts today.

**Rate limiting**: re-confirmed 0 hits for `rate-limiting`/`rate_limiting` plugin blocks anywhere in `terraform-kong/prod/api/api.tf` (`grep -c` = 0). Matches 21§4/W6/C43 exactly.

### 2. Passport (`X-Passport-JWT-V1`) — minting (Edge) and verification (PS)

Minting mechanics: CONFIRMED as documented in `21_edge_kong_authz.md` §2 (`edge/kong-plugins/kong-plugin-upstream-jwt/kong/plugins/upstream-jwt/access.lua:86-205`) — RS256, hardcoded 300s TTL, `kid` from static config, no JWKS in Edge's own runtime.

**Claim schema (wire-verified against `goutils/passport/passport.go`, re-read this pass, CONFIRMED matches `24_shared_libs_proto.md` §1 exactly):**

| Claim | Type | Source | Example |
|---|---|---|---|
| `iss`,`sub`,`aud`,`exp`,`nbf`,`iat`,`jti` | string/int (embedded `*jwt.StandardClaims`) | Edge config / current time | `iss="https://edge.razorpay.com"`, `sub="https://payouts.razorpay.com"` |
| `authenticated` | bool | Kong ctx `authenticated` flag | `true` |
| `identified` | bool | Kong ctx `identified` flag | `true` |
| `mode` | string, omitempty | `rzp_mode` ctx (key prefix or explicit) | `"live"`/`"test"` |
| `domain` | string, omitempty, deprecated | fallback `"razorpay"` | — |
| `org` | string, omitempty | e.g. `"100000_razorpay"` (Edge) / `"100000razorpay"` (Kong terraform var) — **NOTE: two different literal formats seen across repos** (underscore vs none); not reconciled from either repo alone | — |
| `product` | string, omitempty | e.g. `"banking"` | — |
| `roles` | `[]string`, omitempty | `passport_roles`/`authorization_roles` ctx | `["r~finance"]` |
| `consumer` | `*ConsumerClaims{ID,Type,Meta}` | resolved consumer; `Type` ∈ `user\|merchant\|admin\|application` | `{"id":"M10000000000000","type":"merchant"}` |
| `oauth` | `*OAuthClaims{OwnerType,OwnerID,ClientID,AppID,Env,AccessTokenID}`, omitempty | jwt-x internal-app flow | — |
| `impersonation` | `*ImpersonationClaims{Type,Consumer}` **— no JSON tags in v4 source, so wire keys are `Type`/`Consumer` (capitalized), not `type`/`consumer`** (`goutils/passport/passport.go:59-62`, CONFIRMED — this is a real upstream bug/oversight, not a typo in the report) | two producers, see below | — |
| `credential` | `*CredentialClaims{Username,PublicKey,SecretRefID,ExpiryTime}`, omitempty | auth plugin | — |
| `additional_identities` | `map[string][]*ConsumerClaims`, omitempty | rollout-gated | — |

Header: `X-Passport-JWT-V1` (`goutils/passport.HeaderKeyPassportJWTV1`, wired at `payouts/internal/routing/middleware/passport.go:37`).

**Two independent producers of the `impersonation` claim** (CONFIRMED, `21_edge_kong_authz.md` §2.2):
- API-key impersonation (partner acting on sub-merchant): `edge/kong-plugins/kong-plugin-impersonation-grant/kong/plugins/impersonation-grant/extractor.lua:4-8` — the account/sub-merchant id is extracted from, in priority order, header `X-Razorpay-Account`, query param `account_id`, or body field `account_id` (`source_key_map`, re-read this pass, CONFIRMED). `access.lua` looks up `principal_type . "~" . consumer_ext_id` against a `kong.db.impersonation_grant` DAO row before granting.
- Dashboard-proxy impersonation (`user-auth` plugin, non-admin branch): always `impersonation={grant.principal_type="user_merchant", consumer={username=<merchant_id>, type="merchant"}}` (21§2.2). Admin-auth branch (`X-Admin-Token`) sets no `impersonation` at all.

**`X-Razorpay-Account` is consumed entirely at the Kong layer, never seen by PS as a header** for the
Edge-fronted path — it is folded into the passport's `impersonation.consumer.id` before the request
reaches any upstream. PS-internal callers (payout-links, vendor-payments) additionally send
`X-Razorpay-Account` themselves when calling `payouts_internal`/`internal_contact_payout` routes
(`raw-findings/09_variants_public_api.md:114-119`, CONFIRMED against that file) — but PS never reads
this header directly (grep of `payouts/internal` for `Razorpay-Account`/`RazorpayAccount` returns zero
hits outside `payouts/pkg/workflow/workflow_create.go:89,336` (an **outbound** header PS itself sets
when calling Workflow Service, value = `payout.GetMerchantID()`) and `payouts/pkg/api/client.go:25`
(outbound-only constant `XRxAccount`, used when PS calls the monolith for pricing/credits/FTS-create).
So on the *inbound* side, sub-merchant/tenant resolution for PS always goes through the passport's
`consumer`/`impersonation.consumer` claims — there is no header-based override path into PS.

**PS-side verification and claim mapping** (CONFIRMED, direct read):
- `payouts` uses **passport v3** (`go.mod`: `github.com/razorpay/goutils/passport/v3 v3.4.0`; import in
  `payouts/internal/routing/middleware/passport.go:9`, `payouts/pkg/passport/handler.go:6`), **not** v4/JWKS.
  v3 is static-key: `payouts/internal/provider/passport_handler.go:23-43` builds a `passport.Client` from
  `config.Passport` (`[passport.api]`/`[passport.edge]`), and `payouts/pkg/passport/handler.go:39-58` loads
  exactly two named keys via `passportSdk.NewJwtKeyIdentifier(issuer, publicKeyPEM)` — `apiv1` (key minted by
  the API monolith itself, when it calls PS with its own passport) and `edgev1`/`edgev2` (key minted by Edge).
  `config/default.toml:411-417`: `[passport.api] identifier="apiv1"`; `[passport.edge] identifier="edgev1"`.
  `config/devstack.toml:171-181`: `[passport.edge] identifier="edgev2"` (devstack/base/stage cells share
  `edgev2`, per the in-file comment referencing `terraform-kong module/plugins.tf:282` and
  `base/api/api.tf:5067`).
- Verification entrypoint: `payouts/internal/routing/middleware/passport.go:37-58` — reads
  `X-Passport-JWT-V1`, calls `ph.FromToken(jwtToken)`. **No header present → `401` (`respondWithJwtTokenNotPresent`,
  errorclass `JwtTokenNotPresentError`, `errors.BadRequestError` public code but `http.StatusUnauthorized`
  wire status)** (`passport.go:142-155`). Parse failure → `500` (`respondWithPassportNotParsedFromTokenError`).
  There is **no fallback to header-only auth** in this middleware — if a route requires
  `PassportAuthentication`, a missing/invalid passport always fails the request regardless of BasicAuth
  status.
- Claim → identity mapping (`payouts/internal/auth/{authHelper,headers}.go`, CONFIRMED by direct read):
  - `GetLegacyAuthType` — delegates to `passportSdk.GetLegacyAuthType(p)` (v3 API, same decision table as
    v4's `helpers.go` reconstructed in `24_shared_libs_proto.md`: `direct`/`public`/`admin`/`privilege`/
    `private`/`proxy`).
  - `getMerchantIdFromPassport` (`internal/auth/authHelper.go:173-208`): if `authType == "private"` → merchant
    id = `consumer.ID` (consumer must be type `merchant`); **else** (any other auth type, i.e. `proxy`/
    `privilege`/`admin`) → merchant id = `impersonation.Consumer.ID` **only if** that consumer's `Type ==
    "merchant"`. So `mode`/tenant scoping for a proxied (dashboard-user / partner-impersonation) request
    resolves through `impersonation.consumer`, never through the top-level `consumer` claim.
  - `GetMerchantIdFromContext` (`internal/auth/authHelper.go:12-25`) tries passport first, **falls back to
    header** `getMerchantIdFromHeaders` (context key `merchant_id`, populated from request header
    `x-merchant-id`, see §3) only if the passport-derived value is empty.
  - `GetUserIdFromCtx` (`internal/auth/passport.go:142-157`): passport `consumer.ID` (if `consumer.Type ==
    "user"`) → else, if privilege/internal-app auth, context key `app_user_id` (header `App-User-ID`) → else
    header-derived `user_id` (populated from `x-creator-id` when `x-creator-type == "user"`, batch's
    convention — see §3).
  - `GetAppNameFromPassport` reads `consumer.Meta["name"]` — used to disambiguate which internal app is
    calling under `LegacyAuthTypePrivilege` (`payout-links`, `batch`, `x_payroll`, etc. — allowlist in
    `internal/auth/headers.go:11-25`, `allowedInternalApps`).
  - **`mode` (live/test)**: `auth.GetMode(ctx)` (`internal/auth/authHelper.go:106-116`) reads the passport's
    top-level `mode` claim directly — no header override path found for `mode` in PS's own auth package.

**Two `identifier`/`kid` naming inconsistencies confirmed, worth flagging to the owning teams (not
resolvable from repos alone)**:
1. `payouts/config/default.toml` uses `edgev1` for `[passport.edge]`, while `devstack.toml` (and, per its own
   comment, `base`/`stage`/`non-prod-cells`) use `edgev2`. Prod's actual effective value is **not** visible
   in this shallow clone (only `default.toml`/`devstack.toml`/`sample.toml` exist; no `prod.toml` for
   payouts was found in this clone) — see Cannot-derive list.
2. `org` claim literal format differs across repos (`100000_razorpay` in `goutils/passport` examples and
   `ledger-sdk`, `100000razorpay` in `terraform-kong` variables) — not reconciled from either repo.

### 3. Service-to-service (Basic-Auth) callers into PS — `[auth.*]` and route wiring

`payouts/config/default.toml:93-132` `[auth]` sections (names only, secrets are placeholders/`env|` refs
in every clone read):

| Section | Username (default.toml) | Caller (from route wiring, confirmed by direct read of `internal/routing/router/*.go`) |
|---|---|---|
| `[auth.api]` | `api` | API monolith's internal hop into PS — the widest-scoped credential; appears on almost every route group (`payout_routes.go:20`, `payout_admin_routes.go:16`, `payout_bulk_routes.go:17`, `payout_proxy_routes.go:17`, `payout_routes_v2.go:17`, `payout_internal_routes.go:16`, `internal_action_routes.go:15`, `fund_account_validation_routes.go:17`, `merchant_routes.go:15`, `workflow_routes.go:15`, `elasticsearch_internal_routes.go:15`, `ikey_excusions_routes.go:16`, `payout_status_details.go:14`, `fund_management_payout_admin_routes.go:17`, `internal_routes.go:36`) |
| `[auth.workflow]` | `rzp_live` | Workflow Service (WFS) callback into PS — `payout_routes.go:20` (`/v1/payouts`), `payout_internal_routes.go:16`, `fund_account_validation_routes.go:17`, `workflow_routes.go:15`, `bas_internal_routes.go:15`, `payout_status_details.go:14` |
| `[auth.fts]` | `fts` | FTS webhook/status-update caller — `payout_internal_routes.go:16`, `internal_routes.go:14` (`/v1/notify`, dedicated), `bas_internal_routes.go:31`, `payout_status_details.go:14` |
| `[auth.fastcron]` | `fast_cron` | FastCron SaaS — the **only** credential on `cron_routes.go:15` (`/v1/cron/*`, exhaustively listed above) and one of two on `merchant_onboarding_routes.go:14` and `fund_management_payout_admin_routes.go:17` |
| `[auth.xperience]` | env-sourced | Xperience (downtime/status carve-off service) — `payout_internal_routes.go:16`, `payout_status_details.go:14` |
| `[auth.vendorpayments]` | `vp_user` | Vendor Payments internal app — `payout_internal_routes.go:16` only |
| `[auth.settlements]` | env-sourced | Settlements Service — `payout_internal_routes.go:16` only |
| `[auth.irctc]` | env-sourced | IRCTC internal caller — `payout_internal_routes.go:16` only |
| `[auth.bankingaccounts]` | `banking_accounts_user` | Banking Accounts service — `merchant_onboarding_routes.go:14` only |
| `[auth.xbalances]` | `x_balances` | x-balances internal caller — `xbalances_internal_routes.go:16` (`/v1/internal`) only |
| `[auth.merchantconfiguration]` | `merchant_configuration` | dedicated caller for `/v1/admin/merchant-configuration` (`merchant_configuration_routes.go:13`) — identity of the real caller not determinable from this repo (name suggests a config-management tool/dashboard-BFF) |
| `[auth.dashboard]`, `[auth.reminder]` | `dashboard`/`reminder` | **Declared in config but zero references found anywhere in `internal/routing/router/*.go`** — dead/unwired credentials as far as this repo shows (INFERRED; could be consumed by a binary/route not in this clone) |

**No dedicated "dashboard BFF" or "admin" Basic-Auth credential exists** — the admin surface
(`/v1/admin`, `/v1/admin/internal_actions`) is guarded by `BasicAuth(cred.API)` (the same monolith
credential) **plus** `PassportAuthentication([]string{passportSdk.LegacyAuthTypeAdmin})`
(`payout_admin_routes.go:16-17`, `internal_action_routes.go:15-16`) — i.e., admin identity/role comes
entirely from the passport's `admin` legacy-auth-type + `consumer.Meta["org_id"]`/`roles`, minted upstream
by Edge's `user-auth` plugin on `X-Admin-Token`, not from a PS-local admin credential.

**Precedence between passport and headers, exact (re-derived from `internal/auth/*.go`, direct read):**
1. **Merchant identity**: passport (`private`→`consumer.id`; else→`impersonation.consumer.id` if type
   merchant) **first**; header `x-merchant-id` (context key `merchant_id`, only set into context if the
   monolith/batch explicitly sent it — `request_context.go:69-75`, also overridable to `X-Entity-Id`'s value
   for batch-originated calls, `request_context.go:47-53`, `constants.HeaderMerchantIDFromBatch = "X-Entity-Id"`)
   only as **fallback** if the passport path returns empty.
2. **User identity**: passport `consumer.id` (type `user`) first; else `app_user_id` context (header
   `App-User-ID`) if privilege/internal-app auth; else header-derived `user_id` context (only populated when
   `x-creator-type == "user"`, setting it from `x-creator-id` — Batch's convention of stamping the acting
   dashboard user as "creator", `request_context.go:109-116`).
3. **Actor id/type** (used by the idempotency-key middleware's fallback path, `idempotency_key.go:87-93`):
   read directly from headers `X-Payout-Actor-Id`/`X-Payout-Actor-Type` into context, with an explicit code
   comment (`idempotency_key.go:80-86`) framing this as a fallback for "passport authentication incomplete"
   scenarios (proxy servers stripping the passport, load balancers mutating headers, legacy callers) —
   headers here are a deliberate escape hatch, not a competing source of truth; passport is authoritative
   when both authenticate types are supported on the route.
4. There is **no generic precedence rule** enforced centrally — each of merchant-id/user-id/actor-id has its
   own bespoke fallback chain implemented ad hoc in `internal/auth/*.go`; a caller sending both a valid
   passport and a conflicting `x-merchant-id` header gets the **passport value silently, with the header
   ignored** (fallback code paths are `if merchantId == "" { …headers… }`, never a mismatch check).

**Idempotency header** (task item 6): `X-Payout-Idempotency` (`payouts/internal/constants/headers.go:22`,
`HeaderXPayoutIdempotency`), used for the `payout` entity via `EntityToHeaderMap` in
`internal/routing/middleware/idempotency_key.go:38-40`. Confirmed public-facing name matches
`raw-findings/09_variants_public_api.md:40,147,163` (documented in `api/x/payout-idempotency.md`: same
header + identical body required for safe retry within 7 calendar days; different body ⇒ `BAD_REQUEST`).
**PS-side mechanics** (`idempotency_key.go`, direct read):
- Per-(idempotencyKey, merchantID) Redis mutex, key = `idempotencyKey + merchantID`, TTL **20 minutes**
  (`MutexTimeOutForIdempotencyKey = 20 * time.Minute`, line 34) via `controllers.BaseService.AcquireMutex`.
- Missing-key handling is **feature-flagged**: `provider.GetConfig(ctx).Features.IKeyAutoEnforcement` (bool,
  **not set in `default.toml`** → Go zero-value `false`, i.e. idempotency-key absence is **not enforced by
  default**) gated further by a Splitz experiment `SplitzExperimentList.IKeyAutoEnforcementRampUp` — only
  when both are true does a missing key become a hard `400` (`MissingIdempotencyKeyInRequest`), and even
  then a merchant/entity-specific exclusion list (`CheckIdempotencyExclusion`) can still bypass it.
- Request replay-safety check: SHA-256 hash of the canonicalized (alphabetical-key JSON) request body,
  compared against the stored hash for that key; mismatch → `400 SameIdempotencyKeyDifferentRequest`.
- **Kong/Edge does not touch this header at all** — 21§1.3/§4 confirm no header-rewrite plugin targets
  `X-Payout-Idempotency` on any payout route; it passes through Kong unmodified end-to-end,
  caller→Kong→(monolith or PS).

### 4. AuthZ — enforcer mode, policy model, route reach

Baseline `21_edge_kong_authz.md` §2.4/§3 is CONFIRMED (re-read `edge/kong-plugins/kong-plugin-authz-enforcer/…/handler.lua`
priority/shadow-mode logic descriptions match; re-read `goutils/authz/v2/enforcer/model.go` directly this
pass — the Casbin model text is byte-identical to what 24§2 quoted):
```
[request_definition] r = sub, org, svc, obj, act
[policy_definition]  p = sub, org, svc, obj, act, eft
[role_definition]    g = _, _, _
[policy_effect]      e = some(where (p.eft == allow))
[matchers]
m = g(r.sub, p.sub, r.org) && r.org == p.org && r.svc == p.svc && KeyMatch(r.obj, p.obj) &&
    ((r.act == "head" && p.act == "get") || r.act == p.act || p.act == "*")
```
(`goutils/authz/enforcer/model.go:3-18`, CONFIRMED direct read.)

`authz/config/prod.toml:51` `watchResourceGroups = ["gateway", "capital", "platform_identity"]` —
**re-confirmed this pass by direct read**; `x_platform` is present in `bvt.toml:42`, `stage.toml:52`,
`slit.toml:50`, `dev-serve.toml:53`, but **absent from prod.toml**. This is exactly C46; independently
reproduced.

**Critically — PS itself never calls AuthZ.** `payouts/go.mod` has no `require` on
`github.com/razorpay/goutils/authz` (grepped, zero hits), and no route file imports any authz package.
The only enforcement point for the `authz-enforcer` Kong plugin is at Edge, in `shadow_mode=true`
(non-blocking) for the public API surface and enforced (`shadow_mode=false`) for the dashboard-merchant
surface — but **PS-side approve/reject/cancel routes have no independent role check of their own**:
`payout_internal_routes.go:12-18` (`/payouts_internal/:payout_id/approve`, `.../reject`) is guarded by
**BasicAuth only** (`cred.API, cred.Workflow, cred.Xperience, cred.FTS, cred.VendorPayments, cred.Settlements,
cred.Irctc`) — **no `PassportAuthentication` middleware at all** on this route group, confirmed by direct
read (no `middleware.PassportAuthentication` call anywhere in that file). PS trusts whichever caller
authenticated with a valid service Basic-Auth pair to have already done role/approval enforcement
upstream (monolith's `AdminAccess::policyChecker` for admin/ops flows, or WFS's own unauthenticated
actor-property matching per `ARCHITECTURE_DELTA.md` §(a) Workflow Service paragraph). This matches and
reinforces C44/C48: **there is no independent AuthZ or role check inside PS itself**, anywhere.

**Payout permission CSV rows** (task item 4): baseline table in `21_edge_kong_authz.md` §3.2/§3.5 is
CONFIRMED (re-read `authz/scripts/policies/xplatform.csv` structure and the CAC/`payout_approve_reject_view`
reconciliation-gap finding — not re-quoted here to avoid duplication; see that file for the full CSV).

**How role reaches Kong**: for dashboard-user traffic, `roles` in the passport come from `passport_roles`
(preferred) or `authorization_roles` Kong ctx keys (`upstream-jwt/access.lua`, per 21§2.1) — themselves set
by the monolith's internal `/v1/edge/internal/authenticate` synchronous call that `kong-plugin-user-auth`
makes (endpoint confirmed to exist and be called; its internal role-resolution logic lives in `api`,
out of this lane's repo set — see Cannot-derive list). For API-key/service auth, `roles` come from whatever
the `keys`/merchant record carries — **not found wired to any real role source in `edge`'s own repo** for
that path; production API-key auth passports observed in this pass's example payload (21§2.2a) show
illustrative-only role values, not a confirmed mechanism.

### 5. Tenant model — mode, sub-merchant, partner/OAuth, impersonation

- **mode (live/test)**: passport top-level claim, set by Edge from the API-key prefix (`rzp_live_`/`rzp_test_`)
  for API-key auth, or from Kong's `rzp_mode` ctx for dashboard-proxy traffic (`request_mode` in the cutover's
  own dashboard override conditions, 21§1.2). PS reads it via `auth.GetMode(ctx)` — no independent mode
  derivation logic in PS (CONFIRMED, `internal/auth/authHelper.go:106-116` is a direct passthrough).
- **Sub-merchant / partner-on-behalf-of (`X-Razorpay-Account`)**: resolved entirely at Edge into
  `impersonation.consumer` (§2 above); PS has no header-based override, and no `partner`/`oauth`-specific
  code path exists anywhere in `payouts/internal` (grepped `"partner"`, `Partner\b`, `OAuth`, `oauth`,
  `Impersonat` across `payouts/internal` — the only `Impersonat` hits are the generic passport-claim
  accessors already documented in §2/§3; there is no OAuth-app-specific business logic, no partner-specific
  branch). A partner acting via API-key-impersonation and a dashboard user acting via session-impersonation
  are **indistinguishable to PS** — both simply resolve to `impersonation.consumer.id` (type merchant) as
  the effective merchant id; the `oauth.*` claim block itself is never read by `payouts/internal/auth` (only
  `goutils/passport`'s own `GetResourceOwnerID` helper — unused by payouts — would fall back to it).
- **Impersonation-aware routing decision** (new, not covered by 21_edge_kong_authz.md — see §6): PS's own
  `internal/routing/shadowgateway/mode.go:150-200` (`merchantIdentityFromPassport`) independently
  re-implements the exact same two-branch resolution (`consumer.type==merchant` else
  `impersonation.consumer.type==merchant`) purely for shadow-gateway routing classification, explicitly
  documented in its own comment as "never authentication" — a second, PS-internal confirmation of the
  tenant-resolution algorithm in §2/§3, independently arrived at for a different purpose.

### 6. NEW — PS's own shadow-gateway framework (`internal/routing/shadowgateway`)

**Not documented in `21_edge_kong_authz.md` or `ARCHITECTURE_DELTA.md`** (neither mentions this package;
it postdates or was missed by that pass). This is squarely in-lane because it (a) reads the passport for
routing decisions, (b) implements its own loop-guard header, and (c) determines whether the monolith or PS
ultimately serves a request — directly relevant to "how api consumes the passport and what it forwards to
PS," from the opposite direction of the Edge→PS divert documented in 21.

- Purpose: a config-driven (`[shadow_gateway]`, `payouts/config/default.toml:976-991`) per-route framework
  that can proxy a request straight through to the API monolith (`ModeProxy`, the fail-safe default), or run
  both PS and the monolith and honor one side's response while comparing against the other
  (`ModeShadowHonorAPI`/`ModeShadowHonorPS`), or do nothing (`ModeNative`).
- **Kill switch**: `enabled` (bool). `default.toml:978`: `false` (fully inert). `devstack.toml:711`: `true`,
  with exactly one route onboarded (`get /v1/payouts/schedule/timeslots`, Splitz experiment
  `TUnTsUB8Os2kX0`) — this is the same pilot route named in 21§1.2/§1.3 for the dashboard-proxy cutover,
  now confirmed to also be gated a second time by this PS-internal framework, not just Kong's
  `dashboard_override_conditions`.
- **Mode resolution** (`internal/routing/shadowgateway/mode.go:58-117`): per-route Splitz experiment lookup,
  keyed by merchant id; **any failure (missing merchant identity, Splitz client nil, Splitz error/timeout,
  unknown variant) resolves to `ModeProxy`** — fail-safe posture identical in spirit to Kong's `shadow_mode`
  default. Shadow modes are hard-restricted to `GET` (`mode.go:106-114`) — a non-GET route configured for
  shadow is forced back to proxy with an error log, since shadow comparison requires safely re-executing a
  buffered request copy.
- **Merchant identity for routing** (`mode.go:150-200`, quoted in §5): re-verifies the passport itself
  (`ph.FromToken`, requires `p.IsAuthenticated()`) rather than trusting any pre-parsed context value — this
  routing-layer check runs **before** the `PassportAuthentication` middleware in the normal sense, so it is
  a second, independent passport parse per request when the framework is enabled.
- **Loop guard**: `HeaderPSProxied = "X-PS-Proxied"` (`middleware.go:18`). Every request PS proxies to the
  monolith (`monolith_client.go:118-138`, `proxy()`) gets this header **appended** before forwarding
  (`monolith_client.go:183`, `req.Header.Set(HeaderPSProxied, "true")`). On the way in, if a request already
  carries this header, the gateway **never intercepts again** — it runs the request natively through PS
  (`middleware.go:91-96`). **This is a real, working loop guard — but it guards the PS→monolith leg of this
  specific framework, not the Edge→PS direct-divert design** that 21§2.3/§4/C45 correctly found has no loop
  guard at the Edge/Kong layer. Both statements are true simultaneously: Edge/Kong has none; PS's own
  shadow-gateway (a different, newer mechanism, config-disabled by default) has one for its own traffic
  direction.
- **Internal-consumer bypass** (`internal_consumer.go:43-54`): any request whose Basic-Auth username matches
  one of PS's own configured internal-caller credentials (`internalConsumerCreds(auth)` — same `[auth.*]`
  table as §3) is **never** intercepted by the shadow gateway, always runs native — the explicit rationale
  in-code is that the monolith cannot authenticate PS-internal service credentials, so proxying an
  internal-caller request to the monolith would simply fail auth there.

### 7. Rate limiting / IP allowlist / loop guard — final state

- **Rate limiting**: none, confirmed twice now (21§4 original grep; this pass's independent
  `grep -c "rate-limiting\|rate_limiting" terraform-kong/prod/api/api.tf` → `0`).
- **IP allowlist**: `ip-restriction-x` present but `ip_restrictions_rollout=0` (off) on payout routes — CONFIRMED
  baseline (21§1.3/§4), not independently re-run this pass (no new evidence found or sought beyond the prior
  report's grep, which is exhaustive by construction).
- **Loop guard**: **two, in different places** — see §6. Edge/Kong: none (C45 stands for the Edge→PS direct
  divert design). PS↔monolith shadow-gateway: `X-PS-Proxied`, real, working, config-disabled by default in
  every environment this clone shows.

---

## Corrections to existing reports

| Report | Claim | What source says | Evidence |
|---|---|---|---|
| `raw-findings/21_edge_kong_authz.md` §2.3/§4/§7 item 7, `ARCHITECTURE_DELTA.md` C45/Flow-G | "No loop guard mechanism of any kind exists in either repo… confirmed absent, not merely unfound." | True for `edge`/`terraform-kong` (Edge→PS direct-divert design). **But `payouts` itself implements a real loop guard, `X-PS-Proxied`, for its own PS→monolith shadow-gateway leg** — a mechanism the prior pass did not read (the `internal/routing/shadowgateway` package is not cited anywhere in 21 or the ARCHITECTURE_DELTA). Needs a caveat: "no loop guard at the Edge/Kong layer for the Edge→PS divert; PS's own shadow-gateway framework (config-disabled by default) has an unrelated, working loop guard for its PS→monolith leg." | `payouts/internal/routing/shadowgateway/middleware.go:14-18,91-96`; `monolith_client.go:114-138,183` |
| `21_edge_kong_authz.md` §5 point 1 (Kong-lite spec, API-key auth) | "username = merchant API key (`rzp_live_...`/`rzp_test_...`, regex `^rzp_(\w{4})_(\w{14})$`)" — implicitly treats the 14-char group as merchant-id-bearing | The `\w{14}` group is a **random base62 key id, independent of the merchant id** — `Key::generateUniqueId()` builds it from 4×`random_bytes(4)` base62-encoded chunks, with no merchant-id input at all. The merchant id is recovered only via the `keys` table's `merchant_id` foreign-key column, never from the key string itself. | `api/app/Models/Key/Entity.php:261-282` (`generateUniqueId`), `:308` (`stripSign`); `api/database/migrations/2014_04_23_221828_create_keys.php:19-38` |
| ENV2 twin, `substitutes/kong-lite/seeds/merchants.json` + `CONTRACT.md` | Embeds the merchant id directly in the key suffix (`rzp_test_ARENAM00000001`) as if that were how production keys work | This is a **simplification, not a faithful reproduction** — real key ids carry no merchant-id information; a real Kong/monolith would need a keys-table lookup. Twin verdict below reflects this as REPRESENTATIVE, not CONTRACT-FAITHFUL, on this specific point. | `ENV2_COMPOSE/seeds/merchants.json:1-24` vs. `api/app/Models/Key/Entity.php:261-282` |
| ENV2 twin, `verifier/helpers/passport.py` docstring | "Real `payouts-api` validates this header as an RS256 JWT checked against a JWKS endpoint (`goutils/passport/handler.go`)" | Payouts uses **passport v3** (static per-`kid` public keys loaded from TOML config), not v4's JWKS-fetching `handler.go` — the docstring describes the wrong major version's verification mechanism, though the *practical* consequence (RS256-only, no HS256 fallback, `kid`-keyed lookup) happens to hold for both versions, so the helper's actual behavior (delegating to `kong-lite`'s static-key signer) is still correct. Doc-only inaccuracy, not a functional bug. | `ENV2_COMPOSE/verifier/helpers/passport.py:1-9` vs. `payouts/pkg/passport/handler.go:1-58`, `payouts/go.mod` (`goutils/passport/v3`) |
| ENV2 twin, `kong-lite/CONTRACT.md:60-61` | "`kid` matches `config/templates/payouts.toml.tmpl`'s `[passport.arena] identifier` exactly" | The actual template section is `[passport.edge]`, not `[passport.arena]` — `arena.toml:446-448` shows `[passport.edge] identifier = "arena-passport-1"`. Functionally correct (the `kid` does match), just a stale section name in the twin's own contract doc. | `ENV2_COMPOSE/config/templates/base/payouts/arena.toml:442-448` |
| `21_edge_kong_authz.md` (route table, task item 1) | Enumerates payouts/contacts/fund_accounts/payout-links but not `/v1/transactions*` | `GET /v1/transactions/{id}` and `GET /v1/transactions_banking` exist in `prod/api/api.tf` on the identical `authenticated_paths`/passport-enabler bucket as payout routes — omission, not contradiction; the task for this lane explicitly named "transactions" so it's recorded here. | `terraform-kong/prod/api/api.tf:2291-2296,2968-2973` |
| `24_shared_libs_proto.md` §1 | Describes v4's `ImpersonationClaims` lacking JSON tags as a bug, without stating the wire-format consequence explicitly | Confirmed and made explicit here: wire keys are `Type`/`Consumer` (capitalized) for the impersonation object specifically — any local minter must emit capitalized keys for this one nested object or v4 consumers (e.g. `ledger`) will fail to deserialize it; v3 (`payouts`'s own version) was not independently checked for the same defect (payouts never reads impersonation claims off the wire via generic JSON — it goes through the SDK's own accessor, so this would only bite a custom minter, e.g. `kong-lite`, if it ever needs to mint an impersonation claim, which it currently does not). | `goutils/passport/passport.go:59-62` |

---

## Twin comparison

| Component | Production mechanism | Twin mechanism (file) | Verdict | What differs | Controls |
|---|---|---|---|---|---|
| Kong reverse proxy | Kong OSS + Lua plugin chain (`basic-auth-x`, `consumer-authentication`, `impersonation-grant`, `upstream-jwt`, `authz-enforcer`, `ip-restriction-x`, `user-auth`), declarative Terraform config, priority-ordered handlers | `ENV2_COMPOSE/substitutes/kong-lite/server.py` — single-file Python `http.server` reverse proxy | REPRESENTATIVE SUBSTITUTE | No real Kong runtime, no plugin priority chain, no consumer/DB-backed auth, no rollout percentages, no `ip-restriction-x`/rate-limiting (both are off in prod anyway, so their absence is contract-faithful by coincidence), no impersonation-grant (`X-Razorpay-Account`) handling at all — kong-lite only mints `consumer.type=merchant` passports, never `impersonation` | auth (partial — API-key path only), tenant (merchant-only, no sub-merchant impersonation), routing (single static route table, no rollout/cutover logic) |
| Passport minting | Edge `upstream-jwt` Lua plugin, RS256, hardcoded 300s TTL, full claim schema (§2) | `kong-lite/server.py:_mint_passport_jwt` — pure-stdlib RS256 signer, same claim shape for the `private`/merchant-API-key case only | CONTRACT-FAITHFUL (for API-key auth only) | Only mints the `consumer.type=merchant` shape (21§2.2a); never mints `impersonation`, `oauth`, admin, or identification-only (both-false) shapes — those auth flows have no twin coverage at all | auth, tenant |
| Passport verification (PS side) | `goutils/passport/v3`, static per-`kid` public key map, loaded from `[passport.api]`/`[passport.edge]` TOML | Real payouts binary running against the twin's `arena.toml` `[passport.edge] identifier="arena-passport-1"` | REAL (this is the real `payouts` binary, unmodified) | None on the verification side — kong-lite's `kid` (`arena-passport-1`) is provisioned to match the real config exactly (`gen-secrets.sh:64-70`, `arena.toml:447-448`) | auth |
| AuthZ enforcer | `authz` service + Casbin + Consul-watch, `shadow_mode` per route, real policy CSVs | **Not present in ENV2 substitutes list at all** (no `authz-stub`/`casbin` substitute found under `ENV2_COMPOSE/substitutes/`) | MISSING (by design — matches production reality that PS itself never calls AuthZ, so its absence has zero behavioral effect on PS) | N/A — correctly omitted, since no in-scope service (`payouts`) calls out to AuthZ | approval (N/A — real production also has no PS-side authz call) |
| Service Basic-Auth (`[auth.*]`) | Static per-caller username/password pairs in payouts TOML | `arena.toml:114-153` mirrors every section 1:1, secrets templated from `gen-secrets.sh`-generated files | REAL (same config shape, freshly generated secrets) | None structural; `[auth.dashboard]`/`[auth.reminder]` are carried forward even though this pass confirms they're unwired in the real router too (so twin fidelity here is accidentally correct) | auth |
| PS shadow-gateway (`[shadow_gateway]`) | Config-driven, disabled by default in prod (`default.toml:978`), one pilot route enabled in devstack | `arena.toml:1026-1040` — `enabled = false`, identical structure to `default.toml`, no routes onboarded | CONTRACT-FAITHFUL (disabled state correctly mirrored) | Twin never exercises the enabled/pilot-route state (devstack's `[shadow_gateway.routes."get /v1/payouts/schedule/timeslots"]`) — acceptable since that pilot is explicitly experimental and per §6 fails safe to proxy on any error | routing (inert in twin, matching prod's default posture) |
| Idempotency key handling | PS-side Redis mutex + request-hash replay-safety, `X-Payout-Idempotency`, feature-flagged enforcement | Not examined in ENV2 substitutes (out of kong-lite's scope; this is PS-internal logic, runs unmodified in the real `payouts-api` binary the twin launches) | REAL (same binary) | None — this logic isn't part of any substitute, it's the real payouts service | idempotency |

---

## Recommendation: real vs substitute

**Kong**: **SUBSTITUTE, correctly chosen.** Running real Kong + the full custom Lua plugin set
(`upstream-jwt`, `impersonation-grant`, `authz-enforcer`, `consumer-authentication`, `user-auth`, etc.)
locally would require: a Kong/OpenResty runtime, a Postgres-backed Kong database (consumers, credentials,
plugin configs), the entire `kong-plugin-*` Lua codebase built and installed as rocks, and either a real
Consul+authz-enforcer service or a stub answering its HTTP contract. This is buildable in principle (the
plugins are plain Lua in this repo, no closed-source dependency found), but is a multi-service, multi-day
lift for behavior that, on the payout-relevant routes specifically, reduces to: Basic-Auth-verify a
merchant key, mint one specific passport shape, forward. `kong-lite`'s scope (mint the `private`/API-key
passport shape correctly) is the right cut for that reduction. **To raise fidelity incrementally** (still
substitute, not real Kong): add impersonation-grant emulation (accept `X-Razorpay-Account`, mint
`impersonation.consumer`), and an identification-only (`both-false`) passport path for unauthenticated
dashboard-proxy-style testing — both are small, additive changes to `kong-lite/server.py`, not a Kong
install.

**AuthZ**: **Correctly absent.** Real `authz` requires Casbin + a live/local Consul agent (`consul agent
-dev` minimum) plus policy seeding via `NewConsulStore`/`AddPolicyLine`. Since PS never calls AuthZ
(confirmed §4), and Edge's own enforcement is `shadow_mode=true` (non-blocking) on the public API route
this lane cares about, a substitute AuthZ has **zero behavioral effect to reproduce** for the `payouts`
service under test. If a future twin needs to test the **dashboard-merchant** surface specifically
(`payouts_with_otp`, which runs `shadow_mode=false`, i.e. enforced), that would require either (a) a real
local `authz` + Consul + seeded policies (heavier), or (b) `goutils/authz`'s own
`adapter.NewMockAdapter()` + `AddPolicyLine` in-process pattern (lightest — no network, already
demonstrated as the SDK's own test pattern, `goutils/authz/adapter/mock.go`) if the substitute service
itself embeds `goutils/authz/v2` rather than calling a real enforcer over the network.

**Passport minting (kong-lite's signer)**: **SUBSTITUTE, exact contract below** (this is a case where the
substitute contract, not the choice, is what needs precision):
- Route: any method, matched by longest path-prefix in `KONG_LITE_ROUTES_JSON` (default table in §CONTRACT.md).
- Auth: HTTP Basic, username = `rzp_(test|live)_<14-char-key-id>`, password = per-merchant secret file.
  Failure → `401`, `WWW-Authenticate: Basic realm="Authorization Required"`, JSON body
  `{"error":"unauthorized","detail":"invalid or missing merchant API key"}`.
- On success: strip inbound `Authorization`; add `X-Passport-JWT-V1` (RS256, header `{typ:"JWT",alg:"RS256",kid:<PASSPORT_IDENTIFIER>}`,
  body `{iss,sub,jti,iat,nbf,exp(now+300),identified:true,authenticated:true,mode,org,product,consumer:{id,type:"merchant"},roles?}`)
  and a service `Authorization: Basic <cred.API>` header; forward verbatim otherwise.
- **Gap to close for full fidelity**: no `impersonation`/`oauth`/admin/identification-only shapes; no
  IP/rate-limit simulation (acceptable — both off in prod); no keys-table indirection (merchant id is
  embedded in the fake key id rather than looked up — acceptable for a closed synthetic fixture set, but
  should not be presented as "how real API keys work").

---

## Synthetic data

| Family/table | Field | Source evidence | Type+length | Constraints | Allowed values | FK/relationships | State rules | Distribution matters? | Generation rule | Exactness |
|---|---|---|---|---|---|---|---|---|---|---|
| `keys` (api monolith) | `id` | `api/database/migrations/2014_04_23_221828_create_keys.php:23-24` | `CHAR(14)`, PK | random base62, **independent of merchant_id** | any base62 string | none (it's the PK) | none | no | 4×`random_bytes(4)`→base62, take last 14 chars (`Key::generateUniqueId`) | EXACT (mechanism), synthetic value obviously fake |
| `keys` | `merchant_id` | same migration:26 | `CHAR(14)` | FK → `merchants.id`, `on_delete=restrict` | any valid merchant id | → `merchants` | none | no | pick from seeded merchant pool | EXACT |
| `keys` | `secret` | same migration:28, `Key/Entity.php:265` (`SECRET_LENGTH=24`) | `VARCHAR(256)` (stores Crypt::encrypt of a 24-char plaintext) | plaintext secret is exactly 24 chars, base62 | any base62 string | none | none | no | 4×`random_bytes(4)`→base62→truncate 24 | EXACT |
| `keys` | `expired_at` | migration:32 | `INT`, nullable | unix epoch or null | null or past/future timestamp | none | expiry gate presumably enforced in monolith auth code (not read this pass) | no | null for "active" fixture keys | REPRESENTATIVE (expiry-check code not read) |
| public key string | — | `Key/Entity.php:308` (`stripSign`), `getFormattedKey` | `rzp_<mode>_<14-char-id>` (or with country-code segment for whitelisted countries) | `mode ∈ {live,test}` | — | derived from `keys.id` | none | no | `"rzp_" + mode + "_" + id` | EXACT |
| `merchant_users` | `merchant_id`,`user_id`,`product`,`role` | `api/database/migrations/2017_03_28_104101_create_merchant_users_table.php:18-45` | `CHAR(14)`,`CHAR(14)`,`VARCHAR(255)` default `'primary'`,`VARCHAR` | unique on (merchant_id,product,user_id,role) — **a user can hold multiple roles per merchant/product** (no single "the role" column semantics) | `role` values not enumerated in this migration (application-level, likely matches xplatform.csv role names — `owner`,`admin`,`finance_l1`, etc., 21§3.2/§3.5) | → `merchants`, → `users` | none captured here | yes (role mix determines dashboard permission fixtures) | one row per (merchant, user, role) combination needed for a test persona | REPRESENTATIVE (role enum not independently confirmed against this table) |
| Passport claim: `consumer` | `id`,`type` | `goutils/passport/passport.go:42-46` | struct, `type ∈ {user,merchant,admin,application}` | — | — | — | — | yes | see minting recipes §2/24§1 | EXACT |
| Passport claim: `impersonation` | `Type`,`Consumer` (capitalized wire keys) | `goutils/passport/passport.go:59-62` | struct, no JSON tags | wire keys are `Type`/`Consumer`, not lowercase | `Type="user_merchant"` observed | `Consumer` nests a `consumer`-shaped object | — | yes (needed to test partner/dashboard impersonation) | mint per 21§2.2b shape, capitalized keys | EXACT (and load-bearing — lowercase keys will silently fail to deserialize against v4 consumers like `ledger`) |
| `[auth.*]` credential | username | `payouts/config/default.toml:93-132` | plain string | one physical credential per named internal caller | see §3 table | maps to a specific caller identity by convention, not enforced by any schema | none | no | copy literal usernames, generate fresh random passwords | EXACT (usernames), synthetic (passwords) |
| ENV2 twin `merchants.json` | `key_id_test`/`key_id_live` | `ENV2_COMPOSE/seeds/merchants.json:8-23` | `rzp_(test|live)_ARENAM0000000N` | 14-char suffix after mode prefix | — | maps 1:1 to a merchant id in the same file | — | no | literal, hand-authored | REPRESENTATIVE (embeds merchant id in the key id, unlike real production keys — see Corrections) |

---

## Cannot be derived from repositories

1. **Prod's effective `payouts` `[passport.edge]` identifier value.** Only `default.toml` (`edgev1`),
   `devstack.toml`/`sample.toml` (`edgev2`) exist in this clone; no `payouts/config/prod.toml` (or
   equivalent per-cell override) was found. Matters because a wrong `kid` mapping would make PS reject
   every real Edge-minted passport in prod. Owner: payouts team (`@razorpay/razorpayx_payouts_be` per
   `21_edge_kong_authz.md` remaining-unknowns list). Minimal request: sanitized `prod.toml`
   `[passport]` section only (no other secrets).
2. **The monolith's internal `/v1/edge/internal/authenticate` role-resolution logic** (called synchronously
   by `kong-plugin-user-auth` to populate `passport_roles`/`authorization_roles` before `upstream-jwt` mints
   the dashboard-proxy passport). Matters because it's the one link between `merchant_users.role` and the
   passport `roles` claim for dashboard-user traffic — without it, this lane cannot confirm exactly how a
   `merchant_users` row's `role` string becomes a passport role string (same casing? prefixed? mapped
   through another table?). Owner: `api` monolith team. Minimal request: read access to `api`'s
   `app/Http/Controllers/*Authenticate*` or equivalent — a full clone rather than a schema export, since
   this is business logic, not a schema question.
3. **Whether PR 2577's policy-seeding pipeline used `non_lms` as `POLICY_SUBPRODUCT`** (carried over
   unresolved from `21_edge_kong_authz.md` §3.3/§6 item 11 — this pass found no new evidence). Owner:
   Spine-Edge/authz team. Minimal request: the actual `POLICY_ORG_ID`/`POLICY_SUBPRODUCT` values used for
   that pipeline run (a config/runbook fact, not a code artifact).
4. **How production API-key auth's `roles` claim gets populated** (§4 last paragraph) — not wired to any
   confirmed role source in `edge`; the 21§2.2a example payload's role values are illustrative only. Matters
   for anyone trying to test role-gated behavior on the pure-API-key (non-dashboard) path. Owner: Edge team.
   Minimal request: point to the actual Lua/config that sets `passport_roles`/`authorization_roles` ctx for
   the API-key auth flow, if one exists, or confirm it doesn't (API-key traffic simply has no roles in prod).
5. **Batch service's own outbound auth identity/host config for `payouts/bulk_approve`** (this pass found
   the JSON endpoint definition, `batch/src/main/resources/payout_approval.json:64-65`, but not the
   Spring config resolving `authType`/base-host for that specific `apiProcessor` call type — likely lives in
   an `application-*.yml` not present or not searched exhaustively in this clone). Matters for confirming
   whether Batch calls PS directly or through the monolith for bulk approvals — directly bears on whether
   `cred.API` (monolith identity) or a Batch-specific credential is what PS actually sees for this flow.
   This overlaps lane 23 (workflows/batch) — flagging here only because it touches this lane's auth-caller
   table. Owner: batch/workflows lane or Batch service team.

---

## Fidelity tier verdicts

- **Kong route/plugin table (prod)**: REAL (source-derived, terraform is the actual deployed config) — CONTRACT-FAITHFUL SUBSTITUTE in twin (`kong-lite`), correctly scoped to the one auth flow (API-key/merchant) the twin needs.
- **Passport claim schema + minting (Edge)**: REAL (source-derived) — CONTRACT-FAITHFUL SUBSTITUTE in twin for the merchant-API-key shape only; other shapes (impersonation, oauth, admin, identification-only) are MISSING from the twin, not built.
- **Passport verification (PS)**: REAL — the twin runs the actual `payouts` binary and real `goutils/passport/v3`, unmodified.
- **AuthZ enforcer**: UNKNOWN-BLOCKED only on two narrow points (non_lms org-string, PR-2577 seeding params); otherwise REAL (source-derived) that PS itself has no AuthZ dependency — twin's omission of any AuthZ substitute is therefore REAL/correct, not a gap.
- **Service-to-service Basic-Auth (`[auth.*]`) and precedence rules**: REAL (source-derived, direct code read) — twin config is REAL (same TOML shape, generated secrets).
- **PS shadow-gateway framework**: REAL (source-derived, newly documented this pass) — twin config is CONTRACT-FAITHFUL (mirrors the disabled default), untested in its enabled/pilot state.
- **Idempotency (`X-Payout-Idempotency`)**: REAL (source-derived) — not substituted in the twin at all (runs in the real binary); enforcement is feature-flagged off by default in every environment this clone shows.
- **`keys` table / API-key format**: REAL (source-derived) — twin's `merchants.json` is REPRESENTATIVE SUBSTITUTE only (embeds merchant id in the key string, which real keys do not do).
