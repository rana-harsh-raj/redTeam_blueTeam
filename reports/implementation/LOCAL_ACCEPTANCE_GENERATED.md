# Local acceptance (generated)

Produced by `ENV2_COMPOSE/scripts/local-acceptance.py` from the artifacts named in `reports/implementation/acceptance-manifest.json`. Every boolean below is computed; none is attested.

**Status: PASSED**

Current tree config digest `afb1aa02eb3112e9` over 122 hashed input files.

## Required checks

| Check | Result | Reason |
|---|---|---|
| `fresh_build` | PASS | - |
| `empty_volume_boot` | PASS | - |
| `runtime_endpoint_audit` | PASS | - |
| `secret_scan` | PASS | - |
| `egress_same_window` | PASS | - |
| `verifier_suite` | PASS | - |
| `logical_replay` | PASS | - |
| `route_fidelity` | PASS | - |
| `resource_profile` | PASS | - |
| `explorer_saved_and_live` | PASS | - |
| `staleness` | PASS | - |
| `declared_deviations_present` | PASS | - |
| `fixed_twin_defects_recorded` | PASS | - |
| `no_unexplained_results` | PASS | - |

## Route fidelity

| Profile | Scenario | Transport | State | Expected failure | Fidelity |
|---|---|---|---|---|---|
| direct | golden | observed | passed | - | PASS |
| direct-create-monolith-status | golden | observed | passed | - | PASS |
| kafka | direct_after_shared | observed | passed | - | PASS |
| kafka | failed_dropped_direct | observed | expected_failure | EF-002 | PASS |
| kafka | failed_dropped_shared | observed | expected_failure | EF-002 | PASS |
| kafka | golden-shared | observed | failed | EF-001 | PASS |
| kafka | reversed_dropped_direct | observed | expected_failure | EF-003 | PASS |
| monolith | ambiguous_with_utr | observed | passed | - | PASS |
| monolith | ambiguous_without_utr | observed | passed | - | PASS |
| monolith | delayed_success | observed | passed | - | PASS |
| monolith | direct_success | observed | passed | - | PASS |
| monolith | duplicate | observed | passed | - | PASS |
| monolith | failed_direct | observed | passed | - | PASS |
| monolith | failed_shared | observed | passed | - | PASS |
| monolith | golden | observed | passed | - | PASS |
| monolith | hold | observed | passed | - | PASS |
| monolith | queued | observed | passed | - | PASS |
| monolith | returned | observed | passed | - | PASS |
| monolith | scheduled | observed | passed | - | PASS |
| monolith | timeout | observed | passed | - | PASS |

## Product route health (informational)

| Profile | Scenario | Business completed | Citation |
|---|---|---|---|
| direct | golden | yes | - |
| direct-create-monolith-status | golden | yes | - |
| kafka | direct_after_shared | yes | - |
| kafka | failed_dropped_direct | NO | EF-002 |
| kafka | failed_dropped_shared | NO | EF-002 |
| kafka | golden-shared | NO | EF-001 |
| kafka | reversed_dropped_direct | NO | EF-003 |
| monolith | ambiguous_with_utr | yes | - |
| monolith | ambiguous_without_utr | yes | - |
| monolith | delayed_success | yes | - |
| monolith | direct_success | yes | - |
| monolith | duplicate | yes | - |
| monolith | failed_direct | yes | - |
| monolith | failed_shared | yes | - |
| monolith | golden | yes | - |
| monolith | hold | yes | - |
| monolith | queued | yes | - |
| monolith | returned | yes | - |
| monolith | scheduled | yes | - |
| monolith | timeout | yes | - |

## Expected failures matched

- **EF-001** MATCHED - Shared-account PROCESSED over Kafka cannot enqueue the processed Ledger journal (kafka/golden-shared)
- **EF-002** MATCHED - FTS FAILED delivered over Kafka is acknowledged and dropped (kafka/failed_dropped_direct, kafka/failed_dropped_shared)
- **EF-003** MATCHED - FTS REVERSED delivered over Kafka is acknowledged and dropped (kafka/reversed_dropped_direct)

## Declared deviations

76 entries from `ENV2_COMPOSE/config/declared-deviations.yaml`.

## Fixed twin defects

- **FD-001** payouts [kafka_producer].Brokers carried the upstream dummy, crashing the Kafka consumer on any failed message (ENV2_COMPOSE/config/templates/base/payouts/arena.toml (rendered to generated/payouts/arena.toml:460); ENV2_COMPOSE/config/routes.py:58-63 rewrites only the FTS producer)
- **FD-002** ledger-gate stripped idempotency-key and the SDK correlation headers on the Payouts-to-Ledger leg (ENV2_COMPOSE/substitutes/ledger-gate/server.py:15)
- **FD-003** Generated arena passwords could start with '-' and were then parsed as CLI options by the CFA seed step (ENV2_COMPOSE/secrets/gen-secrets.sh rand_password(); consumer ENV2_COMPOSE/scripts/up.sh:94-95 (mongosh --password "$MONGO_CFA_ROOT_PW"))

## Unexplained results

None.

## Blockers

None.

139 evidence files are hash-bound in the JSON gate.
