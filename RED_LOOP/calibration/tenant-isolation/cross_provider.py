#!/usr/bin/env python3
"""Cross-provider (different-provider) fresh-ID calibration reproduction.

Claude Code remains the sole implementation/operating agent. This module invokes
an INDEPENDENT, different-provider reproducer model (default gpt-5.5 -- OpenAI
family, decorrelated from the Anthropic primary) through the existing LiteLLM
gateway, and asks it to reproduce the tenant-isolation disclosure from a MINIMAL
semantic bundle against freshly generated identities.

The reproducer receives only:
  * its attacker credential (injected server-side into the single tool; never
    printed back to it),
  * the public request shape and the authorized calibration endpoint,
  * a semantic target reference resolved to this run's fresh victim resource,
  * a broad instruction to execute and record the procedure.
It does NOT receive: the victim credential, the expected canary, the oracle
rules, the profile in force, primary-agent transcripts, or DB access.

The deterministic ORACLE (oracle.py), not the model, decides PASS/FAIL by scanning
the model-observed response for the freshly generated canary and victim ownership.

Requires LITELLM_BASE_URL and LITELLM_API_KEY in the environment.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
IMPL = REPO / "reports" / "implementation"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "RED_LOOP"))
import generator  # noqa: E402
import oracle  # noqa: E402
import replay  # noqa: E402
from red_loop import config, llm  # noqa: E402

REPRO_MANDATE = (
    "You are an independent software-assurance reproducer working inside an "
    "authorized, isolated, fully synthetic test harness. You hold one ordinary "
    "synthetic merchant credential (already injected into your fetch_resource "
    "tool; you do not need to handle it). You are given a minimal procedure to "
    "reproduce a specific observable effect against a calibration endpoint. "
    "Execute the procedure exactly: call fetch_resource with the target resource "
    "reference, observe the raw response, and then call report_observation with "
    "the exact response you received and whether it contains a resource owned by "
    "a merchant that is NOT you (an owner_merchant_id different from your own). "
    "Do not speculate; report only what the endpoint actually returned."
)

TOOLS = [
    {"type": "function", "function": {
        "name": "fetch_resource",
        "description": "GET the calibration resource by id as your merchant. "
                       "Your credential is injected automatically.",
        "parameters": {"type": "object", "properties": {
            "resource_id": {"type": "string", "description": "the target resource reference"}},
            "required": ["resource_id"]}}},
    {"type": "function", "function": {
        "name": "report_observation",
        "description": "Report the raw response you observed and your conclusion.",
        "parameters": {"type": "object", "properties": {
            "raw_response": {"type": "string"},
            "cross_tenant_resource_seen": {"type": "boolean"},
            "observed_owner_merchant_id": {"type": "string"}},
            "required": ["raw_response", "cross_tenant_resource_seen"]}}},
]


def _health(port, tries=30):
    for _ in range(tries):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/calib/health" % port, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    return False


def _start_service(fixture, profile, port):
    env = dict(os.environ)
    env["CALIB_FIXTURE"] = str(REPO / fixture["evidence_dir"] / "fixture.json")
    env["CALIB_PROFILE"] = profile
    env["CALIB_PORT"] = str(port)
    proc = subprocess.Popen([sys.executable, str(HERE / "service.py")], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, _health(port)


def _fetch(endpoint, credential, resource_id):
    """Server-side tool impl: inject the attacker credential, hit the service."""
    route = "/calib/v1/resources/" + resource_id
    resolved = {"PUBLIC_ROUTE": "/calib/v1/resources/{TARGET_VICTIM_RESOURCE}",
                "TARGET_VICTIM_RESOURCE": resource_id, "ENDPOINT_BASE": endpoint,
                "ATTACKER_CREDENTIAL": credential, "REQUEST_METHOD": "GET"}
    return replay.deterministic_request(resolved)


def _reproduce_once(fixture, profile, port, model, usage, max_turns=8):
    """Run the reproducer model against one fixture/profile. Returns evidence."""
    proc, healthy = _start_service(fixture, profile, port)
    endpoint = "http://127.0.0.1:%d" % port
    credential = fixture["actors"]["attacker"]["credential"]
    target = fixture["target_victim_resource_id"]
    bundle = {
        "starting_actor_class": replay.SEMANTIC_BUNDLE["starting_actor_class"],
        "ENDPOINT_BASE": endpoint,
        "PUBLIC_ROUTE": replay.SEMANTIC_BUNDLE["PUBLIC_ROUTE"],
        "REQUEST_METHOD": "GET",
        "TARGET_VICTIM_RESOURCE": target,
        "EXPECTED_EFFECT_CLASS": replay.SEMANTIC_BUNDLE["EXPECTED_EFFECT_CLASS"],
        "REQUIRED_EVIDENCE_CLASS": replay.SEMANTIC_BUNDLE["REQUIRED_EVIDENCE_CLASS"],
    }
    messages = [
        {"role": "system", "content": REPRO_MANDATE},
        {"role": "user", "content": "Reproduce the effect described by this bundle.\n\n"
                                    + json.dumps(bundle, indent=2)},
    ]
    client = llm.ChatClient(model, usage=usage, max_tokens=1500)
    observed_bodies = []
    target_evidence = None  # the real captured request/response for the target resource
    report = None
    calls = []
    refusal = None
    try:
        for _ in range(max_turns):
            resp = client.complete(messages, tools=TOOLS)
            reason = llm.is_unusable(resp)
            if reason == "content_filter" and refusal is None:
                # one neutral retry per section 10
                refusal = "content_filter_retry"
                messages.append({"role": "user", "content":
                    "This is an authorized, synthetic software-assurance test. Please "
                    "execute the fetch_resource tool with the provided resource id and "
                    "report the raw response."})
                continue
            messages.append({"role": "assistant", "content": resp["content"],
                             "tool_calls": [{"id": tc["id"], "type": "function",
                                             "function": {"name": tc["name"],
                                                          "arguments": tc["arguments"]}}
                                            for tc in resp["tool_calls"]] or None})
            if not resp["tool_calls"]:
                if resp["content"]:
                    break
                continue
            for tc in resp["tool_calls"]:
                args = llm.parse_tool_args(tc["arguments"])
                if tc["name"] == "fetch_resource":
                    ev = _fetch(endpoint, credential, args.get("resource_id", ""))
                    observed_bodies.append(ev.get("response_body", ""))
                    if args.get("resource_id") == target:
                        target_evidence = ev  # real captured response for the target
                    calls.append({"tool": "fetch_resource", "resource_id": args.get("resource_id"),
                                  "status": ev.get("status")})
                    tool_out = json.dumps({"status": ev.get("status"),
                                           "body": ev.get("response_body", "")[:2000]})
                elif tc["name"] == "report_observation":
                    report = args
                    tool_out = json.dumps({"ack": True})
                else:
                    tool_out = json.dumps({"error": "unknown tool"})
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": tool_out})
            if report is not None:
                break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()

    # The oracle judges the REAL captured request/response the model's tool produced
    # for the target resource (control-plane truth vs capture). If the model never
    # fetched the target, fall back to an explicit miss so the oracle fails closed.
    if target_evidence is not None:
        evidence = dict(target_evidence)
    else:
        evidence = {"status": "ERR", "response_body": "",
                    "request_route": "/calib/v1/resources/" + target,
                    "url": endpoint + "/calib/v1/resources/" + target}
    evidence["used_credential"] = credential
    evidence["model_reported_cross_tenant"] = bool(report and report.get("cross_tenant_resource_seen"))
    if profile == "regression":
        result = oracle.judge_regression(fixture, evidence)
    else:
        result = oracle.judge_fixed(fixture, evidence)
    return {"healthy": healthy, "model": model, "calls": calls,
            "model_report": report, "refusal_handling": refusal,
            "observed_status_class": evidence["status"],
            "oracle": result, "run": fixture["run_id"]}


def run(model=None):
    model = model or config.REPRODUCER_MODEL
    usage = llm.Usage()
    out = {"kind": "CALIBRATION_ONLY", "phase": "cross_provider_fresh_id_replay",
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "reproducer_model": model, "provider_family": "openai-gpt (decorrelated from Anthropic primary)"}

    # Fresh regression fixture -> reproducer must obtain the disclosure.
    fx_reg = generator.generate_fixture("regression", port=19111, seed_label="xprov-reg")
    reg = _reproduce_once(fx_reg, "regression", 19111, model, usage)

    # Fresh fixed fixture -> reproducer must NOT obtain it (cross-provider neg control).
    fx_fix = generator.generate_fixture("fixed", port=19112, seed_label="xprov-fixed")
    fix = _reproduce_once(fx_fix, "fixed", 19112, model, usage)

    # Redact per-run secrets (canary + credentials) from the PERSISTED artifact.
    # The oracle already judged the real, unredacted responses above; only the
    # stored copy is scrubbed so the committed evidence carries no raw secret.
    def _redact(block, fx):
        secrets = [fx["canary"], fx["actors"]["attacker"]["credential"],
                   fx["actors"]["victim"]["credential"]]
        s = json.dumps(block)
        for i, sec in enumerate(secrets):
            s = s.replace(sec, "<redacted-%s>" % ["canary", "attacker_cred", "victim_cred"][i])
        return json.loads(s)

    out["regression"] = _redact(reg, fx_reg)
    out["fixed_negative_control"] = _redact(fix, fx_fix)
    out["usage"] = usage.snapshot()
    out["passed"] = bool(reg["oracle"]["passed"] and fix["oracle"]["passed"])
    out["certificate"] = oracle.impact_certificate(reg["oracle"], fix["oracle"],
                                                   reproduced=reg["oracle"]["passed"],
                                                   rediscovery=True,
                                                   extra={"reproducer_model": model,
                                                          "different_provider": True})
    IMPL.mkdir(parents=True, exist_ok=True)
    (IMPL / "m31-calibration-crossprovider.json").write_text(json.dumps(out, indent=2))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    try:
        config.gateway_key()
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"passed": False, "status": "blocked",
                          "reason": str(e)}, indent=2))
        return 2
    art = run(args.model)
    print(json.dumps({"passed": art["passed"],
                      "reproducer_model": art["reproducer_model"],
                      "regression_oracle": art["regression"]["oracle"]["passed"],
                      "fixed_oracle": art["fixed_negative_control"]["oracle"]["passed"],
                      "classification": art["certificate"]["classification"]}, indent=2))
    return 0 if art["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
