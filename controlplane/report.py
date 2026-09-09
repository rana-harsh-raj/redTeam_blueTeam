"""Renderers for the campaign timeline and the final report.

Everything is read from the durable ledgers, so a report is reproducible from a
completed campaign directory with no live model or Docker.
"""
import json
import time
from pathlib import Path

from . import paths
from .views import Views

IMPL = paths.REPO / "reports" / "implementation"
M10_DIR = IMPL / "m10"


def _ensure():
    M10_DIR.mkdir(parents=True, exist_ok=True)


def write_timeline(control, manifest, out=None):
    _ensure()
    v = Views(control, manifest)
    timeline = {
        "campaign_id": control.campaign_id,
        "manifest_content_id": manifest.get("manifest_content_id"),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "phases": [c for c in _phase_markers(control)],
        "events": v.narrative(limit=100000),
        "checkpoints": [json.loads(p.read_text()) for p in sorted(control.checkpoints_dir.glob("ckpt-*.json"))],
    }
    out = Path(out) if out else (M10_DIR / ("%s-timeline.json" % control.campaign_id))
    out.write_text(json.dumps(timeline, indent=2, default=str))
    return out


def _phase_markers(control):
    for e in control._read_all("events"):
        if e.get("event") in ("campaign_started", "campaign_paused", "campaign_resumed",
                              "campaign_draining", "campaign_terminated", "campaign_completed"):
            yield {"seq": e.get("seq"), "ts": e.get("ts"), "phase": e.get("event")}


def write_report(control, manifest, out=None):
    _ensure()
    v = Views(control, manifest)
    cov, alloc, cost, res = v.coverage(), v.allocation(), v.cost(), v.results()
    recoveries = control._read_all("recoveries")
    ev = control._read_all("events")

    def count(evt):
        return sum(1 for e in ev if e.get("event") == evt)

    recovery_events = {
        "worker_failures_recovered": count("task_recovered"),
        "lease_expiries": count("lease_expired"),
        "director_restarts": count("campaign_resumed"),
        "model_fallbacks": count("model_fallback"),
        "twin_recoveries": count("twin_recovered"),
        "twins_reproduced": sum(1 for r in recoveries if r.get("kind") == "reproduce_ok"),
    }
    tool_usage = _histogram(control._read_all("tool_calls"), "tool")
    model_usage = cost.get("by_model", {})
    st = control.control_state()

    lines = []
    w = lines.append
    w("# M10 — Durable Autonomous Campaign Control Plane — Final Report")
    w("")
    w("Generated %s" % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    w("")
    w("## Campaign")
    w("- campaign_id: `%s`" % control.campaign_id)
    w("- manifest_content_id: `%s`" % manifest.get("manifest_content_id"))
    w("- mode: `%s`" % manifest.get("mode"))
    w("- architecture snapshot: `%s`" % manifest.get("architecture_snapshot_id"))
    w("- mandate: %s" % manifest.get("mandate"))
    w("- phase: `%s`  stop_reason: `%s`" % (st.get("phase"), st.get("stop_reason")))
    w("- final state_hash: `%s`" % control.state_hash())
    w("")
    w("## Twins")
    for t in alloc["twins"]:
        w("- `%s` (now `%s`, %s) access=`%s` — tasks=%d cells=%d candidates=%d"
          % (t["instance_id"], t.get("current_instance_id"), t.get("status"),
             t.get("starting_access_profile"), t["allocation"].get("tasks", 0),
             t["allocation"].get("cells", 0), t["allocation"].get("candidates", 0)))
    w("")
    w("## Duration and actions")
    w("- wall_seconds: %s" % cost.get("wall_seconds"))
    w("- model_calls: %s   actions(tool_calls): %s" % (cost.get("model_calls"), cost.get("actions")))
    w("- tokens: prompt=%s completion=%s   estimated_usd=%s"
      % (cost.get("prompt_tokens"), cost.get("completion_tokens"), cost.get("estimated_usd")))
    w("- budget_status: `%s`" % cost.get("budget_status"))
    w("")
    w("## Theses, cells, hypotheses")
    w("- planning_cycles: %s" % cov.get("planning_cycles"))
    w("- theses: %d %s" % (cov["theses_total"], cov["theses_by_status"]))
    w("- distinct subjects: %d" % cov["distinct_subjects"])
    w("- cells: %d %s   reallocated/retired tracked in ledger" % (alloc["cells_total"], alloc["cells_by_status"]))
    w("- hypotheses: %d %s" % (cov["hypotheses_total"], cov["hypotheses_by_state"]))
    w("- independent replicas scheduled: %d   replication groups: %d"
      % (alloc["independent_replicas"], alloc["replication_groups"]))
    w("")
    w("## Recovery events")
    for k, val in recovery_events.items():
        w("- %s: %d" % (k, val))
    w("")
    w("## Tool and model usage")
    w("- tools: %s" % json.dumps(tool_usage))
    w("- models: %s" % json.dumps(model_usage))
    w("")
    w("## Results (candidates and verification)")
    w("- candidates: %d  by_status=%s" % (cov["candidates_total"], cov["candidates_by_status"]))
    w("- verified: %d   rejected: %d   invalid: %d   pending: %d"
      % (res["verified"], res["rejected"], res["invalid"], res["unverified_or_pending"]))
    w("- verifications: %d (decorrelated=%d, self_verifications=%d)"
      % (res["verifications"], res["decorrelated_verifications"], res["self_verifications"]))
    w("- duplicate hypotheses suppressed: %d" % res["duplicate_hypotheses_suppressed"])
    w("- invalid/duplicate candidates: invalid=%d, duplicate-suppressed(theses)=%d"
      % (res["invalid"], cov.get("duplicate_suppressed", 0)))
    w("")
    w("## Human intervention")
    w("- human task assignments: 0 (the human set only mode, budgets, policy and the broad mandate)")
    w("- operator control actions (pause/resume/drain/terminate): %d"
      % (count("campaign_paused") + count("campaign_resumed") + count("campaign_draining") + count("campaign_terminated")))
    w("")
    w("## Remaining work before the capability / evidence plane")
    w("- a generalized capability graph and a cross-campaign finding registry (out of M10 scope);")
    w("- an executive dashboard over campaigns (out of M10 scope);")
    w("- scale beyond two twins to ten (deferred by the brief).")
    w("")
    body = "\n".join(lines)
    out = Path(out) if out else (IMPL / "M10_FINAL_REPORT.md")
    out.write_text(body)
    # also drop machine-readable views next to the report
    (M10_DIR / ("%s-views.json" % control.campaign_id)).write_text(json.dumps(
        {"coverage": cov, "allocation": alloc, "cost": cost, "results": res,
         "recovery_events": recovery_events, "tool_usage": tool_usage,
         "model_usage": model_usage, "control_state": st}, indent=2, default=str))
    return out


def _histogram(rows, key):
    from collections import Counter
    return dict(Counter(r.get(key) for r in rows))
