# Route coverage from retained runtime evidence

This report separates observed transport from state, accounting and delivery acceptance. Account type and fixture namespace do not select or prove transport. The real Payouts/FTS/Ledger/CFA/x-balances binaries run against explicitly bounded substitutes. [Source contracts](ROUTE_SOURCE_EVIDENCE.md) and the [fidelity matrix](FIDELITY_IMPLEMENTATION_MATRIX.csv) describe that boundary.

The final original suite passed on two clean runs: replay19 and replay20 each recorded 26 passed, zero failed and zero skipped. The [strict logical comparison](logical-replay-final.json) passed with both inputs valid and no differences. The [replay19 audit](runs/replay19/full/egress.json) and [replay20 audit](runs/replay20/full/egress.json) both passed with exit 0 and zero outside packets. Earlier failed comparisons remain retained; this acceptance covers the tested observations and source-backed normalization rules.

## Transport profiles

| Route / account | Observed hops | Acceptance |
|---|---|---|
| Monolith create and return / Shared | Payouts → monolith → FTS → monolith → Payouts | [Final boot20 golden passed](runs/final-acceptance/golden/live-result.json): processed states, balanced initiated/processed journals and one signed terminal receipt. [Audit passed](runs/final-acceptance/golden/egress.json), exit 0. |
| Direct create and direct HTTP return / Shared | [Direct2 capture](runs/profile-direct2/golden/route-evidence.json): direct origin metadata and actual FTS HTTP callback; no monolith create/status | [Passed](runs/profile-direct2/golden/live-result.json): processed states, two balanced journals and one signed terminal receipt. [Audit](runs/profile-direct2/golden/egress.json): passed, exit 0, zero outside packets. |
| Direct create and monolith return / Shared | [Mixed2 capture](runs/profile-mixed2/golden/route-evidence.json): actual direct-create response plus monolith status relay | [Passed](runs/profile-mixed2/golden/live-result.json): processed states, two balanced journals and one signed terminal receipt. [Audit](runs/profile-mixed2/golden/egress.json): passed, exit 0, zero outside packets. |
| Direct create and raw Kafka return / Shared | [Kafka capture](runs/profile-kafka/golden/route-evidence.json): actual INITIATED and PROCESSED messages received by the real consumer | [Failed](runs/profile-kafka/golden/live-result.json): FTS PROCESSED, Payouts initiated, no processed journal. Missing job.RegisterJobs() in Kafka bootstrap prevents Shared Ledger enqueue. |
| Direct create and raw Kafka return / Direct account, fresh Direct-first run | [Kafka2 capture](runs/profile-kafka2/direct-account/route-evidence.json): raw Kafka received, direct create observed, no direct HTTP/monolith status route | [Passed](runs/profile-kafka2/direct-account/route-results.json): processed states, one signed terminal receipt, no payout Ledger journals. [Audit](runs/profile-kafka2/direct-account/egress.json): passed, exit 0, zero outside packets. Auxiliary dual-write still logs unregistered-job failures. |

[Kafka evidence](KAFKA_ROUTE_EVIDENCE.md) gives the exact source chain and timestamps. The Shared immediate blocker occurs before queue transport. Raw FTS fields also disagree with the internal consumer DTO, but the downstream Ledger consequence was not reached. Both consumers ignore FAILED/REVERSED. Direct success therefore does not establish Shared accounting, failure/reversal handling or successful auxiliary dual-write.

Earlier [Direct failure](runs/profile-direct/golden/live-result.json), [mixed failure](runs/profile-direct-create-monolith-status/golden/live-result.json) and [Direct-after-Shared Kafka timeout](runs/profile-kafka/direct-account/route-results.json) remain retained. The Direct fixture originally supplied API-valid plural savings, which FTS rejects; the corrected baseline uses API-valid null and Payouts' existing saving default. Mixed originally forwarded unsupported narration into the strict details DTO; the substitute now filters it and has a regression. The first Direct Kafka timeout followed the failing Shared message and had no matching consumer observation in its bounded wait; blocking/backoff is a possibility, not an established diagnosis.

## Final six route scenarios

All six pass under monolith transport in [final boot20 route results](runs/final-acceptance/route/route-results.json), with complete snapshots and [audit passed, exit 0, zero outside packets](runs/final-acceptance/route/egress.json). The [earlier current-boot results](runs/final-current-boot/route/route-results.json) and [audit](runs/final-current-boot/route/egress.json) also passed. The [earlier six-case results](runs/final-supplemental/route/route-results.json) and [earlier audit](runs/final-supplemental/route/egress.json) remain retained.

| Case | Observable result | Boundary |
|---|---|---|
| direct_success | FTS/Payouts processed, exactly one signed processed receipt, no payout Ledger journal | Direct account under monolith transport; not statement/BAS accounting. |
| scheduled | Processed states, balanced initiated/processed journals, one signed processed receipt | Valid slot plus recorded scheduled_at SQL fixture and explicit cron; not wall-clock cadence coverage. |
| queued | Real Ledger top-up, API mirror/event, processed states, balanced journals, one signed processed receipt | Mirror control reproduces selected side effects; it is not the production journal consumer. |
| returned | Initial success then FTS/Payouts reversed, balanced reversal journal, one signed reversed receipt | Explicit real FTS admin verification and safe update with independent bank verification. No automatic return polling claim. |
| failed_direct | One actual INVALID_ACCOUNT_NUMBER/FAILED attempt, FTS FAILED, Payouts failed, no reversal or payout journal, one signed failed receipt | Immediate bank decline on Direct account. |
| failed_shared | One actual INVALID_ACCOUNT_NUMBER/FAILED attempt, FTS FAILED, Payouts reversed, balanced initiated/failed journals, linked reversal, exact restored Ledger balance, one signed reversed receipt | Immediate bank decline on Shared account. |

The [earlier routes-monolith2 command](runs/routes-monolith2/route-results.json) passed Direct/scheduled/queued but failed its original returned trigger; its [audit](runs/routes-monolith2/egress.json) passed with command exit 1. The [corrected standalone return](runs/returned-monolith2/route-results.json) then passed. These results are not rewritten by the final six-case success. [Return source evidence](RETURNED_ROUTE_EVIDENCE.md) distinguishes ordinary status checking from manual reconciliation.

## Final six bank scenarios

All six pass in [final boot20 bank effects](runs/final-acceptance/bank/bank-effects.json), with [audit passed, exit 0, zero outside packets](runs/final-acceptance/bank/egress.json). The [earlier current-boot effects](runs/final-current-boot/bank/bank-effects.json) and [audit](runs/final-current-boot/bank/egress.json) also passed. The [earlier six-case effects](runs/final-supplemental/bank/bank-effects.json) and [earlier audit](runs/final-supplemental/bank/egress.json) remain retained.

| Bank outcome | Assertion |
|---|---|
| Hold | Pending FTS/Payouts, exact debit held, one balanced initiated journal, no reversal or terminal receipt during bounded observation. |
| Delayed success | Two actual status checks progress pending to processed, two balanced journals and exactly one signed processed receipt. |
| Ambiguous with UTR | UTR retained and pending classification, exact debit held, no reversal/terminal accounting or delivery during observation. |
| Ambiguous without UTR | No UTR and pending classification, exact debit held, no terminal outcome claimed. |
| Duplicate bank result | Actual DUPLICATE_TXN classification remains pending with held debit; separate from source-identity create dedupe. |
| Gateway timeout | Bank simulator waits 30 seconds then returns HTTP 504 without a valid Mozart envelope; real FTS classifies MOZART_INDETERMINATE and remains pending. FTS's 180-second client deadline did not expire. |

Per-case traces retain actual requests, bank events, Payouts/FTS/Ledger snapshots and merchant receipts. [The earlier six-bank run](runs/bank-monolith/bank-effects.json) also remains available. Generic bank scenarios do not establish the full gateway/version/classification matrix.

## Verification limits and unknown routes

The 26 original verifiers include adapter-boundary injections and controlled faults. V16 establishes a bounded lost-relay observation, V17 actual Ledger outage/recovery, and V21 ordered details/terminal stimuli plus duplicate suppression. V21 now reads receipts for its own merchant and payout; the read-only sink filter preserves duplicates and full global capture, as verified by the [scope checks](sink-scoping-check.json). These are not 26 complete transport proofs. Relay delay/reorder convergence and Stork outage/retry/disable lifecycle remain without full live acceptance.

Workflow approval/expiry, dashboard OTP/roles, full Edge/AuthZ and internal-application identity, Batch chunk/retry/approval, UPS/VPA validation, general XAS statement/BAS/ART reconciliation, automatic return detection and broad bank/channel/version failover remain missing or unverified. Passing manual FTS return reconciliation does not cover all admin repair. Production feature assignments, pricing breadth, queue/DLQ policy and recurring schedules need the precise artifacts listed in [remaining blockers](REMAINING_BLOCKERS.md).

V6's replay7 journal appeared on the ordinary retry at about 31 seconds, after its original 20-second deadline. [Scoped observation](runs/replay7/v6-retry-observation.json) preserves the mutex collision and successful retry. The focused wait now budgets one source-configured 30-second retry plus the original 20-second settlement allowance; no source behavior is patched. The earlier supplemental traces retain their original passing results; all twelve also passed again on final boot20. All dispatched hold verifiers now await the actual persisted held-bank response before fixture cleanup; Payouts scenarios also require the actual FTS transfer ID and matching channel. V14/V22 and V16 first exposed the earlier handoff boundary. Immediate assertions retain their timing, and the completion barriers preserve material state and transition fields. [Systematic repair evidence](VERIFIER_REPAIR_NOTES.md) records the source and bounded checks; replay19/20 both passed all 26 checks and compare equally under the strict logical comparison.

Final original-suite results, clean replay equivalence and aggregate acceptance are owned by [verifier results](VERIFIER_RESULTS.md), [current logical comparison](logical-replay-final.json), [comparison history and source rationale](LOGICAL_REPLAY_REVIEW.md) and [aggregate acceptance](local-acceptance.json). The final boot20 six-route, six-bank and golden reruns all passed, each with an audit pass, exit 0 and zero outside packets. Aggregate safety acceptance is recorded separately; these route results do not establish the unimplemented or source-blocked families.
