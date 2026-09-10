#!/usr/bin/env python3
"""Fresh-identity fixture generator for the tenant-isolation calibration.

Every call mints a brand-new attacker tenant, victim tenant, victim-owned
resource, hidden canary and evidence namespace. NOTHING here is fixed: there is
no ``pout_1234``, no M1/M2 static merchant, no reused credential. Re-running
produces disjoint identities (the run id seeds all of them).

Two artifacts are produced per run:

  * the CONTROL-PLANE manifest (``fixture.json``) -- full ground truth, including
    the victim credential and the hidden canary. The judge/oracle reads this; the
    requester/reproducer never does.
  * the REQUESTER view is derived separately by ``replay.py`` and deliberately
    excludes the victim credential, the canary, and any mode/oracle internals.

Identity shapes are chosen to be visibly calibration-only and to not collide with
the arena's ARENAM9* provisioner range: ``ARENACALATK*`` / ``ARENACALVIC*``.
"""
import json
import secrets
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RUNS = REPO / "RED_LOOP" / "runs"


def _rand(n=10):
    return secrets.token_hex(n // 2 + 1)[:n].upper()


def generate_fixture(profile_target, port, run_id=None, seed_label="calib"):
    """Mint a fresh fixture. ``profile_target`` records the profile this fixture is
    meant to be served under ('regression' or 'fixed'); it does not itself change
    the identities, which are always freshly generated."""
    run_id = run_id or ("calibfx-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                        + "-" + _rand(6).lower())
    attacker_mid = "ARENACALATK" + _rand(8)
    victim_mid = "ARENACALVIC" + _rand(8)
    attacker_cred = "catk_" + secrets.token_hex(20)
    victim_cred = "cvic_" + secrets.token_hex(20)
    resource_id = "calres_" + secrets.token_hex(10)
    canary = "CANARY-" + secrets.token_hex(16).upper()

    fixture = {
        "run_id": run_id,
        "kind": "CALIBRATION_ONLY",
        "profile_target": profile_target,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "actors": {
            "attacker": {"merchant_id": attacker_mid, "credential": attacker_cred,
                         "role": "attacker"},
            "victim": {"merchant_id": victim_mid, "credential": victim_cred,
                       "role": "victim (credential hidden from requester)"},
        },
        "resources": [
            {"resource_id": resource_id, "owner_merchant_id": victim_mid,
             "type": "payout_destination",
             "payload": {
                 "beneficiary_name": "Victim Beneficiary " + victim_mid[-4:],
                 "account_number": "9" + _rand(13),
                 "ifsc": "HDFC000" + _rand(4),
                 "secret_canary": canary,
             }},
        ],
        # control-plane-only truth for the oracle (never handed to the requester):
        "canary": canary,
        "target_victim_resource_id": resource_id,
        "env_fingerprint": {
            "service": "RED_LOOP/calibration/tenant-isolation/service.py",
            "profile_target": profile_target, "port": port, "host": "127.0.0.1",
            "seed_label": seed_label,
        },
        "evidence_dir": str((RUNS / run_id).relative_to(REPO)),
    }
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "fixture.json").write_text(json.dumps(fixture, indent=2))
    return fixture


if __name__ == "__main__":
    import sys
    prof = sys.argv[1] if len(sys.argv) > 1 else "regression"
    fx = generate_fixture(prof, port=19099)
    print(json.dumps({"run_id": fx["run_id"], "attacker": fx["actors"]["attacker"]["merchant_id"],
                      "victim": fx["actors"]["victim"]["merchant_id"],
                      "resource": fx["target_victim_resource_id"],
                      "fixture": fx["evidence_dir"] + "/fixture.json"}, indent=2))
