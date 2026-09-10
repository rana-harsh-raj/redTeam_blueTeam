"""Isolated adapter behavior tests; no core service or database runs on the host."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "substitutes"))
spec = importlib.util.spec_from_file_location("monolith", Path(__file__).with_name("server.py"))
monolith = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monolith)


class FakeDB:
    def __init__(self, row=None):
        self.row, self.statements, self.committed = row, [], False
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def cursor(self): return self
    def execute(self, sql, params): self.statements.append((sql, params))
    def fetchone(self): return self.row
    def begin(self): pass
    def commit(self): self.committed = True


class Response:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *_): pass


class Contracts(unittest.TestCase):
    payout_id = "ARENAPAYOUT010"

    def setUp(self):
        monolith.RELAY_CONTROLS.clear()
        monolith.RELAY_PENDING.clear()
        monolith.CREDIT_DEDUCTIONS.clear()
        monolith.CREDIT_REVERSALS.clear()
        monolith.FTA_RESULTS.clear()
        monolith.FTA_RETRY_PENDING.clear()

    def test_identity_uses_single_parameterized_primary_key_lookup(self):
        db = FakeDB({"id": self.payout_id, "merchant_id": "ARENAM00000001"})
        with patch.object(monolith, "_db", return_value=db), patch.object(monolith, "_http") as http:
            self.assertEqual(monolith._ps_get_payout(self.payout_id)["merchant_id"], "ARENAM00000001")
            self.assertEqual(db.statements, [("SELECT * FROM payouts WHERE id = %s LIMIT 1", (self.payout_id,))])
            http.assert_not_called()

    def test_relay_maps_source_account_not_beneficiary(self):
        request = {"source_id": self.payout_id, "status": "PROCESSED", "source_account_id": 900001,
                   "fund_account_id": 999999, "bank_account_type": "NODAL"}
        sent = []
        def capture(req, **_):
            sent.append(json.loads(req.data))
            return Response()
        with patch.object(monolith, "_ps_get_payout", return_value={"merchant_id": "ARENAM00000001"}), \
             patch.object(monolith.urllib.request, "urlopen", side_effect=capture):
            self.assertEqual(monolith._update_fts_fund_transfer(None, json.dumps(request).encode())[0], 200)
        self.assertEqual(sent[0]["fts_fund_account_id"], "900001")
        self.assertEqual(sent[0]["fts_account_type"], "nodal")

    def test_relay_failure_is_not_acknowledged_as_success(self):
        with patch.object(monolith, "_ps_get_payout", return_value={"merchant_id": "ARENAM00000001"}), \
             patch.object(monolith.urllib.request, "urlopen", side_effect=TimeoutError):
            result = monolith._update_fts_fund_transfer(None, json.dumps({"source_id": self.payout_id, "status": "PROCESSED"}).encode())
        self.assertEqual(result[0], 502)

    def test_details_relay_omits_fts_narration_from_strict_payouts_dto(self):
        # payouts/internal/app/dtos/payoutUpdate.go:34 DetailsUpdateRequest has
        # no narration field, even when a directly created FTS transfer has one.
        request = {"source_id": self.payout_id, "status": "PROCESSED", "fund_transfer_id": 42,
                   "source_account_id": 900001, "bank_account_type": "NODAL",
                   "utr": "UTR_CONTRACT", "mode": "IMPS", "channel": "RBL",
                   "bank_status_code": "SUCCESS", "failure_reason": "", "remarks": "completed",
                   "return_utr": None, "gateway_ref_no": "GATEWAY_CONTRACT",
                   "narration": "Nonempty narration from direct FTS creation"}
        sent = []
        def strict_payouts(req, **_):
            payload = json.loads(req.data)
            self.assertNotIn("narration", payload)
            sent.append((req.method, req.full_url, payload))
            return Response()
        with patch.object(monolith, "_ps_get_payout", return_value={"merchant_id": "ARENAM00000001"}), \
             patch.object(monolith.urllib.request, "urlopen", side_effect=strict_payouts):
            self.assertEqual(monolith._update_fts_fund_transfer(None, json.dumps(request).encode())[0], 200)
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0][0], "POST")
        self.assertTrue(sent[0][1].endswith("/update_payouts_details_with_fts"))
        self.assertEqual(sent[0][2], {"source_id": self.payout_id, "fund_transfer_id": 42,
                                    "utr": "UTR_CONTRACT", "mode": "IMPS", "channel": "RBL",
                                    "bank_status_code": "SUCCESS", "remarks": "completed",
                                    "gateway_ref_no": "GATEWAY_CONTRACT", "fta_status": "processed"})
        self.assertEqual(sent[1][0], "PATCH")
        self.assertEqual(sent[1][2]["status"], "processed")

    def test_failed_relay_normalizes_fts_status_before_shared_reversal(self):
        request = {"source_id": self.payout_id, "status": "FAILED", "fund_transfer_id": 42,
                   "source_account_id": 900001, "bank_account_type": "NODAL"}
        sent = []
        def capture(req, **_):
            sent.append((req.method, json.loads(req.data)))
            return Response()
        with patch.object(monolith, "_ps_get_payout", return_value={"merchant_id": "ARENAM00000001"}), \
             patch.object(monolith.urllib.request, "urlopen", side_effect=capture):
            self.assertEqual(monolith._update_fts_fund_transfer(None, json.dumps(request).encode())[0], 200)
        self.assertEqual(sent[0][0], "POST")
        self.assertEqual(sent[0][1]["fta_status"], "failed")
        self.assertEqual(sent[1][0], "PATCH")
        self.assertEqual(sent[1][1]["fts_status"], "failed")
        self.assertEqual(sent[1][1]["status"], "reversed")

    def test_balance_read_uses_mirror_without_fetching_new_ledger_truth(self):
        rows = [{"id": "ARENABAL000001", "balance": 123}]
        with patch.object(monolith, "_balance_rows", return_value=rows), patch.object(monolith, "_ledger_merchant_balance") as ledger:
            status, body = monolith._internal_balances_queued(None, b'{"balance_ids":["ARENABAL000001"]}')
        self.assertEqual((status, body), (200, {"balances": {"ARENABAL000001": 123}}))
        ledger.assert_not_called()

    def test_balance_sync_commits_ledger_truth_before_event(self):
        db = FakeDB()
        rows = [{"id": "ARENABAL000001", "merchant_id": "ARENAM00000001", "account_type": "shared"}]
        def send(*args):
            self.assertTrue(db.committed)
            self.assertEqual(args[2], {"balance_ids": ["ARENABAL000001"]})
            return 200, {"accepted": True}
        with tempfile.NamedTemporaryFile(mode="w") as secret:
            secret.write("synthetic-test-secret"); secret.flush()
            with patch.object(monolith, "_balance_rows", return_value=rows), \
                 patch.object(monolith, "_ledger_merchant_balance", return_value=456), \
                 patch.object(monolith, "_db", return_value=db), patch.object(monolith, "_http", side_effect=send), \
                 patch.object(monolith, "PS_RELAY_AUTH_PASS_FILE", secret.name), patch.object(monolith.time, "time", return_value=12345):
                status, result = monolith._arena_balance_sync(None, b'{"balance_ids":["ARENABAL000001"]}')
        self.assertEqual(status, 200)
        self.assertTrue(result["event_delivered"])
        self.assertEqual(db.statements[0][1], (456, 12345, "ARENABAL000001"))

    def test_balance_sync_rejects_direct_account_without_inventing_balance(self):
        rows = [{"id": "ARENABAL000002", "merchant_id": "ARENAM00000002", "account_type": "direct"}]
        with patch.object(monolith, "_balance_rows", return_value=rows), patch.object(monolith, "_ledger_merchant_balance") as ledger:
            self.assertEqual(monolith._arena_balance_sync(None, b'{"balance_ids":["ARENABAL000002"]}')[0], 422)
        ledger.assert_not_called()

    def test_reorder_requires_exact_message_permutation(self):
        monolith.RELAY_PENDING[self.payout_id] = [b"first", b"second"]
        with self.assertRaises(ValueError): monolith._release_relay(self.payout_id, order=[0, 0])
        with patch.object(monolith, "_update_fts_fund_transfer", return_value=(200, {})) as relay:
            monolith._release_relay(self.payout_id, order=[1, 0])
            self.assertEqual([c.args[1] for c in relay.call_args_list], [b"second", b"first"])

    def test_drop_is_scoped_to_one_payout(self):
        body = json.dumps({"payout_id": self.payout_id, "mode": "drop"}).encode()
        self.assertEqual(monolith._arena_relay_control(None, body)[0], 200)
        with patch.object(monolith, "_ps_get_payout") as lookup:
            result = monolith._update_fts_fund_transfer(None, json.dumps({"source_id": self.payout_id}).encode())
            self.assertEqual(result[1]["mode"], "drop")
            lookup.assert_not_called()
        self.assertNotIn("ARENAPAYOUT011", monolith.RELAY_CONTROLS)

    def test_direct_create_uses_merchant_specific_source_mapping(self):
        merchant_id = "ARENAM00000002"
        payout = {"merchant_id": merchant_id, "status": "initiated", "amount": 123, "mode": "IMPS"}
        fixture = {"account_type": "direct", "fts_fund_account_id": 900192, "fts_source_account_id": 900193}
        with patch.object(monolith, "MERCHANTS", {merchant_id: fixture}), \
             patch.object(monolith, "_ps_get_payout", return_value=payout), \
             patch.object(monolith, "_http", return_value=(200, {})) as transfer:
            status, _ = monolith._create_fta(SimpleNamespace(path="/payouts_service/create_fta/" + self.payout_id), b"")
        self.assertEqual(status, 200)
        request = transfer.call_args.args[2]
        self.assertEqual(request["account"]["fund_account_id"], 900192)
        self.assertEqual(request["transfer"]["preferred_source_account_id"], 900193)

    def test_dual_write_returns_success(self):
        self.assertEqual(monolith._dual_write(None,b'{"payout_id":"ARENAPAYOUT010","timestamp":1}'),(200,{"status":"success"}))

    def test_merchant_response_does_not_expose_adapter_routing(self):
        fixture={"merchant":{"id":"M1"},"merchant_detail":{},"account_type":"shared","fts_fund_account_id":1}
        with patch.object(monolith,"MERCHANTS",{"M1":fixture}):
            self.assertEqual(monolith._get_merchant(SimpleNamespace(path="/internal/merchants/M1"),b"")[1],
                             {"merchant":{"id":"M1","feature":[]},"merchant_detail":{}})   # M6 applies runtime feature overrides to the record PS parses

    def test_status_details_returns_types_not_source_ids(self):
        request={"source_details":[{"source_id":"WF00001","source_type":"workflow"},{"source_id":"WH00001","source_type":"webhook"}]}
        self.assertEqual(monolith._status_details_source_update(None,json.dumps(request).encode())[1],
                         {"sources_updated":["workflow","webhook"]})

    def test_credit_deduction_and_reversal_are_idempotent(self):
        request={"payout_id":"P1","merchant_id":"M1","balance_id":"B1","fees":236,"tax":36,"status":"created"}
        with patch.object(monolith,"CREDIT_BALANCES",{"B1":1000}):
            for _ in range(2):
                self.assertEqual(monolith._deduct_credits(None,json.dumps(request).encode())[1],{"fees":200,"tax":0,"credits_used":True})
            self.assertEqual(monolith.CREDIT_BALANCES["B1"],800)
            reverse={"payout_id":"P1","merchant_id":"M1","balance_id":"B1","entity_type":"payout","fee_type":"reward_fee"}
            for _ in range(2): self.assertEqual(monolith._reverse_credits(None,json.dumps(reverse).encode())[1],{"success":True})
            self.assertEqual(monolith.CREDIT_BALANCES["B1"],1000)

    def test_pricing_matches_all_seeded_dimensions_and_fails_unknown(self):
        plan={"plan_id":"PLAN1","rules":[{"method":"fund_transfer","mode":"IMPS","channel":"RBL","fee_type":"","min_amount":1,"max_amount":999,"fees":200,"tax":36}]}
        request={"payout_id":"P1","merchant_id":"M1","balance_id":"B1","method":"fund_transfer","mode":"IMPS","channel":"RBL","fee_type":"","amount":100}
        with patch.object(monolith,"PRICING",{"M1":plan}):
            self.assertEqual(monolith._fetch_pricing_info(None,json.dumps(request).encode())[1]["fees"],200)
            request["amount"]=1000
            self.assertTrue(monolith._fetch_pricing_info(None,json.dumps(request).encode())[1]["error"])
            request["fee_type"]="free_payout"
            self.assertEqual(monolith._fetch_pricing_info(None,json.dumps(request).encode())[1]["fees"],0)

    def test_free_payout_counter_decreases_per_balance(self):
        rows=[{"id":"ARENABAL000001","type":"banking"}]
        request={"merchant_id":"M1","balance_id":"ARENABAL000001","payout_id":"P1"}
        with patch.object(monolith,"_balance_rows",return_value=rows),patch.object(monolith,"FREE_PAYOUT_COUNTERS",{"ARENABAL000001":{"balance_id":"ARENABAL000001","free_payouts_consumed":5}}):
            self.assertEqual(monolith._decrement_free_payouts(None,json.dumps(request).encode())[1]["free_payouts_consumed"],4)

    def test_fts_create_timeout_is_one_second_and_queues_retry(self):
        payout={"merchant_id":"M1","status":"initiated","amount":100,"transaction_id":"J1"}
        fixture={"fts_fund_account_id":101,"fts_source_account_id":101}
        with patch.object(monolith,"MERCHANTS",{"M1":fixture}),patch.object(monolith,"_ps_get_payout",return_value=payout), \
             patch.object(monolith,"_http",side_effect=TimeoutError) as http,patch.object(monolith.threading,"Timer") as timer:
            status,result=monolith._create_fta(SimpleNamespace(path="/payouts_service/create_fta/"+self.payout_id),b"")
            self.assertEqual(status,200)
            self.assertIsNone(result["error"])
            self.assertEqual(http.call_args.kwargs["timeout"],1)
            timer.return_value.start.assert_called_once()



if __name__ == "__main__":
    unittest.main()
