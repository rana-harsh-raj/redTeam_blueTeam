"""Per-campaign namespace allocation (Section 9D).

The only fully-funded merchants in the frozen twin are the three named fixtures
(M1 shared, M2 direct/rbl, M3 shared/workflow); the ~93 generated merchants are
identity-only. So a campaign allocates:
  attacker  = M1 (ordinary: no elevated finance roles) -- its key IS given to red
  victims   = M2, M3 -- funded control tenants whose keys are NOT given to red
Isolation across campaigns is by time-window (since_ts) plus per-campaign
idempotency/payout namespacing and opening-balance snapshots, since the funded
fixtures are shared. This limitation is declared in the fidelity statement.
"""
import json
from pathlib import Path

from . import config

NAMED = {
    "M1": {"merchant_id": "ARENAM00000001", "secret_file": "merchant_arena_m1_secret.txt",
           "archetype": "shared", "account_number": "2323230099999999",
           "fund_account_id": "fa_ARENAFAX000001", "balance_id": "ARENABAL000001", "roles": []},
    "M2": {"merchant_id": "ARENAM00000002", "secret_file": "merchant_arena_m2_secret.txt",
           "archetype": "direct", "account_number": "2323230000000002",
           "fund_account_id": "fa_ARENAFAX000002", "balance_id": "ARENABAL000002", "roles": []},
    "M3": {"merchant_id": "ARENAM00000003", "secret_file": "merchant_arena_m3_secret.txt",
           "archetype": "workflow", "account_number": "2323230099999999",
           "fund_account_id": "fa_ARENAFAX000003", "balance_id": "ARENABAL000003",
           "roles": ["finance_l1", "finance_l2", "owner"]},
}


def _read_secret(secret_file):
    p = config.ENV2 / "secrets" / secret_file
    if not p.exists():
        p = config.MERCHANT_KEYS_DIR / secret_file
    return p.read_text().strip() if p.exists() else ""


def allocate(attacker_key="M1", victim_keys=("M2", "M3")):
    a = NAMED[attacker_key]
    secret = _read_secret(a["secret_file"])
    if not secret:
        raise RuntimeError("attacker merchant secret not found for %s" % attacker_key)
    attacker = {
        "merchant_id": a["merchant_id"],
        "key_id": "rzp_live_" + a["merchant_id"],
        "secret": secret,   # control-plane only; never given to the agent
        "mode": "live",
        "archetype": a["archetype"], "roles": a["roles"],
        "account_number": a["account_number"], "fund_account_id": a["fund_account_id"],
    }
    victims = []
    for vk in victim_keys:
        v = NAMED[vk]
        victims.append({"key": vk, "merchant_id": v["merchant_id"], "archetype": v["archetype"],
                        "roles": v["roles"], "balance_id": v["balance_id"]})
    actor_merchants = [a["merchant_id"]] + [v["merchant_id"] for v in victims]
    return {"attacker": attacker, "victims": victims, "actor_merchants": actor_merchants}


def public_attacker_view(attacker):
    """What the agent is allowed to know about itself -- no secret."""
    return {"merchant_id": attacker["merchant_id"], "key_id": attacker["key_id"],
            "mode": attacker["mode"]}
