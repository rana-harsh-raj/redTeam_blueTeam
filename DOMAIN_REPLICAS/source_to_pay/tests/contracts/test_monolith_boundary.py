import base64, json, os, socket, subprocess, tempfile, time, unittest, urllib.error, urllib.request
from pathlib import Path
SERVER=Path(__file__).parents[2]/'runtime/boundary/server.py'
class BoundaryContract(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  s=socket.socket(); s.bind(('127.0.0.1',0)); cls.port=s.getsockname()[1]; s.close()
  cls.tmp=tempfile.TemporaryDirectory(); env={**os.environ,'PORT':str(cls.port),'BOUNDARY_DB':cls.tmp.name+'/boundary.sqlite','API_SECRET':'test-secret'}
  cls.proc=subprocess.Popen(['python3',str(SERVER)],env=env)
  for _ in range(50):
   try:urllib.request.urlopen(f'http://127.0.0.1:{cls.port}/health'); break
   except Exception:time.sleep(.05)
 @classmethod
 def tearDownClass(cls):cls.proc.terminate(); cls.proc.wait(); cls.tmp.cleanup()
 def call(self,method,path,body=None,password='test-secret',extra=None):
  h={'Authorization':'Basic '+base64.b64encode(f'rzp_live:{password}'.encode()).decode(),'X-Razorpay-Account':'merchant_s2p_001','Content-Type':'application/json',**(extra or {})}
  r=urllib.request.Request(f'http://127.0.0.1:{self.port}'+path,json.dumps(body).encode() if body is not None else None,h,method=method)
  try:x=urllib.request.urlopen(r); return x.status,json.load(x)
  except urllib.error.HTTPError as e:return e.code,json.load(e)
 def test_exact_caller_shapes_auth_tagback_and_idempotency(self):
  req={'account_number':'7878780011','fund_account_id':'fa_tax_internal_1','amount':12500,'currency':'INR','mode':'IMPS','purpose':'tax_payment','queue_if_low_balance':False,'source_details':[{'source_id':'txpy_00000000000001','source_type':'tax_payments','priority':1}]}
  h={'X-Payout-Idempotency':'tax-remit-1','X-Dashboard-User-Id':'user_1','X-Request-ID':'s2p-golden-20260820-001','X-Razorpay-TaskId':'s2p-golden-20260820-001'}
  code,p=self.call('POST','/v1/internalContactPayout/',req,extra=h); self.assertEqual(code,200); self.assertEqual(p['status'],'created')
  _,again=self.call('POST','/v1/internalContactPayout/',req,extra=h); self.assertEqual(p['id'],again['id'])
  code,_=self.call('POST','/_control/seed-source-payout',{'id':'pout_00000000000001'}); self.assertEqual(code,200)
  for invalid_tax_id in ('txpy_short','badp_00000000000001'):
   code,error=self.call('PATCH','/v1/payouts_internal/pout_00000000000001/tax-payment-id',{'tax_payment_id':invalid_tax_id}); self.assertEqual(code,400); self.assertEqual(error['error']['code'],'BAD_REQUEST_INVALID_TAX_PAYMENT_ID')
   _,unchanged=self.call('GET','/_evidence'); source_row=[x for x in unchanged['payouts'] if x['id']=='pout_00000000000001'][0]; self.assertIsNone(source_row['tax_payment_id'])
  code,tag=self.call('PATCH','/v1/payouts_internal/pout_00000000000001/tax-payment-id',{'tax_payment_id':'txpy_00000000000001'}); self.assertEqual((code,tag),(200,{'status':'SUCCESS'}))
  code,_=self.call('PATCH','/v1/payouts_internal/pout_99999999999999/tax-payment-id',{'tax_payment_id':'txpy_00000000000001'}); self.assertEqual(code,400)
  code,ba=self.call('GET','/v1/banking_accounts_internal/'); self.assertEqual(code,200); self.assertEqual(ba['items'][0]['account_number'],'7878780011')
  code,contacts=self.call('GET','/v1/contacts_internal/'); self.assertEqual(code,200); self.assertEqual(contacts['items'][0]['type'],'rzp_tax_pay'); self.assertTrue(contacts['items'][0]['fund_accounts'][0]['active'])
  code,fa=self.call('GET','/v1/fund_accounts_internal/fa_tax_internal_1'); self.assertEqual(code,200); self.assertEqual(fa['contact']['type'],'rzp_tax_pay')
  code,otp=self.call('POST','/v1/vendor-payments/verify-otp',{'otp':'754081'}); self.assertEqual((code,otp),(200,{'Success':True}))
  code,hidden=self.call('POST','/_control/contact',{'enabled':False}); self.assertEqual((code,hidden),(200,{'enabled':False}))
  _,contacts=self.call('GET','/v1/contacts_internal/'); self.assertEqual(contacts['count'],0)
  code,denied=self.call('POST','/v1/internalContactPayout/',req,password='local-xpayroll-secret',extra=h); self.assertEqual(code,400); self.assertIn('AppNotPermitted',denied['error']['description'])
  code,other=self.call('POST','/v1/internalContactPayout/',req,extra={**h,'X-Razorpay-Account':'S2PMerchant002'}); self.assertEqual(code,200); self.assertNotEqual(p['id'],other['id'])
  _,evidence=self.call('GET','/_evidence'); observed=json.loads([x for x in evidence['audit'] if x['path']=='/v1/internalContactPayout/' and x['response_code']==200][0]['request']); self.assertEqual(observed['headers']['X-Request-ID'],'s2p-golden-20260820-001'); self.assertNotIn('Authorization',observed['headers'])
if __name__=='__main__':unittest.main()
