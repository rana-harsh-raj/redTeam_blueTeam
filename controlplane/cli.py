"""controlplane CLI — durable autonomous campaign control plane (M10).

  python3 -m controlplane create   --mandate ... --snapshot ID --twin a:full:s1:merchant_ordinary
                                    --twin b:critical-payouts:s2:merchant_fresh --model claude-opus-4-8 --model gpt-5.5
  python3 -m controlplane start     <campaign_id> [--fake] [--live] [--max-ticks N] [--worker-concurrency K]
  python3 -m controlplane pause|resume|drain|terminate <campaign_id>
  python3 -m controlplane status|manifest|narrative|coverage|allocation|cost|results <campaign_id>
  python3 -m controlplane timeline|report <campaign_id>
  python3 -m controlplane ls | version
"""
import argparse
import json
import sys
import time
from pathlib import Path

from . import paths, __version__, SCHEMA_VERSION
from .store import ControlStore
from .manifest import build_manifest, validate
from .views import Views


def _load(campaign_id):
    control = ControlStore(campaign_id)
    manifest = control.read_manifest()
    if not manifest:
        raise SystemExit("no campaign %r under %s" % (campaign_id, paths.CAMPAIGNS))
    return control, manifest


def _parse_twin(spec):
    parts = spec.split(":")
    keys = ["instance_id", "profile", "seed", "starting_access_profile", "role"]
    t = {}
    for k, v in zip(keys, parts):
        t[k] = v
    if "instance_id" not in t:
        raise SystemExit("twin spec needs at least an instance id")
    return t


def cmd_create(a):
    paths.ensure_home()
    twins = [_parse_twin(s) for s in a.twin]
    budgets = {}
    for kv in a.budget or []:
        k, v = kv.split("=", 1)
        budgets[k] = _num(v)
    mani = build_manifest(a.mandate, a.snapshot, twins, a.model, mode=a.mode,
                          budgets=budgets or None, human=a.human, notes=a.notes)
    control = ControlStore(mani["campaign_id"])
    if control.manifest_path.exists():
        raise SystemExit("campaign %s already exists" % mani["campaign_id"])
    control.write_manifest(mani)
    control.set_control_state(phase="created")
    control.event("campaign_created", campaign_id=mani["campaign_id"], mode=a.mode,
                  twins=[t["instance_id"] for t in twins])
    print(json.dumps({"campaign_id": mani["campaign_id"],
                      "manifest_content_id": mani["manifest_content_id"],
                      "snapshot": a.snapshot, "twins": [t["instance_id"] for t in twins],
                      "mode": a.mode, "path": str(control.root)}, indent=2))


def cmd_start(a):
    control, manifest = _load(a.campaign_id)
    ok, cid = validate(manifest)
    if not ok:
        raise SystemExit("manifest content id mismatch (tampered): %s" % cid)
    clock = time.time
    if a.fake:
        from .fakes import build_fake_engine
        finders = set((a.fake_finders or "").split(",")) - {""}
        fixtures = json.loads(a.fake_broker) if a.fake_broker else None
        eng, _ = build_fake_engine(control, manifest, clock=clock,
                                   find_candidate_for=finders or None, broker_fixtures=fixtures,
                                   worker_concurrency=a.worker_concurrency,
                                   plan_max_theses=a.plan_max_theses,
                                   director_model=a.director_model)
    else:
        from .build import build_engine
        eng = build_engine(control, manifest, fake_model=a.fake_model,
                           fake_finders=list((a.fake_finders or "").split(",")) if a.fake_finders else None,
                           worker_concurrency=a.worker_concurrency, lease_ttl=a.lease_ttl,
                           plan_max_theses=a.plan_max_theses, director_model=a.director_model)
    if a.resume:
        eng.resume()
    stop = eng.run(max_ticks=a.max_ticks, tick_sleep=a.tick_sleep)
    print(json.dumps({"campaign_id": a.campaign_id, "stop_reason": stop,
                      "consumption": eng.budget.consumption()}, indent=2))


def cmd_pause(a):
    control, manifest = _load(a.campaign_id)
    control.pause_file.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ"))
    control.set_control_state(phase="paused")
    control.checkpoint("paused")
    control.event("campaign_paused", state_hash=control.state_hash())
    print("paused %s (state_hash=%s)" % (a.campaign_id, control.state_hash()[:16]))


def cmd_resume(a):
    a.resume = True
    a.fake = a.fake or _was_fake(a.campaign_id)
    cmd_start(a)


def cmd_drain(a):
    control, manifest = _load(a.campaign_id)
    control.set_control_state(phase="draining")
    a.resume = False
    a.fake = a.fake or _was_fake(a.campaign_id)
    cmd_start(a)


def cmd_terminate(a):
    control, manifest = _load(a.campaign_id)
    control.stop_file.write_text(a.reason or "operator_terminate")
    control.set_control_state(phase="terminated", terminated_reason=a.reason)
    control.checkpoint("terminated")
    control.event("campaign_terminated", reason=a.reason)
    print("terminated %s" % a.campaign_id)


def cmd_status(a):
    control, manifest = _load(a.campaign_id)
    v = Views(control, manifest)
    print(json.dumps({"campaign_id": a.campaign_id, "phase": control.control_state().get("phase"),
                      "state_hash": control.state_hash(), "counts": control.counts(),
                      "coverage": v.coverage(), "results": v.results(),
                      "cost": v.cost()}, indent=2, default=str))


def cmd_manifest(a):
    control, manifest = _load(a.campaign_id)
    ok, cid = validate(manifest)
    manifest["_validated"] = ok
    print(json.dumps(manifest, indent=2, sort_keys=True))


def _view_cmd(name):
    def fn(a):
        control, manifest = _load(a.campaign_id)
        v = Views(control, manifest)
        print(json.dumps(getattr(v, name)(), indent=2, default=str))
    return fn


def cmd_timeline(a):
    from .report import write_timeline
    control, manifest = _load(a.campaign_id)
    out = write_timeline(control, manifest, a.out)
    print("wrote %s" % out)


def cmd_report(a):
    from .report import write_report
    control, manifest = _load(a.campaign_id)
    out = write_report(control, manifest, a.out)
    print("wrote %s" % out)


def cmd_ls(a):
    paths.ensure_home()
    rows = []
    for d in sorted(paths.CAMPAIGNS.glob("camp-*")):
        mp = d / "manifest.json"
        if mp.exists():
            m = json.loads(mp.read_text())
            cs = d / "control_state.json"
            phase = json.loads(cs.read_text()).get("phase") if cs.exists() else "?"
            rows.append({"campaign_id": m.get("campaign_id"), "mode": m.get("mode"),
                         "phase": phase, "twins": [t["instance_id"] for t in m.get("twins", [])]})
    print(json.dumps(rows, indent=2))


def cmd_version(a):
    print(json.dumps({"controlplane": __version__, "schema": SCHEMA_VERSION}))


def _was_fake(campaign_id):
    return False


def _num(v):
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def main(argv=None):
    p = argparse.ArgumentParser(prog="controlplane", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create"); c.set_defaults(fn=cmd_create)
    c.add_argument("--mandate", required=True)
    c.add_argument("--snapshot", required=True)
    c.add_argument("--twin", action="append", required=True,
                   help="instance_id:profile:seed:starting_access_profile[:role]")
    c.add_argument("--model", action="append", required=True)
    c.add_argument("--mode", default="broad_autonomous")
    c.add_argument("--budget", action="append", help="k=v")
    c.add_argument("--human", default="operator")
    c.add_argument("--notes", default=None)

    for name in ("start", "resume", "drain"):
        s = sub.add_parser(name); s.set_defaults(fn={"start": cmd_start, "resume": cmd_resume, "drain": cmd_drain}[name])
        s.add_argument("campaign_id")
        s.add_argument("--fake", action="store_true", help="fully fake engine (no docker/gateway)")
        s.add_argument("--live", action="store_true")
        s.add_argument("--fake-model", action="store_true", dest="fake_model",
                       help="real twins + deterministic model policy (declared action budget)")
        s.add_argument("--fake-finders", default=None)
        s.add_argument("--fake-broker", default=None)
        s.add_argument("--director-model", action="store_true", dest="director_model")
        s.add_argument("--worker-concurrency", type=int, default=2, dest="worker_concurrency")
        s.add_argument("--plan-max-theses", type=int, default=8, dest="plan_max_theses")
        s.add_argument("--lease-ttl", type=int, default=180, dest="lease_ttl")
        s.add_argument("--max-ticks", type=int, default=100000, dest="max_ticks")
        s.add_argument("--tick-sleep", type=float, default=0, dest="tick_sleep")
        s.add_argument("--resume", action="store_true")

    for name, fn in (("pause", cmd_pause), ("status", cmd_status), ("manifest", cmd_manifest)):
        s = sub.add_parser(name); s.set_defaults(fn=fn); s.add_argument("campaign_id")
    t = sub.add_parser("terminate"); t.set_defaults(fn=cmd_terminate)
    t.add_argument("campaign_id"); t.add_argument("--reason", default="operator_terminate")

    for name in ("narrative", "coverage", "allocation", "cost", "results"):
        s = sub.add_parser(name); s.set_defaults(fn=_view_cmd(name)); s.add_argument("campaign_id")

    for name, fn in (("timeline", cmd_timeline), ("report", cmd_report)):
        s = sub.add_parser(name); s.set_defaults(fn=fn)
        s.add_argument("campaign_id"); s.add_argument("--out", default=None)

    sub.add_parser("ls").set_defaults(fn=cmd_ls)
    sub.add_parser("version").set_defaults(fn=cmd_version)

    a = p.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
