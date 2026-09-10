# Substitute contract — Edge/Kong + passport minting (`kong-lite`)

Tier: **CONTRACT-FAITHFUL SUBSTITUTE** for the merchant-API-key shape today; must grow the impersonation,
admin and dashboard-proxy shapes to cover the dashboard/approval families. Real Kong + Lua plugin chain
(`edge/kong-plugins/*`) is buildable but disproportionate (lane 03 §5). PS itself verifies passports with
`goutils/passport/v3` static keys, so the twin needs only a correct minter.

## Auth inputs the substitute must accept
| Flow | Inbound credential | Passport shape to mint | Source |
|---|---|---|---|
| Merchant API key | Basic `rzp_<live|test>_<14-char key id>` : secret; key id is random, merchant resolved via `keys.merchant_id` (`api/app/Models/Key/Entity.php:261-282,308`; migration `2014_04_23_221828_create_keys.php`) | `consumer{id:<merchant>, type:"merchant"}`, `mode` from prefix, `authenticated:true, identified:true` | edge upstream-jwt `access.lua:86-205` |
| Partner on behalf of sub-merchant | as above + `X-Razorpay-Account` header / `account_id` query/body | add `impersonation{Type:"user_merchant"?/partner grant, Consumer{id:<sub-merchant>, type:"merchant"}}` — wire keys **capitalised** `Type`/`Consumer` (`goutils/passport/passport.go:59-62`) | `kong-plugin-impersonation-grant/extractor.lua:4-8` |
| Dashboard user session (proxy) | in prod: dashboard PHP proxy Basic `rzp_<mode>_<merchantId>` : shared secret + `X-Dashboard-User-*` headers to the monolith; for a PS-direct twin mint `consumer{id:<user>, type:"user"}` + `impersonation{Consumer{id:<merchant>,type:"merchant"}}` + `roles[]` | user-auth plugin non-admin branch (lane 03 §2) |
| Admin (RZP staff) | `X-Admin-Token` | `consumer{type:"admin", Meta{org_id, name}}`, legacy auth type admin | user-auth admin branch |
| Identification-only | none | `authenticated:false, identified:true` | upstream-jwt |

## Claim schema (all)
`iss, sub, aud, exp (=iat+300), nbf, iat, jti, authenticated, identified, mode, domain, org, product, roles[], consumer{id,type,meta}, oauth{owner_type,owner_id,client_id,app_id,env,access_token_id}, impersonation{Type,Consumer}, credential{username,public_key,secret_ref_id,expiry_time}, additional_identities` — `goutils/passport/passport.go`. Header `X-Passport-JWT-V1`, RS256, `kid` = payouts `[passport.edge].identifier` (prod `edgev2` per `payouts/config/prod.toml:198`; default.toml `edgev1`; arena `arena-passport-1`).

## Forwarding
- Strip inbound `Authorization`; add `Authorization: Basic <[auth.api]>` for PS; forward `X-Payout-Idempotency` untouched (Kong never rewrites it).
- Do not add `X-Razorpay-Account` to PS (PS never reads it inbound; it is folded into the passport).
- Route table: `/v1/payouts*`, `/v1/contacts*`, `/v1/fund_accounts*`, `/v1/transactions*` → PS in the twin (prod: all on monolith; cutover devstack-only).
- Errors: 401 `WWW-Authenticate: Basic realm="Authorization Required"` JSON `{"error":{"code":"BAD_REQUEST_ERROR","description":"..."}}`.
- No rate limiting, `ip-restriction-x` rollout 0, authz-enforcer shadow mode on public routes (all match prod).

## Arena-only endpoints
`POST /_arena/mint` (verifier helper) — keep, document as non-production.
