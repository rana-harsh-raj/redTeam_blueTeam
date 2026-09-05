"""V20 -- Tenant isolation: another merchant cannot fetch or cancel a payout.

Covers: C6, Invariant 4.
"""
import pytest

from helpers import payouts_flow as pf


@pytest.mark.spec_id("V20")
@pytest.mark.status("END_TO_END_CONFIRMED")
def test_other_merchant_cannot_fetch_or_cancel(
    ps_public_client, payouts_mysql, merchant_m1, merchant_m2
):
    if not merchant_m1["fund_account_id"] or not merchant_m1["account_number"]:
        pytest.skip("missing fixture: ARENA_M1_FUND_ACCOUNT_ID / ARENA_M1_ACCOUNT_NUMBER")

    m1_jwt = pf.passport_or_skip(merchant_m1)
    m2_jwt = pf.passport_or_skip(merchant_m2)

    body = pf.build_create_body(
        merchant_m1["fund_account_id"], merchant_m1["account_number"], amount=100, queue_if_low_balance=True, mode="NEFT"
    )
    resp = pf.create_payout(ps_public_client, m1_jwt, body)
    assert resp.status in (200, 201), "M1 create failed: %s" % resp
    m1_payout_id = resp.json()["id"]

    m1_row = pf.get_payout_row(payouts_mysql, m1_payout_id)
    if m1_row["status"] not in ("queued", "scheduled", "on_hold"):
        pytest.skip(
            "M1 seed payout did not land in a cancellable state (%r) -- cannot run the cancel half of "
            "this negative test meaningfully" % m1_row["status"]
        )

    fetch_resp = ps_public_client.get("/v1/payouts/%s" % m1_payout_id, passport_jwt=m2_jwt)
    assert fetch_resp.status in (400, 404), (
        "M2 must not be able to fetch M1's payout via the public route, got %s: %s"
        % (fetch_resp.status, fetch_resp.text)
    )

    cancel_resp = ps_public_client.post(
        "/v1/payouts/cancel_payout/%s" % m1_payout_id, body={}, passport_jwt=m2_jwt
    )
    assert cancel_resp.status in (400, 404), (
        "M2 must not be able to cancel M1's payout via the public route, got %s: %s"
        % (cancel_resp.status, cancel_resp.text)
    )

    m1_row_after = pf.get_payout_row(payouts_mysql, m1_payout_id)
    assert m1_row_after["status"] == m1_row["status"], "M1's payout status changed after M2's cancel attempt"
