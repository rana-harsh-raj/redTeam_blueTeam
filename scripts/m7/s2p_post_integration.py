#!/usr/bin/env python3
"""Source-to-Pay standalone acceptance FROM THE INTEGRATED BRANCH (M7 gate 5).

  python3 scripts/m7/s2p_post_integration.py --run [--commit <sha>]

1. creates a fresh worktree of <commit> (default HEAD) under /Users/rana.singh/rzp-m7-s2p-post (+ a second one for
   the fresh-final run), on a throwaway branch `m7-s2p-post-<sha7>`;
2. captures an isolation baseline (scripts/m7/isolation_baseline.py: running containers only, the two executing
   checkouts excluded, every other registered worktree protected) and commits it there -- BEFORE any run;
3. runs DOMAIN_REPLICAS/source_to_pay/scripts/clean-run.py three times (ids m7post-1, m7post-2 in the first
   checkout, m7post-3 in the second) with compose projects s2p_m7post / s2p_m7post_final;
4. scans deliverables, runs acceptance.sh --runs m7post-1 m7post-2 m7post-3;
5. copies acceptance.json (+ isolation-after.json) into reports/implementation/m7-s2p-post-integration-acceptance.json
   and m7-s2p-post-integration-isolation.json, annotated with the evaluated commit.
The arena is never touched. The throwaway branch and worktrees are left for inspection.
"""
import argparse, json, os, shutil, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
IMPL = REPO / "reports/implementation"
W1 = Path("/Users/rana.singh/rzp-m7-s2p-post"); W2 = Path("/Users/rana.singh/rzp-m7-s2p-post-final")


def sh(cmd, cwd=None, env=None, timeout=3600, check=True):
    p = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError("%s rc=%d\n%s\n%s" % (" ".join(map(str, cmd)), p.returncode, p.stdout[-2000:], p.stderr[-2000:]))
    return p


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--run", action="store_true"); ap.add_argument("--commit", default=None)
    a = ap.parse_args()
    if not a.run:
        print(__doc__); return 0
    commit = a.commit or sh(["git", "rev-parse", "HEAD"], cwd=REPO).stdout.strip()
    branch = "m7-s2p-post-" + commit[:7]
    for w in (W1, W2):
        if w.exists():
            sh(["git", "worktree", "remove", "--force", str(w)], cwd=REPO, check=False); shutil.rmtree(w, ignore_errors=True)
    sh(["git", "branch", "-D", branch], cwd=REPO, check=False)
    sh(["git", "worktree", "add", "-b", branch, str(W1), commit], cwd=REPO)
    rec = {"schema_version": 1, "started_at": datetime.now(timezone.utc).isoformat(), "integrated_commit": commit, "branch": branch, "worktrees": [str(W1), str(W2)]}
    # baseline BEFORE any run: every registered worktree except the two executing ones; running containers only
    S2P = W1 / "DOMAIN_REPLICAS/source_to_pay"
    sh([sys.executable, str(REPO / "scripts/m7/isolation_baseline.py"), "--repo", str(REPO), "--exclude", str(W1), "--exclude", str(W2),
        "--out", str(S2P / "artifacts/isolation-before.json")])
    sh(["git", "add", "-A"], cwd=W1)
    sh(["git", "-c", "user.name=rana.singh", "-c", "user.email=rana.singh@razorpay.com", "commit", "-q", "-m",
        "M7 gate 5: Source-to-Pay post-integration isolation baseline (captured before the runs; executing checkouts excluded)"], cwd=W1)
    evaluated = sh(["git", "rev-parse", "HEAD"], cwd=W1).stdout.strip()
    rec["evaluated_commit"] = evaluated
    sh(["git", "worktree", "add", "--detach", str(W2), evaluated], cwd=REPO)
    t0 = time.time()
    for rid, wt, proj in (("m7post-1", W1, "s2p_m7post"), ("m7post-2", W1, "s2p_m7post"), ("m7post-3", W2, "s2p_m7post_final")):
        d = wt / "DOMAIN_REPLICAS/source_to_pay"
        p = sh([sys.executable, str(d / "scripts/clean-run.py"), "--run-id", rid], cwd=d, env={"COMPOSE_PROJECT_NAME": proj}, check=False, timeout=3600)
        rec.setdefault("runs", []).append({"run_id": rid, "rc": p.returncode, "tail": p.stdout[-400:]})
        if p.returncode:
            break
        if rid in ("m7post-2", "m7post-3"):
            sh([str(d / "scripts/reset.sh")], cwd=d, env={"COMPOSE_PROJECT_NAME": proj}, check=False)
    ok = all(r["rc"] == 0 for r in rec.get("runs", [])) and len(rec.get("runs", [])) == 3
    if ok:
        shutil.copytree(W2 / "DOMAIN_REPLICAS/source_to_pay/artifacts/clean-runs/m7post-3", S2P / "artifacts/clean-runs/m7post-3", dirs_exist_ok=True)
        scan = sh([sys.executable, str(S2P / "scripts/scan-deliverables.py")], cwd=W1, check=False)
        rec["scan"] = scan.stdout[-300:]
        acc = sh([str(S2P / "scripts/acceptance.sh"), "--runs", "m7post-1", "m7post-2", "m7post-3"], cwd=W1, check=False, timeout=1800)
        rec["acceptance_rc"] = acc.returncode; rec["acceptance_stdout"] = acc.stdout[-800:]
        result = json.loads((S2P / "artifacts/acceptance.json").read_text())
        result["m7_post_integration"] = {"integrated_commit": commit, "evaluated_commit": evaluated, "worktrees": [str(W1), str(W2)], "compose_projects": ["s2p_m7post", "s2p_m7post_final"]}
        (IMPL / "m7-s2p-post-integration-acceptance.json").write_text(json.dumps(result, indent=2) + "\n")
        shutil.copyfile(S2P / "artifacts/isolation-after.json", IMPL / "m7-s2p-post-integration-isolation.json")
        rec["accepted"] = result.get("accepted"); rec["failed_gates"] = [g["id"] for g in result["gates"] if not g["passed"]]
    rec["seconds"] = round(time.time() - t0, 1); rec["finished_at"] = datetime.now(timezone.utc).isoformat()
    (IMPL / "m7-s2p-post-integration-run.json").write_text(json.dumps(rec, indent=2))
    print(json.dumps({k: rec.get(k) for k in ("integrated_commit", "evaluated_commit", "accepted", "failed_gates", "seconds")}))
    return 0 if rec.get("accepted") else 1


if __name__ == "__main__":
    sys.exit(main())
