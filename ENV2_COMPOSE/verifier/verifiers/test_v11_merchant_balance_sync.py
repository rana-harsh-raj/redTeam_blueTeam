"""V11 -- MerchantBalance debit is synchronous with the API response.

Covers: C15 ("MerchantBalance always synchronous"). The balance assertion uses
a single immediate read; a retry would mask an async regression. Only after
that assertion does the verifier wait for held dispatch before fixture cleanup.
"""
import pytest

from helpers import payouts_flow as pf


@pytest.mark.spec_id("V11")
@pytest.mark.status("END_TO_END_CONFIRMED")
def test_merchant_balance_debited_synchronously(ps_public_client, ledger_pg, merchant_m1, payouts_mysql, fts_mysql):
    if not merchant_m1["fund_account_id"] or not merchant_m1["account_number"]:
        pytest.fail("missing fixture: ARENA_M1_FUND_ACCOUNT_ID / ARENA_M1_ACCOUNT_NUMBER")

    passport_jwt = pf.passport_or_skip(merchant_m1)
    balance_before = pf.get_account_balance(ledger_pg, merchant_m1["merchant_id"])
    if balance_before is None:
        pytest.fail("missing fixture: no ledger accounts row seeded for ARENA_M1_MERCHANT_ID")

    amount = 100
    body = pf.build_create_body(merchant_m1["fund_account_id"], merchant_m1["account_number"], amount=amount)
    resp = pf.create_payout(ps_public_client, passport_jwt, body)
    assert resp.status in (200, 201), "create failed: %s" % resp

    # No sleep, no wait_until: the assertion is that the debit is already
    # visible the instant the HTTP response returns, because the Ledger
    # Journal.Create Twirp call is synchronous inside the create request
    # (Flow A/B sequence diagrams).
    balance_after = pf.get_account_balance(ledger_pg, merchant_m1["merchant_id"])
    assert balance_after is not None
    expected = amount + resp.json()["fees"]
    assert balance_before - balance_after == expected, (balance_before,balance_after,expected)
    # Completion is after the immediate debit assertion, preserving its timing.
    pf.wait_for_held_handoff(payouts_mysql,fts_mysql,resp.json()["id"])
