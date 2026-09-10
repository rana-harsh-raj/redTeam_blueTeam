#!/usr/bin/env python3
"""family:approval-workflow -- maker/checker approval of a payout, against the REAL engine.

M6 wired `substitutes/workflow-engine` (the durable maker/checker engine M5 reconstructed) in
as the arena default: payouts-api runs with `[workflow] host = "http://workflow-engine:8093"`.
Every driver below therefore drives the live engine, not a stand-in:

  * operator/control plane -- `POST /_arena/decide {payout_id, decision}`
    (ENV2_COMPOSE/substitutes/workflow-engine/ARENA.md section 1), which moves the workflow to
    its terminal state and fires the REAL payouts callback
    `POST /v1/payouts/payouts_internal/{payout_id}/{approve|reject}`
    (payout_internal_routes.go:71/:77, Basic rzp_live + auth_workflow_payouts, x-creator-id and
    X-Razorpay-Account headers; 200/201/409 all count as delivered);
  * actor plane -- `POST /v1/workflows/{id}/{approve|reject}` with a per-actor bearer token
    minted through the admin plane (`POST /admin/actors`), used by the `n_of_m` and
    `maker_checker` variants together with `POST /admin/policies`
    (required_approvals=2, separation=1).

Making a fresh merchant workflow-applicable (ARENA.md section 2):

  2a PS side  -- the `Workflows` config. ARENA.md documents the DCS object; in THIS arena the
     DCS plane is inert against the pristine payouts binary (dcs-stub/CONTRACT.md "Known,
     load-bearing gap": payouts' goutils/dcs client resolves its login host from a hardcoded
     env->hostname map and never consults ServerURL, so `[dcs] Env` is deliberately unrecognised
     and dcs.New() fails before any network call). The name payouts actually resolves is the
     appConstants one, `payout_workflows`, off the monolith merchant-config feature list --
     exactly how the M2 surface acceptance activated this slice for the M3 fixture
     (seeds/monolith/merchants.json ARENAM00000003.merchant.feature). framework.Arena
     .make_workflow_applicable writes BOTH planes and drops payouts' MerchantConfig Redis cache
     (`{payouts_merchant_config_key}_<mid>`), which is load-bearing: that cache is the mechanism
     behind the recurring production "payout_workflows ignored" incident class
     (reports/findings/11, reports/findings/12 A.6).
  2b engine side -- `WFE_AUTOPROVISION_POLICY=1` with `WFE_TENANT_KEY=owner_id`, so the first
     Create for an unknown merchant mints the org (org == merchant id) and the default policy
     (1 approval, any actor holding `approver`, maker != checker, 24h). Actors are never
     auto-created; the n_of_m / maker_checker variants provision them explicitly.

The admin token is read host-side from ENV2_COMPOSE/secrets/wfe_admin_token.txt by
framework.Arena.wfe_admin_token and is registered as a redacted secret before its first use, so
neither it nor any actor token can reach an evidence bundle or a report.
"""
import sys
import time
import uuid

import framework as F
from framework import journey

sys.path.insert(0, str(F.REPO / "RED_LOOP" / "m5"))

WF = "workflow"          # a fresh Shared merchant, made workflow-applicable per ARENA.md 2a


# ---------------------------------------------------------------------------
def _probe(ctx):
    """Live capability probe: is the engine up and is payouts actually pointed at it?"""
    probe = {}
    import subprocess
    out = subprocess.run(
        ["docker", "exec", F.P.cname("payouts-api"), "sh", "-c",
         "grep -A6 '^\\[workflow\\]' /app/config/arena.toml | grep '^ *host'"],
        capture_output=True, text=True, timeout=30)
    probe["payouts_workflow_host"] = (out.stdout + out.stderr).strip()[:200]
    probe["workflow_host_is_dead_address"] = "127.0.0.1:1" in probe["payouts_workflow_host"]
    # M11: the REAL Workflow service (razorpay/workflows Twirp API) serves the same wfe_* surface through the adapter
    probe["workflow_impl"] = "workflows-api (razorpay/workflows)" if F.TRUST["real_workflows"] else "workflow-engine (M5 reconstruction)"
    probe["workflow_host_is_engine"] = ("workflow-engine:8093" in probe["payouts_workflow_host"]) or \
        (F.TRUST["real_workflows"] and "workflows-api:9400" in probe["payouts_workflow_host"])
    probe["engine_health"] = ctx.a.wfe_health()
    st, body = ctx.a.jhttp("GET", F.WORKFLOW_SIM + "/_arena/health",
                           note="workflow-sim (M2 stand-in, still running, no longer wired)")
    probe["workflow_sim_health"] = {"status": st, "body": body}
    ctx.ev["capability_probe"] = probe
    return probe


def _require_engine(ctx):
    probe = _probe(ctx)
    healthy = (probe["engine_health"].get("/health", {}).get("status") == 200
               and probe["engine_health"].get("/_arena/health", {}).get("status") == 200)
    if not (healthy and probe["workflow_host_is_engine"]):
        ctx.blocked(
            "payouts-api pointed at a reachable workflow engine. Observed: %s ; engine /health "
            "and /_arena/health -> %s. Needs ARENA_WORKFLOW_HOST=http://workflow-engine:8093 "
            "(ENV2_COMPOSE/.env.arena, the M6 default) or http://workflows-api:9400 (M11 real variant) plus a healthy container."
            % (probe["payouts_workflow_host"], probe["engine_health"]),
            {"probe": probe, "then": "rerun run.py --family approval-workflow"})
    return probe


def _applicable(ctx):
    """Make this journey's fresh merchant workflow-applicable and record both planes."""
    if ctx.state.get("wf_applicable") != ctx.m["merchant_id"]:
        ctx.ev["workflow_applicability"] = ctx.a.make_workflow_applicable(ctx.m["merchant_id"], True)
        ctx.state["wf_applicable"] = ctx.m["merchant_id"]
    else:
        ctx.ev["workflow_applicability"] = {"already_applied_this_run": ctx.m["merchant_id"]}
    return ctx.ev["workflow_applicability"]


def _create_pending(ctx, amount, note, require=True):
    """Create on the workflow-enabled merchant and require PS status `pending` + a workflow id."""
    _require_engine(ctx)
    _applicable(ctx)
    ctx.a.mozart(ctx.m["merchant_id"], "hold")
    cr = ctx.create(amount, note)
    ctx.ev.setdefault("creates", []).append(
        {k: cr.get(k) for k in ("status", "id", "response_status", "raw")})
    row = ctx.wait_status(cr["id"], "pending", timeout=60) if cr["id"] else None
    if (row or {}).get("status") != "pending":
        if not require:
            return cr, row, None
        ctx.blocked(
            "a workflow-applicable merchant. The engine is wired in and healthy, but this fresh "
            "merchant's payout did not reach `pending`: payouts' IsWorkflowApplicable did not "
            "fire. Feature list the monolith returned to payouts: %r; payout row: %r."
            % ((ctx.ev.get("workflow_applicability") or {}).get("feature_list_returned_to_payouts"),
               row),
            {"workflow_applicability": ctx.ev.get("workflow_applicability"),
             "create": ctx.ev["creates"][-1], "payout_row": row,
             "engine_pending": ctx.a.wfe_pending()})
    ctx.ck("payout_parked_at_pending_by_the_workflow_engine", True, row)
    wmap = ctx.a.ps_workflow_map(cr["id"])
    ctx.ev.setdefault("workflow_entity_map", []).append(wmap)
    ctx.ck("payouts_stored_the_engine_workflow_id(workflow_entity_map, 14 chars)",
           bool(wmap and wmap.get("workflow_id")) and len(wmap["workflow_id"]) == 14, wmap)
    wf = ctx.a.wfe_for_payout(cr["id"])
    ctx.ev.setdefault("engine_workflow", []).append(wf)
    ctx.ck("the_engine_holds_a_pending_workflow_for_this_payout",
           bool(wf) and wf.get("state") == "pending", wf)
    ctx.ck("engine_org_is_the_merchant(WFE_TENANT_KEY=owner_id)",
           bool(wf) and wf.get("org_id") == ctx.m["merchant_id"], wf)
    dl = ctx.wait_delivery(cr["id"], {"payout.pending"}, timeout=45)
    ctx.ck("payout.pending_webhook_delivered(StatusToWebhookEventMap PENDING)", len(dl) >= 1, dl)
    return cr, row, wf


def _decide(ctx, pid, decision, queue_if_low_balance=None):
    st, body = ctx.a.wfe_decide(pid, decision, queue_if_low_balance)
    ctx.ev.setdefault("decisions", []).append({"decision": decision, "status": st, "body": body})
    return st, body


def _cb_ok(body):
    cb = (body or {}).get("callback") or {}
    return bool(cb.get("ok")) or cb.get("status_code") in (200, 201, 409)


# ---------------------------------------------------------------------------
@journey("approval-workflow", "success", priority="P0", profile=WF,
         title="workflow-enabled merchant: create -> pending -> approve via the engine -> initiated -> processed",
         source_ref="pkg/workflow/workflow_create.go WfCreate; payout_internal_routes.go:71 approve; "
                    "substitutes/workflow-engine/ARENA.md sections 1 and 3")
def approval_success(ctx):
    cr, row, wf = _create_pending(ctx, 6200, "wf-approve")
    pid = cr["id"]
    st, body = _decide(ctx, pid, "approve")
    ctx.ck("engine_accepted_the_decision_200", st == 200, {"status": st, "body": body})
    ctx.ck("engine_delivered_the_REAL_payouts_approve_callback(200/201/409)", _cb_ok(body),
           (body or {}).get("callback"))
    ctx.ck("callback_target_is_the_payouts_internal_approve_route",
           "/payouts_internal/" in str(((body or {}).get("callback") or {}).get("url"))
           and str(((body or {}).get("callback") or {}).get("url")).endswith("/approve"),
           ((body or {}).get("callback") or {}).get("url"))
    row = ctx.wait_status(pid, "initiated", timeout=60)
    ctx.ck("approved_payout_left_pending_for_initiated", (row or {}).get("status") == "initiated", row)
    logs = ctx.a.payout_logs(pid)
    ctx.ck("payout_logs_record_pending->the_next_state",
           any(l["from"] == "pending" for l in logs), logs)
    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=120)
    ctx.ck("processed_after_approval", (row or {}).get("status") == "processed", row)
    js = ctx.wait_journal("pout_" + pid, "payout_processed", timeout=80)
    ctx.ck("ledger_payout_processed_once", len(js) == 1 and js[0]["balanced"], js)
    dl = ctx.wait_delivery(pid, {"payout.processed"}, timeout=60)
    ctx.ck("payout.processed_webhook", len(dl) == 1, dl)
    after = ctx.a.wfe_for_payout(pid)
    ctx.ck("engine_workflow_is_terminal_approved", (after or {}).get("state") == "approved", after)
    ctx.state["wf_approved_pid"] = pid
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("approval-workflow", "failure", priority="P0", profile=WF,
         title="reject via the engine -> payout.rejected, no dispatch, no debit",
         source_ref="payout_internal_routes.go:77 reject; state_machine.go StateRejected fires WebhookEventRejected")
def approval_reject(ctx):
    before = ctx.a.merchant_balance(ctx.m["merchant_id"])
    cr, row, wf = _create_pending(ctx, 5800, "wf-reject")
    pid = cr["id"]
    st, body = _decide(ctx, pid, "reject")
    ctx.ck("engine_accepted_the_rejection_200", st == 200, {"status": st})
    ctx.ck("engine_delivered_the_REAL_payouts_reject_callback", _cb_ok(body),
           (body or {}).get("callback"))
    row = ctx.wait_status(pid, "rejected", timeout=60)
    ctx.ck("payout_rejected", (row or {}).get("status") == "rejected", row)
    dl = ctx.wait_delivery(pid, {"payout.rejected"}, timeout=60)
    ctx.ck("payout.rejected_webhook_delivered", len(dl) >= 1, dl)
    ctx.ck("no_fts_transfer_for_a_rejected_payout", ctx.a.transfer(pid) is None, None)
    ctx.ck("no_ledger_journal_for_a_rejected_payout", ctx.a.journals("pout_" + pid) == [], None)
    ctx.ck("balance_unchanged",
           abs((ctx.a.merchant_balance(ctx.m["merchant_id"]) or 0) - (before or 0)) < 1e-6,
           {"before": before})
    after = ctx.a.wfe_for_payout(pid)
    ctx.ck("engine_workflow_is_terminal_rejected", (after or {}).get("state") == "rejected", after)
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("approval-workflow", "cancel_or_reverse", priority="P1", profile=WF,
         title="cancel a pending payout: the merchant API refuses it (pending is not a cancel source); the engine's reject is the real route, and a decision on a terminal workflow is refused",
         source_ref="state_machine.go:380 EventCancelled From(queued, scheduled, on_hold) -- `pending` absent; "
                    "workflow-engine ARENA.md: /_arena/decide -> 404 no_pending_workflow / 409 terminal_state")
def approval_cancel_pending(ctx):
    cr, row, wf = _create_pending(ctx, 5600, "wf-cancel")
    pid = cr["id"]
    st, body = ctx.cancel(pid, "m6 cancel while pending")
    ctx.ck("merchant_cancel_of_a_pending_payout_refused_4xx",
           isinstance(st, int) and 400 <= st < 500, {"status": st, "body": str(body)[:250]})
    ctx.ck("payout_still_pending_after_the_refusal",
           (ctx.a.payout(pid) or {}).get("status") == "pending", ctx.a.payout(pid))
    ctx.ck("engine_still_holds_it_pending",
           (ctx.a.wfe_for_payout(pid) or {}).get("state") == "pending", None)
    st2, b2 = _decide(ctx, pid, "reject")
    ctx.ck("engine_reject_is_the_working_cancellation_route_for_pending",
           st2 == 200 and _cb_ok(b2), {"status": st2, "callback": (b2 or {}).get("callback")})
    ctx.ck("payout_rejected",
           (ctx.wait_status(pid, "rejected", timeout=60) or {}).get("status") == "rejected", None)
    # ARENA.md section 1: deciding a payout with no pending workflow is refused, and a
    # lazily-expired one answers 409 -- the engine never re-fires a callback (workflow-sim would).
    st3, b3 = _decide(ctx, pid, "approve")
    ctx.ck("a_second_decision_on_a_terminal_workflow_is_refused(404 no_pending_workflow / 409 terminal_state)",
           st3 in (404, 409), {"status": st3, "body": b3})
    ctx.ck("the_rejected_payout_did_not_move", (ctx.a.payout(pid) or {}).get("status") == "rejected",
           ctx.a.payout(pid))
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("approval-workflow", "idempotency", priority="P0", profile=WF,
         title="a repeated approve callback is idempotent: exactly one transition, one journal set, one terminal webhook",
         source_ref="payoutController.go:1057 PayoutNotInPendingStatus; workflow_create.go:320-325 treats 409 as success; "
                    "workflow-engine callbacks.py stable Idempotency-Key cbk_<wfid>_approve")
def approval_idempotency(ctx):
    cr, row, wf = _create_pending(ctx, 5300, "wf-idem")
    pid = cr["id"]
    st1, b1 = _decide(ctx, pid, "approve")
    ctx.ck("first_approve_accepted_and_delivered", st1 == 200 and _cb_ok(b1),
           {"status": st1, "callback": (b1 or {}).get("callback")})
    ctx.wait_status(pid, "initiated", timeout=60)
    logs_before = ctx.a.payout_logs(pid)
    st2, b2 = _decide(ctx, pid, "approve")
    ctx.ck("second_approve_refused_by_the_engine_rather_than_re-fired(404/409)",
           st2 in (404, 409), {"status": st2, "body": b2})
    # and the callback itself is idempotent on the payouts side: replay it directly.
    wid = (ctx.a.ps_workflow_map(pid) or {}).get("workflow_id")
    cb_url = str(((b1 or {}).get("callback") or {}).get("url") or "")
    ctx.ck("the_engine_recorded_the_exact_callback_url_it_used", bool(cb_url), cb_url)
    hdrs = {"x-creator-id": ctx.m["merchant_id"],
            "X-Razorpay-Account": "acc_" + ctx.m["merchant_id"],
            "Idempotency-Key": "cbk_%s_approve" % (wid or "unknown")}
    basic = "rzp_live:" + F.P._pw("auth_workflow_payouts.txt")
    st3, txt3 = ctx.a.http("POST", cb_url or (F.PAYOUTS + "/v1/payouts/payouts_internal/%s/approve" % pid),
                           {"queue_if_low_balance": False}, hdrs, basic=basic,
                           note="replay of the REAL workflow approve callback, byte-for-byte the "
                                "url the engine used (payout is no longer pending)")
    ctx.ck("replayed_approve_callback_answered_4xx_PayoutNotInPendingStatus(not a second transition)",
           isinstance(st3, int) and 400 <= st3 < 500, {"status": st3, "body": (txt3 or "")[:300]})
    # the same callback addressed with the PUBLIC (pout_-prefixed) id -- recorded, not asserted
    st4, txt4 = ctx.a.http(
        "POST", F.PAYOUTS + "/v1/payouts/payouts_internal/pout_%s/approve" % pid,
        {"queue_if_low_balance": False}, hdrs, basic=basic,
        note="the same replay addressed with the pout_-prefixed id")
    ctx.ev["prefixed_id_replay"] = {"status": st4, "body": (txt4 or "")[:300]}
    if isinstance(st4, int) and st4 >= 500:
        ctx.note("OBSERVATION (reported, not asserted): the workflow approve callback answers "
                 "HTTP %d server_error when the payout is addressed by its PUBLIC `pout_`-prefixed "
                 "id, while the same call with the bare id answers %s. The internal route does not "
                 "normalise the id, so a caller that passes the public form gets a 500 instead of "
                 "a 4xx." % (st4, st3))
    time.sleep(5)
    ctx.ck("no_second_pending->initiated_transition_logged",
           len(ctx.a.payout_logs(pid)) == len(logs_before), {"before": logs_before})
    ctx.a.finish_bank(pid, "success")
    ctx.ck("completes_once",
           (ctx.wait_status(pid, "processed", timeout=120) or {}).get("status") == "processed", None)
    js = ctx.wait_journal("pout_" + pid, "payout_processed", timeout=90)
    ctx.ck("exactly_one_processed_journal", len(js) == 1 and js[0]["balanced"], js)
    ctx.ck("exactly_one_payout.processed_webhook",
           len(ctx.wait_delivery(pid, {"payout.processed"}, timeout=60)) == 1, None)
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("approval-workflow", "concurrency", priority="P1", profile=WF,
         title="approve and reject raced against one pending workflow: exactly one wins, the other is refused, and the payout takes exactly one terminal path",
         source_ref="workflow-engine core.py optimistic version + terminal guard; ARENA.md section 1 "
                    "(/_arena/decide -> 409 terminal_state once decided)")
def approval_concurrency(ctx):
    import threading
    cr, row, wf = _create_pending(ctx, 5900, "wf-race")
    pid = cr["id"]
    results = {}

    def go(decision):
        results[decision] = ctx.a.wfe_decide(pid, decision)

    threads = [threading.Thread(target=go, args=(d,)) for d in ("approve", "reject")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    ctx.ev["race"] = {d: {"status": r[0], "state": (r[1] or {}).get("state"),
                          "callback": (r[1] or {}).get("callback")} for d, r in results.items()}
    winners = [d for d, r in results.items() if r[0] == 200]
    losers = [d for d, r in results.items() if r[0] in (404, 409)]
    ctx.ck("exactly_one_decision_won", len(winners) == 1, ctx.ev["race"])
    ctx.ck("the_other_was_refused_404/409_rather_than_also_applied", len(losers) == 1, ctx.ev["race"])
    won = winners[0] if winners else None
    expect = {"approve": ("initiated", "processing", "processed", "queued"),
              "reject": ("rejected",)}.get(won, ())
    end = ctx.wait_terminal(pid, timeout=90,
                            terminal=("initiated", "rejected", "processed", "queued", "processing"))
    ctx.ck("the_payout_followed_only_the_winning_decision(%s)" % won,
           (end or {}).get("status") in expect, {"won": won, "row": end})
    logs = ctx.a.payout_logs(pid)
    ctx.ck("exactly_one_transition_out_of_pending",
           sum(1 for l in logs if l["from"] == "pending") == 1, logs)
    ctx.ck("engine_workflow_terminal_state_matches_the_winner",
           (ctx.a.wfe_for_payout(pid) or {}).get("state") ==
           {"approve": "approved", "reject": "rejected"}.get(won), ctx.a.wfe_for_payout(pid))
    if won == "approve":
        ctx.a.finish_bank(pid, "success")
        ctx.wait_status(pid, "processed", timeout=120)
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("approval-workflow", "maker_checker", priority="P0", profile=WF,
         title="maker != checker: with separation on, the actor that raised the workflow cannot approve it",
         source_ref="workflow-engine ARENA.md section 2c (separation -> 403 separation_violation); "
                    "RED_LOOP/m5/wfclient.py ActorClient")
def approval_maker_checker(ctx):
    _require_engine(ctx)
    _applicable(ctx)
    org = ctx.m["merchant_id"]
    # 1-of-1 with separation enforced (the arena default, made explicit so the driver is
    # independent of WFE_DEFAULT_* env values).
    stp, pol = ctx.a.wfe_set_policy(org, required_approvals=1, separation=1)
    ctx.ev["policy"] = {"status": stp, "policy": pol}
    ctx.ck("policy_bound_for_this_merchant_org(1 approval, separation on)",
           stp == 200, {"status": stp, "policy": pol})
    sta, maker = ctx.a.wfe_create_actor(org, "m6-maker", ["requester", "approver"])
    stb, checker = ctx.a.wfe_create_actor(org, "m6-checker", ["approver"])
    ctx.ck("two_distinct_approver_identities_provisioned",
           sta == 200 and stb == 200 and maker.get("actor_id") and checker.get("actor_id")
           and maker["actor_id"] != checker["actor_id"],
           {"maker": maker.get("actor_id"), "checker": checker.get("actor_id"),
            "token_returned_once": bool(maker.get("token")) and bool(checker.get("token"))})
    if not (maker.get("token") and checker.get("token")):
        ctx.blocked("workflow-engine admin plane could not mint actor tokens (status %s/%s)"
                    % (sta, stb), {"maker": maker, "checker": checker})
    if F.TRUST["real_workflows"]:
        # M11: the real service's Cadence workflow calls payouts back for every state (clients.payouts_live), so the
        # maker's request must be a REAL payout: the merchant (maker) creates it through the gateway and payouts raises
        # the workflow (WfCreate, creator = the merchant); a synthetic entity id would fail its callback (500) and never
        # reach a terminal domain_status.
        cr, row, wf = _create_pending(ctx, 5000, "wf-maker-checker")
        wid = (wf or {}).get("workflow_id")
        ctx.ev["maker_workflow"] = {"status": cr.get("status"), "payout": cr.get("id"), "workflow": wf}
        ctx.ck("maker_raised_the_workflow", bool(wid) and (wf or {}).get("state") == "pending", wf)
    else:
        # the maker raises the request through the actor plane
        st, wfr = ctx.a.wfe("POST", "/v1/workflows",
                            {"entity_id": "pout_m6mc" + uuid.uuid4().hex[:8], "amount": 5000},
                            token=maker["token"], note="maker raises a workflow (requester role)")
        ctx.ev["maker_workflow"] = {"status": st, "body": wfr}
        ctx.ck("maker_raised_the_workflow", st in (200, 201) and (wfr or {}).get("id"), wfr)
        wid = (wfr or {}).get("id")
    st1, b1 = ctx.a.wfe_actor(maker["token"], "approve", wid)
    if F.TRUST["real_workflows"]:
        # M11 source-supported correction: razorpay/workflows has NO maker != checker rule (internal/action: any actor
        # presenting the checker state's role property advances the state; approveStateIfApplicable only auto-approves
        # behind a DCS feature when the creator's role equals the checker role). The M5 reconstruction's 403
        # separation_violation was an assumption; the real service accepts the creator's own approval.
        ctx.ck("real_workflow_service_accepts_the_makers_own_approval_(no_separation_rule_in_razorpay/workflows)",
               st1 == 200, {"status": st1, "body": b1})
        ctx.note("separation is not enforced by the real Workflow service (source: internal/action, internal/workflow state "
                 "machine); recorded as a difference from the M5 reconstruction in M11_DIFFERENTIAL.md")
        st2, b2 = ctx.a.wfe("GET", "/v1/workflows/%s" % wid, token=maker["token"],
                            note="workflow state after the maker's approval")
        ctx.ck("workflow_is_approved_by_that_single_approval", (b2 or {}).get("state") == "approved", b2)
        st3, b3 = ctx.a.wfe_actor(checker["token"], "approve", wid)
        ctx.ck("a_second_approval_on_the_terminal_workflow_is_refused_409", st3 == 409, {"status": st3, "body": b3})
        audit = ctx.a.wfe_audit(checker["token"], wid)
        ctx.ev["audit"] = audit
        ctx.ck("the_action_trail_records_the_approval_with_the_makers_actor_id",
               any(a.get("action") == "approved" and a.get("actor_id") == maker["actor_id"] for a in audit), audit)
    else:
        ctx.ck("the_maker's_own_approval_is_REFUSED_403_separation_violation",
               st1 == 403, {"status": st1, "body": b1})
        ctx.ck("refusal_names_the_separation_rule",
               "separation" in str(b1).lower(), b1)
        st2, b2 = ctx.a.wfe("GET", "/v1/workflows/%s" % wid, token=maker["token"],
                            note="workflow state after the refused self-approval")
        ctx.ck("workflow_still_pending_after_the_refused_self_approval",
               (b2 or {}).get("state") == "pending", b2)
        st3, b3 = ctx.a.wfe_actor(checker["token"], "approve", wid)
        ctx.ck("a_distinct_checker_CAN_approve", st3 == 200, {"status": st3, "body": b3})
        ctx.ck("workflow_is_approved", (b3 or {}).get("state") == "approved", b3)
        audit = ctx.a.wfe_audit(checker["token"], wid)
        ctx.ev["audit"] = audit
        ctx.ck("the_append_only_audit_trail_records_the_approval_with_its_actor_identity",
               any(a.get("action") == "approved" and a.get("actor_id") == checker["actor_id"]
                   for a in audit), audit)
        ctx.ck("the_refused_self_approval_left_no_approval_row_in_the_trail",
               not any(a.get("action") == "approved" and a.get("actor_id") == maker["actor_id"]
                       for a in audit), audit)
    # cross-tenant: a token minted for THIS merchant cannot read another merchant's workflow
    other = ctx.state.get("wf_engine_other_org_wf")
    if other and not F.TRUST["real_workflows"]:
        st4, b4 = ctx.a.wfe("GET", "/v1/workflows/%s" % other, token=checker["token"],
                            note="cross-org read attempt with this merchant's actor token")
        ctx.ck("cross_merchant_workflow_read_refused_403_cross_org", st4 in (403, 404),
               {"status": st4, "body": b4})
    elif other:
        ctx.note("cross-org read scoping is a caller (dashboard/monolith) concern for the real Workflow service: its API is "
                 "service-authenticated (payouts identity), actor ids are claims -- no per-actor read scoping exists in razorpay/workflows")
    ctx.state["wf_engine_other_org_wf"] = wid


@journey("approval-workflow", "n_of_m", priority="P0", profile=WF,
         title="N-of-M: with required_approvals=2 one approval leaves the payout pending and only the second fires the payouts callback",
         source_ref="workflow-engine admin/policies required_approvals; core.py [CHECK:dup-approval] "
                    "(a repeat approval by the same actor must not increment the distinct count); "
                    "ARENA.md section 2b; payout_internal_routes.go:71 approve callback")
def approval_n_of_m(ctx):
    _require_engine(ctx)
    _applicable(ctx)
    org = ctx.m["merchant_id"]
    # The policy is snapshotted onto the workflow row at Create time
    # (wfengine/core.py create -> required_approvals / separation / eligible_approvers),
    # so it must be bound BEFORE the payout that raises the workflow is created.
    sta, a1 = ctx.a.wfe_create_actor(org, "m6-checker-a", ["approver"])
    stb, a2 = ctx.a.wfe_create_actor(org, "m6-checker-b", ["approver"])
    if not (a1.get("token") and a2.get("token")):
        ctx.blocked("workflow-engine admin plane could not mint actor tokens (status %s/%s)"
                    % (sta, stb), {"a1_status": sta, "b1_status": stb})
    stp, pol = ctx.a.wfe_set_policy(org, required_approvals=2, separation=1,
                                    eligible_approvers=[a1["actor_id"], a2["actor_id"]])
    ctx.ev["policy"] = {"status": stp, "policy": pol}
    ctx.ck("policy_bound_before_create:_2_distinct_approvals_from_an_explicit_eligible_set",
           stp == 200 and int((pol or {}).get("required_approvals") or 0) == 2, pol)
    try:
        cr, row, wf = _create_pending(ctx, 5400, "wf-2of2")
        pid = cr["id"]
        wid = (ctx.a.ps_workflow_map(pid) or {}).get("workflow_id")
        ctx.ck("payouts_and_the_engine_agree_on_the_workflow_id",
               bool(wid) and wid == (wf or {}).get("workflow_id"),
               {"ps": wid, "engine": (wf or {}).get("workflow_id")})
        ctx.ck("the_workflow_carries_the_2-approval_requirement",
               int((wf or {}).get("required_approvals") or 0) == 2, wf)
        st1, b1 = ctx.a.wfe_actor(a1["token"], "approve", wid)
        ctx.ev["first_approval"] = {"status": st1, "body": b1}
        ctx.ck("first_approval_accepted", st1 == 200, {"status": st1, "body": b1})
        ctx.ck("one_of_two:_workflow_is_still_pending", (b1 or {}).get("state") == "pending", b1)
        time.sleep(6)
        ctx.ck("the_payout_did_NOT_leave_pending_on_a_single_approval",
               (ctx.a.payout(pid) or {}).get("status") == "pending", ctx.a.payout(pid))
        # the SAME actor approving again must not count twice
        st2, b2 = ctx.a.wfe_actor(a1["token"], "approve", wid)
        if F.TRUST["real_workflows"]:
            # M11: what the real service does with a repeat approval by the same actor id is OBSERVED and recorded
            # (razorpay/workflows internal/action counts approvals per state; distinct-actor de-duplication is a
            # production unknown until observed)
            ctx.ev["repeat_approval_same_actor"] = {"status": st2, "body": b2, "payout": ctx.a.payout(pid)}
            # observed on the real service: a repeat approval by the same actor on the same state is refused
            # (ActionAPI -> not_found "record not found"); it is neither counted nor accepted
            ctx.ck("repeat_approval_by_the_same_actor_is_refused_or_not_counted_(observed_real_semantics)",
                   st2 in (403, 404, 409) or ((b2 or {}).get("state") == "pending" and int((b2 or {}).get("approvals") or 0) == 1),
                   ctx.ev["repeat_approval_same_actor"])
            if (b2 or {}).get("state") == "approved":
                ctx.note("DIFFERENCE: the real Workflow service counted the same actor twice (state approved after two "
                         "approvals by one actor id). Recorded for M11_DIFFERENTIAL.md; the payout-side checks below adapt.")
        else:
            ctx.ck("a_repeat_approval_by_the_same_actor_does_not_reach_the_threshold",
                   (b2 or {}).get("state") == "pending" and int((b2 or {}).get("approvals") or 0) == 1,
                   {"status": st2, "body": b2})
            ctx.ck("still_pending_after_the_duplicate_approval",
                   (ctx.a.payout(pid) or {}).get("status") == "pending", ctx.a.payout(pid))
        # an actor outside the eligible set is refused (eligible sets are a reconstruction concept; the real service
        # admits any actor presenting the checker role -> role mismatch is the real refusal)
        stc, a3 = ctx.a.wfe_create_actor(org, "m6-checker-outsider", ["approver"] if not F.TRUST["real_workflows"] else ["viewer"])
        if a3.get("token") and (b2 or {}).get("state") == "pending":
            st_o, b_o = ctx.a.wfe_actor(a3["token"], "approve", wid)
            if F.TRUST["real_workflows"]:
                ctx.ck("an_actor_without_the_checker_role_is_refused_by_the_real_service", st_o in (403, 409) and (b_o or {}).get("state", "pending") == "pending", {"status": st_o, "body": b_o})
            else:
                ctx.ck("an_approver_outside_the_eligible_set_is_refused_403_not_eligible",
                       st_o == 403, {"status": st_o, "body": b_o})
        st3, b3 = ctx.a.wfe_actor(a2["token"], "approve", wid)
        ctx.ev["second_approval"] = {"status": st3, "body": b3}
        if F.TRUST["real_workflows"] and (b2 or {}).get("state") == "approved":
            ctx.ck("second_actor_finds_the_workflow_already_terminal_(409)", st3 == 409, b3)
        else:
            ctx.ck("second_distinct_approval_accepted_and_terminal",
                   st3 == 200 and (b3 or {}).get("state") == "approved", b3)
        endrow = ctx.wait_status(pid, "initiated", timeout=90)
        ctx.ck("only_the_Nth_approval_fired_the_payouts_callback(payout left pending)",
               (endrow or {}).get("status") == "initiated", endrow)
        logs = ctx.a.payout_logs(pid)
        ctx.ck("exactly_one_transition_out_of_pending",
               sum(1 for l in logs if l["from"] == "pending") == 1, logs)
        audit = ctx.a.wfe_audit(a2["token"], wid)
        ctx.ev["audit"] = audit
        approvers = {a.get("actor_id") for a in audit if a.get("action") == "approved"}
        if F.TRUST["real_workflows"] and (b2 or {}).get("state") == "approved":
            ctx.ck("action_trail_shows_the_approvals_that_reached_the_threshold", a1["actor_id"] in approvers, {"approvers": sorted(approvers)})
        else:
            ctx.ck("audit_trail_shows_exactly_the_two_distinct_approver_identities",
                   approvers == {a1["actor_id"], a2["actor_id"]}, {"approvers": sorted(approvers)})
        ctx.a.finish_bank(pid, "success")
        ctx.ck("completes_after_the_full_approval_set",
               (ctx.wait_status(pid, "processed", timeout=120) or {}).get("status") == "processed",
               None)
    finally:
        # restore the arena default so any later journey on this merchant needs one approval
        ctx.a.wfe_set_policy(org, required_approvals=1, separation=1)
        ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("approval-workflow", "async_state", priority="P0", profile=WF,
         title="an approved payout completes through the real workers after the out-of-band decision, not inline with the approve call",
         source_ref="the engine never auto-decides (ARENA.md section 2b: actors are not auto-created); "
                    "payouts-worker-* perform the post-approval transitions")
def approval_async_state(ctx):
    cr, row, wf = _create_pending(ctx, 5100, "wf-async")
    pid = cr["id"]
    time.sleep(12)
    ctx.ck("engine_never_auto_approves(payout still pending with no decision)",
           (ctx.a.payout(pid) or {}).get("status") == "pending", ctx.a.payout(pid))
    pending_now = ctx.a.wfe_pending()
    ctx.ck("engine_still_lists_it_as_pending",
           any(p.get("payout_id") in ("pout_" + pid, pid) for p in pending_now),
           [p.get("payout_id") for p in pending_now][:10])
    _decide(ctx, pid, "approve")
    ctx.ck("left_pending_after_the_decision",
           (ctx.wait_status(pid, "initiated", timeout=60) or {}).get("status") == "initiated", None)
    ctx.a.finish_bank(pid, "success")
    ctx.ck("processed",
           (ctx.wait_status(pid, "processed", timeout=120) or {}).get("status") == "processed", None)
    hits = ctx.a.worker_log_hits(pid)
    ctx.ev["worker_log_hits"] = hits
    ctx.ck("real_async_worker_containers_completed_it", bool(hits), hits)
    ctx.ck("the_completion_is_worker_driven_not_the_approve_call",
           any(k.startswith("payouts-worker-") for k in hits), hits)
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)
