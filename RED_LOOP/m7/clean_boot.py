#!/usr/bin/env python3
"""M7 clean boot: the M6 clean boot (down -> empty volumes/secrets -> up with the shared ingress) plus the
Source-to-Pay stack (compose overlay) before the journey suite, then the FULL suite (M6 families + M7 families:
shared-ingress, cross-domain-s2p). Writes reports/implementation/m7-clean-boot.json alongside the M6 file.

  python3 RED_LOOP/m7/clean_boot.py [--no-journeys] [--journeys-args "..."] [--attach] [--skip-s2p-build]
"""
import argparse, json, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
IMPL = REPO / "reports" / "implementation"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-journeys", action="store_true"); ap.add_argument("--journeys-args", default="")
    ap.add_argument("--attach", action="store_true"); ap.add_argument("--skip-s2p-build", action="store_true")
    ap.add_argument("--reuse-journeys", action="store_true", help="with --attach --no-journeys: summarize the suite already run on this boot")
    a = ap.parse_args()
    t0 = time.time()
    rep = {"schema_version": 1, "started_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
           "git_head": subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()}
    env_file = REPO / "DOMAIN_REPLICAS" / "source_to_pay" / ".build" / "runtime-image.env"
    if not a.skip_s2p_build and not env_file.exists():
        p = subprocess.run([sys.executable, "RED_LOOP/m7/s2p_stack.py", "build"], cwd=REPO, capture_output=True, text=True)
        rep["s2p_build"] = {"rc": p.returncode, "stdout_tail": p.stdout[-800:], "stderr_tail": p.stderr[-800:]}
        if p.returncode:
            print(json.dumps(rep, indent=1)); return 1
    cmd = [sys.executable, "RED_LOOP/m6/clean_boot.py", "--post-up", "python3 RED_LOOP/m7/s2p_stack.py up"]
    if a.attach:
        cmd.append("--attach")
    if a.no_journeys:
        cmd.append("--no-journeys")
    if a.reuse_journeys:
        cmd.append("--reuse-journeys")
    if a.journeys_args:
        cmd += ["--journeys-args", a.journeys_args]
    p = subprocess.run(cmd, cwd=REPO, text=True)
    rep["m6_clean_boot_rc"] = p.returncode
    try:
        m6 = json.loads((IMPL / "m6-clean-boot.json").read_text())
    except Exception:
        m6 = {}
    rep["m6_clean_boot"] = {k: m6.get(k) for k in ("run_dir", "from_empty_state", "up_rc", "container_count", "running", "healthy",
                                                     "unhealthy", "not_running", "fresh_merchants", "journey_results", "suite_passed",
                                                     "no_stale_local_state", "fingerprint", "post_up", "journeys_run_dir")}
    st = subprocess.run([sys.executable, "RED_LOOP/m7/s2p_stack.py", "status"], cwd=REPO, capture_output=True, text=True)
    try:
        rep["s2p_stack"] = json.loads(st.stdout)
    except ValueError:
        rep["s2p_stack"] = {"raw": st.stdout[-500:], "rc": st.returncode}
    ing = subprocess.run(["docker", "run", "--rm", "--pull", "never", "--network", "rzp-arena", "curlimages/curl:latest", "-s", "http://api-ingress:8080/_ingress/health"],
                         capture_output=True, text=True)
    try:
        rep["ingress_health"] = json.loads(ing.stdout)
    except ValueError:
        rep["ingress_health"] = {"raw": ing.stdout[-300:], "rc": ing.returncode}
    try:
        jr = json.loads((IMPL / "m6-journeys.json").read_text())
        js = jr.get("journeys", [])
        fam = {}
        for j in js:
            fam.setdefault(j.get("family"), {}).setdefault(j.get("result"), 0)
            fam[j["family"]][j["result"]] += 1
        rep["journeys"] = {"total": len(js), "by_family": fam,
                           "m7_families": {f: fam.get(f) for f in ("family:shared-ingress", "family:cross-domain-s2p")},
                           "run_dir": jr.get("run_dir"), "fingerprint_boot_id": (jr.get("fingerprint") or {}).get("boot_id")}
    except Exception:
        rep["journeys"] = None
    ok_ingress = (rep.get("ingress_health") or {}).get("status") == "ok" and (rep.get("ingress_health") or {}).get("passport_signer") is True
    ok_s2p = (rep.get("s2p_stack") or {}).get("vp_source_health_rc") == 0
    rep["shared_ingress_booted_from_clean_state"] = bool(m6.get("from_empty_state")) and m6.get("up_rc") == 0 and ok_ingress
    rep["s2p_stack_healthy"] = ok_s2p
    rep["seconds"] = round(time.time() - t0, 1)
    rep["finished_at"] = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (IMPL / "m7-clean-boot.json").write_text(json.dumps(rep, indent=2))
    print(json.dumps({k: rep.get(k) for k in ("m6_clean_boot_rc", "shared_ingress_booted_from_clean_state", "s2p_stack_healthy", "journeys", "seconds")}, indent=1))
    return 0 if rep["shared_ingress_booted_from_clean_state"] and ok_s2p and p.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
