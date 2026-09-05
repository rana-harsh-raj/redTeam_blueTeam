# Edge (Kong) + Passport + AuthZ — Payouts Architecture

Repos: `terraform-kong` @ `8ffe965b`, `edge` @ `da9ff5b2`, `authz` @ `8687f48f` (all shallow clones, read-only investigation).

Scope note up front, load-bearing for everything below: **`terraform-kong` is declarative Kong config (Terraform/HCL) — it names plugins and their config, but does not contain plugin source.** The Lua implementation lives in `edge`. Claim-schema, signing, TTL answers come from `edge`; route/rollout-knob answers come from `terraform-kong`; policy-model answers come from `authz`.

---

## 1. Route / Service / Plugin Inventory (terraform-kong)

**Headline finding: `payouts-proxy-cutover` is instantiated in exactly one place — `base/payouts-proxy-cutover/` (devstack).** No prod/stage/us/sg/non-prod-cells/automation/perf* environment instantiates the template. So in prod today, both example requests are served by the pre-cutover path (monolith), not by a diverted Payouts Service.

### 1.1 Route table

| Route | File | Env | Auth bucket | Upstream (default) | Diverted by cutover? |
|---|---|---|---|---|---|
| `POST /v1/payouts` | `prod/api/api.tf:663-669` (`payout_create-prod-api`) | prod/us/sg | `authenticated_paths`, `rollout=1, impersonation_rollout=1` | `api.api.svc.cluster.local` (+canary/baseline) | No — no `upstream-override` on this route in prod |
| `GET /v1/payouts/{id}` | `prod/api/api.tf:655-661` | prod | `authenticated_paths` | monolith | No |
| `POST /v1/payouts_with_otp` | `prod/api/api.tf:719-725` (public API) | prod | `authenticated_paths` | monolith | No |
| `POST /v1/payouts_with_otp` | `prod/api-dashboard/templates/merchant-dashboard.tf:2254-2257` (`payout_create_with_otp-api-dashboard-merchant`) | prod | `paths` (no Kong auth wall; dashboard-proxy) | `api-merchant.api.svc.cluster.local` | No — **not** in cutover's diverted set even in devstack |
| `POST /v1/payouts_with_otp` | `prod/api-dashboard/templates/admin-dashboard.tf:4983-4986` | prod (admin dashboard) | `paths` | `api-admin.api.svc.cluster.local` | No |
| `POST /v1/payouts/{id}/approve`, `/cancel`, `/2fa/create` | `prod/api/api.tf:695-717` | prod | `authenticated_paths` | monolith | No |
| `GET/POST /v1/payout-links*`, `/v1/fund_accounts*`, `/v1/contacts*` | `prod/api/api.tf:139-178, 671-685` | prod | `authenticated_paths` | monolith | No |
| `GET /v1/payouts/downtimes*` | `templates/xperience/xperience-downtime.tf` on `api.razorpay.com` | prod | `authenticated_paths`, `regex_priority=1` | **`xperience.int.razorpay.com`** — already carved off the monolith | Not via cutover; separate migration |
| `GET/POST /v1/payouts*` (cohort) | `base/payouts-proxy-cutover/config.tf` → `templates/payouts-proxy-cutover/payout-create.tf` | **devstack only** | `authenticated_paths`, `regex_priority=2` | **`payouts.int.dev.razorpay.in`** (Payouts Service) | Yes — see rollout knobs below |
| `GET /v1/payouts/schedule/timeslots` | `templates/payouts-proxy-cutover/payouts-dashboard-routes.tf` | **devstack only** | `paths`, `regex_priority=2` | Payouts Service | Yes — 100% for all live/test dashboard-proxy traffic (pilot route for authz PR 2577) |
| `POST /v1/payouts/admin/encryption_key` | `templates/payouts/payouts-admin.tf` | prod (misconfigured — host var not overridden, points at devstack host) | n/a | Payouts Service | n/a |
| `GET /v1/payouts/{id}` | `templates/payouts/payouts-ext.tf` on `payouts-ext.razorpay.com` (separate public hostname) | prod | `authenticated_paths` | `payouts.razorpay.com:443` | n/a — always-direct, not part of cutover |
| `GET /v1/payout-links/{id}/view` (customer-facing) | `templates/payout-links/x-customer-payout-links.tf` on `payout-links.razorpay.com` | prod | `paths` | `x-customer-payout-links.razorpay.com` | n/a |

Env parity: `us/prod` and `sg/prod` are byte-identical route definitions to prod (fewer mtls hosts). `sg/stage` mirrors the pattern but has **no `api-dashboard/` at all**. `devstack/api/api.tf` (a *different* Kong tree from `base/`) has no `/v1/payouts` route on the public-API side at all — only `base/` wires the cutover.

### 1.2 Current rollout-knob values (`base/payouts-proxy-cutover/config.tf`, the only live instantiation, devstack)

```hcl
payout_create_upstream_override_conditions = [{
  rollout = 1, priority = 1
  match   = { consumers = ["C0uw3CXseZPwmJ"], auth_flows = ["basic-auth-x:merchant"] }
  upstream = "payouts-proxy-cutover-service"
  upstream_host_header = "payouts.int.dev.razorpay.in"
}]

dashboard_override_conditions = {
  payouts_scheduled_time_slots-payouts-proxy-cutover = [
    { rollout=1, priority=1, match={request_mode="live"}, upstream="payouts-proxy-cutover-service", upstream_host_header="payouts.int.dev.razorpay.in" },
    { rollout=1, priority=2, match={request_mode="test"}, upstream="payouts-proxy-cutover-service", upstream_host_header="payouts.int.dev.razorpay.in" },
  ]
}
dashboard_user_auth_routes = ["payouts_scheduled_time_slots-payouts-proxy-cutover"]
```
- `/v1/payouts*` diverted **only for one named Kong consumer** (`C0uw3CXseZPwmJ`) at 100%; comment in the file explains per-merchant cohorting is structurally impossible for dashboard-proxy traffic (basic-auth-x always maps it to the anonymous consumer).
- `GET /v1/payouts/schedule/timeslots` diverted at 100% for all dashboard-proxy traffic; per-merchant gating is pushed downstream to a Splitz flag (`payouts_shadow_gateway_scheduled_time_slots`) inside the Payouts Service, fail-safe to monolith.
- Admin-dashboard divert conditions: unset (empty) — nothing diverted.
- `api_override_conditions` (GET shadow routes on the api-host module) and all `*_anti_spoof_strip_routes` knobs: **empty `{}`/`[]`** in current devstack config → those plugin instances aren't even created.

### 1.3 Plugin table (per surface)

| Surface | basic-auth-x / consumer-auth | upstream-jwt | ip-restriction-x | rate-limiting | upstream-override | authz-enforcer |
|---|---|---|---|---|---|---|
| `prod-api` (`api.razorpay.com`, public) | Per-route `consumer-authentication` (basic-auth-x + jwt-x), service-level basic-auth-x/jwt-x explicitly skipped on auth'd routes | `issuer=edge, keys=/ssl/JWT_PRIVATE_KEY_V2 /ssl/JWT_PUBLIC_KEY_V2, header=X-Passport-JWT-V1, key_id=edgev2` | Auto per-route, `ip_restrictions_rollout=0` (off) — payout routes don't set it | **Not found** on any payouts/contacts/fund_accounts route | None on payout routes | `shadow_mode=true` **and** route is in `whitelisted_routes_api` — double-disabled |
| `prod-api-dashboard-merchant` (`payouts_with_otp`) | Service-level `basic-auth-x{anonymous=..., hide_credentials=false}` maps to anonymous consumer | same key config | Not configured on this route | Not found | None | `shadow_mode=false` — **enforced** |
| `payouts-proxy-cutover` (devstack) | `upstream-jwt` same key config; `consumer-identifier{rollout=0}` (disabled) | same | n/a | n/a | Route-level, driven by `config.tf` above | `shadow_mode=true`, explicit `whitelisted_routes=[payout_create-*, payout_fetch_by_id-*]` |
| `payouts-proxy-cutover-dashboard` (devstack) | `basic-auth-x{anonymous=...}` | same | n/a | n/a | Per-route, `dashboard_override_conditions` | `shadow_mode=false` (enforced) + `user-auth` plugin on the one diverted route upgrades passport from identification-only to authenticated |
| `xperience-payout-downtime` (`api.razorpay.com/v1/payouts/downtimes*`) | inherits `prod-api`-equivalent auth | same key config | n/a | n/a | n/a (own service, own upstream) | `shadow_mode=false` in prod |

Header transforms found: `templates/payouts-proxy-cutover/*` anti-spoof strip (`remove.headers=["X-Payouts-Service-Proxy","X-Merchant-Id","X-Entity-Id"]`) — **currently inactive** (empty route lists in the live config). `templates/payouts/payouts-admin.tf`: `request-transformer-x` rewrites `/v1/payouts/(.*)` → `/v1/%1` before hitting the Payouts Service (strips the `payouts/` segment).

### 1.4 Request tracing

**(a) `dashboard.razorpay.com/merchant/api/live/payouts_with_otp`**
1. `/merchant/api/live/*` is a dashboard-BFF convention, not present in terraform-kong — by the time Kong sees it, it's `POST api-dashboard-merchant.razorpay.com/v1/payouts_with_otp`.
2. Kong service `prod-api-dashboard-merchant`, route `payout_create_with_otp-api-dashboard-merchant`, plain `paths` bucket (no Kong-level credential wall).
3. `basic-auth-x` maps the request to the **anonymous consumer** (no real API key presented) → `consumer-identifier` → `authz-enforcer` runs **enforced** (`shadow_mode=false`) → `login-as-merchant` no-ops (route not in its critical-path lists) → `upstream-jwt` mints an **identification-only** passport (`authenticated=false`, since no consumer was resolved).
4. Upstream: `api-merchant.api.svc.cluster.local` (monolith), `preserve_host=true`. No `upstream-override` on this route → always monolith, cutover-agnostic.
5. Real dashboard-user auth (merchant_users binding, OTP) happens **inside the monolith**, using the session/cookie forwarded through Kong — Kong's job is anonymous routing + identification-only passport + authz shadow/enforce gate only.

**(b) `api.razorpay.com/v1/payouts`**
1. Kong service `prod-api`, route `payout_create-prod-api`, `authenticated_paths`.
2. Per-route `consumer-authentication` plugin (basic-auth-x merchant-key match against the API key/secret as Basic Auth) runs at `rollout=1`; service-level basic-auth-x/jwt-x are explicitly skipped for this route.
3. `impersonation-grant` auto-attached (`rollout=1`, not whitelisted). `passport-enabler` gates an **authenticated** passport for auth_flows `basic-auth-x:merchant / :partner / :impersonation-grant / jwt-x:`.
4. `ip-restriction-x` present but rolled out at 0% (off). No `rate-limiting` plugin. `authz-enforcer` runs shadow **and** the route is explicitly whitelisted — never blocks.
5. `upstream-jwt` mints a fully **authenticated** passport (real merchant consumer resolved by Kong itself).
6. Upstream: weighted `api.api.svc.cluster.local` (960) / `api-canary` (20) / `api-baseline` (20), `preserve_host=true`. No `upstream-override` → always monolith today.

---

## 2. Passport (`X-Passport-JWT-V1`) — minted by `edge/kong-plugins/kong-plugin-upstream-jwt`

Source: `edge/kong-plugins/kong-plugin-upstream-jwt/kong/plugins/upstream-jwt/access.lua` (`build_jwt_payload`, lines 86-205; `encode_jwt_token`, lines 60-80; handler priority **970**, runs after auth/identifier plugins).

- **Signing**: hand-rolled JWT, `RS256`, header `{typ:"JWT", alg:"RS256", kid:<conf.key_id>}`. Signer: `openssl_pkey.new(key):sign(sha256_digest)`.
- **Key location (names only — no material)**: config fields `private_key_location`/`public_key_location`/`key_id` (`schema.lua:9-19`). Prod/devstack (terraform-kong): `/ssl/JWT_PRIVATE_KEY_V2` / `/ssl/JWT_PUBLIC_KEY_V2`, `key_id="edgev2"`. Edge-repo local-dev seed scripts use `/mnt/jwtRS256.key` / `/mnt/jwtRS256.cer`, `key_id="edgev1"` (`edge/scripts/dev/seed.sh:40-41`) — **v1 vs v2 is just a dev-vs-prod key-id convention, not a schema difference.** No Vault/JWKS integration in `edge`'s own runtime — key bytes are read from a file path (`pl_file.read`) and cached forever (`ttl=0`).
- **TTL**: `exp = current_time + 300` — **hardcoded 300 seconds (5 min)**, with a `TODO: make this equal to request timeout` comment (`access.lua:90`). No config knob. `nbf = current_time` (immediate validity).

### 2.1 Full claim schema (as built, `access.lua:86-205`)

| Claim | Always present? | Source |
|---|---|---|
| `nbf`, `exp`, `jti` | always | current_time, +300, uuid() |
| `payloadhash` | always | SHA-256 hex of request body |
| `iat`, `iss` | if `conf.issuer` set | = "edge" in all terraform-kong instantiations |
| `aud` | if `ngx.ctx.service` set | upstream host |
| `mode` | if `rzp_mode` set in ctx | "live"/"test" |
| `org`, `product` | if both set | e.g. `"100000razorpay"`, `"banking"` |
| `domain` | fallback (deprecated) | default `"razorpay"` |
| `identified` (bool) | always | `kong.ctx.shared.identified or false` |
| `authenticated` (bool) | always | `kong.ctx.shared.authenticated or false` |
| `credential.username/public_key/secret_ref_id` | if set | from the auth plugin |
| `merchant.id` | legacy, only if `consumer_type=="merchant"` | back-compat |
| `consumer.id`, `consumer.type` | if `consumer_id` set | |
| `impersonation.type`, `impersonation.consumer.{id,type}` | if `kong.ctx.shared.impersonation` set | see below |
| `oauth.*` | if set | jwt-x internal-app flow |
| `roles` | `passport_roles` preferred, else `authorization_roles` | |
| `additional_identities[]`, `additional_identities.session[]` | rollout-gated | |

**Important correction to the task's assumed schema: there is no `authType` claim inside the JWT.** `auth_type`/`auth_flow` exists only as Kong-internal ctx (`common.lua:500-517`, used for routing/metrics/`upstream-override` matching) and is never copied into the passport payload. Don't add an `authType` field to a local signer — it wouldn't match production.

`authenticated` vs `identified` — three tiers, set independently by different plugins:
1. **Identification only**: `keyless-identifier`/`key-identifier` set `identified=true`, leave `authenticated` unset/false.
2. **Both true**: `basic-auth-x` (verified credential), `jwt-x` (verified signature+not-revoked), `user-auth` (successful monolith round-trip) all set both true.
3. **Anonymous** (both false): `basic-auth-x` with no credential falls back to the anonymous consumer without calling `set_consumer` → both stay false. This is the dashboard-proxy "identification-only passport" case.

`impersonation` claim — two independent producers, both write `kong.ctx.shared.impersonation={consumer={...}, grant={...}}`, read verbatim by upstream-jwt:
- **API-key impersonation** (`kong-plugin-impersonation-grant/access.lua:66-103`): `impersonation.grant.principal_type` from DB row (`partner`/`merchant`), `impersonation.consumer` = the acted-on merchant.
- **Dashboard-proxy impersonation** (`kong-plugin-user-auth/access.lua:335-460`): non-admin branch always sets `impersonation={grant.principal_type="user_merchant", consumer={username=<merchant id>, type="merchant"}}`. **Admin-auth branch (X-Admin-Token) explicitly leaves `impersonation` nil** — support/ops sessions do not carry impersonation claims from this function.

### 2.2 Three example passport payloads (decoded JWT body — field names/structure exact, values marked EXAMPLE are illustrative)

**(a) Merchant API-key private auth** (`basic-auth-x`, `merchant` credential — the `api.razorpay.com/v1/payouts` case):
```json
{
  "nbf": 1767398400,
  "exp": 1767398700,
  "jti": "EXAMPLE-8f14e45f-ceea-467e-adde-3f4708c5397a",
  "payloadhash": "EXAMPLE-e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "iat": 1767398400,
  "iss": "edge",
  "aud": "payouts-service.internal.razorpay.com",
  "mode": "live",
  "domain": "razorpay",
  "identified": true,
  "authenticated": true,
  "credential": {
    "username": "EXAMPLE-rzp_live_HxAB12cD34eFgH",
    "public_key": "EXAMPLE-rzp_live_HxAB12cD34eFgH"
  },
  "merchant": { "id": "EXAMPLE-HxAB12cD34eF" },
  "consumer": { "id": "EXAMPLE-HxAB12cD34eF", "type": "merchant" },
  "roles": ["EXAMPLE-r~finance", "EXAMPLE-r~payouts_admin"]
}
```
JWT header: `{"typ":"JWT","alg":"RS256","kid":"edgev2"}`. No `impersonation`/`oauth` keys.

**(b) Dashboard-proxy auth with impersonation** (`user-auth`, non-admin merchant-dashboard user — the `payouts_with_otp` case once upgraded past identification-only, e.g. on the one diverted `timeslots` route):
```json
{
  "nbf": 1767398400,
  "exp": 1767398700,
  "jti": "EXAMPLE-2c1f5a90-1e3b-4b7e-9a2c-6f0e2d9b7a11",
  "payloadhash": "EXAMPLE-9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  "mode": "live",
  "org": "100000razorpay",
  "product": "banking",
  "identified": true,
  "authenticated": true,
  "credential": {},
  "consumer": { "id": "EXAMPLE-user_ABC123XYZ0", "type": "user" },
  "impersonation": {
    "type": "user_merchant",
    "consumer": { "id": "EXAMPLE-HxAB12cD34eF", "type": "merchant" }
  },
  "roles": ["EXAMPLE-payouts.approve", "EXAMPLE-payouts.view"]
}
```
For **plain, un-diverted dashboard-proxy traffic today** (e.g. `payouts_with_otp` in prod), the actual passport is the **identification-only** variant instead — `basic-auth-x` never resolves a real consumer:
```json
{
  "nbf": 1767398400,
  "exp": 1767398700,
  "jti": "EXAMPLE-71c6a9c0-anon-...",
  "mode": "live",
  "domain": "razorpay",
  "identified": false,
  "authenticated": false
}
```
(no `consumer`, `impersonation`, or `roles` — nothing resolved a consumer, so those branches never populate.)

**Admin/support variant** (X-Admin-Token, `is_admin_auth=true` — note **no `impersonation` block** by code):
```json
{
  "nbf": 1767398400,
  "exp": 1767398700,
  "jti": "EXAMPLE-71c6a9c0-....",
  "mode": "test",
  "org": "100000razorpay",
  "product": "banking",
  "identified": true,
  "authenticated": true,
  "consumer": { "id": "EXAMPLE-admin_9f8e7d6c5b", "type": "admin" },
  "roles": ["EXAMPLE-role:banking_ops_l2", "EXAMPLE-payouts.approve_external"]
}
```

**(c) Internal privilege-app / service-to-service auth** (`jwt-x`, OAuth2 client-credential):
```json
{
  "nbf": 1767398400,
  "exp": 1767398700,
  "jti": "EXAMPLE-a4b5c6d7-e8f9-4012-a3b4-c5d6e7f8a9b0",
  "payloadhash": "EXAMPLE-cd372fb85148700fa88095e3492d3f9f5beb43e555e5ff26d95f5a6adc36f8e6",
  "mode": "live",
  "domain": "razorpay",
  "identified": true,
  "authenticated": true,
  "credential": {},
  "consumer": { "id": "EXAMPLE-HxAB12cD34eF", "type": "merchant" },
  "oauth": {
    "owner_type": "merchant",
    "owner_id": "EXAMPLE-HxAB12cD34eF",
    "access_token_id": "EXAMPLE-a4b5c6d7-e8f9-4012-a3b4-c5d6e7f8a9b0",
    "app_id": "EXAMPLE-payouts-internal-app",
    "client_id": "EXAMPLE-oauth-client-9f8e7d6c",
    "env": "prod"
  },
  "roles": ["EXAMPLE-oauth::scope::payouts.write", "EXAMPLE-oauth::scope::payouts.read"]
}
```
Note: `roles` prefixed `oauth::scope::` also forces `authz-enforcer` **out of shadow mode** regardless of config (`kong-plugin-authz-enforcer/handler.lua:46-72`) — internal-app auth is always hard-enforced.

### 2.3 Loop-guard / `X-PS-Proxied`

**Not found anywhere in `edge` or `terraform-kong`.** No loop-prevention header mechanism exists in either repo for the direct-to-Payouts-Service divert. The only related header is `X-Payouts-Service-Proxy` (an anti-spoof marker the Payouts Service is meant to check, stripped from client-supplied requests by the — currently inactive — cutover template strip rule). If a loop guard is needed for the direct-divert design, it does not exist today and would need to be built net-new.

### 2.4 authz-enforcer Kong plugin (edge-side mechanics)

`edge/kong-plugins/kong-plugin-authz-enforcer/kong/plugins/authz-enforcer/handler.lua` (priority 975, after auth plugins):
- Only runs if `authenticated or identified` is true.
- Builds `{subject, org="{org_id}::{product}::{sub_product}" (or domain, or "razorpay"), resource=<path>, action=<method>, claims.roles=authorization_roles}`.
- Calls out to a dedicated **internal HTTP AuthZ enforcement service** (`POST {authz_url}`, Basic auth `gateway:$KONG_AUTHZ_ENFORCER_PASSWORD`) — **not Consul directly, not a local cache** at the Kong layer (that's downstream inside the authz service itself, see §3).
- Shadow mode default true; force-disabled (i.e. hard-enforced) when roles are prefixed `app.`/`oauth::scope::`/`settlement_readonly`.
- Separate **merchant-activation check** (local Kong consumer-tag lookup, not the remote call) additionally validates the impersonated sub-merchant's `state` tag, fails closed if not `ACTIVATED`.

---

## 3. AuthZ model (authz repo)

### 3.1 Policy model

Core entities (`authz/admin/repo/entities/*.go`): `Resource{Id,Name}`, `Action{Id,Name,Type}` (Type is a DB C/R/U/D enum, unrelated to the CSV's HTTP-verb Action column), `Service{Id,Name}`, `ResourceGroup{Id,Name}` (the Consul-watch partition key), `Permission` (Resource+Action+Effect), `Role{Id,Name,OrgId,Type,OwnerType,OwnerId,ChildIds}` (Type ∈ internal/external/custom/standard), `Policy` (Service+Permission+Type+IsAssignable+IsActive), `RolePolicyMapping` (M:N join), `SubjectRoleMapping{SubjectURN,RoleID}`.

Enforced Casbin line (`admin/corepolicy/policy.go:30-44`):
```
p,{role_name},{org_id},{service_name},{resource},{action},{effect}
```
Consul key: `{consul.key_prefix}/{resource_group}/{role_id}:{policy_id}`.

Standard CSV import format (`README.md:93-99`, `scripts/policies/sample.csv`):
```
Policy Name,Service,Resource,Action,Resource Group,Role
```
Org is supplied at pipeline-run time (env vars `POLICY_ORG_ID`/`POLICY_PRODUCT`/`POLICY_SUBPRODUCT`, `scripts/core_policy_helper_v3.py:82-85, 172-175`) — a CSV can carry a 7th org-override column but rarely does.

### 3.2 X/banking roles and payout permissions

**`scripts/policies/xplatform.csv`** (service=`x_platform`, resource group=`x_platform`) — the fullest role×action matrix for payouts:

| Action | owner | admin | finance_l1 | finance_l2 | finance_l3 | operations | view_only |
|---|---|---|---|---|---|---|---|
| create (`/v1/payouts`, `_with_otp`, `_batch`) | ✓ | ✓ | ✓ | ✓ | ✓ | | |
| approve / cancel / reject | ✓ | ✓ | ✓ | ✓ | ✓ | | |
| bulk / bulk_approve | ✓ | ✓ | ✓ | ✓ | ✓ | | |
| fetch_by_id | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | |
| fetch_multiple (view) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

Same file also grants contacts/fund-account CRUD to the same role set, `view_only` read-only, `operations` create+read but not write on contacts.

**`support` role**: exists (used broadly on non-payout dashboard surfaces — refunds, disputes, submerchant listing) but **not found granted on any `/v1/payouts*` resource anywhere in the repo.**

**"CAC" roles**: the literal string `CAC`/`cac_role` is **not a role name** — it's an initiative/filename tag (`*_cac.csv`, `*_cac_update.csv`). The actual role it produces is **`payout_approve_reject_view`** (`scripts/policies/12_02_2024_rx_xplateform_cac.csv`, `01_03_2024_rx_xplateform_cac_update.csv`), granted `approve_payout`, `reject_payout`, `reject_payout_bulk`, `approve_payout_bulk`, `approve_payout_links`, `reject_payout_links`, `bulk_approve_payout_links`, `bulk_reject_payout_links` — a **second, independently-evolved policy set** for what's largely the same capability as xplatform.csv's approve/reject rows, using different resource-name strings (`approve_payout` vs `/v1/payouts/{id}/approve`). Worth flagging as a reconciliation gap.

**PR 2577 roles** (`28_08_2026_rx_payouts_proxy_cutover_timeslots.csv`, service=`gateway`, resource=`/v1/payouts/schedule/timeslots`, action=`get`) — 14 roles, one row each: `owner, pseudo_owner, admin, view_only, operations, chartered_accountant, vendor, petty_cash_employee, banking_readonly, finance, finance_l1, finance_l2, finance_l3, authorised_signatory`. This introduces several roles (`pseudo_owner`, `chartered_accountant`, `vendor`, `petty_cash_employee`, `banking_readonly`, `authorised_signatory`, unsuffixed `finance`) that appear **nowhere else** in the repo's payout policies — pre-existing dashboard roles this PR merely extended with one GET grant.

### 3.3 Org scoping — `non_lms` vs `pg`

**`non_lms` literal string: not found anywhere in the authz repo** (CSVs, TOML configs, Go code). What is confirmed:
- Org scoping is a **literal-string match on `role.org_id`** baked into each Casbin policy line at creation time — no hierarchical/prefix resolution. A policy created for org `X::pg::Y` will **not** match an enforce request with `org=X::banking::non_lms` unless a role/policy was explicitly created for that exact string.
- `::pg::` exists as a live pattern (`scripts/policies/2026_04_09_sync_to_IN_org_razorpay::pg::.csv`, README example `100000razorpay::pg::merchant_dashboard`).
- `banking::lms` (not `non_lms`) appears in several `scripts/policies/dashboard/*.csv` filenames/content, but only inside route-path strings (`v1/partner_lms/...`), not as an org-id substring.
- **Conclusion on the open question**: whether `100000razorpay::banking::non_lms` is consulted depends entirely on whether the pipeline that seeded PR 2577's policies passed `non_lms` as `POLICY_SUBPRODUCT`/`org_ids` at creation time — the tracked CSV carries no org column, so **this is not resolvable from the repo alone.** Separately, `authz`'s own `prod.toml` **`watchResourceGroups` does not list `x_platform`** (only `gateway, capital, platform_identity`), while stage/bvt/slit/dev-serve do — either prod's `x_platform` policies are served by a differently-configured enforcer deployment, or this is a real gap; not determinable from this repo alone.

### 3.4 Enforcer sync contract (for a consuming service like payouts)

Casbin engine + Consul-watch client live in the external module `github.com/razorpay/goutils/authz/v2/*` (not vendored here; `authz`'s `cmd/enforcer` is itself the reference consumer). Contract:
```go
consul := policystore.NewConsulStore(client, prefixes, policyChangeChan, logger, consulStoreCfg)
e, _ := enforcer.NewEnforcer(ctx, &enforcerCfg, consul, logger, policyChangeChan,
                              enforcer.WithCache(enforcer.NewARCCache(cacheSize)))
isAllowed, _ := e.Enforce(ctx, &enforcer.Request{JWTClaims, OriginService, Resource, Action})
perms, _ := e.GetImplicitPermissionsForUser(ctx, &JWTClaims{Roles, Sub, Org})
```
Mechanism: **Consul KV blocking long-poll** under `{consul.key_prefix}/{resource_group}` (not gRPC streaming), local Casbin reload on change, in-process ARC cache for hot-path latency. A payouts service embedding `goutils/authz/v2` directly would replicate this same wiring rather than calling authz's gRPC `Enforce` API over the network.

### 3.5 Policy bundle excerpt for payout create/approve/reject/cancel/view (X roles)

Built from `xplatform.csv` (found as-is; format = repo's standard CSV):
```csv
Policy Name,Service,Resource,Action,Resource Group,Role
payout_create,x_platform,/v1/payouts,post,x_platform,owner
payout_create,x_platform,/v1/payouts,post,x_platform,admin
payout_create,x_platform,/v1/payouts,post,x_platform,finance_l1
payout_create,x_platform,/v1/payouts,post,x_platform,finance_l2
payout_create,x_platform,/v1/payouts,post,x_platform,finance_l3
payout_approve,x_platform,/v1/payouts/{id}/approve,post,x_platform,owner
payout_approve,x_platform,/v1/payouts/{id}/approve,post,x_platform,admin
payout_approve,x_platform,/v1/payouts/{id}/approve,post,x_platform,finance_l1
payout_approve,x_platform,/v1/payouts/{id}/approve,post,x_platform,finance_l2
payout_approve,x_platform,/v1/payouts/{id}/approve,post,x_platform,finance_l3
payout_reject,x_platform,/v1/payouts/{id}/reject,post,x_platform,owner
payout_reject,x_platform,/v1/payouts/{id}/reject,post,x_platform,admin
payout_reject,x_platform,/v1/payouts/{id}/reject,post,x_platform,finance_l1
payout_reject,x_platform,/v1/payouts/{id}/reject,post,x_platform,finance_l2
payout_reject,x_platform,/v1/payouts/{id}/reject,post,x_platform,finance_l3
payout_cancel,x_platform,/v1/payouts/{id}/cancel,post,x_platform,owner
payout_cancel,x_platform,/v1/payouts/{id}/cancel,post,x_platform,admin
payout_cancel,x_platform,/v1/payouts/{id}/cancel,post,x_platform,finance_l1
payout_cancel,x_platform,/v1/payouts/{id}/cancel,post,x_platform,finance_l2
payout_cancel,x_platform,/v1/payouts/{id}/cancel,post,x_platform,finance_l3
payout_fetch_multiple,x_platform,/v1/payouts,get,x_platform,owner
payout_fetch_multiple,x_platform,/v1/payouts,get,x_platform,admin
payout_fetch_multiple,x_platform,/v1/payouts,get,x_platform,finance_l1
payout_fetch_multiple,x_platform,/v1/payouts,get,x_platform,finance_l2
payout_fetch_multiple,x_platform,/v1/payouts,get,x_platform,finance_l3
payout_fetch_multiple,x_platform,/v1/payouts,get,x_platform,operations
payout_fetch_multiple,x_platform,/v1/payouts,get,x_platform,view_only
payout_fetch_by_id,x_platform,/v1/payouts_internal/{id},get,x_platform,owner
payout_fetch_by_id,x_platform,/v1/payouts_internal/{id},get,x_platform,admin
payout_fetch_by_id,x_platform,/v1/payouts_internal/{id},get,x_platform,finance_l1
payout_fetch_by_id,x_platform,/v1/payouts_internal/{id},get,x_platform,finance_l2
payout_fetch_by_id,x_platform,/v1/payouts_internal/{id},get,x_platform,finance_l3
payout_fetch_by_id,x_platform,/v1/payouts_internal/{id},get,x_platform,operations
```
**Gaps (inferred by pattern, not present as-is):** `support` has no payout grant anywhere — would need a new row modeled on `view_only`/`operations`. Timeslots-only roles (`pseudo_owner`, `chartered_accountant`, `vendor`, `petty_cash_employee`, `banking_readonly`, `authorised_signatory`, unsuffixed `finance`) have zero CRUD grants beyond the single GET timeslots policy — extending them to full payout CRUD is pure pattern-copy, no repo precedent. No org column is present in these rows; scoping to `100000razorpay::banking::non_lms` specifically would need a 7th column or explicit `POLICY_ORG_ID`/`POLICY_SUBPRODUCT` pipeline params — neither has repo precedent for that exact string.

---

## 4. Rate limits / IP allowlist / header rewrites / loop guards

- **Rate limiting**: not found on any payouts/payout-links/fund_accounts/contacts route anywhere across both repos. The `rate-limiting` plugin exists generically but is not wired to any payouts surface today.
- **IP allowlist**: `ip-restriction-x` auto-attached per-route on `authenticated_paths`, but config is a `ip_restrictions_rollout` **percentage knob** (not inline CIDRs) and defaults to `0` (off) — no payout route overrides it. Explicit service-level `ip-restriction-x={}` exists on `prod-api` and the customer-facing payout-links service, but with default (empty) config.
- **Header rewrites**: anti-spoof strip (`X-Payouts-Service-Proxy`, `X-Merchant-Id`, `X-Entity-Id`) defined in the cutover template but **inactive** (empty route lists in current config); `payouts-admin` does a real path rewrite (`/v1/payouts/(.*)` → `/v1/%1`).
- **Loop guards (`X-PS-Proxied` etc.)**: **not found in either repo.** No proxy-loop-prevention mechanism exists today for the direct-to-Payouts-Service divert design.

---

## 5. Kong-lite spec — Env 2 (public API only, `api.razorpay.com/v1/payouts`)

For a faithful local reverse proxy standing in for Kong on the public-API path only, in order:

1. **Auth (consumer resolution)**: Accept HTTP Basic Auth with username = merchant API key (`rzp_live_...`/`rzp_test_...`, regex `^rzp_(\w{4})_(\w{14})$`), password = key secret. Validate against a local credential store keyed the same way `basic-auth-x` does (`consumer.username` split on `~` if present, else `('merchant', username)`). On success: `identified=true, authenticated=true, consumer_type='merchant', consumer_id=<merchant id>`.
2. **Consumer/context build**: derive `rzp_mode` from the key prefix (`rzp_live_`→`live`, `rzp_test_`→`test`). No impersonation for direct API-key auth (impersonation only applies to sub-merchant/partner flows — skip unless simulating that).
3. **Passport mint**: build and RS256-sign a JWT with exactly the claim set in §2.1: `nbf, exp(now+300), jti, payloadhash(sha256 of body), iat, iss="edge", aud=<upstream host>, mode, identified, authenticated, credential.{username,public_key}, merchant.id, consumer.{id,type}, roles`. **Do not add an `authType` claim** — it doesn't exist in production. Sign with a local RSA keypair; header `X-Passport-JWT-V1`; JWT header `{typ:"JWT",alg:"RS256",kid:"edgev2"}` (or any local `kid` — the value just needs to be consistent between mint and any downstream JWKS-based verification you stand up).
4. **Header set forwarded upstream** (exact names): `X-Passport-JWT-V1` (the signed passport), preserve `Host: api.razorpay.com` (Kong's `preserve_host=true`), pass through original request headers. No `X-Payouts-Service-Proxy`/`X-Merchant-Id`/`X-Entity-Id` stripping needed (not active in prod today). No rate-limit or IP-allowlist headers to simulate — both are effectively off for this route in prod.
5. **AuthZ**: since `authz-enforcer` runs in shadow mode **and** this route is explicitly whitelisted in prod, a faithful Env-2 mock can skip real enforcement entirely (log-only or no-op) and still match prod behavior. If you want authz fidelity anyway, call the same shape as edge's plugin: `POST {authz_url} {subject, org, resource=<path>, action=<METHOD>, claims.roles}` and just don't act on the result (shadow).
6. **Upstream**: forward to whatever local mock stands in for `api.api.svc.cluster.local` (the monolith) — not the Payouts Service, since prod's public API path is not diverted by the cutover.
7. **Not needed for fidelity** (all off/inactive on this route in prod): rate limiting, IP restriction, header-strip anti-spoofing, upstream-override/divert logic, loop-guard headers (none exist).

---

## 6. UNRESOLVED_QUESTIONS closure

**Item 9** (effective Kong routes/plugins for `/v1/payouts*` and `/merchant/api/{mode}/*`, cutover rollout knob values) — **CLOSED.** Full route/plugin inventory above (§1); current rollout knobs are the exact `base/payouts-proxy-cutover/config.tf` values quoted in §1.2. Confirmed: cutover is devstack-only, prod is fully un-diverted, `payouts_with_otp` is excluded from the diverted set.

**Item 10** (passport claim schema, `goutils/passport` v3/v4, `impersonation.consumer` minting for dashboard-proxy traffic) — **CLOSED**, with one correction to the original question framing: there is no separate "goutils/passport v3/v4" versioning found — the minting logic is entirely in edge's `kong-plugin-upstream-jwt`, and the only versioning found is the `key_id` (`edgev1` dev / `edgev2` prod) and the header name literal `X-Passport-JWT-V1` (a single version, not v3/v4). Full claim schema, signing, TTL (300s hardcoded), and the two `impersonation` producers (API-key impersonation-grant vs. dashboard-proxy user-auth) are documented in §2. If "v3/v4" refers to something else (e.g. an internal passport spec doc), it wasn't found in either repo — flag as a naming mismatch to resolve with the Edge team rather than a gap.

**Item 11** (is `100000razorpay::banking::non_lms` consulted for merchant-dashboard traffic) — **PARTIALLY CLOSED.** Mechanism fully explained (§3.3: literal-string org match on the Casbin policy line, no hierarchy) but the literal string `non_lms` does not appear anywhere in the authz repo's tracked files — org values are pipeline parameters at policy-creation time, not baked into the CSVs. Whether PR 2577's seeding pipeline actually used `non_lms` (vs `pg` or something else) as `POLICY_SUBPRODUCT` is **not recoverable from the repo** (shallow clone; PR 2577's own diff/commit isn't reachable either — `git log` shows only one unrelated commit). Also newly surfaced: `authz` prod's own `config/prod.toml` `watchResourceGroups` **excludes `x_platform`** while stage/bvt/slit include it — a possible second explanation for "policies seeded but not enforcing," independent of the org-string question. **Remaining action**: ask the Spine-Edge/authz team directly (a) what `POLICY_ORG_ID`/`POLICY_SUBPRODUCT` PR 2577's pipeline run actually used, and (b) whether prod's authz enforcer for `x_platform`-scoped resources runs from a different config than `authz/config/prod.toml` in this repo.

---

## 7. Remaining unknowns (new, surfaced by this investigation)

1. **Where does the real dashboard `authenticate` call for non-diverted requests live**, and what exact headers/claims does it forward back into Kong context before `upstream-jwt` runs? (This is the monolith-internal `v1/edge/internal/authenticate` endpoint called synchronously by `kong-plugin-user-auth` — endpoint confirmed to exist and be called, but its internal logic is in the `api` monolith repo, out of scope here.)
2. **No JWKS/Vault integration found in `edge`'s runtime path** — key material is a mounted file (`/ssl/JWT_PRIVATE_KEY_V2` etc.). A `jwks.json` static endpoint exists only in `automation/edge/upstream-jwt.tf` (QA env) — unclear if prod downstream services (e.g. Payouts Service) verify passports via a similar static JWKS, a shared mounted public key, or something else; not found in either repo.
3. **`prod/payouts/config.tf` never overrides `payouts_admin_route_config_hosts`** — prod's `payouts-admin` Kong service appears bound to a dev-only hostname (`api-dashboard-merchant.dev.razorpay.in`). Possibly dead/unused in prod, or a real misconfiguration — worth a direct check with the owning team (`@razorpay/razorpayx_payouts_be`).
4. **Prod's admin-dashboard Kong service currently lacks the `basic-auth-x` plugin** that the `payouts-proxy-cutover` template's own code comments assume it has (true only for devstack's admin-dashboard sibling) — if this template is ever instantiated for prod's admin-dashboard host, that assumption needs re-verifying first.
5. **`authz` config's `AllowedOrgs` field** (`config/default.toml: admin.allowedOrgs=["razorpay","razorpayx"]`) is declared but appears **unreferenced** in the Go code searched — possibly dead config, not confirmed causally.
6. **Reconciliation gap between two independently-evolved payout-approval policy sets**: `xplatform.csv`'s `/v1/payouts/{id}/approve` style resources vs. the `payout_approve_reject_view` role's `approve_payout`/`reject_payout` resource-name style (§3.2) — likely both are enforced somewhere for overlapping capability with different resource identifiers; not clear which a payouts-service consumer should actually check.
7. **No loop-guard mechanism exists anywhere** for the direct Edge→Payouts-Service divert design (§4) — if this is required before wider rollout, it needs to be designed and built net-new; it is not merely "not yet found," it's confirmed absent from both repos' current state.
