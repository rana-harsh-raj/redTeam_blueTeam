"""Import the accepted M7 milestone WITHOUT modifying it.

Writes under <store>/<snapshot_id>/imports/m7/:
  projection.json        how M7_CANONICAL_SNAPSHOT.json projects onto the static snapshot (ids/edges equality,
                         every fidelity/label delta with its reason) + sha256 of every M7 file read
  runtime_instance.json  RuntimeInstance for the canonical boot (boot id, compose project, running services, images)
  evidence_bundle.json   EvidenceBundle: journey results + the 143-file hash manifest of M7 (paths + sha256)
  acceptance_record.json AcceptanceRecord: M7_ACCEPTANCE.json by value, bound by sha256
Each import document carries its own content id. Timestamps are allowed here (they are observations).
"""
import json
from . import paths
from .canon import canonical_bytes, content_id, sha256_file
from .compile import LABEL


def _sha(p):
    return sha256_file(p)


def build_projection(body):
    m7 = json.loads(paths.M7_SNAPSHOT.read_bytes())
    G = m7["payouts_graph"]["graph"]
    A = {n["id"]: n for n in body["graph"]["nodes"]}
    B = {n["id"]: n for n in G["nodes"]}
    ea = {(e["from"], e["to"], e["type"], e.get("via") or "") for e in body["graph"]["edges"]}
    eb = {(e["from"], e["to"], e["type"], e.get("via") or "") for e in G["edges"]}
    deltas = []
    for i in sorted(A):
        if i not in B:
            continue
        a, b = A[i], B[i]
        if a.get("fidelity") != b.get("fidelity"):
            if b["kind"] == "substitute" and "zz-runtime-overlay" in (b.get("lanes") or []):
                reason = "M7 runtime overlay upgraded every running substitute to high_fidelity_replacement (scripts/domain/runtime_overlay.py:102); the static snapshot keeps the class declared by the substitute's lane"
            elif b["kind"] in ("service", "worker") and a.get("fidelity_basis") == "compose_definition":
                reason = "M7 classified by observed container state at overlay time; the static snapshot classifies by compose definition (one-shot jobs count as defined)"
            elif b["kind"] == "journey":
                reason = "M7 journey fidelity was derived from the run result; the static snapshot carries the driver-declared execution fidelity"
            else:
                reason = "unclassified delta"
            deltas.append({"id": i, "kind": b["kind"], "m7_fidelity": b.get("fidelity"), "static_fidelity": a.get("fidelity"), "reason": reason})
    label_deltas = [{"id": i, "m7_label": m7["labels"].get(i), "static_label": body["labels"].get(i)} for i in sorted(A) if m7["labels"].get(i) != body["labels"].get(i)]
    return {"kind": "m7_projection", "schema_version": "m8.1", "m7_tag": paths.M7_TAG,
            "m7_files": {paths.rel(p): _sha(p) for p in (paths.M7_SNAPSHOT, paths.M7_STATS, paths.M7_ACCEPTANCE, paths.M7_HASHES, paths.M7_UNKNOWNS_MD)},
            "m7_snapshot_git_head": m7.get("git_head"), "m7_generated_at": m7.get("generated_at"),
            "node_ids_equal": set(A) == set(B), "nodes_only_in_static": sorted(set(A) - set(B)), "nodes_only_in_m7": sorted(set(B) - set(A)),
            "edges_only_in_static": sorted(list(x) for x in ea - eb), "edges_only_in_m7": sorted(list(x) for x in eb - ea),
            "fidelity_deltas": deltas, "fidelity_delta_count": len(deltas),
            "label_deltas": label_deltas, "label_delta_count": len(label_deltas),
            "m7_coverage": m7["coverage"], "m7_label_histogram": m7["label_histogram"], "m7_p0_label_histogram": m7["p0_label_histogram"],
            "m7_production_unknown_ids": [u["id"] for u in m7["production_unknowns"]],
            "volatile_fields_dropped": ["generated_at", "git_head", "runtime_overlay", "node.runtime", "node.result", "node.evidence", "node.merchant_id", "node.notes", "connected_journey.evidence", "coverage.critical_journey", "docker status strings", "scratchpad paths"]}


def build_runtime_instance(body):
    m7 = json.loads(paths.M7_SNAPSHOT.read_bytes())
    cb = json.loads((paths.IMPL / "m6-clean-boot.json").read_bytes())
    cb7 = json.loads((paths.IMPL / "m7-clean-boot.json").read_bytes())
    jr = json.loads((paths.IMPL / "m6-journeys.json").read_bytes())
    fp = jr.get("fingerprint") or {}
    ro = m7["runtime_overlay"]
    running = sorted({s for v in ro["services"].values() for s in v["running_compose_services"]})
    images = {s: v["image"] for s, v in sorted(ro["services"].items()) if v.get("image")}
    inst = {"kind": "runtime_instance", "schema_version": "m8.1", "boot_id": fp.get("boot_id"), "compose_project": fp.get("compose_project"),
            "route_profile": fp.get("route_profile"), "config_digest": fp.get("config_digest"), "git_head": cb.get("git_head"),
            "started_at": cb.get("started_at"), "finished_at": cb.get("finished_at"), "from_empty_state": cb.get("from_empty_state"),
            "container_count": cb.get("container_count"), "running": cb.get("running"), "healthy": cb.get("healthy"),
            "s2p_stack_healthy": cb7.get("s2p_stack_healthy"), "shared_ingress_booted_from_clean_state": cb7.get("shared_ingress_booted_from_clean_state"),
            "running_compose_services_total": ro.get("running_compose_services"), "mapped_running_compose_services": running,
            "images_by_graph_node": images, "observed_at": ro.get("captured_at"),
            "observed_node_fidelity": {i: n.get("fidelity") for i, n in sorted(((n["id"], n) for n in m7["payouts_graph"]["graph"]["nodes"]), key=lambda kv: kv[0]) if "zz-runtime-overlay" in (n.get("lanes") or [])},
            "host": {"docker": "colima single daemon (M7 runbook)", "isolation": "shared daemon; see reports/source-to-pay-replica/ACCEPTANCE_CLOSURE.md"},
            "sources": {paths.rel(paths.IMPL / "m6-clean-boot.json"): _sha(paths.IMPL / "m6-clean-boot.json"),
                        paths.rel(paths.IMPL / "m7-clean-boot.json"): _sha(paths.IMPL / "m7-clean-boot.json"),
                        paths.rel(paths.M7_SNAPSHOT): _sha(paths.M7_SNAPSHOT)}}
    inst["runtime_instance_id"] = content_id(inst)
    return inst


def build_evidence_bundle(body, runtime_instance_id):
    jr = json.loads((paths.IMPL / "m6-journeys.json").read_bytes())
    man = json.loads(paths.M7_HASHES.read_bytes())
    results = {}
    for j in jr.get("journeys", []):
        results[j["id"]] = {"result": j.get("result"), "checks_passed": j.get("checks_passed"), "checks_total": j.get("checks_total"),
                            "duration_s": j.get("duration_s"), "evidence_path": j.get("evidence_path"),
                            "evidence_sha256": man["files"].get(j.get("evidence_path")), "merchant_id": j.get("merchant_id"),
                            "failed_checks": j.get("failed_checks"), "notes": j.get("notes")}
    extra = {}
    for name in ("m7-s2p-connected-runs.json", "m7-reset-proof.json", "m7-refresh-demo.json", "m7-weekly-rebuild.json", "m7-secret-scan.json", "m7-s2p-post-integration-acceptance.json"):
        p = paths.IMPL / name
        if p.is_file():
            d = json.loads(p.read_bytes())
            extra[name] = {"sha256": _sha(p), "summary": {k: v for k, v in d.items() if isinstance(v, (str, int, float, bool)) or v is None}}
    from collections import Counter
    bundle = {"kind": "evidence_bundle", "schema_version": "m8.1", "runtime_instance_id": runtime_instance_id, "milestone": "M7",
              "run_dir": jr.get("run_dir"), "campaign_prefix": jr.get("campaign_prefix"), "generated_at": jr.get("generated_at"),
              "journey_results": dict(sorted(results.items())), "result_histogram": dict(sorted(Counter(r["result"] for r in results.values()).items())),
              "files": man["files"], "file_count": man["count"], "manifest": paths.rel(paths.M7_HASHES), "manifest_sha256": _sha(paths.M7_HASHES),
              "git_ignored_paths_note": "RED_LOOP/runs/** files are git-ignored; they are bound by sha256 here and reported as present/absent at query time",
              "other_evidence": extra}
    bundle["evidence_bundle_id"] = content_id(bundle)
    return bundle


def build_acceptance_record(body, evidence_bundle_id):
    acc = json.loads(paths.M7_ACCEPTANCE.read_bytes())
    rec = {"kind": "acceptance_record", "schema_version": "m8.1", "milestone": acc.get("milestone"), "accepted": acc.get("accepted"),
           "gates_passed": acc.get("gates_passed"), "gates_total": acc.get("gates_total"), "evaluated_commit": acc.get("head"), "branch": acc.get("branch"),
           "evaluated_at": acc.get("evaluated_at"), "tag": paths.M7_TAG, "gates": acc.get("gates"), "evaluator": "scripts/m7/acceptance.py",
           "evidence_bundle_id": evidence_bundle_id, "bound_snapshot_sha256": acc.get("canonical_snapshot_sha256"),
           "bound_manifest_sha256": acc.get("artifact_manifest_sha256"), "file": paths.rel(paths.M7_ACCEPTANCE), "file_sha256": _sha(paths.M7_ACCEPTANCE)}
    rec["acceptance_record_id"] = content_id(rec)
    return rec


def run_import(store, sid=None):
    sid = store.resolve(sid)
    doc = store.load(sid)
    body = doc["body"]
    d = store.dir(sid) / "imports" / "m7"
    d.mkdir(parents=True, exist_ok=True)
    proj = build_projection(body)
    inst = build_runtime_instance(body)
    bundle = build_evidence_bundle(body, inst["runtime_instance_id"])
    rec = build_acceptance_record(body, bundle["evidence_bundle_id"])
    for name, obj in (("projection", proj), ("runtime_instance", inst), ("evidence_bundle", bundle), ("acceptance_record", rec)):
        (d / (name + ".json")).write_bytes(canonical_bytes(obj))
    store.register(sid, imports={"m7": {"tag": paths.M7_TAG, "runtime_instance_id": inst["runtime_instance_id"], "evidence_bundle_id": bundle["evidence_bundle_id"],
                                        "acceptance_record_id": rec["acceptance_record_id"], "m7_snapshot_sha256": proj["m7_files"][paths.rel(paths.M7_SNAPSHOT)]}})
    return {"snapshot_id": store.resolve(sid), "node_ids_equal": proj["node_ids_equal"], "edges_only_in_static": len(proj["edges_only_in_static"]),
            "edges_only_in_m7": len(proj["edges_only_in_m7"]), "fidelity_deltas": proj["fidelity_delta_count"], "label_deltas": proj["label_delta_count"],
            "runtime_instance_id": inst["runtime_instance_id"], "evidence_bundle_id": bundle["evidence_bundle_id"], "acceptance_record_id": rec["acceptance_record_id"],
            "m7_accepted": rec["accepted"], "journey_results": bundle["result_histogram"]}
