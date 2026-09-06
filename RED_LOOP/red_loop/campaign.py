"""Lean campaign runner (Sections 10-11, 16).

Drives ONE sustained logical campaign with the primary model, brokering and
recording every tool call, checkpointing after each turn, detecting stagnation,
and invoking the deterministic judge (coarse feedback only) on each candidate.
The authoritative record is the CampaignStore, not the model's chat context.
"""
import json
import time
from collections import deque
from pathlib import Path

from . import config, llm
from .tools import TOOLS, Dispatcher


class StopReason:
    CONCLUDED = "agent_concluded"
    KILL = "kill_switch"
    MAX_TURNS = "max_turns_ceiling"
    MAX_WALL = "max_wall_ceiling"
    BUDGET = "request_budget_exhausted"
    STAGNATION = "stagnation_detected"
    LLM_ERRORS = "repeated_llm_errors"


def _assistant_msg(resp):
    msg = {"role": "assistant", "content": resp["content"] or ""}
    if resp["tool_calls"]:
        msg["tool_calls"] = [
            {"id": tc["id"], "type": "function",
             "function": {"name": tc["name"], "arguments": tc["arguments"]}}
            for tc in resp["tool_calls"]]
    return msg


def _trim(messages, max_chars=500000, keep_recent=40):
    """Keep system + kickoff + the most recent messages; the store holds the
    full record so trimming the chat cache is safe."""
    if sum(len(json.dumps(m)) for m in messages) <= max_chars:
        return messages
    head = messages[:2]
    tail = messages[-keep_recent:]
    # ensure tail starts on a valid boundary (not an orphan tool result)
    while tail and tail[0].get("role") == "tool":
        tail = tail[1:]
    note = {"role": "user", "content": "[older context trimmed to save space; your recorded "
            "hypotheses/observations remain in the campaign log]"}
    return head + [note] + tail


def run_campaign(store, broker, model, mandate_text, judge_hook,
                 max_turns=160, max_wall_seconds=10800, max_tokens_per_call=8000,
                 stagnation_patience=14):
    usage = llm.Usage()
    client = llm.ChatClient(model, usage=usage, max_tokens=max_tokens_per_call)
    disp = Dispatcher(broker, store, judge_hook)

    system = {"role": "system", "content": mandate_text}
    kickoff = {"role": "user", "content":
               "Begin your assessment now. Orient yourself, then pursue what you judge most promising. "
               "Record a hypothesis before each material experiment."}
    messages = [system, kickoff]

    stop_file = store.root / "STOP"
    t0 = time.time()
    turn = 0
    consecutive_no_action = 0
    consecutive_llm_errors = 0
    recent_fps = deque(maxlen=20)
    turns_since_progress = 0
    last_obs_cand = (0, 0)
    stop_reason = None

    store.event("campaign_start", model=model, max_turns=max_turns,
                max_wall_seconds=max_wall_seconds)

    while True:
        if stop_file.exists():
            stop_reason = StopReason.KILL
            break
        if turn >= max_turns:
            stop_reason = StopReason.MAX_TURNS
            break
        if time.time() - t0 >= max_wall_seconds:
            stop_reason = StopReason.MAX_WALL
            break
        if broker.request_count >= broker.request_budget:
            stop_reason = StopReason.BUDGET
            break

        turn += 1
        messages = _trim(messages)
        try:
            resp = client.complete(messages, tools=TOOLS)
            consecutive_llm_errors = 0
        except llm.LLMError as e:
            consecutive_llm_errors += 1
            store.event("llm_error", turn=turn, detail=llm.redact(str(e)))
            if consecutive_llm_errors >= 5:
                stop_reason = StopReason.LLM_ERRORS
                break
            time.sleep(min(5 * consecutive_llm_errors, 30))
            continue

        store.add_model_call({"turn": turn, "model": model,
                              "content_preview": llm.redact((resp["content"] or "")[:800]),
                              "n_tool_calls": len(resp["tool_calls"]),
                              "tool_names": [t["name"] for t in resp["tool_calls"]],
                              "finish_reason": resp["finish_reason"],
                              "usage": resp["raw_usage"]})
        messages.append(_assistant_msg(resp))

        if not resp["tool_calls"]:
            consecutive_no_action += 1
            messages.append({"role": "user", "content":
                "You did not take an action. Use a tool to investigate or experiment, "
                "record a hypothesis/observation, claim a candidate, or conclude."})
            if consecutive_no_action >= 4:
                store.event("no_action_stop", turn=turn)
                stop_reason = StopReason.STAGNATION
                break
            continue
        consecutive_no_action = 0

        # execute tool calls
        for tc in resp["tool_calls"]:
            args = llm.parse_tool_args(tc["arguments"])
            result = disp.dispatch(tc["name"], args)
            if tc["name"] in ("merchant_request", "merchant_request_concurrent") and isinstance(result, dict):
                fp = result.get("fingerprint") or ("%s:%s" % (args.get("method"), args.get("path")))
                recent_fps.append(fp)
            content = json.dumps(result, default=str)
            if len(content) > 60000:
                content = content[:60000] + '..."[truncated]"}'
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": content})

        if disp.concluded:
            store.event("agent_conclude", summary=disp.conclude_summary)
            stop_reason = StopReason.CONCLUDED
            break

        # progress / stagnation accounting
        counts = store.counts()
        cur = (counts["observations"], counts["candidates"])
        if cur != last_obs_cand:
            turns_since_progress = 0
            last_obs_cand = cur
        else:
            turns_since_progress += 1
        unique_recent = len(set(recent_fps))
        if (turns_since_progress >= stagnation_patience and len(recent_fps) >= 12
                and unique_recent <= 3):
            store.event("stagnation", turn=turn, turns_since_progress=turns_since_progress,
                        unique_recent_fingerprints=unique_recent)
            stop_reason = StopReason.STAGNATION
            break

        if turn % 5 == 0:
            store.update_manifest(turns=turn, elapsed_seconds=int(time.time() - t0),
                                  usage=usage.snapshot(), broker_requests=broker.request_count,
                                  broker_violations=broker.violation_count,
                                  candidates=store.counts()["candidates"])

    elapsed = int(time.time() - t0)
    summary = {"stop_reason": stop_reason, "turns": turn, "elapsed_seconds": elapsed,
               "usage": usage.snapshot(), "broker_requests": broker.request_count,
               "broker_violations": broker.violation_count,
               "candidates": store.counts()["candidates"],
               "hypotheses": store.counts()["hypotheses"],
               "observations": store.counts()["observations"]}
    store.update_manifest(completion=summary, completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    store.event("campaign_end", **summary)
    return summary
