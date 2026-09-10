"""M4 closing lifecycle gate + exporter.

The M3.1 acceptance gate never inspected hypotheses, so a campaign could close
"successfully" with a single ``proposed`` hypothesis and zero evidence. This
module closes that hole: a run cannot close ``successful`` while its
highest-priority hypothesis is still ``proposed``/``claimed`` (or any hypothesis
at the top priority tier lacks a terminal status). It also fails a ``blocked``
without a valid blocker enum and a ``supported`` unauthorized effect that lacks
an independent replay.

``lifecycle_gate(campaign_id)`` returns the same ``{gate, passed, ...}`` shape the
acceptance scripts use. ``export_lifecycle(...)`` writes
``m4-hypothesis-lifecycle.json``.
"""
import json
from pathlib import Path

from . import config
from .hypotheses import (
    HypothesisState as S, Blocker, Priority, TERMINAL_STATES,
    is_open, is_terminal, highest_priority_open, rollup_by_state, project_legacy,
    validate_falsification,
)
from .state import CampaignStore

_VALID_BLOCKERS = {b.value for b in Blocker}
NON_RESOLVED = {S.PROPOSED.value, S.CLAIMED.value}


def gate(name, passed, **d):
    return {"gate": name, "passed": (bool(passed) if passed is not None else None), **d}


def _load_hypotheses(store):
    """Return folded v2 hypotheses; if none exist, project legacy records so the
    gate still works on old runs."""
    from .hypotheses import HypothesisManager
    mgr = HypothesisManager(store)
    hyps = mgr.all()
    if hyps:
        return hyps, "v2"
    legacy = list(store.latest_hypotheses().values())
    return [project_legacy(h) for h in legacy], "legacy_projection"


def evaluate(hyps, claims_success=True):
    """Pure evaluation over a list of hypothesis dicts. Returns a machine-readable
    result with reasons. ``claims_success`` mirrors whether the campaign asserts a
    successful close."""
    reasons = []
    rollup = rollup_by_state(hyps)

    # 1. highest-priority open hypothesis must not still be proposed/claimed
    top_open = highest_priority_open(hyps)
    if top_open is not None and top_open.get("status") in NON_RESOLVED:
        reasons.append({"code": "top_priority_unresolved",
                        "hypothesis_id": top_open.get("hypothesis_id"),
                        "status": top_open.get("status"),
                        "priority": top_open.get("priority")})

    # 2. any hypothesis at the max open priority tier lacking a terminal/resolved
    #    status (proposed/claimed) also blocks a successful close
    opens = [h for h in hyps if is_open(h)]
    if opens:
        top_pri = max(Priority.coerce(h.get("priority")) for h in opens)
        for h in opens:
            if Priority.coerce(h.get("priority")) == top_pri and h.get("status") in NON_RESOLVED:
                if h is not top_open:
                    reasons.append({"code": "high_priority_unresolved",
                                    "hypothesis_id": h.get("hypothesis_id"),
                                    "status": h.get("status")})

    # 3. blocked without a valid blocker enum
    for h in hyps:
        if h.get("status") == S.BLOCKED.value and h.get("blocker") not in _VALID_BLOCKERS:
            reasons.append({"code": "blocked_without_valid_blocker",
                            "hypothesis_id": h.get("hypothesis_id"),
                            "blocker": h.get("blocker")})

    # 4. supported unauthorized effect without an independent replay
    for h in hyps:
        if h.get("status") == S.SUPPORTED.value and h.get("unauthorized_effect"):
            if not h.get("independent_replay_ok"):
                reasons.append({"code": "supported_without_independent_replay",
                                "hypothesis_id": h.get("hypothesis_id")})

    # 5. falsification integrity (blocked != falsified; needs executed evidence)
    for h in hyps:
        ok, why = validate_falsification(h)
        if not ok:
            reasons.append({"code": "invalid_falsification",
                            "hypothesis_id": h.get("hypothesis_id"), "detail": why})

    passed = (not reasons) if claims_success else True
    return {
        "passed": passed,
        "reasons": reasons,
        "rollup": rollup,
        "total_hypotheses": len(hyps),
        "highest_priority_open": (top_open or {}).get("hypothesis_id"),
        "highest_priority_open_status": (top_open or {}).get("status"),
    }


def lifecycle_gate(campaign_id, claims_success=True, store=None):
    """Acceptance-shaped gate. ``passed`` is False if the run claims success while
    a high-priority hypothesis is unresolved (or the integrity checks fail)."""
    store = store or CampaignStore(campaign_id)
    hyps, source = _load_hypotheses(store)
    result = evaluate(hyps, claims_success=claims_success)
    return gate("lifecycle_discipline", result["passed"],
                campaign_id=campaign_id, source=source, **{k: v for k, v in result.items()
                                                           if k != "passed"})


def export_lifecycle(campaign_id, out_path=None, store=None):
    """Write ``m4-hypothesis-lifecycle.json``: all hypotheses, transitions,
    experiments, blockers, replay outcomes. Returns the exported dict."""
    store = store or CampaignStore(campaign_id)
    hyps, source = _load_hypotheses(store)
    leases = store._read_all("leases") if "leases" in store._files else []
    handoffs = store._read_all("handoffs") if "handoffs" in store._files else []
    replans = store._read_all("replans") if "replans" in store._files else []
    doc = {
        "campaign_id": campaign_id,
        "source": source,
        "generated_at": _now(),
        "rollup": rollup_by_state(hyps),
        "blockers": _blocker_summary(hyps),
        "hypotheses": [
            {
                "hypothesis_id": h.get("hypothesis_id"),
                "fingerprint": h.get("fingerprint"),
                "priority": h.get("priority"),
                "claim": h.get("claim"),
                "target_assets": h.get("target_assets"),
                "status": h.get("status"),
                "status_reason": h.get("status_reason"),
                "terminal_status_reason": h.get("terminal_status_reason"),
                "blocker": h.get("blocker"),
                "owner": h.get("owner"),
                "evidence_refs": h.get("evidence_refs"),
                "experiment": h.get("experiment"),
                "transitions": h.get("transitions", []),
                "independent_replay_ok": h.get("independent_replay_ok"),
                "next_best_action": h.get("next_best_action"),
            }
            for h in hyps
        ],
        "leases": leases,
        "handoffs": handoffs,
        "replans": replans,
        "gate": evaluate(hyps),
    }
    out_path = Path(out_path) if out_path else (store.root / "m4-hypothesis-lifecycle.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, indent=2, default=str))
    return doc


def _blocker_summary(hyps):
    out = {b.value: 0 for b in Blocker}
    for h in hyps:
        if h.get("status") == S.BLOCKED.value and h.get("blocker") in out:
            out[h["blocker"]] += 1
    return out


def _now():
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
