"""In-process index over an ArchitectureSnapshot body. Built once per snapshot (~ms), shared by library, CLI and
HTTP service. Deterministic: every collection is sorted; adjacency lists keep edge order (edges are sorted in the
snapshot). No Source-to-Pay detail namespace is touched here."""
from collections import defaultdict

DEP_TYPES = ("calls", "depends_on", "reads", "writes", "consumes", "produces", "callback", "schedules", "implements")
DATA_TYPES = ("reads", "writes", "produces", "consumes", "callback", "owns", "transitions")


class GraphIndex:
    def __init__(self, snapshot_id, body):
        self.snapshot_id = snapshot_id
        self.body = body
        g = body["graph"]
        self.nodes = {n["id"]: n for n in g["nodes"]}
        self.edges = g["edges"]
        self.families = {f["id"]: f for f in g["families"]}
        self.labels = body["labels"]
        self.out = defaultdict(list)
        self.inc = defaultdict(list)
        for i, e in enumerate(self.edges):
            self.out[e["from"]].append(i)
            self.inc[e["to"]].append(i)
        self.by_kind = defaultdict(list)
        for nid in sorted(self.nodes):
            self.by_kind[self.nodes[nid]["kind"]].append(nid)
        self.journeys_by_family = defaultdict(list)
        self.families_of = defaultdict(set)
        for nid, n in self.nodes.items():
            if n["kind"] == "journey":
                for f in [n.get("family")] + list(n.get("proves_families") or []):
                    if f:
                        self.journeys_by_family[f].append(nid)
        for fid, f in self.families.items():
            for c in f.get("components") or []:
                self.families_of[c].add(fid)
            for c in f.get("entry_points") or []:
                self.families_of[c].add(fid)
        for k in self.journeys_by_family:
            self.journeys_by_family[k].sort()
        self.unknowns = {e["id"]: e for e in body["production_unknowns"]["entries"]}
        self.unknowns_by_node = defaultdict(list)
        for e in body["production_unknowns"]["entries"]:
            for a in e["affects"]:
                self.unknowns_by_node[a].append(e["id"])
        self.text = {}
        for nid, n in self.nodes.items():
            blob = " ".join([nid, str(n.get("label") or ""), n["kind"], str(n.get("owner_domain") or ""), str(n.get("twin_ref") or ""),
                             " ".join(n.get("source_refs") or []), " ".join(n.get("entry_points") or []), str(n.get("compose_service") or "")]).lower()
            self.text[nid] = blob
        # attribute-declared data touch points (node.tables / queues / topics) -> reverse index
        self.declared_by = defaultdict(list)
        for nid in sorted(self.nodes):
            n = self.nodes[nid]
            for field in ("tables", "queues", "topics"):
                for t in n.get(field) or []:
                    self.declared_by[t].append((nid, field))
        self.trust_boundaries = sorted(i for i in self.nodes if i.startswith("identity:trust-boundary"))
        self.identities = sorted(i for i in self.by_kind.get("identity", []) if i not in self.trust_boundaries)
        self.s2p_map = {n["s2p_id"]: nid for nid, n in self.nodes.items() if n.get("s2p_id")}

    # ---- adjacency helpers ----
    def edges_of(self, nid, direction="both", types=None):
        out = []
        if direction in ("out", "both"):
            out += [self.edges[i] for i in self.out.get(nid, [])]
        if direction in ("in", "both"):
            out += [self.edges[i] for i in self.inc.get(nid, [])]
        if types:
            out = [e for e in out if e.get("type") in types]
        return out

    def neighbors(self, nid, direction="both", types=None, kinds=None):
        res = []
        for e in self.edges_of(nid, direction, types):
            other = e["to"] if e["from"] == nid else e["from"]
            if other == nid and e["from"] == e["to"]:
                other = nid
            if kinds and self.nodes.get(other, {}).get("kind") not in kinds:
                continue
            res.append((e, other))
        return res

    def label_of(self, nid):
        return self.labels.get(nid)

    def unknowns_for(self, ids):
        seen = []
        for i in ids:
            for u in self.unknowns_by_node.get(i, []):
                if u not in seen:
                    seen.append(u)
        return sorted(seen, key=lambda u: (u.split(":")[0] != "PU", u))
