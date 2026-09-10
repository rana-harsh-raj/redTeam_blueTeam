#!/usr/bin/env python3
"""family:shared-payouts -- the Shared/pool-account payout business journeys.

Every driver here runs on ONE fresh Shared merchant (provisioner.provision_funded_merchant:
apidb + x-balances + payouts + ledger sub-accounts + FTS pool mapping + cfa + monolith
merchant-config/pricing/fund-account + kong key) and drives the real create -> FTS ->
mozart-sim bank -> ledger -> stork -> merchant-sink chain through the merchant's own
public API. Nothing writes payout state directly.

Source anchors (pinned clone .local/twin-repos/accepted/payouts):
  internal/app/payouts/state_machine.go            transitions + which states fire webhooks
  internal/app/common/appConstants/webhooks.go     StatusToWebhookEventMap
  internal/app/common/appConstants/events.go       payout_initiated / payout_processed ledger events
  internal/routing/middleware (IdempotencyKey)     X-Payout-Idempotency semantics
Twin reference behaviour: ENV2_COMPOSE/verifier/route_scenarios.py cases direct_success /
failed_shared / returned, verifiers/test_v05, test_v06, test_v07, test_v21.
"""
import time
import uuid

import framework as F
from framework import journey

SHARED = "shared"


# ---------------------------------------------------------------------------
def _clean(ctx):
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


def _hold_then(ctx, amount, scenario, note, idem=None, queue=False, mode="IMPS"):
    """Create with the bank held so `initiated` is observable, then drive the real
    FTS status-check action with the chosen bank scenario on the selected attempt."""
    ctx.a.mozart(ctx.m["merchant_id"], "hold")
    cr = ctx.create(amount, note, idem=idem, queue_if_low_balance=queue, mode=mode)
    ctx.ck("create_200", cr["status"] == 200 and bool(cr["id"]), cr.get("raw"))
    if not cr["id"]:
        return cr, None, None
    init = ctx.wait_status(cr["id"], "initiated", timeout=45)
    ctx.ck("reaches_initiated", (init or {}).get("status") == "initiated", init)
    t, chk = ctx.a.finish_bank(cr["id"], scenario)
    return cr, init, {"transfer": t, "check": chk}


def _terminal_webhook(ctx, pid, event, status):
    dl = ctx.wait_delivery(pid, {event}, timeout=60)
    ctx.ck("webhook_%s_delivered_once" % event, len(dl) == 1, dl)
    if dl:
        d = dl[0]
        ctx.ck("webhook_signature_present_and_valid",
               d.get("signature_present") is True and d.get("signature_valid") is True, d)
        ctx.ck("webhook_account_id_scoped_to_merchant",
               d.get("account_id") == "acc_" + ctx.m["merchant_id"], d)
        ctx.ck("webhook_entity_status_matches_db", d.get("payout_status") == status, d)
    return dl


# ---------------------------------------------------------------------------
@journey("shared-payouts", "success", priority="P0", profile=SHARED,
         title="create -> initiated -> processed; payout_initiated+payout_processed journals; payout.processed webhook",
         source_ref="state_machine.go StateProcessed Enter -> FireWebhookEventAsyncForPayout; events.go PayoutProcessedLedgerEvent")
def shared_success(ctx):
    mid = ctx.m["merchant_id"]
    amount = 10000
    before = ctx.a.merchant_balance(mid)
    cr, init, fb = _hold_then(ctx, amount, "success", "success")
    pid = cr["id"]
    if not pid:
        return
    row = ctx.wait_status(pid, "processed", timeout=90)
    ctx.ck("terminal_processed", (row or {}).get("status") == "processed", row)
    t = ctx.a.transfer(pid)
    ctx.ck("fts_transfer_PROCESSED", (t or {}).get("status") == "PROCESSED", t)
    ctx.ck("payout_utr_equals_fts_utr", bool(row and row.get("utr")) and row.get("utr") == (t or {}).get("utr"),
           {"payout_utr": (row or {}).get("utr"), "fts_utr": (t or {}).get("utr")})

    js_init = ctx.wait_journal("pout_" + pid, "payout_initiated", timeout=40)
    ctx.ck("ledger_payout_initiated_once_and_balanced",
           len(js_init) == 1 and js_init[0]["balanced"], js_init)
    js_proc = ctx.wait_journal("pout_" + pid, "payout_processed", timeout=80)
    ctx.ck("ledger_payout_processed_once_and_balanced",
           len(js_proc) == 1 and js_proc[0]["balanced"], js_proc)

    after = ctx.a.merchant_balance(mid)
    fees = float((row or {}).get("fees") or 0)
    ctx.ck("balance_debited_amount_plus_fees(incl tax)",
           before is not None and after is not None and abs((before - after) - (amount + fees)) < 1e-6,
           {"before": before, "after": after, "amount": amount, "fees": fees})

    _terminal_webhook(ctx, pid, "payout.processed", "processed")
    ctx.state["shared_success_pid"] = pid
    ctx.state["shared_merchant"] = ctx.m
    _clean(ctx)


@journey("shared-payouts", "failure", priority="P0", profile=SHARED,
         title="bank INVALID_ACCOUNT_NUMBER -> Shared remap to reversed + reversal payout_failed journal + payout.reversed webhook",
         source_ref="test_v18 shared-vs-direct failed remap; route_scenarios.py case failed_shared")
def shared_failure(ctx):
    mid = ctx.m["merchant_id"]
    amount = 7700
    before = ctx.a.merchant_balance(mid)
    cr, init, fb = _hold_then(ctx, amount, "failure", "failure")
    pid = cr["id"]
    if not pid:
        return
    row = ctx.wait_terminal(pid, timeout=90)
    status = (row or {}).get("status")
    ctx.note("Shared + bank FAILED remaps to payout status %r (V18: Shared failed->reversed, "
             "Direct rbl failed->failed)" % status)
    ctx.ck("terminal_is_shared_failure_remap(reversed)", status == "reversed", row)
    t = ctx.a.transfer(pid)
    ctx.ck("fts_transfer_FAILED", (t or {}).get("status") == "FAILED", t)
    rc, code, _ = ctx.a.fts_sql(
        "SELECT bank_status_code FROM attempts WHERE transfer_id=%s ORDER BY id DESC LIMIT 1" % (t or {}).get("id"),
        note="bank status code persisted by FTS")
    ctx.ck("fts_bank_status_code_INVALID_ACCOUNT_NUMBER", code.strip() == "INVALID_ACCOUNT_NUMBER", code.strip())

    rev = ctx.a.reversal(pid)
    ctx.ck("reversal_row_created", bool(rev), rev)
    if rev:
        js = ctx.wait_journal("rvrsl_" + rev["id"], "payout_failed", timeout=60)
        ctx.ck("ledger_payout_failed_journal_on_reversal_once_and_balanced",
               len(js) == 1 and js[0]["balanced"], js)
    js_init = ctx.a.journals("pout_" + pid)
    ctx.ck("ledger_payout_initiated_present", any(j["transactor_event"] == "payout_initiated" for j in js_init), js_init)

    after = ctx.a.merchant_balance(mid)
    ctx.ck("merchant_balance_fully_restored",
           before is not None and after is not None and abs(before - after) < 1e-6,
           {"before": before, "after": after})
    _terminal_webhook(ctx, pid, "payout.reversed", "reversed")
    ctx.state["shared_failure_pid"] = pid
    _clean(ctx)


@journey("shared-payouts", "pending", priority="P1", profile=SHARED,
         title="mozart delayed_success: bank pending on poll 1, success on poll 2 -> processed exactly once",
         source_ref="seeds/mozart_scenarios.json arena_pending_then_processed_100300")
def shared_pending(ctx):
    amount = 6100
    ctx.a.mozart(ctx.m["merchant_id"], "delayed_success", polls=2)
    cr = ctx.create(amount, "pending")
    ctx.ck("create_200", cr["status"] == 200 and bool(cr["id"]), cr.get("raw"))
    pid = cr["id"]
    if not pid:
        return
    init = ctx.wait_status(pid, "initiated", timeout=45)
    ctx.ck("initiated_while_bank_pending", (init or {}).get("status") == "initiated", init)
    t = F.wait_until(lambda: (lambda x: x if x and x.get("attempt_id") else None)(ctx.a.transfer(pid)),
                     timeout=40, interval=2)
    ctx.ck("fts_transfer_still_INITIATED_after_first_poll", (t or {}).get("status") == "INITIATED", t)
    # poll again: mozart-sim returns SUCCESS from poll 2 onwards for this attempt
    ctx.a.fts_check(t["id"])
    row = ctx.wait_status(pid, "processed", timeout=90)
    ctx.ck("processed_after_second_poll", (row or {}).get("status") == "processed", row)
    ctx.ck("processed_has_utr", bool((row or {}).get("utr")), row)
    js = ctx.wait_journal("pout_" + pid, "payout_processed", timeout=80)
    ctx.ck("exactly_one_processed_journal", len(js) == 1, js)
    dl = ctx.wait_delivery(pid, {"payout.processed"}, timeout=60)
    ctx.ck("exactly_one_processed_webhook", len(dl) == 1, dl)
    _clean(ctx)


@journey("shared-payouts", "cancel_or_reverse", priority="P0", profile=SHARED,
         title="bank RETURNED after a processed poll -> FTS admin re-verify -> reversed + payout_reversed journal + payout.reversed webhook",
         source_ref="route_scenarios.py case 'returned'; mozart_scenarios.json arena_reversed_100600")
def shared_reversal(ctx):
    mid = ctx.m["merchant_id"]
    amount = 5300
    before = ctx.a.merchant_balance(mid)
    ctx.a.mozart(mid, "returned", polls=1)
    cr = ctx.create(amount, "returned")
    ctx.ck("create_200", cr["status"] == 200 and bool(cr["id"]), cr.get("raw"))
    pid = cr["id"]
    if not pid:
        return
    row = ctx.wait_status(pid, "processed", timeout=90)
    ctx.ck("first_reaches_processed", (row or {}).get("status") == "processed", row)
    t = ctx.a.transfer(pid)
    aid = (t or {}).get("attempt_id")
    # Normal polling is closed for a terminal attempt: FTS answers ILLEGAL_STATE.
    st, txt = ctx.a.fts_check(t["id"])
    ctx.ck("normal_poll_refused_for_terminal_attempt(ILLEGAL_STATE)",
           st == 400 and "ILLEGAL_STATE" in (txt or ""), {"status": st, "body": (txt or "")[:200]})
    # Explicit FTS admin reconciliation: raw verify observes the bank, then safe_update.
    mono_pw = (F.ENV2 / "secrets" / "auth_monolith_shared.txt").read_text().strip()
    admin = "api_monolith:" + mono_pw
    st, ver = ctx.a.jhttp("POST", F.FTS + "/v1/attempts/verify", {"attempt_ids": [str(aid)]},
                          basic=admin, note="FTS admin raw bank verification")
    raw = F.jload(((ver or {}).get(str(aid)) or {}).get("raw_status") or "{}") or {}
    ctx.ck("bank_reports_RETURNED_with_return_utr",
           (raw.get("data") or {}).get("bank_status_code") == "RETURNED"
           and bool((raw.get("data") or {}).get("return_utr")), raw)
    upd = {str(aid): {"remarks": "M6ReturnReconciliation",
                      "bank_status_code": (raw.get("data") or {}).get("bank_status_code"),
                      "return_utr": (raw.get("data") or {}).get("return_utr"),
                      "meta": {"reversed": True}}}
    st, txt = ctx.a.http("PATCH", F.FTS + "/v1/attempts/safe_update", upd, basic=admin,
                         note="FTS admin safe_update (re-verifies the bank itself)")
    ctx.ck("fts_safe_update_accepted", st == 200, (txt or "")[:200])
    row = ctx.wait_status(pid, "reversed", timeout=90)
    ctx.ck("payout_reversed", (row or {}).get("status") == "reversed", row)
    rev = ctx.a.reversal(pid)
    ctx.ck("reversal_row_created", bool(rev), rev)
    if rev:
        js = ctx.wait_journal("rvrsl_" + rev["id"], "payout_reversed", timeout=70)
        ctx.ck("ledger_payout_reversed_once_and_balanced", len(js) == 1 and js[0]["balanced"], js)
    after = ctx.a.merchant_balance(mid)
    ctx.ck("merchant_balance_restored_after_return",
           before is not None and after is not None and abs(before - after) < 1e-6,
           {"before": before, "after": after})
    _terminal_webhook(ctx, pid, "payout.reversed", "reversed")
    ctx.state["shared_reversal_pid"] = pid
    _clean(ctx)


@journey("shared-payouts", "idempotency", priority="P0", profile=SHARED,
         title="same X-Payout-Idempotency + same body -> same payout id; same key + different body -> rejected",
         source_ref="internal/routing/middleware IdempotencyKey; verifiers test_v01 / test_v02")
def shared_idempotency(ctx):
    ctx.a.mozart(ctx.m["merchant_id"], "hold")
    key = "m6idem-" + uuid.uuid4().hex[:12]
    a = ctx.create(4200, "idem", idem=key)
    ctx.ck("first_create_200", a["status"] == 200 and bool(a["id"]), a.get("raw"))
    b = ctx.create(4200, "idem", idem=key)
    ctx.ck("replay_same_key_same_body_200", b["status"] == 200, b.get("raw"))
    ctx.ck("replay_returns_the_same_payout_id", a["id"] and a["id"] == b["id"],
           {"first": a["id"], "replay": b["id"]})
    c = ctx.create(4201, "idem", idem=key)
    ctx.ck("same_key_different_body_rejected(4xx)",
           isinstance(c["status"], int) and 400 <= c["status"] < 500,
           {"status": c["status"], "body": c.get("raw")})
    rows = ctx.a.idempotency_row(key, ctx.m["merchant_id"])
    ctx.ck("exactly_one_idempotency_key_row", len(rows) == 1, rows)
    rc, out, _ = ctx.a.payouts_sql(
        "SELECT count(*) FROM payouts WHERE merchant_id='%s' AND amount IN (4200,4201) AND id!='%s'"
        % (ctx.m["merchant_id"], a["id"]), note="no sibling payout minted by the replays")
    ctx.ck("no_second_payout_created_by_replay_or_conflict", out.strip() == "0", out.strip())
    ctx.a.finish_bank(a["id"], "success")
    ctx.wait_terminal(a["id"], timeout=90)
    _clean(ctx)


@journey("shared-payouts", "concurrency", priority="P1", profile=SHARED,
         title="parallel creates on one idempotency key -> exactly one payout",
         source_ref="verifiers/test_m4_g52_concurrent_idempotency.py")
def shared_concurrency(ctx):
    import threading
    ctx.a.mozart(ctx.m["merchant_id"], "hold")
    key = "m6conc-" + uuid.uuid4().hex[:12]
    out = []
    lock = threading.Lock()

    def fire():
        r = ctx.create(3300, "conc", idem=key)
        with lock:
            out.append(r)

    threads = [threading.Thread(target=fire) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ids = {r["id"] for r in out if r["id"]}
    statuses = sorted(str(r["status"]) for r in out)
    ctx.ck("all_four_requests_answered", len(out) == 4, statuses)
    ctx.ck("at_most_one_distinct_payout_id", len(ids) <= 1, {"ids": sorted(ids), "statuses": statuses})
    ctx.ck("at_least_one_request_succeeded", any(r["status"] == 200 for r in out), statuses)
    rows = ctx.a.idempotency_row(key, ctx.m["merchant_id"])
    ctx.ck("exactly_one_idempotency_key_row", len(rows) == 1, rows)
    pid = next(iter(ids), None)
    if pid:
        rc, cnt, _ = ctx.a.payouts_sql(
            "SELECT count(*) FROM payouts WHERE merchant_id='%s' AND amount=3300"
            % ctx.m["merchant_id"], note="exactly one 3300 payout exists")
        ctx.ck("exactly_one_payout_row_for_the_concurrent_amount", cnt.strip() == "1", cnt.strip())
        ctx.a.finish_bank(pid, "success")
        ctx.wait_terminal(pid, timeout=90)
    _clean(ctx)


@journey("shared-payouts", "duplicate", priority="P0", profile=SHARED,
         title="replayed terminal FTS transfer_status_webhook is a no-op (no second journal, no second webhook)",
         source_ref="fts_transfer_status_webhook.go state guard; verifiers test_v15 / test_v21 / test_m4_g34")
def shared_duplicate_terminal(ctx):
    cr, init, fb = _hold_then(ctx, 2900, "success", "dup")
    pid = cr["id"]
    if not pid:
        return
    row = ctx.wait_status(pid, "processed", timeout=90)
    ctx.ck("processed", (row or {}).get("status") == "processed", row)
    ctx.wait_journal("pout_" + pid, "payout_processed", timeout=80)
    ctx.wait_delivery(pid, {"payout.processed"}, timeout=60)
    before_j = ctx.a.journals("pout_" + pid)
    before_w = [d for d in ctx.a.deliveries(ctx.m["merchant_id"], pid) if d["event"] == "payout.processed"]
    t = ctx.a.transfer(pid)
    body = {"fund_transfer_id": int(t["id"]), "source_id": pid, "source_type": "payout",
            "status": "processed", "utr": row.get("utr"),
            "source_account_id": int(t["source_account_id"]),
            "bank_account_type": t.get("sa_bank_account_type")}
    st, txt = ctx.a.http("POST", F.PAYOUTS + "/v1/payouts/transfer_status_webhook", body,
                         basic=F.bridge("ps-service"),
                         note="replayed terminal FTS status webhook (payout_internal_routes.go, service Basic-Auth)")
    ctx.ck("replayed_terminal_webhook_reached_the_real_route(not 401/404)",
           isinstance(st, int) and st not in (401, 403, 404), {"status": st, "body": (txt or "")[:250]})
    ctx.ck("replayed_terminal_webhook_accepted_or_guarded",
           isinstance(st, int) and st in (200, 400, 409), {"status": st, "body": (txt or "")[:250]})
    time.sleep(6)
    after_j = ctx.a.journals("pout_" + pid)
    after_w = [d for d in ctx.a.deliveries(ctx.m["merchant_id"], pid) if d["event"] == "payout.processed"]
    ctx.ck("no_extra_ledger_journal_from_the_replay", len(after_j) == len(before_j),
           {"before": [j["transactor_event"] for j in before_j], "after": [j["transactor_event"] for j in after_j]})
    ctx.ck("no_extra_terminal_webhook_from_the_replay", len(after_w) == len(before_w),
           {"before": len(before_w), "after": len(after_w)})
    ctx.ck("payout_status_unchanged", (ctx.a.payout(pid) or {}).get("status") == "processed", None)
    _clean(ctx)


@journey("shared-payouts", "accounting", priority="P0", profile=SHARED,
         title="every journal on a Shared payout balances to zero and the merchant balance delta equals amount+fees",
         source_ref="verifiers test_v05 (initiated debit = amount+fees, fees include tax) / test_v06")
def shared_accounting(ctx):
    mid = ctx.m["merchant_id"]
    amount = 8800
    before = ctx.a.merchant_balance(mid)
    cr, init, fb = _hold_then(ctx, amount, "success", "acct")
    pid = cr["id"]
    if not pid:
        return
    row = ctx.wait_status(pid, "processed", timeout=90)
    ctx.ck("processed", (row or {}).get("status") == "processed", row)
    ctx.wait_journal("pout_" + pid, "payout_processed", timeout=80)
    js = ctx.a.journals("pout_" + pid)
    ctx.ck("both_ledger_events_present",
           sorted(j["transactor_event"] for j in js) == ["payout_initiated", "payout_processed"], js)
    ctx.ck("every_journal_sums_to_zero", bool(js) and all(j["balanced"] for j in js),
           [(j["transactor_event"], j["sum_debit"], j["sum_credit"]) for j in js])
    ctx.ck("every_journal_has_entries", bool(js) and all(j["entries"] >= 2 for j in js), js)
    fees = float((row or {}).get("fees") or 0)
    tax = float((row or {}).get("tax") or 0)
    after = ctx.a.merchant_balance(mid)
    ctx.ck("balance_delta == amount + fees (fees include tax)",
           before is not None and after is not None and abs((before - after) - (amount + fees)) < 1e-6,
           {"before": before, "after": after, "amount": amount, "fees": fees, "tax": tax})
    init_j = [j for j in js if j["transactor_event"] == "payout_initiated"]
    if init_j:
        entries = ctx.a.journal_entries(init_j[0]["id"])
        debit = sum(float(e["amount"]) for e in entries if e["type"].strip() == "debit")
        ctx.ck("initiated_debit == amount + fees", abs(debit - (amount + fees)) < 1e-6,
               {"debit": debit, "expected": amount + fees, "entries": entries})
    _clean(ctx)


@journey("shared-payouts", "async_state", priority="P0", profile=SHARED,
         title="terminal state reached only through the real async workers (transaction-create / webhook-event / ledger) -- no synchronous shortcut",
         source_ref="payouts-worker-* containers; core.go:2583 payout_processed pushed to the async queue")
def shared_async_state(ctx):
    """Proves the completion is worker-driven: the payout is created with the bank
    held (so nothing can complete inline), the API response is non-terminal, and the
    terminal status + ledger journal + webhook all appear only after the real FTS
    status-check action hands off to the payouts async workers."""
    mid = ctx.m["merchant_id"]
    cr, init, fb = _hold_then(ctx, 4400, "success", "async")
    pid = cr["id"]
    if not pid:
        return
    ctx.ck("create_response_is_non_terminal",
           cr.get("response_status") not in ("processed", "reversed", "failed"), cr.get("response_status"))
    ctx.ck("row_was_initiated_before_any_bank_completion", (init or {}).get("status") == "initiated", init)
    row = ctx.wait_status(pid, "processed", timeout=90)
    ctx.ck("worker_moved_payout_to_processed", (row or {}).get("status") == "processed", row)
    js = ctx.wait_journal("pout_" + pid, "payout_processed", timeout=80)
    ctx.ck("async_ledger_worker_wrote_payout_processed_journal", len(js) == 1, js)
    dl = ctx.wait_delivery(pid, {"payout.processed"}, timeout=60)
    ctx.ck("async_webhook_event_worker_delivered_payout.processed", len(dl) == 1, dl)
    ev = ctx.a.stork_events(mid, pid)
    ctx.ck("stork_recorded_the_terminal_event_delivered_200",
           any(e["event_name"] == "payout.processed" and e["delivered_status"] == 200 for e in ev), ev)
    logs = ctx.a.payout_logs(pid)
    ctx.ck("payout_logs_show_the_real_transition_chain(created->initiated->processed)",
           [l["to"] for l in logs][-1:] == ["processed"] and any(l["to"] == "initiated" for l in logs), logs)
    # Evidence that the async worker containers are the ones doing it.
    hits = ctx.a.worker_log_hits(pid)
    ctx.ev["worker_log_hits"] = hits
    ctx.ck("at_least_one_real_payouts_worker_container_logged_this_payout", bool(hits), hits)
    _clean(ctx)


@journey("shared-payouts", "restart", priority="P1", profile=SHARED,
         title="in-flight payout survives a restart of the async workers that own its completion",
         source_ref="payouts-worker-fts-async-processing / -webhook-event and "
                    "fts-worker-fire-transfer-status-webhook are the containers that carry a held "
                    "transfer to processed; SQS visibility-timeout redelivery is what makes that "
                    "survivable ([queue.sqs] visibilityTimeout=120 in the rendered arena.toml)")
def shared_restart(ctx):
    """REAL restart-recovery. Gated on M6_ALLOW_RESTART=1 (run.py sets it, the reboot owner
    delegates these three containers to this journey). The payout is parked at `initiated` with
    the bank held, the three worker containers that own its completion are restarted, the hold is
    then released, and the payout must still complete end to end -- proving the in-flight work was
    picked up again after the restart rather than lost."""
    import os
    if os.environ.get("M6_ALLOW_RESTART") != "1":
        ctx.blocked(
            "explicit permission to restart the three delegated worker containers. This journey "
            "restarts ONLY %s and nothing else; it is gated on M6_ALLOW_RESTART=1 so a run that "
            "must not touch containers stays inert." % ", ".join(F.Arena.RESTARTABLE),
            {"set": "M6_ALLOW_RESTART=1 (run.py sets it by default)",
             "arena_boot_id": (F.fingerprint() or {}).get("boot_id")})
    mid = ctx.m["merchant_id"]
    before_balance = ctx.a.merchant_balance(mid)
    ctx.a.mozart(mid, "hold")
    cr = ctx.create(4600, "restart")
    pid = cr["id"]
    ctx.ck("create_200", cr["status"] == 200 and bool(pid), cr.get("raw"))
    if not pid:
        return
    init = ctx.wait_status(pid, "initiated", timeout=60)
    ctx.ck("payout_is_in_flight_at_initiated_with_the_bank_held",
           (init or {}).get("status") == "initiated", init)
    t = F.wait_until(lambda: (lambda x: x if x and x.get("attempt_id") else None)(ctx.a.transfer(pid)),
                     timeout=45, interval=2)
    ctx.ck("an_fts_transfer_and_attempt_exist_before_the_restart", bool(t), t)

    # the M6 worker trio (+ the shared ingress, M7); the M11 trust-path services on the allow-list (edge-kong, shield-web)
    # have their own journey (trust-path/restart_cache_invalidation) and are absent from the substitute variant
    services = [s for s in F.Arena.RESTARTABLE if s not in ("edge-kong", "shield-web")]
    rec = ctx.a.restart_workers(services)
    ctx.ev["container_restarts"] = rec
    ctx.ck("all_three_delegated_worker_containers_actually_restarted",
           all(r["actually_restarted"] for r in rec["restarts"]),
           [{k: r[k] for k in ("service", "started_at_before", "started_at_after",
                               "restart_issued_at")} for r in rec["restarts"]])
    ctx.ck("all_three_came_back_running_and_healthy",
           all(r["status_after"] == "running" and r["health_after"] in ("healthy", "none")
               for r in rec["restarts"]),
           [{k: r[k] for k in ("service", "status_after", "health_after")} for r in rec["restarts"]])
    ctx.ck("the_payout_was_untouched_by_the_restart_itself",
           (ctx.a.payout(pid) or {}).get("status") == "initiated", ctx.a.payout(pid))

    # release the hold exactly the way every other journey does, now that the workers are new
    t2, chk = ctx.a.finish_bank(pid, "success")
    ctx.ev["finish_bank_after_restart"] = {"transfer": t2, "check": chk}
    row = ctx.wait_status(pid, "processed", timeout=180)
    ctx.ck("the_in_flight_payout_still_completed_after_the_worker_restart",
           (row or {}).get("status") == "processed", row)
    ctx.ck("utr_propagated_from_fts", bool((row or {}).get("utr")), row)
    js = ctx.wait_journal("pout_" + pid, "payout_processed", timeout=100)
    ctx.ck("ledger_payout_processed_journal_written_exactly_once_after_the_restart",
           len(js) == 1 and js[0]["balanced"], js)
    dl = ctx.wait_delivery(pid, {"payout.processed"}, timeout=90)
    ctx.ck("payout.processed_webhook_delivered_exactly_once_after_the_restart", len(dl) == 1, dl)
    hits = ctx.a.worker_log_hits(pid, since="30m")
    ctx.ev["worker_log_hits"] = hits
    ctx.ck("the_restarted_worker_containers_are_the_ones_that_finished_it",
           any(s in hits for s in services), hits)
    after = ctx.a.merchant_balance(mid)
    fees = float((row or {}).get("fees") or 0)
    ctx.ck("balance_moved_exactly_once(no double debit across the restart)",
           before_balance is not None and after is not None
           and abs((before_balance - after) - (4600 + fees)) < 1e-6,
           {"before": before_balance, "after": after, "fees": fees})
    logs = ctx.a.payout_logs(pid)
    ctx.ck("exactly_one_initiated->processed_transition_logged",
           sum(1 for l in logs if l["to"] == "processed") == 1, logs)
    _clean(ctx)
