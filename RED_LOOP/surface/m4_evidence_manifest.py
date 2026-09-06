#!/usr/bin/env python3
"""Milestone 4 evidence manifest builder + verifier.

Builds ``reports/implementation/m4-direct-e2e-evidence-manifest.json``: a
hash-bound index of every committed M4 evidence artifact (the acceptance JSON,
the direct/BAS/XAS/boundary/assurance report set, the source map / decisions /
limits / contradictions / fidelity matrix) plus the external soak run and this
milestone's acceptance artifact. Mirrors ``RED_LOOP/archive/evidence-manifest.json``
conventions: SHA-256 + byte size + kind per file, a ``self_sha256`` computed over
the manifest with that field blanked, and a companion ``.sha256`` file.

Two modes:
  build   (default) — write the manifest and its companion checksum.
  verify           — recompute every listed hash and report mismatches/missing
                     (backs ``make m4-evidence-verify``; gates G68/G69).

Usage:
  python3 RED_LOOP/surface/m4_evidence_manifest.py [--out PATH]
  python3 RED_LOOP/surface/m4_evidence_manifest.py verify [--manifest PATH]

Stdlib only.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
IMPL = REPO / "reports" / "implementation"
RUNS = REPO / "RED_LOOP" / "runs"
DEFAULT_OUT = IMPL / "m4-direct-e2e-evidence-manifest.json"

# Committed M4 evidence artifacts (repo-relative). Missing ones are flagged, not fatal.
COMMITTED_EVIDENCE = [
    "reports/implementation/m4-direct-e2e-acceptance.json",
    "reports/implementation/m4-baseline-report.md",
    "reports/implementation/m4-direct-provision.json",
    "reports/implementation/m4-bas-ingest.json",
    "reports/implementation/m4-xas-ledger.json",
    "reports/implementation/m4-boundary-results.json",
    "reports/implementation/m4-assurance-run.json",
    "reports/implementation/m4-hypothesis-lifecycle.json",
    "reports/implementation/m4-lifecycle-validation.json",
    "reports/implementation/m4-direct-e2e-coverage.md",
    "reports/implementation/m4-route-coverage.json",
    "reports/implementation/m4-invariant-results.json",
    "reports/implementation/m4-source-map.md",
    "reports/implementation/m4-decisions.md",
    "reports/implementation/m4-known-limits.md",
    "reports/implementation/m4-contradictions.md",
    "reports/implementation/m4-fidelity-matrix.csv",
    "RED_LOOP/m4/gate-catalog.json",
]
# External (git-ignored) run evidence hash-bound here but never committed.
EXTERNAL_GLOBS = [
    "RED_LOOP/runs/*/m4-direct-e2e-soak.json",
]


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def git(a):
    try:
        return subprocess.run(["git", "-C", str(REPO)] + a, capture_output=True,
                              text=True, timeout=20).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def tracked_set():
    out = git(["ls-files"])
    return set(out.splitlines()) if out else set()


def classify(rel):
    n = rel.lower()
    if n.endswith("acceptance.json"):
        return "acceptance_json"
    if n.endswith("evidence-manifest.json"):
        return "evidence_manifest"
    if n.endswith("soak.json"):
        return "soak_run"
    if n.endswith("gate-catalog.json"):
        return "gate_catalog"
    if n.endswith(".csv"):
        return "fidelity_matrix"
    if n.endswith(".md"):
        return "report"
    if n.endswith(".json"):
        return "evidence_json"
    return "other"


def collect(tracked):
    entries, missing = [], []
    seen = set()

    def add(rel, committed_expected):
        p = REPO / rel
        if not p.exists():
            missing.append(rel)
            return
        if rel in seen:
            return
        seen.add(rel)
        entries.append({
            "path": rel,
            "sha256": sha256_file(p),
            "bytes": p.stat().st_size,
            "kind": classify(rel),
            "committed": rel in tracked,
            "committed_expected": committed_expected,
        })

    for rel in COMMITTED_EVIDENCE:
        add(rel, True)
    for pattern in EXTERNAL_GLOBS:
        base, _, tail = pattern.partition("*")
        for p in sorted(REPO.glob(pattern)):
            add(str(p.relative_to(REPO)), False)
    return entries, missing


def build(out_path):
    tracked = tracked_set()
    artifacts, missing = collect(tracked)
    manifest = {
        "schema_version": "1.0",
        "manifest_kind": "m4-direct-e2e-evidence",
        "milestone": "M4 — Direct Current-Account, Reconciliation, and Autonomous Architecture Assurance Expansion",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git": {
            "branch": git(["rev-parse", "--abbrev-ref", "HEAD"]),
            "commit": git(["rev-parse", "HEAD"]),
            "worktree_clean": git(["status", "--porcelain"]) == "",
        },
        "counts": {
            "artifacts": len(artifacts),
            "committed": sum(1 for a in artifacts if a["committed"]),
            "external": sum(1 for a in artifacts if not a["committed"]),
            "total_bytes": sum(a["bytes"] for a in artifacts),
        },
        "missing_expected": missing,
        "policy": {
            "external_roots": ["RED_LOOP/runs/"],
            "note": ("Only SHA-256 + size are recorded, never contents; run directories are git-ignored "
                     "and hash-bound here. No secrets, DB volumes, gateway keys or provider tokens included."),
        },
        "artifacts": artifacts,
        "self_sha256": "",
    }
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=str)
    manifest["self_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    out = Path(out_path)
    out.write_text(json.dumps(manifest, indent=2, default=str))
    file_hash = sha256_file(out)
    Path(str(out) + ".sha256").write_text(f"{file_hash}  {out.name}\n")
    return manifest, file_hash


def verify(manifest_path):
    p = Path(manifest_path)
    if not p.exists():
        return {"ok": False, "reason": "manifest absent", "manifest": str(p)}, 1
    man = json.loads(p.read_text())
    # 1. self_sha256 integrity
    check = dict(man)
    check["self_sha256"] = ""
    canonical = json.dumps(check, sort_keys=True, separators=(",", ":"), default=str)
    self_ok = hashlib.sha256(canonical.encode()).hexdigest() == man.get("self_sha256")
    # 2. companion .sha256 integrity
    comp = Path(str(p) + ".sha256")
    comp_ok = comp.exists() and comp.read_text().split()[0] == sha256_file(p)
    # 3. every listed artifact recomputes
    mism, missing = [], []
    for a in man.get("artifacts", []):
        fp = REPO / a["path"]
        if not fp.exists():
            missing.append(a["path"]); continue
        if sha256_file(fp) != a.get("sha256"):
            mism.append(a["path"])
    ok = self_ok and comp_ok and not mism and not missing
    res = {
        "ok": ok,
        "manifest": str(p),
        "self_sha256_ok": self_ok,
        "companion_sha256_ok": comp_ok,
        "artifacts_checked": len(man.get("artifacts", [])),
        "mismatches": mism,
        "missing": missing,
        "missing_expected_at_build": man.get("missing_expected", []),
    }
    return res, (0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(description="M4 evidence manifest build/verify")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("build", help="write the manifest (default)")
    v = sub.add_parser("verify", help="recompute and report mismatches/missing")
    v.add_argument("--manifest", default=str(DEFAULT_OUT))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    if args.cmd == "verify":
        res, code = verify(args.manifest)
        print(json.dumps(res, indent=2))
        return code
    # build (default)
    man, file_hash = build(args.out)
    print(json.dumps({
        "out": args.out,
        "artifacts": man["counts"]["artifacts"],
        "committed": man["counts"]["committed"],
        "external": man["counts"]["external"],
        "missing_expected": man["missing_expected"],
        "self_sha256": man["self_sha256"],
        "file_sha256": file_hash,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
