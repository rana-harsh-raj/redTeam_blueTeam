"""Reproducible, model-free, Docker-free proof of the control-plane mechanics.

One main campaign demonstrates, in a single durable run:
  * a broad mandate only (no target);
  * the Director generating theses over multiple planning cycles;
  * cells created / reallocated / retired;
  * bounded tasks across TWO isolated twins, with durable leases;
  * semantic cross-cell hypothesis dedup + deliberate independent replication;
  * an injected WORKER failure recovered via lease expiry + requeue;
  * a DIRECTOR process kill + restart that resumes from durable state;
  * a MODEL timeout handled by fallback;
  * a failed TWIN reproduced through the (fake) twin pool and re-homed;
  * PAUSE / RESUME with state continuity;
  * decorrelated, non-self verification of candidates.
Two micro-campaigns prove the external kill switch and budget enforcement.

Everything runs on a virtual clock with injected fakes: no network, no model
provider, no Docker — so acceptance reproduces it identically from a clean
checkout.
"""
from pathlib import Path

from .store import ControlStore
from .manifest import build_manifest
from .engine import Engine, InProcessExecutor, WorkerAbandoned
from .fakes import build_fake_engine


class VClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def tick(self, dt=1.0):
        self.t += dt


def _manifest(home, budgets=None, cid_salt="main"):
    return build_manifest(
        "Assess whatever most weakens the payouts money-movement boundary on the "
        "assigned twins. You are given no target, vulnerability class or attack path.",
        "snap-fake-%s" % cid_salt,
        [{"instance_id": "alpha", "profile": "full", "seed": "seed-a",
          "starting_access_profile": "merchant_ordinary", "role": "full"},
         {"instance_id": "beta", "profile": "critical-payouts", "seed": "seed-b",
          "starting_access_profile": "merchant_fresh", "role": "focused"}],
        ["claude-opus-4-8", "gpt-5.5", "gemini-2.5-pro", "gpt-5.4-mini"],
        mode="broad_autonomous", budgets=budgets)


class _Flaky(InProcessExecutor):
    """Kills TASK-001 exactly once (a crashed worker that never released its
    lease) so recovery must come through lease expiry."""

    def __init__(self, engine):
        super().__init__(engine)
        self.killed = set()

    def run(self, task, handle, worker_id):
        if task["task_id"] == "TASK-001" and "TASK-001" not in self.killed:
            self.killed.add("TASK-001")
            self.engine.control.event("injected_worker_kill", task_id="TASK-001", worker_id=worker_id)
            raise WorkerAbandoned("injected worker kill")
        return super().run(task, handle, worker_id)


def run_full_proof(home):
    home = Path(home)
    clock = VClock()
    mani = _manifest(home, budgets={"planning_cycles_min": 2, "max_actions": 2000,
                                    "max_wall_seconds": 10 ** 9})
    cid = mani["campaign_id"]
    control = ControlStore(cid, root=home / cid)
    control.write_manifest(mani)

    # marker fixture makes the (fake) twin genuinely carry the cited evidence, so
    # verification is evidence-bound rather than assumed.
    fixtures = {"/v1/payouts": {"status": 200, "body": '{"note":"canary-observed"}'}}

    def new_engine(store):
        eng, pool = build_fake_engine(
            store, mani, clock=clock, find_candidate_for={"gpt-5.5"},
            broker_fixtures=fixtures, unhealthy_once={"beta": 1},
            worker_concurrency=2, lease_ttl=5, plan_max_theses=4)
        # primary worker model times out -> must fall back to gpt-5.5
        from .fakes import fake_client_factory
        eng.client_factory = fake_client_factory(
            behaviour_map={"claude-opus-4-8": "timeout"}, find_candidate_for={"gpt-5.5"})
        eng.executor = _Flaky(eng)
        # advance the virtual clock past the lease TTL each dispatch so an
        # abandoned lease is reaped on a later tick
        orig = eng._dispatch_ready
        def patched():
            n = orig(); clock.tick(10); return n
        eng._dispatch_ready = patched
        return eng

    eng = new_engine(control)
    eng.run(max_ticks=3)                    # partial run
    eng.pause()                             # operator pause
    hash_at_pause = control.state_hash()
    counts_at_pause = control.counts()

    # --- Director process killed and restarted: fresh objects from durable store
    control2 = ControlStore(cid, root=home / cid)
    eng2 = new_engine(control2)
    eng2.resume()                           # clears PAUSE, recovers expired leases
    stop = eng2.run(max_ticks=400)

    return {"campaign_id": cid, "control": control2, "manifest": mani,
            "stop_reason": stop, "hash_at_pause": hash_at_pause,
            "counts_at_pause": counts_at_pause, "final_hash": control2.state_hash()}


def run_killswitch_proof(home):
    home = Path(home)
    clock = VClock()
    mani = _manifest(home, budgets={"planning_cycles_min": 1}, cid_salt="kill")
    cid = mani["campaign_id"]
    control = ControlStore(cid, root=home / cid)
    control.write_manifest(mani)
    eng, _ = build_fake_engine(control, mani, clock=clock, worker_concurrency=1, plan_max_theses=2)
    control.stop_file.write_text("external kill switch")   # armed before start
    stop = eng.run(max_ticks=50)
    return {"campaign_id": cid, "control": control, "stop_reason": stop}


def run_budget_proof(home):
    home = Path(home)
    clock = VClock()
    mani = _manifest(home, budgets={"planning_cycles_min": 1, "max_actions": 3}, cid_salt="budget")
    cid = mani["campaign_id"]
    control = ControlStore(cid, root=home / cid)
    control.write_manifest(mani)
    eng, _ = build_fake_engine(control, mani, clock=clock, worker_concurrency=1, plan_max_theses=3)
    stop = eng.run(max_ticks=200)
    return {"campaign_id": cid, "control": control, "stop_reason": stop}
