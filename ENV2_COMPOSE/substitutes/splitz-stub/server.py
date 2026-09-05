#!/usr/bin/env python3
"""splitz-stub: substitute for Splitz EvaluateAPI. See CONTRACT.md.

Wire shape from proto/splitz/evaluate/v1/evaluate_api.proto (service
rzp.splitz.evaluate.v1.EvaluateAPI) -- Twirp JSON-over-HTTP, path
/twirp/rzp.splitz.evaluate.v1.EvaluateAPI/{Method}. Endpoint constants
confirmed against goutils/splitz/client.go (EvaluateEndpoint,
BulkEvaluateEndpoint, ExperimentFetchEndpoint).

EvaluateRequest.Id carries the caller's entity id -- for payouts this is
ALWAYS merchantID (confirmed: payouts/.agents/skills/pre-mortem/references/
services-splitz.md's `evalRequest := splitz.EvaluateRequest{Id: merchantID,
ExperimentId: ...}` pattern, repeated at every call site shown there) -- so
this stub resolves a variant by (experiment_id or experiment_name, Id).
"""
import json
import os
import sys
import time

sys.path.insert(0, "/app")
from _common.base_stub import serve, _log  # noqa: E402

SEED_FILE = os.environ.get("SPLITZ_SEED_FILE", "/app/seed/experiments.json")
INJECT_LATENCY_MS = int(os.environ.get("SPLITZ_INJECT_LATENCY_MS", "0"))

EXPERIMENTS_BY_ID = {}   # experiment_id -> {name, variants: {merchant_id: variant_name}}
EXPERIMENTS_BY_NAME = {}  # name -> same record (points at the same dict object)
DEFAULT_VARIANT = "on"


def _load_seed():
    global DEFAULT_VARIANT
    if not os.path.exists(SEED_FILE):
        _log("no seed file at %s -- splitz-stub starting empty (default variant only)" % SEED_FILE)
        return
    with open(SEED_FILE) as f:
        data = json.load(f)
    DEFAULT_VARIANT = data.get("default_variant", "on")
    for exp_id, rec in data.get("experiments", {}).items():
        EXPERIMENTS_BY_ID[exp_id] = rec
        if rec.get("name"):
            EXPERIMENTS_BY_NAME[rec["name"]] = dict(rec, id=exp_id)
    for name, rec in (data.get("_by_name_alias") or {}).items():
        if name.startswith("_") or not isinstance(rec, dict):
            continue
        if name not in EXPERIMENTS_BY_NAME:
            EXPERIMENTS_BY_NAME[name] = dict(rec, id="", name=name)
    _log("loaded %d experiment(s) (%d by-name alias(es)) from %s" %
         (len(EXPERIMENTS_BY_ID), len(EXPERIMENTS_BY_NAME) - len(EXPERIMENTS_BY_ID), SEED_FILE))


_load_seed()


def _maybe_sleep():
    if INJECT_LATENCY_MS > 0:
        time.sleep(INJECT_LATENCY_MS / 1000.0)


def _resolve(exp_id, exp_name, entity_id):
    rec = None
    if exp_id and exp_id in EXPERIMENTS_BY_ID:
        rec = EXPERIMENTS_BY_ID[exp_id]
    elif exp_name and exp_name in EXPERIMENTS_BY_NAME:
        rec = EXPERIMENTS_BY_NAME[exp_name]
        exp_id = rec.get("id") or exp_id
    if rec is None:
        return exp_id, exp_name or "", DEFAULT_VARIANT
    variant_name = rec.get("variants", {}).get(entity_id, DEFAULT_VARIANT)
    return exp_id, rec.get("name", exp_name or ""), variant_name


def _one_evaluate(req):
    exp_id = req.get("experiment_id", "")
    exp_name = req.get("experiment_name", "")
    entity_id = req.get("id", "")
    resolved_id, resolved_name, variant_name = _resolve(exp_id, exp_name, entity_id)
    return {
        "id": req.get("id", ""),
        "project_id": "payouts",
        "experiment": {"id": resolved_id, "name": resolved_name, "exclusion_group_id": "", "updated_at": ""},
        "variant": {
            "id": "var_%s" % variant_name,
            "name": variant_name,
            "variables": [{"key": "result", "value": variant_name}],
            "experiment_id": resolved_id,
            "weight": 100,
            "region": "",
        },
        "Reason": "arena_deterministic_seed" if (exp_id in EXPERIMENTS_BY_ID or exp_name in EXPERIMENTS_BY_NAME)
                  else "arena_default_variant_unknown_experiment",
        "steps": ["whitelisting", "sampler", "exclusion", "audience", "assign_bucket"],
    }


def _evaluate(handler, body):
    _maybe_sleep()
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    return 200, _one_evaluate(req)


def _evaluate_bulk(handler, body):
    _maybe_sleep()
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    results = [_one_evaluate(r) for r in req.get("bulk_evaluate", [])]
    return 200, {"bulk_evaluate_response": results}


def _fetch_decision_context(handler, body):
    return 200, {"experiments": []}


def _allow_cors(handler, body):
    return 200, {}


ROUTES = {
    ("POST", "/twirp/rzp.splitz.evaluate.v1.EvaluateAPI/Evaluate"): _evaluate,
    ("POST", "/twirp/rzp.splitz.evaluate.v1.EvaluateAPI/EvaluateBulk"): _evaluate_bulk,
    ("POST", "/twirp/rzp.splitz.evaluate.v1.EvaluateAPI/FetchDecisionContext"): _fetch_decision_context,
    ("POST", "/twirp/rzp.splitz.evaluate.v1.EvaluateAPI/AllowCors"): _allow_cors,
}

if __name__ == "__main__":
    serve(ROUTES)
