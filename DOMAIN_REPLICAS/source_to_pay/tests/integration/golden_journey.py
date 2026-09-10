#!/usr/bin/env python3
"""Black-box acceptance for the source-backed TDS entry and remittance flow."""
import csv, hashlib, io, json, os, pathlib, subprocess, sys, time
ROOT=pathlib.Path(__file__).parents[2]
ENV={**os.environ,'COMPOSE_PROJECT_NAME':os.getenv('COMPOSE_PROJECT_NAME','s2p_architecture_replica')}
COMPOSE=['docker','compose','-f',str(ROOT/'docker-compose.yml')]
MERCHANT='S2PMerchant001'; SOURCE_PAYOUT='pout_00000000000001'
AUTH={'Authorization':'Basic cnpwX2xpdmU6bG9jYWwtdmVuZG9yLXBheW1lbnRzLXNlY3JldA==','X-Razorpay-Account':MERCHANT,'Content-Type':'application/json'}
def run(*args,input=None):return subprocess.run(list(args),input=input,text=True,capture_output=True,check=True,env=ENV).stdout
def dc(*args,input=None):return run(*COMPOSE,*args,input=input)
def sql(query):
 out=dc('exec','-T','mysql','env','MYSQL_PWD=s2p','mysql','-B','--raw','-us2p','vendor_payments','-e',query)
 return list(csv.DictReader(io.StringIO(out),delimiter='\t'))
def http(service,path,method='GET',body=None,headers=None):
 # Execute from boundary's Python runtime; no host port or external network is opened.
 code="""import json,sys,urllib.request,urllib.error
d=json.load(sys.stdin); r=urllib.request.Request('http://'+d['service']+':8080'+d['path'],json.dumps(d['body']).encode() if d['body'] is not None else None,d['headers'],method=d['method'])
try:
 x=urllib.request.urlopen(r); print(json.dumps({'code':x.status,'body':json.load(x)}))
except urllib.error.HTTPError as e: print(json.dumps({'code':e.code,'body':json.load(e)}))"""
 return json.loads(dc('exec','-T','monolith-boundary','python','-c',code,input=json.dumps({'service':service,'path':path,'method':method,'body':body,'headers':headers or {}})))
def boundary(path,method='GET',body=None,headers=AUTH):return http('boundary',path,method,body,headers)
def source(path,method='GET',body=None):return http('vp-source',path,method,body,{'Content-Type':'application/json'})
def wait_for(fn,timeout=45):
 end=time.time()+timeout; last=None
 while time.time()<end:
  try:
   last=fn()
   if last:return last
  except Exception as e:last=e
  time.sleep(.5)
 raise AssertionError(f'timed out; last={last!r}')
def publish(payload):dc('exec','-T','kafka','rpk','topic','produce','add-tds-entry','-k','s2p-golden-20260820-001',input=json.dumps(payload,separators=(',',':'))+'\n')
def committed_count():return len(sql("SELECT * FROM replica_trace WHERE kind='kafka_committed' ORDER BY id"))
def concatenated_json(text):
 decoder=json.JSONDecoder(); result=[]; pos=0
 while pos<len(text):
  while pos<len(text) and text[pos].isspace():pos+=1
  if pos<len(text):
   item,pos=decoder.raw_decode(text,pos); result.append(item)
 return result
def main():
 started=time.monotonic()
 entry=json.loads((ROOT/'fixtures/tds-entry.json').read_text()); invalid=json.loads((ROOT/'fixtures/tds-entry-invalid.json').read_text())
 assert boundary('/_control/seed-source-payout','POST',{'id':SOURCE_PAYOUT})['code']==200
 before_records=sql('SELECT * FROM records ORDER BY id')
 publish(entry); wait_for(lambda:committed_count()>=1)
 tag=wait_for(lambda:(lambda x:x if x['payouts'] and x['payouts'][0]['tax_payment_id'] else None)(boundary('/_evidence')['body']))
 records=sql('SELECT * FROM records ORDER BY id'); assert len(records)==len(before_records)+1; before_duplicate={'records':records,'tax_payments':sql('SELECT * FROM tax_payments ORDER BY id')}
 tax_rows=sql("SELECT * FROM tax_payments WHERE merchant_id='S2PMerchant001' ORDER BY created_at")
 assert len(tax_rows)==1; tax_id=tax_rows[0]['id']; assert tag['payouts'][0]['tax_payment_id']==tax_id
 trace=sql("SELECT * FROM replica_trace WHERE kind IN ('kafka_received','source_handler_returned','kafka_committed') ORDER BY id")
 assert {x['kind'] for x in trace}=={'kafka_received','source_handler_returned','kafka_committed'}
 # Exact replay: actual Core computes a zero delta and does not add another record.
 publish(entry); wait_for(lambda:committed_count()>=2); duplicate_records=sql('SELECT * FROM records ORDER BY id'); after_duplicate={'records':duplicate_records,'tax_payments':sql('SELECT * FROM tax_payments ORDER BY id')}; assert len(duplicate_records)==len(records)
 # Invalid source payload is swallowed by upstream ProcessMessage; observable state must remain unchanged.
 publish(invalid); wait_for(lambda:committed_count()>=3); assert len(sql('SELECT * FROM records ORDER BY id'))==len(records)
 # Contact absence is tested through the same real Pay path before a successful remittance.
 before_negative={'vendorpayment_payout':sql('SELECT * FROM vendorpayment_payout ORDER BY id'),'boundary':boundary('/_evidence')['body']}
 assert boundary('/_control/contact','POST',{'enabled':False})['body']['enabled'] is False
 pay={'merchant_id':MERCHANT,'purpose':'tax_payment','mode':'IMPS','notes':{'journey':'synthetic'} ,'narration':'Synthetic TDS remittance','account_number':'7878780011','user_id':'S2PUser0000001','otp':'754081','token':'synthetic-token','tax_payment_id':tax_id,'queue_if_low_balance':False,'amount':12500}
 denied=source('/_replica/pay','POST',pay); assert denied['code']==422 and 'contact' in denied['body']['error'].lower()
 after_negative={'vendorpayment_payout':sql('SELECT * FROM vendorpayment_payout ORDER BY id'),'boundary':boundary('/_evidence')['body']}; assert len(after_negative['vendorpayment_payout'])==len(before_negative['vendorpayment_payout'])
 assert boundary('/_control/contact','POST',{'enabled':True})['body']['enabled'] is True
 current_month=source('/_replica/pay','POST',pay); assert current_month['code']==422,current_month
 advance=source('/_replica/clock','POST',{'unix':1788220800}); assert advance['code']==200 # 2026-09-01T00:00:00Z
 paid=source('/_replica/pay','POST',pay); assert paid['code']==200,paid
 boundary_state=boundary('/_evidence')['body']; remittance=[x for x in boundary_state['payouts'] if x['id']!=SOURCE_PAYOUT]; assert len(remittance)==1
 payout_id=remittance[0]['id']; callback={'payout_id':payout_id,'payout_status':'processed','merchant_id':MERCHANT,'source_id':tax_id,'source_type':'tax_payments','utr':'SYNTHETICUTR001'}
 cb=source('/_replica/status','POST',callback); assert cb['code']==200 and cb['body'].get('success') is True,cb
 observed=source('/_replica/tax-payment?merchant_id='+MERCHANT+'&id='+tax_id); assert observed['code']==200,observed
 internal=observed['body']['tax_payment']['internal_status']; assert internal=='money_loading_success',internal; assert observed['body']['tax_payment']['status']=='processing'
 repeated=source('/_replica/pay','POST',pay); assert repeated['code']==422,repeated
 final_tables={name:sql('SELECT * FROM '+name+' ORDER BY id') for name in ('tax_payments','records','tds','vendorpayment_payout','replica_trace')}
 assert len(final_tables['vendorpayment_payout'])==1 and float(final_tables['vendorpayment_payout'][0]['amount'])==12500
 final_boundary=boundary('/_evidence')['body']; source_assoc=final_tables['vendorpayment_payout'][0]; boundary_remit=[x for x in final_boundary['payouts'] if x['id']==payout_id][0]; assert source_assoc['idempotency_key']==boundary_remit['idempotency_key']
 correlated=[]
 for item in final_boundary['audit']:
  try:req=json.loads(item['request'])
  except Exception:continue
  if isinstance(req,dict) and isinstance(req.get('headers'),dict) and req['headers'].get('X-Request-ID'):correlated.append(req['headers'])
 assert correlated and all(x['X-Request-ID']=='s2p-golden-20260820-001' and x['X-Razorpay-TaskId']=='s2p-golden-20260820-001' for x in correlated)
 partition_text=dc('exec','-T','kafka','rpk','topic','describe','add-tds-entry','-p'); partition_lines=[x.split() for x in partition_text.splitlines() if x.strip()]; partitions=[dict(zip(partition_lines[0],x)) for x in partition_lines[1:]]
 broker_text=dc('exec','-T','kafka','rpk','topic','consume','add-tds-entry','-o','start','-n','3','-f','json'); kafka_messages=concatenated_json(broker_text)
 assertion_ids=('source_consumer_trace','source_mysql_entry_persisted','tagback_persisted','duplicate_zero_delta','invalid_payload_no_state_change','missing_contact_rejected_by_actual_pay','current_month_rejected','internal_contact_remittance_created','payout_callback_reached_actual_source','terminal_scope_money_loading_success','repeated_pay_no_duplicate','idempotency_key_cross_boundary','correlation_id_cross_boundary')
 assertions=[{'id':x,'passed':True} for x in assertion_ids]; accepted=all(x['passed'] for x in assertions)
 locks=json.loads((ROOT/'runtime/image-lock.json').read_text()); compose_bytes=(ROOT/'docker-compose.yml').read_bytes()
 evidence={'schema_version':'s2p-golden-evidence/v1','accepted':accepted,'source_sha':source('/health')['body']['source_sha'],'duration_monotonic_seconds':round(time.monotonic()-started,6),'environment':{'image_lock':locks,'compose_sha256':hashlib.sha256(compose_bytes).hexdigest()},'input':entry,'topics':{'input':'add-tds-entry','observed_partitions':partitions},'kafka_messages':kafka_messages,'ids':{'merchant_id':MERCHANT,'source_payout_id':SOURCE_PAYOUT,'tax_payment_id':tax_id,'remittance_payout_id':payout_id},'snapshots':{'before_duplicate':before_duplicate,'after_duplicate':after_duplicate,'before_negative':before_negative,'after_negative':after_negative},'negative':denied,'callback':cb,'source_response':{'current_month_pay':current_month,'pay':paid,'tax_payment':observed,'repeated_pay':repeated},'database':final_tables,'boundary':final_boundary,'assertions':assertions}
 out=pathlib.Path(os.getenv('EVIDENCE_OUT',str(ROOT/'artifacts/journey-latest.json'))); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(evidence,indent=2,sort_keys=True)+'\n'); (ROOT/'artifacts/journey-latest.log').write_text(dc('logs','--no-color','--tail=400')); print(json.dumps({'accepted':accepted,'evidence':str(out),'tax_payment_id':tax_id,'remittance_payout_id':payout_id},sort_keys=True))
if __name__=='__main__':
 try:main()
 except Exception as e: print(json.dumps({'accepted':False,'error':str(e)}),file=sys.stderr); raise
