"""Builders that assemble a LIVE engine against real M9 twins and (optionally)
the approved model gateway. The deterministic path in ``fakes`` mirrors this so
tests and the model-free proof exercise identical engine code.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import paths
from .context import WorldModel
from .engine import Engine, WorkerAbandoned
from .models import ModelRouter
from .twins import FactoryTwinPool


def build_world(snapshot_id=None):
    from archkit.store import SnapshotStore
    from archkit.query import Query
    store = SnapshotStore()
    sid = snapshot_id or store.current_id()
    return WorldModel(Query(store, sid)), sid


def live_client_factory(model):
    from red_loop import llm
    return llm.ChatClient(model)


def fake_client_factory_for(find_candidate_for=None):
    from .fakes import fake_client_factory
    return fake_client_factory(find_candidate_for=find_candidate_for)


class SubprocessExecutor:
    """Runs each worker as a twin-scoped subprocess (real per-twin isolation).
    Verify tasks run through the engine's injected verifier (twin-scoped judge)."""

    def __init__(self, engine, fake_model=False, fake_finders=None, fake_broker=None,
                 python=None):
        self.engine = engine
        self.fake_model = fake_model
        self.fake_finders = fake_finders or []
        self.fake_broker = fake_broker or {}
        self.python = python or sys.executable
        self.procs = {}     # worker_id -> Popen (for external kill injection)

    def run(self, task, handle, worker_id):
        eng = self.engine
        if task.get("kind") == "verify":
            eng.verifier.verify_candidate(task, twin_handle=handle, worker_id=worker_id)
            return {"task_id": task["task_id"], "worker_id": worker_id,
                    "stop_reason": "verified_task", "actions": 0,
                    "candidates": [], "hypotheses": []}
        # precompute the archkit packet so the worker subprocess stays archkit-free
        packet = eng.world.worker_packet(task.get("subject")) if eng.world else {"sections": {}}
        wdir = eng.control.workers_dir / worker_id
        wdir.mkdir(parents=True, exist_ok=True)
        (wdir / "task.json").write_text(json.dumps({"task": task, "packet": packet}, default=str))

        env = dict(os.environ)
        env.update(handle.env or {})
        env["CAMPAIGN_CONTROL_HOME"] = str(paths.HOME)
        env["PYTHONPATH"] = os.pathsep.join([str(paths.REPO), str(paths.RED_LOOP),
                                             env.get("PYTHONPATH", "")])
        if self.fake_model:
            env["CONTROLPLANE_FAKE_MODEL"] = "1"
            env["CONTROLPLANE_FAKE_FINDERS"] = ",".join(self.fake_finders)
            env["CONTROLPLANE_FAKE_BROKER"] = json.dumps(self.fake_broker)
        cmd = [self.python, "-m", "controlplane.worker_main",
               eng.control.campaign_id, worker_id]
        proc = subprocess.Popen(cmd, cwd=str(paths.REPO), env=env)
        self.procs[worker_id] = proc
        proc.wait()
        self.procs.pop(worker_id, None)
        result_path = wdir / "result.json"
        if proc.returncode != 0 or not result_path.exists():
            # killed / crashed without writing a result -> lease expiry recovers it
            raise WorkerAbandoned("worker %s exited rc=%s without result" % (worker_id, proc.returncode))
        return json.loads(result_path.read_text())


def make_live_verification(twin_pool, python=None):
    """replay_fn + judge_fn that run in twin-scoped subprocesses (verify_main).
    Best-effort: on any error the verdict is inconclusive, so a candidate is
    never promoted without genuine corroboration."""
    python = python or sys.executable

    def _run(cand, handle, verifier_model):
        env = dict(os.environ)
        env.update(handle.env or {})
        env["PYTHONPATH"] = os.pathsep.join([str(paths.REPO), str(paths.RED_LOOP), env.get("PYTHONPATH", "")])
        payload = {"candidate": cand, "verifier_model": verifier_model,
                   "twin": handle.instance_id}
        try:
            p = subprocess.run([python, "-m", "controlplane.verify_main"],
                               input=json.dumps(payload), text=True, capture_output=True,
                               cwd=str(paths.REPO), env=env, timeout=1800)
            return json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)[:200]}

    def replay_fn(cand, handle, verifier_model, verifier_worker):
        out = _run(cand, handle, verifier_model)
        return {"reproduced": out.get("reproduced"), "verifier_model": verifier_model,
                "verifier_worker": verifier_worker, "detail": out.get("replay_detail"),
                "error": out.get("error")}

    def judge_fn(cand, handle):
        out = _run(cand, handle, None)
        return {"verdict": out.get("verdict", "inconclusive"), "basis": out.get("judge_basis"),
                "error": out.get("error")}

    return replay_fn, judge_fn


def build_engine(control, manifest, snapshot_id=None, fake_model=False, fake_finders=None,
                 fake_broker=None, worker_concurrency=2, lease_ttl=180,
                 plan_max_theses=8, director_model=False, clock=time.time):
    world, sid = build_world(snapshot_id or manifest.get("architecture_snapshot_id"))
    router = ModelRouter(manifest["model_pool"], store=control)
    pool = FactoryTwinPool()
    dcf = (live_client_factory if director_model else None)
    replay_fn, judge_fn = make_live_verification(pool)
    eng = Engine(control, manifest, world, router, pool,
                 client_factory=(fake_client_factory_for(fake_finders) if fake_model else live_client_factory),
                 replay_fn=replay_fn, judge_fn=judge_fn, clock=clock,
                 lease_ttl=lease_ttl, worker_concurrency=worker_concurrency,
                 plan_max_theses=plan_max_theses, director_client_factory=dcf)
    eng.executor = SubprocessExecutor(eng, fake_model=fake_model, fake_finders=fake_finders,
                                      fake_broker=fake_broker)
    return eng
