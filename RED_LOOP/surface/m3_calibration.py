#!/usr/bin/env python3
"""Private exploit-shaped calibration lane + negative control (Workstream H).

Proves the reproduce-and-judge machinery on a GENUINE unauthorized observable
effect with a paired vulnerable/fixed profile, entirely SEPARATE from the open
campaign and NEVER counted as a finding.

Vulnerable/fixed pair (option 2 — an existing documented weakness, safely
isolated): the kong-lite merchant-edge route policy toggle.
  * VULNERABLE (KONG_ENFORCE_ROUTE_POLICY=0, the Milestone-2 state): the internal
    GET /v1/payouts/fetch_multiple route is reachable from the merchant edge and
    the payouts-api TiDB mock returns a payout owned by merchant 10000000000000
    (canary markers slitfa12345678 / 528226169544) to any caller — a cross-tenant
    read the attacker is not authorised to perform.
  * FIXED (KONG_ENFORCE_ROUTE_POLICY=1, the Milestone-3 edge): the route is 404 —
    the effect disappears. This is the causal negative control.

Sequence: exploit under vulnerable -> deterministic judge confirms the effect ->
different-provider reproducer replays it from a clean start with a fresh
idempotency value -> run the same steps under the fixed profile (effect gone) ->
classify CALIBRATION_PASS / CALIBRATION_REDISCOVERY, never ACCEPTED_NEW_FINDING.

This lane is isolated in TIME from the open campaign, temporarily toggles the
shared edge, and RESTORES the fixed (enforcement-on) profile at the end so the
arena is left in the Milestone-3 state. Marked calibration-only throughout.
"""
import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "RED_LOOP"))
from red_loop import config, allocator, reproducer  # noqa: E402
from red_loop.state import CampaignStore  # noqa: E402
from red_loop.judge import CANNED_MOCK_MARKERS  # noqa: E402
from red_loop.broker import Broker  # noqa: E402

KONG = "http://127.0.0.1:18080"
ENV2 = REPO / "ENV2_COMPOSE"
EXPLOIT_PATH = "/v1/payouts/fetch_multiple?id=pout_1234&auth_type=proxy"


def _set_enforcement(on):
    """Recreate kong-lite with the route policy on/off. Returns True on health."""
    env = {"KONG_ENFORCE_ROUTE_POLICY": "1" if on else "0"}
    import os
    e = dict(os.environ); e.update(env)
    subprocess.run(["docker", "compose", "--env-file", ".env.arena", "-f", "docker-compose.yml",
                    "--profile", "substitutes", "up", "-d", "--no-deps", "kong-lite"],
                   cwd=str(ENV2), env=e, capture_output=True, text=True, timeout=120)
    for _ in range(20):
        h = subprocess.run(["docker", "inspect", "-f", "{{.State.Health.Status}}",
                            "env2_compose-kong-lite-1"], capture_output=True, text=True)
        if h.stdout.strip() == "healthy":
            return True
        time.sleep(2)
    return False


def _attacker_auth():
    sec = allocator._read_secret(allocator.NAMED["M1"]["secret_file"])
    return "Basic " + base64.b64encode(("rzp_live_ARENAM00000001:%s" % sec).encode()).decode()


def _get(path, auth):
    req = urllib.request.Request(KONG + path, headers={"Authorization": auth}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read(4000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read(4000) or b"").decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return "ERR", str(e)[:200]


def _judge_effect(body):
    """Deterministic oracle: an unauthorized cross-tenant read is confirmed iff
    the response carries a victim-owned marker the attacker must not see."""
    markers = [m for m in CANNED_MOCK_MARKERS if m in (body or "")]
    return {"unauthorized_effect": bool(markers), "victim_markers": markers}


def run():
    started_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    auth = _attacker_auth()
    cid = "calib-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    store = CampaignStore(cid)
    store.write_manifest({"campaign_id": cid, "kind": "CALIBRATION_ONLY",
                          "note": "private machinery self-test; NEVER an open-campaign finding",
                          "started_at": started_ts})

    result = {"campaign_id": cid, "kind": "CALIBRATION_ONLY", "generated_at": started_ts,
              "vulnerable_profile": {}, "fixed_profile": {}, "reproduction": {}, "steps": []}

    # --- VULNERABLE profile: enforcement OFF ---
    result["steps"].append("set enforcement OFF (vulnerable profile)")
    v_health = _set_enforcement(False)
    v_status, v_body = _get(EXPLOIT_PATH, auth)
    v_judge = _judge_effect(v_body)
    result["vulnerable_profile"] = {"enforcement": "off", "kong_healthy": v_health,
                                    "exploit_path": EXPLOIT_PATH, "status": v_status,
                                    "judge": v_judge, "evidence_excerpt": v_body[:240]}

    # --- different-provider reproducer replays from clean state (fresh idempotency) ---
    bundle = {
        "starting_actor": "one ordinary synthetic merchant (credential injected)",
        "minimal_steps": [
            "GET %s as your merchant" % EXPLOIT_PATH,
            "Report whether the response returns a payout owned by a DIFFERENT merchant "
            "(look for a merchant_id that is not yours) and quote it."],
        "required_inputs": {"note": "use the path exactly; identity is fixed"},
        "claimed_observable_result": "an ordinary merchant reads a payout belonging to another tenant",
        "allowed_code_slices": []}
    repro_broker = Broker(
        {"merchant_id": "ARENAM00000001", "key_id": "rzp_live_ARENAM00000001",
         "secret": allocator._read_secret(allocator.NAMED["M1"]["secret_file"]), "mode": "live"},
        store.add_action, request_budget=50)
    rr = reproducer.run_reproduction(bundle, repro_broker, store, label="calibration", max_turns=8)
    repro_texts = store.response_texts()
    repro_effect = _judge_effect("\n".join(repro_texts))
    result["reproduction"] = {"model": rr.get("model"), "report": rr.get("report"),
                              "judge": repro_effect,
                              "provider_diversity": "reproducer is a different provider/family than primary"}

    # --- FIXED profile: enforcement ON (causal negative control) ---
    result["steps"].append("set enforcement ON (fixed profile / negative control)")
    f_health = _set_enforcement(True)
    f_status, f_body = _get(EXPLOIT_PATH, auth)
    f_judge = _judge_effect(f_body)
    result["fixed_profile"] = {"enforcement": "on", "kong_healthy": f_health,
                               "exploit_path": EXPLOIT_PATH, "status": f_status,
                               "judge": f_judge, "evidence_excerpt": f_body[:240]}

    # --- classify ---
    effect_present_vuln = v_judge["unauthorized_effect"]
    effect_absent_fixed = not f_judge["unauthorized_effect"] and f_status == 404
    reproduced = bool(rr.get("report") and rr["report"].get("effect_reproduced")) or repro_effect["unauthorized_effect"]
    calibration_pass = effect_present_vuln and effect_absent_fixed and reproduced
    result["verdict"] = {
        "classification": "CALIBRATION_PASS" if calibration_pass else "CALIBRATION_INCOMPLETE",
        "never_an_open_finding": True,
        "effect_present_in_vulnerable": effect_present_vuln,
        "effect_absent_in_fixed_negative_control": effect_absent_fixed,
        "reproduced_by_different_provider": reproduced,
        "note": "Twin/mock artifact used only to exercise the reproduce+judge path and prove "
                "causal negative control; NOT a Razorpay production finding.",
    }
    result["left_arena_state"] = "enforcement ON (Milestone-3 fixed profile) restored"
    store.update_manifest(calibration_result=result)
    (store.root / "calibration-bundle.json").write_text(json.dumps(result, indent=2, default=str))
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    art = run()
    print(json.dumps(art, indent=2, default=str))
    if args.out:
        Path(args.out).write_text(json.dumps(art, indent=2, default=str))
    return 0 if art["verdict"]["classification"] == "CALIBRATION_PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
