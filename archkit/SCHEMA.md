# archkit — schemas (M8, `schema_version: m8.1`)

All documents are canonical JSON (`archkit.canon`: sorted keys, compact separators, ASCII, one trailing newline).
Every document carries a content id = sha256 of its canonical body **without** the id field, so any consumer can
recompute it. Nothing static contains a wall clock, a host path, a docker status, a boot id or a journey result;
`archkit.canon.scan_volatile` enforces this at compile time.

## Store layout

```
reports/architecture/snapshots/
  REGISTRY.json                       lineage: {snapshots: {<id>: {kind, source_lock_id, compiler, imports, recipe_set_id, ...}}, current}
  <snapshot_id>/
    snapshot.json                     {"snapshot_id", "body": ArchitectureSnapshot}
    summary.json                      counts, coverage, populations, inputs (no graph)
    manifest.json                     sha256 of the files above
    recipes/<service>.json            ServiceRecipe (98) + INDEX.json (recipe_set_id)
    imports/m7/projection.json        how the accepted M7 artifact projects onto this snapshot (deltas classified)
    imports/m7/runtime_instance.json  RuntimeInstance (observations of one boot)
    imports/m7/evidence_bundle.json   EvidenceBundle (journey results + hash-bound files)
    imports/m7/acceptance_record.json AcceptanceRecord (M7_ACCEPTANCE.json by value, sha-bound)
```

## ArchitectureSnapshot (`kind: architecture_snapshot`)

| field | content |
|---|---|
| `compiler` | `{name: archkit, version}` |
| `source_lock_id`, `source_lock` | SourceLock (below), embedded |
| `inputs[]` | `{path, sha256, role}` for every committed file the compiler read (parts, compose files, ingress contract, S2P patch/overlay/lock, inventory CSV, unknown sources, compiler dependencies) |
| `graph.nodes[]` | static node fields only: `id, kind, label, owner_domain, criticality, confidence, fidelity, fidelity_basis, fidelity_evidence, twin_ref, compose_service, profile, runtime_definition{compose_file, service, profiles, image, one_shot}, repo, sha, source_refs, entry_points, identity_model, authorization, apis, events, tables, queues, topics, state_transitions, feature_flags, external_deps, build, health, fixture_requirements, missing_dependency, m7_label, s2p_id, lanes, family, variant, proves_families, fidelity_declared, route_name ...` — never `runtime`, `result`, `evidence`, `merchant_id`, `notes` |
| `graph.edges[]` | `{from, to, type, via, identity, source_refs, fidelity, confidence, lanes}`; `type ∈ calls consumes produces reads writes transitions gated_by callback schedules owns authenticates depends_on implements` |
| `graph.families[]` | as in `reports/domain/SCHEMA.md` |
| `graph_stats`, `merge.lane_ranks`, `lanes`, `schema_violations` | build_graph statistics without timestamps; lane precedence used |
| `labels` | node id → one of `ACTUAL_SOURCE_RUNNING, SOURCE_MAPPED_NOT_RUNNING, CONTRACT_FAITHFUL_REPLACEMENT, BEHAVIORAL_STUB, GRAPH_ONLY, PRODUCTION_STATE_UNKNOWN` (M7 vocabulary; static semantics in `static_semantics`) |
| `populations` | `{name: {count, definition}}` — `all_nodes`, `p0_critical_kinds` (364), `p0_non_journey` (525), `p0_families`, `journey_definitions` |
| `label_histogram` | per population |
| `coverage` | `{measure: {numerator, denominator, population, pct, definition}}` — never a single parity number |
| `production_unknowns` | ProductionUnknown registry (below) |
| `shared_ingress` | ingress contract routes (from `contract/routes.json`, sha-bound), identities, trust boundaries, ownership edges, tables |
| `s2p_namespace` | Source-to-Pay detail namespace **by reference**: path + sha256 + counts (27,994 / 34,744), projected part sha, overlay counts; loaded only by `s2p_detail` |
| `static_semantics` | what each fidelity class means in a static snapshot |
| `counts` | nodes, edges, families, journeys, production_unknowns |

**Static fidelity semantics.** `real_source_running` = a real binary built from the pinned SHA is *defined* to run in
the canonical runtime (`fidelity_basis: compose_definition`); whether it was observed running is a RuntimeInstance
fact. Substitutes keep the class their discovery lane / CONTRACT declared (the M7 runtime overlay upgraded every
running substitute to `high_fidelity_replacement`; that observation is recorded in the M7 RuntimeInstance, not here).
Journeys are definitions (`fidelity_basis: journey_definition`, `fidelity_declared` = driver value); results are in
EvidenceBundles.

## SourceLock (`kind: source_lock`)

`repositories: {name: {name, sha, remote, role, language, access, integrity, tree, s2p_role, tracked_files, graph_node,
graph_fidelity, sources[], conflicts[], sha_status}}` built from `DOMAIN_REPLICAS/source_to_pay/source-lock.json`,
`reports/domain/REPOSITORY_INVENTORY_M6.csv` and the graph's `repo:*` nodes. A repository with no committed commit id
carries `sha: null` + `sha_status: "UNKNOWN: ..."` (never guessed). Conflicting shas between sources are kept as `conflicts`.

## ProductionUnknown registry (`kind: production_unknown_registry`)

`entries[]: {id, topic, statement, twin_behaviour, affects[node ids], affects_unresolved[], closure_evidence_required,
domain, origin{file, original_id, also_in|machine_readable_in_m7}}` — PU-1..PU-8 from `scripts/m7/canonical_snapshot.py`,
PU-9..PU-10 from `reports/implementation/M7_PRODUCTION_UNKNOWNS.md`, `S2P:<id>` from
`DOMAIN_REPLICAS/source_to_pay/spec/unresolved-production-unknowns.yaml`; `sources` = sha256 of the three files.

## ServiceRecipe (`kind: service_recipe`)

`{id, compose_file, profiles, graph_node, category, source{kind: pinned-repository|twin-local|pulled-image, sha|content_sha256, ...},
build{kind: build-host|compose-build|pulled, canonical, steps, dockerfile, *_sha256}, runtime{image, image_digest(UNKNOWN), entrypoint,
command, environment_keys, one_shot}, health{test, interval, timeout, retries, start_period}|UNKNOWN, dependencies{compose_depends_on,
graph_edges}, networks[{compose_key, name, internal}], state{volumes, stateful, datastores, queues_topics, migrations}, fixtures[],
identities{identity_model, compose_secrets, secret_files_referenced, secret_names_generated_per_boot}, fidelity{class, m8_label, basis,
evidence, missing_dependency}, deviations[{ref, sha256, kind}], reset, journeys[], families[], production_unknowns[], unknowns[], recipe_id}`.
Unknown values are `{"status": "UNKNOWN", "reason": ...}` and are listed in `unknowns`. Secret *values* are never read.

## RuntimeInstance / EvidenceBundle / AcceptanceRecord

* **RuntimeInstance** — `boot_id, compose_project, route_profile, config_digest, git_head, started_at/finished_at, from_empty_state,
  container_count, running, healthy, mapped_running_compose_services[], images_by_graph_node, observed_node_fidelity, host, sources` → `runtime_instance_id`.
* **EvidenceBundle** — `runtime_instance_id, milestone, run_dir, journey_results{id: {result, checks, duration_s, evidence_path, evidence_sha256, ...}},
  result_histogram, files{path: sha256} (the M7 143-file manifest), other_evidence` → `evidence_bundle_id`. Git-ignored run files are bound by hash and
  reported present/absent at query time.
* **AcceptanceRecord** — the M7 acceptance by value (`gates[]`, `accepted`, `evaluated_commit`, `tag`) + `file_sha256`, `evidence_bundle_id` → `acceptance_record_id`.

## SnapshotDiff (query `compare`) and impact (query `affected`)

`compare` → `{nodes_added, nodes_removed, nodes_modified (content id), fidelity_changes, edges_added, edges_removed, coverage_changes, unknowns_added/removed,
source_lock_changed}`; `affected` → `{changed_nodes, affected_services, affected_substitutes, affected_families, rerun_journeys, compose_services}` using
`scripts/snapshot/affected.py` impact rules over the static graph.

## Query envelope

`{snapshot_id, query, params, count, truncated, items, fidelity{label: count}, source_refs[], production_unknowns[], detail_namespace_loaded}`.
Limits: `limit ≤ 500`, `max_hops ≤ 30` (shortest path) / `≤ 8` (enumerated paths, expansion budget 20,000), `depth ≤ 4`, context budget 800–60,000 chars.
