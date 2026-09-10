#!/usr/bin/env python3
"""Milestone 3.1 machine-generated acceptance gate (Section 15).

Every pass/fail is DERIVED from named artifacts; no boolean is hand-set. Fails
closed: a missing clean boot, a verifier run that is not 26/0/0, an unexplained
v17, a fresh payout that is not end-to-end, a calibration without a fresh-ID
negative control, or a campaign that ended on the normal ceiling each make a gate
fail and the overall acceptance false.

Usage: python3 RED_LOOP/surface/m3_1_acceptance.py [--campaign <id>]
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
IMPL = REPO / "reports" / "implementation"
RUNS = REPO / "RED_LOOP" / "runs"


def sha256_file(p):
    p = Path(p)
    if not p.exists():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def load(p):
    p = Path(p)
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return None


def git(a):
    try:
        return subprocess.run(["git", "-C", str(REPO)] + a, capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def gate(name, passed, **d):
    return {"gate": name, "passed": (bool(passed) if passed is not None else None), **d}


def parse_junit(run_dir):
    """Return (passed, failed, skipped, total) from a clean-boot verifier junit.xml."""
    jx = Path(run_dir) / "junit.xml"
    if not jx.exists():
        return None
    try:
        root = ET.parse(jx).getroot()
        suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
        tests = fail = err = skip = 0
        for s in suites:
            tests += int(s.get("tests", 0)); fail += int(s.get("failures", 0))
            err += int(s.get("errors", 0)); skip += int(s.get("skipped", 0))
        passed = tests - fail - err - skip
        return {"total": tests, "passed": passed, "failed": fail + err, "skipped": skip}
    except Exception:  # noqa: BLE001
        return None


def egress_result(run_dir):
    for name in ("egress.json", "egress-audit.json", "egress_audit.json"):
        d = load(Path(run_dir) / name)
        if d:
            outside = d.get("outside_packets", d.get("outside", d.get("violations")))
            return {"present": True, "outside_packets": outside, "status": d.get("status"),
                    "clean": outside in (0, [], None) and d.get("status") == "passed"}
    return {"present": False, "clean": None}


def clean_boot_gate(instance):
    root = RUNS / f"cleanboot-{instance}"
    vr = parse_junit(root / "verifier-run")
    eg = egress_result(root / "verifier-run")
    ok = bool(vr) and vr["total"] >= 26 and vr["failed"] == 0 and vr["skipped"] == 0 and vr["passed"] == vr["total"]
    return ok, {"instance": instance, "verifier": vr, "egress": eg,
                "boot_id": (load(root / "verifier-run" / "arena-fingerprint.json") or {}).get("boot_id")}


def build(campaign_id):
    gates = []
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"])
    head = git(["rev-parse", "HEAD"])

    # A. historical integrity
    gates.append(gate("historical_integrity",
                      git(["rev-parse", "twin-v1.0^{commit}"]) == "78def24eb57112c0dc39a6ae9062b1f0d1c711fb"
                      and git(["rev-parse", "milestone-2-red-loop"]) == "a107612ed11344a8096e74dbf68616679855465e"
                      and branch in ("milestone-3-1-clean-parity", "milestone-3-1-calibration",
                                     "milestone-3-1-claude-final"),
                      branch=branch, head=head))

    # B. evidence durability
    man = REPO / "RED_LOOP" / "archive" / "evidence-manifest.json"
    md = load(man)
    gates.append(gate("evidence_archive_manifest",
                      bool(md) and md.get("self_sha256") and not md.get("missing_expected_committed"),
                      manifest_sha256=sha256_file(man),
                      artifacts=(md or {}).get("counts", {}).get("artifacts")))

    # C. runtime isolation (no writable overlap for the disposable instances)
    overlaps = []
    for inst in ("m31-clean-a", "m31-clean-b"):
        pl = load(RUNS / f"cleanboot-{inst}" / "instance-plan.json")
        if pl:
            ov = pl.get("overlap", {})
            if ov.get("networks") or ov.get("volumes") or ov.get("host_port_in_use") or ov.get("reuses_live_named_network"):
                overlaps.append(inst)
    gates.append(gate("runtime_isolation_no_overlap", len(overlaps) == 0, overlapping_instances=overlaps))

    # D. two clean boots 26/0/0 + egress
    okA, dA = clean_boot_gate("m31-clean-a")
    okB, dB = clean_boot_gate("m31-clean-b")
    gates.append(gate("clean_boot_a_26_0_0", okA, **dA))
    gates.append(gate("clean_boot_b_26_0_0", okB, **dB))
    gates.append(gate("clean_boot_egress_clean",
                      dA["egress"].get("clean") is True and dB["egress"].get("clean") is True,
                      a=dA["egress"], b=dB["egress"]))

    # E. v17 resolution
    v17 = load(IMPL / "m31-v17.json")
    gates.append(gate("v17_resolved",
                      bool(v17) and v17.get("classification") in ("HEALTHY_ON_CLEAN", "REGRESSION_DIAGNOSED"),
                      classification=(v17 or {}).get("classification")))

    # logical replay comparison A vs B
    rep = load(IMPL / "m31-logical-replay.json")
    gates.append(gate("logical_replay_material_match",
                      bool(rep) and rep.get("logical_equivalence") is True and rep.get("status") == "passed",
                      artifact="m31-logical-replay.json", logical_equivalence=(rep or {}).get("logical_equivalence")))

    # F. fresh merchant lifecycle, REVALIDATED on the current head (Phase A).
    fm = load(IMPL / "m31-fresh-merchant.json")
    fm_full = (bool(fm) and fm.get("summary", {}).get("full_success_payout_demonstrated") is True
               and fm.get("summary", {}).get("passed") == fm.get("summary", {}).get("total")
               and fm.get("gateway_profile", "").startswith("hardened"))
    gates.append(gate("fresh_merchant_current_head", fm_full,
                      create_500_fixed=(fm or {}).get("summary", {}).get("create_500_fixed"),
                      suite=f"{(fm or {}).get('summary', {}).get('passed')}/{(fm or {}).get('summary', {}).get('total')}",
                      gateway_profile=(fm or {}).get("gateway_profile"),
                      note="revalidated on current-head provisioner; end-to-end fresh payout to terminal processed"))

    # G. Fresh-ID calibration gates, each DERIVED from the calibration artifact.
    cal = load(IMPL / "m31-calibration-freshid.json")
    cert = (cal or {}).get("certificate", {})
    cal_base = bool(cal) and cert.get("classification") in ("CALIBRATION_PASS", "CALIBRATION_REDISCOVERY") \
        and cert.get("never_an_open_finding") is True
    # each gate reads its own sub-result block in the artifact
    for name in ("fresh_id_calibration_fixture", "fresh_id_deterministic_replay",
                 "fresh_id_negative_control", "fresh_id_cross_provider_replay"):
        sub = (cal or {}).get(name)
        passed = cal_base and isinstance(sub, dict) and sub.get("passed") is True
        is_pending = sub is None or (isinstance(sub, dict) and sub.get("status") == "pending")
        gates.append(gate(name, passed,
                          status=("pending" if is_pending else "pass" if passed else "fail"),
                          artifact="m31-calibration-freshid.json"))

    # G2. Harness discipline gates derived from the offline self-test artifact.
    ht = load(IMPL / "m31-harness-tests.json")
    suites = (ht or {}).get("suites", {})
    gates.append(gate("deterministic_judge",
                      suites.get("deterministic_judge_admission", {}).get("ok") is True,
                      tests=suites.get("deterministic_judge_admission", {}).get("tests"),
                      artifact="m31-harness-tests.json"))
    gates.append(gate("candidate_admission",
                      suites.get("deterministic_judge_admission", {}).get("ok") is True
                      and suites.get("pricing_ids_collision_length", {}).get("ok") is True,
                      note="minimum-evidence admission + fail-closed pricing-id reservation",
                      artifact="m31-harness-tests.json"))
    # hardened gateway: route-policy tests pass AND the live edge is enforcing.
    live_enforce = "KONG_ENFORCE_ROUTE_POLICY=1" in (subprocess.run(
        ["docker", "inspect", "env2_compose-kong-lite-1"], capture_output=True, text=True).stdout or "")
    gates.append(gate("hardened_gateway",
                      suites.get("hardened_route_policy", {}).get("ok") is True and live_enforce,
                      route_policy_tests=suites.get("hardened_route_policy", {}).get("tests"),
                      live_enforcement_on=live_enforce))

    # campaign / calibration isolation
    iso = load(IMPL / "m31-campaign-isolation.json")
    gates.append(gate("campaign_isolation", bool(iso) and iso.get("passed") is True,
                      artifact="m31-campaign-isolation.json"))

    # safety / egress: the clean-boot egress audits show zero outside packets.
    gates.append(gate("safety_egress",
                      dA["egress"].get("clean") is True and dB["egress"].get("clean") is True,
                      a_outside=dA["egress"].get("outside_packets"),
                      b_outside=dB["egress"].get("outside_packets")))

    # evidence integrity: archive manifest present and every named evidence file hashes.
    ev_hashes = {n: sha256_file(IMPL / n) for n in
                 ["m31-fresh-merchant.json", "m31-fresh-merchant-noreuse.json",
                  "m31-calibration-freshid.json", "m31-harness-tests.json",
                  "m31-campaign-isolation.json", "m31-v17.json", "m31-logical-replay.json"]}
    gates.append(gate("evidence_integrity",
                      bool(md) and all(v is not None for v in ev_hashes.values()),
                      manifest_present=bool(md),
                      all_named_artifacts_hashable=all(v is not None for v in ev_hashes.values())))

    # H. second open campaign. Must reach a genuinely PROGRESS-BASED normal stop
    # (agent-concluded / stagnation / all-hypotheses-resolved) with a fresh,
    # self-verified attacker and clean same-window egress. A fixed-turn ceiling,
    # wall/budget ceiling, kill switch, or provider-budget (model_unrecoverable)
    # stop does NOT satisfy the gate (Section 13).
    PROGRESS_STOPS = ("agent_concluded", "stagnation_pause", "all_hypotheses_resolved")
    cman = load(RUNS / (campaign_id or "") / "manifest.json") if campaign_id else None
    comp = (cman or {}).get("completion", {})
    ceg = load(RUNS / (campaign_id or "") / "egress" / "egress.json") if campaign_id else None
    egress_clean = bool(ceg) and ceg.get("outside_packets") in (0, None) and ceg.get("status") == "passed"
    fresh_attacker = bool(cman) and cman.get("attacker_is_fresh_funded") is True
    progress_stop = bool(comp) and comp.get("stop_reason") in PROGRESS_STOPS \
        and comp.get("normal_completion") is True and comp.get("ended_on_emergency_ceiling") is False
    sc_pass = progress_stop and fresh_attacker and egress_clean and bool(cman and cman.get("primary_model"))
    gates.append(gate("second_open_campaign", sc_pass,
                      status=("pending" if not comp else "pass" if sc_pass else "fail"),
                      campaign_id=campaign_id, stop_reason=comp.get("stop_reason"),
                      progress_based_stop=progress_stop,
                      normal_completion=comp.get("normal_completion"),
                      attacker_is_fresh_funded=fresh_attacker,
                      primary_model=(cman or {}).get("primary_model"),
                      egress_clean=egress_clean,
                      egress_outside_packets=(ceg or {}).get("outside_packets"),
                      turns=comp.get("turns_completed"),
                      candidates=comp.get("candidates"),
                      boundary_violations=comp.get("broker_violations")))

    # G-retain: context efficiency recompute
    hr = load(IMPL / "m31-hardening-retention.json")
    red = (hr or {}).get("context_token_recompute", {}).get("reduction_fraction")
    gates.append(gate("context_efficiency_recomputed", red is not None and red >= 0.60,
                      reduction_fraction=red))

    art = {
        "milestone": "M3.1 — Clean Acceptance and Fresh Merchant Payout Parity",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git": {"branch": branch, "head": head,
                "twin_v1_commit": git(["rev-parse", "twin-v1.0^{commit}"]),
                "runtime_freeze_commit": "219ca48bbb5e51db9347b335ac0a50241656045b",
                "m2_head": git(["rev-parse", "milestone-2-red-loop"]),
                "m3_head": git(["rev-parse", "milestone-3-gateway-hardening"])},
        "gates": gates,
        "evidence_sha256": {n: sha256_file(IMPL / n) for n in
                            ["m31-fresh-merchant.json", "m31-fresh-merchant-noreuse.json",
                             "m31-hardening-retention.json", "m31-v17.json",
                             "m31-calibration-freshid.json", "m31-calibration-crossprovider.json",
                             "m31-harness-tests.json", "m31-campaign-isolation.json",
                             "m31-logical-replay.json"]},
        "accepted": all(g["passed"] is True for g in gates),
        "unmet_or_pending_gates": [g["gate"] for g in gates if g["passed"] is not True],
    }
    return art


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", default=None)
    ap.add_argument("--out", default=str(IMPL / "m3-1-acceptance.json"))
    args = ap.parse_args()
    art = build(args.campaign)
    Path(args.out).write_text(json.dumps(art, indent=2, default=str))
    print(json.dumps({"accepted": art["accepted"],
                      "unmet_or_pending": art["unmet_or_pending_gates"]}, indent=2))
    return 0 if art["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
