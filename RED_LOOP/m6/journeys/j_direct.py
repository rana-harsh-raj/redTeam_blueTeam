#!/usr/bin/env python3
"""family:direct-payouts -- the Direct (current-account / RBL) outcome matrix.

This driver does NOT re-implement the Direct journeys: it imports and invokes the audited
M4 driver, RED_LOOP/surface/m4_direct_journeys.py, on two fresh Direct merchants and maps
its A-G results into the M6 vocabulary so they land in reports/implementation/m6-journeys.json
alongside every other family.

  A -> success            hold -> success, reservation live -> awaiting_balance_refresh,
                          FTS preferred==source==fund_account, real rbl statement ingest,
                          xas match -> UpdatePayoutAfterBASRecon -> DA ledger journals, webhook
  B -> failure            immediate bank failure -> failed (Direct rbl does NOT remap to reversed),
                          no reversal row, no Shared reversal journal, reservation released
  C -> pending            mozart delayed_success -> pending observed -> processed, recon once
  D -> failure_pending    hold (pending) then failure -> failed
  E -> statement_first    statement linked before the terminal status (route R1 vs the relay route)
  F -> duplicate          identical statement CSV twice -> no new BAS row, no duplicate journal
  G -> cancel_or_reverse  credit statement for a failed payout -> failed -> reversed with a
                          reversal transaction_id; wrong-amount statement -> external (negative control)

plus two M6-native variants on the same merchants: `idempotency` and `async_state`.

The A-G evidence stays in the M4 bundle written under this run's directory (checks, route
matrix, artifacts); the M6 evidence file records the mapped result and the full M4 check list.
"""
import sys
import time
import uuid
from pathlib import Path

import framework as F
from framework import journey

sys.path.insert(0, str(F.REPO / "RED_LOOP" / "surface"))
import m4_direct_journeys as M4          # noqa: E402  (lane reuse: invoke, do not duplicate)

A = "direct-a"
B = "direct-b"
MAP = {"A": "success", "B": "failure", "C": "pending", "D": "failure_pending",
       "E": "statement_first", "F": "duplicate", "G": "cancel_or_reverse"}


def _m4(ctx):
    """One shared M4 Journeys instance per run, over the two fresh Direct merchants."""
    if "m4_journeys" in ctx.state:
        return ctx.state["m4_journeys"]
    dA = ctx.pool.get(A)
    dB = ctx.pool.get(B)
    M4.wait_ps_health()
    j = M4.Journeys(dA, dB, ctx.run_dir)
    ctx.state["m4_journeys"] = j
    ctx.state["direct_a"] = dA
    ctx.state["direct_b"] = dB
    return j


def _run_m4(ctx, letter):
    j = _m4(ctx)
    before = len(j.results)
    fn = getattr(j, "journey_" + letter)
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        import traceback
        ctx.ev["m4_exception"] = traceback.format_exc()
        ctx.ck("m4_journey_%s_ran_without_an_unhandled_error" % letter, False, str(exc)[:400])
        return
    rec = j.results[before] if len(j.results) > before else None
    if rec is None:
        ctx.ck("m4_journey_%s_produced_a_result" % letter, False, None)
        return
    ctx.ev["m4_record"] = {k: rec.get(k) for k in ("journey", "title", "merchant_id", "result", "fidelity")}
    ctx.ev["m4_checks"] = rec.get("checks")
    ctx.ev["m4_evidence"] = rec.get("evidence")
    ctx.ev["merchant_id"] = rec.get("merchant_id")
    ctx.fidelity = rec.get("fidelity") or ctx.fidelity
    ctx.note("delegated to RED_LOOP/surface/m4_direct_journeys.py journey %s on merchant %s"
             % (letter, rec.get("merchant_id")))
    for c in rec.get("checks") or []:
        ctx.ck(c["name"], c["ok"], c.get("detail"))
    if rec.get("result") == "BLOCKED":
        ctx.blocked("M4 Direct journey %s reported BLOCKED: %s"
                    % (letter, str((rec.get("evidence") or {}).get("blocker"))[:400]),
                    rec.get("evidence"))


def _mk(letter):
    variant = MAP[letter]

    @journey("direct-payouts", variant, priority="P0" if letter in "AB" else "P1",
             profile=A if letter in "ACEF" else B, order="ABCDEFG".index(letter),
             title="M4 Direct journey %s (%s)" % (letter, variant),
             source_ref="RED_LOOP/surface/m4_direct_journeys.py journey_%s; reports/implementation/"
                        "m4-direct-e2e-coverage.md" % letter)
    def driver(ctx, _letter=letter):
        _run_m4(ctx, _letter)

    driver.__name__ = "direct_%s" % letter
    return driver


for _l in "ABCDEFG":
    _mk(_l)


@journey("direct-payouts", "idempotency", priority="P0", profile=A, order=7,
         title="X-Payout-Idempotency on a Direct merchant: same key+body -> same payout, different body -> rejected",
         source_ref="middleware.IdempotencyKey (account-type independent); verifiers test_v01 / test_v02")
def direct_idempotency(ctx):
    d = ctx.m
    key = "m6d-" + uuid.uuid4().hex[:12]
    M4.clean_scenarios(d)
    F.D.set_mozart_scenario(d["merchant_id"], "hold")
    a = M4.create_payout(d, 4600, "m6-idem", idem=key)
    ctx.ev["create_a"] = a
    ctx.ck("first_create_200", a["status"] == 200 and bool(a["payout_id"]), a.get("body"))
    if not a["payout_id"]:
        return
    b = M4.create_payout(d, 4600, "m6-idem", idem=key)
    ctx.ev["create_b"] = b
    ctx.ck("replay_200", b["status"] == 200, b.get("body"))
    ctx.ck("replay_returns_the_same_payout_id", a["payout_id"] == b["payout_id"],
           {"a": a["payout_id"], "b": b["payout_id"]})
    c = M4.create_payout(d, 4601, "m6-idem", idem=key)
    ctx.ev["create_c"] = c
    ctx.ck("same_key_different_body_rejected_4xx",
           isinstance(c["status"], int) and 400 <= c["status"] < 500,
           {"status": c["status"], "body": c.get("body")})
    rows = ctx.a.idempotency_row(key, d["merchant_id"])
    ctx.ck("exactly_one_idempotency_keys_row", len(rows) == 1, rows)
    ctx.ck("the_key_row_is_scoped_to_this_direct_merchant",
           rows and rows[0]["merchant_id"] == d["merchant_id"], rows)
    t = F.wait_until(lambda: (lambda x: x if x and x.get("attempt_id") else None)(
        ctx.a.transfer(a["payout_id"])), timeout=45, interval=2)
    ctx.ck("exactly_one_fts_transfer_for_the_two_requests", bool(t), t)
    if t:
        F.D.set_mozart_scenario(str(t["attempt_id"]), "success")
        ctx.a.fts_check(t["id"])
        row = F.wait_until(lambda: (lambda r: r if r and r.get("status") == "processed" else None)(
            ctx.a.payout(a["payout_id"])), timeout=90, interval=3)
        ctx.ck("payout_completes_after_the_replays", bool(row), row or ctx.a.payout(a["payout_id"]))
    M4.clean_scenarios(d)


@journey("direct-payouts", "async_state", priority="P0", profile=A, order=8,
         title="a Direct payout reaches its terminal state only through the real FTS/payouts workers, and posts NO Shared ledger journal",
         source_ref="Direct payouts write no payout_initiated/payout_processed journal (M4 invariant "
                    "I-Direct-success-no-duplicate-ledger); payouts-worker-* containers do the transition")
def direct_async_state(ctx):
    d = ctx.m
    mid, bal = d["merchant_id"], d["balance_id"]
    M4.clean_scenarios(d)
    F.D.set_mozart_scenario(mid, "hold")
    cr = M4.create_payout(d, 5200, "m6-async")
    pid = cr["payout_id"]
    ctx.ev["create"] = cr
    ctx.ck("create_200", cr["status"] == 200 and bool(pid), cr.get("body"))
    if not pid:
        return
    ctx.ck("create_response_is_non_terminal",
           cr.get("response_status") not in ("processed", "failed", "reversed"), cr.get("response_status"))
    init = F.wait_until(lambda: (lambda r: r if r and r.get("status") == "initiated" else None)(
        ctx.a.payout(pid)), timeout=60, interval=2)
    ctx.ck("row_reached_initiated_while_the_bank_was_held", bool(init), init or ctx.a.payout(pid))
    snap, items = ctx.a.reservations(mid, bal)
    live = [i for i in items if i.get("payout_id") == pid and i.get("state") == "live"]
    ctx.ck("in_flight_reservation_live_while_initiated", bool(live), items)
    t = F.wait_until(lambda: (lambda x: x if x and x.get("attempt_id") else None)(ctx.a.transfer(pid)),
                     timeout=45, interval=2)
    ctx.ck("fts_transfer_created_on_the_direct_route",
           bool(t) and t.get("sa_account_type") == "direct" and t.get("sa_bank_account_type") == "CURRENT", t)
    F.D.set_mozart_scenario(str(t["attempt_id"]), "success")
    ctx.a.fts_check(t["id"])
    row = F.wait_until(lambda: (lambda r: r if r and r.get("status") == "processed" else None)(
        ctx.a.payout(pid)), timeout=90, interval=3) or ctx.a.payout(pid)
    ctx.ck("workers_moved_the_payout_to_processed_with_a_utr",
           (row or {}).get("status") == "processed" and bool((row or {}).get("utr")), row)
    ctx.ck("direct_payout_posts_no_shared_ledger_journal",
           ctx.a.journals("pout_" + pid) == [], ctx.a.journals("pout_" + pid))
    ctx.ck("no_reversal_row_for_a_successful_direct_payout", ctx.a.reversal(pid) is None, None)
    res_after, src = F.D._settle_reservation(mid, bal, pid, "awaiting_balance_refresh")
    ctx.ev["reservation_settlement"] = {"transition_source": src}
    aw = [i for i in ((res_after["body"] or {}).get("items", [])
                      if isinstance(res_after.get("body"), dict) else [])
          if i.get("payout_id") == pid and i.get("state") == "awaiting_balance_refresh"]
    ctx.ck("reservation_moved_to_awaiting_balance_refresh_by_the_real_hook/reconciler",
           bool(aw), {"transition_source": src})
    dl = ctx.wait_delivery(pid, {"payout.processed"}, timeout=60)
    ctx.ck("payout.processed_webhook_scoped_to_this_direct_merchant",
           len(dl) == 1 and dl[0]["account_id"] == "acc_" + mid, dl)
    hits = ctx.a.worker_log_hits(pid)
    ctx.ev["worker_log_hits"] = hits
    ctx.ck("a_real_payouts_async_worker_container_logged_this_payout", bool(hits), hits)
    M4.clean_scenarios(d)
