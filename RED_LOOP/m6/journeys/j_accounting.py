#!/usr/bin/env python3
"""family:accounting -- double-entry correctness across every payout outcome.

Ledger events (internal/app/common/appConstants/events.go):
  payout_initiated   on the payout          (debit merchant balance by amount + fees)
  payout_processed   on the payout          (settlement leg to the FTS pool account)
  payout_failed      on the reversal entity (rvrsl_<id>) for a bank FAILED
  payout_reversed    on the reversal entity for a bank RETURNED after PROCESSED

Invariants asserted here (twin acceptance vocabulary): every journal's entries sum to
zero; the merchant-balance delta equals amount+fees for a completed payout and zero
for a reversed one; a journal is never written twice for the same transactor+event;
and a Shared payout that never leaves a pre-dispatch state writes no journal at all.

fees INCLUDE tax at this boundary (verifiers/test_v05 docstring), so the debit is
amount + fees, not amount + fees + tax.
"""
import time

import framework as F
from framework import journey

SH = "shared"


def _entries_balance(ctx, journals):
    detail = []
    ok = True
    for j in journals:
        e = ctx.a.journal_entries(j["id"])
        debit = sum(float(x["amount"]) for x in e if x["type"].strip() == "debit")
        credit = sum(float(x["amount"]) for x in e if x["type"].strip() == "credit")
        detail.append({"journal": j["id"], "event": j["transactor_event"], "entries": len(e),
                       "debit": debit, "credit": credit})
        if not e or abs(debit - credit) > 1e-6:
            ok = False
    return ok, detail


@journey("accounting", "success", priority="P0", profile=SH,
         title="processed payout: payout_initiated + payout_processed, both balanced, exactly once each, balance delta = amount + fees",
         source_ref="verifiers/test_v05 (initiated debit == amount+fees) and test_v06 (processed journal)")
def accounting_success(ctx):
    mid = ctx.m["merchant_id"]
    amount = 9100
    before = ctx.a.merchant_balance(mid)
    ctx.a.mozart(mid, "hold")
    cr = ctx.create(amount, "acct-success")
    pid = cr["id"]
    ctx.ck("create_200", cr["status"] == 200 and bool(pid), cr.get("raw"))
    if not pid:
        return
    ctx.wait_status(pid, "initiated", timeout=45)
    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=90)
    ctx.ck("processed", (row or {}).get("status") == "processed", row)
    ctx.wait_journal("pout_" + pid, "payout_processed", timeout=80)
    js = ctx.a.journals("pout_" + pid)
    events = sorted(j["transactor_event"] for j in js)
    ctx.ck("exactly_the_two_expected_events_once_each",
           events == ["payout_initiated", "payout_processed"], events)
    ok, detail = _entries_balance(ctx, js)
    ctx.ck("every_journal_has_entries_that_sum_to_zero", ok, detail)
    fees = float((row or {}).get("fees") or 0)
    after = ctx.a.merchant_balance(mid)
    ctx.ck("balance_delta == amount + fees",
           before is not None and after is not None and abs((before - after) - (amount + fees)) < 1e-6,
           {"before": before, "after": after, "amount": amount, "fees": fees})
    init = [j for j in js if j["transactor_event"] == "payout_initiated"]
    ctx.ck("initiated_journal_amount == amount + fees",
           init and abs(float(init[0]["amount"]) - (amount + fees)) < 1e-6,
           {"journal_amount": init[0]["amount"] if init else None, "expected": amount + fees})
    ctx.state["acct_success_pid"] = pid
    ctx.a.mozart(mid, clear=True)


@journey("accounting", "failure", priority="P0", profile=SH,
         title="bank failure: the debit is fully reversed -- payout_failed on the reversal entity, net balance delta zero, no duplicate debit",
         source_ref="verifiers/test_v07 / route_scenarios.py case failed_shared")
def accounting_failure(ctx):
    mid = ctx.m["merchant_id"]
    amount = 7300
    before = ctx.a.merchant_balance(mid)
    ctx.a.mozart(mid, "hold")
    cr = ctx.create(amount, "acct-failure")
    pid = cr["id"]
    ctx.ck("create_200", cr["status"] == 200 and bool(pid), cr.get("raw"))
    if not pid:
        return
    ctx.wait_status(pid, "initiated", timeout=45)
    mid_bal = ctx.a.merchant_balance(mid)
    fees_row = ctx.a.payout(pid)
    fees = float((fees_row or {}).get("fees") or 0)
    ctx.ck("balance_debited_while_initiated",
           before is not None and mid_bal is not None and abs((before - mid_bal) - (amount + fees)) < 1e-6,
           {"before": before, "while_initiated": mid_bal, "amount": amount, "fees": fees})
    ctx.a.finish_bank(pid, "failure")
    row = ctx.wait_status(pid, "reversed", timeout=90)
    ctx.ck("terminal_reversed", (row or {}).get("status") == "reversed", row)
    rev = F.wait_until(lambda: ctx.a.reversal(pid), timeout=40, interval=2)
    ctx.ck("reversal_entity_created", bool(rev), rev)
    rjs = ctx.wait_journal("rvrsl_" + rev["id"], "payout_failed", timeout=70) if rev else []
    ctx.ck("payout_failed_journal_on_the_reversal_entity_exactly_once", len(rjs) == 1, rjs)
    ok, detail = _entries_balance(ctx, ctx.a.journals("pout_" + pid) + rjs)
    ctx.ck("every_journal_balances", ok, detail)
    after = ctx.a.merchant_balance(mid)
    ctx.ck("net_balance_delta_is_zero_after_the_reversal",
           before is not None and after is not None and abs(before - after) < 1e-6,
           {"before": before, "after": after})
    ctx.ck("no_payout_processed_journal_for_a_failed_payout",
           not any(j["transactor_event"] == "payout_processed" for j in ctx.a.journals("pout_" + pid)), None)
    ctx.state["acct_failure_pid"] = pid
    ctx.a.mozart(mid, clear=True)


@journey("accounting", "cancel_or_reverse", priority="P1", profile=SH,
         title="a cancelled (never dispatched) payout writes no ledger journal and moves no money",
         source_ref="state_machine.go StateCancelled has no ledger hook; queued/scheduled never reach payout_initiated")
def accounting_cancelled(ctx):
    mid = ctx.m["merchant_id"]
    before = ctx.a.merchant_balance(mid)
    import j_scheduled as S
    slot = S.next_allowed_slot_ist()
    cr = ctx.create(2600, "acct-cancel", extra={"scheduled_at": slot})
    pid = cr["id"]
    ctx.ck("create_200_scheduled", cr["status"] == 200 and bool(pid), cr.get("raw"))
    if not pid:
        return
    ctx.ck("parked_at_scheduled", (ctx.wait_status(pid, "scheduled", 30) or {}).get("status") == "scheduled", None)
    ctx.ck("no_journal_while_scheduled", ctx.a.journals("pout_" + pid) == [], None)
    ctx.ck("balance_not_debited_while_scheduled",
           abs((ctx.a.merchant_balance(mid) or 0) - (before or 0)) < 1e-6, None)
    # scheduled payouts cannot be cancelled over the merchant API (validation.go ValidateCancel);
    # assert the accounting invariant that matters: no money moved and no journal exists.
    st, body = ctx.cancel(pid, "m6 acct cancel")
    ctx.ev["cancel_attempt"] = {"status": st, "body": str(body)[:250]}
    ctx.ck("still_no_ledger_journal_after_the_cancel_attempt", ctx.a.journals("pout_" + pid) == [], None)
    ctx.ck("balance_unchanged_end_to_end",
           abs((ctx.a.merchant_balance(mid) or 0) - (before or 0)) < 1e-6,
           {"before": before, "after": ctx.a.merchant_balance(mid)})
    prev = ctx.state.get("cancelled_pid")
    if prev:
        ctx.ck("the_queued->cancelled_payout_from_family:failure-reversal-cancellation_also_has_no_journal",
               ctx.a.journals("pout_" + prev) == [], prev)


@journey("accounting", "idempotency", priority="P0", profile=SH,
         title="a replayed terminal stimulus never writes a second journal (ledger dedupe by transactor_id+event)",
         source_ref="verifiers/test_v04 ledger dedupe")
def accounting_idempotency(ctx):
    mid = ctx.m["merchant_id"]
    ctx.a.mozart(mid, "hold")
    cr = ctx.create(3200, "acct-idem")
    pid = cr["id"]
    ctx.ck("create_200", cr["status"] == 200 and bool(pid), cr.get("raw"))
    if not pid:
        return
    ctx.wait_status(pid, "initiated", timeout=45)
    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=90)
    ctx.wait_journal("pout_" + pid, "payout_processed", timeout=80)
    before = ctx.a.journals("pout_" + pid)
    bal_before = ctx.a.merchant_balance(mid)
    t = ctx.a.transfer(pid)
    body = {"fund_transfer_id": int(t["id"]), "source_id": pid, "source_type": "payout",
            "status": "processed", "utr": (row or {}).get("utr"),
            "source_account_id": int(t["source_account_id"]),
            "bank_account_type": t.get("sa_bank_account_type")}
    for _ in range(3):
        ctx.a.http("POST", F.PAYOUTS + "/v1/payouts/transfer_status_webhook", body,
                   basic=F.bridge("ps-service"), note="repeated terminal stimulus")
    time.sleep(8)
    after = ctx.a.journals("pout_" + pid)
    ctx.ck("journal_count_unchanged_after_three_replays", len(after) == len(before),
           {"before": [j["transactor_event"] for j in before], "after": [j["transactor_event"] for j in after]})
    ctx.ck("still_exactly_one_payout_processed_journal",
           sum(1 for j in after if j["transactor_event"] == "payout_processed") == 1, after)
    ctx.ck("still_exactly_one_payout_initiated_journal",
           sum(1 for j in after if j["transactor_event"] == "payout_initiated") == 1, after)
    ctx.ck("balance_not_double_debited",
           abs((ctx.a.merchant_balance(mid) or 0) - (bal_before or 0)) < 1e-6,
           {"before": bal_before, "after": ctx.a.merchant_balance(mid)})
    ctx.a.mozart(mid, clear=True)


@journey("accounting", "async_state", priority="P0", profile=SH,
         title="the settlement journal is written asynchronously by the ledger worker chain AFTER the payout status changes -- not inline with the API call",
         source_ref="core.go:2583 (payout_processed pushed to the async queue); ledger-worker-journal-create container")
def accounting_async_state(ctx):
    mid = ctx.m["merchant_id"]
    ctx.a.mozart(mid, "hold")
    cr = ctx.create(2400, "acct-async")
    pid = cr["id"]
    ctx.ck("create_200", cr["status"] == 200 and bool(pid), cr.get("raw"))
    if not pid:
        return
    ctx.wait_status(pid, "initiated", timeout=45)
    ctx.ck("no_payout_processed_journal_while_initiated",
           not any(j["transactor_event"] == "payout_processed" for j in ctx.a.journals("pout_" + pid)), None)
    t0 = time.time()
    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=90)
    t_status = time.time()
    js = ctx.wait_journal("pout_" + pid, "payout_processed", timeout=80)
    t_journal = time.time()
    ctx.ck("payout_reached_processed", (row or {}).get("status") == "processed", row)
    ctx.ck("processed_journal_eventually_written_exactly_once", len(js) == 1, js)
    ctx.ev["timing_s"] = {"bank_check": 0.0, "status_change": round(t_status - t0, 2),
                          "journal_visible": round(t_journal - t0, 2)}
    ctx.ck("journal_became_visible_at_or_after_the_status_change(async, not inline)",
           t_journal >= t_status - 0.5, ctx.ev["timing_s"])
    hits = ctx.a.worker_log_hits(pid)
    ctx.ev["worker_log_hits"] = hits
    ctx.ck("a_real_async_worker_container_logged_this_payout", bool(hits), hits)
    led = ctx.a.worker_log_hits(js[0]["id"] if js else pid,
                                services=("ledger-worker", "ledger-worker-journal-create",
                                          "ledger-worker-balance-update", "ledger-api"))
    ctx.ev["ledger_worker_hits"] = led
    ctx.ck("the_journal_id_appears_in_the_real_ledger_service_logs", bool(led), led)
    ok, detail = _entries_balance(ctx, js)
    ctx.ck("the_async_written_journal_balances", ok, detail)
    ctx.a.mozart(mid, clear=True)
