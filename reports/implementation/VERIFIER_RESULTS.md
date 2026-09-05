# Verifier results

The corrected original suite contains **26 tests in 24 files**. The final empty-volume runs **replay19 and replay20 each passed 26 / 0 / 0** (pass / fail / skip), with passing DNS/packet audits covering the same complete command. Their complete traces validate and all 26 recorded outcomes compare logically equivalent: [final comparison](logical-replay-final.json). Runtimes were 45.895 and 46.333 seconds. No runtime skip remains, and historical failures remain unchanged below.

| Run | Pass | Fail | Skip | Evidence |
|---|---:|---:|---:|---|
| Supplied historical count | 9 | 8 | 9 | Not reproduced; baseline clean startup failed before the verifier could run. |
| Captured baseline | — | — | — | [baseline](baseline/): down removed the optional environment file, then startup aborted while sourcing it. No suite result exists. |
| candidate2 | 15 | 11 | 0 | [JUnit](runs/candidate2/full/junit.xml), [audit](runs/candidate2/full/egress.json), [traces](runs/candidate2/full/) |
| replay1 | 21 | 5 | 0 | [JUnit](runs/replay1/full/junit.xml), [audit](runs/replay1/full/egress.json), [traces](runs/replay1/full/) |
| replay2 | 21 | 5 | 0 | [JUnit](runs/replay2/full/junit.xml), [audit](runs/replay2/full/egress.json), [traces](runs/replay2/full/) |
| replay3 | 26 | 0 | 0 | [JUnit](runs/replay3/full/junit.xml), [audit](runs/replay3/full/egress.json), [traces](runs/replay3/full/) |
| replay4 | 26 | 0 | 0 | [JUnit](runs/replay4/full/junit.xml), [audit](runs/replay4/full/egress.json), [traces](runs/replay4/full/) |
| replay5 | 25 | 1 | 0 | [JUnit](runs/replay5/full/junit.xml): V1 waited for a sending account while NEFT was outside its configured bank window. Corrected to the continuously available IMPS fixture. |
| replay6 | 26 | 0 | 0 | [JUnit](runs/replay6/full/junit.xml), [audit](runs/replay6/full/egress.json), [traces](runs/replay6/full/) |
| replay7 | 25 | 1 | 0 | [JUnit](runs/replay7/full/junit.xml), [audit](runs/replay7/full/egress.json): V6 journal created after 31 seconds on configured retry, beyond the old 20-second wait; [worker evidence](runs/replay7/v6-retry-observation.json). |
| replay8 | 24 | 2 | 0 | [JUnit](runs/replay8/full/junit.xml), [audit](runs/replay8/full/egress.json): V12/V13 correctly queued with missing reconciliation heartbeat after the first cron tick hit connection refused during startup. Fixed startup readiness; assertions unchanged. |
| replay9 | 26 | 0 | 0 | [JUnit](runs/replay9/full/junit.xml), [audit](runs/replay9/full/egress.json); full recorded-observation comparison retained separately. |
| replay10 | 26 | 0 | 0 | [JUnit](runs/replay10/full/junit.xml), [audit](runs/replay10/full/egress.json); full recorded-observation comparison retained separately. |
| replay11 | 26 | 0 | 0 | [JUnit](runs/replay11/full/junit.xml), [audit](runs/replay11/full/egress.json); full recorded-observation comparison retained separately. |
| replay12 | 26 | 0 | 0 | [JUnit](runs/replay12/full/junit.xml), [audit](runs/replay12/full/egress.json); full recorded-observation comparison retained separately. |
| replay13 | 26 | 0 | 0 | [JUnit](runs/replay13/full/junit.xml), [audit](runs/replay13/full/egress.json); recorded-observation differences retained in the comparison history. |
| replay14 | 26 | 0 | 0 | [JUnit](runs/replay14/full/junit.xml), [audit](runs/replay14/full/egress.json); recorded-observation differences retained in the comparison history. |
| replay15 | 26 | 0 | 0 | [JUnit](runs/replay15/full/junit.xml), [audit](runs/replay15/full/egress.json); recorded-observation differences retained in the comparison history. |
| replay16 | 26 | 0 | 0 | [JUnit](runs/replay16/full/junit.xml), [audit](runs/replay16/full/egress.json); recorded-observation differences retained in the comparison history. |
| replay17 | — | — | — | Launcher interrupted during startup to apply the systematic handoff review; no verifier result. [Startup log](runs/replay17/up.log). |
| replay19 | 26 | 0 | 0 | [JUnit](runs/replay19/full/junit.xml), [audit](runs/replay19/full/egress.json); first full validation of the systematic held-response/handoff boundaries. |
| replay20 | 26 | 0 | 0 | [JUnit](runs/replay20/full/junit.xml), [audit](runs/replay20/full/egress.json); second clean run, full resource sampling and [logical equivalence passed](logical-replay-final.json). |

Corrections are explained with source references in [VERIFIER_REPAIR_NOTES.md](VERIFIER_REPAIR_NOTES.md). They include signed Ledger identifiers, exact fee-inclusive money checks, API status/error semantics, Direct reservations awaiting balance refresh, deterministic merchant isolation, actual dependency fault hits, bank state controls and terminal HMAC checks. The earlier failed runs are preserved; their numbers have not been retroactively changed.

## Final original-suite inventory

| Test node | Result |
|---|---|
| `test_idempotent_create_same_key_same_body` | passed |
| `test_idempotent_create_same_key_different_body_rejected` | passed |
| `test_fts_transfer_dedupe_on_source_id_and_type` | passed |
| `test_ledger_journal_dedupe_on_transactor_id_and_event` | passed |
| `test_payout_initiated_journal_sums_to_zero_and_debits_merchant` | passed |
| `test_payout_processed_journal_mirrors_ledger` | passed |
| `test_payout_reversed_journal_and_reversal_entity` | passed |
| `test_payout_failed_direct_account_no_reversal_no_ledger` | passed |
| `test_insufficient_balance_queues_when_queue_if_low_balance_true` | passed |
| `test_insufficient_balance_fails_when_queue_if_low_balance_false` | passed |
| `test_merchant_balance_debited_synchronously` | passed |
| `test_inflight_reservation_total_equals_live_reservations` | passed |
| `test_reservation_released_on_terminal_event` | passed |
| `test_no_illegal_transition_in_payout_logs` | passed |
| `test_webhook_on_cancelled_payout_is_noop` | passed |
| `test_stuck_initiated_payout_has_no_automated_repair` | passed |
| `test_reversal_row_committed_before_ledger_call_baseline` | passed |
| `test_reversal_survives_ledger_outage_via_async_retry` | passed |
| `test_failed_webhook_remaps_differently_by_account_type` | passed |
| `test_transaction_id_set_blocks_failed_transition` | passed |
| `test_other_merchant_cannot_fetch_or_cancel` | passed |
| `test_terminal_webhook_fires_exactly_once` | passed |
| `test_shield_timeout_fails_open` | passed |
| `test_pricing_500_rejects_payout` | passed |
| `test_queued_low_balance_payout_dequeues_after_topup_and_cron` | passed |
| `test_scheduled_payout_dequeues_after_slot_and_cron` | passed |

## Supplemental and offline checks

Final boot20 passed all six [route scenarios](runs/final-acceptance/route/route-results.json), all six [bank scenarios](runs/final-acceptance/bank/bank-effects.json), and the [audited Kong golden payout](runs/final-acceptance/golden/live-result.json). Each command has a passing same-window egress report in its result directory.

[SCENARIO_COVERAGE.md](SCENARIO_COVERAGE.md) records supplemental route/bank coverage separately from the original 26. Hold, ambiguous, duplicate and timeout cases make bounded nonterminal claims. Later return is explicitly manual FTS reconciliation. Actual HTTP requests/responses, persisted state snapshots, bank events and merchant receipts are preserved per scenario. Failed exploratory triggers remain in their original directories.

[Offline final checks](offline-final/results.json) passed 143 checks across safety lifecycle, migration admission, replay comparison, deterministic generation, bank/monolith/Splitz contracts, Explorer controls and Daytona gates. This count includes the tuple-returning database snapshot regression; the earlier focused record is [snapshot-contract-tests.txt](snapshot-contract-tests.txt). These checks supplement, rather than replace, the real-service runtime suite.

Sequential duplicate and terminal-delivery checks establish their observed scenario boundaries. They do not establish concurrent uniqueness for arbitrary product traffic, production retries, or unimplemented approval/reconciliation families. See [remaining limits](REMAINING_BLOCKERS.md).
