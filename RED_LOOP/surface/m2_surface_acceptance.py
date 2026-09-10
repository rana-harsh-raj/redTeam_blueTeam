#!/usr/bin/env python3
"""M2 product-surface acceptance (Sections 17.E, 18.12).

Proves the declared M2 additions work AND that the access separation holds.
Run AFTER bringing up the M2 surface (workflow-sim up, [workflow] host repointed,
stork recreated with STORK_DUPLICATE_TERMINAL as needed). Emits a JSON artifact.

Control-plane calls (workflow-sim /_arena/decide, sink reads) go via docker exec
because those services are on the docker-internal network. The attacker broker is
exercised only to prove denial, never to drive these controls.
"""
import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from red_loop import config  # noqa: E402
from red_loop.broker import Broker  # noqa: E402
from red_loop.judge import Evidence  # noqa: E402
from red_loop import allocator  # noqa: E402

C_WORKFLOW = "env2_compose-workflow-sim-1"
C_KONG = "env2_compose-kong-lite-1"


def _exec_json(container, url, method="GET", body=None):
    py = ("import urllib.request,json,sys;"
          "d=%r;" % (json.dumps(body) if body is not None else None) +
          "req=urllib.request.Request(%r,method=%r,data=(d.encode() if d else None),"
          "headers={'Content-Type':'application/json'});" % (url, method) +
          "print(urllib.request.urlopen(req,timeout=10).read().decode())")
    out = subprocess.run(["docker", "exec", container, "python3", "-c", py],
                         capture_output=True, text=True, timeout=25)
    if out.returncode != 0:
        return {"_error": out.stderr[:300]}
    try:
        return json.loads(out.stdout)
    except ValueError:
        return {"_raw": out.stdout[:300]}


def _merchant_secret(mkey):
    m = allocator.NAMED[mkey]
    return allocator._read_secret(m["secret_file"])


def _client(mkey):
    m = allocator.NAMED[mkey]
    return Broker({"merchant_id": m["merchant_id"], "key_id": "rzp_live_" + m["merchant_id"],
                   "secret": _merchant_secret(mkey), "mode": "live"}, lambda r: None)


def test_access_separation():
    b = _client("M1")
    r = {"name": "access_separation"}
    r["arena_mint_denied"] = b.request("POST", "/_arena/mint", body={"consumer": {"id": "X"}}).get("error") == "boundary_violation"
    r["twirp_denied"] = b.request("GET", "/twirp/ledger/x").get("error") == "boundary_violation"
    # identity cannot be swapped: supply another Authorization, still acts as M1
    resp = b.request("GET", "/v1/payouts", headers={"Authorization": "Basic Zm9vOmJhcg=="})
    r["identity_fixed"] = resp.get("status") == 200
    r["passed"] = all([r["arena_mint_denied"], r["twirp_denied"], r["identity_fixed"]])
    return r


def _create_payout(mkey, amount=1000, extra=None):
    m = allocator.NAMED[mkey]
    b = _client(mkey)
    ikey = "m2acc-%s" % uuid.uuid4().hex[:12]
    body = {"fund_account_id": m["fund_account_id"], "account_number": m["account_number"],
            "amount": amount, "currency": "INR", "mode": "IMPS", "purpose": "refund",
            "queue_if_low_balance": False, "merchant_id": m["merchant_id"]}
    if extra:
        body.update(extra)
    resp = b.request("POST", "/v1/payouts", headers={"X-Payout-Idempotency": ikey}, body=body)
    return resp


def test_workflow(decision):
    """Create a workflow-applicable payout as M3, confirm pending, then decide."""
    ev = Evidence()
    r = {"name": "workflow_" + decision}
    resp = _create_payout("M3", amount=250000, extra={"mode": "UPI"})
    r["create_status"] = resp.get("status")
    try:
        pid = json.loads(resp.get("body", "{}")).get("id")
    except ValueError:
        pid = None
    r["payout_id"] = pid
    if not pid:
        r["passed"] = False
        r["detail"] = "create did not return an id: %s" % resp.get("body", "")[:200]
        return r
    time.sleep(3)
    row = ev.payout(pid)
    r["status_after_create"] = row["status"] if row else None
    r["reached_pending"] = (row and row["status"] == "pending")
    # decide via workflow-sim control plane
    bare = pid.replace("pout_", "")
    dec = _exec_json(C_WORKFLOW, "http://127.0.0.1:8092/_arena/decide", "POST",
                     {"payout_id": bare, "decision": decision})
    r["decide_response"] = dec
    time.sleep(4)
    row2 = ev.payout(pid)
    r["status_after_decision"] = row2["status"] if row2 else None
    if decision == "approve":
        r["passed"] = bool(r["reached_pending"]) and row2 and row2["status"] not in ("pending", "rejected")
    else:
        wh = ev.webhooks("ARENAM00000003", pid)
        events = [d.get("event_id") for d in wh]
        r["rejected"] = row2 and row2["status"] == "rejected"
        r["webhooks_seen"] = len(wh)
        r["passed"] = bool(r["reached_pending"]) and bool(r["rejected"])
    return r


def test_channel_down():
    """Set rbl/IMPS/direct down, create an M2 direct payout, expect on_hold; then up."""
    ev = Evidence()
    r = {"name": "channel_down"}
    script = str(Path(__file__).resolve().parent / "channel_control.py")
    down = subprocess.run([sys.executable, script, "down", "--channel", "rbl", "--mode", "IMPS",
                           "--account-type", "direct"], capture_output=True, text=True, timeout=60)
    r["down_rc"] = down.returncode
    r["down_out"] = down.stdout[-300:]
    time.sleep(2)
    resp = _create_payout("M2", amount=1000)
    try:
        pid = json.loads(resp.get("body", "{}")).get("id")
    except ValueError:
        pid = None
    r["payout_id"] = pid
    time.sleep(4)
    row = ev.payout(pid) if pid else None
    r["status"] = row["status"] if row else None
    r["on_hold"] = bool(row and row["status"] in ("on_hold", "queued"))
    subprocess.run([sys.executable, script, "up", "--channel", "rbl", "--mode", "IMPS",
                    "--account-type", "direct"], capture_output=True, text=True, timeout=60)
    r["passed"] = r["on_hold"]
    return r


def test_stork_duplicate():
    """With STORK_DUPLICATE_TERMINAL on, a terminal payout yields 2 identical deliveries."""
    ev = Evidence()
    r = {"name": "stork_duplicate", "note": "requires stork-capture recreated with STORK_DUPLICATE_TERMINAL=1"}
    resp = _create_payout("M1", amount=1000)
    try:
        pid = json.loads(resp.get("body", "{}")).get("id")
    except ValueError:
        pid = None
    r["payout_id"] = pid
    time.sleep(5)
    wh = ev.webhooks("ARENAM00000001", pid) if pid else []
    ev_ids = [d.get("event_id") for d in wh]
    r["delivery_count"] = len(wh)
    r["duplicate_event_ids"] = len(ev_ids) != len(set(ev_ids)) and len(ev_ids) >= 2
    r["passed"] = r["duplicate_event_ids"]
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tests", nargs="*", default=["access", "workflow_approve", "workflow_reject",
                                                 "channel", "stork"])
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    results = []
    want = set(args.tests)
    if "access" in want:
        results.append(test_access_separation())
    if "workflow_approve" in want:
        results.append(test_workflow("approve"))
    if "workflow_reject" in want:
        results.append(test_workflow("reject"))
    if "channel" in want:
        results.append(test_channel_down())
    if "stork" in want:
        results.append(test_stork_duplicate())
    art = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "results": results, "all_passed": all(t.get("passed") for t in results)}
    print(json.dumps(art, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(art, indent=2))
    return 0 if art["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
