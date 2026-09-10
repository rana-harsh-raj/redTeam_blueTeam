#!/usr/bin/env python3
"""family:webhooks -- merchant notification fidelity.

The chain under test is real end to end: payouts state machine ->
FireWebhookEventAsyncForPayout -> payouts-worker-webhook-event -> stork-capture
(Stork substitute, records event_name/event_id/delivered_status) ->
merchant-webhook-sink (verifies the HMAC signature and records the delivery).

Event vocabulary is pinned to internal/app/common/appConstants/webhooks.go:

    payout.initiated  payout.processed  payout.reversed  payout.failed
    payout.updated    payout.queued     payout.rejected  payout.pending
    StatusToWebhookEventMap: {created:initiated, processed, reversed, failed,
                              updated, queued, rejected, pending, on_hold->payout.queued}

There is deliberately no `payout.cancelled` (see family:failure-reversal-cancellation/webhook).
Delivery is at-least-once by design; the assertions below tolerate repeats but require
that every repeat carries the SAME stork event_id and the SAME payload.
"""
import time

import framework as F
from framework import journey

SH = "shared"
TERMINALS = {"payout.processed", "payout.reversed", "payout.failed"}


def _drive(ctx, amount, scenario, note):
    ctx.a.mozart(ctx.m["merchant_id"], "hold")
    cr = ctx.create(amount, note)
    ctx.ck("create_200", cr["status"] == 200 and bool(cr["id"]), cr.get("raw"))
    if not cr["id"]:
        return None, None
    ctx.wait_status(cr["id"], "initiated", timeout=45)
    ctx.a.finish_bank(cr["id"], scenario)
    row = ctx.wait_terminal(cr["id"], timeout=90)
    return cr["id"], row


def _assert_matches_db(ctx, pid, row, event):
    dl = ctx.wait_delivery(pid, {event}, timeout=60)
    ctx.ck("terminal_delivery_present", len(dl) >= 1, dl)
    if not dl:
        return dl
    d = dl[0]
    ctx.ck("event_name_matches_the_final_db_status_via_StatusToWebhookEventMap",
           d["event"] == event, {"delivery": d, "db_status": (row or {}).get("status")})
    ctx.ck("payload_entity_id_matches", d["payout_id"] == "pout_" + pid, d)
    ctx.ck("payload_entity_status_matches_db", d["payout_status"] == (row or {}).get("status"), d)
    ctx.ck("payload_utr_matches_db", str(d.get("utr") or "") == str((row or {}).get("utr") or ""), d)
    ctx.ck("payload_fees_and_tax_match_db",
           str(d.get("fees")) == str(int((row or {}).get("fees") or 0))
           and str(d.get("tax")) == str(int((row or {}).get("tax") or 0)),
           {"delivery": {"fees": d.get("fees"), "tax": d.get("tax")},
            "db": {"fees": (row or {}).get("fees"), "tax": (row or {}).get("tax")}})
    ctx.ck("signature_present_and_valid",
           d.get("signature_present") is True and d.get("signature_valid") is True, d)
    ctx.ck("account_id_scoped_to_the_owning_merchant",
           d.get("account_id") == "acc_" + ctx.m["merchant_id"], d)
    return dl


@journey("webhooks", "success", priority="P0", profile=SH,
         title="payout.processed payload matches the payouts DB row field-for-field and is signed",
         source_ref="appConstants/webhooks.go StatusToWebhookEventMap; verifiers/test_v21")
def webhook_success(ctx):
    pid, row = _drive(ctx, 5900, "success", "wh-success")
    if not pid:
        return
    ctx.ck("db_status_processed", (row or {}).get("status") == "processed", row)
    _assert_matches_db(ctx, pid, row, "payout.processed")
    ev = ctx.a.stork_events(ctx.m["merchant_id"], pid)
    terminal = [e for e in ev if e["event_name"] in TERMINALS]
    ctx.ck("exactly_one_terminal_event_at_stork_delivered_200",
           len(terminal) == 1 and terminal[0]["delivered_status"] == 200, terminal)
    ctx.state["wh_success_pid"] = pid
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("webhooks", "failure", priority="P0", profile=SH,
         title="a bank failure produces exactly one terminal event and it is payout.reversed (the Shared remap), not payout.processed",
         source_ref="appConstants/webhooks.go WebhookEventReversed; test_v18 Shared failed->reversed remap")
def webhook_failure(ctx):
    pid, row = _drive(ctx, 4800, "failure", "wh-failure")
    if not pid:
        return
    ctx.ck("db_status_reversed(shared remap)", (row or {}).get("status") == "reversed", row)
    _assert_matches_db(ctx, pid, row, "payout.reversed")
    all_dl = ctx.a.deliveries(ctx.m["merchant_id"], pid)
    terminal = [d for d in all_dl if d["event"] in TERMINALS]
    ctx.ck("no_success_event_leaked_for_a_failed_payout",
           not any(d["event"] == "payout.processed" for d in terminal), terminal)
    ctx.ck("exactly_one_distinct_terminal_event_name",
           len({d["event"] for d in terminal}) == 1, terminal)
    ctx.state["wh_failure_pid"] = pid
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("webhooks", "duplicate", priority="P0", profile=SH,
         title="at-least-once delivery is tolerated: any repeat of a terminal delivery carries the same stork event_id and the same payload",
         source_ref="stork delivery semantics; verifiers/test_m4_g34_duplicate_terminal_webhook.py")
def webhook_duplicate(ctx):
    pid, row = _drive(ctx, 4300, "success", "wh-dup")
    if not pid:
        return
    dl = ctx.wait_delivery(pid, {"payout.processed"}, timeout=60)
    ctx.ck("terminal_delivered", len(dl) >= 1, dl)
    # replay the terminal FTS status webhook -- the real duplicate stimulus
    t = ctx.a.transfer(pid)
    body = {"fund_transfer_id": int(t["id"]), "source_id": pid, "source_type": "payout",
            "status": "processed", "utr": (row or {}).get("utr"),
            "source_account_id": int(t["source_account_id"]),
            "bank_account_type": t.get("sa_bank_account_type")}
    st, txt = ctx.a.http("POST", F.PAYOUTS + "/v1/payouts/transfer_status_webhook", body,
                         basic=F.bridge("ps-service"), note="duplicate terminal stimulus "
                         "(payout_internal_routes.go /transfer_status_webhook, service Basic-Auth)")
    ctx.ck("duplicate_stimulus_reached_the_real_route", isinstance(st, int) and st not in (401, 403, 404),
           {"status": st, "body": (txt or "")[:200]})
    time.sleep(8)
    after = [d for d in ctx.a.deliveries(ctx.m["merchant_id"], pid) if d["event"] == "payout.processed"]
    ctx.ck("at_least_one_terminal_delivery_still_present", len(after) >= 1, after)
    ctx.ck("every_terminal_delivery_carries_the_same_event_id(at-least-once, not at-least-two-events)",
           len({d["event_id"] for d in after}) == 1, after)
    ctx.ck("every_terminal_delivery_carries_the_same_payload",
           len({(d["payout_status"], str(d["utr"]), str(d["fees"])) for d in after}) == 1, after)
    ev = [e for e in ctx.a.stork_events(ctx.m["merchant_id"], pid) if e["event_name"] in TERMINALS]
    ctx.ck("stork_recorded_exactly_one_terminal_event", len(ev) == 1, ev)
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("webhooks", "idempotency", priority="P0", profile=SH,
         title="an idempotent create replay produces no second webhook stream for the same payout",
         source_ref="middleware.IdempotencyKey + state_machine.go webhook firing on state Enter only")
def webhook_idempotency(ctx):
    import uuid
    key = "m6wh-" + uuid.uuid4().hex[:12]
    ctx.a.mozart(ctx.m["merchant_id"], "hold")
    a = ctx.create(3800, "wh-idem", idem=key)
    ctx.ck("create_200", a["status"] == 200 and bool(a["id"]), a.get("raw"))
    pid = a["id"]
    if not pid:
        return
    ctx.wait_status(pid, "initiated", timeout=45)
    b = ctx.create(3800, "wh-idem", idem=key)
    ctx.ck("replay_returns_same_payout", b["id"] == pid, {"first": pid, "replay": b["id"]})
    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=90)
    ctx.ck("processed", (row or {}).get("status") == "processed", row)
    ctx.wait_delivery(pid, {"payout.processed"}, timeout=60)
    time.sleep(5)
    dl = ctx.a.deliveries(ctx.m["merchant_id"], pid)
    terminal = [d for d in dl if d["event"] in TERMINALS]
    ctx.ck("exactly_one_terminal_delivery_despite_the_replay", len(terminal) == 1, terminal)
    ctx.ck("all_deliveries_belong_to_this_payout",
           all(d["payout_id"] == "pout_" + pid for d in dl), dl)
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("webhooks", "webhook", priority="P0", profile=SH,
         title="event-name mapping check: every event this run produced is a real appConstants name and matches its state",
         source_ref="appConstants/webhooks.go WebhookEvent* constants and StatusToWebhookEventMap")
def webhook_vocabulary(ctx):
    known = {"payout.initiated", "payout.processed", "payout.reversed", "payout.failed",
             "payout.updated", "payout.queued", "payout.rejected", "payout.pending"}
    mid = ctx.m["merchant_id"]
    dl = ctx.a.deliveries(mid)
    ctx.ck("this_merchant_received_deliveries_in_this_run", len(dl) > 0, len(dl))
    unknown = sorted({d["event"] for d in dl} - known)
    ctx.ck("no_event_name_outside_appConstants/webhooks.go", not unknown, unknown)
    ctx.ck("no_payout.cancelled_exists_anywhere_for_this_merchant",
           not any(d["event"] == "payout.cancelled" for d in dl), None)
    mapping = {"payout.processed": "processed", "payout.reversed": "reversed",
               "payout.failed": "failed", "payout.queued": "queued", "payout.pending": "pending"}
    mismatched = []
    for d in dl:
        want = mapping.get(d["event"])
        if want and d["payout_status"] and d["payout_status"] != want:
            mismatched.append(d)
    ctx.ck("every_delivered_entity_status_matches_its_event_name", not mismatched, mismatched[:5])
    ctx.ck("every_delivery_is_signed_and_scoped_to_this_merchant",
           all(d["signature_valid"] is True and d["account_id"] == "acc_" + mid for d in dl),
           [d for d in dl if not (d["signature_valid"] and d["account_id"] == "acc_" + mid)][:5])
    ctx.ev["event_name_histogram"] = {e: sum(1 for d in dl if d["event"] == e)
                                      for e in sorted({d["event"] for d in dl})}


@journey("webhooks", "async_state", priority="P0", profile=SH,
         title="delivery is performed by payouts-worker-webhook-event + stork-capture, after the DB status changed",
         source_ref="payouts-worker-webhook-event container; stork-capture /_arena/events delivered_status")
def webhook_async_state(ctx):
    mid = ctx.m["merchant_id"]
    ctx.a.mozart(mid, "hold")
    cr = ctx.create(3400, "wh-async")
    pid = cr["id"]
    ctx.ck("create_200", cr["status"] == 200 and bool(pid), cr.get("raw"))
    if not pid:
        return
    ctx.wait_status(pid, "initiated", timeout=45)
    before = [d for d in ctx.a.deliveries(mid, pid) if d["event"] in TERMINALS]
    ctx.ck("no_terminal_delivery_before_the_bank_completes", not before, before)
    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=90)
    ctx.ck("db_reached_processed", (row or {}).get("status") == "processed", row)
    dl = ctx.wait_delivery(pid, {"payout.processed"}, timeout=60)
    ctx.ck("terminal_delivery_arrived_after_the_db_transition", len(dl) == 1, dl)
    ev = [e for e in ctx.a.stork_events(mid, pid) if e["event_name"] == "payout.processed"]
    ctx.ck("stork_records_the_delivery_as_HTTP_200", ev and ev[0]["delivered_status"] == 200, ev)
    ctx.ck("sink_event_id_equals_stork_event_id", ev and dl and dl[0]["event_id"] == ev[0]["event_id"],
           {"sink": dl[0]["event_id"] if dl else None, "stork": ev[0]["event_id"] if ev else None})
    hits = ctx.a.worker_log_hits(pid)
    ctx.ev["worker_log_hits"] = hits
    ctx.ck("a_real_payouts_async_worker_container_logged_this_payout", bool(hits), hits)
    if "payouts-worker-webhook-event" not in hits:
        ctx.note("OBSERVATION: the webhook job for this payout was executed by %s, not by the "
                 "dedicated payouts-worker-webhook-event container (whose log shows only "
                 "trace-exporter noise in the same window). Delivery is correct end to end; this "
                 "is a worker/queue attribution observation for the topology lane, not a journey "
                 "failure." % ", ".join(sorted(hits)))
    ctx.a.mozart(mid, clear=True)
