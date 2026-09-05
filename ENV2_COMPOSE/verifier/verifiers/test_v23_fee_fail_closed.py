"""V23: a pricing 500 returns an error before dispatch or debit.

The retained row is create_request_submitted at this boundary. Pricing transport
errors are retryable (processor/payout_pricing.go:456); this is not an assertion
that the payout has permanently failed or cannot recover after fault removal.
"""
import pytest
from helpers import db,payouts_flow as pf

@pytest.mark.spec_id("V23")
@pytest.mark.status("CROSS_SERVICE_CONFIRMED")
def test_pricing_500_rejects_payout(ps_public_client,payouts_mysql,monolith_stub_client,merchant_m1,ledger_pg,fts_mysql):
    mid=merchant_m1["merchant_id"]
    before=pf.get_account_balance(ledger_pg,mid)
    pf.fault(monolith_stub_client,merchant_m1,status=500,path="/v1/payouts_service/fetch_pricing_info")
    try:
        body=pf.build_create_body(merchant_m1["fund_account_id"],merchant_m1["account_number"],100)
        response=pf.create_payout(ps_public_client,pf.passport_or_skip(merchant_m1),body)
        assert response.status==500,response
        assert pf.fault_hits(monolith_stub_client,merchant_m1), "actual pricing call did not hit fault"
        rows=db.fetchall(payouts_mysql,"SELECT id,status,transaction_id FROM payouts WHERE merchant_id=%s",(mid,))
        assert len(rows)==1,rows
        assert rows[0]["status"]=="create_request_submitted",rows
        assert not rows[0]["transaction_id"],rows
        assert pf.get_account_balance(ledger_pg,mid)==before
        assert db.fetchone(fts_mysql,"SELECT count(*) AS c FROM transfers WHERE merchant_id=%s",(mid,))["c"]==0
        assert db.fetchone(ledger_pg,"SELECT count(*) AS c FROM journal WHERE transactor_id=%s",('pout_'+rows[0]['id'],))["c"]==0
    finally: pf.fault(monolith_stub_client,merchant_m1,clear=True)
