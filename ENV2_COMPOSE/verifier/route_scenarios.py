#!/usr/bin/env python3
"""Supplemental real-core route cases, with explicit synthetic clock adjustment."""
import argparse
import json
import os
from pathlib import Path
import signal
import time
from helpers import db, creds, trace, payouts_flow as pf
from helpers.http_client import ArenaHTTPClient
from helpers.wait import wait_until
from scenario import configure_secrets
from verifiers.test_v24_cron_dequeue import _next_allowed_slot_ist

OUT=Path(os.environ.get('ARENA_TRACE_DIR','/results'))
CASES=('direct_success','scheduled','queued','returned','failed_direct','failed_shared')

def deadline(*_):raise TimeoutError('route case exceeded 120-second deadline')

def setup():
    configure_secrets()
    index=json.loads(Path('/fixtures/scenario-index.json').read_text())
    clients={k:ArenaHTTPClient(url,basic_auth=creds.resolve_basic_auth(auth) if auth else None) for k,url,auth in [
        ('payouts','http://payouts-api:9400','PS_SERVICE'),('fts','http://fts-web:8080','FTS'),
        ('cron','http://payouts-api:9400','PS_FASTCRON'),('ledger','http://ledger-api:8080','LEDGER'),
        ('monolith','http://monolith-stub:8080','MONOLITH'),('bank','http://mozart-sim:8085',None),
        ('sink','http://merchant-webhook-sink:8080',None)]}
    stores={'payouts':db.connect_mysql('PAYOUTS_MYSQL_HOST','PAYOUTS_MYSQL_PORT','PAYOUTS_MYSQL_USER','PAYOUTS_MYSQL_PASSWORD','PAYOUTS_MYSQL_DB',{'host':'mysql-payouts','port':3306,'user':'root','db':'payouts'}),
    'fts':db.connect_mysql('FTS_MYSQL_HOST','FTS_MYSQL_PORT','FTS_MYSQL_USER','FTS_MYSQL_PASSWORD','FTS_MYSQL_DB',{'host':'mysql-fts','port':3306,'user':'root','db':'fts'}),
    'ledger':db.connect_postgres('LEDGER_PG_HOST','LEDGER_PG_PORT','LEDGER_PG_USER','LEDGER_PG_PASSWORD','LEDGER_PG_DB',{'host':'postgres-ledger','port':5432,'user':'ledger','db':'ledger'})}
    return index,clients,stores


def journal(stores,pid,event,required=True):
    if required and event == 'payout_processed':
        row=pf.wait_for_processed_journal(stores['ledger'],pid)
    else:
        row=wait_until(lambda:pf.get_ledger_journal_row(stores['ledger'],pid,event),timeout=15,interval=.25,desc=event) if required else pf.get_ledger_journal_row(stores['ledger'],pid,event)
    if required:
        assert row and pf.ledger_entries_sum_to_zero(pf.get_ledger_entries(stores['ledger'],row['id']))
        assert pf.count_ledger_journal_rows(stores['ledger'],pid,event)==1
    else:assert row is None,(event,row)
    return row


def terminal(clients,stores,merchant,pid,status='processed',fts_status=None):
    payout=pf.wait_for_status(stores['payouts'],pid,status,timeout=30)
    transfer=pf.transfer_metadata(stores['fts'],pid)
    assert transfer['status']==(fts_status or status.upper()),transfer
    if status=='processed':assert payout['utr']==transfer['utr'] and payout['utr']
    def events():
        r=clients['sink'].get('/_arena/deliveries');assert r.status==200,r
        return [d for d in r.json()['deliveries'] if d.get('merchant')==merchant['merchant_id']
                and d['body'].get('event')=='payout.'+status and d['body'].get('payload',{}).get('payout',{}).get('entity',{}).get('id')==pid]
    deliveries=wait_until(events,timeout=15,interval=.25,desc='signed terminal webhook')
    assert len(deliveries)==1 and deliveries[0]['signature_valid'] is True and deliveries[0]['signature_present'] is True,deliveries
    assert deliveries[0]['body']['account_id']=='acc_'+merchant['merchant_id'],deliveries
    assert deliveries[0]['body']['payload']['payout']['entity']['status']==status,deliveries
    return {'payout_id':pid,'payout_status':payout['status'],'fts_status':transfer['status'],'fts_transfer_id':transfer['id'],'utr':payout.get('utr'),'webhook_event':'payout.'+status,'signature_valid':True,'terminal_webhook_count':1}


def case(name,index,c,s):
    namespace={'direct_success':'scenario:direct_status','scheduled':'scenario:monolith_relay','queued':'scenario:low_balance',
               'returned':'scenario:monolith_relay','failed_direct':'scenario:invalid_beneficiary','failed_shared':'scenario:kafka'}[name]
    trio=next(x['merchants'] for x in index['namespaces'] if x['namespace']==namespace)
    merchant=trio['M2' if name in ('direct_success','failed_direct') else 'M1']
    is_failure=name in ('failed_direct','failed_shared')
    result={'name':name,'fixture':merchant,'route_profile':index['route_profile'],'status':'failed',
            'fixture_namespace':namespace,'transport_selection':'active route_profile; fixture namespace does not activate transport',
            'expectation_profile':'monolith baseline; alternate-profile execution is an explicit compatibility check'}
    trace.select('route-'+name);trace.record('starting_fixture',merchant)
    starting=pf.get_account_balance(s['ledger'],merchant['merchant_id'])
    result['starting_ledger_balance']=str(starting) if starting is not None else None
    scenario='failure' if is_failure else 'success' if name=='direct_success' else 'returned' if name=='returned' else 'hold'
    try:
        rule=c['bank'].post('/_arena/scenario',body={'merchant_id':merchant['merchant_id'],'scenario':scenario,'polls':1});assert rule.status==200,rule
        if is_failure:
            result['starting_payouts']=db.fetchall(s['payouts'],'SELECT id,status,amount FROM payouts WHERE merchant_id=%s ORDER BY id',(merchant['merchant_id'],))
            trace.record('failure_starting_state',{'fixture':merchant,'payouts':result['starting_payouts'],'ledger_balance':starting})
        amount=10000 if name!='queued' else int(starting)+500
        kwargs={'queue_if_low_balance':True} if name=='queued' else {}
        if name=='scheduled':kwargs['extra']={'scheduled_at':_next_allowed_slot_ist()}
        pid=pf.seed_payout(c['payouts'],merchant,amount,**kwargs)
        result['payout_id']=pid
        if name=='scheduled':
            initial=pf.get_payout_row(s['payouts'],pid);assert initial['status']=='scheduled'
            # Explicitly scoped synthetic clock fixture: validates due-selection
            # and dispatch, not natural wall-clock slot passage or cadence.
            due=int(time.time())-60
            db.execute(s['payouts'],'UPDATE payouts SET scheduled_at=%s WHERE id=%s',(due,pf.db_id(pid)))
            trace.record('synthetic_clock_fixture',{'payout_id':pid,'field':'scheduled_at','original':initial['scheduled_at'],'due':due,'classification':'representative fixture; no state change'})
            r=c['cron'].post('/v1/cron/process_scheduled_payouts',body={});assert r.status==200,r
        elif name=='queued':
            initial=pf.get_payout_row(s['payouts'],pid);assert initial['status']=='queued' and initial['queued_reason']=='low_balance'
            r=pf.ledger_topup(c['ledger'],merchant,amount);assert r.status==200,r
            r=c['monolith'].post('/_arena/balance-sync',body={'balance_ids':[merchant['balance_id']],'deliver_event':True});assert r.status==200,r
        if name in ('scheduled','queued'):
            pf.wait_for_initiated(s['payouts'],pid,timeout=30)
            pf.finish_bank(c['bank'],c['fts'],s['fts'],pid,'success')
        shared=merchant['archetype']!='direct'
        if is_failure:
            final_status='reversed' if shared else 'failed'
            result.update(terminal(c,s,merchant,pid,final_status,fts_status='FAILED'))
            transfer=pf.transfer_metadata(s['fts'],pid)
            assert transfer['bank_status_code']=='INVALID_ACCOUNT_NUMBER',transfer
            attempts=db.fetchall(s['fts'],'SELECT * FROM attempts WHERE transfer_id=%s ORDER BY id',(transfer['id'],))
            assert len(attempts)==1 and attempts[0]['status']=='FAILED' and attempts[0]['bank_status_code']=='INVALID_ACCOUNT_NUMBER',attempts
            r=c['bank'].get('/_arena/scenarios');assert r.status==200,r
            bank_calls=[e for e in r.json()['events'] if e['attempt_id']==str(attempts[0]['id']) and e['action']=='transfer_init']
            assert len(bank_calls)==1 and bank_calls[0]['scenario']=='failure',bank_calls
            result['bank_failure_evidence']=bank_calls
            result['bank_status_code']='INVALID_ACCOUNT_NUMBER'
            identities=[pid]
            if shared:
                initiated=journal(s,pid,'payout_initiated')
                reversal=pf.get_reversal_row(s['payouts'],pid);assert reversal
                reversal_identity='rvrsl_'+reversal['id'];identities.append(reversal_identity)
                reversed_journal=journal(s,reversal_identity,'payout_failed')
                linked=wait_until(lambda:(lambda row:row if row and row.get('transaction_id') else None)(pf.get_reversal_row(s['payouts'],pid)),timeout=15,interval=.25,desc='reversal Ledger transaction link')
                assert str(linked['transaction_id']).removeprefix('txn_')==reversed_journal['id'],linked
                assert pf.get_account_balance(s['ledger'],merchant['merchant_id'])==starting
                result['reversal_id']=reversal['id']
                result['initiated_journal_id']=initiated['id']
                result['reversal_journal_id']=reversed_journal['id']
                result['reversal_ledger_event']='payout_failed'
                result['merchant_balance_restored']=True
            else:
                assert pf.get_reversal_row(s['payouts'],pid) is None
            entries=db.fetchall(s['ledger'],'SELECT id,transactor_id,transactor_event FROM journal WHERE transactor_id=ANY(%s) ORDER BY created_at,id',(identities,))
            expected_events=['payout_failed','payout_initiated'] if shared else []
            assert sorted(j['transactor_event'] for j in entries)==expected_events,entries
            r=c['sink'].get('/_arena/deliveries');assert r.status==200,r
            terminal_deliveries=[d for d in r.json()['deliveries'] if d.get('merchant')==merchant['merchant_id']
                and d['body'].get('event') in ('payout.processed','payout.failed','payout.reversed')
                and d['body'].get('payload',{}).get('payout',{}).get('entity',{}).get('id')==pid]
            assert len(terminal_deliveries)==1 and terminal_deliveries[0]['body']['event']=='payout.'+final_status,terminal_deliveries
            result['accounting_claim']='Shared debit fully reversed with payout_failed journal' if shared else 'Direct failure has no Payouts Ledger journal or reversal entity'
        else:
            result.update(terminal(c,s,merchant,pid))
            journal(s,pid,'payout_initiated',required=shared);journal(s,pid,'payout_processed',required=shared)
        if name=='returned':
            transfer=pf.transfer_metadata(s['fts'],pid)
            # Normal polling is restricted to INITIATED (FTS service.go:1031).
            # Terminal returns use explicit admin reconciliation: raw verify
            # observes the bank, then safe_update rechecks the bank before the
            # PROCESSED -> REVERSED transition (manual_update_handlers_factory).
            r=c['fts'].post('/v1/transfer/%s/check'%transfer['id'],body={})
            assert r.status==400 and r.json()['internal_error']['code']=='ILLEGAL_STATE',r
            attempt=db.fetchone(s['fts'],'SELECT * FROM attempts WHERE transfer_id=%s ORDER BY id DESC LIMIT 1',(transfer['id'],))
            assert attempt and attempt['status']=='PROCESSED',attempt
            aid=str(attempt['id'])
            monolith_auth=creds.resolve_basic_auth('MONOLITH');assert monolith_auth
            # config/generate.py maps FTS users.api to this already mounted
            # synthetic monolith credential; PS is not an admin route caller.
            fts_admin=ArenaHTTPClient('http://fts-web:8080',basic_auth=('api_monolith',monolith_auth[1]))
            r=fts_admin.post('/v1/attempts/verify',body={'attempt_ids':[aid]})
            assert r.status==200,r
            verification=r.json()[aid]
            assert not verification.get('error'),verification
            raw=json.loads(verification['raw_status'])
            assert raw['meta']['failed'] is True and raw['meta']['error_type']=='BBANK',raw
            assert raw['data']['bank_status_code']=='RETURNED' and raw['data']['return_utr'],raw
            assert pf.transfer_metadata(s['fts'],pid)['status']=='PROCESSED'
            assert pf.get_payout_row(s['payouts'],pid)['status']=='processed'
            result['return_trigger']='explicit FTS admin safe_update after raw bank verification; safe_update independently re-verifies the bank'
            result['automatic_return_polling']='unsupported for terminal attempts; normal check returned ILLEGAL_STATE'
            result['observed_return_utr']=raw['data']['return_utr']
            update={aid:{'remarks':'ArenaReturnReconciliation','bank_status_code':raw['data']['bank_status_code'],
                         'return_utr':raw['data']['return_utr'],'meta':{'reversed':True}}}
            # Do not send status: UpdateAttemptStatus common.Fill copies request
            # fields before the state-machine event; meta selects the target.
            r=fts_admin.patch('/v1/attempts/safe_update',body=update)
            assert r.status==200 and r.json()[aid]['status'] is True,r
            result['processed_before_return']=True
            result.update(terminal(c,s,merchant,pid,'reversed'))
            r=c['bank'].get('/_arena/scenarios');assert r.status==200,r
            return_checks=[event for event in r.json()['events'] if event['attempt_id']==aid and event['action']=='transfer_status' and event['scenario']=='returned']
            assert len(return_checks)>=2,return_checks
            result['bank_return_verifications']=return_checks
            reversal=pf.get_reversal_row(s['payouts'],pid);assert reversal
            journal(s,'rvrsl_'+reversal['id'],'payout_reversed')
            result['reversal_id']=reversal['id']
        result['status']='passed'
    except BaseException as exc:
        result['error']={'type':type(exc).__name__,'message':str(exc)}
    finally:
        try:
            cleared=c['bank'].post('/_arena/scenario',body={'merchant_id':merchant['merchant_id'],'clear':True},timeout=3)
            assert cleared.status==200,cleared
        except Exception as exc:
            result['cleanup_error']={'type':type(exc).__name__,'message':str(exc)}
            result['status']='failed'
        if result.get('payout_id'):
            from helpers.trace_snapshot import record_snapshot
            try:
                snapshot=record_snapshot(s['payouts'],s['fts'],s['ledger'],c['sink'],result['payout_id'],bank_client=c['bank'],monolith_client=c['monolith'])
                result['snapshot_complete']=snapshot['complete']
                if not snapshot['complete']:result['status']='failed';result['snapshot_errors']=snapshot['errors']
            except Exception as exc:
                result['status']='failed'
                result['snapshot_complete']=False
                result['snapshot_error']={'type':type(exc).__name__,'message':str(exc)}
        trace.record('route_result',result)
    return trace.clean(result)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--case',choices=['all',*CASES],default='all');a=p.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    import network_check
    assert network_check.main()==0, "runtime DNS checks failed"
    index,c,s=setup();results=[]
    for name in (CASES if a.case=='all' else [a.case]):
        signal.signal(signal.SIGALRM,deadline);signal.alarm(120)
        try:results.append(case(name,index,c,s))
        finally:signal.alarm(0)
        (OUT/'route-results.json').write_text(json.dumps({'status':'passed' if all(r['status']=='passed' for r in results) else 'failed','cases':results},indent=2)+'\n')
        print(name+': '+results[-1]['status'],flush=True)
    return 0 if all(r['status']=='passed' for r in results) else 1
if __name__=='__main__':raise SystemExit(main())
