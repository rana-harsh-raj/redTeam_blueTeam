"""Read-only campaign views: narrative, coverage, allocation, cost.

All derived from the durable ledgers, so they are correct after any restart and
require no live model or Docker.
"""
from collections import Counter, defaultdict


class Views:
    def __init__(self, control, manifest=None):
        self.control = control
        self.manifest = manifest or control.read_manifest()

    # -- narrative ------------------------------------------------------------
    def narrative(self, limit=200):
        """A time-ordered story of the campaign's decisions (planning cycles,
        theses, cells, recoveries, verifications) — not the raw model chatter."""
        keep = {"campaign_started", "planning_cycle", "thesis_created", "cell_created",
                "task_created", "candidate_claimed", "verify_task_created",
                "verification_decided", "twin_unhealthy", "twin_recovered",
                "task_recovered", "lease_expired", "model_fallback", "cell_reallocated",
                "campaign_paused", "campaign_resumed", "campaign_terminated",
                "campaign_completed", "cell_retired", "thesis_exhausted"}
        out = []
        for e in self.control._read_all("events"):
            if e.get("event") in keep:
                out.append({"seq": e.get("seq"), "ts": e.get("ts"), "event": e.get("event"),
                            **{k: v for k, v in e.items()
                               if k not in ("seq", "ts", "event")}})
        out.sort(key=lambda r: r.get("seq") or 0)
        return out[-limit:]

    # -- coverage -------------------------------------------------------------
    def coverage(self):
        theses = self.control.fold("theses")
        tasks = self.control.fold("tasks")
        hyps = self.control.fold("hypotheses_v2")
        cands = self.control.fold("candidates")
        subjects = {t["subject"] for t in theses}
        by_status = Counter(t.get("status") for t in tasks)
        return {
            "snapshot_id": self.manifest.get("architecture_snapshot_id"),
            "planning_cycles": self.control.control_state().get("planning_cycles"),
            "theses_total": len(theses),
            "theses_by_status": dict(Counter(t.get("status") for t in theses)),
            "distinct_subjects": len(subjects),
            "subjects": sorted(subjects),
            "tasks_total": len(tasks),
            "tasks_by_status": dict(by_status),
            "tasks_by_kind": dict(Counter(t.get("kind") for t in tasks)),
            "hypotheses_total": len(hyps),
            "hypotheses_by_state": dict(Counter(h.get("status") for h in hyps)),
            "candidates_total": len(cands),
            "candidates_by_status": dict(Counter(c.get("status") for c in cands)),
            "duplicate_suppressed": sum(1 for e in self.control._read_all("events")
                                        if e.get("event") in ("duplicate_suppressed", "thesis_duplicate_suppressed")),
        }

    # -- allocation -----------------------------------------------------------
    def allocation(self):
        tasks = self.control.fold("tasks")
        cells = self.control.fold("cells")
        by_twin = defaultdict(lambda: {"tasks": 0, "cells": 0, "candidates": 0})
        for t in tasks:
            by_twin[t.get("twin")]["tasks"] += 1
        for c in cells:
            by_twin[c.get("twin")]["cells"] += 1
        for c in self.control.fold("candidates"):
            by_twin[c.get("twin")]["candidates"] += 1
        twins = self.control.fold("twins")
        return {
            "twins": [{"instance_id": t["instance_id"],
                       "current_instance_id": t.get("current_instance_id"),
                       "status": t.get("status"),
                       "starting_access_profile": t.get("starting_access_profile"),
                       "allocation": dict(by_twin.get(t["instance_id"], {}))} for t in twins],
            "cells_total": len(cells),
            "cells_by_status": dict(Counter(c.get("status") for c in cells)),
            "replication_groups": len({t.get("replication_group") for t in tasks}),
            "independent_replicas": sum(1 for t in tasks if int(t.get("replica_index", 0)) > 0),
        }

    # -- cost -----------------------------------------------------------------
    def cost(self):
        from .budget import Budget
        b = Budget(self.control, self.manifest)
        c = b.consumption()
        # token/action/model-call totals are clock-independent (from the ledgers);
        # wall time is authoritative only from the engine's OWN clock at run time,
        # so prefer the last durable budget snapshot (avoids a wall-clock artifact
        # when the campaign ran on a virtual clock in the deterministic proof).
        recs = self.control._read_all("budget")
        if recs:
            last = recs[-1]
            c["wall_seconds"] = last.get("wall_seconds", c["wall_seconds"])
            c["estimated_usd"] = last.get("estimated_usd", c["estimated_usd"])
        bud = self.manifest.get("budgets", {})
        reason = "within_budget"
        if bud.get("max_actions") is not None and c["actions"] >= bud["max_actions"]:
            reason = "action_budget_exhausted"
        elif bud.get("max_model_calls") is not None and c["model_calls"] >= bud["max_model_calls"]:
            reason = "model_call_budget_exhausted"
        elif bud.get("max_wall_seconds") is not None and c["wall_seconds"] >= bud["max_wall_seconds"]:
            reason = "wall_budget_exhausted"
        elif bud.get("max_usd") is not None and c["estimated_usd"] >= bud["max_usd"]:
            reason = "cost_budget_exhausted"
        c["budget_status"] = reason
        c["budgets"] = bud
        return c

    # -- results --------------------------------------------------------------
    def results(self):
        cands = self.control.fold("candidates")
        vers = self.control.fold("verifications")
        by_status = Counter(c.get("status") for c in cands)
        return {
            "candidates_total": len(cands),
            "verified": by_status.get("verified", 0),
            "rejected": by_status.get("rejected", 0),
            "invalid": by_status.get("invalid", 0),
            "unverified_or_pending": by_status.get("unverified", 0) + by_status.get("verifying", 0),
            "verifications": len(vers),
            "decorrelated_verifications": sum(1 for v in vers if v.get("decorrelated")),
            "self_verifications": sum(1 for v in vers if v.get("verifier_worker") == v.get("claimant_worker")),
            "duplicate_hypotheses_suppressed": sum(1 for e in self.control._read_all("events")
                                                   if e.get("event") == "duplicate_suppressed"),
        }
