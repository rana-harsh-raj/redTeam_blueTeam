# M4 known limits

| ID | Limit | Class | Evidence |
|---|---|---|---|
| L-001 | Only one arena at a time on this host (11.65 GiB Docker memory). | environment | `docker info` MemTotal=12513980416 |
| L-002 | `xas-sink` at M3.1 is a health-only placeholder; no BAS/XAS ingestion exists in the twin yet. | substitute gap (to be closed in M4) | ENV2_COMPOSE/substitutes/xas-sink/CONTRACT.md |
