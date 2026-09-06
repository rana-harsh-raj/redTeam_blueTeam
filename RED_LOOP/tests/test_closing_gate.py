#!/usr/bin/env python3
"""M4 closing lifecycle gate tests (T08 §3.5). No network, no docker."""
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "RED_LOOP"))
from red_loop.state import CampaignStore  # noqa: E402
from red_loop import lifecycle_gate as G  # noqa: E402
from red_loop.hypotheses import HypothesisManager, HypothesisState as S  # noqa: E402


def _codes(result):
    return {r["code"] for r in result["reasons"]}


class TestGate(unittest.TestCase):
    def test_gate_fails_when_top_priority_only_proposed(self):
        hyps = [{"hypothesis_id": "H-1", "priority": 4, "status": "proposed",
                 "created_at": "2026-01-01T00:00:00Z", "evidence_refs": []}]
        r = G.evaluate(hyps, claims_success=True)
        self.assertFalse(r["passed"])
        self.assertIn("top_priority_unresolved", _codes(r))

    def test_gate_passes_when_top_priority_resolved(self):
        hyps = [{"hypothesis_id": "H-1", "priority": 4, "status": "supported",
                 "created_at": "2026-01-01T00:00:00Z", "evidence_refs": ["e"]},
                {"hypothesis_id": "H-2", "priority": 2, "status": "closed",
                 "created_at": "2026-01-01T00:00:00Z", "evidence_refs": []}]
        r = G.evaluate(hyps, claims_success=True)
        self.assertTrue(r["passed"], r["reasons"])

    def test_gate_fails_blocked_without_enum(self):
        hyps = [{"hypothesis_id": "H-1", "priority": 3, "status": "blocked",
                 "blocker": None, "created_at": "2026-01-01T00:00:00Z", "evidence_refs": []}]
        r = G.evaluate(hyps, claims_success=True)
        self.assertFalse(r["passed"])
        self.assertIn("blocked_without_valid_blocker", _codes(r))

    def test_gate_passes_blocked_with_valid_enum(self):
        hyps = [{"hypothesis_id": "H-1", "priority": 3, "status": "blocked",
                 "blocker": "fidelity_insufficient", "created_at": "2026-01-01T00:00:00Z",
                 "evidence_refs": []}]
        r = G.evaluate(hyps, claims_success=True)
        self.assertTrue(r["passed"], r["reasons"])

    def test_gate_fails_supported_without_independent_replay(self):
        hyps = [{"hypothesis_id": "H-1", "priority": 4, "status": "supported",
                 "unauthorized_effect": True, "independent_replay_ok": False,
                 "created_at": "2026-01-01T00:00:00Z", "evidence_refs": ["e"]}]
        r = G.evaluate(hyps, claims_success=True)
        self.assertFalse(r["passed"])
        self.assertIn("supported_without_independent_replay", _codes(r))

    def test_gate_does_not_block_non_success_close(self):
        hyps = [{"hypothesis_id": "H-1", "priority": 4, "status": "proposed",
                 "created_at": "2026-01-01T00:00:00Z", "evidence_refs": []}]
        r = G.evaluate(hyps, claims_success=False)
        self.assertTrue(r["passed"])

    def test_gate_over_store_uses_v2(self):
        d = tempfile.mkdtemp(prefix="gate-")
        store = CampaignStore(campaign_id="g", root=Path(d) / "g")
        mgr = HypothesisManager(store)
        mgr.propose("top claim", target_assets=[{"asset": "payouts"}], priority=4)
        g = G.lifecycle_gate("g", claims_success=True, store=store)
        self.assertFalse(g["passed"])  # top hyp only proposed
        self.assertEqual(g["source"], "v2")

    def test_export_writes_file(self):
        d = tempfile.mkdtemp(prefix="gate-")
        store = CampaignStore(campaign_id="g2", root=Path(d) / "g2")
        mgr = HypothesisManager(store)
        hid, _ = mgr.propose("c", target_assets=[{"asset": "payouts"}])
        mgr.advance(hid, S.CLAIMED, reason="claim")
        doc = G.export_lifecycle("g2", store=store)
        self.assertTrue((store.root / "m4-hypothesis-lifecycle.json").exists())
        self.assertEqual(doc["hypotheses"][0]["status"], "claimed")
        self.assertEqual(len(doc["hypotheses"][0]["transitions"]), 1)

    def test_gate_on_legacy_projection(self):
        d = tempfile.mkdtemp(prefix="gate-")
        store = CampaignStore(campaign_id="leg", root=Path(d) / "leg")
        # legacy-only run: single proposed hypothesis (the M3.1 hole)
        store.add_hypothesis("only claim", assets=["payouts"], status="proposed")
        g = G.lifecycle_gate("leg", claims_success=True, store=store)
        self.assertEqual(g["source"], "legacy_projection")
        self.assertFalse(g["passed"])


if __name__ == "__main__":
    unittest.main()
