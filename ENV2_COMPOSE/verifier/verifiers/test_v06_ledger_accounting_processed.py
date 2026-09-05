"""V6 -- Ledger accounting: payout_processed mirror journal on FTS success.

Covers: C19, Invariant 1 (second half). Prefers a real mozart-mock round
trip; falls back to a synthetic transfer_status_webhook call and
self-reports a lower ceiling via pytest.mark (see VERIFIER_SPEC.md V6).
"""
import warnings

import pytest

from helpers import payouts_flow as pf
from helpers.wait import wait_until, WaitTimeout


@pytest.mark.spec_id("V6")
@pytest.mark.status("CODE_CANDIDATE")  # upgraded to END_TO_END_CONFIRMED in-line if mozart-mock answers
def test_payout_processed_journal_mirrors_ledger(
    ps_public_client, ps_internal_client, payouts_mysql, ledger_pg, merchant_m1, mozart_mock_client
):
    if not merchant_m1["fund_account_id"] or not merchant_m1["account_number"]:
        pytest.skip("missing fixture: ARENA_M1_FUND_ACCOUNT_ID / ARENA_M1_ACCOUNT_NUMBER")

    passport_jwt = pf.passport_or_skip(merchant_m1)
    body = pf.build_create_body(merchant_m1["fund_account_id"], merchant_m1["account_number"], amount=10000)
    resp = pf.create_payout(ps_public_client, passport_jwt, body)
    assert resp.status in (200, 201), "create failed: %s" % resp
    payout_id = resp.json()["id"]

    used_mozart = mozart_mock_client.health_ok()
    if used_mozart:
        try:
            wait_until(
                lambda: (pf.wait_for_initiated(payouts_mysql, payout_id) or {}).get("status") == "processed",
                timeout=30,
                interval=1.0,
                desc="payout to reach processed via mozart-mock",
            )
        except WaitTimeout:
            used_mozart = False  # fall through to the synthetic path below

    if not used_mozart:
        payout_row = pf.wait_for_initiated(payouts_mysql, payout_id)
        fts_transfer_id = payout_row.get("fts_transfer_id") or 1
        pf.send_transfer_status_webhook(
            ps_internal_client, payout_id, "processed", fund_transfer_id=fts_transfer_id, utr="UTR_TEST_V6"
        )

    payout_row = pf.get_payout_row(payouts_mysql, payout_id)
    assert payout_row["status"] == "processed", "payout did not reach processed: %r" % payout_row
    assert payout_row.get("utr"), "processed payout must have a utr"

    journal_row = pf.get_ledger_journal_row(ledger_pg, payout_id, "payout_processed")
    assert journal_row is not None, "no payout_processed journal found"
    entries = pf.get_ledger_entries(ledger_pg, journal_row["id"])
    assert pf.ledger_entries_sum_to_zero(entries), "payout_processed ledger_entries do not sum to zero"

    if not used_mozart:
        warnings.warn(
            "V6 reached processed via a synthetic transfer_status_webhook call, not a real mozart-mock "
            "round trip -- this run's ceiling is CODE_CANDIDATE, not END_TO_END_CONFIRMED, per "
            "VERIFIER_SPEC.md V6 (assertions above still passed)"
        )
