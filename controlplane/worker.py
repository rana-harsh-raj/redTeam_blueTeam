"""Worker runtime: a bounded agent turn-set on ONE assigned twin.

A worker executes one task under a durable lease. It reasons with an injected
model client, and acts ONLY through the reused RED_LOOP typed broker (kong-lite
on its twin) and the read-only code/arch corpus. It never touches Docker, host
files, or control-plane state directly. Every model call and every tool call is
recorded in the campaign's durable ledgers, tagged with the campaign, twin,
cell, task and hypothesis.

Hypotheses are proposed through a campaign-wide HypothesisManager, so a claim
that semantically duplicates another cell's claim is suppressed at the fingerprint
(cross-cell dedup). Candidates are only *recorded* here — a worker never verifies
its own claim; verification is a separate task run by a different worker/model.

The model client and broker are injected, so the same code runs live (real
gateway + real broker in a twin-scoped subprocess) and under deterministic tests
(fake client + fake broker, virtual clock, no network, no Docker).
"""
import json
import time

from red_loop import llm
from red_loop.tools import TOOLS, Dispatcher
from red_loop.hypotheses import HypothesisManager

from .store import ControlStore, utc

# tools a worker may use (runtime + read-only corpus + bookkeeping + lifecycle)
DEFAULT_TOOL_ALLOWLIST = {
    "merchant_request", "read_own_webhooks", "whoami",
    "code_search", "code_read", "code_list", "code_find_symbol",
    "record_hypothesis", "update_hypothesis", "record_observation",
    "claim_candidate", "recall", "note", "conclude",
    "define_experiment", "mark_blocked", "request_replay",
}
CONCURRENT_TOOL = "merchant_request_concurrent"


def worker_system_prompt(manifest, task, packet):
    subj = task.get("subject")
    claim = task.get("claim") or ""
    lines = [
        manifest["mandate"].strip(),
        "",
        "You are one worker in an autonomous assessment cell. You have ordinary "
        "merchant access to ONE isolated twin through a typed broker. You do not "
        "control other workers, twins, or the plane.",
        "",
        "Your assigned line of inquiry (a thesis the campaign generated itself):",
        "  subject: %s" % subj,
        "  thesis:  %s" % claim,
    ]
    if task.get("suggested_probes"):
        lines.append("  suggested probes (optional, not a script): %s"
                     % ", ".join(task["suggested_probes"][:6]))
    if task.get("kind") == "replicate":
        lines += ["",
                  "This is an INDEPENDENT REPLICATION task. Investigate the thesis "
                  "afresh on your assigned twin; do not assume the earlier result."]
    lines += [
        "",
        "Bounds: at most %d broker/tool actions and %d seconds. Record a hypothesis "
        "before a material experiment; record observations from real responses; "
        "claim a candidate only when the twin's own responses support it; then "
        "conclude. A candidate you claim is NOT verified — an independent worker "
        "verifies it. Honesty over volume; concluding with nothing is acceptable."
        % (task["bounded"]["max_actions"], task["bounded"]["max_duration_s"]),
        "",
        "Bounded architecture context for your subject (from the world model):",
        json.dumps(packet.get("sections", packet), default=str)[:9000],
    ]
    return "\n".join(lines)


def _tools_for(allowlist):
    names = set(allowlist)
    return [t for t in TOOLS if t["function"]["name"] in names]


def run_task(control, task, manifest, world, broker, client_factory, router,
             twin_handle, clock=time.time, corpus_note=None, tool_allowlist=None,
             worker_id=None, cell_id=None, packet=None):
    """Execute one bounded task. Returns a result dict; all state is durable."""
    worker_id = worker_id or task.get("worker_id") or "W-adhoc"
    cell_id = cell_id or task.get("cell_id")
    allow = set(tool_allowlist or DEFAULT_TOOL_ALLOWLIST)
    if task.get("allow_concurrent"):
        allow.add(CONCURRENT_TOOL)

    subject = task.get("subject")
    if packet is None:
        packet = world.worker_packet(subject, budget=task.get("packet_budget", 6000)) if world else {"sections": {}}
    pkt_ev = control.put_evidence(packet, label="packet-%s" % (subject or "na"))

    # per-worker raw exploration trail (durable, but a working cache vs the ledgers)
    from red_loop.state import CampaignStore
    wroot = control.workers_dir / worker_id
    trail = CampaignStore(campaign_id=worker_id, root=wroot)
    hyp_mgr = HypothesisManager(control)   # campaign-wide dedup

    produced = {"hypotheses": [], "candidates": []}

    def candidate_hook(rec):
        # record the candidate campaign-wide, tagged; NEVER verify it here
        cid = control.next_id("candidates", "CAND")
        control.put("candidates", {
            "candidate_id": cid, "task_id": task["task_id"], "thesis_id": task.get("thesis_id"),
            "twin": task.get("twin"), "cell_id": cell_id, "worker_id": worker_id,
            "producer_model": state["model"], "claimed_outcome": rec.get("claimed_outcome"),
            "minimal_steps": rec.get("minimal_steps"), "affected_assets": rec.get("affected_assets"),
            "suspected_root_cause": rec.get("suspected_root_cause"),
            "evidence_summary": rec.get("evidence_summary"), "status": "unverified",
        })
        produced["candidates"].append(cid)
        control.event("candidate_claimed", candidate_id=cid, task_id=task["task_id"],
                      worker_id=worker_id, thesis_id=task.get("thesis_id"), twin=task.get("twin"))
        return {"recorded": True, "candidate_id": cid,
                "note": "recorded as UNVERIFIED; an independent worker will verify it"}

    disp = Dispatcher(broker, trail, candidate_hook, hyp_manager=hyp_mgr,
                      allowed_tools=allow, owner=worker_id)

    model = router.for_role(task.get("model_role", "worker"))
    state = {"model": model}
    client = client_factory(model)
    tools = _tools_for(allow)
    sys_prompt = worker_system_prompt(manifest, {**task, "subject": subject}, packet)
    messages = [{"role": "system", "content": sys_prompt},
                {"role": "user", "content": "Begin. Orient, then pursue the most promising probe."}]

    t0 = clock()
    max_actions = task["bounded"]["max_actions"]
    max_dur = task["bounded"]["max_duration_s"]
    actions = 0
    consecutive_unusable = 0
    stop_reason = "action_budget"
    hyp_before = set(hyp_mgr.latest().keys())

    control.event("task_started", task_id=task["task_id"], worker_id=worker_id,
                  twin=task.get("twin"), cell_id=cell_id, model=model, subject=subject)

    while actions < max_actions:
        if control.stop_requested():
            stop_reason = "kill_switch"
            break
        if control.pause_requested():
            stop_reason = "paused"
            break
        if (clock() - t0) >= max_dur:
            stop_reason = "time_budget"
            break

        try:
            resp = client.complete(messages, tools=tools)
        except Exception as e:  # noqa: BLE001  (timeout, refusal-as-error, gateway down)
            consecutive_unusable += 1
            control.event("model_error", task_id=task["task_id"], worker_id=worker_id,
                          model=state["model"], detail=llm.redact(str(e))[:300])
            nxt = router.fallback(state["model"])
            if nxt and nxt != state["model"]:
                control.event("model_fallback", task_id=task["task_id"], frm=state["model"], to=nxt)
                state["model"] = nxt
                client = client_factory(nxt)
                continue
            if consecutive_unusable >= 3:
                stop_reason = "model_unusable"
                break
            continue

        usage = resp.get("raw_usage", {}) or {}
        control.put("model_calls", {
            "task_id": task["task_id"], "worker_id": worker_id, "cell_id": cell_id,
            "twin": task.get("twin"), "thesis_id": task.get("thesis_id"),
            "model": state["model"], "finish_reason": resp.get("finish_reason"),
            "n_tool_calls": len(resp.get("tool_calls") or []),
            "tool_names": [t["name"] for t in resp.get("tool_calls") or []],
            "content_preview": llm.redact((resp.get("content") or "")[:400]),
            "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens")})

        unusable = llm.is_unusable(resp)
        if unusable:
            consecutive_unusable += 1
            control.event("model_unusable_turn", task_id=task["task_id"], worker_id=worker_id,
                          reason=unusable, model=state["model"])
            nxt = router.fallback(state["model"])
            if nxt and nxt != state["model"]:
                control.event("model_fallback", task_id=task["task_id"], frm=state["model"], to=nxt, reason=unusable)
                state["model"] = nxt
                client = client_factory(nxt)
            elif consecutive_unusable >= 3:
                stop_reason = "model_unusable"
                break
            continue
        consecutive_unusable = 0

        messages.append({"role": "assistant", "content": resp.get("content") or "",
                         "tool_calls": _raw_tool_calls(resp)})
        if not resp.get("tool_calls"):
            messages.append({"role": "user", "content":
                             "Take an action with a tool, or conclude."})
            actions += 1
            continue

        for tc in resp["tool_calls"]:
            args = llm.parse_tool_args(tc.get("arguments"))
            result = disp.dispatch(tc["name"], args)
            actions += 1
            control.put("tool_calls", {
                "task_id": task["task_id"], "worker_id": worker_id, "cell_id": cell_id,
                "twin": task.get("twin"), "thesis_id": task.get("thesis_id"),
                "tool": tc["name"], "args_preview": llm.redact(json.dumps(args, default=str)[:300]),
                "blocked": bool(isinstance(result, dict) and result.get("blocked")),
                "result_preview": llm.redact(json.dumps(result, default=str)[:300])})
            messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                             "content": json.dumps(result, default=str)[:8000]})
            if disp.concluded:
                stop_reason = "concluded"
                break
        if disp.concluded:
            break

    hyp_after = set(hyp_mgr.latest().keys())
    produced["hypotheses"] = sorted(hyp_after - hyp_before)
    result = {
        "task_id": task["task_id"], "worker_id": worker_id, "stop_reason": stop_reason,
        "actions": actions, "model": state["model"], "packet_ref": pkt_ev["ref"],
        "hypotheses": produced["hypotheses"], "candidates": produced["candidates"],
        "concluded": disp.concluded, "conclude_summary": disp.conclude_summary,
        "duration_s": round(clock() - t0, 3),
    }
    control.event("task_finished", **{k: result[k] for k in
                  ("task_id", "worker_id", "stop_reason", "actions", "candidates", "hypotheses")})
    return result


def _raw_tool_calls(resp):
    out = []
    for tc in resp.get("tool_calls") or []:
        out.append({"id": tc.get("id"), "type": "function",
                    "function": {"name": tc["name"], "arguments": tc.get("arguments") or "{}"}})
    return out or None
