#!/usr/bin/env python3
"""M6 independent validation: clean boot from EMPTY mutable state, then the P0 journey suite.

  python3 RED_LOOP/m6/clean_boot.py [--skip-down] [--journeys-args "..."] [--no-journeys]

Steps (all via the existing arena scripts; nothing bypasses them):
  1. record pre-state: compose volumes, containers, secrets dir, .runtime fingerprint
  2. ENV2_COMPOSE/scripts/down.sh          (removes containers AND volumes AND secrets)
  3. assert empty state: no env2_compose volumes, no secrets/*.txt, no generated/
  4. REGEN_SECRETS=1 ARENA_SKIP_BUILD=1 ENV2_COMPOSE/scripts/up.sh   (fresh secrets, fresh config, migrations, seeds)
  5. health: every container running+healthy (or a documented no-healthcheck service)
  6. RED_LOOP/m6/journeys/run.py           (self-provisions FRESH merchants; writes m6-journeys.json)
  7. write reports/implementation/m6-clean-boot.json
"""
import argparse, json, os, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENV = REPO / "ENV2_COMPOSE"
IMPL = REPO / "reports" / "implementation"
RUNS = REPO / "RED_LOOP" / "runs"


def sh(cmd, cwd=None, env=None, check=False, log=None, timeout=3600):
    e = dict(os.environ); e.update(env or {})
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd or REPO, env=e, capture_output=True, text=True, timeout=timeout)
    rec = {"cmd": cmd if isinstance(cmd, str) else " ".join(cmd), "rc": p.returncode, "secs": round(time.time() - t0, 1),
           "stdout_tail": p.stdout[-4000:], "stderr_tail": p.stderr[-4000:]}
    if log is not None:
        log.append(rec)
    if check and p.returncode != 0:
        raise RuntimeError(f"{rec['cmd']} rc={p.returncode}\n{p.stderr[-2000:]}")
    return p


def volumes(project="env2_compose"):
    p = sh(["docker", "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"])
    return sorted(v for v in p.stdout.split() if v)


def containers(project="env2_compose"):
    p = sh(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
            "--format", "{{.Names}}\t{{.Status}}"])
    out = []
    for line in p.stdout.splitlines():
        name, _, status = line.partition("\t")
        out.append({"name": name, "status": status,
                    "healthy": "(healthy)" in status, "running": status.startswith("Up")})
    return sorted(out, key=lambda c: c["name"])


def secrets_present():
    return sorted(p.name for p in (ENV / "secrets").glob("*.txt"))


def fingerprint():
    p = ENV / ".runtime" / "arena-fingerprint.json"
    try:
        fp = json.loads(p.read_text())
        return {k: fp.get(k) for k in ("boot_id", "compose_project", "route_profile", "config_digest", "git_head", "generated_at")}
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-down", action="store_true", help="arena already down (dangerous: weakens the empty-state proof)")
    ap.add_argument("--no-journeys", action="store_true")
    ap.add_argument("--journeys-args", default="")
    ap.add_argument("--repos-root", default=None)
    a = ap.parse_args()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / f"m6-clean-boot-{ts}"; run_dir.mkdir(parents=True, exist_ok=True)
    log = []
    rep = {"schema_version": 1, "started_at": ts, "run_dir": str(run_dir),
           "git_head": sh(["git", "rev-parse", "HEAD"]).stdout.strip(),
           "pre": {"volumes": volumes(), "containers": len(containers()), "secrets": len(secrets_present()),
                   "fingerprint": fingerprint()}}
    repos_root = a.repos_root or (REPO / ".local" / "repos-root").read_text().strip()

    if not a.skip_down:
        print("== 1/5 down.sh (containers + volumes + secrets)")
        sh(["bash", "scripts/down.sh"], cwd=ENV, log=log, timeout=900)
    empty = {"volumes": volumes(), "containers": [c for c in containers()], "secrets": secrets_present(),
             "generated_exists": (ENV / "generated").exists() and any((ENV / "generated").iterdir())}
    rep["empty_state"] = empty
    rep["from_empty_state"] = (not empty["volumes"]) and (not empty["containers"]) and (not empty["secrets"])
    print("   empty-state:", rep["from_empty_state"], json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in empty.items()}))

    print("== 2/5 up.sh (REGEN_SECRETS=1, fresh config, migrations, seeds, substitutes, core)")
    env = {"REGEN_SECRETS": "1", "ARENA_SKIP_BUILD": os.environ.get("ARENA_SKIP_BUILD", "1"),
           "ARENA_SOURCE_REPOS_ROOT": repos_root}
    if os.environ.get("ARENA_ROUTE_PROFILE"):
        env["ARENA_ROUTE_PROFILE"] = os.environ["ARENA_ROUTE_PROFILE"]
    p = sh(["bash", "scripts/up.sh"], cwd=ENV, env=env, log=log, timeout=3000)
    (run_dir / "up.log").write_text(p.stdout + "\n--- stderr ---\n" + p.stderr)
    rep["up_rc"] = p.returncode
    rep["fingerprint"] = fingerprint()
    rep["no_stale_local_state"] = bool(rep["from_empty_state"] and rep["fingerprint"] and
                                       rep["fingerprint"].get("boot_id") != (rep["pre"]["fingerprint"] or {}).get("boot_id"))
    rep["no_stale_local_state_evidence"] = ("volumes+secrets destroyed by down.sh, secrets regenerated, new boot_id "
                                            f"{(rep['fingerprint'] or {}).get('boot_id')}")

    print("== 3/5 health")
    for _ in range(30):
        cs = containers()
        running = [c for c in cs if c["running"]]
        unhealthy = [c for c in running if not c["healthy"] and "health" in c["status"]]
        if running and not unhealthy:
            break
        time.sleep(10)
    cs = containers()
    rep["containers"] = cs
    rep["container_count"] = len(cs)
    rep["running"] = sum(1 for c in cs if c["running"])
    rep["healthy"] = sum(1 for c in cs if c["healthy"])
    rep["not_running"] = [c["name"] for c in cs if not c["running"]]
    rep["unhealthy"] = [c["name"] for c in cs if c["running"] and not c["healthy"]]
    print(f"   containers={rep['container_count']} running={rep['running']} healthy={rep['healthy']} unhealthy={rep['unhealthy']}")

    if not a.no_journeys:
        print("== 4/5 journeys (fresh merchants)")
        cmd = [sys.executable, "RED_LOOP/m6/journeys/run.py"] + (a.journeys_args.split() if a.journeys_args else [])
        p = sh(cmd, log=log, timeout=7200)
        (run_dir / "journeys.log").write_text(p.stdout + "\n--- stderr ---\n" + p.stderr)
        try:
            jr = json.loads((IMPL / "m6-journeys.json").read_text())
        except Exception:
            jr = {}
        js = jr.get("journeys", [])
        merchants = {j.get("merchant_id") for j in js if j.get("merchant_id")}
        rep["journeys_run_dir"] = jr.get("run_dir")
        rep["fresh_merchants"] = len(merchants)
        rep["journey_results"] = {r: sum(1 for j in js if j.get("result") == r) for r in ("PASS", "FAIL", "EXPECTED_FAILURE", "BLOCKED")}
        p0 = [j for j in js if (j.get("priority") or "P0") == "P0"]
        # the suite "passes" when every P0 journey is PASS / EXPECTED_FAILURE / BLOCKED-with-dependency and
        # at least one PASSes; a FAIL is a real observed twin/product defect (reported, never patched away)
        # and is listed separately so it is never hidden.
        rep["p0_failures"] = [j["id"] for j in p0 if j.get("result") == "FAIL"]
        rep["failures_all"] = [j["id"] for j in js if j.get("result") == "FAIL"]
        rep["blocked_without_dependency"] = [j["id"] for j in js if j.get("result") == "BLOCKED" and not j.get("missing_dependency")]
        rep["suite_passed"] = bool(js) and not rep["p0_failures"] and not rep["blocked_without_dependency"] \
            and any(j.get("result") == "PASS" for j in p0)
        rep["fingerprint_bound"] = (jr.get("fingerprint") or {}).get("boot_id") == (rep["fingerprint"] or {}).get("boot_id")
    rep["finished_at"] = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rep["command_log"] = log
    (run_dir / "clean-boot.json").write_text(json.dumps(rep, indent=2))
    slim = {k: v for k, v in rep.items() if k not in ("command_log",)}
    slim["containers"] = [{"name": c["name"], "running": c["running"], "healthy": c["healthy"]} for c in cs]
    IMPL.mkdir(parents=True, exist_ok=True)
    (IMPL / "m6-clean-boot.json").write_text(json.dumps(slim, indent=2))
    print("== 5/5 wrote", IMPL / "m6-clean-boot.json")
    print(json.dumps({k: slim.get(k) for k in ("from_empty_state", "up_rc", "container_count", "healthy", "unhealthy",
                                                 "fresh_merchants", "journey_results", "suite_passed", "no_stale_local_state")}, indent=2))
    return 0 if (slim.get("from_empty_state") and slim.get("up_rc") == 0 and not slim.get("unhealthy")) else 1


if __name__ == "__main__":
    sys.exit(main())
