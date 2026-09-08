"""Query library. Every result is an envelope:
  {snapshot_id, query, params, count, truncated, items, fidelity: {label: count}, source_refs: [...],
   production_unknowns: [ids], detail_namespace_loaded: bool}
Queries are bounded (limit / max_hops / budget) and deterministic (sorted, no clock). The Source-to-Pay detail
namespace is loaded only by `s2p_detail(...)`; canonical queries never touch it."""
import json
from collections import Counter, deque
from pathlib import Path
from . import paths
from .index import GraphIndex, DEP_TYPES, DATA_TYPES
from .store import SnapshotStore
from .canon import content_id, sha256_file

EXEC_LABELS = ("ACTUAL_SOURCE_RUNNING", "CONTRACT_FAITHFUL_REPLACEMENT")
MAX_LIMIT = 500


class QueryError(ValueError):
    pass


class Query:
    def __init__(self, store=None, snapshot_id=None):
        self.store = store if isinstance(store, SnapshotStore) else SnapshotStore(store)
        self.sid = self.store.resolve(snapshot_id)
        doc = self.store.load(self.sid)
        self.ix = GraphIndex(self.sid, doc["body"])
        self._imports = None
        self._recipes = None

    # ---- envelope ----
    def _node_view(self, nid, full=False):
        n = self.ix.nodes.get(nid)
        if n is None:
            return {"id": nid, "missing": True}
        v = {"id": nid, "kind": n["kind"], "label": n.get("label"), "fidelity": n.get("fidelity"), "m8_label": self.ix.label_of(nid),
             "criticality": n.get("criticality"), "owner_domain": n.get("owner_domain"), "twin_ref": n.get("twin_ref"),
             "source_refs": (n.get("source_refs") or [])[:8], "production_unknowns": self.ix.unknowns_by_node.get(nid, [])}
        if full:
            v["node"] = n
        return v

    def _env(self, query, params, items, truncated=False, ids=(), extra=None):
        ids = list(ids)
        refs = []
        for i in ids[:50]:
            for r in (self.ix.nodes.get(i, {}).get("source_refs") or [])[:3]:
                if r not in refs:
                    refs.append(r)
        env = {"snapshot_id": self.sid, "query": query, "params": params, "count": len(items), "truncated": bool(truncated), "items": items,
               "fidelity": dict(sorted(Counter(self.ix.label_of(i) for i in ids if i in self.ix.nodes).items())),
               "source_refs": refs[:40], "production_unknowns": self.ix.unknowns_for(ids), "detail_namespace_loaded": self.store.s2p_detail_loads > 0}
        if extra:
            env.update(extra)
        return env

    def _need(self, nid):
        if nid not in self.ix.nodes:
            raise QueryError("unknown node id %r (try search)" % nid)
        return nid

    @staticmethod
    def _limit(limit):
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 50
        return max(1, min(limit, MAX_LIMIT))

    # ---- capabilities ----
    def snapshot(self):
        s = self.store.summary(self.sid)
        s["registry"] = self.store.registry().get("snapshots", {}).get(self.sid, {})
        s["imports"] = sorted(self.store.imports(self.sid).keys())
        return self._env("snapshot", {}, [s])

    def snapshots(self):
        reg = self.store.registry()
        items = [{"snapshot_id": i, "registry": reg.get("snapshots", {}).get(i, {}), "current": reg.get("current") == i} for i in self.store.ids()]
        return {"query": "snapshots", "count": len(items), "items": items, "current": reg.get("current"), "truncated": False}

    def node(self, id):
        self._need(id)
        v = self._node_view(id, full=True)
        v["families"] = sorted(self.ix.families_of.get(id, []))
        v["degree"] = {"out": len(self.ix.out.get(id, [])), "in": len(self.ix.inc.get(id, []))}
        return self._env("node", {"id": id}, [v], ids=[id])

    def edges(self, id, direction="both", types=None, limit=100):
        self._need(id)
        limit = self._limit(limit)
        types = _list(types)
        es = self.ix.edges_of(id, direction, types)
        items = es[:limit]
        ids = {id} | {e["from"] for e in items} | {e["to"] for e in items}
        return self._env("edges", {"id": id, "direction": direction, "types": types, "limit": limit}, items, len(es) > limit, sorted(ids))

    def search(self, q, kinds=None, limit=50):
        limit = self._limit(limit)
        kinds = _list(kinds)
        ql = (q or "").lower().strip()
        if not ql:
            raise QueryError("q is required")
        toks = ql.split()
        scored = []
        for nid, blob in self.ix.text.items():
            if kinds and self.ix.nodes[nid]["kind"] not in kinds:
                continue
            if all(t in blob for t in toks):
                score = 0 if nid.lower() == ql else 1 if ql in nid.lower() else 2 if ql in (self.ix.nodes[nid].get("label") or "").lower() else 3
                scored.append((score, nid))
        scored.sort()
        items = [self._node_view(n) for _, n in scored[:limit]]
        return self._env("search", {"q": q, "kinds": kinds, "limit": limit}, items, len(scored) > limit, [i["id"] for i in items])

    def service_card(self, id):
        self._need(id)
        n = self.ix.nodes[id]
        groups = {"calls": [], "called_by": [], "depends_on": [], "depended_on_by": [], "implements": [], "implemented_by": [],
                  "reads": [], "writes": [], "produces": [], "consumes": [], "authenticated_by": [], "gated_by": []}
        for e in self.ix.edges_of(id, "out"):
            t = e["type"]
            if t in ("calls", "depends_on", "implements", "reads", "writes", "produces", "consumes", "gated_by"):
                groups[t].append({"to": e["to"], "via": e.get("via"), "kind": self.ix.nodes.get(e["to"], {}).get("kind")})
        for e in self.ix.edges_of(id, "in"):
            t = e["type"]
            key = {"calls": "called_by", "depends_on": "depended_on_by", "implements": "implemented_by", "authenticates": "authenticated_by"}.get(t)
            if key:
                groups[key].append({"from": e["from"], "via": e.get("via"), "kind": self.ix.nodes.get(e["from"], {}).get("kind")})
        fams = sorted(self.ix.families_of.get(id, []))
        journeys = sorted({j for f in fams for j in self.ix.journeys_by_family.get(f, [])} | {e["from"] for e in self.ix.edges_of(id, "in", ("depends_on",)) if e["from"].startswith("journey:")})
        recipe = None
        svc = n.get("compose_service")
        if svc:
            recipe = self.store.recipe(svc, self.sid)
        card = {"id": id, "kind": n["kind"], "label": n.get("label"), "owner_domain": n.get("owner_domain"), "criticality": n.get("criticality"),
                "fidelity": {"class": n.get("fidelity"), "m8_label": self.ix.label_of(id), "basis": n.get("fidelity_basis"), "evidence": n.get("fidelity_evidence"),
                             "missing_dependency": n.get("missing_dependency")},
                "source": {"repo": n.get("repo"), "sha": n.get("sha"), "source_refs": n.get("source_refs") or [], "entry_points": n.get("entry_points") or []},
                "runtime_definition": n.get("runtime_definition"), "twin_ref": n.get("twin_ref"), "identity_model": n.get("identity_model"),
                "authorization": n.get("authorization"), "build": n.get("build"), "health": n.get("health"),
                "tables": n.get("tables") or [], "queues": n.get("queues") or [], "topics": n.get("topics") or [], "feature_flags": n.get("feature_flags") or [],
                "external_deps": n.get("external_deps") or [], "relations": {k: sorted(v, key=lambda x: x.get("to") or x.get("from")) for k, v in groups.items()},
                "families": fams, "journeys": journeys, "production_unknowns": [self.ix.unknowns[u] for u in self.ix.unknowns_by_node.get(id, [])],
                "recipe": {k: recipe[k] for k in ("id", "kind", "source", "fidelity", "reset", "unknowns") if recipe and k in recipe} if recipe else None,
                "lanes": n.get("lanes")}
        rel_ids = {id} | {x.get("to") or x.get("from") for v in groups.values() for x in v}
        return self._env("service_card", {"id": id}, [card], ids=sorted(i for i in rel_ids if i))

    def neighbors(self, id, depth=1, types=None, kinds=None, limit=100, direction="both"):
        self._need(id)
        depth = max(1, min(int(depth or 1), 4))
        limit = self._limit(limit)
        types, kinds = _list(types), _list(kinds)
        seen = {id: 0}
        q = deque([id])
        items = []
        truncated = False
        while q:
            u = q.popleft()
            if seen[u] >= depth:
                continue
            for e, other in self.ix.neighbors(u, direction, types, kinds):
                if other not in seen:
                    seen[other] = seen[u] + 1
                    if len(items) >= limit:
                        truncated = True
                        continue
                    items.append({"id": other, "depth": seen[other], "via": {"from": e["from"], "to": e["to"], "type": e["type"], "via": e.get("via")},
                                  "kind": self.ix.nodes.get(other, {}).get("kind"), "m8_label": self.ix.label_of(other)})
                    q.append(other)
        return self._env("neighbors", {"id": id, "depth": depth, "types": types, "kinds": kinds, "limit": limit, "direction": direction}, items, truncated, [id] + [i["id"] for i in items])

    def dependencies(self, id, direction="out", transitive=False, max_depth=3, types=None, limit=200):
        types = _list(types) or list(DEP_TYPES)
        if _truthy(transitive):
            return self.neighbors(id, depth=max_depth, types=types, limit=limit, direction=direction) | {"query": "dependencies"}
        return self.neighbors(id, depth=1, types=types, limit=limit, direction=direction) | {"query": "dependencies"}

    def _bfs(self, a, b, types, max_hops, undirected):
        prev = {a: None}
        q = deque([(a, 0)])
        while q:
            u, d = q.popleft()
            if u == b:
                break
            if d >= max_hops:
                continue
            for e, other in self.ix.neighbors(u, "both" if undirected else "out", types):
                if other not in prev:
                    prev[other] = (u, e)
                    q.append((other, d + 1))
        if b not in prev:
            return None
        path, edges = [b], []
        cur = b
        while prev[cur] is not None:
            u, e = prev[cur]
            path.append(u)
            edges.append(e)
            cur = u
        return list(reversed(path)), list(reversed(edges))

    def shortest_path(self, a, b, types=None, max_hops=12, undirected=False):
        self._need(a); self._need(b)
        types = _list(types)
        max_hops = max(1, min(int(max_hops or 12), 30))
        r = self._bfs(a, b, types, max_hops, _truthy(undirected))
        items = [] if r is None else [{"path": r[0], "hops": len(r[1]), "edges": [{"from": e["from"], "to": e["to"], "type": e["type"], "via": e.get("via")} for e in r[1]]}]
        return self._env("shortest_path", {"a": a, "b": b, "types": types, "max_hops": max_hops, "undirected": bool(undirected)}, items, False, r[0] if r else [a, b],
                         {"found": r is not None})

    def paths(self, a, b, max_hops=6, limit=10, types=None):
        self._need(a); self._need(b)
        types = _list(types)
        max_hops = max(1, min(int(max_hops or 6), 8))
        limit = self._limit(limit)
        found, truncated = [], False
        budget = [20000]  # expansion budget keeps the search bounded on dense hubs
        stack = [(a, [a], [])]
        while stack:
            u, path, es = stack.pop()
            budget[0] -= 1
            if budget[0] <= 0:
                truncated = True
                break
            if u == b:
                found.append({"path": path, "hops": len(es), "edges": [{"from": e["from"], "to": e["to"], "type": e["type"], "via": e.get("via")} for e in es]})
                if len(found) >= limit:
                    truncated = True
                    break
                continue
            if len(es) >= max_hops:
                continue
            nxt = [(e, o) for e, o in self.ix.neighbors(u, "out", types) if o not in path]
            for e, o in reversed(nxt):
                stack.append((o, path + [o], es + [e]))
        found.sort(key=lambda p: (p["hops"], p["path"]))
        ids = sorted({n for p in found for n in p["path"]}) or [a, b]
        return self._env("paths", {"a": a, "b": b, "max_hops": max_hops, "limit": limit, "types": types}, found, truncated, ids, {"expansion_budget_exhausted": budget[0] <= 0})

    def identity_reachability(self, identity, max_hops=3, limit=200):
        self._need(identity)
        max_hops = max(1, min(int(max_hops or 3), 6))
        limit = self._limit(limit)
        n = self.ix.nodes[identity]
        boundaries = [e["to"] for e in self.ix.edges_of(identity, "out", ("depends_on", "gated_by")) if e["to"].startswith("identity:trust-boundary")]
        routes = sorted({e["to"] for e in self.ix.edges_of(identity, "out", ("authenticates",))})
        reach = {}
        q = deque([(r, 1) for r in routes])
        for r in routes:
            reach[r] = 1
        while q:
            u, d = q.popleft()
            if d >= max_hops:
                continue
            for e, o in self.ix.neighbors(u, "out", ("calls", "callback", "depends_on", "writes", "produces", "implements", "reads", "consumes")):
                if o not in reach and o != identity:
                    reach[o] = d + 1
                    q.append((o, d + 1))
        items = [{"id": i, "hops": h, "kind": self.ix.nodes.get(i, {}).get("kind"), "m8_label": self.ix.label_of(i)} for i, h in sorted(reach.items(), key=lambda kv: (kv[1], kv[0]))]
        truncated = len(items) > limit
        items = items[:limit]
        return self._env("identity_reachability", {"identity": identity, "max_hops": max_hops, "limit": limit}, items, truncated, [identity] + [i["id"] for i in items],
                         {"identity_model": n.get("identity_model"), "trust_boundaries": boundaries, "authenticated_routes": len(routes),
                          "reachable_by_kind": dict(sorted(Counter(i["kind"] for i in items).items()))})

    def data_flow(self, id, direction="both", limit=200):
        self._need(id)
        limit = self._limit(limit)
        es = self.ix.edges_of(id, direction, DATA_TYPES)
        items = [{"from": e["from"], "to": e["to"], "type": e["type"], "via": e.get("via"), "role": ("writer" if e["type"] in ("writes", "produces") else "reader" if e["type"] in ("reads", "consumes") else e["type"]),
                  "other": (e["to"] if e["from"] == id else e["from"]), "other_kind": self.ix.nodes.get(e["to"] if e["from"] == id else e["from"], {}).get("kind")} for e in es]
        n0 = self.ix.nodes[id]
        if n0["kind"] == "table":
            for e0 in self.ix.edges_of(id, "in", ("owns",)):
                ds = e0["from"]
                for e in self.ix.edges_of(ds, direction, ("reads", "writes", "produces", "consumes")):
                    other = e["to"] if e["from"] == ds else e["from"]
                    items.append({"from": e["from"], "to": e["to"], "type": e["type"], "via": e.get("via"), "role": ("writer" if e["type"] in ("writes", "produces") else "reader"),
                                  "other": other, "other_kind": self.ix.nodes.get(other, {}).get("kind"), "via_datastore": ds})
        for nid, field in self.ix.declared_by.get(id, []):
            items.append({"from": nid, "to": id, "type": "declares:" + field, "via": "node attribute " + field, "role": "declared_user", "other": nid, "other_kind": self.ix.nodes[nid]["kind"]})
        n = self.ix.nodes[id]
        for field in ("tables", "queues", "topics"):
            for t in n.get(field) or []:
                items.append({"from": id, "to": t, "type": "declares:" + field, "via": "node attribute " + field, "role": "declares", "other": t, "other_kind": self.ix.nodes.get(t, {}).get("kind")})
        items.sort(key=lambda x: (x["type"], x["other"]))
        truncated = len(items) > limit
        items = items[:limit]
        return self._env("data_flow", {"id": id, "direction": direction, "limit": limit}, items, truncated, [id] + [i["other"] for i in items],
                         {"by_type": dict(sorted(Counter(e["type"] for e in es).items()))})

    def families(self, priority=None):
        fams = [f for f in self.ix.families.values() if not priority or f.get("priority") == priority]
        items = [{"id": f["id"], "label": f.get("label"), "priority": f.get("priority"), "fidelity": f.get("fidelity"), "components": len(f.get("components") or []),
                  "journey_definitions": len(self.ix.journeys_by_family.get(f["id"], []))} for f in sorted(fams, key=lambda f: f["id"])]
        return self._env("families", {"priority": priority}, items, False, [i["id"] for i in items])

    def family(self, id):
        if id not in self.ix.families:
            raise QueryError("unknown family %r" % id)
        f = dict(self.ix.families[id])
        comps = f.get("components") or []
        f["component_labels"] = {c: self.ix.label_of(c) for c in comps}
        f["journeys"] = [self._journey_view(j) for j in self.ix.journeys_by_family.get(id, [])]
        return self._env("family", {"id": id}, [f], ids=[id] + comps)

    def _journey_view(self, jid):
        n = self.ix.nodes[jid]
        v = {"id": jid, "family": n.get("family"), "variant": n.get("variant"), "priority": n.get("criticality"), "fidelity_declared": n.get("fidelity_declared"),
             "fidelity": n.get("fidelity"), "proves_families": n.get("proves_families") or [], "twin_ref": n.get("twin_ref"),
             "depends_on": sorted(e["to"] for e in self.ix.edges_of(jid, "out", ("depends_on",)))}
        ev = self._evidence_for(jid)
        if ev:
            v["evidence"] = ev
        return v

    def journeys(self, family=None, limit=300):
        limit = self._limit(limit)
        ids = self.ix.journeys_by_family.get(family, []) if family else self.ix.by_kind.get("journey", [])
        items = [self._journey_view(j) for j in ids[:limit]]
        return self._env("journeys", {"family": family, "limit": limit}, items, len(ids) > limit, [i["id"] for i in items])

    def journey(self, id):
        self._need(id)
        return self._env("journey", {"id": id}, [self._journey_view(id)], ids=[id])

    def fidelity_gaps(self, population="p0_critical_kinds", labels=None, kinds=None, limit=200):
        limit = self._limit(limit)
        labels, kinds = _list(labels), _list(kinds)
        from .compile import CRIT_KINDS
        pop = {"p0_critical_kinds": lambda n: n.get("criticality") == "P0" and n["kind"] in CRIT_KINDS,
               "p0_non_journey": lambda n: n.get("criticality") == "P0" and n["kind"] not in ("journey", "family"),
               "all_nodes": lambda n: True}.get(population)
        if pop is None:
            raise QueryError("population must be one of p0_critical_kinds, p0_non_journey, all_nodes")
        gaps = []
        for nid in sorted(self.ix.nodes):
            n = self.ix.nodes[nid]
            if not pop(n) or (kinds and n["kind"] not in kinds):
                continue
            lab = self.ix.label_of(nid)
            if labels and lab not in labels:
                continue
            if not labels and lab in EXEC_LABELS:
                continue
            gaps.append({"id": nid, "kind": n["kind"], "m8_label": lab, "fidelity": n.get("fidelity"), "missing_dependency": n.get("missing_dependency"),
                         "fidelity_evidence": (n.get("fidelity_evidence") or "")[:200], "production_unknowns": self.ix.unknowns_by_node.get(nid, [])})
        items = gaps[:limit]
        return self._env("fidelity_gaps", {"population": population, "labels": labels, "kinds": kinds, "limit": limit}, items, len(gaps) > limit, [i["id"] for i in items],
                         {"population_size": self.ix.body["populations"][population]["count"], "gap_total": len(gaps),
                          "by_label": dict(sorted(Counter(g["m8_label"] for g in gaps).items()))})

    def unknowns(self, node=None, topic=None, domain=None, id=None):
        es = list(self.ix.body["production_unknowns"]["entries"])
        if id:
            es = [e for e in es if e["id"] == id]
        if node:
            es = [e for e in es if node in e["affects"]]
        if topic:
            es = [e for e in es if topic.lower() in (e["topic"] + " " + e["statement"]).lower()]
        if domain:
            es = [e for e in es if e.get("domain") == domain]
        ids = sorted({a for e in es for a in e["affects"]})
        return self._env("unknowns", {"node": node, "topic": topic, "domain": domain, "id": id}, es, False, ids, {"registry_sources": self.ix.body["production_unknowns"]["sources"]})

    def uncovered_trust_boundaries(self):
        items = []
        for tb in self.ix.trust_boundaries:
            identities = sorted(e["from"] for e in self.ix.edges_of(tb, "in", ("depends_on",)))
            routes = sorted(e["from"] for e in self.ix.edges_of(tb, "in", ("gated_by",)))
            fams = sorted({f for r in routes for f in self.ix.families_of.get(r, [])} | {f for i in identities for f in self.ix.families_of.get(i, [])})
            journeys = sorted({j for f in fams for j in self.ix.journeys_by_family.get(f, [])})
            per_route = {}
            for r in routes:
                rf = sorted(self.ix.families_of.get(r, []))
                rj = sorted({j for f in rf for j in self.ix.journeys_by_family.get(f, [])})
                per_route[r] = {"families": rf, "journey_definitions": len(rj), "covered": bool(rj)}
            uncovered_routes = sorted(r for r, v in per_route.items() if not v["covered"])
            ev = self._evidence_summary(journeys)
            items.append({"trust_boundary": tb, "identities": identities, "routes": len(routes), "families": fams, "journey_definitions": len(journeys),
                          "uncovered_routes": uncovered_routes, "covered": bool(journeys) and not uncovered_routes, "evidence": ev,
                          "production_unknowns": self.ix.unknowns_for(routes + identities)})
        return self._env("uncovered_trust_boundaries", {}, items, False, self.ix.trust_boundaries, {"uncovered": [i["trust_boundary"] for i in items if not i["covered"]]})

    # ---- evidence / imports ----
    def _bundle(self):
        if self._imports is None:
            self._imports = self.store.imports(self.sid)
        m7 = self._imports.get("m7") or {}
        return m7.get("evidence_bundle")

    def _evidence_for(self, jid):
        b = self._bundle()
        if not b:
            return None
        r = b["journey_results"].get(jid)
        if not r:
            return None
        p = paths.REPO / r["evidence_path"] if r.get("evidence_path") else None
        present = bool(p and p.is_file())
        intact = present and r.get("evidence_sha256") and sha256_file(p) == r["evidence_sha256"]
        return {"result": r["result"], "checks": "%s/%s" % (r.get("checks_passed"), r.get("checks_total")), "evidence_bundle_id": b["evidence_bundle_id"],
                "runtime_instance_id": b.get("runtime_instance_id"), "evidence_path": r.get("evidence_path"), "local_file_present": present, "local_file_intact": bool(intact)}

    def _evidence_summary(self, journeys):
        b = self._bundle()
        if not b:
            return {"bundle": None}
        res = Counter(b["journey_results"][j]["result"] for j in journeys if j in b["journey_results"])
        return {"evidence_bundle_id": b["evidence_bundle_id"], "results": dict(sorted(res.items())), "journeys_with_results": sum(res.values())}

    def evidence(self, journey=None):
        b = self._bundle()
        if not b:
            return self._env("evidence", {"journey": journey}, [], extra={"note": "no evidence bundle imported for this snapshot"})
        if journey:
            self._need(journey)
            ev = self._evidence_for(journey)
            return self._env("evidence", {"journey": journey}, [ev] if ev else [], ids=[journey])
        present = sum(1 for p in b["files"] if (paths.REPO / p).is_file())
        items = [{"evidence_bundle_id": b["evidence_bundle_id"], "runtime_instance_id": b["runtime_instance_id"], "milestone": b["milestone"], "run_dir": b["run_dir"],
                  "result_histogram": b["result_histogram"], "file_count": b["file_count"], "files_present_locally": present, "other_evidence": sorted(b["other_evidence"])}]
        return self._env("evidence", {"journey": None}, items)

    def imports(self):
        imps = self.store.imports(self.sid)
        items = []
        for name, docs in imps.items():
            items.append({"import": name, "documents": {k: {kk: vv for kk, vv in v.items() if isinstance(vv, (str, int, bool)) and kk.endswith(("_id", "tag", "kind", "accepted", "boot_id", "node_ids_equal", "fidelity_delta_count", "label_delta_count"))} for k, v in docs.items()}})
        return self._env("imports", {}, items)

    def recipe(self, service):
        r = self.store.recipe(service, self.sid)
        if r is None:
            raise QueryError("no recipe for %r in this snapshot" % service)
        return self._env("recipe", {"service": service}, [r], ids=[r.get("graph_node")] if r.get("graph_node") else [])

    def recipes(self):
        idx = self.store.recipes(self.sid)
        if idx is None:
            return self._env("recipes", {}, [], extra={"note": "no recipes in this snapshot"})
        return self._env("recipes", {}, idx["recipes"], False, [], {"count": idx["count"], "unknown_field_histogram": idx.get("unknown_field_histogram"), "recipe_set_id": idx.get("recipe_set_id")})

    # ---- comparison / impact ----
    def compare(self, other):
        o = Query(self.store, other)
        A, B = self.ix.nodes, o.ix.nodes
        ea = {(e["from"], e["to"], e["type"], e.get("via") or ""): e for e in self.ix.edges}
        eb = {(e["from"], e["to"], e["type"], e.get("via") or ""): e for e in o.ix.edges}
        modified = sorted(i for i in set(A) & set(B) if content_id(A[i]) != content_id(B[i]))
        fid = [{"id": i, "from": A[i].get("fidelity"), "to": B[i].get("fidelity")} for i in modified if A[i].get("fidelity") != B[i].get("fidelity")]
        cov = {k: {"from": self.ix.body["coverage"][k], "to": o.ix.body["coverage"].get(k)} for k in self.ix.body["coverage"] if isinstance(self.ix.body["coverage"][k], dict) and self.ix.body["coverage"][k] != o.ix.body["coverage"].get(k)}
        ua, ub = set(self.ix.unknowns), set(o.ix.unknowns)
        item = {"from": self.sid, "to": o.sid, "nodes_added": sorted(set(B) - set(A)), "nodes_removed": sorted(set(A) - set(B)), "nodes_modified": modified,
                "fidelity_changes": fid, "edges_added": sorted(list(k) for k in set(eb) - set(ea)), "edges_removed": sorted(list(k) for k in set(ea) - set(eb)),
                "coverage_changes": cov, "unknowns_added": sorted(ub - ua), "unknowns_removed": sorted(ua - ub),
                "source_lock_changed": self.ix.body["source_lock_id"] != o.ix.body["source_lock_id"], "identical": self.sid == o.sid}
        return self._env("compare", {"other": o.sid}, [item], False, item["nodes_added"] + item["nodes_removed"] + modified)

    def affected(self, nodes=None, other=None, limit=300):
        """Affected services / substitutes / families / journeys for a set of changed node ids (or the diff to another snapshot)."""
        limit = self._limit(limit)
        changed = set(_list(nodes) or [])
        if other:
            c = self.compare(other)["items"][0]
            changed |= set(c["nodes_added"]) | set(c["nodes_removed"]) | set(c["nodes_modified"])
            for k in c["edges_added"] + c["edges_removed"]:
                changed.update([k[0], k[1]])
        plan = _affected_plan(self.ix.body["graph"])
        touched = [self.ix.nodes[i] for i in sorted(changed) if i in self.ix.nodes]
        journeys = plan.journeys_for_nodes(touched) if touched else set()
        journeys |= {i for i in changed if i.startswith("journey:")}
        subs = sorted(i for i in changed if i.startswith("sub:"))
        svcs = sorted(i for i in changed if i.startswith(("svc:", "worker:", "cron:")))
        # substitutes stand in for services: implemented targets are affected too
        for s in subs:
            for e in self.ix.edges_of(s, "out", ("implements",)):
                if e["to"].startswith("svc:") and e["to"] not in svcs:
                    svcs.append(e["to"])
        item = {"changed_nodes": sorted(changed)[:limit], "affected_services": sorted(svcs), "affected_substitutes": subs, "affected_families": sorted(plan.families),
                "rerun_journeys": sorted(journeys)[:limit], "rerun_journeys_total": len(journeys),
                "compose_services": sorted({self.ix.nodes[i].get("compose_service") for i in changed if i in self.ix.nodes and self.ix.nodes[i].get("compose_service")})}
        return self._env("affected", {"nodes": sorted(changed)[:50], "other": other, "limit": limit}, [item], len(journeys) > limit or len(changed) > limit, sorted(changed)[:limit])

    # ---- bounded context packet ----
    def context_packet(self, subject, budget=6000):
        budget = max(800, min(int(budget or 6000), 60000))
        if subject in self.ix.families:
            core = self.family(subject)["items"][0]
            kind = "family"
            ids = list(core.get("components") or [])[:40]
        else:
            self._need(subject)
            core = self.service_card(subject)["items"][0]
            kind = "node"
            ids = [x.get("to") or x.get("from") for v in core["relations"].values() for x in v][:40]
        sections = [("subject", {"id": subject, "kind": kind, "card": core}),
                    ("neighbors", [self._node_view(i) for i in ids]),
                    ("journeys", [self._journey_view(j) for j in (core.get("journeys") or [j["id"] for j in core.get("journeys", [])] if kind == "node" else [j["id"] for j in core.get("journeys", [])])[:12]]),
                    ("production_unknowns", [self.ix.unknowns[u] for u in self.ix.unknowns_for([subject] + ids)]),
                    ("source_refs", sorted({r for i in [subject] + ids for r in (self.ix.nodes.get(i, {}).get("source_refs") or [])[:3]})[:40])]
        packet = {"snapshot_id": self.sid, "subject": subject, "budget_chars": budget, "sections": {}, "truncated": False, "dropped": []}

        def fits(trial):
            return len(json.dumps(trial, sort_keys=True)) <= budget

        for name, content in sections:
            trial = dict(packet["sections"]); trial[name] = content
            if fits(trial):
                packet["sections"][name] = content
                continue
            if isinstance(content, dict) and name == "subject":
                # the subject is never dropped: shrink its card deterministically (relation lists, then long fields)
                card = dict(content["card"])
                for step in ("relations5", "relations0", "core"):
                    if step == "relations5" and isinstance(card.get("relations"), dict):
                        card["relations"] = {k: v[:5] for k, v in card["relations"].items() if v}
                    elif step == "relations0":
                        card.pop("relations", None); card.pop("production_unknowns", None); card.pop("recipe", None)
                    else:
                        card = {k: card.get(k) for k in ("id", "kind", "label", "fidelity", "families", "journeys", "twin_ref", "priority", "description") if k in card}
                        card["journeys"] = (card.get("journeys") or [])[:5]
                    trial[name] = {"id": subject, "kind": kind, "card": card, "shrunk": step}
                    if fits(trial):
                        break
                packet["sections"][name] = trial[name]
                packet["truncated"] = True
                packet["dropped"].append({"section": name, "shrunk": trial[name].get("shrunk")})
                continue
            if isinstance(content, list):
                lst = list(content)
                while lst:
                    lst = lst[:max(0, len(lst) // 2)]
                    trial[name] = lst
                    if fits(trial):
                        break
                if lst:
                    packet["sections"][name] = lst
                    packet["truncated"] = True
                    packet["dropped"].append({"section": name, "kept": len(lst), "of": len(content)})
                    continue
            packet["truncated"] = True
            packet["dropped"].append({"section": name, "kept": 0})
        packet["chars"] = len(json.dumps(packet["sections"], sort_keys=True))
        return self._env("context_packet", {"subject": subject, "budget": budget}, [packet], packet["truncated"], [subject] + ids)

    # ---- Source-to-Pay detail namespace (explicit) ----
    def s2p_detail(self, id=None, prefix=None, expand=False, limit=100):
        """Loads the 27,994-node patch ONLY here. `id` may be a canonical node id (its s2p_id is resolved) or a detail id."""
        limit = self._limit(limit)
        if not (id or prefix):
            raise QueryError("id or prefix is required")
        detail = self.store.load_s2p_detail(self.sid)
        nodes = detail["nodes"]
        by_id = getattr(self, "_s2p_by_id", None)
        if by_id is None:
            by_id = {n["id"]: n for n in nodes}
            self._s2p_by_id = by_id
            self._s2p_out = {}
            self._s2p_in = {}
            for e in detail["edges"]:
                self._s2p_out.setdefault(e.get("from") or e.get("source"), []).append(e)
                self._s2p_in.setdefault(e.get("to") or e.get("target"), []).append(e)
        sel = []
        if id:
            n = self.ix.nodes.get(id)
            if n and n.get("s2p_id") and n["s2p_id"] in by_id:
                sel = [n["s2p_id"]]
            elif id in by_id:
                sel = [id]
            elif n:
                short = id.split("/")[-1].split(":")[-1]
                sel = sorted((i for i, d in by_id.items() if d.get("repository") == short and d.get("type") in ("service", "repository", "package")),
                             key=lambda i: (by_id[i].get("type") != "service", by_id[i].get("type") != "repository", i))[:limit]
        if prefix:
            sel += sorted(i for i in by_id if i.startswith(prefix))
        sel = list(dict.fromkeys(sel))
        truncated = len(sel) > limit
        sel = sel[:limit]
        items = []
        for i in sel:
            d = by_id[i]
            item = {"detail_id": i, "canonical_id": self.ix.s2p_map.get(i), "node": d}
            if _truthy(expand):
                item["out_edges"] = self._s2p_out.get(i, [])[:limit]
                item["in_edges"] = self._s2p_in.get(i, [])[:limit]
                item["edges_truncated"] = len(self._s2p_out.get(i, [])) > limit or len(self._s2p_in.get(i, [])) > limit
            items.append(item)
        canon_ids = [self.ix.s2p_map[i] for i in sel if i in self.ix.s2p_map]
        return self._env("s2p_detail", {"id": id, "prefix": prefix, "expand": bool(expand), "limit": limit}, items, truncated, canon_ids,
                         {"namespace": detail.get("namespace"), "detail_nodes_total": len(nodes), "detail_edges_total": len(detail["edges"]),
                          "patch_sha256": self.ix.body["s2p_namespace"]["sha256"]})

    def verify(self):
        v = self.store.verify(self.sid)
        return self._env("verify", {}, [v])


CAPABILITIES = ["snapshot", "snapshots", "node", "edges", "search", "service_card", "neighbors", "dependencies", "shortest_path", "paths",
                "identity_reachability", "data_flow", "families", "family", "journeys", "journey", "fidelity_gaps", "unknowns",
                "uncovered_trust_boundaries", "evidence", "imports", "recipe", "recipes", "compare", "affected", "context_packet", "s2p_detail", "verify"]


def _list(v):
    if v in (None, "", []):
        return None
    if isinstance(v, (list, tuple)):
        return list(v)
    return [x for x in str(v).split(",") if x]


def _truthy(v):
    return str(v).lower() in ("1", "true", "yes", "on")


def _affected_plan(graph):
    import importlib.util
    p = paths.REPO / "scripts/snapshot/affected.py"
    spec = importlib.util.spec_from_file_location("m6_affected", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.Plan(graph, {})
