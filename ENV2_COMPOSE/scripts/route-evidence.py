#!/usr/bin/env python3
"""Export payout-scoped core HTTP/Kafka log evidence after a golden run.

Reads existing logs only. It does not publish a message, invoke a callback, or
infer transport from a configured profile. Returned route assertions require
both observed calls and persisted scenario state.

With ``--kafka-case NAME`` it summarises one case of a ``kafka-results.json`` run
instead (trace ``kafka-<case>.jsonl``) and adds a ``consumer_state`` block:
container state and restart counts for both payouts Kafka consumers, the broker's
own view of both consumer groups, and unfiltered crash-marker counts from the main
consumer's log since the case started -- the evidence the historical
Direct-after-Shared timeout could not show, because payout-id filtering hides a
process that died. Route and golden runs are unaffected.
"""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'verifier'))
from helpers.trace import clean

SERVICES=['payouts-api','payouts-worker-fts-async-processing',
          'fts-worker-fire-transfer-status-webhook',
          'payouts-kafka-fts-status-updates-consumer']
FIELDS={'msg','msg ','message','input','request_body','response_body','url',
        'method','uri','status','status_code','payout_id','source_id','transfer_id',
        'request_id','task_id','time','status_update_response','details_update_response','error'}

KAFKA_CASES=['shared_source_failure','direct_after_shared','failed_dropped_direct',
             'failed_dropped_shared','reversed_dropped_direct']
MAIN_CONSUMER='payouts-kafka-fts-status-updates-consumer'
RETRY_CONSUMER='payouts-kafka-fts-status-updates-retry-consumer'
# generated/payouts/arena.toml [kafka_consumers.*.config] ConsumerGroup
CONSUMER_GROUPS={MAIN_CONSUMER:'rx-payouts-fts-status-update-consumer-group',
                 RETRY_CONSUMER:'rx-payouts-fts-status-update-retry-consumer-group'}
# taskHandlers/fts_status_updates.go:90 (HandleFailedMessage), core.go queue push failure,
# and the vendored kafka worker's unrecovered goroutine panic.
CRASH_MARKERS=('KAFKA_FTS_STATUS_UPDATE_TASK_FAILED','QUEUE_PUSH_FAILED_FOR_LEDGER_PROCESSED_EVENT','panic:')


def consumer_state(base,since):
    """Container liveness, broker-side group membership and unfiltered crash markers.

    Read-only: `ps -q`, `docker inspect`, `kafka-consumer-groups.sh --describe` and
    `compose logs`. Nothing is produced, consumed, reset or restarted.
    """
    state={'containers':{},'consumer_groups':{},'log_markers':{},'log_marker_samples':{},
           'errors':[],'healthy':False,
           'scope':'both payouts Kafka consumers; log markers are unfiltered by payout id so a crash is visible'}
    for service in (MAIN_CONSUMER,RETRY_CONSUMER):
        info={'service':service}
        try:
            listing=subprocess.run(base+['ps','-a','-q',service],cwd=ROOT,capture_output=True,text=True,timeout=60,check=True)
            ids=[line.strip() for line in listing.stdout.splitlines() if line.strip()]
            if not ids:
                info['error']='no container for service'
            else:
                inspected=subprocess.run(['docker','inspect',ids[0]],cwd=ROOT,capture_output=True,text=True,timeout=60,check=True)
                data=json.loads(inspected.stdout)[0]
                info.update({'container_id':ids[0][:12],'name':data.get('Name'),
                             'status':data['State']['Status'],'restart_count':data.get('RestartCount'),
                             'exit_code':data['State'].get('ExitCode'),'started_at':data['State'].get('StartedAt'),
                             'health':(data['State'].get('Health') or {}).get('Status')})
        except Exception as exc:  # noqa: BLE001 -- absence of evidence is recorded, never treated as health
            info['error']=repr(exc)
        state['containers'][service]=info
    for service,group in CONSUMER_GROUPS.items():
        try:
            described=subprocess.run(base+['exec','-T','kafka','/opt/kafka/bin/kafka-consumer-groups.sh',
                                           '--bootstrap-server','kafka:9092','--describe','--group',group],
                                     cwd=ROOT,capture_output=True,text=True,timeout=120)
            state['consumer_groups'][group]={'service':service,'exit_code':described.returncode,
                                             'describe':described.stdout.strip().splitlines(),
                                             'stderr':described.stderr.strip().splitlines()[:5]}
        except Exception as exc:  # noqa: BLE001
            state['consumer_groups'][group]={'service':service,'error':repr(exc)}
    try:
        logs=subprocess.run(base+['logs','--no-color','--since',since,MAIN_CONSUMER],cwd=ROOT,
                            capture_output=True,text=True,timeout=120,check=True)
        lines=logs.stdout.splitlines()
        state['log_markers']={marker:sum(1 for line in lines if marker in line) for marker in CRASH_MARKERS}
        state['log_markers']['total_lines']=len(lines)
        state['log_marker_samples']={marker:[line for line in lines if marker in line][:3] for marker in CRASH_MARKERS}
    except Exception as exc:  # noqa: BLE001
        state['errors'].append(repr(exc))
    containers=state['containers']
    state['healthy']=(len(containers)==2 and
                      all(info.get('status')=='running' and info.get('restart_count')==0
                          for info in containers.values()))
    return state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    selection=parser.add_mutually_exclusive_group()
    selection.add_argument('--case',choices=['direct_success','scheduled','queued','returned','failed_direct','failed_shared'])
    selection.add_argument('--kafka-case',choices=KAFKA_CASES)
    selection.add_argument('--bank-case',choices=['hold','delayed_success','ambiguous_with_utr','ambiguous_without_utr','duplicate','timeout'])
    args=parser.parse_args()
    if args.kafka_case:
        cases=json.loads((args.run/'kafka-results.json').read_text())['cases']
        matches=[r for r in cases if r['name']==args.kafka_case]
        if len(matches)!=1:parser.error('one matching completed kafka case is required')
        result=dict(matches[0])
        trace=[json.loads(line) for line in (args.run/('kafka-'+args.kafka_case+'.jsonl')).read_text().splitlines()]
        result['started_at']=trace[0]['at']
        snapshots=[r['data'] for r in trace if r['kind']=='scenario_snapshot']
        if len(snapshots)!=1:parser.error('one matching completion snapshot is required')
        result['ending_state']=snapshots[0]
    elif args.bank_case:
        cases=json.loads((args.run/'bank-effects.json').read_text())['scenarios']
        matches=[r for r in cases if r['scenario']==args.bank_case]
        if len(matches)!=1:parser.error('one matching completed bank case is required')
        result=dict(matches[0])
        trace=[json.loads(line) for line in (args.run/('bank-'+args.bank_case+'.jsonl')).read_text().splitlines()]
        result['started_at']=trace[0]['at']
        snapshots=[r['data'] for r in trace if r['kind']=='scenario_snapshot']
        if len(snapshots)!=1:parser.error('one matching completion snapshot is required')
        result['ending_state']=snapshots[0]
    elif args.case:
        cases=json.loads((args.run/'route-results.json').read_text())['cases']
        matches=[r for r in cases if r['name']==args.case]
        if len(matches)!=1:parser.error('one matching completed case is required')
        result=dict(matches[0])
        trace=[json.loads(line) for line in (args.run/('route-'+args.case+'.jsonl')).read_text().splitlines()]
        result['started_at']=trace[0]['at']
        snapshots=[r['data'] for r in trace if r['kind']=='scenario_snapshot']
        if len(snapshots)!=1:parser.error('one matching completion snapshot is required')
        result['ending_state']=snapshots[0]
    else:
        result=json.loads((args.run/'live-result.json').read_text())
    pid=result['payout_id'];bare=pid.removeprefix('pout_')
    since=datetime.datetime.fromtimestamp(result['started_at']-1,datetime.timezone.utc).isoformat()
    base=['docker','compose','--env-file','.env.arena','-f','docker-compose.yml']
    for profile in ('datastores','core','substitutes'):base+=['--profile',profile]
    records=[]
    for service in SERVICES:
        logs=subprocess.run(base+['logs','--no-color','--since',since,service],cwd=ROOT,
                            capture_output=True,text=True,timeout=30,check=True)
        for line in logs.stdout.splitlines():
            if bare not in line:continue
            try:data=json.loads(line[line.index('{'):])
            except (ValueError,TypeError):continue
            narrowed={k:v for k,v in data.items() if k in FIELDS}
            # Payouts' logger nests source fields in context and wraps a field
            # in a same-named object. Retain only the enumerated evidence keys.
            for key,value in data.get('context',{}).items():
                if key not in FIELDS:continue
                if isinstance(value,dict) and set(value)=={key}:value=value[key]
                narrowed.setdefault(key,value)
            for key,value in list(narrowed.items()):
                if isinstance(value,str) and value.startswith(('{','[')):
                    try:narrowed[key]=json.loads(value)
                    except ValueError:pass
            records.append({'service':service,'data':clean(narrowed)})
    profile=result['route_profile']
    snapshot=result.get('ending_state',{}).get('layers',{})
    relay=snapshot.get('monolith',{}).get('events',[])
    meta=snapshot.get('fts',{}).get('transfer_meta',[])
    urls=[r['data'].get('url','') for r in records if r['service']=='fts-worker-fire-transfer-status-webhook']
    kafka=[r for r in records if r['service']=='payouts-kafka-fts-status-updates-consumer' and 'msg ' in r['data']]
    observed={'monolith_create':any(e['kind']=='create_fta' for e in relay),
              'monolith_status':any(e['kind']=='payout_status_relay' for e in relay),
              'direct_create_response':any(r['service']=='payouts-worker-fts-async-processing' and r['data'].get('message')=='PAYOUTS_SERVICE_FUND_TRANSFER_SERVICE_RESPONSE' for r in records),
              'direct_origin_metadata':any(m['origin_service']=='payouts' for m in meta),
              'direct_status_http':any(u=='http://payouts-api:9400/v1/payouts/transfer_status_webhook' for u in urls),
              'kafka_consumer_message':bool(kafka)}
    required={'monolith':['monolith_create','monolith_status'],
              'direct':['direct_origin_metadata','direct_status_http'],
              'direct-create-monolith-status':['direct_create_response','monolith_status'],
              'kafka':['kafka_consumer_message']}[profile]
    if args.bank_case:
        # Bank cases measure bank outcomes; the status leg only fires once FTS reaches a terminal
        # state, so a held/pending case can only ever evidence the create leg.
        fts_rows=snapshot.get('fts',{}).get('transfers',[]) if isinstance(snapshot.get('fts',{}).get('transfers'),list) else []
        terminal=any(str(t.get('status','')).upper() in ('PROCESSED','FAILED','REVERSED') for t in fts_rows)
        required=['monolith_create','monolith_status'] if terminal else ['monolith_create']
    payload={'schema_version':1,'profile':profile,'payout_id':pid,'scenario_status':result['status'],
             'observed':observed,'required':required,'transport_observed':all(observed[k] for k in required),
             'records':records,'note':'Transport observation is separate from terminal/accounting acceptance. Absence of a call alone is not direct-create proof.'}
    if args.kafka_case:
        payload['kafka_case']=args.kafka_case
        payload['matches_prediction']=result.get('matches_prediction')
        payload['predicted_state']=result.get('predicted_state')
        payload['observed_state']=result.get('observed_state')
        payload['expected_failure_ref']=result.get('expected_failure_ref')
        payload['consumer_state']=consumer_state(base,since)
        payload['note']=(payload['note']+' Consumer health is asserted separately from the case verdict: a '
                         'source-predicted failure is only credible while both consumer containers are running with '
                         'zero restarts.')
        out=args.run/('route-evidence-'+args.kafka_case+'.json')
    elif args.case or args.bank_case:
        payload['case']=args.case or args.bank_case
        out=args.run/('route-evidence-'+(args.case or args.bank_case)+'.json')
    else:
        if profile=='kafka':
            # A kafka-profile golden run (the Shared expected failure) is only credible while
            # the consumer survived it; record the same liveness block as the kafka cases.
            payload['consumer_state']=consumer_state(base,since)
        out=args.run/'route-evidence.json'
    out.write_text(json.dumps(payload,indent=2,default=str)+'\n')
    print(json.dumps({k:v for k,v in payload.items() if k!='records'},default=str))
    if 'consumer_state' in payload:
        return 0 if payload['transport_observed'] and payload['consumer_state']['healthy'] else 1
    return 0 if payload['transport_observed'] else 1

if __name__=='__main__':raise SystemExit(main())
