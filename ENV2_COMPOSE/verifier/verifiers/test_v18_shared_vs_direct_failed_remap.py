"""V18 -- Shared-account remap: FTS 'failed' -> payout 'reversed', contrasted
with Direct/RBL 'failed' -> payout 'failed' in a single run.

Covers: C19/C20 FtsToPayoutStatusMap account-type/channel branch.
"""
import warnings

import pytest

from helpers import payouts_flow as pf


@pytest.mark.spec_id("V18")
@pytest.mark.status("CROSS_SERVICE_CONFIRMED")
def test_failed_webhook_remaps_differently_by_account_type(
    ps_public_client, ps_internal_client, payouts_mysql, merchant_m1, merchant_m2
):
    if not merchant_m1["fund_account_id"] or not merchant_m1["account_number"]:
        pytest.skip("missing fixture: ARENA_M1_FUND_ACCOUNT_ID / ARENA_M1_ACCOUNT_NUMBER")
    if not merchant_m2["fund_account_id"] or not merchant_m2["account_number"]:
        pytest.skip("missing fixture: ARENA_M2_FUND_ACCOUNT_ID / ARENA_M2_ACCOUNT_NUMBER")

    m1_jwt = pf.passport_or_skip(merchant_m1)
    m2_jwt = pf.passport_or_skip(merchant_m2)

    m1_body = pf.build_create_body(merchant_m1["fund_account_id"], merchant_m1["account_number"], amount=100)
    m2_body = pf.build_create_body(merchant_m2["fund_account_id"], merchant_m2["account_number"], amount=100)

    m1_resp = pf.create_payout(ps_public_client, m1_jwt, m1_body)
    m2_resp = pf.create_payout(ps_public_client, m2_jwt, m2_body)
    assert m1_resp.status in (200, 201), "M1 create failed: %s" % m1_resp
    assert m2_resp.status in (200, 201), "M2 create failed: %s" % m2_resp
    m1_payout_id = m1_resp.json()["id"]
    m2_payout_id = m2_resp.json()["id"]

    m1_row = pf.get_payout_row(payouts_mysql, m1_payout_id)
    m2_row = pf.get_payout_row(payouts_mysql, m2_payout_id)
    if m1_row["status"] not in ("created", "initiated") or m2_row["status"] not in ("created", "initiated"):
        pytest.skip("one of the seed payouts did not reach a pre-terminal state: M1=%r M2=%r" % (
            m1_row["status"], m2_row["status"]
        ))

    for payout_id, row in ((m1_payout_id, m1_row), (m2_payout_id, m2_row)):
        pf.send_transfer_status_webhook(
            ps_internal_client, payout_id, "failed", fund_transfer_id=row.get("fts_transfer_id") or 1,
            failure_reason="TEST", bank_status_code="91",
        )

    m1_row_after = pf.get_payout_row(payouts_mysql, m1_payout_id)
    m2_row_after = pf.get_payout_row(payouts_mysql, m2_payout_id)

    assert m1_row_after["status"] == "reversed", "M1 (Shared) failed webhook must remap to reversed, got %r" % (
        m1_row_after["status"]
    )
    assert m2_row_after["status"] == "failed", "M2 (Direct/RBL) failed webhook must stay failed, got %r" % (
        m2_row_after["status"]
    )

    m1_reversal = pf.get_reversal_row(payouts_mysql, m1_payout_id)
    m2_reversal = pf.get_reversal_row(payouts_mysql, m2_payout_id)
    assert m1_reversal is not None, "M1 must have a reversals row"
    assert m2_reversal is None, "M2 must NOT have a reversals row"

    warnings.warn("V18 used a synthetic transfer_status_webhook stimulus, not mozart-mock")
