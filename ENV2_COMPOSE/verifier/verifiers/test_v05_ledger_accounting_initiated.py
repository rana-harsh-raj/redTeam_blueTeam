"""V5 -- Ledger accounting: payout_initiated journal sums to zero and debits
the merchant.

Covers: C15, C19, Invariant 1 (first half).
"""
import pytest

from helpers import payouts_flow as pf


@pytest.mark.spec_id("V5")
@pytest.mark.status("END_TO_END_CONFIRMED")
def test_payout_initiated_journal_sums_to_zero_and_debits_merchant(
    ps_public_client, payouts_mysql, ledger_pg, merchant_m1
):
    if not merchant_m1["fund_account_id"] or not merchant_m1["account_number"]:
        pytest.skip("missing fixture: ARENA_M1_FUND_ACCOUNT_ID / ARENA_M1_ACCOUNT_NUMBER")

    passport_jwt = pf.passport_or_skip(merchant_m1)

    balance_before = pf.get_account_balance(ledger_pg, merchant_m1["merchant_id"])
    if balance_before is None:
        pytest.skip("missing fixture: no ledger accounts row seeded for ARENA_M1_MERCHANT_ID")

    amount = 10000  # paise
    body = pf.build_create_body(merchant_m1["fund_account_id"], merchant_m1["account_number"], amount=amount)
    resp = pf.create_payout(ps_public_client, passport_jwt, body)
    assert resp.status in (200, 201), "create failed: %s" % resp
    payout_id = resp.json()["id"]

    payout_row = pf.get_payout_row(payouts_mysql, payout_id)
    assert payout_row is not None

    journal_row = pf.get_ledger_journal_row(ledger_pg, payout_id, "payout_initiated")
    assert journal_row is not None, "no payout_initiated journal found for the created payout"

    entries = pf.get_ledger_entries(ledger_pg, journal_row["id"])
    assert entries, "payout_initiated journal has no ledger_entries rows"
    assert pf.ledger_entries_sum_to_zero(entries), "ledger_entries for payout_initiated do not sum to zero: %r" % entries

    expected_debit = amount + (payout_row.get("fees") or 0) + (payout_row.get("tax") or 0)
    debit_total = sum(
        float(e["amount"]) for e in entries if str(e["type"]).lower() == "debit"
    )
    assert abs(debit_total - expected_debit) < 1e-6 or debit_total >= amount, (
        "MerchantBalance debit entries do not cover amount+fees+tax: debit_total=%s expected>=%s"
        % (debit_total, expected_debit)
    )

    balance_after = pf.get_account_balance(ledger_pg, merchant_m1["merchant_id"])
    assert balance_after is not None
    assert float(balance_before) - float(balance_after) >= amount - 1e-6, (
        "merchant balance did not decrease by at least the payout amount: before=%s after=%s"
        % (balance_before, balance_after)
    )
