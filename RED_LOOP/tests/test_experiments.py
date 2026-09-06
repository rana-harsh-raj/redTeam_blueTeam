#!/usr/bin/env python3
"""M4 cross-merchant experiment template tests (T08 §3.5). No network."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "RED_LOOP"))
from red_loop.experiments import (  # noqa: E402
    CrossMerchantExperiment, Arm, REQUIRED_ARMS, ExperimentError, new_cross_merchant,
)


def _all_arms(exp, merchant_b_effect=True):
    exp.observe(Arm.OWN_MERCHANT_CONTROL, status=200, state_observed={"created": True})
    exp.observe(Arm.MERCHANT_B_VARIANT, status=(200 if merchant_b_effect else 403),
                state_observed={"victim_mutated": merchant_b_effect},
                unauthorized_effect=merchant_b_effect,
                denied=not merchant_b_effect, denied_at_layer=None if merchant_b_effect else "gateway")
    exp.observe(Arm.MALFORMED_OR_ABSENT_IDENTITY_VARIANT, status=400, denied=True, denied_at_layer="broker")
    exp.observe(Arm.EXPECTED_DENIAL_CONTROL, status=403, denied=True, denied_at_layer="gateway")
    exp.observe(Arm.STATE_OBSERVATION, state_observed={"ledger_zero_sum": not merchant_b_effect})
    exp.observe(Arm.CLEAN_RESET, status=200, state_observed={"reset": True})


class TestCrossMerchant(unittest.TestCase):
    def test_all_required_arms_declared(self):
        self.assertEqual(len(REQUIRED_ARMS), 6)

    def test_cross_merchant_requires_all_arms(self):
        exp = new_cross_merchant("H-1", "payouts")
        # only own control observed
        exp.observe(Arm.OWN_MERCHANT_CONTROL, status=200, state_observed={"created": True})
        ok, reason = exp.can_support()
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("missing_arms"))
        # fill all arms with a real merchant-B effect -> can support
        _all_arms(exp, merchant_b_effect=True)
        ok2, reason2 = exp.can_support()
        self.assertTrue(ok2, reason2)

    def test_own_merchant_is_control_only(self):
        exp = new_cross_merchant("H-2", "payouts")
        _all_arms(exp, merchant_b_effect=False)  # only own control succeeds
        self.assertTrue(exp.only_own_control_succeeded())
        # cannot support (no unauthorized effect) and cannot accept
        ok, reason = exp.can_support()
        self.assertFalse(ok)
        self.assertEqual(reason, "no_unauthorized_effect_only_controls")
        ok_a, _ = exp.can_accept()
        self.assertFalse(ok_a)

    def test_denied_path_not_safe_evidence(self):
        # merchant-B arm denied at gateway; that denial must NOT count as service
        # safe and must NOT allow falsification without a state observation.
        exp = new_cross_merchant("H-3", "payouts")
        exp.observe(Arm.MERCHANT_B_VARIANT, status=403, denied=True, denied_at_layer="gateway")
        ok, reason = exp.can_falsify()
        self.assertFalse(ok)
        self.assertEqual(reason, "merchant_b_arm_did_not_execute_with_state")
        # denial summary records the layer, never "safe"
        denials = exp.denial_summary()
        self.assertEqual(denials[0]["denied_at_layer"], "gateway")

    def test_falsify_requires_merchant_b_state(self):
        exp = new_cross_merchant("H-4", "payouts")
        exp.observe(Arm.MERCHANT_B_VARIANT, status=200, state_observed={"no_change": True})
        ok, reason = exp.can_falsify()
        self.assertTrue(ok, reason)

    def test_accept_requires_independent_replay(self):
        exp = new_cross_merchant("H-5", "payouts")
        _all_arms(exp, merchant_b_effect=True)
        ok, reason = exp.can_accept()
        self.assertFalse(ok)
        self.assertEqual(reason, "independent_replay_required")
        exp.record_replay(independent=True, reproduced=True)
        ok2, _ = exp.can_accept()
        self.assertTrue(ok2)

    def test_invalid_denied_layer_rejected(self):
        exp = new_cross_merchant("H-6", "payouts")
        with self.assertRaises(ExperimentError):
            exp.observe(Arm.MERCHANT_B_VARIANT, denied=True, denied_at_layer="database")

    def test_invalid_arm_rejected(self):
        exp = new_cross_merchant("H-7", "payouts")
        with self.assertRaises(ExperimentError):
            exp.observe("not_an_arm", status=200)


if __name__ == "__main__":
    unittest.main()
