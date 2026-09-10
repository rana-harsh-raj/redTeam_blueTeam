"""Unattended soak coordinator (M4).

Loops over the context policies with periodic checkpoints, scenario rotation,
fingerprint duplicate suppression, stagnation detection, and automatic task
reassignment when a lease expires. Bounded by a wall-clock budget and a cycle
cap. Emits ``m4-direct-e2e-soak.json``.

Crucially it does NOT idle-loop: if a cycle surfaces no productive work and no
open hypotheses remain, it records ``no_productive_work`` and stops early and
honestly rather than burning budget.

A ``cycle_runner`` callable is injected so the soak is fully testable offline
(no gateway, no network).
"""
import json
import time
from pathlib import Path

from . import contexts
from .hypotheses import HypothesisManager, is_open, rollup_by_state
from .leases import LeaseManager


class StopReason:
    WALL_BUDGET = "wall_clock_budget_exhausted"
    CYCLE_CAP = "cycle_cap_reached"
    NO_PRODUCTIVE_WORK = "no_productive_work"
    STAGNATION = "stagnation"
    ALL_RESOLVED = "all_hypotheses_resolved"


class Soak:
    def __init__(self, store, cycle_runner, policies=None, clock=time.time):
        self.store = store
        self.cycle_runner = cycle_runner   # callable(policy, cycle_idx, ctx) -> dict
        self.policies = policies or contexts.POLICIES
        self._clock = clock
        self.hyp = HypothesisManager(store)
        self.leases = LeaseManager(store, clock=clock)

    def run(self, wall_seconds=None, max_cycles=None, checkpoint_every=1,
            stagnation_cycles=3, lease_ttl=120, out_path=None):
        t0 = self._clock()
        start = _utc(t0)
        stop_reason = None
        cycles = []
        checkpoints = 0
        duplicates_suppressed = 0
        experiments_executed = 0
        cycles_since_progress = 0
        last_hyp_count = len(self.hyp.all())
        cycle_idx = 0

        # recover + reassign any leases stranded by a prior kill / long gap
        resume_reassignments = self._reap_and_reassign(t0, lease_ttl)
        if resume_reassignments:
            self.store.event("leases_recovered_on_resume",
                             count=len(resume_reassignments))

        while True:
            now = self._clock()
            if wall_seconds is not None and (now - t0) >= wall_seconds:
                stop_reason = StopReason.WALL_BUDGET
                break
            if max_cycles is not None and cycle_idx >= max_cycles:
                stop_reason = StopReason.CYCLE_CAP
                break

            # reap + reassign expired leases before dispatching new work
            reassignments = self._reap_and_reassign(now, lease_ttl)

            policy = self.policies[cycle_idx % len(self.policies)]
            ctx = {"cycle": cycle_idx, "policy": policy.name,
                   "scenario": _scenario_for(cycle_idx)}
            result = dict(self.cycle_runner(policy, cycle_idx, ctx) or {})
            duplicates_suppressed += int(result.get("duplicates_suppressed") or 0)
            experiments_executed += int(result.get("experiments_executed") or 0)

            cycle_rec = {
                "cycle": cycle_idx, "policy": policy.name, "scenario": ctx["scenario"],
                "produced_work": bool(result.get("produced_work")),
                "new_hypotheses": int(result.get("new_hypotheses") or 0),
                "duplicates_suppressed": int(result.get("duplicates_suppressed") or 0),
                "experiments_executed": int(result.get("experiments_executed") or 0),
                "reassignments": reassignments,
            }
            cycles.append(cycle_rec)
            self.store._append("replans", {"kind": "soak_cycle", **cycle_rec, "ts": _utc()})

            if checkpoint_every and (cycle_idx % checkpoint_every == 0):
                self._checkpoint(cycle_idx, t0)
                checkpoints += 1

            # progress / stagnation accounting
            hyp_now = len(self.hyp.all())
            open_now = [h for h in self.hyp.all() if is_open(h)]
            made_progress = (hyp_now != last_hyp_count) or cycle_rec["produced_work"] \
                or cycle_rec["experiments_executed"] > 0
            last_hyp_count = hyp_now
            if made_progress:
                cycles_since_progress = 0
            else:
                cycles_since_progress += 1

            cycle_idx += 1

            # honest early stops
            if not open_now and not cycle_rec["produced_work"] and hyp_now > 0:
                stop_reason = StopReason.ALL_RESOLVED
                break
            if hyp_now == 0 and not cycle_rec["produced_work"] and cycles_since_progress >= 1:
                stop_reason = StopReason.NO_PRODUCTIVE_WORK
                self._record_replan("no_productive_work",
                                    tried=[c["policy"] for c in cycles],
                                    next_policy=None)
                break
            if cycles_since_progress >= stagnation_cycles:
                stop_reason = StopReason.STAGNATION
                self._record_replan("stagnation",
                                    tried=[c["policy"] for c in cycles],
                                    next_policy=None)
                break

        end_t = self._clock()
        summary = {
            "start": start, "end": _utc(end_t),
            "elapsed_seconds": int(end_t - t0),
            "contexts": [p.name for p in self.policies],
            "cycles": cycles,
            "turns": sum(int(c.get("experiments_executed") or 0) for c in cycles),
            "hypotheses_by_status": rollup_by_state(self.hyp.all()),
            "experiments_executed": experiments_executed,
            "leases": self.leases.summary(),
            "leases_expired": self.leases.summary()["expired"],
            "leases_reassigned": self.leases.summary()["reassigned"],
            "checkpoints": checkpoints,
            "duplicates_suppressed": duplicates_suppressed,
            "stop_reason": stop_reason,
        }
        out_path = Path(out_path) if out_path else (self.store.root / "m4-direct-e2e-soak.json")
        out_path.write_text(json.dumps(summary, indent=2, default=str))
        self.store.event("soak_end", stop_reason=stop_reason, cycles=len(cycles),
                         checkpoints=checkpoints)
        return summary

    # -- helpers ------------------------------------------------------------
    def _reap_and_reassign(self, now, lease_ttl):
        freed = self.leases.reap_expired(now=now)
        reassigned = []
        for lease in freed:
            hid = lease.get("hypothesis_id")
            rec = self.hyp.get(hid)
            if rec is None or not is_open(rec):
                continue
            new_owner = "soak:reassigned"
            handoff_id, _ = self.leases.reassign(
                hid, new_owner, ttl=lease_ttl, from_owner=lease.get("owner"),
                reason="expired_lease", next_best_action=rec.get("next_best_action"),
                state_at_handoff=rec.get("status"))
            reassigned.append({"hypothesis_id": hid, "handoff_id": handoff_id,
                               "from": lease.get("owner"), "to": new_owner})
        return reassigned

    def _checkpoint(self, cycle_idx, t0):
        self.store.update_manifest(
            soak_cycle=cycle_idx,
            soak_elapsed_seconds=int(self._clock() - t0),
            soak_hypotheses=len(self.hyp.all()),
            soak_state_hash=self.store.state_hash())

    def _record_replan(self, reason, tried, next_policy):
        self.store._append("replans", {
            "kind": "soak_replan", "reason": reason, "what_was_tried": tried,
            "next_policy": next_policy, "ts": _utc()})


_SCENARIOS = ["baseline", "identity_variation", "concurrency", "reversal_idempotency"]


def _scenario_for(idx):
    return _SCENARIOS[idx % len(_SCENARIOS)]


def _utc(ts=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
