#!/usr/bin/env python3
"""shield-stub: substitute for Shield's payout rule-evaluation API. See CONTRACT.md.

Endpoint POST /v1/rules/evaluate/payout, grounded in shield-sdk/rule_evaluation/service.go
(getShieldEvaluateEndpoint) and shield-sdk/models/evaluate/payout_evaluate.go.
Rules are loaded from a fixture (SHIELD_RULES_FILE) rather than hardcoded, so
scripted scenarios can be edited without touching this file.
"""
import json
import os
import sys
import time

sys.path.insert(0, "/app")
from _common.base_stub import serve, _log  # noqa: E402

# Task-specified env var name (BOM/spec): injects a delay before answering,
# used to exercise payouts' fail-open-on-timeout behaviour ([shield] timeout
# = 200ms in payouts config -- setting this above 200 reproduces that path).
LATENCY_MS = int(os.environ.get("SHIELD_LATENCY_MS", "0"))
RULES_FILE = os.environ.get("SHIELD_RULES_FILE", "/app/seed/rules.json")

RULES = []
DEFAULT_RESPONSE = {"action": "allow", "triggered_rules": {}, "status_code": 200}


def _load_rules():
    global RULES, DEFAULT_RESPONSE
    if not os.path.exists(RULES_FILE):
        _log("no rules file at %s -- shield-stub always allows" % RULES_FILE)
        return
    with open(RULES_FILE) as f:
        data = json.load(f)
    RULES = data.get("rules", [])
    DEFAULT_RESPONSE = data.get("default", DEFAULT_RESPONSE)
    _log("loaded %d rule(s) from %s" % (len(RULES), RULES_FILE))


_load_rules()


def _dig(req, dotted_path):
    """'input.AccountNumber' -> req['input']['AccountNumber']; missing -> None."""
    cur = req
    for part in dotted_path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _match_one(cond, req):
    value = _dig(req, cond.get("field", ""))
    op = cond.get("op", "eq")
    target = cond.get("value")
    if op == "eq":
        matched = (value == target)
    elif op == "ends_with":
        matched = isinstance(value, str) and value.endswith(str(target))
    elif op == "starts_with":
        matched = isinstance(value, str) and value.startswith(str(target))
    elif op == "contains":
        matched = isinstance(value, str) and str(target) in value
    else:
        matched = False
    if not matched:
        return False
    sub_and = cond.get("and")
    if sub_and:
        return _match_one(sub_and, req)
    return True


def _evaluate_payout(handler, body):
    if LATENCY_MS > 0:
        time.sleep(LATENCY_MS / 1000.0)
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}

    for rule in RULES:
        if _match_one(rule.get("match", {}), req):
            resp = dict(rule.get("response", DEFAULT_RESPONSE))
            _log("rule %s matched -> action=%s" % (rule.get("rule_id"), resp.get("action")))
            return resp.get("status_code", 200), resp

    return DEFAULT_RESPONSE.get("status_code", 200), DEFAULT_RESPONSE


ROUTES = {
    ("POST", "/v1/rules/evaluate/payout"): _evaluate_payout,
}

if __name__ == "__main__":
    serve(ROUTES)
