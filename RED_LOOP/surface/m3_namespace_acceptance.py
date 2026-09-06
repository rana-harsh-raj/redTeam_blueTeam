#!/usr/bin/env python3
"""M3 fresh-namespace + canary isolation acceptance (Workstream B / Section 7D).

Proves, against the running twin:
  1. attacker cannot legitimately access victim resources (GET a victim payout
     id as the attacker -> not a 200 with victim data);
  2. victim canary values do NOT appear in normal attacker responses;
  3. two consecutive campaigns get disjoint canary sets and disjoint victim
     payout namespaces (campaign A state absent from campaign B's view);
  4. provisioning records provenance (judge-only canary set is populated).

Emits JSON; exit 0 iff all pass.
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
sys.path.insert(0, str(REPO / "RED_LOOP"))
from red_loop import provisioner, allocator  # noqa: E402

KONG = "http://127.0.0.1:18080"


def _attacker_auth():
    sec = allocator._read_secret(allocator.NAMED["M1"]["secret_file"])
    return "Basic " + base64.b64encode(("rzp_live_ARENAM00000001:%s" % sec).encode()).decode()


def _get(path, auth):
    req = urllib.request.Request(KONG + path, headers={"Authorization": auth}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read(6000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read(6000) or b"").decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return "ERR", str(e)[:200]


def run(per_victim=1):
    auth = _attacker_auth()
    checks = []

    # provision two campaigns' worth of victim canaries
    cidA = "camp-%s-aaa111" % time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    cidB = "camp-%s-bbb222" % time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    A = provisioner.mint_victim_canaries(cidA, per_victim=per_victim)
    B = provisioner.mint_victim_canaries(cidB, per_victim=per_victim)

    setA, setB = set(A["victim_canaries"]), set(B["victim_canaries"])
    victim_pids_A = [r["payout_id"] for r in A["victim_records"] if r.get("payout_id")]
    victim_pids_B = [r["payout_id"] for r in B["victim_records"] if r.get("payout_id")]

    checks.append({"name": "canaries_provisioned",
                   "passed": len(setA) >= 1 and len(setB) >= 1,
                   "campaignA_canaries": len(setA), "campaignB_canaries": len(setB)})

    # 1 + 2: attacker cannot read victim payouts; no canary in attacker responses
    attacker_bodies = []
    victim_read_leak = False
    for pid in victim_pids_A[:4]:
        st, body = _get("/v1/payouts/" + pid, auth)
        attacker_bodies.append(body)
        # a leak is a 200 that returns the victim's payout with its canary
        if st == 200 and any(c in body for c in setA):
            victim_read_leak = True
    # attacker's own list must not contain any victim canary
    st, own_list = _get("/v1/payouts?count=100", auth)
    attacker_bodies.append(own_list)
    canary_in_attacker = any(c in "\n".join(attacker_bodies) for c in (setA | setB))
    checks.append({"name": "attacker_cannot_read_victim_payout",
                   "passed": not victim_read_leak,
                   "victim_payout_ids_probed": victim_pids_A[:4]})
    checks.append({"name": "no_victim_canary_in_attacker_responses",
                   "passed": not canary_in_attacker})

    # 3: disjoint namespaces across campaigns
    disjoint_canaries = setA.isdisjoint(setB)
    disjoint_pids = set(victim_pids_A).isdisjoint(set(victim_pids_B))
    checks.append({"name": "campaigns_have_disjoint_namespaces",
                   "passed": disjoint_canaries and disjoint_pids,
                   "disjoint_canaries": disjoint_canaries, "disjoint_payout_ids": disjoint_pids})

    art = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "campaignA": cidA, "campaignB": cidB,
           "note": "canary values and victim payout ids are judge-only; the primary red "
                   "agent never receives them",
           "checks": checks, "all_passed": all(c["passed"] for c in checks)}
    return art


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--per-victim", type=int, default=1)
    args = ap.parse_args()
    art = run(per_victim=args.per_victim)
    print(json.dumps(art, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(art, indent=2))
    return 0 if art["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
