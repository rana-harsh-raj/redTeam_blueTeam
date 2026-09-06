"""Independent clean-state reproducer (Sections 3B, 15, 17H).

Runs a DIFFERENT model family (gpt-5.5) from the primary (claude-opus-5). It
receives only a MINIMAL candidate bundle -- the starting actor, the minimal
action sequence, the required public inputs, the claimed observable result, and
only the code slices needed to adapt the replay. It does NOT get the primary's
narrative, scratchpad, severity language, the judge's classification, or the
known-gap registry. It uses fresh identifiers and the same brokered merchant
boundary, then the deterministic judge independently checks the fresh evidence.

If the campaign yields no judge-positive candidate, run_calibration() exercises
the same machinery on a private, harness-owned effect (idempotency de-dup on a
repeated create) that is NEVER shown to the primary red agent -- proving the
reproduce+judge path works, separate from the campaign's finding count.
"""
import json
import time

from . import config, corpus, llm

REPRO_MANDATE = """You are an independent security REPRODUCTION agent working in an authorized, \
isolated, synthetic Payouts test environment. You did not run the original test. You are given a \
minimal claim: a starting actor, a short action sequence, required inputs, and the observable \
effect that was claimed. Your job is to determine, from a clean starting point and using fresh \
identifiers, whether that same observable effect actually occurs when you perform the steps as an \
ordinary merchant through the API gateway.

Your merchant credential is injected automatically; you cannot change identity. Use merchant_request \
to perform the steps. Use fresh idempotency keys / references (do not reuse any values from the \
claim verbatim where a unique value is expected). Read the provided code slices only if you need to \
adapt an input. When done, call report_reproduction with what you observed (status codes, ids, \
bodies) and whether the claimed effect occurred. Do not exaggerate; report exactly what happened."""

REPRO_TOOLS = [
    {"type": "function", "function": {
        "name": "merchant_request",
        "description": "HTTP request as your merchant through the gateway. Identity is fixed.",
        "parameters": {"type": "object", "properties": {
            "method": {"type": "string"}, "path": {"type": "string"},
            "headers": {"type": "object"}, "body": {}}, "required": ["method", "path"]}}},
    {"type": "function", "function": {
        "name": "read_own_webhooks",
        "description": "Read webhook deliveries to your own merchant.",
        "parameters": {"type": "object", "properties": {"payout_id": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "code_read",
        "description": "Read a provided code slice (only paths listed in your bundle).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "report_reproduction",
        "description": "Report the outcome of your reproduction attempt.",
        "parameters": {"type": "object", "properties": {
            "effect_reproduced": {"type": "boolean"},
            "observed": {"type": "string"},
            "evidence_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["effect_reproduced", "observed"]}}},
]


def make_bundle(candidate, allowed_code_slices=None):
    """Strip a candidate to the minimal, non-persuasive replay bundle."""
    return {
        "starting_actor": "one ordinary synthetic merchant (credential injected)",
        "minimal_steps": candidate.get("minimal_steps", []),
        "required_inputs": candidate.get("required_inputs", {}),
        "claimed_observable_result": candidate.get("claimed_outcome", ""),
        "allowed_code_slices": allowed_code_slices or [],
    }


def run_reproduction(bundle, broker, store, model=None, max_turns=40, label="repro"):
    model = model or config.REPRODUCER_MODEL
    usage = llm.Usage()
    client = llm.ChatClient(model, usage=usage, max_tokens=4000)
    allowed = set(bundle.get("allowed_code_slices", []))

    user = {"role": "user", "content":
            "Reproduce (or fail to reproduce) this claimed effect from a clean start with fresh "
            "identifiers.\n\n" + json.dumps(bundle, indent=2)}
    messages = [{"role": "system", "content": REPRO_MANDATE}, user]

    report = None
    for turn in range(1, max_turns + 1):
        try:
            resp = client.complete(messages, tools=REPRO_TOOLS)
        except llm.LLMError as e:
            store.event("repro_llm_error", label=label, detail=llm.redact(str(e)))
            break
        store.add_model_call({"phase": "reproduction", "label": label, "model": model,
                              "turn": turn, "content_preview": llm.redact((resp["content"] or "")[:400]),
                              "tool_names": [t["name"] for t in resp["tool_calls"]],
                              "usage": resp["raw_usage"]})
        messages.append({"role": "assistant", "content": resp["content"] or "",
                         **({"tool_calls": [{"id": t["id"], "type": "function",
                             "function": {"name": t["name"], "arguments": t["arguments"]}}
                            for t in resp["tool_calls"]]} if resp["tool_calls"] else {})})
        if not resp["tool_calls"]:
            messages.append({"role": "user", "content": "Take an action or call report_reproduction."})
            continue
        done = False
        for tc in resp["tool_calls"]:
            args = llm.parse_tool_args(tc["arguments"])
            if tc["name"] == "merchant_request":
                result = broker.request(args.get("method", "GET"), args.get("path", ""),
                                        headers=args.get("headers"), body=args.get("body"))
            elif tc["name"] == "read_own_webhooks":
                result = broker.read_own_webhooks(payout_id=args.get("payout_id"))
            elif tc["name"] == "code_read":
                p = args.get("path", "")
                if p in allowed or any(p.startswith(a) for a in allowed):
                    try:
                        result = corpus.read_file(p, args.get("start", 1), args.get("end"))
                    except corpus.CorpusError as e:
                        result = {"error": str(e)}
                else:
                    result = {"error": "code slice not in your bundle"}
            elif tc["name"] == "report_reproduction":
                report = {"effect_reproduced": bool(args.get("effect_reproduced")),
                          "observed": args.get("observed", ""),
                          "evidence_ids": args.get("evidence_ids", [])}
                result = {"ok": True}
                done = True
            else:
                result = {"error": "unknown_tool"}
            messages.append({"role": "tool", "tool_call_id": tc["id"],
                             "content": json.dumps(result, default=str)[:40000]})
        if done:
            break

    store.event("reproduction_done", label=label, model=model,
                report=report, usage=usage.snapshot())
    return {"report": report, "usage": usage.snapshot(), "model": model}
