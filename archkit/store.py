"""Snapshot store: <STORE>/<snapshot_id>/{snapshot.json, summary.json, manifest.json, imports/..., recipes/...}
plus <STORE>/REGISTRY.json (lineage; deterministic, no clock). The full Source-to-Pay detail namespace is loaded
ONLY on explicit request (load_s2p_detail) and is never part of the canonical graph."""
import json
from pathlib import Path
from . import paths
from .canon import canonical_bytes, content_id, sha256_file


class SnapshotStore:
    def __init__(self, root=None):
        self.root = Path(root) if root else paths.STORE
        self._cache = {}
        self._s2p_detail = None
        self.s2p_detail_loads = 0

    def ids(self):
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and (p / "snapshot.json").is_file())

    def registry(self):
        p = self.root / "REGISTRY.json"
        return json.loads(p.read_text()) if p.is_file() else {"kind": "snapshot_registry", "schema_version": "m8.1", "snapshots": {}}

    def write_registry(self, reg):
        reg["snapshots"] = dict(sorted(reg["snapshots"].items()))
        (self.root / "REGISTRY.json").write_bytes(canonical_bytes(reg))

    def register(self, sid, **fields):
        reg = self.registry()
        cur = reg["snapshots"].get(sid, {})
        cur.update(fields)
        reg["snapshots"][sid] = dict(sorted(cur.items()))
        self.write_registry(reg)

    def resolve(self, sid=None):
        """Full id, or unique prefix, or 'latest' (= the registry's `current` pointer, else the only snapshot)."""
        ids = self.ids()
        if sid in (None, "", "latest", "current"):
            cur = self.registry().get("current")
            if cur in ids:
                return cur
            if len(ids) == 1:
                return ids[0]
            raise KeyError("no current snapshot; candidates: %s" % ids)
        if sid in ids:
            return sid
        m = [i for i in ids if i.startswith(sid)]
        if len(m) == 1:
            return m[0]
        raise KeyError("unknown or ambiguous snapshot id %r" % sid)

    def dir(self, sid):
        return self.root / self.resolve(sid)

    def load(self, sid=None, verify=True):
        sid = self.resolve(sid)
        if sid in self._cache:
            return self._cache[sid]
        doc = json.loads((self.root / sid / "snapshot.json").read_bytes())
        if verify:
            rec = content_id(doc["body"])
            if rec != sid or doc["snapshot_id"] != sid:
                raise ValueError("snapshot %s failed content verification (body hashes to %s)" % (sid, rec))
        self._cache[sid] = doc
        return doc

    def summary(self, sid=None):
        return json.loads((self.dir(sid) / "summary.json").read_bytes())

    def verify(self, sid=None):
        sid = self.resolve(sid)
        d = self.root / sid
        man = json.loads((d / "manifest.json").read_bytes())
        bad = [f for f, h in man["files"].items() if not (d / f).is_file() or sha256_file(d / f) != h]
        doc = json.loads((d / "snapshot.json").read_bytes())
        ok_id = content_id(doc["body"]) == sid == doc["snapshot_id"]
        return {"snapshot_id": sid, "id_recomputes": ok_id, "manifest_ok": not bad, "changed_or_missing": bad}

    def imports(self, sid=None):
        d = self.dir(sid) / "imports"
        out = {}
        if d.is_dir():
            for sub in sorted(p for p in d.iterdir() if p.is_dir()):
                out[sub.name] = {f.stem: json.loads(f.read_bytes()) for f in sorted(sub.glob("*.json"))}
        return out

    def recipes(self, sid=None):
        d = self.dir(sid) / "recipes"
        idx = d / "INDEX.json"
        return json.loads(idx.read_bytes()) if idx.is_file() else None

    def recipe(self, service, sid=None):
        p = self.dir(sid) / "recipes" / (service + ".json")
        return json.loads(p.read_bytes()) if p.is_file() else None

    # ---- Source-to-Pay detail namespace: explicit, lazy, counted ----
    def load_s2p_detail(self, sid=None):
        doc = self.load(sid)
        ref = doc["body"]["s2p_namespace"]
        p = paths.REPO / ref["path"]
        if self._s2p_detail is None:
            if sha256_file(p) != ref["sha256"]:
                raise ValueError("Source-to-Pay patch on disk does not match the snapshot's sha256")
            self._s2p_detail = json.loads(p.read_bytes())
            self.s2p_detail_loads += 1
        return self._s2p_detail
