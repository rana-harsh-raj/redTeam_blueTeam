"""V8: real Direct bank failure creates no reversal or Ledger journal."""
import pytest
from helpers import db,payouts_flow as pf

@pytest.mark.spec_id("V8")
@pytest.mark.status("END_TO_END_CONFIRMED")
def test_payout_failed_direct_account_no_reversal_no_ledger(ps_public_client,ps_internal_client,payouts_mysql,ledger_pg,merchant_m2,fts_mysql):
    before=db.fetchone(ledger_pg,"SELECT count(*) AS c FROM journal WHERE merchant_id=%s",(merchant_m2["merchant_id"],))["c"]
    pid=pf.seed_payout(ps_public_client,merchant_m2,5000)
    pf.wait_for_status(payouts_mysql,pid,"failed")
    assert pf.transfer_metadata(fts_mysql,pid)["status"]=="FAILED"
    assert pf.get_reversal_row(payouts_mysql,pid) is None
    after=db.fetchone(ledger_pg,"SELECT count(*) AS c FROM journal WHERE merchant_id=%s",(merchant_m2["merchant_id"],))["c"]
    assert after==before, "Direct failure unexpectedly touched Ledger"
