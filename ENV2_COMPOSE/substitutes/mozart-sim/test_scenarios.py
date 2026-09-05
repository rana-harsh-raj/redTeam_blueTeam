"""HTTP contract checks for explicit synthetic bank controls; no core service execution."""
import importlib.util
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location("arena_mozart_sim", Path(__file__).with_name("server.py"))
bank = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bank)


class BankScenarioContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = bank.ThreadingHTTPServer(("127.0.0.1", 0), bank.MozartSimHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = "http://127.0.0.1:%s" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        bank.SCENARIOS.clear()
        bank.ATTEMPTS.clear()
        bank.EVENTS.clear()

    def post(self, path, body):
        request = urllib.request.Request(self.url+path, data=json.dumps(body).encode(), headers={"Content-Type":"application/json"})
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def control(self, key, scenario, **extra):
        status, data = self.post("/_arena/scenario", {"key":key,"scenario":scenario,**extra})
        self.assertEqual(status, 200, data)

    def call_bank(self, action, attempt="101", merchant="SYNTHETIC00001"):
        status, data = self.post("/payouts/rbl/v1/"+action, {"entities":{"attempt":{"id":attempt,"merchant_id":merchant,"gateway_ref_no":attempt}}})
        self.assertEqual(status, 200, data)
        return data

    def test_held_attempt_remains_held_until_attempt_release(self):
        self.control("SYNTHETIC00001", "hold")
        self.assertEqual(self.call_bank("transfer_init")["data"]["bank_status_code"], "INITIATED")
        self.control("SYNTHETIC00001", "success")
        self.assertEqual(self.call_bank("transfer_status")["data"]["bank_status_code"], "INITIATED")
        self.control("101", "success")
        released = self.call_bank("transfer_status")
        self.assertEqual(released["data"]["bank_status_code"], "SUCCESS")
        self.assertTrue(released["data"]["utr"])

    def test_merchants_have_independent_outcomes(self):
        self.control("SYNTHETIC00001", "hold")
        self.control("SYNTHETIC00002", "failure")
        self.assertEqual(self.call_bank("transfer_init")["data"]["bank_status_code"], "INITIATED")
        failed = self.call_bank("transfer_init", "102", "SYNTHETIC00002")
        self.assertFalse(failed["success"])
        self.assertEqual(failed["error"]["gateway_error_code"], "INVALID_ACCOUNT_NUMBER")
        self.assertFalse(failed["data"]["is_debited"])

    def test_retryable_insufficient_funds_is_a_separate_scenario(self):
        self.control("101", "insufficient_funds")
        response = self.call_bank("transfer_init")
        self.assertEqual(response["error"]["gateway_error_code"], "INSUFFICIENT_FUND")
        self.assertEqual(response["error"]["internal_error_code"], "INSUFFICIENT_FUND")

    def test_delayed_success_counts_status_polls(self):
        self.control("101", "delayed_success", polls=2)
        self.assertEqual(self.call_bank("transfer_init")["data"]["bank_status_code"], "INITIATED")
        self.assertEqual(self.call_bank("transfer_status")["data"]["bank_status_code"], "INITIATED")
        self.assertEqual(self.call_bank("transfer_status")["data"]["bank_status_code"], "SUCCESS")
        self.assertEqual([e["poll"] for e in bank.EVENTS], [0,1,2])

    def test_invalid_control_is_rejected_without_mutation(self):
        for body in ({"key":"x","scenario":"made_up"}, {"key":"x","scenario":"success","polls":0}, {"scenario":"hold"}):
            self.assertEqual(self.post("/_arena/scenario",body)[0],400)
        self.assertEqual(bank.SCENARIOS,{})

    def test_ambiguous_response_preserves_uncertainty(self):
        self.control("101", "ambiguous_without_utr")
        response = self.call_bank("transfer_init")
        self.assertFalse(response["success"])
        self.assertIsNone(response["data"]["is_debited"])
        self.assertNotIn("utr",response["data"])

    def test_timeout_is_gateway_504_without_a_valid_mozart_envelope(self):
        self.control("101", "timeout")
        with patch.object(bank.time,"sleep") as delay:
            status, payload = self.post("/fts/rbl/v1/transfer_init", {"entities":{"attempt":{"id":"101","merchant_id":"SYNTHETIC00001"}}})
        self.assertEqual(status,504)
        self.assertEqual(payload,{})
        delay.assert_called_once_with(30)

    def test_clear_is_scoped_to_one_attempt(self):
        self.control("101", "hold")
        self.control("102", "failure")
        self.call_bank("transfer_init")
        self.assertEqual(self.post("/_arena/scenario",{"key":"101","clear":True})[0],200)
        self.assertNotIn("101",bank.ATTEMPTS)
        self.assertIn("102",bank.SCENARIOS)


if __name__ == "__main__": unittest.main()
