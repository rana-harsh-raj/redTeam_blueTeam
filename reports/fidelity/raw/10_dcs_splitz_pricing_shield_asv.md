# 10. DCS, Splitz, Pricing (Governor/Charge-Collections), Shield, ASV, UPS, XAS — fidelity verification

Lane 10. Verifies `reports/raw-findings/26_dcs_splitz_governor_shield.md` (1402 lines, hereafter
"F26") and `reports/raw-findings/10_infra_config_flags_tests.md` ("F10") against source, and adds
ASV/UPS/XAS (not covered by F26) plus a full comparison against the ENV2_COMPOSE twin
(`substitutes/{dcs-stub,splitz-stub,pricing-stub,shield-stub,asv-stub,bankingaccounts-stub,xas-sink}`,
`seeds/{dcs,splitz,pricing.json,shield}`, `build/arena-patches/`). F26's own text is largely CONFIRMED
by direct re-reading; where this pass found something F26 missed or got wrong, it is called out
explicitly under "Corrections". The single most consequential new finding is that **client-side Splitz
evaluation, which payouts runs with, makes every Splitz-gated payouts behaviour resolve to OFF/default
in the arena today, independent of the splitz-stub's seed data** (see §Production behaviour B and
§Twin comparison).

## Scope and sources read

Repos (`REPOS=/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture`):
`goutils/dcs` (dcs.go, options.go, config/{config,uri,env,mode}.go, cache/{cache,config}.go, server/{service,server}.go,
server/features), `payouts/pkg/dcs` (client.go, features/features.go, service.go), `payouts/internal/provider/dcs_client.go`,
`payouts/internal/app/merchant/core.go`, `goutils/splitz` (client.go, config.go, context.go), `payouts/pkg/splitz/{client,splitz}.go`,
`payouts/internal/app/payouts/processor/payout_pricing.go`, `payouts/pkg/api/fetch_pricing.go`, `payouts/pkg/api/update_api_free_payouts.go`,
`payouts/pkg/ccSdk/*` (via F26, not re-read line-by-line), `api/database/migrations/2014_07_22_114552_create_pricing.php`,
`api/app/Models/Pricing/{Entity,Type}.php`, `api/app/Http/Route.php`, `api/app/Models/Payout/{Service,Core}.php`,
`api/app/Models/Payout/Processor/Base.php`, `api/app/Models/Payout/Validator.php`, `payouts/pkg/shield/*` (via F26),
`payouts/pkg/account/{client,get_account_by_id}.go`, `goutils/account-service/account-service.go`,
`payouts/internal/provider/ups_client.go`, `payouts/pkg/upiService/vpa_mapper.go`, `payouts/internal/provider/xas_client.go`,
`payouts/pkg/xAccountStatement/{client,fetch_multiple,fetch_by_multiple_references}.go`,
`payouts/internal/job/x_account_statement_source_event.go`, `payments-upi` (docker-compose/routes, spot check),
`fts/config/env.default.toml` (splitz section, spot check), `payouts/config/default.toml` (xas/ups/shield sections).

ENV2_COMPOSE: `docker-compose.yml`, `EGRESS_AUDIT.md`, `ENV2_BUILD_STATUS.md`,
`config/templates/base/payouts/{default,arena}.toml`, `build/arena-patches/{README.md,apply-arena-patches.sh,
goutils-dcs-v1.7.3/dcs.go}`, `substitutes/{dcs-stub,splitz-stub,pricing-stub,shield-stub,asv-stub,bankingaccounts-stub,xas-sink}/{server.py,CONTRACT.md}`,
`seeds/dcs/merchants.json`, `seeds/splitz/experiments.json`, `seeds/pricing.json`.

Not re-read (relied on F26, spot-checked only where load-bearing for this lane's new claims): Governor's
own gin router/rule-chain internals, Charge Collections' full proto, `governor-executor`. Not opened at
all: `shield` service repo (not cloned, per F26's own note), `charge-collections-sdk` vendored module
(unreadable — go module cache outside sandbox, same limitation F26 hit).

---

## Production behaviour

### A. DCS

#### A.1 Wire contract (CONFIRMED, matches F26 §A almost exactly)

`dcs.kv.v1.KVService`, grpc-gateway JSON over `POST /v1/kv/{get,evaluate,patch,put,audit,entities}` — see F26
lines 58-90 (re-verified against `dcs/rpc/dcs/kv/v1/kv.proto` is not re-read here; the *client-side* shape is
independently confirmed via `goutils/dcs/rpc/dcs/kv/v1` generated code and `goutils/dcs/dcs.go`, which is the
part this lane needed). `KeyValue.Value` is raw marshalled-protobuf `bytes`, base64 over JSON transport —
CONFIRMED, `goutils/dcs/dcs.go:281` (`protojson.Unmarshal(byteResponse, out)` where `out.Kvs[i].Value []byte`).
`POST /v1/auth/login` issues a JWT bearer token, ~6h TTL per F26 (not re-verified this pass).

#### A.2 Client-side cache (new detail, not in F26)

`goutils/dcs/cache/cache.go:16` + `cache/config.go:7-19`: BigCache, **15s default eviction (`DefaultEviction`),
8MB default hard cache size (`DefaultMaxCacheSize`), 1s clean window, 16 shards, 10000 max entries/window**.
Cache key = `mode/namespace/entity/entityId/domain/objectName/-fieldmask1-fieldmask2...` (`dcs.go:430-452`,
`cacheKey()`). `Get()` (`dcs.go:298-363`) checks this cache per-query first; only cache-misses go over the
wire. `Put()`/`Patch()` do not invalidate other callers' caches (single-process cache, no pub/sub).

#### A.3 `in_flight_reservation_enabled` — exact field→wire→PS mapping (task's specific ask)

| Step | Detail | Evidence |
|---|---|---|
| Proto field | `bool in_flight_reservation_enabled = 1` on message `direct_accounts.Configs`, proto3 (false = wire-omitted) | `config-proto/rzp/x/merchant/payouts/direct_accounts/configs.proto:15` (per F26, not re-read) |
| DCS Key | `namespace=rzp/x, entity=merchant, entity_id=<mid>, domain=payouts/direct_accounts, object_name=Configs` → payouts' own `PayoutDirectAccountsConfigsKey = "rzp/x/merchant/payouts/direct_accounts/Configs"` | `payouts/pkg/dcs/features/features.go:24` (comment explicitly warns: DCS actually stores this under `direct_accounts.Configs`, **not** `FundTransfer`, where the design originally assumed) |
| Feature name (API-side) | `InFlightReservationEnabled = "in_flight_reservation_enabled"` → `ApiInFlightReservationEnabled` | `features.go:62,101` |
| `Feature.Key()` dispatch | `case DcsInFlightReservationEnabled.String(): return PayoutDirectAccountsConfigsKey` | `features.go:160-161` |
| Requested every merchant-config fetch | Listed in `Feature.MerchantFeatures()` (the fixed list of API names batched into one DCS `Get`) | `features.go:277` |
| Wire fetch | `dcsService.EnabledFeatures(ctx, features, flags, entityID, mode)` groups flags by `Key()`, builds one `GetRequest` per distinct key with per-field `Fieldmasks`, calls `client.Get(ctx, req)` | `goutils/dcs/server/service.go:76-127` |
| Decode → enabled-name list | For each `KeyValue` in the response, `EnabledFeaturesForKeyFromResponse(flattenedKey, value)` unmarshals the bytes via `config-proto/gen.FetchUnMarshalFn(key)`, then `protoreflect.Range` over the message and, **for every populated field with `v.Bool()==true`, appends `p.TextName()`** (the proto field's snake_case name, e.g. `in_flight_reservation_enabled`) to the result list — proto3 fields left at zero-value (`false`) are simply absent from the Range iteration, so **presence in the returned list is itself the on/off signal**, not a value to compare | `goutils/dcs/server/features/features.go` interface, impl in `payouts/pkg/dcs/features/features.go:241-259` |
| List → map | `ConvertListToMap(features []string) FeatureMap` sets `featureMap[name] = FeatureEnabled` (1) for every non-empty name in the list | `payouts/internal/app/merchant/feature.go:63-71` |
| Merge with API-DB features | `mergeFeatures(apiFeatures, dcsFeatures)` — plain map union, **DCS wins on key collision** (`dcsFeatures` copied over `apiFeatures` last) | `payouts/internal/app/merchant/core.go:458-469` |
| Read-side check | `merchantConfig.IsFeatureEnabled(name)` — presumably `Feature[name] == 1` (not re-read; matches task's framing) | `payouts/internal/app/merchant/core.go:538` (`IsFeatureEnabled`) |

**So**: for PS to see `Feature["in_flight_reservation_enabled"] == 1`, the DCS server must return, for Key
`(rzp/x, merchant, <mid>, payouts/direct_accounts, Configs)`, a **protobuf-wire-encoded** `Configs` message
with the `in_flight_reservation_enabled` field explicitly set `true` (field 1, varint wire-type 0, tag
`0x08`, value `0x01` — i.e. bytes `08 01`) — not JSON, not a bare `"true"` string. Proto3 field-presence
semantics via `protoreflect.Range` mean a `false`/absent field must be **fully absent from the encoded
bytes** (zero-length message for an all-false `Configs`), otherwise it is silently treated as `false` on
decode regardless of what the encoder "intended" — this matches proto3's own wire semantics, not a
DCS-specific quirk.

**Twin check**: `dcs-stub/server.py`'s `FIELD_SCHEMAS["rzp/x/merchant/payouts/direct_accounts/Configs"] =
[(1, "in_flight_reservation_enabled", "bool")]` and its `encode_message()` correctly omits `false` (proto3
zero-value) and emits `_tag(1,0)+_encode_varint(1)` for `true` — **byte-exact match** to the real proto3
wire format for this specific message (verified by inspection of the encoder logic; this stub's own
CONTRACT.md claims a manual round-trip test). `seeds/dcs/merchants.json` sets
`in_flight_reservation_enabled: true` for `ARENAM00000002` ("M2 -- Direct/current account on RBL ... ON")
and `false` for M1/M3. **The stub and seed are correct.** The reason V12 (in-flight reservation) still
fails in the golden run (`ENV2_BUILD_STATUS.md:64`) is upstream of the stub — see next section.

#### A.4 Why the twin needs a `goutils/dcs` module replace, and why it still doesn't fully work

**Root cause 1 — login URL resolution ignores `ServerURL` when exactly one `Mode` is configured (CONFIRMED
by direct read, matches F26/CONTRACT.md's characterization):**

`payouts/pkg/dcs/client.go:32-39` (`GenerateOptions`) builds the client config with
`WithModes([]config.Mode{config.Live})` (exactly one mode) and **never sets `ServerURL`**. `goutils/dcs`'s
`(*Client).getLoginURI()` (`dcs.go:120-154`) is a `switch` with `case len(c.config.Modes) == 1:` appearing
**before** `case c.config.ServerURL != "":` — Go's switch takes the first true case, so the single-mode
branch wins and calls `config.URIFromEnvAndMode(c.config.Env, mode)`, which looks up a **hardcoded
env→hostname table** (`goutils/dcs/config/uri.go:30-47`, e.g. `dev_live → "https://dcs-live.dev.razorpay.in"`)
— `ServerURL` is consulted only as a fallback if that lookup returns an *empty* string, which it doesn't for
any recognized `Env`. So an unpatched `payouts-api`/`cfa-server` binary's **login call** goes to a real
Razorpay hostname, unreachable from the arena (confirmed no egress leaves the arena subnet,
`EGRESS_AUDIT.md`: "PASS: no packet left the arena subnet"), and `dcs.New()` fails (network error/timeout).

**Root cause 2 — a real, applied build-time patch exists and fixes half the problem:**
`build/arena-patches/goutils-dcs-v1.7.3/dcs.go:183-186` changes the free function `GetContextUrl(ctx)` to
return `os.Getenv("ARENA_DCS_URL")` as a fallback when the context carries no override — this function is
checked **first** in both `getLoginURI` (line 121-123) and the per-request `getURLAndMode` (line 199-210),
so if it returns non-empty it wins over the hardcoded env-map. `build/apply-arena-patches.sh:15-34` inserts,
via a source patch (not a context propagated at every call site), the block:
```go
if arenaURL := os.Getenv("ARENA_DCS_URL"); arenaURL != "" {
    ctx = dcs.SetContextUrl(ctx, arenaURL)
}
```
**only at the top of `payouts/pkg/dcs/client.go`'s `NewService(ctx, ...)`** (the one-time client-construction
function, called once at boot from `payouts/internal/provider/dcs_client.go:39`) — i.e. only into the
**boot-time context used for `dcs.New()`/login**, not into the per-request `ctx` that later flows into
`Get()`/`Patch()`/`EnabledFeatures()` calls from request-handling code (`payouts/internal/app/merchant/core.go`
etc., whose `ctx` originates from the live HTTP/worker request, never from the boot container).

**Net effect (INFERRED from code, not independently re-run against a live arena in this pass — flagged
accordingly)**: login against `dcs-stub` should now succeed (patched context reaches `dcs.New()`), but every
subsequent `Get`/`EnabledFeatures` call's `getURLAndMode()` (`dcs.go:198-258`) still falls through to
`uri := c.config.ServerURL` (empty, since `GenerateOptions` never sets it) → `len(Modes)==1` sets `mode` →
`uri == ""` triggers `config.URIFromEnvAndMode(c.config.Env, m)` again, returning the **same hardcoded real
hostname** — because the per-request `ctx` was never patched with `ARENA_DCS_URL`. With no egress out of the
arena subnet, this call fails (network error), and `payouts/internal/app/merchant/core.go:312-320`'s
non-blocking goroutine catches the error and **substitutes an empty `FeatureMap`** ("Using empty DCS features
due to fetch failure") — silently resolving every DCS-gated flag, including `in_flight_reservation_enabled`,
to "not present" (≠ 1). This is INFERRED (not observed live) but follows directly from the patch's own
insertion point (`apply-arena-patches.sh:20-21`, anchor `func NewService\(`) versus the call sites that
actually issue `Get`/`EnabledFeatures` (which take a fresh request-scoped `ctx`, confirmed at
`payouts/pkg/dcs/service.go:20-32` — `GetEnabledFeatures(ctx, ...)` takes the caller's `ctx` verbatim, no
context rewriting). **This is a plausible, well-evidenced explanation for `ENV2_BUILD_STATUS.md`'s V12
failure** ("in-flight reservation flag is read from dcs-stub for M2; PS did not register a live reservation")
that neither `ENV2_BUILD_STATUS.md` nor `dcs-stub/CONTRACT.md` currently states — recommend validating with
a live curl/log check (`docker logs payouts-api | grep -i dcs`) before treating as fully CONFIRMED.

**Is there an officially supported override?** Yes, at the SDK level: `dcs.SetContextUrl(ctx, uri)` /
`dcs.GetContextUrl(ctx)` (`dcs.go:182-196`) is exactly this — a per-call context override, "takes priority
over default configured in SDK" per its own doc comment. It is a legitimate SDK feature, just not one
`payouts/pkg/dcs`'s current call sites use per-request (only the arena's build patch injects it, and only
at construction time). No env-var-based override exists in the SDK itself for `ServerURL` beyond the
Modes-based env→host table; `ARENA_DCS_URL` is an arena-specific addition, not an upstream SDK feature.

**`Mock` flag semantics**: `config.Config.Mock bool` exists (`goutils/dcs/config/config.go:10`, `WithMock()`
at line 52) but is **never read anywhere in `goutils/dcs` outside its own tests** (confirmed: no non-test
reference to `c.config.Mock`/`.Mock` in `dcs.go`, `options.go`, or `server/*.go`). It is a vestigial/dead
config field in this SDK version. Compounding this, `payouts/pkg/dcs/client.go:34-36` hardcodes
`WithMock(false)` with the intended `WithMock(cnf.Mock)` **commented out** — so even if `Mock` did something,
payouts' own config value for it is discarded. **CORRECTION to CONTRACT.md's mock-mode framing**: there is
no way to make payouts' DCS client behave differently via a `Mock=true` config value; the only actual
lever is the context-URL override described above.

### B. Splitz

#### B.1 Wire contract (CONFIRMED, matches F26 §B)

Twirp JSON, `POST /twirp/rzp.splitz.evaluate.v1.EvaluateAPI/{Evaluate,EvaluateBulk,FetchDecisionContext,AllowCors}`,
HTTP Basic auth. `EvaluateExperimentRequest{id, experiment_id, experiment_name, request_data, track_impression}`,
response carries `experiment{id,name,exclusion_group_id,updated_at}` + `variant{id,name,variables[],experiment_id,
weight,region}` + `Reason` + `steps`. Payouts' `checkIfVariantEnabled` convention: a variant is "on" iff it has
`Variable{Key:"result", Value:"on"}` (`payouts/pkg/splitz/splitz.go:149-161`); `nil` variant → `false`.

#### B.2 Client-side evaluation is ON for payouts, and this is the single most important Splitz-twin finding

`payouts/config/default.toml:510-518`: `[splitz] client_side_eval = true` (CONFIRMED by F26, re-confirmed via
grep). `goutils/splitz/client.go:721-745` (`GetVariant`): if `s.config.ClientSideEval` (or a per-context
override, unused by payouts), it calls `evaluateClientSide(ctx, req)` (line 1089-1112) **instead of** the wire
`Evaluate` RPC:
```go
experiment := s.getExperimentFromCache(evaluateRequest)   // BigCache, keyed by experiment id/name
if experiment == nil {
    experiment, err = s.fetchExperiment(ctx, evaluateRequest)   // -> FetchDecisionContext RPC, NOT Evaluate
    ...
}
if experiment.ID == "" { return nil, nil }                 // not found -> nil variant, no error
variant, err := s.evaluateWithExperiment(ctx, evaluateRequest, experiment)  // local hash-bucket evaluation
```
i.e. with client-side eval on, payouts fetches the **whole experiment definition** (rules/variants/audience)
via `FetchDecisionContext` on cache miss, then buckets locally — it does **not** call `Evaluate` at all for a
cold cache, and never calls it for a warm one either. `GetVariantOrDefault` (`client.go:597-607`) returns the
caller's `defaultVariant` whenever the underlying `GetVariant` returns `variant == nil` — and payouts' own
wrapper (`payouts/pkg/splitz/splitz.go:91-98`) constructs that default as
`{Name:"default", Variables:[{Key:"result",Value:"off"}]}` — i.e. **the code-side default for every
`GetVariantOrDefault` caller is OFF.**

`splitz-stub/server.py`'s `_fetch_decision_context` handler (line ~113-114) is:
```python
def _fetch_decision_context(handler, body):
    return 200, {"experiments": []}
```
**always** an empty list, regardless of the request body's `identifiers`. Consequence, traced end to end:
`fetchExperiment` → `FetchDecisionContext` → empty response → `getExperimentFromServer`
(`client.go:986-1010`) treats "not in response map" as `experiment.ID == ""` → cached as
`CachedMetaData{IsNotFound:true}` → `evaluateClientSide` returns `(nil, nil)` → `GetVariantOrDefault` returns
the hardcoded `off` default; raw `GetVariant` callers get `(nil, nil)` which `checkIfVariantEnabled(nil)`
(`splitz.go:150-152`) turns into `false`. **This holds for every Splitz experiment payouts ever asks about —
the 8 named ones in F26's table, the `default_variant: "on"` fallback the stub is built to provide for
*unseeded* ids/names, and every other Splitz-gated flag in `payouts/internal/config/config.go`'s
`SplitzExperimentList` (e.g. `PerformanceOptimizationExperiment`, `ChargeCollectionsCallExperiment`,
`MerchantConfigViaAsvAndDcs`) — none of it is reachable, because the short-circuit happens one layer above
the per-merchant/default-variant lookup logic, at the "is this experiment known at all" check.**

This is **not documented in `splitz-stub/CONTRACT.md`**, which describes the `Evaluate`/`EvaluateBulk` seed
lookup in detail but says nothing about `FetchDecisionContext` mattering for payouts specifically (its one
line on the route just says "stub: `{experiments: []}`" with no fidelity-impact callout). See §Corrections
and §Twin comparison.

**Scope check — is this payouts-only?** `fts/config/env.default.toml:9742-9789`'s `[splitz]` block has no
`client_side_eval` key; `goutils/splitz/config.go:35-36`'s `ClientSideEval bool` zero-value is `false`
(server-side eval, i.e. real wire `Evaluate`/`EvaluateBulk` calls) — so **FTS's Splitz calls in this arena
should correctly reach the stub's per-merchant seed data** (not independently re-verified against `ledger`'s
or `cfa`'s configs this pass — flagged as UNKNOWN, quick grep found no `splitz` config block under
`ledger/config/` or `cfa/config/` at all, i.e. those two may not call Splitz directly in this arena).

#### B.3 Experiment/DB model, bucketing, local run

CONFIRMED unchanged from F26 §B (experiment schema, `MurmurHash` seeds 113/919, evaluation order
`whitelisting→sampler→exclusion→audience→assign_bucket`, MySQL-only experiment storage, no in-repo
definitions for any of the 8 target ids). Not re-derived here.

---

### C. Pricing (Governor/Charge-Collections vs API-Monolith `fetch_pricing_info`)

**CORRECTION to F26's framing**: F26 §C describes the ccSdk→Governor(+ChargeCollections) call topology in
detail but frames it as *the* pricing path. Re-reading `payouts/internal/app/payouts/processor/payout_pricing.go`
shows this is **one of three parallel pricing sources, selected per-payout, and NOT the default one** —
Governor/ChargeCollections is the exception path, gated by a Splitz experiment that (per §B.2) cannot
currently turn on in this arena at all.

#### C.1 Real production decision tree (`BasePayoutProcessor.FetchPricing`, `payout_pricing.go:138-355`)

1. Skip entirely if `payout.GetTransactionID()` already set or fee already populated (idempotent re-entry guard).
2. **`isChargeCollectionsCallExperimentEnabled(merchantID)`** (Splitz experiment
   `SplitzExperimentList.ChargeCollectionsCallExperiment`, `payout_pricing.go:853-867`) → if true, **primary
   source is `ChargeCollectionsPricingRuleFetcher`** → `fetchPricingUsingChargeCollections` →
   `FetchPricingRuleInfo(payout, "charge_collections")` → in-process `ccSdk.CalculatePayoutFee` (Governor +
   optional Charge Collections, per F26 §C — CONFIRMED unchanged).
3. Else (**the default path when that experiment is off, which per §B.2 is currently always, in this
   arena**): for non-`free_payout` fee types, if `PerformanceOptimizationExperiment` is on **or** the banking
   account is `Direct`, try `FetchPricingRuleInfo(payout, "redis")` — a **Redis cache read** of a key
   `MerchantId_BalanceId_Channel_Mode_Purpose_Method_Amount_FeeType_UserId` that the **API Monolith
   pre-populated at payout-creation time** (`payout_pricing.go:88-131`); used only if
   `IsPricingRuleInputFromRedisMatching` confirms the cached inputs still match the current payout and a
   `PricingRuleId` is present. Optionally fires an async **shadow** Charge-Collections call
   (`isChargeCollectionsCanaryCallExperimentEnabled` → `go b.executeChargeCollectionsCall(payout)`) purely for
   comparison/canary metrics, not used for the actual fee.
4. **Fallback (and the only path for `free_payout` fee type, and the only path when Redis misses/mismatches)**:
   live call to the **API Monolith**, `FetchPricingRuleInfo(payout, "api")` → `FetchPricingFromAPI` →
   `apiClient.GetPricingRequest` → `POST {api.Host}/payouts_service/fetch_pricing_info`
   (`payouts/pkg/api/fetch_pricing.go:16,93-113`).

So: **for any merchant not explicitly flipped onto the Charge-Collections experiment, and for every
`free_payout`, the actual production fee source is the API Monolith's live endpoint or its Redis-cached
mirror — Governor/Charge-Collections is not on the default path.**

#### C.2 `/payouts_service/fetch_pricing_info` — exact contract (CONFIRMED, api monolith source)

Route: `api/app/Http/Route.php:4818` — `POST payouts_service/fetch_pricing_info →
PayoutController@fetchPricingInfoForPayoutService`. Request validated against
`Validator::PAYOUT_SERVICE_FETCH_PRICING_INFO = 'payout_service_fetch_pricing_info'`
(`api/app/Models/Payout/Validator.php:138`). Handler:
`api/app/Models/Payout/Service.php:298-327` → `Core::fetchPricingInfoForPayoutService` (`Core.php:8635-8639`)
→ delegates to the `fund_account_payout` processor's `Base.php:4996-5069`:

| Field | Direction | Type | Notes | Evidence |
|---|---|---|---|---|
| `payout_id` | req | string | | `Base.php:5000` |
| `merchant_id` | req | string | used to `findOrFail` the merchant | `Base.php:5004` |
| `method` | req | string | | `Base.php:5012` |
| `amount` | req | int (paisa) | | `Base.php:5014` |
| `mode` | req | string (IMPS/NEFT/RTGS/UPI) | | `Base.php:5016` |
| `channel` | req | string | | `Base.php:5018` |
| `purpose` | req, optional | string | e.g. `rzp_fees`, `refund`, `vendor_payments` | `Base.php:5020-5023` |
| `fee_type` | req, optional | string | e.g. `free_payout` | `Base.php:5025-5028` |
| `user_id` | req, optional | string | | `Base.php:5030-5033` |
| `balance_id` | req | string | `findOrFailById` on Balance | `Base.php:5035-5037` |
| `fees` | resp | int (paisa) | `payout.GetFees()` after `DownstreamProcessor::processFetchPricingInfoForPayoutsService()` computes it | `Base.php:5044` |
| `tax` | resp | int (paisa) | | `Base.php:5045` |
| `pricing_rule_id` | resp | string, 14-char id | | `Base.php:5046` |
| `error`/`public_error_code` | resp, on exception | string | returned instead of fees/tax on failure | `Base.php:5060-5065` |

Payouts' Go client mirrors this **field-for-field**: `FetchPricingRequestBody{PayoutId, Purpose, BalanceId,
MerchantId, Amount, Method, FeeType, Mode, Channel, UserID}` →
`FetchPricingApiHttpResponse{Fees, Tax, PricingRuleId, Error, Code}` (`payouts/pkg/api/fetch_pricing.go:32-37,69-80`).

#### C.3 Where pricing plans/rules actually live (**CORRECTION to task framing — no separate `pricing_plans` table**)

**There is one table, `pricing`, not two (`pricing_plans` + `pricing_rules`)** — every "rule" is a row in this
single table, and `plan_id`/`plan_name` are plain columns on it (grouping is by shared `plan_id` value, not
a foreign key to a separate plans table). Migration: `api/database/migrations/2014_07_22_114552_create_pricing.php:19-144`.
Full column list (matches the Charge-Collections `pricing_api.proto` `Rule` message from F26 field-for-field,
confirming Charge Collections' proto was modeled directly on this legacy table): `id` (char 14, PK),
`plan_id` (char 14), `plan_name`, `product` (default `primary`), `feature`, `type` (default `pricing`; enum
`pricing|commission|buy_pricing`, `api/app/Models/Pricing/Type.php:12-14`), `app_name`, `gateway`, `procurer`,
`payment_method`, `payment_method_type`, `payment_method_subtype`, `auth_type`, `payment_network`,
`payment_issuer`, `emi_duration` (int, nullable), `international` (tinyint, default 0), `fee_bearer` (tinyint,
default 0 = platform), `receiver_type`, `amount_range_active` (tinyint, default 0),
`amount_range_min`/`amount_range_max` (unsigned bigint, nullable — the per-mode amount-slab bounds),
`percent_rate`/`fixed_rate`/`min_fee` (unsigned int, default 0), `max_fee` (unsigned int, nullable),
`account_type`, `channel`, `payouts_filter` (nullable — the payouts-specific matcher, e.g. `vendor_payments`),
`org_id` (char 14, nullable), `created_at`/`updated_at`/`deleted_at`/`expired_at` (int, unix ts), `audit_id`
(char 14), `fee_model` (nullable), `percent_rate_scale_factor` (unsigned int, default **100** — i.e.
`percent_rate` is scaled `/100` unless overridden, matching F26's proto note). Indexes on `plan_id`,
`(plan_name,plan_id)`, `(org_id,plan_id)`, `(org_id,plan_name)`, `international`, `account_type`, `channel`,
`deleted_at`, `created_at`.

Slab structure for a payout: multiple `pricing` rows share one `plan_id`, differentiated by `channel`/`mode`-
equivalent columns (`account_type`, `channel`, `payouts_filter`) and `amount_range_min/max` when
`amount_range_active=1` — i.e. "IMPS/NEFT/UPI slabs" are just distinct rows filtered by these columns, not a
separate schema construct. `TDS` (tax deducted at source) is a **separate concept**, unrelated to this fee/tax
pair — computed and published later via a Kafka message (`payouts/internal/app/payouts/core.go:8184-8263`,
`KafkaMessageTDS{TDSCategoryId, TDSAmount}`), out of scope for the fee/tax pricing call itself.

`decrement_free_payouts`: separate monolith endpoint `POST /payouts_service/decrement_free_payouts`
(`payouts/pkg/api/update_api_free_payouts.go:14`, response shape referenced in `seeds/pricing.json`'s
`_source` note as `{balance_id, free_payouts_consumed, free_payouts_consumed_last_reset_at}` — not
independently re-read against the monolith handler this pass).

---

### D. Shield

CONFIRMED unchanged from F26 §D on re-check of the stub (no source re-read beyond what F26 already covered
in depth): `POST /v1/rules/evaluate/payout`, actions `allow|block|review`, payouts branches only on `block`
(`review`≡`allow` from payouts' perspective), 200ms configured timeout with Hystrix-style breaker, fail-open
on both error and any non-`block` action.

---

### E. ASV ("account service")

#### E.1 Wire protocol — **CORRECTION to task framing: neither REST `/account/fetch` nor Twirp — real gRPC**

`payouts/pkg/account/client.go:15-28` (`NewClient`) builds `accountServiceSdk.NewClient(ctx,
accountServiceSdk.WithConfig(asvConfig))` where `asvConfig := accountServiceConf.DefaultConfig().
WithServerURL(ascfg.Host).WithCredentials(...)`. `goutils/account-service/account-service.go:1-90` shows this
SDK constructs a `*grpc_client.GrpcClient` and typed proto clients (`accountv1.NewAccountServiceClient(...)`,
plus `accountv2`, `merchantv1`, payment-config v2) — this is a native `google.golang.org/grpc` client
(HTTP/2 + protobuf framing), not JSON-over-HTTP and not Twirp. Payouts calls
`asvClient.Account().GetByID(ctx, request)` (`payouts/internal/app/merchant/core.go:250`) where `request =
account.GetAccountByIdRequest(ctx, merchantID, paths) = &dto.GetAccountByIDRequest{Id: merchantId, Paths: paths}`
(`payouts/pkg/account/get_account_by_id.go:8-13`) — `GetByID` with a field-mask `paths` list, gRPC unary RPC.

#### E.2 Fields payouts requests (full field-mask, CONFIRMED)

`payouts/internal/app/merchant/core.go:34-58` (`AccountServiceFetchParams`, 25 dotted paths):
`account.{id,name,email,activated,live,billing_label,business_banking,hold_funds,org_id,created_at,category,
country_code,category2,pricing_plan_id,purpose_code}`, `account.account_detail.{business_type,business_name,
business_registered_address.{line1,line2,line3,city,country,zipcode},iec_code}`. No field named "kyc" is
requested — `activated`/`business_type` are the closest proxies to "activation status"/"business type" the
task names; there is no separate KYC-status field in this mask (KYC status may live elsewhere, not traced
this pass — flagged as UNKNOWN below).

#### E.3 `merchant_config_via_asv_and_dcs_experiment` branch (CONFIRMED, matches F26 partially, adds detail)

`payouts/internal/app/merchant/core.go:111-114` gates the entire ASV-vs-legacy choice:
```go
if !IsMerchantConfigViaAsvAndDcsEnabled(ctx, merchantID) {
    fetchService = "api"
    err := c.fetchMerchantConfigFromApi(ctx, &merchantConfig, merchantID)   // legacy monolith path
} else {
    // ASV path: fetchMerchantConfigFromAsvAndDcs — asv.Account().GetByID (BLOCKING) + concurrent
    // API-DB features + DCS features, merged via mergeFeatures (see §A.3)
}
```
`IsMerchantConfigViaAsvAndDcsEnabled` (`core.go:1077-1082`) is a plain `IsSplitzExperimentOn` check against
`SplitzExperimentList.MerchantConfigViaAsvAndDcs` (prod id `Qnc6b4fg1Hi36k` per F26) — **subject to the exact
same client-side-eval short-circuit as §B.2**: in this arena, this experiment can never evaluate to "on" via
the stub's seed data, so `IsMerchantConfigViaAsvAndDcsEnabled` always returns `false` and **every merchant
config fetch in the arena takes the legacy API path (`fetchMerchantConfigFromApi`), never the ASV path** —
independent of whether asv-stub could even answer (§Twin comparison, it can't — wrong wire protocol either
way, so the two gaps compound rather than partially cancelling).

#### E.4 `banking_account_service` — a distinct, correctly-stubbable client with a similar name

Payouts also has `[banking_account_service]` → `payouts/pkg/bankingAccountService` — plain HTTP/JSON,
`GET /payouts/shield/merchant/{merchant_id}/details?account_number=...` →
`{"data":{"merchant_id","business_id","business_type","sales_team","account_number"}}`
(per `bankingaccounts-stub/CONTRACT.md`, not independently re-read against payouts source this pass — flagged
UNCONFIRMED at the field level but the endpoint shape/client-distinctness is corroborated by
`asv-stub/CONTRACT.md`'s own explicit note and the arena config difference below). This is used on the
Direct-account create path (business details lookup), **separate from** the gRPC ASV client above despite the
similarly-named config sections. Confirmed correctly wired: `config/templates/base/payouts/arena.toml:278-286`
sets `[banking_account_service] host = "http://bankingaccounts-stub:8080"`.

---

### F. UPS (VPA validation)

`payouts/pkg/upiService/vpa_mapper.go:17-77`: `POST {ups.Host}/v1/payments/validate/account`, request
`{entity: "vpa", value: "<upi-handle>"}` (`VpaMapperRequestBody{Entity, Value}`, called with
`appConstants.VPA` at the one call site — `payouts/internal/app/payouts/core.go:8153`), response
`{vpa, success, customer_name}` (`VpaMapperResponse`). Consumed by `Core.FetchMappedVpa` (`core.go:8121-…`),
exposed as an internal HTTP route (`payoutController.go:1417`, `FetchMappedVpa`) — an on-demand VPA→customer-
name resolution endpoint, not something every payout calls.

**Twin gap — MISSING, not merely a placeholder**: `config/templates/base/payouts/arena.toml:361-362` sets
`[ups] host = "http://127.0.0.1:1"` — a deliberately unroutable loopback address (connection refused
immediately). **No `ups-stub` substitute exists in `substitutes/` or `docker-compose.yml`** (grep for
"validate/account"/"ups-stub" in the compose file returns nothing). Any exercise of `FetchMappedVpa` in this
arena will fail fast with a connection error; whether payouts' caller path tolerates that gracefully was not
traced this pass (single call site, not read beyond the request builder).

Real service (`payments-upi`) **is** independently buildable/runnable locally:
`payments-upi/deployment/dev/docker-compose.yml` (api + mysql `db` + `migration` + `redis`, ports 8080-8082),
route confirmed server-side at `payments-upi/internal/app/validate/server.go:59` ("ValidateAccount acts as
the new RPC endpoint for payments/validate/account requests for VPA entity to UPS") and
`.../validate/validation.go:25`.

---

### G. XAS (x-account-statements)

Two distinct, unrelated mechanisms both loosely called "XAS" in this codebase:

**G.1 — Read path (payouts → XAS, HTTP/JSON, real client, config present but host not wired to any stub for
this specific client in the reviewed templates)**: `payouts/pkg/xAccountStatement` — `GET
{xas.Host}/v1/account_statements?entity_ids=...&entity_type=...` (`fetch_multiple.go:13,39-79`,
`FetchMultipleStatementsQueryParams{EntityIds []string, EntityType string}` →
`FetchMultipleStatementsResponse{Page,Limit,TotalPages,TotalCount,Statements []AccountStatementData}`), plus a
sibling `fetch_by_multiple_references.go` (also `GET`, not read in full this pass). `[xas]` config section:
`payouts/config/default.toml:633-651` (`host = "https://x-account-statements.dev.razorpay.in"` in real envs).
This is a **query**, used by payouts to look up account-statement rows XAS already indexed (e.g. for UTR/GRN
reconciliation, gated by Splitz experiment `xas_source_event_utr_and_grn_match_experiment` id
`RStPt2vqXrd5Wl`, `payouts/config/default.toml:555` — not traced beyond the config-key discovery this pass).

**G.2 — Write/publish path (payouts → queue, what "xas-sink" substitutes for)**: `payouts/internal/job/
x_account_statement_source_event.go` defines `XasEventDetails{EntityID, EntityType, Utr, EventCreatedTimestamp,
EventId, GatewayRefNumber, CmsRefNumber, Status, Mode, Amount, BalanceId, FundAccountID, ContactID, Name,
Contact, Email, PayoutPurpose, ContactType}` and `QueuePushForAccountStatementSourceJob(ctx, eventDetails)`
(`:78-99`), which JSON-marshals the struct and calls `provider.GetWorker(ctx).PerformWithoutSerialization(...)`
to push it onto a queue named by config key `Job.XAccountStatementSourceEvent` (`internal/config/config.go:282`,
`mapstructure:"x_account_statement_source_event"` — exact queue-name value not located in `default.toml` this
pass, likely under a `[configs.*]`/queue-name-mapping block not grepped; flagged UNKNOWN below). This looks
like a fire-and-forget publish that the **real** x-account-statements service would consume as its own
ingestion pipeline; **nothing in this arena drains it** — `xas-sink/server.py` implements only `GET /health`
(`ROUTES = {}`), by its own CONTRACT.md's explicit admission: "Deliberately does not drain LocalStack SQS
queues yet... Env 2's own flow catalogue does not require it." So: **PS sends `XasEventDetails` payloads to a
queue; nothing reads them back in this arena; this is a known, documented gap** (not a fidelity bug — `xas-sink`
never claimed otherwise), consistent with the task's framing.

---

## Corrections to existing reports

| Report | Claim | What source actually says | Evidence |
|---|---|---|---|
| F26 §C (Governor) | Frames ccSdk→Governor(+Charge Collections) as the pricing call path payouts uses, without noting it is conditional | Governor/Charge-Collections is used only when `isChargeCollectionsCallExperimentEnabled` (Splitz) is on; the default path (that experiment off) is a Redis-cache read (populated by the monolith at payout creation) falling back to a **live monolith call** `POST /payouts_service/fetch_pricing_info`, and `free_payout` fee type *always* uses the monolith call regardless of the experiment | `payouts/internal/app/payouts/processor/payout_pricing.go:138-355` |
| F26 §B / task framing | Implies the splitz-stub's per-merchant seed assignments (and its `default_variant: on` fallback) determine what payouts observes | Because payouts runs with `client_side_eval = true`, every `GetVariant`/`GetVariantOrDefault` call resolves via `FetchDecisionContext`, which `splitz-stub` always answers with `{"experiments": []}` — every experiment is "not found" regardless of the `Evaluate`/`EvaluateBulk` seed data, so **every Splitz-gated payouts behaviour currently evaluates to its code-side default (typically off)**, not to the seeded per-merchant variant | `goutils/splitz/client.go:1089-1112` (`evaluateClientSide`), `splitz-stub/server.py` `_fetch_decision_context` (always `{"experiments": []}`), `payouts/config/default.toml:511` (`client_side_eval = true`) |
| `dcs-stub/CONTRACT.md` | States the workaround for the DCS URL-resolution blocker is setting `[dcs] Env` to an unrecognized string so `dcs.New()` fails fast, non-fatally | `build/arena-patches/` shows a *different*, more targeted fix is actually applied: an `ARENA_DCS_URL` env-var override patched into `goutils/dcs`'s `GetContextUrl()` plus a source-patch that threads it through `NewService`'s boot-time context — this reaches login, but (per §A.4) not the per-request `Get`/`EnabledFeatures` calls, which use a different, unpatched `ctx`. CONTRACT.md documents the *earlier* workaround, not the one actually wired into `docker-compose.yml`'s `ARENA_DCS_URL` env var | `build/arena-patches/README.md`, `build/apply-arena-patches.sh:15-34`, `docker-compose.yml` (multiple `ARENA_DCS_URL: "http://dcs-stub:8080"` lines), `dcs-stub/CONTRACT.md`'s "Known, load-bearing gap" section |
| Task brief framing (item 5) | Asks whether ASV is `/account/fetch` (REST) or Twirp | Neither — it is native gRPC (`google.golang.org/grpc`, protobuf framing) via `goutils/account-service`, confirmed by the SDK's own `grpc_client.GrpcClient` and `accountv1.NewAccountServiceClient` construction | `goutils/account-service/account-service.go:1-90`, `payouts/pkg/account/client.go:15-28` |
| Task brief framing (item 3) | Asks about "API-DB `pricing_plans`/`pricing_rules` tables" (plural, implying two tables) | There is **one** table, `pricing` — `plan_id`/`plan_name` are columns on individual rule rows, not a foreign key to a separate plans table | `api/database/migrations/2014_07_22_114552_create_pricing.php:19-144` |
| F26 §A Unknowns | "Whether DCS injects any non-zero-value defaults server-side... was not confirmed" | Not resolved by this pass either (DCS server internals out of this lane's re-read scope) — carried forward as UNKNOWN, not contradicted. |

---

## Twin comparison

| Component | Production mechanism | Twin mechanism (file) | Verdict | What differs | Controls |
|---|---|---|---|---|---|
| DCS KV Get/Evaluate | grpc-gateway JSON, protobuf-bytes `value`, JWT bearer | `dcs-stub/server.py`: hand-rolled proto3 encoder, byte-exact for the 7 payouts/CFA messages it knows, static bearer token | CONTRACT-FAITHFUL for the wire shape and seed content | Unreachable by an unpatched binary's *business* calls (only boot-time login is patched, per §A.4) — INFERRED gap, not directly re-observed live | tenant config, feature flags incl. `in_flight_reservation_enabled`, workflow gating |
| DCS client (`goutils/dcs`) URL resolution | Modes-based hardcoded env→host map, `ServerURL` mostly dead for single-mode configs | `build/arena-patches` module replace + boot-only `ARENA_DCS_URL` context injection | INCORRECT (partial fix) | Login succeeds; per-request Get/Patch calls still resolve to the real hostname and fail (no egress) → DCS features silently resolve empty | same as above |
| Splitz Evaluate/EvaluateBulk (server-side eval callers, e.g. likely FTS) | Twirp JSON, per-merchant bucketing | `splitz-stub` seeded lookup by `(experiment_id\|name, merchant_id)`, `default_variant:"on"` fallback | CONTRACT-FAITHFUL | None found for server-side callers | routing/flag gating for those services |
| Splitz client-side eval (payouts, `client_side_eval=true`) | `FetchDecisionContext` fetch + local hash-bucket eval | `splitz-stub`'s `FetchDecisionContext` always returns `{"experiments":[]}` | INCORRECT | Every payouts Splitz experiment evaluates to code-side default (off), regardless of seed data — this is the dominant Splitz behaviour for the lane's primary consumer | pricing-source selection (`ChargeCollectionsCallExperiment`), ASV-vs-legacy merchant config (`MerchantConfigViaAsvAndDcs`), workflow-naming, redis-vs-api pricing (`PerformanceOptimizationExperiment`), and every other payouts `SplitzExperimentList` entry |
| Governor rule-chain execution | `POST /v1/namespaces/:id/execute`, dynamic JSON, govaluate rules | None — ccSdk boots `Mock=true`, builds `MockPayoutCalculator` only, no HTTP client ever constructed | N/A (bypassed, not substituted) | Consistent with "Governor path is conditional and currently unreachable anyway" (§B.2/§C) — no fidelity loss from omitting it, since the gating experiment can't turn on | fee/pricing-rule selection (only when experiment on) |
| Pricing (`/payouts_service/fetch_pricing_info`) | API-monolith live handler, real `pricing` table lookup via `DownstreamProcessor` | `monolith-stub`'s `_fetch_pricing_info`: flat per-mode table from `seeds/pricing.json`, task-specified Rs2+18%GST flat across IMPS/NEFT/RTGS/UPI, no amount-range slabs, no absent-rule error path | REPRESENTATIVE | Real production differentiates by `amount_range_min/max`, `channel`, `payouts_filter`, etc.; twin is intentionally flat (documented in `seeds/pricing.json`'s own `_task_requirement` note) and never returns the `error`/`code` failure shape | fee/tax set on every payout; free-payout/rzp_fees/refund overrides correctly zeroed |
| `pricing-stub` (separate container) | — | Reads the same `seeds/pricing.json`, answers `/fetch_pricing_info` | Dead code path (documented) | ccSdk never dials it (Mock=true, no host rendered) | none currently |
| Shield evaluate | `POST /v1/rules/evaluate/payout`, allow/block/review, 200ms timeout | `shield-stub`: fixture-driven rule matcher (`eq/ends_with/starts_with/contains`, one level of `and`), latency-injection env var | CONTRACT-FAITHFUL | None found | payout block/review/allow, fail-open on timeout |
| ASV `Account().GetByID` | Native gRPC (protobuf framing) | `asv-stub`: stdlib `http.server` JSON | INCORRECT (wrong wire protocol, not just wrong schema) | Cannot be reached by an unpatched binary at all — confirmed by `asv-stub/CONTRACT.md`'s own analysis, corroborated by direct read of `goutils/account-service`'s gRPC construction | activation, business type, KYC-adjacent fields, merchant-config source selection |
| `banking_account_service` (business/KYC-ish details, Direct-account create) | Plain HTTP/JSON | `bankingaccounts-stub`, correctly wired (`arena.toml:279`) | CONTRACT-FAITHFUL (per its own CONTRACT.md; field-level contract not independently re-verified against payouts source this pass) | — | Direct-account creation merchant/business detail lookup |
| UPS `validate/account` | HTTP/JSON, real `payments-upi` | **No stub at all**; `[ups] host` deliberately points at an unroutable address | MISSING | Any `FetchMappedVpa` call fails fast; not exercised by golden flows per this lane's scope (not independently confirmed which golden flows touch it) | VPA→customer-name resolution (on-demand internal endpoint, not on the main payout critical path per code read) |
| XAS read (`GET /v1/account_statements`) | Real x-account-statements HTTP/JSON | No dedicated stub found wired to `[xas]` host in the reviewed templates (not confirmed either way this pass — `xas-sink` implements no such route) | Likely MISSING (UNCONFIRMED — `config/templates/base/payouts/arena.toml`'s `[xas]` host value was not directly inspected this pass) | — | UTR/GRN reconciliation source-event matching |
| XAS write (`XasEventDetails` queue push) | Would feed a real XAS ingestion pipeline | `xas-sink`: `GET /health` only, does not drain the queue | MISSING (explicitly documented as such) | Events are pushed and never consumed in this arena | none observable downstream currently |

---

## Recommendation: real vs substitute

- **DCS**: keep the substitute, but **fix the patch's scope** — thread `ARENA_DCS_URL`/`SetContextUrl` into
  every per-request call site that constructs a fresh `ctx` before calling `Get`/`Patch`/`EnabledFeatures`
  (not just `NewService`'s boot context), or patch `getURLAndMode` itself the same way `getLoginURI` was
  patched (arguably simpler — one function, one patch, matches what `GetContextUrl`'s doc comment already
  promises: "takes priority over default configured in SDK", so making it actually take priority
  consistently is in the spirit of the SDK's own design). Real DCS (docker-compose e2e stack, DynamoDB-local +
  MySQL + Kafka, per F26) is buildable but heavier than fixing this one patch; recommend fixing the patch
  first and only falling back to real DCS if the fix proves insufficient.
- **Splitz**: **implement `FetchDecisionContext` for real** in `splitz-stub` — return, for each requested
  `ExperimentIdentifier`, a full `Experiment{id, name, status:"activated", sampling_percentage:100, variants:[...],
  default_variant, rules:[]}` synthesized from the same per-merchant assignment table the stub already has for
  `Evaluate`, expressed as `whitelisting: [{merchant_id, variant_id}]` or a 100%-weighted single-variant
  `variants` list per merchant-targeted rule — whichever is simpler given the existing
  `evaluateWithExperiment`/bucketer code path expects a real rule/variant structure, not a raw
  merchant→variant map. This is the single highest-leverage fix in this whole lane: it silently governs
  pricing-source selection, ASV-vs-legacy config routing, and every other payouts experiment. Real Splitz
  (docker-compose dev stack + seeded MySQL `experiments`/`variants` tables, per F26) is a heavier alternative;
  recommend the `FetchDecisionContext` fix first.
- **Governor/Charge-Collections**: no action needed while the gating experiment is unreachable (see above);
  if the Splitz fix above is made and a test scenario deliberately turns `ChargeCollectionsCallExperiment` on,
  either build a small Governor+Charge-Collections stub pair per F26's own stub spec, or (simpler) point
  ccSdk at `pricing-stub` and set `Mock=false` with a real host rendered — not attempted this pass, flagged as
  a follow-up once Splitz is fixed.
- **Pricing (monolith `fetch_pricing_info`)**: substitute is fine for happy-path golden flows; if a
  regression test needs to exercise per-mode fee differences or the "pricing rule absent" error code, extend
  `seeds/pricing.json` with distinct per-mode fees and add an `error`/`code` response for unmatched
  merchant/mode combos to `monolith-stub`.
- **Shield**: substitute is adequate as-is.
- **ASV**: keep `asv-stub` only as a documented placeholder (per its own CONTRACT.md); do not invest further
  in it unless a golden flow specifically needs the gRPC path reachable, in which case a real gRPC mock
  server (Go, using the SDK's own generated `accountv1` types) is the only faithful option — a JSON REST stub
  can never satisfy this client. Given §E.3's finding that the gating Splitz experiment is itself unreachable,
  fixing Splitz first may make this moot for most flows (everything falls through to the legacy `api` path
  already, which the monolith-stub does serve).
- **UPS**: build a minimal `ups-stub` (`POST /v1/payments/validate/account` → `{vpa, success, customer_name}`)
  if any golden flow exercises `FetchMappedVpa`; otherwise leave the blackhole address as an intentional
  "not exercised" marker, but document that intent explicitly (currently only inferable from the config
  value, not stated anywhere).
- **XAS**: leave as documented placeholder per its own CONTRACT.md unless Env 4 (per that file's own
  forward-reference) is scaffolded.

---

## Synthetic data

| Family/table | Field | Source evidence | Type+length | Constraints | Allowed values | FK/relationships | State rules | Distribution matters? | Generation rule | Tier |
|---|---|---|---|---|---|---|---|---|---|---|
| DCS `Configs` (in-flight reservation) | `in_flight_reservation_enabled` | `config-proto/.../configs.proto:15` (per F26); `features.go:24,160-161` | bool | proto3, false=wire-omitted | true/false | keyed by merchant_id | per-merchant toggle | yes — need ≥1 merchant true, ≥1 false to test both branches | seed distinct per archetype (direct=ON, shared=OFF) | EXACT (field name/type), REPRESENTATIVE (which merchants get which value) |
| DCS `Workflows`/`FundTransfer`/`ApiInterface`/`Cfa`/`PayoutModeConfig` | all fields | F26 schema table (config-proto), re-confirmed via `dcs-stub`'s `FIELD_SCHEMAS` | bool/int64/repeated string/map | proto3 zero-value defaults | see F26 | keyed by merchant_id | independent per-field | yes, per archetype | `seeds/dcs/merchants.json` per-merchant | EXACT field shape, REPRESENTATIVE values |
| Splitz experiment | `id` | `splitz/internal/experiment/model.go` (per F26) | CHAR(14) | primary key | base62-ish | referenced by config `*_experiment` keys | activated/created/terminated/scheduled | n/a (id is opaque) | use the 8 real-looking ids from F26/prod configs | EXACT (real prod ids where known) |
| Splitz variant assignment | merchant_id → variant name | `splitz-stub`'s seed format | string | must match a `variants[].name` | "on"/"off"/custom | (experiment_id, merchant_id) | per-experiment override, else `default_variant` | yes, need per-merchant differentiation to test branching | 3 archetypes × 8 experiments, hand-picked per F26's "known semantics" heuristic (F26's own words: "not reverse-engineered from any real Splitz data") | REPRESENTATIVE (assignments are guessed, not real) — **and currently UNREACHABLE for payouts regardless, per §B.2** |
| `pricing` row | `percent_rate/fixed_rate/min_fee/max_fee` | `api/database/migrations/2014_07_22_114552_create_pricing.php:89-103` | unsigned int | default 0/0/0/null | any | `plan_id` groups rows | none (static config) | yes if testing amount slabs | current twin uses one flat rule per mode, no slabs | REPRESENTATIVE (flat, task-specified deliberate deviation, documented in `seeds/pricing.json`) |
| `pricing` row | `amount_range_min/max`, `amount_range_active` | same migration:80-87 | unsigned bigint / tinyint | nullable / default 0 | any | — | slab boundary | yes for slab tests | not currently modeled in twin at all | ASSUMED-MISSING (not present in current seed) |
| Shield rule | match condition + response | `seeds/shield/rules.json` (per shield-stub CONTRACT.md) | JSON | first-match-wins, `and`-nestable | eq/ends_with/starts_with/contains | keyed by `input.*` field | none | yes, need block/review/allow coverage | 4 scripted scenarios (suffix-9999 block, M1+vendor_payments block, M3 review, default allow) | REPRESENTATIVE (scripted, not derived from real Shield rules — real rule catalogue not accessible, per F26) |
| ASV account fields | 25-path field mask | `payouts/internal/app/merchant/core.go:34-58` | mixed | — | — | keyed by merchant_id | — | only matters if asv-stub is ever made reachable | `asv-stub` currently returns a fixed 4-field shape (`activated,hold_funds,account_id,status`), far narrower than the real 25-field mask | ASSUMED-INSUFFICIENT if ASV is ever made reachable — would need all 25 paths modeled |
| XAS `XasEventDetails` | 17 fields (utr, grn, status, mode, amount, ...) | `payouts/internal/job/x_account_statement_source_event.go:35-50` | mixed | `omitempty` on most | — | keyed by entity_id/entity_type | — | n/a, nothing consumes it yet | none needed until xas-sink drains queues | EXACT shape (struct read directly), no synthetic values needed yet |

---

## Cannot be derived from repositories

1. **Real Splitz experiment definitions** (rollout %, audience rules, whitelist) for any of the ids named in
   F26/this report. Owner: Splitz/experimentation platform team (MySQL is the only source of truth). Minimal
   request: a sanitized export of the `experiments`/`variants` rows for the ~10-15 ids `payouts`,
   `x-balances`, `fts` reference (F26 §B table + `SplitzExperimentList` in `payouts/internal/config/config.go`),
   or read access to a non-prod Splitz admin UI/DB replica. A schema-only export is *not* sufficient here —
   the actual rollout values are the point.
2. **Shield's real rule catalogue** (rule IDs/conditions for the payout entity type). Owner: Shield/risk
   platform team; repo not cloned (F26's own limitation, unresolved by this pass). A sanitized rule-definition
   export (rule name/condition/action, no merchant lists) would be sufficient for a more representative stub.
3. **Charge-Collections-SDK's exact wire shape to Governor's `/execute`** (how `Payout`/`Balance`/`Merchant`
   map into `EvaluatingEntity`/`SupportingEntities`) — vendored module unreadable in this sandbox (F26's
   limitation, unresolved). Owner: Payments-Platform/Governor team; a copy of the vendored
   `charge-collections-sdk` module source (or its own repo, if separate from `governor`) would resolve this.
4. **Whether `ledger`/`cfa` call Splitz directly in this arena, and with what `ClientSideEval` setting** —
   grepped and found no `[splitz]` config block in either repo's `config/` directory, but this was a quick
   grep, not a full trace of every possible experiment call site. Owner: this investigation, follow-up pass;
   no external artifact needed, just more read time.
5. **Real XAS ingestion contract** (what shape it expects for the `XasEventDetails` queue payload, which
   queue names, whether it's SQS or Kafka) — `x-account-statements` repo not in this lane's read list at all.
   Owner: x-account-statements team. A schema-only description of its inbound event contract (queue names +
   payload shape) would let `xas-sink` actually drain and validate, closing the "nothing reads it back" gap.
6. **KYC-status field** — the task names it explicitly as something PS consumes from ASV; the 25-path field
   mask in `AccountServiceFetchParams` has no field literally named for KYC status. Either it doesn't exist on
   this path (activation/business_type are the actual proxies) or it's fetched elsewhere not traced this
   pass. Owner: payouts team / ASV proto definition; a look at the full `accountv1.Account` proto message
   (not present in this clone set) would resolve this definitively.
7. **Exact queue name for `x_account_statement_source_event`** — config key confirmed
   (`internal/config/config.go:282`) but its value not located in `payouts/config/default.toml` in this pass's
   grep (likely present under a queue-name-mapping block not searched specifically). Not a missing artifact,
   just unresolved with the time budget — a follow-up grep/read would close this without external input.

---

## Fidelity tier verdicts

- **DCS wire/seed format**: CONTRACT-FAITHFUL SUBSTITUTE — proto3 wire bytes are correct for the 7 messages modeled; reachability from an unpatched binary's business calls is the open question (see next line).
- **DCS client reachability (business calls, not login)**: UNKNOWN-BLOCKED — the applied patch fixes login but (by code inspection) not per-request Get/Patch calls; not independently re-run live this pass, so "PS never sees DCS features" is INFERRED, not directly observed — most important reason: `ARENA_DCS_URL` is injected only at `NewService`'s boot-time `ctx`, and every business call uses a fresh, unpatched request-scoped `ctx`.
- **Splitz Evaluate/EvaluateBulk (server-side callers)**: CONTRACT-FAITHFUL SUBSTITUTE — matches the real Twirp contract and seed-driven per-merchant resolution.
- **Splitz client-side eval (payouts, the lane's primary consumer)**: INCORRECT SUBSTITUTE — most important reason: `FetchDecisionContext` always returns an empty experiment list, so every payouts Splitz check silently resolves to its code-side default (usually off), making the carefully-built per-merchant seed table unreachable for payouts specifically.
- **Governor/Charge-Collections**: REPRESENTATIVE (bypassed via ccSdk `Mock=true`) — most important reason: the gating experiment can't turn on anyway (previous bullet), so bypassing it currently loses no real fidelity, but this is contingent on the Splitz fix status, not an independent design choice.
- **Pricing (`fetch_pricing_info`)**: REPRESENTATIVE SUBSTITUTE — most important reason: flat Rs2+18%GST across all modes/merchants is a deliberate, documented simplification of the real amount-range/channel-differentiated `pricing` table.
- **Shield**: CONTRACT-FAITHFUL SUBSTITUTE — most important reason: matches the real endpoint, action vocabulary, and timeout/fail-open semantics with a scripted, first-match-wins rule fixture.
- **ASV (`Account().GetByID`)**: UNKNOWN-BLOCKED / effectively INCORRECT — most important reason: the real client is native gRPC; a stdlib JSON HTTP stub is a different wire protocol entirely and cannot be dialed by an unpatched binary, independent of any schema fidelity question.
- **`banking_account_service`**: CONTRACT-FAITHFUL SUBSTITUTE (per its own CONTRACT.md; not independently re-verified at the field level this pass).
- **UPS**: MISSING — most important reason: no substitute container exists at all; the configured host is a deliberate blackhole address.
- **XAS read path**: UNKNOWN-BLOCKED — most important reason: not confirmed this pass whether any stub answers `[xas]`'s configured host at all.
- **XAS write/queue path**: MISSING (documented) — most important reason: `xas-sink` explicitly implements no queue-draining logic yet, by its own CONTRACT.md's admission.
