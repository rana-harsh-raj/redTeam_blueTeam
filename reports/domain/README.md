# reports/domain — the Payouts functional graph (M6)

| artefact | produced by | what it is |
|---|---|---|
| `SCHEMA.md` | hand-written | node/edge/family schema and the six fidelity classes every lane uses |
| `parts/<lane>.json` + `.md` | discovery lanes (payouts-core, fts-mozart, money-statements, identity-ingress, external-config-deploy, twin-inventory) | per-lane source-derived nodes/edges/families with file:line refs |
| `PAYOUTS_FUNCTIONAL_GRAPH.json/.csv/.mmd/.graphml` | `scripts/domain/build_graph.py` | merged, validated graph (lane precedence: twin-inventory wins on runtime fidelity) |
| `GRAPH_STATS.json` | `build_graph.py` | coverage: critical-component representation %, P0 family journey coverage, fidelity histogram, blocked nodes |
| `FIDELITY_CLASSIFICATION.csv` | `build_graph.py` | every node → real_source_running / real_source_mapped_not_running / high_fidelity_replacement / behavioural_placeholder / graph_only / blocked_missing_access |
| `REPOSITORY_INVENTORY_M6.csv` | `scripts/domain/inventory.py` | every reachable repository (SHA, date, role) + deployment artefacts + blocked repos |
| `REMAINING_ACCESS_MANIFEST.md` | `scripts/domain/inventory.py` | the items preventing the final ~1% (blocked repos, blocked graph nodes, runtime values) |
| `recipes/<service>.yaml` + `SNAPSHOT_MANIFEST.json` | `scripts/snapshot/recipes.py` | snapshot-aware service recipes + domain snapshot manifest |
| `snapshots/<UTC>.json`, `refresh/<UTC>-*.json` | `scripts/snapshot/{capture,refresh}.py` | captured snapshots and daily/weekly refresh plans |

Rebuild everything from parts:

```bash
python3 scripts/domain/build_graph.py --strict     # graph + stats + classification
python3 scripts/domain/inventory.py                # inventory + remaining-access manifest
python3 RED_LOOP/surface/m6_acceptance.py          # the M6 gates
```
