"""python3 -m twinfactory <command> ...   (backend-independent lifecycle CLI)

  profiles [--snapshot ID]                       list derivable profiles + counts
  profile <name> [--json]                        print a derived profile with its derivation trace
  create <id> --profile P --seed S [--backend colima|local-docker|remote-docker] [--snapshot ID] [--cpus N --memory G --disk G]
  build <id> | start <id> | status <id> | health <id> | reset <id> | stop <id> [--boundary] | destroy <id> [--purge]
  snapshot-state <id> [--label L] | restore-state <id> --label L
  journeys <id> [--only a,b] [--family f]
  reproduce <id> <new-id>
  ls | manifest <id> | events <id>
  images export --from <DOCKER_HOST> [--refs r1,r2 | --profile P]   seed the factory image cache from a daemon that has the images
  images ls
  inputs export | inputs ls                      copy the checkout's accepted credential-free build inputs (migration assets,
                                                 fts config) into the factory home so a clean checkout can provision
  isolation ... / acceptance / report / bench   (see twinfactory.isolation, .acceptance, .report)
"""
import argparse
import json
import sys

from . import paths, __version__
from .factory import Factory


def _print(obj):
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="twinfactory", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=None, help="factory home (default $TWIN_FACTORY_HOME or ~/.twin-factory)")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("profiles"); p.add_argument("--snapshot"); p.add_argument("--trust-path", default="real", choices=["real", "substitute"])
    p = sub.add_parser("profile"); p.add_argument("name"); p.add_argument("--snapshot"); p.add_argument("--json", action="store_true"); p.add_argument("--trust-path", default="real", choices=["real", "substitute"])
    p = sub.add_parser("create"); p.add_argument("id"); p.add_argument("--profile", required=True); p.add_argument("--seed", required=True)
    p.add_argument("--trust-path", default="real", choices=["real", "substitute"], help="M11: real promoted trust-path services (default) or the M6/M7 substitutes")
    p.add_argument("--backend", default="colima"); p.add_argument("--snapshot"); p.add_argument("--cpus", type=int); p.add_argument("--memory", type=int); p.add_argument("--disk", type=int)
    p.add_argument("--docker-host"); p.add_argument("--s2p-image"); p.add_argument("--force", action="store_true")
    for c in ("build", "start", "status", "health", "reset", "manifest", "events"):
        p = sub.add_parser(c); p.add_argument("id")
        if c == "start":
            p.add_argument("--resume-from", choices=["materialize", "datastores", "migrations", "seed", "substitutes", "trust_path", "core", "post_core", "bridge", "fingerprint", "s2p"],
                           help="re-enter a FAILED boot at this stage (earlier stages' containers/volumes are kept)")
    p = sub.add_parser("stop"); p.add_argument("id"); p.add_argument("--boundary", action="store_true")
    p = sub.add_parser("destroy"); p.add_argument("id"); p.add_argument("--purge", action="store_true"); p.add_argument("--keep-boundary", action="store_true")
    p = sub.add_parser("snapshot-state"); p.add_argument("id"); p.add_argument("--label")
    p = sub.add_parser("restore-state"); p.add_argument("id"); p.add_argument("--label", required=True)
    p = sub.add_parser("journeys"); p.add_argument("id"); p.add_argument("--only"); p.add_argument("--family"); p.add_argument("--timeout", type=int, default=7200)
    p = sub.add_parser("reproduce"); p.add_argument("id"); p.add_argument("new_id"); p.add_argument("--backend")
    sub.add_parser("ls")
    p = sub.add_parser("images"); p.add_argument("action", choices=["export", "ls"]); p.add_argument("--from", dest="src"); p.add_argument("--refs"); p.add_argument("--profile")
    p = sub.add_parser("inputs"); p.add_argument("action", choices=["export", "ls"])
    p = sub.add_parser("isolation"); p.add_argument("args", nargs=argparse.REMAINDER)
    p = sub.add_parser("evidence"); p.add_argument("--ids", required=True); p.add_argument("--proof", default=None)
    sub.add_parser("acceptance"); sub.add_parser("report")
    p = sub.add_parser("version")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help(); return 2
    f = Factory(a.home)
    if a.cmd == "version":
        print(__version__); return 0
    if a.cmd == "profiles":
        from . import profiles as P
        inp = f.inputs(a.snapshot)
        out = {"architecture_snapshot_id": inp.snapshot_id, "recipe_set_id": inp.recipe_set_id, "profiles": {}}
        for name in ["full"] + list(P.ALIASES):
            pr = P.derive(inp, name, a.trust_path); out["profiles"][name] = {"resolved": pr["name"], **pr["counts"], "s2p": pr["s2p"], "trust_path": pr.get("trust_path")}
        out["focused_families"] = sorted(k.replace("family:", "") for k in inp.index.families)
        _print(out); return 0
    if a.cmd == "profile":
        from . import profiles as P
        pr = P.derive(f.inputs(a.snapshot), a.name, a.trust_path)
        if a.json:
            _print(pr)
        else:
            print("%s  snapshot=%s  %s" % (pr["name"], pr["architecture_snapshot_id"][:12], json.dumps(pr["counts"])))
            for s, v in pr["services"].items():
                print("  %-58s %-20s %s" % (s, v["category"], v["reasons"][0][:120]))
            print("  jobs:", ", ".join(pr["jobs"]), "| s2p:", pr["s2p"])
        return 0
    if a.cmd == "create":
        sizing = {"cpus": a.cpus, "memory_gib": a.memory, "disk_gib": a.disk}
        opts = {"docker_host": a.docker_host} if a.docker_host else None
        m = f.create(a.id, a.profile, a.seed, a.backend, snapshot_id=a.snapshot, sizing=sizing, backend_options=opts, s2p_image=a.s2p_image, force=a.force, trust_path=a.trust_path)
        _print({k: m[k] for k in ("instance_id", "state", "profile", "trust_path", "architecture_snapshot_id", "recipe_set_id", "seed", "seed_epoch", "compose_project", "kong_host_port", "arena_subnet", "exec_dir", "inputs_hash", "backend")}); return 0
    if a.cmd == "build":
        m = f.build(a.id); _print({"instance_id": m["instance_id"], "state": m["state"], "image_digests": m["image_digests"], "timings": m["timings"], "runtime_instance_id": m["runtime_instance_id"]}); return 0
    if a.cmd == "start":
        m = f.start(a.id, resume_from=getattr(a, "resume_from", None)); _print({"instance_id": m["instance_id"], "state": m["state"], "boot_id": (m.get("boot") or {}).get("boot_id"), "timings": m["timings"], "runtime_instance_id": m["runtime_instance_id"]}); return 0
    if a.cmd == "status":
        _print(f.status(a.id)); return 0
    if a.cmd == "health":
        h = f.health(a.id); _print(h); return 0 if h["healthy"] else 1
    if a.cmd == "reset":
        r = f.reset(a.id); _print({k: r.get(k) for k in ("instance_id", "secs", "queues_purged", "ingress_reset")}); return 0
    if a.cmd == "stop":
        _print(f.stop(a.id, boundary=a.boundary)); return 0
    if a.cmd == "destroy":
        _print(f.destroy(a.id, purge=a.purge, keep_boundary=a.keep_boundary)); return 0
    if a.cmd == "snapshot-state":
        _print(f.snapshot_state(a.id, a.label)); return 0
    if a.cmd == "restore-state":
        _print(f.restore_state(a.id, a.label)); return 0
    if a.cmd == "journeys":
        r = f.journeys(a.id, only=a.only, family=a.family, timeout=a.timeout)
        _print({k: r[k] for k in ("instance_id", "run_dir", "rc", "secs", "summary", "attributed", "journeys")}); return 0 if r["rc"] == 0 else 1
    if a.cmd == "reproduce":
        m = f.reproduce(a.id, a.new_id, a.backend); _print({k: m[k] for k in ("instance_id", "profile", "seed", "inputs_hash", "reproduced_from")}); return 0
    if a.cmd == "ls":
        for r in f.list():
            print("%-24s %-10s %-26s snap=%s port=%s backend=%s" % (r["instance_id"], r.get("state"), r.get("profile"), (r.get("architecture_snapshot_id") or "")[:12], r.get("kong_host_port"), (r.get("backend") or {}).get("kind")))
        return 0
    if a.cmd == "manifest":
        _print(f.manifest(a.id)); return 0
    if a.cmd == "events":
        p = f.idir(a.id) / "events.jsonl"
        print(p.read_text() if p.is_file() else ""); return 0
    if a.cmd == "images":
        from . import images as I
        if a.action == "ls":
            _print(f.cache.index()); return 0
        env = {"DOCKER_HOST": a.src} if a.src else {}
        refs = [r for r in (a.refs or "").split(",") if r]
        if a.profile:
            from . import profiles as P
            inp = f.inputs(None); pr = P.derive(inp, a.profile)
            import re
            tag = None
            for line in (paths.ENV2 / ".env.arena").read_text().splitlines():
                mm = re.match(r"ARENA_TAG=(\S+)", line)
                if mm:
                    tag = mm.group(1)
            from .factory import _s2p_image
            for s in list(pr["services"]) + list(pr["jobs"]):
                img = (inp.recipes.get(s) or {}).get("runtime", {}).get("image") if s in inp.recipes else None
                if img and "S2P_SOURCE_IMAGE" in img:
                    img = _s2p_image(f.cache)
                if img:
                    img = img.replace("${ARENA_TAG:-local}", tag or "local")
                if img and "${" not in img:
                    refs.append(img)
            refs += I.ALWAYS
            refs = [r for r in refs if "@sha256:" not in r]   # digest-pinned public refs are pulled by digest per instance
        done = {}
        for ref in sorted(set(refs)):
            try:
                done[ref] = f.cache.export(ref, env, "export:%s" % (a.src or "default"))["image_id"]
            except Exception as e:  # noqa: BLE001
                done[ref] = "ERROR: %s" % str(e)[:200]
        _print(done); return 0 if not any(str(v).startswith("ERROR") for v in done.values()) else 1
    if a.cmd == "inputs":
        from . import workspace as W
        if a.action == "export":
            _print(W.export_input_bundles()); return 0
        _print(json.loads((paths.FACTORY_HOME / "inputs" / "INDEX.json").read_text()) if (paths.FACTORY_HOME / "inputs" / "INDEX.json").is_file() else {}); return 0
    if a.cmd == "isolation":
        from .isolation import main as iso
        return iso(a.args, f)
    if a.cmd == "evidence":
        from .evidence import collect
        _print(collect(f, [x for x in a.ids.split(",") if x], a.proof)); return 0
    if a.cmd == "acceptance":
        from .acceptance import main as acc
        return acc()
    if a.cmd == "report":
        from .report import render
        print(render()); return 0
    return 2
