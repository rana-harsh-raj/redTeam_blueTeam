#!/usr/bin/env python3
"""Turn reports/implementation/m6-journeys.json (the executed M6 journey suite) into a graph part.

Each executed journey becomes a journey:* node whose fidelity is real_source_running when it PASSed
(or EXPECTED_FAILURE, which is a faithful reproduction) against the arena, graph_only when BLOCKED/FAIL.
`proves_families` lets one executable path count for every family it demonstrably exercises
(a shared success run exercises fund-transfer execution, FTS status propagation, the Mozart gateway,
public-API ingress and the async worker chain at once) — only the mapping below, grounded in what the
driver asserts, is applied.
"""
import json, sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "reports" / "implementation" / "m6-journeys.json"
OUT = REPO / "reports" / "domain" / "parts" / "m6-journeys.json"

# what a PASSing journey of a family demonstrably exercises beyond its own family
PROVES = {
    # every shared/direct payout is routed FTS -> mozart-sim by bank channel (RBL) — bank-channel-routing
    "family:shared-payouts": ["family:fund-transfer-execution", "family:fts-status-propagation",
                              "family:mozart-gateway", "family:public-api-ingress", "family:async-workers",
                              "family:bank-channel-routing"],
    # the direct journeys are the M4 statement journeys (E statement-first, F duplicate statement,
    # G credit->reversed) run by the real rbl_banking_account_statement worker + xas-sim matching
    "family:direct-payouts": ["family:fund-transfer-execution", "family:fts-status-propagation",
                              "family:mozart-gateway", "family:public-api-ingress", "family:async-workers",
                              "family:balance-refresh-reservations", "family:bank-channel-routing",
                              "family:statement-reconciliation"],
    "family:queued-low-balance": ["family:async-workers", "family:accounting"],
    "family:scheduled-payouts": ["family:async-workers"],
    "family:webhooks": ["family:async-workers"],
    "family:approval-workflow": ["family:internal-service-routes"],
    "family:bulk-payouts": ["family:async-workers", "family:internal-service-routes"],
}
# substitutes a journey of a family exercises (grounded in the driver modules under RED_LOOP/m6/journeys)
DEPENDS_ON_SUBS = {
    "family:bulk-payouts": ["sub:batch-sim"],
    "family:approval-workflow": ["sub:workflow-engine"],
    "family:failure-reversal-cancellation": ["sub:workflow-engine"],
    "family:on-hold": ["sub:monolith-stub"],
    "family:pricing-free-payouts": ["sub:monolith-stub", "sub:pricing-stub"],
    "family:webhooks": ["sub:stork-capture", "sub:merchant-webhook-sink"],
    "family:direct-payouts": ["sub:xas-sim", "sub:bankingaccounts-stub"],
    "family:statement-reconciliation": ["sub:xas-sim"],
}
DEPENDS_ON_ALL = ["sub:kong-lite", "sub:mozart-sim", "sub:monolith-stub", "sub:dcs-stub", "sub:splitz-stub"]
# variant-specific extra families
VARIANT_PROVES = {
    "fetch": ["family:fetch-list"], "fetch_list": ["family:fetch-list"], "list": ["family:fetch-list"],
    "source_update": ["family:source-updates"], "source_updates": ["family:source-updates"],
}


def main():
    if not SRC.exists():
        print("no m6-journeys.json yet"); return 1
    jr = json.loads(SRC.read_text())
    nodes, edges = [], []
    for j in jr.get("journeys", []):
        res = j.get("result")
        fid = "real_source_running" if res in ("PASS", "EXPECTED_FAILURE") else "graph_only"
        if res in ("PASS", "EXPECTED_FAILURE") and (j.get("fidelity") or "").startswith("substitute"):
            fid = "high_fidelity_replacement"
        proves = list(PROVES.get(j.get("family"), [])) + list(VARIANT_PROVES.get(j.get("variant"), []))
        n = {"id": j["id"], "kind": "journey", "label": f"{j.get('family','')}/{j.get('variant','')} [{res}]",
             "owner_domain": "payouts", "family": j.get("family"), "variant": j.get("variant"),
             "criticality": j.get("priority") or "P0", "fidelity": fid, "confidence": "confirmed",
             "result": res, "evidence": j.get("evidence_path"), "merchant_id": j.get("merchant_id"),
             "missing_dependency": j.get("missing_dependency"), "notes": (j.get("notes") or "")[:300],
             "fidelity_evidence": f"m6-journeys.json result={res} run_dir={jr.get('run_dir')}",
             "twin_ref": "RED_LOOP/m6/journeys/run.py",
             "proves_families": proves if res in ("PASS", "EXPECTED_FAILURE") else []}
        nodes.append(n)
        if j.get("family"):
            edges.append({"from": j["id"], "to": j["family"], "type": "implements", "confidence": "confirmed"})
        for sub in DEPENDS_ON_SUBS.get(j.get("family"), []) + DEPENDS_ON_ALL:
            edges.append({"from": j["id"], "to": sub, "type": "depends_on", "confidence": "confirmed",
                          "via": "substitute exercised by this journey"})
    part = {"lane": "m6-journeys", "generated_at": datetime.now(timezone.utc).isoformat(),
            "sources": [{"repo": "twin", "path": str(SRC.relative_to(REPO)), "run_dir": jr.get("run_dir"),
                         "git_head": jr.get("git_head"), "boot_id": (jr.get("fingerprint") or {}).get("boot_id")}],
            "nodes": nodes, "edges": edges, "families": []}
    OUT.write_text(json.dumps(part, indent=1))
    print(f"{len(nodes)} journey nodes -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
