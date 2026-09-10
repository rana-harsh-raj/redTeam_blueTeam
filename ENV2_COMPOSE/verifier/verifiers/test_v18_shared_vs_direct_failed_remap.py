"""V18: real bank decline maps Shared to reversed and Direct/RBL to failed."""
import pytest
from helpers import payouts_flow as pf

@pytest.mark.spec_id("V18")
@pytest.mark.status("END_TO_END_CONFIRMED")
def test_failed_webhook_remaps_differently_by_account_type(ps_public_client,ps_internal_client,payouts_mysql,merchant_m1,merchant_m2,fts_mysql):
    p1=pf.seed_payout(ps_public_client,merchant_m1)
    p2=pf.seed_payout(ps_public_client,merchant_m2)
    pf.wait_for_status(payouts_mysql,p1,"reversed")
    pf.wait_for_status(payouts_mysql,p2,"failed")
    assert pf.transfer_metadata(fts_mysql,p1)["status"]=="FAILED"
    assert pf.transfer_metadata(fts_mysql,p2)["status"]=="FAILED"
    assert pf.get_reversal_row(payouts_mysql,p1) is not None
    assert pf.get_reversal_row(payouts_mysql,p2) is None
