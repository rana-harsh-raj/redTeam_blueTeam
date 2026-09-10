# Payouts functional graph — node/edge schema (M6)

Every discovery lane writes `reports/domain/parts/<lane>.json` in this shape. The
merge step (`scripts/domain/build_graph.py`) unions parts, validates ids, and
produces `reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.json` + `.csv` + `.mmd`.

## Part file

```json
{
  "lane": "payouts-core",
  "generated_at": "2026-09-07T..Z",
  "sources": [{"repo": "razorpay/payouts", "sha": "4bf3dbf9...", "path": "/abs/clone/path"}],
  "nodes": [ {...node} ],
  "edges": [ {...edge} ],
  "families": [ {...family} ],
  "open_questions": ["..."]
}
```

## Node ids (stable, lowercase, no spaces)

| kind | id pattern | example |
|---|---|---|
| repository | `repo:<name>` | `repo:payouts` |
| service | `svc:<name>` | `svc:payouts-api`, `svc:fts-web`, `svc:workflows` |
| worker (queue/kafka/cron consumer process) | `worker:<svc>/<name>` | `worker:payouts/queued_payout` |
| cron | `cron:<svc>/<name>` | `cron:payouts/process_queued_payouts` |
| route (API entry point) | `route:<svc>/<METHOD> <path>` | `route:payouts/POST /v1/payouts` |
| event / message type | `event:<name>` | `event:payout.processed` |
| queue | `queue:<name>` | `queue:queued_payout` |
| topic | `topic:<name>` | `topic:fts-status-updates` |
| datastore | `db:<svc>/<engine>` | `db:payouts/mysql` |
| table / collection | `table:<svc>/<table>` | `table:payouts/payouts` |
| feature flag / experiment | `flag:<source>/<name>` | `flag:dcs/payout_workflows`, `flag:splitz/<exp>` |
| identity / credential class | `identity:<name>` | `identity:merchant-api-key`, `identity:service-basic-auth:workflow` |
| role | `role:<name>` | `role:finance_l1` |
| state | `state:<entity>/<name>` | `state:payout/pending` |
| external dependency | `ext:<name>` | `ext:bank-rbl`, `ext:fastcron` |
| business family | `family:<name>` | `family:bulk-payouts` |
| journey (executable path) | `journey:<family>/<name>` | `journey:bulk-payouts/success` |
| twin substitute | `sub:<name>` | `sub:workflow-engine` |

## Node fields

```json
{
  "id": "worker:payouts/queued_payout",
  "kind": "worker",
  "label": "queued_payout worker",
  "owner_domain": "payouts",             // payouts|fts|ledger|cfa|x-balances|banking-accounts|xas|workflows|batch|api-monolith|edge|stork|mozart|dcs|splitz|governor|shield|validx|ups|payout-links|vendor-payments|virtual-account|recon|platform
  "repo": "repo:payouts", "sha": "4bf3dbf9...",
  "source_refs": ["internal/job/process_queued_payout.go:16"],
  "entry_points": ["PAYOUTS_WORKER_NAME=queued_payout"],
  "identity_model": "service basic auth / passport / merchant key / none",
  "authorization": "short statement or null",
  "apis": ["route:..."], "events": ["event:..."],
  "tables": ["table:..."], "queues": ["queue:..."], "topics": ["topic:..."],
  "state_transitions": ["state:payout/queued -> state:payout/created"],
  "feature_flags": ["flag:..."],
  "external_deps": ["ext:..."],
  "build": "go build ./cmd/workers", "health": "GET :9400/status",
  "fixture_requirements": ["funded merchant", "fund account"],
  "criticality": "P0|P1|P2",
  "fidelity": "real_source_running|real_source_mapped_not_running|high_fidelity_replacement|behavioural_placeholder|graph_only|blocked_missing_access",
  "twin_ref": "compose service / substitute / file that implements it in the twin, or null",
  "fidelity_evidence": "why this classification (file/run reference)",
  "confidence": "confirmed|probable|inferred"
}
```

Only `id`, `kind`, `label`, `owner_domain`, `criticality`, `fidelity`, `confidence`
are mandatory; fill the rest when known. Unknown → `null`, never guessed.

## Edge fields

```json
{"from": "svc:payouts-api", "to": "svc:ledger-api", "type": "calls",
 "via": "Twirp Journal.Create", "identity": "identity:service-basic-auth:payouts",
 "source_refs": ["pkg/ledger/client.go:..."], "fidelity": "real_source_running",
 "confidence": "confirmed"}
```

Edge `type` ∈ `calls | consumes | produces | reads | writes | transitions | gated_by |
callback | schedules | owns | authenticates | depends_on | implements`.

## Family fields

```json
{"id": "family:bulk-payouts", "label": "Bulk / batch payouts", "priority": "P0",
 "description": "...", "entry_points": ["route:..."], "components": ["svc:batch", "..."],
 "variants": ["success","failure","pending","retry","duplicate","idempotency","cancel_or_reverse","concurrency","accounting","webhook","restart"],
 "journeys_existing": ["journey:..."], "fidelity": "...", "source_refs": ["..."]}
```

## Fidelity vocabulary (exactly these six)

- `real_source_running` — real binary built from the pinned SHA runs in the twin.
- `real_source_mapped_not_running` — source is readable and mapped, no runtime yet.
- `high_fidelity_replacement` — contract-faithful substitute validated against source.
- `behavioural_placeholder` — shape right, behaviour partly inferred.
- `graph_only` — inventoried, no runtime, no substitute.
- `blocked_missing_access` — source/artefact not readable by this identity.
