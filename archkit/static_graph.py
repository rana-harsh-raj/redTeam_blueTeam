"""Static functional graph: the M6/M7 discovery parts merged WITHOUT runtime observations.

Differences from scripts/domain/build_graph.py's canonical output (which is what M7 shipped):
  * the `zz-runtime-overlay` lane (docker ps) is replaced by a compose-DEFINITION overlay: a node whose compose
    service is defined in ENV2_COMPOSE/docker-compose.yml gets twin_ref/compose_service/profile/image and, for
    real-binary services and workers, fidelity `real_source_running` with basis `compose_definition`
    ("a real pinned binary is defined to run in the canonical runtime"). Substitutes KEEP their lane-declared
    class (the runtime overlay upgraded every running substitute to high_fidelity_replacement; that is an
    observation, not a design fact).
  * the `m6-journeys` lane (executed results) is replaced by journey DEFINITIONS: id, family, variant, priority,
    declared execution fidelity, proves_families / depends_on as declared by scripts/domain/journeys_part.py.
    Results, merchant ids and evidence paths belong to the EvidenceBundle.
  * `_reachable_repos` (which inspects .local/repos-root on the host) is replaced by the SourceLock.
  * timestamps, host paths and `version` are removed; source refs are normalized; ordering is total.
The merge/validation/stats logic itself is reused from scripts/domain/build_graph.py.
"""
import importlib.util
import json
import re
from collections import Counter
from . import paths
from .canon import normalize_ref

VOLATILE_NODE_FIELDS = ("runtime", "result", "evidence", "merchant_id", "notes", "missing_dependency_observed")
DROP_PART_KEYS = ("generated_at", "_file")


def _mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def load_build_graph():
    return _mod("m6_build_graph", paths.REPO / "scripts/domain/build_graph.py")


# M11: promoted trust-path services and their datastores in the overlay compose file -> graph nodes
M11_SVC_MAP = {"edge-kong": "svc:edge-kong", "shield-web": "svc:shield", "banking-accounts-api": "svc:banking-accounts",
               "workflows-api": "svc:workflows", "workflows-worker": "svc:workflows-workers",
               "postgres-kong": "db:edge/postgres", "mysql-shield": "db:shield/mysql", "redis-shield": "db:shield/redis",
               "mysql-bas": "db:banking-accounts/mysql", "mysql-workflows": "db:workflows/mysql", "cadence": "db:workflows/cadence"}


def load_parts(parts_dir=paths.PARTS, exclude=("zz-runtime-overlay", "m6-journeys")):
    parts = []
    inputs = []
    for p in sorted(parts_dir.glob("*.json")):
        if p.stem in exclude:
            continue
        d = json.loads(p.read_text())
        d.setdefault("lane", p.stem)
        d["_file"] = p.name
        parts.append(d)
        inputs.append(p)
    return parts, inputs


# --------------------------------------------------------------------------- compose-definition overlay
def compose_definition_part(graph_node_ids, compose_path=paths.COMPOSE, s2p_path=paths.COMPOSE_S2P, criticality=None):
    criticality = criticality or {}
    import yaml
    ro = _mod("m6_runtime_overlay", paths.REPO / "scripts/domain/runtime_overlay.py")
    comp = dict(yaml.safe_load(compose_path.read_text())["services"])
    compose_file_of = {s: "ENV2_COMPOSE/docker-compose.yml" for s in comp}
    if paths.COMPOSE_M11.is_file():   # M11: the real trust-path overlay is part of the canonical definition
        for s, spec in (yaml.safe_load(paths.COMPOSE_M11.read_text()).get("services") or {}).items():
            if s not in comp and set(spec.get("profiles") or []) & {"trustpath", "trustpath-migrations"}:
                comp[s] = spec
                compose_file_of[s] = "ENV2_COMPOSE/docker-compose.m11.yml"
    svc_map = dict(ro.SVC_MAP)
    svc_map.update({"api-ingress": "sub:api-ingress", "xas-sink": "sub:xas-sink", "mozart-mock": "sub:mozart-mock", "verifier": "sub:verifier"})  # M7 additions / lane-declared subs
    svc_map.update(M11_SVC_MAP)
    s2p = yaml.safe_load(s2p_path.read_text())["services"] if s2p_path.is_file() else {}
    nodes, edges = [], []
    core_images = ("rzp-arena/payouts:", "rzp-arena/ledger:", "rzp-arena/fts:", "rzp-arena/cfa:", "rzp-arena/xbalances:",
                   "rzp-arena/edge-kong:", "rzp-arena/shield:", "rzp-arena/banking-accounts:", "rzp-arena/workflows:")   # M11 promoted
    for svc, spec in sorted(comp.items()):
        env = spec.get("environment") or {}
        if isinstance(env, list):
            env = dict(e.split("=", 1) for e in env if "=" in e)
        cands = []
        if svc in svc_map:
            cands.append(svc_map[svc])
        wn = env.get("PAYOUTS_WORKER_NAME")
        if wn:
            cands += ["worker:payouts/%s" % wn, "worker:payouts/%s" % wn.replace("-", "_")]
        if svc.startswith("payouts-kafka-"):
            cands += ["worker:payouts/%s" % svc[len("payouts-"):], "worker:payouts/kafka-%s" % svc[len("payouts-kafka-"):]]
        for prefix, dom in (("fts-worker-", "fts"), ("ledger-worker", "ledger"), ("cfa-worker-", "cfa"), ("xbalances-worker", "x-balances")):
            if svc.startswith(prefix):
                name = svc[len(prefix):].lstrip("-") or "default"
                cands += ["worker:%s/%s" % (dom, name), "worker:%s/%s" % (dom, name.replace("-", "_")), "worker:%s/%s" % (dom, svc)]
        if svc == "ledger-scheduler":
            cands += ["worker:ledger/scheduler", "svc:ledger-scheduler"]
        hit = [c for c in cands if c in graph_node_ids]
        if not hit and svc in ro.NEW_SUBS:
            hit = [ro.SVC_MAP[svc]]
            edges.append({"from": ro.SVC_MAP[svc], "to": ro.NEW_SUBS[svc]["implements"], "type": "implements",
                          "via": ro.NEW_SUBS[svc]["contract"], "confidence": "confirmed", "source_refs": [ro.NEW_SUBS[svc]["contract"]]})
        if not hit:
            continue
        image = str(spec.get("image") or ("build:" + str((spec.get("build") or {}).get("context", "")) if spec.get("build") else ""))
        profiles = sorted(spec.get("profiles") or [])
        for nid in hit:
            kind = {"svc": "service", "sub": "substitute", "worker": "worker", "db": "datastore"}[nid.split(":")[0]]
            n = {"id": nid, "kind": kind, "criticality": criticality.get(nid, "P0"), "confidence": "confirmed", "owner_domain": "twin",
                 "twin_ref": "compose:" + svc, "compose_service": svc, "profile": profiles[0] if profiles else None,
                 "runtime_definition": {"compose_file": compose_file_of.get(svc, "ENV2_COMPOSE/docker-compose.yml"), "service": svc, "profiles": profiles,
                                        "image": image, "one_shot": svc == "ledger-scheduler"}}
            n["label"] = ro.NEW_SUBS.get(svc, {}).get("label", nid)
            if kind in ("service", "worker") and image.startswith(core_images):
                n["fidelity"] = "real_source_running"
                n["fidelity_basis"] = "compose_definition"
                n["fidelity_evidence"] = "real pinned binary defined to run in the canonical runtime: compose service %s (image %s, profile %s)" % (svc, image.split(":")[0], ",".join(profiles))
                if svc == "shield-web":
                    n["fidelity_note"] = "real source ADAPTED: one unavailable external SDK module (fingerprint-sdk, 404) stubbed at build time; everything else is the pinned repository (ENV2_COMPOSE/build/m11/build-shield.sh)"
            elif kind == "datastore" and compose_file_of.get(svc, "").endswith("m11.yml"):
                n["fidelity"] = "real_source_running"
                n["fidelity_basis"] = "compose_definition"
                n["fidelity_evidence"] = "real datastore engine defined for the promoted service: compose service %s (image %s)" % (svc, image)
            elif svc in ro.NEW_SUBS:
                n["fidelity"] = "high_fidelity_replacement"
                n["fidelity_basis"] = "contract_declaration"
                n["fidelity_evidence"] = "substitute defined by compose service %s; class per %s" % (svc, ro.NEW_SUBS[svc]["contract"])
                n["source_refs"] = [ro.NEW_SUBS[svc]["contract"]]
                n["owner_domain"] = "workflows" if svc == "workflow-engine" else "batch"
            else:
                # substitutes: class stays with the declaring lane; only the definition is recorded
                n["fidelity"] = None
            nodes.append(n)
    for svc, spec in sorted(s2p.items()):
        nid = {"s2p-vp-source": "svc:s2p/vendor-payments", "s2p-kafka": "db:s2p/kafka", "s2p-mysql": "db:s2p/mysql", "s2p-redis": "db:s2p/redis"}.get(svc)
        if nid and nid in graph_node_ids:
            nodes.append({"id": nid, "kind": nid.split(":")[0].replace("svc", "service").replace("db", "datastore"), "criticality": criticality.get(nid, "P0"), "confidence": "confirmed",
                          "owner_domain": "vendor-payments", "twin_ref": "compose:" + svc, "compose_service": svc, "profile": "s2p",
                          "runtime_definition": {"compose_file": "ENV2_COMPOSE/docker-compose.s2p.yml", "service": svc, "profiles": ["s2p"],
                                                 "image": str(spec.get("image") or ""), "one_shot": False}, "fidelity": None, "label": nid})
    return {"lane": "zz-compose-definition", "sources": [{"repo": "twin", "path": "ENV2_COMPOSE/docker-compose.yml + docker-compose.s2p.yml"}],
            "nodes": nodes, "edges": edges, "families": []}


# --------------------------------------------------------------------------- journey definitions
DECLARED_FIDELITY = {"real": "real_source_running", "substitute": "high_fidelity_replacement"}


def journey_definition_part(part_path=paths.PARTS / "m6-journeys.json", summary_path=paths.IMPL / "m6-journeys.json"):
    jp = _mod("m6_journeys_part", paths.REPO / "scripts/domain/journeys_part.py")
    src = json.loads(part_path.read_text())
    declared = {}
    if summary_path.is_file():
        for j in json.loads(summary_path.read_text()).get("journeys", []):
            declared[j["id"]] = j.get("fidelity")
    nodes, edges = [], []
    for n in src.get("nodes", []):
        fam, var = n.get("family"), n.get("variant")
        raw = declared.get(n["id"]) or "real"
        fid = "real_source_running" if raw.split()[0].startswith("real") else DECLARED_FIDELITY.get(raw, "high_fidelity_replacement")
        proves = list(jp.PROVES.get(fam, [])) + list(jp.VARIANT_PROVES.get(var, []))
        d = {"id": n["id"], "kind": "journey", "label": "%s/%s" % (fam, var), "owner_domain": "payouts", "family": fam, "variant": var,
             "criticality": n.get("criticality") or "P0", "fidelity": fid, "fidelity_basis": "journey_definition",
             "fidelity_declared": raw, "confidence": "confirmed", "twin_ref": "RED_LOOP/m6/journeys/run.py",
             "fidelity_evidence": "journey driver declares execution fidelity '%s' (RED_LOOP/m6/journeys); results live in the evidence bundle" % raw,
             "proves_families": proves}
        nodes.append(d)
        if fam:
            edges.append({"from": n["id"], "to": fam, "type": "implements", "confidence": "confirmed"})
        for sub in jp.DEPENDS_ON_SUBS.get(fam, []) + jp.DEPENDS_ON_ALL:
            edges.append({"from": n["id"], "to": sub, "type": "depends_on", "confidence": "confirmed", "via": "substitute exercised by this journey"})
    return {"lane": "m6-journeys", "sources": [{"repo": "twin", "path": "reports/domain/parts/m6-journeys.json (definitions only)"}],
            "nodes": nodes, "edges": edges, "families": []}


# --------------------------------------------------------------------------- build
def _clean_node(n):
    n = dict(n)
    for k in VOLATILE_NODE_FIELDS:
        n.pop(k, None)
    if n.get("source_refs"):
        n["source_refs"] = [normalize_ref(s) for s in n["source_refs"]]
    if n.get("evidence_file"):
        n["evidence_file"] = normalize_ref(n["evidence_file"])
    fe = n.get("fidelity_evidence")
    if fe:
        fe = re.sub(r"container for compose service (\S+) is Up [^;|)]*\(healthy\)", r"compose service \1 defined in the canonical runtime", fe)
        fe = re.sub(r"container for compose service (\S+) is Up [^;|)]*", r"compose service \1 defined in the canonical runtime", fe)
        n["fidelity_evidence"] = normalize_ref(fe)
    if "lanes" in n:
        n["lanes"] = sorted(n["lanes"])
    return {k: n[k] for k in sorted(n)}


def _clean_edge(e):
    e = dict(e)
    if e.get("source_refs"):
        e["source_refs"] = [normalize_ref(s) for s in e["source_refs"]]
    if "lanes" in e:
        e["lanes"] = sorted(set(e["lanes"]))
    return {k: e[k] for k in sorted(e)}


def build_static_graph(lock, parts_dir=paths.PARTS):
    from . import lock as L
    bg = load_build_graph()
    readable = L.readable_repo_names(lock)
    bg._reachable_repos = lambda: readable
    parts, inputs = load_parts(parts_dir)
    for d in parts:
        for s in d.get("sources", []) or []:
            if isinstance(s, dict) and "path" in s:
                s["path"] = normalize_ref(str(s["path"]))
    ids = {n["id"] for d in parts for n in d.get("nodes", [])}
    # criticality as the declaring lanes see it (highest rank wins, like the merge); the definition overlay never upgrades it
    crit, crit_rank = {}, {}
    for d in parts:  # load order == build_graph.merge order; a later lane overrides only with a strictly higher rank
        rank = bg.LANE_RANK.get(d["lane"], 10)
        for n in d.get("nodes", []):
            if n.get("criticality") and (n["id"] not in crit or rank > crit_rank[n["id"]]):
                crit[n["id"]] = n["criticality"]; crit_rank[n["id"]] = rank
    jdef = journey_definition_part()
    cdef = compose_definition_part(ids | {n["id"] for n in jdef["nodes"]}, criticality=crit)
    # substitutes keep their lane class: strip the None fidelity before validation/merge
    for n in cdef["nodes"]:
        if n.get("fidelity") is None:
            n.pop("fidelity", None)
    # journeys rank 150 (as M6), compose-definition rank 200 (the slot the runtime overlay had): a real binary's
    # compose definition decides fidelity for svc/worker nodes; substitutes are untouched (no fidelity emitted)
    bg.LANE_RANK["zz-compose-definition"] = 200
    all_parts = parts + [jdef, cdef]
    errs = bg.validate([p for p in all_parts if p["lane"] != "zz-compose-definition"])
    errs += [e for e in bg.validate([cdef]) if "bad fidelity" not in e]
    nodes, edges, families, dangling = bg.merge(all_parts)
    st = bg.stats(nodes, edges, families, dangling, all_parts)
    st.pop("generated_at", None)
    graph = {"nodes": [_clean_node(n) for n in sorted(nodes.values(), key=lambda n: n["id"])],
             "edges": sorted((_clean_edge(e) for e in edges), key=lambda e: (e["from"], e["to"], e.get("type") or "", e.get("via") or "")),
             "families": [dict(sorted(f.items())) for f in sorted(families.values(), key=lambda f: f["id"])]}
    return graph, st, [paths.rel(p) for p in inputs], errs, {"lane_ranks": dict(sorted(bg.LANE_RANK.items()))}
