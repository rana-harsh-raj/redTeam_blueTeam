# VERIFIER_SPEC — Env 2 (Public API payout) cross-service invariant verifiers

Version 2026-09-04.v1. Companion to `CONTROL_AND_INVARIANT_CATALOG.md` (controls C1–C42, invariants 1–6),
`PAYOUTS_FLOW_CATALOG.md` (flows B, E, F, I) and `INITIAL_PAYOUTS_ENVIRONMENT_BOM.md` §2 (Env 2 golden flows
G1–G8) and §8 (validation policy). Implementation: `ENV2_COMPOSE/verifier/` (pytest package, see its own
README-equivalent at the top of `conftest.py`).

All HTTP paths, header names, table/column names and status-transition tables below are taken from source, not
inferred, with file:symbol citations. Where a fact could not be confirmed from an accessible repo (topology not
yet finalized by the ENV2_COMPOSE build track, or a substitute whose own `CONTRACT.md` records a TODO), the
verifier is written to `skip` rather than assert, and the gap is called out under "Fidelity notes".

## 0. Topology, ports and auth conventions used by every verifier

Confirmed from `ENV2_COMPOSE/substitutes/kong-lite/server.py` (`DEFAULT_ROUTES`) and each service's own
`config/default.toml`, not the task's a-priori assumption (two ports differ from that assumption — noted below):

| Service | Address (in `rzp-arena` network) | Source |
|---|---|---|
| kong-lite (host entrypoint) | `kong-lite:8000` (host-mapped; only container on both `rzp-ingress` and `rzp-arena`) | `kong-lite/CONTRACT.md` |
| payouts-api | `payouts-api:9400` | `payouts/config/default.toml` `port = ":9400"`; `kong-lite/server.py` |
| ledger-api (Twirp) | `ledger-api:8080` | `ledger/config/default.toml` `port = "127.0.0.1:8080"`; `kong-lite/server.py` |
| fts-web | `fts-web:80` | `kong-lite/server.py` `DEFAULT_ROUTES["/v1/fts"]` |
| cfa-server | `cfa-server:8081` | `kong-lite/server.py` |
| xbalances-server | `xbalances-server:8080` | `kong-lite/server.py` — **note: differs from the task prompt's a-priori `:8081`; the actual substitute code says `:8080`, used here** |
| monolith-stub / dcs-stub / splitz-stub / shield-stub / pricing-stub / stork-capture / asv-stub | `<name>:8080` (each `base_stub.py` `STUB_PORT` default) | `substitutes/_common/base_stub.py` |
| mozart-mock | `mozart-mock:8085` (assumed; **no substitute directory exists yet under `ENV2_COMPOSE/substitutes/` at spec time** — see Fidelity notes on V17/V18) | task assumption only |
| mysql-payouts / mysql-fts / mysql-xbalances | `:3306` each, DB names assumed `payouts`/`fts`/`xbalances` | task assumption; schema confirmed from goose migrations |
| postgres-ledger | `:5432`, DB assumed `ledger` | `ledger/config/default.toml` |
| mongo-cfa | `:27017` | task assumption |
| redis | `redis:6379` shared, or per-service `PAYOUTS_REDIS_URL`/`FTS_REDIS_URL` if the arena splits them | BOM §1 lists per-service Redis; kong-lite doesn't route Redis so this can't be confirmed from the substitute code |

**kong-lite is a byte-forwarding path-prefix proxy only** — it does not rewrite Twirp method names and its own
`CONTRACT.md` flags that any prefix mismatch 404s at the upstream, not at kong-lite. The real ledger Twirp path is
`/twirp/rzp.ledger.journal.v1.JournalAPI/Create` (confirmed in `ledger/rpc/ledger/journal/v1/journal_api.twirp.go:90`,
`baseServicePath(...,"rzp.ledger.journal.v1","JournalAPI")`), which **does** match kong-lite's `/twirp/ledger` prefix
rule textually (`/twirp/ledger...` vs the real `/twirp/rzp.ledger...` — it does **not** actually match, since the
literal prefix is `/twirp/rzp.ledger`, not `/twirp/ledger`). Verifiers therefore call `ledger-api`, `fts-web`,
`cfa-server` and `xbalances-server` **directly** (the verifier container runs inside `rzp-arena`) for anything that
isn't the public payouts create/fetch/cancel path, and use `kong-lite` only for the merchant-facing `/v1/payouts*`
calls that flow catalog B documents as the public entrypoint. This is recorded as a fidelity note on every verifier
that talks to ledger/FTS/CFA/x-balances directly.

**Auth — corrected against `payouts/internal/routing/router/payout_routes.go`, not just the flow catalog's prose**:
`payoutRoutes` (the group carrying `POST ""`/create, `GET /:id`, `GET ""`, `POST /cancel_payout/:id`) requires
**both** `middleware.BasicAuth(cred.API, cred.Workflow)` (a *service* credential — the identity payouts-api expects
from whatever process is calling it: the API monolith in production) **and** `middleware.PassportAuthentication`
with `X-Passport-JWT-V1` (`goutils/passport/passport.go:9`, RS256-only, JWKS-validated —
`goutils/passport/handler.go:56-83`) carrying `consumer.id = merchant_id`, `consumer.type = "merchant"`. **Payouts-api
does not accept a merchant's raw `key_id:key_secret` directly** — that pair is validated at Kong/the monolith in
production and translated into the passport's `consumer` claim before the request ever reaches payouts-api. This
matters for the Env 2 scaffold: `kong-lite/CONTRACT.md` states plainly that kong-lite "performs NO authentication/
authorization itself" and its default route table sends `/v1/payouts` straight through to `payouts-api:9400` with no
translation — which means, **as currently built, a merchant cannot successfully call `POST {kong_lite}/v1/payouts`
with just their key pair**; this is gap #9 below, and it is a real gap in the scaffold relative to its own BOM
(`INITIAL_PAYOUTS_ENVIRONMENT_BOM.md` §2 lists "Kong-lite reverse proxy (basic-auth-x → consumer, **mint passport
private**...)" as the intended F2 behavior — that minting is not implemented in `kong-lite/server.py` yet). Every
verifier below that calls a `payoutRoutes` endpoint therefore supplies the service Basic-Auth pair itself (env
prefix `PS_SERVICE`, matching `cred.API`) **and** a passport JWT for the target merchant (via
`helpers/passport.py` — see below), standing in for the missing Kong/monolith translation, and documents this
substitution inline rather than silently calling it a "public API" test. Internal routes
(`/v1/payouts/transfer_status_webhook`, `/v1/payouts/update_payouts_with_fts`, `/v1/inflight_reservations`) accept
the same `cred.API/Workflow/Xperience/FTS/VendorPayments/Settlements/Irctc` family (so `PS_SERVICE` covers them too);
`/v1/cron/*` uses the separate `cred.FastCron` (`payout_internal_routes.go`, `cron_routes.go`). None of these
credential pairs are generated yet (`ENV2_COMPOSE/secrets/` is empty at spec time), so `helpers/creds.py` resolves
them from env vars first, then `/run/secrets/<name>` files (mirroring `substitutes/_common/base_stub.py`'s own
convention), and a verifier **skips** — it does not fabricate credentials — when neither is present.

**Passport JWT for merchant identity on `payoutRoutes` calls**: `helpers/passport.py` resolves a token either from
a pre-minted `PASSPORT_STATIC_JWT_<MERCHANT_KEY>` env var, or by POSTing a claims shape to a live
`PASSPORT_SIGNER_URL` minting endpoint — **there is no HS256/shared-secret shortcut**: `goutils/passport/handler.go`
rejects any signing method other than `*jwt.SigningMethodRSA`, so a verifier without a real RSA-signed, JWKS-published
token cannot authenticate a `payoutRoutes` call at all and must skip (this itself is gap #9's practical consequence
for every public-route verifier below, not only a theoretical note).

**Merchants** (seeded per `dcs-stub/CONTRACT.md`): `ARENA_M1` (Shared/ledger-backed balance),
`ARENA_M2` (Direct/current account, RBL, `in_flight_reservation_enabled=on`), `ARENA_M3` (workflow-enabled,
`enable_payout_workflow=on`). Concrete merchant_id / key_id / key_secret / balance_id / fund_account_id values are
not yet seeded (`ENV2_COMPOSE/seeds/` is empty); `conftest.py` reads them from `ARENA_M{1,2,3}_MERCHANT_ID` /
`_KEY_ID` / `_KEY_SECRET` / `_BALANCE_ID` / `_FUND_ACCOUNT_ID` env vars and skips any test whose merchant fixture is
unset.

**Validation-status legend** (verbatim from `INITIAL_PAYOUTS_ENVIRONMENT_BOM.md` §8): `CODE_CANDIDATE` <
`CROSS_SERVICE_CONFIRMED` < `END_TO_END_CONFIRMED` < `PRODUCTION_REPRESENTATIVE`; `BLOCKED_BY_FIDELITY_GAP` when a
missing/simplified dependency could change the conclusion. Each verifier below states the status it is *capable* of
reaching when all its "substitutes must be active" are real/live and its assertions pass — this is a ceiling, not a
promise; a run where a required substitute is absent produces a `skip`, not a passing lower-status result.

---

## V1 — Idempotency: same key, same body → same payout id

- **Covers**: C5 (idempotent create), Invariant 3 (`(idempotency_key, merchant_id)` → same payout id).
- **Preconditions**: `ARENA_M1` fixture (Shared, ledger-backed); dcs-stub, splitz-stub, shield-stub (allow), cfa-server,
  ledger-api, fts-web, monolith-stub, stork-capture all reachable.
- **Stimulus**:
  1. `POST {kong_lite}/v1/payouts` — Basic Auth `PS_SERVICE` (service credential, **not** the merchant's key pair —
     see "Auth" above), header `X-Passport-JWT-V1: <M1 passport, consumer.id=M1_MERCHANT_ID, authType=private>`,
     `X-Payout-Idempotency: <uuid>`, body `{"account_number": M1_ACCOUNT_NUMBER, "fund_account_id":
     M1_FUND_ACCOUNT_ID, "amount": 100, "currency":"INR", "mode":"IMPS", "purpose":"payout",
     "queue_if_low_balance": true}`.
  2. Repeat the identical request (same idempotency key, byte-identical body) after a random 0–2s jitter.
- **Observation points**:
  - Both HTTP responses' `id` field.
  - `SELECT id, idempotency_key, merchant_id, request_hash, source_id, source_type FROM idempotency_keys WHERE idempotency_key=%s AND merchant_id=%s` (payouts MySQL; columns per
    `payouts/internal/database/migrations/20221004002554_create_idempotency_keys_table.go`) — expect exactly one row.
  - `SELECT id, status FROM payouts WHERE id=%s` — exactly one payout row.
- **Assertions**: both responses' `id` are equal; exactly one `idempotency_keys` row and one `payouts` row exist;
  second call latency ≤ first call's DB round trip + mutex overhead (no duplicate FTS/ledger side effects — see V3/V4
  cross-check that FTS `transfers` and ledger `journal` also have exactly one row each for this payout).
- **Max wait**: 15s total (mutex `MutexTimeOutForIdempotencyKey` is 20 min in prod; the verifier does not wait that
  long — a hang past 15s is itself a failure of the mutex-release path).
- **Expected result per status**: `END_TO_END_CONFIRMED` when payouts+ledger+fts+cfa are all real (F3) and dcs/splitz/
  shield/stork are the documented F2 stubs — the full create path (Flow B) executes and lands in an
  independently-observed DB state in three services. `CROSS_SERVICE_CONFIRMED` if the payout does not reach a
  terminal FTS state within the wait window (mozart-mock absent/unscripted) — the idempotency invariant itself is
  still confirmed from `idempotency_keys`+`payouts`, just not the full lifecycle.
- **Substitutes required active**: dcs-stub, splitz-stub, shield-stub, monolith-stub, stork-capture (F2); payouts,
  ledger, fts, cfa (F3).

## V2 — Idempotency: same key, different body → 400 BAD_REQUEST

- **Covers**: C5, `errorclass.SameIdempotencyKeyDifferentRequest` (`payouts/internal/routing/middleware/idempotency_key.go:214-232`).
- **Preconditions**: same as V1, one already-created payout (reuse V1's idempotency key + M1).
- **Stimulus**: `POST {kong_lite}/v1/payouts` with the **same** `X-Payout-Idempotency` key as an already-succeeded
  request but `amount` changed (e.g. +1 paisa).
- **Observation points**: HTTP status code and body; `idempotency_keys` row for that key (`request_hash` unchanged).
- **Assertions**: HTTP `400`; response body error code corresponds to `errors.BadRequestError` /
  `ErrSameIdempotencyKeyDifferentRequest`; `idempotency_keys.request_hash` for the key is unchanged from V1's first
  request (no overwrite); no second row in `payouts` for that merchant+key.
- **Max wait**: 10s (single synchronous call, no async path).
- **Expected result per status**: `CROSS_SERVICE_CONFIRMED` — only payouts + its own MySQL are on the path; no
  downstream service participates in a rejected request, so this cannot exceed cross-service (in-service) confirmation
  even with every substitute real.
- **Substitutes required active**: none beyond payouts + MySQL; runs whenever V1 has run.

## V3 — FTS transfer dedupe on (source_id, source_type)

- **Covers**: C20, Invariant 3 (`(source_id, source_type)` → same transfer id).
- **Preconditions**: FTS reachable directly (`fts-web:80`), FTS Basic-Auth credential for the `PS` identity
  (`fts/.agents/skills/repo-skill/modules/integration/apis/transfer-api.md` auth table: `PayoutsService`/`"PS"`).
- **Stimulus**: two identical `POST http://fts-web:80/v1/transfer` calls (Basic Auth as `PS`), body
  `{"product":"PAYOUT","merchant_id":M1_MERCHANT_ID,"source_type":"payout","source_id":"<fixed test id>",
  "fund_account_id":<int>,"amount":100,"mode":"IMPS"}` (fields per `fts/internal/migrations/00005_transfers_table_create.go`
  and `internal/transfer/transfer.go:171-213` validation rules), same `source_id`/`source_type` both times.
- **Observation points**: `SELECT id, source_id, source_type, status FROM transfers WHERE source_type='payout' AND source_id=%s`
  (unique key `unique_source (source_type, source_id)` per the migration) — exactly one row; both HTTP responses' `id`.
- **Assertions**: both calls return the same transfer `id`; exactly one `transfers` row for the `(source_id,
  source_type)` pair; second call does not create a second `attempts` row
  (`SELECT COUNT(*) FROM attempts WHERE transfer_id=%s`).
- **Max wait**: 10s.
- **Expected result per status**: `CROSS_SERVICE_CONFIRMED` — FTS + its own MySQL only; becomes part of V1's
  `END_TO_END_CONFIRMED` chain when driven via the full payouts create path instead of direct FTS calls.
- **Substitutes required active**: none (direct FTS + MySQL).

## V4 — Ledger dedupe on (transactor_id, transactor_event)

- **Covers**: C18 (app-level mutex, **no DB unique index** — this verifier is the one the catalog calls out as
  "test it"), Invariant 3.
- **Preconditions**: ledger-api reachable directly, ledger `[auth]` service credential, M1 ledger account seeded
  (`accounts` row with `merchant_id=M1_MERCHANT_ID`, `balance ≥ 100`).
- **Stimulus**: two concurrent (fired within the same 100ms window, not sequential) `POST http://ledger-api:8080/twirp/rzp.ledger.journal.v1.JournalAPI/Create`
  calls with identical `transactor_id` (a fixed test payout-shaped id) and `transactor_event="payout_initiated"`
  (event-name convention confirmed via `payouts/internal/app/reversals/core.go:284-285` comments and
  `payouts/internal/app/payouts/core_test.go:10302` — literal `payout_processed`, not `payout.processed`), same
  `amount`, `currency`, `merchant_id`.
- **Observation points**: `SELECT id, transactor_id, transactor_event, amount FROM journal WHERE transactor_id=%s AND
  transactor_event=%s` (Postgres, `ledger/internal/database/rx_migrations/20201001011143_create_journal.go` +
  `20210925113658_rename_transactor_type_to_transactor_event_in_journal_go.go`) — expect exactly one row **despite
  no DB-level unique constraint** (the mutex in `ledger/internal/journal/server.go:47-63` is the only guard);
  `SELECT SUM(amount) FROM ledger_entries WHERE journal_id=%s` for balance-sum sanity (see V5).
- **Assertions**: exactly one `journal` row for the `(transactor_id, transactor_event)` pair; the losing concurrent
  call either receives the same journal back (idempotent) or a `twirp.InvalidArgument` from the mutex
  (`store.ErrorResourceAlreadyAcquired` path, `server.go:52-56`) — both are acceptable, a **second created journal
  row** is the only failure. Because there is no DB unique index, this verifier explicitly races two goroutines
  (not just two sequential calls) to exercise the actual protection mechanism rather than only the request-hash path.
- **Max wait**: 10s.
- **Expected result per status**: `CROSS_SERVICE_CONFIRMED` (ledger + Postgres only). If the race exposes a second
  row, the verifier reports this as a confirmed finding against the catalog's own "invariant weak — test it" flag,
  not as a tooling bug.
- **Substitutes required active**: none (direct ledger + Postgres).

## V5 — Ledger accounting: `payout_initiated` sums to zero and the merchant is debited

- **Covers**: C15, C19, Invariant 1 (first half).
- **Preconditions**: M1 (Shared) with a seeded ledger `accounts` balance; dcs/splitz/shield stubs allow; cfa has the
  fund account.
- **Stimulus**: `POST {kong_lite}/v1/payouts` for M1, amount 10000 paise, `mode: IMPS`.
- **Observation points**:
  - Ledger: `SELECT id FROM journal WHERE transactor_id=<payout_id> AND transactor_event='payout_initiated'` → one row;
    `SELECT account_id, type, amount FROM ledger_entries WHERE journal_id=%s` (columns per
    `20201001011149_create_ledger_entries.go`).
  - Payouts: `SELECT status, amount, fees, tax FROM payouts WHERE id=%s`.
- **Assertions**: `SUM(CASE WHEN type='debit' THEN amount ELSE -amount END)` over the journal's `ledger_entries` = 0
  (double-entry sum-to-zero, catalog Invariant 1's "MerchantBalance debits/credits sum to zero"); the MerchantBalance
  account's entry amount equals `payout.amount + payout.fees + payout.tax` (C19: "payout_initiated debits
  MerchantBalance(amount+commission)"); the accounts.balance for M1 decreased by exactly that amount
  (`SELECT balance FROM accounts WHERE merchant_id=%s` before/after).
- **Max wait**: 10s (synchronous Twirp call inside payout creation).
- **Expected result per status**: `END_TO_END_CONFIRMED` — payouts + ledger both real, independently observed DB
  state in both.
- **Substitutes required active**: dcs-stub, splitz-stub, shield-stub, cfa-server, monolith-stub (fund account /
  merchant lookups), stork-capture; payouts + ledger F3.

## V6 — Ledger accounting: `payout_processed` mirror journal on FTS success

- **Covers**: C19, Invariant 1 (second half — "every `processed` eventually one `payout_processed`").
- **Preconditions**: builds on V5's payout (or a fresh one); requires a way to drive the transfer to FTS `PROCESSED`
  — either mozart-mock scripted success (Env 4 substitute, **not present under `ENV2_COMPOSE/substitutes/` at spec
  time**) or, as a fallback the verifier uses by default, a direct simulated webhook (see V15's stimulus pattern)
  since Env 2's own golden flow G1 requires *some* success path to exist.
- **Stimulus**: (a) create payout as in V5; (b) if mozart-mock is reachable, wait for the natural FTS→bank→webhook
  path; else `POST {payouts-api}/v1/payouts/transfer_status_webhook` directly (internal Basic-Auth) with
  `{"fund_transfer_id": <fts transfer id>, "source_id": <payout_id>, "source_type":"payout", "status":"processed",
  "utr":"UTR_TEST_1234"}` (fields per `payouts/internal/app/dtos/transfer_status_webhook_request.go`) — this second
  path is flagged as a **synthetic** stimulus, not a real bank round trip.
- **Observation points**: `SELECT id FROM journal WHERE transactor_id=<payout_id> AND transactor_event='payout_processed'`;
  `SELECT status, utr FROM payouts WHERE id=%s`.
- **Assertions**: payout status is `processed` with a non-empty `utr`; exactly one `payout_processed` journal exists;
  `ledger_entries` for that journal sum to zero.
- **Max wait**: 30s if via mozart-mock (bank round trip + FTS polling), 10s if via the synthetic webhook fallback.
- **Expected result per status**: `END_TO_END_CONFIRMED` only when reached via the real mozart-mock path (no F1/
  unknown component). `CODE_CANDIDATE` when the synthetic-webhook fallback is used — it bypasses FTS's own state
  machine and thus cannot confirm the FTS-side control, only the payouts↔ledger mirror-journal invariant.
- **Substitutes required active**: mozart-mock (for `END_TO_END_CONFIRMED`) or none beyond V5's (for the capped
  `CODE_CANDIDATE` path — direct webhook, no FTS state machine exercised).
- **Fidelity note**: mozart-mock has no substitute directory yet; the verifier `skip`s the mozart-mock branch with
  reason `"mozart-mock unreachable"` and runs the synthetic-webhook branch, explicitly downgrading its own
  self-reported ceiling in the pytest output.

## V7 — Ledger accounting: `payout_reversed` mirror journal, amount includes fees

- **Covers**: C19, C24, Invariant 1.
- **Preconditions**: as V6, but the synthetic webhook (or mozart-mock scripted failure) reports `status: "reversed"`
  or `status: "failed"` for a Shared-account payout (which FTS→PS webhook remaps `failed`→`reversed` for shared
  accounts, see V18).
- **Stimulus**: as V6 but `{"status": "failed", "failure_reason": "TEST_BANK_DECLINE", "bank_status_code":"91"}`
  against a Shared-account (M1) payout in `initiated` state.
- **Observation points**: `SELECT status, failure_reason FROM payouts WHERE id=%s` (expect `reversed`, per
  `FtsToPayoutStatusMap["shared"]["default"]["failed"] = "reversed"`); `SELECT amount FROM reversals WHERE payout_id=%s`
  (expect `payout.amount + payout.fees`, per `internal/app/reversals/core.go:221-279` — reward_fee payouts are
  amount-only, excluded here); `SELECT id FROM journal WHERE transactor_id=<payout_id> AND transactor_event='payout_reversed'`.
- **Assertions**: payout status `reversed`; exactly one `reversals` row with `amount = payout.amount + payout.fees`;
  exactly one `payout_reversed` journal; that journal's `ledger_entries` sum to zero; `reversals.transaction_id`
  equals the journal id (`reversalViaLedgerService.go:25-88` sets `reversal.SetTransactionID(res.GetJournalID())`).
- **Max wait**: 15s (DB transaction commits reversal + payout row before the synchronous ledger call per V17).
- **Expected result per status**: `CODE_CANDIDATE` via the synthetic-webhook stimulus (same caveat as V6);
  `END_TO_END_CONFIRMED` if driven by a real mozart-mock scripted-failure scenario once that substitute exists.
- **Substitutes required active**: none beyond payouts + ledger for the synthetic path; mozart-mock for the
  end-to-end path.

## V8 — Ledger accounting: `payout_failed` (Direct account, no reversal entity)

- **Covers**: C19, Invariant 1 — Direct-account failure branch (`FtsToPayoutStatusMap["direct"]["RBL"]["failed"] =
  "failed"`, not `reversed`).
- **Preconditions**: M2 (Direct/RBL, current account, no Ledger integration per
  `reversalViaLedgerService.go:1055-1057` — "Skip for Direct/Current Account").
- **Stimulus**: create M2 payout, then synthetic `transfer_status_webhook` with `status: "failed"` for M2's
  `initiated` payout.
- **Observation points**: `SELECT status FROM payouts WHERE id=%s` (expect `failed`, terminal, no further
  transition); `SELECT COUNT(*) FROM reversals WHERE payout_id=%s` (expect **0** — Direct failures create no
  reversal entity); ledger `journal` table for `transactor_id=<payout_id>` (expect **0** rows — Direct accounts never
  touch Ledger).
- **Assertions**: `payouts.status='failed'`; zero `reversals` rows; zero ledger `journal` rows for this payout —
  confirms the account-type branch, not just the shared-account happy path V7 covers.
- **Max wait**: 10s.
- **Expected result per status**: `CROSS_SERVICE_CONFIRMED` (payouts + ledger, confirming ledger's **absence** of
  activity) via the synthetic-webhook stimulus; `END_TO_END_CONFIRMED` once mozart-mock exists.
- **Substitutes required active**: none beyond payouts (+ ledger to confirm the negative).

## V9 — Balance authorization: insufficient balance, `queue_if_low_balance=true` → queued

- **Covers**: C15 ("insufficient ⇒ queued if queue_if_low_balance else failed"), Flow E, golden flow G3.
- **Preconditions**: M1 ledger `accounts.balance` set below the requested amount (verifier reads current balance
  first and requests `balance + 1`).
- **Stimulus**: `POST {kong_lite}/v1/payouts` for M1, `"amount": <balance+1>`, `"queue_if_low_balance": true`.
- **Observation points**: ledger — no successful `payout_initiated` journal (`ValidateCreateRequest`/`Create`
  returns `ErrInsufficientBalanceFailure`, mapped to Twirp `InvalidArgument` per `journal/server.go:104-110`);
  payouts — `SELECT status, queued_reason FROM payouts WHERE id=%s`.
- **Assertions**: HTTP create response has `status: "queued"` (public status; internal `queued`); `queued_reason =
  "low_balance"` (`payouts/internal/app/payouts/model.go:189-192` valid values); `accounts.balance` for M1
  unchanged (no debit occurred — the Ledger call failed before any `ledger_entries` were written).
- **Max wait**: 10s.
- **Expected result per status**: `END_TO_END_CONFIRMED` — payouts + ledger, independently observed in both, no F1
  component on the path.
- **Substitutes required active**: dcs-stub, splitz-stub, shield-stub, cfa-server, monolith-stub, stork-capture.

## V10 — Balance authorization: insufficient balance, `queue_if_low_balance=false` → failed

- **Covers**: C15 (else-branch).
- **Preconditions**: same as V9.
- **Stimulus**: identical to V9 but `"queue_if_low_balance": false`.
- **Observation points**: same as V9.
- **Assertions**: `payouts.status = "failed"` (not `queued`); `queued_reason` empty; `accounts.balance` unchanged.
- **Max wait**: 10s.
- **Expected result per status**: `END_TO_END_CONFIRMED`.
- **Substitutes required active**: same as V9.

## V11 — MerchantBalance debit is synchronous with the API response

- **Covers**: C15 ("MerchantBalance always synchronous").
- **Preconditions**: M1 with known starting balance.
- **Stimulus**: `POST {kong_lite}/v1/payouts` for M1, amount within balance. Immediately (no wait) after the HTTP
  201/200 response is received, query ledger.
- **Observation points**: `SELECT balance FROM accounts WHERE merchant_id=%s` queried within 500ms of the HTTP
  response returning.
- **Assertions**: the debited balance is already visible — no polling/wait loop is permitted for this specific
  assertion (a `wait_until` here would mask an async regression); balance decreased by exactly `amount+fees+tax`
  before the create call's HTTP response was even received on the client side (ledger call is synchronous Twirp
  inside the create request per Flow A/B sequence diagrams).
- **Max wait**: 0s deliberately (single immediate read; a retry loop would defeat the point of this verifier).
- **Expected result per status**: `END_TO_END_CONFIRMED`.
- **Substitutes required active**: same as V5.

## V12 — Direct-rail reservation gate: reservation total equals live reservations

- **Covers**: C16, Invariant 5.
- **Preconditions**: M2 (Direct, `in_flight_reservation_enabled=on` per `dcs-stub` seed table).
- **Stimulus**: create 3 M2 payouts in quick succession (amounts well under the gateway balance so all 3 reserve
  successfully), leaving them in a non-terminal dispatched state (do not resolve via webhook yet).
- **Observation points**: `GET {payouts-api}/v1/inflight_reservations?merchant_id=M2_MERCHANT_ID&balance_id=M2_BALANCE_ID`
  (internal Basic-Auth; response shape `{store_trusted, merchant_id, balance_id, reserved_total, items:[{payout_id,
  amount, dispatched_at, state}]}` per `payouts/internal/app/dtos/inflightReservationInspectResponse.go`).
- **Assertions**: `reserved_total == sum(item.amount for item in items if item.state == "live")`; each of the 3
  created payout ids appears exactly once in `items` with `state == "live"`; `store_trusted == true` (a `false`
  value would itself be a finding — the reconciler hasn't run since a Redis flush).
- **Max wait**: 10s.
- **Expected result per status**: `END_TO_END_CONFIRMED` — the inspector endpoint is real payouts code observing
  real Redis state, no substitute on the path for the assertion itself (xbalances F3 supplies the gateway balance
  gating decision that got the payouts reserved in the first place).
- **Substitutes required active**: dcs-stub (feature flag), asv-stub (banking account lookup), xbalances-server (F3).

## V13 — Direct-rail reservation: released on terminal event

- **Covers**: C16, Invariant 5 ("release on terminal").
- **Preconditions**: one of V12's 3 reserved M2 payouts.
- **Stimulus**: synthetic `transfer_status_webhook` (or mozart-mock) driving that payout to `processed` (or
  `failed`).
- **Observation points**: `GET /v1/inflight_reservations?...` before and after.
- **Assertions**: `reserved_total` decreases by exactly that payout's amount; the payout's id is absent from
  `items`, or present with `state == "awaiting_balance_refresh"` if the design holds it pending a balance-refresh
  event (per `reservation/store.go` `StateAwaitingBalanceRefresh` — the verifier accepts either as correct per the
  documented two-phase release, but asserts it is **not** `"live"`).
- **Max wait**: 15s.
- **Expected result per status**: `CODE_CANDIDATE` via synthetic webhook (bypasses the real terminal-event trigger
  path from FTS); `END_TO_END_CONFIRMED` via mozart-mock once that substitute exists.
- **Substitutes required active**: same as V12; mozart-mock for the higher ceiling.

## V14 — State machine legality: no illegal transition ever recorded in `payout_logs`

- **Covers**: general state-machine soundness underlying all of C22/C23/C26/Invariant list; cross-checks the
  transition table in `payouts/internal/app/payouts/state_machine.go` / `appConstants/states.go`.
- **Preconditions**: a batch of payouts already exercised by V1, V5–V10 (any prior test run is enough fixture data;
  this verifier is a **read-only audit**, it creates no new payouts of its own).
- **Stimulus**: none (pure observation) — optionally, if `SEED_ADVERSARIAL_TRANSITIONS=1` is set, the verifier first
  attempts a handful of deliberately illegal calls (e.g. `POST /v1/payouts/cancel_payout/:id` on a payout in
  `initiated`) to generate negative fixtures, asserting each is rejected (see V-cancel-illegal below, folded into
  this verifier's negative-path half).
- **Observation points**: `SELECT payout_id, event, `from`, `to`, mode, triggered_by, created_at FROM payout_logs
  ORDER BY payout_id, created_at` (columns per `payouts/internal/database/migrations/20210524161952_create_payout_logs_table.go`).
- **Assertions**: for every consecutive `(from, to)` pair per `payout_id`, `(from, to)` is a member of the allowed
  edge set reconstructed from `state_machine.go`'s `sm.Event(...).To(...).From(...)` declarations (the verifier
  hardcodes this table from source, e.g. `reversed` reachable `From(create_request_submitted, ledger_response_awaited,
  pending, on_hold, queued, scheduled, processed, failed, initiated, created, batch_submitted)`, `cancelled` only
  `From(queued, scheduled, on_hold)`); no payout has more than one row transitioning **into** a terminal state
  (`processed`, `failed`, `reversed`, `cancelled`, `rejected`) except the documented idempotent self-loops
  (`processed→processed`, etc., per `AllowedStateTransitionForTransferWebhook`).
- **Max wait**: 10s (single query + in-memory check).
- **Expected result per status**: `CROSS_SERVICE_CONFIRMED` — this is a PS-internal invariant, but is scored against
  every payout produced by the *other* verifiers' cross-service flows, so a violation here implicates whichever
  upstream flow produced the bad transition.
- **Substitutes required active**: none (read-only against payouts MySQL); more valuable the more other verifiers
  have run first in the same session.

## V15 — FTS webhook state guard: webhook on a non-initiated payout ⇒ 200, no change

- **Covers**: C23, `AllowedStateTransitionForTransferWebhook` (`payouts/internal/app/common/appConstants/states.go`),
  `IsValidStateTransitionForTransferWebhook` (`fts_transfer_status_webhook.go:645`).
- **Preconditions**: a payout already in a terminal state (`cancelled` from V-cancel, or `processed`/`reversed` from
  V6/V7).
- **Stimulus**: `POST {payouts-api}/v1/payouts/transfer_status_webhook` (internal Basic-Auth) with a status the
  transition table forbids from that state, e.g. `{"source_id": <cancelled payout id>, "source_type":"payout",
  "status":"processed", "fund_transfer_id": <arbitrary>}` against a `cancelled` payout (not in
  `AllowedStateTransitionForTransferWebhook`'s key set at all, so **every** target status is illegal from
  `cancelled`).
- **Observation points**: HTTP status/body; `SELECT status, updated_at FROM payouts WHERE id=%s` before/after;
  `SELECT COUNT(*) FROM payout_logs WHERE payout_id=%s` before/after.
- **Assertions**: HTTP `200` (per `fts_transfer_status_webhook.go:58-67` — "return 200 on error to stop [FTS]
  retries", mirrored from the WFS-callback convention in C14); response body message equals `"webhook update
  skipped due to invalid state transition"` (or the current literal — the verifier matches on message content, not
  just status code, to distinguish a true skip from a silent 200-on-unrelated-error); `payouts.status` and
  `updated_at` unchanged; `payout_logs` row count unchanged (no audit entry for a skipped transition).
- **Max wait**: 10s.
- **Expected result per status**: `END_TO_END_CONFIRMED` — single-service call, but the assertion is the
  documented cross-service contract (FTS retry-suppression behavior), independently observed via DB state, with no
  F1 component on the path.
- **Substitutes required active**: none.

## V16 — Stuck-initiated detection: FTS terminal + payout `initiated`, no automated repair (expected finding)

- **Covers**: C22, Invariant 2 ("no payout stays `initiated` while its transfer is terminal — currently violated in
  production and has no automated repair"). This verifier's job is to **reproduce the gap**, not to find a fix.
- **Preconditions**: a payout driven to FTS `initiated` and then the underlying FTS `transfers.status` flipped to a
  terminal value (`PROCESSED`/`FAILED`) **without** going through the PS webhook path — i.e. directly updating FTS's
  own DB (`UPDATE transfers SET status='PROCESSED', utr='UTR_STUCK_TEST' WHERE id=%s`), simulating the documented
  failure mode where the webhook was dropped (retries exhausted, Kafka path off, or origin-service check failed).
- **Stimulus**: (a) create an M1 payout normally, capture its `fts_transfer_id`; (b) directly mutate FTS `transfers`
  to a terminal status via SQL (bypassing PS entirely — this is the point: no webhook fires); (c) wait
  `STUCK_DETECTION_WAIT_SECONDS` (default 90s, configurable) with **no** call to any repair endpoint; (d) re-check.
- **Observation points**: `SELECT status FROM payouts WHERE id=%s` (payouts MySQL) vs `SELECT status, utr FROM
  transfers WHERE id=%s` (FTS MySQL) after the wait.
- **Assertions**: this verifier asserts the **divergence persists** — `payouts.status == "initiated"` while
  `transfers.status` is terminal — and reports this as `xfail`-shaped (pytest `xfail(strict=False)`, not a bug in
  the verifier suite): a passing result here (i.e., the divergence *auto-healing*) would itself be the interesting
  finding and is surfaced as a loud non-fatal warning, since the catalog records no automated repair mechanism as
  existing.
- **Max wait**: 90s (configurable) — long enough to rule out the documented cron cadence assumptions
  (`process_queued_low_balance_payouts` etc., none of which target `initiated`+FTS-terminal specifically) coming
  in and closing the gap by coincidence.
- **Expected result per status**: `CROSS_SERVICE_CONFIRMED` for the reproduction itself (payouts + FTS, both real,
  divergence directly observed in both DBs). Cannot reach `END_TO_END_CONFIRMED` by definition — there is no
  terminal, correct, independently-observed outcome to converge on; this verifier's *pass* condition is the absence
  of convergence.
- **Substitutes required active**: none beyond payouts + FTS.
- **Fidelity note**: directly mutating FTS's DB from the verifier is a deliberate substitute for "FTS webhook got
  dropped" (there is no F2/F3 way to force a real webhook drop without also disabling FTS's Splitz
  `CreateTransferMetaRollout`, which is a merchant-wide config the verifier does not want to mutate). This is called
  out inline in the pytest docstring as a simulated, not observed, failure injection.

## V17 — Reversal ordering: payout `reversed` row committed before the ledger journal call

- **Covers**: C24 (direct path: DB commit before Ledger call; async retry ×3 on Ledger failure).
- **Preconditions**: M1 payout in `initiated`; a way to make the ledger call fail on the first attempt (verifier
  temporarily blocks `ledger-api` reachability at the network layer — see Fidelity note — or, if unavailable, relies
  on the natural ordering assertion alone without fault injection).
- **Stimulus**: (a) create M1 payout, reach `initiated`; (b) [fault-injection variant, only if the verifier has
  permission to manipulate `rzp-arena` network policy, which it does **not** by default — see note] send the
  synthetic `reversed` webhook while ledger-api is briefly unreachable; (c) [baseline variant, always run] send the
  synthetic `reversed` webhook normally and assert ordering via timestamps.
- **Observation points**: `SELECT created_at, updated_at FROM payouts WHERE id=%s` and `SELECT created_at FROM
  reversals WHERE payout_id=%s` (payouts MySQL, second-resolution `created_at int`) vs `SELECT created_at FROM
  journal WHERE transactor_id=%s AND transactor_event='payout_reversed'` (ledger Postgres). Because both timestamp
  columns are second-resolution, the verifier additionally captures **wall-clock request timestamps** on its own
  side (before/after each HTTP call it issues) to get sub-second ordering where the DB columns can't.
  - Fault-injection branch only: after ledger-api is unblocked, poll `SELECT COUNT(*) FROM journal WHERE
    transactor_id=%s AND transactor_event='payout_reversed'` until it becomes 1 (async retry, `asyncFailureHandlingHelper.go`
    `LedgerReversedEventFailureType`, exponential backoff, max 5 attempts / 30 min per the reversals skill doc).
- **Assertions**: baseline — `reversals` row exists and `payouts.status='reversed'` is observable strictly before
  (or, at worst, in the same synchronous response as) the ledger journal appears, consistent with "DB commit BEFORE
  ledger" (Flow F: "repo.Transaction{status-details, CreateReversalEntity, EventReversed, update} — DB commit BEFORE
  ledger"). Fault-injection — `payouts.status='reversed'` and the `reversals` row exist immediately (within the
  synchronous webhook response) even while the ledger journal is absent; the journal appears later (async retry),
  confirming the reversal is not blocked on ledger success.
- **Max wait**: 10s baseline; up to 5 min for the fault-injection async-retry branch (backoff per the reversals
  skill doc: initial 30s, exponential, max 5 attempts).
- **Expected result per status**: `CROSS_SERVICE_CONFIRMED` for the baseline ordering assertion (payouts + ledger,
  real). `BLOCKED_BY_FIDELITY_GAP` for the fault-injection branch by default, because the verifier container has no
  documented mechanism (no `network/` policy exists yet in `ENV2_COMPOSE/` at spec time) to selectively blackhole
  ledger-api without also breaking other concurrently-running verifiers — the spec defines the assertions so the
  branch is ready the moment that mechanism exists (env var `ALLOW_NETWORK_FAULT_INJECTION=1` gates it).
- **Substitutes required active**: none for baseline; a network-fault capability (not yet built) for the full claim.

## V18 — Shared-account remap: FTS `failed` → payout `reversed`

- **Covers**: C19/C20 `FtsToPayoutStatusMap["shared"]["default"]["failed"] = "reversed"` vs
  `["direct"]["RBL"]["failed"] = "failed"` (the account-type-dependent branch already exercised separately in V7/V8
  — this verifier asserts the **contrast** in a single run for a stronger cross-service claim).
- **Preconditions**: one M1 (Shared) and one M2 (Direct/RBL) payout, both in `initiated`.
- **Stimulus**: identical synthetic webhook `{"status":"failed","failure_reason":"TEST","bank_status_code":"91"}` to
  both payouts' `transfer_status_webhook`.
- **Observation points**: `SELECT status FROM payouts WHERE id IN (%s, %s)`.
- **Assertions**: M1's payout status is `reversed` (with a `reversals` row created); M2's payout status is `failed`
  (with **no** `reversals` row) — same webhook payload shape, different outcome, driven purely by account type/
  channel, per the `FtsToPayoutStatusMap` table.
- **Max wait**: 10s.
- **Expected result per status**: `CROSS_SERVICE_CONFIRMED` via synthetic webhook (payouts + ledger); `END_TO_END_CONFIRMED`
  once mozart-mock exists to drive both merchants through a real bank-declined scenario.
- **Substitutes required active**: none beyond payouts + ledger.

## V19 — Direct failed-verification: XAS debit-statement match blocks the `failed` transition

- **Covers**: C25 (`VerifyPayoutFailedTransaction` refuses to mark failed if `TransactionID` set or XAS debit
  statement matched).
- **Preconditions**: M2 (Direct) payout in `initiated`; XAS is **not** listed among Env 2's F2/F3 substitutes (it is
  an Env 4 addition per the BOM — "Additional F3: x-account-statements"). At Env 2 spec time there is no
  `banking_account_statement`/BAS ingestion path wired, so this verifier can only exercise the `TransactionID`-set
  half of the guard.
- **Stimulus**: (a) set `payouts.transaction_id` to a non-empty synthetic value directly in payouts MySQL (standing
  in for a prior XAS/BAS-recon linkage — the part of the guard Env 2 can reach); (b) send the synthetic `failed`
  webhook for that payout.
- **Observation points**: `SELECT status, transaction_id FROM payouts WHERE id=%s` before/after.
- **Assertions**: the payout does **not** transition to `failed` (the guard in
  `VerifyPayoutFailedTransaction` should refuse); response/log indicates the refusal reason.
- **Max wait**: 10s.
- **Expected result per status**: `BLOCKED_BY_FIDELITY_GAP` — XAS is not in Env 2's closure at all (its BOM entry is
  Env 4), so the `TransactionID`-set precondition is manufactured directly in the DB rather than produced by a real
  XAS statement match; the verifier documents this explicitly and caps its own ceiling rather than claiming
  `CROSS_SERVICE_CONFIRMED`.
- **Substitutes required active**: none (payouts only); full claim needs Env 4's x-account-statements F3.

## V20 — Tenant isolation: another merchant cannot fetch or cancel a payout

- **Covers**: C6, Invariant 4 ("never visible to a different merchant through public routes").
- **Preconditions**: an M1 payout (any state); an M2 passport JWT (`consumer.id = M2_MERCHANT_ID`).
- **Stimulus**: both calls use `PS_SERVICE` Basic-Auth (the service credential is the same regardless of which
  merchant the passport claims — tenant scoping happens inside payouts-api from the passport's `consumer.id`, not
  from the service credential) with the **M2** passport JWT: (a) `GET {kong_lite}/v1/payouts/<M1 payout id>`; (b)
  `POST {kong_lite}/v1/payouts/cancel_payout/<M1 payout id>` (payout must be in a cancellable state —
  `queued`/`scheduled`/`on_hold` — for this to be a meaningful negative test rather than trivially rejected on
  state grounds first).
- **Observation points**: HTTP status/body for both calls; `SELECT status FROM payouts WHERE id=%s` unchanged after
  (b).
- **Assertions**: (a) returns `404`/`400` (not the payout, not `200`) — the public `GetPayout` query is
  merchant-scoped (`payout_routes.go` `GET /:id` → `controllers.PayoutService.GetPayout`, which the flow catalog
  confirms is merchant-scoped for public routes); (b) also rejected, and `payouts.status` for the M1 payout is
  unchanged.
- **Max wait**: 10s.
- **Expected result per status**: `END_TO_END_CONFIRMED` for the public-route half (independently observed:
  correct HTTP rejection + unchanged DB state). The catalog separately flags that **internal** approve/reject/
  reversal lookups are unscoped by ID (TODO in source) — this verifier does not attempt that path since it has no
  merchant-boundary concept at all on internal routes (there is nothing to assert isolation *of*); that gap is
  recorded as a known, not-tested, finding in the spec rather than fabricating an assertion.
- **Substitutes required active**: none.

## V21 — Webhook completeness: every terminal state appears exactly once in stork-capture

- **Covers**: C36, Invariant 6 ("merchant webhooks for a payout contain every terminal state exactly once, ordering
  not guaranteed").
- **Preconditions**: a payout driven through create → terminal (any of V6/V7/V8/V9/V10's payouts, or a fresh one),
  stork-capture reachable.
- **Stimulus**: none beyond whatever produced the terminal payout; poll `GET http://stork-capture:8080/v1/captured`.
- **Observation points**: `captured.sms`/`captured.email` alone are insufficient — the actual webhook channel is
  Twirp `WebhookAPI/ProcessEvent` (`payouts/pkg/stork`), which `stork-capture`'s `CONTRACT.md` documents only for
  SMS/email; **the webhook-event capture endpoint itself is not documented in `stork-capture/CONTRACT.md`** — this
  is a scaffold gap, not a payouts-code gap. The verifier calls `GET /v1/captured` and searches for any entry whose
  payload references the test payout id, tolerant of the shape being SMS/email-only today.
- **Assertions**: for the payout's actual terminal state (e.g. `reversed`), at least one captured event exists whose
  content matches `payout.reversed` (or the corresponding template) and mentions the payout id; no *other* terminal
  event (`payout.processed`, `payout.failed`, `payout.cancelled`, `payout.rejected`) is also present for the same
  payout id (mutual exclusivity of terminal webhooks — a payout has exactly one terminal state and should fire
  exactly one terminal webhook, per the state machine's terminal-state set).
- **Max wait**: 15s (async Stork push via SQS `webhook_event` job, 3 retries/120s per the flow catalog's retry
  table — the verifier polls, it does not wait the full retry budget on the happy path).
- **Expected result per status**: `BLOCKED_BY_FIDELITY_GAP` at spec time — `stork-capture`'s own `CONTRACT.md` does
  not document a webhook-event capture endpoint distinct from SMS/email, so the verifier cannot make a confirmed
  positive assertion about `payout.*` webhook payloads specifically; it is written to `skip` with reason
  `"stork-capture has no documented /v1/captured entry type for Twirp WebhookAPI/ProcessEvent — extend the stub
  before this verifier can assert"` unless a captured entry matching the payout id is found, in which case it
  degrades gracefully to asserting on whatever it finds.
- **Substitutes required active**: stork-capture (as currently scoped, insufficient for the full claim).

## V22 — Shield fail-open: evaluation latency > 200ms ⇒ payout proceeds

- **Covers**: C11 (binary block/allow, fail-open on error/200ms timeout).
- **Preconditions**: shield-stub supports a latency-injection mode (not documented in its current `CONTRACT.md`,
  which only documents `SHIELD_DENY_LIST`) — the verifier checks for a `X-Test-Latency-Ms` request header or
  `SHIELD_LATENCY_INJECT_MS` env-driven behavior on shield-stub and skips if neither is honored.
- **Stimulus**: create an M1 payout while shield-stub is configured/instructed to sleep > 200ms before responding
  (`payouts/config/default.toml` `[shield] timeout = 200`, `[shield.httpclient.httpclient] timeout = 200`).
- **Observation points**: HTTP create response; `SELECT status FROM payouts WHERE id=%s`.
- **Assertions**: the payout is **not** blocked by Shield timeout alone — it proceeds to whatever state its other
  inputs dictate (`created`/`initiated`/`queued`, not a Shield-rejection error) — confirming fail-open rather than
  fail-closed on timeout.
- **Max wait**: 10s (the 200ms Shield timeout plus normal create-path latency).
- **Expected result per status**: `CODE_CANDIDATE` at spec time — shield-stub's documented contract has no latency-
  injection lever, so this verifier can only assert the *absence* of a Shield-latency failure mode under whatever
  the stub's default (fast, always-allow) behavior is, which does not exercise the timeout path at all. It is
  written to `skip` with reason `"shield-stub CONTRACT.md documents no latency-injection control"` unless the
  stub responds to one of the probed injection mechanisms, in which case it upgrades to asserting the real claim
  and reports `CROSS_SERVICE_CONFIRMED`.
- **Substitutes required active**: shield-stub (with a latency-injection extension not yet built).

## V23 — Fee fail-closed: pricing dependency 500 ⇒ payout rejected

- **Covers**: C27 (fail-closed on pricing/fee lookup failure).
- **Preconditions**: **the real call payouts makes is `POST {monolith_base_url}/payouts_service/fetch_pricing_info`**
  (confirmed from `payouts/pkg/api/fetch_pricing.go:15-16`, `const FetchPricing =
  "/payouts_service/fetch_pricing_info"`) — i.e. it goes through **monolith-stub**, not the standalone
  `pricing-stub` container. `monolith-stub/CONTRACT.md` does **not** currently list `/payouts_service/fetch_pricing_info`
  among its endpoints. `pricing-stub`'s own `CONTRACT.md` flags this exact ambiguity ("TODO: confirm whether
  payouts actually makes an external pricing call in this environment's code paths, or whether this stub is dead
  weight"). This verifier resolves the ambiguity from source (it does make the call, to monolith-stub, not
  pricing-stub) and is written to target whichever of the two containers actually answers the path, probing
  monolith-stub first.
- **Stimulus**: configure the answering stub to return `500` for `/payouts_service/fetch_pricing_info` for the test
  merchant (env-driven fault injection, e.g. `MONOLITH_STUB_FAULT_PATHS=/payouts_service/fetch_pricing_info:500`);
  create a payout for that merchant.
- **Observation points**: HTTP create response status/body; `SELECT status FROM payouts WHERE id=%s` (expect no row,
  or a `failed`/rejected row depending on how far creation proceeded before the pricing call).
- **Assertions**: the payout is rejected (non-2xx create response, or immediately `failed`) rather than proceeding
  with a zero/default fee — confirming fail-closed.
- **Max wait**: 10s.
- **Expected result per status**: `BLOCKED_BY_FIDELITY_GAP` at spec time — neither `monolith-stub` nor
  `pricing-stub`'s current `CONTRACT.md` documents `/payouts_service/fetch_pricing_info` or a fault-injection lever
  for it, so the verifier `skip`s with reason `"monolith-stub does not yet implement /payouts_service/
  fetch_pricing_info with fault injection — see pricing-stub/CONTRACT.md TODO"`. Once the endpoint + fault
  injection exist on monolith-stub, this upgrades to `CROSS_SERVICE_CONFIRMED` (payouts + monolith-stub, F2).
- **Substitutes required active**: monolith-stub extended with `/payouts_service/fetch_pricing_info` (not yet
  built).

## V24 — Scheduled/queued cron dequeue

- **Covers**: C17, C32, Flow E.
- **Preconditions**: FastCron Basic-Auth credential (`cred.FastCron`, `payouts/internal/routing/router/cron_routes.go`);
  a payout already `queued` with `queued_reason='low_balance'` (from V9) or `scheduled` with `scheduled_at` in the
  near past.
- **Stimulus**:
  - Queued: top up M1's ledger `accounts.balance` directly (simulating a `BalanceRefreshEvent`/manual credit) above
    the queued payout's amount, then `POST {payouts-api}/v1/cron/process_queued_low_balance_payouts` (FastCron
    Basic-Auth) — mirrors `payouts/internal/routing/router/cron_routes.go`
    `ProcessInitiateForQueuedPayouts`, which per Flow E only re-scans "balances changed in last 6 h"
    (`FindQueuedPayoutsForBalanceId(limit 5000, no ORDER BY)`), so the balance top-up must be recent.
  - Scheduled: create a payout with `scheduled_at` = now+15s, wait past that timestamp, then
    `POST {payouts-api}/v1/cron/process_scheduled_payouts`.
- **Observation points**: `SELECT status, queued_reason, scheduled_at FROM payouts WHERE id=%s` before/after each
  cron call.
- **Assertions**: queued case — status transitions from `queued` to `created`/`initiated` (not still `queued`)
  after the cron call, and `accounts.balance` reflects the debit; scheduled case — status transitions from
  `scheduled` to `created`/`initiated` after `scheduled_at` has passed and the cron endpoint is called (still-pending
  **at** slot time with no cron call yet should remain `scheduled` — the verifier also asserts this negative case by
  checking status immediately after `scheduled_at` passes but **before** calling the cron endpoint).
- **Max wait**: 20s per cron call (per-payout mutex `payout_<id>` 30s per Flow E; the SQS `queued_payout` job dispatch
  adds latency).
- **Expected result per status**: `END_TO_END_CONFIRMED` — payouts + ledger (balance top-up observed), driven via
  the documented cron HTTP endpoints, no F1 component. Note per the BOM, real cron **cadence** (how often an
  external scheduler calls these endpoints in a live arena) is unverified/assumed — this verifier calls the
  endpoints directly rather than waiting on a scheduler, so it confirms the *dequeue logic* but not the *cadence*;
  cadence itself stays at `CODE_CANDIDATE` per C39.
- **Substitutes required active**: dcs-stub, splitz-stub, shield-stub, cfa-server, monolith-stub, stork-capture.

---

## Coverage cross-reference

| Requirement from the task brief | Verifier(s) |
|---|---|
| Idempotency: same key same body → same id | V1 |
| Idempotency: same key different body → BAD_REQUEST | V2 |
| FTS (source_id, source_type) dedupe | V3 |
| Ledger (transactor_id, event) dedupe | V4 |
| Ledger accounting sum-to-zero: initiated/processed/reversed/failed | V5, V6, V7, V8 |
| Balance authorization: insufficient → queued/failed; MerchantBalance sync | V9, V10, V11 |
| Direct-rail reservation gate: total = live reservations; release on terminal | V12, V13 |
| State machine legality: no illegal transition in payout_logs | V14 |
| FTS webhook state guard | V15 |
| Stuck-initiated detection (expected finding, no repair) | V16 |
| Reversal ordering (DB before ledger; async retry) | V17 |
| Shared FTS-failed → payout-reversed remap | V18 |
| Direct failed verification (XAS blocks failed) | V19 |
| Tenant isolation | V20 |
| Webhook completeness (each terminal state once) | V21 |
| Shield fail-open | V22 |
| Fee fail-closed | V23 |
| Scheduled/queued cron dequeue | V24 |

## Known scaffold gaps blocking full-ceiling runs (as of this spec's writing)

1. **No mozart-mock substitute exists yet** under `ENV2_COMPOSE/substitutes/` — every verifier that needs a real
   bank round trip (V6, V7, V8 upper ceiling, V13, V18 upper ceiling) falls back to a synthetic direct-webhook
   stimulus and self-caps below `END_TO_END_CONFIRMED`.
2. **`ENV2_COMPOSE/seeds/`, `secrets/`, `config/`, `network/` are empty** — merchant fixtures and service credentials
   are read from env vars with no baked-in default; every verifier skips cleanly (not errors) when unset.
3. **kong-lite's Twirp routing does not match ledger's real path** (`/twirp/ledger` prefix vs real
   `/twirp/rzp.ledger.journal.v1.JournalAPI/...`) — verifiers bypass kong-lite for ledger/FTS/CFA/x-balances.
4. **`monolith-stub` does not implement `/payouts_service/fetch_pricing_info`** — blocks V23 at
   `BLOCKED_BY_FIDELITY_GAP`.
5. **`shield-stub` has no latency-injection control** — blocks V22's timeout-specific claim.
6. **`stork-capture` documents no webhook-event (Twirp `ProcessEvent`) capture distinct from SMS/email** — blocks
   V21's full claim.
7. **No network-fault-injection capability** — blocks V17's fault-injection branch.
8. **XAS/x-account-statements is out of Env 2's closure entirely** (Env 4 addition per the BOM) — V19 can only
   exercise half of C25's guard.
9. **`kong-lite` does not mint a passport or translate merchant Basic-Auth**, contrary to its own BOM entry
   (`INITIAL_PAYOUTS_ENVIRONMENT_BOM.md` §2: "mint passport private"). `payoutRoutes` in payouts-api requires a
   service Basic-Auth pair (`cred.API`/`cred.Workflow`) **plus** an RS256 passport JWT — a merchant's raw key pair
   alone is rejected. Every verifier that calls `POST/GET/PATCH {kong_lite}/v1/payouts*` therefore supplies both a
   `PS_SERVICE` credential and a passport JWT itself (`helpers/passport.py`), standing in for the missing Kong/
   monolith translation layer; a `PASSPORT_SIGNER_URL` (or pre-minted `PASSPORT_STATIC_JWT_<MERCHANT>` tokens) is a
   required fixture for every such verifier and none of them exist in the arena yet either.

## Arena execution notes (2026-09-04, after the golden run)

- Run: `cd ENV2_COMPOSE && bash scripts/golden-run.sh --with-egress-audit` (verifier container on `rzp-arena`, credentials and fixtures injected by compose; results in the run log and `EGRESS_AUDIT.md`).
- Timing: every wait window is multiplied by `ARENA_WAIT_SCALE` (default 6) — the arena's single-replica workers and cron-driven status checks are slower than the production-derived windows in this spec.
- Live bank path: a healthy `MOZART_MOCK_URL` (arena: `mozart-sim`) makes the processed/reversed verifiers wait for the real FTS → Mozart-sim → webhook → monolith relay → PS chain; the synthetic PS `transfer_status_webhook` path is only used when that chain is unavailable and now waits for the payout to be `initiated` with an FTS transfer id first (`helpers.payouts_flow.wait_for_initiated`).
- Balances: top-ups go through a real ledger journal (`helpers.payouts_flow.ledger_topup`, `positive_adjustment_processed`), never SQL — ledger-api caches balances. `get_account_balance` reads the MerchantBalance account (entities payable/merchant_va), not an arbitrary account of the merchant.
- Contracts corrected against code: `merchant_id` in the create body (from the passport), monolith fund-account ids (`ARENAFAX…`), minimum amount 100 paise, IMPS per-transaction cap (deliberately over-balance payouts use NEFT), scheduled slots {9,13,17,21} IST, fts `/v1/transfer` body and `fund_transfer_id` response, PS ids compared without the `pout_` prefix.
- Still gated on substitutes that do not exist: V17 (network fault injection), V22 (shield latency injection), V23 (`X-Test-Fault` on the pricing route), V21 (stork-capture ProcessEvent capture endpoint) — the verifiers skip with the exact reason.
- Confidence tier of the bank leg: the arena's bank gateway is `mozart-sim` (envelopes and `bank_status_code` values taken verbatim from `mozart/app/testdata/fts/rbl/v1/*`), not `mozart -mock` (unbuildable: private deps). Results on the FTS → bank → webhook leg are therefore "simulator-grounded": they prove FTS/PS behaviour for the documented response shapes, not Mozart's own behaviour. (Raised by the independent audit, raw-findings/32.)
