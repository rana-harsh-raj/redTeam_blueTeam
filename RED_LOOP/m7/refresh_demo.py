#!/usr/bin/env python3
"""M7 incremental-refresh proof: one controlled change that affects ONLY the cross-domain Source-to-Pay
journeys, detected by the daily snapshot machinery and rerun without touching the rest of the suite.

  python3 RED_LOOP/m7/refresh_demo.py [--execute] [--change s2p|ingress]

Steps
 1. baseline snapshot (scripts/snapshot/capture.py)
 2. apply a REAL, reversible change:
      s2p     -> append a dated revision note to DOMAIN_REPLICAS/source_to_pay/integration/payouts-boundary-contract.yaml
                 (a Source-to-Pay boundary-contract input captured by snapshot_contracts.s2p)
      ingress -> append a dated note to ENV2_COMPOSE/substitutes/api-ingress/CONTRACT.md (captured by snapshot_contracts.ingress
                 AND substitutes; maps to everything implemented by sub:api-ingress -- deliberately the large set)
 3. capture again -> diff (exit 3) -> affected (rebuild / rerun_journeys)
 4. with --execute: rerun exactly `rerun_journeys` via RED_LOOP/m6/journeys/run.py --only ... into a SEPARATE run
    directory, and verify that file contains only those ids; the canonical reports/implementation/m6-journeys.json
    is restored afterwards so canonical evidence is preserved
 5. revert the change; write reports/implementation/m7-refresh-demo.json
"""
import argparse, json, os, shutil, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SNAP = REPO / "scripts" / "snapshot"
IMPL = REPO / "reports" / "implementation"
CHANGES = {"s2p": REPO / "DOMAIN_REPLICAS/source_to_pay/integration/payouts-boundary-contract.yaml",
           "ingress": REPO / "ENV2_COMPOSE/substitutes/api-ingress/CONTRACT.md"}
SCRATCH = Path(os.environ.get("M7_SCRATCH", "/tmp")) / "m7-refresh-demo"


def sh(cmd, check=True, timeout=7200):
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode not in (0, 3):
        raise RuntimeError("%s rc=%d\n%s" % (" ".join(cmd), p.returncode, p.stderr[-2000:]))
    return p


def latest_snapshot():
    return json.loads((REPO / "reports/domain/snapshots/latest.json").read_text())


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--execute", action="store_true"); ap.add_argument("--change", choices=list(CHANGES), default="s2p")
    a = ap.parse_args()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    target = CHANGES[a.change]
    rep = {"schema_version": 1, "started_at": datetime.now(timezone.utc).isoformat(), "change_kind": a.change, "steps": []}
    sh([sys.executable, str(SNAP / "capture.py")])
    base = latest_snapshot().get("path") or latest_snapshot().get("snapshot")
    rep["baseline_snapshot"] = base
    original = target.read_bytes()
    canonical = (IMPL / "m6-journeys.json").read_bytes() if (IMPL / "m6-journeys.json").exists() else None
    canonical_cov = (IMPL / "m6-journey-coverage.md").read_bytes() if (IMPL / "m6-journey-coverage.md").exists() else None
    marker = ("\n# contract-revision demo %s: gate `merchant-scoped-tagback` added to PATCH payouts_internal/{id}/tax-payment-id (M7 refresh demo, reverted)\n"
              % datetime.now(timezone.utc).isoformat()) if a.change == "s2p" else \
             ("\n\n<!-- contract-revision demo %s : route inventory revision (M7 refresh demo, reverted) -->\n" % datetime.now(timezone.utc).isoformat())
    try:
        target.write_bytes(original + marker.encode())
        rep["change"] = {"file": str(target.relative_to(REPO)), "kind": "boundary-contract revision" if a.change == "s2p" else "ingress contract revision"}
        sh([sys.executable, str(SNAP / "capture.py")])
        new = latest_snapshot().get("path") or latest_snapshot().get("snapshot")
        rep["new_snapshot"] = new
        diff_path = SCRATCH / "diff.json"
        p = sh([sys.executable, str(SNAP / "diff.py"), str(base), str(new), "--json", str(diff_path)])
        rep["diff_rc"] = p.returncode; rep["change_detected"] = p.returncode == 3; rep["diff_summary"] = p.stdout[-1500:]
        diff = json.loads(diff_path.read_text()) if diff_path.exists() else {}
        rep["diff_contracts"] = (diff.get("contracts") or {})
        aff_path = SCRATCH / "affected.json"
        p = sh([sys.executable, str(SNAP / "affected.py"), str(diff_path), "--json", str(aff_path)], check=False)
        aff = json.loads(aff_path.read_text()) if aff_path.exists() else {}
        rep["affected"] = {k: aff.get(k) for k in ("rebuild", "rerun_journeys", "regen_config", "restart", "reasons", "fallback_used") if k in aff}
        ids = sorted(aff.get("rerun_journeys") or [])
        rep["rerun_journeys"] = ids
        rep["affected_services"] = aff.get("rebuild") or []
        rep["changed"] = [rep["change"]["file"]]
        all_journeys = []
        try:
            all_journeys = [j["id"] for j in json.loads(canonical.decode())["journeys"]] if canonical else []
        except Exception:
            pass
        rep["suite_size"] = len(all_journeys)
        rep["unaffected_not_rerun"] = sorted(set(all_journeys) - set(ids))
        rep["affected_is_small_subset"] = bool(ids) and len(ids) < max(1, len(all_journeys)) // 2
        if a.execute and ids:
            run_dir = REPO / "RED_LOOP" / "runs" / ("m7-refresh-demo-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
            cmd = [sys.executable, "RED_LOOP/m6/journeys/run.py", "--only", ",".join(ids), "--run-dir", str(run_dir)]
            p = sh(cmd, check=False, timeout=7200)
            rep["rerun_rc"] = p.returncode; rep["rerun_stdout_tail"] = p.stdout[-2500:]
            try:
                rerun = json.loads((run_dir / "summary.json").read_text())
                got = sorted(j["id"] for j in rerun.get("journeys", []))
                rep["rerun_ids"] = got; rep["rerun_only_affected"] = got == ids
                rep["rerun_results"] = {j["id"]: j["result"] for j in rerun.get("journeys", [])}
                rep["rerun_run_dir"] = str(run_dir.relative_to(REPO))
            except Exception as e:
                rep["rerun_only_affected"] = False; rep["rerun_error"] = str(e)
        elif not a.execute:
            rep["rerun_only_affected"] = None
        else:
            rep["rerun_only_affected"] = False
    finally:
        target.write_bytes(original)
        rep["reverted"] = target.read_bytes() == original
        if canonical is not None:
            (IMPL / "m6-journeys.json").write_bytes(canonical)
        if canonical_cov is not None:
            (IMPL / "m6-journey-coverage.md").write_bytes(canonical_cov)
        rep["canonical_evidence_preserved"] = canonical is None or (IMPL / "m6-journeys.json").read_bytes() == canonical
        git = sh(["git", "status", "--porcelain", "--", str(target.relative_to(REPO))], check=False)
        rep["git_clean_after_revert"] = git.stdout.strip() == ""   # the changed file is back to its committed content
    rep["finished_at"] = datetime.now(timezone.utc).isoformat()
    (IMPL / "m7-refresh-demo.json").write_text(json.dumps(rep, indent=2))
    print(json.dumps({k: rep.get(k) for k in ("change_detected", "affected_services", "rerun_journeys", "suite_size", "affected_is_small_subset",
                                                "rerun_only_affected", "rerun_results", "reverted", "canonical_evidence_preserved", "git_clean_after_revert")}, indent=1))
    return 0 if rep.get("change_detected") and rep.get("reverted") and (rep.get("rerun_only_affected") in (True, None)) else 1


if __name__ == "__main__":
    sys.exit(main())
