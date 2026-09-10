# kong-lite substitute contract

Stands in for the Kong+edge public-API path that fronts payouts in real
environments. Auth/minting behaviour is grounded in
`findings/21_edge_kong_authz.md` §2 (passport claim schema, minted by
`edge/kong-plugins/kong-plugin-upstream-jwt`) and §5 ("Kong-lite spec —
Env 2"), and `findings/24_shared_libs_proto.md` §1 (payouts' passport v3
static-key config, `[passport.api]`/`[passport.edge]` identifiers).

## Role in Env 2

`kong-lite` is the ONLY container attached to both `rzp-ingress`
(host-reachable) and `rzp-arena` (internal-only) — the sole entrypoint a
developer's shell or test runner talks to from the host.

It is a minimal reverse proxy (stdlib `http.server` + `urllib.request` +
a pure-stdlib RS256 signer, `_common/rsa_sign.py` — no third-party deps),
not a real Kong install — "lite" is literal.

## Route table (env var `KONG_LITE_ROUTES_JSON`, JSON object, prefix -> upstream)

| Path prefix | Upstream | Real service |
|---|---|---|
| `/v1/payouts` | `payouts-api:9400` | payouts cmd/api (public create/fetch/approve/reject/cancel) |
| `/v1/contacts` | `payouts-api:9400` | payouts cmd/api (Env 2's public-API path is direct-to-PS, not monolith-proxied — task spec routes these here, not to monolith-stub) |
| `/v1/fund_accounts` | `payouts-api:9400` | payouts cmd/api |
| `/twirp/rzp.payouts` | `payouts-api:9400` | payouts cmd/api (twirp) |
| `/twirp/ledger` | `ledger-api:8080` | ledger cmd/api |
| `/v1/fts` | `fts-web:8080` | fts `cmd/web` binary (corrected in this pass -- earlier scaffold assumed an nginx front on :80; `build/build-host.sh` confirms `fts-web` is its own `go build ./cmd/web` binary listening directly on `[application] LISTEN_PORT` = 8080) |
| `/twirp/cfa` | `cfa-server:8081` | cfa cmd/server HTTP |
| `/v1/balances` | `xbalances-server:8080` | x-balances cmd/server |
| `/health`, `/_arena/health` | answered locally, no auth | kong-lite self |
| `POST /_arena/mint` | arena-only passport minting for the verifier (`{"consumer":{"id":"ARENA…"},"mode","roles"}`, plus the optional arena-control `"user":{"id":…}` described below → `{"token"}`); refuses non-`ARENA*` ids | kong-lite self |

## Auth pipeline (per request, in order)

1. **Health check bypass** — `/health`/`/_arena/health` always answered
   locally, unauthenticated, never proxied.
2. **Route match** — 404 if no configured prefix matches (before auth, so
   an unrouted path never leaks an auth-failure vs. not-found distinction
   that doesn't exist in this arena).
3. **Basic-Auth verification** against `seeds/merchants.json` — username =
   merchant API key (`rzp_test_ARENAM0000000N` / `rzp_live_ARENAM0000000N`,
   14-char merchant id embedded per findings/21's `^rzp_(\w{4})_(\w{14})$`
   regex), password = the per-merchant secret from
   `secrets/merchant_arena_m{1,2,3}_secret.txt` (mounted read-only at
   `KONG_MERCHANT_SECRETS_DIR`, default `/run/secrets/merchants/`). Failure
   → `401` with `WWW-Authenticate: Basic realm="..."`, matching real
   `basic-auth-x` behaviour.
4. **Mode derivation** — `rzp_test_` prefix → `mode: "test"`,
   `rzp_live_` prefix → `mode: "live"` (findings/21 §5 point 2).
5. **Passport mint** — RS256-signs the claim set from findings/21 §2.2(a)
   (merchant API-key private auth): `iss, sub, jti, iat, nbf,
   exp(now+300s), identified:true, authenticated:true, mode, org, product,
   consumer:{id:<merchant_id>, type:"merchant"}` (+ `roles` if the
   merchant's seed row carries any — M3 in this arena, for
   dashboard-role-flavoured testing even though this is API-key auth, not
   impersonation). Signed with `secrets/passport_private_key.txt`
   (`PASSPORT_PRIVATE_KEY_FILE` env), header
   `{typ:"JWT",alg:"RS256",kid:"arena-passport-1"}` — `kid` matches
   `config/templates/payouts.toml.tmpl`'s `[passport.arena] identifier`
   exactly, so payouts' real passport v3 handler (static per-`kid` public
   key) resolves the right key. Header name: `X-Passport-JWT-V1`
   (`goutils/passport.HeaderKeyPassportJWTV1`).
   An already-authenticated caller may opt in to the **dashboard/proxy** claim
   shape instead — see "ARENA CONTROL" below; without those headers this step is
   unchanged.
6. **Header rewrite** — strips the client's own inbound `Authorization`
   (the merchant API-key credential is kong-lite's concern, never
   forwarded upstream, matching real Kong not leaking the client secret to
   the origin), adds `X-Passport-JWT-V1` (above) and a **service**
   `Authorization: Basic <cred.API>` header — `cred.API` per
   `payouts/internal/routing/router/payout_routes.go`'s
   `middleware.BasicAuth(cred.API, cred.Workflow)`, which every route in
   the `/v1/payouts` group requires **in addition to** the passport JWT.
   Username/password from `PS_API_AUTH_USER`/`PS_API_AUTH_PASS_FILE`
   (secret `auth_api_payouts`, wired into `payouts.toml.tmpl`'s new
   `[auth.api]` section — see that template's comment for the exact
   username chosen and why it's a documented assumption, not confirmed
   against a real value).
7. **Forward** — proxies method/body/remaining headers to the matched
   upstream, streams the response back verbatim.

## ARENA CONTROL (not a production behaviour): dashboard/proxy passport shape

> **This section documents an arena control surface, not something Kong or the
> real edge does.** In production this passport is minted by the monolith
> (`api/app/Http/BasicAuth/BasicAuth.php:1246-1248`
> `setPassportImpersonationClaims(PASSPORT_IMPERSONATION_TYPE_USER_MERCHANT, $merchantId)`,
> constant at `:143`) off a dashboard **session**, and serialised by
> `edge/kong-plugins/kong-plugin-upstream-jwt/kong/plugins/upstream-jwt/access.lua:135-152`.
> The arena has no session/user store, so the shape is requested explicitly.

### Why it exists

`_mint_passport_jwt` used to hard-code `consumer.type = "merchant"`. goutils
`passport/helpers.go:207` maps that to `LegacyAuthTypePrivate`, and
`payouts/internal/app/payouts/validation.go` `ValidateCancel` refuses to cancel a
**scheduled** payout unless `GetLegacyAuthType` returns `LegacyAuthTypeProxy`
(`CancelScheduledPayoutInvalidAuth`, "Scheduled Payouts can only be cancelled via
dashboard"). `helpers.go:212-213` returns `proxy` only for
`consumer.Type == ConsumerTypeUser` **and** a non-nil `impersonation` whose
`Type == "user_merchant"`. Without a way to mint that, the whole dashboard cancel
path was unreachable in the twin (`journey:scheduled-payouts/cancel_via_dashboard`
was BLOCKED).

### How to ask for it

Opt-in, per request, on the normal proxy path. Send **in addition to** the
merchant's Basic-Auth API key:

| Request header | Value | Required |
|---|---|---|
| `X-Arena-Passport-Consumer-Type` | `user` (case-insensitive) | yes |
| `X-Arena-User-Id` | dashboard user id, `[A-Za-z0-9_.-]{1,40}` | yes |
| `X-Dashboard-User-Role` | a role string appended to `roles`, same charset | no |

`POST /_arena/mint` accepts the same opt-in as a body field: `"user": {"id": …}`.

**Safety properties**

* Honoured **only after** the merchant credential has been verified — the
  merchant identity always comes from the API key, never from a header, so no
  caller can assert an identity for a merchant it cannot already authenticate as.
* Absent, malformed or non-`user` headers change nothing: the passport is minted
  exactly as before. Default behaviour is byte-identical.
* The three headers are consumed by kong-lite and **stripped before forwarding**
  (added to the same drop-set as the client `Authorization`), so no upstream ever
  sees them.

### Claim shape minted

Default (unchanged, merchant API-key private auth):

```json
"consumer": {"id": "<merchant_id>", "type": "merchant"}
```

With the opt-in headers (dashboard/proxy):

```json
"consumer":      {"id": "<X-Arena-User-Id>", "type": "user"},
"impersonation": {"type": "user_merchant",
                  "consumer": {"id": "<merchant_id from the API key>", "type": "merchant"}}
```

Everything else (`iss/sub/jti/iat/nbf/exp`, `identified`, `authenticated`,
`mode`, `org`, `product`, `roles`, the RS256 header/`kid`) is unchanged. Field
names follow `access.lua:146-152` verbatim; goutils' `ImpersonationClaims` struct
(`passport/passport.go:56-59`) carries no json tags and Go's `encoding/json`
matches field names case-insensitively, so the lowercase keys the edge emits bind
to `Type`/`Consumer`. `internal/auth/authHelper.go` `getMerchantIdFromPassport`
then reads the merchant from `impersonation.Consumer` for every non-`private`
auth type — which is why the merchant id moves out of `consumer`.

Offline cover: `RED_LOOP/tests/test_kong_passport.py` (claim shapes, header
handling, and a signed-JWT decode + signature verify against a throwaway key,
asserted through a port of `helpers.go` `GetLegacyAuthType`).

## RS256 signer — pure stdlib, no `cryptography`/`pyjwt`

`_common/rsa_sign.py` implements a minimal ASN.1 DER reader (enough for
PKCS#1 and PKCS#8-wrapped RSA private keys — `openssl genrsa` on this
host's OpenSSL 3.x emits PKCS#8 "BEGIN PRIVATE KEY", confirmed by
inspecting `secrets/passport_private_key.txt`'s own header; PKCS#1 is also
accepted for robustness) plus RSASSA-PKCS1-v1_5 SHA-256 padding + modexp
via Python's built-in arbitrary-precision `pow(base, exp, mod)`. No pip
dependency added to `substitutes/Dockerfile` — this environment doesn't
have `cryptography`/`pyjwt` installed either, so a from-scratch signer was
both necessary to validate locally (per the task's `python3 <stub>.py` +
curl validation convention) and consistent with every other stub's
stdlib-only design.

## Health

`GET /health` and `GET /_arena/health` → `200
{"status":"ok","service":"kong-lite","routes":N,"merchants_loaded":N}`,
answered without proxying or auth.

## Validated

`python3 -m py_compile` (both `server.py` and `_common/rsa_sign.py`).
`_common/rsa_sign.py`'s own `__main__` self-test signs+verifies a JWT
against the actual `secrets/passport_{private,public}_key.txt` generated
by this scaffold's `gen-secrets.sh` — `verify_ok=True`. Ran `kong-lite`
on port 18811 against a throwaway mock `payouts-api` (127.0.0.1:19401):
confirmed a request Basic-Authed as `rzp_test_ARENAM00000001` with the
real generated M1 secret (a) got proxied through with `Authorization:
Basic <cred.API base64>` (decoded to the configured `api:<password>`
pair) and a present `X-Passport-JWT-V1` header, and (b) that JWT's
header/payload/signature were independently decoded and re-verified
against `secrets/passport_public_key.txt` using
`rsa_verify_pkcs1v15_sha256` — signature valid, claims exactly matching
the findings/21 §2.2(a) shape (`mode:"test"`,
`consumer:{id:"ARENAM00000001",type:"merchant"}`, `org`, `product`, 5-min
`exp`). Also confirmed an unauthenticated request is rejected `401`
before reaching the upstream.
