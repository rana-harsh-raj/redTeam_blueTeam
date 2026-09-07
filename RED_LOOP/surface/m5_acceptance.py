#!/usr/bin/env python3
"""Milestone 5 acceptance evaluator.

Evaluates the M5 passing metrics as a fixed gate set against the canonical
evidence under reports/implementation/. Every gate declares a validation_mode:
  direct_artifact  — reads a produced JSON/CSV evidence file
  direct_git       — inspects committed git state
  source_inspection— asserts a source/label file exists with required content
  direct_runtime   — (reserved) a live check

Writes reports/implementation/m5-acceptance.json and exits 0 iff all mandatory
gates pass. Stdlib only; safe to run from a clean checkout (dry-run reads the
committed evidence rather than re-running the campaign).
"""
import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
IMPL = REPO / "reports" / "implementation"

# metric thresholds (from the milestone spec)
T = {"hypotheses": 15, "experiments": 30, "baselines": 10, "meaningful": 5,
     "reallocations": 3, "recovered": 1, "defects": 3, "families": 2}

REQUIRED_SCENARIOS = [
    "approval_success", "rejection", "cancellation", "expiry",
    "duplicate_approval", "duplicate_callback", "stale_decision",
    "concurrent_decisions", "restart_recovery", "cross_org_denial",
    "maker_checker_separation", "callback_identity",
]


def load(name):
    p = IMPL / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def git(args):
    try:
        return subprocess.run(["git", "-C", str(REPO)] + args,
                              capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


def evaluate():
    gates = []

    def g(gid, desc, mode, passed, detail=""):
        gates.append({"id": gid, "desc": desc, "validation_mode": mode,
                      "passed": bool(passed), "detail": str(detail)})

    # --- engine + fidelity ------------------------------------------------
    eng = REPO / "ENV2_COMPOSE" / "substitutes" / "workflow-engine"
    core = (eng / "wfengine" / "core.py")
    g("M5-01", "workflow decision engine present", "source_inspection",
      core.exists() and (eng / "wfengine" / "server.py").exists(),
      "wfengine core+server")
    fid = eng / "FIDELITY.md"
    g("M5-02", "engine labelled high-fidelity RECONSTRUCTION", "source_inspection",
      fid.exists() and "RECONSTRUCTION" in (fid.read_text() if fid.exists() else ""),
      "FIDELITY.md")

    # --- scenarios --------------------------------------------------------
    scn = load("m5-workflow-scenarios.json")
    g("M5-03", "workflow scenario suite all-passed", "direct_artifact",
      scn and scn.get("all_passed") and scn.get("total", 0) >= 12,
      f"{scn.get('passed') if scn else '?'}/{scn.get('total') if scn else '?'}")
    passed_scn = {r["scenario"]: r["passed"] for r in (scn or {}).get("results", [])}
    for i, name in enumerate(REQUIRED_SCENARIOS, start=4):
        g(f"M5-{i:02d}", f"scenario {name} passed e2e (not skipped)",
          "direct_artifact", passed_scn.get(name) is True, name)

    # --- benchmark integrity ---------------------------------------------
    bi = load("m5-benchmark-integrity.json")
    g("M5-16", "4 mutants built (fixed reference + 4)", "direct_artifact",
      bi and bi.get("mutants") == 4, f"mutants={bi.get('mutants') if bi else '?'}")
    g("M5-17", "fixed reference violates no invariant", "direct_artifact",
      bi and bi.get("fixed_reference_clean"), "")
    g("M5-18", "each mutant isolated to its own family", "direct_artifact",
      bi and bi.get("each_mutant_isolated"), "")
    g("M5-19", ">=4 invariant families represented", "direct_artifact",
      bi and len(bi.get("families", [])) >= 4,
      f"families={len(bi.get('families', [])) if bi else '?'}")

    # --- campaign quality (metric 3) -------------------------------------
    cs = load("m5-campaign-summary.json")
    def cm(k, default=0):
        v = (cs or {}).get(k)
        return v if v is not None else default
    g("M5-20", f">={T['hypotheses']} unique hypotheses", "direct_artifact",
      cm("unique_hypotheses") >= T["hypotheses"], cm("unique_hypotheses"))
    g("M5-21", f">={T['experiments']} nonduplicate experiments reached service",
      "direct_artifact", cm("experiments_reached") >= T["experiments"],
      cm("experiments_reached"))
    g("M5-22", f">={T['baselines']} valid baseline/control operations",
      "direct_artifact", cm("baseline_ops") >= T["baselines"], cm("baseline_ops"))
    g("M5-23", f">={T['meaningful']} observation-driven experiment changes",
      "direct_artifact", cm("meaningful_changes") >= T["meaningful"],
      cm("meaningful_changes"))
    g("M5-24", f">={T['reallocations']} strategic reallocations", "direct_artifact",
      cm("strategic_reallocations") >= T["reallocations"], cm("strategic_reallocations"))
    g("M5-25", f">={T['recovered']} recovered worker interruption", "direct_artifact",
      cm("recovered_workers") >= T["recovered"], cm("recovered_workers"))
    g("M5-26", "no uncontrolled repeated-invalid-request loop", "direct_artifact",
      cs is not None and cm("duplicates_suppressed") >= 0
      and _no_runaway_loop(), "dedup active")

    # --- blind benchmark result (metric 4) -------------------------------
    g("M5-27", f">={T['defects']} of 4 hidden defects discovered", "direct_artifact",
      cm("defect_count") >= T["defects"],
      f"{cm('defect_count')} defects: {cm('distinct_defects_found')}")
    g("M5-28", f"findings span >={T['families']} invariant families",
      "direct_artifact", len(cm("invariant_families_found", [])) >= T["families"],
      cm("invariant_families_found"))
    g("M5-29", "zero accepted findings against the fixed reference",
      "direct_artifact", cs is not None and cm("false_findings_on_fixed_reference") == 0,
      cm("false_findings_on_fixed_reference"))

    # --- independent verification (metric 6) -----------------------------
    findings = cm("confirmed_findings", [])
    all_verified = bool(findings) and all(
        f.get("verification", {}).get("verdict") == "CONFIRMED"
        and f["verification"].get("reproduced_twice")
        and f["verification"].get("negative_control_holds")
        for f in findings)
    g("M5-30", "every finding independently verified + fresh-ID reproduced",
      "direct_artifact", all_verified, f"{len(findings)} findings")
    distinct_rc = {f.get("root_cause") for f in findings}
    g("M5-31", "findings deduplicated by root cause", "direct_artifact",
      len(distinct_rc) == len(findings), f"{len(distinct_rc)} root causes")
    cross_model = any(len(f.get("reproduced_by", [])) >= 2 for f in findings)
    g("M5-32", "at least one finding reproduced by >=2 models", "direct_artifact",
      cross_model, "cross-model reproduction")

    # --- safety (metric 7) -----------------------------------------------
    sf = load("m5-safety-egress.json")
    g("M5-33", "safety/egress attestation passes", "direct_artifact",
      sf and sf.get("pass"), "")
    g("M5-34", "services loopback-only", "direct_artifact",
      sf and sf.get("loopback_only_services"), "")
    g("M5-35", "single external host is the model gateway", "direct_artifact",
      sf and sf.get("single_external_host_is_model_gateway"), "")

    # --- git (metric 8) --------------------------------------------------
    tag_commit = git(["rev-list", "-n1", "assurance-m5-autonomous-discovery"])
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"])
    head = git(["rev-parse", "HEAD"])
    # accept the milestone branch OR a clean (detached) checkout of the tag
    g("M5-36", "on milestone-5 branch or at the assurance-m5 tag", "direct_git",
      "milestone-5" in branch or (bool(tag_commit) and head == tag_commit),
      branch if "milestone-5" in branch else f"detached@{head[:8]}")
    g("M5-37", "annotated tag assurance-m5-autonomous-discovery exists",
      "direct_git", bool(tag_commit), tag_commit[:12])
    g("M5-38", "evidence artifacts tracked in git", "direct_git",
      "m5-campaign-summary.json" in git(["ls-files", "reports/implementation"]),
      "")

    passed = sum(1 for x in gates if x["passed"])
    total = len(gates)
    result = {
        "milestone": "M5 — Autonomous discovery on maker-checker workflows",
        "passed": passed, "total": total, "accepted": passed == total,
        "thresholds": T,
        "gates": gates,
    }
    return result


def _no_runaway_loop():
    """No uncontrolled repeated-invalid-request loop (the M4 failure mode).

    A runaway loop = the SAME rejected request resent many times without the
    Director suppressing it. Legitimate authorized operations (status 200:
    creates, gets, approvals) and dedup-suppressed repeats are NOT a runaway.
    Property holds iff (a) dedup suppression was active, and (b) no un-suppressed
    INVALID (non-200) request signature was executed more than 6 times."""
    led = IMPL / "m5-task-ledger.jsonl"
    if not led.exists():
        return False
    invalid = {}
    suppressed = 0
    for line in led.read_text().splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("kind") != "step":
            continue
        res = r.get("result", {}) or {}
        if res.get("duplicate_suppressed"):
            suppressed += 1
            continue
        if r.get("status") == 200:
            continue  # a valid authorized operation, not an invalid loop
        k = (r.get("env"), r.get("tool"), r.get("identity"),
             r.get("status"), res.get("error"))
        invalid[k] = invalid.get(k, 0) + 1
    max_invalid_repeat = max(invalid.values()) if invalid else 0
    return suppressed > 0 and max_invalid_repeat <= 6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="evaluate committed evidence (default behaviour)")
    ap.add_argument("--out", default=str(IMPL / "m5-acceptance.json"))
    args = ap.parse_args()
    res = evaluate()
    Path(args.out).write_text(json.dumps(res, indent=2))
    for x in res["gates"]:
        mark = "PASS" if x["passed"] else "FAIL"
        print(f"[{mark}] {x['id']} {x['desc']} ({x['detail']})")
    print(f"\n{res['passed']}/{res['total']} gates  accepted={res['accepted']}  -> {args.out}")
    return 0 if res["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
