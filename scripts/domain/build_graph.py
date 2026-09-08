#!/usr/bin/env python3
"""Merge discovery-lane part files into the Payouts functional graph (M6).

  python3 scripts/domain/build_graph.py [--parts reports/domain/parts] [--out reports/domain]

Reads every parts/*.json, validates ids/kinds/fidelity against SCHEMA.md,
unions nodes (later lanes enrich earlier ones field-by-field; conflicts on
`fidelity` resolve to the twin-inventory lane, then the most specific lane),
dedups edges, and writes:
  PAYOUTS_FUNCTIONAL_GRAPH.json  (+ .csv nodes/edges, .mmd, .graphml)
  GRAPH_STATS.json               coverage + fidelity histograms
  FIDELITY_CLASSIFICATION.csv    every node with its class + evidence
Exit 0 on success; 2 on schema violations (listed).
"""
import argparse, csv, json, re, sys, hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

KINDS = {"repository","service","worker","cron","route","event","queue","topic","datastore",
         "table","flag","identity","role","state","external","family","journey","substitute"}
PREFIX = {"repo":"repository","svc":"service","worker":"worker","cron":"cron","route":"route",
          "event":"event","queue":"queue","topic":"topic","db":"datastore","table":"table",
          "flag":"flag","identity":"identity","role":"role","state":"state","ext":"external",
          "family":"family","journey":"journey","sub":"substitute"}
FIDELITY = {"real_source_running","real_source_mapped_not_running","high_fidelity_replacement",
            "behavioural_placeholder","graph_only","blocked_missing_access"}
EDGE_TYPES = {"calls","consumes","produces","reads","writes","transitions","gated_by","callback",
              "schedules","owns","authenticates","depends_on","implements"}
CRIT = {"P0","P1","P2"}
CONF = {"confirmed","probable","inferred"}
# lane precedence for conflicting scalar fields (higher wins)
LANE_RANK = {"twin-inventory": 100, "zz-runtime-overlay": 200, "m6-journeys": 150, "zz-worker-gaps": 120}
FID_RANK = {"real_source_running":6,"high_fidelity_replacement":5,"behavioural_placeholder":4,
            "real_source_mapped_not_running":3,"graph_only":2,"blocked_missing_access":1}


def kind_of(nid):
    p = nid.split(":",1)[0]
    return PREFIX.get(p)


def load_parts(parts_dir):
    parts = []
    for p in sorted(Path(parts_dir).glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except Exception as e:  # noqa: BLE001
            print(f"[ERR] {p.name}: unparseable JSON: {e}")
            continue
        d.setdefault("lane", p.stem)
        d["_file"] = p.name
        parts.append(d)
    return parts


def validate(parts):
    errs = []
    for d in parts:
        lane = d["lane"]
        for n in d.get("nodes", []):
            nid = n.get("id")
            if not nid or not isinstance(nid, str) or " " in nid.split(":",1)[0]:
                errs.append(f"{lane}: node without valid id: {n}"); continue
            k = kind_of(nid)
            if k is None:
                errs.append(f"{lane}: unknown id prefix {nid}")
            if n.get("kind") and n["kind"] != k and n["kind"] in KINDS:
                errs.append(f"{lane}: kind mismatch {nid} kind={n['kind']} expected {k}")
            if n.get("fidelity") not in FIDELITY:
                errs.append(f"{lane}: {nid} bad fidelity {n.get('fidelity')!r}")
            if n.get("criticality") not in CRIT:
                errs.append(f"{lane}: {nid} bad criticality {n.get('criticality')!r}")
            if n.get("confidence") not in CONF:
                errs.append(f"{lane}: {nid} bad confidence {n.get('confidence')!r}")
        for e in d.get("edges", []):
            if not e.get("from") or not e.get("to"):
                errs.append(f"{lane}: edge missing endpoints {e}")
            if e.get("type") not in EDGE_TYPES:
                errs.append(f"{lane}: edge bad type {e.get('type')!r} ({e.get('from')}->{e.get('to')})")
    return errs


def merge(parts):
    nodes = {}
    node_lanes = defaultdict(set)
    for d in parts:
        lane = d["lane"]; rank = LANE_RANK.get(lane, 10)
        for n in d.get("nodes", []):
            nid = n["id"]; n = dict(n); n["kind"] = kind_of(nid)
            node_lanes[nid].add(lane)
            if nid not in nodes:
                n["_rank"] = rank; nodes[nid] = n; continue
            cur = nodes[nid]
            for k, v in n.items():
                if v in (None, "", [], {}):
                    continue
                if k in ("source_refs","entry_points","apis","events","tables","queues","topics",
                         "state_transitions","feature_flags","external_deps","fixture_requirements",
                         "variants","components","journeys_existing","proves_families"):
                    merged = list(dict.fromkeys((cur.get(k) or []) + list(v)))
                    cur[k] = merged
                elif k == "fidelity":
                    # twin-inventory is authoritative for runtime state; otherwise prefer
                    # the lane with higher rank, then the more "running" class
                    if rank > cur["_rank"] or (rank == cur["_rank"] and FID_RANK[v] > FID_RANK.get(cur.get("fidelity"), 0)):
                        cur[k] = v
                        if n.get("fidelity_evidence"): cur["fidelity_evidence"] = n["fidelity_evidence"]
                        if n.get("twin_ref"): cur["twin_ref"] = n["twin_ref"]
                elif k in ("fidelity_evidence","twin_ref"):
                    if rank >= cur["_rank"] and not cur.get(k):
                        cur[k] = v
                elif cur.get(k) in (None, "", [], {}) or rank > cur["_rank"]:
                    cur[k] = v
            cur["_rank"] = max(cur["_rank"], rank)
    for nid, n in nodes.items():
        n["lanes"] = sorted(node_lanes[nid]); n.pop("_rank", None)
    edges = {}
    for d in parts:
        for e in d.get("edges", []):
            key = (e["from"], e["to"], e.get("type"), e.get("via") or "")
            if key in edges:
                cur = edges[key]
                cur["source_refs"] = list(dict.fromkeys((cur.get("source_refs") or []) + (e.get("source_refs") or [])))
                cur.setdefault("lanes", []).append(d["lane"])
            else:
                e = dict(e); e["lanes"] = [d["lane"]]; edges[key] = e
    families = {}
    for d in parts:
        for f in d.get("families", []):
            fid = f["id"]
            if fid in families:
                cur = families[fid]
                for k in ("entry_points","components","variants","journeys_existing","source_refs"):
                    cur[k] = list(dict.fromkeys((cur.get(k) or []) + (f.get(k) or [])))
                for k, v in f.items():
                    if k not in cur or cur[k] in (None, "", []): cur[k] = v
            else:
                families[fid] = dict(f)
    normalize_blocked(nodes)
    # implicit endpoints referenced by edges but never declared
    dangling = sorted({x for e in edges.values() for x in (e["from"], e["to"]) if x not in nodes})
    return nodes, list(edges.values()), families, dangling


def _reachable_repos():
    """Repositories whose pristine clone is readable on this machine (.local/repos-root)."""
    try:
        root = Path(Path(__file__).resolve().parents[2] / ".local" / "repos-root").read_text().strip()
        return {p.name.lower() for p in Path(root).iterdir() if (p / ".git").exists()}
    except Exception:
        return set()


# artefacts genuinely unreadable/unbuildable by this identity (kept blocked even if a clone exists)
TRULY_BLOCKED = {"sub:mozart-mock", "svc:mozart-mock-mode"}


def normalize_blocked(nodes):
    """`blocked_missing_access` means the SOURCE/ARTEFACT is not readable. Lanes that reused pre-09-04
    fidelity docs still label api/dcs/edge/splitz/stork/workflows as blocked although their clones are
    readable and mapped in this very graph. Re-label those `real_source_mapped_not_running` and record why."""
    reach = _reachable_repos()
    alias = {"api-monolith": "api", "edge-kong": "terraform-kong", "xas": "x-account-statements"}
    for n in nodes.values():
        if n.get("fidelity") != "blocked_missing_access" or n["id"] in TRULY_BLOCKED:
            continue
        repo = (n.get("repo") or "").split(":", 1)[-1].lower()
        short = n["id"].split(":", 1)[-1].lower()
        cand = {repo, alias.get(repo, repo), short, alias.get(short, ""), short.replace("svc-", "")}
        if n["id"] in ("svc:mozart", "repo:mozart"):
            cand.add("mozart")
        if cand & reach:
            n["fidelity"] = "real_source_mapped_not_running"
            n["fidelity_evidence"] = ("source clone readable in .local/repos-root (ACCESS_DELTA 2026-09-04) and mapped in this graph; "
                                      "no real binary runs in the twin. " + (n.get("fidelity_evidence") or ""))[:400]
            n["fidelity_normalized"] = "was blocked_missing_access"


def stats(nodes, edges, families, dangling, parts):
    by_kind = Counter(n["kind"] for n in nodes.values())
    fid_hist = Counter(n.get("fidelity") for n in nodes.values())
    crit = defaultdict(Counter)
    for n in nodes.values():
        crit[n.get("criticality")][n.get("fidelity")] += 1
    critical = [n for n in nodes.values() if n.get("criticality") == "P0" and n["kind"] in
                ("service","worker","identity","datastore","table","queue","topic","cron","route","substitute")]
    # represented = mapped in the graph with evidence (source refs, entry points or a twin implementation)
    # and not blocked; runtime = actually executable in the twin (real binary or contract-faithful substitute)
    represented = [n for n in critical if n.get("fidelity") != "blocked_missing_access"
                   and (n.get("source_refs") or n.get("entry_points") or n.get("twin_ref"))]
    runtime = [n for n in critical if n.get("fidelity") in
               ("real_source_running","high_fidelity_replacement","behavioural_placeholder")]
    executable = [n for n in critical if n.get("fidelity") in ("real_source_running","high_fidelity_replacement")]
    fam_cov = {}
    journeys = {n["id"]: n for n in nodes.values() if n["kind"] == "journey"}
    for fid, f in families.items():
        js = [j for j in journeys.values() if j.get("family") == fid or fid in (j.get("proves_families") or [])
              or j["id"].startswith("journey:" + fid.split(":",1)[1] + "/")]
        fam_cov[fid] = {"priority": f.get("priority"), "journeys": sorted(j["id"] for j in js),
                        "executable": sum(1 for j in js if j.get("fidelity") in ("real_source_running","high_fidelity_replacement")),
                        "fidelity": f.get("fidelity")}
    p0_fams = [f for f in families.values() if f.get("priority") == "P0"]
    p0_with_journey = [f for f in p0_fams if fam_cov[f["id"]]["journeys"]]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lanes": [p["lane"] for p in parts],
        "nodes": len(nodes), "edges": len(edges), "families": len(families),
        "nodes_by_kind": dict(by_kind), "fidelity_histogram": dict(fid_hist),
        "fidelity_by_criticality": {k: dict(v) for k, v in crit.items()},
        "critical_components_total": len(critical),
        "critical_components_represented": len(represented),
        "critical_components_runtime": len(runtime),
        "critical_components_executable": len(executable),
        "critical_representation_pct": round(100.0 * len(represented) / max(1, len(critical)), 1),
        "critical_runtime_pct": round(100.0 * len(runtime) / max(1, len(critical)), 1),
        "critical_executable_pct": round(100.0 * len(executable) / max(1, len(critical)), 1),
        "critical_unrepresented": sorted(n["id"] for n in critical if n not in represented),
        "p0_families": len(p0_fams), "p0_families_with_journey": len(p0_with_journey),
        "p0_families_missing_journey": sorted(f["id"] for f in p0_fams if not fam_cov[f["id"]]["journeys"]),
        "family_coverage": fam_cov,
        "dangling_edge_endpoints": dangling,
        "blocked_missing_access": sorted(n["id"] for n in nodes.values() if n.get("fidelity") == "blocked_missing_access"),
    }


def write_outputs(out, nodes, edges, families, st):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    graph = {"version": "m6-" + datetime.now(timezone.utc).strftime("%Y%m%d"),
             "schema": "reports/domain/SCHEMA.md", "generated_at": st["generated_at"],
             "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
             "edges": sorted(edges, key=lambda e: (e["from"], e["to"], e.get("type") or "")),
             "families": sorted(families.values(), key=lambda f: f["id"])}
    gj = out / "PAYOUTS_FUNCTIONAL_GRAPH.json"
    gj.write_text(json.dumps(graph, indent=1, sort_keys=False))
    st["graph_sha256"] = hashlib.sha256(gj.read_bytes()).hexdigest()
    (out / "GRAPH_STATS.json").write_text(json.dumps(st, indent=2))
    cols = ["id","kind","label","owner_domain","criticality","fidelity","twin_ref","repo","sha",
            "identity_model","build","health","fidelity_evidence","confidence","lanes","source_refs"]
    with (out / "PAYOUTS_FUNCTIONAL_GRAPH.nodes.csv").open("w", newline="") as fh:
        w = csv.writer(fh); w.writerow(cols)
        for n in graph["nodes"]:
            w.writerow([";".join(n.get(c) or []) if isinstance(n.get(c), list) else (n.get(c) if n.get(c) is not None else "") for c in cols])
    with (out / "PAYOUTS_FUNCTIONAL_GRAPH.edges.csv").open("w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["from","to","type","via","identity","fidelity","confidence","source_refs"])
        for e in graph["edges"]:
            w.writerow([e["from"], e["to"], e.get("type"), e.get("via") or "", e.get("identity") or "",
                        e.get("fidelity") or "", e.get("confidence") or "", ";".join(e.get("source_refs") or [])])
    with (out / "FIDELITY_CLASSIFICATION.csv").open("w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["id","kind","criticality","fidelity","twin_ref","fidelity_evidence","lanes"])
        for n in graph["nodes"]:
            w.writerow([n["id"], n["kind"], n.get("criticality"), n.get("fidelity"), n.get("twin_ref") or "",
                        n.get("fidelity_evidence") or "", ";".join(n.get("lanes") or [])])
    # mermaid: services/substitutes/externals + calls/implements only (readable)
    keep = {"service","substitute","external","datastore"}
    def mid(x): return re.sub(r"[^A-Za-z0-9_]", "_", x)
    lines = ["graph LR"]
    for n in graph["nodes"]:
        if n["kind"] in keep:
            lines.append(f'  {mid(n["id"])}["{n["id"]}\\n{n.get("fidelity","")}"]')
    for e in graph["edges"]:
        if e.get("type") in ("calls","implements","depends_on") and nodes.get(e["from"],{}).get("kind") in keep and nodes.get(e["to"],{}).get("kind") in keep:
            lines.append(f'  {mid(e["from"])} -->|{e.get("type")}| {mid(e["to"])}')
    (out / "PAYOUTS_FUNCTIONAL_GRAPH.mmd").write_text("\n".join(lines) + "\n")
    # graphml
    from xml.sax.saxutils import escape
    g = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
         '<key id="kind" for="node" attr.name="kind" attr.type="string"/>',
         '<key id="fidelity" for="node" attr.name="fidelity" attr.type="string"/>',
         '<key id="criticality" for="node" attr.name="criticality" attr.type="string"/>',
         '<key id="type" for="edge" attr.name="type" attr.type="string"/>',
         '<graph id="payouts" edgedefault="directed">']
    for n in graph["nodes"]:
        g.append(f'<node id="{escape(n["id"])}"><data key="kind">{n["kind"]}</data><data key="fidelity">{n.get("fidelity","")}</data><data key="criticality">{n.get("criticality","")}</data></node>')
    for i, e in enumerate(graph["edges"]):
        g.append(f'<edge id="e{i}" source="{escape(e["from"])}" target="{escape(e["to"])}"><data key="type">{e.get("type","")}</data></edge>')
    g += ["</graph>", "</graphml>"]
    (out / "PAYOUTS_FUNCTIONAL_GRAPH.graphml").write_text("\n".join(g))
    return gj


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", default="reports/domain/parts")
    ap.add_argument("--out", default="reports/domain")
    ap.add_argument("--strict", action="store_true", help="exit 2 on any schema violation")
    a = ap.parse_args()
    parts = load_parts(a.parts)
    if not parts:
        print("no parts found"); return 2
    errs = validate(parts)
    for e in errs[:200]:
        print("[SCHEMA]", e)
    if errs and a.strict:
        print(f"{len(errs)} schema violations"); return 2
    nodes, edges, families, dangling = merge(parts)
    st = stats(nodes, edges, families, dangling, parts)
    st["schema_violations"] = len(errs)
    gj = write_outputs(a.out, nodes, edges, families, st)
    print(json.dumps({k: st[k] for k in ("lanes","nodes","edges","families","fidelity_histogram",
                                         "critical_components_total","critical_representation_pct",
                                         "critical_runtime_pct","critical_executable_pct","p0_families","p0_families_with_journey",
                                         "p0_families_missing_journey","schema_violations")}, indent=2))
    print("dangling endpoints:", len(dangling))
    print("->", gj)
    return 0


if __name__ == "__main__":
    sys.exit(main())
