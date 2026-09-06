"""Offline contract tests for xas-sim (stdlib unittest; no container, no network, no LocalStack).

Each case names the XAS source it reproduces (x-account-statements@e73fd5a). Outbound HTTP
(monolith payout_update) is captured by patching server._post_enrichment_update's transport.
"""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "substitutes"))
sys.path.insert(0, str(Path(__file__).parent))
spec = importlib.util.spec_from_file_location("xas_sim", Path(__file__).with_name("server.py"))
xas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xas)

ACCT, CH, BAL, MID = "2323000085000001", "rbl", "ARENADB5000001", "ARENAD85000001"


def _reset():
    with xas.LOCK:
        xas.STATE["accounts"].clear()
        xas.STATE["statements"].clear()
        xas.STATE["source_events"].clear()
        del xas.STATE["links"][:]
        del xas.STATE["outbound"][:]
        del xas.STATE["fetch_log"][:]


def _account():
    xas.upsert_account({"account_number": ACCT, "channel": CH, "balance_id": BAL, "merchant_id": MID})


def _stmt(**over):
    row = {"id": over.pop("id", "ARENABS0000001"), "merchant_id": MID, "account_number": ACCT, "channel": CH, "type": "debit",
           "utr": "UTR000000001", "amount": 5000, "bank_transaction_id": "S1", "gateway_ref_number": "RZP0000000001",
           "transaction_date": 1788700000, "posted_date": 1788700100, "bank_serial_number": "1", "balance": 100}
    row.update(over)
    return row


def _event(**over):
    ev = {"entity_id": "POUT0000000001", "entity_type": "payout", "utr": "UTR000000001", "event_created_timestamp": 1788700200,
          "event_id": "EVT00000000001", "gateway_ref_no": "RZP0000000001", "cms_ref_no": "S1", "status": "processed", "mode": "IMPS",
          "amount": 5000, "balance_id": BAL}
    ev.update(over)
    return ev


class Capture:
    def __init__(self):
        self.calls = []

    def __call__(self, body, trigger):
        rec = {"request": body, "trigger": trigger, "status": 200, "response": {"status": "success"}}
        self.calls.append(rec)
        with xas.LOCK:
            xas.STATE["outbound"].append(rec)
        return rec


class Handler:
    """Minimal stand-in for the base_stub handler (path + headers)."""
    command = "GET"

    def __init__(self, path):
        self.path = path
        self.headers = {"User-Agent": "test", "Authorization": ""}


class XasSimContract(unittest.TestCase):
    def setUp(self):
        _reset()
        _account()
        self.out = Capture()
        self.patcher = patch.object(xas, "_post_enrichment_update", self.out)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    # ---- schema mismatch (dto.go:15 vs payouts job :41) ----
    def test_event_create_timestamp_mismatch_reproduced(self):
        res = xas.handle_source_event(json.dumps(_event()), "test")
        ev = xas.STATE["source_events"][("EVT00000000001", "payout")]
        self.assertEqual(ev["event_create_timestamp"], 0)
        self.assertTrue(ev["_schema"]["event_created_timestamp_present"])
        self.assertFalse(ev["_schema"]["event_create_timestamp_present"])
        self.assertEqual(res["result"], "handled")

    def test_event_with_xas_spelling_is_stored(self):
        e = _event()
        del e["event_created_timestamp"]
        e["event_create_timestamp"] = 42
        xas.handle_source_event(json.dumps(e), "test")
        self.assertEqual(xas.STATE["source_events"][("EVT00000000001", "payout")]["event_create_timestamp"], 42)

    # ---- validation (dto.go:54-56, service.go:61-66) ----
    def test_event_without_any_reference_is_invalid_and_not_stored(self):
        res = xas.handle_source_event(json.dumps(_event(utr="", gateway_ref_no="", cms_ref_no="")), "test")
        self.assertEqual(res["result"], "invalid_no_reference")
        self.assertEqual(len(xas.STATE["source_events"]), 0)

    # ---- dedup UNIQUE(event_id, entity_type) (service.go:68-88,140-156) ----
    def test_duplicate_event_id_reuses_stored_row(self):
        xas.ingest_statement(_stmt())
        xas.handle_source_event(json.dumps(_event()), "test")
        res = xas.handle_source_event(json.dumps(_event(utr="DIFFERENT")), "test")
        self.assertTrue(res["duplicate"])
        self.assertEqual(len(xas.STATE["source_events"]), 1)
        self.assertEqual(xas.STATE["source_events"][("EVT00000000001", "payout")]["utr"], "UTR000000001")
        self.assertEqual(len(self.out.calls), 1, "replayed event must not produce a second outbound")

    def test_same_event_id_different_entity_type_is_a_second_row(self):
        xas.handle_source_event(json.dumps(_event()), "test")
        xas.handle_source_event(json.dumps(_event(entity_type="payout_x")), "test")
        self.assertEqual(len(xas.STATE["source_events"]), 2)

    # ---- match order utr -> grn -> cms (service.go:190-218) with amount/account guards ----
    def test_event_then_statement_matches_by_utr_and_links_payout(self):
        xas.handle_source_event(json.dumps(_event()), "test")
        rec, existed = xas.ingest_statement(_stmt())
        self.assertFalse(existed)
        res = xas.enrich_with_statement(rec["id"])
        self.assertEqual(res["result"], "linked")
        self.assertEqual(res["matched_key"], "utr")
        self.assertEqual(rec["entity_type"], "payout")
        self.assertEqual(rec["entity_id"], "POUT0000000001")

    def test_statement_then_event_matches_by_grn_case_insensitive(self):
        rec, _ = xas.ingest_statement(_stmt(utr="", gateway_ref_number="rzp0000000001"))
        xas.enrich_with_statement(rec["id"])
        self.assertEqual(rec["entity_type"], "external")
        res = xas.handle_source_event(json.dumps(_event(utr="")), "test")
        self.assertEqual(res["enrichment"]["matched_key"], "gateway_ref_number")
        self.assertEqual(rec["entity_type"], "payout")
        self.assertTrue(self.out.calls[-1]["request"]["converted_from_external"])

    def test_cms_fallback_uses_bank_transaction_id(self):
        rec, _ = xas.ingest_statement(_stmt(utr="", gateway_ref_number=""))
        res = xas.handle_source_event(json.dumps(_event(utr="", gateway_ref_no="")), "test")
        self.assertEqual(res["enrichment"]["matched_key"], "cms_ref_number")
        self.assertEqual(rec["entity_id"], "POUT0000000001")

    def test_wrong_amount_is_external_and_no_link(self):
        xas.handle_source_event(json.dumps(_event()), "test")
        rec, _ = xas.ingest_statement(_stmt(amount=5001))
        res = xas.enrich_with_statement(rec["id"])
        self.assertEqual(res["result"], "external")
        self.assertEqual(rec["entity_type"], "external")
        self.assertEqual(rec["entity_id"], "")
        self.assertEqual(self.out.calls, [])

    def test_other_balance_does_not_match(self):
        xas.upsert_account({"account_number": "2323000085000002", "channel": CH, "balance_id": "OTHERBAL000001", "merchant_id": "OTHERMID000001"})
        rec, _ = xas.ingest_statement(_stmt(account_number="2323000085000002", merchant_id="OTHERMID000001"))
        res = xas.handle_source_event(json.dumps(_event()), "test")
        self.assertEqual(res["enrichment"]["result"], "statement_not_found")
        self.assertEqual(rec["entity_type"], "")

    def test_event_for_unknown_balance_is_noop(self):
        res = xas.handle_source_event(json.dumps(_event(balance_id="NOPE")), "test")
        self.assertEqual(res["enrichment"]["result"], "account_not_found")

    # ---- >2 and dual-type guards (service.go:343-357) ----
    def test_more_than_two_statements_is_noop(self):
        for i in range(3):
            xas.ingest_statement(_stmt(id="ARENABS000000%d" % i, bank_serial_number=str(i), bank_transaction_id="S%d" % i))
        res = xas.handle_source_event(json.dumps(_event()), "test")
        self.assertEqual(res["enrichment"]["result"], "too_many_statements_noop")
        self.assertTrue(all(s["entity_type"] == "" for s in xas.STATE["statements"].values()))

    def test_two_debits_is_noop(self):
        xas.ingest_statement(_stmt(id="ARENABS0000001", bank_serial_number="1", bank_transaction_id="S1"))
        xas.ingest_statement(_stmt(id="ARENABS0000002", bank_serial_number="2", bank_transaction_id="S2"))
        res = xas.handle_source_event(json.dumps(_event()), "test")
        self.assertEqual(res["enrichment"]["result"], "dual_same_type_noop")

    def test_debit_plus_credit_links_payout_and_payout_reversal(self):
        d, _ = xas.ingest_statement(_stmt(id="ARENABS0000001", bank_serial_number="1"))
        c, _ = xas.ingest_statement(_stmt(id="ARENABS0000002", bank_serial_number="2", type="credit", bank_transaction_id="S2"))
        res = xas.handle_source_event(json.dumps(_event()), "test")
        self.assertEqual(res["enrichment"]["result"], "enriched")
        self.assertEqual((d["entity_type"], c["entity_type"]), ("payout", "payout_reversal"))
        self.assertEqual({o["request"]["entity_type"] for o in self.out.calls}, {"payout", "payout_reversal"})

    # ---- relink refusal (payout_enricher.go:38-63) ----
    def test_relink_to_different_entity_refused(self):
        rec, _ = xas.ingest_statement(_stmt())
        xas.handle_source_event(json.dumps(_event()), "test")
        self.assertEqual(rec["entity_id"], "POUT0000000001")
        res = xas.handle_source_event(json.dumps(_event(event_id="EVT00000000002", entity_id="POUT0000000002")), "test")
        self.assertEqual(res["enrichment"]["statements"][0]["decision"], "refused_relink")
        self.assertEqual(rec["entity_id"], "POUT0000000001")
        self.assertEqual(len(self.out.calls), 1)
        self.assertTrue(any(l["kind"] == "StatementAlreadyLinkedError" for l in xas.STATE["links"]))

    def test_relink_same_entity_is_idempotent_no_second_outbound(self):
        rec, _ = xas.ingest_statement(_stmt())
        xas.handle_source_event(json.dumps(_event()), "test")
        res = xas.handle_source_event(json.dumps(_event(event_id="EVT00000000009")), "test")
        self.assertEqual(res["enrichment"]["statements"][0]["decision"], "unchanged")
        self.assertEqual(len(self.out.calls), 1)

    # ---- outbound 9-field body (gateway/api/params.go:38-49, dual_write getSourceUpdateInput) ----
    def test_outbound_body_has_exactly_nine_fields(self):
        rec, _ = xas.ingest_statement(_stmt())
        xas.handle_source_event(json.dumps(_event()), "test")
        body = self.out.calls[0]["request"]
        self.assertEqual(set(body), {"bas_id", "entity_id", "entity_type", "merchant_id", "transaction_date",
                                     "converted_from_external", "utr", "grn", "cms_ref_no"})
        self.assertEqual(body["bas_id"], "ARENABS0000001")
        self.assertEqual(body["entity_id"], "POUT0000000001")
        self.assertEqual(body["entity_type"], "payout")
        self.assertEqual(body["merchant_id"], MID)
        self.assertEqual(body["transaction_date"], 1788700000)
        self.assertIs(body["converted_from_external"], False)
        self.assertEqual((body["utr"], body["grn"], body["cms_ref_no"]), ("UTR000000001", "RZP0000000001", "S1"))

    def test_dual_write_api_resends_unconditionally(self):
        rec, _ = xas.ingest_statement(_stmt())
        xas.enrich_with_statement(rec["id"])       # external -> no CDC outbound
        self.assertEqual(self.out.calls, [])
        st, body = xas._statement_dual_write(Handler("/v1/statement/dual_write"), json.dumps({"id": rec["id"]}).encode())
        self.assertEqual(st, 200)
        self.assertEqual(self.out.calls[0]["trigger"], "dual_write_api")
        self.assertEqual(self.out.calls[0]["request"]["entity_type"], "external")

    # ---- dedupe tuple on sync (rbl.go:740-750) ----
    def test_statement_dedupe_tuple(self):
        a, e1 = xas.ingest_statement(_stmt())
        b, e2 = xas.ingest_statement(_stmt(id="ARENABS0000009"))   # same tuple, different id -> duplicate
        self.assertFalse(e1)
        self.assertTrue(e2)
        self.assertIs(a, b)
        self.assertEqual(len(xas.STATE["statements"]), 1)

    # ---- GET fetch_multiple_by_reference_numbers (get/service.go:425-480, validate.go:174-215) ----
    def test_fetch_multiple_validation(self):
        st, body = xas._fetch_multiple_by_reference_numbers(
            Handler("/v1/account_statements/fetch_multiple_by_reference_numbers?type=debit&account_number=%s&amount=5000&channel=rbl" % ACCT), b"")
        self.assertEqual(st, 400)
        self.assertIn("at least one reference number", body["error"]["description"])
        st, body = xas._fetch_multiple_by_reference_numbers(
            Handler("/v1/account_statements/fetch_multiple_by_reference_numbers?utr=X&type=debit"), b"")
        self.assertEqual(st, 400)

    def test_fetch_multiple_filters_type_and_channel_and_serialises_int64_as_strings(self):
        xas.ingest_statement(_stmt(id="ARENABS0000001", bank_serial_number="1"))
        xas.ingest_statement(_stmt(id="ARENABS0000002", bank_serial_number="2", type="credit", bank_transaction_id="S2"))
        for s in xas.STATE["statements"].values():
            s["entity_type"] = "external"
        h = Handler("/v1/account_statements/fetch_multiple_by_reference_numbers?type=debit&account_number=%s&amount=5000&channel=rbl&utr=UTR000000001" % ACCT)
        st, body = xas._fetch_multiple_by_reference_numbers(h, b"")
        self.assertEqual(st, 200)
        self.assertEqual(len(body["statements"]), 1)
        self.assertEqual(body["statements"][0]["type"], "debit")
        self.assertEqual(body["statements"][0]["entity_type"], "external")
        self.assertIsInstance(body["statements"][0]["amount"], str)
        self.assertIsInstance(body["statements"][0]["transaction_date"], str)
        self.assertIsInstance(body["total_count"], str)
        self.assertEqual(body["total_count"], "2")
        self.assertIsInstance(body["page"], int)
        h = Handler("/v1/account_statements/fetch_multiple_by_reference_numbers?type=debit&account_number=%s&amount=5000&channel=icici&utr=UTR000000001" % ACCT)
        st, body = xas._fetch_multiple_by_reference_numbers(h, b"")
        self.assertEqual(body["statements"], [])
        self.assertEqual(body["total_count"], "2")   # unfiltered count, as get/service.go passes len(accountStatements)

    def test_fetch_multiple_key_order_prefers_utr_over_grn(self):
        xas.ingest_statement(_stmt(id="ARENABS0000001", utr="UTRA", gateway_ref_number="G1", bank_serial_number="1"))
        xas.ingest_statement(_stmt(id="ARENABS0000002", utr="UTRB", gateway_ref_number="G1", bank_serial_number="2", bank_transaction_id="S2"))
        h = Handler("/v1/account_statements/fetch_multiple_by_reference_numbers?type=debit&account_number=%s&amount=5000&channel=rbl&utr=UTRA&gateway_reference_number=G1" % ACCT)
        st, body = xas._fetch_multiple_by_reference_numbers(h, b"")
        self.assertEqual([s["id"] for s in body["statements"]], ["ARENABS0000001"])

    # ---- per-merchant reset ----
    def test_reset_is_per_merchant_only(self):
        xas.ingest_statement(_stmt())
        st, body = xas._arena_reset(Handler("/_arena/reset"), b"")
        self.assertEqual(st, 400)
        st, body = xas._arena_reset(Handler("/_arena/reset?merchant_id=" + MID), b"")
        self.assertEqual(st, 200)
        self.assertEqual(body["removed"]["statements"], 1)
        self.assertEqual(len(xas.STATE["accounts"]), 0)


if __name__ == "__main__":
    unittest.main()
