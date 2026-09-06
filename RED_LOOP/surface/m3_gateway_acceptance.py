#!/usr/bin/env python3
"""M3 merchant-gateway fidelity acceptance (live, against kong-lite).

Proves, against the running twin with KONG_ENFORCE_ROUTE_POLICY=1:
  A. public merchant routes still work end to end (create + fetch own);
  B. internal/admin/workflow routes that share the /v1/payouts prefix are
     unreachable (404 no_route) from an ordinary merchant;
  C. the exact Milestone-2 canned-victim path (fetch_multiple) no longer
     returns fabricated cross-tenant data;
  D. client-supplied identity headers cannot swap the authenticated merchant.

Uses a DIRECT authenticated client (not the RED_LOOP broker) so it can probe
internal paths the broker would refuse, to observe kong-lite's own 404. Emits
a JSON artifact. Exit 0 iff every check passes.
"""
import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KONG = "http://127.0.0.1:18080"
M1_SECRET = REPO / "ENV2_COMPOSE" / "secrets" / "merchant_arena_m1_secret.txt"
CANNED_MARKERS = ("slitfa12345678", "10000000000000", "528226169544")

# public routes that must remain reachable (not necessarily 200; must NOT be 404 no_route)
PUBLIC_PROBES = [
    ("GET", "/v1/payouts"),
    ("GET", "/v1/payouts/payouts_status_reason_map"),
]
# internal/admin routes that must be 404 (unreachable from the merchant edge)
INTERNAL_PROBES = [
    ("GET", "/v1/payouts/fetch_multiple?id=pout_1234&auth_type=proxy"),
    ("POST", "/v1/payouts/manual_action"),
    ("POST", "/v1/payouts/rzp_fees_payout"),
    ("GET", "/v1/payouts/analytics"),
    ("POST", "/v1/payouts/payouts_internal/pout_x/approve"),
    ("GET", "/v1/payouts/payouts_internal/pout_x"),
    ("POST", "/v1/payouts/bulk"),
    ("GET", "/v1/internal/non_terminal_payouts/ARENABAL000002"),
    ("POST", "/v1/admin/merchant-configuration"),
    ("POST", "/v1/workflow/state"),
    ("POST", "/v1/notify/health/update"),
    ("GET", "/v1/contacts"),
    ("POST", "/twirp/ledger/x"),
]


def _auth():
    sec = M1_SECRET.read_text().strip()
    return "Basic " + base64.b64encode(("rzp_live_ARENAM00000001:%s" % sec).encode()).decode()


def _call(method, path, auth, body=None, hdr=None):
    h = {"Authorization": auth}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if hdr:
        h.update(hdr)
    req = urllib.request.Request(KONG + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read(4000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read(4000) or b"").decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return "ERR", str(e)[:200]


def run():
    auth = _auth()
    checks = []

    # C(pre): is enforcement actually active? fetch_multiple must be 404 and carry no canned markers.
    fm_status, fm_body = _call("GET", "/v1/payouts/fetch_multiple?id=pout_1234&auth_type=proxy", auth)
    canned_leak = any(m in fm_body for m in CANNED_MARKERS)
    checks.append({"name": "enforcement_active",
                   "passed": fm_status == 404 and not canned_leak,
                   "fetch_multiple_status": fm_status,
                   "canned_markers_present": canned_leak})

    # B: internal routes unreachable (404)
    internal = []
    for m, p in INTERNAL_PROBES:
        st, body = _call(m, p, auth)
        leak = any(mk in body for mk in CANNED_MARKERS)
        internal.append({"method": m, "path": p, "status": st, "canned_leak": leak,
                         "ok": st == 404 and not leak})
    checks.append({"name": "internal_routes_unreachable",
                   "passed": all(x["ok"] for x in internal), "probes": internal})

    # A: public routes reachable (not 404 no_route)
    pub = []
    for m, p in PUBLIC_PROBES:
        st, _ = _call(m, p, auth)
        pub.append({"method": m, "path": p, "status": st, "ok": st != 404})
    # full create + fetch own
    ik = "m3acc-" + uuid.uuid4().hex[:12]
    body = {"fund_account_id": "fa_ARENAFAX000001", "account_number": "2323230099999999",
            "amount": 1000, "currency": "INR", "mode": "IMPS", "purpose": "refund",
            "queue_if_low_balance": False, "merchant_id": "ARENAM00000001"}
    cst, cbody = _call("POST", "/v1/payouts", auth, body, {"X-Payout-Idempotency": ik})
    pid = None
    try:
        pid = json.loads(cbody).get("id")
    except ValueError:
        pass
    fetch_ok = False
    fetch_status = None
    own_merchant = None
    if pid:
        fst, fbody = _call("GET", "/v1/payouts/" + pid, auth)
        fetch_status = fst
        try:
            own_merchant = json.loads(fbody).get("merchant_id")
        except ValueError:
            pass
        fetch_ok = (fst == 200 and own_merchant == "ARENAM00000001")
    checks.append({"name": "public_routes_functional",
                   "passed": all(x["ok"] for x in pub) and cst == 200 and bool(pid) and fetch_ok,
                   "probes": pub, "create_status": cst, "payout_id": pid,
                   "fetch_status": fetch_status, "fetched_merchant": own_merchant})

    # D: identity header spoof ignored (passport wins)
    spoof_ok = None
    spoof_merchant = None
    if pid:
        sst, sbody = _call("GET", "/v1/payouts/" + pid, auth,
                           hdr={"x-merchant-id": "ARENAM00000002", "X-Entity-Id": "ARENAM00000002"})
        try:
            spoof_merchant = json.loads(sbody).get("merchant_id")
        except ValueError:
            pass
        spoof_ok = (sst == 200 and spoof_merchant == "ARENAM00000001")
    checks.append({"name": "identity_header_cannot_swap_merchant",
                   "passed": bool(spoof_ok), "returned_merchant": spoof_merchant,
                   "note": "supplied victim x-merchant-id/X-Entity-Id; passport consumer wins"})

    art = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "gateway": KONG, "enforcement_expected": True,
           "checks": checks, "all_passed": all(c["passed"] for c in checks)}
    return art


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    art = run()
    print(json.dumps(art, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(art, indent=2))
    return 0 if art["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
