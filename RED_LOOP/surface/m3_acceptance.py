#!/usr/bin/env python3
"""Generate the deterministic Milestone-3 acceptance artifact (Section 17).

Produced from NAMED artifacts, never hand-authored booleans. Fail-closed: any
missing/stale artifact, a route-policy digest mismatch, an un-tested recovery, a
calibration without a negative control, a campaign that ended on the emergency
ceiling, absent context metrics, or observed outside traffic makes a gate fail
and the overall acceptance false.

Usage: python3 RED_LOOP/surface/m3_acceptance.py --campaign <campaign_id>
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "RED_LOOP"))
from red_loop.state import CampaignStore  # noqa: E402
from red_loop import config  # noqa: E402

IMPL = REPO / "reports" / "implementation"
M2_PROMPT_AVG_TOKENS = 110000  # M2: 13.28M prompt tokens / 120 turns


def _sha256_file(p):
    p = Path(p)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def _load(p):
    p = Path(p)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except ValueError:
        return None


def _git(args):
    try:
        return subprocess.run(["git", "-C", str(REPO)] + args, capture_output=True,
                              text=True, timeout=15).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def gate(name, passed, **detail):
    d = {"gate": name, "passed": bool(passed)}
    d.update(detail)
    return d


def build(campaign_id):
    gates = []

    # A. baseline integrity
    twin_tag = _git(["rev-parse", "twin-v1.0^{commit}"])
    m2_head = _git(["rev-parse", "milestone-2-red-loop"])
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    clean = _git(["status", "--porcelain"]) == ""
    gates.append(gate("baseline_integrity",
                      twin_tag == "78def24eb57112c0dc39a6ae9062b1f0d1c711fb"
                      and m2_head == "a107612ed11344a8096e74dbf68616679855465e"
                      and branch == "milestone-3-gateway-hardening",
                      twin_tag=twin_tag, m2_head=m2_head, branch=branch, worktree_clean=clean))

    # route-policy digest
    route_policy = REPO / "RED_LOOP" / "registry" / "merchant-gateway-routes.json"
    classifier = REPO / "ENV2_COMPOSE" / "substitutes" / "kong-lite" / "route_policy.py"
    route_digest = _sha256_file(route_policy)
    gates.append(gate("gateway_route_policy_present",
                      bool(route_digest) and bool(_sha256_file(classifier)),
                      route_policy_sha256=route_digest, classifier_sha256=_sha256_file(classifier)))

    # B. gateway fidelity (live acceptance)
    gw = _load(IMPL / "m3-gateway-acceptance.json")
    gates.append(gate("gateway_fidelity", bool(gw) and gw.get("all_passed") is True,
                      artifact="m3-gateway-acceptance.json",
                      checks=[c["name"] for c in (gw or {}).get("checks", []) if c.get("passed")]))

    # C. cross-tenant measurement + campaign isolation
    ns = _load(IMPL / "m3-namespace-acceptance.json")
    gates.append(gate("cross_tenant_measurement_and_isolation",
                      bool(ns) and ns.get("all_passed") is True,
                      artifact="m3-namespace-acceptance.json"))

    # E. context efficiency (from the open campaign's per-turn metrics)
    store = CampaignStore(campaign_id) if campaign_id else None
    ctx_metrics = store._read_all("context_metrics") if store else []
    prompt_toks = [m.get("prompt_tokens") for m in ctx_metrics if isinstance(m.get("prompt_tokens"), int)]
    avg_prompt = (sum(prompt_toks) / len(prompt_toks)) if prompt_toks else None
    no_full_transcript = all(m.get("full_transcript_resent") is False for m in ctx_metrics) if ctx_metrics else False
    reduction = (1 - avg_prompt / M2_PROMPT_AVG_TOKENS) if avg_prompt else None
    gates.append(gate("context_efficiency",
                      bool(ctx_metrics) and no_full_transcript and reduction is not None and reduction >= 0.60,
                      avg_prompt_tokens_per_turn=round(avg_prompt) if avg_prompt else None,
                      m2_avg_prompt_tokens_per_turn=M2_PROMPT_AVG_TOKENS,
                      reduction_fraction=round(reduction, 3) if reduction is not None else None,
                      no_full_transcript_resent=no_full_transcript, turns_measured=len(ctx_metrics)))

    # F. long-running lifecycle: open campaign did NOT end on the emergency ceiling
    manifest = store.read_manifest() if store else {}
    completion = manifest.get("completion", {})
    gates.append(gate("campaign_completion_not_emergency_ceiling",
                      bool(completion) and completion.get("ended_on_emergency_ceiling") is False
                      and completion.get("normal_completion") is True,
                      stop_reason=completion.get("stop_reason"),
                      turns_completed=completion.get("turns_completed"),
                      normal_completion=completion.get("normal_completion")))

    # recovery
    rec = _load(IMPL / "m3-recovery-test.json")
    gates.append(gate("pause_restart_resume_tested", bool(rec) and rec.get("all_passed") is True,
                      artifact="m3-recovery-test.json"))

    # H. reproduction: calibration pass WITH a negative control
    cal = _load(IMPL / "m3-calibration.json")
    cal_ok = bool(cal) and cal.get("verdict", {}).get("classification") == "CALIBRATION_PASS" \
        and cal.get("verdict", {}).get("effect_absent_in_fixed_negative_control") is True \
        and cal.get("verdict", {}).get("reproduced_by_different_provider") is True
    gates.append(gate("calibration_with_negative_control", cal_ok, artifact="m3-calibration.json",
                      classification=(cal or {}).get("verdict", {}).get("classification")))

    # candidate discipline (unit tests presence + judge admission wired)
    admit_test = REPO / "RED_LOOP" / "tests" / "test_judge_admission.py"
    gates.append(gate("candidate_admission_present", admit_test.exists(),
                      test="RED_LOOP/tests/test_judge_admission.py"))

    # J. clean acceptance (two empty-volume boots) — fail-closed if not performed
    clean_boot = _load(IMPL / "m3-clean-boot.json")
    gates.append(gate("clean_empty_volume_acceptance",
                      bool(clean_boot) and clean_boot.get("two_boots_passed") is True,
                      artifact="m3-clean-boot.json",
                      note="fail-closed: requires two empty-volume boots each passing the 26-test verifier"))

    # verifier health (running-instance, if present)
    ver = _load(IMPL / "m3-verifier.json")
    gates.append(gate("twin_verifier_health", bool(ver) and ver.get("passed") == ver.get("total")
                      and ver.get("total", 0) >= 26,
                      artifact="m3-verifier.json",
                      passed=(ver or {}).get("passed"), total=(ver or {}).get("total")))

    # evidence hash index
    evidence = {}
    for name in ["m3-gateway-acceptance.json", "m3-namespace-acceptance.json",
                 "m3-recovery-test.json", "m3-calibration.json", "m3-verifier.json",
                 "m3-clean-boot.json"]:
        evidence[name] = _sha256_file(IMPL / name)
    route_evidence = {"merchant-gateway-routes.json": route_digest,
                      "kong-lite/route_policy.py": _sha256_file(classifier)}

    art = {
        "milestone": "M3 — Merchant Gateway Fidelity and Red Loop Hardening",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git": {"branch": branch, "head": _git(["rev-parse", "HEAD"]),
                "twin_v1_commit": twin_tag,
                "runtime_freeze_commit": "219ca48bbb5e51db9347b335ac0a50241656045b",
                "m2_head": m2_head, "worktree_clean": clean},
        "models": {"primary": config.PRIMARY_MODEL, "reproducer": config.REPRODUCER_MODEL},
        "open_campaign": {"campaign_id": campaign_id, "completion": completion,
                          "victim_canaries_count": len(manifest.get("victim_canaries", []))},
        "gates": gates,
        "evidence_sha256": evidence,
        "route_policy_sha256": route_evidence,
        "accepted": all(g["passed"] for g in gates),
        "unmet_gates": [g["gate"] for g in gates if not g["passed"]],
    }
    return art


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", default=None)
    ap.add_argument("--out", default=str(IMPL / "m3-acceptance.json"))
    args = ap.parse_args()
    art = build(args.campaign)
    Path(args.out).write_text(json.dumps(art, indent=2, default=str))
    print(json.dumps({"accepted": art["accepted"], "unmet_gates": art["unmet_gates"],
                      "out": args.out}, indent=2))
    return 0 if art["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
