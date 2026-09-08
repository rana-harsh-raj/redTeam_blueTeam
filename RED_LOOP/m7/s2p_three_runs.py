#!/usr/bin/env python3
"""Three clean runs of the connected Source-to-Pay journey (M7 gate 13). Before each run the shared ingress is
reset (POST /_ingress/reset) and the S2P stack is torn down and brought up fresh (new MySQL volume, new Kafka),
then RED_LOOP/m6/journeys/run.py --only journey:cross-domain-s2p/success runs into its own run directory.
The canonical reports/implementation/m6-journeys.json is preserved. Writes m7-s2p-connected-runs.json.
"""
import json, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENV2 = REPO / "ENV2_COMPOSE"
IMPL = REPO / "reports/implementation"
CURL = ["docker", "run", "--rm", "--pull", "never", "--network", "rzp-arena", "curlimages/curl:latest", "-s", "-m", "20"]


def main():
    admin = "admin:" + (ENV2 / "secrets/ingress_admin_token.txt").read_text().strip()
    canonical = (IMPL / "m6-journeys.json").read_bytes()
    canonical_cov = (IMPL / "m6-journey-coverage.md").read_bytes() if (IMPL / "m6-journey-coverage.md").exists() else None
    runs = []
    try:
        for i in range(1, 4):
            rec = {"run_id": "s2p-connected-%d" % i, "started_at": datetime.now(timezone.utc).isoformat()}
            r = subprocess.run(CURL + ["-X", "POST", "-u", admin, ENV2.name and "http://api-ingress:8080/_ingress/reset"], capture_output=True, text=True)
            rec["reset_before"] = '"reset": true' in r.stdout
            subprocess.run([sys.executable, "RED_LOOP/m7/s2p_stack.py", "down"], cwd=REPO, capture_output=True, text=True)
            up = subprocess.run([sys.executable, "RED_LOOP/m7/s2p_stack.py", "up"], cwd=REPO, capture_output=True, text=True)
            rec["s2p_fresh_up_rc"] = up.returncode
            run_dir = REPO / "RED_LOOP/runs" / ("m7-s2p-connected-%d-%s" % (i, time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())))
            p = subprocess.run([sys.executable, "RED_LOOP/m6/journeys/run.py", "--only", "journey:cross-domain-s2p/success", "--run-dir", str(run_dir)], cwd=REPO, capture_output=True, text=True, timeout=3600)
            rec["rc"] = p.returncode; rec["stdout_tail"] = p.stdout[-600:]
            try:
                summary = json.loads((run_dir / "summary.json").read_text())
                j = [x for x in summary["journeys"] if x["id"] == "journey:cross-domain-s2p/success"][0]
                rec["success"] = j["result"]; rec["checks"] = "%s/%s" % (j["checks_passed"], j["checks_total"]); rec["merchant_id"] = j.get("merchant_id")
                ev = json.loads((REPO / j["evidence_path"]).read_text())
                rec["remittance_payout_id"] = ((ev.get("ingress_callbacks") or [{}])[0].get("payout_id")) or (ev.get("pay_response") or {}).get("payout_id")
                rec["evidence_path"] = j["evidence_path"]
            except Exception as e:
                rec["success"] = "ERROR"; rec["error"] = str(e)
            rec["run_dir"] = str(run_dir.relative_to(REPO))
            runs.append(rec)
            print(json.dumps({k: rec.get(k) for k in ("run_id", "reset_before", "s2p_fresh_up_rc", "success", "checks", "remittance_payout_id")}), flush=True)
    finally:
        (IMPL / "m6-journeys.json").write_bytes(canonical)
        if canonical_cov is not None:
            (IMPL / "m6-journey-coverage.md").write_bytes(canonical_cov)
    doc = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "runs": runs,
           "all_passed": len(runs) == 3 and all(r.get("success") == "PASS" for r in runs), "canonical_evidence_preserved": (IMPL / "m6-journeys.json").read_bytes() == canonical}
    (IMPL / "m7-s2p-connected-runs.json").write_text(json.dumps(doc, indent=2))
    print(json.dumps({"all_passed": doc["all_passed"], "distinct_remittances": len({r.get("remittance_payout_id") for r in runs})}))
    return 0 if doc["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
