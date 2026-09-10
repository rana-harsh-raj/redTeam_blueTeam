#!/usr/bin/env python3
"""Recompute invariants from SQL, broker records, API responses and boundary audit.
Never trust the producer's accepted flag or its assertion list.
"""
import json
from pathlib import Path

def verify(j):
 checks={}
 def check(name,value): checks[name]=bool(value)
 def decoded(value): return json.loads(value) if isinstance(value,str) else value
 db=j['database']; ids=j['ids']; snaps=j['snapshots']; tax=ids['tax_payment_id']; payout=ids['remittance_payout_id']
 records=db['records']; rows=db['tax_payments']; associations=db['vendorpayment_payout']; trace=db['replica_trace']; messages=j['kafka_messages']
 check('source_sha',j['source_sha']=='20c4f4d59970471067388afea8b1d65ac39ee126')
 check('broker_input',len(messages)==3 and [m['offset'] for m in messages]==[0,1,2] and all(m['topic']=='add-tds-entry' for m in messages) and json.loads(messages[0]['value'])==j['input']==json.loads(messages[1]['value']) and json.loads(messages[2]['value'])['data']['entity_type']=='invalid_entity')
 received=[json.loads(t['payload']) for t in trace if t['kind']=='kafka_received']
 check('source_consumer',len(received)==3 and all(any(r['offset']==m['offset'] and r['value']==m['value'] for r in received) for m in messages) and sum(t['kind']=='kafka_committed' for t in trace)==3)
 check('record_persistence',len(records)==1 and float(records[0]['amount'])==12500 and records[0]['entity_id']==ids['source_payout_id'] and records[0]['added_to_tax_payment_id']==tax)
 check('duplicate_zero_delta',snaps['before_duplicate']==snaps['after_duplicate'] and len(snaps['after_duplicate']['records'])==1 and records==snaps['after_duplicate']['records'])
 check('negative_contact',j['negative']['code']==422 and 'contact' in j['negative']['body']['error'].lower() and snaps['before_negative']['vendorpayment_payout']==snaps['after_negative']['vendorpayment_payout']==[] and snaps['before_negative']['boundary']['payouts']==snaps['after_negative']['boundary']['payouts'])
 check('remittance_persistence',len(associations)==1 and associations[0]['payout_id']==payout and associations[0]['tax_payment_id']==tax and float(associations[0]['amount'])==12500 and associations[0]['payout_status']=='processed')
 check('state_scope',len(rows)==1 and rows[0]['id']==tax and rows[0]['internal_status']=='money_loading_success' and rows[0]['status']=='processing' and rows[0]['merchant_id']==ids['merchant_id'] and len(db['tds'])==1 and db['tds'][0]['tax_payment_id']==tax)
 response=j['source_response']['tax_payment']
 check('api_agrees',response['code']==200 and response['body']['tax_payment']['internal_status']==rows[0]['internal_status'] and response['body']['tax_payment']['status']==rows[0]['status'])
 callback_traces=[decoded(t['payload']) for t in trace if t['kind']=='callback_received']
 callback_expected={'payout_id':payout,'payout_status':'processed','merchant_id':ids['merchant_id'],'source_id':tax,'source_type':'tax_payments'}
 check('callback',j['callback']['code']==200 and j['callback']['body'].get('success') is True and len(callback_traces)==1 and all(callback_traces[0].get(k)==v for k,v in callback_expected.items()))
 check('pay_controls',j['source_response']['current_month_pay']['code']==422 and j['source_response']['pay']['code']==200 and j['source_response']['repeated_pay']['code']==422)
 payouts=j['boundary']['payouts']; origin=[p for p in payouts if p['id']==ids['source_payout_id']]; remit=[p for p in payouts if p['id']==payout]
 check('tagback',len(payouts)==2 and len(origin)==1 and origin[0]['tax_payment_id']==tax and len(remit)==1)
 check('idempotency_boundary',len(remit)==1 and bool(associations[0]['idempotency_key']) and remit[0]['idempotency_key']==associations[0]['idempotency_key'])
 audits=j['boundary']['audit']; tags=[a for a in audits if a['method']=='PATCH' and a['path'].endswith('/tax-payment-id')]; creates=[a for a in audits if a['method']=='POST' and a['path']=='/v1/internalContactPayout/']
 tag=tags[0] if len(tags)==1 else {}; tag_request=decoded(tag.get('request','{}')); tag_headers=tag_request.get('headers',{}); tag_body=tag_request.get('body',{})
 check('tagback_request',len(tags)==1 and tag.get('path')==f"/v1/payouts_internal/{ids['source_payout_id']}/tax-payment-id" and tag.get('app')=='vendor_payments' and tag.get('merchant_id')==ids['merchant_id'] and tag.get('response_code')==200 and decoded(tag.get('response','{}'))=={'status':'SUCCESS'} and tag_body=={'tax_payment_id':tax} and tag_headers.get('X-Razorpay-Account')==ids['merchant_id'] and tag_headers.get('X-Request-ID')==tag_headers.get('X-Razorpay-TaskId')=='s2p-golden-20260820-001')
 create=creates[0] if len(creates)==1 else {}; create_request=decoded(create.get('request','{}')); create_headers=create_request.get('headers',{}); create_body=create_request.get('body',{}); source_details=create_body.get('source_details',[])
 stored_request=decoded(remit[0].get('request','{}')) if len(remit)==1 else {}; stored_response=decoded(remit[0].get('response','{}')) if len(remit)==1 else {}
 check('remittance_request',len(creates)==1 and create.get('app')=='vendor_payments' and create.get('merchant_id')==ids['merchant_id'] and create.get('response_code')==200 and create_headers.get('X-Razorpay-Account')==ids['merchant_id'] and create_headers.get('X-Request-ID')==create_headers.get('X-Razorpay-TaskId')=='s2p-golden-20260820-001' and bool(create_headers.get('X-Dashboard-User-Id')) and create_headers.get('X-Payout-Idempotency')==associations[0]['idempotency_key'] and create_body==stored_request and create_body.get('account_number')=='7878780011' and create_body.get('fund_account_id')=='fa_tax_internal_1' and create_body.get('amount')==12500 and create_body.get('currency')=='INR' and create_body.get('mode')=='IMPS' and create_body.get('purpose')=='tax_payment' and source_details==[{'priority':1,'source_id':tax,'source_type':'tax_payments'}] and stored_response.get('id')==payout and decoded(create.get('response','{}'))==stored_response)
 check('tagback_before_remittance',len(tags)==1 and len(creates)==1 and tag['id']<create['id'])
 correlated=[json.loads(a['request']).get('headers',{}) for a in audits if isinstance(json.loads(a['request']),dict)]
 correlated=[h for h in correlated if h.get('X-Request-ID')]
 check('correlation',bool(correlated) and all(h['X-Request-ID']==h['X-Razorpay-TaskId']=='s2p-golden-20260820-001' for h in correlated) and all(t['correlation_id']=='s2p-golden-20260820-001' for t in trace))
 statuses=[decoded(t['payload']) for t in trace if t['kind']=='source_pubsub' and decoded(t['payload']).get('topic')=='tax_payment_updated']
 # The source's initial unpaid publication can carry an empty Id. Later money-loading
 # publications carry the tax-payment identity and are the independently useful link.
 scoped=[p.get('data',{}) for p in statuses if p.get('data',{}).get('Id')==tax]
 check('source_pubsub_states',[p.get('Status') for p in scoped]==['money_loading_initiated','money_loading_success'])
 return {'passed':all(checks.values()),'checks':checks,'business_outcome':{'records':len(records),'amount':12500,'tax_status':rows[0]['status'],'tax_internal_status':rows[0]['internal_status'],'associations':len(associations),'broker_offsets':[m['offset'] for m in messages],'boundary_payouts':len(payouts)}}

if __name__=='__main__':
 import sys
 result=verify(json.loads(Path(sys.argv[1]).read_text())); print(json.dumps(result,indent=2)); sys.exit(0 if result['passed'] else 1)
