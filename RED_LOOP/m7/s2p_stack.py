#!/usr/bin/env python3
"""M7: the Source-to-Pay stack inside the Payouts arena (compose overlay ENV2_COMPOSE/docker-compose.s2p.yml).

  python3 RED_LOOP/m7/s2p_stack.py build     # pinned source -> runtime image (DOMAIN_REPLICAS/source_to_pay/scripts)
  python3 RED_LOOP/m7/s2p_stack.py up        # start s2p-kafka/s2p-mysql/s2p-redis/s2p-vp-source, attach api-ingress as `boundary`
  python3 RED_LOOP/m7/s2p_stack.py down      # stop the s2p services + remove their volume (arena untouched)
  python3 RED_LOOP/m7/s2p_stack.py status    # JSON: containers, health, source sha, topics

`build` runs the replica's own bootstrap/build/build-runtime chain in a fresh S2P_BUILD_ROOT (the same
scripts the standalone acceptance uses) and records the image tag in
DOMAIN_REPLICAS/source_to_pay/.build/runtime-image.env, which `up` reads. Nothing here touches the
standalone replica's compose project.
"""
import argparse, json, os, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENV2 = REPO / "ENV2_COMPOSE"
S2P = REPO / "DOMAIN_REPLICAS" / "source_to_pay"
IMAGE_ENV = S2P / ".build" / "runtime-image.env"
PROJECT = os.environ.get("ARENA_COMPOSE_PROJECT", os.environ.get("COMPOSE_PROJECT_NAME", "env2_compose"))
TOPICS = ("add-tds-entry", "prod.x.vendor-payments.accounting-payouts.status-update")


def compose(*args, check=True, env=None, capture=True):
    cmd = ["docker", "compose", "--env-file", str(ENV2 / ".env.arena"), "-f", str(ENV2 / "docker-compose.yml"),
           "-f", str(ENV2 / "docker-compose.s2p.yml"), "--profile", "datastores", "--profile", "substitutes",
           "--profile", "core", "--profile", "s2p"] + list(args)
    e = dict(os.environ); e.update(env or {})
    return subprocess.run(cmd, cwd=ENV2, env=e, capture_output=capture, text=True, check=check)


def image_tag():
    if not IMAGE_ENV.exists():
        return None
    for line in IMAGE_ENV.read_text().splitlines():
        if line.startswith("S2P_SOURCE_IMAGE="):
            return line.split("=", 1)[1].strip()
    return None


def cname(service):
    return "%s-%s-1" % (PROJECT, service)


def build(run_id=None):
    run_id = run_id or datetime.now(timezone.utc).strftime("m7-%Y%m%dT%H%M%SZ")
    root = S2P / ".build" / "arena-runs" / run_id
    if root.exists():
        raise SystemExit("build root exists: %s" % root)
    env = dict(os.environ)
    env["S2P_BUILD_ROOT"] = str(root)
    env["S2P_GENERATED_STAGE"] = str(root / "staging" / "vendor-payments")
    t0 = time.time()
    for step in ("bootstrap.sh", "build.sh", "build-runtime.sh"):
        p = subprocess.run([str(S2P / "scripts" / step)], env=env, capture_output=True, text=True)
        if p.returncode:
            sys.stderr.write(p.stdout[-3000:] + p.stderr[-3000:])
            raise SystemExit("%s failed rc=%d" % (step, p.returncode))
    build_json = json.loads((S2P / "artifacts" / "build-runtime.json").read_text())
    if build_json["generated_stage"] != env["S2P_GENERATED_STAGE"]:
        raise SystemExit("runtime used a different source stage")
    out = {"image": image_tag(), "source_sha": build_json["source_sha"], "binary_sha256": build_json["binary"]["sha256"],
           "build_root": str(root), "seconds": round(time.time() - t0, 1)}
    print(json.dumps(out))
    return out


def up():
    tag = image_tag()
    if not tag:
        raise SystemExit("no runtime image recorded; run `s2p_stack.py build`")
    insp = subprocess.run(["docker", "image", "inspect", tag], capture_output=True, text=True)
    if insp.returncode:
        raise SystemExit("runtime image %s is not present; run `s2p_stack.py build`" % tag)
    env = {"S2P_SOURCE_IMAGE": tag}
    # (re)create api-ingress with the S2P network attachment + relay URL, then the S2P services
    compose("up", "-d", "--no-build", "api-ingress", env=env)
    compose("up", "-d", "--no-build", "--wait", "s2p-kafka", "s2p-mysql", "s2p-redis", env=env)
    for topic in TOPICS:
        r = subprocess.run(["docker", "exec", cname("s2p-kafka"), "rpk", "topic", "describe", topic], capture_output=True, text=True)
        if r.returncode:
            subprocess.run(["docker", "exec", cname("s2p-kafka"), "rpk", "topic", "create", topic], check=True, capture_output=True)
    compose("up", "-d", "--no-build", "--wait", "s2p-vp-source", env=env)
    print(json.dumps(status()))


def down():
    tag = image_tag() or "none"
    # stop the S2P services and drop ONLY the S2P mysql volume; api-ingress is recreated without the s2p network
    subprocess.run(["docker", "rm", "-f", cname("s2p-vp-source"), cname("s2p-kafka"), cname("s2p-mysql"), cname("s2p-redis")],
                   capture_output=True, text=True)
    subprocess.run(["docker", "volume", "rm", "-f", "rzp-arena-s2p-mysql-data" + os.environ.get("ARENA_SUFFIX", "")], capture_output=True, text=True)
    subprocess.run(["docker", "network", "rm", "rzp-s2p-internal" + os.environ.get("ARENA_SUFFIX", "")], capture_output=True, text=True)
    print(json.dumps({"down": True, "image": tag}))


def status():
    out = {"project": PROJECT, "image": image_tag(), "containers": {}, "at": datetime.now(timezone.utc).isoformat()}
    for svc in ("s2p-kafka", "s2p-mysql", "s2p-redis", "s2p-vp-source", "api-ingress"):
        r = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}} {{.State.StartedAt}} {{.Id}}", cname(svc)],
                           capture_output=True, text=True)
        out["containers"][svc] = r.stdout.strip() if r.returncode == 0 else "absent"
    r = subprocess.run(["docker", "exec", cname("s2p-vp-source"), "/service", "health"], capture_output=True, text=True)
    out["vp_source_health_rc"] = r.returncode
    r = subprocess.run(["docker", "exec", cname("s2p-kafka"), "rpk", "topic", "list"], capture_output=True, text=True)
    out["topics"] = [l.split()[0] for l in r.stdout.splitlines()[1:]] if r.returncode == 0 else None
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("action", choices=["build", "up", "down", "status"]); ap.add_argument("--run-id")
    a = ap.parse_args()
    if a.action == "build":
        build(a.run_id)
    elif a.action == "up":
        up()
    elif a.action == "down":
        down()
    else:
        print(json.dumps(status(), indent=1))


if __name__ == "__main__":
    main()
