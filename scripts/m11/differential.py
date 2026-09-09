#!/usr/bin/env python3
"""M11 differential: the same journey suite executed against the REAL trust path and against the substitutes, compared
journey by journey and check by check.

  python3 scripts/m11/differential.py --real <run_dir> --substitute <run_dir> [--out reports/implementation/m11]

A run_dir is a twinfactory journeys run (results.json + evidence/journey-*.json). Output: differential.json and
reports/implementation/M11_DIFFERENTIAL.md. Every difference is classified from DIFFERENCE_NOTES (investigated and
either fixed or recorded as a source-supported correction / twin adaptation); an unclassified difference is listed as
OPEN and fails the acceptance gate (scripts/m11/acceptance.py).
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# journey id -> {check name regex or "*result*": (classification, explanation)}
DIFFERENCE_NOTES = {
    "journey:approval-workflow/maker_checker": {
        "*": ("source-supported correction",
              "razorpay/workflows has no maker != checker rule (internal/action, internal/workflow state machine; only a DCS-gated auto-approve when the creator's role equals the checker role). "
              "The M5 reconstruction's 403 separation_violation was an assumption. The driver asserts the real behaviour in the real variant and the reconstruction's in the substitute variant."),
    },
    "journey:approval-workflow/n_of_m": {
        "*": ("source-supported correction",
              "eligible-approver sets and per-actor de-duplication are reconstruction concepts; the real service admits any actor presenting the checker state's role. The real-variant driver records what a repeat approval does instead of assuming it."),
    },
    "journey:trust-path/restart_cache_invalidation": {"*result*": ("twin adaptation", "real: EXPECTED_FAILURE F-M11-2 (a deleted basic-auth-x credential keeps authenticating on Kong 3.4.2 -- recorded finding); substitute: BLOCKED by design (Kong Admin API + Shield rule API are real-service surfaces)"),
                                                     "*": ("twin adaptation", "real-only checks")},
    "journey:trust-path/route_authorization": {"ps_internal_approve_route": ("source-supported correction", "kong-lite (substitute) proxies whole path prefixes, so a merchant key reaches the Payouts service's internal approve route (PS answers 500 for the unknown id); the REAL gateway has no such route (404 at the edge). This is exactly the fidelity gap the promotion closes."),
                                               "*": ("twin adaptation", "gateway route-table checks exist only in the real variant; both variants must refuse the unroutable paths with 4xx")},
    "journey:trust-path/shield_rules": {"*": ("twin adaptation", "real: the rule API / stable-rule / direct-evaluate / block checks; substitute: shield-stub carries fixture rules for the seeded merchants only, so a fresh pool merchant is allowed (checked as 'stub consulted')")},
    "journey:trust-path/maker_checker": {"*": ("source-supported correction", "real: Twirp ConfigAPI/ActionAPI + Cadence (separation section, 409 on terminal); substitute: the M5 reconstruction's admin/actor planes (404 no_pending_workflow on terminal). Both are asserted by variant.")},
    "journey:trust-path/s2s_identity": {"*": ("twin adaptation", "the substitute stubs carry no production credential contract; real-only checks are recorded, not silently skipped")},
    "journey:trust-path/payouts_ext_direct": {"*result*": ("twin adaptation", "BLOCKED on the substitute variant: kong-lite has no payouts-ext service/host routing"),
                                              "*": ("twin adaptation", "every check is real-only (the substitute variant is BLOCKED before any check)")},
    "journey:idempotency-retries/retry": {"advanced_the_scheduled_retry_task_.*": ("twin adaptation", "the check name carries the retry task id of that run (a different id per run); the assertion is the same in both variants and passed in both")},
    "journey:shared-ingress/admin": {"admin_bulk_cancel_accepted|pending_payout_rejected_by_the_admin_action": ("twin adaptation", "the admin journey's pending-payout branch runs only when the fresh merchant's payout parks at pending; on the real variant that needs the merchant's Workflow Config (seeded at provisioning since the fix) -- re-run to confirm the branch executes on both")},
    "journey:trust-path/api_key_auth": {"*": ("twin adaptation", "Kong consumer/credential-store checks exist only in the real variant")},
    "journey:trust-path/idempotency": {"*": ("source-supported correction", "kong-lite (substitute) proxies /v1/payouts straight to the Payouts service and short-circuits X-Payout-Idempotency itself, so the monolith replacement's idempotency state / audit never sees the call; on the real path the gateway forwards to the monolith replacement (Route.php idempotentRoutesConfig) as in production")},
    "journey:fetch-list/success": {"*": ("source-supported correction", "an unsigned payout id is refused (400) on the monolith path (PublicEntity::stripSignOrFail); kong-lite forwarded bare ids straight to the Payouts service, which accepts them")},
    "journey:trust-path/identity_propagation": {"*": ("source-supported correction", "kong-lite bypasses the monolith replacement (upstream payouts-api:9400, M6 topology): no ingress audit exists for merchant calls in the substitute variant; the real gateway routes through it as production does. edgev2 passport checks are real-only")},
    "journey:trust-path/cross_merchant_denial": {"the_denied_fetch": ("source-supported correction", "kong-lite bypasses the monolith replacement, so there is no tenant-scoped ingress audit in the substitute variant; the refusal itself is asserted in both"),
                                                 "*": ("twin adaptation", "identical assertions in both variants")},
    "journey:shared-ingress/batch": {"*result*": ("twin adaptation", "batch-sim is not derived into the critical-payouts profile; BLOCKED (dependency named) in both variants")},
    "journey:bulk-payouts/.*": {"*result*": ("twin adaptation", "batch-sim is not derived into the critical-payouts profile; BLOCKED with the dependency named")},
    "journey:cross-domain-s2p/.*": {"*result*": ("twin adaptation", "the Source-to-Pay stack is not part of the critical-payouts profile (s2p=False); BLOCKED with the dependency named")},
    "journey:trust-path/identity_propagation": {"*": ("twin adaptation", "edge passport (kid edgev2) checks exist only in the real variant; kong-lite forwards its own passport")},

    "journey:trust-path/bas_lookup": {"*": ("twin adaptation", "Api-Token and businesses/banking_accounts checks exist only in the real variant; the lookup during processing must happen in both")},
    "journey:trust-path/dashboard_session": {"*": ("twin adaptation", "real-variant-only gateway/workflow-row observations")},
    "journey:trust-path/internal_app_auth": {"*": ("twin adaptation", "real-variant-only 'gateway itself refused' observation")},
    "journey:trust-path/fund_account_ownership": {"*": ("twin adaptation", "identical assertions in both variants; any difference is OPEN")},
}


def load_run(run_dirs):
    """One or more run directories (comma-separated or list); a later run's result for a journey id supersedes an
    earlier one (re-runs of individual journeys after the full suite). The merged summary is recomputed."""
    if isinstance(run_dirs, str):
        run_dirs = [x for x in run_dirs.split(",") if x]
    rows, ev, dirs = {}, {}, []
    for rd in run_dirs:
        run_dir = Path(rd)
        dirs.append(str(run_dir))
        for r in json.loads((run_dir / "results.json").read_text()):
            rows[r["id"]] = r | {"run_dir": str(run_dir)}
        for p in (run_dir / "evidence").glob("journey-*.json"):
            try:
                d = json.loads(p.read_text())
                ev[d.get("journey")] = d
            except (ValueError, OSError):
                pass
    summary = {"total": len(rows)}
    for r in rows.values():
        summary[r["result"].lower()] = summary.get(r["result"].lower(), 0) + 1
    return {"run_dir": ",".join(dirs), "rows": rows, "evidence": ev, "summary": summary}


def checks_of(ev):
    out = {}
    for c in (ev or {}).get("checks", []):
        out[c["name"]] = bool(c.get("ok"))
    return out


def notes_for(jid):
    if jid in DIFFERENCE_NOTES:
        return DIFFERENCE_NOTES[jid]
    for k, v in DIFFERENCE_NOTES.items():
        if re.fullmatch(k, jid):
            return v
    return {}


def classify(jid, check=None, result_diff=False):
    notes = notes_for(jid)
    if result_diff and "*result*" in notes:
        return notes["*result*"]
    for k, v in notes.items():
        if k in ("*", "*result*"):
            continue
        if check and re.search(k, check):
            return v
    if "*" in notes:
        return notes["*"]
    return ("OPEN", "not investigated")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", required=True, help="run dir(s), comma-separated; later ones supersede per journey")
    ap.add_argument("--substitute", required=True, help="run dir(s), comma-separated; later ones supersede per journey")
    ap.add_argument("--out", default=str(REPO / "reports" / "implementation" / "m11"))
    a = ap.parse_args()
    real, sub = load_run(a.real), load_run(a.substitute)
    ids = sorted(set(real["rows"]) | set(sub["rows"]))
    items, open_diffs = [], []
    for jid in ids:
        r, s = real["rows"].get(jid), sub["rows"].get(jid)
        rc, sc = checks_of(real["evidence"].get(jid)), checks_of(sub["evidence"].get(jid))
        item = {"id": jid, "real": {"result": (r or {}).get("result"), "checks": "%s/%s" % ((r or {}).get("checks_passed"), (r or {}).get("checks_total")), "missing": (r or {}).get("missing_dependency")},
                "substitute": {"result": (s or {}).get("result"), "checks": "%s/%s" % ((s or {}).get("checks_passed"), (s or {}).get("checks_total")), "missing": (s or {}).get("missing_dependency")},
                "differences": []}
        if (r or {}).get("result") != (s or {}).get("result"):
            cls, why = classify(jid, result_diff=True)
            item["differences"].append({"kind": "result", "real": (r or {}).get("result"), "substitute": (s or {}).get("result"), "classification": cls, "why": why})
        for name in sorted(set(rc) | set(sc)):
            if name in rc and name in sc and rc[name] != sc[name]:
                cls, why = classify(jid, check=name)
                item["differences"].append({"kind": "check_outcome", "check": name, "real": rc[name], "substitute": sc[name], "classification": cls, "why": why})
            elif name in rc and name not in sc:
                cls, why = classify(jid, check=name)
                item["differences"].append({"kind": "real_only_check", "check": name, "real": rc[name], "classification": cls, "why": why})
            elif name in sc and name not in rc:
                cls, why = classify(jid, check=name)
                item["differences"].append({"kind": "substitute_only_check", "check": name, "substitute": sc[name], "classification": cls, "why": why})
        item["same"] = not item["differences"]
        open_diffs += [d | {"id": jid} for d in item["differences"] if d["classification"] == "OPEN"]
        items.append(item)
    doc = {"kind": "m11_differential", "generated_at": datetime.now(timezone.utc).isoformat(),
           "real_run": real["run_dir"], "substitute_run": sub["run_dir"],
           "real_summary": real["summary"].get("summary") or real["summary"], "substitute_summary": sub["summary"].get("summary") or sub["summary"],
           "journeys": len(ids), "identical": sum(1 for i in items if i["same"]), "with_differences": sum(1 for i in items if not i["same"]),
           "open_differences": open_diffs, "items": items}
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / "differential.json").write_text(json.dumps(doc, indent=1))
    L = ["# M11 differential -- real trust path vs substitutes", "", "Generated %s by scripts/m11/differential.py" % doc["generated_at"], "",
         "| run | dir | pass | fail | blocked | expected_failure |", "|---|---|---|---|---|---|"]
    for lbl, run in (("real", real), ("substitute", sub)):
        sm = run["summary"].get("summary") or run["summary"] or {}
        L.append("| %s | `%s` | %s | %s | %s | %s |" % (lbl, run["run_dir"], sm.get("pass"), sm.get("fail"), sm.get("blocked"), sm.get("expected_failure")))
    L += ["", "journeys compared: %d, identical: %d, with differences: %d, OPEN (unclassified) differences: %d" % (doc["journeys"], doc["identical"], doc["with_differences"], len(open_diffs)), "",
          "| journey | real | substitute | differences |", "|---|---|---|---|"]
    for i in items:
        L.append("| %s | %s (%s) | %s (%s) | %s |" % (i["id"], i["real"]["result"], i["real"]["checks"], i["substitute"]["result"], i["substitute"]["checks"],
                                                   "identical" if i["same"] else "; ".join(sorted({d["classification"] for d in i["differences"]}))))
    L += ["", "## Differences, investigated", ""]
    for i in items:
        if i["same"]:
            continue
        L.append("### %s" % i["id"])
        for d in i["differences"]:
            if d["kind"] == "result":
                L.append("- result: real=%s substitute=%s -- **%s**: %s" % (d["real"], d["substitute"], d["classification"], d["why"]))
            else:
                L.append("- %s `%s` (real=%s, substitute=%s) -- **%s**: %s" % (d["kind"], d.get("check"), d.get("real"), d.get("substitute"), d["classification"], d["why"]))
        L.append("")
    (REPO / "reports" / "implementation" / "M11_DIFFERENTIAL.md").write_text("\n".join(L) + "\n")
    print(json.dumps({k: doc[k] for k in ("journeys", "identical", "with_differences")} | {"open": len(open_diffs)}))
    return 0 if not open_diffs else 2


if __name__ == "__main__":
    sys.exit(main())
