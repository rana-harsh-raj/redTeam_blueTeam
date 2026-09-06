#!/usr/bin/env python3
"""Host-side runner for the M4 boundary/identity/concurrency suite (T14).

Mirrors scripts/golden-run.sh's compose invocation (same --env-file, profiles,
verifier service, -v RUN_DIR:/results, --junitxml) and adds the read-only mounts
these tests need but the stock verifier service does not carry:

  * ENV2_COMPOSE/secrets/merchant_arena_m{1,2,3}_secret.txt -> /run/m4/  (merchant
    API-key secrets, so the tests can speak Basic-Auth to the hardened edge as a
    real merchant; ARENA_MERCHANT_SECRETS_DIR=/run/m4)
  * RED_LOOP/ -> /redloop  (registry/merchant-gateway-routes.json for probe
    enumeration; red_loop/broker.py for the G50 broker fingerprint;
    M4_REDLOOP_DIR=/redloop)

It sets KONG_LITE_URL to the in-network kong-lite (the tests run inside the
verifier container, exactly as golden-run.sh runs the v-suite), then writes
reports/implementation/m4-boundary-results.json from the run's junit + traces.

Usage:
    python3 verifier/m4_boundary.py [-k EXPR] [extra pytest args...]
Run from ENV2_COMPOSE/ (or anywhere; paths are resolved from this file).
Requires the arena already up with KONG_ENFORCE_ROUTE_POLICY=1 (the hardened
edge; the suite's `hardened_edge` fixture fails fast otherwise).
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ENV2 = Path(__file__).resolve().parents[1]          # ENV2_COMPOSE/
REPO = ENV2.parent
REDLOOP = REPO / "RED_LOOP"
SECRETS = ENV2 / "secrets"


def compose_base():
    return ["docker", "compose", "--env-file", str(ENV2 / ".env.arena"),
            "-f", str(ENV2 / "docker-compose.yml"),
            "--profile", "datastores", "--profile", "migrations",
            "--profile", "substitutes", "--profile", "core", "--profile", "verify"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=None, help="evidence dir (default RED_LOOP/runs/m4-boundary-<ts>)")
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = Path(args.run_dir) if args.run_dir else (REDLOOP / "runs" / ("m4-boundary-%s" % ts))
    run_dir.mkdir(parents=True, exist_ok=True)
    run_dir = run_dir.resolve()

    # wait for the hardened edge to be healthy (same helper golden-run.sh uses)
    subprocess.run([str(ENV2 / "scripts" / "healthcheck.sh"), "kong-lite", "--timeout", "60"], cwd=str(ENV2), check=True)
    if not args.no_build and os.environ.get("ARENA_SKIP_BUILD", "0") != "1":
        subprocess.run(compose_base() + ["build", "verifier"], cwd=str(ENV2), check=True)

    # The verifier ENTRYPOINT is run.sh, which already runs network_check.py and
    # appends `-p no:cacheprovider -rs -v --tb=short ... verifiers`; args here are
    # passed through verbatim BEFORE that trailing `verifiers`. Default to the M4
    # subset so a bare run does not re-run the whole v-suite.
    extra = [a for a in (args.pytest_args or []) if a != "--"]
    if not any(a in ("-k", "--collect-only") or a.startswith("-k") for a in extra):
        extra = ["-k", "test_m4_ or free_payout_cross_tenant"] + extra

    cmd = compose_base() + [
        "run", "--rm", "--no-deps",
        "-v", "%s:/results" % run_dir,
        "-v", "%s:/run/m4/merchant_arena_m1_secret.txt:ro" % (SECRETS / "merchant_arena_m1_secret.txt"),
        "-v", "%s:/run/m4/merchant_arena_m2_secret.txt:ro" % (SECRETS / "merchant_arena_m2_secret.txt"),
        "-v", "%s:/run/m4/merchant_arena_m3_secret.txt:ro" % (SECRETS / "merchant_arena_m3_secret.txt"),
        "-v", "%s:/redloop:ro" % REDLOOP,
        "-e", "ARENA_TRACE_DIR=/results",
        "-e", "ARENA_MERCHANT_SECRETS_DIR=/run/m4",
        "-e", "M4_REDLOOP_DIR=/redloop",
        "-e", "KONG_LITE_URL=http://kong-lite:8080",
        "verifier", "--junitxml=/results/junit.xml",
    ] + extra
    print("[m4-boundary] run_dir=%s" % run_dir, file=sys.stderr)
    proc = subprocess.run(cmd, cwd=str(ENV2))

    # summary is written regardless of the pytest exit code (xfail/failures are data)
    summarize = subprocess.run([sys.executable, str(ENV2 / "verifier" / "m4_summarize.py"), str(run_dir)],
                               cwd=str(ENV2))
    print("[m4-boundary] pytest_exit=%d summarize_exit=%d" % (proc.returncode, summarize.returncode), file=sys.stderr)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
