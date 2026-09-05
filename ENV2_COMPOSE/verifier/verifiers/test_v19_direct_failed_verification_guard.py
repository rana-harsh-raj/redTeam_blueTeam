"""V19 -- Direct failed-verification: a linked transaction_id blocks the
'failed' transition (VerifyPayoutFailedTransaction).

Covers: C25 -- only the TransactionID-set half of the guard is reachable in
Env 2 (XAS/BAS statement matching is an Env 4 addition per the BOM), so this
verifier is capped at BLOCKED_BY_FIDELITY_GAP by design, not by a bug.
"""
import pytest

from helpers import db, payouts_flow as pf


@pytest.mark.spec_id("V19")
@pytest.mark.status("BLOCKED_BY_FIDELITY_GAP")
def test_transaction_id_set_blocks_failed_transition(
    ps_public_client, ps_internal_client, payouts_mysql, merchant_m2
):
    if not merchant_m2["fund_account_id"] or not merchant_m2["account_number"]:
        pytest.skip("missing fixture: ARENA_M2_FUND_ACCOUNT_ID / ARENA_M2_ACCOUNT_NUMBER")

    passport_jwt = pf.passport_or_skip(merchant_m2)
    body = pf.build_create_body(merchant_m2["fund_account_id"], merchant_m2["account_number"], amount=100)
    resp = pf.create_payout(ps_public_client, passport_jwt, body)
    assert resp.status in (200, 201), "create failed: %s" % resp
    payout_id = resp.json()["id"]

    payout_row = pf.wait_for_initiated(payouts_mysql, payout_id)
    if payout_row["status"] not in ("created", "initiated"):
        pytest.skip("payout not in a pre-terminal state suitable for this stimulus: %r" % payout_row)

    # Manufacture the "already linked to a transaction" precondition directly
    # -- standing in for a real XAS/BAS debit-statement match, which is out
    # of Env 2's closure (see VERIFIER_SPEC.md V19).
    db.execute(
        payouts_mysql, "UPDATE payouts SET transaction_id=%s WHERE id=%s", ("txn_TESTLINKED0001", payout_id)
    )

    fts_transfer_id = payout_row.get("fts_transfer_id") or 1
    pf.send_transfer_status_webhook(
        ps_internal_client, payout_id, "failed", fund_transfer_id=fts_transfer_id,
        failure_reason="TEST_BANK_DECLINE", bank_status_code="91",
    )

    payout_row_after = pf.get_payout_row(payouts_mysql, payout_id)
    assert payout_row_after["status"] != "failed", (
        "VerifyPayoutFailedTransaction should have refused to mark this payout failed once "
        "transaction_id was set, but status is now %r" % payout_row_after["status"]
    )
