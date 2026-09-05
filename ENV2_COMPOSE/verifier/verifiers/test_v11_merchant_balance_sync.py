"""V11 -- MerchantBalance debit is synchronous with the API response.

Covers: C15 ("MerchantBalance always synchronous"). Deliberately does NOT use
wait_until -- a single immediate read is the point of this verifier; a retry
loop would mask an async regression.
"""
import pytest

from helpers import payouts_flow as pf


@pytest.mark.spec_id("V11")
@pytest.mark.status("END_TO_END_CONFIRMED")
def test_merchant_balance_debited_synchronously(ps_public_client, ledger_pg, merchant_m1):
    if not merchant_m1["fund_account_id"] or not merchant_m1["account_number"]:
        pytest.skip("missing fixture: ARENA_M1_FUND_ACCOUNT_ID / ARENA_M1_ACCOUNT_NUMBER")

    passport_jwt = pf.passport_or_skip(merchant_m1)
    balance_before = pf.get_account_balance(ledger_pg, merchant_m1["merchant_id"])
    if balance_before is None:
        pytest.skip("missing fixture: no ledger accounts row seeded for ARENA_M1_MERCHANT_ID")

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
    assert float(balance_before) - float(balance_after) >= amount - 1e-6, (
        "MerchantBalance was not debited synchronously: before=%s after=%s expected_drop>=%s"
        % (balance_before, balance_after, amount)
    )
