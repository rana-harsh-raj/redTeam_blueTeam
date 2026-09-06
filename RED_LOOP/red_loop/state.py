"""Durable, append-only campaign state (Section 11).

The agent's chat context is a working cache; THIS is the authoritative record.
Everything is JSONL flushed on every append so the campaign survives a
model-call failure or process restart, and can be resumed by replaying the
files. Evidence blobs are content-addressed under evidence/ and referenced by
hash so records stay small and tamper-evident.
"""
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from . import config


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class CampaignStore:
    def __init__(self, campaign_id=None, root=None):
        self.campaign_id = campaign_id or ("camp-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
        self.root = Path(root) if root else (config.RUNS_DIR / self.campaign_id)
        self.evidence_dir = self.root / "evidence"
        self.root.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(exist_ok=True)
        self._files = {
            "hypotheses": self.root / "hypotheses.jsonl",
            "actions": self.root / "actions.jsonl",
            "observations": self.root / "observations.jsonl",
            "candidates": self.root / "candidates.jsonl",
            "model_calls": self.root / "model_calls.jsonl",
            "events": self.root / "events.jsonl",
        }
        self.manifest_path = self.root / "manifest.json"

    # -- manifest --------------------------------------------------------------
    def write_manifest(self, manifest):
        manifest = dict(manifest)
        manifest.setdefault("campaign_id", self.campaign_id)
        manifest.setdefault("created_at", _utc())
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        tmp.replace(self.manifest_path)
        return manifest

    def read_manifest(self):
        if self.manifest_path.exists():
            return json.loads(self.manifest_path.read_text())
        return {}

    def update_manifest(self, **fields):
        m = self.read_manifest()
        m.update(fields)
        return self.write_manifest(m)

    # -- generic append --------------------------------------------------------
    def _append(self, kind, record):
        record = dict(record)
        record.setdefault("ts", _utc())
        path = self._files[kind]
        with open(path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return record

    def _read_all(self, kind):
        path = self._files[kind]
        if not path.exists():
            return []
        out = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
        return out

    # -- typed records ---------------------------------------------------------
    def add_hypothesis(self, claim, assets=None, preconditions=None, weakness=None,
                       expected_impact=None, experiment=None, status="proposed"):
        hid = "H-%03d" % (len(self._read_all("hypotheses")) + 1)
        return self._append("hypotheses", {
            "hypothesis_id": hid, "claim": claim, "assets": assets or [],
            "preconditions": preconditions or {}, "suspected_weakness": weakness,
            "expected_impact": expected_impact, "planned_experiment": experiment,
            "status": status, "evidence_refs": []})

    def update_hypothesis(self, hid, **fields):
        fields["hypothesis_id"] = hid
        fields["_update"] = True
        return self._append("hypotheses", fields)

    def add_action(self, record):
        return self._append("actions", record)

    def add_observation(self, raw_evidence_ref=None, interpretation=None,
                        deterministic_facts=None, confidence_change=None,
                        unlocked_capability=None, hypothesis_id=None):
        oid = "O-%04d" % (len(self._read_all("observations")) + 1)
        return self._append("observations", {
            "observation_id": oid, "hypothesis_id": hypothesis_id,
            "raw_evidence_ref": raw_evidence_ref, "interpretation": interpretation,
            "deterministic_facts": deterministic_facts or {},
            "confidence_change": confidence_change,
            "unlocked_capability": unlocked_capability})

    def add_candidate(self, record):
        cid = record.get("candidate_id") or ("C-%03d" % (len(self._read_all("candidates")) + 1))
        record = dict(record)
        record["candidate_id"] = cid
        return self._append("candidates", record)

    def add_model_call(self, record):
        return self._append("model_calls", record)

    def event(self, kind, **fields):
        fields["event"] = kind
        return self._append("events", fields)

    # -- evidence store (content-addressed) ------------------------------------
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

    # -- resume ----------------------------------------------------------------
    def counts(self):
        return {k: len(self._read_all(k)) for k in self._files}

    def latest_hypotheses(self):
        """Fold _update records over base hypotheses to current state."""
        merged = {}
        for rec in self._read_all("hypotheses"):
            hid = rec.get("hypothesis_id")
            if hid not in merged:
                merged[hid] = {}
            merged[hid].update({k: v for k, v in rec.items() if k != "_update"})
        return merged


def new_campaign_id():
    return "camp-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
