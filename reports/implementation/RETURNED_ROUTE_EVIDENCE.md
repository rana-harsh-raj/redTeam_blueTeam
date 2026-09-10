# Later bank return through explicit FTS reconciliation

Verified run: `runs/returned-monolith2/route-results.json`, with its complete per-payout `scenario_snapshot` in `route-returned.jsonl`. Result passed; egress audit passed with command exit 0. The payout first reached processed in Payouts and FTS, with balanced initiated/processed journals and a signed processed merchant event. The subsequent return reached reversed in both services, with a balanced `payout_reversed` journal and one valid signed reversed merchant event.

This is an explicit admin reconciliation flow. It does not establish automatic post-terminal polling or natural reconciliation cadence.

## Pinned FTS source contract

FTS commit: `2a09e763116db47a2f28553c677ad73ef8133ebf`. Paths below are within that repository.

| Source | Verified behavior |
|---|---|
| `internal/transfer/service.go:1031` | Ordinary `POST /v1/transfer/{id}/check` accepts only an INITIATED attempt. A processed attempt returns HTTP 400 with internal ILLEGAL_STATE. The scenario asserts this limitation. |
| `internal/routing/router/route_list.go:175` | `/v1/attempts` is guarded by API/ART caller authentication; PS caller authentication is insufficient. Arena uses its existing generated `api_monolith` credential, whose mapping is in `config/generate.py`. |
| `internal/transfer/service.go:897` and `:1671` | `POST /v1/attempts/verify` with `attempt_ids` reads raw bank status regardless of the attempt's terminal state, without updating the database. The runner requires actual RETURNED, BBANK classification and a nonempty return UTR, and confirms persisted states remain processed. |
| `internal/providers/mozart/error_code.go:438` | RETURNED is classified failed with error type BBANK. This classification is used by the safe-update bank check. |
| `internal/transfer/manual_update_handlers_factory.go:84` | Safe manual update explicitly supports PROCESSED→REVERSED. Its handler first verifies bank status and allows the transition only when that response is classified failed; processed or pending bank responses block it. |
| `internal/transfer/manual_update_handlers.go:83` and `:225` | The worker performs an independent bank status request, fills the observed admin metadata and invokes the real attempt/transfer state-machine transaction. |
| `internal/transfer/service.go:891` | `PATCH /v1/attempts/safe_update` queues the ordinary ProcessAttemptUpdate worker. HTTP success is an enqueue acknowledgment; the runner separately waits for persisted convergence. |
| `internal/transfer/attempt_processor.go:2987` | The real update path queues the transfer status webhook after its state processing. The arena does not inject a Payouts terminal callback. |

The update body is keyed by the actual FTS attempt ID and supplies observed `bank_status_code`, observed `return_utr`, an explicit synthetic reconciliation remark and `meta.reversed=true`. It omits `status`: `common.Fill` otherwise copies that optional field before the state-machine event, turning the source state into REVERSED prematurely.

## Retained diagnostic attempts

- `runs/routes-monolith2`: Direct/current-account success, queued and scheduled terminal cases passed. Returned failed because the runner used ordinary transfer check after processed; the ILLEGAL_STATE response was correct core behavior.
- `runs/returned-monolith`: raw verification and the queued safe-update worker both reached the bank. The optional request status field caused an illegal in-memory REVERSED→REVERSED event; the database transaction rolled back and persisted states remained processed.
- `runs/returned-monolith2`: corrected source-supported request passed, with two actual bank status observations, committed reversal, Ledger journal and signed merchant delivery. No core source or persisted-state fixture mutation was used for the return.
