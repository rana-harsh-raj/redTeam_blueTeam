"""ArchitectureSnapshot compiler: committed inputs -> immutable, content-addressed snapshot (no Docker, no clock).

    python3 -m archkit compile [--store DIR]           -> <store>/<snapshot_id>/snapshot.json (+ manifest.json)

The body is canonical JSON (archkit.canon); snapshot_id = sha256(canonical(body)). Compiling twice from the same
tree yields byte-identical files. Any volatile field (timestamp, host path, docker status, boot id ...) aborts
the compile: static architecture must never carry runtime observations.
"""
import json
from collections import Counter
from . import paths, __version__, SCHEMA_VERSION
from .canon import canonical_bytes, content_id, sha256_file, scan_volatile
from .lock import build_source_lock
from .static_graph import build_static_graph
from .unknowns import build_registry

LABEL = {"real_source_running": "ACTUAL_SOURCE_RUNNING", "real_source_mapped_not_running": "SOURCE_MAPPED_NOT_RUNNING",
         "high_fidelity_replacement": "CONTRACT_FAITHFUL_REPLACEMENT", "behavioural_placeholder": "BEHAVIORAL_STUB",
         "graph_only": "GRAPH_ONLY", "blocked_missing_access": "PRODUCTION_STATE_UNKNOWN"}
CRIT_KINDS = ("service", "worker", "identity", "datastore", "table", "queue", "topic", "cron", "route", "substitute")
EXECUTABLE = ("real_source_running", "high_fidelity_replacement")
STATIC_SEMANTICS = {
    "real_source_running": "a real binary built from the pinned SHA is DEFINED to run in the canonical runtime (compose definition); whether it was observed running belongs to a RuntimeInstance",
    "high_fidelity_replacement": "contract-faithful substitute as declared by its lane / CONTRACT.md",
    "behavioural_placeholder": "substitute whose behaviour is partly inferred, as declared by its lane",
    "real_source_mapped_not_running": "source readable and mapped; no runtime defined",
    "graph_only": "inventoried; no runtime, no substitute",
    "blocked_missing_access": "source/artefact not readable by this identity",
    "journeys": "journey nodes are DEFINITIONS (driver-declared execution fidelity); PASS/FAIL results live in EvidenceBundles",
}


def _measure(num, den, population, definition):
    return {"numerator": num, "denominator": den, "population": population, "pct": round(100.0 * num / den, 1) if den else None, "definition": definition}


def compute_labels(nodes, unknown_affects):
    labels = {}
    for n in nodes:
        labels[n["id"]] = n.get("m7_label") or LABEL.get(n.get("fidelity"), "GRAPH_ONLY")
    for nid in unknown_affects:
        if labels.get(nid) == "GRAPH_ONLY":
            labels[nid] = "PRODUCTION_STATE_UNKNOWN"
    return labels


def compute_coverage(graph, st, labels, registry):
    nodes = graph["nodes"]
    pops = {
        "all_nodes": {"count": len(nodes), "definition": "every node of the static functional graph"},
        "p0_critical_kinds": {"count": sum(1 for n in nodes if n.get("criticality") == "P0" and n["kind"] in CRIT_KINDS),
                              "definition": "P0 nodes of kind %s (scripts/domain/build_graph.py stats)" % ",".join(CRIT_KINDS)},
        "p0_non_journey": {"count": sum(1 for n in nodes if n.get("criticality") == "P0" and n["kind"] not in ("journey", "family")),
                           "definition": "P0 nodes of any kind except journey and family (M7 canonical_snapshot.py p0 population)"},
        "p0_families": {"count": sum(1 for f in graph["families"] if f.get("priority") == "P0"), "definition": "families with priority P0"},
        "journey_definitions": {"count": sum(1 for n in nodes if n["kind"] == "journey"), "definition": "journey nodes (definitions, no results)"},
    }
    crit = [n for n in nodes if n.get("criticality") == "P0" and n["kind"] in CRIT_KINDS]
    p0nj = [n for n in nodes if n.get("criticality") == "P0" and n["kind"] not in ("journey", "family")]
    hist = {"all_nodes": dict(sorted(Counter(labels[n["id"]] for n in nodes).items())),
            "p0_critical_kinds": dict(sorted(Counter(labels[n["id"]] for n in crit).items())),
            "p0_non_journey": dict(sorted(Counter(labels[n["id"]] for n in p0nj).items()))}
    fam_defs = {}
    for n in nodes:
        if n["kind"] == "journey":
            for f in [n.get("family")] + list(n.get("proves_families") or []):
                if f:
                    fam_defs.setdefault(f, set()).add(n["id"])
    p0f = [f for f in graph["families"] if f.get("priority") == "P0"]
    affected = {a for e in registry["entries"] for a in e["affects"]}
    cov = {
        "mapping": _measure(st["critical_components_represented"], st["critical_components_total"], "p0_critical_kinds",
                            "P0 critical-kind nodes carrying source refs, entry points or a twin_ref and not blocked"),
        "executable_defined": _measure(sum(1 for n in crit if n.get("fidelity") in EXECUTABLE), len(crit), "p0_critical_kinds",
                                       "P0 critical-kind nodes whose fidelity is real_source_running (defined real binary) or high_fidelity_replacement"),
        "actual_source_defined": _measure(hist["p0_non_journey"].get("ACTUAL_SOURCE_RUNNING", 0), len(p0nj), "p0_non_journey", "label ACTUAL_SOURCE_RUNNING (static semantics: defined, not observed)"),
        "contract_faithful_replacement": _measure(hist["p0_non_journey"].get("CONTRACT_FAITHFUL_REPLACEMENT", 0), len(p0nj), "p0_non_journey", "label CONTRACT_FAITHFUL_REPLACEMENT"),
        "behavioral_stub": _measure(hist["p0_non_journey"].get("BEHAVIORAL_STUB", 0), len(p0nj), "p0_non_journey", "label BEHAVIORAL_STUB"),
        "p0_families_with_journey_definition": _measure(sum(1 for f in p0f if f["id"] in fam_defs), len(p0f), "p0_families", "P0 families with at least one journey definition (own or proves_families)"),
        "nodes_affected_by_production_unknowns": _measure(sum(1 for n in nodes if n["id"] in affected), len(nodes), "all_nodes", "nodes named in a ProductionUnknown.affects"),
        "no_single_parity_number": "coverage is reported per measure with an explicit population; there is no 'percent identical to production'",
    }
    return pops, hist, cov


def _input(path, role):
    p = paths.REPO / path
    return {"path": path, "sha256": sha256_file(p), "role": role}


def ingress_block(part_path=paths.PARTS / "m7-ingress.json"):
    contract = json.loads(paths.INGRESS_CONTRACT.read_text())
    part = json.loads(part_path.read_text())
    routes = {n: {k: v for k, v in r.items()} for n, r in sorted(contract["routes"].items())}
    return {"contract": paths.rel(paths.INGRESS_CONTRACT), "contract_sha256": sha256_file(paths.INGRESS_CONTRACT),
            "contract_sources": {k: {"repo": v.get("repo"), "sha": v.get("sha")} for k, v in sorted((contract.get("sources") or {}).items())},
            "routes": routes, "part": paths.rel(part_path),
            "identities": sorted(n["id"] for n in part["nodes"] if n["kind"] == "identity" and not n["id"].startswith("identity:trust-boundary")),
            "trust_boundaries": sorted(n["id"] for n in part["nodes"] if n["id"].startswith("identity:trust-boundary")),
            "ownership_edges": sorted((e for e in part["edges"] if e["type"] == "owns"), key=lambda e: (e["from"], e["to"])),
            "tables": sorted(n["id"] for n in part["nodes"] if n["kind"] == "table")}


def s2p_block():
    head = json.loads(paths.S2P_PATCH.read_text())
    ov = json.loads(paths.S2P_OVERLAY.read_text())
    proj = paths.PARTS / "zz-s2p-namespace.json"
    return {"namespace": head.get("namespace"), "operation": head.get("operation"), "path": paths.rel(paths.S2P_PATCH), "sha256": sha256_file(paths.S2P_PATCH),
            "nodes": len(head["nodes"]), "edges": len(head["edges"]), "edge_semantics": head.get("edge_semantics"),
            "projected_part": paths.rel(proj), "projected_part_sha256": sha256_file(proj),
            "projected_nodes": len(json.loads(proj.read_text())["nodes"]),
            "detail_loading": "on-demand only (archkit.store.load_s2p_detail); never part of canonical path search",
            "runtime_fidelity_overlay": {"path": paths.rel(paths.S2P_OVERLAY), "sha256": sha256_file(paths.S2P_OVERLAY),
                                         "mandatory_closure_count": ov.get("mandatory_closure_count"),
                                         "actual_source_running_count": ov.get("actual_source_running_count"),
                                         "contract_faithful_replacement_count": ov.get("contract_faithful_replacement_count"),
                                         "execution_claim": ov.get("execution_claim")}}


def compile_snapshot():
    # graph first (lock needs repo nodes; graph needs the lock's readable set) -> two passes, the second is authoritative
    lock0 = build_source_lock([])
    graph0, _, _, _, _ = build_static_graph(lock0)
    lock = build_source_lock(graph0["nodes"])
    graph, st, part_inputs, errs, meta = build_static_graph(lock)
    node_ids = {n["id"] for n in graph["nodes"]}
    registry = build_registry(node_ids, {n["route_name"]: n["id"] for n in graph["nodes"] if n.get("route_name")})
    labels = compute_labels(graph["nodes"], {a for e in registry["entries"] for a in e["affects"]})
    pops, hist, cov = compute_coverage(graph, st, labels, registry)
    inputs = [_input(p, "discovery-part") for p in part_inputs]
    inputs += [_input("reports/domain/parts/m6-journeys.json", "journey-definitions-source"),
               _input("reports/implementation/m6-journeys.json", "journey-declared-fidelity-source"),
               _input(paths.rel(paths.COMPOSE), "compose-definition"), _input(paths.rel(paths.COMPOSE_S2P), "compose-definition"),
               _input(paths.rel(paths.COMPOSE_M11), "compose-definition"), _input(paths.rel(paths.KONG_CONFIG), "gateway-config-derived-from-terraform"),
               _input("ENV2_COMPOSE/build/m11/edge-kong.Dockerfile", "build-definition"), _input("ENV2_COMPOSE/build/m11/build-shield.sh", "build-definition"),
               _input("ENV2_COMPOSE/build/m11/build-banking-accounts.sh", "build-definition"), _input("ENV2_COMPOSE/build/m11/build-workflows.sh", "build-definition"),
               _input("ENV2_COMPOSE/trustpath/edge/provision_kong.py", "provisioning"), _input("ENV2_COMPOSE/trustpath/shield/seed_rules.py", "provisioning"),
               _input("ENV2_COMPOSE/trustpath/bankingaccounts/render_seed.py", "provisioning"), _input("ENV2_COMPOSE/trustpath/workflows/seed_configs.py", "provisioning"),
               _input(paths.rel(paths.INGRESS_CONTRACT), "ingress-contract"), _input(paths.rel(paths.S2P_PATCH), "s2p-detail-namespace"),
               _input(paths.rel(paths.S2P_OVERLAY), "s2p-runtime-fidelity-overlay"), _input(paths.rel(paths.S2P_LOCK), "source-lock"),
               _input("reports/domain/REPOSITORY_INVENTORY_M6.csv", "source-lock"), _input("scripts/domain/build_graph.py", "compiler-dependency"),
               _input("scripts/domain/runtime_overlay.py", "compiler-dependency"), _input("scripts/domain/journeys_part.py", "compiler-dependency")]
    inputs += [_input(p, "production-unknowns") for p in sorted(registry["sources"])]
    body = {"kind": "architecture_snapshot", "schema_version": SCHEMA_VERSION, "compiler": {"name": "archkit", "version": __version__},
            "source_lock_id": lock["source_lock_id"], "source_lock": {k: v for k, v in lock.items() if k != "source_lock_id"},
            "inputs": sorted(inputs, key=lambda i: i["path"]), "graph": graph, "graph_stats": st, "merge": meta,
            "schema_violations": sorted(errs), "labels": labels, "populations": pops, "label_histogram": hist, "coverage": cov,
            "production_unknowns": registry, "shared_ingress": ingress_block(), "s2p_namespace": s2p_block(),
            "static_semantics": STATIC_SEMANTICS,
            "lanes": sorted({l for n in graph["nodes"] for l in n.get("lanes", [])}),
            "counts": {"nodes": len(graph["nodes"]), "edges": len(graph["edges"]), "families": len(graph["families"]),
                       "journeys": sum(1 for n in graph["nodes"] if n["kind"] == "journey"), "production_unknowns": registry["count"]}}
    hits = scan_volatile(body)
    if hits:
        raise RuntimeError("volatile content in static snapshot body: %s" % hits[:10])
    sid = content_id(body)
    return sid, body


def write_snapshot(sid, body, store=None):
    store = store or paths.STORE
    d = store / sid
    d.mkdir(parents=True, exist_ok=True)
    doc = {"snapshot_id": sid, "body": body}
    (d / "snapshot.json").write_bytes(canonical_bytes(doc))
    summary = {"snapshot_id": sid, "kind": body["kind"], "schema_version": body["schema_version"], "compiler": body["compiler"],
               "source_lock_id": body["source_lock_id"], "counts": body["counts"], "coverage": body["coverage"], "populations": body["populations"],
               "label_histogram": body["label_histogram"], "inputs": body["inputs"], "s2p_namespace": {k: v for k, v in body["s2p_namespace"].items() if k != "edge_semantics"}}
    (d / "summary.json").write_bytes(canonical_bytes(summary))
    manifest = {"snapshot_id": sid, "files": {"snapshot.json": sha256_file(d / "snapshot.json"), "summary.json": sha256_file(d / "summary.json")}}
    (d / "manifest.json").write_bytes(canonical_bytes(manifest))
    return d


def load_snapshot_file(path):
    doc = json.loads(path.read_bytes())
    recomputed = content_id(doc["body"])
    if recomputed != doc["snapshot_id"]:
        raise ValueError("snapshot id mismatch: file says %s, body hashes to %s" % (doc["snapshot_id"], recomputed))
    return doc


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", default=None)
    ap.add_argument("--print-id", action="store_true")
    a = ap.parse_args(argv)
    sid, body = compile_snapshot()
    store = paths.Path(a.store) if a.store else paths.STORE
    d = write_snapshot(sid, body, store)
    print(json.dumps({"snapshot_id": sid, "dir": str(d), "counts": body["counts"], "coverage": {k: (v if isinstance(v, str) else "%s/%s (%s)" % (v["numerator"], v["denominator"], v["pct"])) for k, v in body["coverage"].items()}}, indent=1))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
