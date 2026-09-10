"""Campaign runner (Workstreams C, D, E, F; Sections 10, 11, 16).

Drives ONE sustained autonomous campaign with the primary model:
- context is COMPILED from the durable store each turn, not resent whole (C);
- blank/content-filtered turns are detected and recovered, switching model if a
  provider stays unusable (D);
- completion is progress/stagnation/conclusion based, NOT a normal turn ceiling —
  a high emergency ceiling exists only as a safety backstop (E);
- the authoritative record is the CampaignStore, so a killed runner can resume
  from durable state (F).
"""
import json
import time
from collections import deque

from . import config, context, llm
from .tools import TOOLS, Dispatcher


class StopReason:
    CONCLUDED = "agent_concluded"
    KILL = "kill_switch"
    EMERGENCY_CEILING = "emergency_turn_ceiling"   # backstop only, NOT normal completion
    MAX_WALL = "max_wall_ceiling"
    BUDGET = "request_budget_exhausted"
    STAGNATION = "stagnation_pause"                 # not a finding
    RESOLVED = "all_hypotheses_resolved"
    LLM_UNUSABLE = "model_unrecoverable"


NORMAL_COMPLETIONS = {StopReason.CONCLUDED, StopReason.STAGNATION, StopReason.RESOLVED,
                      StopReason.KILL, StopReason.BUDGET, StopReason.MAX_WALL}


def _assistant_msg(resp):
    msg = {"role": "assistant", "content": resp["content"] or ""}
    if resp["tool_calls"]:
        msg["tool_calls"] = [
            {"id": tc["id"], "type": "function",
             "function": {"name": tc["name"], "arguments": tc["arguments"]}}
            for tc in resp["tool_calls"]]
    return msg


def _progress_signature(store, recent_fps):
    c = store.counts()
    verified = sum(1 for o in store._read_all("observations") if o.get("deterministic_facts"))
    supported = sum(1 for h in store.latest_hypotheses().values()
                    if (h.get("status") or "").lower() in ("supported", "confirmed", "refuted"))
    # M4: also count v2 terminal lifecycle states so lifecycle progress registers.
    v2_terminal = 0
    for rec in store._read_all("hypotheses_v2"):
        st = (rec.get("status") or "").lower()
        if st in ("falsified", "blocked", "supported", "accepted", "closed", "rejected"):
            v2_terminal += 1
    return (c["hypotheses"], c["observations"], c["candidates"], verified, supported,
            len(set(recent_fps)), v2_terminal)


def _record_replan(store, turn, reason, what_was_tried, next_policy):
    """Persist a machine-readable reasoned replan before a stagnation stop."""
    store.ensure_kind("replans")
    store._append("replans", {"kind": "campaign_replan", "turn": turn, "reason": reason,
                              "what_was_tried": what_was_tried, "next_policy": next_policy})


def _complete_with_model(client, messages, store, model, turn, phase="turn",
                         fallbacks=None, usage=None):
    """One completion with Workstream-D recovery. Returns (resp, model, note).
    On a blank/filtered/refused response: retry once with a SMALLER packet
    (system+state only, neutral nudge); if still unusable, switch to a distinct
    approved model and try once more. Never bypasses provider safeguards."""
    fallbacks = list(fallbacks or [])
    resp = client.complete(messages, tools=TOOLS)
    reason = llm.is_unusable(resp)
    if not reason:
        return resp, model, None

    store.event("model_unusable", turn=turn, phase=phase, model=model,
                finish_reason=resp.get("finish_reason"), reason=reason)
    # Retry once, smaller context, neutral precise wording (no history, no bulk).
    smaller = [messages[0], messages[1],
               {"role": "user", "content":
                "Continue the authorized security assessment. Choose one concrete next "
                "action using a tool, or record a hypothesis/observation. Be brief."}]
    resp2 = client.complete(smaller, tools=TOOLS)
    if not llm.is_unusable(resp2):
        store.event("model_recovered", turn=turn, model=model, how="smaller_context")
        return resp2, model, "recovered_smaller_context"

    # Switch to a distinct approved model from the live inventory.
    alt = llm.pick_fallback(model, available=(fallbacks or llm.list_models()))
    if alt and alt != model:
        store.event("model_switch", turn=turn, from_model=model, to_model=alt, reason=reason)
        alt_client = llm.ChatClient(alt, usage=usage if usage is not None else client.usage,
                                    max_tokens=client.default_max_tokens)
        resp3 = alt_client.complete(smaller, tools=TOOLS)
        if not llm.is_unusable(resp3):
            return resp3, alt, "switched_model:%s" % alt
        store.event("model_switch_still_unusable", turn=turn, model=alt,
                    reason=llm.is_unusable(resp3))
        return resp3, alt, "switched_model_unusable"
    return resp2, model, "no_fallback_available"


def run_campaign(store, broker, model, mandate_text, judge_hook,
                 emergency_max_turns=2000, max_wall_seconds=None,
                 max_tokens_per_call=8000, stagnation_patience=14,
                 raw_window=context.DEFAULT_RAW_WINDOW, resume=False,
                 model_fallbacks=None):
    usage = llm.Usage()
    client = llm.ChatClient(model, usage=usage, max_tokens=max_tokens_per_call)
    disp = Dispatcher(broker, store, judge_hook)
    fallbacks = model_fallbacks if model_fallbacks is not None else llm.list_models()

    stop_file = store.root / "STOP"
    pause_file = store.root / "PAUSE"
    t0 = time.time()
    start_turn = 0
    if resume:
        prev = store.read_manifest().get("turns_completed", 0)
        start_turn = int(prev)
        store.event("campaign_resume", from_turn=start_turn,
                    resume_snapshot=store.resume_snapshot())
    turn = start_turn

    raw_msgs = []                       # rolling window of assistant+tool messages
    consecutive_no_action = 0
    consecutive_unusable = 0
    recent_fps = deque(maxlen=24)
    turns_since_progress = 0
    last_sig = _progress_signature(store, recent_fps)
    stop_reason = None
    stagnation_replan_used = False
    recon_replan_used = False
    recon_only_streak = 0
    retrieved_slices_this_turn = 0
    recon_patience = max(8, stagnation_patience)

    store.event("campaign_start" if not resume else "campaign_continue",
                model=model, emergency_max_turns=emergency_max_turns,
                max_wall_seconds=max_wall_seconds, raw_window=raw_window)

    nudge = ("Begin your assessment now. Orient yourself, then pursue what you judge most "
             "promising. Record a hypothesis before each material experiment.")

    while True:
        if stop_file.exists():
            stop_reason = StopReason.KILL
            break
        if pause_file.exists():
            store.event("graceful_pause", turn=turn)
            stop_reason = StopReason.KILL
            break
        if turn >= emergency_max_turns:
            stop_reason = StopReason.EMERGENCY_CEILING     # backstop, not normal completion
            break
        if max_wall_seconds and (time.time() - t0) >= max_wall_seconds:
            stop_reason = StopReason.MAX_WALL
            break
        if broker.request_count >= broker.request_budget:
            stop_reason = StopReason.BUDGET
            break

        turn += 1
        # --- compile the bounded packet from durable state (Workstream C) ---
        raw_msgs = context.trim_raw_window(raw_msgs, raw_window)
        state_msg, reasons = context.compile_state_message(store, broker)
        messages = context.build_messages(mandate_text, state_msg, raw_msgs, nudge=nudge)
        nudge = None

        try:
            resp, model, dnote = _complete_with_model(
                client, messages, store, model, turn, fallbacks=fallbacks, usage=usage)
            if model != client.model:
                client = llm.ChatClient(model, usage=usage, max_tokens=max_tokens_per_call)
        except llm.LLMError as e:
            consecutive_unusable += 1
            store.event("llm_error", turn=turn, detail=llm.redact(str(e)))
            if consecutive_unusable >= 6:
                stop_reason = StopReason.LLM_UNUSABLE
                break
            time.sleep(min(5 * consecutive_unusable, 30))
            turn -= 1
            continue

        unusable = llm.is_unusable(resp)
        # per-turn context metrics (Workstream C acceptance)
        metrics = context.packet_metrics(reasons, raw_msgs, retrieved_slices_this_turn)
        metrics.update({"turn": turn, "model": model, "prompt_tokens": resp["raw_usage"].get("prompt_tokens"),
                        "completion_tokens": resp["raw_usage"].get("completion_tokens"),
                        "finish_reason": resp.get("finish_reason"),
                        "unusable": unusable, "recovery": dnote})
        store.add_context_metric(metrics)
        retrieved_slices_this_turn = 0

        store.add_model_call({"turn": turn, "model": model,
                              "content_preview": llm.redact((resp["content"] or "")[:600]),
                              "n_tool_calls": len(resp["tool_calls"]),
                              "tool_names": [t["name"] for t in resp["tool_calls"]],
                              "finish_reason": resp["finish_reason"],
                              "recovery": dnote, "usage": resp["raw_usage"]})

        if unusable:
            consecutive_unusable += 1
            store.event("unusable_turn_skipped", turn=turn, reason=unusable)
            if consecutive_unusable >= 6:
                stop_reason = StopReason.LLM_UNUSABLE
                break
            continue
        consecutive_unusable = 0

        raw_msgs.append(_assistant_msg(resp))

        if not resp["tool_calls"]:
            consecutive_no_action += 1
            raw_msgs.append({"role": "user", "content":
                "You did not take an action. Use a tool to investigate or experiment, "
                "record a hypothesis/observation, use recall, claim a candidate, or conclude."})
            if consecutive_no_action >= 4:
                store.event("no_action_stop", turn=turn)
                stop_reason = StopReason.STAGNATION
                break
            continue
        consecutive_no_action = 0

        did_runtime_experiment = False
        for tc in resp["tool_calls"]:
            args = llm.parse_tool_args(tc["arguments"])
            result = disp.dispatch(tc["name"], args)
            if tc["name"] in ("merchant_request", "merchant_request_concurrent") and isinstance(result, dict):
                fp = result.get("fingerprint") or ("%s:%s" % (args.get("method"), args.get("path")))
                recent_fps.append(fp)
                did_runtime_experiment = True
            if tc["name"] in ("read_own_webhooks",):
                did_runtime_experiment = True
            if tc["name"] in ("code_read", "code_search", "recall"):
                retrieved_slices_this_turn += 1
            content = json.dumps(result, default=str)
            if len(content) > 40000:
                content = content[:40000] + '..."[truncated]"}'
            raw_msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": content})

        if disp.concluded:
            store.event("agent_conclude", summary=disp.conclude_summary)
            stop_reason = StopReason.CONCLUDED
            break

        # --- progress / stagnation accounting (Workstream E) ---
        sig = _progress_signature(store, recent_fps)
        if sig != last_sig:
            turns_since_progress = 0
            last_sig = sig
        else:
            turns_since_progress += 1

        # recon-only loop: many turns of code/recall analysis with no runtime
        # experiment. The mandate requires proving effects at runtime, so a long
        # code-reading streak with no experiment is stagnation, not progress.
        if did_runtime_experiment:
            recon_only_streak = 0
        else:
            recon_only_streak += 1
        if recon_only_streak >= recon_patience and not recon_replan_used:
            store.event("recon_loop_replan_requested", turn=turn, recon_only_streak=recon_only_streak)
            _record_replan(store, turn, "recon_only_loop",
                           what_was_tried="many code/recall turns without a runtime experiment",
                           next_policy="run one concrete merchant_request experiment or conclude")
            recon_replan_used = True
            recon_only_streak = 0
            nudge = ("You have spent many turns reading source without running a runtime experiment. "
                     "The rules require proving an effect in the RUNNING system, not from code alone. "
                     "Either run a concrete merchant_request experiment against a hypothesis now, or if "
                     "you have genuinely exhausted reachable avenues, call conclude with a summary.")
            continue
        if recon_only_streak >= recon_patience and recon_replan_used:
            store.event("stagnation_pause", turn=turn, reason="recon_only_loop",
                        recon_only_streak=recon_only_streak,
                        note="paused after a replan still produced no runtime experiment; NOT a finding")
            stop_reason = StopReason.STAGNATION
            break

        unique_recent = len(set(recent_fps))
        stagnating = (turns_since_progress >= stagnation_patience and len(recent_fps) >= 12
                      and unique_recent <= 3)
        if stagnating and not stagnation_replan_used:
            store.event("stagnation_replan_requested", turn=turn,
                        turns_since_progress=turns_since_progress, unique_recent=unique_recent)
            _record_replan(store, turn, "fingerprint_stagnation",
                           what_was_tried="repeated similar requests with no new evidence",
                           next_policy="state one materially different hypothesis + new experiment, or conclude")
            stagnation_replan_used = True
            turns_since_progress = 0
            nudge = ("You appear to be repeating similar actions without new evidence. Step back: "
                     "state ONE materially different hypothesis and a concrete new experiment, or if "
                     "you have genuinely exhausted productive avenues, call conclude with a summary.")
            continue
        if stagnating and stagnation_replan_used:
            store.event("stagnation_pause", turn=turn, turns_since_progress=turns_since_progress,
                        unique_recent=unique_recent,
                        note="paused after a final replan produced no materially new line; NOT a finding")
            stop_reason = StopReason.STAGNATION
            break

        if turn % 5 == 0:
            store.update_manifest(turns_completed=turn, elapsed_seconds=int(time.time() - t0),
                                  usage=usage.snapshot(), broker_requests=broker.request_count,
                                  broker_violations=broker.violation_count,
                                  candidates=store.counts()["candidates"],
                                  state_hash=store.state_hash())

    elapsed = int(time.time() - t0)
    summary = {"stop_reason": stop_reason,
               "normal_completion": stop_reason in NORMAL_COMPLETIONS,
               "ended_on_emergency_ceiling": stop_reason == StopReason.EMERGENCY_CEILING,
               "turns_completed": turn, "elapsed_seconds": elapsed,
               "usage": usage.snapshot(), "broker_requests": broker.request_count,
               "broker_violations": broker.violation_count,
               "candidates": store.counts()["candidates"],
               "hypotheses": store.counts()["hypotheses"],
               "observations": store.counts()["observations"],
               "final_state_hash": store.state_hash()}
    store.update_manifest(turns_completed=turn, completion=summary,
                          completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    store.event("campaign_end", **summary)
    return summary
