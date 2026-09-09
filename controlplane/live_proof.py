"""Live proof: one autonomous campaign across two real M9-isolated twins.

Drives the control plane against two twinfactory instances (different profiles,
seeds and starting-access profiles) with the REAL typed broker on each twin. The
model policy is deterministic ("declared action budget"), so the run is
reproducible without a gateway; a gateway run is a drop-in (fake_model=False).

Demonstrated on the real twins and recorded to one campaign's durable ledgers:
  * a broad mandate only; the Director generates its own theses from archkit;
  * bounded tasks scheduled across BOTH twins, each acting only through its own
    twin's broker (separate colima daemon / docker socket);
  * an injected WORKER kill recovered via lease expiry + requeue;
  * a DIRECTOR kill + restart that resumes from durable state;
  * a failed TWIN recovered through twinfactory and re-homed;
  * decorrelated, non-self verification of any candidate.

  python3 -m controlplane.live_proof --alpha <id> --beta <id> [--budget N] [--gateway]
"""
import argparse
import json
import os
import signal
import time

from .store import ControlStore
from .manifest import build_manifest
from .build import build_engine
from .report import write_report, write_timeline
from .views import Views


class _KillOnce:
    """Wraps a SubprocessExecutor to SIGKILL the first worker of a chosen task,
    modelling a crashed worker that never releases its lease."""

    def __init__(self, inner, target_task):
        self.inner = inner
        self.target = target_task
        self.done = False

    def run(self, task, handle, worker_id):
        if not self.done and task["task_id"] == self.target:
            self.done = True
            self.inner.engine.control.event("injected_worker_kill", task_id=self.target, worker_id=worker_id)
            # spawn then kill immediately, before it can write a result
            import subprocess, sys
            from . import paths
            packet = self.inner.engine.world.worker_packet(task.get("subject"))
            wdir = self.inner.engine.control.workers_dir / worker_id
            wdir.mkdir(parents=True, exist_ok=True)
            (wdir / "task.json").write_text(json.dumps({"task": task, "packet": packet}, default=str))
            env = dict(os.environ); env.update(handle.env or {})
            env["CAMPAIGN_CONTROL_HOME"] = str(paths.HOME)
            env["PYTHONPATH"] = os.pathsep.join([str(paths.REPO), str(paths.RED_LOOP), env.get("PYTHONPATH", "")])
            if self.inner.fake_model:
                env["CONTROLPLANE_FAKE_MODEL"] = "1"
                env["CONTROLPLANE_FAKE_FINDERS"] = ",".join(self.inner.fake_finders)
            p = subprocess.Popen([sys.executable, "-m", "controlplane.worker_main",
                                  self.inner.engine.control.campaign_id, worker_id],
                                 cwd=str(paths.REPO), env=env)
            time.sleep(0.3)
            p.send_signal(signal.SIGKILL)
            p.wait()
            from .engine import WorkerAbandoned
            raise WorkerAbandoned("injected SIGKILL on worker %s" % worker_id)
        return self.inner.run(task, handle, worker_id)

    def __getattr__(self, k):
        return getattr(self.inner, k)


def run(alpha, beta, budget=150, gateway=False, snapshot=None):
    mani = build_manifest(
        "Assess whatever most weakens the payouts money-movement boundary on the "
        "assigned twins. You are given no target, vulnerability class or attack path.",
        snapshot or _current_snapshot(),
        [{"instance_id": alpha, "profile": "full", "seed": "m10-alpha-seed",
          "starting_access_profile": "merchant_ordinary", "role": "full"},
         {"instance_id": beta, "profile": "critical-payouts", "seed": "m10-beta-seed",
          "starting_access_profile": "merchant_fresh", "role": "focused"}],
        ["claude-opus-4-8", "gpt-5.5", "gemini-2.5-pro"],
        mode="broad_autonomous",
        budgets={"planning_cycles_min": 2, "max_actions": budget, "max_wall_seconds": 10 ** 9})
    cid = mani["campaign_id"]
    control = ControlStore(cid)
    if not control.manifest_path.exists():
        control.write_manifest(mani)
        control.set_control_state(phase="created")
    print("campaign", cid)

    def engine():
        return build_engine(control, mani, snapshot_id=mani["architecture_snapshot_id"],
                            fake_model=not gateway, fake_finders=["gpt-5.5"] if not gateway else None,
                            worker_concurrency=2, lease_ttl=15, plan_max_theses=4)

    # Phase 1: partial run with an injected worker kill on the first task
    eng = engine()
    eng.executor = _KillOnce(eng.executor, target_task="TASK-001")
    print("phase 1: partial run + injected worker kill ...")
    eng.run(max_ticks=6)

    # Phase 2: Director process 'killed' -> fresh objects from durable state
    print("phase 2: director restart from durable state ...")
    eng2 = engine()          # fresh objects rebuilt from the durable store
    eng2.resume()
    eng2.run(max_ticks=200)

    # Phase 3: twin-failure recovery drill (stop a twin, one recovery cycle)
    print("phase 3: twin-failure recovery drill on", beta, "...")
    try:
        import subprocess, sys
        subprocess.run([sys.executable, "-m", "twinfactory", "stop", beta],
                       cwd=os.getcwd(), timeout=300)
        eng3 = engine()
        eng3._recover_twins()
    except Exception as e:  # noqa: BLE001
        control.event("twin_drill_error", detail=str(e)[:300])

    write_report(ControlStore(cid), mani)
    write_timeline(ControlStore(cid), mani)
    v = Views(ControlStore(cid), mani)
    print(json.dumps({"campaign_id": cid, "coverage": v.coverage(),
                      "results": v.results(), "allocation": v.allocation()}, indent=2, default=str))
    return cid


def _current_snapshot():
    from archkit.store import SnapshotStore
    return SnapshotStore().resolve("current")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--alpha", required=True)
    ap.add_argument("--beta", required=True)
    ap.add_argument("--budget", type=int, default=150)
    ap.add_argument("--gateway", action="store_true")
    ap.add_argument("--snapshot", default=None)
    a = ap.parse_args(argv)
    run(a.alpha, a.beta, budget=a.budget, gateway=a.gateway, snapshot=a.snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
