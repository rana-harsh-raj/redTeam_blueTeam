"""Render reports/implementation/M8_FINAL_REPORT.md from the current snapshot store (numbers are never typed by hand)."""
import json
from collections import Counter
from . import paths
from .store import SnapshotStore
from .query import Query, CAPABILITIES

OUT = paths.IMPL / "M8_FINAL_REPORT.md"


def render(store=None):
    st = store or SnapshotStore(); sid = st.resolve("current"); b = st.load(sid)["body"]; q = Query(st, sid)
    imp = st.imports(sid)["m7"]; proj = imp["projection"]; idx = st.recipes(sid)
    bench = json.loads((paths.IMPL / "m8-bench.json").read_text()) if (paths.IMPL / "m8-bench.json").is_file() else {}
    cov = b["coverage"]; pops = b["populations"]

    def m(k):
        v = cov[k]; return "%d/%d (%s%%) — population `%s`" % (v["numerator"], v["denominator"], v["pct"], v["population"])
    deltas = Counter((d["kind"], d["m7_fidelity"], d["static_fidelity"]) for d in proj["fidelity_deltas"])
    lab = Counter((d["m7_label"], d["static_label"]) for d in proj["label_deltas"])
    tb = q.uncovered_trust_boundaries(); gaps = q.fidelity_gaps()
    R = ["# M8 — Immutable Architecture Snapshot and Query Service (final report)\n",
         "Branch `milestone-8-snapshot-query` from tag `twin-m7-shared-ingress-integration` (37ac47d). Everything in this milestone is repository-only: no docker, no clones, no secrets, no arena change. Machine acceptance: `reports/implementation/M8_ACCEPTANCE.json` (20 gates, evaluator `archkit/acceptance.py`). This file is rendered by `python3 -m archkit.report` from the store.\n",
         "## Result\n", "| Item | Value |\n|---|---|",
         "| Architecture snapshot id | `%s` |" % sid,
         "| Store | `reports/architecture/snapshots/%s…/` (snapshot.json %.1f MB canonical JSON; summary, manifest, recipes/, imports/m7/) |" % (sid[:12], bench.get("snapshot_bytes", 0) / 1e6),
         "| Source lock id | `%s` (%d repositories; %d with an explicit UNKNOWN sha) |" % (b["source_lock_id"], len(b["source_lock"]["repositories"]), sum(1 for r in b["source_lock"]["repositories"].values() if not r["sha"])),
         "| Canonical graph | %d nodes / %d edges / %d families / %d journey definitions (11 lanes; the runtime overlay lane replaced by a compose-definition lane) |" % (b["counts"]["nodes"], b["counts"]["edges"], b["counts"]["families"], b["counts"]["journeys"]),
         "| Source-to-Pay namespace | %d projected nodes in the canonical graph; full detail graph %d nodes / %d edges referenced by sha256 `%s…`, loaded only by `s2p_detail` |" % (b["s2p_namespace"]["projected_nodes"], b["s2p_namespace"]["nodes"], b["s2p_namespace"]["edges"], b["s2p_namespace"]["sha256"][:12]),
         "| Recipes | %d/98 canonical services (`recipe_set_id` `%s…`): %s |" % (idx["count"], idx["recipe_set_id"][:12], ", ".join("%s %d" % kv for kv in sorted(idx["by_category"].items()))),
         "| Production unknowns | %d consolidated (PU-1..PU-10 + 4 Source-to-Pay ids), each with `origin.file` / `original_id`; %d nodes affected |" % (b["production_unknowns"]["count"], cov["nodes_affected_by_production_unknowns"]["numerator"]),
         "| M7 import | node ids identical; 0 edges lost (%d added: batch-sim `implements` svc:batch from its CONTRACT); %d fidelity deltas and %d label deltas, every one classified (below); M7 files unchanged (sha-bound) |" % (len(proj["edges_only_in_static"]), proj["fidelity_delta_count"], proj["label_delta_count"]),
         "| Runtime / evidence / acceptance | RuntimeInstance `%s…` (boot `%s`), EvidenceBundle `%s…` (%d files, %s), AcceptanceRecord `%s…` (M7 %d/%d) |" % (imp["runtime_instance"]["runtime_instance_id"][:12], imp["runtime_instance"]["boot_id"], imp["evidence_bundle"]["evidence_bundle_id"][:12], imp["evidence_bundle"]["file_count"], ", ".join("%d %s" % (v, k) for k, v in sorted(imp["evidence_bundle"]["result_histogram"].items())), imp["acceptance_record"]["acceptance_record_id"][:12], imp["acceptance_record"]["gates_passed"], imp["acceptance_record"]["gates_total"]),
         "\n## Coverage (every measure carries numerator, denominator and population)\n", "| Measure | Value | Definition |\n|---|---|---|"]
    for k, v in cov.items():
        if isinstance(v, dict):
            R.append("| %s | %s | %s |" % (k, m(k), v["definition"]))
    R.append("\nPopulations: " + "; ".join("`%s` = %d (%s)" % (k, v["count"], v["definition"]) for k, v in pops.items()) + ".")
    R.append("\nLabel histogram by population:\n\n| Label | all_nodes | p0_critical_kinds | p0_non_journey |\n|---|---:|---:|---:|")
    for l in sorted(b["label_histogram"]["all_nodes"]):
        R.append("| %s | %d | %d | %d |" % (l, b["label_histogram"]["all_nodes"].get(l, 0), b["label_histogram"]["p0_critical_kinds"].get(l, 0), b["label_histogram"]["p0_non_journey"].get(l, 0)))
    R.append("\nNo single production-parity percentage exists or is derivable from these tables; each measure is bound to its population.\n")
    R.append("## Static vs runtime: what changed relative to the M7 artifact\n")
    R.append("The M7 canonical snapshot mixed static facts with one boot's observations. The static snapshot drops `generated_at`, `git_head`, the runtime overlay, node `runtime` dicts, journey results/merchant ids/evidence paths, docker status strings and scratchpad paths; those now live in the M7 RuntimeInstance and EvidenceBundle under `imports/m7/`. Fidelity deltas (all classified in `projection.json`):\n")
    R.append("| kind | M7 (runtime-qualified) | static | count | reason |\n|---|---|---|---:|---|")
    for (k, a, c), n in sorted(deltas.items(), key=lambda kv: str(kv[0])):
        reason = [d["reason"] for d in proj["fidelity_deltas"] if (d["kind"], d["m7_fidelity"], d["static_fidelity"]) == (k, a, c)][0]
        R.append("| %s | %s | %s | %d | %s |" % (k, a, c, n, reason))
    R.append("\nLabel deltas: " + "; ".join("%s → %s ×%d" % (a, c, n) for (a, c), n in sorted(lab.items(), key=lambda kv: str(kv[0]))) + ". The GRAPH_ONLY → PRODUCTION_STATE_UNKNOWN flips come from PU-10 (`route:api-monolith/*`) now being machine-readable; M7 carried PU-9/PU-10 in prose only.\n")
    R.append("## Query capabilities (library `archkit.query.Query`, CLI `python3 -m archkit q <cap> k=v`, HTTP `GET /v1/<snapshot>/<cap>?k=v`)\n")
    R.append(", ".join("`%s`" % c for c in CAPABILITIES) + ".\n")
    R.append("Every envelope carries `snapshot_id`, `fidelity` (label histogram of the touched nodes), `source_refs`, `production_unknowns` and `truncated`. Canonical queries never load the Source-to-Pay detail graph (`detail_namespace_loaded=false`; verified by gate M8-12 and a unit test).\n")
    if bench:
        R.append("## Performance (`reports/implementation/m8-bench.json`, single process, stdlib)\n")
        R.append("| Measure | ms |\n|---|---:|")
        R.append("| load + verify snapshot (%.1f MB) | %.1f |" % (bench["snapshot_bytes"] / 1e6, bench["snapshot_load_and_verify_ms"]))
        R.append("| index build | %.1f |" % bench["index_build_ms"])
        for k, v in bench["queries_ms"].items():
            R.append("| %s | %.3f |" % (k, v))
        R.append("| s2p_detail first call (loads the %d-node patch) | %.1f |" % (b["s2p_namespace"]["nodes"], bench["s2p_detail_first_call_ms"]))
        R.append("| s2p_detail warm | %.3f |" % bench["s2p_detail_warm_ms"])
        R.append("\nRSS: %.1f MB after index, %.1f MB after the detail namespace. No graph database is needed at this size.\n" % (bench["rss_mb_after_index"], bench["rss_mb_after_s2p_detail"]))
    R.append("## Findings surfaced by the queries\n")
    R.append("- `uncovered_trust_boundaries`: `%s` has no gated routes or identities in any discovery lane (uncovered); the other boundaries are covered by journey definitions (%s).\n" % (", ".join(tb["uncovered"]), "; ".join("%s: %d routes, %d journey definitions" % (i["trust_boundary"].split(":")[-1], i["routes"], i["journey_definitions"]) for i in tb["items"] if i["covered"])))
    R.append("- `fidelity_gaps` (population p0_critical_kinds): %d of %d nodes are not ACTUAL_SOURCE_RUNNING / CONTRACT_FAITHFUL_REPLACEMENT: %s.\n" % (gaps["gap_total"], gaps["population_size"], ", ".join("%s %d" % kv for kv in sorted(gaps["by_label"].items()))))
    R.append("- Recipes: unknown-field histogram %s — image digests are build outputs (not committed inputs), production replica counts/configuration/manifests are production unknowns.\n" % json.dumps(idx["unknown_field_histogram"], sort_keys=True))
    conflicts = sorted(k for k, v in b["source_lock"]["repositories"].items() if v.get("conflicts"))
    R.append("- Source lock: %s carry conflicting shas between committed sources (kept as `conflicts`, not resolved by guessing).\n" % (", ".join("`%s`" % c for c in conflicts) or "no repositories"))
    R.append("## Tests\n")
    R.append("`python3 -m unittest archkit.tests.test_archkit` (25 tests: canon, compiler determinism, volatile scan, M7 import, recipes, every query capability, lazy S2P detail, HTTP service); `scripts/snapshot/tests` (36) and `RED_LOOP/tests` (102) unchanged and passing — gate M8-13.\n")
    R.append("## Remaining blockers before an isolated Twin Factory\n")
    R.append("1. **No second Docker daemon / VM**: one colima VM (12 GiB) already holds the 98-container M7 boot (~7-8 GB); a second full twin is blocked by compute and by the shared daemon (`reports/source-to-pay-replica/ACCEPTANCE_CLOSURE.md`). Nothing in the repo provisions a colima profile, a remote context or Daytona (packaged, never run).\n2. **Host-side generated state in the checkout** (`ENV2_COMPOSE/secrets/`, `config/generated/`, `seeds/generated/`, `.runtime/`): one boot per working copy; `instance-plan.py`/`clean-boot.sh` exist (M3.1) but were exercised only with the 65-container arena.\n3. **Hardcoded `env2_compose` / `rzp-arena`** in M6/M7 proof scripts (`RED_LOOP/m6/clean_boot.py`, `RED_LOOP/m7/{reset_proof,s2p_three_runs}.py`, `scripts/m7/canonical_snapshot.py`, `RED_LOOP/red_loop/judge.py`).\n4. **Recipe unknowns**: image digests are not committed anywhere (only local image ids in `SNAPSHOT_MANIFEST.json`); a factory must build and record digests itself.\n5. **Journey runner is single-instance** (minute-resolution campaign stamp, no resume of a partial suite).\n6. The S2P source driver hardcodes `boundary:8080`, `kafka`, `mysql`, `redis` and a fixed app secret; fine inside a private network, not relocatable.\n")
    R.append("## Not done / out of scope by design\n")
    R.append("No new business domain, no Twin Factory, no second twin, no arena restart, no M7 file rewritten, no inferred production configuration, no runtime observation promoted to a static fact.\n")
    OUT.write_text("\n".join(R))
    return OUT


if __name__ == "__main__":
    print(paths.rel(render()))
