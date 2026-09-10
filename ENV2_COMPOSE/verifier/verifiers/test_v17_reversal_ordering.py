"""V17: observe payout/reversal commits while the actual Ledger request is delayed or unavailable."""
import threading
import pytest
from helpers import payouts_flow as pf,trace
from helpers.wait import wait_until

@pytest.mark.spec_id("V17")
@pytest.mark.status("CROSS_SERVICE_CONFIRMED")
def test_reversal_row_committed_before_ledger_call_baseline(ps_public_client,ps_internal_client,payouts_mysql,ledger_pg,merchant_m1,fts_mysql,ledger_gate_client):
    pid=pf.seed_payout(ps_public_client,merchant_m1)
    pf.wait_for_initiated(payouts_mysql,pid)
    pf.wait_for_held_handoff(payouts_mysql,fts_mysql,pid)
    pf.fault(ledger_gate_client,merchant_m1,delay_ms=2500,path="/twirp/rzp.ledger.journal.v1.JournalAPI/Create")
    result=[]
    def stimulate():
        try: result.append(pf.inject_transfer_webhook(ps_internal_client,fts_mysql,pid,"failed",failure_reason="TEST",bank_status_code="91"))
        except Exception as exc: result.append(exc)
    worker=threading.Thread(target=stimulate)
    try:
        worker.start()
        wait_until(lambda:pf.fault_hits(ledger_gate_client,merchant_m1),timeout=15,interval=.05,desc="actual delayed Ledger request")
        assert pf.get_payout_row(payouts_mysql,pid)["status"]=="reversed"
        assert pf.get_reversal_row(payouts_mysql,pid) is not None
        assert pf.reversal_journal(ledger_pg,payouts_mysql,pid) is None, "Ledger completed before ordering observation"
        trace.record("ordering_observation",{"payout_id":pid,"reversal_committed":True,"ledger_journal_present":False})
        worker.join(timeout=35)
        assert not worker.is_alive() and len(result)==1 and getattr(result[0],"status",None)==200,result
    finally:
        pf.fault(ledger_gate_client,merchant_m1,clear=True)
        worker.join(timeout=35)
    journal=wait_until(lambda:pf.reversal_journal(ledger_pg,payouts_mysql,pid),timeout=60,interval=1,desc="Ledger after delayed call")
    assert pf.ledger_entries_sum_to_zero(pf.get_ledger_entries(ledger_pg,journal["id"]))

@pytest.mark.spec_id("V17")
@pytest.mark.status("CROSS_SERVICE_CONFIRMED")
def test_reversal_survives_ledger_outage_via_async_retry(ps_public_client,ps_internal_client,payouts_mysql,ledger_pg,merchant_m1,fts_mysql,ledger_gate_client):
    pid=pf.seed_payout(ps_public_client,merchant_m1)
    pf.wait_for_initiated(payouts_mysql,pid)
    pf.wait_for_held_handoff(payouts_mysql,fts_mysql,pid)
    pf.fault(ledger_gate_client,merchant_m1,status=503,path="/twirp/rzp.ledger.journal.v1.JournalAPI/Create")
    try:
        response=pf.inject_transfer_webhook(ps_internal_client,fts_mysql,pid,"failed",failure_reason="TEST",bank_status_code="91")
        assert response.status==500,response
        assert response.json()["error"]["code"]=="server_error",response
        assert pf.fault_hits(ledger_gate_client,merchant_m1), "outage not hit"
        assert pf.get_payout_row(payouts_mysql,pid)["status"]=="reversed"
        assert pf.get_reversal_row(payouts_mysql,pid) is not None
        assert pf.reversal_journal(ledger_pg,payouts_mysql,pid) is None
    finally: pf.fault(ledger_gate_client,merchant_m1,clear=True)
    journal=wait_until(lambda:pf.reversal_journal(ledger_pg,payouts_mysql,pid),timeout=90,interval=1,desc="real async reversal retry after Ledger restore")
    assert pf.ledger_entries_sum_to_zero(pf.get_ledger_entries(ledger_pg,journal["id"]))
    wait_until(lambda:pf.db_id(pf.get_reversal_row(payouts_mysql,pid)["transaction_id"])==journal["id"],timeout=15,interval=.5,desc="reversal transaction link")
