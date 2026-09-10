"""V24 -- Scheduled/queued cron dequeue.

Covers: C17, C32, Flow E. Calls the documented cron HTTP endpoints directly
(payouts/internal/routing/router/cron_routes.go) rather than waiting on a
real external scheduler -- confirms dequeue *logic*, not cadence (cadence
itself stays CODE_CANDIDATE per C39/C59).
"""
import time

import pytest

from helpers import db, payouts_flow as pf
from helpers.wait import wait_until, WaitTimeout


@pytest.mark.spec_id("V24")
@pytest.mark.status("END_TO_END_CONFIRMED")
def test_queued_low_balance_payout_dequeues_after_topup_and_cron(
    ps_public_client, ps_fastcron_client, payouts_mysql, ledger_pg, ledger_client, merchant_m1, monolith_stub_client, fts_mysql
):
    if not merchant_m1["fund_account_id"] or not merchant_m1["account_number"]:
        pytest.fail("missing fixture: ARENA_M1_FUND_ACCOUNT_ID / ARENA_M1_ACCOUNT_NUMBER")

    passport_jwt = pf.passport_or_skip(merchant_m1)
    balance_before = pf.get_account_balance(ledger_pg, merchant_m1["merchant_id"])
    if balance_before is None:
        pytest.fail("missing fixture: no ledger accounts row seeded for ARENA_M1_MERCHANT_ID")

    amount = int(balance_before) + 500
    body = pf.build_create_body(
        # Exercise dequeue independently of NEFT's bank-window scheduling.
        merchant_m1["fund_account_id"], merchant_m1["account_number"], amount=amount, queue_if_low_balance=True, mode="IMPS"
    )
    resp = pf.create_payout(ps_public_client, passport_jwt, body)
    assert resp.status in (200, 201), "create failed: %s" % resp
    payout_id = resp.json()["id"]

    payout_row = pf.get_payout_row(payouts_mysql, payout_id)
    if payout_row["status"] != "queued" or payout_row.get("queued_reason") != "low_balance":
        pytest.fail("seed payout did not land in queued/low_balance: %r" % payout_row)

    # Top up the account directly, simulating a BalanceRefreshEvent / manual
    # credit, recent enough to fall inside the cron's "changed in last 6h" scan.
    topup = pf.ledger_topup(ledger_client, merchant_m1, amount)
    assert topup.status in (200, 201), "ledger top-up journal failed: %s" % topup

    sync = monolith_stub_client.post("/_arena/balance-sync",body={"balance_ids":[merchant_m1["balance_id"]],"deliver_event":False})
    assert sync.status==200, sync
    cron_resp = ps_fastcron_client.post("/v1/cron/process_queued_low_balance_payouts", body={"balance_ids": [merchant_m1["balance_id"]]})
    assert cron_resp.status in (200, 201), "cron call failed: %s" % cron_resp

    try:
        final_row = wait_until(
            lambda: (lambda r: r if r and r["status"] == "initiated" else None)(
                pf.get_payout_row(payouts_mysql, payout_id)
            ),
            timeout=20,
            interval=1.0,
            desc="queued payout to dequeue after cron call",
        )
    except WaitTimeout as exc:
        pytest.fail("payout never left 'queued' after topping up balance and calling the dequeue cron: %s" % exc)

    assert final_row["status"] == "initiated", (
        "expected the payout to have progressed out of queued, got %r" % final_row["status"]
    )
    _assert_transfer_details(payouts_mysql, fts_mysql, payout_id)


@pytest.mark.spec_id("V24")
@pytest.mark.status("END_TO_END_CONFIRMED")
def test_scheduled_payout_dequeues_after_slot_and_cron(
    ps_public_client, ps_fastcron_client, payouts_mysql, merchant_m1, fts_mysql
):
    if not merchant_m1["fund_account_id"] or not merchant_m1["account_number"]:
        pytest.fail("missing fixture: ARENA_M1_FUND_ACCOUNT_ID / ARENA_M1_ACCOUNT_NUMBER")

    passport_jwt = pf.passport_or_skip(merchant_m1)
    # payouts validates scheduled_at against real rules (internal/app/payouts/schedule.go): strictly after
    # the end of the current IST hour, within SCHEDULED_AT_MONTHS_ALLOWED, and the IST hour must be one of
    # the allowed slots {9, 13, 17, 21}; the stored value is truncated to the start of that hour.
    scheduled_at = _next_allowed_slot_ist()
    body = pf.build_create_body(
        merchant_m1["fund_account_id"], merchant_m1["account_number"], amount=100,
        extra={"scheduled_at": scheduled_at},
    )
    resp = pf.create_payout(ps_public_client, passport_jwt, body)
    assert resp.status in (200, 201), "create failed: %s" % resp
    payout_id = resp.json()["id"]

    payout_row = pf.get_payout_row(payouts_mysql, payout_id)
    if payout_row["status"] != "scheduled":
        pytest.fail("seed payout did not land in scheduled: %r" % payout_row)

    # Arena-only time travel: the real slot is hours away; move the stored scheduled_at into the past
    # (what the passage of time would do) so the dispatch cron sees it as due. Nothing else changes.
    db.execute(payouts_mysql, "UPDATE payouts SET scheduled_at=%s WHERE id=%s", (int(time.time()) - 60, pf.db_id(payout_id)))
    scheduled_at = int(time.time()) - 60

    time.sleep(max(0, scheduled_at - int(time.time())) + 2)

    # Negative check: still scheduled immediately after the slot passes, before
    # the cron endpoint is called -- PS does not self-dispatch on a timer.
    still_scheduled_row = pf.get_payout_row(payouts_mysql, payout_id)
    assert still_scheduled_row["status"] == "scheduled", (
        "payout left 'scheduled' before the dispatch cron was ever called: %r" % still_scheduled_row
    )

    cron_resp = ps_fastcron_client.post("/v1/cron/process_scheduled_payouts", body={"balance_ids": [merchant_m1["balance_id"]]})
    assert cron_resp.status in (200, 201), "cron call failed: %s" % cron_resp

    try:
        final_row = wait_until(
            lambda: (lambda r: r if r and r["status"] == "initiated" else None)(
                pf.get_payout_row(payouts_mysql, payout_id)
            ),
            timeout=20,
            interval=1.0,
            desc="scheduled payout to dispatch after cron call",
        )
    except WaitTimeout as exc:
        pytest.fail("payout never left 'scheduled' after the slot passed and the dispatch cron was called: %s" % exc)

    assert final_row["status"] == "initiated", (
        "expected the payout to have progressed out of scheduled, got %r" % final_row["status"]
    )
    _assert_transfer_details(payouts_mysql, fts_mysql, payout_id)


def _assert_transfer_details(payouts_mysql, fts_mysql, payout_id):
    # Retain actual bank response, transfer identity and channel before cleanup.
    pf.wait_for_held_handoff(payouts_mysql,fts_mysql,payout_id)


def _next_allowed_slot_ist():
    """Epoch seconds of the next IST hour in payouts' allowed slots {9,13,17,21} that is strictly after
    the end of the current IST hour."""
    ist_offset = 5 * 3600 + 30 * 60
    now_ist = int(time.time()) + ist_offset
    end_of_hour_ist = (now_ist // 3600 + 1) * 3600
    day_start = (now_ist // 86400) * 86400
    for day in range(0, 3):
        for hour in (9, 13, 17, 21):
            slot = day_start + day * 86400 + hour * 3600
            if slot > end_of_hour_ist:
                return slot - ist_offset
    raise AssertionError("no allowed slot found")
