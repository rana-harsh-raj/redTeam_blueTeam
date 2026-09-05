#!/usr/bin/env python3
"""Additional real-bank-boundary scenarios, separate from the original 26 checks.

Uses actual Payouts/FTS workers, bank controls and persisted evidence. Nonterminal
results are bounded observations; no DB mutation or terminal callback injection.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import time
import uuid

from helpers import creds, db, payouts_flow as pf, trace
from helpers.http_client import ArenaHTTPClient
from helpers.wait import wait_until
from helpers.trace_snapshot import record_snapshot
from scenario import configure_secrets

SCENARIOS = ("hold", "delayed_success", "ambiguous_with_utr", "ambiguous_without_utr", "duplicate", "timeout")
TERMINAL_EVENTS = {"payout.processed", "payout.failed", "payout.reversed", "payout.cancelled", "payout.rejected"}
EXPECTED_PENDING_CODES = {
    "hold": "INITIATED",
    "delayed_success": "INITIATED",
    # createResponseStruct replaces bank_status_code with error.internal_error_code;
    # fillMeta treats this synthetic unmapped code as Pending/UNMAPPED.
    "ambiguous_with_utr": "TECHNICAL_ERROR_AMBIGUOUS",
    "ambiguous_without_utr": "TECHNICAL_ERROR_AMBIGUOUS",
    "duplicate": "DUPLICATE_TXN",
    # Delayed gateway504 lacks a valid Mozart envelope -> MOZART_INDETERMINATE.
    "timeout": "MOZART_INDETERMINATE",
}


def deadline(*_):
    raise TimeoutError("bank scenario exceeded its bounded total runtime")


def auth(prefix):
    value = creds.resolve_basic_auth(prefix)
    assert value, "missing mounted synthetic service credential: " + prefix
    return value


class Arena:
    def __init__(self, namespace):
        index = json.loads(Path('/fixtures/scenario-index.json').read_text())
        groups = [n for n in index['namespaces'] if n['namespace'] == namespace]
        assert len(groups) == 1, "generated namespace missing or ambiguous: " + namespace
        self.namespace = namespace
        self.merchant = groups[0]['merchants']['M1']
        assert self.merchant['archetype'] == 'shared'
        self.profile = index['route_profile']
        assert self.profile == 'monolith', "these expectations require monolith transport profile"
        self.ps = ArenaHTTPClient(os.environ.get('PS_PUBLIC_URL', 'http://payouts-api:9400'), basic_auth=auth('PS_SERVICE'))
        self.fts_http = ArenaHTTPClient(os.environ.get('FTS_WEB_URL', 'http://fts-web:8080'), basic_auth=auth('FTS'))
        self.bank = ArenaHTTPClient(os.environ.get('MOZART_MOCK_URL', 'http://mozart-sim:8085'))
        self.sink = ArenaHTTPClient('http://merchant-webhook-sink:8080')
        # Monolith relay capture (create_fta / payout_status_relay events) so bank snapshots carry
        # the same transport evidence layer as route scenarios (route_scenarios.py setup()).
        self.monolith = ArenaHTTPClient('http://monolith-stub:8080', basic_auth=auth('MONOLITH'))
        self.payouts = db.connect_mysql('PAYOUTS_MYSQL_HOST','PAYOUTS_MYSQL_PORT','PAYOUTS_MYSQL_USER','PAYOUTS_MYSQL_PASSWORD','PAYOUTS_MYSQL_DB', {'host':'mysql-payouts','port':3306,'user':'root','db':'payouts'})
        self.fts = db.connect_mysql('FTS_MYSQL_HOST','FTS_MYSQL_PORT','FTS_MYSQL_USER','FTS_MYSQL_PASSWORD','FTS_MYSQL_DB', {'host':'mysql-fts','port':3306,'user':'root','db':'fts'})
        self.ledger = db.connect_postgres('LEDGER_PG_HOST','LEDGER_PG_PORT','LEDGER_PG_USER','LEDGER_PG_PASSWORD','LEDGER_PG_DB', {'host':'postgres-ledger','port':5432,'user':'ledger','db':'ledger'})

    def close(self):
        for connection in (self.payouts, self.fts, self.ledger):
            connection.close()

    def control(self, scenario):
        response = self.bank.post('/_arena/scenario', body={'merchant_id':self.merchant['merchant_id'], 'scenario':scenario, 'polls':2})
        assert response.status == 200, response

    def transfer(self, pid):
        return db.fetchone(self.fts, "SELECT * FROM transfers WHERE source_id=%s AND source_type='payout'", (pf.db_id(pid),))

    def attempt(self, transfer_id):
        return db.fetchone(self.fts, 'SELECT * FROM attempts WHERE transfer_id=%s ORDER BY id DESC LIMIT 1', (transfer_id,))

    def bank_events(self, attempt_id):
        response = self.bank.get('/_arena/scenarios')
        assert response.status == 200, response
        return [e for e in response.json()['events'] if e['attempt_id'] == str(attempt_id)]

    def deliveries(self, pid):
        response = self.sink.get('/_arena/deliveries')
        assert response.status == 200, response
        return [d for d in response.json()['deliveries']
                if d.get('merchant') == self.merchant['merchant_id']
                and d.get('body', {}).get('payload', {}).get('payout', {}).get('entity', {}).get('id') == pid]

    def journals(self, pid):
        reversal = pf.get_reversal_row(self.payouts, pid)
        identities = [pid]
        if reversal:
            identities.append('rvrsl_' + reversal['id'])
        return db.fetchall(self.ledger, 'SELECT id, transactor_id, transactor_event FROM journal WHERE transactor_id = ANY(%s) ORDER BY created_at,id', (identities,))

    def snapshot(self, pid):
        return record_snapshot(self.payouts,self.fts,self.ledger,self.sink,pid,
                               bank_client=self.bank,monolith_client=self.monolith,label='bank_scenario_completion')

    def assert_held_accounting(self, pid, before):
        payout = pf.get_payout_row(self.payouts, pid)
        assert payout['status'] == 'initiated', payout
        assert pf.get_reversal_row(self.payouts,pid) is None
        journals = self.journals(pid)
        assert len(journals) == 1 and journals[0]['transactor_event'] == 'payout_initiated', journals
        assert pf.ledger_entries_sum_to_zero(pf.get_ledger_entries(self.ledger,journals[0]['id']))
        assert pf.get_account_balance(self.ledger,self.merchant['merchant_id']) == before-payout['amount']-(payout['fees'] or 0)
        delivered = self.deliveries(pid)
        assert not [d for d in delivered if d['body'].get('event') in TERMINAL_EVENTS], delivered
        assert pf.get_payout_logs(self.payouts,pid), 'missing persisted payout transition evidence'

    def check(self, transfer_id):
        response = self.fts_http.post('/v1/transfer/%s/check' % transfer_id, body={})
        assert response.status == 200, response


def run_case(arena, name, observation_seconds, timeout):
    trace.select('bank-' + name)
    started = time.time()
    result = {'scenario':name, 'status':'failed', 'passed':False,
              'namespace':arena.namespace, 'merchant_id':arena.merchant['merchant_id'],
              'route_profile':arena.profile, 'started_at':started, 'checks':[]}
    pid = None
    signal.alarm(timeout)
    try:
        before = pf.get_account_balance(arena.ledger,arena.merchant['merchant_id'])
        starting = {'merchant':arena.merchant, 'ledger_balance':before,
                    'existing_payouts':db.fetchall(arena.payouts,'SELECT id,status,amount FROM payouts WHERE merchant_id=%s ORDER BY id',(arena.merchant['merchant_id'],))}
        result['starting_state'] = starting
        trace.record('starting_state',starting)
        arena.control(name)
        body = pf.build_create_body(arena.merchant['fund_account_id'],arena.merchant['account_number'],100)
        body['reference_id'] = 'bank-' + name[:12] + '-' + uuid.uuid4().hex[:8]
        response = pf.create_payout(arena.ps,pf.passport_or_skip(arena.merchant),body,idempotency_key=str(uuid.uuid4()))
        assert response.status == 200,response
        pid = response.json()['id']
        result['payout_id'] = pid
        pf.wait_for_initiated(arena.payouts,pid,timeout=30)
        transfer = wait_until(lambda:arena.transfer(pid),timeout=20,interval=.25,desc='real FTS transfer')
        attempt = wait_until(lambda:arena.attempt(transfer['id']),timeout=20,interval=.25,desc='real bank attempt')
        events = wait_until(lambda:arena.bank_events(attempt['id']),timeout=20,interval=.25,desc='actual bank scenario invocation')
        assert events[0]['scenario'] == name and events[0]['action'] == 'transfer_init',events
        result['fts_transfer_id'] = transfer['id']
        result['fts_attempt_id'] = attempt['id']
        result['checks'].append({'name':'real bank invocation','passed':True,'event':events[0]})

        code = EXPECTED_PENDING_CODES[name]
        attempt = wait_until(lambda:(lambda row:row if row and row.get('bank_status_code')==code else None)(arena.attempt(transfer['id'])),timeout=45,interval=.5,desc='persisted source-classified bank result '+code)
        transfer = arena.transfer(pid)
        assert attempt['status'] == 'INITIATED' and transfer['status'] == 'INITIATED', {'attempt':attempt,'transfer':transfer}
        if name == 'ambiguous_with_utr':
            assert attempt.get('utr'),attempt
        elif name in ('ambiguous_without_utr','duplicate','timeout'):
            assert not attempt.get('utr'),attempt
        arena.assert_held_accounting(pid,before)
        result['checks'].append({'name':'pending state and exact held Ledger debit','passed':True,'bank_status_code':code})

        if name == 'delayed_success':
            # The init callback is pending. Request actual FTS status checks; never
            # synthesize a Payouts terminal callback or change a persisted status.
            arena.check(transfer['id'])
            first = wait_until(lambda:[e for e in arena.bank_events(attempt['id']) if e['action']=='transfer_status' and e['poll']>=1],timeout=20,interval=.25,desc='first actual bank status poll')
            assert first[0]['poll'] == 1,first
            arena.assert_held_accounting(pid,before)
            arena.check(transfer['id'])
            payout = pf.wait_for_status(arena.payouts,pid,'processed',timeout=40)
            transfer = arena.transfer(pid)
            assert transfer['status']=='PROCESSED' and transfer['utr']==payout['utr'] and payout['utr'], {'payout':payout,'transfer':transfer}
            processed = pf.wait_for_processed_journal(arena.ledger,pid)
            assert pf.ledger_entries_sum_to_zero(pf.get_ledger_entries(arena.ledger,processed['id']))
            journals = arena.journals(pid)
            assert sorted(j['transactor_event'] for j in journals)==['payout_initiated','payout_processed'],journals
            assert pf.get_account_balance(arena.ledger,arena.merchant['merchant_id'])==before-payout['amount']-(payout['fees'] or 0)
            terminal = wait_until(lambda:[d for d in arena.deliveries(pid) if d['body'].get('event') in TERMINAL_EVENTS],timeout=15,interval=.5,desc='real signed merchant terminal delivery')
            assert len(terminal)==1 and terminal[0]['body']['event']=='payout.processed',terminal
            assert terminal[0]['signature_present'] is True and terminal[0]['signature_valid'] is True,terminal
            assert terminal[0]['body']['payload']['payout']['entity']['status']=='processed'
            assert pf.get_reversal_row(arena.payouts,pid) is None
            result['checks'].append({'name':'pending-to-processed through real bank checks, Ledger and signed webhook','passed':True})
            result['claim'] = 'terminal convergence through actual FTS bank status checks'
        else:
            end = time.monotonic()+observation_seconds
            while time.monotonic()<end:
                current = arena.transfer(pid)
                assert current['status']=='INITIATED',current
                arena.assert_held_accounting(pid,before)
                time.sleep(.5)
            result['observation_seconds'] = observation_seconds
            result['claim'] = 'pending, merchant debit held, no terminal journal or terminal webhook during bounded observation'
            result['checks'].append({'name':'bounded nonterminal observation across all layers','passed':True,'seconds':observation_seconds})
        result['status']='passed'
        result['passed']=True
    except Exception as exc:
        result['error']={'type':type(exc).__name__,'message':str(exc)}
    finally:
        signal.alarm(0)
        if pid:
            try:
                signal.alarm(20)
                result['ending_state']=arena.snapshot(pid)
                assert result['ending_state']['complete'],result['ending_state']['errors']
            except Exception as exc:
                result['snapshot_error']={'type':type(exc).__name__,'message':str(exc)}
                result['status']='failed'
                result['passed']=False
            finally: signal.alarm(0)
        try:
            clear=arena.bank.post('/_arena/scenario',body={'merchant_id':arena.merchant['merchant_id'],'clear':True},timeout=3)
            assert clear.status==200,clear
        except Exception as exc:
            result['cleanup_error']={'type':type(exc).__name__,'message':str(exc)}
            result['status']='failed'
            result['passed']=False
        result['finished_at']=time.time()
        result['duration_seconds']=result['finished_at']-started
        trace.record('scenario_result',result)
    return trace.clean(result)


def main(args):
    output=Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    os.environ.setdefault('ARENA_TRACE_DIR',str(output.parent))
    trace.select('bank-scenarios-setup')
    result={'status':'failed','passed':False,'namespace':args.namespace,'scenarios':[], 'started_at':time.time()}
    arena=None
    try:
        configure_secrets()
        import network_check
        assert network_check.main()==0,'runtime DNS controls failed'
        signal.signal(signal.SIGALRM,deadline)
        arena=Arena(args.namespace)
        for name in args.scenarios.split(','):
            result['scenarios'].append(run_case(arena,name,args.observation_seconds,args.scenario_timeout))
            output.write_text(json.dumps(result,indent=2,default=str)+'\n')
        result['passed']=all(case['passed'] for case in result['scenarios'])
        result['status']='passed' if result['passed'] else 'failed'
    except Exception as exc:
        result['error']={'type':type(exc).__name__,'message':str(exc)}
    finally:
        signal.alarm(0)
        if arena: arena.close()
        result['finished_at']=time.time()
        result['duration_seconds']=result['finished_at']-result['started_at']
        output.write_text(json.dumps(trace.clean(result),indent=2,default=str)+'\n')
        print(json.dumps({'status':result['status'],'passed':result['passed'],'checks':len(result['scenarios']),
                          'failed':[s['scenario'] for s in result['scenarios'] if not s['passed']],'result':str(output)}))
    return 0 if result['passed'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace',default='scenario:invalid_beneficiary',help='dedicated generated Shared M1 namespace, separate from the original26')
    parser.add_argument('--scenarios',default=','.join(SCENARIOS),help='comma-separated '+','.join(SCENARIOS))
    parser.add_argument('--output',default='/results/bank-effects.json')
    parser.add_argument('--observation-seconds',type=int,default=5)
    parser.add_argument('--scenario-timeout',type=int,default=150)
    args=parser.parse_args()
    if not args.scenarios or any(s not in SCENARIOS for s in args.scenarios.split(',')):
        parser.error('unsupported scenario')
    if not 1<=args.observation_seconds<=60 or not 60<=args.scenario_timeout<=300:
        parser.error('observation must be 1..60 seconds and total scenario timeout 60..300 seconds')
    sys.exit(main(args))
