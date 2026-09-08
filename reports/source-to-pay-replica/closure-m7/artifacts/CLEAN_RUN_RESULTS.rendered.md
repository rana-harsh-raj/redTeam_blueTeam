# Clean-run results

Evaluated commit: `d87591000f359f1a58fc805c9667d84dc1871d09`. These results are recomputed from raw observations by `scripts/acceptance.sh`.

| Run | Reset (s) | Clean build (s) | Boot (s) | Journey (s) | Independent result | Fresh final checkout |
|---|---:|---:|---:|---:|---|---|
| m7-iso-5 | 0.071 | 78.855 | 11.452 | 3.488 | PASS | False |
| m7-iso-6 | 10.299 | 87.902 | 15.773 | 4.616 | PASS | False |
| m7-iso-7 | 0.131 | 91.907 | 15.297 | 3.541 | PASS | True |

Each run removes only its own project containers and named volumes, verifies their absence, rebuilds all three source repositories in fresh staging, builds the source runtime image, boots, and proves all business tables and the boundary are empty before seeding. Three broker records are then observed at offsets 0, 1, 2.

Initial fixtures use seed `s2p-golden-20260820-001`. Source-generated tax, TDS, payout and idempotency IDs and some wall-clock timestamps vary. The normalized outcome is one record of 12,500, one payout association, a tagged originating payout, and `processing/money_loading_success`. This is deterministic business replay, not byte-identical runtime output.

Per-run source SHAs, image IDs, config and fixture hashes, command exit codes, empty-state snapshots, raw broker/SQL/API/boundary evidence and logs are under [clean-runs](../../DOMAIN_REPLICAS/source_to_pay/artifacts/clean-runs). The outer [artifact manifest](../../DOMAIN_REPLICAS/source_to_pay/artifacts/artifact-hashes.json) binds each run envelope and this report.
