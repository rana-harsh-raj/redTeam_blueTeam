"""One scoped read of persisted scenario evidence; never infers queue messages.

No retries, status changes, callbacks, or broad datastore scans. Queries have
row caps; HTTP reads have a five-second deadline. Callers may wrap the complete
operation in their existing scenario deadline. `complete` covers these reads,
not the unobserved queue/transition history described in observability.
"""
import time
from . import db, trace


def record_snapshot(payouts_conn, fts_conn, ledger_conn, sink_client, payout_id,
                    *, bank_client=None, monolith_client=None, label='scenario_completion'):
    bare = str(payout_id).removeprefix('pout_')
    if len(bare) != 14 or not bare.isalnum():
        raise ValueError('snapshot requires a real 14-character payout identifier')
    public = 'pout_' + bare
    captured = {'label':label, 'payout_id':public, 'observed_at':time.time(),
                'complete':False, 'errors':[], 'layers':{},
                'observability':{
                    'payout_state_history':'persisted payout_logs, ordered by created_at and id',
                    'fts_attempt_chain':'all persisted attempts for the source transfer(s), ordered by id',
                    'fts_state_history':'attempt rows expose current status; an event history is not inferred',
                    'queue_events':'not_observed; no queue message or acknowledgment is fabricated',
                    'atomicity':'successive reads across databases; not a cross-service atomic snapshot',
                    'capture_retention':'bank and webhook capture endpoints retain bounded process-local history',
                }}

    def read(name, callback, default):
        try:
            return callback()
        except Exception as exc:
            captured['errors'].append({'layer':name,'error_type':type(exc).__name__,'message':str(exc)})
            return default

    def capped(name, conn, sql, params, maximum):
        rows = read(name, lambda:db.fetchall(conn,sql + ' LIMIT %s',tuple(params)+(maximum+1,)), [])
        if len(rows)>maximum:
            captured['errors'].append({'layer':name,'error_type':'SnapshotRowLimit','maximum':maximum})
        # PyMySQL returns tuples for fetchall (including an empty tuple),
        # while psycopg returns lists. Expose one sequence type across layers.
        return list(rows[:maximum])

    payout = read('payouts',lambda:db.fetchone(payouts_conn,'SELECT * FROM payouts WHERE id=%s',(bare,)),None)
    if payout is None:
        captured['errors'].append({'layer':'payouts','error_type':'PayoutNotFound'})
    merchant = payout['merchant_id'] if payout else None
    captured['merchant_id'] = merchant
    logs = capped('payout_logs',payouts_conn,'SELECT * FROM payout_logs WHERE payout_id=%s ORDER BY created_at,id',(bare,),500)
    reversals = capped('reversals',payouts_conn,'SELECT * FROM reversals WHERE payout_id=%s ORDER BY created_at,id',(bare,),20)
    captured['layers']['payouts']={'row':payout,'state_history':logs,'reversals':reversals}

    transfers = capped('fts_transfers',fts_conn,"SELECT * FROM transfers WHERE source_id=%s AND source_type='payout' ORDER BY id",(bare,),20)
    transfer_ids=[row['id'] for row in transfers]
    attempts=[]
    if transfer_ids:
        placeholders=','.join(['%s']*len(transfer_ids))
        attempts=capped('fts_attempts',fts_conn,
                        'SELECT * FROM attempts WHERE transfer_id IN ('+placeholders+') ORDER BY id',
                        transfer_ids,500)
    metadata=capped('fts_transfer_meta',fts_conn,
                    'SELECT * FROM transfer_meta WHERE source_id=%s ORDER BY id',(bare,),20)
    captured['layers']['fts']={'transfers':transfers,'attempt_chain':attempts,'transfer_meta':metadata}

    transactors=[public]+['rvrsl_'+r['id'] for r in reversals]
    journal_ids=[]
    for row in ([payout] if payout else [])+reversals:
        if row.get('transaction_id'):
            journal_ids.append(str(row['transaction_id']).removeprefix('txn_'))
    journals=capped('ledger_journals',ledger_conn,
                    'SELECT * FROM journal WHERE transactor_id = ANY(%s) OR id = ANY(%s) ORDER BY created_at,id',
                    (transactors,journal_ids),100)
    entries=[]
    if journals:
        entries=capped('ledger_entries',ledger_conn,
                       'SELECT * FROM ledger_entries WHERE journal_id = ANY(%s) ORDER BY created_at,id',
                       ([j['id'] for j in journals],),1000)
    accounts=[]
    if entries:
        accounts=capped('ledger_accounts',ledger_conn,
                        'SELECT * FROM accounts WHERE id = ANY(%s) ORDER BY id',
                        (sorted({e['account_id'] for e in entries}),),200)
    captured['layers']['ledger']={'transactor_ids':transactors,'journals':journals,
                                  'entries':entries,'accounts_at_observation':accounts}

    def merchant_events():
        response=sink_client.get('/_arena/deliveries',timeout=5)
        if response.status!=200:
            raise RuntimeError('merchant webhook capture returned HTTP '+str(response.status))
        return [d for d in response.json()['deliveries']
                if d.get('merchant')==merchant
                and d.get('body',{}).get('payload',{}).get('payout',{}).get('entity',{}).get('id')==public]
    captured['layers']['merchant_webhooks']={
        'deliveries':read('merchant_webhooks',merchant_events,[]),
        'boundary':'actual merchant-webhook-sink HTTP receipt; signature bytes are redacted',
    }
    if bank_client:
        def bank_events():
            response=bank_client.get('/_arena/scenarios',timeout=5)
            if response.status!=200:
                raise RuntimeError('bank capture returned HTTP '+str(response.status))
            ids={str(a['id']) for a in attempts}
            return [e for e in response.json()['events'] if str(e.get('attempt_id')) in ids]
        captured['layers']['bank']={'events':read('bank',bank_events,[])}
    else:
        captured['layers']['bank']={'events':None,'status':'not_observed_by_this_snapshot'}

    if monolith_client:
        def relay_events():
            response=monolith_client.get('/_arena/log',timeout=5)
            if response.status!=200:
                raise RuntimeError('monolith capture returned HTTP '+str(response.status))
            def belongs(value):
                if isinstance(value,dict):
                    return any((k in ('payout_id','source_id','id') and str(v) in (bare,public)) or belongs(v) for k,v in value.items())
                if isinstance(value,list):return any(belongs(v) for v in value)
                return False
            return [e for e in response.json()['log'] if belongs(e.get('payload'))]
        captured['layers']['monolith']={'events':read('monolith',relay_events,[]),
            'boundary':'observed substitute calls; absence alone does not prove a different route'}

    captured['complete']=not captured['errors']
    captured['finished_at']=time.time()
    captured['duration_seconds']=captured['finished_at']-captured['observed_at']
    clean=trace.clean(captured)
    trace.record('scenario_snapshot',clean)
    return clean
