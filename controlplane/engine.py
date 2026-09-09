"""The campaign engine: the durable orchestration loop.

A single Director process drives cooperative worker units. Nothing authoritative
lives in memory: every tick is rebuilt from the durable ledgers, so a killed and
restarted Director resumes exactly where it left off. The loop, per tick:

  1. honour STOP (kill switch) and PAUSE;
  2. enforce budgets from the ledgers;
  3. recover expired task leases -> requeue (worker-failure recovery);
  4. health-check each assigned twin; recover/reproduce a failed twin and
     reallocate its cells + requeue its tasks (twin-failure recovery);
  5. plan: the Director generates/updates theses from archkit;
  6. schedule: theses -> bounded tasks across both twins, + verification tasks;
  7. dispatch ready tasks under fresh leases to workers (in-process or
     subprocess); record results; requeue on failure; verify candidates with a
     decorrelated, non-claimant verifier;
  8. retire exhausted cells; checkpoint; record budget.

Workers act only through the typed broker on their assigned twin. Model and tool
activity, leases, handoffs, recoveries, checkpoints — all durable.
"""
import time

from red_loop.leases import LeaseManager

from .store import ControlStore, utc
from .director import Director
from .cells import CellManager
from .scheduler import Scheduler
from .verify import Verifier
from .budget import Budget


class Engine:
    def __init__(self, control, manifest, world, router, twin_pool, client_factory,
                 executor=None, replay_fn=None, judge_fn=None, clock=time.time,
                 lease_ttl=120, worker_concurrency=2, plan_max_theses=8,
                 director_client_factory=None):
        self.control = control
        self.manifest = manifest
        self.world = world
        self.router = router
        self.twin_pool = twin_pool
        self.client_factory = client_factory
        self.clock = clock
        self.lease_ttl = lease_ttl
        self.worker_concurrency = worker_concurrency
        self.plan_max_theses = plan_max_theses
        self.leases = LeaseManager(control, clock=clock)
        self.cells = CellManager(control)
        self.scheduler = Scheduler(control, self.cells)
        self.director = Director(control, world, router,
                                 client_factory=director_client_factory)
        self.verifier = Verifier(control, router, replay_fn=replay_fn, judge_fn=judge_fn)
        self.budget = Budget(control, manifest, clock=clock)
        self.executor = executor or InProcessExecutor(self)
        # active twin id -> possibly-replaced current instance id
        self._twin_current = {t["instance_id"]: t["instance_id"] for t in manifest["twins"]}

    # ---------------------------------------------------------------- lifecycle
    def start(self):
        st = self.control.control_state()
        if not st.get("started_epoch"):
            self.control.set_control_state(phase="running", started_epoch=self.clock(),
                                           started_at=utc())
            for t in self.manifest["twins"]:
                self.control.put("twins", {"instance_id": t["instance_id"],
                                 "role": t.get("role"), "profile": t.get("profile"),
                                 "seed": t.get("seed"),
                                 "starting_access_profile": t.get("starting_access_profile"),
                                 "status": "assigned", "current_instance_id": t["instance_id"]})
            self.control.event("campaign_started", twins=[t["instance_id"] for t in self.manifest["twins"]])
        else:
            self.control.set_control_state(phase="running")
            self.control.event("campaign_resumed", resume_from=st.get("phase"),
                               state_hash=self.control.state_hash())
        return self.control.control_state()

    def pause(self):
        self.control.pause_file.write_text(utc())
        self.control.set_control_state(phase="paused")
        self.control.checkpoint("paused")
        self.control.event("campaign_paused", state_hash=self.control.state_hash())

    def resume(self):
        if self.control.pause_file.exists():
            self.control.pause_file.unlink()
        # recover any lease that expired while paused/dead
        freed = self.leases.recover_on_resume(now=self.clock())
        for lease in freed:
            self._requeue_for_lease(lease, reason="resume_recovery")
        self.control.set_control_state(phase="running")
        self.control.event("campaign_resumed", freed_leases=len(freed),
                           state_hash=self.control.state_hash())
        return self.control.control_state()

    def drain(self):
        """Stop scheduling NEW work; let in-flight tasks and pending verifications
        finish, then complete."""
        self.control.set_control_state(phase="draining")
        self.control.event("campaign_draining")
        return self.run(drain=True)

    def terminate(self, reason="operator_terminate"):
        self.control.stop_file.write_text(reason)
        self.control.set_control_state(phase="terminated", terminated_reason=reason)
        self.control.checkpoint("terminated")
        self.control.event("campaign_terminated", reason=reason)

    # ---------------------------------------------------------------- main loop
    def run(self, max_ticks=100000, drain=False, tick_sleep=0):
        self.start()
        stop_reason = "completed"
        for tick in range(max_ticks):
            if self.control.stop_requested():
                stop_reason = "kill_switch"
                break
            if self.control.pause_requested():
                stop_reason = "paused"
                break
            ok, breason, cons = self.budget.check()
            if not ok:
                stop_reason = breason
                break
            self._recover_leases()
            self._recover_twins()
            phase = self.control.control_state().get("phase")
            draining = drain or phase == "draining"
            if not draining:
                self._plan_if_needed()
                self.scheduler.schedule_theses(self.manifest, max_new=12)
            self.scheduler.schedule_verifications(self.manifest, self.router)
            dispatched = self._dispatch_ready()
            self.cells.retire_exhausted()
            self.director.update_theses(self.manifest)
            if tick % 5 == 0:
                self.control.checkpoint("tick", extra={"tick": tick})
                self.budget.record("tick")
            if not dispatched and not self._has_open_work(draining):
                stop_reason = "no_open_work" if not draining else "drained"
                break
            if tick_sleep:
                time.sleep(tick_sleep)
        self._finalize(stop_reason)
        return stop_reason

    def _finalize(self, stop_reason):
        self.budget.record("final")
        self.control.set_control_state(phase="completed", stop_reason=stop_reason,
                                       finished_at=utc())
        self.control.checkpoint("final", extra={"stop_reason": stop_reason})
        self.control.event("campaign_completed", stop_reason=stop_reason,
                           consumption=self.budget.consumption())

    # ---------------------------------------------------------------- planning
    def _plan_if_needed(self):
        st = self.control.control_state()
        cycles = int(st.get("planning_cycles", 0))
        min_cycles = int(self.manifest.get("budgets", {}).get("planning_cycles_min", 3))
        ready = [t for t in self.control.fold("tasks") if t.get("status") == "ready"]
        open_theses = [t for t in self.control.fold("theses") if t.get("status") == "open"]
        # plan when under the minimum, or when there is little work queued
        if cycles < min_cycles or (not ready and not open_theses):
            self.director.plan_cycle(self.manifest, max_theses=self.plan_max_theses)

    # ---------------------------------------------------------------- dispatch
    def _dispatch_ready(self):
        ready = self.scheduler.ready_tasks()
        n = 0
        for task in ready[: self.worker_concurrency]:
            self._run_one(task)
            n += 1
        return n

    def _run_one(self, task):
        task_id = task["task_id"]
        worker_id = self.control.next_id("workers", "W")
        twin = self._twin_current.get(task["twin"], task["twin"])
        self.control.put("workers", {"worker_id": worker_id, "task_id": task_id,
                         "twin": twin, "cell_id": task.get("cell_id"),
                         "kind": task.get("kind"), "status": "leased", "started_at": utc()})
        try:
            lease = self.leases.acquire(task_id, owner=worker_id, ttl=self.lease_ttl)
        except Exception as e:  # noqa: BLE001  (already leased -> skip this tick)
            self.control.event("lease_conflict", task_id=task_id, detail=str(e)[:200])
            return
        self.scheduler.mark(task_id, "running", worker_id=worker_id, lease_id=lease["lease_id"])
        try:
            handle = self.twin_pool.handle(twin)
        except Exception as e:  # noqa: BLE001
            self.control.event("twin_handle_failed", twin=twin, detail=str(e)[:200])
            self.leases.release(lease["lease_id"], reason="twin_unavailable")
            self.scheduler.requeue(task_id, reason="twin_unavailable")
            return
        try:
            result = self.executor.run(task, handle, worker_id)
        except WorkerAbandoned as e:
            # a crashed worker leaves its lease to expire; recovery on a later tick
            self.control.update("workers", worker_id, status="crashed", detail=str(e)[:200])
            self.control.event("worker_crashed", worker_id=worker_id, task_id=task_id, detail=str(e)[:200])
            return
        except Exception as e:  # noqa: BLE001
            self.control.update("workers", worker_id, status="error", detail=str(e)[:200])
            self.leases.release(lease["lease_id"], reason="worker_error")
            self.scheduler.requeue(task_id, reason="worker_error")
            return
        self._on_result(task, worker_id, lease, result)

    def _on_result(self, task, worker_id, lease, result):
        task_id = task["task_id"]
        stop_reason = (result or {}).get("stop_reason")
        self.control.update("workers", worker_id, status="done",
                            stop_reason=stop_reason, finished_at=utc())
        if stop_reason in ("model_unusable",):
            self.leases.release(lease["lease_id"], reason="model_unusable")
            self.scheduler.requeue(task_id, reason="model_unusable")
            return
        if stop_reason == "paused":
            self.leases.release(lease["lease_id"], reason="paused")
            self.scheduler.mark(task_id, "ready")     # resume will pick it up
            return
        self.leases.release(lease["lease_id"], reason="completed")
        self.scheduler.mark(task_id, "done", worker_id=worker_id,
                            result_stop_reason=stop_reason,
                            candidates=(result or {}).get("candidates"),
                            hypotheses=(result or {}).get("hypotheses"))
        if task.get("kind") == "verify":
            # verification result already written by the executor's verify path
            pass

    # ---------------------------------------------------------------- recovery
    def _recover_leases(self):
        freed = self.leases.reap_expired(now=self.clock())
        for lease in freed:
            self._requeue_for_lease(lease, reason="lease_expired")

    def _requeue_for_lease(self, lease, reason):
        task_id = lease.get("hypothesis_id")   # LeaseManager keys leases by this field
        t = self.control.get("tasks", task_id)
        if t and t.get("status") in ("running", "leased", "requeued"):
            self.scheduler.requeue(task_id, reason=reason)
            self.control.event("task_recovered", task_id=task_id, reason=reason,
                               owner=lease.get("owner"))

    def _recover_twins(self):
        for t in self.manifest["twins"]:
            base = t["instance_id"]
            cur = self._twin_current.get(base, base)
            try:
                h = self.twin_pool.health(cur)
            except Exception as e:  # noqa: BLE001
                h = {"healthy": False, "error": str(e)[:200]}
            if h.get("healthy"):
                continue
            self.control.event("twin_unhealthy", instance_id=cur, detail=h)
            handle = self.twin_pool.recover(cur, store=self.control)
            new_id = handle.instance_id
            if new_id != cur:
                self._twin_current[base] = new_id
                self.control.update("twins", base, status="recovered",
                                    current_instance_id=new_id, replaced=cur)
                moved = self.cells.reallocate(cur, new_id, reason="twin_reproduced")
                # requeue any active task on the failed twin to the replacement
                for task in self.control.fold("tasks"):
                    if task.get("twin") == cur and task.get("status") in ("ready", "running", "leased", "requeued"):
                        self.control.update("tasks", task["task_id"], twin=new_id)
                        if task.get("status") != "ready":
                            self.scheduler.requeue(task["task_id"], reason="twin_reproduced")
                self.control.event("twin_recovered", frm=cur, to=new_id, moved_cells=len(moved))
            else:
                self.control.update("twins", base, status="restarted", current_instance_id=new_id)

    # ---------------------------------------------------------------- helpers
    def _has_open_work(self, draining):
        tasks = self.control.fold("tasks")
        pending = [t for t in tasks if t.get("status") in ("ready", "running", "leased", "requeued")]
        if pending:
            return True
        if draining:
            return False
        open_theses = [t for t in self.control.fold("theses") if t.get("status") in ("open", "scheduled")]
        # more planning possible only under the minimum cycle floor
        st = self.control.control_state()
        cycles = int(st.get("planning_cycles", 0))
        min_cycles = int(self.manifest.get("budgets", {}).get("planning_cycles_min", 3))
        unverified = [c for c in self.control.fold("candidates") if c.get("status") in ("unverified", "verifying")]
        return bool(open_theses) or cycles < min_cycles or bool(unverified)


class WorkerAbandoned(RuntimeError):
    """Raised by an executor to model a worker that died without releasing its
    lease (recovered via lease expiry, not immediate requeue)."""


class InProcessExecutor:
    """Runs the worker (and verification) in the Director process. Used by the
    deterministic tests and the model-free proof; the live engine uses the
    subprocess executor for true per-twin process isolation."""

    def __init__(self, engine):
        self.engine = engine

    def run(self, task, handle, worker_id):
        from . import worker as worker_mod
        eng = self.engine
        broker = eng.twin_pool.broker_for(handle) if hasattr(eng.twin_pool, "broker_for") else None
        if task.get("kind") == "verify":
            eng.verifier.verify_candidate(task, twin_handle=handle, worker_id=worker_id)
            return {"task_id": task["task_id"], "worker_id": worker_id, "stop_reason": "verified_task",
                    "actions": 0, "candidates": [], "hypotheses": []}
        return worker_mod.run_task(eng.control, task, eng.manifest, eng.world, broker,
                                   eng.client_factory, eng.router, handle,
                                   clock=eng.clock, worker_id=worker_id, cell_id=task.get("cell_id"))
