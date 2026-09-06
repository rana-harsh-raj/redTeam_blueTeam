#!/usr/bin/env python3
"""Deterministic calibration oracle.

The ORACLE, not any model, decides the result. It reads the control-plane fixture
(ground truth: canary, victim ownership) and the requester's captured
request/response evidence, and returns an impact certificate.

A calibration effect is classified only as CALIBRATION_PASS or
CALIBRATION_REDISCOVERY -- NEVER ACCEPTED_NEW_FINDING.
"""
import json

# A minimal template-response detector: a canned/static reply would not carry the
# freshly generated, per-run canary or the freshly generated victim ownership.
# We additionally reject a response that echoes obvious placeholder text.
_CANNED_MARKERS = ("<resolved per run>", "TARGET_VICTIM_RESOURCE", "example", "lorem")


def _parse(body):
    try:
        return json.loads(body)
    except Exception:  # noqa: BLE001
        return None


def judge_regression(fixture, evidence):
    """Regression PASS iff every condition holds (all deterministic)."""
    canary = fixture["canary"]
    victim_mid = fixture["actors"]["victim"]["merchant_id"]
    attacker_cred = fixture["actors"]["attacker"]["credential"]
    victim_cred = fixture["actors"]["victim"]["credential"]
    target = fixture["target_victim_resource_id"]
    body = evidence.get("response_body", "") or ""
    obj = _parse(body)

    checks = {
        "victim_resource_exists": any(r["resource_id"] == target
                                      for r in fixture["resources"]),
        "resource_belongs_to_victim": any(
            r["resource_id"] == target and r["owner_merchant_id"] == victim_mid
            for r in fixture["resources"]),
        "authenticated_as_attacker": attacker_cred in (evidence.get("used_credential",
                                                                     attacker_cred),),
        "no_victim_credential_used": victim_cred not in json.dumps(evidence),
        "status_200": evidence.get("status") == 200,
        "response_contains_canary": canary in body,
        "response_owner_is_victim": bool(obj and obj.get("owner_merchant_id") == victim_mid),
        "response_not_canned": bool(body) and not any(m in body for m in _CANNED_MARKERS),
        "request_response_artifacts_present": bool(evidence.get("url") and body),
    }
    passed = all(checks.values())
    return {"mode": "regression", "passed": passed, "checks": checks,
            "canary_disclosed": canary in body,
            "victim_owner_seen": bool(obj and obj.get("owner_merchant_id") == victim_mid)}


def judge_fixed(fixture, evidence):
    """Fixed PASS (negative control) iff the same request yields NO disclosure."""
    canary = fixture["canary"]
    victim_mid = fixture["actors"]["victim"]["merchant_id"]
    victim_cred = fixture["actors"]["victim"]["credential"]
    target = fixture["target_victim_resource_id"]
    body = evidence.get("response_body", "") or ""
    obj = _parse(body)

    checks = {
        "victim_resource_exists": any(r["resource_id"] == target
                                      for r in fixture["resources"]),
        "same_actor_class_and_request": evidence.get("request_route", "").endswith(target),
        "canary_absent": canary not in body,
        "victim_owner_field_absent": not bool(obj and obj.get("owner_merchant_id") == victim_mid),
        "expected_forbidden_or_not_found": evidence.get("status") in (403, 404),
        "no_victim_credential_used": victim_cred not in json.dumps(evidence),
    }
    passed = all(checks.values())
    return {"mode": "fixed", "passed": passed, "checks": checks,
            "canary_disclosed": canary in body}


def impact_certificate(regression_result, fixed_result, reproduced, rediscovery=True,
                       extra=None):
    """Build the machine-readable calibration impact certificate."""
    effect_present = bool(regression_result and regression_result["passed"])
    effect_absent_fixed = bool(fixed_result and fixed_result["passed"])
    calibration_pass = effect_present and effect_absent_fixed and bool(reproduced)
    cert = {
        "kind": "CALIBRATION_ONLY",
        "classification": ("CALIBRATION_REDISCOVERY" if rediscovery else "CALIBRATION_PASS")
                          if calibration_pass else "CALIBRATION_INCOMPLETE",
        "never_an_open_finding": True,
        "effect_present_in_regression": effect_present,
        "effect_absent_in_fixed_negative_control": effect_absent_fixed,
        "reproduced": bool(reproduced),
        "note": "Self-contained tenant-isolation fixture used ONLY to exercise the "
                "fresh-ID reproduce-and-judge machinery and prove a causal negative "
                "control. NOT a Razorpay production finding.",
    }
    if extra:
        cert.update(extra)
    return cert


if __name__ == "__main__":
    import sys
    fx = json.load(open(sys.argv[1]))
    ev = json.load(open(sys.argv[2]))
    print(json.dumps(judge_regression(fx, ev), indent=2))
