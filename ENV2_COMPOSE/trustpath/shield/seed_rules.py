#!/usr/bin/env python3
"""M11: seed the REAL Shield with the arena's payout rules through Shield's own rule API
(app/router/router.go setRuleRoutes: POST /v1/merchants/:merchant_id/rules, BasicAuth over shieldAuthUser pairs).

The rules reproduce the semantics of the retired shield-stub fixture (seeds/shield/rules.json) as real govaluate
expressions over the payout input keys Shield receives (shield-sdk PayoutInput json tags become rule parameters,
app/services/ruler/ruler.go addParameters):
  1. beneficiary account number ending in 9999 -> block   (every merchant: seeded per merchant, see below)
  2. merchant ARENAM00000001 + payout_amount > 5000000 -> block   (the fixture's `purpose` variable is not a Shield parameter)
  3. merchant ARENAM00000003 -> review
Shield evaluates the merchant's store for the rulesets named by the caller plus "<entity_type>_primary" (ruler.go
addRulesets), so payout rules are seeded under ruleset "payout_primary", attached to the merchant. Idempotent: existing
expressions are not re-created (rules.unique_expression).

  python3 seed_rules.py apply                 # every merchant in the arena seed
  python3 seed_rules.py merchant <merchant_id>
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

SHIELD = os.environ.get("SHIELD_URL", "http://shield-web:8090")
USER = os.environ.get("SHIELDAUTHUSER_PAYOUT_USERNAME", "payouts")
PASS = os.environ.get("SHIELDAUTHUSER_PAYOUT_PASSWORD", "")
MERCHANTS = os.environ.get("SHIELD_MERCHANTS_FILE", "/app/seed/merchants.json")

SUFFIX_RULE = {"name": "arena_block_bene_acct_suffix_9999", "expression": "account_number =~ '9999$'", "action": "block",
               "description": "Beneficiary account ending 9999 => block (M11 real Shield rule reproducing seeds/shield/rules.json arena_block_bene_acct_suffix_9999)"}
# NOTE (M11 source-supported correction): the retired shield-stub fixture blocked M1 on `purpose == 'vendor_payments'`.
# The REAL Shield validates every rule variable against its parameter registry (app/services/params: HasParameter ->
# app/services/rule/validator.go "Invalid parameter [purpose]"); `purpose` is NOT a Shield parameter, so that fixture rule
# could never exist in production. The M1 velocity intent is expressed with a real payout parameter instead.
SPECIAL = {
    "ARENAM00000001": [{"name": "arena_m1_velocity_block", "expression": "payout_amount > 5000000", "action": "block",
                        "description": "M1 large payout (> 50,000 INR in paise) => block (replaces the fixture's non-existent `purpose` parameter; see seed_rules.py note)"}],
    "ARENAM00000003": [{"name": "arena_m3_review_new_beneficiary", "expression": "payout_amount > 0", "action": "review",
                        "description": "M3 => review verdict (reproduces arena_m3_review_new_beneficiary; payouts treats review as non-blocking)"}],
}


def call(method, path, body=None):
    req = urllib.request.Request(SHIELD + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Content-Type": "application/json", "Authorization": "Basic " + base64.b64encode(("%s:%s" % (USER, PASS)).encode()).decode()})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else {}
        except ValueError:
            return e.code, {"raw": raw.decode("utf-8", "replace")[:400]}


def wait(timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            st, _ = call("GET", "/v1/status")
            if st == 200:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    raise RuntimeError("shield not reachable at %s" % SHIELD)


def existing(mid):
    """Rules already attached to the merchant. GET /v1/merchants/:mid/rules sits behind middlewares.Authorize
    (Reports_Lead / FRM POC roles the payouts service identity does not carry -> 403); Shield's own
    rules.unique_expression key is then the idempotency guarantee (see merchant())."""
    st, doc = call("GET", "/v1/merchants/%s/rules" % mid)
    if st != 200:
        return {}
    items = doc.get("items") if isinstance(doc, dict) else None
    if items is None and isinstance(doc, dict):
        items = doc.get("rules") or doc.get("data") or []
    return {r.get("expression"): r for r in (items or []) if isinstance(r, dict)}


def merchant(mid):
    have = existing(mid)
    out = []
    for r in [SUFFIX_RULE] + SPECIAL.get(mid, []):
        if r["expression"] in have:
            out.append({"expression": r["expression"], "status": "exists", "id": have[r["expression"]].get("id")})
            continue
        # skip_canary: the rules API (controllers/rules.go Create -> rolloutManagement.CreateRule) creates a CANARY rule
        # with a 0% feature flag unless the request carries skip_canary=true (constants.SkipCanary); a canary rule at 0%
        # is never applied (ruler.go fetchUpdatedRuleWithCanaryContext), so the seed asks for the direct (stable) rule.
        # ruleset: ruler.go newSession -> addRulesets() evaluates the rulesets the CALLER names plus "<entity_type>_primary"
        # (and the merchant id as a ruleset name); payouts sends none (pkg/shield RuleSets: nil), so a payout rule is only
        # ever selected from the "payout_primary" ruleset. "primary" belongs to the payments entity.
        body = {"name": r["name"], "expression": r["expression"], "description": r["description"], "is_active": True,
                "ruleset": "payout_primary", "merchant_id": mid, "action": r["action"], "weight": 0, "type": "action", "skip_canary": True}
        st, doc = call("POST", "/v1/merchants/%s/rules" % mid, body)
        if st == 400 and "unique_expression" in json.dumps(doc):
            out.append({"expression": r["expression"], "status": "exists", "via": "rules.unique_expression"})
            continue
        out.append({"expression": r["expression"], "status": st, "response": json.dumps(doc)[:300]})
        if st not in (200, 201):
            print("rule create failed for %s: %s %s" % (mid, st, json.dumps(doc)[:300]), file=sys.stderr)
    return out


if __name__ == "__main__":
    wait()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "apply"
    if cmd == "apply":
        report = {}
        for mid in json.load(open(MERCHANTS)).get("merchants", {}):
            report[mid] = merchant(mid)
        bad = sum(1 for v in report.values() for r in v if r["status"] not in ("exists", 200, 201))
        print(json.dumps({"merchants": len(report), "failed": bad, "sample": dict(list(report.items())[:2])}))
        sys.exit(1 if bad else 0)
    elif cmd == "merchant":
        print(json.dumps(merchant(sys.argv[2])))
