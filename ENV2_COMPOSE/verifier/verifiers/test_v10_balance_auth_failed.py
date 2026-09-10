"""V10/I20: synchronous insufficient-balance create is rejected with 400 and no debit."""
import pytest
from helpers import db,payouts_flow as pf

@pytest.mark.spec_id("V10")
@pytest.mark.status("END_TO_END_CONFIRMED")
def test_insufficient_balance_fails_when_queue_if_low_balance_false(ps_public_client,payouts_mysql,ledger_pg,merchant_m1,fts_mysql):
    before=pf.get_account_balance(ledger_pg,merchant_m1["merchant_id"])
    assert before is not None
    body=pf.build_create_body(merchant_m1["fund_account_id"],merchant_m1["account_number"],int(before)+100000,mode="NEFT",queue_if_low_balance=False)
    response=pf.create_payout(ps_public_client,pf.passport_or_skip(merchant_m1),body)
    assert response.status==400,response
    assert "balance" in response.text.lower(),response
    assert pf.get_account_balance(ledger_pg,merchant_m1["merchant_id"])==before
    assert db.fetchone(fts_mysql,"SELECT count(*) AS c FROM transfers WHERE merchant_id=%s",(merchant_m1["merchant_id"],))["c"]==0
