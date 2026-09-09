"""Scheduler: theses -> diverse, bounded work across the assigned twins.

Turns each open thesis into one or more bounded tasks, spreads them across the
campaign's twins (both twins participate), and deliberately schedules an
independent replica of a high-priority thesis on a DIFFERENT twin so a result is
corroborated by decorrelated work rather than trusted once. It never creates a
duplicate active task for the same (thesis, twin, kind), and it opens a
verification task — assigned away from the claimant — for every unverified
candidate. Task assignment is durable; leases (not the scheduler) guard
concurrency.
"""
from .store import utc

TASK_DEFAULTS = {"max_actions": 30, "max_duration_s": 600}
REPLICATE_PRIORITY_MIN = 3          # theses at/above this get an independent replica
MAX_TASK_ATTEMPTS = 3


class Scheduler:
    def __init__(self, control, cells, bounds=None):
        self.control = control
        self.cells = cells
        self.bounds = dict(TASK_DEFAULTS)
        self.bounds.update(bounds or {})

    # -- twin assignment (durable round-robin so both twins get work) ---------
    def _twins(self, manifest):
        return [t["instance_id"] for t in manifest["twins"]]

    def _next_twin(self, manifest, avoid=None):
        twins = self._twins(manifest)
        st = self.control.control_state()
        cur = int(st.get("twin_cursor", 0))
        for _ in range(len(twins)):
            t = twins[cur % len(twins)]
            cur += 1
            if t != avoid:
                self.control.set_control_state(twin_cursor=cur)
                return t
        self.control.set_control_state(twin_cursor=cur)
        return twins[0]

    # -- dedup ----------------------------------------------------------------
    def _active_task_exists(self, thesis_id, twin, kind):
        for t in self.control.fold("tasks"):
            if (t.get("thesis_id") == thesis_id and t.get("twin") == twin
                    and t.get("kind") == kind
                    and t.get("status") in ("ready", "leased", "running", "requeued")):
                return True
        return False

    def _make_task(self, manifest, thesis, twin, kind, cell_id, replication_group=None,
                   replica_index=0, bounds=None, model_role="worker"):
        tid = self.control.next_id("tasks", "TASK")
        b = dict(self.bounds)
        b.update(bounds or {})
        self.control.put("tasks", {
            "task_id": tid, "thesis_id": thesis["thesis_id"], "twin": twin, "cell_id": cell_id,
            "kind": kind, "subject": thesis["subject"], "claim": thesis["claim"],
            "suggested_probes": thesis.get("suggested_probes") or [],
            "target_assets": thesis.get("target_assets"),
            "bounded": b, "priority": int(thesis.get("priority", 2)),
            "replication_group": replication_group or thesis["thesis_id"],
            "replica_index": replica_index, "model_role": model_role,
            "status": "ready", "attempts": 0, "created_at": utc(),
        })
        self.control.event("task_created", task_id=tid, thesis_id=thesis["thesis_id"],
                           twin=twin, task_kind=kind, cell_id=cell_id, replica_index=replica_index)
        return tid

    # -- scheduling passes ----------------------------------------------------
    def schedule_theses(self, manifest, max_new=12):
        created = []
        open_theses = [t for t in self.control.fold("theses") if t.get("status") == "open"]
        open_theses.sort(key=lambda t: (-int(t.get("priority", 2)), t["thesis_id"]))
        for th in open_theses:
            if len(created) >= max_new:
                break
            twin = self._next_twin(manifest)
            cell_id = self._ensure_cell(twin, th)
            if not self._active_task_exists(th["thesis_id"], twin, "probe"):
                created.append(self._make_task(manifest, th, twin, "probe", cell_id, replica_index=0))
            # deliberate independent replication on a different twin
            if int(th.get("priority", 2)) >= REPLICATE_PRIORITY_MIN and len(self._twins(manifest)) > 1:
                twin2 = self._next_twin(manifest, avoid=twin)
                cell2 = self._ensure_cell(twin2, th)
                if not self._active_task_exists(th["thesis_id"], twin2, "probe"):
                    created.append(self._make_task(manifest, th, twin2, "probe", cell2,
                                   replica_index=1, model_role="worker"))
            self.control.update("theses", th["thesis_id"], status="scheduled")
        return created

    def _ensure_cell(self, twin, thesis):
        # one cell per (twin, family-ish focus): group by subject's context/family
        focus = thesis.get("subject_kind") or "node"
        focus_key = "%s:%s" % (twin, thesis.get("target_assets", [{}])[0].get("kind", focus))
        for c in self.cells.for_twin(twin):
            if c.get("focus") == focus_key:
                self.cells.attach_thesis(c["cell_id"], thesis["thesis_id"])
                return c["cell_id"]
        cid = self.cells.create(twin, focus_key, theses=[thesis["thesis_id"]])
        return cid

    def schedule_verifications(self, manifest, router):
        """One verification task per unverified candidate, on the candidate's own
        twin, flagged so the runtime assigns a DIFFERENT worker and a decorrelated
        model than the claimant."""
        created = []
        cands = self.control.fold("candidates")
        existing = {t.get("verifies_candidate") for t in self.control.fold("tasks")
                    if t.get("kind") == "verify"}
        for c in cands:
            if c.get("status") != "unverified" or c["candidate_id"] in existing:
                continue
            twin = c.get("twin")
            th = self.control.get("theses", c.get("thesis_id")) or {"thesis_id": c.get("thesis_id"),
                 "subject": None, "claim": c.get("claimed_outcome"), "priority": 3}
            cell_id = self._ensure_cell(twin, th)
            tid = self.control.next_id("tasks", "TASK")
            self.control.put("tasks", {
                "task_id": tid, "thesis_id": c.get("thesis_id"), "twin": twin, "cell_id": cell_id,
                "kind": "verify", "verifies_candidate": c["candidate_id"],
                "claimant_worker": c.get("worker_id"), "producer_model": c.get("producer_model"),
                "subject": th.get("subject"), "claim": th.get("claim"),
                "bounded": {"max_actions": 20, "max_duration_s": 600},
                "priority": 4, "replication_group": c["candidate_id"], "replica_index": 0,
                "model_role": "verifier", "status": "ready", "attempts": 0, "created_at": utc()})
            self.control.update("candidates", c["candidate_id"], status="verifying")
            self.control.event("verify_task_created", task_id=tid, candidate_id=c["candidate_id"],
                               twin=twin, claimant_worker=c.get("worker_id"))
            created.append(tid)
        return created

    # -- queue / requeue ------------------------------------------------------
    def ready_tasks(self):
        rt = [t for t in self.control.fold("tasks") if t.get("status") == "ready"]
        rt.sort(key=lambda t: (-int(t.get("priority", 2)), t.get("created_at", ""), t["task_id"]))
        return rt

    def mark(self, task_id, status, **fields):
        self.control.update("tasks", task_id, status=status, **fields)

    def requeue(self, task_id, reason="worker_failed"):
        t = self.control.get("tasks", task_id)
        attempts = int(t.get("attempts", 0)) + 1
        if attempts >= MAX_TASK_ATTEMPTS:
            self.control.update("tasks", task_id, status="blocked", attempts=attempts,
                                status_reason="max_attempts:%s" % reason)
            self.control.event("task_blocked", task_id=task_id, attempts=attempts, reason=reason)
            return False
        self.control.update("tasks", task_id, status="ready", attempts=attempts,
                            status_reason="requeued:%s" % reason)
        self.control.event("task_requeued", task_id=task_id, attempts=attempts, reason=reason)
        return True
