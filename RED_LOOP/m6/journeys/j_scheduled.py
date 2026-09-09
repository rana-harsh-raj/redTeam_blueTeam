#!/usr/bin/env python3
"""family:scheduled-payouts -- scheduled_at slot validation and cron dispatch.

payouts validates scheduled_at against the real rules (internal/app/payouts/schedule.go):
strictly after the end of the current IST hour, inside SCHEDULED_AT_MONTHS_ALLOWED, and the
IST hour must be one of the allowed slots {9, 13, 17, 21}; the stored value is truncated to
the start of that hour. Dispatch is NOT self-timed: `POST /v1/cron/process_scheduled_payouts`
(cron_routes.go, the arena cron-driver's `scheduled` job) selects due payouts and hands them
to the payouts-worker-schedule-payout container.

Arena-scoped synthetic clock: the next real slot is hours away, so after asserting the real
slot validation the driver moves the STORED scheduled_at into the past -- exactly what the
passage of time would do, and exactly the fixture verifiers/test_v24 and
ENV2_COMPOSE/verifier/route_scenarios.py use. The state transition itself is still performed
by the real cron + worker; nothing writes `status`.
"""
import time
import uuid

import framework as F
from framework import journey

SH = "shared"


def next_allowed_slot_ist():
    """Epoch seconds of the next IST hour in payouts' allowed slots {9,13,17,21} strictly
    after the end of the current IST hour (mirrors verifiers/test_v24 _next_allowed_slot_ist)."""
    ist_offset = 5 * 3600 + 30 * 60
    now_ist = int(time.time()) + ist_offset
    end_of_hour_ist = (now_ist // 3600 + 1) * 3600
    candidate = end_of_hour_ist
    for _ in range(24 * 8):
        hour = (candidate // 3600) % 24
        if hour in (9, 13, 17, 21) and candidate > end_of_hour_ist:
            return candidate - ist_offset
        candidate += 3600
    raise RuntimeError("no allowed IST slot found")


def _schedule(ctx, amount, note, idem=None):
    slot = next_allowed_slot_ist()
    ctx.a.mozart(ctx.m["merchant_id"], "hold")
    cr = ctx.create(amount, note, idem=idem, extra={"scheduled_at": slot})
    ctx.ck("create_200_with_a_real_allowed_slot", cr["status"] == 200 and bool(cr["id"]),
           {"scheduled_at": slot, "body": cr.get("raw")})
    if not cr["id"]:
        return cr, None, slot
    row = ctx.wait_status(cr["id"], "scheduled", timeout=30)
    ctx.ck("parked_at_scheduled", (row or {}).get("status") == "scheduled", row)
    ctx.ck("scheduled_at_truncated_to_the_slot_hour",
           row and row.get("scheduled_at") and int(row["scheduled_at"]) % 3600 == (
               (-(5 * 3600 + 30 * 60)) % 3600), row)
    return cr, row, slot


def _time_travel(ctx, pid):
    """Arena-only synthetic clock: move the STORED scheduled_at into the past. Only the clock
    field is touched -- status/transitions stay the product's job (test_v24 precedent)."""
    due = int(time.time()) - 60
    ctx.a.payouts_sql("UPDATE payouts SET scheduled_at=%d WHERE id='%s'" % (due, pid),
                      note="SYNTHETIC CLOCK FIXTURE: scheduled_at moved into the past; no status change")
    ctx.ev["synthetic_clock_fixture"] = {
        "payout_id": pid, "field": "scheduled_at", "due": due,
        "classification": "representative fixture for the passage of time; the dispatch itself is "
                          "performed by the real /v1/cron/process_scheduled_payouts + "
                          "payouts-worker-schedule-payout"}
    ctx.note("synthetic clock: scheduled_at moved into the past; the transition is still cron+worker driven")
    return due


@journey("scheduled-payouts", "success", priority="P0", profile=SH,
         title="scheduled_at in a real PS slot -> scheduled -> cron dispatch -> initiated -> processed",
         source_ref="internal/app/payouts/schedule.go slot rules; cron_routes.go process_scheduled_payouts")
def scheduled_success(ctx):
    cr, row, slot = _schedule(ctx, 5100, "sched-success")
    if not cr["id"]:
        return
    pid = cr["id"]
    st, body = ctx.fetch(pid)
    ctx.ck("merchant_fetch_shows_scheduled", st == 200 and (body or {}).get("status") == "scheduled", body)
    ctx.ck("no_fts_transfer_while_scheduled", ctx.a.transfer(pid) is None, None)
    _time_travel(ctx, pid)
    ok, out = ctx.a.cron("scheduled")
    ctx.ck("cron_driver_scheduled_tick_ok", ok, out)
    row = ctx.wait_status(pid, "initiated", timeout=60)
    ctx.ck("dispatched_to_initiated", (row or {}).get("status") == "initiated", row)
    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_status(pid, "processed", timeout=90)
    ctx.ck("processed_after_dispatch", (row or {}).get("status") == "processed", row)
    js = ctx.wait_journal("pout_" + pid, "payout_processed", timeout=80)
    ctx.ck("ledger_payout_processed_once", len(js) == 1 and js[0]["balanced"], js)
    dl = ctx.wait_delivery(pid, {"payout.processed"}, timeout=60)
    ctx.ck("payout.processed_webhook", len(dl) == 1, dl)
    ctx.state["scheduled_pid"] = pid
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("scheduled-payouts", "failure", priority="P0", profile=SH,
         title="scheduled_at outside the allowed IST slots {9,13,17,21} is rejected at create",
         source_ref="internal/app/payouts/schedule.go allowed-slot validation")
def scheduled_failure(ctx):
    good = next_allowed_slot_ist()
    bad = good + 3600            # one hour past an allowed slot -> not an allowed slot
    cr = ctx.create(4100, "sched-bad-slot", extra={"scheduled_at": bad})
    ctx.ck("non_slot_scheduled_at_rejected_4xx",
           isinstance(cr["status"], int) and 400 <= cr["status"] < 500,
           {"scheduled_at": bad, "status": cr["status"], "body": cr.get("raw")})
    past = int(time.time()) - 7200
    cr2 = ctx.create(4100, "sched-past", extra={"scheduled_at": past})
    ctx.ck("past_scheduled_at_rejected_4xx",
           isinstance(cr2["status"], int) and 400 <= cr2["status"] < 500,
           {"scheduled_at": past, "status": cr2["status"], "body": cr2.get("raw")})
    ctx.ck("neither_rejected_create_produced_an_fts_transfer",
           all(ctx.a.transfer(c["id"]) is None for c in (cr, cr2) if c.get("id")), None)


@journey("scheduled-payouts", "cancel_or_reverse", priority="P0", profile=SH,
         title="cancelling a scheduled payout over the merchant (private-auth) API is refused by the source rule; only proxy/dashboard auth may",
         source_ref="internal/app/payouts/validation.go:103-118 ValidateCancel -- isScheduled && authType != LegacyAuthTypeProxy -> "
                    "errorclass.ErrCancelScheduledPayoutInvalidAuth 'Scheduled Payouts can only be cancelled via dashboard'")
def scheduled_cancel(ctx):
    cr, row, slot = _schedule(ctx, 3900, "sched-cancel")
    if not cr["id"]:
        return
    pid = cr["id"]
    st, body = ctx.cancel(pid, "m6 cancel while scheduled")
    err = (body or {}).get("error") if isinstance(body, dict) else {}
    ctx.ck("private_auth_cancel_refused_400", st == 400, {"status": st, "body": str(body)[:300]})
    ctx.ck("refusal_is_the_source_rule(only via dashboard)",
           "dashboard" in str((err or {}).get("description", "")).lower(), err)
    ctx.ck("refusal_points_at_the_scheduled_at_field", (err or {}).get("field") == "scheduled_at", err)
    ctx.ck("payout_left_scheduled_by_the_refusal",
           (ctx.a.payout(pid) or {}).get("status") == "scheduled", ctx.a.payout(pid))
    ctx.note("this journey deliberately uses the plain merchant API key: kong-lite mints a "
             "ConsumerType=merchant passport for it, which goutils passport helpers.go "
             "GetLegacyAuthType maps to LegacyAuthTypePrivate. LegacyAuthTypeProxy needs "
             "ConsumerTypeUser + user_merchant impersonation claims; the dashboard cancel path "
             "that does get them is journey:scheduled-payouts/cancel_via_dashboard")
    # The payout must remain schedulable and dispatchable after the refused cancel.
    _time_travel(ctx, pid)
    ctx.a.cron("scheduled")
    row = ctx.wait_status(pid, "initiated", timeout=60)
    ctx.ck("still_dispatchable_after_the_refused_cancel", (row or {}).get("status") == "initiated", row)
    ctx.a.finish_bank(pid, "success")
    ctx.wait_status(pid, "processed", timeout=90)
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


def dashboard_headers(ctx, role="owner"):
    """kong-lite's opt-in arena control for the dashboard/proxy passport shape
    (ENV2_COMPOSE/substitutes/kong-lite/CONTRACT.md, "ARENA CONTROL"). The
    merchant identity still comes from the verified API key; these headers only
    select consumer {type:"user"} + user_merchant impersonation, which is what
    goutils passport helpers.go GetLegacyAuthType needs to return
    LegacyAuthTypeProxy."""
    return {"X-Arena-Passport-Consumer-Type": "user",
            "X-Arena-User-Id": "ARENAUSR" + F.bare(ctx.m["merchant_id"])[-6:],
            "X-Dashboard-User-Role": role}


def _cancel_as_dashboard(ctx, pid, remarks="m6 dashboard cancel"):
    if F.TRUST["real_gateway"]:
        # M11: the REAL edge gateway carries no arena control (production dashboard traffic reaches the monolith through the
        # api-dashboard Kong service + dashboard app, neither granted); the dashboard identity is BasicAuth::proxyAuth at the
        # monolith replacement -- mint a session for a user of this merchant and cancel on Route.php payout_cancel.
        uid, tok, st0 = ctx.a.ingress_session(ctx.m["merchant_id"])
        if st0 != 200 or not tok:
            return st0, {"error": {"description": "dashboard session could not be minted at api-ingress"}}
        st, body = ctx.a.ingress("POST", "/v1/payouts/pout_%s/cancel" % F.bare(pid), {"remarks": remarks}, basic="dashboard:" + tok,
                                 note="dashboard (proxy-auth) cancel at the monolith replacement (real gateway variant)")
        return st, body
    st, txt = ctx.a.kong("POST", "/v1/payouts/cancel_payout/" + F.bare(pid), ctx.auth,
                         {"remarks": remarks}, dashboard_headers(ctx),
                         note="dashboard (proxy-auth) cancel via the kong-lite arena control")
    return st, (F.jload(txt) or txt)


@journey("scheduled-payouts", "cancel_via_dashboard", priority="P1", profile=SH,
         title="cancel a scheduled payout over the dashboard (proxy-auth) path -> cancelled",
         source_ref="validation.go:103-118 ValidateCancel requires passportSdk.LegacyAuthTypeProxy; "
                    "goutils passport helpers.go GetLegacyAuthType returns proxy only for "
                    "ConsumerTypeUser + impersonation type user_merchant; minted by the kong-lite "
                    "arena-control headers (substitutes/kong-lite/CONTRACT.md)")
def scheduled_cancel_dashboard(ctx):
    cr, row, slot = _schedule(ctx, 3800, "sched-dash-cancel")
    if not cr["id"]:
        return
    pid = cr["id"]
    st, body = _cancel_as_dashboard(ctx, pid)
    err = (body or {}).get("error") if isinstance(body, dict) else {}
    desc = str((err or {}).get("description", "")).lower()

    # Fallback: if the arena control is not honoured (kong-lite not rebuilt, or the
    # headers stripped), payouts answers with exactly the private-auth refusal that
    # journey:scheduled-payouts/cancel_or_reverse already pins. Stay BLOCKED rather
    # than reporting a false failure.
    if st == 400 and "dashboard" in desc:
        ctx.blocked(
            "kong-lite is not honouring the dashboard/proxy passport arena control. The cancel "
            "was still refused with the private-auth rule, so _mint_passport_jwt is still "
            "minting consumer {\"type\": \"merchant\"} with no impersonation claims (goutils "
            "passport helpers.go GetLegacyAuthType -> LegacyAuthTypePrivate).",
            {"observed_refusal": {"status": st, "error": err},
             "sent_headers": sorted(dashboard_headers(ctx)),
             "needed": "rebuild + restart kong-lite so ENV2_COMPOSE/substitutes/kong-lite/"
                       "server.py _dashboard_override / _passport_claims are live: "
                       "docker compose --env-file .env.arena -f docker-compose.yml build kong-lite "
                       "&& docker compose --env-file .env.arena -f docker-compose.yml up -d kong-lite"})

    ctx.ck("dashboard_proxy_auth_cancel_accepted_200", st == 200,
           {"status": st, "body": str(body)[:400]})
    if st != 200:
        # Some other refusal: record it and let the journey fail honestly.
        ctx.ck("refusal_was_not_the_proxy-auth_rule", "dashboard" not in desc, err)
        ctx.a.mozart(ctx.m["merchant_id"], clear=True)
        return
    final = ctx.wait_status(pid, "cancelled", timeout=30)
    ctx.ck("scheduled->cancelled_by_the_dashboard_path", (final or {}).get("status") == "cancelled", final)
    ctx.ck("no_fts_transfer_was_ever_created_for_a_cancelled_schedule",
           ctx.a.transfer(pid) is None, None)
    # Source-faithful: state_machine.go StateCancelled.Enter fires no webhook and
    # appConstants/webhooks.go StatusToWebhookEventMap has no `cancelled` entry,
    # so there is no payout.cancelled event to wait for. See TWIN_SPEC/README.md
    # "Source-faithful behaviours that look like gaps".
    ctx.ck("no_payout.cancelled_webhook(source-faithful)",
           not any(d["event"] == "payout.cancelled"
                   for d in ctx.a.deliveries(ctx.m["merchant_id"], pid)), None)
    ctx.note("the merchant credential is unchanged: only the kong-lite arena-control headers "
             "were added, so the merchant identity still came from the verified API key and "
             "arrived in the passport's impersonation.consumer claim")
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("scheduled-payouts", "idempotency", priority="P0", profile=SH,
         title="replaying the idempotency key of a scheduled create returns the same scheduled payout",
         source_ref="middleware.IdempotencyKey")
def scheduled_idempotency(ctx):
    key = "m6s-" + uuid.uuid4().hex[:12]
    slot = next_allowed_slot_ist()
    ctx.a.mozart(ctx.m["merchant_id"], "hold")
    a = ctx.create(3700, "sched-idem", idem=key, extra={"scheduled_at": slot})
    ctx.ck("first_create_200", a["status"] == 200 and bool(a["id"]), a.get("raw"))
    if not a["id"]:
        return
    ctx.ck("parked_at_scheduled", (ctx.wait_status(a["id"], "scheduled", 30) or {}).get("status") == "scheduled", None)
    b = ctx.create(3700, "sched-idem", idem=key, extra={"scheduled_at": slot})
    ctx.ck("replay_200", b["status"] == 200, b.get("raw"))
    ctx.ck("replay_returns_the_same_scheduled_payout", a["id"] == b["id"], {"first": a["id"], "replay": b["id"]})
    c = ctx.create(3700, "sched-idem", idem=key, extra={"scheduled_at": slot + 4 * 3600})
    ctx.ck("same_key_different_scheduled_at_rejected",
           isinstance(c["status"], int) and 400 <= c["status"] < 500, {"status": c["status"], "body": c.get("raw")})
    ctx.ck("exactly_one_idempotency_row", len(ctx.a.idempotency_row(key, ctx.m["merchant_id"])) == 1, None)
    ctx.ck("payout_still_scheduled", (ctx.a.payout(a["id"]) or {}).get("status") == "scheduled", None)
    ctx.cancel(a["id"], "m6 cleanup")
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)


@journey("scheduled-payouts", "async_state", priority="P0", profile=SH,
         title="a due scheduled payout stays scheduled until the cron runs; the schedule-payout worker performs the transition",
         source_ref="cron-driver JOBS['scheduled']; payouts-worker-schedule-payout container")
def scheduled_async_state(ctx):
    cr, row, slot = _schedule(ctx, 3500, "sched-async")
    if not cr["id"]:
        return
    pid = cr["id"]
    _time_travel(ctx, pid)
    time.sleep(4)
    still = ctx.a.payout(pid)
    ctx.ck("due_payout_does_NOT_self_dispatch_before_the_cron",
           (still or {}).get("status") == "scheduled", still)
    ok, out = ctx.a.cron("scheduled")
    ctx.ck("cron_tick_via_the_arena_cron_driver_container", ok, out)
    row = ctx.wait_status(pid, "initiated", timeout=60)
    ctx.ck("scheduled->initiated_after_the_cron", (row or {}).get("status") == "initiated", row)
    hits = ctx.a.worker_log_hits(pid)
    hits["cron-driver(/v1/cron/process_scheduled_payouts)"] = ctx.a.cron_driver_hits(
        "process_scheduled_payouts")
    ctx.ev["worker_log_hits"] = hits
    ctx.ck("real_cron/worker_containers_show_the_dispatch", bool(hits), hits)
    logs = ctx.a.payout_logs(pid)
    ctx.ck("payout_logs_record_scheduled_then_initiated",
           any(l["to"] == "scheduled" for l in logs) and any(l["to"] == "initiated" for l in logs), logs)
    ctx.a.finish_bank(pid, "success")
    ctx.wait_status(pid, "processed", timeout=90)
    ctx.a.mozart(ctx.m["merchant_id"], clear=True)
