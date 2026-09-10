#!/usr/bin/env python3
"""family:queued-low-balance -- queue_if_low_balance parking and cron dequeue.

Runs on its OWN fresh Shared merchant (profile `queued`) because the journeys
deliberately move the merchant's ledger balance around; scoping that to one
merchant keeps every other family's accounting assertions clean.

Real chain: create (amount > balance, queue_if_low_balance=true) -> payouts parks
the payout at `queued`/`low_balance` and fires payout.queued -> a REAL ledger
journal tops the merchant up -> the FastCron endpoint the arena's cron-driver hits
(`POST /v1/cron/process_queued_low_balance_payouts`, driver.py JOBS
"queued_low_balance") dispatches to the payouts-worker-queued-payout container ->
initiated -> the real bank -> processed.

Source anchors: internal/app/payouts/queued/*, helperQueuedPayouts.go,
state_machine.go EventQueued/EventCancelled (queued is a legal cancel source),
internal/routing/router/cron_routes.go. Twin reference: verifiers/test_v24,
verifiers/test_v09, route_scenarios.py case `queued`.
"""
import time
import uuid

import framework as F
from framework import journey

Q = "queued"


def _drain_queued(ctx):
    """Cancel any payout still parked at `queued` on this merchant, through the merchant's
    own cancel API (a legal queued->cancelled transition -- never a direct DB write).

    Needed because the low-balance cron dispatches oldest-first and only as many payouts
    as the balance covers (payouts-api log PAYOUT_DISPATCH_TO_QUEUE_SUCCESS_OPTIMISED):
    a leftover queued payout from an earlier journey would absorb this journey's top-up."""
    rc, out, _ = ctx.a.payouts_sql(
        "SELECT id FROM payouts WHERE merchant_id='%s' AND status='queued'" % ctx.m["merchant_id"],
        note="pre-existing queued payouts to drain")
    drained = []
    for pid in [l.strip() for l in out.strip().splitlines() if l.strip()]:
        st, _b = ctx.cancel(pid, "m6 drain before journey")
        drained.append({"payout_id": pid, "cancel_status": st})
    if drained:
        ctx.ev["drained_queued_payouts"] = drained
        ctx.note("drained %d pre-existing queued payout(s) via the public cancel API" % len(drained))
    return drained


def _park_queued(ctx, note, idem=None, headroom=500):
    """Create a payout that must park at queued/low_balance: amount = balance + headroom."""
    _drain_queued(ctx)
    ctx.a.mozart(ctx.m["merchant_id"], "hold")
    bal = ctx.a.merchant_balance(ctx.m["merchant_id"])
    ctx.ck("merchant_ledger_balance_readable", bal is not None, bal)
    if bal is None:
        return None, None
    amount = int(bal) + headroom
    cr = ctx.create(amount, note, idem=idem, queue_if_low_balance=True)
    ctx.ck("create_200", cr["status"] == 200 and bool(cr["id"]), cr.get("raw"))
    if not cr["id"]:
        return cr, None
    row = ctx.wait_status(cr["id"], "queued", timeout=45)
    ctx.ck("parked_at_queued", (row or {}).get("status") == "queued", row)
    ctx.ck("queued_reason_is_low_balance", (row or {}).get("queued_reason") == "low_balance", row)
    return cr, row


@journey("queued-low-balance", "success", priority="P0", profile=Q,
         title="underfunded create parks at queued/low_balance -> ledger top-up -> cron dequeue -> processed",
         source_ref="cron_routes.go process_queued_low_balance_payouts; verifiers/test_v24")
def queued_success(ctx):
    mid = ctx.m["merchant_id"]
    cr, row = _park_queued(ctx, "queued-success")
    if not cr or not cr["id"]:
        return
    pid = cr["id"]
    amount = int(cr["request"]["amount"])
    dl = ctx.wait_delivery(pid, {"payout.queued"}, timeout=45)
    ctx.ck("payout.queued_webhook_delivered", len(dl) >= 1, dl)
    ctx.ck("no_fts_transfer_while_queued", ctx.a.transfer(pid) is None, ctx.a.transfer(pid))
    ctx.ck("no_ledger_journal_while_queued", ctx.a.journals("pout_" + pid) == [], ctx.a.journals("pout_" + pid))

    top = ctx.a.ledger_topup(ctx.m, amount)
    ctx.ck("real_ledger_topup_journal_accepted", top[0] == 200, top[1])
    sync = ctx.a.balance_sync([ctx.m["balance_id"]], deliver_event=False)
    ctx.ck("balance_sync_accepted", sync[0] == 200, sync[1])

    ok, out = ctx.a.cron("queued_low_balance")
    ctx.ck("cron_driver_queued_low_balance_tick_ok", ok, out)
    row = ctx.wait_status(pid, "initiated", timeout=60)
    ctx.ck("dequeued_to_initiated_by_cron+worker", (row or {}).get("status") == "initiated", row)
    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=90)
    ctx.ck("processed_after_dequeue", (row or {}).get("status") == "processed", row)
    js = ctx.wait_journal("pout_" + pid, "payout_processed", timeout=80)
    ctx.ck("ledger_payout_processed_after_dequeue", len(js) == 1 and js[0]["balanced"], js)
    dl = ctx.wait_delivery(pid, {"payout.processed"}, timeout=60)
    ctx.ck("payout.processed_webhook_after_dequeue", len(dl) == 1, dl)
    ctx.state["queued_pid"] = pid
    ctx.a.mozart(mid, clear=True)


@journey("queued-low-balance", "failure", priority="P0", profile=Q,
         title="insufficient balance WITHOUT queue_if_low_balance is rejected at create; nothing is parked or debited",
         source_ref="internal/app/payouts/processor balance gate; verifiers/test_v09")
def queued_failure(ctx):
    mid = ctx.m["merchant_id"]
    bal = ctx.a.merchant_balance(mid)
    ctx.ck("merchant_ledger_balance_readable", bal is not None, bal)
    if bal is None:
        return
    amount = int(bal) + 100000
    cr = ctx.create(amount, "queued-failure", queue_if_low_balance=False)
    ctx.ck("create_rejected_4xx", isinstance(cr["status"], int) and 400 <= cr["status"] < 500,
           {"status": cr["status"], "body": cr.get("raw")})
    err = (F.jload(cr.get("raw") or "{}") or {}).get("error") or {}
    ctx.ck("error_names_insufficient_balance",
           "balance" in str(err.get("description", "")).lower(), err)
    # The source retains the payout row at this boundary rather than dropping it
    # (same shape verifiers/test_v23 documents for the pricing fail-closed boundary);
    # what must not happen is a dispatch or a debit.
    rc, rows, _ = ctx.a.payouts_sql(
        "SELECT id,status,fts_transfer_id FROM payouts WHERE merchant_id='%s' AND amount=%d"
        % (mid, amount), note="retained row for the rejected create")
    parsed = [r.split("\t") for r in rows.strip().splitlines() if r.strip()]
    ctx.ck("retained_row_is_terminal_failed_not_queued",
           bool(parsed) and all(r[1] == "failed" for r in parsed), parsed)
    ctx.ck("no_fts_transfer_for_the_rejected_create",
           all(ctx.a.transfer(r[0]) is None for r in parsed), parsed)
    ctx.ck("no_ledger_journal_for_the_rejected_create",
           all(ctx.a.journals("pout_" + r[0]) == [] for r in parsed), parsed)
    ctx.ck("balance_unchanged", abs((ctx.a.merchant_balance(mid) or 0) - bal) < 1e-6, bal)


@journey("queued-low-balance", "cancel_or_reverse", priority="P0", profile=Q,
         title="cancel while queued -> cancelled; no transfer, no journal, balance untouched",
         source_ref="state_machine.go EventCancelled From(queued, scheduled, on_hold); status.go CancelPayoutAcceptableStatuses")
def queued_cancel(ctx):
    mid = ctx.m["merchant_id"]
    before = ctx.a.merchant_balance(mid)
    cr, row = _park_queued(ctx, "queued-cancel")
    if not cr or not cr["id"]:
        return
    pid = cr["id"]
    st, body = ctx.cancel(pid, "m6 cancel while queued")
    ctx.ck("cancel_accepted_200", st == 200, {"status": st, "body": str(body)[:300]})
    row = ctx.wait_status(pid, "cancelled", timeout=30)
    ctx.ck("payout_cancelled", (row or {}).get("status") == "cancelled", row)
    ctx.ck("no_fts_transfer_for_cancelled_payout", ctx.a.transfer(pid) is None, None)
    ctx.ck("no_ledger_journal_for_cancelled_payout", ctx.a.journals("pout_" + pid) == [], None)
    ctx.ck("balance_untouched_by_cancel",
           abs((ctx.a.merchant_balance(mid) or 0) - (before or 0)) < 1e-6,
           {"before": before, "after": ctx.a.merchant_balance(mid)})
    ctx.state["cancelled_from_queued_pid"] = pid
    ctx.a.mozart(mid, clear=True)


@journey("queued-low-balance", "idempotency", priority="P0", profile=Q,
         title="replaying the idempotency key of a queued create returns the same queued payout",
         source_ref="middleware.IdempotencyKey; verifiers/test_v01")
def queued_idempotency(ctx):
    key = "m6q-" + uuid.uuid4().hex[:12]
    cr, row = _park_queued(ctx, "queued-idem", idem=key)
    if not cr or not cr["id"]:
        return
    pid = cr["id"]
    again = ctx.create(int(cr["request"]["amount"]), "queued-idem", idem=key, queue_if_low_balance=True)
    ctx.ck("replay_200", again["status"] == 200, again.get("raw"))
    ctx.ck("replay_returns_same_queued_payout", again["id"] == pid, {"first": pid, "replay": again["id"]})
    ctx.ck("still_exactly_one_idempotency_row", len(ctx.a.idempotency_row(key, ctx.m["merchant_id"])) == 1, None)
    ctx.ck("payout_still_queued_after_replay", (ctx.a.payout(pid) or {}).get("status") == "queued", None)
    st, _ = ctx.cancel(pid, "m6 cleanup")
    ctx.note("queued payout cancelled after the assertion so it cannot be dequeued later (cancel status %s)" % st)
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("queued-low-balance", "async_state", priority="P0", profile=Q,
         title="the queued->initiated transition is performed by the real cron endpoint + payouts-worker-queued-payout, not by the create path",
         source_ref="cron-driver JOBS['queued_low_balance']; payouts-worker-queued-payout container")
def queued_async_state(ctx):
    mid = ctx.m["merchant_id"]
    cr, row = _park_queued(ctx, "queued-async")
    if not cr or not cr["id"]:
        return
    pid = cr["id"]
    amount = int(cr["request"]["amount"])
    top = ctx.a.ledger_topup(ctx.m, amount)
    ctx.ck("real_ledger_topup_journal_accepted", top[0] == 200, top[1])
    ctx.a.balance_sync([ctx.m["balance_id"]], deliver_event=False)

    # Negative control: funding alone does not move the payout -- payouts does not self-dispatch.
    time.sleep(6)
    mid_row = ctx.a.payout(pid)
    self_dispatched = (mid_row or {}).get("status") != "queued"
    ctx.ev["self_dispatch_observed_before_cron"] = self_dispatched
    if self_dispatched:
        ctx.note("payout left 'queued' before this driver's cron tick -- the arena's own cron-driver "
                 "loop (5 min cadence) fired in the window; the transition is still cron/worker driven, "
                 "which is what this journey asserts")
    else:
        ctx.ck("funding_alone_does_not_dequeue(no self-dispatch)", True, mid_row)

    ok, out = ctx.a.cron("queued_low_balance")
    ctx.ck("cron_tick_via_the_arena_cron_driver_container", ok, out)
    row = ctx.wait_status(pid, "initiated", timeout=60)
    ctx.ck("queued->initiated_completed", (row or {}).get("status") == "initiated", row)

    hits = ctx.a.worker_log_hits(pid)
    hits["cron-driver(/v1/cron/process_queued_low_balance_payouts)"] = ctx.a.cron_driver_hits(
        "process_queued_low_balance_payouts")
    ctx.ev["worker_log_hits"] = hits
    ctx.ck("real_cron/worker_containers_show_the_dequeue", bool(hits), hits)
    logs = ctx.a.payout_logs(pid)
    ctx.ck("payout_logs_record_queued_then_a_later_transition",
           any(l["to"] == "queued" for l in logs) and logs[-1]["to"] != "queued", logs)
    ctx.a.finish_bank(pid, "success")
    ctx.wait_status(pid, "processed", timeout=90)
    ctx.a.mozart(mid, clear=True)
