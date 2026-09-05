"""V10 -- Balance authorization: insufficient balance, queue_if_low_balance=false -> failed.

Covers: C15 (else-branch of V9).
"""
import pytest

from helpers import payouts_flow as pf


@pytest.mark.spec_id("V10")
@pytest.mark.status("END_TO_END_CONFIRMED")
def test_insufficient_balance_fails_when_queue_if_low_balance_false(
    ps_public_client, payouts_mysql, ledger_pg, merchant_m1
):
    if not merchant_m1["fund_account_id"] or not merchant_m1["account_number"]:
        pytest.skip("missing fixture: ARENA_M1_FUND_ACCOUNT_ID / ARENA_M1_ACCOUNT_NUMBER")

    passport_jwt = pf.passport_or_skip(merchant_m1)
    balance_before = pf.get_account_balance(ledger_pg, merchant_m1["merchant_id"])
    if balance_before is None:
        pytest.skip("missing fixture: no ledger accounts row seeded for ARENA_M1_MERCHANT_ID")

    amount = int(float(balance_before)) + 100000

    body = pf.build_create_body(
        merchant_m1["fund_account_id"], merchant_m1["account_number"], amount=amount, queue_if_low_balance=False, mode="NEFT"
    )
    resp = pf.create_payout(ps_public_client, passport_jwt, body)
    assert resp.status in (200, 201), "create failed: %s" % resp
    payout_id = resp.json()["id"]

    payout_row = pf.get_payout_row(payouts_mysql, payout_id)
    assert payout_row["status"] == "failed", "expected status failed, got %r" % payout_row["status"]
    assert not payout_row.get("queued_reason"), "failed payout must not carry a queued_reason"

    balance_after = pf.get_account_balance(ledger_pg, merchant_m1["merchant_id"])
    assert float(balance_after) == float(balance_before), "balance must be unchanged for a failed payout"
