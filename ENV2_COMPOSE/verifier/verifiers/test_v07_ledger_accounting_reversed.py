"""V7 -- Ledger accounting: payout_reversed mirror journal, amount includes fees.

Covers: C19, C24, Invariant 1. Shared-account (M1) payout, FTS webhook
reports "failed" -> FtsToPayoutStatusMap remaps to payout status "reversed".
"""
import warnings

import pytest

from helpers import payouts_flow as pf


@pytest.mark.spec_id("V7")
@pytest.mark.status("CODE_CANDIDATE")  # synthetic webhook stimulus; see VERIFIER_SPEC.md V7
def test_payout_reversed_journal_and_reversal_entity(
    ps_public_client, ps_internal_client, payouts_mysql, ledger_pg, merchant_m1
):
    if not merchant_m1["fund_account_id"] or not merchant_m1["account_number"]:
        pytest.skip("missing fixture: ARENA_M1_FUND_ACCOUNT_ID / ARENA_M1_ACCOUNT_NUMBER")

    passport_jwt = pf.passport_or_skip(merchant_m1)
    amount = 5000
    body = pf.build_create_body(merchant_m1["fund_account_id"], merchant_m1["account_number"], amount=amount)
    resp = pf.create_payout(ps_public_client, passport_jwt, body)
    assert resp.status in (200, 201), "create failed: %s" % resp
    payout_id = resp.json()["id"]

    payout_row = pf.wait_for_initiated(payouts_mysql, payout_id)
    if payout_row["status"] not in ("created", "initiated"):
        pytest.skip("payout did not reach a pre-terminal state suitable for the reversal stimulus: %r" % payout_row)

    fts_transfer_id = payout_row.get("fts_transfer_id") or 1
    webhook_resp = pf.send_transfer_status_webhook(
        ps_internal_client,
        payout_id,
        "failed",
        fund_transfer_id=fts_transfer_id,
        failure_reason="TEST_BANK_DECLINE",
        bank_status_code="91",
    )
    assert webhook_resp.status == 200, "transfer_status_webhook call itself failed: %s" % webhook_resp

    payout_row = pf.get_payout_row(payouts_mysql, payout_id)
    assert payout_row["status"] == "reversed", (
        "Shared-account failed webhook must remap to payout status 'reversed' "
        "(FtsToPayoutStatusMap['shared']['default']['failed']='reversed'), got %r" % payout_row["status"]
    )

    reversal_row = pf.get_reversal_row(payouts_mysql, payout_id)
    assert reversal_row is not None, "no reversals row created"
    expected_amount = amount + (payout_row.get("fees") or 0)
    assert reversal_row["amount"] == expected_amount, (
        "reversal amount must equal payout.amount + payout.fees: got %s expected %s"
        % (reversal_row["amount"], expected_amount)
    )

    journal_row = pf.get_ledger_journal_row(ledger_pg, payout_id, "payout_reversed")
    assert journal_row is not None, "no payout_reversed journal found"
    entries = pf.get_ledger_entries(ledger_pg, journal_row["id"])
    assert pf.ledger_entries_sum_to_zero(entries), "payout_reversed ledger_entries do not sum to zero"

    assert str(reversal_row.get("transaction_id")) == str(journal_row["id"]), (
        "reversals.transaction_id must equal the ledger journal id"
    )

    warnings.warn(
        "V7 reached reversed via a synthetic transfer_status_webhook call, not a real mozart-mock "
        "scripted-failure round trip -- this run's ceiling is CODE_CANDIDATE per VERIFIER_SPEC.md V7"
    )
