#!/usr/bin/env python3
"""Bounded Shared payout demonstration via Kong merchant API-key authentication."""
import json
import os
from pathlib import Path
import signal
import sys
import time
import uuid

from helpers import db, trace, creds, payouts_flow as pf
from helpers.http_client import ArenaHTTPClient
from helpers.wait import wait_until
from helpers.trace_snapshot import record_snapshot


def configure_secrets():
    for env, name in (("PAYOUTS_MYSQL_PASSWORD","mysql_payouts_root_password"),
                      ("FTS_MYSQL_PASSWORD","mysql_fts_root_password"),
                      ("LEDGER_PG_PASSWORD","postgres_ledger_password")):
        path=Path('/run/secrets')/name
        if not os.environ.get(env) and path.is_file(): os.environ[env]=path.read_text().strip()
    os.environ.setdefault("LEDGER_PG_USER","ledger")
    os.environ["ARENA_WAIT_SCALE"]="1"


def fail_deadline(*_):
    raise TimeoutError("live scenario exceeded its total runtime limit")


def main():
    result_path=Path(os.environ.get("ARENA_LIVE_RESULT","/results/live-result.json"))
    result_path.parent.mkdir(parents=True,exist_ok=True)
    os.environ.setdefault("ARENA_TRACE_DIR",str(result_path.parent))
    trace.select("live-golden-shared")
    result={"status":"failed","passed":False,"scenario":"shared_success_via_kong","steps":[],"started_at":time.time()}
    bank=None
    merchant=None
    payouts=fts=ledger=sink=None
    pid=None
    try:
        timeout=int(os.environ.get("ARENA_LIVE_TIMEOUT","90"))
        assert 10<=timeout<=300,"ARENA_LIVE_TIMEOUT must be 10..300 seconds"
        signal.signal(signal.SIGALRM,fail_deadline)
        signal.alarm(timeout)
        configure_secrets()
        import network_check
        assert network_check.main()==0, "runtime DNS controls failed"
        index=json.loads(Path('/fixtures/scenario-index.json').read_text())
        namespace=os.environ.get("ARENA_SCENARIO_NAMESPACE","baseline")
        trio=index['baseline'] if namespace=='baseline' else next(n['merchants'] for n in index['namespaces'] if n['namespace']==namespace)
        merchant=trio['M1']
        result['merchant_id']=merchant['merchant_id']
        result['route_profile']=index['route_profile']
        keyfile=Path(os.environ.get("ARENA_MERCHANT_KEY_FILE","/run/golden-merchant-key"))
        credential=keyfile.read_text().strip()
        assert ':' in credential,"merchant credential file must contain key_id:key_secret"
        key_id,secret=credential.split(':',1)
        assert key_id and secret,"merchant credential must not be empty"
        kong=ArenaHTTPClient(os.environ.get("KONG_LITE_URL","http://kong-lite:8080"),basic_auth=(key_id,secret))
        bank=ArenaHTTPClient(os.environ.get("MOZART_MOCK_URL","http://mozart-sim:8085"))
        sink=ArenaHTTPClient("http://merchant-webhook-sink:8080")
        control=bank.post('/_arena/scenario',body={'merchant_id':merchant['merchant_id'],'scenario':'success'})
        assert control.status==200,control
        payouts=db.connect_mysql('PAYOUTS_MYSQL_HOST','PAYOUTS_MYSQL_PORT','PAYOUTS_MYSQL_USER','PAYOUTS_MYSQL_PASSWORD','PAYOUTS_MYSQL_DB',{'host':'mysql-payouts','port':3306,'user':'root','db':'payouts'})
        fts=db.connect_mysql('FTS_MYSQL_HOST','FTS_MYSQL_PORT','FTS_MYSQL_USER','FTS_MYSQL_PASSWORD','FTS_MYSQL_DB',{'host':'mysql-fts','port':3306,'user':'root','db':'fts'})
        ledger=db.connect_postgres('LEDGER_PG_HOST','LEDGER_PG_PORT','LEDGER_PG_USER','LEDGER_PG_PASSWORD','LEDGER_PG_DB',{'host':'postgres-ledger','port':5432,'user':'ledger','db':'ledger'})
        body=pf.build_create_body(merchant['fund_account_id'],merchant['account_number'],int(os.environ.get('ARENA_LIVE_AMOUNT','10000')))
        body['merchant_id']=merchant['merchant_id']  # PS DTO requires the monolith-supplied field.
        response=kong.post('/v1/payouts',body=body,headers={'X-Payout-Idempotency':str(uuid.uuid4())})
        assert response.status in (200,201),response
        pid=response.json()['id']
        result['payout_id']=pid
        result['steps'].append({'name':'Kong merchant authentication and payout create','passed':True,'http_status':response.status,'payout_id':pid})
        initiated=pf.get_ledger_journal_row(ledger,pid,'payout_initiated')
        assert initiated and pf.ledger_entries_sum_to_zero(pf.get_ledger_entries(ledger,initiated['id']))
        result['steps'].append({'name':'Synchronous balanced initiated journal','passed':True,'journal_id':initiated['id']})
        payout=pf.wait_for_status(payouts,pid,'processed',timeout=45)
        transfer=pf.transfer_metadata(fts,pid)
        assert transfer['status']=='PROCESSED' and transfer['utr']==payout['utr'] and payout['utr']
        result['steps'].append({'name':'FTS and bank terminal result','passed':True,'fts_transfer_id':transfer['id'],'status':'processed','utr':payout['utr']})
        processed=pf.wait_for_processed_journal(ledger,pid)
        assert pf.ledger_entries_sum_to_zero(pf.get_ledger_entries(ledger,processed['id']))
        assert pf.count_ledger_journal_rows(ledger,pid,'payout_processed')==1
        result['steps'].append({'name':'Balanced asynchronous processed journal','passed':True,'journal_id':processed['id']})
        def deliveries():
            response=sink.get('/_arena/deliveries')
            assert response.status==200,response
            return [d for d in response.json()['deliveries'] if d.get('merchant')==merchant['merchant_id']
                    and d.get('body',{}).get('event')=='payout.processed'
                    and d['body'].get('payload',{}).get('payout',{}).get('entity',{}).get('id')==pid]
        received=wait_until(deliveries,timeout=15,interval=.5,desc='signed merchant terminal webhook')
        assert len(received)==1,received
        event=received[0]
        assert event['signature_valid'] is True and event['signature_present'] is True
        assert event['body']['payload']['payout']['entity']['status']=='processed'
        assert event['body']['account_id']=='acc_'+merchant['merchant_id']
        result['steps'].append({'name':'Exactly one signed merchant terminal webhook','passed':True,'event_id':event['event_id'],'signature_valid':True})
        result['status']='passed'
        result['passed']=True
    except BaseException as exc:
        result['error']={'type':type(exc).__name__,'message':str(exc)}
        result['steps'].append({'name':'scenario failure','passed':False,'error_type':type(exc).__name__,'message':str(exc)})
    finally:
        signal.alarm(0)
        if pid and all(connection is not None for connection in (payouts,fts,ledger,sink)):
            try:
                signal.alarm(20)
                result['ending_state']=record_snapshot(payouts,fts,ledger,sink,pid,
                                                      bank_client=bank,monolith_client=ArenaHTTPClient('http://monolith-stub:8080',basic_auth=creds.resolve_basic_auth('MONOLITH')),label='live_scenario_completion')
                assert result['ending_state']['complete'],result['ending_state']['errors']
            except Exception as exc:
                result['snapshot_error']={'type':type(exc).__name__,'message':str(exc)}
                result['status']='failed'
                result['passed']=False
            finally: signal.alarm(0)
        if bank and merchant:
            try:
                cleared=bank.post('/_arena/scenario',body={'merchant_id':merchant['merchant_id'],'clear':True},timeout=3)
                assert cleared.status==200,cleared
            except Exception as exc:
                result['cleanup_error']={'type':type(exc).__name__,'message':str(exc)}
                result['status']='failed'
                result['passed']=False
        for connection in (payouts,fts,ledger):
            if connection is not None:
                try: connection.close()
                except Exception as exc:
                    result.setdefault('connection_cleanup_errors',[]).append({'type':type(exc).__name__,'message':str(exc)})
                    result['status']='failed'
                    result['passed']=False
        result['finished_at']=time.time()
        result['duration_seconds']=result['finished_at']-result['started_at']
        trace.record('live_result',result)
        result_path.write_text(json.dumps(trace.clean(result),indent=2,default=str)+'\n')
        print(json.dumps({'status':result['status'],'passed':result['passed'],'payout_id':result.get('payout_id'),'result':str(result_path)}))
    return 0 if result['passed'] else 1


if __name__=='__main__': sys.exit(main())
