"""Request and lifecycle checks with fake IO; never contacts Kong or Docker."""
import base64
from email.message import Message
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

spec=importlib.util.spec_from_file_location('ingress',Path(__file__).resolve().parents[2]/'scripts/ingress.py')
ingress=importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingress)


class IngressTests(unittest.TestCase):
    def request(self,path='/v1/payouts',headers=(),body=b'{}'):
        handler=object.__new__(ingress.Handler)
        handler.headers=Message()
        handler.headers['Host']='127.0.0.1:18080'
        handler.headers['Content-Length']=str(len(body))
        for name,value in headers:
            handler.headers[name]=value
        handler.server=SimpleNamespace(server_port=18080)
        handler.path=path
        handler.command='POST'
        handler.rfile=io.BytesIO(body)
        handler.wfile=io.BytesIO()
        handler.send_error=Mock()
        handler.send_response=Mock()
        handler.send_header=Mock()
        handler.end_headers=Mock()
        return handler

    def test_path_boundaries_accept_real_routes_and_query(self):
        for path in ('/v1/payouts','/v1/payouts/pout_123','/v1/payouts/pout_123/approve',
                     '/v1/payouts?count=5','/_arena/mint','/_arena/health'):
            with self.subTest(path=path): self.assertTrue(ingress.allowed_path(path))

    def test_ambiguous_paths_are_rejected(self):
        for path in ('/v1/payouts-other','/_arena/mint/other','/_arena/healthful',
                     '/v1/payouts/../other','/v1/payouts/%2e%2e/other',
                     '/v1/payouts/a%2fb','/v1/payouts//other',
                     'http://example.invalid/v1/payouts','//example.invalid/v1/payouts',
                     '/v1/payouts#fragment','/v1/payouts\n','http://['):
            with self.subTest(path=path): self.assertFalse(ingress.allowed_path(path))

    def test_external_null_and_other_loopback_origin_are_rejected(self):
        for origin in ('https://example.invalid','null','http://localhost:18080','http://127.0.0.1:18765'):
            with self.subTest(origin=origin),patch.object(ingress.subprocess,'run') as run:
                handler=self.request(headers=[('Origin',origin)])
                handler.proxy()
                self.assertEqual(handler.send_error.call_args.args[0],403)
                run.assert_not_called()

    def test_cross_site_fetch_metadata_is_rejected_without_origin(self):
        for site in ('cross-site','same-site'):
            with self.subTest(site=site):
                handler=self.request(headers=[('Sec-Fetch-Site',site)])
                handler.proxy()
                self.assertEqual(handler.send_error.call_args.args[0],403)

    def test_same_origin_and_cli_requests_forward_with_deadline(self):
        response=json.dumps({'status':200,'body':base64.b64encode(b'ok').decode(),'content_type':'application/json'})
        for headers in ([],[('Origin','http://127.0.0.1:18080'),('Sec-Fetch-Site','same-origin')]):
            with self.subTest(headers=headers),patch.object(ingress.subprocess,'run',return_value=SimpleNamespace(stdout=response)) as run:
                handler=self.request(headers=headers)
                handler.proxy()
                self.assertEqual(handler.wfile.getvalue(),b'ok')
                self.assertEqual(run.call_args.kwargs['timeout'],30)
                payload=json.loads(run.call_args.kwargs['input'])
                self.assertEqual(payload['path'],'/v1/payouts')
                self.assertNotIn('Host',payload['headers'])

    def test_duplicate_host_origin_and_length_are_rejected(self):
        for header in [('Host','127.0.0.1:18080'),('Content-Length','2')]:
            handler=self.request(headers=[header]);handler.proxy()
            self.assertIn(handler.send_error.call_args.args[0],(400,403))
        handler=self.request(headers=[('Origin','http://127.0.0.1:18080')]*2)
        handler.proxy();self.assertEqual(handler.send_error.call_args.args[0],403)

    def test_unsupported_transfer_encoding_and_incomplete_body_rejected(self):
        handler=self.request(headers=[('Transfer-Encoding','chunked')]);handler.proxy()
        self.assertEqual(handler.send_error.call_args.args[0],400)
        handler=self.request();handler.rfile=io.BytesIO(b'{');handler.proxy()
        self.assertEqual(handler.send_error.call_args.args[0],400)

    def test_subprocess_timeout_is_bounded_gateway_error(self):
        with patch.object(ingress.subprocess,'run',side_effect=subprocess.TimeoutExpired(['fake'],30)):
            handler=self.request();handler.proxy()
        self.assertEqual(handler.send_error.call_args.args[0],502)

    def test_failed_readiness_terminates_child_and_removes_its_pidfile(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory);pidfile=state/'ingress.pid';pidfile.write_text('4321')
            proc=Mock(pid=4321);proc.poll.return_value=None
            with patch.object(ingress,'STATE',state),patch.object(ingress,'probe',return_value=False),\
                 patch.object(ingress.subprocess,'Popen',return_value=proc),\
                 patch.object(ingress.time,'monotonic',side_effect=[0,0,11]),patch.object(ingress.time,'sleep'):
                with self.assertRaises(SystemExit): ingress.start(18080,pidfile)
            proc.terminate.assert_called_once()
            proc.wait.assert_called_once_with(timeout=ingress.STOP_TIMEOUT)
            self.assertFalse(pidfile.exists())

    def test_termination_timeout_escalates_and_waits_again(self):
        proc=Mock();proc.poll.return_value=None
        proc.wait.side_effect=[subprocess.TimeoutExpired(['fake'],5),0]
        ingress.stop_child(proc)
        proc.kill.assert_called_once()
        self.assertEqual(proc.wait.call_count,2)

    def test_malformed_pidfile_is_stale_and_does_not_kill(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(ingress.os,'kill') as kill:
            pidfile=Path(directory)/'ingress.pid';pidfile.write_text('broken')
            ingress.stop(pidfile)
            self.assertFalse(pidfile.exists());kill.assert_not_called()

    def test_redirect_handler_never_follows_redirect(self):
        self.assertIsNone(ingress.NoRedirect().redirect_request(None,None,None,None,None,None))


if __name__=='__main__': unittest.main()
