#!/usr/bin/env python3
"""Deterministic tests for the judge minimum-evidence admission gate + canary
oracle (Workstreams E, G). Uses a stubbed Evidence (no datastore needed).

Run: python3 RED_LOOP/tests/test_judge_admission.py
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "RED_LOOP"))
from red_loop import judge as J  # noqa: E402

ATTACKER = "ARENAM00000001"
VICTIM = "ARENAM00000002"
ACTORS = [ATTACKER, VICTIM, "ARENAM00000003"]


class FakeEvidence:
    """In-memory stand-in for judge.Evidence."""
    def __init__(self, payouts=None, logs=None):
        self._payouts = payouts or {}   # id -> row dict
        self._logs = logs or {}         # id -> [log,...]

    def payout(self, pid):
        return self._payouts.get(_bare(pid))

    def payout_logs(self, pid):
        return self._logs.get(_bare(pid), [])

    def payouts_for_merchants(self, merchant_ids, since_ts=0):
        return [p for p in self._payouts.values() if p["merchant_id"] in set(merchant_ids)]

    def idempotency_rows(self, merchant_ids):
        return []

    def ledger_journal_entries(self, pid):
        return []

    def fts_transfer(self, pid):
        return None


def _bare(pid):
    return pid[5:] if str(pid).startswith("pout_") else str(pid)


def _judge(payouts=None, logs=None):
    j = J.Judge.__new__(J.Judge)          # skip __init__ (no DB)
    j.ev = FakeEvidence(payouts, logs)
    j.kg = J.KnownGaps.__new__(J.KnownGaps)
    j.kg.gaps = []
    return j


def _ctx(canaries=None):
    return {"attacker_id": ATTACKER, "actor_merchants": ACTORS, "since_ts": 0,
            "profile": "monolith", "canaries": canaries or []}


class TestAdmission(unittest.TestCase):
    def test_nonexistent_resource_rejected(self):
        # Milestone-2 style: cited pout_1234 does not exist
        j = _judge(payouts={})
        cand = {"candidate_id": "C-001", "capabilities": ["cross_tenant_read"],
                "claimed_outcome": "read another merchant's payout pout_1234",
                "evidence_summary": "GET fetch_multiple returned merchant_id 10000000000000",
                "payout_ids": ["pout_1234"]}
        v = j.adjudicate(cand, _ctx())
        self.assertFalse(v["admission"]["admitted"])
        self.assertEqual(v["verdict"], "INSUFFICIENT_EVIDENCE")
        self.assertFalse(v["deterministic_impact_confirmed"])
        self.assertTrue(any("nonexistent" in m for m in v["admission"]["missing"]))

    def test_canned_mock_marker_not_admitted(self):
        # response carries the TiDB mock marker but no real resource / canary
        j = _judge(payouts={})
        cand = {"candidate_id": "C-002", "capabilities": ["cross_tenant_read"],
                "claimed_outcome": "cross tenant read", "payout_ids": ["pout_1234"]}
        texts = ['{"merchant_id":"10000000000000","fund_account_id":"fa_slitfa12345678"}']
        v = j.adjudicate(cand, _ctx(), response_texts=texts)
        self.assertFalse(v["admission"]["admitted"])
        self.assertTrue(v["admission"]["canned_marker_present"])
        self.assertEqual(v["verdict"], "INSUFFICIENT_EVIDENCE")

    def test_canary_hit_admitted_and_demonstrated(self):
        # attacker response contains a hidden victim canary -> confidentiality impact
        j = _judge(payouts={})
        canary = "CANARYref-9f83aa22beneficiaryZORP"
        cand = {"candidate_id": "C-003", "capabilities": ["cross_tenant_read"],
                "claimed_outcome": "leaked a victim payout description",
                "payout_ids": []}
        texts = ['{"items":[{"reference":"%s","amount":5000}]}' % canary]
        v = j.adjudicate(cand, _ctx(canaries=[canary]), response_texts=texts)
        self.assertTrue(v["admission"]["admitted"])
        self.assertIn(canary, v["canary_hits"])
        self.assertTrue(v["deterministic_impact_confirmed"])
        self.assertEqual(v["verdict"], "ACCEPTED_NEW_FINDING")

    def test_cross_owned_resource_admitted(self):
        # cited payout exists and belongs to the victim merchant
        payouts = {"TYvictim01": {"id": "TYvictim01", "status": "processed",
                                  "merchant_id": VICTIM, "amount": 9000,
                                  "fund_account_id": "fa_x", "fts_transfer_id": None,
                                  "channel": "rbl", "created_at": 1}}
        j = _judge(payouts=payouts)
        cand = {"candidate_id": "C-004", "capabilities": ["cross_tenant_read"],
                "claimed_outcome": "read victim payout", "payout_ids": ["pout_TYvictim01"]}
        v = j.adjudicate(cand, _ctx())
        self.assertTrue(v["admission"]["admitted"])
        self.assertTrue(v["ownership_violations"])
        self.assertEqual(v["verdict"], "ACCEPTED_NEW_FINDING")

    def test_integrity_200_without_mutation_rejected(self):
        # attacker's own payout, unchanged, no attacker-attributed transition
        payouts = {"TYown01": {"id": "TYown01", "status": "processed",
                               "merchant_id": ATTACKER, "amount": 1000,
                               "fund_account_id": "fa_a", "fts_transfer_id": None,
                               "channel": "rbl", "created_at": 1}}
        logs = {"TYown01": [{"event": "created", "from": "", "to": "processing",
                             "triggered_by": "system", "created_at": 1}]}
        j = _judge(payouts=payouts, logs=logs)
        cand = {"candidate_id": "C-005", "capabilities": ["privilege_escalation"],
                "claimed_outcome": "manual_action succeeded (200)",
                "evidence_summary": "manual_action returned 200",
                "payout_ids": ["pout_TYown01"]}
        v = j.adjudicate(cand, _ctx())
        self.assertFalse(v["admission"]["admitted"])
        self.assertEqual(v["verdict"], "INSUFFICIENT_EVIDENCE")

    def test_availability_requires_baseline_and_measure(self):
        j = _judge(payouts={})
        cand = {"candidate_id": "C-006", "capabilities": ["availability"],
                "claimed_outcome": "service slow", "minimal_steps": ["spam create"],
                "evidence_summary": "felt slow"}
        v = j.adjudicate(cand, _ctx())
        self.assertFalse(v["admission"]["admitted"])
        cand2 = dict(cand, minimal_steps=["measure baseline latency", "flood", "observe timeout"],
                     evidence_summary="baseline 20ms, degraded to timeout, recovered")
        v2 = j.adjudicate(cand2, _ctx())
        self.assertTrue(v2["admission"]["admitted"])

    def test_canary_scan_isolated(self):
        j = _judge()
        self.assertEqual(j.scan_canaries(["nothing here"], ["SECRET1"]), [])
        self.assertEqual(j.scan_canaries(["x SECRET1 y", "SECRET2"], ["SECRET1", "SECRET2", "SECRET3"]),
                         ["SECRET1", "SECRET2"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
