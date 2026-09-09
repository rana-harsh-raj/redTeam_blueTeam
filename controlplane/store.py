"""ControlStore — the authoritative durable state of one campaign.

Every mutation is an append to a JSONL ledger, fsync'd on write, so the campaign
survives a worker/model/process/Director kill and resumes purely by replaying
the files. Records that describe an evolving object (task, cell, thesis, worker)
are written as a base record plus ``_update`` deltas and folded last-writer-wins
by id. Evidence blobs are content-addressed under ``evidence/``.

The store deliberately exposes the exact duck-type the reused RED_LOOP managers
require (``ensure_kind`` / ``_append`` / ``_read_all`` / ``event``) so the M4
LeaseManager and HypothesisManager run against it unchanged.

No credential is ever written to a ledger; callers pass names+digests only.
"""
import hashlib
import json
import os
import threading
import fcntl
import time
from pathlib import Path

from . import paths


def utc(ts=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


# Ledgers folded last-writer-wins by these id fields.
_FOLD_KEYS = {
    "theses": "thesis_id",
    "tasks": "task_id",
    "cells": "cell_id",
    "workers": "worker_id",
    "twins": "instance_id",
    "hypotheses_v2": "hypothesis_id",
    "leases": "lease_id",
    "candidates": "candidate_id",
    "verifications": "verification_id",
}


class ControlStore:
    def __init__(self, campaign_id, root=None):
        self.campaign_id = campaign_id
        self.root = Path(root) if root else paths.campaign_dir(campaign_id)
        self.evidence_dir = self.root / "evidence"
        self.checkpoints_dir = self.root / "checkpoints"
        self.workers_dir = self.root / "workers"
        for d in (self.root, self.evidence_dir, self.checkpoints_dir, self.workers_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "manifest.json"
        self._files = {}
        self._lock = threading.Lock()
        self._seq_path = self.root / ".seq"
        self._lock_path = self.root / ".lock"
        for kind in ("theses", "tasks", "cells", "workers", "twins", "leases",
                     "handoffs", "hypotheses_v2", "candidates", "verifications",
                     "events", "model_calls", "tool_calls", "planning", "budget",
                     "narrative", "recoveries"):
            self.ensure_kind(kind)

    # -- duck-type required by reused managers --------------------------------
    def ensure_kind(self, kind, filename=None):
        if kind not in self._files:
            self._files[kind] = self.root / (filename or (kind + ".jsonl"))
        return self._files[kind]

    def _next_seq_locked(self):
        # durable monotonic sequence; caller already holds the cross-process lock
        n = 0
        if self._seq_path.exists():
            try:
                n = int(self._seq_path.read_text() or "0")
            except ValueError:
                n = 0
        n += 1
        tmp = self._seq_path.with_suffix(".tmp")
        tmp.write_text(str(n))
        tmp.replace(self._seq_path)
        return n

    def _append(self, kind, record):
        self.ensure_kind(kind)
        record = dict(record)
        record.setdefault("ts", utc())
        path = self._files[kind]
        line = None
        with self._lock:
            # a single cross-process advisory lock serialises append + seq across
            # every worker subprocess writing to this campaign's ledgers.
            with open(self._lock_path, "a+") as lk:
                fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
                try:
                    record.setdefault("seq", self._next_seq_locked())
                    line = json.dumps(record, default=str)
                    with open(path, "a") as f:
                        f.write(line + "\n")
                        f.flush()
                        os.fsync(f.fileno())
                finally:
                    fcntl.flock(lk.fileno(), fcntl.LOCK_UN)
        return record

    def _read_all(self, kind):
        self.ensure_kind(kind)
        path = self._files[kind]
        if not path.exists():
            return []
        out = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    def event(self, kind, **fields):
        fields["event"] = kind
        return self._append("events", fields)

    # -- folded reads ---------------------------------------------------------
    def fold(self, kind, key=None):
        """Fold base+``_update`` records last-writer-wins into current objects,
        preserving first-seen order."""
        key = key or _FOLD_KEYS.get(kind)
        if key is None:
            raise KeyError("no fold key for %s" % kind)
        merged, order = {}, []
        for rec in self._read_all(kind):
            k = rec.get(key)
            if k is None:
                continue
            if k not in merged:
                merged[k] = {}
                order.append(k)
            merged[k].update({kk: vv for kk, vv in rec.items() if kk != "_update"})
        return [merged[k] for k in order]

    def get(self, kind, id_value, key=None):
        key = key or _FOLD_KEYS.get(kind)
        for obj in self.fold(kind, key):
            if obj.get(key) == id_value:
                return obj
        return None

    def put(self, kind, record):
        return self._append(kind, record)

    def update(self, kind, id_value, **fields):
        key = _FOLD_KEYS[kind]
        rec = {key: id_value, "_update": True, "updated_at": utc()}
        rec.update(fields)
        return self._append(kind, rec)

    def next_id(self, kind, prefix, key=None):
        key = key or _FOLD_KEYS.get(kind)
        n = len({r.get(key) for r in self._read_all(kind) if r.get(key)}) + 1
        return "%s-%03d" % (prefix, n)

    # -- manifest (immutable, write-once) -------------------------------------
    def write_manifest(self, manifest):
        if self.manifest_path.exists():
            raise RuntimeError("manifest is immutable and already exists")
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        tmp.replace(self.manifest_path)
        return manifest

    def read_manifest(self):
        if self.manifest_path.exists():
            return json.loads(self.manifest_path.read_text())
        return {}

    # -- control-state (mutable single doc: phase, budgets consumed, cursors) --
    def control_state(self):
        p = self.root / "control_state.json"
        if p.exists():
            return json.loads(p.read_text())
        return {"phase": "created", "planning_cycles": 0}

    def set_control_state(self, **fields):
        st = self.control_state()
        st.update(fields)
        p = self.root / "control_state.json"
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(st, indent=2, sort_keys=True))
        tmp.replace(p)
        return st

    # -- evidence (content-addressed) -----------------------------------------
    def put_evidence(self, data, label="evidence", ext="json"):
        if isinstance(data, (dict, list)):
            blob = json.dumps(data, indent=2, default=str).encode()
        elif isinstance(data, str):
            blob = data.encode()
        else:
            blob = bytes(data)
        digest = hashlib.sha256(blob).hexdigest()
        name = "%s-%s.%s" % (label, digest[:16], ext)
        path = self.evidence_dir / name
        if not path.exists():
            path.write_bytes(blob)
        return {"ref": name, "sha256": digest, "bytes": len(blob)}

    def read_evidence(self, ref):
        p = self.evidence_dir / ref
        return p.read_bytes() if p.exists() else None

    # -- checkpoint / recovery ------------------------------------------------
    def state_hash(self):
        """Stable hash over the authoritative decision ledgers (excludes volatile
        model_calls/tool_calls/events churn) for pause/resume verification."""
        h = hashlib.sha256()
        for kind in ("theses", "tasks", "cells", "hypotheses_v2", "leases",
                     "handoffs", "candidates", "verifications", "recoveries"):
            p = self._files[kind]
            if p.exists():
                h.update(p.read_bytes())
        return h.hexdigest()

    def counts(self):
        return {k: len(self._read_all(k)) for k in self._files}

    def checkpoint(self, phase, extra=None):
        snap = {
            "campaign_id": self.campaign_id,
            "ts": utc(),
            "phase": phase,
            "state_hash": self.state_hash(),
            "counts": self.counts(),
            "control_state": self.control_state(),
        }
        if extra:
            snap["extra"] = extra
        path = self.checkpoints_dir / ("ckpt-%s.json" % time.strftime("%Y%m%dT%H%M%S", time.gmtime()))
        path.write_text(json.dumps(snap, indent=2, sort_keys=True))
        self.event("checkpoint", phase=phase, state_hash=snap["state_hash"])
        return snap

    def latest_checkpoint(self):
        cks = sorted(self.checkpoints_dir.glob("ckpt-*.json"))
        return json.loads(cks[-1].read_text()) if cks else None

    # -- control files --------------------------------------------------------
    @property
    def stop_file(self):
        return self.root / "STOP"

    @property
    def pause_file(self):
        return self.root / "PAUSE"

    def stop_requested(self):
        return self.stop_file.exists()

    def pause_requested(self):
        return self.pause_file.exists()
