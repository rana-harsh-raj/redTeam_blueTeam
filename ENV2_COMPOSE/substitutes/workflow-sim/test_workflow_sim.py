#!/usr/bin/env python3
"""Self-contained stdlib unittest for workflow-sim/server.py.

Run:  python3 ENV2_COMPOSE/substitutes/workflow-sim/test_workflow_sim.py

Starts the real server module on an ephemeral port pointed at a tiny local
stub that impersonates payouts-api and records every inbound request, then
asserts the create/park/decide protocol end to end. No docker, no network
beyond loopback.
"""
import base64
import importlib.util
import json
import os
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CALLBACK_PASSWORD = "s3cr3t-callback-pass"


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---- fake payouts-api ----------------------------------------------------
class _FakePayoutsAPI(BaseHTTPRequestHandler):
    requests = []            # shared: [{path, headers, body, method}]
    force_status = 200       # override to exercise 201/409 paths

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = raw.decode("utf-8", "replace")
        _FakePayoutsAPI.requests.append({
            "path": self.path,
            "method": "POST",
            # lowercase keys: HTTP header names are case-insensitive and
            # urllib/http.client may re-case them on the wire
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": body,
        })
        status = _FakePayoutsAPI.force_status
        payload = json.dumps({"entity": "payout", "status": "processing"}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _post_json(url, payload, headers=None):
    data = json.dumps(payload).encode()
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read() or b"{}")


def _get_json(url):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.status, json.loads(resp.read() or b"{}")


def _make_create_body(payout_id, owner_id, amount, creator_id):
    approve_path = "/v1/payouts/payouts_internal/%s/approve" % payout_id
    reject_path = "/v1/payouts/payouts_internal/%s/reject" % payout_id
    hdrs = {"x-creator-id": creator_id, "X-Razorpay-Account": owner_id}
    resp_handler = {"type": "success_status_codes", "success_status_codes": [200, 201, 409]}
    domain = {
        "approved": {"type": "basic", "method": "post", "service": "payouts_live",
                     "url_path": approve_path, "headers": hdrs,
                     "payload": {"queue_if_low_balance": False},
                     "response_handler": resp_handler},
        "rejected": {"type": "basic", "method": "post", "service": "payouts_live",
                     "url_path": reject_path, "headers": hdrs,
                     "payload": {"queue_if_low_balance": False},
                     "response_handler": resp_handler},
    }
    return {"workflow": {
        "entity_id": payout_id,
        "entity_type": "payout",
        "owner_id": owner_id,
        "owner_type": "merchant",
        "creator_id": creator_id,
        "creator_type": "user",
        "service": "rx_live",
        "config_version": "1",
        "diff": {"old": {"merchant_id": "", "amount": 0},
                 "new": {"merchant_id": owner_id, "amount": amount}},
        "callback_details": {
            "state_callbacks": {},
            "workflow_callbacks": {"processed": {"domain_status": domain}},
        },
    }}


class WorkflowSimTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sim_port = _free_port()
        cls.stub_port = _free_port()

        # temp callback password file
        fd, cls.pass_file = tempfile.mkstemp(prefix="wfl_cb_pass_")
        with os.fdopen(fd, "w") as f:
            f.write(CALLBACK_PASSWORD + "\n")

        os.environ["STUB_PORT"] = str(cls.sim_port)
        os.environ["STUB_NAME"] = "workflow-sim-test"
        os.environ["PS_API_URL"] = "http://127.0.0.1:%d" % cls.stub_port
        os.environ["WORKFLOW_INBOUND_USER"] = "workflow"
        os.environ["WORKFLOW_INBOUND_PASS"] = "workflow"
        os.environ["WORKFLOW_CALLBACK_USER"] = "rzp_live"
        os.environ["WORKFLOW_CALLBACK_PASS_FILE"] = cls.pass_file

        # import the server module fresh, AFTER env is set (it reads env + the
        # secret file at import time)
        spec = importlib.util.spec_from_file_location(
            "workflow_sim_server", os.path.join(HERE, "server.py"))
        cls.server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.server)

        # start workflow-sim
        cls.sim_httpd = ThreadingHTTPServer(("127.0.0.1", cls.sim_port),
                                            cls.server.WorkflowSimHandler)
        cls.sim_thread = threading.Thread(target=cls.sim_httpd.serve_forever, daemon=True)
        cls.sim_thread.start()

        # start fake payouts-api
        _FakePayoutsAPI.requests = []
        _FakePayoutsAPI.force_status = 200
        cls.stub_httpd = ThreadingHTTPServer(("127.0.0.1", cls.stub_port), _FakePayoutsAPI)
        cls.stub_thread = threading.Thread(target=cls.stub_httpd.serve_forever, daemon=True)
        cls.stub_thread.start()

        cls.sim_base = "http://127.0.0.1:%d" % cls.sim_port
        cls.create_url = cls.sim_base + cls.server.WfCreate

    @classmethod
    def tearDownClass(cls):
        cls.sim_httpd.shutdown()
        cls.stub_httpd.shutdown()
        try:
            os.remove(cls.pass_file)
        except OSError:
            pass

    def _basic(self, user, password):
        return {"Authorization": "Basic " + base64.b64encode(
            ("%s:%s" % (user, password)).encode()).decode()}

    def test_01_create_requires_auth(self):
        body = _make_create_body("poutNOAUTH01", "MERCHANT01", 10000, "user_A")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            _post_json(self.create_url, body, self._basic("workflow", "wrong"))
        self.assertEqual(cm.exception.code, 401)

    def test_02_create_parks_pending_no_callback(self):
        _FakePayoutsAPI.requests = []
        body = _make_create_body("poutABC0000001", "MERCHANT01", 250000, "user_creator_1")
        status, resp = _post_json(self.create_url, body, self._basic("workflow", "workflow"))
        self.assertEqual(status, 200)
        self.assertTrue(resp["id"], "id must be non-empty")
        self.assertTrue(resp["id"].startswith("wfl_"))
        self.assertEqual(resp["entity_id"], "poutABC0000001")
        self.assertTrue(resp.get("config_id"))
        # NO callback fired on create
        self.assertEqual(_FakePayoutsAPI.requests, [])
        # pending recorded
        st, pend = _get_json(self.sim_base + "/_arena/pending")
        self.assertEqual(st, 200)
        ids = [p["payout_id"] for p in pend["pending"]]
        self.assertIn("poutABC0000001", ids)
        # health reflects count
        st, h = _get_json(self.sim_base + "/_arena/health")
        self.assertEqual(h["status"], "ok")
        self.assertGreaterEqual(h["pending"], 1)

    def test_03_decide_approve_fires_real_callback(self):
        _FakePayoutsAPI.requests = []
        pid = "poutAPPROVE001"
        _post_json(self.create_url,
                   _make_create_body(pid, "MERCHANT77", 500000, "user_creator_2"),
                   self._basic("workflow", "workflow"))
        status, resp = _post_json(self.sim_base + "/_arena/decide",
                                  {"payout_id": pid, "decision": "approve",
                                   "queue_if_low_balance": True})
        self.assertEqual(status, 200)
        self.assertTrue(resp["callback"]["ok"])
        self.assertEqual(resp["callback"]["status_code"], 200)

        self.assertEqual(len(_FakePayoutsAPI.requests), 1)
        req = _FakePayoutsAPI.requests[0]
        self.assertEqual(req["path"], "/v1/payouts/payouts_internal/%s/approve" % pid)
        # correct callback Basic auth (rzp_live:<secret file>)
        expected_auth = "Basic " + base64.b64encode(
            ("rzp_live:%s" % CALLBACK_PASSWORD).encode()).decode()
        self.assertEqual(req["headers"].get("authorization"), expected_auth)
        # X-Razorpay-Account = owner/merchant id, x-creator-id = creator
        self.assertEqual(req["headers"].get("x-razorpay-account"), "MERCHANT77")
        self.assertEqual(req["headers"].get("x-creator-id"), "user_creator_2")
        # body carries queue_if_low_balance
        self.assertEqual(req["body"], {"queue_if_low_balance": True})

    def test_04_decide_reject_fires_real_callback(self):
        _FakePayoutsAPI.requests = []
        pid = "poutREJECT0001"
        _post_json(self.create_url,
                   _make_create_body(pid, "MERCHANT88", 900000, "user_creator_3"),
                   self._basic("workflow", "workflow"))
        status, resp = _post_json(self.sim_base + "/_arena/decide",
                                  {"payout_id": pid, "decision": "reject"})
        self.assertEqual(status, 200)
        self.assertTrue(resp["callback"]["ok"])
        self.assertEqual(len(_FakePayoutsAPI.requests), 1)
        req = _FakePayoutsAPI.requests[0]
        self.assertEqual(req["path"], "/v1/payouts/payouts_internal/%s/reject" % pid)
        self.assertEqual(req["headers"].get("x-razorpay-account"), "MERCHANT88")
        self.assertEqual(req["body"], {})  # reject sends empty body

    def test_05_409_treated_as_success(self):
        _FakePayoutsAPI.requests = []
        _FakePayoutsAPI.force_status = 409
        try:
            pid = "poutCONFLICT01"
            _post_json(self.create_url,
                       _make_create_body(pid, "MERCHANT99", 100, "user_creator_4"),
                       self._basic("workflow", "workflow"))
            status, resp = _post_json(self.sim_base + "/_arena/decide",
                                      {"payout_id": pid, "decision": "approve"})
            self.assertEqual(status, 200)
            self.assertTrue(resp["callback"]["ok"], "409 must be treated as success")
            self.assertEqual(resp["callback"]["status_code"], 409)
        finally:
            _FakePayoutsAPI.force_status = 200

    def test_06_decide_unknown_payout_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            _post_json(self.sim_base + "/_arena/decide",
                       {"payout_id": "poutDOESNOTEXIST", "decision": "approve"})
        self.assertEqual(cm.exception.code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
