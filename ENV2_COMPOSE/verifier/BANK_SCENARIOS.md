# Additional live bank scenarios

`bank_scenarios.py` runs six scenarios separately from the original 26 pytest checks:

| Scenario | Payouts / FTS expectation | Ledger / merchant webhook expectation |
|---|---|---|
| `hold` | initiated / INITIATED; bank code INITIATED | One balanced initiated journal; exact amount-plus-fee debit remains held; no terminal webhook during observation. |
| `delayed_success` | Pending at init and first bank status check; processed after second real FTS status-check request | Exactly initiated + processed balanced journals; one valid HMAC merchant processed webhook. |
| `ambiguous_with_utr` | initiated / INITIATED; actual FTS attempt preserves UTR and classifies synthetic unmapped error as pending | Debit remains held; no terminal journal or webhook during observation. |
| `ambiguous_without_utr` | Same pending state, with no attempt UTR | Same held-debit/no-terminal assertions. |
| `duplicate` | `DUPLICATE_TXN` is pending (PBANK), not a failed or processed payout | Same held-debit/no-terminal assertions. |
| `timeout` | Delayed HTTP 504 without a valid Mozart envelope becomes pending `MOZART_INDETERMINATE` | Same held-debit/no-terminal assertions. This is a gateway-timeout response, not proof that the FTS 180-second HTTP-client deadline fired. |

The runner uses the `monolith` route profile and a dedicated generated Shared M1 merchant (`scenario:invalid_beneficiary` by default). It records the exact starting balance and existing payout rows before each case, uses a unique payout identity, and filters deliveries by payout ID. It does not mutate Payouts/FTS/Ledger state or inject a terminal callback. The bank stimulus is controlled by merchant/actual attempt; the real FTS workers perform the transition. A nonterminal success means the stated invariant held for the bounded observation; it does not claim eventual settlement.

Run inside the existing verifier service so all mounted synthetic credentials and dependencies match the original suite. From `ENV2_COMPOSE`, after the audited 26-test suite finishes:

```sh
docker compose --env-file .env.arena -f docker-compose.yml --profile verify run --rm --no-deps \
  --entrypoint python3 -v /absolute/path/to/results:/results \
  verifier /verifier/bank_scenarios.py --output /results/bank-effects.json
```

For acceptance, wrap that same command in the project's egress/resource audit launcher; an unaudited command alone is not the final acceptance artifact. No golden merchant key is required: this runner uses the existing post-Kong PS service credential and minted merchant passport, while the Explorer golden separately exercises actual merchant API-key ingress.

Options:

- `--scenarios hold,delayed_success,...` selects any of the six names.
- `--namespace scenario:invalid_beneficiary` chooses a generated namespace; it must contain Shared M1 and use the monolith profile.
- `--observation-seconds 5` controls a bounded nonterminal observation (1–60).
- `--scenario-timeout 150` sets a per-case deadline (60–300 seconds).
- `--output /results/bank-effects.json` writes aggregate results. Each case also produces `bank-<name>.jsonl` with HTTP and DB evidence plus a bounded completion snapshot containing every persisted attempt, payout transition logs, reversal rows, Ledger journals and entries, linked accounts, bank events and merchant deliveries. Queue messages remain explicitly unobserved; snapshot read errors fail the scenario.

Failures remain failures with ending-state evidence. Missing fixtures, unrecognized states, wrong bank classification, missing journals or delivery errors are not skipped. Run against a freshly seeded dedicated namespace for comparable acceptance results.

Source anchors: FTS `internal/providers/mozart/provider.go` (`createResponseStruct`, `createEmptyResponse`, `fillMeta`), `internal/providers/mozart/error_code.go` (DUPLICATE_TXN, MOZART_INDETERMINATE), and `internal/transfer/attempt_processor.go` (`handleStateTransitionForAttemptUpdate`). Exact source commits are recorded in fixture provenance.

## Verified supplemental evidence

`reports/implementation/runs/bank-monolith` records all six bank cases passed with egress audit passed and command exit 0. The snapshots record the actual starting balances and existing payouts; these supplemental cases ran after the clean 26-test replay rather than claiming a new empty arena per case.

The separate `route_scenarios.py --case returned` covers a later bank return after processed. Its successful audited evidence is `reports/implementation/runs/returned-monolith2`. FTS ordinary transfer checks reject processed attempts. The runner instead calls the source-supported admin raw verification route, observes RETURNED/BBANK and the actual return UTR, then calls `safe_update`, whose worker independently verifies the bank before committing PROCESSED→REVERSED. It checks both actual bank status calls, FTS/Payouts reversal, the balanced reversal journal and signed reversed webhook. This is explicit manual reconciliation; it does not claim automatic return polling. Exact source anchors and retained failed-run explanations are in `reports/implementation/RETURNED_ROUTE_EVIDENCE.md`.
