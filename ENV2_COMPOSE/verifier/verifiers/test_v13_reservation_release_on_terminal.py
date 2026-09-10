"""V13/I30: processed holds for balance refresh; failed releases immediately."""
import pytest
from helpers import payouts_flow as pf

@pytest.mark.spec_id("V13")
@pytest.mark.status("CROSS_SERVICE_CONFIRMED")
def test_reservation_released_on_terminal_event(ps_public_client,ps_internal_client,payouts_mysql,merchant_m2,fts_mysql):
    processed=pf.seed_payout(ps_public_client,merchant_m2,queue_if_low_balance=True)
    failed=pf.seed_payout(ps_public_client,merchant_m2,queue_if_low_balance=True)
    for pid in (processed,failed): pf.wait_for_initiated(payouts_mysql,pid)
    def inspect():
        response=pf.get_inflight_reservations(ps_internal_client,merchant_m2["merchant_id"],merchant_m2["balance_id"])
        assert response.status==200,response
        return response.json()
    before=inspect()
    live={i["payout_id"]:i for i in before["items"] if i["state"]=="live"}
    assert set(live)=={pf.db_id(processed),pf.db_id(failed)}, before
    assert before["reserved_total"]==200,before
    for pid in (processed,failed): pf.wait_for_held_handoff(payouts_mysql,fts_mysql,pid)
    response=pf.inject_transfer_webhook(ps_internal_client,fts_mysql,processed,"processed",utr="UTR_V13")
    assert response.status==200,response
    pf.wait_for_status(payouts_mysql,processed,"processed")
    after_processed=inspect()
    items={i["payout_id"]:i for i in after_processed["items"]}
    assert items[pf.db_id(processed)]["state"]=="awaiting_balance_refresh",after_processed
    assert after_processed["reserved_total"]==200,after_processed
    response=pf.inject_transfer_webhook(ps_internal_client,fts_mysql,failed,"failed",failure_reason="TEST",bank_status_code="91")
    assert response.status==200,response
    pf.wait_for_status(payouts_mysql,failed,"failed")
    after_failed=inspect()
    assert after_failed["reserved_total"]==100,after_failed
    assert all(i["payout_id"]!=pf.db_id(failed) for i in after_failed["items"]),after_failed
