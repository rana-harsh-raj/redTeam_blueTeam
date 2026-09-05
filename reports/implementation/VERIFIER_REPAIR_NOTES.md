# Verifier contract repairs

The existing 26 test node names remain unchanged, preserving generated scenario-index lookup. Missing required fixtures and services fail visibly; no verifier skips, terminal fallback injections, swallowed state timeouts, floating-point accounting tolerances, or zero-event completeness passes remain.

- Each node uses its generated merchant namespace and sets explicit bank behavior before creation. Real bank success/failure drives V6, V7, V8 and V18. V16 releases the actual bank attempt and calls FTS `/v1/transfer/{id}/check`; it never manufactures FTS terminal rows.
- Adapter-boundary tests V13, V17, V19 and V21 use the actual FTS transfer ID, selected source account and bank account type. V15 deliberately supplies an unrelated transfer ID to an already-cancelled payout to test the state guard. These checks do not claim to exercise a full bank return path.
- Shared journal accounting uses exact Decimal arithmetic and fee-inclusive amounts. Payout fees already include tax. Reversal journals are located using the pinned source's `rvrsl_<reversal_id>` transactor ID; payout journals use `pout_<payout_id>`.
- V4 sends sequential duplicate complete Ledger DTOs with tenant X, validates both HTTP responses and exactly one persisted journal, and asserts no unique index on the transactor pair (I03). V3 requires one actual bank attempt after duplicate transfer creation.
- V12 requires exactly three live reservations totaling 300 paise. V13 follows the newer I30 source contract: processed retains `awaiting_balance_refresh`; a separate failed payout releases immediately. The earlier catalog's blanket immediate-release claim was incorrect.
- V14 creates its own payout, requires nonempty transition logs and checks the exact pinned state-machine transition table. A legal processed-to-reversed transition is allowed.
- V16 records the actual bounded observation duration. Its finding is only “no repair observed in this window,” not proof that no repair exists anywhere.
- V17 delays or rejects the real Ledger dependency call, observes a committed reversal while the journal is absent, then checks normal recovery after clearing the scoped fault. It does not infer ordering merely because both rows exist after an HTTP response.
- V21 requires exactly one terminal event at Stork and the merchant receiver, correct entity ID/status, successful delivery and valid HMAC; duplicate terminal stimulus must not add a delivery.
- V22/V23 install scoped dependency faults and require the actual payout request to hit them. V24 copies the real Ledger top-up into the API balance mirror before calling the queued cron; scheduled-time acceleration is explicitly recorded as a synthetic database fixture.
- HTTP requests/responses and DB observations/mutations are written to per-node sanitized JSONL traces. Result reports must distinguish test findings, setup failures and incomplete runtime checks.

Offline evidence: all 26 nodes collect in the verifier container with `--network none`; Python syntax compilation passed. Six bank HTTP control contract checks passed (see `bank-control-tests.txt`). Sixteen monolith adapter checks and three Splitz contract checks also passed. Live outcomes are recorded separately by the audited run; these offline checks do not constitute an integration pass.

## Handoff reconciliations

- V10 now requires HTTP 400 with a balance error and no debit/FTS transfer (I20); it no longer requires a persisted failed payout.
- Hidden wait scaling default changed from 6 to 1, so reported timeout budgets match wall time unless explicitly overridden.
- P0.6 handoff URI/body are inconsistent with the pinned code: `payout_internal_routes.go:12` groups under `/v1/payouts`, its `:52` route is `/balance_update_event`, and `payoutController.go:766-770` consumes `BalanceIDs`. The supported call remains `/v1/payouts/balance_update_event` with `{balance_ids:[...]}`. The explicit arena sync first commits Ledger truth into the API mirror and optionally sends that event; normal balance reads remain mirror reads.
- A separate `scenario.py` drives baseline M1 through Kong with an actual generated merchant Basic credential and produces `/results/live-result.json`. Its default total timeout is 90 seconds. The offline missing-fixture path was tested; it correctly returns failed JSON and exit 1.


## Candidate 2 diagnostic corrections (2026-09-05)

The first full run is retained under `runs/candidate2/full`: 15 passed, 11 failed, zero skipped. These are harness or fixture corrections awaiting clean replay, not retroactive changes to that outcome:

- V04: pinned Ledger `internal/journal/validation.go:353` rejects a sequential duplicate with HTTP 400 `invalid_argument` / `record_already_exist`; the original and replay still must leave exactly one journal.
- V07/V18: `INSUFFICIENT_FUND` is retryable for Shared FTS (`internal/providers/mozart/error_code.go:395`). The explicit `failure` scenario now returns `INVALID_ACCOUNT_NUMBER`, a terminal merchant error (`:290`). A separate `insufficient_funds` scenario retains the retryable outcome. This is substitute fidelity, not a core retry-policy change.
- V12/V13: the reservation gate was actually enabled/trusted but received gateway balance zero. `bankingAccountStatementDetails/core.go:292,714` selects XBalances only when its explicit gate is on, else BASD. Direct fixtures seed XBalances but leave BASD unseeded. The Direct synthetic gate is now explicitly on in Splitz; fallback to unseeded BASD remains a fidelity gap. This selects the source of balance truth; it does not fabricate reservation state or change the core decision.
- V15/V20: cancellation route requires a bare 14-character payout ID (`dtos/payoutCancelRequest.go:11`). Tenant denial now also requires owner fetch success and the exact missing-ID error, so input-format rejection cannot pass the isolation check.
- V17: Shared bank declines reverse Payouts but post Ledger `payout_failed`, while a later bank return posts `payout_reversed` (`reversals/core.go:284`). The delayed journal existed; the helper selected the wrong event. Outage delivery is HTTP 500 after the reversal commit, propagated by `fts_transfer_status_webhook.go:1186`. Recovery still requires the actual async worker, a balanced journal and transaction link.
- V19: the explicit existing-transaction guard returns an error/HTTP 500 (`fts_transfer_status_webhook.go:654`), while leaving payout initiated and its transaction ID intact.
- V21: captures are scoped to actual payout ID plus merchant, so an earlier smoke payout cannot contaminate exact delivery counts.
- V23: fault matching happens before `/v1` normalization; the real pricing dependency path is `/v1/payouts_service/fetch_pricing_info`. The test still requires a recorded actual fault hit, rejected payout, unchanged Ledger balance and no FTS transfer.

Offline validation after these changes: 7 bank control contract tests and 16 deterministic generator tests pass. Runtime replay remains separately recorded.


## Replay 1 findings and staged corrections (2026-09-05)

The audited clean replay under `runs/replay1/full` completed with 21 passed, 5 failed, zero skipped. Its egress audit passed. The failures are retained; the next run must independently verify the following corrections:

- V07/V18: real FTS now reaches FAILED with terminal INVALID_ACCOUNT_NUMBER. The monolith adapter was forwarding uppercase `fts_status`, while PHP `FundTransfer/Attempt/Core.php:496` lowercases it before Payouts dispatch. Payouts reversal code matches lowercase `failed`/`reversed`. Both `fts_status` and details `fta_status` now normalize to lowercase; the added regression verifies details-before-status and Shared failed-to-reversed translation. All 17 isolated monolith tests pass.
- V12/V13: XBalances was reachable only on container loopback. The HTTP and internal listeners are staged for peer-reachable `0.0.0.0`; gRPC keeps its documented local gateway self-dial. The observed failure was connection refusal, followed by zero-balance fallback, rather than a reservation arithmetic defect.
- V23: the pricing fault hit the actual path and returned HTTP 500. The retained row was `create_request_submitted`, not `failed`. `processor/payout_pricing.go:456` marks transport errors retryable; Shared `Process` returns before Ledger and the base processor persists the unchanged pre-create state. The verifier now requires exactly one such row, no transaction link, no payout journal, no FTS transfer and unchanged Ledger balance at the response boundary. It does not claim permanent rejection after the fault clears.

V17's two real Ledger delay/outage tests passed in this replay. The standalone bank and golden runners now emit the shared bounded completion snapshot, including all persisted FTS attempts, payout state logs, reversals, linked Ledger journals/entries/accounts, actual bank events and actual signed merchant receipts. Snapshot read errors fail the scenario. Queue contents and cross-service atomicity remain explicitly unobserved. Supplemental runtime results remain pending.


## Replay 2 channel fixture correction

Replay 2 completed with 21 passed, 5 failed, zero skipped and a passing egress audit. V07 confirmed actual bank failure now reverses Shared payout and Ledger correctly; V23 confirmed the exact pre-create/no-debit error boundary. The five M2 failures (V08/V12/V13/V18/V19) all returned HTTP 400 `Invalid channel name: RBL` once XBalances became peer-reachable.

`x-balances/internal/enum/channel/channel.go:27` defines `rbl`; `payouts/internal/app/balance/core.go:240` copies it verbatim and `payouts/internal/app/payouts/mode.go:65` validates the channel case. The three XBalances source seed rows now use lowercase `rbl`, while the distinct FTS enum remains uppercase `RBL`. The generated fixture check validates both domains over every namespace. All 17 generator tests pass. The next clean runtime replay must verify this seed correction.


## Clean replay 3 verified

`runs/replay3/full` records **26 passed, zero failed, zero skipped**. Its egress audit reports `status=passed` and command exit 0. This clean boot validates the corrected Shared reversal, Direct XBalances selection, exact reservation behavior and pricing-error boundary. Candidate/replay failures remain retained as diagnostic history. Supplemental all-layer route/bank scenarios and the second final clean replay are separate acceptance work; this result does not promote untested controls to live coverage.


## Supplemental scenarios after replay 3

`runs/bank-monolith/bank-effects.json` and its audit record six passed cases and command exit 0: held, delayed success, ambiguous with/without UTR, bank duplicate and delayed invalid-envelope HTTP 504. The five nonterminal cases assert exact held debit and absence of terminal journal/webhook during a bounded observation; delayed success asserts real status checks, terminal convergence, balanced journals and signed delivery.

The first supplemental route run found a snapshot-only Python tuple/list error after Direct payout success. `trace_snapshot` now normalizes driver fetch sequences; its offline regression covers an empty MySQL reversal tuple plus multiple attempt rows. `routes-monolith2` passes Direct/current-account success, scheduled and queued terminal cases. Returned initially failed because normal FTS check correctly rejects a processed attempt with ILLEGAL_STATE. The corrected returned runner uses the source-supported explicit admin raw verify followed by safe_update, which independently verifies bank status before committing PROCESSED→REVERSED; its live result is pending. No automatic later-return polling is claimed.


## Returned after processed: source-backed reconciliation verified

`runs/returned-monolith2/route-results.json` passes with a complete snapshot; egress audit passes with command exit 0. A real Shared payout first processed through FTS/bank, Ledger and a signed merchant event. FTS raw verification then observed RETURNED/BBANK and an actual return UTR. The existing FTS admin `safe_update` worker performed a second bank verification, committed FTS/Payouts reversal, posted the balanced `payout_reversed` journal and delivered the signed reversed event. This is explicit manual reconciliation, not automatic post-terminal polling.

Prior failures remain: `routes-monolith2` used ordinary transfer check, which correctly rejects terminal attempts; `returned-monolith` supplied optional status=REVERSED in the admin update, causing `common.Fill` to overwrite the in-memory source state before the state-machine event and roll back an illegal REVERSED→REVERSED transition. The corrected request omits status and uses only meta.reversed to select the target. Source anchors and exact route semantics are in `RETURNED_ROUTE_EVIDENCE.md`.


## V21 deterministic delivery boundary

Replay comparison exposed source-dependent payout.updated payload status: the combined UTR/terminal callback schedules the updated webhook asynchronously using the mutable payout entity (`payouts/internal/app/payouts/core.go:2130,2218`; `webhooks.go:20`), then processes terminal status. The updated payload may observe processing or processed. This unconstrained ordering remains a source limitation.

V21 now explicitly separates stimuli at supported adapter routes: wait for held/initiated payout and actual FTS details assignment; POST details-only with the real transfer ID and UTR_V21; await one signed payout.updated receipt with processing status and that UTR plus completed Stork delivery; then submit processed with the same UTR and repeat it. Exact one terminal receipt and unchanged updated count remain required. This makes the test's starting condition observable without changing application behavior or inventing a terminal bank path. Live deterministic replay remains pending.

## Replay7 V6: ordinary mutex retry exceeded the verifier deadline

The retained replay7 run has 25 passed and one V6 failure: no processed journal was observed within the original 20-second wait. [Allowlisted runtime records](runs/replay7/v6-retry-observation.json) show payout pout_TYMtZoB9SBq65l enqueued its processed-Ledger job at 13:15:06.717Z, the worker failed to acquire the payout mutex at .720Z, and its next attempt created the journal at 13:15:37.839Z. The original failed test trace is unchanged.

Payouts core.go:1479–1484 holds ResourcePayout while HandlePayoutProcessed calls ledgerProcessingForPayoutProcessed and queues the job at :2589. The asynchronous handler acquires the same resource at internal/app/payouts/asyncFailureHandlingHelper.go:119. pkg/worker/manager.go:250–255 requeues handler errors using RetryDelay; pkg/worker/config.go:11 defaults to 30s and the literal arena worker.retrydelay is 30s. This is a source producer/consumer mutex race with ordinary successful retry, not missing FTS metadata, an injected terminal event or permanent accounting failure.

The focused wait_for_processed_journal helper allows one 30-second retry plus the original 20-second settlement allowance, total 50 seconds. V6 and the processed-event waits in scenario.py, bank_scenarios.py and route_scenarios.py use it; all other waits and exact accounting assertions remain unchanged. Its trace policy contains constants, does not infer that a retry occurred in every run, and does not record timing variance as logical state. Virtual-clock tests accept an observed journal at 31 seconds, still fail a missing journal at 50 seconds, and verify the 30-second literal worker configuration. Reproduce with: uv run --offline --with pytest python -m unittest discover -s ENV2_COMPOSE/verifier/offline_tests -p test_processed_journal_wait.py -v. All three checks passed. New clean-run acceptance is separate.

## Replay8 V12/V13: missing startup reservation heartbeat

Replay8 records 24 passed and 2 failed (V12/V13). The failed V12 trace shows three queued creates, store_trusted=false, reserved_total=0 and no reservation items. The initial cron-driver reservation call failed with ConnectionRefusedError because substitutes started before Payouts core; the normal loop scheduled the next attempt 300 seconds later despite the failure. Source gate behavior was correct: reservation/store.go:447 checks heartbeat existence, and queueingstrategy/reservation_gate.go:235–239 fails closed when absent.

The source reconciler cadence is 5 minutes and its heartbeat TTL is 12.5 minutes (inflight_reservation_reconciler.go:51–56). Heartbeat refresh occurs only after a healthy pass (:210–233), yet the cron can return 200/counts even on per-balance errors (:97–101). HTTP health or cron 200 alone is therefore insufficient readiness.

Startup now executes the real cron once after core health and Ledger configuration initialization, then scripts/reservation-readiness.py checks the real API's store_trusted value using the monolith container's existing scoped API credential. No Redis heartbeat is written directly and no extra credential mount was added. up.sh also fails on Ledger configuration HTTP errors. [Manual one-shot observation](runs/replay8/bootstrap-reconcile.log) records 200; [semantic readiness](runs/replay8/bootstrap-trust.log) records true. Normal cadence and verifier assertions are preserved; new clean acceptance is required.

The source's separate post-flush enumeration gap remains: the tracked-balances index lives in Redis, and a flush can remove the enumeration set while DB payouts remain in flight (reconciler.go:153–172). Initial readiness on a fresh empty arena does not establish crash/flush reconstruction.

## Replay9/10 V24: scoped cron input and complete dispatch observation

Both replay9 and replay10 passed all 26 original tests with zero failures/skips and passing command-window audits (exit 0, zero outside packets). Their [logical comparison](logical-replay-replay9-10.json) validates both inputs but preserves differences in the two V24 tests: 24 tests compare equally. These successful assertions alone did not prove equivalent final observations.

The cron tests previously ended as soon as Payouts became initiated, before the asynchronous FTS details update necessarily completed. The compared rows could therefore contain RBL and a real nonzero FTS transfer ID in one run, but the initial yesbank channel and transfer ID 0 in the other. In addition, the unscoped low-balance cron response contained three balance objects whose Go map iteration order varied.

V24 now sends its own fixture balance in balance_ids. Payouts internal/app/dtos/payoutCronRequests.go:5–7 defines the scheduled request's balance_ids/balance_ids_not fields; internal/app/dtos/process_queued_payouts_initiate_request.go:7–9 defines the low-balance request's same fields. This expresses the intended request scope, but the low-balance implementation widens it downstream, as confirmed below; exclusive low-balance processing is not established. The queued fixture uses IMPS so the dequeue assertion does not depend on NEFT bank-window scheduling.

After asserting initiated, both tests observe the actual routed FTS transfer, wait until the Payouts row contains that transfer's ID while still initiated, and require matching channel. The bank remains explicitly held. Existing state, channel, FTS-presence, money and mutation observations remain material; no comparator rule or trace field was removed. This corrects test scope and completion boundaries without changing service behavior. Original replay9/10 results and differences are retained; clean runs 11/12 are the next acceptance pair and remain pending until captured and compared.

### Follow-up: low-balance request scope is expanded by the source

The replay11/12 low-balance request contains one balance_id, yet the response still contains three balances. This is not an ignored HTTP body or a feature branch. The controller builds the request DTO (payoutController.go:828–840); core.go:8583 extracts its filters and applies the whitelist at :8635. The same method then calls ProcessQueuedPayoutsForBalanceIdsOptimised at :8647. That helper calls getBalanceIdsWithQueuedPayouts (helperQueuedPayouts.go:164–237), which unions the input with recent queued DB balances and the global queued-balance cache. Its resulting map is enumerated without sorting; the optimized processor also ranges a balances map and appends worker-channel results (core.go:4626–4729). Other queued balances and variable response ordering can therefore remain despite the requested whitelist.

The scheduled path is different: core.go:4182 passes its whitelist/blacklist directly to GetEligibleScheduledPayouts. The source files inspected here match the admitted build copies byte for byte. No test, runtime or comparator change was made for this clarification, and the multi-balance responses remain visible.

The final replay observations also retain a source-dependent V13 payout.updated body in a global sink read made by V21. V21's own ordered events match; its barrier cannot order another test's combined UTR/processed callback. V13 uses that combined stimulus, while webhooks.go:20–38 launches an asynchronous call with a mutable payout pointer and :70–84 selects the event name then builds its body later. Processing versus processed entity snapshots remain possible without a source behavior change or an explicitly narrower observation contract. V22 separately stops at initiated before FTS details are necessarily assigned. These observations do not justify deleting status/metadata fields from comparison or calling full logical equivalence established.

## Final observation boundaries after replay13/14

Replay13 and replay14 each passed26/0/0 with same-window audits. The exact unordered low-balance cron response normalization retained every balance/count/error and reduced comparison differences; V21 requested a read-only merchant/payout-specific capture, matching its assertions while preserving the complete sink and append-only log. The [isolated scoping check](sink-scoping-check.json) preserves duplicate multiplicity and all stored records. Historical V13 background `payout.updated` variance remains in the retained replay11/12 evidence; this is not a source-webhook fix.

V22 now waits for its real FTS handoff, preserving the Shield-hit and fail-open assertions. The remaining replay13/14 mismatch was V14's initiated row before versus after the same handoff (`yesbank`/FTS0 versus `RBL`/assigned ID). V14 now waits for actual FTS metadata and matching Payouts transfer ID/channel before reading its transition log. Legal-transition assertions and the comparator's state/amount rules are unchanged. Final clean15/16 evidence is reported separately.

### Replay15/16: relay-loss starting state

Both suites passed all26 with passing egress audits; their strict comparison still retained V16's populated versus zero FTS ID and RBL versus initial yesbank channel. The per-payout drop switch affected initial details as well as terminal status. V16 now waits for the real FTS selection and matching Payouts transfer ID/channel before enabling drop. This is a verifier starting-state correction, not a core or relay-behavior change. The original [comparison](logical-replay-replay15-16.json) remains failed. Replay17 was interrupted during startup for the broader handoff review and has no verifier count.


## Systematic held-dispatch completion before replay19/20

The full verifier review found the same timing boundary beyond V16. Replay15/16 retained V2 and V5 payout snapshots at created/fts_transfer_id=0/yesbank, while V3's transfer-status read and immediate duplicate response observed CREATED. These observations matched in that pair but did not establish completion of the held dispatch. V13/V17/V19 could inject callbacks or mutate a transaction link after initiated alone. V11/V12 correctly asserted immediate balances/reservations, yet could finish before the asynchronous bank call had consumed its configured hold rule.

The fixture teardown removes only that test's merchant bank rule. Mozart binds a rule to an attempt when transfer_init reaches `_controlled` (substitutes/mozart-sim/server.py:53–65). If a late init arrives after removal, no attempt-specific rule exists and the legacy amount-100 response is success (:174–175). A successful create or a selected source account therefore does not by itself make cleanup safe.

`wait_for_held_fts_transfer` now requires a scoped persisted attempt with status INITIATED, bank_status_code INITIATED and positive acknowledged_at, followed by FTS transfer status INITIATED. The hold simulator emits that bank code; FTS invokes gateway.DoTransfer and processes its response (internal/transfer/attempt_processor.go:623–681). Positive acknowledged_at alone is insufficient: Attempt.SetDefaults also fills it before the bank call (internal/transfer/attempt.go:122–133). The query records `(acknowledged_at > 0) AS bank_response_received`, preserving the evidence condition without adding a raw clock to the logical comparison. The [retained Shared hold snapshot](runs/final-current-boot/bank/bank-hold.jsonl) verifies actual INITIATED status/code and positive acknowledgement. Direct retained success/decline snapshots confirm the same acknowledgement field; live held Direct acceptance remains part of the fresh pair.

`wait_for_held_handoff` then requires Payouts initiated, its FTS transfer ID equal to the actual selected transfer and case-insensitively matching channel. V1/V2/V5/V11/V12/V13/V14/V16/V17/V19/V21/V22/V24 use this complete boundary; V3 uses the FTS-only boundary. V1 and V3 place the repeated create after the stable held state and preserve identity/one-row/one-attempt assertions. V2's mismatch response, V5's accounting checks, V11's single immediate debit read and V12's immediate reservation assertions remain before completion waits. Terminal injections, V19's transaction-link SQL mutation, V16's drop switch and V21's ordered details stimulus occur after completed held dispatch. Intentional no-dispatch paths do not acquire this wait.

No product state machine, callback, bank result, comparator rule or material state field was patched or weakened. Four virtual-clock checks pass: attempt existence/initiated/positive acknowledgement cannot bypass the missing bank response; missing response or details time out; a mismatched channel times out. Run `uv run --offline --with pytest python -m unittest discover -s ENV2_COMPOSE/verifier/offline_tests -p test_held_handoff_wait.py -v`. Replay19/20 and their exact-window audits are the next acceptance evidence; previous failed comparisons remain retained in full.

## Milestone 1 (2026-09-05)

Following `reports/claude-review/TWIN_V1_AUDIT_AND_NEXT_STEP.md` section 8 completion criterion 5, three existing nodes were strengthened and one new supplemental family was added; the 26 original test node names and generated scenario-index lookup are unchanged.

- **V04** (`ENV2_COMPOSE/verifier/verifiers/test_v04_ledger_dedupe.py`, I03) now guards its own negative assertion. Before asserting that `pg_indexes` shows no unique index on `(transactor_id, transactor_event)` for `public.journal`, it first requires `pg_indexes` to return rows at all and to include the `journal_pkey` primary key -- a wrong table name, wrong schema, wrong `search_path` or a renamed table would otherwise satisfy the negative vacuously. The catalogue read is recorded to trace as `journal_index_catalogue` before the negative assertion runs. Sequential-duplicate behaviour (HTTP 200 then 400 `invalid_argument`/`record_already_exist`, exactly one persisted journal row) is unchanged.

- **V06** (`ENV2_COMPOSE/verifier/verifiers/test_v06_ledger_accounting_processed.py`, I21) now asserts all three of I21's named clauses explicitly, per the docstring added to that file. Clause (a) -- `fts_fund_account_id`/`fts_account_type` come verbatim (lower-cased) from the FTS webhook -- is asserted against the `payout_meta_temporary` `fts_info` meta that `ledgerProcessingForPayoutProcessed` writes (`core.go:2532-2542`), since Ledger's own `journal`/`ledger_entry_details` schema does not persist these identifiers at all (`pkg/ledger/ledger_journal_create.go:210-218` puts them only in the request `Identifiers` map, which Ledger consumes for account discovery and discards). Clause (b) -- the credited Ledger account's own `account_details.entities.fund_account_type`/`fts_fund_account_id` match, under the matching Nodal/Current parent -- is asserted directly against Ledger Postgres, proving the identifiers were correct end-to-end because Ledger's discovery landed the credit on the matching account. Clause (c) -- balanced entries, exactly one `payout_processed` journal row -- is unchanged from the prior version.

- **V16** (`ENV2_COMPOSE/verifier/verifiers/test_v16_stuck_initiated_no_repair.py`, I80/I70) is unchanged in mechanism but its label now matches the invariant text: the docstring states explicitly that this is **stronger than production** (production has no repair mechanism for a stuck-initiated payout at all -- `adminClientController.go:267-356` ManualAction has exactly three cases, none a stuck-initiated repair, and the relevant alert rules are dead through a label mismatch), so the test actively drives all eight cron routes from `ENV2_COMPOSE/scripts/cron-driver/driver.py` JOBS, scoped to the stuck payout's own merchant/balance, through the real `FastCron` identity, rather than merely observing passively.

- **`bank_scenarios.py`** (`ENV2_COMPOSE/verifier/bank_scenarios.py`) now wires a monolith-stub client into its `Arena` fixture (`self.monolith = ArenaHTTPClient('http://monolith-stub:8080', basic_auth=auth('MONOLITH'))`), capturing the same `create_fta`/`payout_status_relay` transport-evidence events that `route_scenarios.py`'s `setup()` already captured. This means bank snapshots now carry the monolith relay event layer, so `route-evidence.py --bank-case` can evidence bank cases the same way it evidences route cases -- previously that evidence layer existed only for route scenarios.

- **`ENV2_COMPOSE/verifier/kafka_scenarios.py`** is new (I70/I71): five cases (`shared_source_failure`, `direct_after_shared`, `failed_dropped_direct`, `failed_dropped_shared`, `reversed_dropped_direct`), each owning its own merchant/fixture, run only under `ARENA_ROUTE_PROFILE=kafka`. It introduces no Kafka client library into the verifier image; every message is produced by the real FTS producer and consumed by the real payouts consumer as a consequence of a real bank outcome. Four of the five cases are source-predicted failures (recorded `expected_failure`, never `passed`) with a citation into `TWIN_SPEC/expected-failures.yaml`. `direct_after_shared` is the regression test for the FD-001 consumer-crash fix (`reports/implementation/runs/claude-audit-kafka-repro-20260905T152544Z/`) and refuses selection unless `shared_source_failure` ran first in the same boot. See [KAFKA_ROUTE_EVIDENCE.md](KAFKA_ROUTE_EVIDENCE.md) for the full mechanism and source citations.
