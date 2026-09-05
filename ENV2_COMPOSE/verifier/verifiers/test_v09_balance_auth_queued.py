"""V9 -- Balance authorization: insufficient balance, queue_if_low_balance=true -> queued.

Covers: C15, Flow E, golden flow G3.
"""
import pytest

from helpers import payouts_flow as pf


@pytest.mark.spec_id("V9")
@pytest.mark.status("END_TO_END_CONFIRMED")
def test_insufficient_balance_queues_when_queue_if_low_balance_true(
    ps_public_client, payouts_mysql, ledger_pg, merchant_m1
):
    if not merchant_m1["fund_account_id"] or not merchant_m1["account_number"]:
        pytest.fail("missing fixture: ARENA_M1_FUND_ACCOUNT_ID / ARENA_M1_ACCOUNT_NUMBER")

    passport_jwt = pf.passport_or_skip(merchant_m1)
    balance_before = pf.get_account_balance(ledger_pg, merchant_m1["merchant_id"])
    if balance_before is None:
        pytest.fail("missing fixture: no ledger accounts row seeded for ARENA_M1_MERCHANT_ID")

    amount = int(balance_before) + 100000  # deliberately over balance

    body = pf.build_create_body(
        merchant_m1["fund_account_id"], merchant_m1["account_number"], amount=amount, queue_if_low_balance=True, mode="NEFT"
    )
    resp = pf.create_payout(ps_public_client, passport_jwt, body)
    assert resp.status in (200, 201), "create failed: %s" % resp
    payout_id = resp.json()["id"]

    payout_row = pf.get_payout_row(payouts_mysql, payout_id)
    assert payout_row["status"] == "queued", "expected status queued, got %r" % payout_row["status"]
    assert payout_row["queued_reason"] == "low_balance", (
        "expected queued_reason low_balance, got %r" % payout_row["queued_reason"]
    )

    balance_after = pf.get_account_balance(ledger_pg, merchant_m1["merchant_id"])
    assert balance_after == balance_before, "balance must be unchanged for a queued payout"
