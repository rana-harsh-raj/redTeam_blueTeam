#!/usr/bin/env python3
"""Compare complete passing verifier runs using final DB reads and HTTP evidence.

Explicit clock fields, identified generated string IDs and typed FTS transfer/
attempt autoincrements are normalized. Named UTR fixtures remain literal; only
the local bank's attempt-derived UTR is aliased. Poll counts are excluded; the
final read per query or GET URL is retained. Concurrent HTTP requests are paired
by method/URL; mutation request order remains material. Capture collection arrival
order is normalized, with all nested payloads, signatures, unknown fields and
duplicates retained. Raw webhook body hashes are verified before canonicalization.
Other lists and SQL ORDER BY results retain order. Status differences stay visible.
The exact low-balance cron response is a multiset of per-balance outcomes because
its source collects map/worker results without an ordering contract.
This compares recorded observations, not every possible execution behavior.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

CLOCK_FIELDS={'created_at','updated_at','timestamp','at','delivered_at','received_at',
              'queued_at','scheduled_at','initiate_at','on_hold_at','expires_at',
              'started_at','finished_at','duration','duration_seconds',
              'transfer_by','dispatched_at','transaction_date',
              'created_at_microsecond','updated_at_microsecond',
              'payout_updated_at'}  # V16 (M1) snapshots the payout clock before/after; equality is asserted in-test
GENERATED=re.compile(r'(?:(pout_|rvrsl_))?([A-Za-z0-9]{14})$')
UUID=re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$',re.I)
EVENT_ID=re.compile(r'(ev_|wh_)ARENA[0-9]+$')
TOPUP_ID=re.compile(r'arenatopup_[0-9a-f]{14}$')
LOW_BALANCE_CRON_URL='http://payouts-api:9400/v1/cron/process_queued_low_balance_payouts'
MONOLITH_LOG_URL='http://monolith-stub:8080/_arena/log'
# Global (unfiltered) capture endpoints: one list for the whole suite, appended by
# concurrent workers. A test reading one is compared only on the entries that
# reference its own payout identities. Filtered reads (?merchant=...) are untouched.
GLOBAL_CAPTURES={MONOLITH_LOG_URL:'log','http://merchant-webhook-sink:8080/_arena/deliveries':'deliveries',
                 'http://stork-capture:8080/_arena/events':'events','http://stork-capture:8080/_captured/events':'events'}
GATEWAY_REF=re.compile(r'[A-Za-z0-9]{12}$')
RESERVATION_INSPECT_URL='http://payouts-api:9400/v1/inflight_reservations'
NORMALIZATION_SOURCES={
    'concurrent_http':'verifier/verifiers/test_v17_reversal_ordering.py:16-25; verifier/helpers/http_client.py:65-73',
    'fts_autoincrements':'fts/internal/migrations/00005_transfers_table_create.go:15; 00006_attempts_table_create.go:15',
    'attempt_derived_utr':'substitutes/mozart-sim/server.py:53-68',
    'transfer_by_clock':'fts/internal/transfer/attempt_processor.go:2217-2237',
    'dispatched_at_clock':'payouts/internal/app/payouts/processor/queueingstrategy/reservation_gate.go:241',
    'ledger_clocks':'ledger/internal/database/pg_migrations/20201001011143_create_journal.go:23-27; verifier/verifiers/test_v04_ledger_dedupe.py:14',
    'topup_identity_clock':'verifier/helpers/payouts_flow.py:199-201',
    'scheduled_fixture_offset':'verifier/verifiers/test_v24_cron_dequeue.py:93-96',
    'capture_ids_arrival_order':'substitutes/stork-capture/server.py:26-32,42-43,128-134',
    'capture_digest':'substitutes/merchant-webhook-sink/server.py:50-70; substitutes/stork-capture/server.py:136-142',
    'low_balance_cron_response_order':'payouts/internal/app/payouts/helperQueuedPayouts.go:164-237; payouts/internal/app/payouts/core.go:4568-4615,4686-4719',
    'reservation_items_order':'payouts/internal/app/payouts/reservation/store.go:353-354 (ListItems = HGETALL of in_flight_items hash; unordered); payout_internal_routes.go:198',
    'global_capture_scope':'substitutes/merchant-webhook-sink/server.py:97 (/_arena/deliveries) and substitutes/stork-capture/server.py:220-236 (/_arena/events) are suite-wide unfiltered captures; V16 (M1) reads the sink unfiltered',
    'monolith_log_scope':'substitutes/monolith-stub/server.py:65-70,99-100 (one global in-memory sink LOG shared by every test, appended by concurrent workers); verifier/verifiers/test_v06_ledger_accounting_processed.py and test_v16_stuck_initiated_no_repair.py read it (M1)',
    'gateway_ref_no':'substitutes/mozart-sim/server.py:24,55,69 (simulator-generated 12-character bank reference per transfer_init)',
    'v16_clock_snapshot':'verifier/verifiers/test_v16_stuck_initiated_no_repair.py (payout_updated_at recorded as an explicit clock; the test asserts it is unchanged across the window)',
}


def expected_tests():
    folder=Path(__file__).resolve().parents[1]/'verifier/verifiers'
    return {node.name for f in folder.glob('test_v*.py') for node in ast.walk(ast.parse(f.read_text()))
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name.startswith('test_')}


def payout_identities(files):
    """Stable cross-test payout identities for the sink's arena-wide collection.

    Each fixture namespace belongs to one test. Use that test's create-response
    order, never delivery arrival order, to name its returned payout identities.
    """
    identities={}
    for name,file in sorted(files.items()):
        seen=[]
        try:
            records=[json.loads(line) for line in file.read_text().splitlines() if line]
            for record in records:
                data=record.get('data',{});body=data.get('body',{})
                if (record.get('kind')=='http_response' and data.get('method')=='POST'
                        and data.get('url','').endswith('/v1/payouts') and isinstance(body,dict)
                        and isinstance(body.get('id'),str) and body['id'].startswith('pout_')):
                    raw=body['id'][5:]
                    if raw not in seen:
                        seen.append(raw)
                        identities[('entity',raw)]=name+':payout_'+str(len(seen))
        except (OSError,ValueError,TypeError):
            pass  # summarize reports malformed evidence; no validity is granted here.
    return identities


class Normalizer:
    def __init__(self,identities=None,test=None):
        self.aliases=dict(identities or {})
        self.numeric={}
        self.test=test

    def alias(self,value,kind='entity'):
        key=(kind,value)
        if key not in self.aliases:
            self.aliases[key]=kind+'_'+str(sum(k[0]==kind for k in self.aliases)+1)
        return self.aliases[key]

    def string(self,value,key=None):
        if key=='gateway_ref_no' and GATEWAY_REF.fullmatch(value):
            # mozart-sim mints a fresh 12-character bank reference per transfer_init;
            # it carries no business meaning beyond identity.
            return self.alias(value,'gateway_ref')
        if key in ('utr','return_utr'):
            # mozart-sim _controlled derives this UTR from the generated attempt
            # ID. Named test vectors (UTR_V21, SIM_UTR_100, etc.) remain literal.
            if re.fullmatch(r'ARENA[0-9]{10}',value):
                return 'ARENA_'+self.numeric_id(int(value[5:]),'fts_attempt')
            return value
        # Arbitrary 14-character status, reason, money and account strings are
        # never mistaken for generated IDs. Preserve fixture ARENA identities.
        if key and (key=='id' or key.endswith('_id') or key in ('idempotency_key','key')):
            match=GENERATED.fullmatch(value)
            if match and not match[2].startswith('ARENA'):
                return (match[1] or '')+self.alias(match[2])
            if UUID.fullmatch(value) or EVENT_ID.fullmatch(value) or TOPUP_ID.fullmatch(value):
                return self.alias(value)
        # Substitute only IDs already observed in an identity-bearing field.
        for (kind,raw),alias in sorted(self.aliases.items(),key=lambda x:-len(x[0][1])):
            if kind=='entity':value=value.replace(raw,alias)
        return value

    def numeric_id(self,value,kind):
        if isinstance(value,int) and not isinstance(value,bool) and value>0:
            pair=(kind,value)
            if pair not in self.numeric:
                self.numeric[pair]=kind+'_'+str(sum(k[0]==kind for k in self.numeric)+1)
            return self.numeric[pair]
        return value

    def clean(self,value,key=None,table=None):
        if isinstance(value,dict):
            # A recorded digest is checked against the exact compact JSON shape
            # emitted by the local Stork adapter before deriving a semantic hash.
            # Missing/tampered digests do not disappear through normalization.
            if 'body_sha256' in value and isinstance(value.get('body'),dict):
                original=json.dumps(value['body'],ensure_ascii=False,separators=(',',':')).encode()
                if hashlib.sha256(original).hexdigest()!=value['body_sha256']:
                    raise ValueError('webhook body_sha256 does not match recorded body')
            cleaned={k:self.clean(v,k,table if k=='rows' or (key=='rows' and k=='id') else None)
                     for k,v in sorted(value.items()) if k not in CLOCK_FIELDS}
            if 'body_sha256' in value and isinstance(value.get('body'),dict):
                cleaned['body_sha256']=hashlib.sha256(json.dumps(cleaned['body'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
            return cleaned
        if isinstance(value,list):
            items=[self.clean(v,key,table) for v in value]
            # Debug capture collections do not promise arrival order. Keep all
            # records and duplicates; ordinary lists and SQL ORDER BY stay ordered.
            if key in ('deliveries','events') and all(isinstance(v,dict) and 'event_id' in v for v in value):
                items.sort(key=lambda v:json.dumps(v,sort_keys=True))
            return items
        if isinstance(value,str):
            if value.startswith(('{','[')):
                try:return self.clean(json.loads(value),key,table)
                except ValueError:pass
            return self.string(value,key)
        if key in ('fts_transfer_id','fund_transfer_id','transfer_id') or (key=='id' and table=='transfers'):
            return self.numeric_id(value,'fts_transfer')
        if key=='attempt_id' or (key=='id' and table=='attempts'):
            return self.numeric_id(value,'fts_attempt')
        return value

    def record(self,record):
        data=record['data'];sql=data.get('sql','')
        match=re.search(r'\bfrom\s+(transfers|attempts)\b',sql,re.I)
        table=match[1].lower() if match else None
        cleaned=self.clean(data,table=table)
        if (record['kind']=='http_response' and data.get('method')=='POST'
                and data.get('url')==LOW_BALANCE_CRON_URL
                and isinstance(cleaned.get('body'),list)
                and all(isinstance(row,dict) and isinstance(row.get('balance_id'),str)
                        for row in cleaned['body'])):
            # The source unions explicit balances with queued cache/DB balances,
            # then collects Go map/worker results. Preserve whole rows, duplicate
            # multiplicity, counts, errors and unknown fields; only order changes.
            cleaned['body']=sorted(cleaned['body'],key=lambda row:(row['balance_id'],json.dumps(row,sort_keys=True)))
        if (record['kind']=='http_response' and data.get('method')=='GET'
                and str(data.get('url','')).startswith(RESERVATION_INSPECT_URL)
                and isinstance(cleaned.get('body'),dict) and isinstance(cleaned['body'].get('items'),list)):
            # The inspect endpoint lists live reservations from a Redis hash via HGETALL
            # (reservation/store.go:353-354); Go map iteration gives no order. Keep every
            # item and its fields; only the order is dropped.
            cleaned['body']=dict(cleaned['body'],items=sorted(cleaned['body']['items'],key=lambda e:json.dumps(e,sort_keys=True)))
        field=GLOBAL_CAPTURES.get(data.get('url'))
        if (record['kind']=='http_response' and data.get('method')=='GET' and field
                and isinstance(cleaned.get('body'),dict)
                and isinstance(cleaned['body'].get(field),list)):
            # The monolith substitute exposes ONE global append-only sink log for the
            # whole suite. A test that reads it sees every other test's entries and the
            # arrival order of concurrent workers. Keep only entries that reference this
            # test's own payout identities (they carry that identity in their payload after
            # aliasing), keep duplicates, and drop arrival order; the other tests' entries
            # are compared inside those tests' own observations.
            own=self.test+':' if self.test else None
            entries=[e for e in cleaned['body'][field] if own is None or own in json.dumps(e,sort_keys=True)]
            cleaned['body']=dict(cleaned['body'],**{field:sorted(entries,key=lambda e:json.dumps(e,sort_keys=True)),
                                 'capture_scope':'entries referencing '+(self.test or 'any test')+' only; arrival order dropped'})
        if 'params' in data:
            params=list(cleaned['params'] or [])
            # Only these verified SQL placeholders denote generated FTS IDs;
            # account IDs, row counts, amounts and all other integers stay literal.
            fields=re.findall(r'(\w+)\s*=\s*%s',sql,re.I)
            for i,field in enumerate(fields):
                if i>=len(params):break
                if field.lower()=='transfer_id' or (field.lower()=='id' and table=='transfers'):
                    params[i]=self.numeric_id(data['params'][i],'fts_transfer')
            if record['kind']=='db_mutation' and sql=='UPDATE payouts SET scheduled_at=%s WHERE id=%s':
                # V24 moves only the clock fixture to now-60, retaining the
                # actual UPDATE and its relative offset rather than dropping it.
                params[0]={'seconds_from_observation':data['params'][0]-int(record['at'])}
            cleaned['params']=params
        return cleaned

    def register_captures(self,records):
        captures=[]
        def visit(value):
            if isinstance(value,dict):
                if 'event_id' in value and isinstance(value.get('body',value.get('payload')),dict):
                    captures.append(value)
                for child in value.values():visit(child)
            elif isinstance(value,list):
                for child in value:visit(child)
        for record in records:visit(record['data'])
        def key(capture):
            payload=capture.get('body',capture.get('payload',{}))
            payout=payload.get('payload',{}).get('payout',{}).get('entity',{})
            pid=payout.get('id','')
            raw=pid[5:] if pid.startswith('pout_') else pid
            return (capture.get('merchant',capture.get('owner_id','')),
                    self.aliases.get(('entity',raw),raw),payload.get('event',''),
                    payout.get('status',''),str(payout.get('amount','')))
        # Counter-derived IDs are attached to stable event content groups before
        # cleaning the collection; cross-merchant completion order is irrelevant.
        seen={};counts={}
        for capture in sorted(captures,key=key):
            raw=capture['event_id']
            if raw in seen:continue
            semantic=key(capture);counts[semantic]=counts.get(semantic,0)+1
            label='event_'+hashlib.sha256(json.dumps(semantic).encode()).hexdigest()[:16]+'_'+str(counts[semantic])
            self.aliases[('entity',raw)]=label;seen[raw]=label


def summarize(folder,expected=None):
    expected=expected_tests() if expected is None else set(expected)
    errors=[];outcomes={};summary={}
    try:
        suite=ET.parse(folder/'junit.xml').getroot()
        for test in suite.iter('testcase'):
            name=test.attrib['name']
            if name in outcomes:errors.append('duplicate JUnit test: '+name)
            outcomes[name]='failed' if test.find('failure') is not None or test.find('error') is not None else 'skipped' if test.find('skipped') is not None else 'passed'
    except (OSError,ValueError,KeyError,ET.ParseError) as exc:
        errors.append('Unreadable JUnit evidence: '+type(exc).__name__)
    if not expected:errors.append('No expected verifier tests were found')
    if set(outcomes)!=expected:errors.append('JUnit test inventory differs from expected suite')
    if any(v!='passed' for v in outcomes.values()):errors.append('All JUnit tests must pass without skips')
    files={f.stem:f for f in folder.glob('test_*.jsonl')}
    if set(files)!=expected:errors.append('Trace inventory differs from expected suite')
    identities=payout_identities(files)
    for name,file in sorted(files.items()):
        try:
            records=[json.loads(line) for line in file.read_text().splitlines() if line]
            if not records or any(not isinstance(r,dict) or not isinstance(r.get('data'),dict) for r in records):raise ValueError('malformed trace')
            if [r.get('sequence') for r in records]!=list(range(1,len(records)+1)):raise ValueError('trace sequence incomplete or repeated')
            fixtures=[r['data'] for r in records if r['kind']=='starting_fixture']
            phases=[r['data'] for r in records if r['kind']=='test_result']
            if len(fixtures)!=1 or not fixtures[0]:raise ValueError('one nonempty starting fixture required')
            if [r.get('when') for r in phases]!=['setup','call','teardown'] or any(r.get('outcome')!='passed' for r in phases):raise ValueError('complete passing test phases required')
            if any(r.get('nodeid','').split('::')[-1]!=name for r in phases):raise ValueError('test identity mismatch')
            latest={};mutations=[];observations=[];requests=[]
            for record in records:
                kind,data=record['kind'],record['data']
                if kind=='http_request':requests.append((record['sequence'],data))
                elif kind=='http_response':
                    matches=[i for i,(_,request) in enumerate(requests) if (request.get('method'),request.get('url'))==(data.get('method'),data.get('url'))]
                    if not matches:raise ValueError('unpaired HTTP response')
                    # Different URLs may legitimately overlap (V17). Two in-flight
                    # requests to the same URL cannot be unambiguously paired from
                    # this trace schema, so reject instead of guessing the body.
                    if len(matches)>1:raise ValueError('ambiguous concurrent HTTP requests')
                    sequence,request=requests.pop(matches[0])
                    record={'kind':kind,'sequence':sequence,'data':dict(data,request_body=request.get('body'))}
                    if data.get('method')=='GET':latest[('http',data['url'])]=record
                    else:mutations.append(record)
                elif kind=='db_read':latest[('db',data['sql'],json.dumps(data.get('params'),sort_keys=True))]=record
                elif kind not in ('starting_fixture','test_result'):observations.append(record)
            if requests:raise ValueError('HTTP request has no response')
            if not latest and not mutations:raise ValueError('no runtime observations')
            mutations.sort(key=lambda r:r['sequence'])
            normal=Normalizer(identities,test=name)
            # Establish identities from mutation responses first, then final
            # observations. Intermediate DB polls never allocate aliases.
            raw=mutations+list(latest.values())+observations
            normal.register_captures(raw)
            for record in raw:normal.record(record)
            final=[]
            for record in latest.values():
                data=normal.record(record)
                # SQL result order is meaningful only with an ORDER BY clause.
                if record['kind']=='db_read' and 'order by' not in data['sql'].lower() and isinstance(data.get('rows'),list):
                    data['rows']=sorted(data['rows'],key=lambda x:json.dumps(x,sort_keys=True))
                final.append({'kind':record['kind'],'data':data})
            summary[name]={'fixture':fixtures[0], 'final_observations':sorted(final,key=lambda x:json.dumps(x,sort_keys=True)),
                           'http_mutations':[normal.record(r) for r in mutations],
                           'mutation_history':[{'kind':r['kind'],'data':normal.record(r)}
                                               for r in sorted(mutations+[r for r in observations if r['kind']=='db_mutation'],
                                                               key=lambda r:r['sequence'])],
                           'other_observations':[{'kind':r['kind'],'data':normal.record(r)} for r in observations]}
        except (OSError,ValueError,KeyError,TypeError) as exc:
            errors.append(name+': '+str(exc))
    return {'valid':not errors,'validation_errors':errors,'tests':summary,'outcomes':outcomes}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('first',type=Path);p.add_argument('second',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    one,two=summarize(a.first),summarize(a.second)
    differences=[name for name in sorted(set(one['tests'])|set(two['tests'])) if one['tests'].get(name)!=two['tests'].get(name)]
    independent=a.first.resolve()!=a.second.resolve()
    same=one['valid'] and two['valid'] and independent and one['tests']==two['tests'] and one['outcomes']==two['outcomes']
    payload={'schema_version':3,'status':'passed' if same else 'failed','logical_equivalence':same,'independent_directories':independent,'differences':differences,
             'input_directories':{'first':str(a.first),'second':str(a.second)},
             'normalization':__doc__,'normalization_sources':NORMALIZATION_SOURCES,'first':one,'second':two}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'status':payload['status'],'different_tests':differences,'first_valid':one['valid'],'second_valid':two['valid'],'report':str(a.output)}))
    return 0 if same else 1
if __name__=='__main__':sys.exit(main())
