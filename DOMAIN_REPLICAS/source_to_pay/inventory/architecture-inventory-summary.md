# Architecture inventory summary

Detected denominator: **22116** observations; represented: **22116** (100.0%).

> This is representation coverage of detected observations, not proof of extraction or production completeness.

## Detector audit

Tracked files: 5636; eligible: 4447; corroborating test/fixture/mock files: 1053; excluded: 136; unreadable: 0; eligible residuals: 2226.
Corroborating observations: 8203.

## Category counts

- `build_or_deployment_input`: 108
- `config_definition`: 5457
- `config_member_access`: 1172
- `config_or_flag`: 4
- `database_schema`: 62
- `executable_entrypoint`: 83
- `generated_rpc_artifact`: 348
- `health_or_startup`: 1127
- `http_route`: 480
- `http_route_definition`: 97
- `kafka_or_queue`: 813
- `message_contract`: 155
- `migration_artifact`: 139
- `migration_index`: 213
- `migration_sql`: 250
- `observability`: 3094
- `orm_model`: 134
- `retry_or_idempotency`: 1908
- `rpc_interface`: 88
- `service_client`: 881
- `sql_target`: 565
- `state_transition`: 1594
- `storage_cache_notification`: 3203
- `worker_or_job`: 26
- `worker_registration`: 115

## Known blind spots

- Regex detectors cannot prove semantic completeness or detect dynamically constructed registrations.
- Configuration structs and indirect read chains are only partially captured; config definitions are broad and require semantic review.
- Generated RPC files are included only when --generated-root is supplied, and their implementation bodies are intentionally not interpreted.
- Go dependency edges cover literal imports that resolve to one of the three pinned repository modules; dynamic and external dependencies remain outside this graph.
- Callers and consumers require semantic or runtime analysis and remain empty unless separately curated.
- Test, fixture, and mock observations are retained as corroborating evidence but excluded from the material executable denominator.
- A 100% representation percentage means every detected denominator item has an evidence-backed graph representation; it does not mean all production architecture was discovered.
