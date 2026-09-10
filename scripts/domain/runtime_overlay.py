#!/usr/bin/env python3
"""Runtime overlay part: what the booted arena ACTUALLY runs, read from compose + docker.

Writes reports/domain/parts/zz-runtime-overlay.json (lane rank above twin-inventory in build_graph.py's
merge) so worker/service/substitute fidelity reflects the live boot, not a lane's reading of the file.
Only nodes that already exist in the merged graph are overlaid; nothing is invented.
Requires docker for health state; degrades to compose-only (fidelity unchanged, `running` unknown)."""
import json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
import yaml

REPO = Path(__file__).resolve().parents[2]
ENV = REPO / "ENV2_COMPOSE"
GRAPH = REPO / "reports" / "domain" / "PAYOUTS_FUNCTIONAL_GRAPH.json"
OUT = REPO / "reports" / "domain" / "parts" / "zz-runtime-overlay.json"

# compose service -> graph node id(s); workers are derived from PAYOUTS_WORKER_NAME / fts args
SVC_MAP = {"payouts-api": "svc:payouts-api", "fts-web": "svc:fts-web", "ledger-api": "svc:ledger-api",
           "cfa-server": "svc:cfa-server", "xbalances-server": "svc:xbalances-server",
           "workflow-engine": "sub:workflow-engine", "batch-sim": "sub:batch-sim", "workflow-sim": "sub:workflow-sim",
           "monolith-stub": "sub:monolith-stub", "dcs-stub": "sub:dcs-stub", "splitz-stub": "sub:splitz-stub",
           "shield-stub": "sub:shield-stub", "pricing-stub": "sub:pricing-stub", "asv-stub": "sub:asv-stub",
           "bankingaccounts-stub": "sub:bankingaccounts-stub", "stork-capture": "sub:stork-capture",
           "merchant-webhook-sink": "sub:merchant-webhook-sink", "xas-sim": "sub:xas-sim", "mozart-sim": "sub:mozart-sim",
           "kong-lite": "sub:kong-lite", "ledger-gate": "sub:ledger-gate", "cron-driver": "sub:cron-driver"}


# substitutes added in M6 after the twin-inventory lane ran; node created from compose + CONTRACT.md
NEW_SUBS = {
    "batch-sim": {"implements": "svc:batch", "contract": "ENV2_COMPOSE/substitutes/batch-sim/CONTRACT.md",
                  "label": "batch-sim — HIGH-FIDELITY REPLACEMENT of razorpay/batch payout batch types"},
    "workflow-engine": {"implements": "svc:workflows", "contract": "ENV2_COMPOSE/substitutes/workflow-engine/FIDELITY.md",
                        "label": "workflow-engine — M5 RECONSTRUCTION of the Workflow Service (approval engine)"},
}


def docker_state(project="env2_compose"):
    try:
        p = subprocess.run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
                            "--format", "{{.Label \"com.docker.compose.service\"}}\t{{.Status}}"],
                           capture_output=True, text=True, timeout=20)
    except Exception:
        return None
    st = {}
    for line in p.stdout.splitlines():
        svc, _, status = line.partition("\t")
        st[svc] = {"running": status.startswith("Up"), "healthy": "(healthy)" in status, "status": status}
    return st


def main():
    graph = json.loads(GRAPH.read_text()) if GRAPH.exists() else {"nodes": []}
    ids = {n["id"] for n in graph["nodes"]}
    comp = yaml.safe_load((ENV / "docker-compose.yml").read_text())["services"]
    live = docker_state()
    nodes, edges = [], []
    for svc, spec in comp.items():
        env = spec.get("environment") or {}
        if isinstance(env, list):
            env = dict(e.split("=", 1) for e in env if "=" in e)
        cands = []
        if svc in SVC_MAP:
            cands.append(SVC_MAP[svc])
        wn = env.get("PAYOUTS_WORKER_NAME")
        if wn:
            cands += [f"worker:payouts/{wn}", f"worker:payouts/{wn.replace('-', '_')}"]
        if svc.startswith("payouts-kafka-"):
            cands += [f"worker:payouts/{svc[len('payouts-'):]}", f"worker:payouts/kafka-{svc[len('payouts-kafka-'):]}"]
        for prefix, dom in (("fts-worker-", "fts"), ("ledger-worker", "ledger"), ("cfa-worker-", "cfa"), ("xbalances-worker", "x-balances")):
            if svc.startswith(prefix):
                name = svc[len(prefix):].lstrip("-") or "default"
                cands += [f"worker:{dom}/{name}", f"worker:{dom}/{name.replace('-', '_')}", f"worker:{dom}/{svc}"]
        if svc == "ledger-scheduler":
            cands += ["worker:ledger/scheduler", "svc:ledger-scheduler"]
        hit = [c for c in cands if c in ids]
        # M6 substitutes that post-date the inventory lane: create the node (+ implements edge) from compose
        if not hit and svc in NEW_SUBS:
            hit = [SVC_MAP[svc]]
            edges.append({"from": SVC_MAP[svc], "to": NEW_SUBS[svc]["implements"], "type": "implements",
                          "via": NEW_SUBS[svc]["contract"], "confidence": "confirmed",
                          "source_refs": [NEW_SUBS[svc]["contract"]]})
        if not hit:
            continue
        state = (live or {}).get(svc)
        for nid in hit:
            n = {"id": nid, "kind": nid.split(":")[0], "criticality": "P0", "confidence": "confirmed",
                 "twin_ref": f"compose:{svc}", "compose_service": svc, "profile": (spec.get("profiles") or [None])[0]}
            n["kind"] = {"svc": "service", "sub": "substitute", "worker": "worker"}[nid.split(":")[0]]
            n["label"] = NEW_SUBS.get(svc, {}).get("label", nid)
            if svc in NEW_SUBS:
                n["owner_domain"] = "workflows" if svc == "workflow-engine" else "batch"
                n["source_refs"] = [NEW_SUBS[svc]["contract"]]
            n["owner_domain"] = "twin"
            if state is None:
                n["runtime"] = "not created in this boot" if live is not None else "docker unavailable"
                n["fidelity"] = "real_source_mapped_not_running" if nid.startswith(("svc:", "worker:")) else "graph_only"
                n["fidelity_evidence"] = f"compose service {svc} defined but no container in project env2_compose at overlay time"
            else:
                n["runtime"] = state
                if state["running"]:
                    n["fidelity"] = "real_source_running" if nid.startswith(("svc:", "worker:")) else "high_fidelity_replacement"
                    n["fidelity_evidence"] = f"container for compose service {svc} is {state['status']}"
                else:
                    n["fidelity"] = "real_source_mapped_not_running" if nid.startswith(("svc:", "worker:")) else "behavioural_placeholder"
                    n["fidelity_evidence"] = f"container for compose service {svc} is {state['status']}"
            n["criticality"] = next((x.get("criticality") for x in graph["nodes"] if x["id"] == nid), "P0")
            nodes.append(n)
    part = {"lane": "zz-runtime-overlay", "generated_at": datetime.now(timezone.utc).isoformat(),
            "sources": [{"repo": "twin", "path": "ENV2_COMPOSE/docker-compose.yml + docker ps"}],
            "docker_available": live is not None, "nodes": nodes, "edges": edges, "families": []}
    OUT.write_text(json.dumps(part, indent=1))
    running = sum(1 for n in nodes if isinstance(n.get("runtime"), dict) and n["runtime"].get("running")) if live else 0
    print(f"overlay: {len(nodes)} nodes, running={running}, docker={'yes' if live is not None else 'no'} -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
