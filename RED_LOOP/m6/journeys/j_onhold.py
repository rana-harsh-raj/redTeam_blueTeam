#!/usr/bin/env python3
"""family:on-hold -- beneficiary-bank downtime parking and release.

The real entry condition (processor/onHoldHelper.go HoldPayoutIfBeneBankDown) is a
conjunction, and every clause is reachable in the arena without touching topology:

  merchantConfig.IsFeatureEnabled("payouts_on_hold")   -> monolith-stub
                                                          POST /_arena/merchant_features
  fund account is a BANK_ACCOUNT and mode is IMPS      -> the provisioned fund account
  checkIfBeneBankIsDown(ifsc[:4])                      -> payouts reads the Redis key
                                                          `{bene_bank_status}` (constants.go:84)
                                                          and holds when the value is
                                                          BeneDowntimeStarted ("started")
  feature "skip_test_txn_for_dmt"                      -> otherwise only ~90% of payouts are held
                                                          (GenerateRandomNumberAndCheckIfPayoutToHold)

Release (onHoldHelper.go GetPayoutIdsForWhichBeneBankIsUp + cron_routes.go
process_beneficiary_bank_on_hold_payouts, the arena cron-driver's `on_hold` job): once the
bank leaves the down map, on_hold payouts for it are dispatched to the
payouts-worker-on-hold-payout container.

on_hold is also a legal cancel source (state_machine.go:380) and maps to the
`payout.queued` webhook (StatusToWebhookEventMap ONHOLD -> WebhookEventQueued).
"""
import json
import time
import uuid

import framework as F
from framework import journey

OH = "onhold"
BENE_KEY = "{bene_bank_status}"
BENE_BANK = "RATN"            # first 4 of the provisioned fund account's IFSC RATN0000001


BLOCKED_DEP = (
    "the payouts_on_hold feature actually reaching payouts' merchantConfig. M6 fixed the first "
    "half: ENV2_COMPOSE/substitutes/monolith-stub/server.py `_get_merchant` now merges the "
    "FEATURE_OVERRIDES that POST /_arena/merchant_features records into `merchant.feature`, the "
    "list payouts parses. The second half is payouts' own cache: the whole merchant-config "
    "document is cached in Redis under `{payouts_merchant_config_key}_<merchant_id>` and "
    "`UpdateMerchantFeatureInCache` is a no-op (reports/findings/12 A.6), so a feature flipped "
    "after payouts has read that merchant once stays invisible until the entry expires -- the "
    "same mechanism as the recurring production `payout_workflows`-ignored incidents "
    "(reports/findings/11). framework.Arena.set_merchant_features drops that key; if a payout "
    "still does not reach on_hold with the key dropped and the feature list confirmed on the "
    "route payouts reads, the remaining gap is in the hold predicate itself "
    "(processor/onHoldHelper.go HoldPayoutIfBeneBankDown) and the evidence below shows which "
    "clause failed.")


def _require_on_hold(ctx, cr, row):
    """Assert we actually reached on_hold, or BLOCK with the exact, evidenced dependency."""
    st, cfg = ctx.a.jhttp("GET", F.MONOLITH + "/internal/merchants/" + ctx.m["merchant_id"],
                          basic=F.D._mono_basic(),
                          note="the merchant-config route payouts reads for IsFeatureEnabled")
    features = ((cfg or {}).get("merchant") or {}).get("feature")
    ctx.ev["merchant_config_features_seen_by_payouts"] = features
    st2, ov = ctx.a.jhttp("GET", F.MONOLITH + "/_arena/merchant_features?merchant_id=" + ctx.m["merchant_id"],
                          basic=F.D._mono_basic(), note="the /_arena override view of the same merchant")
    ctx.ev["arena_feature_override_view"] = ov
    if (row or {}).get("status") == "on_hold":
        return True
    ctx.blocked(BLOCKED_DEP,
                {"payout_row": row,
                 "merchant_config_feature_list_returned_to_payouts": features,
                 "arena_override_view": ov,
                 "payouts_cached_merchant_config": ctx.a.redis(
                     "GET", ctx.a.MERCHANT_CONFIG_CACHE_KEY % ctx.m["merchant_id"],
                     note="payouts' cached MerchantConfig at the moment of the failure")[:600],
                 "feature_flips_this_journey": ctx.ev.get("merchant_feature_flips"),
                 "bene_bank_status_redis": ctx.a.redis("GET", BENE_KEY),
                 "payout_mode": "IMPS", "fund_account_type": "bank_account",
                 "then": "rerun run.py --family on-hold"})


def _feature(ctx, on):
    """Flip the two features the hold predicate needs AND drop payouts' cached MerchantConfig --
    without the cache drop payouts keeps serving the feature list it read at provisioning time."""
    st, body, flip = ctx.a.set_merchant_features(
        ctx.m["merchant_id"], {"payouts_on_hold": bool(on), "skip_test_txn_for_dmt": bool(on)})
    ctx.ev.setdefault("feature_overrides", []).append({"on": on, "status": st, "flip": flip})
    return st, body


def _bank_down(ctx, down):
    """Set / clear the beneficiary-bank downtime map payouts reads from Redis."""
    payload = json.dumps({BENE_BANK: "started"}) if down else json.dumps({})
    ctx.a.redis("SET", BENE_KEY, payload,
                note="payouts BeneBankStatusKey (constants.go:84) -> "
                     + ("RATN downtime STARTED" if down else "no bank down"))
    return ctx.a.redis("GET", BENE_KEY, note="bene bank status readback")


def _cleanup(ctx):
    ctx.a.redis("DEL", BENE_KEY, note="remove the synthetic bene-bank downtime map")
    _feature(ctx, False)
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


def _park_on_hold(ctx, amount, note):
    _feature(ctx, True)
    ctx.ck("bene_bank_downtime_map_written", BENE_BANK in _bank_down(ctx, True), None)
    ctx.a.mozart(ctx.m["merchant_id"], "hold")
    cr = ctx.create(amount, note, mode="IMPS")
    ctx.ck("create_200", cr["status"] == 200 and bool(cr["id"]), cr.get("raw"))
    if not cr["id"]:
        return cr, None
    row = ctx.wait_status(cr["id"], "on_hold", timeout=45)
    return cr, row


@journey("on-hold", "success", priority="P1", profile=OH,
         title="beneficiary-bank downtime parks an IMPS payout at on_hold; clearing the downtime + the on_hold cron releases it to processed",
         source_ref="processor/onHoldHelper.go HoldPayoutIfBeneBankDown; payouts constants.go:84 BeneBankStatusKey; "
                    "onHoldHelper.go GetPayoutIdsForWhichBeneBankIsUp; cron_routes.go process_beneficiary_bank_on_hold_payouts")
def onhold_success(ctx):
    try:
        cr, row = _park_on_hold(ctx, 6800, "onhold-success")
        if not cr["id"]:
            return
        pid = cr["id"]
        _require_on_hold(ctx, cr, row)
        ctx.ck("parked_at_on_hold", True, row)
        ctx.ck("queued_reason_is_bene_bank_down",
               (row or {}).get("queued_reason") in ("bene_bank_down", "beneficiary_bank_down"), row)
        ctx.ck("on_hold_at_stamped", bool((row or {}).get("on_hold_at")), row)
        ctx.ck("no_fts_transfer_while_on_hold", ctx.a.transfer(pid) is None, None)
        dl = ctx.wait_delivery(pid, {"payout.queued"}, timeout=45)
        ctx.ck("on_hold_maps_to_the_payout.queued_webhook(StatusToWebhookEventMap ONHOLD)",
               len(dl) >= 1, dl)
        # bank recovers
        ctx.ck("bene_bank_downtime_cleared", BENE_BANK not in _bank_down(ctx, False), None)
        ok, out = ctx.a.cron("on_hold")
        ctx.ck("on_hold_cron_tick_ok", ok, out)
        row = F.wait_until(lambda: (lambda r: r if r and r.get("status") != "on_hold" else None)(
            ctx.a.payout(pid)), timeout=90, interval=3) or ctx.a.payout(pid)
        ctx.ck("released_from_on_hold_by_the_cron", (row or {}).get("status") != "on_hold", row)
        ctx.a.finish_bank(pid, "success")
        row = ctx.wait_status(pid, "processed", timeout=90)
        ctx.ck("processed_after_release", (row or {}).get("status") == "processed", row)
        js = ctx.wait_journal("pout_" + pid, "payout_processed", timeout=80)
        ctx.ck("ledger_payout_processed_after_release", len(js) == 1 and js[0]["balanced"], js)
        ctx.state["onhold_pid"] = pid
    finally:
        _cleanup(ctx)


@journey("on-hold", "cancel_or_reverse", priority="P1", profile=OH,
         title="on_hold is a legal cancel source: cancelling a held payout moves it to cancelled and the release cron leaves it alone",
         source_ref="state_machine.go:380 EventCancelled From(queued, scheduled, on_hold); status.go CancelPayoutAcceptableStatuses")
def onhold_cancel(ctx):
    try:
        cr, row = _park_on_hold(ctx, 5400, "onhold-cancel")
        if not cr["id"]:
            return
        pid = cr["id"]
        _require_on_hold(ctx, cr, row)
        ctx.ck("parked_at_on_hold", True, row)
        st, body = ctx.cancel(pid, "m6 cancel while on_hold")
        ctx.ck("cancel_from_on_hold_accepted_200", st == 200, {"status": st, "body": str(body)[:250]})
        row = ctx.wait_status(pid, "cancelled", timeout=30)
        ctx.ck("payout_cancelled", (row or {}).get("status") == "cancelled", row)
        _bank_down(ctx, False)
        ctx.a.cron("on_hold")
        time.sleep(6)
        ctx.ck("release_cron_does_not_resurrect_a_cancelled_payout",
               (ctx.a.payout(pid) or {}).get("status") == "cancelled", ctx.a.payout(pid))
        ctx.ck("no_fts_transfer_ever", ctx.a.transfer(pid) is None, None)
        ctx.ck("no_ledger_journal_ever", ctx.a.journals("pout_" + pid) == [], None)
    finally:
        _cleanup(ctx)


@journey("on-hold", "async_state", priority="P1", profile=OH,
         title="the on_hold -> dispatched transition is done by the on_hold cron + payouts-worker-on-hold-payout, never by the create path",
         source_ref="cron-driver JOBS['on_hold']; payouts-worker-on-hold-payout / -partner-bank-hold-payouts containers")
def onhold_async_state(ctx):
    try:
        cr, row = _park_on_hold(ctx, 5000, "onhold-async")
        if not cr["id"]:
            return
        pid = cr["id"]
        _require_on_hold(ctx, cr, row)
        ctx.ck("parked_at_on_hold", True, row)
        # negative control: the release cron must NOT move it while the bank is still down
        ctx.a.cron("on_hold")
        time.sleep(6)
        ctx.ck("cron_leaves_it_held_while_the_bank_is_still_down",
               (ctx.a.payout(pid) or {}).get("status") == "on_hold", ctx.a.payout(pid))
        _bank_down(ctx, False)
        ok, out = ctx.a.cron("on_hold")
        ctx.ck("on_hold_cron_tick_ok", ok, out)
        row = F.wait_until(lambda: (lambda r: r if r and r.get("status") != "on_hold" else None)(
            ctx.a.payout(pid)), timeout=90, interval=3) or ctx.a.payout(pid)
        ctx.ck("released_only_after_the_bank_came_back_up", (row or {}).get("status") != "on_hold", row)
        hits = ctx.a.worker_log_hits(pid)
        hits["cron-driver(/v1/cron/process_beneficiary_bank_on_hold_payouts)"] = ctx.a.cron_driver_hits(
            "process_beneficiary_bank_on_hold_payouts")
        ctx.ev["worker_log_hits"] = hits
        ctx.ck("real_cron/worker_containers_show_the_release", bool(hits), hits)
        logs = ctx.a.payout_logs(pid)
        ctx.ck("payout_logs_record_on_hold_then_a_later_transition",
               any(l["to"] == "on_hold" for l in logs) and logs[-1]["to"] != "on_hold", logs)
        ctx.a.finish_bank(pid, "success")
        ctx.wait_status(pid, "processed", timeout=90)
    finally:
        _cleanup(ctx)


@journey("on-hold", "failure", priority="P1", profile=OH,
         title="without the payouts_on_hold feature the same bank downtime holds nothing -- the payout goes straight to the bank",
         source_ref="processor/onHoldHelper.go: IsFeatureEnabled(PayoutsOnHold)==false short-circuits the hold")
def onhold_feature_gate(ctx):
    try:
        _feature(ctx, False)
        ctx.ck("bene_bank_downtime_map_written", BENE_BANK in _bank_down(ctx, True), None)
        ctx.a.mozart(ctx.m["merchant_id"], "hold")
        cr = ctx.create(4700, "onhold-featureoff", mode="IMPS")
        ctx.ck("create_200", cr["status"] == 200 and bool(cr["id"]), cr.get("raw"))
        if not cr["id"]:
            return
        pid = cr["id"]
        row = ctx.wait_status(pid, "initiated", timeout=45)
        ctx.ck("feature_off_means_no_hold(payout reached initiated)",
               (row or {}).get("status") == "initiated", row)
        ctx.ck("no_on_hold_at_stamped", not (row or {}).get("on_hold_at"), row)
        ctx.ck("an_fts_transfer_was_actually_created", ctx.a.transfer(pid) is not None, None)
        ctx.a.finish_bank(pid, "success")
        ctx.ck("completes_normally",
               (ctx.wait_status(pid, "processed", timeout=90) or {}).get("status") == "processed", None)
    finally:
        _cleanup(ctx)


@journey("on-hold", "idempotency", priority="P0", profile=OH,
         title="replaying the idempotency key of a held payout returns that same held payout: one payout, one hold, one release",
         source_ref="middleware.IdempotencyKey stores source_id before the hold decision; "
                    "processor/onHoldHelper.go HoldPayoutIfBeneBankDown runs inside the create path")
def onhold_idempotency(ctx):
    """The hold happens inside the create path, so the idempotency guard has to hold across it:
    a replay must return the SAME on_hold payout rather than parking a second one behind the same
    downtime."""
    try:
        key = "m6oh-" + uuid.uuid4().hex[:12]
        tag = "onhold-idem-" + key[-6:]
        _feature(ctx, True)
        ctx.ck("bene_bank_downtime_map_written", BENE_BANK in _bank_down(ctx, True), None)
        ctx.a.mozart(ctx.m["merchant_id"], "hold")
        a = ctx.create(5200, tag, idem=key, mode="IMPS")
        ctx.ck("first_create_200", a["status"] == 200 and bool(a["id"]), a.get("raw"))
        if not a["id"]:
            return
        pid = a["id"]
        row = ctx.wait_status(pid, "on_hold", timeout=45)
        _require_on_hold(ctx, a, row)
        ctx.ck("the_first_create_was_parked_at_on_hold", True, row)

        b = ctx.create(5200, tag, idem=key, mode="IMPS")
        ctx.ck("replay_200", b["status"] == 200, b.get("raw"))
        ctx.ck("the_replay_returned_the_SAME_payout", b["id"] == pid, {"a": pid, "b": b["id"]})
        ctx.ck("the_replay_entity_still_reports_the_held_state",
               (b["json"] or {}).get("status") in ("on_hold", "queued", "processing"),
               (b["json"] or {}).get("status"))
        ctx.ck("exactly_one_idempotency_keys_row",
               len(ctx.a.idempotency_row(key, ctx.m["merchant_id"])) == 1,
               ctx.a.idempotency_row(key, ctx.m["merchant_id"]))
        rc, cnt, _ = ctx.a.payouts_sql(
            "SELECT count(*) FROM payouts WHERE merchant_id='%s' AND narration='m6 %s'"
            % (ctx.m["merchant_id"], tag), note="one payout row for the two identical requests")
        ctx.ck("only_one_payout_row_exists_for_the_two_requests", cnt.strip() == "1", cnt.strip())
        logs = ctx.a.payout_logs(pid)
        ctx.ck("the_payout_was_put_on_hold_exactly_once",
               sum(1 for l in logs if l["to"] == "on_hold") == 1, logs)
        ctx.ck("no_second_fts_transfer_and_no_transfer_at_all_while_held",
               ctx.a.transfer(pid) is None, None)

        # and the single held payout releases once, not twice
        ctx.ck("bene_bank_downtime_cleared", BENE_BANK not in _bank_down(ctx, False), None)
        ctx.a.cron("on_hold")
        released = F.wait_until(lambda: (lambda r: r if r and r.get("status") != "on_hold" else None)(
            ctx.a.payout(pid)), timeout=90, interval=3) or ctx.a.payout(pid)
        ctx.ck("released_once_by_the_cron", (released or {}).get("status") != "on_hold", released)
        ctx.a.finish_bank(pid, "success")
        final = ctx.wait_status(pid, "processed", timeout=120)
        ctx.ck("completes_once", (final or {}).get("status") == "processed", final)
        ctx.ck("exactly_one_payout_processed_ledger_journal",
               len(ctx.wait_journal("pout_" + pid, "payout_processed", timeout=90)) == 1, None)
    finally:
        _cleanup(ctx)
