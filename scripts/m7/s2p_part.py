#!/usr/bin/env python3
"""M7 graph part: the Source-to-Pay namespace projected into the functional graph
(reports/domain/parts/zz-s2p-namespace.json), regenerated deterministically from the accepted replica's own
machine artifacts -- never hand-merged:

  DOMAIN_REPLICAS/source_to_pay/integration/unified-graph-patch.json   (27,994 nodes / 34,744 edges, namespace source_to_pay)
  DOMAIN_REPLICAS/source_to_pay/inventory/runtime-fidelity-overlay.json (18 mandatory runtime nodes, 13 ACTUAL_SOURCE_RUNNING)
  DOMAIN_REPLICAS/source_to_pay/spec/runtime-topology.yaml              (processes, excluded components)

The full patch keeps its own schema and is referenced by hash from the canonical snapshot; this part carries the
subset that has a meaning in the M6 functional-graph schema: the three services, every worker/job, every Kafka
topic/queue the inventory detected, the datastores, the 18 mandatory runtime symbols (as state nodes of the
selected journey), the boundary interfaces now served by the shared ingress, and the tax-payment states.
Ids are namespaced `<kind>:s2p/...`; nothing outside the namespace is modified.
"""
import json, re
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
S2P = REPO / "DOMAIN_REPLICAS/source_to_pay"
OUT = REPO / "reports/domain/parts/zz-s2p-namespace.json"
LANE = "zz-s2p-namespace"
SRC_SHA = "20c4f4d59970471067388afea8b1d65ac39ee126"

STATUS_TO_FIDELITY = {"ACTUAL_SOURCE_RUNNING": "real_source_running", "SOURCE_MAPPED_NOT_RUNNING": "real_source_mapped_not_running",
                      "CONTRACT_FAITHFUL_REPLACEMENT": "high_fidelity_replacement", "BEHAVIORAL_STUB": "behavioural_placeholder",
                      "GRAPH_ONLY": "graph_only", "PRODUCTION_STATE_UNKNOWN": "graph_only", "REPLICA_ONLY": "high_fidelity_replacement"}
KIND_MAP = {"service": "svc", "worker_or_job": "worker", "kafka_or_queue": "topic", "sql_target": "table", "orm_model": "table",
            "database_schema": "db", "runtime_fidelity_symbol": "state", "runtime_fidelity_adapter": "state", "http_route": "route"}


def slug(s):
    return re.sub(r"[^A-Za-z0-9_./\-]+", "-", s).strip("-")[:120]


def main():
    patch = json.loads((S2P / "integration/unified-graph-patch.json").read_text())
    overlay = json.loads((S2P / "inventory/runtime-fidelity-overlay.json").read_text())
    inv = {i["id"]: i for i in json.loads((S2P / "inventory/architecture-inventory.json").read_text())["items"]}
    by_id = {n["id"]: n for n in patch["nodes"]}
    nodes, edges = [], []
    seen = set()

    def add(nid, **kw):
        if nid in seen:
            return
        seen.add(nid)
        n = {"id": nid, "kind": nid.split(":", 1)[0], "owner_domain": "vendor-payments", "repo": "repo:vendor-payments", "sha": SRC_SHA, "confidence": "confirmed"}
        n.update(kw)
        nodes.append(n)

    running_fidelity = {"vendor-payments": "real_source_running", "vendor-experience": "real_source_mapped_not_running", "accounting-integrations": "real_source_mapped_not_running"}
    for name in ("vendor-payments", "vendor-experience", "accounting-integrations"):
        add("svc:s2p/%s" % name, label="%s (Source-to-Pay)" % name, criticality="P1" if name == "vendor-payments" else "P2",
            fidelity=running_fidelity[name], repo="repo:" + name,
            twin_ref="ENV2_COMPOSE/docker-compose.s2p.yml s2p-vp-source (selected function slice via runtime/source-driver)" if name == "vendor-payments" else None,
            fidelity_evidence="DOMAIN_REPLICAS/source_to_pay/spec/runtime-topology.yaml; build-manifest binaries" if name != "vendor-payments" else "runtime-fidelity-overlay.json 13 actual-source symbols traced in every clean run",
            source_refs=["DOMAIN_REPLICAS/source_to_pay/source-lock.json"], m7_label="ACTUAL_SOURCE_RUNNING" if name == "vendor-payments" else "SOURCE_MAPPED_NOT_RUNNING")
    # the selected-journey runtime nodes (18) and their order
    for n in overlay.get("nodes", []):
        st = n.get("status") or n.get("representation_status") or "SOURCE_MAPPED_NOT_RUNNING"
        nid = "state:s2p/%s" % slug(n["id"])
        add(nid, label=n.get("label") or n["id"], criticality="P0", fidelity=STATUS_TO_FIDELITY.get(st, "graph_only"),
            twin_ref="s2p-vp-source" if st == "ACTUAL_SOURCE_RUNNING" else ("api-ingress" if "boundary" in n["id"] else "runtime/source-driver"),
            fidelity_evidence="runtime-fidelity-overlay.json %s (trace requirements %s)" % (st, ",".join(n.get("trace_requirements") or [])),
            source_refs=[e for e in (n.get("evidence_ids") or [])][:6], m7_label=st, s2p_id=n["id"])
        edges.append({"from": nid, "to": "svc:s2p/vendor-payments", "type": "implements", "via": "selected journey node", "confidence": "confirmed"})
    for e in overlay.get("edges", []):
        edges.append({"from": "state:s2p/%s" % slug(e["source"]), "to": "state:s2p/%s" % slug(e["target"]), "type": "transitions",
                      "via": "selected_journey_next (%s)" % ",".join(e.get("trace_requirements") or []), "confidence": "confirmed"})
    # boundary adapters are now served by the shared ingress in the integrated arena
    for bid in ("adapter.boundary-get", "adapter.boundary-post", "adapter.boundary-patch"):
        edges.append({"from": "state:s2p/%s" % slug(bid), "to": "sub:api-ingress", "type": "implements", "via": "integrated arena: boundary alias resolves to api-ingress (docker-compose.s2p.yml)", "confidence": "confirmed"})
    # workers/jobs, topics/queues, datastores from the inventory (namespaced, mapped-not-running unless the driver runs them)
    counts = {}
    for n in patch["nodes"]:
        t = n.get("type")
        if t not in ("worker_or_job", "kafka_or_queue", "database_schema"):
            continue
        item = inv.get(n["id"], {})
        st = item.get("representation_status", "SOURCE_MAPPED_NOT_RUNNING")
        kind = KIND_MAP[t]
        nid = "%s:s2p/%s" % (kind, slug(n["id"]))
        add(nid, label=(n.get("label") or n["id"])[:120], criticality="P2", fidelity=STATUS_TO_FIDELITY.get(st, "graph_only"),
            repo="repo:" + (n.get("repository") or "vendor-payments"), source_refs=(n.get("evidence_ids") or [])[:3], m7_label=st,
            fidelity_evidence="architecture-inventory.json representation_status", s2p_id=n["id"])
        edges.append({"from": nid, "to": "svc:s2p/%s" % (n.get("repository") or "vendor-payments"), "type": "depends_on", "via": "inventory", "confidence": "confirmed"})
        counts[kind] = counts.get(kind, 0) + 1
    # tax payment states observed by the executed journey (business-state-machines.yaml)
    for s in ("created", "money_loading_initiated", "money_loading_success", "processing"):
        add("state:s2p/tax_payment/%s" % s, label="tax_payment %s" % s, criticality="P1", fidelity="real_source_running", owner_domain="vendor-payments",
            twin_ref="s2p-vp-source", source_refs=["DOMAIN_REPLICAS/source_to_pay/spec/business-state-machines.yaml"], m7_label="ACTUAL_SOURCE_RUNNING")
    edges += [{"from": "state:s2p/tax_payment/created", "to": "state:s2p/tax_payment/money_loading_initiated", "type": "transitions", "via": "taxpayments.Core.Pay", "confidence": "confirmed"},
              {"from": "state:s2p/tax_payment/money_loading_initiated", "to": "state:s2p/tax_payment/money_loading_success", "type": "transitions", "via": "VPServer.PayoutStatusChange (processed)", "confidence": "confirmed"},
              {"from": "state:s2p/tax_payment/money_loading_success", "to": "state:s2p/tax_payment/processing", "type": "transitions", "via": "public status projection", "confidence": "confirmed"}]
    add("db:s2p/mysql", label="vendor_payments MySQL (source migrations)", criticality="P1", fidelity="high_fidelity_replacement", twin_ref="s2p-mysql", source_refs=["DOMAIN_REPLICAS/source_to_pay/runtime/mysql/init.sql"], m7_label="CONTRACT_FAITHFUL_REPLACEMENT")
    add("db:s2p/redis", label="vendor_payments Redis", criticality="P2", fidelity="high_fidelity_replacement", twin_ref="s2p-redis", source_refs=["DOMAIN_REPLICAS/source_to_pay/docker-compose.yml"], m7_label="CONTRACT_FAITHFUL_REPLACEMENT")
    add("sub:s2p-source-driver", label="s2p source driver (additive Go entrypoint around unchanged source)", criticality="P0", fidelity="high_fidelity_replacement", twin_ref="DOMAIN_REPLICAS/source_to_pay/runtime/source-driver/main.go", source_refs=["DOMAIN_REPLICAS/source_to_pay/spec/declared-deviations.yaml D-STARTUP"], m7_label="CONTRACT_FAITHFUL_REPLACEMENT")
    add("sub:s2p-monolith-boundary", label="standalone replica boundary (NOT started in the integrated arena)", criticality="P1", fidelity="high_fidelity_replacement", twin_ref="DOMAIN_REPLICAS/source_to_pay/runtime/boundary/server.py (standalone acceptance only)", source_refs=["DOMAIN_REPLICAS/source_to_pay/integration/payouts-boundary-contract.yaml"], m7_label="CONTRACT_FAITHFUL_REPLACEMENT", notes="replaced by sub:api-ingress for every route in the boundary contract; retained for the standalone S2P acceptance")
    edges.append({"from": "sub:s2p-source-driver", "to": "svc:s2p/vendor-payments", "type": "implements", "via": "boot.go selects existing initializers", "confidence": "confirmed"})
    edges.append({"from": "sub:s2p-monolith-boundary", "to": "svc:api-monolith", "type": "implements", "via": "standalone replica only", "confidence": "confirmed"})
    edges.append({"from": "svc:s2p/vendor-payments", "to": "db:s2p/mysql", "type": "writes", "via": "gorm repositories", "confidence": "confirmed"})
    edges.append({"from": "svc:s2p/vendor-payments", "to": "topic:s2p/add-tds-entry", "type": "consumes", "via": "tasks.InitiateTdsTask.ProcessMessage", "confidence": "confirmed"})
    part = {"lane": LANE, "generated_at": datetime.now(timezone.utc).isoformat(), "namespace": "source_to_pay",
            "sources": [{"repo": "razorpay/vendor-payments", "sha": SRC_SHA, "path": "DOMAIN_REPLICAS/source_to_pay/integration/unified-graph-patch.json"}],
            "derived_from": {"unified_graph_patch_nodes": len(patch["nodes"]), "unified_graph_patch_edges": len(patch["edges"]), "overlay_nodes": len(overlay.get("nodes", []))},
            "nodes": nodes, "edges": edges, "families": [], "open_questions": []}
    OUT.write_text(json.dumps(part, indent=1))
    print(json.dumps({"nodes": len(nodes), "edges": len(edges), "by_kind": counts, "out": str(OUT.relative_to(REPO))}))


if __name__ == "__main__":
    main()
