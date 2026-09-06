#!/usr/bin/env python3
"""Workstream B — disposable arena instance cleanup (Milestone 3.1).

Removes ONLY the Docker resources owned by a named disposable instance:
its compose project (containers), its explicit rzp-arena*-<suffix> networks and
config/secret volumes, and its project-scoped datastore volumes. Refuses to run
against the live default plan (empty suffix / project env2_compose) so it can
never tear down the live arena. Verifies absence afterward.

Host-side evidence directories are NEVER touched.

Usage:
  python3 scripts/instance-cleanup.py <instance-id>          # plan + remove
  python3 scripts/instance-cleanup.py <instance-id> --dry-run
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

COMPOSE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(COMPOSE_ROOT / "scripts"))
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location("instance_plan", COMPOSE_ROOT / "scripts" / "instance-plan.py")
instance_plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(instance_plan)


def run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, timeout=120, **kw)


def docker_exists_net(name):
    return name in run(["docker", "network", "ls", "--format", "{{.Name}}"]).stdout.split()


def docker_exists_vol(name):
    return name in run(["docker", "volume", "ls", "--format", "{{.Name}}"]).stdout.split()


def containers_for(project):
    return [c for c in run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
                            "--format", "{{.Names}}"]).stdout.split() if c]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("instance_id")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    p = instance_plan.plan(args.instance_id)
    if p["is_default_live_plan"] or p["rendered"]["project"] == instance_plan.LIVE_PROJECT:
        print("REFUSING: cleanup target resolves to the live default arena "
              f"(project {p['rendered']['project']}). Instance cleanup only removes "
              "disposable suffixed instances.", file=sys.stderr)
        return 3

    owns = p["cleanup_owns"]
    project = owns["project"]
    env = dict(os.environ)
    env.update(p["env"])

    print(f"Cleanup plan for instance '{args.instance_id}':")
    print(f"  project    {project}")
    print(f"  containers {containers_for(project) or 'none'}")
    print(f"  networks   {owns['networks']}")
    print(f"  volumes    {owns['volumes']}")
    if args.dry_run:
        print("  (dry-run; nothing removed)")
        return 0

    # 1. compose down for the project (removes containers + project-scoped
    #    volumes + its default network); -v removes named volumes it declares.
    dn = run(["docker", "compose", "--env-file", ".env.arena",
              "--profile", "datastores", "--profile", "substitutes", "--profile", "core",
              "down", "-v", "--remove-orphans"], cwd=str(COMPOSE_ROOT), env=env)
    print(f"  compose down rc={dn.returncode}")
    if dn.returncode != 0:
        sys.stderr.write(dn.stderr[-1500:] + "\n")

    # 2. Belt-and-braces: remove any leftover explicitly-named resources.
    for net in owns["networks"]:
        if docker_exists_net(net):
            run(["docker", "network", "rm", net])
    for vol in owns["volumes"]:
        if docker_exists_vol(vol):
            run(["docker", "volume", "rm", vol])

    # 2b. Belt-and-braces suffix sweep: remove any rzp-arena*/<suffix> volume or
    # network not in the active-profile compose render (e.g. config-mozart-mock,
    # which is declared but unused when the boot falls back to mozart-sim).
    suffix = p["env"]["ARENA_SUFFIX"]
    if suffix:
        for v in run(["docker", "volume", "ls", "--format", "{{.Name}}"]).stdout.split():
            if v.endswith(suffix) and (v.startswith("rzp-arena") or v.startswith(project)):
                run(["docker", "volume", "rm", v])
        for n in run(["docker", "network", "ls", "--format", "{{.Name}}"]).stdout.split():
            if n.endswith(suffix) and n.startswith(("rzp-arena", "rzp-ingress")):
                run(["docker", "network", "rm", n])

    # 3. Verify absence.
    left_c = containers_for(project)
    left_n = [n for n in owns["networks"] if docker_exists_net(n)]
    left_v = [v for v in owns["volumes"] if docker_exists_vol(v)]
    clean = not (left_c or left_n or left_v)
    print(f"  after cleanup: containers={left_c or 0} networks={left_n or 0} volumes={left_v or 0}")
    print(f"  CLEAN={clean}")
    return 0 if clean else 4


if __name__ == "__main__":
    sys.exit(main())
