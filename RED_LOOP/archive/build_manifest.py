#!/usr/bin/env python3
"""Workstream A — top-level evidence archive manifest (Milestone 3.1).

Binds the material Milestone-2 and Milestone-3 artifacts under one hash-bound
index. Campaign run directories (`RED_LOOP/runs/`, `reports/implementation/runs/`)
are git-ignored external evidence by project policy; this manifest is the single
top-level object that hash-binds them together so no historical claim depends on a
mutable running database or an un-indexed directory.

Principles:
- Records SHA-256 + byte size for every material artifact; never file contents,
  so no secret is transcribed into the manifest.
- Marks each file committed (git-tracked) vs external (git-ignored, retained on
  host only).
- Flags redaction status: run logs are synthetic-only but may embed synthetic
  merchant keys, so they are classified external_only_unredacted and are never
  committed.
- Detects missing expected per-campaign artifacts.
- Emits a self-hash (sha256 over the manifest with the self-hash field blanked)
  and a companion `.sha256` checksum file.

Usage: python3 RED_LOOP/archive/build_manifest.py [--out <path>]
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "RED_LOOP" / "runs"
IMPL = REPO / "reports" / "implementation"
IMPL_RUNS = IMPL / "runs"

# Per-campaign artifacts we expect a complete campaign directory to carry.
EXPECTED_CAMPAIGN_FILES = ["manifest.json", "model_calls.jsonl", "events.jsonl"]

# Committed report artifacts that are part of the M2/M3/M3.1 evidence chain.
COMMITTED_ARTIFACTS = [
    "reports/implementation/m2-surface-acceptance.json",
    "reports/implementation/m3-acceptance.json",
    "reports/implementation/m3-gateway-acceptance.json",
    "reports/implementation/m3-namespace-acceptance.json",
    "reports/implementation/m3-recovery-test.json",
    "reports/implementation/m3-calibration.json",
    "reports/implementation/m3-verifier.json",
    "reports/implementation/RED_LOOP_MILESTONE3_COMPLETION.md",
    "reports/implementation/local-acceptance.json",
    "RED_LOOP/registry/merchant-gateway-routes.json",
    "RED_LOOP/registry/m3-fidelity.md",
    "ENV2_COMPOSE/substitutes/kong-lite/route_policy.py",
]


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(args):
    try:
        return subprocess.run(["git", "-C", str(REPO)] + args, capture_output=True,
                              text=True, timeout=20).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def tracked_set():
    out = git(["ls-files"])
    return set(out.splitlines()) if out else set()


def classify(rel):
    n = rel.lower()
    if n.endswith("model_calls.jsonl"):
        return "model_call_log"
    if n.endswith("responses.jsonl"):
        return "attacker_response_capture"
    if n.endswith("actions.jsonl"):
        return "tool_call_log"
    if n.endswith("hypotheses.jsonl"):
        return "hypothesis_ledger"
    if n.endswith("context_metrics.jsonl"):
        return "context_metric_log"
    if n.endswith("events.jsonl"):
        return "event_log"
    if n.endswith("manifest.json"):
        return "campaign_manifest"
    if n.endswith("evidence-index.json"):
        return "evidence_index"
    if n.endswith("final-ledger.json"):
        return "judge_ledger"
    if "calib" in n:
        return "calibration_bundle"
    if n.endswith(".md"):
        return "report"
    if n.endswith(".json"):
        return "acceptance_or_report_json"
    if n.endswith(".py"):
        return "source"
    return "other"


def redaction_status(rel, tracked):
    # Run logs may embed synthetic merchant keys; they are never committed.
    if rel.startswith("RED_LOOP/runs/") or rel.startswith("reports/implementation/runs/"):
        return "external_only_unredacted_synthetic"
    if rel in tracked:
        return "committed_no_secrets"
    return "external_unclassified"


def index_dir(root, tracked, owner_prefix):
    entries = []
    if not root.exists():
        return entries
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.name.endswith(".pyc") or "__pycache__" in p.parts:
            continue
        rel = str(p.relative_to(REPO))
        # owner = first path segment under the runs root (campaign / calib id)
        try:
            owner = p.relative_to(root).parts[0]
        except Exception:  # noqa: BLE001
            owner = owner_prefix
        entries.append({
            "path": rel,
            "sha256": sha256_file(p),
            "bytes": p.stat().st_size,
            "type": classify(rel),
            "owner": owner,
            "committed": rel in tracked,
            "redaction": redaction_status(rel, tracked),
        })
    return entries


def build():
    tracked = tracked_set()

    campaign_ids = sorted([d.name for d in RUNS.iterdir() if d.is_dir()]) if RUNS.exists() else []

    artifacts = []
    artifacts += index_dir(RUNS, tracked, "red_loop_runs")
    artifacts += index_dir(IMPL_RUNS, tracked, "impl_runs")

    committed = []
    missing_committed = []
    for rel in COMMITTED_ARTIFACTS:
        p = REPO / rel
        if p.exists():
            committed.append({
                "path": rel, "sha256": sha256_file(p), "bytes": p.stat().st_size,
                "type": classify(rel), "owner": "committed_evidence",
                "committed": rel in tracked, "redaction": redaction_status(rel, tracked),
            })
        else:
            missing_committed.append(rel)
    artifacts += committed

    # Missing expected per-campaign files.
    missing_campaign = []
    for cid in campaign_ids:
        for want in EXPECTED_CAMPAIGN_FILES:
            if not (RUNS / cid / want).exists():
                missing_campaign.append(f"{cid}/{want}")

    manifest = {
        "schema_version": "1.0",
        "manifest_kind": "redgrid-evidence-archive",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git": {
            "branch": git(["rev-parse", "--abbrev-ref", "HEAD"]),
            "commit": git(["rev-parse", "HEAD"]),
            "twin_v1_tag_commit": git(["rev-parse", "twin-v1.0^{commit}"]),
            "runtime_freeze_commit": "219ca48bbb5e51db9347b335ac0a50241656045b",
            "m2_head": git(["rev-parse", "milestone-2-red-loop"]),
            "m3_head": git(["rev-parse", "milestone-3-gateway-hardening"]),
            "worktree_clean": git(["status", "--porcelain"]) == "",
        },
        "campaign_ids": campaign_ids,
        "counts": {
            "artifacts": len(artifacts),
            "external_run_files": sum(1 for a in artifacts if not a["committed"]),
            "committed_files": sum(1 for a in artifacts if a["committed"]),
            "total_bytes": sum(a["bytes"] for a in artifacts),
        },
        "missing_expected_committed": missing_committed,
        "missing_expected_campaign_files": missing_campaign,
        "policy": {
            "runs_are_external": True,
            "external_roots": ["RED_LOOP/runs/", "reports/implementation/runs/"],
            "note": ("Run directories are git-ignored per RED_LOOP/.gitignore and "
                     "ENV2_COMPOSE runs/ policy; this manifest hash-binds them. "
                     "No secrets, DB volumes, gateway keys or provider tokens are "
                     "included: only SHA-256 + size are recorded, never contents."),
        },
        "artifacts": artifacts,
        "self_sha256": "",
    }

    # Self-hash: canonical JSON with self_sha256 blanked.
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=str)
    manifest["self_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "RED_LOOP" / "archive" / "evidence-manifest.json"))
    args = ap.parse_args()
    m = build()
    out = Path(args.out)
    out.write_text(json.dumps(m, indent=2, default=str))
    # Companion checksum over the written file (bind the file itself).
    file_hash = sha256_file(out)
    Path(str(out) + ".sha256").write_text(f"{file_hash}  {out.name}\n")
    print(json.dumps({
        "out": str(out),
        "artifacts": m["counts"]["artifacts"],
        "committed_files": m["counts"]["committed_files"],
        "external_run_files": m["counts"]["external_run_files"],
        "total_mib": round(m["counts"]["total_bytes"] / (1 << 20), 1),
        "campaigns": len(m["campaign_ids"]),
        "missing_committed": m["missing_expected_committed"],
        "missing_campaign_files": m["missing_expected_campaign_files"],
        "self_sha256": m["self_sha256"],
        "file_sha256": file_hash,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
