# mozart-sim — CONTRACT (PRIMARY Mozart substitute)

**`mozart-sim` is the PRIMARY Mozart substitute for this arena**, not a
fallback — confirmed during this pass that the real `mozart` binary
(`build/mozart.Dockerfile`, the `mozart-mock` service) cannot be built:
`go build` of the `../mozart` clone fails on the private module
`github.com/razorpay/integrations-utils` (404 for this build identity), a
hard blocker independent of anything in this scaffold. `mozart-mock`
remains defined in `docker-compose.yml` under an opt-in `mozart-real`
profile (off by default, not depended on by `core`) in case a working
`integrations-utils` credential is available in a future environment; see
that service's comment. `config/generate.py`/`ARENA_MOZART_IMPL` default
is now `mozart-sim`.

Spec taken verbatim from `findings/25_stork_mozart.md` §C.2 ("Lightweight
standalone substitute") — that section explicitly precedents this pattern
on the *existing* `fts/cmd/mock-server` in this same codebase, so this is
not a novel design. The 6 grounded scenarios (`arena_processed_100100`
through `arena_reversed_100600`) were cross-checked directly against real
files under `mozart/app/testdata/fts/rbl/v1/{transfer_init,transfer_status}/`
and against `fts/internal/providers/mozart/error_code.go`'s status mapping
— see `seeds/mozart_scenarios.json`'s own `_source` note.

## Endpoints (all POST, single process multiplexes every namespace)

```
POST /{namespace}/{gateway}/{version}/transfer_init
POST /{namespace}/{gateway}/{version}/transfer_status
POST /{namespace}/{gateway}/{version}/gateway_auth
POST /{namespace}/{gateway}/{version}/gateway_session
POST /{namespace}/{gateway}/{version}/beneficiary_verify
POST /{namespace}/{gateway}/{version}/beneficiary_register
POST /{namespace}/{gateway}/{version}/account_balance
POST /{namespace}/{gateway}/{version}/create_otp
```

Auth: accepts any HTTP Basic credentials (does not replicate Mozart's
per-client credential-name reflection, per §C.2's explicit simplification).

## Response envelope (matches real Mozart exactly — no `meta` field, findings/25 §B.2)

```json
{"data": {"...action-specific..."}, "error": null, "external_trace_id": "SIM_TRACE_ID", "mozart_id": "SIM_MOZART_ID", "success": true, "next": {}}
```

## Scenario selection

**Grounded scenarios (priority, checked first)** — `seeds/mozart_scenarios.json`,
`transfer_init` keyed on `entities.attempt.amount` (as a string, per real
Mozart's own matching field), `transfer_status` keyed on
`entities.attempt.gateway_ref_no` (matches real Mozart `-mock`'s own
selection field per `mozart/app/mock/mappings.go`):

| amount | init `bank_status_code` | `gateway_ref_no` | status behaviour |
|---|---|---|---|
| `100100` | `INITIATED` | `ARENAUTR0000001` | always `SUCCESS`, utr=ref, credited+debited |
| `100200` | `INSUFFICIENT_FUND` (init fails) | — | n/a |
| `100300` | `INITIATED` | `ARENAUTR0000003` | poll 1 `PENDING`, poll 2+ `SUCCESS` (stateful) |
| `100400` | `INITIATED` | `ARENAUTR0000004` | always `CBS:188` ambiguous, UTR present, `is_credited/is_debited: null` |
| `100500` | `DUPLICATE_TXN` (init fails, `Pending` per FTS mapping) | — | n/a |
| `100600` | `INITIATED` | `ARENAUTR0000006` | poll 1 `SUCCESS`, poll 2+ `RETURNED` with `return_utr` (stateful) |

**Legacy/generic scenarios** (kept for back-compat, checked after the
grounded table above finds no match) — **amount-keyed**
(`entities.attempt.amount`, falling back to top-level `amount`):

| amount | transfer_init | transfer_status |
|---|---|---|
| 1 | min-amount success | min-amount success |
| 100 | success | rejected |
| 200000 | max-amount success | — |
| 200005 | invalid-amount failure | — |
| 300 | validation error | — |
| 500 | — | transaction-not-found |
| 800 | gateway error | — |
| 1000 | — | Vault bad-request |
| 2000 | — | gateway-session success |
| 3000 | — | gateway-auth success |
| other | — | generic success |

**Beneficiary-account-suffix-keyed** for `beneficiary_register`/`beneficiary_verify`
(last 2 digits of `entities.fund_account.bank_account.account_number`, falling
back to top-level `account_number`):

| suffix | register | verify |
|---|---|---|
| `00` | duplicate (pending) | — |
| `01` | failed | failed |
| other | success | success |

## Stateful `transfer_status` polling

In-memory dict keyed on `gateway_ref_no` (`entities.attempt.gateway_ref_no` /
top-level `gateway_ref_no`) storing `{scenario, poll_count}` so repeated
polls of the same ref can progress `pending -> processed` after
`MOZART_SIM_POLL_TO_SUCCESS_AFTER` polls (default 2) — mirrors real gateway
ambiguity-then-resolve behaviour without needing every scenario hardcoded.

## Health

`GET /health` -> 200.

## Validated

`python3 -m py_compile`; ran `server.py` on port 18812, curled all 6
grounded scenarios end-to-end (init 100100 -> status ARENAUTR0000001
processed; init 100200 -> insufficient-fund failure; status ARENAUTR0000003
poll1 pending / poll2 processed; status ARENAUTR0000004 ambiguous CBS:188
with UTR; init 100500 -> duplicate-txn failure; status ARENAUTR0000006
poll1 success / poll2 returned with `return_utr`) — every response body
byte-matches `seeds/mozart_scenarios.json`'s `init_response`/
`status_response`/`poll_sequence` fixtures.
