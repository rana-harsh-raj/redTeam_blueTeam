#!/usr/bin/env python3
"""M7 weekly clean-rebuild record (gate 18): binds the clean boot (down -> empty state -> up -> S2P stack -> FULL
suite) and the source-regenerated graph into reports/implementation/m7-weekly-rebuild.json, and records the
weekly re-pin PLAN from scripts/snapshot/refresh.py (the 5 core images were built by build-host.sh before this
milestone and are not rebuilt here; the plan lists that step).

  python3 RED_LOOP/m7/weekly.py            # after `make m7-clean-boot` and `make m7-graph`
"""
import json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
IMPL = REPO / "reports/implementation"
DOM = REPO / "reports/domain"


def load(p):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return {}


def main():
    m6 = load(IMPL / "m6-clean-boot.json"); m7 = load(IMPL / "m7-clean-boot.json"); jr = load(IMPL / "m6-journeys.json"); st = load(DOM / "GRAPH_STATS.json")
    js = jr.get("journeys", [])
    p0_fail = [j["id"] for j in js if (j.get("priority") or "P0") == "P0" and j.get("result") == "FAIL"]
    blocked_nodep = [j["id"] for j in js if j.get("result") == "BLOCKED" and not j.get("missing_dependency")]
    plan = subprocess.run([sys.executable, "scripts/snapshot/refresh.py", "weekly"], cwd=REPO, capture_output=True, text=True)
    graph_ok = bool(st.get("nodes")) and "m7-ingress" in (st.get("lanes") or []) and "zz-s2p-namespace" in (st.get("lanes") or [])
    fp_bound = (jr.get("fingerprint") or {}).get("boot_id") and (jr.get("fingerprint") or {}).get("boot_id") == (m6.get("fingerprint") or {}).get("boot_id")
    doc = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
           "run_dir": m6.get("run_dir"), "from_empty_state": m6.get("from_empty_state"), "up_rc": m6.get("up_rc"), "container_count": m6.get("container_count"),
           "healthy": m6.get("healthy"), "unhealthy": m6.get("unhealthy"), "s2p_stack_healthy": m7.get("s2p_stack_healthy"),
           "shared_ingress_booted_from_clean_state": m7.get("shared_ingress_booted_from_clean_state"),
           "suite": {"total": len(js), "results": {r: sum(1 for j in js if j.get("result") == r) for r in ("PASS", "FAIL", "EXPECTED_FAILURE", "BLOCKED")},
                     "p0_failures": p0_fail, "blocked_without_dependency": blocked_nodep, "families": len({j.get("family") for j in js}),
                     "m7_families": {f: {r: sum(1 for j in js if j.get("family") == f and j.get("result") == r) for r in ("PASS", "FAIL", "EXPECTED_FAILURE", "BLOCKED")} for f in ("family:shared-ingress", "family:cross-domain-s2p")}},
           "journeys_fingerprint_bound": bool(fp_bound), "graph_regenerated": graph_ok,
           "graph": {k: st.get(k) for k in ("nodes", "edges", "families", "lanes", "critical_components_total", "critical_representation_pct", "critical_executable_pct", "critical_runtime_pct")},
           "weekly_plan_rc": plan.returncode, "weekly_plan_tail": plan.stdout[-1500:],
           "note": "core images (payouts/ledger/fts/cfa/x-balances) reused from the M6 build-host.sh run; substitutes rebuilt by compose; S2P runtime image rebuilt from the pinned source by s2p_stack.py build"}
    doc["passed"] = bool(m6.get("from_empty_state") and m6.get("up_rc") == 0 and not m6.get("unhealthy") and m7.get("shared_ingress_booted_from_clean_state")
                        and m7.get("s2p_stack_healthy") and js and not p0_fail and not blocked_nodep and graph_ok and fp_bound)
    (IMPL / "m7-weekly-rebuild.json").write_text(json.dumps(doc, indent=2))
    print(json.dumps({k: doc[k] for k in ("passed", "container_count", "suite", "journeys_fingerprint_bound", "graph_regenerated")}, indent=1))
    return 0 if doc["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
