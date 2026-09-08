# api-ingress substitute contract (M7 shared API-monolith ingress)

Fidelity class: **CONTRACT_FAITHFUL_REPLACEMENT** of the API monolith's *ingress* responsibilities for the
selected surface. It is not the monolith and does not claim production completeness. Every route it
serves is listed in `contract/routes.json`, which `scripts/m7/derive_contract.py` derives mechanically from
the pinned `api/app/Http/Route.php` (route name, method, path, controller, auth groups, `Route::$internalApps`),
the pinned `payouts/internal/routing/router/*.go` (the Payouts-service route each proxy forwards to) and the
pinned `vendor-payments` path literals (call sites). The server refuses a route that has no inventory entry.

## Position in the arena

```
merchant / dashboard / internal app / admin
        |  Basic auth (four credential classes)
        v
   api-ingress  ---- merchant-scoped ownership (SQLite: resources, banking_accounts)
        |  cred.API + X-Passport-JWT-V1 (+ x-merchant-id for internal apps)
        v
   payouts-api (REAL)  ----> GET /v1/fund_accounts_internal/{id}  (back to api-ingress, X-Razorpay-Account = merchant)
        |                    POST /v1/payouts_service/*           (api-ingress pass-through to monolith-stub)
   payouts workers -> FTS -> mozart-sim ...  -> payouts_service/source_update -> api-ingress -> vp-source (/_replica/status)
```

`payouts` `[api] host` points at api-ingress (`config/templates/base/payouts/arena.toml`). The
payouts-service-facing callback group (`payouts_service/*`, `internal/merchants/*`, `on_hold_slas_internal`,
`actor_info_internal`, `users_internal`, `internal_balances_queued`, `update_fts_fund_transfer`,
`banking_account_statement/payout_update`, `payouts/purposes`) is passed through to the M6 monolith-stub
unchanged, authenticated with the ingress's own stub credential. `fund_accounts_internal`, `contacts_internal`,
`banking_accounts_internal` are served by the ingress and are **no longer reachable from payouts on the stub**.

## Identity contexts (api/app/Http/BasicAuth/BasicAuth.php)

| Context | Credential | Source | Tenant |
|---|---|---|---|
| merchant (private auth) | `Basic rzp_{live,test}_<14>:<secret>` from `seeds/generated/merchants.json` + `secrets/merchant-keys` | `BasicAuth::setCredentials`, `Route::$private` | the credential's merchant, only |
| user (proxy auth) | `Basic dashboard:<session token>`; sessions minted by `POST /_ingress/session` | `BasicAuth::proxyAuth`, `Route::$proxy`, `Route::$userWhitelist` | the session's merchant |
| application (privilege / appAuth) | `Basic rzp_live:<app secret>` (key blank) | `BasicAuth::appAuth`, `verifyInternalApp`, `Route::$internalApps` | **only** `X-Razorpay-Account`, must be a known merchant (`checkAndSetAccountScope`) |
| admin | `Basic admin:<admin token>` | `Route::$admin` | optional `X-Razorpay-Account` |

Rejections: bad merchant key -> 401 `BAD_REQUEST_UNAUTHORIZED` ("Authentication failed"); any other context
mismatch (merchant key on an internal/admin route, app not in `Route::$internalApps[app]`, app credential on a
merchant route, bad app/admin/session credential) -> 400 `BAD_REQUEST_URL_NOT_FOUND` ("The requested URL was
not found on the server."), i.e. `ApiResponse::routeNotFound()`, the monolith never confirming the route exists.

Headers stripped from every inbound request and re-issued from the authenticated context: `Authorization`,
`x-merchant-id`, `X-Entity-Id`, `X-Razorpay-Account` (for merchant/user contexts), `X-Passport-JWT-V1`,
`X-Dashboard-*`, `X-Payout-Actor-*`, `App-User-ID`, `x-creator-*`, `x-batch-id`, `X-Payouts-Service-Proxy`.
`body.merchant_id` is overwritten with the tenant (payouts `CreatePayoutToFundAccount` overwrites it again
from the passport).

## Ownership (explicit records)

`resources(kind, id, merchant_id, contact_id, contact_type, record, source)` with `source` in
`seed` (seeds/generated/monolith/fund_accounts.json, every generated fund account now carries its owner),
`registered` (control plane) or `created:<app>` (`POST /v1/contacts_internal`, `POST /v1/fund_accounts_internal`).
`GET /v1/fund_accounts_internal/{id}` = `FundAccount/Service.php:210-212 fetch()` ->
`findByPublicIdAndMerchant($id, $this->merchant)`: a record another merchant owns, or one with no owner, answers
400 `BAD_REQUEST_INVALID_ID` ("The id provided does not exist"). The response is the PS-facing shape of
`Services/PayoutService/Create.php:521 generateFundAccountResponseForPayoutsService` for the payouts_service
caller (no `merchant_id` field: ownership is enforced, not disclosed) and `toArrayPublic` otherwise.
`internalContactPayout` (`Payout/Service.php:330-362`): fund_account_id required -> owned by tenant -> contact type
internal -> app allowed for that type (`payouts/internal/app/contact/type.go:16-21`) -> PS
`/v1/payouts/internal_contact_payout` with the application passport (`consumer.meta.name`), where PS re-checks the
contact type through the same `fund_accounts_internal` route.

## Idempotency (api/app/Http/Middleware/MerchantIdempotencyHandler.php)

Applies to `payout_create`, `payout_create_with_otp`, `payout_create_internal`, `payout_create_on_internal_contact`
for strict private auth, dashboard users and the internal apps the handler lists (vendor_payments, xpayroll,
payout_links, ...). Key = `X-Payout-Idempotency`, scope = (merchant, route). Same key + same key-sorted body hash
-> stored 200 response replayed without an upstream call; different body -> 400
`BAD_REQUEST_SAME_IDEM_KEY_DIFFERENT_REQUEST`; missing key on an internal-app route -> 400
`BAD_REQUEST_MISSING_IDEM_KEY`. The key is also forwarded to payouts, which keeps its own idempotency table.

## Tax-payment tag-back (source-faithful, see M7_OWNERSHIP_INVESTIGATION.md)

`PATCH /v1/payouts_internal/{id}/tax-payment-id`: `Validator::UPDATE_TAX_PAYMENT` (`required|string|size:19`),
`PublicEntity::stripDefaultSign` (txpy_ prefix) then `PayoutsDetails/Core.php:378 updateTaxPayment` ->
`Repository::updatePayoutDetails([$payoutId])`, an **id-only** update that returns SUCCESS whether or not the
payout belongs to the calling merchant or exists. The ingress stores the tag together with the calling app,
tenant and request id so evidence can show who tagged what.

## Control plane (`/_ingress/*`, admin identity; never under `/v1`)

`GET /health`, `GET /_ingress/health`, `GET /_ingress/contract`, `POST /_ingress/reset` (removes every mutable
row, keeps seed ownership, reloads seeds), `POST /_ingress/session`, `POST /_ingress/otp`,
`POST /_ingress/registry/{internal_contact,banking_account,fund_account,reload}`, `GET /_ingress/evidence`,
`GET /_ingress/registry/fund_accounts`.

## Correlation

Every request carries `X-Request-ID` (client-supplied or `ing_<hex>`), echoed in the response, forwarded to
payouts as `X-Request-ID`/`X-Razorpay-TaskId` (payouts `request_context.go` binds it as the external request /
task id) and written to `audit(request_id, route, identity, tenant, upstream, upstream_status, decision)`.
SourceUpdater relays to vendor-payments carry the same id as `X-Request-ID`/`X-Razorpay-TaskId`
(`api/app/Services/VendorPayments/Service.php:966-968`).

## Not implemented (explicitly)

Composite payouts (`fund_account` object), partner/OAuth auth, `payouts_batch` (Batch service), account-number
translation (payouts does it), Raven OTP, dashboard route permissions (`Route::$routePermission`), rate limits,
2FA sessions, the SNS SourceUpdater transport (off in the pinned config), and every route outside
`contract/routes.json`.
