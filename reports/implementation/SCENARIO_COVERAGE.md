# Scenario coverage and remaining acceptance gaps

Updated 2026-09-05 after clean replay 3. `runs/replay3/full` records 26 passed, zero failed, zero skipped with a passing egress audit and command exit 0. Earlier candidates remain retained: candidate2 15/11/0, replay1 21/5/0, replay2 21/5/0. Corrections and source references are in `VERIFIER_REPAIR_NOTES.md`. A control existing in a substitute, an offline contract test, and a live end-to-end scenario are distinct evidence levels. No row below is promoted based only on implemented controls.

The supplemental `runs/bank-monolith/bank-effects.json` records all six cases passed: hold, delayed success, both ambiguous outcomes, bank duplicate and gateway timeout. Egress audit passed with command exit 0. Each includes a complete ending snapshot of Payouts, FTS attempt chain, Ledger journals/entries/accounts, bank events and actual merchant receipts. Nonterminal results are bounded observations. CLI and exact assertions are in `verifier/BANK_SCENARIOS.md`.

Subsequent route-profile runs are now recorded separately: Shared golden passes under direct HTTP (`profile-direct2`) and direct-create/monolith-status (`profile-mixed2`); a fresh Direct-account Kafka success passes (`profile-kafka2`). Shared Kafka fails at the consumer's missing job registration before its Ledger event can enqueue. All three passing runs have passing egress audits with command exit 0 and zero outside packets. Earlier failed candidates remain retained; the profile evidence below does not change their results.

## Nine requested bank outcomes

| Outcome | Controllable synthetic starting state / trigger | Existing verification | Remaining evidence gap after replay 3 |
|---|---|---|---|
| Immediate success | Merchant `success` before create; actual FTS/Mozart request | V06 passed candidate2: processed payout, real FTS terminal status, balanced processed journal. Separate baseline Explorer golden passed Payouts + FTS + two balanced journals + signed merchant delivery. | V06 passes clean replay 3. V06 alone does not assert signed delivery. |
| Immediate failure | Merchant `failure` before create; now terminal `INVALID_ACCOUNT_NUMBER` | V07/V08/V18 inspect Shared reversal versus Direct failure. V08 passed candidate2 with prior code; Shared tests failed because prior `INSUFFICIENT_FUND` was retryable. Eight offline bank tests pass after correction. | V07/V08/V18 pass clean replay 3, including corrected lowercase monolith status normalization. Final supplemental failed_shared/failed_direct cases both pass with exact signed final delivery and complete snapshots; the final route audit passes with exit 0. |
| Hold initiated | Merchant `hold`; attempt remains pending until explicit attempt control | V01/V02/V05/V11/V14 use held real payouts. V16 creates hold then releases real bank success with relay dropped. Offline test proves merchant changes do not release an already held attempt. | `bank-monolith` additionally verifies exact held debit, one balanced initiated journal, no reversal and no terminal merchant delivery during a five-second observation. No terminal claim is appropriate while deliberately held. |
| Delayed success | `delayed_success`, pending for two status polls then success | `bank-monolith`: actual FTS checks transition pending to processed, two balanced journals and exactly one signed processed webhook. | Verified through explicit status checks; no scheduler cadence claim. |
| Returned/reversed after processed | `returned` emits initial SUCCESS, then RETURNED during explicit FTS admin verification and safe update | `returned-monolith2` passes under audit: actual processed states first, raw bank RETURNED/BBANK, independent second bank verification, Payouts/FTS reversed, balanced reversal journal and signed reversed delivery. | Explicit manual reconciliation. Ordinary check rejects processed attempts with ILLEGAL_STATE; no automatic return detection is claimed. See `RETURNED_ROUTE_EVIDENCE.md`. |
| Ambiguous with UTR | `ambiguous_with_utr` emits uncertain debit/credit indicators and UTR | `bank-monolith`: actual FTS attempt retains UTR and pending classification, exact debit held, no reversal/terminal journal or webhook during bounded observation. | Generic synthetic unmapped code verified; full gateway-specific classification matrix remains unverified. |
| Ambiguous without UTR | `ambiguous_without_utr` emits uncertain indicators without UTR | `bank-monolith`: no attempt UTR, pending FTS/Payouts, exact debit held, no terminal journal or webhook during bounded observation. | No eventual-settlement claim. |
| Duplicate transaction | `duplicate` emits `DUPLICATE_TXN` | `bank-monolith`: real attempt pending with DUPLICATE_TXN, held debit and no terminal journal/webhook during bounded observation. | Distinct from V03 source-identity create dedupe; no eventual-settlement claim. |
| Timeout | `timeout` delays bank response for 30 seconds then HTTP 504 without a valid Mozart envelope | `bank-monolith`: actual FTS MOZART_INDETERMINATE pending classification, held debit and no terminal journal/webhook during bounded observation. | Gateway timeout response; the FTS 180-second HTTP-client deadline did not fire. |

`insufficient_funds` is an additional explicit retryable bank scenario. Its code remains `INSUFFICIENT_FUND`; deterministic terminal decline uses `INVALID_ACCOUNT_NUMBER` because pinned FTS marks it failed, merchant-caused, and nonretryable (`fts/internal/providers/mozart/error_code.go:290,395`). Neither changes FTS classification code.

## Route and balance families

| Family | Implemented selection | Evidence / limit |
|---|---|---|
| Payouts → monolith → FTS | `monolith` profile | Actual baseline golden and candidate2 V06. |
| Direct Payouts → FTS | `direct` or `direct-create-monolith-status` | Shared goldens pass in [profile-direct2](runs/profile-direct2/golden/live-result.json) and [profile-mixed2](runs/profile-mixed2/golden/live-result.json): actual FTS/Payouts processed, balanced initiated and processed journals, and exactly one signed terminal receipt. Their [direct](runs/profile-direct2/golden/route-evidence.json) and [mixed](runs/profile-mixed2/golden/route-evidence.json) captures prove the selected hops. A Direct/current-account payout alone does not prove direct transport. |
| FTS → monolith → Payouts status | `monolith` and `direct-create-monolith-status` | Baseline golden and [mixed2 golden](runs/profile-mixed2/golden/live-result.json) pass. Mixed2 records the direct-create response plus actual monolith status delivery; the relay sends supported details before terminal status with selected source-account metadata. The earlier mixed failure from forwarding unsupported narration remains retained. |
| Direct FTS → Payouts status | `direct` | [Direct2 transport evidence](runs/profile-direct2/golden/route-evidence.json) records Payouts origin metadata and the actual direct HTTP callback, with passing Shared accounting and signed terminal delivery. V13/V17/V19/V21 remain separate adapter-boundary assertions. The earlier beneficiary-type failure is retained. |
| FTS Kafka → Payouts consumer, Shared account | `kafka`, M1 | [First Shared run](runs/profile-kafka/golden/live-result.json) fails: raw Kafka consumed, FTS PROCESSED, Payouts initiated, no processed Ledger journal. The immediate blocker is the Kafka process omitting job registration before Ledger enqueue. The internal DTO metadata mismatch is source-confirmed but its downstream Ledger consequence was not reached. FAILED/REVERSED remain ignored by the consumer. See [Kafka evidence](KAFKA_ROUTE_EVIDENCE.md). |
| FTS Kafka → Payouts consumer, Direct account | `kafka`, M2, fresh environment and Direct first | [Kafka2 Direct result](runs/profile-kafka2/direct-account/route-results.json) passes: FTS/Payouts processed, exactly one signed terminal receipt, no payout Ledger journals. [Transport evidence](runs/profile-kafka2/direct-account/route-evidence.json) confirms raw Kafka consumption and no HTTP/monolith status route. Dual-write job-registration errors remain visible; this does not establish dual-write success. The earlier Direct-after-Shared timeout remains inconclusive. |
| Shared / Ledger balance | M1 fixture | V05/V06/V07/V11/V17 all pass clean replay 3. |
| Direct / current-account balance | M2 fixture | V08/V12/V13 pass clean replay 3 with explicit XBalances balance-fetch gate, peer-reachable HTTP and lowercase channel enum; BASD fallback remains unseeded. |
| Queued low balance | M1 amount exceeds real Ledger balance, top-up then mirror sync then cron | V09/V24 pass clean replay 3. `routes-monolith2` queued case additionally reaches actual FTS/Payouts processed, balanced journals and signed merchant delivery after real Ledger top-up and mirror event. |
| Scheduled payout | Valid future IST slot; explicit DB time fixture makes it due; documented cron | V24 passes clean replay 3. `routes-monolith2` scheduled case additionally reaches actual FTS/Payouts processed, balanced journals and signed merchant delivery. The explicit scheduled_at SQL fixture is recorded; this is not proof of waiting for wall-clock scheduler cadence. |
| Failed / reversed payout | Bank terminal-decline route plus explicit failed adapter tests | V07/V17/V18 pass clean replay 3. `returned-monolith2` separately verifies post-processed return through explicit FTS manual reconciliation, balanced reversal journal and signed delivery. |
| Merchant webhook | Actual Stork capture and signed sink | Baseline golden passed. V21 exact one terminal delivery and duplicate no-op; payout-ID scoping corrected after smoke contamination; V21 passes clean replay 3. |

## Relay and dependency faults

| Control | Evidence / limit |
|---|---|
| Deliver | Baseline golden traverses real callback relay. |
| Drop | V16 passed candidate2 with actual FTS PROCESSED / Payouts initiated divergence for 10 seconds. This only proves no repair observed in that window. |
| Delay | Scoped relay control implemented; no live acceptance trace yet. V17 delays Ledger, a different boundary. |
| Duplicate | Scoped relay control implemented; V21 duplicates Payouts callback directly, not the relay. |
| Reorder | Exact permutation validation tested offline; no live ordered-message convergence scenario yet. |
| Shield delay | V22 passed candidate2: actual scoped delay hit and Payouts initiated. |
| Pricing 500 | V23 path corrected to actual `/v1/payouts_service/fetch_pricing_info`; V23 passes clean replay 3: actual fault hit, HTTP 500, exact retained create_request_submitted row, no transaction/journal/FTS and unchanged balance. This is a response-boundary assertion, not permanent rejection after fault removal. |
| Ledger delay / 503 | V17 observes committed reversal before delayed journal; corrected event query and expected outage HTTP 500. Both V17 scenarios pass clean replay 3 with real async journal recovery. |
| Stork / merchant sink error | Common faults available where identifiers match request body; no live outage/delivery-retry acceptance scenario yet. |
| Network failure | Internal-network DNS/egress audit is separate isolation evidence. No application recovery scenario for a dropped network connection is established by that audit. |

## P1 contract scope still incomplete

Mozart currently exposes generic arena scenario selection by merchant or attempt. It does not reproduce the full gateway/version-specific selectors and payload classification matrix required by `TWIN_SPEC/substitute-contracts/mozart-sim.md` (RBL v1/v2, ICICI, Yesbank, IDFC raw specials, and all listed buckets). Generic control tests must not be described as full Mozart contract fidelity.

Monolith credit/free-payout state and FTA retry bookkeeping are process-local synthetic state. Full durable API FTA/credit state is not implemented. Explicit balance synchronization preserves Ledger as Shared truth and commits the API mirror before event delivery; an automatic monolith read does not refresh Ledger truth.

The verified 26 green assertions are necessary but insufficient to claim every requested scenario has exact starting state, Payouts/FTS/Ledger/webhook assertions and a live trace. The rows above identify the additional evidence required.


## Immediate-failure supplemental cases verified

`route_scenarios.py` includes `failed_direct` and `failed_shared` in its default six-case `all` selection. They also run individually with `--case failed_direct` or `--case failed_shared`. [The final six-case run](runs/final-supplemental/route/route-results.json) passes all six with complete snapshots; [its audit](runs/final-supplemental/route/egress.json) passes with command exit 0 and zero outside packets. Both immediate failure cases have exactly one signed final receipt and their required accounting/reversal behavior. The final [six-bank run](runs/final-supplemental/bank/bank-effects.json) also passes with a [passing audit](runs/final-supplemental/bank/egress.json), exit 0 and zero outside packets. Full route distinctions are in [ROUTE_COVERAGE.md](ROUTE_COVERAGE.md).

- `failed_direct` uses M2 from generated `scenario:invalid_beneficiary`, independently of the six bank cases using that namespace's M1. Actual bank failure must produce exactly one INVALID_ACCOUNT_NUMBER/FAILED attempt, FTS FAILED and Payouts failed, no reversal entity and no payout Ledger journal. Exactly one signed final payout.failed receipt is required.
- `failed_shared` uses M1 from generated `scenario:kafka`, which is a data-isolation label and does not activate Kafka transport. Actual bank failure must produce one INVALID_ACCOUNT_NUMBER/FAILED attempt, FTS FAILED and Payouts reversed, one balanced payout_initiated journal plus one balanced rvrsl_ payout_failed journal, a matching reversal transaction link and exact restoration of the starting Ledger balance. Exactly one signed final payout.reversed receipt is required.

Each records existing payouts and starting balance, validates actual bank invocation, rejects unexpected processed/failure terminal receipts and emits the shared completion snapshot with monolith evidence. The report records the actual `route_profile`; the expectations describe the monolith baseline. Running them with an alternate active profile is an explicit compatibility check. A fixture namespace never establishes which transport executed. The original 26 test node names and assertions are unchanged.


Direct transport's first live golden exposed an existing vocabulary mismatch for nonempty beneficiary type `savings`: API uses plural savings, Payouts forwards it unchanged, and FTS accepts singular saving (case-insensitively). The baseline Shared fixture now uses API-valid null, exercising Payouts' existing saving default. This nullable baseline does not establish support for nonempty savings; that source contract limitation remains visible. Source evidence is in `CONTRACT_FIXES.md`.
