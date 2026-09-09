import tempfile, unittest
from pathlib import Path
from controlplane.tests import _util  # noqa: F401
from controlplane.store import ControlStore
from controlplane.models import ModelRouter, family_of, _is_mini
from controlplane.verify import Verifier


class TestModels(unittest.TestCase):
    def test_mini_tier_excludes_gemini(self):
        self.assertTrue(_is_mini("gpt-5.4-mini"))
        self.assertFalse(_is_mini("gemini-2.5-pro"))

    def test_verifier_decorrelated_and_not_producer(self):
        r = ModelRouter(["claude-opus-4-8", "gpt-5.5", "gemini-2.5-pro"])
        v = r.verifier_for("claude-opus-4-8")
        self.assertNotEqual(family_of(v), "claude")
        self.assertNotEqual(v, "claude-opus-4-8")

    def test_fallback_is_a_different_model(self):
        r = ModelRouter(["claude-opus-4-8", "gpt-5.5"])
        self.assertEqual(r.fallback("claude-opus-4-8", available=["claude-opus-4-8", "gpt-5.5"]), "gpt-5.5")


class TestVerifyDecision(unittest.TestCase):
    def test_decision_honesty(self):
        D = Verifier._decide
        self.assertEqual(D({"reproduced": True}, {"verdict": "confirmed"}), "verified")
        self.assertEqual(D({"reproduced": False}, {"verdict": "confirmed"}), "rejected")
        self.assertEqual(D({"reproduced": True}, {"verdict": "refuted"}), "rejected")
        self.assertEqual(D({"reproduced": True}, {"verdict": "known_gap"}), "invalid")
        self.assertEqual(D({"reproduced": None}, {"verdict": "inconclusive"}), "rejected")

    def test_verifier_never_self_verifies(self):
        control = ControlStore("camp-v", root=Path(tempfile.mkdtemp()) / "camp-v")
        control.put("candidates", {"candidate_id": "CAND-001", "worker_id": "W-claim",
                                   "producer_model": "claude-opus-4-8", "twin": "alpha",
                                   "thesis_id": "TH-1", "status": "verifying",
                                   "evidence_summary": "x", "minimal_steps": []})
        r = ModelRouter(["claude-opus-4-8", "gpt-5.5"], store=control)
        calls = {}

        def replay_fn(cand, handle, vmodel, vworker):
            calls["replay_worker"] = vworker
            return {"reproduced": True}

        def judge_fn(cand, handle):
            return {"verdict": "confirmed"}

        ver = Verifier(control, r, replay_fn=replay_fn, judge_fn=judge_fn)
        rec = ver.verify_candidate({"verifies_candidate": "CAND-001"}, worker_id="W-claim")
        self.assertNotEqual(rec["verifier_worker"], "W-claim")
        self.assertNotEqual(family_of(rec["verifier_model"]), "claude")
        self.assertEqual(rec["status"], "verified")
        self.assertTrue(rec["decorrelated"])


if __name__ == "__main__":
    unittest.main()
