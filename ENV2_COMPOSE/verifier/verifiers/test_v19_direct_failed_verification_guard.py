"""V19: adapter-boundary guard with an explicit transaction-link SQL fixture.
Statement matching is outside this check; it does not claim a full bank route.
"""
import pytest
from helpers import db,payouts_flow as pf,trace

@pytest.mark.spec_id("V19")
@pytest.mark.status("CROSS_SERVICE_CONFIRMED")
def test_transaction_id_set_blocks_failed_transition(ps_public_client,ps_internal_client,payouts_mysql,merchant_m2,fts_mysql):
    pid=pf.seed_payout(ps_public_client,merchant_m2)
    pf.wait_for_initiated(payouts_mysql,pid)
    pf.wait_for_held_handoff(payouts_mysql,fts_mysql,pid)
    fixture_transaction="LINKEDARENA001"
    db.execute(payouts_mysql,"UPDATE payouts SET transaction_id=%s WHERE id=%s",(fixture_transaction,pf.db_id(pid)))
    trace.record("explicit_fixture_mutation",{"payout_id":pid,"transaction_id":fixture_transaction,"scope":"transaction-link guard only; not statement matching"})
    before=pf.get_payout_row(payouts_mysql,pid)
    assert before["transaction_id"]==fixture_transaction
    response=pf.inject_transfer_webhook(ps_internal_client,fts_mysql,pid,"failed",failure_reason="TEST_BANK_DECLINE",bank_status_code="91")
    assert response.status==500,response
    assert response.json()["error"]["code"]=="server_error",response
    after=pf.get_payout_row(payouts_mysql,pid)
    assert after["status"]=="initiated", after
    assert after["transaction_id"]==fixture_transaction
