#!/usr/bin/env python3
"""M6 metric 8 demonstration: a contract change produces an incremental snapshot and reruns
ONLY the affected paths.

  python3 RED_LOOP/m6/refresh_demo.py [--execute]

Steps:
 1. baseline snapshot (scripts/snapshot/capture.py)
 2. apply a REAL, reversible contract change: append a dated "contract-revision" note to
    ENV2_COMPOSE/substitutes/batch-sim/CONTRACT.md (a substitute contract hash input)
 3. capture again → diff (must exit 3) → affected (rebuild / rerun_journeys)
 4. with --execute: rerun exactly `rerun_journeys` via RED_LOOP/m6/journeys/run.py --only ...
    and verify the journeys file contains only those ids
 5. revert the change; write reports/implementation/m6-refresh-demo.json
"""
import argparse, json, os, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SNAP = REPO / "scripts" / "snapshot"
CONTRACT = REPO / "ENV2_COMPOSE" / "substitutes" / "batch-sim" / "CONTRACT.md"
IMPL = REPO / "reports" / "implementation"
SCRATCH = Path(os.environ.get("M6_SCRATCH", "/tmp")) / "m6-refresh-demo"


def sh(cmd, check=True, timeout=7200):
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode not in (0, 3):
        raise RuntimeError(f"{' '.join(cmd)} rc={p.returncode}\n{p.stderr[-2000:]}")
    return p


def latest_snapshot():
    return json.loads((REPO / "reports" / "domain" / "snapshots" / "latest.json").read_text())


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--execute", action="store_true"); a = ap.parse_args()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    rep = {"started_at": datetime.now(timezone.utc).isoformat(), "steps": []}
    sh([sys.executable, str(SNAP / "capture.py")])
    base = latest_snapshot().get("path") or latest_snapshot().get("snapshot")
    rep["baseline_snapshot"] = base
    original = CONTRACT.read_text()
    marker = f"\n\n<!-- contract-revision demo {datetime.now(timezone.utc).isoformat()} : field `notes` added to batch entry (M6 refresh demo, reverted) -->\n"
    try:
        CONTRACT.write_text(original + marker)
        rep["change"] = {"file": str(CONTRACT.relative_to(REPO)), "kind": "substitute contract revision"}
        sh([sys.executable, str(SNAP / "capture.py")])
        new = latest_snapshot().get("path") or latest_snapshot().get("snapshot")
        rep["new_snapshot"] = new
        diff_path = SCRATCH / "diff.json"
        p = sh([sys.executable, str(SNAP / "diff.py"), str(base), str(new), "--json", str(diff_path)])
        rep["diff_rc"] = p.returncode
        rep["change_detected"] = p.returncode == 3
        rep["diff_summary"] = p.stdout[-1500:]
        aff_path = SCRATCH / "affected.json"
        p = sh([sys.executable, str(SNAP / "affected.py"), str(diff_path), "--json", str(aff_path)], check=False)
        aff = json.loads(aff_path.read_text()) if aff_path.exists() else {}
        if not aff:
            try:
                aff = json.loads(p.stdout)
            except Exception:
                aff = {"raw": p.stdout[-2000:]}
        rep["affected"] = aff
        rep["affected_services"] = aff.get("rebuild") or aff.get("affected_services") or []
        ids = aff.get("rerun_journeys") or []
        rep["rerun_journeys"] = ids
        rep["changed"] = [rep["change"]["file"]]
        if a.execute and ids:
            # run.py always writes the canonical reports; preserve the full-suite evidence around the subset run
            canon = {n: (IMPL / n).read_bytes() for n in ("m6-journeys.json", "m6-journey-coverage.md") if (IMPL / n).exists()}
            p = sh([sys.executable, str(REPO / "RED_LOOP" / "m6" / "journeys" / "run.py"), "--only", ",".join(ids),
                    "--run-dir", str(SCRATCH / "rerun")], check=False)
            rep["rerun_rc"] = p.returncode
            try:
                jr = json.loads((IMPL / "m6-journeys.json").read_text())
                (IMPL / "m6-refresh-demo-journeys.json").write_text(json.dumps(jr, indent=2))
                for n, b in canon.items():
                    (IMPL / n).write_bytes(b)
                ran = sorted(j["id"] for j in jr.get("journeys", []))
                rep["rerun_ids"] = ran
                rep["rerun_only_affected"] = set(ran) <= set(ids) and bool(ran)
                rep["rerun_results"] = {r: sum(1 for j in jr["journeys"] if j.get("result") == r) for r in ("PASS", "FAIL", "EXPECTED_FAILURE", "BLOCKED")}
            except Exception as e:
                rep["rerun_only_affected"] = False; rep["rerun_error"] = str(e)
        elif ids:
            rep["rerun_only_affected"] = False
            rep["rerun_note"] = "not executed (run with --execute)"
        else:
            rep["rerun_only_affected"] = False
            rep["rerun_note"] = "affected.py mapped no journeys"
    finally:
        CONTRACT.write_text(original)
        rep["reverted"] = CONTRACT.read_text() == original
    rep["finished_at"] = datetime.now(timezone.utc).isoformat()
    (IMPL / "m6-refresh-demo.json").write_text(json.dumps(rep, indent=2))
    print(json.dumps({k: rep.get(k) for k in ("change_detected", "affected_services", "rerun_journeys", "rerun_only_affected", "rerun_results", "reverted")}, indent=2))
    return 0 if rep.get("change_detected") else 1


if __name__ == "__main__":
    sys.exit(main())
