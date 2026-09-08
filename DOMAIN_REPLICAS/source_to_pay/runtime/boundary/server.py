"""Local replacement for the unavailable API-monolith/Payouts boundary.

The request and response shapes follow vendor-payments' rxclient, payout.Request,
payout.Payout and taxpayments BankingAccountAPIResponse. Basic-auth maps the
synthetic rzp_live credential to the vendor_payments internal application, as
the real caller does not send an internal-app header.
"""
import base64, json, os, sqlite3, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
DB=os.getenv('BOUNDARY_DB','/state/boundary.sqlite'); USER='rzp_live'; PASSWORD=os.getenv('API_SECRET','local-vendor-payments-secret')
FA_NUMBER=os.getenv('TAX_FA_NUMBER','000000000000001'); FA_IFSC=os.getenv('TAX_FA_IFSC','TEST0000001'); FA_ID='fa_tax_internal_1'; CONTACT_ID='cont_tax_internal_1'
os.makedirs(os.path.dirname(DB),exist_ok=True)
def db():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
 c.executescript('''CREATE TABLE IF NOT EXISTS payouts(id TEXT PRIMARY KEY,merchant_id TEXT,request TEXT,response TEXT,idempotency_key TEXT,status TEXT,tax_payment_id TEXT, UNIQUE(merchant_id,idempotency_key));
 CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,method TEXT,path TEXT,app TEXT,merchant_id TEXT,request TEXT,response_code INTEGER,response TEXT,created_at INTEGER);
 CREATE TABLE IF NOT EXISTS control(name TEXT PRIMARY KEY,value TEXT);'''); return c
def record(method,path,app,merchant,req,code,resp):
 with db() as c:c.execute('INSERT INTO audit(method,path,app,merchant_id,request,response_code,response,created_at) VALUES(?,?,?,?,?,?,?,?)',(method,path,app,merchant,json.dumps(req,sort_keys=True),code,json.dumps(resp,sort_keys=True),int(time.time())))
def auth(h):
 try:
  scheme,val=h.split(' ',1); u,p=base64.b64decode(val).decode().split(':',1)
  if scheme.lower()!='basic' or u!=USER:return None
  if p==PASSWORD:return 'vendor_payments'
  if p=='local-xpayroll-secret':return 'xpayroll'
  return None
 except:return None
class H(BaseHTTPRequestHandler):
 def body(self):
  try:return json.loads(self.rfile.read(int(self.headers.get('content-length','0'))) or b'{}')
  except:return None
 def sendj(self,code,obj):
  b=json.dumps(obj,sort_keys=True).encode(); self.send_response(code); self.send_header('content-type','application/json'); self.send_header('content-length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def context(self):return auth(self.headers.get('Authorization','')),self.headers.get('X-Razorpay-Account','')
 def observed_request(self,body):
  return {'body':body,'headers':{k:self.headers.get(k,'') for k in ('X-Request-ID','X-Razorpay-TaskId','X-Payout-Idempotency','X-Dashboard-User-Id','X-Razorpay-Account')}}
 def reject_auth(self,method,body):
  app,merchant=self.context()
  if app and merchant:return app,merchant
  resp={'error':{'code':'BAD_REQUEST_ERROR','description':'AppNotPermittedToCreatePayoutOnThisContactType'}}; record(method,self.path,'unknown',merchant,body,400,resp); self.sendj(400,resp); return None
 def do_GET(self):
  if self.path=='/health':return self.sendj(200,{'status':'ok','replacement':'api-monolith-payouts-boundary'})
  if self.path.startswith('/_evidence'):
   with db() as c: out={'payouts':[dict(x) for x in c.execute('SELECT * FROM payouts ORDER BY id')],'audit':[dict(x) for x in c.execute('SELECT * FROM audit ORDER BY id')]}
   return self.sendj(200,out)
  if self.path=='/_control/contact':
   with db() as c:r=c.execute("SELECT value FROM control WHERE name='contact_enabled'").fetchone()
   return self.sendj(200,{'enabled':not r or r['value']=='1'})
  if self.path.startswith('/v1/contacts_internal/'):
   ctx=self.reject_auth('GET',{});
   if not ctx:return
   app,merchant=ctx
   with db() as c:r=c.execute("SELECT value FROM control WHERE name='contact_enabled'").fetchone()
   enabled=not r or r['value']=='1'; fa={'id':FA_ID,'entity':'fund_account','contact_id':CONTACT_ID,'account_type':'bank_account','bank_account':{'name':'RZPX PRIVATE LIMITED','ifsc':FA_IFSC,'account_number':FA_NUMBER,'bank_name':'Synthetic Bank'},'active':True,'created_at':int(time.time())}; item={'id':CONTACT_ID,'name':'Razorpay Tax Payment','type':'rzp_tax_pay','fund_accounts':[fa]}
   resp={'entity':'collection','count':1 if enabled else 0,'items':[item] if enabled else []}; record('GET',self.path,app,merchant,{},200,resp); return self.sendj(200,resp)
  if self.path==f'/v1/fund_accounts_internal/{FA_ID}':
   ctx=self.reject_auth('GET',{});
   if not ctx:return
   app,merchant=ctx; resp={'id':FA_ID,'entity':'fund_account','contact_id':CONTACT_ID,'account_type':'bank_account','bank_account':{'name':'RZPX PRIVATE LIMITED','ifsc':FA_IFSC,'account_number':FA_NUMBER,'bank_name':'Synthetic Bank'},'active':True,'contact':{'id':CONTACT_ID,'name':'Razorpay Tax Payment','type':'rzp_tax_pay'}}; record('GET',self.path,app,merchant,{},200,resp); return self.sendj(200,resp)
  if self.path.startswith('/v1/banking_accounts_internal'):
   ctx=self.reject_auth('GET',{});
   if not ctx:return
   app,merchant=ctx; resp={'count':1,'items':[{'id':'ba_s2p_001','account_number':'7878780011','channel':'rbl','account_type':'direct','balance':{'balance':10000000}}]}; record('GET',self.path,app,merchant,{},200,resp); return self.sendj(200,resp)
  return self.sendj(404,{'error':{'code':'BAD_REQUEST_ERROR','description':'route not found'}})
 def do_POST(self):
  body=self.body(); ctx=self.reject_auth('POST',body)
  if not ctx:return
  app,merchant=ctx
  if self.path=='/_control/contact':
   enabled=bool(body and body.get('enabled'))
   with db() as c:c.execute("INSERT INTO control(name,value) VALUES('contact_enabled',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",('1' if enabled else '0',))
   return self.sendj(200,{'enabled':enabled})
  if self.path=='/_control/seed-source-payout':
   pid=(body or {}).get('id');
   if not pid or not pid.startswith('pout_'):
    return self.sendj(400,{'error':{'code':'BAD_REQUEST_ERROR','description':'valid payout id required'}})
   with db() as c:c.execute("INSERT OR IGNORE INTO payouts(id,merchant_id,request,response,idempotency_key,status,tax_payment_id) VALUES(?,?, '{}','{}',?,'processed',NULL)",(pid,merchant,'source:'+pid))
   return self.sendj(200,{'id':pid,'merchant_id':merchant,'status':'processed'})
  if self.path=='/v1/vendor-payments/verify-otp':
   resp={'Success':bool(body and body.get('otp')=='754081')}; record('POST',self.path,app,merchant,body,200,resp); return self.sendj(200,resp)
  if self.path!='/v1/internalContactPayout/':return self.sendj(404,{'error':{'code':'BAD_REQUEST_ERROR','description':'route not found'}})
  if app!='vendor_payments':
   resp={'error':{'code':'BAD_REQUEST_ERROR','description':'AppNotPermittedToCreatePayoutOnThisContactType'}}; record('POST',self.path,app,merchant,body,400,resp); return self.sendj(400,resp)
  required=('account_number','fund_account_id','amount','currency','mode','purpose'); missing=[x for x in required if body is None or body.get(x) in (None,'')]
  if missing:
   resp={'error':{'code':'BAD_REQUEST_ERROR','description':'The '+missing[0]+' field is required.'}}; record('POST',self.path,app,merchant,body,400,resp); return self.sendj(400,resp)
  ik=self.headers.get('X-Payout-Idempotency','')
  if not ik:
   resp={'error':{'code':'BAD_REQUEST_ERROR','description':'X-Payout-Idempotency header is required'}}; record('POST',self.path,app,merchant,body,400,resp); return self.sendj(400,resp)
  if body['fund_account_id']!=FA_ID:
   resp={'error':{'code':'BAD_REQUEST_ERROR','description':'fund account is not the active rzp_tax_pay internal contact account'}}; record('POST',self.path,app,merchant,body,400,resp); return self.sendj(400,resp)
  with db() as c:
   row=c.execute('SELECT request,response FROM payouts WHERE merchant_id=? AND idempotency_key=?',(merchant,ik)).fetchone()
   if row:
    if row['request']!=json.dumps(body,sort_keys=True):
     resp={'error':{'code':'BAD_REQUEST_ERROR','description':'X-Payout-Idempotency key used with a different request'}}; record('POST',self.path,app,merchant,body,400,resp); return self.sendj(400,resp)
    resp=json.loads(row['response'])
   else:
    pid='pout_'+uuid.uuid4().hex[:14]; resp={'id':pid,'entity':'payout','fund_account_id':body['fund_account_id'],'amount':body['amount'],'currency':body['currency'],'mode':body['mode'],'purpose':body['purpose'],'queue_if_low_balance':bool(body.get('queue_if_low_balance',False)),'reference_id':body.get('reference_id',''),'narration':body.get('narration',''),'notes':body.get('notes',{}),'status':'created','created_at':int(time.time()),'source_details':body.get('source_details',[])}
    c.execute('INSERT INTO payouts(id,merchant_id,request,response,idempotency_key,status,tax_payment_id) VALUES(?,?,?,?,?,?,NULL)',(pid,merchant,json.dumps(body,sort_keys=True),json.dumps(resp,sort_keys=True),ik,'created'))
  audit_request=self.observed_request(body)
  record('POST',self.path,app,merchant,audit_request,200,resp); return self.sendj(200,resp)
 def do_PATCH(self):
  body=self.body(); ctx=self.reject_auth('PATCH',body)
  if not ctx:return
  app,merchant=ctx
  if self.path.startswith('/v1/payouts_internal/') and self.path.endswith('/tax-payment-id'):
   pid=self.path.split('/')[3]
   if body is None or not body.get('tax_payment_id'):
    resp={'error':{'code':'BAD_REQUEST_ERROR','description':'The tax_payment_id field is required.'}}; record('PATCH',self.path,app,merchant,body,400,resp); return self.sendj(400,resp)
   tax_payment_id=body['tax_payment_id']
   if not isinstance(tax_payment_id,str) or len(tax_payment_id)!=19 or not tax_payment_id.startswith('txpy_'):
    resp={'error':{'code':'BAD_REQUEST_INVALID_TAX_PAYMENT_ID','description':'tax_payment_id must be a 19-character public ID with txpy_ prefix'}}; record('PATCH',self.path,app,merchant,self.observed_request(body),400,resp); return self.sendj(400,resp)
   with db() as c:
    row=c.execute('SELECT id FROM payouts WHERE id=? AND merchant_id=?',(pid,merchant)).fetchone()
    if not row:
     resp={'error':{'code':'BAD_REQUEST_ERROR','description':'payout id does not exist for merchant'}}; record('PATCH',self.path,app,merchant,body,400,resp); return self.sendj(400,resp)
    c.execute('UPDATE payouts SET tax_payment_id=? WHERE id=?',(tax_payment_id,pid))
   resp={'status':'SUCCESS'}; record('PATCH',self.path,app,merchant,self.observed_request(body),200,resp); return self.sendj(200,resp)
  return self.sendj(404,{'error':{'code':'BAD_REQUEST_ERROR','description':'route not found'}})
 def log_message(self,*args):pass
if __name__ == '__main__':
 ThreadingHTTPServer(('0.0.0.0',int(os.getenv('PORT','8080'))),H).serve_forever()
