"""Offline runner lifecycle and loopback HTTP policy checks; never runs Docker."""
import http.client
import importlib.util
import json
import os
import runpy
import signal
import sys
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

spec=importlib.util.spec_from_file_location('explorer_server',Path(__file__).with_name('server.py'))
server=importlib.util.module_from_spec(spec);spec.loader.exec_module(server)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.root=root/'explorer';self.arena=root/'arena';self.results=root/'results'
        (self.root/'data').mkdir(parents=True);(self.root/'data/saved-run.json').write_text('{"previous":true}')
        (self.arena/'seeds/generated').mkdir(parents=True);(self.arena/'secrets/merchant-keys').mkdir(parents=True)
        (self.arena/'seeds/generated/scenario-index.json').write_text(json.dumps({'baseline':{'M1':{'merchant_id':'ARENA1'}}}))
        (self.arena/'seeds/generated/merchants.json').write_text(json.dumps({'merchants':{'ARENA1':{'secret_file':'local','key_id_live':'local_key'}}}))
        (self.arena/'secrets/merchant-keys/local.txt').write_text('synthetic-only')
        for obj,key,value in ((server,'ROOT',self.root),(server,'ARENA',self.arena),(server,'COMPOSE',['docker','compose'])):
            p=patch.object(obj,key,value);p.start();self.addCleanup(p.stop)
        p=patch.dict(os.environ,{'ARENA_LIVE_RUN_DIR':str(self.results),'ARENA_LIVE_CONTAINER':'twin-live-testing123'});p.start();self.addCleanup(p.stop)
        self.calls=[];self.mode='success';self.checks=0

    def command(self,cmd,**kwargs):
        self.calls.append(cmd)
        if cmd[1:3]==['container','ls']:
            self.checks+=1
            found=self.mode=='collision' or (self.mode=='cleanup_failure' and self.checks>1)
            return subprocess.CompletedProcess(cmd,0,'twin-live-testing123\n' if found else '')
        if 'run' in cmd:
            keys=list((self.arena/'.runtime').iterdir());self.assertEqual(len(keys),1)
            self.assertEqual(keys[0].stat().st_mode&0o777,0o600)
            if self.mode=='timeout':raise subprocess.TimeoutExpired(cmd,125)
            if self.mode=='terminate':raise SystemExit(143)
            (self.results/'live-result.json').write_text(json.dumps({'status':'passed','passed':True,'payout_id':'pout_synthetic'}))
            (self.results/'live-golden-shared.jsonl').write_text('')
            return subprocess.CompletedProcess(cmd,1 if self.mode=='exit_failure' else 0,'')
        return subprocess.CompletedProcess(cmd,0,'')

    def execute(self):
        with patch.object(server.subprocess,'run',side_effect=self.command):server.golden()

    def assert_cleaned(self):
        self.assertTrue(any(cmd[1:3]==['rm','-f'] for cmd in self.calls))
        self.assertEqual(list((self.arena/'.runtime').iterdir()),[])

    def test_success_saves_after_cleanup(self):
        self.execute();self.assert_cleaned();self.assertEqual(server.RUN['status'],'passed')
        self.assertTrue(json.loads((self.root/'data/saved-run.json').read_text())['passed'])

    def test_timeout_cleans_runner_and_key(self):
        self.mode='timeout';self.execute();self.assert_cleaned();self.assertEqual(server.RUN['status'],'failed')

    def test_termination_unwinds_cleanup(self):
        self.mode='terminate'
        with self.assertRaises(SystemExit):self.execute()
        self.assert_cleaned()

    def test_cleanup_failure_does_not_replace_saved_success(self):
        self.mode='cleanup_failure';self.execute();self.assert_cleaned();self.assertEqual(server.RUN['status'],'failed')
        self.assertEqual(json.loads((self.root/'data/saved-run.json').read_text()),{'previous':True})

    def test_existing_container_never_removed(self):
        self.mode='collision';self.execute();self.assertEqual(server.RUN['status'],'failed')
        self.assertFalse(any('run' in cmd or cmd[1:3]==['rm','-f'] for cmd in self.calls))

    def test_previous_evidence_not_reused(self):
        self.results.mkdir();(self.results/'live-result.json').write_text('{"status":"passed","passed":true}')
        self.execute();self.assertEqual(server.RUN['status'],'failed')
        self.assertFalse(any('run' in cmd for cmd in self.calls))

    def test_unscoped_container_name_rejected(self):
        with patch.dict(os.environ,{'ARENA_LIVE_CONTAINER':'payouts-api'}):self.execute()
        self.assertEqual(self.calls,[]);self.assertEqual(server.RUN['status'],'failed')

    def test_runner_exit_disagreement_fails(self):
        self.mode='exit_failure';self.execute();self.assert_cleaned();self.assertEqual(server.RUN['status'],'failed')


class CLITests(unittest.TestCase):
    def test_interrupted_audit_wrapper_is_terminated_and_waited(self):
        process=MagicMock();process.wait.side_effect=[SystemExit(143),0];process.poll.return_value=None
        handler=signal.getsignal(signal.SIGTERM)
        try:
            with patch.dict(sys.modules,{'server':server}), patch.object(sys,'argv',['run-golden.py','--with-egress-audit']), patch('subprocess.Popen',return_value=process):
                with self.assertRaises(SystemExit):runpy.run_path(str(Path(__file__).with_name('run-golden.py')),run_name='__main__')
            process.terminate.assert_called_once();self.assertEqual(process.wait.call_count,2)
        finally:signal.signal(signal.SIGTERM,handler)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.thread=threading.Thread(target=cls.httpd.serve_forever,daemon=True);cls.thread.start()
        cls.host='127.0.0.1:'+str(cls.httpd.server_port)

    @classmethod
    def tearDownClass(cls):cls.httpd.shutdown();cls.httpd.server_close();cls.thread.join()

    def request(self,method,path,body=None,headers=None):
        conn=http.client.HTTPConnection(self.host,timeout=3)
        conn.request(method,path,body=body,headers=headers or {});response=conn.getresponse();result=response.status;response.read();conn.close();return result

    def test_static_private_routes_forbidden(self):
        for method in ('GET','HEAD'):
            self.assertEqual(self.request(method,'/server.py'),404)
            self.assertEqual(self.request(method,'/data/saved-run.json',headers={'Host':'external.invalid'}),403)

    def test_cross_origin_and_parameters_cannot_start_runner(self):
        with patch.object(server,'golden') as action:
            self.assertEqual(self.request('POST','/api/run','{}',{'Origin':'http://external.invalid','X-Twin-Action':'golden','Content-Type':'application/json'}),403)
            self.assertEqual(self.request('POST','/api/run','{"amount":1}',{'Origin':'http://'+self.host,'X-Twin-Action':'golden','Content-Type':'application/json'}),400)
            action.assert_not_called()

if __name__=='__main__':unittest.main()
