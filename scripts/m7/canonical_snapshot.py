#!/usr/bin/env python3
"""Compile the M7 canonical architecture snapshot: reports/architecture/M7_CANONICAL_SNAPSHOT.json (+ _STATS.json).

One machine artifact that carries, without inventing anything:
  payouts_graph          the M6/M7 functional graph (reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.json), embedded
  s2p_namespace          the Source-to-Pay graph patch, by path + sha256 + its projected part (parts/zz-s2p-namespace.json)
  shared_ingress         nodes/routes/identities/trust boundaries/ownership edges from parts/m7-ingress.json + contract inventory
  connected_journey      queues, workers, topics and services the cross-domain journey touched (from the journey evidence)
  runtime_overlay        which compose services are running right now (docker inspect) and which image each runs
  labels                 every material component labelled with one of the six M7 classes
  coverage               mapping / executable / actual-source runtime / contract-faithful / critical-journey coverage, separately
  production_unknowns    explicit annotations (never a single "percent identical to production" number)
"""
import hashlib, json, subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "reports/architecture/M7_CANONICAL_SNAPSHOT.json"
STATS = REPO / "reports/architecture/M7_CANONICAL_SNAPSHOT_STATS.json"
LABEL = {"real_source_running": "ACTUAL_SOURCE_RUNNING", "real_source_mapped_not_running": "SOURCE_MAPPED_NOT_RUNNING",
         "high_fidelity_replacement": "CONTRACT_FAITHFUL_REPLACEMENT", "behavioural_placeholder": "BEHAVIORAL_STUB",
         "graph_only": "GRAPH_ONLY", "blocked_missing_access": "PRODUCTION_STATE_UNKNOWN"}
PROD_UNKNOWN_KINDS = {"flag"}   # a flag's production value is never known from source; its existence is
UNKNOWNS = [
    {"id": "PU-1", "topic": "payout create route selection", "statement": "Whether a given merchant's POST /v1/payouts is served Edge->PS directly or Edge->API->PS (PayoutController::postFundAccountPayout logs X-Payouts-Service-Proxy / PROXY_CUTOVER_DECISION) depends on live routing and merchant flags; not decidable from source.", "affects": ["route:api-monolith/POST payouts", "sub:api-ingress", "sub:kong-lite"]},
    {"id": "PU-2", "topic": "fund-account ownership on the PS-direct path", "statement": "On a path where Edge reaches PS without the monolith, ownership still rests on the monolith's merchant-scoped GET fund_accounts_internal answered to PS; whether any production configuration lets PS resolve a beneficiary without that hop (cache pre-warm, composite create) is unknown.", "affects": ["route:api-monolith/GET fund_accounts_internal/{id}", "table:api-ingress/resources"]},
    {"id": "PU-3", "topic": "tax-payment tag-back scoping", "statement": "PayoutsDetails/Core.php updateTaxPayment updates by payout id only; whether another production layer scopes vendor_payments' tag-back to the merchant is unknown.", "affects": ["route:api-monolith/PATCH payouts_internal/{id}/tax-payment-id"]},
    {"id": "PU-4", "topic": "internal-app secrets and Passport-issued app auth", "statement": "Production internal applications may authenticate through Passport (BasicAuth::isValidPassportForAppAuth) rather than key-blank secrets; the twin models the key-blank appAuth branch only.", "affects": ["identity:ingress-app-secret"]},
    {"id": "PU-5", "topic": "dashboard session/OTP", "statement": "Production sessions and OTPs come from the dashboard + Raven; the twin's session/OTP store is synthetic (control plane).", "affects": ["identity:dashboard-session", "identity:dashboard-otp"]},
    {"id": "PU-6", "topic": "SourceUpdater transport", "statement": "The SNS SourceUpdater leg is off in the pinned config; the live flag value and the vendor-payments Twirp authentication on PayoutStatusChange are unknown; the twin relays over HTTP to the source driver's callback adapter.", "affects": ["route:vendor-payments/POST /twirp/vendorpayments.Vendorpayments/PayoutStatusChange"]},
    {"id": "PU-7", "topic": "Source-to-Pay deployment", "statement": "Deployed vendor-payments/vendor-experience/accounting-integrations topology, Kafka partitioning, flags and secrets are unknown (DOMAIN_REPLICAS/source_to_pay/spec/unresolved-production-unknowns.yaml).", "affects": ["svc:s2p/vendor-payments", "svc:s2p/vendor-experience", "svc:s2p/accounting-integrations"]},
    {"id": "PU-8", "topic": "batch approval hop", "statement": "Batch per-row approve/reject in the twin still bypasses the monolith (bulk_approve is outside the selected ingress surface).", "affects": ["sub:batch-sim"]},
]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def running_services():
    out = subprocess.run(["docker", "ps", "--filter", "label=com.docker.compose.project=env2_compose", "--format",
                          "{{.Label \"com.docker.compose.service\"}}\t{{.Image}}\t{{.Status}}\t{{.ID}}"], capture_output=True, text=True)
    svcs = {}
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 4:
            svcs[parts[0]] = {"image": parts[1], "status": parts[2], "id": parts[3]}
    return svcs


def main():
    graph = json.loads((REPO / "reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.json").read_text())
    stats = json.loads((REPO / "reports/domain/GRAPH_STATS.json").read_text())
    ingress_part = json.loads((REPO / "reports/domain/parts/m7-ingress.json").read_text())
    s2p_part = json.loads((REPO / "reports/domain/parts/zz-s2p-namespace.json").read_text())
    contract = json.loads((REPO / "ENV2_COMPOSE/substitutes/api-ingress/contract/routes.json").read_text())
    patch_path = REPO / "DOMAIN_REPLICAS/source_to_pay/integration/unified-graph-patch.json"
    patch_head = json.loads(patch_path.read_text())
    nodes = graph["nodes"]
    labels = {}
    for n in nodes:
        lab = n.get("m7_label") or LABEL.get(n.get("fidelity"), "GRAPH_ONLY")
        labels[n["id"]] = lab
    unknown_ids = {a for u in UNKNOWNS for a in u["affects"]}
    for nid in unknown_ids:
        if nid in labels and labels[nid] == "GRAPH_ONLY":
            labels[nid] = "PRODUCTION_STATE_UNKNOWN"
    hist = Counter(labels.values())
    p0 = [n for n in nodes if n.get("criticality") == "P0" and n.get("kind") not in ("journey", "family")]
    p0_labels = Counter(labels[n["id"]] for n in p0)
    journeys = [n for n in nodes if n.get("kind") == "journey"]
    jr_pass = [j for j in journeys if j.get("result") in ("PASS", "EXPECTED_FAILURE")]
    critical_families = [f for f in graph["families"] if f.get("priority") == "P0"]
    fam_with_pass = {j.get("family") for j in journeys if j.get("result") == "PASS"} | {p for j in journeys if j.get("result") == "PASS" for p in (j.get("proves_families") or [])}
    try:
        jr = json.loads((REPO / "reports/implementation/m6-journeys.json").read_text())
        run_dir = REPO / jr["run_dir"]
        connected = {}
        for ev in sorted((run_dir / "evidence").glob("journey-cross-domain-s2p-*.json")):
            d = json.loads(ev.read_text())
            touched = set()
            for h in d.get("http", []):
                touched.add(h.get("url", "").split("/")[2] if "://" in h.get("url", "") else h.get("transport"))
            for r in d.get("db", []):
                touched.add(r.get("store"))
            connected[ev.stem] = {"result": d.get("result"), "touched": sorted(x for x in touched if x)}
    except Exception:
        connected = {}
    running = running_services()
    svc_nodes = {n["id"]: n for n in nodes if n.get("kind") in ("service", "substitute")}
    overlay = {}
    for nid, n in svc_nodes.items():
        ref = str(n.get("twin_ref") or "")
        match = [s for s in running if s and (s in ref or ("sub:" + s) == nid or nid.endswith("/" + s) or nid == "svc:" + s)]
        overlay[nid] = {"running_compose_services": match, "image": running[match[0]]["image"] if match else None, "label": labels[nid]}
    coverage = {
        "mapping": {"critical_components_total": stats["critical_components_total"], "represented": stats["critical_components_represented"], "pct": stats["critical_representation_pct"]},
        "executable": {"critical_components_executable": stats["critical_components_executable"], "pct": stats["critical_executable_pct"], "definition": "P0 nodes whose fidelity is real_source_running or high_fidelity_replacement"},
        "actual_source_runtime": {"p0": p0_labels.get("ACTUAL_SOURCE_RUNNING", 0), "p0_pct": round(100.0 * p0_labels.get("ACTUAL_SOURCE_RUNNING", 0) / max(1, len(p0)), 1), "all_nodes": hist.get("ACTUAL_SOURCE_RUNNING", 0)},
        "contract_faithful_replacement": {"p0": p0_labels.get("CONTRACT_FAITHFUL_REPLACEMENT", 0), "p0_pct": round(100.0 * p0_labels.get("CONTRACT_FAITHFUL_REPLACEMENT", 0) / max(1, len(p0)), 1), "all_nodes": hist.get("CONTRACT_FAITHFUL_REPLACEMENT", 0)},
        "behavioral_stub": {"p0": p0_labels.get("BEHAVIORAL_STUB", 0), "all_nodes": hist.get("BEHAVIORAL_STUB", 0)},
        "critical_journey": {"p0_families": len(critical_families), "p0_families_with_passing_journey": sum(1 for f in critical_families if f["id"] in fam_with_pass),
                             "journeys_executed": len(journeys), "journeys_pass_or_expected": len(jr_pass)},
        "s2p_namespace": {"patch_nodes": len(patch_head["nodes"]), "patch_edges": len(patch_head["edges"]), "projected_nodes": len(s2p_part["nodes"]),
                          "mandatory_runtime_nodes": patch_head.get("runtime_fidelity_overlay", {}).get("mandatory_closure_count")},
        "detector_limitations": [
            "Payouts graph: lane detectors are regex/AST based over the pinned repos; a node absent from every lane is absent from the graph (REMAINING_ACCESS_MANIFEST.md lists blocked repos).",
            "Source-to-Pay: representation is measured against mechanically detected observations, not detector recall (architecture-inventory-summary.md 'extraction_completeness_is_not_measured_by_percentage').",
            "Runtime overlay: 'running' means a compose service is up and healthy right now, not that every code path of the service executed.",
            "Ingress contract: routes.json is derived from Route.php arrays and router files; middleware-level behaviour beyond the cited groups (rate limits, permissions) is not inventoried.",
        ],
    }
    snap = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
            "git_head": subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
            "payouts_graph": {"path": "reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.json", "sha256": sha(REPO / "reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.json"),
                              "nodes": len(nodes), "edges": len(graph["edges"]), "families": len(graph["families"]), "lanes": stats.get("lanes"), "graph": graph},
            "s2p_namespace": {"path": "DOMAIN_REPLICAS/source_to_pay/integration/unified-graph-patch.json", "sha256": sha(patch_path),
                              "nodes": len(patch_head["nodes"]), "edges": len(patch_head["edges"]), "namespace": patch_head.get("namespace"),
                              "projected_part": "reports/domain/parts/zz-s2p-namespace.json", "projected_part_sha256": sha(REPO / "reports/domain/parts/zz-s2p-namespace.json"),
                              "runtime_fidelity_overlay": json.loads((REPO / "DOMAIN_REPLICAS/source_to_pay/inventory/runtime-fidelity-overlay.json").read_text())},
            "shared_ingress": {"part": "reports/domain/parts/m7-ingress.json", "contract": "ENV2_COMPOSE/substitutes/api-ingress/contract/routes.json",
                               "contract_sha256": sha(REPO / "ENV2_COMPOSE/substitutes/api-ingress/contract/routes.json"),
                               "routes": {n: {"method": r["method"], "path": r["path"], "role": r["ingress_role"], "groups": sorted(r["groups"]), "internal_apps": r["internal_apps"], "ps_upstream": r.get("ps_upstream")} for n, r in contract["routes"].items()},
                               "identities": [n["id"] for n in ingress_part["nodes"] if n["kind"] == "identity" and not n["id"].startswith("identity:trust-boundary")],
                               "trust_boundaries": [n["id"] for n in ingress_part["nodes"] if n["id"].startswith("identity:trust-boundary")],
                               "ownership_edges": [e for e in ingress_part["edges"] if e["type"] == "owns"],
                               "tables": [n["id"] for n in ingress_part["nodes"] if n["kind"] == "table"]},
            "connected_journey": {"queues_workers_topics": ["queue:prod-api-payout-source-updater-live", "worker:payouts/payout_source_updater", "topic:s2p/add-tds-entry", "svc:s2p/vendor-payments", "sub:s2p-source-driver", "sub:api-ingress", "svc:payouts-api", "svc:fts-web", "sub:mozart-sim"],
                                  "evidence": connected},
            "runtime_overlay": {"captured_at": datetime.now(timezone.utc).isoformat(), "running_compose_services": len(running), "services": overlay},
            "labels": labels, "label_histogram": dict(hist), "p0_label_histogram": dict(p0_labels), "coverage": coverage, "production_unknowns": UNKNOWNS,
            "no_single_parity_number": "This snapshot deliberately reports mapping, executable, actual-source, replacement and journey coverage separately; there is no 'percent identical to production'."}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snap, indent=1))
    stats_doc = {k: snap[k] for k in ("generated_at", "git_head", "label_histogram", "p0_label_histogram", "coverage", "production_unknowns")}
    stats_doc.update(payouts_graph={k: v for k, v in snap["payouts_graph"].items() if k != "graph"}, s2p_namespace={k: v for k, v in snap["s2p_namespace"].items() if k != "runtime_fidelity_overlay"},
                     shared_ingress_routes=len(snap["shared_ingress"]["routes"]), running_compose_services=len(running), snapshot_sha256=sha(OUT))
    STATS.write_text(json.dumps(stats_doc, indent=1))
    print(json.dumps({"nodes": len(nodes), "labels": dict(hist), "p0": dict(p0_labels), "running_services": len(running), "out": str(OUT.relative_to(REPO))}))


if __name__ == "__main__":
    main()
