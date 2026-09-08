# M8 — Immutable Architecture Snapshot and Query Service (final report)

Branch `milestone-8-snapshot-query` from tag `twin-m7-shared-ingress-integration` (37ac47d). Everything in this milestone is repository-only: no docker, no clones, no secrets, no arena change. Machine acceptance: `reports/implementation/M8_ACCEPTANCE.json` (20 gates, evaluator `archkit/acceptance.py`).

## Result

| Item | Value |
|---|---|
| Architecture snapshot id | `5b6a5dade17eba6c0a407d3d4ba8423c8bf0b8ebeb9ea2bc3706483e04e8d141` |
| Store | `reports/architecture/snapshots/5b6a5dade17e/` (snapshot.json 3.7 MB canonical JSON; summary, manifest, recipes/, imports/m7/) |
| Source lock id | `6f1f77748e9260b12ab82d61a842bd5a46b123d67526c0694b5731ba44a98f61` (86 repositories; 27 with an explicit UNKNOWN sha) |
| Canonical graph | 3065 nodes / 5267 edges / 45 families / 202 journey definitions (11 lanes; the runtime overlay lane replaced by a compose-definition lane) |
| Source-to-Pay namespace | 930 projected nodes in the canonical graph; full detail graph 27994 nodes / 34744 edges referenced by sha256 `a16269693289…`, loaded only by `s2p_detail` |
| Recipes | 98/98 canonical services (`recipe_set_id` `422d2535e31c…`): core-real-binary 67, datastore-or-infra 9, source-to-pay 4, substitute 18 |
| Production unknowns | 14 consolidated (PU-1..PU-10 + 4 Source-to-Pay ids), each with `origin.file` / `original_id`; 81 nodes affected |
| M7 import | node ids identical; 0 edges lost (1 added: batch-sim `implements` svc:batch from its CONTRACT); 10 fidelity deltas and 49 label deltas, every one classified (below); M7 files unchanged (sha-bound) |
| Runtime / evidence / acceptance | RuntimeInstance `10d78b6bfce4…` (boot `6cdf3d73-c39c-4ff7-aafb-856774ed2fd0`), EvidenceBundle `76907ee5d6e1…` (143 files, 110 PASS / 1 EXPECTED_FAILURE), AcceptanceRecord `057671330877…` (M7 22/22) |

## Coverage (every measure carries numerator, denominator and population)

| Measure | Value | Definition |
|---|---|---|
| actual_source_defined | 242/525 (46.1%) — population `p0_non_journey` | label ACTUAL_SOURCE_RUNNING (static semantics: defined, not observed) |
| behavioral_stub | 20/525 (3.8%) — population `p0_non_journey` | label BEHAVIORAL_STUB |
| contract_faithful_replacement | 141/525 (26.9%) — population `p0_non_journey` | label CONTRACT_FAITHFUL_REPLACEMENT |
| executable_defined | 259/364 (71.2%) — population `p0_critical_kinds` | P0 critical-kind nodes whose fidelity is real_source_running (defined real binary) or high_fidelity_replacement |
| mapping | 364/364 (100.0%) — population `p0_critical_kinds` | P0 critical-kind nodes carrying source refs, entry points or a twin_ref and not blocked |
| nodes_affected_by_production_unknowns | 81/3065 (2.6%) — population `all_nodes` | nodes named in a ProductionUnknown.affects |
| p0_families_with_journey_definition | 26/26 (100.0%) — population `p0_families` | P0 families with at least one journey definition (own or proves_families) |

Populations: `all_nodes` = 3065 (every node of the static functional graph); `journey_definitions` = 202 (journey nodes (definitions, no results)); `p0_critical_kinds` = 364 (P0 nodes of kind service,worker,identity,datastore,table,queue,topic,cron,route,substitute (scripts/domain/build_graph.py stats)); `p0_families` = 26 (families with priority P0); `p0_non_journey` = 525 (P0 nodes of any kind except journey and family (M7 canonical_snapshot.py p0 population)).

Label histogram by population:

| Label | all_nodes | p0_critical_kinds | p0_non_journey |
|---|---:|---:|---:|
| ACTUAL_SOURCE_RUNNING | 797 | 161 | 242 |
| BEHAVIORAL_STUB | 86 | 14 | 20 |
| CONTRACT_FAITHFUL_REPLACEMENT | 346 | 98 | 141 |
| GRAPH_ONLY | 423 | 51 | 72 |
| PRODUCTION_STATE_UNKNOWN | 42 | 11 | 11 |
| SOURCE_MAPPED_NOT_RUNNING | 1371 | 29 | 39 |

No single production-parity percentage exists or is derivable from these tables; each measure is bound to its population.

## Static vs runtime: what changed relative to the M7 artifact

The M7 canonical snapshot mixed static facts with one boot's observations. The static snapshot drops `generated_at`, `git_head`, the runtime overlay, node `runtime` dicts, journey results/merchant ids/evidence paths, docker status strings and scratchpad paths; those now live in the M7 RuntimeInstance and EvidenceBundle under `imports/m7/`. Fidelity deltas (all classified in `projection.json`):

| kind | M7 (runtime-qualified) | static | count | reason |
|---|---|---|---:|---|
| substitute | high_fidelity_replacement | behavioural_placeholder | 8 | M7 runtime overlay upgraded every running substitute to high_fidelity_replacement (scripts/domain/runtime_overlay.py:102); the static snapshot keeps the class declared by the substitute's lane |
| substitute | high_fidelity_replacement | graph_only | 1 | M7 runtime overlay upgraded every running substitute to high_fidelity_replacement (scripts/domain/runtime_overlay.py:102); the static snapshot keeps the class declared by the substitute's lane |
| worker | real_source_mapped_not_running | real_source_running | 1 | M7 classified by observed container state at overlay time; the static snapshot classifies by compose definition (one-shot jobs count as defined) |

Label deltas: CONTRACT_FAITHFUL_REPLACEMENT → BEHAVIORAL_STUB ×8; CONTRACT_FAITHFUL_REPLACEMENT → GRAPH_ONLY ×1; GRAPH_ONLY → PRODUCTION_STATE_UNKNOWN ×39; SOURCE_MAPPED_NOT_RUNNING → ACTUAL_SOURCE_RUNNING ×1. The GRAPH_ONLY → PRODUCTION_STATE_UNKNOWN flips come from PU-10 (`route:api-monolith/*`) now being machine-readable; M7 carried PU-9/PU-10 in prose only.

## Query capabilities (library `archkit.query.Query`, CLI `python3 -m archkit q <cap> k=v`, HTTP `GET /v1/<snapshot>/<cap>?k=v`)

`snapshot`, `snapshots`, `node`, `edges`, `search`, `service_card`, `neighbors`, `dependencies`, `shortest_path`, `paths`, `identity_reachability`, `data_flow`, `families`, `family`, `journeys`, `journey`, `fidelity_gaps`, `unknowns`, `uncovered_trust_boundaries`, `evidence`, `imports`, `recipe`, `recipes`, `compare`, `affected`, `context_packet`, `s2p_detail`, `verify`.

Every envelope carries `snapshot_id`, `fidelity` (label histogram of the touched nodes), `source_refs`, `production_unknowns` and `truncated`. Canonical queries never load the Source-to-Pay detail graph (`detail_namespace_loaded=false`; verified by gate M8-12 and a unit test).

## Performance (`reports/implementation/m8-bench.json`, single process, stdlib)

| Measure | ms |
|---|---:|
| load + verify snapshot (3.7 MB) | 21.3 |
| index build | 6.7 |
| node | 0.005 |
| search | 0.808 |
| service_card | 0.309 |
| neighbors_d2 | 0.220 |
| shortest_path | 0.264 |
| paths_h6 | 0.149 |
| identity_reachability | 0.076 |
| data_flow | 0.047 |
| fidelity_gaps | 0.451 |
| uncovered_trust_boundaries | 0.089 |
| affected | 2.223 |
| context_packet | 1.207 |
| compare_self | 28.162 |
| s2p_detail first call (loads 27,994-node patch) | 75.3 |
| s2p_detail warm | 0.415 |

RSS: 44.6 MB after index, 140.4 MB after the detail namespace. No graph database is needed at this size.

## Findings surfaced by the queries

- `uncovered_trust_boundaries`: `identity:trust-boundary:payouts-service` has no gated routes or identities in any discovery lane (uncovered); the other three boundaries are covered by journey definitions (administrator: 2 routes, 10 journey definitions; internal-service: 16 routes, 34 journey definitions; public-merchant: 7 routes, 49 journey definitions).

- `fidelity_gaps` (population p0_critical_kinds): 105 of 364 nodes are not ACTUAL_SOURCE_RUNNING / CONTRACT_FAITHFUL_REPLACEMENT: BEHAVIORAL_STUB 14, GRAPH_ONLY 51, PRODUCTION_STATE_UNKNOWN 11, SOURCE_MAPPED_NOT_RUNNING 29.

- Recipes: unknown-field histogram {"fixtures": 14, "graph_node": 36, "health": 1, "production.configuration": 98, "production.deployment_manifest": 98, "production.replica_count": 98, "runtime.image_digest": 98} — image digests are build outputs (not committed inputs), production replica counts/configuration/manifests are production unknowns.

- Source lock: `ledger-sdk` carries a conflicting sha between the inventory CSV and the graph (kept as `conflicts`, not resolved by guessing).

## Tests

`python3 -m unittest archkit.tests.test_archkit` (25 tests: canon, compiler determinism, volatile scan, M7 import, recipes, every query capability, lazy S2P detail, HTTP service); `scripts/snapshot/tests` (36) and `RED_LOOP/tests` (102) unchanged and passing — gate M8-13.

## Remaining blockers before an isolated Twin Factory

1. **No second Docker daemon / VM**: one colima VM (12 GiB) already holds the 98-container M7 boot (~7-8 GB); a second full twin is blocked by compute and by the shared daemon (`reports/source-to-pay-replica/ACCEPTANCE_CLOSURE.md`). Nothing in the repo provisions a colima profile, a remote context or Daytona (packaged, never run).
2. **Host-side generated state in the checkout** (`ENV2_COMPOSE/secrets/`, `config/generated/`, `seeds/generated/`, `.runtime/`): one boot per working copy; `instance-plan.py`/`clean-boot.sh` exist (M3.1) but were exercised only with the 65-container arena.
3. **Hardcoded `env2_compose` / `rzp-arena`** in M6/M7 proof scripts (`RED_LOOP/m6/clean_boot.py`, `RED_LOOP/m7/{reset_proof,s2p_three_runs}.py`, `scripts/m7/canonical_snapshot.py`, `RED_LOOP/red_loop/judge.py`).
4. **Recipe unknowns**: image digests are not committed anywhere (only local image ids in `SNAPSHOT_MANIFEST.json`); a factory must build and record digests itself.
5. **Journey runner is single-instance** (minute-resolution campaign stamp, no resume of a partial suite).
6. The S2P source driver hardcodes `boundary:8080`, `kafka`, `mysql`, `redis` and a fixed app secret; fine inside a private network, not relocatable.

## Not done / out of scope by design

No new business domain, no Twin Factory, no second twin, no arena restart, no M7 file rewritten, no inferred production configuration, no runtime observation promoted to a static fact.
