#!/usr/bin/env python3
"""gitleaks over the M7 authored sources and reports (no generated seeds, no secrets/, no runtime volumes).
Writes reports/implementation/m7-secret-scan.json. Exit 1 on findings."""
import json, shutil, subprocess, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TARGETS = ["ENV2_COMPOSE/substitutes/api-ingress", "ENV2_COMPOSE/docker-compose.s2p.yml", "RED_LOOP/m7", "RED_LOOP/m6/journeys/j_ingress.py",
           "RED_LOOP/m6/journeys/j_s2p.py", "scripts/m7", "reports/implementation/M7_FINAL_REPORT.md", "reports/implementation/M7_RUNBOOK.md",
           "reports/implementation/M7_FIDELITY_MATRIX.md", "reports/implementation/M7_OWNERSHIP_INVESTIGATION.md",
           "reports/implementation/M7_INTEGRATION_TOPOLOGY.md", "reports/implementation/M7_PRODUCTION_UNKNOWNS.md",
           "reports/source-to-pay-replica/ACCEPTANCE_CLOSURE.md", "reports/implementation/m7-clean-boot.json", "reports/implementation/m7-refresh-demo.json",
           "reports/implementation/m7-reset-proof.json", "reports/implementation/m7-s2p-connected-runs.json"]


def main():
    stage = Path(tempfile.mkdtemp(prefix="m7-scan-"))
    scanned = []
    for t in TARGETS:
        src = REPO / t
        if not src.exists():
            continue
        dst = stage / t
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.sqlite"))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src, dst)
        scanned.append(t)
    report = stage / "findings.json"
    p = subprocess.run(["gitleaks", "dir", str(stage), "--redact", "--no-banner", "--report-format", "json", "--report-path", str(report), "--max-target-megabytes", "50"],
                       capture_output=True, text=True)
    findings = json.loads(report.read_text()) if report.exists() and report.read_text().strip() else []
    ver = subprocess.run(["gitleaks", "version"], capture_output=True, text=True).stdout.strip()
    doc = {"tool": "gitleaks " + ver, "generated_at": datetime.now(timezone.utc).isoformat(), "exit_code": p.returncode, "findings": len(findings),
           "scanned": scanned, "finding_files": sorted({f.get("File", "").replace(str(stage) + "/", "") for f in findings})[:20],
           "excluded": ["ENV2_COMPOSE/secrets/* (generated per boot, destroyed by down.sh)", "seeds/generated/*", "runtime volumes", "RED_LOOP/runs/* (git-ignored evidence; merchant descriptors carry synthetic keys, redacted in evidence bundles)"]}
    (REPO / "reports/implementation/m7-secret-scan.json").write_text(json.dumps(doc, indent=2))
    shutil.rmtree(stage, ignore_errors=True)
    print(json.dumps({k: doc[k] for k in ("exit_code", "findings", "finding_files")}))
    return 0 if p.returncode == 0 and not findings else 1


if __name__ == "__main__":
    sys.exit(main())
