#!/usr/bin/env python3
"""M4 hypothesis lifecycle unit tests (T08 §3.5). No network, no docker."""
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "RED_LOOP"))
from red_loop.state import CampaignStore  # noqa: E402
from red_loop import hypotheses as H  # noqa: E402
from red_loop.hypotheses import HypothesisState as S, Blocker, LifecycleError  # noqa: E402


def _store(name="t"):
    d = tempfile.mkdtemp(prefix="hyp-")
    return CampaignStore(campaign_id=name, root=Path(d) / name)


class TestTransitions(unittest.TestCase):
    def _rec(self):
        return {"status": S.PROPOSED.value, "evidence_refs": [], "transitions": []}

    def test_legal_transition_allowed(self):
        r = self._rec()
        H.advance(r, S.CLAIMED, reason="c")
        H.advance(r, S.EXPERIMENT_DEFINED, reason="d")
        self.assertEqual(r["status"], S.EXPERIMENT_DEFINED.value)

    def test_illegal_transition_rejected(self):
        r = self._rec()
        with self.assertRaises(LifecycleError):
            H.advance(r, S.ACCEPTED, reason="nope")

    def test_blocked_requires_blocker_enum(self):
        r = self._rec()
        with self.assertRaises(LifecycleError):
            H.advance(r, S.BLOCKED, reason="stuck")  # no blocker
        r2 = self._rec()
        with self.assertRaises(LifecycleError):
            H.advance(r2, S.BLOCKED, reason="stuck", blocker="not_a_real_blocker")
        r3 = self._rec()
        H.advance(r3, S.BLOCKED, reason="stuck", blocker=Blocker.ROUTE_NOT_EXPOSED)
        self.assertEqual(r3["blocker"], "route_not_exposed")

    def test_blocked_is_not_falsified(self):
        blocked = {"status": S.BLOCKED.value, "blocker": "fidelity_insufficient"}
        falsified = {"status": S.FALSIFIED.value, "evidence_refs": ["e"],
                     "transitions": [{"to": "executing"}]}
        roll = H.rollup_by_state([blocked, falsified])
        self.assertEqual(roll["blocked"], 1)
        self.assertEqual(roll["falsified"], 1)
        self.assertTrue(H.is_blocked(blocked))
        self.assertFalse(H.is_falsified(blocked))

    def test_terminal_reason_stamped(self):
        r = self._rec()
        H.advance(r, S.BLOCKED, reason="route missing", blocker=Blocker.ROUTE_NOT_EXPOSED)
        self.assertEqual(r["terminal_status_reason"], "route missing")
        r2 = {"status": S.EVIDENCE_GATHERED.value, "evidence_refs": ["e"],
              "transitions": [{"to": "executing"}]}
        H.advance(r2, S.FALSIFIED, reason="no effect")
        self.assertEqual(r2["terminal_status_reason"], "no effect")

    def test_falsified_requires_evidence_and_execution(self):
        # falsified without evidence refs is invalid
        r = {"status": S.FALSIFIED.value, "evidence_refs": [], "transitions": []}
        ok, why = H.validate_falsification(r)
        self.assertFalse(ok)
        self.assertEqual(why, "falsified_without_evidence_refs")
        # with evidence but no executed experiment is still invalid
        r2 = {"status": S.FALSIFIED.value, "evidence_refs": ["e"], "transitions": []}
        ok2, why2 = H.validate_falsification(r2)
        self.assertFalse(ok2)


class TestFingerprint(unittest.TestCase):
    def test_fingerprint_stable_and_id_insensitive(self):
        a = [{"service": "payouts", "asset": "CreatePayout", "kind": "route"}]
        fp1 = H.fingerprint(a, "override merchant on pout_ABC123 to write cross-tenant")
        fp2 = H.fingerprint(a, "override merchant on pout_ZZZ999 to write cross-tenant")
        self.assertEqual(fp1, fp2)
        # different asset -> different fingerprint
        b = [{"service": "contacts", "asset": "CreateContact", "kind": "route"}]
        fp3 = H.fingerprint(b, "override merchant on pout_ABC123 to write cross-tenant")
        self.assertNotEqual(fp1, fp3)

    def test_duplicate_detection(self):
        existing = [{"hypothesis_id": "H-001", "fingerprint": "abc123", "status": "proposed"}]
        self.assertEqual(H.duplicate_of("abc123", existing), "H-001")
        self.assertIsNone(H.duplicate_of("zzz", existing))


class TestPriorityAndProjection(unittest.TestCase):
    def test_highest_priority_open(self):
        # mandate: higher int == higher priority; ties broken by earliest created_at
        hyps = [
            {"hypothesis_id": "H-1", "priority": 2, "status": "claimed", "created_at": "2026-01-01T00:00:00Z"},
            {"hypothesis_id": "H-2", "priority": 4, "status": "proposed", "created_at": "2026-01-02T00:00:00Z"},
            {"hypothesis_id": "H-3", "priority": 4, "status": "executing", "created_at": "2026-01-01T00:00:00Z"},
            {"hypothesis_id": "H-4", "priority": 9, "status": "accepted", "created_at": "2026-01-01T00:00:00Z"},
        ]
        top = H.highest_priority_open(hyps)
        # H-4 is highest priority but terminal (accepted) -> excluded.
        # Among open pri-4 (H-2, H-3), earliest created_at wins -> H-3.
        self.assertEqual(top["hypothesis_id"], "H-3")

    def test_all_terminal_returns_none(self):
        hyps = [{"hypothesis_id": "H-1", "priority": 3, "status": "accepted"}]
        self.assertIsNone(H.highest_priority_open(hyps))

    def test_legacy_projection(self):
        legacy = {"hypothesis_id": "H-001", "claim": "x", "assets": ["payouts"],
                  "status": "testing", "ts": "2026-01-01T00:00:00Z"}
        v2 = H.project_legacy(legacy)
        self.assertEqual(v2["status"], S.EXECUTING.value)
        self.assertTrue(v2["_legacy"])


class TestManager(unittest.TestCase):
    def test_propose_and_dedup(self):
        store = _store()
        mgr = H.HypothesisManager(store)
        hid, dup = mgr.propose("cross tenant write on pout_ABC123", target_assets=[{"asset": "payouts"}])
        self.assertFalse(dup)
        hid2, dup2 = mgr.propose("cross tenant write on pout_XYZ999", target_assets=[{"asset": "payouts"}])
        self.assertTrue(dup2)
        self.assertEqual(hid, hid2)
        events = [e for e in store._read_all("events") if e.get("event") == "duplicate_suppressed"]
        self.assertEqual(len(events), 1)

    def test_manager_advance_persists_and_recovers(self):
        store = _store("recov")
        mgr = H.HypothesisManager(store)
        hid, _ = mgr.propose("c", target_assets=[{"asset": "payouts"}])
        mgr.advance(hid, S.CLAIMED, reason="claim")
        # fresh manager over same dir recovers the state
        store2 = CampaignStore(campaign_id="recov", root=store.root)
        mgr2 = H.HypothesisManager(store2)
        self.assertEqual(mgr2.get(hid)["status"], S.CLAIMED.value)

    def test_illegal_advance_via_manager_raises(self):
        store = _store()
        mgr = H.HypothesisManager(store)
        hid, _ = mgr.propose("c", target_assets=[{"asset": "payouts"}])
        with self.assertRaises(LifecycleError):
            mgr.advance(hid, S.ACCEPTED, reason="skip")


if __name__ == "__main__":
    unittest.main()
