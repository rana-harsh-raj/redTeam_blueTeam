"""ProductionUnknown registry: PU-1..PU-8 (scripts/m7/canonical_snapshot.py UNKNOWNS), PU-9..PU-10
(reports/implementation/M7_PRODUCTION_UNKNOWNS.md rows) and the four Source-to-Pay ids
(DOMAIN_REPLICAS/source_to_pay/spec/unresolved-production-unknowns.yaml), each keeping its original id and file."""
import importlib.util
import re
from . import paths
from .canon import sha256_file


def _load_m7_unknowns():
    p = paths.REPO / "scripts/m7/canonical_snapshot.py"
    spec = importlib.util.spec_from_file_location("m7_canonical_snapshot", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.UNKNOWNS, paths.rel(p)


def _parse_md_rows(text):
    rows = {}
    for line in text.splitlines():
        m = re.match(r"^\|\s*(PU-\d+)\s*\|(.*)\|\s*$", line)
        if not m:
            continue
        raw = re.sub(r"`[^`]*`", lambda mm: mm.group(0).replace("|", "\x00"), m.group(2))
        cells = [c.replace("\x00", "|").strip() for c in raw.split("|")]
        if len(cells) >= 4:
            rows[m.group(1)] = {"topic": cells[0], "statement": cells[1], "twin_behaviour": cells[2], "affects_text": cells[3]}
    closing = ""
    m = re.search(r"^Closing any row requires (.*?)\n\n", text + "\n\n", re.S | re.M)
    if m:
        closing = "Closing requires " + " ".join(m.group(1).split())
    return rows, closing


def _affects_from_text(t):
    out = []
    for tok in re.split(r",\s*", t):
        tok = tok.strip().strip("`")
        if not tok:
            continue
        out.append(tok)
    return out


def _load_s2p():
    import yaml
    d = yaml.safe_load(paths.S2P_UNKNOWNS.read_text())
    return d.get("unknowns", [])


def build_registry(node_ids, route_names=None) -> dict:
    route_names = route_names or {}
    m7, m7_src = _load_m7_unknowns()
    md_text = paths.M7_UNKNOWNS_MD.read_text()
    rows, closing = _parse_md_rows(md_text)
    md_rel = paths.rel(paths.M7_UNKNOWNS_MD)
    entries = []
    for u in m7:
        r = rows.get(u["id"], {})
        entries.append({"id": u["id"], "topic": u["topic"], "statement": u["statement"],
                        "twin_behaviour": r.get("twin_behaviour"), "affects": sorted(u["affects"]),
                        "affects_unresolved": sorted(a for a in u["affects"] if a not in node_ids),
                        "closure_evidence_required": closing or None, "domain": "payouts",
                        "origin": {"file": m7_src, "original_id": u["id"], "also_in": md_rel}})
    for pid, r in sorted(rows.items(), key=lambda kv: int(kv[0].split("-")[1])):
        if any(e["id"] == pid for e in entries):
            continue
        affects = _affects_from_text(r["affects_text"])
        resolved = []
        for a in affects:
            if a in node_ids:
                resolved.append(a)
            elif a in route_names:
                resolved.append(route_names[a])
            elif a.endswith("/*"):
                pref = a[:-1]
                resolved.extend(sorted(i for i in node_ids if i.startswith(pref)))
            else:
                cand = [i for i in node_ids if i.endswith("/" + a) or i == "route:api-monolith/" + a]
                resolved.extend(sorted(cand))
        entries.append({"id": pid, "topic": r["topic"], "statement": r["statement"], "twin_behaviour": r["twin_behaviour"],
                        "affects": sorted(set(resolved)), "affects_unresolved": sorted(a for a in affects if a not in node_ids and not resolved),
                        "affects_text": r["affects_text"], "closure_evidence_required": closing or None, "domain": "payouts",
                        "origin": {"file": md_rel, "original_id": pid, "machine_readable_in_m7": False}})
    for u in _load_s2p():
        uid = u["id"]
        entries.append({"id": "S2P:" + uid, "topic": uid.split(".")[-1].replace("-", " "), "statement": u["description"],
                        "twin_behaviour": None, "affects": sorted(i for i in node_ids if i.startswith(("svc:s2p/", "sub:s2p-"))) if "deployment" in uid or "feature" in uid else [],
                        "affects_unresolved": [], "closure_evidence_required": None, "domain": "source_to_pay",
                        "origin": {"file": paths.rel(paths.S2P_UNKNOWNS), "original_id": uid}})
    return {"kind": "production_unknown_registry", "schema_version": "m8.1", "count": len(entries), "entries": entries,
            "sources": {m7_src: sha256_file(paths.REPO / m7_src), md_rel: sha256_file(paths.M7_UNKNOWNS_MD),
                        paths.rel(paths.S2P_UNKNOWNS): sha256_file(paths.S2P_UNKNOWNS)}}
