"""V8 -- Ledger accounting: payout_failed (Direct account, no reversal entity,
no ledger journal at all).

Covers: C19, Invariant 1 -- the Direct-account failure branch, contrasted
with V7's Shared-account branch.
"""
import warnings

import pytest

from helpers import payouts_flow as pf


@pytest.mark.spec_id("V8")
@pytest.mark.status("CROSS_SERVICE_CONFIRMED")  # synthetic webhook stimulus; see VERIFIER_SPEC.md V8
def test_payout_failed_direct_account_no_reversal_no_ledger(
    ps_public_client, ps_internal_client, payouts_mysql, ledger_pg, merchant_m2
):
    if not merchant_m2["fund_account_id"] or not merchant_m2["account_number"]:
        pytest.skip("missing fixture: ARENA_M2_FUND_ACCOUNT_ID / ARENA_M2_ACCOUNT_NUMBER")

    passport_jwt = pf.passport_or_skip(merchant_m2)
    body = pf.build_create_body(merchant_m2["fund_account_id"], merchant_m2["account_number"], amount=5000)
    resp = pf.create_payout(ps_public_client, passport_jwt, body)
    assert resp.status in (200, 201), "create failed: %s" % resp
    payout_id = resp.json()["id"]

    payout_row = pf.wait_for_initiated(payouts_mysql, payout_id)
    if payout_row["status"] not in ("created", "initiated"):
        pytest.skip("payout did not reach a pre-terminal state suitable for the failed stimulus: %r" % payout_row)

    fts_transfer_id = payout_row.get("fts_transfer_id") or 1
    webhook_resp = pf.send_transfer_status_webhook(
        ps_internal_client, payout_id, "failed", fund_transfer_id=fts_transfer_id,
        failure_reason="TEST_BANK_DECLINE", bank_status_code="91",
    )
    assert webhook_resp.status == 200, "transfer_status_webhook call itself failed: %s" % webhook_resp

    payout_row = pf.get_payout_row(payouts_mysql, payout_id)
    assert payout_row["status"] == "failed", (
        "Direct/RBL failed webhook must stay 'failed' (FtsToPayoutStatusMap['direct']['RBL']['failed']='failed'), "
        "got %r" % payout_row["status"]
    )

    reversal_row = pf.get_reversal_row(payouts_mysql, payout_id)
    assert reversal_row is None, "Direct-account failure must not create a reversals row"

    journal_count = pf.count_ledger_journal_rows(ledger_pg, payout_id, "payout_reversed")
    journal_count += pf.count_ledger_journal_rows(ledger_pg, payout_id, "payout_failed")
    assert journal_count == 0, "Direct-account failure must not touch Ledger at all"

    warnings.warn(
        "V8 reached failed via a synthetic transfer_status_webhook call, not a real mozart-mock round trip"
    )
