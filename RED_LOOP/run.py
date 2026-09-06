#!/usr/bin/env python3
"""RED_LOOP orchestrator (Milestone 2).

Usage (source the LiteLLM env first; see RED_LOOP/README.md):
  python3 RED_LOOP/run.py preflight
  python3 RED_LOOP/run.py models        # write model-selection artifact
  python3 RED_LOOP/run.py campaign [--turns N] [--wall S] [--dry-run]

The campaign runs one sustained logical campaign, adjudicates candidates with
the deterministic judge, reproduces judge-positive candidates with a different
model, and (if none) runs a private calibration self-test of the replay path.
All records land under RED_LOOP/runs/<campaign_id>/ (git-ignored, hash-indexed).
"""
import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from red_loop import config, allocator, corpus  # noqa: E402
from red_loop.state import CampaignStore, new_campaign_id  # noqa: E402
from red_loop.broker import Broker  # noqa: E402
from red_loop.judge import Judge, Evidence  # noqa: E402


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def preflight():
    out = {"ts": _utc(), "checks": {}}
    # kong-lite health (attacker ingress)
    try:
        with urllib.request.urlopen(config.KONG_LITE_URL + "/_arena/health", timeout=8) as r:
            out["checks"]["kong_lite"] = json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        out["checks"]["kong_lite"] = {"error": str(e)}
    # attacker secret present
    try:
        a = allocator.allocate()
        out["checks"]["attacker"] = {"merchant_id": a["attacker"]["merchant_id"],
                                     "secret_present": bool(a["attacker"]["secret"]),
                                     "victims": [v["merchant_id"] for v in a["victims"]]}
    except Exception as e:  # noqa: BLE001
        out["checks"]["attacker"] = {"error": str(e)}
    # corpus present
    out["checks"]["corpus_roots"] = corpus.list_dir()["roots"]
    # judge evidence reachable (opening balances)
    try:
        ev = Evidence()
        out["checks"]["ledger_balance_M1"] = ev.ledger_balance("ARENAM00000001")
    except Exception as e:  # noqa: BLE001
        out["checks"]["ledger_balance_M1"] = {"error": str(e)}
    return out


def write_models_artifact():
    base = config.gateway_base()
    with urllib.request.urlopen(
            urllib.request.Request(base + "/v1/models",
                                   headers={"Authorization": "Bearer " + config.gateway_key()}),
            timeout=25) as r:
        inventory = json.loads(r.read())
    ids = [m["id"] for m in inventory.get("data", [])]
    art = {
        "generated_at": _utc(),
        "gateway_base_url": base,
        "inventory_model_ids": sorted(ids),
        "inventory_detail": inventory.get("data", []),
        "selected": {
            "primary": {"alias": config.PRIMARY_MODEL,
                        "family": "anthropic-claude", "role": "primary red agent",
                        "why": "strongest available Claude via the gateway; 1M input / 128k output; "
                               "strong large-codebase reasoning + long tool use"},
            "reproducer": {"alias": config.REPRODUCER_MODEL,
                           "family": "openai-gpt", "role": "independent reproducer",
                           "why": "strongest available GPT; different provider/family from the "
                                  "primary for decorrelated replay; 1.05M input / 128k output"},
            "utility": {"alias": config.UTILITY_MODEL, "role": "non-authoritative bookkeeping only"},
        },
        "provider_diversity": "YES -- primary is Anthropic Claude, reproducer is OpenAI GPT",
        "tool_calling_verified": True,
        "limitations": "Gateway reports all models owned_by 'openai' (OpenAI-compatible shim); "
                       "underlying provider inferred from alias family. No price table exposed, so "
                       "cost is recorded as UNKNOWN. Model ranking used gateway metadata only; no "
                       "public-internet research was used.",
    }
    p = config.REGISTRY_DIR / "model-selection.json"
    p.write_text(json.dumps(art, indent=2))
    return p, art


def evidence_index(store):
    idx = []
    for f in sorted(store.root.rglob("*")):
        if f.is_file():
            b = f.read_bytes()
            idx.append({"path": str(f.relative_to(store.root)),
                        "sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)})
    (store.root / "evidence-index.json").write_text(json.dumps(idx, indent=2))
    return idx


def run_campaign_cmd(args):
    from red_loop.campaign import run_campaign
    from red_loop import reproducer, provisioner
    from red_loop.state import CampaignStore

    cid = new_campaign_id()
    store = CampaignStore(cid)
    since_ts = int(time.time())

    # Per-campaign provisioning: fresh victim canaries + fresh idempotency
    # namespace (Workstream B). fund_fresh_attacker=False -> fixture attacker with
    # a fresh idempotency namespace (the fresh-funded-merchant path is available
    # via provisioner.provision_funded_merchant but needs full seed parity).
    want_fresh = bool(getattr(args, "fresh_attacker", False))
    prov = provisioner.provision_campaign(cid, fund_fresh_attacker=want_fresh)
    attacker = prov["attacker"]
    # Fail-closed (Section 8): a requested fresh attacker that did not self-verify
    # through a real payout must ABORT startup, never silently fall back to the M1
    # fixture. A campaign on a static funded merchant is not a fresh-merchant campaign.
    if want_fresh and not prov["manifest"].get("attacker_is_fresh_funded"):
        fm = prov["manifest"].get("fresh_merchant_detail") or {}
        raise SystemExit(
            "ABORT: fresh-attacker provisioning did not self-verify "
            "(merchant_id=%s verify_status=%s). Refusing to fall back to the M1 fixture."
            % (fm.get("merchant_id"), fm.get("verify_status")))
    alloc = {"attacker": attacker, "victims": prov["victims"],
             "actor_merchants": prov["actor_merchants"]}

    ev = Evidence()
    opening = {m: ev.ledger_balance(m) for m in alloc["actor_merchants"]}

    manifest = {
        "campaign_id": cid, "twin_release": "twin-v1.0",
        "runtime_freeze_commit": "219ca48bbb5e51db9347b335ac0a50241656045b",
        "started_at": _utc(), "since_ts": since_ts,
        "primary_model": config.PRIMARY_MODEL, "reproducer_model": config.REPRODUCER_MODEL,
        "attacker_identity_class": "one ordinary synthetic merchant (no elevated finance roles)",
        "attacker_merchant_id": attacker["merchant_id"],
        "victim_control_merchants": [v["merchant_id"] for v in alloc["victims"]],
        "opening_ledger_balances": opening,
        "gateway_route_policy": "KONG_ENFORCE_ROUTE_POLICY (source-derived public/internal split)",
        "network_policy": "attacker traffic only via kong-lite 127.0.0.1:18080; /_arena/* and "
                          "service planes denied; internal networks are docker-internal",
        "safety": {"kill_switch": str(store.root / "STOP"),
                   "pause_switch": str(store.root / "PAUSE"),
                   "request_budget": 4000},
    }
    manifest.update(prov["manifest"])                    # victim_canaries (judge-only), provenance
    store.write_manifest(manifest)

    # mandate with attacker identity (public view only)
    mandate = (config.PROMPTS_DIR / "primary_mandate.txt").read_text()
    mandate = mandate.replace("{attacker_merchant_id}", attacker["merchant_id"]) \
                     .replace("{attacker_key_id}", attacker["key_id"])

    broker = Broker(attacker, store.add_action, request_budget=manifest["safety"]["request_budget"])
    judge = Judge()

    canaries = manifest.get("victim_canaries", [])

    def judge_hook(candidate):
        ctx = {"attacker_id": attacker["merchant_id"], "actor_merchants": alloc["actor_merchants"],
               "since_ts": since_ts, "profile": "monolith",
               "capabilities": candidate.get("capabilities", []),
               "canaries": canaries}
        try:
            # judge independently scans the attacker's captured responses for canaries
            verdict = judge.adjudicate(candidate, ctx, response_texts=store.response_texts())
        except Exception as e:  # noqa: BLE001
            verdict = {"verdict": "INSUFFICIENT_EVIDENCE", "error": str(e)[:300],
                       "deterministic_impact_confirmed": False}
        ev_ref = store.put_evidence(verdict, label="verdict-" + candidate.get("candidate_id", "C"))
        store.add_candidate({"candidate_id": candidate.get("candidate_id"),
                             "status": "adjudicated", "verdict": verdict.get("verdict"),
                             "known_gap": verdict.get("known_gap"),
                             "deterministic_impact_confirmed": verdict.get("deterministic_impact_confirmed"),
                             "verdict_ref": ev_ref["ref"]})
        # COARSE feedback only
        return {"recorded": True,
                "deterministic_impact_confirmed": bool(verdict.get("deterministic_impact_confirmed"))}

    if args.dry_run:
        store.event("dry_run", note="preflight only, no model calls")
        print(json.dumps({"campaign_id": cid, "dry_run": True,
                          "opening_balances": opening,
                          "attacker": attacker["merchant_id"]}, indent=2))
        return

    summary = run_campaign(store, broker, config.PRIMARY_MODEL, mandate, judge_hook,
                           emergency_max_turns=args.emergency_turns,
                           max_wall_seconds=args.wall)

    # post-campaign: fold candidates, reproduce positives, else calibrate
    candidates = _fold_candidates(store)
    positives = [c for c in candidates.values() if c.get("verdict") == "ACCEPTED_NEW_FINDING"]
    repro_results = []
    for c in positives:
        bundle = reproducer.make_bundle(c)
        fresh_broker = Broker(attacker, store.add_action, request_budget=500)
        rr = reproducer.run_reproduction(bundle, fresh_broker, store)
        repro_results.append({"candidate_id": c.get("candidate_id"), "result": rr})

    calibration = None
    if not positives:
        calibration = _run_calibration(store, attacker)

    final = {"campaign_summary": summary, "candidates_folded": len(candidates),
             "verdict_breakdown": _verdict_breakdown(candidates),
             "positives": len(positives), "reproductions": repro_results,
             "calibration": calibration}
    store.update_manifest(final_ledger=final)
    (store.root / "final-ledger.json").write_text(json.dumps(final, indent=2, default=str))
    evidence_index(store)
    print(json.dumps({"campaign_id": cid, "run_dir": str(store.root),
                      "summary": summary, "verdicts": _verdict_breakdown(candidates),
                      "positives": len(positives)}, indent=2, default=str))


def resume_campaign_cmd(args):
    """Resume a killed/paused campaign purely from its durable records (Workstream F).
    Reconstructs the store, attacker boundary and judge from the manifest; the
    compiled context re-derives active hypotheses/verified facts from the store,
    so the discarded model conversation is not needed."""
    from red_loop.campaign import run_campaign
    from red_loop.state import CampaignStore

    store = CampaignStore(args.campaign_id)
    manifest = store.read_manifest()
    if not manifest:
        print(json.dumps({"error": "no manifest for campaign", "campaign_id": args.campaign_id}))
        return
    before = store.resume_snapshot()
    # M4: recover any task leases stranded by the kill so expired ones are
    # reassignable on resume (additive; no-op for legacy runs without leases).
    try:
        from red_loop.leases import LeaseManager
        recovered = LeaseManager(store).recover_on_resume()
        if recovered:
            store.event("resume_lease_recovery", count=len(recovered))
    except Exception as e:  # noqa: BLE001
        store.event("resume_lease_recovery_error", detail=str(e)[:200])
    alloc = allocator.allocate()
    attacker = alloc["attacker"]
    if manifest.get("attacker_merchant_id") and manifest["attacker_merchant_id"] != attacker["merchant_id"]:
        # keep provenance honest if a non-default attacker was used
        store.event("resume_attacker_mismatch", manifest_attacker=manifest.get("attacker_merchant_id"),
                    reallocated=attacker["merchant_id"])
    since_ts = manifest.get("since_ts", 0)
    canaries = manifest.get("victim_canaries", [])
    broker = Broker(attacker, store.add_action,
                    request_budget=manifest.get("safety", {}).get("request_budget", 4000))
    # already-spent requests should count against the budget on resume
    broker.request_count = int(manifest.get("broker_requests", 0) or 0)
    judge = Judge()

    def judge_hook(candidate):
        ctx = {"attacker_id": attacker["merchant_id"], "actor_merchants": alloc["actor_merchants"],
               "since_ts": since_ts, "profile": "monolith",
               "capabilities": candidate.get("capabilities", []), "canaries": canaries}
        try:
            verdict = judge.adjudicate(candidate, ctx, response_texts=store.response_texts())
        except Exception as e:  # noqa: BLE001
            verdict = {"verdict": "INSUFFICIENT_EVIDENCE", "error": str(e)[:300],
                       "deterministic_impact_confirmed": False}
        ev_ref = store.put_evidence(verdict, label="verdict-" + candidate.get("candidate_id", "C"))
        store.add_candidate({"candidate_id": candidate.get("candidate_id"), "status": "adjudicated",
                             "verdict": verdict.get("verdict"),
                             "deterministic_impact_confirmed": verdict.get("deterministic_impact_confirmed"),
                             "verdict_ref": ev_ref["ref"]})
        return {"recorded": True,
                "deterministic_impact_confirmed": bool(verdict.get("deterministic_impact_confirmed"))}

    mandate = (config.PROMPTS_DIR / "primary_mandate.txt").read_text()
    mandate = mandate.replace("{attacker_merchant_id}", attacker["merchant_id"]) \
                     .replace("{attacker_key_id}", attacker["key_id"])
    summary = run_campaign(store, broker, config.PRIMARY_MODEL, mandate, judge_hook,
                           emergency_max_turns=args.emergency_turns, max_wall_seconds=args.wall,
                           resume=True)
    after = store.resume_snapshot()
    print(json.dumps({"resumed": args.campaign_id, "before": before, "after": after,
                      "summary": summary}, indent=2, default=str))


def _fold_candidates(store):
    merged = {}
    for rec in store._read_all("candidates"):
        cid = rec.get("candidate_id")
        merged.setdefault(cid, {}).update(rec)
    return merged


def _verdict_breakdown(candidates):
    out = {}
    for c in candidates.values():
        v = c.get("verdict", "claimed_unadjudicated")
        out[v] = out.get(v, 0) + 1
    return out


def _run_calibration(store, attacker):
    """Private machinery self-test: reproducer creates one legitimate fresh payout;
    the judge confirms the expected deterministic state (own tenant + persisted).
    NEVER shown to the primary red agent; separate from the finding count."""
    from red_loop import reproducer
    bundle = {"starting_actor": "one ordinary synthetic merchant",
              "minimal_steps": [
                  "POST /v1/payouts creating a small payout to your own fund account "
                  "fa_ARENAFAX000001 with account_number 2323230099999999, amount 1000, "
                  "mode IMPS, purpose refund, currency INR, merchant_id " + attacker["merchant_id"] +
                  ", using a FRESH X-Payout-Idempotency header",
                  "GET /v1/payouts/<id> to confirm it persisted"],
              "required_inputs": {"fund_account_id": "fa_ARENAFAX000001",
                                  "account_number": "2323230099999999"},
              "claimed_observable_result": "a payout is created under your own merchant and is retrievable",
              "allowed_code_slices": []}
    fresh_broker = Broker(attacker, store.add_action, request_budget=200)
    rr = reproducer.run_reproduction(bundle, fresh_broker, store, label="calibration")
    # deterministic confirmation
    ev = Evidence()
    import re
    ids = re.findall(r"pout_[A-Za-z0-9]+", json.dumps(rr))
    confirmed = False
    detail = {}
    for pid in ids:
        row = ev.payout(pid)
        if row and row["merchant_id"] == attacker["merchant_id"]:
            confirmed = True
            detail = {"payout_id": pid, "merchant_id": row["merchant_id"], "status": row["status"]}
            break
    result = {"machinery_ok": confirmed, "reproducer_report": rr.get("report"),
              "deterministic_detail": detail,
              "note": "Machinery self-test only (legitimate own-tenant effect). NOT a finding."}
    store.event("calibration_result", **result)
    return result


def lifecycle_gate_cmd(args):
    """M4 closing gate: fails closed if a high-priority hypothesis is unresolved.
    Gateway-free; reads only durable records. Exit 1 when the gate fails."""
    from red_loop import lifecycle_gate as lg
    result = lg.lifecycle_gate(args.campaign_id, claims_success=not args.no_success_claim)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["passed"] else 1


def lifecycle_export_cmd(args):
    """M4 exporter: writes m4-hypothesis-lifecycle.json for a campaign."""
    from red_loop import lifecycle_gate as lg
    doc = lg.export_lifecycle(args.campaign_id, out_path=args.out)
    from red_loop.state import CampaignStore
    out = args.out or str(CampaignStore(args.campaign_id).root / "m4-hypothesis-lifecycle.json")
    print("wrote", out)
    print(json.dumps({"rollup": doc["rollup"], "gate": doc["gate"]["passed"],
                      "hypotheses": len(doc["hypotheses"])}, indent=2, default=str))


def recovery_matrix_cmd(args):
    """M4 failure-recovery matrix. Docker scenarios are environment-gated."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "m4_recovery_matrix", str(Path(__file__).resolve().parent / "surface" / "m4_recovery_matrix.py"))
    mrm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mrm)
    return mrm.main(["--out", args.out] if args.out else [])


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("preflight")
    sub.add_parser("models")
    lg = sub.add_parser("lifecycle-gate", help="M4 closing gate over a campaign's hypotheses")
    lg.add_argument("campaign_id")
    lg.add_argument("--no-success-claim", action="store_true",
                    help="evaluate without asserting a successful close (never blocks)")
    le = sub.add_parser("lifecycle-export", help="write m4-hypothesis-lifecycle.json")
    le.add_argument("campaign_id")
    le.add_argument("--out", default=None)
    rm = sub.add_parser("recovery-matrix", help="run the M4 failure-recovery matrix")
    rm.add_argument("--out", default=None)
    cp = sub.add_parser("campaign")
    cp.add_argument("--emergency-turns", dest="emergency_turns", type=int, default=2000,
                    help="EMERGENCY safety backstop only; NOT a normal completion condition")
    cp.add_argument("--wall", type=int, default=None, help="optional wall-clock backstop seconds")
    cp.add_argument("--fresh-attacker", dest="fresh_attacker", action="store_true",
                    help="provision a brand-new funded attacker merchant (needs full seed parity)")
    cp.add_argument("--dry-run", action="store_true")
    rp = sub.add_parser("resume")
    rp.add_argument("campaign_id")
    rp.add_argument("--emergency-turns", dest="emergency_turns", type=int, default=2000)
    rp.add_argument("--wall", type=int, default=None)
    args = ap.parse_args()

    if args.cmd == "preflight":
        print(json.dumps(preflight(), indent=2))
    elif args.cmd == "models":
        p, art = write_models_artifact()
        print("wrote", p)
        print(json.dumps(art["selected"], indent=2))
    elif args.cmd == "campaign":
        run_campaign_cmd(args)
    elif args.cmd == "resume":
        resume_campaign_cmd(args)
    elif args.cmd == "lifecycle-gate":
        raise SystemExit(lifecycle_gate_cmd(args))
    elif args.cmd == "lifecycle-export":
        lifecycle_export_cmd(args)
    elif args.cmd == "recovery-matrix":
        raise SystemExit(recovery_matrix_cmd(args))


if __name__ == "__main__":
    main()
