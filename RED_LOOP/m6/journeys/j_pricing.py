#!/usr/bin/env python3
"""family:pricing-free-payouts -- fee computation and the free-payout allowance.

Fee path: payouts processor/payout_pricing.go -> monolith `POST /v1/payouts_service/
fetch_pricing_info` (substitutes/monolith-stub `_fetch_pricing_info`, which resolves the
merchant's seeded plan from seeds/generated/pricing.json) -> payouts.fees / payouts.tax ->
the ledger `payout_initiated` debit (amount + fees; fees INCLUDE tax at this boundary,
verifiers/test_v05) -> the merchant webhook payload.

Free payouts: processor/base.go:1368 HandleFreePayout -> freePayout/core.go:967
IncrementFreePayoutConsumedIfApplicable, gated by
  settings.GetFreePayoutSupportedModes(balance_id)   (IMPS/NEFT/RTGS/UPI/IFT by default)
  settings.GetFreePayoutCount(balance_id, account_type, channel)
GetFreePayoutCount first reads the per-balance `settings` row
(entity_id=<balance_id>, module='free_payout', config_key='free_payouts_count',
appConstants.FreePayoutsCount),
and only then falls back to the slab defaults -- and a merchant onboarded after
FreePayoutSlab3RolloutDate (2023-10-16) lands in Slab3, whose default is 0. A freshly
provisioned merchant is therefore born with ZERO free payouts; the free_payout variant seeds
that per-balance settings row (merchant configuration, exactly the class of fixture the
audited provisioners write) so the real allowance logic can run.

Fail-closed: a pricing 500 must stop the payout before any dispatch or debit
(verifiers/test_v23).
"""
import calendar
import datetime as _dt
import time
import uuid

import framework as F
from framework import journey

PR = "pricing"
EXPECTED = {"IMPS": (200, 36), "NEFT": (100, 18), "RTGS": (500, 90), "UPI": (50, 9)}


def _counters(ctx):
    rc, out, _ = ctx.a.payouts_sql(
        "SELECT id,free_payouts_consumed,account_type FROM counters WHERE balance_id='%s'"
        % ctx.m["balance_id"], note="payouts.counters free-payout counter")
    rows = [l.split("\t") for l in out.strip().splitlines() if l.strip()]
    return [dict(zip(("id", "free_payouts_consumed", "account_type"), r)) for r in rows]


def _current_month_timestamp_ist():
    """payouts utils.CurrentMonthTimestamp(): 00:00 IST on the 1st of the current month."""
    ist = 5 * 3600 + 1800
    d = _dt.datetime.utcfromtimestamp(int(time.time()) + ist)
    return calendar.timegm(_dt.datetime(d.year, d.month, 1).timetuple()) - ist


def _align_counter_reset_window(ctx):
    """TWIN FIXTURE REPAIR (scoped to this journey's own fresh merchant).

    payouts counters/core.go DecrementCounterIfApplicable only gives a free payout back when
    `counters.free_payouts_consumed_last_reset_at == utils.CurrentMonthTimestamp()` (00:00 IST
    on the 1st of the month). RED_LOOP/red_loop/provisioner.py seeds that column with
    int(time.time()), so no provisioner-minted merchant can ever have a free payout reverted.
    The journey aligns the column with the shape the real monthly reset writes so the SOURCE
    revert logic can actually run; the defect itself is reported, not hidden."""
    ts = _current_month_timestamp_ist()
    ctx.a.payouts_sql(
        "UPDATE counters SET free_payouts_consumed_last_reset_at=%d WHERE balance_id='%s'"
        % (ts, ctx.m["balance_id"]),
        note="COUNTER RESET-WINDOW FIXTURE REPAIR: free_payouts_consumed_last_reset_at set to "
             "utils.CurrentMonthTimestamp() (%d); provisioner.py seeds time.time() instead" % ts)
    ctx.ev["counter_reset_window_repair"] = {
        "balance_id": ctx.m["balance_id"], "current_month_timestamp_ist": ts,
        "defect": "RED_LOOP/red_loop/provisioner.py provision_funded_merchant seeds "
                  "counters.free_payouts_consumed_last_reset_at = int(time.time()); payouts "
                  "internal/app/counters/core.go DecrementCounterIfApplicable requires it to equal "
                  "utils.CurrentMonthTimestamp(), so free-payout reverts are impossible for every "
                  "merchant that recipe mints"}
    return ts


def _clear_free_payout_allowance(ctx):
    """Remove any per-balance free-payout allowance so ordinary priced payouts are exercised.

    Without this a fee journey that runs after a free-payout journey on the same merchant would
    be silently priced free (GetFreePayoutCount reads the per-balance settings row before falling
    back to the Slab3 default of 0), which is a fixture interaction, not a pricing failure."""
    ctx.a.payouts_sql("DELETE FROM settings WHERE entity_id='%s' AND module='free_payout'"
                      % ctx.m["balance_id"],
                      note="FIXTURE: no per-balance free-payout allowance for this fee journey")
    st, attrs = ctx.free_payout_attrs()
    ctx.ev["free_payout_allowance_at_start"] = {"status": st, "body": attrs}
    ctx.ck("this_merchant_has_no_free_payout_allowance_for_this_journey",
           st == 200 and int((attrs or {}).get("free_payouts_count") or 0) == 0, attrs)
    return attrs


def _seed_free_payout_allowance(ctx, count=3):
    """Per-balance free-payout allowance (payouts `settings` table). Merchant configuration,
    not payout state; scoped to this fresh merchant's balance only."""
    sid = ("M6FP" + ctx.m["balance_id"][-10:])[:14]
    ts = int(time.time())
    ctx.a.payouts_sql(
        "DELETE FROM settings WHERE entity_id='%s' AND module='free_payout'" % ctx.m["balance_id"],
        note="reset the per-balance free-payout configuration before reseeding")
    ctx.a.payouts_sql(
        "INSERT INTO settings (id,entity_type,entity_id,module,config_key,config_value,created_at,updated_at) "
        "VALUES ('%s','balance','%s','free_payout','free_payouts_count',"
        "'{\"value_type\":\"Int\",\"value\":\"%d\"}',%d,%d) "
        "ON DUPLICATE KEY UPDATE config_value=VALUES(config_value),updated_at=VALUES(updated_at)"
        % (sid, ctx.m["balance_id"], count, ts, ts),
        note="MERCHANT CONFIGURATION FIXTURE: per-balance free-payout allowance "
             "(settings entity_id=balance_id, module=free_payout)")
    rc, out, _ = ctx.a.payouts_sql(
        "SELECT config_key,config_value FROM settings WHERE entity_id='%s'" % ctx.m["balance_id"],
        note="allowance readback")
    return out.strip()


@journey("pricing-free-payouts", "success", priority="P0", profile=PR,
         title="the seeded pricing plan's fee and tax are applied to the payout, the ledger debit and the webhook payload",
         source_ref="processor/payout_pricing.go -> monolith fetch_pricing_info; seeds/generated/pricing.json; test_v05")
def pricing_success(ctx):
    _clear_free_payout_allowance(ctx)
    mid = ctx.m["merchant_id"]
    amount = 12000
    before = ctx.a.merchant_balance(mid)
    ctx.a.mozart(mid, "hold")
    cr = ctx.create(amount, "pricing-success", mode="IMPS")
    pid = cr["id"]
    ctx.ck("create_200", cr["status"] == 200 and bool(pid), cr.get("raw"))
    if not pid:
        return
    fees, tax = EXPECTED["IMPS"]
    ctx.ck("create_response_carries_the_planned_fee_and_tax",
           cr["fees"] == fees and cr["tax"] == tax,
           {"got": (cr["fees"], cr["tax"]), "plan": (fees, tax)})
    row = ctx.wait_status(pid, "initiated", timeout=45)
    ctx.ck("db_row_carries_the_same_fee_and_tax",
           int((row or {}).get("fees") or -1) == fees and int((row or {}).get("tax") or -1) == tax, row)
    calls = ctx.a.monolith_log(pid)
    priced = [c for c in calls if "pricing" in str(c).lower()]
    ctx.ev["monolith_pricing_calls"] = priced[-3:]
    js = ctx.wait_journal("pout_" + pid, "payout_initiated", timeout=45)
    ctx.ck("ledger_initiated_journal_amount == amount + fees",
           js and abs(float(js[0]["amount"]) - (amount + fees)) < 1e-6,
           {"journal_amount": js[0]["amount"] if js else None, "expected": amount + fees})
    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=90)
    ctx.ck("processed", (row or {}).get("status") == "processed", row)
    after = ctx.a.merchant_balance(mid)
    ctx.ck("balance_delta == amount + fees (fees include tax)",
           before is not None and after is not None and abs((before - after) - (amount + fees)) < 1e-6,
           {"before": before, "after": after, "expected_delta": amount + fees})
    dl = ctx.wait_delivery(pid, {"payout.processed"}, timeout=60)
    ctx.ck("webhook_payload_carries_the_same_fee_and_tax",
           dl and str(dl[0]["fees"]) == str(fees) and str(dl[0]["tax"]) == str(tax), dl)
    ctx.a.mozart(mid, clear=True)


@journey("pricing-free-payouts", "accounting", priority="P1", profile=PR,
         title="the fee lands in the ledger entries, not just on the payout row: the initiated debit is amount+fees and the journal balances",
         source_ref="verifiers/test_v05 clause: initiated debit == amount + fees")
def pricing_accounting(ctx):
    _clear_free_payout_allowance(ctx)
    mid = ctx.m["merchant_id"]
    amount = 11000
    for mode in ("IMPS", "NEFT"):
        fees, tax = EXPECTED[mode]
        ctx.a.mozart(mid, "hold")
        cr = ctx.create(amount, "pricing-acct-" + mode, mode=mode)
        pid = cr["id"]
        ctx.ck("%s_create_200" % mode, cr["status"] == 200 and bool(pid), cr.get("raw"))
        if not pid:
            continue
        ctx.ck("%s_fee_matches_the_seeded_tariff" % mode,
               cr["fees"] == fees and cr["tax"] == tax, {"got": (cr["fees"], cr["tax"]), "plan": (fees, tax)})
        js = ctx.wait_journal("pout_" + pid, "payout_initiated", timeout=45)
        if js:
            entries = ctx.a.journal_entries(js[0]["id"])
            debit = sum(float(e["amount"]) for e in entries if e["type"].strip() == "debit")
            credit = sum(float(e["amount"]) for e in entries if e["type"].strip() == "credit")
            ctx.ck("%s_initiated_debit == amount + fees" % mode, abs(debit - (amount + fees)) < 1e-6,
                   {"debit": debit, "expected": amount + fees, "entries": entries})
            ctx.ck("%s_initiated_journal_balances" % mode, abs(debit - credit) < 1e-6,
                   {"debit": debit, "credit": credit})
        else:
            ctx.ck("%s_initiated_journal_written" % mode, False, None)
        ctx.a.finish_bank(pid, "success")
        ctx.wait_status(pid, "processed", timeout=90)
    ctx.a.mozart(mid, clear=True)


@journey("pricing-free-payouts", "free_payout", priority="P0", profile=PR,
         title="with a per-balance free-payout allowance the payout is priced free (fee_type=free_payout, fees=0) and the payouts counter is consumed",
         source_ref="processor/base.go:1368 HandleFreePayout; freePayout/core.go:967 "
                    "IncrementFreePayoutConsumedIfApplicable; settings/core.go GetFreePayoutCount")
def pricing_free_payout(ctx):
    mid = ctx.m["merchant_id"]
    reset_ts = _align_counter_reset_window(ctx)
    ctx.note("counters.free_payouts_consumed_last_reset_at aligned to utils.CurrentMonthTimestamp() "
             "(%d): see counter_reset_window_repair in this evidence bundle -- provisioner defect "
             "reported, not hidden" % reset_ts)
    seeded = _seed_free_payout_allowance(ctx, 5)
    ctx.ck("per_balance_free_payout_allowance_seeded", "free_payouts_count" in seeded, seeded)
    st, attrs = ctx.free_payout_attrs()
    ctx.ev["free_payout_attributes_api"] = {"status": st, "body": attrs}
    ctx.ck("payouts_reports_the_seeded_free_payout_allowance",
           st == 200 and int((attrs or {}).get("free_payouts_count") or 0) == 5,
           {"status": st, "body": attrs})
    before = _counters(ctx)
    ctx.ev["counters_before"] = before
    ctx.a.mozart(mid, "hold")
    cr = ctx.create(4500, "free-payout", mode="IMPS")
    pid = cr["id"]
    ctx.ck("create_200", cr["status"] == 200 and bool(pid), cr.get("raw"))
    if not pid:
        return
    ctx.ck("no_fee_charged_on_the_free_payout", cr["fees"] == 0 and cr["tax"] == 0,
           {"fees": cr["fees"], "tax": cr["tax"]})
    rc, out, _ = ctx.a.payouts_sql("SELECT fee_type,fees,tax FROM payouts WHERE id='%s'" % pid,
                                   note="fee_type on the payout row")
    ctx.ck("payout_row_fee_type_is_free_payout", out.strip().split("\t")[0] == "free_payout", out.strip())
    after = F.wait_until(lambda: (lambda c: c if c and c != before else None)(_counters(ctx)),
                         timeout=30, interval=3) or _counters(ctx)
    ctx.ev["counters_after"] = after
    ctx.ck("payouts_free_payout_counter_incremented",
           after and before
           and int(after[0]["free_payouts_consumed"]) == int(before[0]["free_payouts_consumed"]) + 1,
           {"before": before, "after": after})
    st2, attrs2 = ctx.free_payout_attrs()
    ctx.ck("free_payout_attributes_api_reflects_the_consumption",
           st2 == 200 and int((attrs2 or {}).get("free_payouts_consumed") or -1)
           == int(after[0]["free_payouts_consumed"]), attrs2)
    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=90)
    ctx.ck("free_payout_completes_normally", (row or {}).get("status") == "processed", row)
    js = ctx.wait_journal("pout_" + pid, "payout_initiated", timeout=45)
    ctx.ck("ledger_initiated_debit_is_the_bare_amount(no fee)",
           js and abs(float(js[0]["amount"]) - 4500) < 1e-6,
           {"journal_amount": js[0]["amount"] if js else None, "expected": 4500})
    ctx.state["free_payout_merchant"] = mid
    ctx.a.mozart(mid, clear=True)
    ctx.a.payouts_sql("DELETE FROM settings WHERE entity_id='%s' AND module='free_payout'"
                      % ctx.m["balance_id"], note="FIXTURE cleanup: drop the free-payout allowance")


def _free_payout_and_reverse(ctx, amount, note):
    """One free payout driven to `reversed`; returns (consumed_before, consumed_after, pid)."""
    mid = ctx.m["merchant_id"]
    before = int(_counters(ctx)[0]["free_payouts_consumed"])
    ctx.a.mozart(mid, "hold")
    cr = ctx.create(amount, note, mode="IMPS")
    pid = cr["id"]
    if not pid:
        return before, before, None, None
    rc, out, _ = ctx.a.payouts_sql("SELECT fee_type FROM payouts WHERE id='%s'" % pid,
                                   note="fee_type for " + note)
    free = out.strip() == "free_payout"
    consumed = F.wait_until(
        lambda: (lambda c: c if int(c[0]["free_payouts_consumed"]) == before + 1 else None)(_counters(ctx)),
        timeout=30, interval=3)
    mid_val = int((consumed or _counters(ctx))[0]["free_payouts_consumed"])
    ctx.a.finish_bank(pid, "failure")
    ctx.wait_status(pid, "reversed", timeout=90)
    back = F.wait_until(
        lambda: (lambda c: c if int(c[0]["free_payouts_consumed"]) == mid_val - 1 else None)(_counters(ctx)),
        timeout=45, interval=3)
    final = int((back or _counters(ctx))[0]["free_payouts_consumed"])
    rc, tx, _ = ctx.a.payouts_sql(
        "SELECT transaction_type,payout_status FROM counter_transaction WHERE payout_id='%s' ORDER BY created_at"
        % pid, note="counter_transaction ledger for " + note)
    rc, ft, _ = ctx.a.payouts_sql("SELECT fee_type FROM payouts WHERE id='%s'" % pid,
                                  note="fee_type after the reversal for " + note)
    return mid_val, final, pid, {"free_at_create": free, "counter_transactions": tx.strip(),
                                 "fee_type_after_reversal": ft.strip()}


@journey("pricing-free-payouts", "cancel_or_reverse", priority="P0", profile=PR,
         title="a reversed free payout must give the free payout back -- differential probe: the decrement works when it lands on a non-zero counter and is LOST when it would land on zero",
         source_ref="freePayout/core.go:1009 DecreaseFreePayoutsConsumedIfApplicable -> counters/core.go "
                    "decrementFreePayoutsConsumed -> goutils/spine@v0.12.4 repository.go:212-236 "
                    "updateSelective -> gorm Updates(struct), which skips zero-valued fields")
def pricing_free_payout_revert(ctx):
    mid = ctx.m["merchant_id"]
    _align_counter_reset_window(ctx)
    _seed_free_payout_allowance(ctx, 5)
    # normalise the counter to 0 so leg A really is the 1 -> 0 case
    ctx.a.payouts_sql("UPDATE counters SET free_payouts_consumed=1 WHERE balance_id='%s'"
                      % ctx.m["balance_id"],
                      note="FIXTURE: park the counter at 1 so the next decrement targets 0 "
                           "(the case under test); no payout state is touched")
    ctx.a.payouts_sql("UPDATE counters SET free_payouts_consumed=0 WHERE balance_id='%s'"
                      % ctx.m["balance_id"], note="FIXTURE: reset the counter to 0 before leg A")
    a_mid, a_final, a_pid, a_ev = _free_payout_and_reverse(ctx, 4400, "fp-revert-A")
    ctx.ev["leg_A_zero_target"] = {"payout_id": a_pid, "consumed_after_increment": a_mid,
                                   "consumed_after_reversal": a_final, "expected": a_mid - 1, **(a_ev or {})}
    ctx.ck("leg_A: the free payout was actually priced free and consumed the counter",
           a_mid == 1 and (a_ev or {}).get("free_at_create"), ctx.ev["leg_A_zero_target"])
    ctx.ck("leg_A: a decrement counter_transaction was recorded",
           "decrement" in ((a_ev or {}).get("counter_transactions") or ""), a_ev)

    # leg B: make the next decrement land on a NON-zero value
    ctx.a.payouts_sql("UPDATE counters SET free_payouts_consumed=1 WHERE balance_id='%s'"
                      % ctx.m["balance_id"],
                      note="FIXTURE: park the counter at 1 so leg B's decrement targets 1 (non-zero)")
    b_mid, b_final, b_pid, b_ev = _free_payout_and_reverse(ctx, 4300, "fp-revert-B")
    ctx.ev["leg_B_nonzero_target"] = {"payout_id": b_pid, "consumed_after_increment": b_mid,
                                      "consumed_after_reversal": b_final, "expected": b_mid - 1, **(b_ev or {})}
    ctx.ck("leg_B: the free payout was actually priced free and consumed the counter",
           b_mid == 2 and (b_ev or {}).get("free_at_create"), ctx.ev["leg_B_nonzero_target"])
    ctx.ck("leg_B: decrementing to a NON-zero value returns the free payout",
           b_final == b_mid - 1, ctx.ev["leg_B_nonzero_target"])

    zero_ok = a_final == a_mid - 1
    ctx.ev["differential"] = {"decrement_to_zero_worked": zero_ok,
                              "decrement_to_nonzero_worked": b_final == b_mid - 1}
    ctx.a.payouts_sql("DELETE FROM settings WHERE entity_id='%s' AND module='free_payout'"
                      % ctx.m["balance_id"], note="FIXTURE cleanup: drop the free-payout allowance")
    if zero_ok:
        ctx.ck("leg_A: decrementing to ZERO also returns the free payout", True, ctx.ev["leg_A_zero_target"])
        ctx.a.mozart(mid, clear=True)
        return
    ctx.a.mozart(mid, clear=True)
    ctx.expected_failure(
        "EF-009 (TWIN_SPEC/expected-failures.yaml: 'Free-payout counter revert is lost "
        "whenever the decrement would empty the counter (1 -> 0)', case "
        "pricing-free-payouts/cancel_or_reverse). Mechanism: "
        "freePayout/core.go:1009 DecreaseFreePayoutsConsumedIfApplicable -> "
        "internal/app/counters/core.go decrementFreePayoutsConsumed writes the counter with "
        "spine Repo.Update (goutils/spine@v0.12.4 repository.go:212-236 updateSelective -> "
        "gorm `Updates(receiver)` on a struct), and gorm's struct form skips zero-valued fields. "
        "A decrement whose result is 0 therefore never reaches the database, while the decrement "
        "counter_transaction row IS written and the payout's fee_type IS cleared -- the merchant "
        "silently loses the free payout and counters disagrees with counter_transaction. "
        "Reproduced differentially in this journey: 1 -> 0 does not persist, 2 -> 1 does.",
        {"leg_A_zero_target": ctx.ev["leg_A_zero_target"],
         "leg_B_nonzero_target": ctx.ev["leg_B_nonzero_target"],
         "impact": "one free payout permanently consumed per reversal that would empty the counter; "
                   "counters.free_payouts_consumed and counter_transaction diverge",
         "mechanism_confidence": "observations are direct; the gorm zero-value explanation is the "
                                 "most probable mechanism, inferred from spine repository.go:235 "
                                 "q.Updates(receiver) and confirmed by the differential result"})


@journey("pricing-free-payouts", "failure", priority="P0", profile=PR,
         title="pricing fails closed: a 500 from the monolith pricing route rejects the create with no dispatch, no journal and no debit",
         source_ref="verifiers/test_v23 (retained row is create_request_submitted at this boundary)")
def pricing_failure(ctx):
    _clear_free_payout_allowance(ctx)
    mid = ctx.m["merchant_id"]
    before = ctx.a.merchant_balance(mid)
    rc, before_transfers, _ = ctx.a.fts_sql("SELECT count(*) FROM transfers WHERE merchant_id='%s'" % mid,
                                            note="transfers before the fault")
    st, _b = ctx.a.jhttp("POST", F.MONOLITH + "/_arena/faults",
                         {"scope": mid, "status": 500, "path": "/v1/payouts_service/fetch_pricing_info"},
                         basic=F.D._mono_basic(), note="inject a merchant-scoped pricing 500")
    ctx.ck("fault_registered", st == 200, st)
    try:
        cr = ctx.create(9900, "pricing-failclosed")
        ctx.ck("create_rejected_5xx_or_4xx",
               isinstance(cr["status"], int) and cr["status"] >= 400,
               {"status": cr["status"], "body": cr.get("raw")})
        sf, faults = ctx.a.jhttp("GET", F.MONOLITH + "/_arena/faults", basic=F.D._mono_basic(),
                                 note="fault hit log")
        hits = [e for e in (faults or {}).get("events", []) if e.get("scope") == mid]
        ctx.ck("the_real_pricing_call_hit_the_fault", bool(hits), hits[-2:])
        rc, rows, _ = ctx.a.payouts_sql(
            "SELECT id,status,fts_transfer_id,transaction_id FROM payouts WHERE merchant_id='%s' AND amount=9900" % mid,
            note="retained row(s) for the fail-closed create")
        parsed = [r.split("\t") for r in rows.strip().splitlines() if r.strip()]
        ctx.ck("retained_row_never_dispatched",
               bool(parsed) and all(r[1] in ("create_request_submitted", "failed") for r in parsed), parsed)
        ctx.ck("no_ledger_transaction_on_the_retained_row",
               all(r[3] in ("NULL", "") for r in parsed), parsed)
        ctx.ck("no_ledger_journal_for_the_retained_row",
               all(ctx.a.journals("pout_" + r[0]) == [] for r in parsed), parsed)
        rc, after_transfers, _ = ctx.a.fts_sql("SELECT count(*) FROM transfers WHERE merchant_id='%s'" % mid,
                                               note="transfers after the fault")
        ctx.ck("no_new_fts_transfer", after_transfers.strip() == before_transfers.strip(),
               {"before": before_transfers.strip(), "after": after_transfers.strip()})
        ctx.ck("merchant_balance_untouched",
               abs((ctx.a.merchant_balance(mid) or 0) - (before or 0)) < 1e-6,
               {"before": before, "after": ctx.a.merchant_balance(mid)})
    finally:
        ctx.a.jhttp("POST", F.MONOLITH + "/_arena/faults", {"scope": mid, "clear": True},
                    basic=F.D._mono_basic(), note="clear the injected fault")
    # the merchant must be healthy again once the dependency recovers
    ctx.a.mozart(mid, "hold")
    ok = ctx.create(1500, "pricing-recovered")
    ctx.ck("pricing_recovers_after_the_fault_is_cleared", ok["status"] == 200 and ok["fees"] == 200,
           {"status": ok["status"], "fees": ok["fees"]})
    if ok["id"]:
        ctx.a.finish_bank(ok["id"], "success")
        ctx.wait_status(ok["id"], "processed", timeout=90)
    ctx.a.mozart(mid, clear=True)


@journey("pricing-free-payouts", "idempotency", priority="P0", profile=PR,
         title="pricing is computed once: an idempotent replay returns the same fee/tax and prices nothing twice",
         source_ref="middleware.IdempotencyKey + processor/payout_pricing.go")
def pricing_idempotency(ctx):
    _clear_free_payout_allowance(ctx)
    mid = ctx.m["merchant_id"]
    key = "m6p-" + uuid.uuid4().hex[:12]
    ctx.a.mozart(mid, "hold")
    a = ctx.create(7700, "pricing-idem", idem=key)
    ctx.ck("first_create_200", a["status"] == 200 and bool(a["id"]), a.get("raw"))
    if not a["id"]:
        return
    b = ctx.create(7700, "pricing-idem", idem=key)
    ctx.ck("replay_returns_same_payout", b["id"] == a["id"], {"a": a["id"], "b": b["id"]})
    ctx.ck("replay_returns_the_same_fee_and_tax",
           (b["fees"], b["tax"]) == (a["fees"], a["tax"]),
           {"first": (a["fees"], a["tax"]), "replay": (b["fees"], b["tax"])})
    row = ctx.a.payout(a["id"])
    ctx.ck("db_fee_unchanged_by_the_replay",
           int(row.get("fees") or -1) == a["fees"] and int(row.get("tax") or -1) == a["tax"], row)
    ctx.a.finish_bank(a["id"], "success")
    ctx.wait_status(a["id"], "processed", timeout=90)
    js = ctx.wait_journal("pout_" + a["id"], "payout_initiated", timeout=45)
    ctx.ck("exactly_one_initiated_journal_carrying_the_fee_once",
           len(js) == 1 and abs(float(js[0]["amount"]) - (7700 + a["fees"])) < 1e-6, js)
    ctx.a.mozart(mid, clear=True)


@journey("pricing-free-payouts", "async_state", priority="P0", profile=PR,
         title="the money side of pricing is completed by the real async workers: the fee-bearing ledger journals and the free-payout counter revert both land through worker containers, never inline",
         source_ref="core.go:2583 pushes payout_processed to the async queue; ledger-worker / "
                    "ledger-worker-journal-create write the journals; "
                    "asyncFailureHandlingHelper.go + payouts-worker-payout-update-failure-handling "
                    "drive the reversal that calls freePayout/core.go DecreaseFreePayoutsConsumedIfApplicable")
def pricing_async_state(ctx):
    mid = ctx.m["merchant_id"]

    # -- leg 1: a PRICED payout. fees are computed on create, but the fee-bearing ledger
    #    journals are written by the ledger workers after the payouts workers complete it.
    _clear_free_payout_allowance(ctx)
    ctx.a.mozart(mid, "hold")
    cr = ctx.create(6100, "pricing-async", mode="IMPS")
    pid = cr["id"]
    ctx.ck("create_200", cr["status"] == 200 and bool(pid), cr.get("raw"))
    if not pid:
        return
    fees, tax = EXPECTED["IMPS"]
    ctx.ck("the_create_response_carries_the_seeded_plan's_fee_and_tax",
           int((cr["json"] or {}).get("fees") or 0) == fees
           and int((cr["json"] or {}).get("tax") or 0) == tax,
           {"fees": (cr["json"] or {}).get("fees"), "tax": (cr["json"] or {}).get("tax"),
            "expected": {"fees": fees, "tax": tax}})
    ctx.ck("the_create_response_is_non_terminal",
           cr.get("response_status") not in ("processed", "reversed", "failed"),
           cr.get("response_status"))
    init = ctx.wait_status(pid, "initiated", timeout=60)
    ctx.ck("a_worker_moved_it_to_initiated", (init or {}).get("status") == "initiated", init)
    js_init = ctx.wait_journal("pout_" + pid, "payout_initiated", timeout=60)
    ctx.ck("the_ledger_worker_wrote_the_fee-bearing_initiated_journal_once",
           len(js_init) == 1 and js_init[0]["balanced"], js_init)
    entries = ctx.a.journal_entries(js_init[0]["id"]) if js_init else []
    debit = sum(float(e["amount"]) for e in entries if e["type"].strip() == "debit")
    ctx.ck("the_debit_the_ledger_worker_wrote_is_amount_+_fees(fees include tax)",
           abs(debit - (6100 + fees)) < 1e-6,
           {"debit": debit, "expected": 6100 + fees, "entries": entries})
    ctx.ck("no_processed_journal_before_the_bank_completes",
           not [j for j in ctx.a.journals("pout_" + pid)
                if j["transactor_event"] == "payout_processed"], None)
    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=120)
    ctx.ck("the_workers_completed_it", (row or {}).get("status") == "processed", row)
    js_proc = ctx.wait_journal("pout_" + pid, "payout_processed", timeout=90)
    ctx.ck("the_ledger_worker_wrote_the_processed_journal_once_and_balanced",
           len(js_proc) == 1 and js_proc[0]["balanced"], js_proc)
    ctx.ck("the_webhook_payload_carries_the_same_fees_and_tax",
           any(int(d.get("fees") or 0) == fees and int(d.get("tax") or 0) == tax
               for d in ctx.wait_delivery(pid, {"payout.processed"}, timeout=60)), None)
    hits = ctx.a.worker_log_hits(pid)
    ctx.ev["priced_payout_worker_hits"] = hits
    ctx.ck("the_priced_payout_is_attributable_to_real_worker_containers",
           any(k.startswith("payouts-worker-") for k in hits), hits)

    # -- leg 2: a FREE payout reverted by the async failure path. The counter revert is done by
    #    the reversal workers, not by the create or by the bank call. The counter is parked at 2
    #    so the decrement lands on a NON-zero value and is not the EF-009 (1 -> 0) case.
    _align_counter_reset_window(ctx)
    _seed_free_payout_allowance(ctx, 5)
    ctx.a.payouts_sql("UPDATE counters SET free_payouts_consumed=2 WHERE balance_id='%s'"
                      % ctx.m["balance_id"],
                      note="FIXTURE: park the counter at 2 so the async revert targets a NON-zero "
                           "value (the EF-009 1->0 case is covered by cancel_or_reverse)")
    before = int(_counters(ctx)[0]["free_payouts_consumed"])
    ctx.a.mozart(mid, "hold")
    fcr = ctx.create(4500, "pricing-async-free", mode="IMPS")
    fpid = fcr["id"]
    ctx.ck("free_payout_create_200", fcr["status"] == 200 and bool(fpid), fcr.get("raw"))
    if not fpid:
        ctx.a.mozart(mid, clear=True)
        return
    rc, ft, _ = ctx.a.payouts_sql("SELECT fee_type FROM payouts WHERE id='%s'" % fpid,
                                  note="fee_type of the free payout")
    ctx.ck("it_was_priced_as_a_free_payout", ft.strip() == "free_payout", ft.strip())
    consumed = F.wait_until(
        lambda: (lambda c: c if int(c[0]["free_payouts_consumed"]) == before + 1 else None)(_counters(ctx)),
        timeout=40, interval=3) or _counters(ctx)
    mid_val = int(consumed[0]["free_payouts_consumed"])
    ctx.ck("the_counter_was_consumed_on_create", mid_val == before + 1,
           {"before": before, "after_create": mid_val})
    ctx.wait_status(fpid, "initiated", timeout=60)
    ctx.a.finish_bank(fpid, "failure")
    frow = ctx.wait_terminal(fpid, timeout=140, terminal=("reversed", "failed"))
    ctx.ck("the_async_failure_workers_reversed_it",
           (frow or {}).get("status") in ("reversed", "failed"), frow)
    back = F.wait_until(
        lambda: (lambda c: c if int(c[0]["free_payouts_consumed"]) == mid_val - 1 else None)(_counters(ctx)),
        timeout=60, interval=3)
    final = int((back or _counters(ctx))[0]["free_payouts_consumed"])
    ctx.ev["free_payout_counter"] = {"before": before, "after_create": mid_val, "after_revert": final}
    ctx.ck("the_free_payout_was_given_back_by_the_async_reversal_path(non-zero target)",
           final == mid_val - 1, ctx.ev["free_payout_counter"])
    rc, tx, _ = ctx.a.payouts_sql(
        "SELECT transaction_type,payout_status FROM counter_transaction WHERE payout_id='%s' "
        "ORDER BY created_at" % fpid, note="counter_transaction rows written by the async path")
    ctx.ev["counter_transactions"] = tx.strip()
    ctx.ck("both_an_increment_and_a_decrement_counter_transaction_were_recorded",
           "increment" in tx and "decrement" in tx, tx.strip())
    rev = F.wait_until(lambda: ctx.a.reversal(fpid), timeout=45, interval=2)
    ctx.ck("the_async_workers_created_the_reversal_row", bool(rev), rev)
    if rev:
        ctx.ck("and_the_ledger_worker_wrote_its_reversal_journal",
               len(ctx.wait_journal("rvrsl_" + rev["id"], "payout_failed", timeout=80)) == 1, None)
    fhits = ctx.a.worker_log_hits(fpid)
    ctx.ev["free_payout_worker_hits"] = fhits
    ctx.ck("the_revert_is_attributable_to_the_real_failure-handling_worker_containers",
           any(k.startswith("payouts-worker-") for k in fhits), fhits)
    ctx.a.mozart(mid, clear=True)
