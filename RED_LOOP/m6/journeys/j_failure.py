#!/usr/bin/env python3
"""family:failure-reversal-cancellation -- cancellation legality, reversal and the
cancelled-webhook question.

State-machine facts this family pins (pinned clone
internal/app/payouts/state_machine.go:380 and internal/app/payouts/status.go:25):

  EventCancelled.To(cancelled).From(queued, scheduled, on_hold)
  CancelPayoutAcceptableStatuses = {scheduled, queued, on_hold}

so `initiated` (and every terminal state) is NOT a legal cancel source, and
internal/app/payouts/validation.go ValidateCancel additionally refuses a scheduled
payout unless the caller holds proxy (dashboard) auth.

And the webhook fact: state_machine.go:80 `sm.State(StateCancelled).Enter(...)` returns
without calling FireWebhookEventAsyncForPayout, and
internal/app/common/appConstants/webhooks.go StatusToWebhookEventMap has NO `cancelled`
entry -- there is no `payout.cancelled` event in this source at all. The
`webhook` variant asserts that absence against the live twin rather than inventing one.
"""
import time
import uuid

import framework as F
from framework import journey

import j_queued as Q


@journey("failure-reversal-cancellation", "success", priority="P0", profile="queued",
         title="cancel from queued is legal -> cancelled, and the payout is gone from the dequeue cron's view",
         source_ref="state_machine.go:380 EventCancelled From(queued, scheduled, on_hold)")
def cancel_from_queued(ctx):
    cr, row = Q._park_queued(ctx, "cancel-legal")
    if not cr or not cr["id"]:
        return
    pid = cr["id"]
    st, body = ctx.cancel(pid, "m6 legal cancel from queued")
    ctx.ck("cancel_from_queued_accepted_200", st == 200, {"status": st, "body": str(body)[:300]})
    ctx.ck("response_entity_reports_cancelled",
           isinstance(body, dict) and body.get("status") == "cancelled", body)
    r = ctx.wait_status(pid, "cancelled", timeout=30)
    ctx.ck("db_status_cancelled", (r or {}).get("status") == "cancelled", r)
    logs = ctx.a.payout_logs(pid)
    ctx.ck("payout_logs_record_queued->cancelled",
           any(l["from"] == "queued" and l["to"] == "cancelled" for l in logs), logs)
    # fund it and run the dequeue cron: a cancelled payout must never be resurrected
    ctx.a.ledger_topup(ctx.m, int(cr["request"]["amount"]))
    ctx.a.balance_sync([ctx.m["balance_id"]])
    ctx.a.cron("queued_low_balance")
    time.sleep(5)
    ctx.ck("dequeue_cron_does_not_resurrect_a_cancelled_payout",
           (ctx.a.payout(pid) or {}).get("status") == "cancelled", ctx.a.payout(pid))
    ctx.ck("no_fts_transfer_ever", ctx.a.transfer(pid) is None, None)
    ctx.ck("no_ledger_journal_ever", ctx.a.journals("pout_" + pid) == [], None)
    ctx.state["cancelled_pid"] = pid
    ctx.state["cancelled_merchant"] = ctx.m["merchant_id"]
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("failure-reversal-cancellation", "failure", priority="P0", profile="shared",
         title="cancel from initiated is refused by the state machine; the payout keeps running to processed",
         source_ref="status.go:25 CancelPayoutAcceptableStatuses = {scheduled, queued, on_hold}; "
                    "validation.go ValidateCancel -> errorclass.PayoutNotQueuedOrScheduled")
def cancel_from_initiated(ctx):
    ctx.a.mozart(ctx.m["merchant_id"], "hold")
    cr = ctx.create(3100, "cancel-illegal")
    ctx.ck("create_200", cr["status"] == 200 and bool(cr["id"]), cr.get("raw"))
    pid = cr["id"]
    if not pid:
        return
    init = ctx.wait_status(pid, "initiated", timeout=45)
    ctx.ck("payout_is_initiated", (init or {}).get("status") == "initiated", init)
    st, body = ctx.cancel(pid, "m6 illegal cancel from initiated")
    err = (body or {}).get("error") if isinstance(body, dict) else {}
    ctx.ck("cancel_from_initiated_refused_4xx",
           isinstance(st, int) and 400 <= st < 500, {"status": st, "body": str(body)[:300]})
    ctx.ck("refusal_names_the_queued_or_scheduled_rule",
           any(w in str((err or {}).get("description", "")).lower()
               for w in ("queued", "scheduled")), err)
    ctx.ck("payout_still_initiated_after_the_refusal",
           (ctx.a.payout(pid) or {}).get("status") == "initiated", ctx.a.payout(pid))
    # the refusal must not have damaged the in-flight payout
    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=90)
    ctx.ck("payout_still_completes_normally_after_the_refused_cancel",
           (row or {}).get("status") == "processed", row)
    ctx.ck("no_cancellation_user_recorded", not (row or {}).get("cancellation_user_id"), row)
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("failure-reversal-cancellation", "cancel_or_reverse", priority="P0", profile="shared",
         title="terminal payouts cannot be cancelled: cancel on a processed payout and a second cancel on a cancelled payout are both refused",
         source_ref="validation.go IsCancellable; state_machine.go EventCancelled From-set")
def cancel_terminal(ctx):
    ctx.a.mozart(ctx.m["merchant_id"], "hold")
    cr = ctx.create(2700, "cancel-terminal")
    pid = cr["id"]
    ctx.ck("create_200", cr["status"] == 200 and bool(pid), cr.get("raw"))
    if not pid:
        return
    ctx.wait_status(pid, "initiated", timeout=45)
    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=90)
    ctx.ck("payout_processed", (row or {}).get("status") == "processed", row)
    st, body = ctx.cancel(pid, "m6 cancel a processed payout")
    ctx.ck("cancel_on_processed_refused_4xx", isinstance(st, int) and 400 <= st < 500,
           {"status": st, "body": str(body)[:300]})
    ctx.ck("processed_payout_unchanged", (ctx.a.payout(pid) or {}).get("status") == "processed", None)
    # second leg: a payout already cancelled in the queued family cannot be cancelled again
    prev = ctx.state.get("cancelled_pid")
    if prev:
        ctx.note("re-cancelling journey:failure-reversal-cancellation/success payout %s" % prev)
        st2, body2 = ctx.a.kong("POST", "/v1/payouts/cancel_payout/" + prev,
                                F.P._auth(ctx.m["key_id"], ctx.m["secret"]), {"remarks": "m6 re-cancel"},
                                note="cross-merchant re-cancel attempt (also a tenant check)")
        ctx.ck("re_cancel_from_another_merchant_refused",
               isinstance(st2, int) and 400 <= st2 < 500, {"status": st2, "body": (body2 or "")[:250]})
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("failure-reversal-cancellation", "idempotency", priority="P0", profile="queued",
         title="repeating a successful cancel is refused and changes nothing (cancel is not replayable)",
         source_ref="validation.go IsCancellable -> PayoutNotQueuedOrScheduled on the second call")
def cancel_idempotency(ctx):
    cr, row = Q._park_queued(ctx, "cancel-idem")
    if not cr or not cr["id"]:
        return
    pid = cr["id"]
    st1, b1 = ctx.cancel(pid, "m6 first cancel")
    ctx.ck("first_cancel_200", st1 == 200, {"status": st1, "body": str(b1)[:250]})
    r1 = ctx.wait_status(pid, "cancelled", timeout=30)
    logs1 = ctx.a.payout_logs(pid)
    st2, b2 = ctx.cancel(pid, "m6 second cancel")
    ctx.ck("second_cancel_refused_4xx", isinstance(st2, int) and 400 <= st2 < 500,
           {"status": st2, "body": str(b2)[:250]})
    r2 = ctx.a.payout(pid)
    ctx.ck("status_still_cancelled", (r2 or {}).get("status") == "cancelled", r2)
    logs2 = ctx.a.payout_logs(pid)
    ctx.ck("no_second_cancelled_transition_logged", len(logs2) == len(logs1),
           {"before": logs1, "after": logs2})
    ctx.ck("updated_at_unchanged_by_the_refused_repeat",
           (r1 or {}).get("updated_at") == (r2 or {}).get("updated_at"),
           {"first": (r1 or {}).get("updated_at"), "second": (r2 or {}).get("updated_at")})
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("failure-reversal-cancellation", "webhook", priority="P0", profile="queued",
         title="the source emits NO payout.cancelled event -- cancellation is silent to the merchant",
         source_ref="state_machine.go:80 StateCancelled.Enter returns without FireWebhookEventAsyncForPayout; "
                    "appConstants/webhooks.go StatusToWebhookEventMap has no CANCELLED key")
def cancel_webhook(ctx):
    cr, row = Q._park_queued(ctx, "cancel-webhook")
    if not cr or not cr["id"]:
        return
    pid = cr["id"]
    before = ctx.a.deliveries(ctx.m["merchant_id"], pid)
    ctx.ck("queued_state_did_emit_payout.queued",
           any(d["event"] == "payout.queued" for d in before), before)
    st, _b = ctx.cancel(pid, "m6 cancel for webhook check")
    ctx.ck("cancel_accepted", st == 200, st)
    ctx.wait_status(pid, "cancelled", timeout=30)
    time.sleep(10)
    after = ctx.a.deliveries(ctx.m["merchant_id"], pid)
    stork = ctx.a.stork_events(ctx.m["merchant_id"], pid)
    ctx.ck("no_payout.cancelled_delivery(source-faithful)",
           not any(d["event"] == "payout.cancelled" for d in after), after)
    ctx.ck("no_payout.cancelled_event_at_stork(source-faithful)",
           not any(e["event_name"] == "payout.cancelled" for e in stork), stork)
    ctx.ck("no_extra_delivery_of_any_kind_after_the_cancel", len(after) == len(before),
           {"before": [d["event"] for d in before], "after": [d["event"] for d in after]})
    ctx.note("verified against the pinned source: 'payout.cancelled' does not exist in "
             "appConstants/webhooks.go; the merchant learns about a cancellation only by fetching "
             "the payout. Recorded as a product observation, not a twin defect.")
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("failure-reversal-cancellation", "pending", priority="P1", profile="workflow",
         title="cancel a payout parked at pending by an approval workflow: the merchant cancel route refuses it, the engine's reject is the cancellation route",
         source_ref="state_machine.go:380 EventCancelled From(queued, scheduled, on_hold) excludes `pending`; "
                    "payout_internal_routes.go:77 reject is the real cancellation route for a pending payout; "
                    "ENV2_COMPOSE/substitutes/workflow-engine/ARENA.md sections 1-3")
def cancel_pending(ctx):
    import j_approval as A
    cr, row, wf = A._create_pending(ctx, 4900, "frc-pending")
    pid = cr["id"]
    before_logs = ctx.a.payout_logs(pid)
    st, body = ctx.cancel(pid, "m6 cancel a pending payout")
    err = (body or {}).get("error") if isinstance(body, dict) else {}
    ctx.ck("merchant_cancel_of_a_pending_payout_refused_4xx",
           isinstance(st, int) and 400 <= st < 500, {"status": st, "body": str(body)[:300]})
    ctx.ck("refusal_names_the_queued_or_scheduled_rule",
           any(w in str((err or {}).get("description", "")).lower()
               for w in ("queued", "scheduled")), err)
    ctx.ck("payout_still_pending_and_untouched",
           (ctx.a.payout(pid) or {}).get("status") == "pending"
           and len(ctx.a.payout_logs(pid)) == len(before_logs), ctx.a.payout(pid))
    ctx.ck("no_cancellation_user_recorded", not (ctx.a.payout(pid) or {}).get("cancellation_user_id"),
           None)
    ctx.ck("the_engine_still_holds_the_workflow_pending",
           (ctx.a.wfe_for_payout(pid) or {}).get("state") == "pending", None)
    st2, b2 = ctx.a.wfe_decide(pid, "reject")
    ctx.ck("the_engine_reject_callback_is_the_working_route", st2 == 200 and A._cb_ok(b2),
           {"status": st2, "callback": (b2 or {}).get("callback")})
    ctx.ck("payout_rejected",
           (ctx.wait_status(pid, "rejected", timeout=60) or {}).get("status") == "rejected", None)
    ctx.ck("no_fts_transfer_was_ever_created", ctx.a.transfer(pid) is None, None)
    ctx.ck("no_ledger_journal_was_ever_written", ctx.a.journals("pout_" + pid) == [], None)
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("failure-reversal-cancellation", "async_state", priority="P0", profile="shared",
         title="a bank failure becomes a reversal entirely through the async workers: reversal row, reversal ledger journal and payout.reversed webhook",
         source_ref="asyncFailureHandlingHelper.go; payouts-worker-payout-update-failure-handling; test_v17 reversal ordering")
def reversal_async(ctx):
    mid = ctx.m["merchant_id"]
    before = ctx.a.merchant_balance(mid)
    ctx.a.mozart(mid, "hold")
    cr = ctx.create(6600, "reversal-async")
    pid = cr["id"]
    ctx.ck("create_200", cr["status"] == 200 and bool(pid), cr.get("raw"))
    if not pid:
        return
    init = ctx.wait_status(pid, "initiated", timeout=45)
    ctx.ck("initiated_before_any_failure", (init or {}).get("status") == "initiated", init)
    ctx.ck("no_reversal_row_yet", ctx.a.reversal(pid) is None, None)
    ctx.a.finish_bank(pid, "failure")
    row = ctx.wait_status(pid, "reversed", timeout=90)
    ctx.ck("async_workers_moved_the_payout_to_reversed", (row or {}).get("status") == "reversed", row)
    rev = F.wait_until(lambda: ctx.a.reversal(pid), timeout=40, interval=2)
    ctx.ck("async_workers_created_the_reversal_row", bool(rev), rev)
    if rev:
        js = ctx.wait_journal("rvrsl_" + rev["id"], "payout_failed", timeout=70)
        ctx.ck("async_ledger_worker_wrote_the_reversal_journal(payout_failed)",
               len(js) == 1 and js[0]["balanced"], js)
        link = F.wait_until(lambda: (lambda r: r if r and r.get("transaction_id") else None)(ctx.a.reversal(pid)),
                            timeout=40, interval=2)
        ctx.ck("reversal_linked_to_its_ledger_transaction", bool(link and link.get("transaction_id")), link)
    dl = ctx.wait_delivery(pid, {"payout.reversed"}, timeout=60)
    ctx.ck("async_webhook_worker_delivered_payout.reversed", len(dl) == 1, dl)
    after = ctx.a.merchant_balance(mid)
    ctx.ck("balance_restored_by_the_reversal",
           before is not None and after is not None and abs(before - after) < 1e-6,
           {"before": before, "after": after})
    hits = ctx.a.worker_log_hits(pid)
    ctx.ev["worker_log_hits"] = hits
    ctx.ck("real_async_worker_containers_show_the_reversal", bool(hits), hits)
    ctx.ev["linked_reversal_journeys"] = {
        "shared_failure_pid": ctx.state.get("shared_failure_pid"),
        "shared_returned_reversal_pid": ctx.state.get("shared_reversal_pid"),
        "this_pid": pid}
    ctx.a.mozart(mid, clear=True)
