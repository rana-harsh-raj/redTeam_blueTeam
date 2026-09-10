# Shared libs + proto: passport, authz, DCS/Splitz/ASV, queues, proto generation, ledger-sdk

All findings below are from the shallow (single-commit, current-master) clones under
`scratchpad/rzp-payouts-architecture/`. No git history / tags were available (shallow clones), so
version deltas are inferred from go.mod/go.sum pins in consumer repos, not from goutils commit log.

---

## 1. goutils/passport — JWT claim schema, verification, minting

There are **two passport major versions in live use**, and they work quite differently:

- **`goutils/passport/v4`** (module `github.com/razorpay/goutils/passport/v4`) — full source present in
  this clone at `goutils/passport/`. Used by e.g. **ledger** (`ledger/internal/provider/passport/passport.go`,
  pinned `v4.0.1` in ledger/go.mod).
- **`goutils/passport/v3`** — **not present in this clone** (goutils here is a shallow single-commit
  checkout of current master and v3 has apparently been removed/superseded by v4 in-tree). Used by
  **payouts** (`go.mod`: `github.com/razorpay/goutils/passport/v3 v3.4.0`). Payouts' own comment
  (`payouts/internal/auth/passport.go`) is stale, referencing v3.0.1, but go.mod pins v3.4.0.
  v3's public API surface is fully reconstructable from payouts' usage (`payouts/pkg/passport/handler.go`,
  `payouts/internal/auth/passport.go`) — see below. Treat any v3 field not exercised by payouts as unverified.

### Claim struct (v4, `goutils/passport/passport.go`)

```go
type passportClaims struct {
    *jwt.StandardClaims                                              // iss, sub, aud, exp, nbf, iat, jti
    Authenticated        bool                         `json:"authenticated"`
    Identified            bool                         `json:"identified"`
    Mode                 string                       `json:"mode,omitempty"`
    Domain               string                       `json:"domain,omitempty"`         // deprecated, use Org/Product
    Org                  string                       `json:"org,omitempty"`            // e.g. "100000_razorpay"
    Product              string                       `json:"product,omitempty"`        // e.g. "pg", "banking"
    Roles                []string                     `json:"roles,omitempty"`
    Consumer             *ConsumerClaims              `json:"consumer,omitempty"`
    OAuth                *OAuthClaims                 `json:"oauth,omitempty"`
    Impersonation        *ImpersonationClaims         `json:"impersonation,omitempty"`
    Credential           *CredentialClaims            `json:"credential,omitempty"`
    AdditionalIdentities map[string][]*ConsumerClaims `json:"additional_identities,omitempty"`
}

type ConsumerClaims struct {
    ID   string            `json:"id"`
    Type string            `json:"type"`             // "user" | "merchant" | "admin" | "application"
    Meta map[string]string `json:"meta,omitempty"`    // e.g. org_id (admin), name (application)
}

type OAuthClaims struct {
    OwnerType     string `json:"owner_type"`
    OwnerID       string `json:"owner_id"`
    ClientID      string `json:"client_id"`
    AppID         string `json:"app_id"`
    Env           string `json:"env"`
    AccessTokenID string `json:"access_token_id"`
}

// NOTE: ImpersonationClaims has NO json tags in v4 source (bug/oversight upstream) —
// {"Type":"...","Consumer":{...}} is what actually (de)serializes on the wire, not "type"/"consumer".
type ImpersonationClaims struct {
    Type     string
    Consumer *ConsumerClaims
}

type CredentialClaims struct {
    Username    string `json:"username"`
    PublicKey   string `json:"public_key"`
    SecretRefID string `json:"secret_ref_id,omitempty"`
    ExpiryTime  int64  `json:"expiry_time,omitempty"`
}
```

Header sent to services: `X-Passport-JWT-V1` (const `passport.HeaderKeyPassportJWTV1`).

Consumer types (`helpers.go`): `ConsumerTypeUser="user"`, `ConsumerTypeMerchant="merchant"`,
`ConsumerTypeAdmin="admin"`, `ConsumerTypeApplication="application"`.

### `GetLegacyAuthType` derivation (`goutils/passport/helpers.go`)

Constants: `LegacyAuthTypeAdmin="admin"`, `LegacyAuthTypeDirect="direct"`, `LegacyAuthTypePrivate="private"`,
`LegacyAuthTypeProxy="proxy"`, `LegacyAuthTypePrivilege="privilege"`, `LegacyAuthTypePublic="public"`.
(Mirrors `razorpay/api` `app/Http/BasicAuth/Type.php`.)

Decision table, in order:
1. `!IsIdentified()` → **`direct`**
2. `IsIdentified() && !IsAuthenticated()` → **`public`**
3. `consumer.Type == "admin"` → **`admin`**
4. `consumer.Type == "application"` → **`privilege`**
5. `consumer.Type == "merchant" && oauth == nil` → **`private`**
6. `consumer.Type == "user" && impersonation != nil && impersonation.Type == "user_merchant"` → **`proxy`**
7. else → `ErrLegacyAuthTypeUnknown`

So to mint each legacy auth type:
- **private**: `identified=true, authenticated=true, consumer={type:"merchant", id:"M..."}`, no `oauth`.
- **proxy** (impersonation): `identified=true, authenticated=true, consumer={type:"user", id:"U..."}, impersonation={"Type":"user_merchant","Consumer":{"id":"M...","type":"merchant"}}`.
- **privilege**: `identified=true, authenticated=true, consumer={type:"application", id:"...", meta:{"name":"..."}}`.
- **admin**: `identified=true, authenticated=true, consumer={type:"admin", id:"...", meta:{"org_id":"..."}}`.

`GetResourceOwnerID`/`GetResourceOwnerType` prefer `impersonation.Consumer` > `oauth.Owner*` > `consumer.*`.

### Verification (v4, `goutils/passport/handler.go`, `config.go`)

- `passport.InitHandler(jwksHost string, ...Option) (IPassportHandler, error)` fetches
  `GET {jwksHost}/jwks` once at boot (retried), parses as a **JWKS JSON doc**
  (`{"keys":[{"kty":"RSA","alg":"RS256","use":"sig","n":...,"e":...,"kid":"api.razorpay.com"}]}`) via
  `lestrrat-go/jwx/v2/jwk`. No periodic refresh — keys are fixed for process lifetime (this is why
  `mock.NewMockJwksServer()` exists, to serve keys statically for tests).
- Signature check: **only `jwt.SigningMethodRSA` (RS256-family) accepted**; header must carry `kid`;
  `kid` looked up in the fetched JWKS set (`jwks.LookupKeyID(kid)`); RSA public key extracted and used
  to verify via `golang-jwt/jwt/v4`.
- **No issuer/audience validation** — `passportClaims` embeds `*jwt.StandardClaims` (`iss`,`aud`,`sub`
  etc. all present as fields, exposed only via `GetAudienceClaim()`), but `parseAndValidateToken` never
  checks `iss`/`aud` against expected values. It relies purely on `jwt.ParseWithClaims`'s built-in
  **expiry (`exp`) check** (golang-jwt v4 default; **no configurable clock-skew leeway** — none of the
  `Option`s in `config.go` touch this).
- Config options (functional, via `passport.Option`): `WithJwksFetchConnectionTimeout` (default 2s),
  `WithJwksFetchMaxRetryAttempts` (default 2), `WithJwksFetchDelayBetweenRetries` (default 200ms, fixed
  backoff via `avast/retry-go/v4`).
- **Multiple issuers / kid selection ("apiv1"/"edgev1")**: in v4's JWKS-host model this is implicit —
  the `kid` header selects among whatever keys the JWKS endpoint serves (e.g. `kid: "api.razorpay.com"`
  vs `kid: "edge.razorpay.com"` in the mock JWKS fixtures). **v3 makes this explicit and config-driven**
  (see below) — that's where the literal strings `apiv1`/`edgev1` live, in **payouts' own config**, not
  in the passport SDK itself.

### v3 API surface (reconstructed from payouts usage — `payouts/pkg/passport/handler.go`)

v3 is **not** JWKS-host based; it's initialized with **static public keys per named identifier**, read
from service config (no outbound HTTP call at boot):

```go
kid, err := passportSdk.NewJwtKeyIdentifier(issuer /* e.g. "apiv1" */, publicKeyPEMBytes)
ph, err := passportSdk.InitHandler(kid1, kid2, ...)   // variadic *JwtKeyIdentifier
```
`passportSdk.IPassport`/`ConsumerClaims`/`ImpersonationClaims`/`GetLegacyAuthType`/`LegacyAuthType*`/
`ConsumerType*` all exist identically in v3 per payouts' direct usage — the claim schema and
`GetLegacyAuthType` decision table above should be treated as v3-compatible too, module aside.
(Cross-checked against a second independent consumer, `payout-links/pkg/passport/handler.go` — byte-for-byte
the same `HandlerConfig{Identifier,PublicKey}` / `NewJwtKeyIdentifier` / `InitHandler(kids...)` pattern,
confirming this v3 surface rather than being a payouts-specific quirk.)

payouts' config (`payouts/config/{default,sample,devstack}.toml`, section `[passport]`):
```toml
[passport]
    [passport.api]
        identifier = "apiv1"
        publicKey  = "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"
    [passport.edge]
        identifier = "edgev1"   # "edgev2" in devstack.toml — see comment there:
        # "Edge signs upstream passports for the api Kong service with kid edgev2
        #  (terraform-kong module/plugins.tf:282 default; base/api/api.tf:5067 sets
        #  key_id = "edgev2" for this cell). Key material is shared across non-prod
        #  cells and is taken from terraform-kong stage/edge/jwks.json."
        publicKey  = "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"
```
i.e. `identifier` == the JWT header `kid` that must match for that key to be selected; payouts loads
exactly two named keys (`apiv1` for tokens minted by the api/Kong service, `edgev1`/`edgev2` for tokens
minted by edge) — **this fully resolves UNRESOLVED_QUESTIONS #10's `apiv1`/`edgev1` question**: it's a
payouts-side config convention layered on top of passport v3's multi-key `kid`→pubkey map, not something
baked into the SDK.

ledger (v4, JWKS-host mode) config is just:
```toml
[passport]
    host = "https://edge-base.dev.razorpay.in"   # devstack; prod/stage/automation hosts differ, see README
```
(`ledger/internal/provider/passport/passport.go`: `Config{Host string}`, `passport.InitHandler(cfg.Host, passport.WithJwksFetchMaxRetryAttempts(3))`).

Known JWKS hosts (from `goutils/passport/README.md` and examples):
- Devstack: `https://edge-base.dev.razorpay.in`
- Stage: `https://edge-admin.int.stage.razorpay.in`
- Automation/QA: `https://edge-admin.int.qa.razorpay.in`
- Prod: `https://edge-admin-internal.razorpay.com`

### Minting recipe for a local test token (v4-compatible; same shape works for v3 given a matching kid/pubkey)

The SDK ships exactly this recipe for its own tests — reuse it verbatim
(`goutils/passport/mock/mock.go`, `goutils/passport/common_test.go`):

1. `mock.NewMockJwksServer(mock.WithKeyPath(mock.PublicKeyAPIGood)).Run()` → returns a `httptest` URL
   serving the embedded JWKS doc `{"keys":[{...,"kid":"api.razorpay.com"}]}` at `GET /` (any path — the
   mock handler ignores the path, so it also satisfies the real `/jwks` suffix `InitHandler` appends).
2. `ph, _ := passport.InitHandler(serverURL)` — same call app boot code uses.
3. Load the matching **embedded RSA private key** via `mock.GetPrivateKeyContent("sample-keys/rs256-private-key.txt")`
   (paired with `rs256-public-key.txt`, kid `api.razorpay.com`; there's also `rs256-edge-public-key.txt`
   for kid `edge.razorpay.com`, and `rs256-bad-public-key.txt` for negative tests — all embedded via
   `//go:embed sample-keys/*.txt` so they ship inside the `mock` package, no filesystem setup needed).
4. Build and sign:
```go
privKey, _ := jwt.ParseRSAPrivateKeyFromPEM(privateKeyBytes)
claims := passportClaims{ /* fields below */ }
token := jwt.NewWithClaims(jwt.GetSigningMethod("RS256"), claims)
token.Header = map[string]interface{}{
    "alg": "RS256", "typ": "JWT", "kid": "api.razorpay.com", "payload_version": "1.0.0",
}
signed, _ := token.SignedString(privKey)
```
(Note: `passportClaims` itself is **unexported** in `goutils/passport` — a consumer test can't construct
it directly from outside the package. Two ways around this for a standalone stub-minting tool: (a) vendor
this exact flow as a small `_test.go`-style helper living inside a fork of the passport package, or
(b) build the JWT payload as a plain `map[string]interface{}`/custom struct with the exact JSON tags
documented above — `golang-jwt` doesn't care that it's not `passportClaims` as long as the wire JSON
matches, since `FromToken` on the receiving side unmarshals JSON generically into its own `passportClaims`.)

Payload shape example (private/merchant auth), exact JSON good for any of the four told cases:
```json
{
  "iss": "https://edge.razorpay.com",
  "sub": "https://payouts.razorpay.com",
  "jti": "1234567890",
  "iat": 1581432514,
  "nbf": 1581432514,
  "exp": 1898192749,
  "identified": true,
  "authenticated": true,
  "mode": "live",
  "org": "100000_razorpay",
  "product": "banking",
  "consumer": { "id": "M10000000000000", "type": "merchant" }
}
```
Swap in `"consumer": {"type":"user","id":"U..."}` + `"impersonation": {"Type":"user_merchant","Consumer":{"id":"M...","type":"merchant"}}` for **proxy**;
`"consumer": {"type":"application","id":"...","meta":{"name":"svc"}}` for **privilege**;
`"consumer": {"type":"admin","id":"...","meta":{"org_id":"100000_razorpay"}}` for **admin**.
(header `kid` must always match the `kid` the receiving service's JWKS/static-key map has loaded for that
environment, e.g. `api.razorpay.com`/`edge.razorpay.com` for the mock, `apiv1`/`edgev1` for payouts.)

Service config keys needed (summary):
| Version | Key | Example |
|---|---|---|
| v4 | `passport.host` (JWKS host) | `https://edge-base.dev.razorpay.in` |
| v3 | `passport.api.identifier` / `passport.api.publicKey` | `"apiv1"` / PEM |
| v3 | `passport.edge.identifier` / `passport.edge.publicKey` | `"edgev1"`/`"edgev2"` / PEM |

---

## 2. goutils/authz — enforcer, Consul policy sync, local seeding

`goutils/authz/v2` (module path `github.com/razorpay/goutils/authz/v2`) wraps `casbin` (v1, old
`github.com/casbin/casbin`, not casbin/v2) over a Consul-backed, hot-reloading policy store.

### Model (`enforcer/model.go`)
```
[request_definition]  r = sub, org, svc, obj, act
[policy_definition]   p = sub, org, svc, obj, act, eft
[role_definition]     g = _, _, _
[policy_effect]       e = some(where (p.eft == allow))
[matchers]
m = g(r.sub, p.sub, r.org) && r.org == p.org && r.svc == p.svc && KeyMatch(r.obj, p.obj) &&
    ((r.act == "head" && p.act == "get") || r.act == p.act || p.act == "*")
```
So: subject is resolved to a role via `g` grouping scoped by org; org and service (`svc` =
`Request.OriginService`) must match exactly; resource (`obj`) matches via casbin `KeyMatch` (prefix/`*`
globbing); action matches exactly, wildcard `*`, or `head`↔`get` equivalence.

Enforcer API (`enforcer/enforcer.go`):
```go
type JWTClaims struct { Roles []string; Sub string; Org string }
type Request struct { JWTClaims JWTClaims; OriginService string; Resource string; Action string }
type IEnforcer interface {
    Enforce(context.Context, *Request) (bool, error)
    GetImplicitPermissionsForUser(context.Context, *JWTClaims) ([][]string, error)
}
```
Top-level constructor: `authz.NewEnforcer(ctx, *authz.Config{Enforcer, Consul}, logger, ...enforcer.Option) (enforcer.IEnforcer, error)`.

### Policy sync source (Consul)
- Consul KV, list+watch on `keyprefix`: default `authz/<Consul.Environment>/v1/policy` (override via
  `Consul.KeyPrefix`), further scoped per-service by appending `Enforcer.WatchResourceGroups` entries
  (`<prefix>/<resourceGroup>`) — services should only watch resource groups relevant to them to bound
  in-memory policy size.
- Each KV value is JSON: `{"Policy": "p, sub, org, svc, obj, act, eft", "is_deleted": false, "created_at": "..."}`
  (`policystore/corepolicystore.go` `PolicyChange`/`PolicyLine` — `PolicyLine` is a raw casbin CSV policy
  line string, e.g. `p, role:payouts_admin, 100000_razorpay, payouts, /payouts/*, create, allow`, and
  role bindings are just casbin `g` lines: `g, user_sub, role:payouts_admin, 100000_razorpay`).
- Consul client default addresses: `consul-prod.razorpay.com` (prod) or
  `consul.concierge.stage.np.razorpay.in` (non-prod), overridable via `Consul.Address`; `Consul.Token`
  required.
- Reload debounced via `Enforcer.DebounceInterval` (default 1s) on any KV change under watched prefixes.

### Local seeding (no Consul, deterministic)
There is **no built-in file/env-var policy source** — `NewConsulStore` is the only production adapter
shipped, and it always talks to a real (or locally-run) Consul agent. Two viable local options:

1. **Bypass `authz.NewEnforcer` and call `enforcer.NewEnforcer` directly** with
   `adapter.NewMockAdapter()` (in `goutils/authz/adapter/mock.go`) seeded via
   `mockAdapter.AddPolicyLine("p, sub, org, svc, obj, act, eft")` / `AddPolicyLine("g, sub, role, org")`
   calls before constructing the enforcer — this is exactly the pattern the SDK's own tests use, fully
   in-process, no network. Recommended for deterministic unit/integration stubs.
2. Run a real local Consul dev agent (`consul agent -dev`) and `PUT` KV entries under
   `authz/<env>/v1/policy/<resourceGroup>/<key>` with the `PolicyChange` JSON shape above — heavier, but
   exercises the watch/reload path too.

`enforcer.MockIEnforcer` (gomock-generated, `enforcer/enforcer_mock.go`) is also available for pure
interface-level mocking (`Enforce`, `GetImplicitPermissionsForUser`) when the test doesn't care about
casbin evaluation at all.

Org/resource/action model in one line: **org** = tenant scope (e.g. merchant org id), **resource** =
`Request.Resource` matched by casbin `KeyMatch` against policy `obj` (glob/prefix semantics), **action**
= verb (`get`/`create`/`update`/... or `*`), and **role** is the indirection between a passport `sub`
and policy rows via casbin's `g` grouping (roles typically come from `passport.GetRoles()`).

---

## 3. DCS, Splitz, ASV, worker (SQS), kafka, mutex (Redis), spine (DB), wda, telemetry

### DCS (`goutils/dcs`) — Dynamic Configuration Service client

- Not a plain REST client: it's a **login-then-bearer-token client, every call is an HTTP POST** with the
  verb encoded in the path (`goutils/dcs/httpclient/request_type.go`), auth via `Authorization: Bearer
  <sessionToken>` (`LOGIN` itself sends none):
  - `POST {ServerURL}/v1/auth/login` — login, `config.UserCredentials{Username,Password}` → session token.
  - `POST {ServerURL}/v1/kv/put`, `POST {ServerURL}/v1/kv/get`, `POST {ServerURL}/v1/kv/patch`,
    `POST {ServerURL}/v1/kv/audit` — the four `ConfigClient` operations.
  `dcs.New(ctx, opts...)` performs the login step at construction against a per-env/per-mode URL resolved
  by `config.URIFromEnvAndMode(Env, Mode)` (or an explicit `WithServerURL`, default
  `http://localhost:8081`). Requests/responses are **protobuf messages marshalled as protojson**
  (schemas come from a separate repo, `github.com/razorpay/config-proto`, not from `goutils` itself).
- Key/value addressing (`rpc.Key`): `{Namespace, Entity, EntityId, Domain, ObjectName}` — e.g.
  `Namespace:"example/pg", Entity:"merchant", EntityId:"mid_swiggy", Domain:"refund", ObjectName:"Features"`.
  `Get` supports fieldmasks for partial fetch (GraphQL-like).
  `Put` replaces a key entirely (with mandatory `AuditLog{ChangeBy,ChangeReason,ChangeApprovedBy}`);
  `Patch` updates a subset of fields.
- **Client-side cache**: in-process `cache.Cache` (own package, TTL default 15s, max size default 8MB,
  configurable via `cache.NewConfig().WithEviction()/.WithMaxCacheSize()/.WithCleanWindow()`), keyed by
  `mode/namespace/entity/entityId/domain/objectName/-fieldmask1-fieldmask2...`. `Get` reads cache first
  per-query, only round-trips for cache misses, and populates cache on response.
- **Dual mode**: if a service handles both test/live traffic off one deployment, set
  `dcs.SetContextMode(ctx, "test"|"live")` per-request; URL/mode resolution then reads from ctx.
- **`Mock=true`**: `config.Config.Mock` field + `config.NewConfig().WithMock(true)` exist, but in this
  clone's `dcs.New`/`getURLAndMode` code path **the flag is not read anywhere** — it looks like a
  documented-but-unwired knob (verify against a non-shallow clone before relying on it). The actually
  wired, deterministic stub is **`goutils/dcs/server/mock.Client`**: it embeds `*dcs.Client` and
  overrides `Get`/`Patch` in-process (`ReturnFailure bool` to force an error, `ReturnDisabled bool` to
  return an empty-bytes value instead of `[]byte{0x8,0x1}`) — implements the `dcs.ConfigClient` interface
  fully, no network. Use this for deterministic tests instead of standing up a DCS server.
- payouts consumes DCS heavily (`payouts/pkg/dcs/{client,service,interfaces}.go`,
  `payouts/pkg/dcs/features/features.go`, used from `fundManagement`, `balance`, `merchantOnboarding`,
  payout processor queueing/reservation-gate logic) — go.mod pin `github.com/razorpay/goutils/dcs v1.7.3`.

### Splitz (`goutils/splitz`) — experimentation/feature-flag client

- HTTP(Twirp) client, `splitz.NewClient(ctx, *splitz.Config, ...)`. `Config{Endpoint, Auth{Key,Secret}}`
  — auth is **HTTP Basic Auth** (`reqObj.SetBasicAuth(Key, Secret)`), not a bearer token.
- Exact Twirp endpoints (`goutils/splitz/client.go`):
  - `POST {Endpoint}/twirp/rzp.splitz.evaluate.v1.EvaluateAPI/Evaluate` — single `GetVariant`/`GetVariantOrDefault`.
  - `POST {Endpoint}/twirp/rzp.splitz.evaluate.v1.EvaluateAPI/EvaluateBulk` — `GetVariantInBulk`.
  - `POST {Endpoint}/twirp/rzp.splitz.evaluate.v1.EvaluateAPI/FetchDecisionContext`.
  - `POST {Endpoint}/twirp/rzp.splitz.segment.v1.SegmentAPI/Lookup` — segment lookups.
- Request shape: `EvaluateRequest{Id, ExperimentId /*or*/ ExperimentName, RequestData}` (one of
  ExperimentId/ExperimentName required); bulk wraps `[]*EvaluateRequest` in `BulkEvaluateRequest`.
- Two eval modes: **server-side eval** (default — request goes to Splitz service, no local cache) vs
  **client-side eval** (`ClientSideEval:true` — experiment definitions are cached locally via
  `BigCacheStorage`/`BigCacheSegmentStorage` with `Ttl`/`FallbackTTL`, and evaluation runs in-process
  against `coreevaluator/`). No pure in-memory "mock mode" flag documented in the README; deterministic
  stubbing should target the `ConfigClient`-style interface payouts wraps it in
  (`payouts/pkg/splitz/{client,splitz}.go`, `internal/provider/splitz_client.go`) rather than the SDK
  itself.
- `TrackEvent` is a **Kafka producer**, not HTTP: pushes to topic `events.splitz.v1.live` using
  `goutils/kafka`'s `ProducerConfig` supplied via `splitz.Config.KafkaConfig`; only active if
  `TrackEvents:true`.
- payouts pin: `github.com/razorpay/goutils/splitz v1.4.0`; used from payout core/service and
  `internal/routing/middleware/splitz.go` + shadow-gateway mode selection.

### ASV — `goutils/account-service` SDK

- **gRPC** client, not HTTP/Twirp: `accountService.NewClient(ctx, ...Option)` with
  `config.DefaultConfig().WithServerURL(grpcHost).WithGrpcDialOptions(...).WithCredentials(&config.UserCredentials{ClientID,Password})`.
  Optional in-memory response cache (`cache.NewInMemoryCache`, TTL default 15s / 8MB, same shape as
  DCS's cache config). Recommended grpc keepalive/backoff params documented in README (`grpc.ConnectParams.Backoff`,
  `keepalive.ClientParameters`). Client-side rate limiting is configured server-side per client ID
  (`account-service` repo's own `config/default.toml`), not in the SDK.
- `X-DB-Source` request header selects backing store per-call (`ApiMaster` vs `AsvReader`); default
  fallback is `DefaultDB` server config, recommended `AsvReader`.
- Stub needs: mock the generated gRPC client interface (under `goutils/account-service/grpcclient` /
  `internalmodules`) — this is a real network dependency (gRPC over TLS/mTLS to account-service), no
  built-in local/mock mode found in the goutils SDK itself. **Confirmed in payouts**: payouts pins
  `github.com/razorpay/goutils/account-service v1.1.20` and wraps it behind its own
  `pkg/account.IClient{Account() interfaces.IAccount}` (`payouts/pkg/account/client.go`,
  built via `accountServiceSdk.NewClient(ctx, accountServiceSdk.WithConfig(asvConfig))` from
  `config.HttpClientConfig{Host, Auth{Username,Password}}`), and ships its own deterministic test double
  at `payouts/pkg/account/mock.go` (`account.NewMockClient().WithFailure(err)/.WithNilResponse()/.WithCustomResponse(*dto.AccountResponse)`)
  — use that mock directly for payouts-side stubs rather than mocking the gRPC transport.

### `goutils/worker` (SQS) + queue broker

- `worker` package (`goutils/worker`, module `.../worker/v3`) is **broker-agnostic**: `worker.NewManager(*worker.Config, queueProvider.IQueue, logger, retrier)` against an `IQueue` interface
  (`Enqueue`/`EnqueueWithMessageKey`/`Dequeue`/`Acknowledge`/`NegativeAcknowledge`/`IsNackSupported`/`IsDLQEnabled`/`RawEnqueue`/`RawDequeue`).
  `worker.Config{QueueName, MaxConcurrency(default 1), WaitTime(default 30s), BatchSize(default 1), SyncRetryCount(default 0)}`.
- The actual SQS implementation lives at `goutils/worker/queue/broker/sqs` (`sqs.Config{QueueName, BatchSize, Region, Prefix, MaxRetries, VisibilityTimeout, WaitTimeout, Endpoint, HTTPClient, UseAnonymousCredentials, EnableRetryer, Retryer}`).
  Two constructors:
  - `sqs.New(conf)` — real AWS, driver id `sqs.Dialect = "sqs"`.
  - **`sqs.NewWithEndpoint(conf)`** — points the AWS SDK session at `conf.Endpoint` (any local SQS
    emulator/LocalStack listening there), driver id `sqs.LocalDialect = "sqs_local"`. Selected
    automatically by `queue.New(ctx, &queue.Config{Driver: "sqs_local", SQS: sqs.Config{Endpoint: "http://localhost:4566", UseAnonymousCredentials: true, ...}})`
    (see `goutils/worker/queue/queue.go`'s driver switch). This is the local/dev config knob the task
    asked about.
  - Other broker drivers available under the same `queue.Config`: `redis`, `metro` (GCP Pub/Sub-alike),
    `kafka` — each with its own sub-config (`redis.Config`, `metro.Config`, `kafka.Config`).
  - `goutils/worker/mock/manager.go` provides a mockable manager surface for pure unit tests (no queue at
    all).
  - payouts config (`payouts/config/*.toml`, `[queue]`/`[queue.sqs]`) sets `driver="sqs"`,
    real `prefix = "https://sqs.ap-south-1.amazonaws.com/<account>/"` in shown configs — swap `driver` to
    `sqs_local` + set `SQS.Endpoint` for a fully local run.

### `goutils/kafka` — producer/consumer config

- `ProducerConfig{Brokers []string, EnableTLS bool, UserCertificate/UserKey/CACertificate string, EnableSASL bool, SASLMechanism/SASLUsername/SASLPassword string, Partitioner, MaxRetry, MaxMessages, CompressionEnabled/Type, KafkaVersion, RetryBackoff, Timeout, FlushFrequency, Telemetry *telemetry.Telemetry}`
  — same shape mirrored in `ConsumerConfig` (`Brokers`, `EnableTLS`, cert fields, `RetryBackoff`, `MaxRetry`).
- **Local/LocalStack-style override**: just point `Brokers` at a local broker address (e.g.
  `["127.0.0.1:29092"]`, as shown in splitz's own Kafka example) and leave `EnableTLS:false` (default) —
  there's no separate "endpoint" field, `Brokers` *is* the endpoint list. `EnableTLS:true` with all three
  cert fields empty falls back to `InsecureSkipVerify:true` TLS (still encrypted, no cert validation) —
  useful for a TLS-enabled-but-self-signed local broker; `EnableTLS:false` is plaintext, the simplest
  local/dev path.
- Uses `IBM/Sarama` (imported as `github.com/aws/...`? no — Sarama) underneath; `Telemetry` field wires
  OTel spans/propagation onto produced messages when a `*telemetry.Telemetry` is supplied (nil = no
  tracing, safe default for tests).

### `goutils/mutex` (Redis-backed distributed lock)

- Generic `IStore` interface (`Get/Obtain/Release/KeepAlive`) — `store.FromRedisClient(redisClient)`
  adapts any go-redis-compatible client into a compatible store. `mutex.NewClient(store, Config{Scope string})`,
  then `client.New(ctx, resourceID, requestID, ttl)` to acquire, with retry helpers
  (`mock/redis.go` + `mock/provider.go` give a fake in-memory store for tests — no real Redis needed).
  For local stubbing: use `goutils/mutex/mock` directly rather than standing up Redis.

### `goutils/spine` (DB layer)

- GORM v2 wrapper: `db.NewDb(cr IConfigReader, options...)`, dialector selected by
  `ConnectionConfig.Dialect` — supported dialect constants: **`db.DialectMySQL = "mysql"`** and
  **`db.DialectPostgres = "postgres"`** (`goutils/spine/db/db.go`); dialector can also be injected
  directly via `db.Dialector(customGormDialector)` to bypass the string-based switch entirely (e.g. to
  point a MySQL-dialect service at a local MySQL/MariaDB container, or use `sqlite` for fully in-process
  tests, since any `gorm.Dialector` works once injected this way). Supports read/write/warm-storage
  connection splitting and nested transactions via savepoints.

### `goutils/wda` — WDA Service client (correction on "TiDB client" framing)

- This is **not** a direct TiDB (or any DB) driver — it's an HTTP(Twirp) client to a separate **WDA
  Service** (`wda_client.NewWdaClient(baseURI, username, password, httpClient, ...Option)`,
  HTTP Basic Auth). The generated Twirp services under `goutils/wda/rpc/wda-service/` (`tidbfind`,
  `async_query`, `wda_analyze_cron`, `wdaqueryrouter`) show the *server* queries TiDB internally, but the
  client SDK itself only ever talks HTTP to `http://<wda-host>` — there is **no MySQL/TiDB connection
  string in this SDK to redirect**. To stub WDA locally: run a fake HTTP/Twirp server implementing the
  same route(s) (Twirp-generated server interfaces are available in the same `rpc/` packages) and point
  `NewWdaClient` at it — no TiDB/MySQL instance needed for the client side. `WithTelemetry(tel)` option
  wires OTel header propagation on outbound calls.

### `goutils/telemetry` — OTel exporter config

- `telemetry.Config{ServiceName, ServiceNamespace, Environment, SamplingRatio (default 1.0 if 0/omitted — NOT a disable switch), DisableBaggageSpanAttributes, ExporterHost (default "localhost"), ExporterPort (default "4318" — OTLP/HTTP), SpanLimits, HTTPSkipPaths, HTTPEnabledPaths, EnableHTTPClientSpans}`.
  `telemetry.NewTelemetry(ctx, cfg)` builds the tracer provider + OTLP HTTP exporter pointed at
  `ExporterHost:ExporterPort`.
- **To disable telemetry for local/test runs**: there is no explicit `Enabled:false` field in this
  clone — the convention across every consumer package inspected (dcs/httpclient, kafka, wda-client) is
  simply **don't call `NewTelemetry`/don't pass a `*telemetry.Telemetry`** (`nil` is treated as
  "telemetry off" everywhere: no header propagation, no spans, calls proceed normally). To point export
  at a local collector instead of disabling, just set `ExporterHost`/`ExporterPort` to a local OTel
  collector's OTLP/HTTP listener (e.g. `127.0.0.1:4318`), which is also exactly what the SDK's own tests
  do to avoid hitting a real collector (`ExporterHost:"127.0.0.1", ExporterPort:"19999"` — nothing
  listening, connection is lazy/non-blocking, so this is a safe default for CI/local runs too).

---

## 4. proto + rpc — where things live, and how to regenerate `payouts/rpc`

Two separate repos, different roles:
- **`proto/`** (module name in its own `buf.yaml`: `buf.build/razorpay/proto`) — raw `.proto` source, one
  directory tree, organized by domain/service. This is what services `buf generate` against.
- **`rpc/`** (Go module `github.com/razorpay/rpc`, **plus several independently-versioned nested Go
  modules**: `rpc/x/go.mod` → `github.com/razorpay/rpc/x`, `rpc/validx/go.mod` →
  `github.com/razorpay/rpc/validx`, `rpc/workflows/go.mod` → `github.com/razorpay/rpc/workflows`) — this
  is the **central, already-generated Go bindings repo**: `.pb.go`/`_grpc.pb.go`/`.twirp.go` files for
  most domains, presumably produced by a central CI job running buf against `proto/` and committed here
  for consumers to `go get` directly (no per-consumer buf toolchain needed for most services).

### Locations (confirmed by directory walk)
| What | proto/ (raw source) | rpc/ (generated Go) |
|---|---|---|
| Stork webhook | `proto/stork/webhook/v1/{webhook.proto,webhook_api.proto,analytics.proto}` | **not present** — no top-level `stork` dir in `rpc/` at all |
| Ledger (platform ledger, used by payouts via ledger-sdk) | `proto/platform/ledger/{journal,ledger_config,account_detail,dashboard,ledger_entry,account}/v1/*.proto` | `rpc/platform/ledger/journal/v1/{journal_api.pb.go,journal_api_grpc.pb.go,journal_api.swagger.json}` (+ siblings for the other 5 subdomains) — **gRPC**, not Twirp |
| x-cfa (fund account / contact) | `proto/x/x-cfa/{contact,fund_account}/...` | `rpc/x/x-cfa/...` (part of the `github.com/razorpay/rpc/x` module) |
| x-balances | `proto/x/x-balances/{balance,sub_balance,accounts,admin_actions,balance_fetch}/...` | `rpc/x/x-balances/...` (same `rpc/x` module) |
| validx | **two copies**: `proto/validx/protocol/{v1,common}/*.proto` (newer/canonical, own buf module) **and** `proto/x/validx/{routing,validation}/...` (older, nested under the `x` tree) | mirrors both: `rpc/validx/protocol/...` (own module `github.com/razorpay/rpc/validx`) **and** `rpc/x/validx/...` (part of `github.com/razorpay/rpc/x`) — treat `rpc/validx` as the current one unless a consumer specifically pins the `x/validx` path |
| workflows | `proto/workflows/{config,comment,action,workflow}/v1/*.proto` | `rpc/workflows/...` (own module `github.com/razorpay/rpc/workflows`) — has **both** `_grpc.pb.go` and `.twirp.go` |
| batch (payouts' own bulk-payouts / accounting-integrations batch) | closest match is `proto/accounting_integrations/batch/v1/batch_api.proto` (belongs to `accounting-integrations`, not payouts) | not found under a payouts-specific name in `rpc/` — **payouts does not appear to have its own published proto-defined batch API**; its bulk-payouts flow is in-process (see `payouts` core repo directly, out of scope for this file) |
| charge_collections usages (payouts actually consumes this today) | `proto/charge_collections/usages/v1/...` (implied — this is one of the 2 modules payouts fetches) | `rpc/charge_collections` exists too |

payouts/go.mod has **no `require` on `github.com/razorpay/rpc`, `github.com/razorpay/rpc/x`, `github.com/razorpay/rpc/validx`, or `github.com/razorpay/rpc/workflows`** — it does not currently vendor any pre-generated package from the central `rpc/` repo at all.

### How `payouts/rpc` is actually generated (fully confirmed from `payouts/Makefile` + `payouts/scripts/proto_modules`)

payouts does **not** reference `github.com/razorpay/proto` as a buf/BSR dependency in its `buf.yaml`. Instead
its `buf.yaml` (`build.roots: ["."]`, `name: buf.build/razorpay/proto`) treats a **locally sparse-checked-out
copy of the `proto/` repo** as its own build root:

```makefile
PROTO_GIT_URL := https://github.com/razorpay/proto        # (token-injected in CI: https://$(GIT_TOKEN)@github.com/razorpay/proto)
PROTO_BRANCH  ?= master                                     # overridable, e.g. PROTO_BRANCH=some-dev-branch
PROTO_ROOT    := proto/
RPC_ROOT      := rpc/
BUF_VERSION            := v1.32.0
PROTOC_GEN_GO_VERSION   := v1.5.2     # github.com/golang/protobuf/protoc-gen-go (legacy, not google.golang.org/protobuf's)
PROTOC_GEN_TWIRP_VERSION := v5.10.1   # github.com/twitchtv/twirp/protoc-gen-twirp
GRPC_VERSION            := v1.2.0     # google.golang.org/grpc/cmd/protoc-gen-go-grpc (installed but buf.gen.yaml only wires "go"+"twirp", not grpc, for payouts)
MOCKGEN_VERSION         := v1.6.0
```
Targets:
```make
deps:            go install .../protoc-gen-go-grpc@v1.2.0 github.com/bufbuild/buf/cmd/buf@v1.32.0 \
                  github.com/golang/protobuf/protoc-gen-go@v1.5.2 github.com/twitchtv/twirp/protoc-gen-twirp@v5.10.1 \
                  github.com/golang/mock/mockgen@v1.6.0
proto-fetch:      mkdir proto/ && cd proto/ && git init --quiet && git config core.sparseCheckout true && \
                  cp scripts/proto_modules .git/info/sparse-checkout && \
                  git remote add origin https://github.com/razorpay/proto && \
                  git fetch origin master --quiet && git checkout origin/master --quiet
proto-buf-update: buf mod update proto/
proto-generate:   buf generate proto/          # uses payouts/buf.gen.yaml
proto-refresh:    clean proto-fetch proto-buf-update proto-generate     # one-shot regenerate
```
`payouts/buf.gen.yaml` wires exactly two plugins into `rpc/`, both with `paths=source_relative`:
```yaml
plugins:
  - name: go
    out: rpc
    opt: [paths=source_relative]
  - name: twirp
    out: rpc
    opt: [paths=source_relative]
```
(managed mode rewrites `go_package` to `github.com/razorpay/payouts/rpc/...` for everything except the
`googleapis`/`grpc-gateway` BSR deps declared in `buf.yaml`.)

**`payouts/scripts/proto_modules`** is the sparse-checkout allowlist — this is the actual scope of what
payouts pulls in, and today it is exactly two modules:
```
stork/webhook/v1
charge_collections/usages/v1
```
This is why `payouts/rpc/` in this clone contains only `rpc/stork/webhook` and
`rpc/charge_collections/usages` (confirmed present, non-empty, despite `rpc` being listed in
`payouts/.gitignore` line 30 — gitignore only stops it from being committed, it's still generated/checked
out on disk by CI/dev builds). **payouts has no locally-generated ledger/x-balances/x-cfa/validx/workflows
Twirp/gRPC client** — those integrations must go through `ledger-sdk` (HTTP, see §5) or other REST-style
SDKs rather than buf-generated stubs.

### Exact commands to regenerate `payouts/rpc` locally
```bash
cd payouts
make deps            # installs buf v1.32.0 + protoc-gen-go v1.5.2 + protoc-gen-twirp v5.10.1 + protoc-gen-go-grpc v1.2.0 + mockgen v1.6.0
make proto-fetch      # sparse-checkout scripts/proto_modules from github.com/razorpay/proto@master into ./proto
make proto-buf-update # buf mod update proto/   (refreshes buf.lock: googleapis + grpc-gateway BSR deps)
make proto-generate   # buf generate proto/     (go + twirp plugins → ./rpc, per buf.gen.yaml)
# or in one shot:
make proto-refresh    # = clean + proto-fetch + proto-buf-update + proto-generate
```
To pull in a **new** proto module (e.g. to add a real ledger Twirp/gRPC client instead of ledger-sdk's
HTTP wrapper), add its path to `payouts/scripts/proto_modules` (e.g. `platform/ledger/journal/v1`) and
re-run `make proto-refresh`. `GIT_TOKEN` env var is required in CI (Drone) to authenticate the sparse
checkout against the private `razorpay/proto` GitHub repo; for local dev an already-authenticated `git`
(SSH key/PAT in git config) should suffice since the Makefile falls back to the plain HTTPS URL when
`GIT_TOKEN` is unset.

### Alternative: vendor from `razorpay/rpc` via `go.mod replace`/`require` instead of running buf
Since `rpc/platform/ledger`, `rpc/x/x-balances`, `rpc/x/x-cfa`, `rpc/validx`, and `rpc/workflows` are
**already generated and committed** in the central `rpc` repo (with real `_grpc.pb.go`/`.twirp.go`
files, not just `.pb.go` messages), payouts could add a direct dependency instead of running its own buf
pipeline for any of those domains:
```
require github.com/razorpay/rpc v0.0.0-<pseudo-version-or-tag>          # for platform/ledger, x-payroll, xas, mozart, etc.
require github.com/razorpay/rpc/x v0.0.0-<pseudo-version-or-tag>        # for x-balances, x-cfa, x/validx
require github.com/razorpay/rpc/validx v0.0.0-<pseudo-version-or-tag>   # for the newer validx/protocol
require github.com/razorpay/rpc/workflows v0.0.0-<pseudo-version-or-tag>
```
then `import "github.com/razorpay/rpc/platform/ledger/journal/v1"` etc. directly — no local buf toolchain,
no `proto/` sparse checkout needed. **`stork` is the one exception**: it has no entry in `rpc/` at all, so
Stork webhook consumption must always go through payouts' own buf-generate step (or an equivalent in
whichever consumer needs it) — it cannot be vendored from `razorpay/rpc`. This vendoring path is
theoretical (payouts' `go.mod` doesn't currently reference any `razorpay/rpc*` module — verified above) —
it hasn't been validated end-to-end (e.g. whether `razorpay/rpc`'s go.sum/versions are compatible with
payouts' pinned dependency graph), so treat it as a viable option to evaluate, not a confirmed-working path.

**This closes the "payouts rpc/ is gitignored and generated from razorpay/proto via buf" open item** —
confirmed exactly how (sparse-checkout + `buf generate`, not a BSR module dependency), and gives both the
regenerate recipe and a vendoring alternative.
**This also closes UNRESOLVED_QUESTIONS #26 (Stork proto location)**: `proto/stork/webhook/v1/` in the
`proto` repo; not present in pre-generated form in `rpc`; payouts already consumes it today via its own
buf pipeline (`payouts/rpc/stork/webhook`), which is direct evidence the mechanism works end-to-end.

---
## 5. ledger-sdk

`ledger-sdk` (`github.com/razorpay/ledger-sdk`, payouts pins `v0.0.32`) is a **plain HTTP client that
speaks Twirp-style JSON** (POST + JSON body to `/twirp/<package>.<Service>/<Method>` paths — not
protobuf-binary, not gRPC); requests are hand-built via `dto.APIClient.Run` (`ledger-sdk/dto/api.go`), no
buf-generated stubs involved client-side.

### Endpoints (`ledger-sdk/common/constant.go`)
```go
EndpointJournalCreate                             = "/twirp/rzp.ledger.journal.v1.JournalAPI/Create"
EndpointJournalCreateInBulk                       = "/twirp/rzp.ledger.journal.v1.JournalAPI/CreateInBulk"
EndpointJournalFetchByID                          = "/twirp/rzp.ledger.journal.v1.JournalAPI/FetchById"
EndpointJournalFetchByTransactor                  = "/twirp/rzp.ledger.journal.v1.JournalAPI/FetchByTransactor"
EndpointAccountCreateOnEvent                      = "/twirp/rzp.ledger.account.v1.AccountAPI/CreateOnEvent"
EndpointUpdateByEntitiesAndMerchantID             = "/twirp/rzp.ledger.account.v1.AccountAPI/UpdateByEntitiesAndMerchantID"
EndpointAccountFetchByMerchantID                  = "/twirp/rzp.ledger.account.v1.AccountAPI/FetchByMerchantID"
EndpointAccountFetchByEntitiesAndMerchantID       = "/twirp/rzp.ledger.account.v1.AccountAPI/FetchByEntitiesAndMerchantID"
EndpointAccountFetchInBulkByEntitiesAndMerchantID = "/twirp/rzp.ledger.account.v1.AccountAPI/FetchInBulkByEntitiesAndMerchantID"
EndpointAccountDeactivate                         = "/twirp/rzp.ledger.account.v1.AccountAPI/Deactivate"
EndpointAccountActivate                           = "/twirp/rzp.ledger.account.v1.AccountAPI/Activate"
EndpointAccountArchive                            = "/twirp/rzp.ledger.account.v1.AccountAPI/Archive"
EndpointDashboardFetchPGMerchantAccounts          = "/twirp/rzp.ledger.dashboard.v1.DashboardAPI/FetchPGMerchantAccounts"
EndpointDashboardFetchPGMerchantBalances          = "/twirp/rzp.ledger.dashboard.v1.DashboardAPI/FetchPGMerchantBalances"
```
(Full URL = `config.APIConfig.Hostname + Endpoint`, always `POST`, JSON body = request struct.)

### Headers (`ledger-sdk/dto/api.go`, `APIClient.Run`)
```go
headers := map[string]string{
    "Accept":        "application/json",
    "Content-Type":  "application/json",
    "Ledger-Tenant": a.Tenant,   // const common.HeaderLedgerTenant = "Ledger-Tenant"
}
// + caller-supplied params.Headers merged in (request id / trace id per README)
req.SetBasicAuth(a.Username, a.Password)   // HTTP Basic Auth, from APIConfig.Username/Password
```

### Journal Create — request shape
`JournalCreateRequest` (`ledger-sdk/dto/struct.go`):
```go
type JournalCreateRequest struct {
    MerchantID         string              `json:"merchant_id"`
    Currency            string              `json:"currency"`
    TransactorID        string              `json:"transactor_id"`
    TransactorEvent     string              `json:"transactor_event"`
    TransactionDate     int64               `json:"transaction_date"`
    Notes               common.Values       `json:"notes"`                 // map[string]interface{}
    APITransactionID    string              `json:"api_transaction_id"`
    AdditionalParams    common.Values       `json:"additional_params"`
    MoneyParams         map[string]string   `json:"money_params"`
    Identifiers         common.Values       `json:"identifiers"`
    DynamicMoneyParams  []DynamicMoneyParam `json:"dynamic_money_params"`
}
```
Bulk variant: `JournalCreateBulkRequest{Journals []JournalCreateRequest}` → `EndpointJournalCreateInBulk`.
**Response shape unverified** — `APIClient.Run` returns raw `common.HTTPResponse{Response []byte, Code
int64}`; the concrete unmarshal target for journal create/fetch responses wasn't pinned down in this pass.

### Errors, incl. insufficient balance
Twirp error envelope (`ledger-sdk/common/struct.go`):
```go
type ErrorResponse struct {
    Code string `json:"code"`
    Msg  string `json:"msg"`
}
```
The SDK only defines **transport-level** error codes (`ledger-sdk/ledger/sdk_errors/error.go`, prefix
`PFLS0000xx`: `CodeLedgerServiceError`, `CodeBadRequestError`, `CodeJsonMarshalError`,
`CodeHTTPRequestError`) — on `500`/`504` it unmarshals `ErrorResponse` and wraps `errorResponse.Msg` into
`CodeLedgerServiceError`; on `502` it wraps the raw body text. **There is no typed "insufficient balance"
error** — it's a business-level string in `ErrorResponse.Msg`, detected by substring match. Confirmed via
payouts (`payouts/pkg/ledger/error.go`):
```go
const (
    BadRequestValidationFailure   = "BAD_REQUEST_VALIDATION_FAILURE"
    BadRequestInsufficientBalance = "BAD_REQUEST_INSUFFICIENT_BALANCE"
    RecordNotFound                = "record_not_found"
)
func (c *Client) IsInsufficientBalanceErrorMsg(msg string) bool {
    return strings.Contains(msg, BadRequestInsufficientBalance)
}
```
i.e. to simulate insufficient-balance in a local Ledger stub, return HTTP `500`/`504` with Twirp body
`{"code":"...","msg":"...BAD_REQUEST_INSUFFICIENT_BALANCE..."}` from
`/twirp/rzp.ledger.journal.v1.JournalAPI/Create`; payouts
(`internal/app/payouts/processor/payoutViaLedgerService.go:239`,
`internal/app/fundAccountValidation/fav_ledger.go:164`) maps this via
`errorclass.CreateLedgerJournalFailed` → `IsInsufficientBalanceErrorMsg` to
`PayoutNotEnoughBalanceViaLedger`/`FavNotEnoughBalanceViaLedger`.

### How payouts calls ledger-sdk (config + real usage)
- `payouts/go.mod`: `github.com/razorpay/ledger-sdk v0.0.32`.
- `payouts/internal/provider/ledger_client.go:19`: `LedgerTenant = "X"` — hardcoded tenant constant sent
  as `Tenant: LedgerTenant` (ends up as the `Ledger-Tenant` header value on every request).
- `payouts/config/default.toml`:
```toml
[ledger]
    host = "https://ledger-live.dev.razorpay.in"
    timeout = "200ms"
    keepAliveTimeout = "60000ms"
    maxIdleConnections = 10
    httpretryattempts = 3
    [ledger.auth]
        username = "payouts_key"
        password = "randompass"
    [ledger.httpclient.resiliency]
        maxconcurrentrequests = 100
        requestvolumethreshold = 20
        circuitbreakersleepwindow = 5000
        errorpercentthreshold = 50
        circuitbreakertimeout = 10000
    [ledger.httpclient.httpclient]
        timeout = "200ms"
```
- Consumers: `payouts/pkg/ledger/{client.go,base.go,ledger_journal_create.go,
  ledger_journal_fetch_by_transactor.go,fav_ledger_journal_create.go,
  fav_ledger_journal_fetch_by_transactor.go}`; business callers
  `internal/app/payouts/processor/payoutViaLedgerService.go` (payout journal creation) and
  `internal/app/fundAccountValidation/fav_ledger.go` (FAV journal creation), plus
  `internal/app/fundManagement/core.go`.

---

## 6. shield-sdk, governor-executor, error-mapping-module, pg-sdk, rzpconv, bvs-sdk — one paragraph each

**Real payouts dependency check first** (`payouts/go.mod`): `shield-sdk` — direct
(`github.com/razorpay/shield-sdk v0.0.5-0.20250928185645-857e66198338`). `governor-executor/v2` — present
only as `// indirect` (pulled in transitively by something else, e.g. ledger-sdk/shield-sdk/error-mapping-module;
payouts code has **zero direct imports** of it — `grep -rl "governor-executor"` across payouts `.go` files
returns nothing). `pg-sdk`, `rzpconv`, `business-verification-service-sdk-go` — **not in payouts/go.mod at
all**, direct or indirect. Treat those three as "in this workspace, not part of the payouts dependency
graph" rather than "payouts uses a subset of them."

- **shield-sdk** (`github.com/razorpay/shield-sdk`) — a pure-library (no standalone service) risk/fraud
  client with two independent surfaces: `IShieldSdkClient.PushPaymentEvent(WithContext)` publishes payment
  lifecycle events to an SQS queue (allowlist-filtered, JSON-serialized, opt-in OTel span on the SQS
  producer path via a `*telemetry.Telemetry` on `config.Sqs.Telemetry`), and `IRuleEvaluationClient` makes
  a synchronous HTTP POST (Heimdall/Hystrix circuit-breaker client) to Shield's `/v1/rules/evaluate`
  (payments) or `/v1/rules/evaluate/{entityType}` (payouts). Payouts uses **only the evaluation path**:
  `pkg/shield/client.go` wraps `shieldSdk.EvaluatePayout(ctx, shieldSdkModels.PayoutEvaluateRequest)`,
  called from `internal/app/payouts/core.go`'s `EvaluatePayoutShieldRulesForApiPayout` — a real synchronous
  network call in the payout creation path (circuit-broken, so failures are bounded, not fatal by default).
  A stub needs: an HTTP mock for `POST /v1/rules/evaluate/{entityType}` returning a
  `PayoutEvaluateResponse`-shaped body; payouts' own tests already gomock the `IShieldSdkClient` interface
  directly (`pkg/shield/shield_payout_rule_evaluate_test.go`), which is the simpler path — no live network
  needed.

- **governor-executor** (`github.com/razorpay/governor-executor/v2` in payouts' graph, `/v3` is current
  upstream per its own AGENTS.md) — an in-process business-rule-evaluation SDK (expression engines:
  govaluate/expr/grule-rule-engine) used for terminal/payment routing and rule-based filtering elsewhere in
  Razorpay (pg-router, API monolith). It can run as a **pure library** (rules constructed programmatically)
  or in **"Governor-as-source-of-truth" mode**, syncing rule namespaces from a Governor service via SQS
  worker + Redis-backed local store, with its own MySQL/GORM v1 store reader and Kafka event publishing for
  rule-execution telemetry. Since payouts has no direct import, there is nothing in payouts itself to stub
  for this one — it's only present because some direct payouts dependency (most likely shield-sdk or
  ledger-sdk, not independently confirmed which) needs it transitively. If a deeper payouts integration
  with governor-executor is ever added, the pure-library mode (no Governor service, no Redis/Kafka) is the
  cheapest to stub deterministically.

- **error-mapping-module** (`github.com/razorpay/error-mapping-module`, imported via
  `payouts/pkg/ledger/client.go`) — not a network client at all: it's a **build-time/static mapping
  library**. Services declare JSON alias files (gateway/service error code → canonical "identifier code",
  e.g. `{"Z6":"PGUP000070"}`) under a `mapper/` folder; a git pre-commit hook (or `./build_implant.sh`)
  generates a static Go mapping file that's committed to the repo. At runtime this is pure in-memory
  lookup (`errors.InitMapping(...)`, as shown in ledger-sdk's own README) — no HTTP/gRPC calls, nothing to
  stub beyond making sure the static mapping file is present and `InitMapping` is called once at boot; test
  doubles can just call the real `InitMapping` with a minimal mapping set.

- **pg-sdk** (`github.com/razorpay/pg-sdk`) — **payouts does not depend on this at all** (verified: absent
  from `payouts/go.mod`, no `.go` file references it). It's a client for **Reminders and Stork** (i.e. PG's
  own SMS/notification/webhook plumbing via `pg-sdk/reminder` and `pg-sdk/webhook` packages, each with an
  outboxer for async delivery) — conceptually adjacent to what payouts does for its own notifications, but
  a separate, PG-team-owned SDK payouts has no reason to import. No further payouts-specific notes apply.

- **rzpconv** (`github.com/razorpay/rzpconv`) — confirmed **pure in-process helper library**, no network
  calls: "Razorpay Semantic Conventions," a shared vocabulary of typed OpenTelemetry attribute
  constructors/constants (`payment.PaymentID(...)`, `merchant.MerchantID(...)`, service/environment/
  namespace enum types like `rzpconv.ServiceNamespaceX`, `rzpconv.EnvDevelopment` — seen earlier being used
  by `goutils/telemetry`'s own tests) so that the same field means the same thing across tracing, logging,
  analytics, and Kafka headers org-wide. **Not in payouts' dependency graph** — payouts doesn't reference it
  directly (it's consumed by `goutils/telemetry` and other lower-level libs instead), so nothing
  payouts-specific to stub; if used, it's compile-time constants/functions, trivially deterministic.

- **business-verification-service-sdk-go (bvs-sdk)** (`github.com/razorpay/business-verification-service-sdk-go`)
  — **not in payouts' dependency graph either** (confirmed absent from go.mod). It's a **Twirp RPC client**
  wrapping the Business Verification Service backend for KYC/CKYC/GST/PAN/website/credence-check
  operations, with one carve-out: a subset of CKYC operations bypass BVS entirely and call **Hyperverge**
  directly (different auth/config requirements for that path). Config is a `sync.Once` singleton
  (`CreateClientConfig()`, callable exactly once per process — tests must reset the package-level
  `singleInstance` directly to re-init). Since payouts doesn't touch merchant KYC/business verification,
  this is out of scope for payouts stubbing; if another RazorpayX service needing BVS is investigated
  later, treat it as: real network dependency (Twirp to BVS, or direct HTTPS to Hyperverge for the CKYC
  carve-out), test via mocking the generated Twirp client interface, not the transport.

---

## 7. go-foundation-v2 — service scaffold, not a library dependency

**Important correction to the task's framing**: `go-foundation-v2` is a **project template/scaffold repo**
("Golang Service Template" per its own README — `make rename NAME=my-new-service` rewrites the module
path and clones it into a new service), not something other services `go.mod`-import. The actual
batteries-included runtime library it demonstrates is a **separate module**,
`github.com/razorpay/foundation` (pinned `v1.0.0-alpha.12` in `go-foundation-v2/go.mod`, alpha — this
whole Foundation framework looks early-stage/new). Confirmed **none of `cfa`, `x-balances`, `ValidX`, or
`fts` depend on either `go-foundation-v2` or `github.com/razorpay/foundation`** (grepped all four go.mod
files, no hits) — so none of these payouts-adjacent services in this workspace are Foundation-based; they
must all use some other bootstrap pattern (out of scope here).

### What "Foundation" provides (from `go-foundation-v2/cmd/user/main.go`, the template's example service)
```go
server, err := foundation.NewServer("./config/user", providers.WithCustomConfig(&config.AppConfig{}))
...
server.Start(
    foundation.WithGRPCHandlers(userServer.GRPCHandler),
    foundation.WithHTTPHandlers(userServer.HTTPHandler(server.Context())),   // grpc-gateway-based HTTP
    foundation.WithHealthChecks(
        dbCollection.NewHealthCheck("primary_db", false),
        dbCollection.NewHealthCheck("replica_db", true),
    ),
    foundation.WithShutdownSignals(syscall.SIGHUP),
)
```
- **Server bootstrap**: single `foundation.NewServer(configDir, ...providerOpts)` call wires up a
  **gRPC server + an HTTP server generated from the same protos via grpc-gateway** + an internal metrics
  listener, from one config tree. `server.Container()` exposes shared boot-time singletons — confirmed:
  `.Telemetry()` (OTel handle) and `.GetDatabaseCollection()` (named DB connections, each yieldable as a
  `*spine`-style handle plus a ready-made health check via `.NewHealthCheck(name, isReplica)`).
- **Config loader**: **TOML files under `config/<service-name>/`**, per-environment
  (`config/user/default.toml`, `config/user/dev.toml` in the template), loaded by path
  (`foundation.NewServer("./config/user", ...)`) — same convention family as payouts' own
  `config/*.toml` (dialect-keyed `[databases.primary_db.connection]` blocks support `postgres`/`mysql`/
  `sqlite`, connection-pool tuning, etc.), plus a service-specific typed struct merged in via
  `providers.WithCustomConfig(config)`.
- **Health-check endpoint paths**: a *separate* `route_config.toml` per service maps concrete RPC paths to
  auth requirements — the template's are:
```toml
"/common.health.v2.HealthService/ReadinessCheck" = { basic_auth = false }
"/common.health.v2.HealthService/LivenessCheck"  = { basic_auth = true }
```
  i.e. health is itself a **gRPC/Twirp-style service** (`common.health.v2.HealthService`, presumably a
  shared proto in the central `proto`/`rpc` repos — not independently located in this pass), exposed over
  the same grpc-gateway HTTP surface as everything else, at those two exact paths.
  `ReadinessCheck` is unauthenticated (safe for k8s probes); `LivenessCheck` requires basic auth (unusual
  for a liveness probe — worth flagging if adopted, since k8s kubelet probes typically can't supply auth
  headers unless configured to).
- **Ports** (`config/user/default.toml`, `[grpc_server.server_addresses]`): `grpc = ":8080"`,
  `http = ":8081"` (the grpc-gateway HTTP surface — this is where the health paths above are actually
  reachable over plain HTTP/JSON), `internal = ":8082"` (metrics/internal-only).
- **Other batteries-included pieces visible via `go-foundation-v2/go.mod`'s indirect deps**: structured
  logging (`goutils/logger/v2` and a newer `v3` beta, both pulled in — mid-migration), tracing
  (`goutils/tracing`, `goutils/telemetry` presumably via `foundation` itself), error mapping
  (`error-mapping-module` — see §6), passport auth (`goutils/passport/v4`), config loading
  (`goutils/configloader`), a feature-flag-like `goutils/changegate`, unique ID generation
  (`goutils/uniqueid`), and worker/queue (`goutils/worker/v3`) — i.e. Foundation appears to be the
  "opinionated glue" wiring together the same goutils ecosystem described in §§1-3 of this file, rather
  than a from-scratch reimplementation.

### Unknowns / not independently verified
- `github.com/razorpay/foundation`'s own source wasn't cloned in this workspace — everything above about
  its *internal* behavior is inferred from how `go-foundation-v2`'s example service calls it, not from
  reading Foundation's implementation directly.
- `common.health.v2.HealthService` proto **is** located: `proto/common/health/v2/health_check_api.proto`
  (`package common.health.v2;`) — a shared, central health-check contract, not payouts- or
  foundation-specific. (There's also a separate, older `proto/healthcheck/v1/healthcheck.proto` and many
  per-service `<domain>/health` proto packages elsewhere in `proto/` — those are unrelated one-offs, not
  what Foundation's `route_config.toml` references.)
- Whether any *other* service in this broader Razorpay estate (outside this workspace's clone list) is
  built on Foundation is unknown; the four checked here (cfa/x-balances/ValidX/fts) are not.

---

## UNRESOLVED_QUESTIONS closed by this pass

- **#10 (passport claim schema)** — fully closed. Complete v4 claim struct with JSON tags (§1), the
  `GetLegacyAuthType` decision table with exact minting recipes for private/proxy/privilege/admin (§1),
  and the `apiv1`/`edgev1` mystery resolved as payouts' own v3 config convention (`passport.api.identifier`
  / `passport.edge.identifier`), not an SDK concept — v4 does the equivalent implicitly via JWKS `kid`.
- **#26 (Stork webhook proto location)** — closed: `proto/stork/webhook/v1/{webhook.proto,webhook_api.proto}`
  in the `proto` repo; not present in the pre-generated `rpc` repo; payouts already consumes it today via
  its own buf pipeline into `payouts/rpc/stork/webhook` (§4), which is live evidence the whole mechanism
  works end-to-end.
- **#28 (module access)** — was already granted per this task's briefing; no blockers hit accessing any of
  the 11 goutils/proto/ledger-sdk-family repos investigated here.
- **"payouts rpc/ is gitignored and generated from razorpay/proto via buf"** — closed with the exact
  mechanism (sparse-checkout of `payouts/scripts/proto_modules` from `github.com/razorpay/proto`, then
  `buf generate`), the exact toolchain versions, the exact `make` targets/commands to reproduce it, and a
  viable (but unverified/untested) alternative of vendoring pre-generated packages from the central
  `razorpay/rpc` repo for everything except Stork (§4).

## Overall unknowns / caveats carried across this whole file

- Every repo here is a **shallow, single-commit clone** — no git history, no tags. Version claims are
  inferred entirely from consumer `go.mod`/`go.sum` pins (payouts, ledger, go-foundation-v2), not from the
  library's own changelog/tags. Anything stated as "v3 does X" (passport) is reconstructed from consumer
  usage, not read from v3 source directly, since v3 isn't present in this goutils checkout at all.
  `goutils/passport`'s `ImpersonationClaims` missing JSON tags looks like a real upstream bug worth
  flagging to the passport SDK owners rather than something to work around silently.
- §3's ASV section couldn't confirm a first-party in-SDK mock/fake gRPC server (unlike DCS's
  `server/mock.Client`) — payouts' own `pkg/account/mock.go` fills that gap at the payouts layer instead.
- §4's validx "two locations" (`proto/validx` vs `proto/x/validx`) and which is canonical was not
  independently confirmed — flagged, not resolved.
- §6's governor-executor transitive-dependency source (which direct payouts dep pulls it in) wasn't pinned
  down — noted as unconfirmed rather than guessed.
- §7 is scoped to the four repos this workspace happens to have cloned (cfa/x-balances/ValidX/fts) — it is
  not a claim about Foundation adoption across all of Razorpay.

---

