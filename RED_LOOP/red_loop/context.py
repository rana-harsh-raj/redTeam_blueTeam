"""Context compiler (Workstream C).

The model conversation is a CACHE; the CampaignStore is the authoritative record.
Instead of resending the full transcript every turn (Milestone 2 resent ~13.28M
prompt tokens across 120 turns), each turn compiles a BOUNDED packet from the
structured store plus a short rolling window of the most recent raw messages
(needed only so an in-flight tool_call keeps its matching tool result).

Compiled packet contents (Section 8): mandate (system), attacker capability,
active hypotheses, verified facts, observed-but-unverified facts, recent material
actions, latest evidence summaries, failed assumptions to avoid repeating,
available retrieval, and the requested next decision. Older raw transcript is
dropped (it lives in the store and is reachable via the `recall` tool).

Every turn records WHY each item was included and the resulting size, so a
high-token turn is attributable to an explicit retrieval, not chat accretion.
"""
import json


# Rolling window of raw assistant/tool messages kept for tool-call continuity.
DEFAULT_RAW_WINDOW = 6           # last N turns' worth of assistant+tool messages
MAX_ACTIONS_SUMMARY = 12
MAX_OBS_SUMMARY = 8
MAX_EVIDENCE_CHARS = 1200


def _fold_hypotheses(store):
    return store.latest_hypotheses()


def compile_state_message(store, broker, extra_note=None):
    """Build the single compiled campaign-state message from durable records."""
    hyps = _fold_hypotheses(store)
    active, supported, refuted, testing = [], [], [], []
    for hid, h in sorted(hyps.items()):
        status = (h.get("status") or "proposed").lower()
        entry = {"id": hid, "claim": h.get("claim"),
                 "weakness": h.get("suspected_weakness"),
                 "experiment": h.get("planned_experiment"), "note": h.get("note")}
        if status in ("proposed", "testing"):
            active.append(entry)
            if status == "testing":
                testing.append(hid)
        elif status in ("supported", "confirmed"):
            supported.append(entry)
        elif status in ("refuted", "failed", "rejected"):
            refuted.append(entry)

    observations = store._read_all("observations")
    verified_facts, unverified = [], []
    for o in observations:
        fact = {"id": o.get("observation_id"), "hypothesis_id": o.get("hypothesis_id"),
                "facts": o.get("deterministic_facts"), "interpretation": o.get("interpretation"),
                "unlocked": o.get("unlocked_capability")}
        if o.get("deterministic_facts"):
            verified_facts.append(fact)
        else:
            unverified.append(fact)

    actions = store._read_all("actions")
    recent_actions = []
    for a in actions[-MAX_ACTIONS_SUMMARY:]:
        recent_actions.append({"tool": a.get("tool"), "method": a.get("method"),
                               "path": a.get("path"), "status": a.get("status"),
                               "result": a.get("result"), "fp": a.get("fingerprint")})

    candidates = store._read_all("candidates")
    cand_state = []
    for c in candidates:
        cand_state.append({"id": c.get("candidate_id"), "status": c.get("status"),
                           "verdict": c.get("verdict"),
                           "impact_confirmed": c.get("deterministic_impact_confirmed")})

    state = {
        "attacker_capability": broker.attacker_identity(),
        "active_hypotheses": active[:20],
        "hypotheses_under_test": testing,
        "verified_facts": verified_facts[-MAX_OBS_SUMMARY:],
        "observed_unverified": unverified[-MAX_OBS_SUMMARY:],
        "failed_assumptions_do_not_repeat": [
            {"id": e["id"], "claim": e["claim"], "why": e.get("note")} for e in refuted[:20]],
        "supported_findings_so_far": supported[:20],
        "recent_actions": recent_actions,
        "candidate_state": cand_state[-8:],
        "requests_so_far": broker.request_count,
        "retrieval": "Use `recall` to reopen older hypotheses/observations/actions "
                     "by id or keyword, and code_read/code_search for source. Older raw "
                     "conversation has been compacted; your recorded state above is authoritative.",
    }
    body = ("CAMPAIGN STATE (authoritative, compiled from your durable records — "
            "the older raw conversation was compacted to save context; nothing was lost):\n"
            + json.dumps(state, indent=2, default=str))
    if extra_note:
        body += "\n\nNEXT DECISION: " + extra_note
    reasons = {
        "active_hypotheses": len(active), "verified_facts": len(verified_facts),
        "unverified": len(unverified), "failed_assumptions": len(refuted),
        "recent_actions": len(recent_actions), "candidates": len(cand_state),
        "bytes": len(body),
    }
    return {"role": "user", "content": body}, reasons


def build_messages(system_mandate, state_msg, raw_window, nudge=None):
    """Assemble the bounded per-turn message list:
    system(mandate) + compiled state + short raw window (+ optional nudge)."""
    msgs = [{"role": "system", "content": system_mandate}, state_msg]
    # ensure the raw window starts on a valid boundary (not an orphan tool result)
    win = list(raw_window)
    while win and win[0].get("role") == "tool":
        win = win[1:]
    msgs.extend(win)
    if nudge:
        msgs.append({"role": "user", "content": nudge})
    return msgs


def trim_raw_window(raw_msgs, window=DEFAULT_RAW_WINDOW):
    """Keep only the most recent `window` assistant turns and their tool results."""
    if not raw_msgs:
        return []
    # count assistant messages from the end; keep the tail after the Nth-from-last
    assistant_idx = [i for i, m in enumerate(raw_msgs) if m.get("role") == "assistant"]
    if len(assistant_idx) <= window:
        return raw_msgs
    cut = assistant_idx[-window]
    return raw_msgs[cut:]


def packet_metrics(state_reasons, raw_window, retrieved_slices=0):
    """Per-turn context metrics for the compiler artifact."""
    raw_bytes = sum(len(json.dumps(m, default=str)) for m in raw_window)
    return {
        "compiled_state_bytes": state_reasons.get("bytes", 0),
        "raw_window_msgs": len(raw_window),
        "raw_window_bytes": raw_bytes,
        "included_records": {
            "active_hypotheses": state_reasons.get("active_hypotheses", 0),
            "verified_facts": state_reasons.get("verified_facts", 0),
            "recent_actions": state_reasons.get("recent_actions", 0),
            "candidates": state_reasons.get("candidates", 0),
        },
        "retrieved_code_slices": retrieved_slices,
        "full_transcript_resent": False,
    }
