"""V5: initiated journal balances exactly and debits amount plus fees, which include tax."""
from decimal import Decimal
import pytest
from helpers import payouts_flow as pf

@pytest.mark.spec_id("V5")
@pytest.mark.status("END_TO_END_CONFIRMED")
def test_payout_initiated_journal_sums_to_zero_and_debits_merchant(ps_public_client,payouts_mysql,ledger_pg,merchant_m1,fts_mysql):
    before=pf.get_account_balance(ledger_pg,merchant_m1["merchant_id"])
    assert before is not None
    pid=pf.seed_payout(ps_public_client,merchant_m1,10000)
    payout=pf.get_payout_row(payouts_mysql,pid)
    journal=pf.get_ledger_journal_row(ledger_pg,pid,"payout_initiated")
    assert journal is not None
    entries=pf.get_ledger_entries(ledger_pg,journal["id"])
    assert pf.ledger_entries_sum_to_zero(entries), entries
    expected=Decimal(payout["amount"])+Decimal(payout["fees"] or 0)
    debit=sum((Decimal(str(e["amount"])) for e in entries if e["type"].lower()=="debit"),Decimal(0))
    assert debit==expected, (debit,expected,entries)
    after=pf.get_account_balance(ledger_pg,merchant_m1["merchant_id"])
    assert before-after==expected, (before,after,expected)
    pf.wait_for_held_handoff(payouts_mysql,fts_mysql,pid)
