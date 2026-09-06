# M4 Direct end-to-end coverage (T17)

Generated 2026-09-06T20:35:51Z. git 8965ec25a2c4. arena 06330f7e.


Merchants (fresh each run): A=`ARENAD83881351` (balance `ARENADB3881351`, acct `2323000083881351`), B=`ARENAD86313421` (balance `ARENADB6313421`, acct `2323000086313421`).

Run evidence dir: `RED_LOOP/runs/m4-journeys-20260906T202456Z`


## Journeys A-G

| Journey | Result | Merchant | Fidelity | Checks |
|---|---|---|---|---|
| A Success (create->recon->DA ledger->webhook) | **PASS** | ARENAD83881351 | real payout/PS/FTS/ledger; real-worker statement; substitute xas matching | 12/12 |
| B Immediate failure (failed, no reversal, released, idempotent) | **PASS** | ARENAD86313421 | real | 7/7 |
| C Pending->success (poll1 PENDING, poll2 SUCCESS; recon once; idempotent) | **PASS** | ARENAD83881351 | real payout/PS/FTS/ledger; real-worker statement; substitute xas | 7/7 |
| D Pending->failure (failed, released, no shared reversal) | **PASS** | ARENAD86313421 | real | 7/7 |
| E Statement-first: FTS-direct guard refuses (R1) vs monolith-relay moves to failed (R2/F-T10-1) | **PASS** | ARENAD83881351 | real (VerifyPayoutFailedTransaction) + real-worker statement; substitute relay control | 3/3 |
| F Duplicate statement (no new row / no duplicate journal / no double release) | **PASS** | ARENAD83881351 | real-worker dedupe + real ledger dedup; substitute xas | 3/3 |
| G Conflict: credit->reversed (transaction_id) + wrong-amount->external (negative control) | **PASS** | ARENAD86313421 | real PS reversal + real ledger; credit stmt substitute_injected; substitute xas | 2/2 |

## Routes R1-R3 + status remap

| Route | Support | Actor / transport | Status mapping | Evidence |
|---|---|---|---|---|
| R1 FTS status webhook POST /v1/payouts/transfer_sta | **native** | FTS (fts service credential [auth.ft / HTTP (FTS -> payouts-api, di | failed->refused when transaction_id set (VerifyPayoutFailedTransaction | journey B; journey E |
| R2 FTS -> monolith-stub /update_fts_fund_transfer - | **native (substitute monolith-stub; route_profile=monolith)** | monolith relay (rzp_live basic; subs / HTTP relay | processed->processed; failed->failed (Direct rbl); no statement-first  | journey B |
| R3 Kafka topic rx-fts-status-update-events (FTS sta | **expected-failure** | payouts-kafka-fts-status-updates-con / Kafka (FTS producer -> broke | FAILED/REVERSED -> return nil, message Acked and DROPPED (no state cha | reports/implementation/KAFKA_ROUTE_EVIDENCE.md; ENV2_COMPOSE/verifier/kafka_scenarios.py |
| R-remap terminal status remap by account_type/channel | **native** | payouts-service status adapter (fts_ / n/a (mapping logic) | Direct (rbl) bank failed -> payout FAILED (no reversal); Shared (nodal | journeys B/D + V18 |

## Invariants (Phase 5 subset)

| Invariant | Result | Fidelity | Formal statement |
|---|---|---|---|
| I-Direct-failure-no-shared-reversal(G31) | **PASS** | real | A failed Direct (rbl) payout produces NO reversal row and NO Shared-style reversal journal; the payout stays failed. |
| I-Direct-success-no-duplicate-ledger(G54) | **PASS** | real | A processed Direct payout yields exactly two balanced DA journals (da_payout_processed + _recon); replaying the recon/payout_update path cre |
| I-reservation-not-released-twice(G53) | **PASS** | real | An in-flight reservation transitions live -> awaiting_balance_refresh -> released AT MOST ONCE; re-running the reconciler does not double-re |
| I-balance-not-decremented-twice(G28-adj) | **PASS** | real | A duplicate statement / replayed recon does not debit the merchant ledger balance a second time: no additional debit entries appear and the  |
| I-conservation(G51) | **PASS** | real (ledger) + modeled (reservation/balance conservation) | For a successful Direct payout the DA journal entries sum to zero (sum_debit==sum_credit per journal) AND the modeled money-state is conserv |
| I-reconciliation-correct-tuple(G26) | **PASS** | real-worker statement + real PS link; substitute xas decision | Statement->payout matching uses the correct tuple: same merchant_id, account_number, currency, amount and statement-detail; the wrong-amount |

Full invariant evidence (setup/action/expected/negative-control/evidence): `reports/implementation/m4-invariant-results.json`.

## Fidelity legend

- **real** = accepted service binary executed the step on a real route.
- **real-worker/substitute-bank** = REAL rbl_banking_account_statement worker fed by mozart-sim + bankingaccounts-stub.
- **substitute** = a contract-faithful stub executed it (xas-sim matching, monolith-stub relay/DA emitter).
- **substitute_injected** = the harness wrote the row/event in the exact shape the real producer would have.
- **expected-failure** = preserved production-source behaviour (Kafka FAILED/REVERSED drop, EF-002/003).
