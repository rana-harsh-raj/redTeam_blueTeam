# M4 decision log

| ID | Date | Decision | Rationale / evidence |
|---|---|---|---|
| D-001 | 2026-09-06 | Branch `milestone-4-direct-reconciliation` created from `red-loop-m3.1` (3a044f8). No push; no rewrite of M3.1 tag or evidence. | `git rev-parse red-loop-m3.1^{}` = 3a044f8; tree clean at branch time. |
| D-002 | 2026-09-06 | Execution contexts (G63, Phase 7) are per-merchant namespaces inside ONE arena (separate merchants, webhook-sink namespaces, artifact dirs, correlation IDs, Kafka key/SQS message namespaces), not concurrent arenas. | Docker Desktop memory 11.65 GiB; one 66-container arena ≈ host capacity (M3.1 evidence). Namespace isolation will be *proven* per store, not assumed; contamination checks will be explicit. |
| D-003 | 2026-09-06 | Clean-boot reverification is sequential (stop live arena → boot disposable instance → teardown → restart live), reusing `ENV2_COMPOSE/scripts/clean-boot.sh`. | Same RAM constraint. |
