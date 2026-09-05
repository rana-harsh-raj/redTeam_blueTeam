"""V21: exact terminal delivery after an observed details-only update.

Separating the UTR/details stimulus from terminal status avoids the source's
async payout.updated snapshot race; it does not claim that race is fixed.
"""
import time
import pytest
from helpers import payouts_flow as pf
from helpers.wait import wait_until

@pytest.mark.spec_id("V21")
@pytest.mark.status("CROSS_SERVICE_CONFIRMED")
def test_terminal_webhook_fires_exactly_once(ps_public_client,ps_internal_client,payouts_mysql,stork_capture_client,merchant_m1,fts_mysql,merchant_sink_client):
    pid=pf.seed_payout(ps_public_client,merchant_m1)
    sink_path='/_arena/deliveries?merchant='+merchant_m1['merchant_id']+'&payout_id='+pid
    pf.wait_for_held_handoff(payouts_mysql,fts_mysql,pid)
    transfer=pf.transfer_metadata(fts_mysql,pid)
    # core.go HandlePayoutDetailsUpdateViaFTS schedules payout.updated when UTR
    # changes; use its real details-only endpoint before the terminal stimulus.
    details=ps_internal_client.post('/v1/payouts/update_payouts_details_with_fts',body={
        'source_id':pf.db_id(pid),'fund_transfer_id':transfer['id'],'fta_status':'initiated','utr':'UTR_V21'})
    assert details.status==200,details
    def updated_deliveries():
        response=merchant_sink_client.get(sink_path)
        assert response.status==200,response
        return [d for d in response.json()['deliveries'] if d['merchant']==merchant_m1['merchant_id']
                and d['body'].get('event')=='payout.updated'
                and d['body']['payload']['payout']['entity']['id']==pid]
    updated=wait_until(updated_deliveries,timeout=20,interval=.25,desc='signed details-only updated delivery before terminal')
    assert len(updated)==1,updated
    assert updated[0]['signature_valid'] is True and updated[0]['signature_present'] is True,updated
    assert updated[0]['body']['payload']['payout']['entity']['status']=='processing',updated
    assert updated[0]['body']['payload']['payout']['entity']['utr']=='UTR_V21',updated
    def completed_update():
        response=stork_capture_client.get('/_arena/events?merchant='+merchant_m1['merchant_id'])
        assert response.status==200,response
        return [e for e in response.json()['events'] if e['event_name']=='payout.updated'
                and e['payload']['payload']['payout']['entity']['id']==pid and e.get('delivered_status')==200]
    update_hits=wait_until(completed_update,timeout=10,interval=.25,desc='Stork updated delivery completion')
    assert len(update_hits)==1 and update_hits[0]['event_id']==updated[0]['event_id'],update_hits
    assert pf.get_payout_row(payouts_mysql,pid)['status']=='initiated'
    response=pf.inject_transfer_webhook(ps_internal_client,fts_mysql,pid,"processed",utr="UTR_V21")
    assert response.status==200,response
    pf.wait_for_status(payouts_mysql,pid,"processed")
    def events():
        response=stork_capture_client.get("/_arena/events?merchant="+merchant_m1["merchant_id"])
        assert response.status==200,response
        return [e for e in response.json()["events"] if e["payload"]["payload"]["payout"]["entity"]["id"]==pid and e["event_name"] in {"payout.processed","payout.reversed","payout.failed","payout.cancelled","payout.rejected"}]
    hits=wait_until(events,timeout=30,interval=.5,desc="terminal Stork delivery")
    assert len(hits)==1 and hits[0]["event_name"]=="payout.processed",hits
    assert hits[0]["delivered_status"]==200,hits
    def sink_terminal():
        response=merchant_sink_client.get(sink_path)
        assert response.status==200,response
        return [d for d in response.json()["deliveries"] if d["merchant"]==merchant_m1["merchant_id"]
                and d["body"]["payload"]["payout"]["entity"]["id"]==pid
                and d["body"].get("event") in {"payout.processed","payout.reversed","payout.failed","payout.cancelled","payout.rejected"}]
    deliveries=wait_until(sink_terminal,timeout=10,interval=.5,desc="merchant received terminal delivery")
    assert len(deliveries)==1,deliveries
    delivered=deliveries[0]
    assert delivered["signature_present"] is True and delivered["signature_valid"] is True,delivered
    assert delivered["body"]["event"]=="payout.processed"
    assert delivered["body"]["payload"]["payout"]["entity"]["id"]==pid
    assert delivered["body"]["payload"]["payout"]["entity"]["status"]=="processed"
    assert delivered["body"]["account_id"]=="acc_"+merchant_m1["merchant_id"]
    assert delivered["event_id"]==hits[0]["event_id"]
    duplicate=pf.inject_transfer_webhook(ps_internal_client,fts_mysql,pid,"processed",utr="UTR_V21")
    assert duplicate.status==200,duplicate
    time.sleep(2)
    assert len(events())==1, "duplicate terminal update caused duplicate terminal delivery"
    assert len(sink_terminal())==1, "duplicate merchant terminal delivery"
    assert len(updated_deliveries())==1, "same-UTR terminal stimulus duplicated updated delivery"
