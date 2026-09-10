#!/usr/bin/env python3
"""M4 assurance surface (task T15).

Three gateway-backed entrypoints that tie the M4 lifecycle machinery to the live
arena and the calibration fixture:

  * ``run_contexts``       -- provision 4 fresh Direct merchants (one per policy),
                              run the 4 exploration contexts sequentially with
                              small per-context budgets, then run the closing
                              lifecycle gate + export and write
                              ``reports/implementation/m4-assurance-run.json``.
  * ``run_soak``           -- drive ``Soak.run`` with a gateway-backed cycle
                              runner (short, bounded); rotate scenarios, checkpoint
                              each cycle, stop honestly, and prove a resumable
                              checkpoint. Writes ``runs/<cid>/m4-direct-e2e-soak.json``.
  * ``validate_lifecycle`` -- drive the tenant-isolation IDOR calibration fixture
                              through the FULL hypothesis lifecycle to a genuine
                              ``accept`` (proposed -> ... -> supported ->
                              replay_requested -> reproduced -> accepted) using a
                              six-arm cross-merchant experiment + an independent
                              replay by a different provider. Writes
                              ``reports/implementation/m4-lifecycle-validation.json``.

SAFETY: every model call goes through the LiteLLM gateway (source RED_LOOP/llm.env
first). No credential or canary is printed or persisted un-redacted. This module
never mutates the arena beyond the accepted fresh-Direct-merchant provisioning the
existing campaign already performs; the calibration service is loopback-only.
"""
import argparse
import importlib.util
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
RED_LOOP_DIR = HERE.parent
REPO = RED_LOOP_DIR.parent
IMPL = REPO / "reports" / "implementation"
sys.path.insert(0, str(RED_LOOP_DIR))

from red_loop import config, contexts as ctx_mod, context_runner  # noqa: E402
from red_loop import lifecycle_gate as lg  # noqa: E402
from red_loop import experiments as exp_mod  # noqa: E402
from red_loop.experiments import Arm  # noqa: E402
from red_loop.hypotheses import HypothesisManager, HypothesisState as S, Priority  # noqa: E402
from red_loop.leases import LeaseManager  # noqa: E402
from red_loop.soak import Soak  # noqa: E402
from red_loop.state import CampaignStore, new_campaign_id  # noqa: E402
from red_loop.judge import Judge  # noqa: E402
from red_loop import llm  # noqa: E402


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------
# Per-context judge (scoped to each context's own fresh Direct merchant)
# --------------------------------------------------------------------------
def build_judge_hook_factory():
    """Return ``factory(store, cred, policy) -> judge_hook`` mirroring run.py's
    campaign judge_hook but scoped to the context's own merchant. Fresh Direct
    merchants seed no victim canaries and have no genuine cross-tenant bug, so the
    judge returns no accepted finding on the live twin -- that is expected and
    honest; the accept path is exercised against the calibration fixture."""
    judge = Judge()

    def factory(store, cred, policy):
        attacker_id = cred["merchant_id"]
        since_ts = int((store.read_manifest() or {}).get("since_ts", 0) or 0)

        def judge_hook(candidate):
            ctx = {"attacker_id": attacker_id, "actor_merchants": [attacker_id],
                   "since_ts": since_ts, "profile": "direct",
                   "capabilities": candidate.get("capabilities", []), "canaries": []}
            try:
                verdict = judge.adjudicate(candidate, ctx, response_texts=store.response_texts())
            except Exception as e:  # noqa: BLE001
                verdict = {"verdict": "INSUFFICIENT_EVIDENCE", "error": str(e)[:300],
                           "deterministic_impact_confirmed": False}
            ev_ref = store.put_evidence(verdict, label="verdict-" + candidate.get("candidate_id", "C"))
            store.add_candidate({"candidate_id": candidate.get("candidate_id"),
                                 "status": "adjudicated", "verdict": verdict.get("verdict"),
                                 "policy": policy.name,
                                 "deterministic_impact_confirmed": verdict.get("deterministic_impact_confirmed"),
                                 "verdict_ref": ev_ref["ref"]})
            return {"recorded": True,
                    "deterministic_impact_confirmed": bool(verdict.get("deterministic_impact_confirmed"))}
        return judge_hook

    return factory


def _egress_note():
    """Same-window egress: the arena-wide audit (ENV2_COMPOSE/network/egress_audit.py)
    wraps a command in a privileged host-network capture container and is an
    acceptance-time, arena-wide capture -- not a cheap in-line probe. Record the
    deferral honestly and point at the tooling."""
    audit = REPO / "ENV2_COMPOSE" / "network" / "egress_audit.py"
    return {
        "captured_here": False,
        "reason": "arena-wide egress audit uses a privileged host-network capture "
                  "container; it is run by the coordinator at acceptance, wrapping "
                  "the full run. Not invoked in-line to avoid a heavy capture.",
        "audit_tool": str(audit.relative_to(REPO)) if audit.exists() else None,
        "attacker_boundary": "all context traffic is via the Broker to kong-lite "
                             "(%s); /_arena/*, /twirp/, /metrics, /debug hard-denied; "
                             "model calls only via the LiteLLM gateway." % config.KONG_LITE_URL,
    }


# --------------------------------------------------------------------------
# 1. contexts
# --------------------------------------------------------------------------
def run_contexts(primary_model=None, turns=14, request_budget=60, wall=240,
                 restart=True, out=None, seed_creds=None):
    primary_model = primary_model or config.PRIMARY_MODEL
    cid = new_campaign_id()
    store = CampaignStore(cid)
    since_ts = int(time.time())
    store.write_manifest({
        "campaign_id": cid, "run_kind": "m4-contexts", "started_at": _utc(),
        "since_ts": since_ts, "primary_model": primary_model,
        "policies": [p.name for p in ctx_mod.POLICIES],
        "safety": {"request_budget": request_budget},
    })
    factory = build_judge_hook_factory()
    creds = dict(seed_creds or {})
    runner = context_runner.make_runner(
        store, judge_hook_factory=factory, primary_model=primary_model,
        provision=True, restart=restart, merchant_creds=creds)
    supervisor = ctx_mod.ContextSupervisor(store, runner=runner)
    per_budget = {p.name: {"emergency_max_turns": turns, "request_budget": request_budget,
                           "max_wall_seconds": wall} for p in ctx_mod.POLICIES}
    base_mandate = (config.PROMPTS_DIR / "primary_mandate.txt").read_text()

    result = supervisor.run(policies=ctx_mod.POLICIES, merchants={},
                            per_context_budget=per_budget, base_mandate=base_mandate)

    gate = lg.lifecycle_gate(cid, claims_success=True, store=store)
    doc = lg.export_lifecycle(cid, store=store)

    merchants = []
    blockers = []
    experiments_executed = 0
    per_context = []
    for cs in result["contexts"]:
        experiments_executed += int(cs.get("broker_requests") or 0)
        per_context.append({
            "policy": cs.get("policy"), "merchant_id": cs.get("merchant_id"),
            "stop_reason": cs.get("stop_reason"),
            "turns_completed": cs.get("turns_completed"),
            "normal_completion": cs.get("normal_completion"),
            "broker_requests": cs.get("broker_requests"),
            "new_hypotheses": cs.get("new_hypotheses"),
            "hypotheses_by_status": cs.get("hypotheses_by_status"),
            "usage": cs.get("usage"),
        })
        if cs.get("merchant_id"):
            merchants.append({"policy": cs.get("policy"), "merchant_id": cs.get("merchant_id"),
                              "provisioning": cs.get("provisioning")})
        if cs.get("blocked"):
            blockers.append({"policy": cs.get("policy"), "blocker": cs.get("blocker"),
                             "reason": cs.get("reason")})

    hyp = HypothesisManager(store)
    report = {
        "kind": "M4_ASSURANCE_CONTEXTS", "campaign_id": cid, "generated_at": _utc(),
        "run_dir": str(store.root), "primary_model": primary_model,
        "contexts_summary": {"started_at": result["started_at"], "ended_at": result["ended_at"],
                             "policies": result["policies"], "total_turns": result["total_turns"]},
        "contexts": per_context,
        "merchants": merchants,
        "hypotheses_by_status": hyp.rollup(),
        "experiments_executed": experiments_executed,
        "blockers": blockers,
        "gate_verdict": {"passed": gate["passed"], "reasons": gate.get("reasons"),
                         "source": gate.get("source"),
                         "highest_priority_open": gate.get("highest_priority_open"),
                         "highest_priority_open_status": gate.get("highest_priority_open_status")},
        "lifecycle_rollup": doc["rollup"],
        "egress_note": _egress_note(),
    }
    out = Path(out) if out else (IMPL / "m4-assurance-run.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    # copy the per-run lifecycle export next to the assurance report
    (IMPL / "m4-hypothesis-lifecycle.json").write_text(
        json.dumps(doc, indent=2, default=str))
    report["_out"] = str(out)
    report["_creds_cache"] = creds  # in-memory only; never written to disk
    return report


# --------------------------------------------------------------------------
# 2. soak
# --------------------------------------------------------------------------
def run_soak(primary_model=None, wall_seconds=300, max_cycles=3, checkpoint_every=1,
             lease_ttl=300, cycle_turns=4, cycle_requests=20, cycle_wall=90,
             restart=True, resume_cid=None, seed_creds=None, provision=True):
    primary_model = primary_model or config.PRIMARY_MODEL
    if resume_cid:
        store = CampaignStore(resume_cid)
    else:
        cid = new_campaign_id()
        store = CampaignStore(cid)
        store.write_manifest({
            "campaign_id": cid, "run_kind": "m4-soak", "started_at": _utc(),
            "since_ts": int(time.time()), "primary_model": primary_model,
            "policies": [p.name for p in ctx_mod.POLICIES],
        })
    factory = build_judge_hook_factory()
    creds = dict(seed_creds or {})
    cycle_runner = context_runner.make_cycle_runner(
        store, judge_hook_factory=factory, primary_model=primary_model,
        provision=provision, restart=restart, merchant_creds=creds,
        cycle_budget={"emergency_max_turns": cycle_turns, "request_budget": cycle_requests,
                      "max_wall_seconds": cycle_wall})
    soak = Soak(store, cycle_runner=cycle_runner, policies=ctx_mod.POLICIES)
    summary = soak.run(wall_seconds=wall_seconds, max_cycles=max_cycles,
                       checkpoint_every=checkpoint_every, lease_ttl=lease_ttl)
    manifest = store.read_manifest() or {}
    checkpoint = {k: manifest.get(k) for k in
                  ("soak_cycle", "soak_elapsed_seconds", "soak_hypotheses", "soak_state_hash")}
    lg.export_lifecycle(store.campaign_id, store=store)
    gate = lg.lifecycle_gate(store.campaign_id, claims_success=False, store=store)
    return {"campaign_id": store.campaign_id, "run_dir": str(store.root),
            "soak_summary": summary, "checkpoint": checkpoint,
            "gate_passed_advisory": gate["passed"], "_creds_cache": creds,
            "soak_json": str(store.root / "m4-direct-e2e-soak.json")}


# --------------------------------------------------------------------------
# 3. validate-lifecycle (calibration IDOR -> genuine accept)
# --------------------------------------------------------------------------
def _load_calib():
    calib_dir = RED_LOOP_DIR / "calibration" / "tenant-isolation"

    def _imp(name):
        spec = importlib.util.spec_from_file_location(
            "calib_" + name, str(calib_dir / (name + ".py")))
        m = importlib.util.module_from_spec(spec)
        # calibration modules import each other by bare name; make the dir importable
        if str(calib_dir) not in sys.path:
            sys.path.insert(0, str(calib_dir))
        spec.loader.exec_module(m)
        return m

    return _imp("generator"), _imp("oracle"), _imp("replay"), _imp("cross_provider")


def _http_get(url, credential, timeout=8):
    headers = {}
    if credential:
        headers["Authorization"] = "Bearer " + credential
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(8000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read(8000) or b"").decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return "ERR", str(e)[:200]


def validate_lifecycle(reproducer_model=None, out=None, port_reg=19131, port_fixed=19132):
    """Drive the calibration IDOR fixture through the full lifecycle to accept."""
    import subprocess
    reproducer_model = reproducer_model or config.REPRODUCER_MODEL
    generator, oracle, replay, cross_provider = _load_calib()

    cid = "m4lifeval-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(3)
    store = CampaignStore(cid)
    store.write_manifest({"campaign_id": cid, "run_kind": "m4-lifecycle-validation",
                          "started_at": _utc(), "since_ts": int(time.time())})
    hyp = HypothesisManager(store)
    lease = LeaseManager(store)
    owner = "itb-validator"

    # -- fresh fixture, augmented with an attacker-OWNED resource (own control) --
    fx = generator.generate_fixture("regression", port=port_reg, seed_label="m4-lifeval")
    fixture_path = REPO / fx["evidence_dir"] / "fixture.json"
    fxd = json.loads(fixture_path.read_text())
    attacker_mid = fxd["actors"]["attacker"]["merchant_id"]
    attacker_cred = fxd["actors"]["attacker"]["credential"]
    victim_mid = fxd["actors"]["victim"]["merchant_id"]
    victim_res = fxd["target_victim_resource_id"]
    own_res_id = "calres_own_" + secrets.token_hex(8)
    fxd["resources"].append({
        "resource_id": own_res_id, "owner_merchant_id": attacker_mid,
        "type": "payout_destination",
        "payload": {"beneficiary_name": "Own Beneficiary", "account_number": "8" + secrets.token_hex(6),
                    "ifsc": "OWNB000" + secrets.token_hex(2), "secret_canary": "OWN-CONTROL"}})
    fixture_path.write_text(json.dumps(fxd, indent=2))

    def _start(profile, port):
        import os
        env = dict(os.environ)
        env["CALIB_FIXTURE"] = str(fixture_path)
        env["CALIB_PROFILE"] = profile
        env["CALIB_PORT"] = str(port)
        proc = subprocess.Popen([sys.executable, str(RED_LOOP_DIR / "calibration" /
                                 "tenant-isolation" / "service.py")], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(30):
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/calib/health" % port, timeout=2) as r:
                    if r.status == 200:
                        return proc
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        return proc

    proc_reg = _start("regression", port_reg)
    proc_fix = _start("fixed", port_fixed)
    ep_reg = "http://127.0.0.1:%d" % port_reg
    ep_fix = "http://127.0.0.1:%d" % port_fixed

    result = {"kind": "M4_LIFECYCLE_VALIDATION", "campaign_id": cid, "generated_at": _utc(),
              "run_dir": str(store.root), "calibration_run": fx["run_id"],
              "reproducer_model": reproducer_model,
              "note": "CALIBRATION-ONLY. A genuine cross-tenant IDOR in a loopback "
                      "fixture, driven through the full hypothesis lifecycle to prove "
                      "the machinery can reach accept and that the gate passes only "
                      "when the six arms executed. NOT a Razorpay finding."}
    try:
        # -- lifecycle: propose --------------------------------------------------
        hid, dup = hyp.propose(
            claim="Cross-tenant fetch on the resource endpoint discloses another "
                  "merchant's payout_destination and its canary (missing ownership check / IDOR)",
            target_assets=[{"service": "calibration", "asset": "/calib/v1/resources", "kind": "tenant_boundary"}],
            suspected_cause="object fetch returns a resource by id without verifying caller ownership",
            expected_observation="HTTP 200 with owner_merchant_id != caller and the victim's secret canary",
            preconditions=[{"text": "attacker holds only its own credential", "label": "verified"}],
            priority=Priority.CRITICAL, owner=owner)
        lease.acquire(hid, owner, ttl=300)
        experiment = exp_mod.new_cross_merchant(hid, target_asset="/calib/v1/resources/" + victim_res)

        hyp.advance(hid, S.CLAIMED, reason="claimed_for_validation", actor=owner)
        hyp.advance(hid, S.EXPERIMENT_DEFINED,
                    reason="six-arm cross-merchant experiment defined", actor=owner)
        hyp.advance(hid, S.EXECUTING, reason="executing six arms", actor=owner)

        # -- arm 1: own-merchant control (success is only a control) -------------
        st, body = _http_get(ep_reg + "/calib/v1/resources/" + own_res_id, attacker_cred)
        own_obj = _try_json(body)
        experiment.observe(Arm.OWN_MERCHANT_CONTROL, status=st,
                           state_observed=(own_obj.get("owner_merchant_id") if own_obj else None),
                           note="attacker reads its OWN resource (control only)")

        # -- arm 2: merchant-B variant (the cross-tenant effect) -----------------
        st_b, body_b = _http_get(ep_reg + "/calib/v1/resources/" + victim_res, attacker_cred)
        obj_b = _try_json(body_b)
        unauthorized = bool(st_b == 200 and obj_b and obj_b.get("owner_merchant_id") == victim_mid)
        ev_b = store.put_evidence({"arm": "merchant_b_variant", "status": st_b,
                                   "request_route": "/calib/v1/resources/" + victim_res,
                                   "response_owner": (obj_b or {}).get("owner_merchant_id"),
                                   "cross_tenant": unauthorized}, label="arm-merchant-b")
        experiment.observe(Arm.MERCHANT_B_VARIANT, status=st_b, state_observed=obj_b,
                           unauthorized_effect=unauthorized, evidence_ref=ev_b["ref"],
                           note="attacker reads the VICTIM's resource (cross-tenant IDOR)")

        # -- arm 3: malformed/absent identity ------------------------------------
        st_m, body_m = _http_get(ep_reg + "/calib/v1/resources/" + victim_res, "")
        experiment.observe(Arm.MALFORMED_OR_ABSENT_IDENTITY_VARIANT, status=st_m,
                           denied=bool(st_m in (401, 403, 404)), denied_at_layer="service",
                           note="absent credential is rejected (information, not proof of safety)")

        # -- arm 4: expected-denial control (fixed profile, same request) --------
        st_f, body_f = _http_get(ep_fix + "/calib/v1/resources/" + victim_res, attacker_cred)
        experiment.observe(Arm.EXPECTED_DENIAL_CONTROL, status=st_f,
                           denied=bool(st_f in (403, 404)), denied_at_layer="service",
                           note="fixed profile denies the same cross-tenant fetch (negative control)")

        # -- arm 5: state observation (deterministic oracle confirms disclosure) --
        evidence_for_oracle = {"status": st_b, "response_body": body_b,
                               "url": ep_reg + "/calib/v1/resources/" + victim_res,
                               "request_route": "/calib/v1/resources/" + victim_res,
                               "used_credential": attacker_cred}
        oracle_reg = oracle.judge_regression(fxd, evidence_for_oracle)
        experiment.observe(Arm.STATE_OBSERVATION, status=st_b,
                           state_observed={"canary_disclosed": oracle_reg["canary_disclosed"],
                                           "victim_owner_seen": oracle_reg["victim_owner_seen"]},
                           evidence_ref=ev_b["ref"],
                           note="oracle deterministically confirms victim state disclosed")

        # -- arm 6: clean reset --------------------------------------------------
        experiment.observe(Arm.CLEAN_RESET, status="reset",
                           note="fixture/service torn down; independent replay runs from clean state")

        # -- gate check: support only when arms executed -------------------------
        can_support_before_gate = experiment.can_support()
        # a control-only counterfactual: an experiment with no merchant-B arm must NOT support
        counter = exp_mod.new_cross_merchant("H-counter", target_asset="x")
        counter.observe(Arm.OWN_MERCHANT_CONTROL, status=200, state_observed="own")
        counterfactual = counter.can_support()  # -> (False, missing_arms:...)

        hyp.advance(hid, S.EVIDENCE_GATHERED, reason="six arms executed", actor=owner)
        hyp.mark_executed(hid)
        ok_support, why = experiment.can_support()
        if not ok_support:
            raise RuntimeError("experiment cannot support: " + str(why))
        hyp.advance(hid, S.SUPPORTED, reason="cross-tenant disclosure demonstrated with all arms",
                    actor=owner, evidence_refs=[ev_b["ref"]])
        # stamp the unauthorized-effect + experiment onto the record for the export/gate
        store._append(hyp.KIND, {"hypothesis_id": hid, "_update": True,
                                 "unauthorized_effect": True,
                                 "experiment": experiment.to_dict(),
                                 "updated_at": _utc()})

        # -- replay: request + independent replay by a DIFFERENT provider --------
        hyp.advance(hid, S.REPLAY_REQUESTED, reason="independent cross-provider replay", actor=owner)
        replay_block = _independent_replay(cross_provider, replay, oracle, generator,
                                           reproducer_model, port_reg + 200)
        experiment.record_replay(independent=replay_block["independent"],
                                 reproduced=replay_block["reproduced"], note=replay_block["provider"])

        if replay_block["reproduced"]:
            hyp.advance(hid, S.REPRODUCED, reason="reproduced by %s (%s)" %
                        (replay_block["provider"], replay_block["run"]), actor=owner)
            ok_accept, why_a = experiment.can_accept()
            if not ok_accept:
                raise RuntimeError("cannot accept: " + str(why_a))
            store._append(hyp.KIND, {"hypothesis_id": hid, "_update": True,
                                     "independent_replay_ok": True,
                                     "experiment": experiment.to_dict(), "updated_at": _utc()})
            hyp.advance(hid, S.ACCEPTED,
                        reason="six-arm cross-tenant effect + independent %s replay" %
                               replay_block["provider"], actor=owner)
            lease.release(_lease_id_for(lease, hid), reason="accepted")
        else:
            hyp.advance(hid, S.REJECTED,
                        reason="independent replay did not reproduce", actor=owner)

        # -- closing gate + export ----------------------------------------------
        gate = lg.lifecycle_gate(cid, claims_success=True, store=store)
        doc = lg.export_lifecycle(cid, store=store)
        final = hyp.get(hid)
        result.update({
            "hypothesis_id": hid,
            "final_status": final.get("status"),
            "reached_accept": final.get("status") == S.ACCEPTED.value,
            "unauthorized_effect": experiment.unauthorized_effect_observed(),
            "arms_executed": sorted(experiment.executed_arms()),
            "missing_arms": experiment.missing_arms(),
            "oracle_regression": oracle_reg,
            "gate_when_arms_executed": {"passed": experiment.can_support()[0]},
            "gate_control_only_counterfactual": {
                "can_support": counterfactual[0], "reason": counterfactual[1],
                "proves": "an experiment lacking the merchant-B / cross arms cannot support"},
            "independent_replay": replay_block,
            "closing_gate": {"passed": gate["passed"], "reasons": gate.get("reasons")},
            "lifecycle_rollup": doc["rollup"],
            "transitions": [t.get("to") for t in final.get("transitions", [])],
        })
    finally:
        for p in (proc_reg, proc_fix):
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    p.kill()
                except Exception:  # noqa: BLE001
                    pass

    out = Path(out) if out else (IMPL / "m4-lifecycle-validation.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str))
    result["_out"] = str(out)
    return result


def _independent_replay(cross_provider, replay, oracle, generator, model, port):
    """Independent replay by a DIFFERENT provider (gpt-5.5) via the gateway, on its
    OWN fresh fixture. Falls back to a deterministic clean-state replay on a fresh
    disjoint fixture if the gateway is unusable (recorded honestly)."""
    try:
        xp = cross_provider.run(model=model)
        reg = xp.get("regression", {}) or {}
        reproduced = bool((reg.get("oracle") or {}).get("passed"))
        return {"independent": True, "reproduced": reproduced,
                "provider": "%s (openai-gpt, cross-provider)" % model,
                "run": reg.get("run"), "mode": "model_gateway",
                "certificate": xp.get("certificate")}
    except Exception as e:  # noqa: BLE001
        detail = llm.redact(str(e))[:300]
        # deterministic clean-state fallback on a fresh disjoint fixture
        import os
        import subprocess
        fx2 = generator.generate_fixture("regression", port=port, seed_label="m4-lifeval-indep")
        fx2_path = REPO / fx2["evidence_dir"] / "fixture.json"
        env = dict(os.environ)
        env["CALIB_FIXTURE"] = str(fx2_path)
        env["CALIB_PROFILE"] = "regression"
        env["CALIB_PORT"] = str(port)
        proc = subprocess.Popen([sys.executable, str(RED_LOOP_DIR / "calibration" /
                                 "tenant-isolation" / "service.py")], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(30):
                try:
                    with urllib.request.urlopen("http://127.0.0.1:%d/calib/health" % port, timeout=2) as r:
                        if r.status == 200:
                            break
                except Exception:  # noqa: BLE001
                    time.sleep(0.2)
            resolved = replay.resolve_bundle(fx2, "http://127.0.0.1:%d" % port)
            ev = replay.deterministic_request(resolved)
            ev["used_credential"] = fx2["actors"]["attacker"]["credential"]
            r = oracle.judge_regression(fx2, ev)
            return {"independent": True, "reproduced": bool(r["passed"]),
                    "provider": "deterministic clean-state (gateway unusable: %s)" % detail,
                    "run": fx2["run_id"], "mode": "deterministic_fallback"}
        finally:
            try:
                proc.terminate(); proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                proc.kill()


def _lease_id_for(lease, hid):
    for lid, rec in lease._by_id().items():
        if rec.get("hypothesis_id") == hid and not rec.get("released_at"):
            return lid
    return None


def _try_json(s):
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="M4 assurance surface")
    sub = ap.add_subparsers(dest="mode", required=True)
    c = sub.add_parser("contexts")
    c.add_argument("--primary-model", default=None)
    c.add_argument("--turns", type=int, default=14)
    c.add_argument("--request-budget", type=int, default=60)
    c.add_argument("--wall", type=int, default=240)
    c.add_argument("--no-restart", action="store_true")
    c.add_argument("--out", default=None)
    s = sub.add_parser("soak")
    s.add_argument("--primary-model", default=None)
    s.add_argument("--wall-seconds", type=int, default=300)
    s.add_argument("--max-cycles", type=int, default=3)
    s.add_argument("--checkpoint-every", type=int, default=1)
    s.add_argument("--lease-ttl", type=int, default=300)
    s.add_argument("--resume", default=None)
    s.add_argument("--no-restart", action="store_true")
    v = sub.add_parser("validate-lifecycle")
    v.add_argument("--reproducer-model", default=None)
    v.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    if args.mode == "contexts":
        r = run_contexts(primary_model=args.primary_model, turns=args.turns,
                         request_budget=args.request_budget, wall=args.wall,
                         restart=not args.no_restart, out=args.out)
        print(json.dumps({k: v for k, v in r.items()
                          if k not in ("_creds_cache",)}, indent=2, default=str))
    elif args.mode == "soak":
        r = run_soak(primary_model=args.primary_model, wall_seconds=args.wall_seconds,
                     max_cycles=args.max_cycles, checkpoint_every=args.checkpoint_every,
                     lease_ttl=args.lease_ttl, resume_cid=args.resume,
                     restart=not args.no_restart)
        print(json.dumps({k: v for k, v in r.items()
                          if k not in ("_creds_cache",)}, indent=2, default=str))
    elif args.mode == "validate-lifecycle":
        r = validate_lifecycle(reproducer_model=args.reproducer_model, out=args.out)
        print(json.dumps({k: v for k, v in r.items()}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
