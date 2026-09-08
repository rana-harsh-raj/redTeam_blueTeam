# Clean-run results

Evaluated commit: `b6e4976f9baebfe770dfa30794f6f61ea08ccb1b`. These results are recomputed from raw observations by `scripts/acceptance.sh`.

| Run | Reset (s) | Clean build (s) | Boot (s) | Journey (s) | Independent result | Fresh final checkout |
|---|---:|---:|---:|---:|---|---|
| final-1 | 10.927 | 95.622 | 14.837 | 3.905 | PASS | False |
| final-2 | 10.573 | 96.769 | 12.071 | 3.999 | PASS | False |
| final-4 | 0.597 | 192.107 | 15.481 | 4.013 | PASS | True |

Each run removes only its own project containers and named volumes, verifies their absence, rebuilds all three source repositories in fresh staging, builds the source runtime image, boots, and proves all business tables and the boundary are empty before seeding. Three broker records are then observed at offsets 0, 1, 2.

Initial fixtures use seed `s2p-golden-20260820-001`. Source-generated tax, TDS, payout and idempotency IDs and some wall-clock timestamps vary. The normalized outcome is one record of 12,500, one payout association, a tagged originating payout, and `processing/money_loading_success`. This is deterministic business replay, not byte-identical runtime output.

Per-run source SHAs, image IDs, config and fixture hashes, command exit codes, empty-state snapshots, raw broker/SQL/API/boundary evidence and logs are under [clean-runs](../../DOMAIN_REPLICAS/source_to_pay/artifacts/clean-runs). The outer [artifact manifest](../../DOMAIN_REPLICAS/source_to_pay/artifacts/artifact-hashes.json) binds each run envelope and this report.
