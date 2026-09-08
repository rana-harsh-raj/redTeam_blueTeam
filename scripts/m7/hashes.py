#!/usr/bin/env python3
"""Bind every M7 runtime, graph and report artifact into reports/implementation/M7_ARTIFACT_HASHES.json.

  python3 scripts/m7/hashes.py            # (re)build the manifest
  python3 scripts/m7/hashes.py --verify   # read-only: exit 1 on any changed / missing file

The manifest excludes itself. Files are listed explicitly (no globs into git-ignored run directories); the
journey evidence bundles of the canonical run are bound through the run directory recorded in
reports/implementation/m6-journeys.json (RED_LOOP/runs/<run>/evidence/*.json), which is git-ignored by design
and therefore hash-bound here rather than tracked.
"""
import hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "reports/implementation/M7_ARTIFACT_HASHES.json"
TRACKED = [
    "reports/implementation/M7_FINAL_REPORT.md", "reports/implementation/M7_ACCEPTANCE.json", "reports/implementation/M7_RUNBOOK.md",
    "reports/implementation/M7_FIDELITY_MATRIX.md", "reports/implementation/M7_OWNERSHIP_INVESTIGATION.md",
    "reports/implementation/M7_INTEGRATION_TOPOLOGY.md", "reports/implementation/M7_PRODUCTION_UNKNOWNS.md",
    "reports/implementation/m7-clean-boot.json", "reports/implementation/m6-clean-boot.json", "reports/implementation/m6-journeys.json",
    "reports/implementation/m6-journey-coverage.md", "reports/implementation/m6-acceptance.json", "reports/implementation/m7-refresh-demo.json",
    "reports/implementation/m7-weekly-rebuild.json", "reports/implementation/m7-s2p-post-integration-acceptance.json",
    "reports/architecture/M7_CANONICAL_SNAPSHOT.json", "reports/architecture/M7_CANONICAL_SNAPSHOT_STATS.json",
    "reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.json", "reports/domain/GRAPH_STATS.json", "reports/domain/FIDELITY_CLASSIFICATION.csv",
    "reports/domain/parts/m7-ingress.json", "reports/domain/parts/zz-s2p-namespace.json", "reports/domain/parts/m6-journeys.json",
    "ENV2_COMPOSE/substitutes/api-ingress/contract/routes.json", "ENV2_COMPOSE/substitutes/api-ingress/CONTRACT.md",
    "ENV2_COMPOSE/substitutes/api-ingress/server.py", "ENV2_COMPOSE/docker-compose.s2p.yml",
    "reports/source-to-pay-replica/ACCEPTANCE_CLOSURE.md", "reports/source-to-pay-replica/closure-m7/artifacts/acceptance.json",
    "reports/source-to-pay-replica/historical-b6e4976/artifacts/acceptance-18of20.json",
    "reports/implementation/m7/starting-audit/M7_ACCEPTANCE.json", "reports/implementation/m7/starting-audit/M7_FINAL_REPORT.md",
]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def evidence_bundles():
    try:
        jr = json.loads((REPO / "reports/implementation/m6-journeys.json").read_text())
        run = REPO / jr["run_dir"]
        return sorted(p for p in (run / "evidence").glob("*.json")) + [run / "summary.json"]
    except Exception:
        return []


def build():
    files = {}
    missing = []
    for rel in TRACKED:
        p = REPO / rel
        if p.is_file():
            files[rel] = sha(p)
        else:
            missing.append(rel)
    for p in evidence_bundles():
        if p.is_file():
            files[str(p.relative_to(REPO))] = sha(p)
    doc = {"algorithm": "sha256", "generated_at": datetime.now(timezone.utc).isoformat(), "self_excluded": str(OUT.relative_to(REPO)),
           "files": dict(sorted(files.items())), "missing_at_build": missing, "count": len(files)}
    OUT.write_text(json.dumps(doc, indent=2) + "\n")
    return doc


def verify():
    doc = json.loads(OUT.read_text())
    changed = [rel for rel, h in doc["files"].items() if not (REPO / rel).is_file() or sha(REPO / rel) != h]
    return {"passed": not changed, "changed_or_missing": changed, "count": len(doc["files"])}


if __name__ == "__main__":
    if "--verify" in sys.argv:
        r = verify(); print(json.dumps(r)); sys.exit(0 if r["passed"] else 1)
    d = build(); print(json.dumps({"count": d["count"], "missing_at_build": d["missing_at_build"], "out": str(OUT.relative_to(REPO))}))
