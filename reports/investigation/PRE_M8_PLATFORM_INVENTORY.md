# Pre-M8 Repository Preflight — Platform Inventory

Investigation-only. Written 2026-09-08 against the accepted M7 state. Nothing was modified, restarted, regenerated, committed or re-tagged. All temporary checks wrote only to the session scratchpad (`/private/tmp/claude-502/.../scratchpad`); one accidental side effect of `scripts/snapshot/recipes.py --out <tmp>` (it rewrites `reports/domain/SNAPSHOT_MANIFEST.json` regardless of `--out`) was reverted with `git checkout --` and is reported in §2.6 as a finding. Final `git status --porcelain` is empty.

Source-of-truth order used: Git at the tag → machine acceptance / hash-bound evidence → raw runtime and journey evidence → M7 reports → specs and older reports.

---

## 1. Verified accepted base

| Check | Result | Evidence |
|---|---|---|
| Tag object | `twin-m7-shared-ingress-integration` is an annotated tag object `fab349ed…` → commit `37ac47db1e630c44e54707694aa84f44301f0b19`; tagger rana.singh, 1788860565 +0530; message cites the acceptance sha256 `13d7ef0b…` | `git cat-file -t`, `git rev-parse <tag>^{commit}`, `git cat-file -p <tag>` |
| Branch HEAD | `milestone-7-shared-ingress-integration` @ `37ac47d` (== tag target) | `git rev-parse --abbrev-ref HEAD`, `git rev-parse HEAD` |
| Clean status | `git status --porcelain` → 0 lines (before and after this investigation) | |
| M7 acceptance | `reports/implementation/M7_ACCEPTANCE.json`: `accepted=true`, 22/22, `head=4f914d6b…`, `evaluated_at=2026-09-08T09:42:23Z`; file sha256 `13d7ef0b…` matches the tag message | `python3 -c json.load` |
| Artifact hashes | `python3 scripts/m7/hashes.py --verify` → `{"passed": true, "changed_or_missing": [], "count": 143}`; manifest sha256 `f97e1b27…` == `M7_ACCEPTANCE.json.artifact_manifest_sha256`; `M7_CANONICAL_SNAPSHOT.json` sha256 `d88a2634…` == `canonical_snapshot_sha256` | read-only verify |
| Historical tags | All eight tags resolve to the commits recorded in gate M7-01 (`twin-m6-complete-domain`→76024ae, `s2p-acceptance-closure-m7`→4505e62, `assurance-m5-autonomous-discovery`→2613f38, `red-loop-m4`→3ae1773, `assurance-m4.1-reproducible`→ef4ba72, `red-loop-m3.1`→3a044f8, `twin-v1.0`→78def24) | `for t in $(git tag) …` |
| Arena state | Docker context `colima` (single daemon, VM 4 CPU / 12 GiB / 60 GiB). 103 containers: **98 in project `env2_compose`** (97 Up + `ledger-scheduler` Exited(0), a one-shot job) = the canonical M7 boot (93 arena + api-ingress + 4 S2P) — this is the boot whose `boot_id 6cdf3d73…` the journey evidence carries; **5 extra containers in project `s2p_corrected_final_verification`** (kafka, mysql, redis, monolith-boundary, vp-source, Up 15 h) left over from the S2P closure era and **not part of M7**. Networks `rzp-arena`, `rzp-ingress`, `rzp-s2p-internal` exist. | `docker ps -a --format '{{.Label "com.docker.compose.project"}}'` |

### 1.1 Commit relationship: 37ac47d vs `git_head 409cbe0…`

```
37ac47d  M7 acceptance: 22/22 (rewrites M7_ACCEPTANCE.json only)            15:12:23 IST
4f914d6  final report + hash manifest bound to the 22-gate acceptance         15:12:22  <- M7_ACCEPTANCE.head
e9d1bfe  S2P post-integration evidence (3 clean runs, 20/20)                 15:12:21
6fc53bd  gate-5 isolation baseline (branch m7-s2p-post-d0e8144, fast-forwarded in)
d0e8144  M7 evidence: clean boot, 111 journeys, snapshot, reports, interim 20/22   15:04:04  <- snapshot FIRST COMMITTED here
409cbe0  observation hardening … (last IMPLEMENTATION commit)                14:22:08  <- snapshot.git_head
```

- `scripts/m7/canonical_snapshot.py:116` records `git rev-parse HEAD` **at generation time**. The snapshot was generated at `2026-09-08T09:33:30Z` (15:03:30 IST) while the working tree was at `409cbe0` with uncommitted evidence (graph, parts, journeys). Thirty-four seconds later `d0e8144` committed it. The snapshot blob `4ed9f656…` is byte-identical in `d0e8144`, `6fc53bd`, `e9d1bfe`, `4f914d6`, `37ac47d` and the working tree, so `git_head` legitimately names the *code* commit the snapshot was compiled from, not the commit that stores it.
- The acceptance evaluator (`scripts/m7/acceptance.py:47`) records its own `HEAD` (`4f914d6`) and the snapshot's sha256 (line 207) but **never reads or checks the snapshot's `git_head`**; the graph and journey evidence carry the same `409cbe0` (`GRAPH_STATS.json`, `m6-journeys.json`, `m6-clean-boot.json`). The final commit `37ac47d` differs from `4f914d6` only by the acceptance JSON itself (gate M7-22 excludes that file by design, line 201).
- Verdict: the mismatch is **intentional and structurally forced** (an evidence file cannot contain the id of the commit that stores it) and is **machine-consistent** (hash-bound snapshot identical across all five commits, `git_head` is an ancestor of the tag). It is **not machine-verified** as such: no gate asserts `snapshot.git_head` ∈ ancestors(HEAD) or that the graph inputs hashed in the snapshot equal the tree at `git_head`. The working tree at `409cbe0` was dirty when the snapshot ran (`git diff --stat 409cbe0 d0e8144` shows the runtime-overlay and S2P parts changed between them), so `git_head` alone does not identify the snapshot's inputs — the embedded sha256s of the graph / patch / part files do. This is exactly the gap an M8 `ArchitectureSnapshot` with an explicit, content-addressed input set closes (§3).

---

## 2. Snapshot-compilation machinery inventory

Legend: **Det** = byte-identical on repeated runs with identical inputs (measured, not inferred); **Ts** = embeds wall-clock timestamps; **Rt** = embeds live runtime state; **Committed**: output tracked in Git at the tag.

| # | Implementation | Input | Output | Det | Ts | Rt | Input pinned | Committed | M8 disposition |
|---|---|---|---|---|---|---|---|---|---|
| 2.1 | `scripts/domain/build_graph.py` (graph compiler: parts → merged graph; lane precedence `LANE_RANK`, fidelity conflict rules, stats) | `reports/domain/parts/*.json` | `PAYOUTS_FUNCTIONAL_GRAPH.{json,nodes.csv,edges.csv,mmd,graphml}`, `GRAPH_STATS.json`, `FIDELITY_CLASSIFICATION.csv` | **Content-deterministic, not byte-deterministic**: two temp runs differ only in `generated_at` (json+stats) and `version: m6-<date>`; graphml/csv identical; temp output == committed modulo those two fields | yes (`generated_at`, `version`) | no (but merges parts that do) | parts only; no lock of which part set/version | yes | **Retain** merge semantics; **modify**: remove wall-clock, derive `version` from content hash, accept an explicit input manifest |
| 2.2 | Discovery-lane parts (`payouts-core`, `fts-mozart`, `money-statements`, `identity-ingress`, `external-config-deploy`, `twin-inventory`) — produced by M6 agents, no generator script in repo | pinned clones | `parts/<lane>.json` + `.md` | n/a (hand/agent-produced, frozen) | yes | no | `sources[].sha` present; `path` points at a **decayed scratchpad** (`/private/tmp/claude-502/…`) | yes | **Retain as frozen inputs**, address by sha256 |
| 2.3 | `scripts/domain/runtime_overlay.py` | compose + `docker ps -a` | `parts/zz-runtime-overlay.json` (87 nodes with `runtime:{running,healthy,status}`, evidence strings like "is Up 2 hours (healthy)") | **no** (live docker) | yes | **yes** | no | yes | **Split out** into RuntimeInstance observation; never merge into the architecture graph |
| 2.4 | `scripts/domain/journeys_part.py` | `reports/implementation/m6-journeys.json` (+ run dir) | `parts/m6-journeys.json` (111 journey nodes: `result`, `merchant_id`, `evidence` = `RED_LOOP/runs/...` path) | deterministic given the input file | yes | yes (results, merchant ids, run paths) | run dir git-ignored | yes | **Split out** into EvidenceBundle; keep only journey *definitions* in the architecture graph |
| 2.5 | `scripts/domain/worker_gaps.py` | merged graph + `ENV2_COMPOSE/fts-worker-gaps.json` | `parts/zz-worker-gaps.json` | deterministic | yes | indirectly (reads fidelity from overlaid graph) | — | yes | Retain; feed from the static graph |
| 2.6 | `scripts/snapshot/capture.py` (input/repository snapshot: repos, accepted-copy digests, recipe hashes, config hashes via `ENV2_COMPOSE/scripts/fingerprint.py`, migrations, contracts incl. `ingress`/`s2p`, flags, images, compose) | working tree, `.local/repos-root`, `.local/twin-repos/accepted`, docker (optional) | `reports/domain/snapshots/<UTC>.json` + `latest.json` | **Digest-deterministic**: two `--no-docker` temp captures → identical `digest f67ba5f3…` (digest excludes `captured_at`, `images_reason`); only `_path`/`captured_at` differ | yes | `twin.dirty`, image ids (when docker on) | records SHAs, but reads whatever is on disk (not a lock) | yes (27 snapshots committed) | **Retain** as the *input-lock* capturer; **modify**: strip `captured_at` from the file body, add explicit `snapshot_id = digest` |
| 2.7 | `scripts/snapshot/recipes.py` | compose, build-host.sh, Dockerfiles, accepted copies, graph, CONTRACT.md files | `reports/domain/recipes/*.yaml` (139 now; 138 committed), `SNAPSHOT_MANIFEST.json` | **no**: every recipe carries `generated_at`; `recipe_hash` excludes it, but `fidelity_reason` embeds live docker status ("Up 2 hours (healthy)"); `--out DIR` still overwrites the tracked manifest (observed, reverted) | yes | yes | no | yes, **stale** (`api-ingress` and the 4 S2P overlay services have no recipe; graph_version in manifest `a13555b6` ≠ current graph `95498fb9`) | **Refactor** into the ServiceRecipe schema (§5); make `--out` honest |
| 2.8 | `scripts/snapshot/diff.py` | two snapshots | diff JSON (exit 3 on change) | deterministic | yes (`generated_at`) | no | — | via refresh dir | **Retain**; this is the SnapshotDiff kernel |
| 2.9 | `scripts/snapshot/affected.py` (`Plan`: repo/table/flag/sub/ingress/s2p → services → families → journeys; `implements`/`depends_on` edge walks) | diff + graph + compose | affected plan | deterministic | yes | no | — | via refresh dir | **Retain** as the impact-query library; expose through the query service |
| 2.10 | `scripts/snapshot/refresh.py daily|weekly|status` | above | `reports/domain/refresh/<UTC>-*.json` | plan deterministic, execution not | yes | executes docker/journeys with `--execute` | — | yes | Retain the planner; the executor is milestone-shaped |
| 2.11 | `RED_LOOP/m7/refresh_demo.py`, `RED_LOOP/m7/weekly.py`, `RED_LOOP/m6/refresh_demo.py` | live arena | `m7-refresh-demo.json`, `m7-weekly-rebuild.json` | no | yes | yes | — | yes | Milestone proofs; **do not** build on |
| 2.12 | `scripts/m7/ingress_part.py`, `scripts/m7/s2p_part.py` | `contract/routes.json`, S2P patch/overlay | `parts/m7-ingress.json`, `parts/zz-s2p-namespace.json` (930 projected nodes) | content-deterministic | yes | no | routes.json/patch sha in snapshot | yes | Retain; drop timestamps |
| 2.13 | `scripts/m7/derive_contract.py` | pinned api/payouts/vendor-payments clones | `ENV2_COMPOSE/substitutes/api-ingress/contract/routes.json` | content-deterministic (sort_keys) but `generated_at` embedded → **not byte-identical**, yet its sha256 is what the canonical snapshot binds (`contract_sha256`) | yes | no | api sha checked by gate M7-07 | yes | Retain; drop timestamp |
| 2.14 | `scripts/m7/canonical_snapshot.py` | graph, stats, parts, routes.json, S2P patch + overlay, `m6-journeys.json` + run-dir evidence, **`docker ps`** | `reports/architecture/M7_CANONICAL_SNAPSHOT.json` (4.5 MB, embeds the whole graph) + `_STATS.json` | **no** — `generated_at`, `runtime_overlay.captured_at`, `docker ps` images/ids, journey evidence | yes | **yes** | by embedded sha256 of graph/patch/part/contract | yes | **Replace** with the M8 compiler; keep its label/coverage logic and PU list as inputs |
| 2.15 | `scripts/m7/hashes.py` (`--verify`) | explicit list of 31 tracked files + 112 git-ignored run-dir files | `M7_ARTIFACT_HASHES.json` | deterministic content, `generated_at` embedded | yes | no | — | yes | Retain the pattern; generalize to a content-addressed manifest |
| 2.16 | `scripts/m7/fidelity_matrix.py`, `final_report.py` | stats/snapshot/journeys | Markdown | deterministic | some | no | — | yes | Report renderers only |
| 2.17 | `scripts/domain/inventory.py` | `.local/repos-root` clones, older CSV/MD | `REPOSITORY_INVENTORY_M6.csv`, `REMAINING_ACCESS_MANIFEST.md` | deterministic given clones | yes | no | records SHAs | yes | Retain as a source-lock feeder |
| 2.18 | **Source-to-Pay** `DOMAIN_REPLICAS/source_to_pay/source-lock.json` (9 repos: name, sha, tree hash, tracked_files, remote, recovery, role) | — | itself | n/a | `generated_at` | no | **yes — this is the only true source lock in the repo** | yes | **Retain as the SourceLock schema model**; extend to the Payouts repos |
| 2.19 | S2P `scripts/generate-inventory.py` → `inventory/architecture-inventory.json`, `integration/unified-graph-patch.json` (27,994 nodes / 34,744 edges, sorted, `sort_keys=True`, no timestamp in the patch), `inventory/generated-artifact-provenance.json`, `runtime-fidelity-overlay.json` | pinned S2P clones | above | patch is byte-deterministic by construction (sorted, no clock); not re-run here | no (patch) | overlay: yes (run traces) | yes (source-lock) | yes | Retain; model for deterministic lane output |
| 2.20 | S2P `scripts/{clean-run.py, acceptance.py, isolation.py, isolation_baseline.py, artifact_integrity.py, scan-deliverables.py}` and `scripts/m7/isolation_baseline.py` | worktree + docker | acceptance JSON, isolation before/after | no | yes | yes | — | closure artifacts yes | Retain isolation-baseline as a RuntimeInstance pre/post record |
| 2.21 | `ENV2_COMPOSE/scripts/fingerprint.py` (`config_hashes`, `config_digest`, image ids, container ids, boot_id) | compose tree + docker | `.runtime/arena-fingerprint.json` (git-ignored), copied into every run dir | config part deterministic; boot part not | yes | yes | — | no | **Retain** as the RuntimeInstance identity (boot_id + config_digest + image ids) |
| 2.22 | `TWIN_SPEC/*.yaml` (`architecture`, `components` (24), `runtime-topology`, `route-matrix`, `state-machines`, `configuration-manifest`, `synthetic-data-model`, `acceptance-invariants`, `substitute-contracts/*.md`) | hand-written 2026-09-05 | spec | n/a | `generated` field | no | commits in `architecture.yaml` | yes | Reference input; not a compiler artifact |
| 2.23 | `ARCHITECTURE_EXPLORER/data/architecture.json` (29 nodes / 42 edges, M1 illustrative) | hand-curated | static UI data | n/a | — | — | — | yes (last touched 219ca48, M1) | Not connected to the functional graph (0 references); do not reuse as data |

**Snapshot history / lineage / content-addressed storage:** `reports/domain/snapshots/` holds 27 timestamp-named capture files plus a `latest.json` pointer (`capture.py:250-265`, never overwrites). There is no lineage record (parent snapshot id, produced-by commit) other than the diff files in `reports/domain/refresh/`. The only content-addressed store is `RED_LOOP/red_loop/state.py:161 put_evidence` (per-campaign `evidence/<label>-<sha16>.<ext>`) and the M7 hash manifest. **No first-class `architecture_snapshot_id` exists anywhere**: identity is by file path + wall-clock stamp (`snapshots/<UTC>.json`), by sha256 of a file that itself embeds a timestamp (`M7_CANONICAL_SNAPSHOT.json`), or by `graph_version = sha256(file)` (`common.py:219`), which changes on every regeneration because of `generated_at`.

Unit tests: `scripts/snapshot/tests/test_snapshot.py` — 36 tests, all pass offline (run now, temp dirs). Graph builder and canonical snapshot have no tests.

---

## 3. Static snapshot vs runtime vs evidence

Decomposition of `M7_CANONICAL_SNAPSHOT.json` (top-level sizes: `payouts_graph` 3.4 MB, `labels` 224 KB, everything else < 30 KB):

| Class | Fields / content | Deterministic? |
|---|---|---|
| **A. Immutable source/architecture facts** | graph nodes' `id, kind, label, owner_domain, repo, sha (43 distinct), source_refs, entry_points, identity_model, authorization, apis, tables, queues, topics, state_transitions, feature_flags, external_deps, build, health, criticality, confidence`; edges `from,to,type,via,identity,source_refs`; families; `s2p_namespace.{path,sha256,nodes,edges}`; `shared_ingress.{routes,identities,trust_boundaries,ownership_edges,tables,contract_sha256}`; `production_unknowns` | yes, once lane `sources[].path` (dead scratchpad paths) are dropped in favour of sha |
| **B. Selected synthetic design/configuration** | `twin_ref` (compose service / substitute), `fidelity` class where it expresses a *design decision* (substitute vs real), `lanes`, `m7_label` (45 P0 overrides), `compose_service`, `profile`, `missing_dependency` table, `coverage.detector_limitations`, S2P `runtime_fidelity_overlay.execution_claim` | yes |
| **C. Mutable runtime observations** | `runtime_overlay.{captured_at, running_compose_services, services[*].image/running_compose_services}`; 87 graph nodes with `runtime:{running,healthy,status}`; 55 `fidelity_evidence` strings containing "is Up N hours (healthy)"; fidelity values that were *set by* the overlay lane (`real_source_running` ⇔ container up at overlay time, `runtime_overlay.py:102`) | **no** |
| **D. Journey evidence** | 111 journey nodes: `result, evidence (RED_LOOP/runs/… path, 222 occurrences), merchant_id (108 nodes), notes, fidelity (derived from result)`; `connected_journey.evidence` (touched hosts per run); `coverage.critical_journey.*` | no (run-specific) |
| **E. Acceptance metadata** | none inside the snapshot itself; `_STATS.json.snapshot_sha256`; the acceptance JSON binds the snapshot the other way round | — |
| **F. Wall-clock / host-specific** | `generated_at`, `runtime_overlay.captured_at`, `git_head` (of a dirty tree), part `generated_at`s, 8 `/tmp/` strings, docker image tags `:v1-candidate` (host tag, not digest), `GRAPH_STATS.generated_at`, `graph.version = m6-<date>` | no |

**Fields that prevent a deterministic, content-addressed artifact today** (exhaustive from the measurements above): `generated_at` (snapshot, stats, graph, every part, routes.json, recipes, hash manifest), `runtime_overlay.captured_at`, `git_head`-of-dirty-tree, the whole `runtime_overlay` block, node `runtime` dicts, "Up N hours" evidence strings, journey `result/evidence/merchant_id/notes` and result-derived `fidelity`, `connected_journey.evidence`, `coverage.critical_journey` and `coverage.executable/actual_source_runtime` (they depend on overlay-set fidelity), image tag strings instead of digests, lane `sources[].path` scratchpad paths, `graph.version` date stamp.

### 3.1 Proposed schema boundaries (not implemented)

- **SourceLock** — `{lock_id=sha256(body), repositories[{name, sha, tree, remote, role, tracked_files, verification_ref}], generated_by_commit}`; model: `DOMAIN_REPLICAS/source_to_pay/source-lock.json`; Payouts side fed by `capture.py.snapshot_repos` + `inventory.py`.
- **ArchitectureSnapshot** — `{snapshot_id=sha256(canonical JSON without this field), schema_version, source_lock_id, inputs[{path, sha256, kind: part|contract|patch|spec}], graph{nodes,edges,families} restricted to class A+B fields, labels (design-time only: ACTUAL_SOURCE / SOURCE_MAPPED / CONTRACT_FAITHFUL / STUB / GRAPH_ONLY / UNKNOWN as *intended realisation*, not as observed liveness), production_unknowns[] (by id), coverage.mapping only, detector_limitations, compiler{script, version, commit}}`. No timestamps, no paths outside the repo, no docker. `parent_snapshot_id` optional for lineage.
- **ServiceRecipe** (child of ArchitectureSnapshot, §5) — one per component.
- **RuntimeInstance** — `{instance_id (boot_id), snapshot_id, host{daemon, context, vm}, compose{project, suffix, subnets, port}, config_digest, image_digests[], container_ids[], started_at, stopped_at, isolation_baseline_ref}`; model: `fingerprint.py` output + `isolation_baseline.py` + `instance-plan.py` env.
- **EvidenceBundle** — `{bundle_id=sha256(manifest), instance_id, snapshot_id, kind: journeys|clean_boot|reset_proof|s2p_run|refresh_demo, files[{path, sha256}], summary}`; model: `hashes.py` + run-dir `summary.json` + `evidence/*.json`.
- **AcceptanceRecord** — `{milestone, evaluator{script, sha256}, evaluated_commit, snapshot_id, evidence_bundle_ids[], gates[], accepted, evaluated_at}`; model: `M7_ACCEPTANCE.json` (already close; add `snapshot_id` and bundle ids instead of a single manifest sha).
- **ProductionUnknown** — `{id, topic, statement, affects[node ids], twin_behaviour, closure_evidence_required, source: payouts|s2p, first_recorded_snapshot_id}`; union of `canonical_snapshot.UNKNOWNS` (PU-1..8), `M7_PRODUCTION_UNKNOWNS.md` (PU-9, PU-10) and S2P `unresolved-production-unknowns.yaml` (4 ids).
- **SnapshotDiff** — `{from_snapshot_id, to_snapshot_id, sections{repos, contracts, schemas, flags, config, compose, graph{nodes_added/removed/modified, edges…}}, affected{rebuild, restart, rerun_journeys, families}}`; model: `diff.py.diff_snapshots` + `affected.py.Plan.result` (already the right shape; only ids change).

### 3.2 Importing M7 without touching history

Compile an `ArchitectureSnapshot` **from the committed M7 inputs at the tag** (parts, routes.json, S2P patch, `labels` block, `production_unknowns`) by projecting `M7_CANONICAL_SNAPSHOT.json` through a pure function that strips classes C–F; record `imported_from = {file: reports/architecture/M7_CANONICAL_SNAPSHOT.json, sha256: d88a2634…, git_head: 409cbe0, tag: twin-m7-shared-ingress-integration}`. Emit a `RuntimeInstance` for boot `6cdf3d73…` and an `EvidenceBundle` from the 143-file manifest, and an `AcceptanceRecord` pointing at `M7_ACCEPTANCE.json` by sha. Write all of these under a new directory (e.g. `reports/architecture/snapshots/<snapshot_id>/`) — never rewrite the M7 files, never re-run `canonical_snapshot.py` on the M7 branch, and add a gate that the projection of the M7 file equals the imported snapshot byte-for-byte so the import is machine-checkable.

---

## 4. Architecture query capabilities

Graph libraries: **none** — no networkx/igraph/rustworkx/duckdb in the repo or the interpreter (`python3 -c importlib.util.find_spec` → all False; `yaml` and `sqlite3` present; Python 3.14.6). `sqlite3` is used only by the api-ingress, batch-sim and workflow-engine substitutes. Every consumer loads the JSON with `json.loads` and scans lists.

| Capability | Status | Where |
|---|---|---|
| Node lookup | implemented but internal (dict built ad hoc) | `scripts/snapshot/affected.py:97` (`Plan.nodes`), `canonical_snapshot.py:92`, `runtime_overlay.py:54` |
| Edge lookup by type/endpoint | implemented but internal (linear scans) | `affected.py:115-138` (`gated_by`, `implements`, `depends_on`) |
| Path search / reachability | **absent** (no BFS/shortest path anywhere; `build_graph.py:147 _reachable_repos` is about filesystem clones) | — |
| Identity reachability | absent as a query; identities exist as nodes/edges (`authenticates`, ingress trust boundaries) | `parts/m7-ingress.json`, `identity-ingress.json` |
| Service cards | report-generation-only | `recipes.py` (per-service YAML), `M7_FIDELITY_MATRIX.md` |
| Data-flow lookup | absent as a query; edges `reads/writes/produces/consumes` exist | graph |
| Business-family / journey lookup | implemented but internal | `common.py:280 journeys_for_family`, `affected.py:140-163 families_for / journeys_for_nodes` |
| Fidelity filtering | report-generation-only (`build_graph.py:181 stats`, `FIDELITY_CLASSIFICATION.csv`) | — |
| Production-unknown lookup | absent (a static list; `affects` ids not indexed) | `canonical_snapshot.py:26` |
| Graph slicing | partial: `affected.py` computes an impact slice from a diff; no generic slice API | — |
| Source/evidence-reference retrieval | report-generation-only (`source_refs`, `evidence` strings carried on nodes; no resolver) | — |
| Graph diff queries | implemented and **tested** (36 tests) but CLI/library only | `diff.py:122 diff_graph`, `affected.py:364 apply_graph` |
| HTTP / RPC / CLI / library interface | CLI scripts only; the only HTTP server (`ARCHITECTURE_EXPLORER/server.py`, loopback, 11 tests) serves a static M1 `architecture.json` and a golden-run trigger — it does **not** read the functional graph | — |
| Caching / indexing | absent | — |

Measurements (`scratchpad/measure_graph.py`, stdlib, single process):

| Measure | Value |
|---|---|
| `M7_CANONICAL_SNAPSHOT.json` (4.5 MB) parse | 10 ms, +20 MB RSS |
| `PAYOUTS_FUNCTIONAL_GRAPH.json` (3,065 nodes / 5,266 edges) parse | 9 ms, +10 MB RSS |
| S2P `unified-graph-patch.json` (27,994 nodes / 34,744 edges) parse | 47 ms, +89 MB RSS |
| Linear node lookup (as current code does it) | 0.05 ms |
| Build id + adjacency indexes | 1.4 ms; dict lookup 0.03 µs |
| BFS `identity:merchant-api-key → table:payouts/payouts` | 0.21 ms, 7 hops |
| BFS `sub:api-ingress → svc:fts-web` | 0.02 ms |
| Forward reachability from `svc:payouts-api` | 974 nodes in 0.19 ms |
| Max out-degree | 276 |
| Process RSS with everything loaded | 137 MB |

Bottlenecks for repeated concurrent queries: not compute — the graph is tiny. The real costs are (a) re-parsing 4.5 MB per process (every script does), (b) no shared index/ID→node map, (c) the S2P patch (89 MB) if loaded per request, (d) no stable snapshot id to key a cache on. Conclusion: **no new database is justified**. An in-process index (dict + adjacency, optionally persisted to a `sqlite3` file per `snapshot_id` for multi-process sharing, both stdlib) satisfies the expected load by orders of magnitude.

---

## 5. Service-recipe and twin-manifest readiness

Existing machine-readable descriptions of build/run: (1) `reports/domain/recipes/*.yaml` (138 committed: 100 compose services + 38 source repos; schema in `recipes.py:383 recipe_for_compose_service` / `:493 recipe_for_repo`); (2) `docker-compose.yml` (101 services) + `docker-compose.s2p.yml` (5); (3) `TWIN_SPEC/components.yaml` (24 components: `controls, evidence, repo, role, target, today`) and `runtime-topology.yaml`; (4) S2P `spec/runtime-topology.yaml`, `spec/components.yaml`, `spec/declared-deviations.yaml`, `source-lock.json`, `artifacts/build-manifest.json` (binary sha256s), `.build/runtime-image.env`; (5) `.local/twin-repos/build-evidence-*/{provenance,build-results}.json` (untracked; build commands, elapsed, candidate image ids).

Field coverage of the recipe schema against the required list (from the 138 committed recipes):

| Required field | Present in recipes? | Notes |
|---|---|---|
| source SHA | yes for 110 (`sha`); 28 lack it (substitutes/datastores/`repo-*` without clone) | substitutes carry `replaced_source_sha` |
| build command | yes (`build_command.{canonical,steps,dockerfile}`) | for core: `build-host.sh` steps; pulled images: "not built" |
| image digest | **only in `SNAPSHOT_MANIFEST.json.image_digests`** (23 names, local ids not registry digests; tag `v1-candidate`) | not per recipe |
| runtime command | yes (`runtime.{image,entrypoint,command,environment_keys,volumes}`) | values never copied |
| health check | 95 of 138 (43 lack; 38 are `repo-*`, a few services have none) | |
| dependencies | yes (`dependencies.compose_depends_on`, `graph_edges`) | |
| network policy | **no** (networks not recorded; `internal: true` and the `rzp-ingress` single-egress rule live only in compose) | |
| datastore & queue requirements | partial (graph edges `db:*`, `queue:*`; migrations in `database_migrations`) | |
| synthetic fixtures | 91 of 138 (`synthetic_seed_generator`) | |
| identity & secrets | partial (`runtime.secrets_referenced` — empty for all; `environment_keys` names only) | no identity model |
| fidelity | yes (`fidelity_label`, `fidelity_reason`) — but reason embeds live docker status | |
| deviations | **no** (only S2P has `declared-deviations.yaml`; M6 substitutes have prose `CONTRACT.md`) | |
| reset behaviour | **no** | reset lives in `down.sh -v`, `/_ingress/reset`, `s2p_stack.py down`, `reset_proof.py` |
| relevant journeys | **no** (families/journeys reachable only via the graph) | |

Counts (running services in the canonical boot = 97 up + ledger-scheduler one-shot = 98 compose services, plus the 4 S2P overlay services already inside the 98):

- **Complete recipes** (all 14 fields): **0**.
- **Partial recipes** (recipe YAML exists, missing network policy / deviations / reset / journeys / secrets model): **93 of 98** running compose services (`api-ingress`, `s2p-kafka`, `s2p-mysql`, `s2p-redis`, `s2p-vp-source` have **no recipe**; `mozart-mock`, `verifier`, `workflow-sim`, migrate jobs have recipes but are not running).
- **Compose or prose only**: **5** running services (the M7 additions above; `api-ingress` also has `CONTRACT.md` + `contract/routes.json`; S2P has its spec yamls).

Overlapping schemas: recipe YAML ↔ compose (build/run/health duplicated), recipe `fidelity_label` ↔ graph `fidelity` ↔ `m7_label` ↔ S2P `status` (four vocabularies: 6 M6 classes, 6 M7 labels, S2P `ACTUAL_SOURCE_RUNNING|CONTRACT_FAITHFUL_REPLACEMENT|SOURCE_MAPPED_NOT_RUNNING`, M4/M5 matrix words mapped by `MATRIX_LABEL_MAP`), `TWIN_SPEC/components.yaml` `today/target` ↔ all of the above, S2P `source-lock.json` ↔ `capture.py.repos` ↔ `provenance.json`.

Smallest normalization before a Twin Factory could consume them: (1) regenerate recipes so the 5 missing services exist and the manifest matches the current graph; (2) replace `fidelity_reason` live text with a reference to the RuntimeInstance; (3) add four fields per recipe: `networks[]` (from compose), `reset` (down -v / endpoint / volume), `deviations_ref` (CONTRACT.md or declared-deviations id), `journeys[]` (from graph `family.components` reverse index); (4) add `image_digest` (from `docker image inspect` RepoDigests/Id) and `secrets[]` (names from `secrets/gen-secrets.sh`, never values); (5) one label vocabulary (the six M7 labels) with a mechanical mapping table for the other three.

---

## 6. Provisioning and isolation capabilities

| Capability | Status | Evidence |
|---|---|---|
| Compose project parameterization | implemented (M3.1): `COMPOSE_PROJECT_NAME` + `ARENA_SUFFIX` + `ARENA_SUBNET/INGRESS_SUBNET/KONG_LITE_HOST_PORT`, derived deterministically from an instance id | `ENV2_COMPOSE/scripts/instance-plan.py:52-76 derive`, fail-closed overlap check; `clean-boot.sh` |
| Unique networks | implemented: `rzp-arena${ARENA_SUFFIX}`, `rzp-ingress${ARENA_SUFFIX}`, `rzp-s2p-internal${ARENA_SUFFIX}`, distinct /24 pair per instance | `docker-compose.yml networks`, `docker-compose.s2p.yml:65` |
| Unique volumes | implemented: named volumes carry `${ARENA_SUFFIX}`; unnamed datastore volumes are project-scoped; `secrets/materialize.py:21-34` honours the suffix | |
| Unique secrets | implemented per boot (`REGEN_SECRETS=1`, `gen-secrets.sh`) **but written into the source tree** (`ENV2_COMPOSE/secrets/`, `config/generated/`, `seeds/generated/`, `.runtime/`) → a second instance needs a separate working copy (`clean-boot.sh` rsyncs one) | `instance-plan.py:17-20` docstring |
| Unique ports | implemented: only `kong-lite` publishes a host port (`127.0.0.1:${KONG_LITE_HOST_PORT}`); instance port 18100-18899 | compose; `instance-plan.py:66` |
| Multiple Docker contexts | not implemented in repo; host has contexts `colima` (active) and `default` (both unix sockets on this machine) | `docker context ls` |
| Separate Docker daemons | not implemented; one colima daemon (`colima list`: profile `default` only, 4 CPU / 12 GiB / 60 GiB). No `colima` or profile handling anywhere in scripts (grep: 0 hits) | |
| Colima profiles / VMs | not implemented | |
| Remote execution | not implemented | |
| Daytona / sandbox providers | packaged but **never run**: `DAYTONA/` scripts (dry-run only, CLI and SDK absent, authorization gate closed) | `reports/implementation/DAYTONA_RUN_REPORT.md` |
| State reset | implemented at three granularities: arena `down.sh -v` (volumes + secrets), ingress `POST /_ingress/reset` (mutable tables, seed ownership kept), S2P `s2p_stack.py down` (volume removed); proven by `m7-reset-proof.json` (52 → 0 mutable rows, replay after reset treated as new) | |
| State snapshot / restore | implemented for datastores as SQL/BSON dumps (`ENV2_COMPOSE/scripts/snapshot.sh`, `restore.sh`, live containers only); no image/volume-level snapshot; M6 verified `from_empty_state` via volume creation timestamps (`clean_boot.py --attach`) | |
| Lifecycle records | partial: `fingerprint.py` boot identity (`boot_id`, container ids, config digest) per boot; `m6-clean-boot.json`; `instance-plan.json` per disposable instance; no registry of instances | |
| Resource accounting | implemented once (M3.1): `ENV2_COMPOSE/scripts/resource-profile.py` → `reports/implementation/resource-profile.json` (65-container arena: idle peak 4.18 GiB / 1.02 cores; full verifier peak 1.40 cores; disk 4.31 GiB) | |
| Concurrent acceptance | **explicitly judged insufficient** on a shared daemon: `reports/source-to-pay-replica/ACCEPTANCE_CLOSURE.md:79-89`; the historical 18/20 failed gates 02/03 precisely because another task shared the daemon and worktree registry | |

Hardcoded names / shared mutable dependencies that prevent two full M7 twins on one daemon (exact locations):

- `env2_compose` project literal: `scripts/m7/canonical_snapshot.py:43`, `scripts/domain/runtime_overlay.py:38`, `RED_LOOP/m6/clean_boot.py:38,43`, `RED_LOOP/m7/reset_proof.py:79` (container `env2_compose-s2p-mysql-1`), `RED_LOOP/red_loop/judge.py:23-27` (five container names), `RED_LOOP/red_loop/broker.py:172`, `RED_LOOP/m7/s2p_stack.py:22` (env-overridable default).
- `rzp-arena` network literal in journey/proof transports: `RED_LOOP/m7/reset_proof.py:16`, `RED_LOOP/m7/s2p_three_runs.py:14` (M6 `framework.py` and `up.sh` honour `ARENA_SUFFIX`).
- Host-side generated state in the checkout (`ENV2_COMPOSE/secrets/`, `config/generated/`, `seeds/generated/`, `.runtime/arena-fingerprint.json`) — one boot per working copy.
- `.env.arena` absolute migration-dir paths (`PAYOUTS_REPO_MIGRATIONS_DIR=/Users/rana.singh/…`) and `.local/repos-root` → shared read-only inputs (acceptable) but host-specific.
- `app_vendor_payments` fixed secret `local-vendor-payments-secret` (S2P `boot.go`), `boundary:8080`, `kafka`, `mysql`, `redis` hostnames — fine within a private network, but the S2P source driver cannot be pointed elsewhere.
- Ports: only kong-lite; S2P closure compose projects use ports? none published (isolation via project networks). M5 benchmark uses host ports 8400+.
- Shared image tags (`rzp-arena/*:v1-candidate`, S2P image by source sha) are read-only and shareable.
- Journey campaign stamp with minute resolution collides if two `run.py` run at once (M7 memory), and merchant id space `ARENAM…` is per-datastore so separate instances do not collide.

Measurements (existing evidence, this host):

| Quantity | Value | Source |
|---|---|---|
| Memory, full M7 twin (98 containers) + leftover 5-container S2P project | VM `free -m`: 8,230 MB used of 11,934 (buff/cache 3,479); `docker stats` sum ≈ 7.9 GiB of which the arena ≈ 7.2 GiB (kafka 960 MB, localstack 440 MB, 5 MySQL ≈ 2.1 GB, 25 payouts workers ≈ 1 GB, 27 FTS workers ≈ 0.7 GB, S2P mysql 465 MB) | live `docker stats --no-stream` |
| Memory, 65-container M3.1 arena | idle peak 4.18 GiB | `resource-profile.json` |
| Disk: images 42.06 GB total (rzp-arena set ≈ 3.7 GB across `:local`+`:v1-candidate` tags; base images ≈ 8 GB); active volumes 7.33 GB (arena volumes ≈ 3.0 GB incl. kafka 1.1 GB); build cache 28.7 GB (21.5 GB reclaimable); VM disk 47 of 59 GB used (85%) | `docker system df`, `colima ssh df` |
| Disk: sources | clones 4.3 GB (`~/rzp-payouts-clones`), S2P source root 594 MB, accepted copies 48 MB, build evidence 200 MB, repo `.git` 38 MB, `reports/` 182 MB, `RED_LOOP/runs` 54 MB | `du` |
| Clean build time (5 core images) | payouts 26-39 s, ledger 22 s, … (per-service `elapsed_seconds` in `build-evidence-20260905T160213Z/build-results.json`); S2P runtime image ≈ 2.5 min (runbook) | |
| Boot time (down -v + secrets + config + preflight + datastores + migrations + seeds + substitutes + core + health) | ≈ 2 min (run `m6-clean-boot-20260908T071402Z`: 07:14:02Z → 07:16:00Z, 94 containers, no journeys) | run dir |
| Reset time | ingress reset: one HTTP call; S2P down + up + first query ≈ 76 s (`m7-reset-proof.json` 09:25:03 → 09:26:19) | |
| Journey suite (111 journeys) | 1,985 s of journey time, ≈ 59 min wall including merchant provisioning (run 074458Z: boot 07:44:58 → journeys done 08:45:29) | `summary.json`, run dir |
| S2P standalone clean run ×3 + acceptance | ≈ 25 min wall (post-integration driver 09:16 → 09:41:35 per acceptance `generated_at`) | |

Maximum number of full twins on this host, without guessing: **1 implemented and experimentally proven** (every accepted milestone ran exactly one arena on the single colima daemon; the S2P closure additionally ran up to three small S2P projects beside it and recorded that this was already unsafe for acceptance). **A second full M7 twin on the same daemon is blocked by compute**: the VM has 12 GiB with ~8.2 GB in use; a second twin needs ≈ 7 GB more and would also need ≈ 3 GB of fresh volumes on a VM disk at 85%. The host itself (24 GiB RAM, 15 CPUs, 307 GB free) could in theory carry a second colima VM of 8-10 GiB for one more twin — **theoretically possible, not implemented, not proven**, and it would leave the host with < 4 GiB for everything else. Isolation-wise, two twins on one daemon are **implemented** (instance-plan/clean-boot, separate working copy) but were only ever exercised with the 65-container M3.1 arena, never with the 98-container M7 arena, and the M7-era scripts listed above still hardcode `env2_compose`.

---

## 7. Durable control-plane inventory

Location: `RED_LOOP/red_loop/` (M2-M4), `RED_LOOP/m5/campaign/` (M5), `RED_LOOP/m6/journeys/` (M6/M7), `scripts/m7/` (M7). Tests: `python3 -m unittest discover RED_LOOP/tests` → **102 tests OK** (offline, run now).

| Component | Implementation | Storage | Survives process restart | Survives host restart | >1 campaign / twin | Coupled to | Tests | Recommendation |
|---|---|---|---|---|---|---|---|---|
| Campaign manifest | `state.py:56 write_manifest/read_manifest/update_manifest` | `RED_LOOP/runs/<campaign>/manifest.json` (atomic rename) | yes | yes (disk; runs dir git-ignored) | one dir per campaign; single twin (`env2_compose`) | M2 attacker model (`allocator.py` M1/M2/M3 fixtures) | via campaign tests | **Retain** |
| Campaign API | CLI only: `run.py campaign|resume|contexts|soak|lifecycle-gate|lifecycle-export|recovery-matrix` | — | — | — | — | LiteLLM gateway env | — | Refactor into a library API; no HTTP |
| Persistent campaign state | `state.py CampaignStore` — append-only JSONL, fsync per write, `resume_snapshot`, `state_hash` | 13 ledgers (`hypotheses, actions, observations, candidates, model_calls, events, responses, context_metrics, hypotheses_v2, leases, handoffs, replans, contexts`) | yes | yes | per campaign | none (generic) | `test_hypotheses.py`, `test_leases.py`, `test_soak.py` | **Retain** — the reusable core |
| Event log | `state.py:156 event()` → `events.jsonl` | JSONL | yes | yes | per campaign | none | yes | Retain |
| Director state | M5 `director.py:78 Director` + `Ledger` (`m5-task-ledger.jsonl`, `m5-provenance-events.jsonl`), `checkpoint()` → `m5-checkpoint.json` | JSONL + JSON | yes (proven: injected worker kill + resume, `director.py:214-218`) | yes | per campaign out dir; blind-benchmark engines on host ports 8400+ | M5 benchmark (`wfclient`, families) | M5 acceptance evidence, no unit tests | Refactor: keep checkpoint/ledger pattern, drop benchmark coupling |
| Thesis / hypothesis records | `hypotheses.py HypothesisManager` — 14-state machine, semantic fingerprint, dup suppression, priority, blockers | `hypotheses_v2.jsonl` (legacy `hypotheses.jsonl` projected) | yes | yes | per campaign | none | `test_hypotheses.py` (14) | **Retain** |
| Task queue | absent as a queue; hypotheses ordered by `priority` and claimed via leases | — | — | — | — | — | — | Build minimal queue on the ledger |
| Task leases | `leases.py LeaseManager` — acquire/heartbeat/release/reap_expired/reassign/recover_on_resume | `leases.jsonl`, `handoffs.jsonl` | yes | yes | per campaign | none | `test_leases.py` (6) | **Retain** |
| Scheduler | `soak.py Soak` (cycle over context policies, wall/cycle budgets, stagnation) ; `refresh.py` cron/launchd examples (documented, not installed) | JSON summary | checkpoint every cycle | yes | single twin | contexts/M4 | `test_soak.py` | Refactor: generic loop, inject work source |
| Worker registration | absent (owners are strings in leases; `contexts.py` policies are the "workers") | — | — | — | — | — | — | Build |
| Checkpoints | `campaign.py` per-turn `turns_completed` in manifest; M5 `checkpoint()`; `soak.py` `checkpoint_every` | JSON | yes | yes | per campaign | — | `test_soak.py`, `test_recovery_matrix.py` | Retain pattern |
| Crash recovery | `run.py:225 resume_campaign_cmd` (+ lease recovery), `surface/m4_recovery_matrix.py` (7 scenarios incl. process kill, partial artifact, interrupted replay/acceptance) | — | proven | proven for process; host restart not exercised | single | M4 | `test_recovery_matrix.py` (9) | Retain |
| Model routing | `config.py PRIMARY/REPRODUCER/UTILITY_MODEL`, `llm.py pick_fallback/list_models`, `campaign.py _complete_with_model` (blank/filtered → smaller packet → switch model), `registry/model-selection.json` (27 gateway models) | env + JSON | n/a | n/a | shared | LiteLLM gateway (`LITELLM_BASE_URL/KEY`) | none directly | Retain |
| Typed tool broker | `tools.py TOOLS` (18 tool schemas) + `Dispatcher` (allow-list filter, lifecycle tools); `broker.py Broker` (kong-lite-only HTTP, fixed identity, denied fragments, request budget) | — | n/a | n/a | one attacker identity per campaign | M2 attacker boundary, `127.0.0.1:18080` | `test_route_policy.py`, `test_kong_passport.py`, `test_judge_admission.py` | Retain schema pattern; generalise identity injection |
| Budgets | `broker.request_budget`, `campaign.py` turn/wall ceilings, `soak.py` wall/cycle, `llm.Usage` token accounting; no cost table (gateway exposes none) | manifest/events | yes | yes | per campaign | — | yes | Retain |
| Capability graph | absent (the functional graph is not consulted by any control plane) | — | — | — | — | — | — | Build via query service |
| Finding registry | `candidates.jsonl` + `judge.py` verdicts + `registry/known_gaps.yaml` (judge-only) + `reproducer.py`; M5 `verify/verifier.py` | JSONL/YAML | yes | yes | per campaign | M2-M5 twin specifics | `test_judge_admission.py` | Refactor: keep verdict vocabulary |
| Memory | `context.py compile_state_message` (packet compiled from durable store each turn, `recall` tool) | derived | yes (from ledgers) | yes | per campaign | — | `test_contexts.py` | Retain |
| Replay requests | `hypotheses.py` `replay_requested → reproduced/rejected`, `tools.request_replay`, `reproducer.py` (different model family), `surface/m4_replay.py` (stub) | JSONL | yes | yes | per campaign | M4 | `test_closing_gate.py` (9) | Retain |
| Journey runner | `RED_LOOP/m6/journeys/{framework,run}.py` — per-journey evidence JSON written immediately, `summary.json` at end, `MerchantPool` descriptors, `--only/--family`, fingerprint bound | run dir | per-journey files survive; **no resume** of a partial suite | yes | single twin (`env2_compose`), one runner at a time | M6/M7 | none (proven by runs) | Retain; add resume/parallel-safe stamp |

Not a durable service: everything under `RED_LOOP/m6`, `RED_LOOP/m7`, `RED_LOOP/surface/m*_acceptance.py`, `scripts/m7/*` — these are milestone scripts with hardcoded paths, one twin, one daemon.

---

## 8. Schema and report inconsistencies

**A. PU-1..PU-8 vs PU-9, PU-10.** `scripts/m7/canonical_snapshot.py:26-35` hardcodes `UNKNOWNS` with eight entries; `reports/implementation/M7_PRODUCTION_UNKNOWNS.md` was hand-written in the same commit (`0b726c4`) with ten rows (`git show 0b726c4:… | grep -c PU-9\|PU-10` → 2; the script at that commit → 8 ids). PU-9 (monolith dashboard approve variant) and PU-10 (purposes/permissions/rate limits) were added to the prose only; the markdown even says the machine-readable list is PU-1..PU-8. `final_report.py:56` and the final report say "PU-1..PU-10". No generator links the two.

**B. Machine-authoritative list.** Neither list is evaluated by any gate: `acceptance.py` gate M7-21 only checks that the six report files exist, contain no parity phrases and one allowed ownership classification. Both files are hash-bound (the snapshot via `canonical_snapshot_sha256`, the markdown via `M7_ARTIFACT_HASHES.json`), so **both are immutable evidence and neither is authoritative for acceptance**. By the truth-precedence rule (machine evidence before reports) the snapshot's PU-1..8 is the machine list and PU-9/10 are report-level annotations. The S2P baseline adds four more ids (`spec/unresolved-production-unknowns.yaml`) that are referenced by PU-7 but not merged.

**C. 242 / 147 / 364 / 265.** Two populations are in play (recomputed from the graph, this session):
- **364** = P0 nodes whose `kind ∈ {service, worker, identity, datastore, table, queue, topic, cron, route, substitute}` (`build_graph.py:187`). Executable **265** = those 364 with `fidelity ∈ {real_source_running, high_fidelity_replacement}` (`:195`) = 161 + 104.
- **525** = P0 nodes of any kind except `journey`/`family` (`canonical_snapshot.py:71`), i.e. 364 + 161 others (event 29, external 23, flag 31, repository 15, role 1, state 62). Label histogram over 525: ACTUAL_SOURCE_RUNNING **242**, CONTRACT_FAITHFUL_REPLACEMENT **147**, SOURCE_MAPPED 39, STUB 14, GRAPH_ONLY 83. Over the 364 population the same labels are 161 / 104 / 29 / 8 / 62 — so 242 + 147 = 389 is **not** comparable with 265; they measure different populations (and 161 + 104 = 265 shows the label and fidelity vocabularies agree exactly on the 364 set; `m7_label` overrides never contradict `fidelity`, 0 mismatches).

**D. Explicit numerator/denominator/population?** In the snapshot JSON: `coverage.mapping` has all three (364/364, definition implicit in `build_graph.py`); `coverage.executable` has numerator 265 and pct but **no denominator field** (364 must be borrowed from `mapping`); `coverage.actual_source_runtime` and `contract_faithful_replacement` carry `p0` and `p0_pct` but the **denominator 525 is nowhere in the snapshot** — it is recomputed by `fidelity_matrix.py:45` as the sum of the P0 histogram; no population definition string exists for it; `all_nodes` counts have no denominator (3065 is in `payouts_graph.nodes`).

**E. Could a report present them as one denominator?** Yes: `M7_FINAL_REPORT.md:10-13` prints "364/364 … 265/364 (72.8%) … 242 P0 nodes (46.1%) … 147 P0 nodes (28.0%)" — the last two rows omit their denominator, so a reader naturally assumes 364 and gets 66.5% / 40.4%. `M7_FIDELITY_MATRIX.md` is correct ("242/525 P0 nodes"). The M7_CANONICAL_SNAPSHOT `label_histogram`/`p0_label_histogram` and `coverage` blocks can be summed by any consumer without a population guard.

**Smallest M8 schema correction (no M7 rewrite):** in the new `ArchitectureSnapshot.coverage`, every measure is `{numerator, denominator, population_id, pct}` with a `populations` table (`p0_critical_kinds: 364`, `p0_all_non_journey: 525`, `all_nodes: 3065`) and a `label_histogram_by_population`; `production_unknowns` becomes the single merged `ProductionUnknown` list (PU-1..10 + the four S2P ids) with `source` and `first_recorded_snapshot_id`; M7's files stay as-is and the importer records the mapping.

---

## 9. Recommended M8 boundary

Evaluation of the options against verified state:

| Criterion | A: compiler only | B: compiler + query service | C: B + initial Twin Factory | D: other |
|---|---|---|---|---|
| Dependency order | first | needs A's `snapshot_id` | needs B (recipes, impact queries) **and** a second daemon/VM that does not exist | — |
| Existing reusable code | high: `capture.py` (36 tests), `build_graph.py`, `diff.py`, `affected.py`, `hashes.py`, S2P `source-lock.json`, `canonical_snapshot.py` labels/PU | medium: index/BFS trivial (measured < 1 ms), `affected.Plan` walks; no server, no index today | low: `instance-plan.py`/`clean-boot.sh` (M3.1, 65-container arena) + recipes 0/98 complete; hardcoded `env2_compose` in M7 tooling | — |
| Overnight feasibility | yes | yes (stdlib HTTP + sqlite index; the graph is 3k nodes) | **no**: second-twin memory is blocked on this host; separate daemon not scripted; 98-container boot ≈ 2 min but suite ≈ 1 h per twin | — |
| Risk of another milestone script | low if the compiler is a library with an id-addressed store | low if the service is snapshot-id-keyed, not "M7"-keyed | high (would be a third `clean_boot.py`) | — |
| Acceptance from clean checkout | yes: compile the M7 import from tracked inputs and compare hashes; no docker needed | yes: query tests against the imported snapshot | no: needs images, clones, daemon; note M7 itself is **not** clean-checkout acceptable (112 of 143 manifest files live in git-ignored `RED_LOOP/runs/`; gates M7-04/20 shell out to docker) | — |
| Dependence on cloud / production info | none | none | Daytona unavailable (report: not run, no CLI/SDK); production facts unchanged (PU list) | — |

**Recommendation: Option B** — immutable snapshot compiler **plus** the architecture query service, with the Twin Factory limited to *schema and readiness work* (ServiceRecipe normalization and a RuntimeInstance record for the existing boot), not provisioning.

Recommended M8 scope:
1. `ArchitectureSnapshot` compiler (library + CLI): inputs = SourceLock + committed parts/contracts/patch; output = `reports/architecture/snapshots/<snapshot_id>/{snapshot.json, inputs.json, coverage.json, unknowns.json}`; `snapshot_id = sha256(canonical body)`; byte-identical on re-run (test); no docker, no wall clock inside the body.
2. Importer: project `M7_CANONICAL_SNAPSHOT.json` (sha `d88a2634…`, tag `twin-m7-shared-ingress-integration`) into snapshot v1 + RuntimeInstance(`boot_id 6cdf3d73…`) + EvidenceBundle(143 files) + AcceptanceRecord(sha `13d7ef0b…`) without modifying any M7 file; gate: projection equality.
3. Split lanes: static graph (A+B) vs runtime overlay (C) vs journey evidence (D); `build_graph.py` gains `--static` (no `zz-runtime-overlay`, journeys as definitions only) and loses timestamps.
4. Query service: stdlib HTTP (loopback) + library, keyed by `snapshot_id`: node/edge/family/journey lookup, path/reachability, fidelity/label filters with population-aware coverage, PU lookup by affected node, impact slice (`affected.Plan`), snapshot diff; sqlite3 index file per snapshot for multi-process reuse; tests.
5. Recipe normalization (schema only): regenerate recipes (add `api-ingress`, `s2p-*`), add `networks/reset/deviations_ref/journeys/image_digest/secrets` fields, one label vocabulary; fix `recipes.py --out`.
6. Merge ProductionUnknown lists (PU-1..10 + 4 S2P) with population-explicit coverage.

Explicitly deferred: second-daemon/VM provisioning (colima profile or remote context), Twin Factory boot orchestration, multi-twin concurrent acceptance, Daytona, any new business domain, any change to M7 evidence or tags, de-hardcoding of `env2_compose` in M7 proof scripts (record as debt; touch only where the query service needs it).

Branch / base / tag: branch `milestone-8-snapshot-compiler-query` from `twin-m7-shared-ingress-integration` (37ac47d); acceptance tag `arch-m8-snapshot-query` on the evidence commit. Leave `s2p_corrected_final_verification` containers for the operator to remove (not part of M7; ~0.6 GB RAM).

Major files likely to change/appear: `scripts/domain/build_graph.py` (static mode, no clock), `scripts/snapshot/{capture,common,recipes,diff,affected}.py` (ids, `--out` fix, recipe fields), new `scripts/arch/{compile,import_m7,serve,index}.py` (or a package `archsnap/`), `reports/domain/SCHEMA.md` (v2 sections), `reports/architecture/snapshots/…`, `Makefile` `m8-*` targets, tests under `scripts/arch/tests/`.

Acceptance gates (machine-computed, clean-checkout, no docker): (1) compiler byte-determinism ×3 runs; (2) `snapshot_id` recomputes from body; (3) M7 import projection equals the imported snapshot and references the M7 sha256s/tag unchanged; (4) historical tags unchanged; (5) no wall-clock/host path/docker field in any snapshot body (regex gate); (6) coverage measures carry numerator/denominator/population; (7) PU list merged, every `affects` id resolves; (8) query service: lookup/path/slice/diff tests pass against the imported snapshot and a synthetic fixture; (9) p50 query latency recorded (< 10 ms target from the measurements); (10) recipes exist for all 98 canonical compose services with the six added fields; (11) tree clean; (12) `scripts/snapshot` 36 tests + RED_LOOP 102 tests still pass.

Resource requirements: host only; CPU-light; < 200 MB RSS per process (measured 137 MB with everything loaded); disk < 50 MB per snapshot directory (do not embed the 27,994-node S2P patch — reference it by sha as M7 does).

Unresolved user decision (genuinely needed): whether the M8 query service must serve the full S2P namespace graph (27,994 nodes, 89 MB in memory, 47 ms parse) as first-class nodes, or only the 930-node projection plus the patch by reference as M7 does. Everything else in this recommendation follows from verified repository state.
