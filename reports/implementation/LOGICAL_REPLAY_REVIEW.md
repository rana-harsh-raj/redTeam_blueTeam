# Logical replay comparison review

## Final verdict

**Passed:** the independent empty-volume replay19/full and replay20/full runs each passed 26 tests, with no failures, errors or skips and passing same-window egress audits. [The final comparison](logical-replay-final.json) validates both complete inputs and reports all 26 equivalent, with no differing tests. This proves equality of the recorded observations under the documented normalization rules; it does not prove every concurrency schedule or unobserved intermediate state.

The verifier now establishes the actual held bank response and matching Payouts/FTS handoff where a scenario requires that starting state. Immediate synchronous balance/reservation assertions remain immediate. V1/V3 repeat requests after a fixed held state; loss/fault/callback stimuli follow their stated prerequisites. Four fail-closed helper checks supplement the real-service runs. No core behavior was changed to obtain this comparison, and no comparator rule drops a payout state, channel or missing FTS identity.

All earlier failed comparisons remain below and in their original artifacts. Their observation differences led to source-derived barriers, rather than retrospective changes to saved results.

## Historical replay3/replay4 review

The saved **replay3/full** and **replay4/full** runs each contain 26 passing JUnit cases and complete passing setup/call/teardown traces. After correcting the comparator, both inputs validate, **24 tests compare equally and 2 retain material observation differences**. [logical-replay.json](logical-replay.json) remains `status: failed` and `logical_equivalence: false`. No scenario was rerun, no saved input trace was changed and no acceptance gate was altered by this review.

The first comparison incorrectly rejected V17 because it paired all HTTP requests with responses using a single FIFO. V17 deliberately issues a delayed mutation in a worker thread while the main thread polls another endpoint. A GET response therefore completes before the earlier POST. Matching now uses method and URL, keeps the request body and status, and records mutations in request-submission order. Unmatched requests/responses and ambiguous overlapping requests to the same endpoint still invalidate evidence.

## Source-backed normalizations

Source repository paths below refer to the admitted local sources identified in [build provenance](BUILD_PROVENANCE.md). Package paths refer to `ENV2_COMPOSE/`. These rules normalize observation artifacts; they do not remove monetary values, states, unknown payload fields, signatures, event counts or mutations.

| Difference | Rule and evidence |
|---|---|
| Concurrent HTTP completion | Pair by method/URL. `verifier/verifiers/test_v17_reversal_ordering.py:16–25` explicitly starts a worker POST and polls fault hits. `verifier/helpers/http_client.py:65–73` records submission and completion separately. HTTP and DB mutation ordering is also retained in one `mutation_history` collection. |
| Payout identities in the arena-wide sink | Register payout IDs from each test's `/v1/payouts` create responses, using test identity and distinct create-result order. Repeated create responses preserve their shared ID. This avoids assigning identities by cross-merchant webhook arrival order. Fixture merchant/account identities remain literal. |
| FTS transfer and attempt IDs | `fts/internal/migrations/00005_transfers_table_create.go:15` and `00006_attempts_table_create.go:15` declare `AUTO_INCREMENT` IDs. Normalize only typed transfer/attempt row IDs, their named foreign-key fields and verified SQL parameters. Zero/absent IDs remain distinguishable; numeric money, counts, fixture account IDs and unknown nested IDs remain literal. Replay3 V3 used transfer/attempt 2; replay4 used 1. |
| Bank-generated UTR | `substitutes/mozart-sim/server.py:53–68` constructs `ARENA` plus the zero-padded generated attempt number. Only that exact shape is normalized through the attempt identity map. Named fixtures such as `UTR_V21`, `UTR_V13`, `SIM_UTR_100` and `SIM_UTR_GENERIC` remain literal; UTR equality relationships remain comparable. |
| FTS `transfer_by` | `fts/internal/transfer/attempt_processor.go:2217–2237` derives it from the current timestamp, optionally with configured beneficiary SLA. Absolute values differ across boots. This comparison omits that absolute clock field; it does not establish a separate SLA-equivalence claim. |
| Reservation `dispatched_at` | `payouts/internal/app/payouts/processor/queueingstrategy/reservation_gate.go:241` calls `Reserve` with `time.Now().Unix()`. Amount, reservation state, payout identity and totals remain material. |
| Ledger timestamps | Journal migrations `ledger/internal/database/pg_migrations/20201001011143_create_journal.go:23–27` define transaction and microsecond timestamps; the latter default to current epoch time. `verifier/verifiers/test_v04_ledger_dedupe.py:14` and `verifier/helpers/payouts_flow.py:201` explicitly use `int(time.time())` for synthetic journal requests. Absolute `transaction_date`, `created_at_microsecond` and `updated_at_microsecond` are omitted alongside the existing explicit clock list. Unknown time-like field names are not dropped by a broad pattern. |
| Top-up transactor identity | `verifier/helpers/payouts_flow.py:199` builds `arenatopup_` plus `uuid4().hex[:14]`. Only this exact generated shape is aliased. The actual top-up mutation and monetary entries remain compared. |
| Scheduled-payout clock fixture | `verifier/verifiers/test_v24_cron_dequeue.py:93–96` explicitly updates only `scheduled_at` to `int(time.time()) - 60`. The exact SQL mutation, payout ID and relative offset are retained. Both traces record a minus-60-second offset; a different offset remains a difference. |
| Stork event IDs and capture arrival order | `substitutes/stork-capture/server.py:26–32` documents no delivery-order guarantee; `:42–43` generates counter IDs. Capture identities are registered by stable merchant/payout/event content before sorting the debug `events`/`deliveries` collections. All records and duplicate multiplicity are retained, including other merchants' background events. Ordinary lists, ordered SQL results and actual mutation histories retain order. |
| Webhook `body_sha256` | `substitutes/merchant-webhook-sink/server.py:50–70` stores parsed JSON and the SHA-256 of received bytes; `substitutes/stork-capture/server.py:136–142` sends the compact payout JSON string. Every compared digest is first verified against reconstructed compact JSON in preserved key order. All 48 final sink records in each replay match. The comparator then hashes the normalized body, so generated IDs/timestamps do not create misleading hash differences. Signature flags and payload contents remain material. A tampered digest invalidates evidence. |
| Repeated read polling | The final record per identical SQL/query parameters or GET URL remains the comparison observation. V17's separate `ordering_observation` remains material, preserving the assertion made while Ledger was blocked. No HTTP or DB mutation is discarded as a poll. |

The digest reconstruction intentionally fails closed if a future adapter changes JSON byte serialization: a new source-backed decoder would be needed rather than ignoring the digest. This is recorded-observation equivalence, not proof that every intermediate system state or every possible execution is identical.

## Remaining differences

**V1 — `test_idempotent_create_same_key_same_body`.** At trace sequence 12, the duplicate-create response has `internal_status: initiated` in replay3 and `created` in replay4. At sequence 14, the final payout read is `initiated / RBL / nonzero FTS transfer ID` versus `created / yesbank / FTS ID 0`. The IDs, request, amount and idempotency assertions pass in both runs. The verifier waits a fixed 0.5 seconds and asserts identity and row existence; it does not wait for a common final progression state. The comparator retains the state/channel/FTS-presence differences. A completion barrier and new clean replays can establish the same later logical result; these existing traces alone cannot.

**V21 — `test_terminal_webhook_fires_exactly_once`.** Final Stork observation sequence 25 and the corresponding sink record each contain one `payout.initiated`, one `payout.updated` and one `payout.processed`. Both runs have one signed terminal delivery, matching money and terminal status. The `payout.updated` entity is `processing` without the processed status details/error fields in replay3; it is `processed` with those fields in replay4. This difference remains after capture ordering and identifiers are normalized.

The source explains a possible timing mechanism: `payouts/internal/app/payouts/webhooks.go:38` starts `FireWebhookEventForPayout` asynchronously; `:70` selects the event name from the supplied status, while `:80–84` loads status details and builds the payload from the payout object later. A fixed event name can therefore carry a later entity snapshot. The explanation is not used to delete or merge the observed status fields. Any future bounded-variance policy must state this exact scope, retain the observed variance and require a matching canonical final outcome; this comparator currently reports the difference.

## Verification

`python3 -m unittest discover -s ENV2_COMPOSE/scripts -p test_compare_replays.py -v`: **22 passed**. Added checks cover concurrent HTTP completion, rejection of ambiguous overlap, mutation ordering, typed autoincrement relationships, literal money/fixture UTRs, unknown nested IDs/time-like fields, preserved scheduled-clock offsets, capture ordering, verified digest normalization, digest tampering, state changes and duplicate deliveries. Existing missing/failed/skipped/incomplete trace checks remain passing.

`python3 ENV2_COMPOSE/scripts/compare-replays.py reports/implementation/runs/replay3/full reports/implementation/runs/replay4/full --output reports/implementation/logical-replay.json` exits **1**, with both inputs valid and the two differing tests above. This is the expected fail-closed outcome for the saved evidence.

## Later replay11/replay12 review

Both later runs contain 26 passing tests. The original [replay11/replay12 comparison](logical-replay-replay11-12.json) is preserved. An additional exact response-order rule now produces [the reviewed comparison](logical-replay-replay11-12-reviewed.json): both inputs valid, **24 tests equal and 2 still different**. It remains failed; no state normalization or comparator-side capture filtering was added.

The one new rule applies only to a `POST` response from `http://payouts-api:9400/v1/cron/process_queued_low_balance_payouts`, when its body is an array of objects identified by `balance_id`. It sorts whole objects by balance ID and full content, preserving duplicate multiplicity, all counts/errors and unknown fields. It does not sort request bodies, other endpoints/methods or unidentified rows. This is a per-balance outcome multiset, not a mutation reorder.

Source proof: `payouts/internal/app/payouts/helperQueuedPayouts.go:164–237` combines the input balances with cached and recently queried queued balances. The downstream helper therefore treats the requested balances as additions to the discovered set; the response containing three balances despite a one-balance request is visible source behavior. `core.go:4568–4615` iterates balances and collects goroutine results into the response, while the optimized path at `:4686–4719` queues Go-map entries and collects worker results. Neither establishes response-array order. `internal/app/dtos/process_queued_payouts_initiate_request.go:12–20` does bind the JSON body; the broader response is not explained by an ignored request parser.

The preserved mismatches are:

- **V22 Shield fail-open:** both runs observe the injected 1500 ms Shield delay and reach payout `initiated`, but one last read has `RBL` and a populated FTS transfer ID while the other has `yesbank` and ID 0. No completed handoff is established by that older verifier's final read. These fields remain different in the comparator; adding a real handoff completion read in future runs is preferable to deleting them.
- **V21 global sink observation:** V21's own payout fields and signed terminal event agree. Its unscoped sink GET additionally records a V13 payout whose `payout.updated` payload is `processed` in one replay and `processing` in the other, with corresponding status details/error fields. The mutable asynchronous webhook mechanism described above remains a source gap. The full background record remains in these input traces and their reviewed comparison. New requests may ask the sink for an explicit merchant/payout scope matching the verifier's assertions while retaining the sink's global history, but this review does not retrospectively discard captured evidence.

The comparator suite now has **25 passing tests**, including three new checks for the exact cron response multiset, narrow endpoint/method scope, retained request order, all result fields and duplicates. The reviewed replay11/replay12 command still exits 1, as required for the two remaining differences.

## Replay13–16 and scenario starting-state correction

[Replay13/14](logical-replay-replay13-14.json) passed all 26 assertions in both runs but retained V14 FTS ID/channel differences. The transition verifier now awaits the actual asynchronous handoff before its final transition snapshot; it preserves the source transition table.

[Replay15/16](logical-replay-replay15-16.json) also passed 26/0/0 twice, but retained V16 FTS ID/channel differences (25 equivalent, one different). The relay-drop control was enabled after Payouts reached initiated, before the initial FTS details callback necessarily arrived. Dropping that callback could leave FTS ID zero and the initial channel. V16 now requires the real handoff before dropping subsequent status delivery. Its ten-second bounded no-repair observation and terminal FTS assertion remain unchanged. No stored comparison was rewritten into a pass.

## Held-bank acknowledgement observation

The later held-bank completion helper selects `(acknowledged_at > 0) AS bank_response_received` from the real FTS attempt row. Its recorded result is SQL `1`, `0` or `NULL`; the absolute timestamp is never selected. The comparator therefore needs no new clock normalization. The projection, attempt status, bank status code, transfer identity and payout handoff remain compared, including zero, null and missing-field distinctions. Any future raw `acknowledged_at` field remains literal pending explicit review.

Pinned FTS source at commit `2a09e763116db47a2f28553c677ad73ef8133ebf` calls `gateway.DoTransfer` in `internal/transfer/attempt_processor.go:622–627`, assigns `AcknowledgedAt = utils.GetCurrentTimeStamp()` at `:649`, and invokes `ProcessResponse` at `:678`. `pkg/utils/utils.go:43–45` implements that clock as `time.Now().Unix()`. `ProcessResponse` calls `UpdateAttempt` (`attempt_processor.go:376–381`); response fields are filled at `:3079` and persisted by `repo.Update` at `:3836` before updating the transfer.

Positivity alone is not sufficient evidence of a consumed response: `internal/transfer/attempt.go:131–132` also initializes this field in `SetDefaults`. The helper additionally requires the persisted `bank_status_code` and attempt/transfer status to be `INITIATED`, then waits for Payouts to contain the actual FTS ID and channel. The initial schema's bank status is nullable (`internal/migrations/00006_attempts_table_create.go:24`), and the explicitly held adapter response supplies `bank_status_code: INITIATED` (`substitutes/mozart-sim/server.py:69–75`). Together these observations establish the held response boundary used by these fixtures; the timestamp alone makes no such claim.

The comparator's **26 tests pass**. The additional regression verifies that the projected acknowledgement result survives and that `1`, `0`, `NULL`, missing fields and unexpected raw timestamps remain material. No comparator implementation change or saved-trace rewrite was made for this check.

## Milestone 1 comparator rules (2026-09-05)

Two strengthened verifiers introduced observations the comparator had not seen before. The first Milestone 1 comparison
(`runs/m1-20260905T161523Z`, retained) reported two differences that were traced to test instrumentation rather than to
service behaviour, and the comparator gained two documented rules; no verifier or service behaviour was changed.

1. **Monolith sink log scope.** V06 and V16 now read the monolith substitute's `GET /_arena/log`, which is ONE global
   append-only in-memory list for the whole suite (`substitutes/monolith-stub/server.py:65-70,99-100`), appended by
   concurrent workers. A test reading it therefore sees every other test's entries and the arrival order of asynchronous
   sinks (`source_update` vs `mail_and_sms`). The comparator keeps only entries that reference the reading test's own
   payout identities, keeps duplicates, and drops arrival order (`log_scope` is recorded in the normalised record). Other
   tests' entries are compared inside those tests' own observations. A different `kind` or status inside the scoped
   entries remains material (unit test `test_monolith_log_is_scoped_to_the_test_and_unordered`).
2. **Simulator bank reference.** `gateway_ref_no` is a fresh 12-character reference minted by mozart-sim per
   `transfer_init` (`substitutes/mozart-sim/server.py:24,55,69`). It is aliased as a generated identity, like the
   attempt-derived UTR already was.
   The same scoping applies to the other suite-wide unfiltered captures a test may read without a merchant filter:
   the merchant sink's `GET /_arena/deliveries` and Stork capture's `/_arena/events`; V16 reads the sink unfiltered.
   Filtered reads (`?merchant=...`) are untouched. Unit test `test_unfiltered_sink_deliveries_are_scoped_to_the_test`.
3. **V16 clock snapshot.** V16 records `payout_updated_at` before and after its no-repair window and asserts equality
   inside the test; the comparator treats that field as an explicit clock (added to the clock-field set).

After these rules the second comparison of the same two runs passed with no differences; the third comparison
(`logical-replay-m1.json`) is the one the generated gate embeds. 4. **Reservation inspect order.** `GET /v1/inflight_reservations` lists live items from a Redis hash via HGETALL
   (`payouts/internal/app/payouts/reservation/store.go:353-354`); Go map iteration gives no order, and V12 (unchanged
   by Milestone 1) read the three items in a different order on the third clean pair. Items, their fields and their
   count remain material; only the order is dropped (unit test `test_reservation_inspect_items_are_unordered_but_material`).

The comparator test suite is 29 tests.
