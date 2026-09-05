"""V7: bank decline traverses FTS and Shared reversal with an exact fee-inclusive credit."""
from decimal import Decimal
import pytest
from helpers import payouts_flow as pf
from helpers.wait import wait_until

@pytest.mark.spec_id("V7")
@pytest.mark.status("END_TO_END_CONFIRMED")
def test_payout_reversed_journal_and_reversal_entity(ps_public_client,ps_internal_client,payouts_mysql,ledger_pg,merchant_m1,fts_mysql):
    before=pf.get_account_balance(ledger_pg,merchant_m1["merchant_id"])
    pid=pf.seed_payout(ps_public_client,merchant_m1,5000)
    payout=pf.wait_for_status(payouts_mysql,pid,"reversed")
    assert pf.transfer_metadata(fts_mysql,pid)["status"]=="FAILED"
    reversal=pf.get_reversal_row(payouts_mysql,pid)
    assert reversal and reversal["amount"]==payout["amount"]+(payout["fees"] or 0)
    journal=wait_until(lambda:pf.reversal_journal(ledger_pg,payouts_mysql,pid),timeout=20,interval=.5,desc="reversal journal")
    assert pf.ledger_entries_sum_to_zero(pf.get_ledger_entries(ledger_pg,journal["id"]))
    wait_until(lambda:pf.db_id(pf.get_reversal_row(payouts_mysql,pid)["transaction_id"])==journal["id"],timeout=15,interval=.5,desc="reversal transaction link")
    assert pf.get_account_balance(ledger_pg,merchant_m1["merchant_id"])==before
