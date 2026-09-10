# dcs-stub substitute contract

Substitutes `goutils/dcs` (the DCS config/feature-flag server). Endpoints and
message shapes below are taken directly from the generated grpc-gateway code
and cross-checked against `findings/26_dcs_splitz_governor_shield.md` section
A, not guessed:

- `goutils/dcs/rpc/dcs/kv/v1/kv.pb.go` / `kv.pb.gw.go` — message field names, REST paths.
- `goutils/dcs/rpc/dcs/auth/v1/auth.pb.go` — `LoginResponse{AccessToken string \`json:"access_token"\`}`.
- `goutils/dcs/key.go` — key flatten format (`{namespace}/{entity}[/{entity_id}]/{domain}/{object_name}`).
- `goutils/dcs/dcs.go` `getLoginURI()` / `goutils/dcs/config/{env,uri}.go` — see
  **Known, load-bearing gap** below; this is the reason the gap exists.
- `config-proto/rzp/x/merchant/payouts/*.proto` (field numbers/types, via
  findings/26's schema table) — drives this stub's proto3 wire encoder.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/auth/login` | any Basic-Auth/body accepted → `{"access_token": "arena-dcs-static-access-token"}` |
| POST | `/v1/kv/get` | batched key reads, real protobuf-encoded `value` bytes |
| POST | `/v1/kv/evaluate` | identical behaviour to `Get` in this stub (single storage "level", no level-combining to simulate) |
| POST | `/v1/kv/put` | full replace of one key's value |
| POST | `/v1/kv/patch` | partial update (fieldmask-respecting if `fieldmasks` given, else full merge) |
| POST | `/v1/kv/audit` | stub: always `{"audit_logs": []}` |
| POST | `/v1/kv/entities` | stub: `{"entity_ids": [...]}`, all seeded merchant ids |
| GET | `/_arena/legacy/{merchant_id}` | **stub-only** — returns the legacy-name alias table for that merchant (see below) |

## Wire format — real protobuf, not opaque JSON

`KeyValue.value` is base64(protobuf bytes) of the object named by
`Key.object_name`, exactly as real DCS serves it (`kv.pb.go`). This stub
ships a small proto3 encoder/decoder (varint bool/int64, length-delimited
string/repeated-string, and `map<string,string>` via nested
`{1:key,2:value}` entries) driven by `FIELD_SCHEMAS` in `server.py`, one
entry per config-proto message this arena's fixtures use (`Workflows`,
`FundTransfer`, `ApiInterface`, `Cfa`, `direct_accounts.Configs`,
`direct_accounts.PayoutModeConfig`) — field numbers/types taken from
`findings/26_dcs_splitz_governor_shield.md`'s schema table (itself read
from `config-proto/rzp/x/merchant/payouts/*.proto`). Proto3 zero-value
omission (bool `false`, int64 `0`, empty string/list/map are NOT written to
the wire) is reproduced for byte-level fidelity. Verified round-trip
locally: `python3 substitutes/dcs-stub/server.py` + curl + manual
`decode_message()` call (see `findings/30_env2_substitutes.md`).

## Seed data

`seeds/dcs/merchants.json`, mounted at `/app/seed/merchants.json`
(`DCS_SEED_FILE` env). Per merchant (`ARENAM00000001/2/3`):
- `dcs`: the real DCS objects (`rzp/x/merchant/payouts/...` full paths →
  field-name→value dicts), served via `/v1/kv/get|evaluate` as protobuf bytes.
- `legacy`: a stub-only convenience alias table (see next section), served
  via `GET /_arena/legacy/{merchant_id}` as plain JSON.

## Legacy names — NOT a real DCS contract

The task asks this fixture to serve both the DCS names AND legacy names
(`payout_workflows`, `skip_workflow_for_api`, `skip_wf_for_payroll`).
`findings/26_dcs_splitz_governor_shield.md` (:184-224) establishes that DCS
itself has **no such literal second name per flag** — the real legacy-interop
mechanism is the Proxy service's `DCSFeature{write_via_client,
read_enabled_via_dcs, dual_write_enabled}` (per-flag booleans gating whether
DCS or the legacy API-monolith MySQL `Feature` table is authoritative), not
a name mapping. Since this arena's API monolith is itself `monolith-stub`
(fixture-driven, not a live dual-write), the "legacy name" surface is
represented here as a **hand-kept alias table** (`legacy` block in the seed
file) rather than a live mechanism — `GET /_arena/legacy/{merchant_id}` is a
stub-only debug/convenience endpoint, not a real DCS or Proxy API path. Alias
map used to build the seed file:

| Legacy name | DCS name | DCS object |
|---|---|---|
| `payout_workflows` | `enable_payout_workflow` | `Workflows` |
| `skip_workflow_for_api` | `skip_approval_workflow_for_api` | `Workflows` |
| `skip_wf_for_payroll` | `skip_workflow_for_payroll` | `Workflows` |
| `in_flight_reservation_enabled` | `in_flight_reservation_enabled` | `direct_accounts.Configs` (same name both sides) |
| `queue_payout_bal_buffer` | `queue_payout_bal_buffer` | `FundTransfer` (same name both sides) |
| `skip_ifsc_lookup` | `skip_ifsc_lookup` | `Cfa` (same name both sides) |
| `payout_service_enabled` | `payout_service_enabled` | `ApiInterface` (same name both sides) |

## Known, load-bearing gap: pristine payouts-api/cfa-server cannot reach this stub

`payouts/pkg/dcs/client.go` (`GenerateOptions`) and
`cfa/internal/dcsservice/client.go` (`newDCSServer`) both build their
`goutils/dcs` client with `WithModes([]config.Mode{config.Live})` (exactly
one mode) and never set `ServerURL`. `goutils/dcs@v1.7.3`'s
`(*Client).getLoginURI()` (`goutils/dcs/dcs.go:120-` +
`goutils/dcs/config/uri.go`) takes this exact branch:

```go
switch {
case c.dualMode:                       // false here
case len(c.config.Modes) == 1:         // TRUE -- payouts and CFA both hit this
    mode = c.config.Modes[0]
    // falls through to URIFromEnvAndMode(env, mode), a HARDCODED
    // env->real-hostname map (dcs-test.dev.razorpay.in etc) -- ServerURL
    // is NEVER consulted in this branch.
case c.config.ServerURL != "":         // dead code for payouts/CFA's construction
    return c.config.ServerURL, nil
```

So the `[dcs] ServerURL = "http://dcs-stub:8080"` key this arena's
`payouts.toml.tmpl`/`cfa.toml.tmpl` renders is **inert** against an
unpatched binary — there is no config-only way to redirect either service's
DCS client at this stub. Both build spikes independently hit and documented
this exact blocker (`findings/28_build_spike_payouts.md` "Blockers" #1,
`findings/28_build_spike_cfa.md` incident #1) and both used the SAME
confirmed-safe workaround: set `[dcs] Env` to a string `config.GetEnv()`
does not recognize (e.g. `"arena-disabled"`), so `dcs.New()` fails
immediately on `config.GetEnv(cfg.Env)` **before any network call**, and the
caller treats this as non-fatal (payouts logs+returns the error but does not
panic per the spike; CFA's own caller explicitly treats it as non-fatal,
DCS-driven flags just resolve `false`). `config/templates/payouts.toml.tmpl`
and `cfa.toml.tmpl` are set to this value — see their own `[dcs]`/`[Dcs]`
comments. **Net effect: DCS-driven feature flags are effectively OFF for
every merchant in this arena unless a payouts/cfa binary patched with
`dcs.SetContextUrl()` (the build spike's "bonus" experiment) is used
instead of the pristine one.** This stub is still fully spec-built (for the
verifier, manual testing, or a future patched binary) — the gap is
documented here rather than worked around by weakening the stub, since
weakening it wouldn't fix the actual blocker (it's in `goutils/dcs`, not in
this stub).

## x-balances

`x-balances`'s own `DCSConfig` (see `xbalances.toml.tmpl`) sets `Mock=false`
and a `ServerURL`, same as payouts/CFA — findings/26 did not trace
x-balances' own DCS client wrapper code, so it is unknown (not confirmed
either way) whether x-balances' wrapper has the same `WithModes([Live])`
single-mode shape that triggers the identical blocker above, or whether it
differs. Flagged as an open TODO, not assumed either way.

## Fieldmask projection on Get/Evaluate (T16, M4 — panic fix)

**Deviation corrected, 2026-09-07.** Real DCS applies a query's per-slot
`Fieldmasks` as a *redaction after the read*: the returned config object
carries **only** the fields the query listed (empty/absent fieldmask =
whole object, no redaction). Ground truth in the pinned DCS clone:
`dcs/internal/kv/helper.go` `redaction.apply()` -> registry `RedactFn`, and
`dcs/internal/kv/service.go:436 redactionForQuery` (test
`helper_test.go:219 TestEvaluateFeatureValue_FieldmaskExcludesUnlistedFields`
asserts unlisted fields are dropped).

Before this fix, `_get_or_evaluate` / `_resolve_value_bytes` **ignored
fieldmasks** and always encoded the whole stored object. That over-return
crashed the pristine `payouts-api`: on `GET /v1/payouts/_meta/summary` the
merchant-config path (`payouts internal/app/merchant/core.go:524`
`fetchFeaturesFromDcsSimple` -> `fetchAllFeaturesConcurrently`) calls
`pkg/dcs/service.go:26 GetEnabledFeatures` -> goutils `EnabledFeatures`,
which groups the requested **bool** feature flags by their DCS object
(`rzp/x/merchant/payouts/FundTransfer`) into one Get with a bool-only
fieldmask, then hands the response to
`payouts pkg/dcs/features/features.go:241 EnabledFeaturesForKeyFromResponse`.
That function `Range()`s **every populated field** of the returned object and
calls `.Bool()` on each. Because the stub also returned the object's
`queue_payout_bal_buffer` (**field 6, int64**, seeded = 50000 for every Direct
merchant and for fixture ARENAM00000002/3), `.Bool()` panicked
`type mismatch: cannot convert int64 to bool` and the process exited.

Production never hits this: its fieldmask redaction drops
`queue_payout_bal_buffer` from the response because it is not in the
bool-only fieldmask the EnabledFeatures path sends (payouts'
`MerchantFeatures()` does not list the int64 flag). This is therefore a
**substitute bug** (stub over-returned), not payouts source fragility that
the stub should preserve.

**Fix (production-faithful):** `_get_or_evaluate` now passes each query's
fieldmask names to `_resolve_value_bytes`, which — when the fieldmask is
non-empty — encodes only the stored fields named in it. Empty/absent
fieldmask still returns the whole object (matching `redaction.apply`'s
`len(fields)==0 -> value unchanged`). No other key, route, or the
stdlib-only proto encoder/decoder changed. Reads that ask only for bool
flags now receive only bool flags; a caller that explicitly masks
`queue_payout_bal_buffer` still gets it (verified: full object, bool-only
mask, and int64-only mask all round-trip correctly).
