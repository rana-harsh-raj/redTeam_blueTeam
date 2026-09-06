#!/usr/bin/env python3
"""Fresh-ID tenant-isolation calibration orchestrator (Milestone 3.1).

Runs the private, calibration-only reproduce-and-judge machinery on freshly
generated identities. Covers the gateway-INDEPENDENT gates deterministically:

  * fresh_id_calibration_fixture   -- fresh identities minted; no fixed IDs
  * fresh_id_deterministic_replay  -- one ID-free semantic bundle reproduces the
                                      effect across TWO disjoint regression fixtures
  * fresh_id_negative_control      -- the same bundle yields NO disclosure under
                                      the fixed profile (causal negative control)

The cross-provider gate is handled by ``cross_provider.py`` (gateway-gated) and
folded in here when its artifact is present.

Nothing here touches the real Kong gateway, Payouts, Ledger, FTS or CFA. The
calibration service binds to loopback on a dedicated port and is torn down at the
end of each phase. Default service behaviour (no CALIB_PROFILE) is the FIXED
profile; the regression profile is requested explicitly per phase.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
IMPL = REPO / "reports" / "implementation"
sys.path.insert(0, str(HERE))
import generator  # noqa: E402
import oracle  # noqa: E402
import replay  # noqa: E402


def _health(port, tries=30):
    for _ in range(tries):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/calib/health" % port,
                                        timeout=2) as r:
                if r.status == 200:
                    return json.loads(r.read())
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    return None


def _start_service(fixture, profile, port):
    env = dict(os.environ)
    env["CALIB_FIXTURE"] = str(REPO / fixture["evidence_dir"] / "fixture.json")
    env["CALIB_PROFILE"] = profile
    env["CALIB_PORT"] = str(port)
    proc = subprocess.Popen([sys.executable, str(HERE / "service.py")], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    h = _health(port)
    return proc, h


def _run_phase(fixture, profile, port):
    """Start the service in `profile`, execute the resolved bundle deterministically,
    judge, tear down. Returns (evidence, oracle_result, health)."""
    proc, health = _start_service(fixture, profile, port)
    try:
        endpoint = "http://127.0.0.1:%d" % port
        resolved = replay.resolve_bundle(fixture, endpoint)
        resolved_view = {k: v for k, v in resolved.items() if k != "ATTACKER_CREDENTIAL"}
        resolved_view["ATTACKER_CREDENTIAL"] = "<injected, redacted>"
        evidence = replay.deterministic_request(resolved)
        evidence["used_credential"] = fixture["actors"]["attacker"]["credential"]
        if profile == "regression":
            result = oracle.judge_regression(fixture, evidence)
        else:
            result = oracle.judge_fixed(fixture, evidence)
        # persist per-run evidence next to its fixture
        run_dir = REPO / fixture["evidence_dir"]
        (run_dir / "requester-evidence.json").write_text(json.dumps(
            {**evidence, "used_credential": "<redacted>"}, indent=2))
        (run_dir / "oracle-result.json").write_text(json.dumps(result, indent=2))
        (run_dir / "resolved-bundle.json").write_text(json.dumps(resolved_view, indent=2))
        return evidence, result, health, resolved_view
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()


def run_deterministic():
    out = {"kind": "CALIBRATION_ONLY", "milestone": "M3.1 fresh-ID tenant-isolation",
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "gateway_independent": True}

    # --- Gate 1: fresh fixture (two independent regression sets, disjoint IDs) ---
    fx1 = generator.generate_fixture("regression", port=19101)
    fx2 = generator.generate_fixture("regression", port=19102)
    ids1 = {fx1["actors"]["attacker"]["merchant_id"], fx1["actors"]["victim"]["merchant_id"],
            fx1["target_victim_resource_id"], fx1["canary"],
            fx1["actors"]["attacker"]["credential"]}
    ids2 = {fx2["actors"]["attacker"]["merchant_id"], fx2["actors"]["victim"]["merchant_id"],
            fx2["target_victim_resource_id"], fx2["canary"],
            fx2["actors"]["attacker"]["credential"]}
    no_fixed_ids = not any(x in json.dumps([fx1, fx2]) for x in ("pout_1234",
                           "ARENAM00000001", "ARENAM00000002"))
    out["fresh_id_calibration_fixture"] = {
        "passed": bool(ids1.isdisjoint(ids2)) and no_fixed_ids,
        "fixture_a_run": fx1["run_id"], "fixture_b_run": fx2["run_id"],
        "identities_disjoint": ids1.isdisjoint(ids2),
        "no_fixed_or_static_ids": no_fixed_ids,
        "attacker_a": fx1["actors"]["attacker"]["merchant_id"],
        "attacker_b": fx2["actors"]["attacker"]["merchant_id"],
        "victim_a": fx1["actors"]["victim"]["merchant_id"],
        "victim_b": fx2["actors"]["victim"]["merchant_id"],
    }

    # --- Gate 2: deterministic replay across the two disjoint fixtures ---
    ev1, r1, h1, rb1 = _run_phase(fx1, "regression", 19101)
    ev2, r2, h2, rb2 = _run_phase(fx2, "regression", 19102)
    out["fresh_id_deterministic_replay"] = {
        "passed": bool(r1["passed"] and r2["passed"]),
        "bundle_is_id_free": "<resolved per run>" in json.dumps(replay.SEMANTIC_BUNDLE),
        "set_a": {"run": fx1["run_id"], "status": ev1["status"],
                  "oracle_passed": r1["passed"], "canary_disclosed": r1["canary_disclosed"],
                  "checks": r1["checks"]},
        "set_b": {"run": fx2["run_id"], "status": ev2["status"],
                  "oracle_passed": r2["passed"], "canary_disclosed": r2["canary_disclosed"],
                  "checks": r2["checks"]},
        "different_ids_same_bundle": fx1["target_victim_resource_id"] != fx2["target_victim_resource_id"],
    }

    # --- Gate 3: fixed-profile negative control (fresh fixture) ---
    fx3 = generator.generate_fixture("fixed", port=19103)
    ev3, r3, h3, rb3 = _run_phase(fx3, "fixed", 19103)
    out["fresh_id_negative_control"] = {
        "passed": bool(r3["passed"]),
        "run": fx3["run_id"], "status": ev3["status"],
        "canary_disclosed": r3["canary_disclosed"], "checks": r3["checks"],
        "note": "same semantic bundle, fixed profile => not-found, no victim data",
    }

    out["certificate"] = oracle.impact_certificate(r1, r3, reproduced=r2["passed"],
                                                   rediscovery=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(IMPL / "m31-calibration-freshid.json"))
    args = ap.parse_args()
    det = run_deterministic()
    # fold in cross-provider artifact if present
    xp = IMPL / "m31-calibration-crossprovider.json"
    if xp.exists():
        det["fresh_id_cross_provider_replay"] = json.loads(xp.read_text())
    else:
        det["fresh_id_cross_provider_replay"] = {
            "passed": False, "status": "pending",
            "reason": "run cross_provider.py with LITELLM_API_KEY set"}
    IMPL.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(det, indent=2))
    summary = {
        "fresh_id_calibration_fixture": det["fresh_id_calibration_fixture"]["passed"],
        "fresh_id_deterministic_replay": det["fresh_id_deterministic_replay"]["passed"],
        "fresh_id_negative_control": det["fresh_id_negative_control"]["passed"],
        "fresh_id_cross_provider_replay": det["fresh_id_cross_provider_replay"].get("passed"),
        "classification": det["certificate"]["classification"],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
