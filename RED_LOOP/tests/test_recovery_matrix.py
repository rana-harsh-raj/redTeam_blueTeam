#!/usr/bin/env python3
"""M4 recovery-matrix tests: every offline scenario passes; docker ones gated."""
import importlib.util
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "RED_LOOP"))

_spec = importlib.util.spec_from_file_location(
    "m4_recovery_matrix", REPO / "RED_LOOP" / "surface" / "m4_recovery_matrix.py")
mrm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mrm)


class TestRecoveryMatrix(unittest.TestCase):
    def test_subagent_kill(self):
        self.assertTrue(mrm.scenario_subagent_kill()["passed"])

    def test_expired_lease(self):
        self.assertTrue(mrm.scenario_expired_lease()["passed"])

    def test_partially_written_artifact(self):
        self.assertTrue(mrm.scenario_partially_written_artifact()["passed"])

    def test_interrupted_replay(self):
        r = mrm.scenario_interrupted_replay()
        self.assertTrue(r["passed"])
        self.assertEqual(r["status"], "replay_requested")

    def test_interrupted_acceptance(self):
        r = mrm.scenario_interrupted_acceptance()
        self.assertTrue(r["passed"])
        self.assertTrue(r["resumable_marker"])

    def test_model_provider_timeout_no_real_call(self):
        r = mrm.scenario_model_provider_timeout()
        self.assertTrue(r["no_real_call"])
        self.assertTrue(r["passed"])

    def test_explorer_process_kill(self):
        self.assertTrue(mrm.scenario_explorer_process_kill()["passed"])

    def test_docker_scenarios_are_gated(self):
        os.environ.pop("M4_DOCKER_RECOVERY", None)
        for fn in (mrm.scenario_service_restart, mrm.scenario_queue_consumer_restart,
                   mrm.scenario_database_restart):
            r = fn()
            self.assertIsNone(r["passed"])
            self.assertEqual(r["blocked"], "environment_unavailable")

    def test_run_all_reports_blocked(self):
        os.environ.pop("M4_DOCKER_RECOVERY", None)
        report = mrm.run_all()
        self.assertEqual(len(report["scenarios_blocked"]), 3)
        self.assertTrue(report["all_passed"])


if __name__ == "__main__":
    unittest.main()
