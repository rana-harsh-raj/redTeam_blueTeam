"""Durable instance registry (FACTORY_HOME/registry.json) guarded by an advisory file lock, plus per-instance event log.
Records hold identity and location only; the instance manifest is the full binding (snapshot, recipes, seed, hashes)."""
import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path

from . import paths
from .util import now, append_jsonl


class Registry:
    def __init__(self, path=None):
        self.path = Path(path) if path else paths.registry_path()

    @contextmanager
    def lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(".lock")
        with lock.open("a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def load(self):
        if not self.path.is_file():
            return {"kind": "twin_instance_registry", "schema_version": "m9.1", "instances": {}}
        return json.loads(self.path.read_text())

    def _save(self, doc):
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def records(self):
        return list(self.load()["instances"].values())

    def get(self, instance_id):
        return self.load()["instances"].get(instance_id)

    def upsert(self, rec):
        with self.lock():
            doc = self.load()
            cur = doc["instances"].get(rec["instance_id"], {})
            cur.update(rec)
            cur["updated_at"] = now()
            cur.setdefault("created_at", cur["updated_at"])
            doc["instances"][rec["instance_id"]] = cur
            self._save(doc)
            return cur

    def set_state(self, instance_id, state, **fields):
        rec = {"instance_id": instance_id, "state": state}
        rec.update(fields)
        out = self.upsert(rec)
        self.event(instance_id, "state", state=state, **{k: v for k, v in fields.items() if isinstance(v, (str, int, float, bool)) or v is None})
        return out

    def remove(self, instance_id, keep_history=True):
        with self.lock():
            doc = self.load()
            rec = doc["instances"].pop(instance_id, None)
            if rec and keep_history:
                doc.setdefault("destroyed", []).append({k: rec.get(k) for k in ("instance_id", "profile", "architecture_snapshot_id", "runtime_instance_id", "created_at", "exec_dir")} | {"destroyed_at": now()})
            self._save(doc)
            return rec

    def event(self, instance_id, kind, **fields):
        d = paths.instances_dir() / instance_id
        append_jsonl(d / "events.jsonl", {"at": now(), "kind": kind, **fields})
