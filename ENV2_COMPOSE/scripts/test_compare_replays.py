"""Offline regression tests for evidence completeness and material differences."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('compare_replays',Path(__file__).with_name('compare-replays.py'))
compare=importlib.util.module_from_spec(spec);spec.loader.exec_module(compare)


class ReplayTests(unittest.TestCase):
    def fixture(self,pid='AbCdEf01234567'):
        return [
            ('starting_fixture',{'M1':{'merchant_id':'ARENA000000001','balance':100}}),
            ('test_result',{'when':'setup','outcome':'passed','nodeid':'test.py::test_example'}),
            ('http_request',{'method':'POST','url':'http://local/v1/payouts','body':{'amount':10}}),
            ('http_response',{'method':'POST','url':'http://local/v1/payouts','status':201,'body':{'id':'pout_'+pid,'amount':10,'created_at':100}}),
            ('db_read',{'sql':'SELECT * FROM payouts WHERE id=%s','params':[pid],'rows':{'id':pid,'status':'processed','amount':10,'utr':'dynamic-utr'}}),
            ('http_request',{'method':'GET','url':'http://local/events','body':None}),
            ('http_response',{'method':'GET','url':'http://local/events','status':200,'body':{'deliveries':[{'signature_valid':True,'payload':{'payout':{'id':'pout_'+pid,'status':'processed'}}}]}}),
            ('test_result',{'when':'call','outcome':'passed','nodeid':'test.py::test_example'}),
            ('test_result',{'when':'teardown','outcome':'passed','nodeid':'test.py::test_example'})]

    def summarize(self,records=None,junit='<testcase name="test_example"/>'):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);(path/'junit.xml').write_text('<testsuite>'+junit+'</testsuite>')
            if records is not None:
                (path/'test_example.jsonl').write_text(''.join(json.dumps({'kind':kind,'data':data,'sequence':i})+'\n' for i,(kind,data) in enumerate(records,1)))
            return compare.summarize(path,{'test_example'})

    def test_generated_id_and_clock_change_equivalent(self):
        a=self.fixture();b=self.fixture('ZyXwVu98765432');b[3][1]['body']['created_at']=999
        self.assertEqual(self.summarize(a),self.summarize(b))
        self.assertTrue(self.summarize(a)['valid'])

    def test_missing_trace_invalid(self):self.assertFalse(self.summarize()['valid'])
    def test_empty_junit_invalid(self):self.assertFalse(self.summarize(self.fixture(),junit='')['valid'])
    def test_failed_junit_invalid(self):self.assertFalse(self.summarize(self.fixture(),'<testcase name="test_example"><failure/></testcase>')['valid'])
    def test_skipped_junit_invalid(self):self.assertFalse(self.summarize(self.fixture(),'<testcase name="test_example"><skipped/></testcase>')['valid'])
    def test_incomplete_phases_invalid(self):self.assertFalse(self.summarize(self.fixture()[:-1])['valid'])
    def test_duplicate_junit_invalid(self):self.assertFalse(self.summarize(self.fixture(),'<testcase name="test_example"/><testcase name="test_example"/>')['valid'])
    def test_unpaired_http_invalid(self):self.assertFalse(self.summarize([r for r in self.fixture() if r[0]!='http_response'])['valid'])

    def test_signature_nested_payload_and_multiplicity_material(self):
        for mutate in (lambda r:r[6][1]['body']['deliveries'][0].update(signature_valid=False),
                       lambda r:r[6][1]['body']['deliveries'][0]['payload']['payout'].update(status='failed'),
                       lambda r:r[6][1]['body']['deliveries'].append(copy.deepcopy(r[6][1]['body']['deliveries'][0]))):
            a=self.fixture();b=copy.deepcopy(a);mutate(b)
            self.assertNotEqual(self.summarize(a)['tests'],self.summarize(b)['tests'])

    def test_unknown_fields_http_status_request_amount_material(self):
        for mutate in (lambda r:r[4][1]['rows'].update(unexpected_flag=True),
                       lambda r:r[3][1].update(status=400),lambda r:r[2][1]['body'].update(amount=99)):
            a=self.fixture();b=copy.deepcopy(a);mutate(b)
            self.assertNotEqual(self.summarize(a)['tests'],self.summarize(b)['tests'])

    def test_only_final_poll_material(self):
        a=self.fixture();b=copy.deepcopy(a);poll=copy.deepcopy(b[4]);poll[1]['rows']['status']='initiated';b.insert(4,poll)
        self.assertEqual(self.summarize(a),self.summarize(b))

    def test_relationships_and_non_id_text_material(self):
        a=self.fixture();b=copy.deepcopy(a);b[6][1]['body']['deliveries'][0]['payload']['payout']['id']='pout_QwErTy12345678'
        self.assertNotEqual(self.summarize(a)['tests'],self.summarize(b)['tests'])
        n=compare.Normalizer();self.assertEqual(n.clean({'status':'fourteenletter'}),{'status':'fourteenletter'})
        self.assertNotEqual(n.clean({'utr':'a','nested':{'utr':'a'}}),compare.Normalizer().clean({'utr':'a','nested':{'utr':'b'}}))

    def test_concurrent_different_endpoints_pair_by_request_identity(self):
        a=self.fixture()
        # The delayed POST is pending while a GET on another endpoint completes.
        b=a[:3]+a[5:7]+a[3:5]+a[7:]
        self.assertTrue(self.summarize(b)['valid'])
        self.assertEqual(self.summarize(a),self.summarize(b))

    def test_ambiguous_same_endpoint_overlap_is_rejected(self):
        a=self.fixture();a.insert(3,copy.deepcopy(a[2]));a.insert(5,copy.deepcopy(a[4]))
        self.assertFalse(self.summarize(a)['valid'])

    def test_actual_http_mutation_order_and_bodies_stay_material(self):
        a=self.fixture();pair=copy.deepcopy(a[2:4]);pair[0][1]['url']='http://local/another-mutation';pair[1][1]['url']='http://local/another-mutation'
        a[4:4]=pair;b=copy.deepcopy(a);b[2:6]=b[4:6]+b[2:4]
        self.assertNotEqual(self.summarize(a)['tests'],self.summarize(b)['tests'])

    def test_db_and_http_mutation_interleaving_stays_material(self):
        mutation=('db_mutation',{'sql':'UPDATE accounts SET balance=%s WHERE id=%s','params':[10,'fixture-account']})
        a=self.fixture();a.insert(2,mutation)
        b=self.fixture();b.insert(4,copy.deepcopy(mutation))
        self.assertEqual(self.summarize(a)['tests']['test_example']['http_mutations'],self.summarize(b)['tests']['test_example']['http_mutations'])
        self.assertNotEqual(self.summarize(a)['tests']['test_example']['mutation_history'],self.summarize(b)['tests']['test_example']['mutation_history'])

    def test_fts_autoincrement_ids_preserve_identity_and_money(self):
        def observations(transfer,attempt):
            return [
                {'kind':'db_read','data':{'sql':'SELECT id, status FROM transfers WHERE source_id=%s','params':['source'],'rows':[{'id':transfer,'status':'CREATED','amount':transfer,'metadata':{'id':55}}]}},
                {'kind':'db_read','data':{'sql':'SELECT id FROM attempts WHERE transfer_id=%s','params':[transfer],'rows':[{'id':attempt}]}},
                {'kind':'http_response','data':{'body':{'fund_transfer_id':transfer,'utr':'ARENA'+str(attempt).zfill(10)}}}]
        first=observations(1,5);second=observations(9,17)
        # Money and unknown nested identities never become autoincrement aliases.
        second[0]['data']['rows'][0]['amount']=1
        n1,n2=compare.Normalizer(),compare.Normalizer()
        self.assertEqual([n1.record(r) for r in first],[n2.record(r) for r in second])
        changed=copy.deepcopy(second);changed[1]['data']['params'][0]=10
        n1,n2=compare.Normalizer(),compare.Normalizer()
        self.assertNotEqual([n1.record(r) for r in first],[n2.record(r) for r in changed])

    def test_fixture_utrs_and_unknown_time_fields_remain_literal(self):
        n=compare.Normalizer()
        self.assertEqual(n.clean({'utr':'UTR_V21','business_date':123}),{'utr':'UTR_V21','business_date':123})
        self.assertNotEqual(n.clean({'utr':'UTR_V21'}),n.clean({'utr':'UTR_V13'}))
        self.assertEqual(n.clean({'dispatched_at':123,'transaction_date':456,'created_at_microsecond':123456789}),{})

    def test_bank_response_presence_projection_remains_material(self):
        sql=('SELECT id,transfer_id,status,bank_status_code,(acknowledged_at > 0) AS bank_response_received '
             'FROM attempts WHERE transfer_id=%s ORDER BY id DESC LIMIT 1')
        def record(row):
            return compare.Normalizer().record({'kind':'db_read','data':{
                'sql':sql,'params':[7],'rows':[dict(id=9,transfer_id=7,status='INITIATED',
                    bank_status_code='INITIATED',**row)]}})
        acknowledged=record({'bank_response_received':1})
        self.assertEqual(acknowledged['rows'][0]['bank_response_received'],1)
        for row in ({'bank_response_received':0},{'bank_response_received':None},{}):
            with self.subTest(row=row):
                self.assertNotEqual(acknowledged,record(row))
        self.assertNotEqual(record({'bank_response_received':0}),record({'bank_response_received':None}))
        self.assertNotEqual(record({'bank_response_received':None}),record({}))
        # No raw acknowledged_at rule is needed by this SQL projection. Should
        # another observation introduce it, retain its value for explicit review.
        self.assertNotEqual(record({'acknowledged_at':100}),record({'acknowledged_at':200}))

    def test_clock_fixture_update_preserves_relative_offset_and_mutation(self):
        def record(at,when):return {'kind':'db_mutation','at':at,'data':{'sql':'UPDATE payouts SET scheduled_at=%s WHERE id=%s','params':[when,'payout']}}
        self.assertEqual(compare.Normalizer().record(record(100,40)),compare.Normalizer().record(record(200,140)))
        self.assertNotEqual(compare.Normalizer().record(record(100,40)),compare.Normalizer().record(record(200,130)))

    def capture_fixture(self,reverse=False,clock=1):
        a=self.fixture();records=[]
        for merchant in ('ARENA-A','ARENA-B'):
            body={'event':'payout.processed','account_id':merchant,'created_at':clock,'payload':{'payout':{'entity':{'id':'pout_AbCdEf01234567','status':'processed','amount':10}}}}
            records.append({'merchant':merchant,'event_id':'ev_ARENA00000'+str(len(records)+clock),
                            'body':body,'signature_present':True,'signature_valid':True,
                            'body_sha256':hashlib.sha256(json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()})
        if reverse:records.reverse()
        a[6][1]['body']['deliveries']=records
        return a

    def test_capture_arrival_order_and_verified_body_digest_normalize(self):
        a=self.capture_fixture();b=self.capture_fixture(reverse=True,clock=10)
        self.assertTrue(self.summarize(a)['valid']);self.assertEqual(self.summarize(a),self.summarize(b))

    def test_capture_digest_tampering_is_invalid(self):
        a=self.capture_fixture();a[6][1]['body']['deliveries'][0]['body_sha256']='0'*64
        self.assertFalse(self.summarize(a)['valid'])

    def test_capture_state_changes_and_duplicates_remain_material(self):
        a=self.capture_fixture();b=copy.deepcopy(a)
        item=b[6][1]['body']['deliveries'][0];item['body']['payload']['payout']['entity']['status']='processing'
        item['body_sha256']=hashlib.sha256(json.dumps(item['body'],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        self.assertNotEqual(self.summarize(a)['tests'],self.summarize(b)['tests'])
        b=copy.deepcopy(a);b[6][1]['body']['deliveries'].append(copy.deepcopy(b[6][1]['body']['deliveries'][0]))
        self.assertNotEqual(self.summarize(a)['tests'],self.summarize(b)['tests'])

    def test_low_balance_cron_response_preserves_per_balance_outcome_multiset(self):
        a={'kind':'http_response','data':{'method':'POST','url':compare.LOW_BALANCE_CRON_URL,'status':200,
            'request_body':{'balance_ids':['B']},'body':[
                {'balance_id':'A','successful_queued_payout_count':0,'failed_queued_payout_count':0,'total_queued_payout_count':1,'error':'','unknown':{'value':1}},
                {'balance_id':'B','successful_queued_payout_count':1,'failed_queued_payout_count':0,'total_queued_payout_count':1,'error':''}]}}
        b=copy.deepcopy(a);b['data']['body'].reverse()
        self.assertEqual(compare.Normalizer().record(a),compare.Normalizer().record(b))
        # The map/goroutine source order may vary, but no row, count, error,
        # duplicate or novel field may vanish or detach from its balance.
        for modify in (lambda r:r['data']['body'].append(copy.deepcopy(r['data']['body'][0])),
                       lambda r:r['data']['body'][0].update(successful_queued_payout_count=7),
                       lambda r:r['data']['body'][0].update(error='real failure'),
                       lambda r:r['data']['body'][0]['unknown'].update(value=2)):
            changed=copy.deepcopy(a);modify(changed)
            self.assertNotEqual(compare.Normalizer().record(a),compare.Normalizer().record(changed))

    def test_low_balance_order_rule_does_not_apply_to_other_method_or_url(self):
        for method,url in (('GET',compare.LOW_BALANCE_CRON_URL),('POST','http://payouts-api:9400/another-endpoint'),
                           ('POST','http://different-host:9400/v1/cron/process_queued_low_balance_payouts')):
            a={'kind':'http_response','data':{'method':method,'url':url,'body':[{'balance_id':'A'},{'balance_id':'B'}]}}
            b=copy.deepcopy(a);b['data']['body'].reverse()
            self.assertNotEqual(compare.Normalizer().record(a),compare.Normalizer().record(b))

    def test_low_balance_rule_keeps_request_order_and_unidentified_rows(self):
        a={'kind':'http_response','data':{'method':'POST','url':compare.LOW_BALANCE_CRON_URL,
             'request_body':{'balance_ids':['B','A']},'body':[{'balance_id':'A'},{'unexpected':'B'}]}}
        b=copy.deepcopy(a);b['data']['body'].reverse()
        self.assertNotEqual(compare.Normalizer().record(a),compare.Normalizer().record(b))
        b=copy.deepcopy(a);b['data']['request_body']['balance_ids'].reverse()
        self.assertNotEqual(compare.Normalizer().record(a),compare.Normalizer().record(b))

    def test_monolith_log_is_scoped_to_the_test_and_unordered(self):
        def rec(entries):
            return {'kind':'http_response','data':{'method':'GET','url':compare.MONOLITH_LOG_URL,'status':200,
                    'body':{'log':entries}}}
        own=[{'kind':'create_fta','payload':{'payout_id':'test_a:payout_1','gateway_ref_no':'TYQ0ZMlLClAC'}},
             {'kind':'mail_and_sms','payload':{'entity_id':'test_a:payout_1'}}]
        other=[{'kind':'create_fta','payload':{'payout_id':'test_b:payout_1'}}]
        a=compare.Normalizer(test='test_a').record(rec(own+other))
        b=compare.Normalizer(test='test_a').record(rec(list(reversed(own))+[{'kind':'x','payload':{'payout_id':'test_c:payout_9'}}]))
        self.assertEqual(a,b)
        self.assertEqual(len(a['body']['log']),2)
        # A different bank reference is identity only; a different status is material.
        c=copy.deepcopy(own);c[0]['payload']['gateway_ref_no']='TYQ5eL9PBDHF'
        self.assertEqual(a,compare.Normalizer(test='test_a').record(rec(c)))
        d=copy.deepcopy(own);d[0]['kind']='payout_status_relay'
        self.assertNotEqual(a,compare.Normalizer(test='test_a').record(rec(d)))
        # Other URLs keep order and all entries.
        e={'kind':'http_response','data':{'method':'GET','url':'http://monolith-stub:8080/other','status':200,'body':{'log':own+other}}}
        self.assertEqual(len(compare.Normalizer(test='test_a').record(e)['body']['log']),3)

    def test_unfiltered_sink_deliveries_are_scoped_to_the_test(self):
        url='http://merchant-webhook-sink:8080/_arena/deliveries'
        own={'body':{'event':'payout.processed','payload':{'payout':{'entity':{'id':'test_a:payout_1','status':'processed'}}}}}
        other={'body':{'event':'payout.updated','payload':{'payout':{'entity':{'id':'test_b:payout_1','status':'processing'}}}}}
        other2=copy.deepcopy(other);other2['body']['payload']['payout']['entity']['status']='processed'
        rec=lambda rows:{'kind':'http_response','data':{'method':'GET','url':url,'status':200,'body':{'deliveries':rows}}}
        a=compare.Normalizer(test='test_a').record(rec([other,own]));b=compare.Normalizer(test='test_a').record(rec([own,other2]))
        self.assertEqual(a,b);self.assertEqual(len(a['body']['deliveries']),1)
        mine=copy.deepcopy(own);mine['body']['payload']['payout']['entity']['status']='processing'
        self.assertNotEqual(a,compare.Normalizer(test='test_a').record(rec([mine])))
        filtered={'kind':'http_response','data':{'method':'GET','url':url+'?merchant=ARENAX','status':200,'body':{'deliveries':[other,own]}}}
        self.assertEqual(len(compare.Normalizer(test='test_a').record(filtered)['body']['deliveries']),2)

    def test_reservation_inspect_items_are_unordered_but_material(self):
        url=compare.RESERVATION_INSPECT_URL+'?merchant_id=ARENAX&balance_id=ARENAY'
        rows=[{'payout_id':'test_a:payout_1','amount':100,'state':'live'},{'payout_id':'test_a:payout_2','amount':100,'state':'live'}]
        rec=lambda r:{'kind':'http_response','data':{'method':'GET','url':url,'status':200,'body':{'items':r,'live_total':200}}}
        self.assertEqual(compare.Normalizer().record(rec(rows)),compare.Normalizer().record(rec(list(reversed(rows)))))
        changed=copy.deepcopy(rows);changed[0]['state']='awaiting_balance_refresh'
        self.assertNotEqual(compare.Normalizer().record(rec(rows)),compare.Normalizer().record(rec(changed)))
        self.assertNotEqual(compare.Normalizer().record(rec(rows)),compare.Normalizer().record(rec(rows[:1])))

if __name__=='__main__':unittest.main()
