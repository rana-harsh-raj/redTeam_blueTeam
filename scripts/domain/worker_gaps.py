#!/usr/bin/env python3
"""Explicit-blocker overlay for P0 workers/crons that the twin does not run.

M6 metric 6 requires every important consumer/status-return/reconciliation/webhook path to be
executable OR explicitly blocked with an exact missing dependency. This script emits
reports/domain/parts/zz-worker-gaps.json: for each P0 worker/cron in the merged graph whose
fidelity is not executable, a `missing_dependency` (+ evidence) from the table below or from
ENV2_COMPOSE/fts-worker-gaps.json (written by the FTS topology lane). Nodes with no entry are
listed so the gap table can be extended; the acceptance gate treats a missing entry as unresolved.
"""
import json, re, sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GRAPH = REPO / "reports" / "domain" / "PAYOUTS_FUNCTIONAL_GRAPH.json"
OUT = REPO / "reports" / "domain" / "parts" / "zz-worker-gaps.json"
FTS_GAPS = REPO / "ENV2_COMPOSE" / "fts-worker-gaps.json"
EXEC = {"real_source_running", "high_fidelity_replacement", "behavioural_placeholder"}

# id-pattern -> (missing dependency, evidence). Order matters; first match wins.
TABLE = [
    (r"^worker:mozart/", "real Mozart binary is unbuildable for this identity: private Go module github.com/razorpay/integrations-utils (404); "
                         "mozart-sim (contract-faithful, RBL-only fixtures) stands in", "ENV2_COMPOSE/substitutes/mozart-sim/CONTRACT.md; .env.arena MOZART note"),
    (r"^worker:stork/", "no Stork runtime in the twin: needs Stork's own MySQL + SQS p0/p1/p2 + scheduler + Trino-backed disable tables; "
                        "stork-capture delivers inline (single attempt, no retry FSM)", "reports/domain/parts/external-config-deploy.md (stork divergence #1)"),
    (r"^worker:workflows/", "real Workflow Service needs a Cadence server + Cassandra (no manifest in kube-manifests, no image tag known); "
                            "the M5 workflow-engine RECONSTRUCTION serves WorkflowAPI/Create + callbacks instead", "reports/domain/parts/identity-ingress.md §3.2; ENV2_COMPOSE/substitutes/workflow-engine/FIDELITY.md"),
    (r"^worker:x-balances/account_activation_events_worker", "banking-accounts is a stub that never publishes account-activation events; "
                            "activation rows are seeded by SQL (seeds/generated/s4/xbalances.sql)", "reports/domain/parts/money-statements.md (BAS publisher)"),
    (r"^cron:x-balances/balance_fetch_per_channel", "cron-driver has no x-balances job and mozart-sim serves account_balance for RBL only; "
                            "per-channel polling for other banks needs mozart fixtures", "ENV2_COMPOSE/scripts/cron-driver; m4-subagent-handoffs/T04.md C4"),
    (r"^worker:xas/", "no XAS runtime: needs the x-account-statements build + Mozart statement fixtures + its MySQL/SQS/Kafka CDC; "
                      "xas-sim covers UTR matching + entity-link dual-write only", "ENV2_COMPOSE/substitutes/xas-sim/CONTRACT.md"),
    (r"^worker:fts/", "no compose service for this FTS bank/channel worker; needs the [queue.worker_queue_map] key + mozart-sim gateway fixtures for that channel",
                      "ENV2_COMPOSE/FTS_WORKER_TOPOLOGY.md"),
    (r"^cron:ledger/", "ledger CronJobs are suspend: true in every prod environment (source-faithful idle)", "kube-manifests cde/ledger"),
]


def main():
    g = json.loads(GRAPH.read_text())
    fts = json.loads(FTS_GAPS.read_text()) if FTS_GAPS.exists() else {}
    nodes, unresolved = [], []
    for n in g["nodes"]:
        if n["kind"] not in ("worker", "cron") or n.get("criticality") != "P0" or n.get("fidelity") in EXEC:
            continue
        dep = ev = None
        if n["id"] in fts:
            dep, ev = fts[n["id"]].get("missing_dependency"), fts[n["id"]].get("evidence")
        else:
            for pat, d, e in TABLE:
                if re.match(pat, n["id"]):
                    dep, ev = d, e; break
        if not dep:
            unresolved.append(n["id"]); continue
        nodes.append({"id": n["id"], "kind": n["kind"], "label": n.get("label") or n["id"], "owner_domain": n.get("owner_domain") or "twin",
                      "criticality": "P0", "confidence": "confirmed", "fidelity": n["fidelity"],
                      "missing_dependency": dep, "fidelity_evidence": (n.get("fidelity_evidence") or "") + f" | blocked_by: {dep} [{ev}]"})
    part = {"lane": "zz-worker-gaps", "generated_at": datetime.now(timezone.utc).isoformat(),
            "sources": [{"repo": "twin", "path": "scripts/domain/worker_gaps.py TABLE + ENV2_COMPOSE/fts-worker-gaps.json"}],
            "nodes": nodes, "edges": [], "families": [], "unresolved": unresolved}
    OUT.write_text(json.dumps(part, indent=1))
    print(f"worker gaps: {len(nodes)} explicit, {len(unresolved)} unresolved -> {OUT}")
    for u in unresolved: print("  UNRESOLVED", u)
    return 0


if __name__ == "__main__":
    sys.exit(main())
