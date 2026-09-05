#!/usr/bin/env python3
"""pricing-stub: thin alias, NOT wired into any real client config in this
arena. See CONTRACT.md "Is this stub used?" section for the full grounding:
payouts' ccSdk client boots with `mock = true`
(findings/28_build_spike_payouts.md's confirmed required fix), which makes
`pkg/ccSdk.Initialize` build only `NewMockPayoutFeeCalculatorClient()` --
no HTTP client, no configured host, ever constructed. The real pricing
source for this arena is `monolith-stub`'s `/payouts_service/fetch_pricing_info`
(payouts' `[api]` client, always live). This stub reads the SAME seed file
(`seeds/pricing.json`) so if a future patched payouts binary DOES dial a
`[ccSdk] ... governor.hostname`-shaped config at this stub, it answers
consistently with monolith-stub rather than drifting.
"""
import json
import os
import sys

sys.path.insert(0, "/app")
from _common.base_stub import serve, _log  # noqa: E402

PRICING_FILE = os.environ.get("PRICING_SEED_FILE", "/app/seed/pricing.json")

PLANS = {}
OVERRIDES = {}


def _load_seed():
    global PLANS, OVERRIDES
    if not os.path.exists(PRICING_FILE):
        _log("no seed file at %s -- pricing-stub always returns the flat default" % PRICING_FILE)
        return
    with open(PRICING_FILE) as f:
        data = json.load(f)
    PLANS = data.get("plans", {})
    OVERRIDES = data.get("special_case_overrides", {})
    _log("loaded %d pricing plan(s) from %s" % (len(PLANS), PRICING_FILE))


_load_seed()


def _fetch_pricing_info(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    mid = req.get("merchant_id", "")
    mode = req.get("mode") or req.get("payout_mode", "")
    purpose = req.get("purpose", "")
    fee_type = req.get("fee_type", "")
    if purpose == "rzp_fees":
        rule = OVERRIDES.get("purpose_rzp_fees", {"fees": 0, "tax": 0})
    elif purpose == "refund":
        rule = OVERRIDES.get("purpose_refund", {"fees": 0, "tax": 0})
    elif fee_type == "free_payout":
        rule = OVERRIDES.get("fee_type_free_payout", {"fees": 0, "tax": 0})
    else:
        rule = PLANS.get(mid, {}).get("rules", {}).get(mode, {"fees": 200, "tax": 36})
    return 200, {"fee": rule.get("fees", 200), "tax": rule.get("tax", 36),
                 "pricing_rule_id": PLANS.get(mid, {}).get("plan_id", "arena_default_pricing")}


ROUTES = {
    ("POST", "/fetch_pricing_info"): _fetch_pricing_info,
}

if __name__ == "__main__":
    serve(ROUTES)
