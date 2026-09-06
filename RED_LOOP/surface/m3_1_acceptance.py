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
                      and branch in ("milestone-3-1-clean-parity", "milestone-3-1-calibration"),
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

    # F. fresh merchant lifecycle
    fm = load(IMPL / "m31-fresh-merchant.json")
    fm_full = bool(fm) and fm.get("summary", {}).get("full_success_payout_demonstrated") is True
    gates.append(gate("fresh_merchant_full_success", fm_full,
                      create_500_fixed=(fm or {}).get("summary", {}).get("create_500_fixed"),
                      note="requires an end-to-end successful fresh payout (terminal processed)"))

    # G. Separate evidence gates; missing evidence remains explicitly pending.
    cal = load(IMPL / "m31-calibration-freshid.json")
    cal_base = (bool(cal) and cal.get("classification") in
                ("CALIBRATION_PASS", "CALIBRATION_REDISCOVERY") and cal.get("fresh_ids") is True)
    for name in ("fresh_id_calibration_fixture", "fresh_id_deterministic_replay",
                 "fresh_id_cross_provider_replay", "fresh_id_negative_control"):
        evidence = (cal or {}).get("checks", {}).get(name)
        passed = cal_base and isinstance(evidence, dict) and evidence.get("passed") is True
        if name == "fresh_id_negative_control":
            passed = passed and cal.get("effect_absent_in_fixed_control") is True
        if name == "fresh_id_cross_provider_replay":
            passed = passed and cal.get("reproduced_by_different_provider") is True
        gates.append(gate(name, passed, status=("pending" if evidence is None else
                                               "pass" if passed else "fail"),
                          artifact="m31-calibration-freshid.json"))

    # H. second open campaign (not ended on normal ceiling)
    store_manifest = load(RUNS / (campaign_id or "") / "manifest.json") if campaign_id else None
    comp = (store_manifest or {}).get("completion", {})
    gates.append(gate("second_open_campaign",
                      bool(comp) and comp.get("ended_on_emergency_ceiling") is False and comp.get("normal_completion") is True,
                      status=("pending" if not comp else "pass" if
                              comp.get("ended_on_emergency_ceiling") is False and
                              comp.get("normal_completion") is True else "fail"),
                      campaign_id=campaign_id, stop_reason=comp.get("stop_reason"),
                      turns=comp.get("turns_completed")))

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
                            ["m31-fresh-merchant.json", "m31-hardening-retention.json",
                             "m31-v17.json", "m31-calibration-freshid.json", "m31-logical-replay.json"]},
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
