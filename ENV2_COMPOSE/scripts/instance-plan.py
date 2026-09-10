#!/usr/bin/env python3
"""Workstream B — disposable arena instance planner / preflight (Milestone 3.1).

Given an instance id, deterministically derives every host-global or explicitly
named Docker resource the instance would use, renders the authoritative names via
`docker compose config`, lists existing Docker resources, and FAILS CLOSED if the
plan would share any writable network, volume, container or host port with an
already-existing arena (the live `env2_compose` arena or another instance).

The default (no instance id / empty suffix) reproduces the current local names, so
existing workflows are unaffected.

Isolation model:
- Docker resources are isolated by ARENA_SUFFIX (explicit rzp-arena* names) plus
  COMPOSE_PROJECT_NAME (project-scoped datastore/secret volumes + container names)
  plus a distinct host port and distinct /24 subnets.
- Host-side generated material (secrets/, config/generated/, seeds/generated/,
  .runtime/) is written into the source tree, so a disposable boot must run from a
  SEPARATE working copy (see --emit-env / clean-boot wrapper). This planner only
  reasons about Docker resource collisions.

Usage:
  python3 scripts/instance-plan.py <instance-id>            # plan + overlap check
  python3 scripts/instance-plan.py <instance-id> --emit-env # print sourceable env
  python3 scripts/instance-plan.py <instance-id> --json     # machine-readable plan
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

COMPOSE_ROOT = Path(__file__).resolve().parents[1]
LIVE_PROJECT = "env2_compose"
LIVE_NETWORKS = {"rzp-arena", "rzp-ingress"}
LIVE_PORT = 18080


def sanitize(instance_id):
    s = re.sub(r"[^a-z0-9-]", "", instance_id.lower())
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        raise SystemExit("instance id sanitizes to empty; use [a-z0-9-]")
    return s


def derive(instance_id):
    """Deterministic env for an instance. Empty/none -> live defaults."""
    if not instance_id or instance_id in ("default", "live", LIVE_PROJECT):
        return {
            "ARENA_INSTANCE": "",
            "ARENA_SUFFIX": "",
            "COMPOSE_PROJECT_NAME": LIVE_PROJECT,
            "ARENA_SUBNET": "172.28.16.0/24",
            "INGRESS_SUBNET": "172.28.17.0/24",
            "KONG_LITE_HOST_PORT": str(LIVE_PORT),
        }
    s = sanitize(instance_id)
    h = int(hashlib.sha256(s.encode()).hexdigest(), 16)
    # Distinct /24 pair away from the live 16/17 block: 172.28.[32..230].0/24.
    base = 32 + (h % 99) * 2
    arena_octet, ingress_octet = base, base + 1
    # Distinct host port away from 18080: 18100..18899.
    port = 18100 + (h % 800)
    return {
        "ARENA_INSTANCE": s,
        "ARENA_SUFFIX": f"-{s}",
        "COMPOSE_PROJECT_NAME": f"env2c_{s}".replace("-", "_"),
        "ARENA_SUBNET": f"172.28.{arena_octet}.0/24",
        "INGRESS_SUBNET": f"172.28.{ingress_octet}.0/24",
        "KONG_LITE_HOST_PORT": str(port),
    }


def compose_config_names(env):
    """Authoritative rendered names from docker compose config."""
    import os
    e = dict(os.environ)
    e.update(env)
    cmd = ["docker", "compose", "--env-file", ".env.arena",
           "--profile", "datastores", "--profile", "substitutes", "--profile", "core",
           "config"]
    out = subprocess.run(cmd, cwd=str(COMPOSE_ROOT), env=e, capture_output=True,
                         text=True, timeout=120)
    if out.returncode != 0:
        raise SystemExit(f"docker compose config failed:\n{out.stderr[:2000]}")
    txt = out.stdout
    networks, volumes = set(), set()
    section = None
    for line in txt.splitlines():
        if re.match(r"^networks:", line):
            section = "net"; continue
        if re.match(r"^volumes:", line):
            section = "vol"; continue
        if re.match(r"^[a-z]", line):
            section = None
        m = re.match(r"^\s+name:\s*(\S+)", line)
        if m and section == "net":
            networks.add(m.group(1))
        elif m and section == "vol":
            volumes.add(m.group(1))
    project = ""
    m = re.search(r"^name:\s*(\S+)", txt, re.M)
    if m:
        project = m.group(1)
    return project, networks, volumes


def existing_docker():
    def _ls(args):
        r = subprocess.run(args, capture_output=True, text=True, timeout=30)
        return set(x for x in r.stdout.split() if x)
    nets = _ls(["docker", "network", "ls", "--format", "{{.Name}}"])
    vols = _ls(["docker", "volume", "ls", "--format", "{{.Name}}"])
    ports = subprocess.run(["docker", "ps", "--format", "{{.Ports}}"],
                           capture_output=True, text=True, timeout=30).stdout
    used_ports = set(re.findall(r"127\.0\.0\.1:(\d+)->", ports)) | set(re.findall(r"0\.0\.0\.0:(\d+)->", ports))
    return nets, vols, used_ports


def plan(instance_id):
    env = derive(instance_id)
    project, networks, volumes = compose_config_names(env)
    ex_nets, ex_vols, ex_ports = existing_docker()

    is_default = env["ARENA_SUFFIX"] == ""
    # Overlap detection (skip for the intentional default/live plan).
    net_overlap = sorted((networks & ex_nets)) if not is_default else []
    vol_overlap = sorted((volumes & ex_vols)) if not is_default else []
    port = env["KONG_LITE_HOST_PORT"]
    port_overlap = (port in ex_ports) and not is_default
    # Even the default plan must never *accidentally* be run as a second instance:
    live_name_reuse = bool((networks & LIVE_NETWORKS)) and not is_default

    safe = not (net_overlap or vol_overlap or port_overlap or live_name_reuse)
    return {
        "instance_id": instance_id,
        "is_default_live_plan": is_default,
        "env": env,
        "rendered": {
            "project": project,
            "networks": sorted(networks),
            "volumes": sorted(volumes),
            "kong_host_port": port,
        },
        "cleanup_owns": {
            "project": project,
            "networks": sorted(networks),
            "volumes": sorted(volumes),
            "container_prefix": project + "-",
            "host_port": port,
        },
        "overlap": {
            "networks": net_overlap,
            "volumes": vol_overlap,
            "host_port_in_use": port_overlap,
            "reuses_live_named_network": live_name_reuse,
        },
        "safe_to_launch": bool(safe),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("instance_id", nargs="?", default="")
    ap.add_argument("--emit-env", action="store_true", help="print sourceable env exports")
    ap.add_argument("--json", action="store_true", help="print full JSON plan")
    args = ap.parse_args()

    p = plan(args.instance_id)

    if args.emit_env:
        for k, v in p["env"].items():
            print(f'export {k}="{v}"')
        return 0 if p["safe_to_launch"] else 2

    if args.json:
        print(json.dumps(p, indent=2))
        return 0 if p["safe_to_launch"] else 2

    # Human-readable
    print(f"Instance plan: {args.instance_id or '(default/live)'}")
    print(f"  project           {p['rendered']['project']}")
    print(f"  networks          {', '.join(p['rendered']['networks'])}")
    print(f"  kong host port    {p['rendered']['kong_host_port']}")
    print(f"  subnets           {p['env']['ARENA_SUBNET']}  {p['env']['INGRESS_SUBNET']}")
    print(f"  config/secret vols {sum(1 for v in p['rendered']['volumes'] if v.startswith('rzp-arena-'))} explicit + "
          f"{sum(1 for v in p['rendered']['volumes'] if not v.startswith('rzp-arena-'))} project-scoped")
    ov = p["overlap"]
    if p["is_default_live_plan"]:
        print("  NOTE: default/live plan (no suffix) -- overlap check intentionally skipped")
    else:
        print(f"  overlap networks  {ov['networks'] or 'none'}")
        print(f"  overlap volumes   {ov['volumes'] or 'none'}")
        print(f"  host port in use  {ov['host_port_in_use']}")
        print(f"  reuses live net   {ov['reuses_live_named_network']}")
    print(f"  SAFE TO LAUNCH    {p['safe_to_launch']}")
    if not p["safe_to_launch"]:
        print("  REFUSING: plan overlaps an existing arena's writable resources.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
