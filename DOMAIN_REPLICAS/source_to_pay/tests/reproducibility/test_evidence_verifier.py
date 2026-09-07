#!/usr/bin/env python3
"""Tamper tests for cross-boundary identity checks in journey evidence."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import unittest

DOMAIN=Path(__file__).resolve().parents[2]
FIXTURE=Path(os.environ.get('S2P_JOURNEY_EVIDENCE',DOMAIN/'artifacts/journey-latest.json'))
SPEC=importlib.util.spec_from_file_location('verify_evidence',DOMAIN/'tests/integration/verify_evidence.py')
VERIFIER=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(VERIFIER)

class EvidenceVerifierTamperTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  if not FIXTURE.is_file(): raise unittest.SkipTest(f'journey evidence fixture unavailable: {FIXTURE}')
  cls.fixture=json.loads(FIXTURE.read_text())
  baseline=VERIFIER.verify(cls.fixture)
  if not baseline['passed']: raise AssertionError(f'fixture does not pass verifier: {baseline["checks"]}')

 def verify_tamper(self,mutate,failed_check):
  value=copy.deepcopy(self.fixture); mutate(value); result=VERIFIER.verify(value)
  self.assertFalse(result['passed'])
  self.assertFalse(result['checks'][failed_check])

 def test_rejects_callback_for_another_tax_payment(self):
  def mutate(j):
   row=next(x for x in j['database']['replica_trace'] if x['kind']=='callback_received')
   payload=json.loads(row['payload']); payload['source_id']='txpy_CrossEntity01'; row['payload']=json.dumps(payload)
  self.verify_tamper(mutate,'callback')

 def test_rejects_tagback_audit_with_substituted_body(self):
  def mutate(j):
   row=next(x for x in j['boundary']['audit'] if x['method']=='PATCH')
   request=json.loads(row['request']); request['body']['tax_payment_id']='txpy_CrossEntity01'; row['request']=json.dumps(request)
  self.verify_tamper(mutate,'tagback_request')

 def test_rejects_remittance_request_with_cross_merchant_header(self):
  def mutate(j):
   row=next(x for x in j['boundary']['audit'] if x['method']=='POST' and x['path']=='/v1/internalContactPayout/')
   request=json.loads(row['request']); request['headers']['X-Razorpay-Account']='S2POtherMerchant'; row['request']=json.dumps(request)
  self.verify_tamper(mutate,'remittance_request')

 def test_rejects_unrelated_later_pubsub_identity(self):
  def mutate(j):
   rows=[x for x in j['database']['replica_trace'] if x['kind']=='source_pubsub']
   payload=json.loads(rows[-1]['payload']); payload['data']['Id']='txpy_CrossEntity01'; rows[-1]['payload']=json.dumps(payload)
  self.verify_tamper(mutate,'source_pubsub_states')

if __name__=='__main__': unittest.main()
