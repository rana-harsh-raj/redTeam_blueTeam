import tempfile, unittest
from pathlib import Path
from controlplane.tests import _util  # noqa: F401
from controlplane import proof
from controlplane.views import Views


class TestEngineRecovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = Path(tempfile.mkdtemp(prefix="cp-engine-"))
        cls.r = proof.run_full_proof(cls.home)
        cls.c = cls.r["control"]
        cls.ev = [e.get("event") for e in cls.c._read_all("events")]

    def has(self, e):
        return e in self.ev

    def test_completes_without_human_task_assignment(self):
        self.assertEqual(self.r["stop_reason"], "no_open_work")
        # no event assigns a task by a human; tasks all come from scheduler
        self.assertFalse(any(e.get("event") == "human_task_assignment" for e in self.c._read_all("events")))

    def test_two_twins_participated(self):
        twins = {t["twin"] for t in self.c.fold("tasks")}
        self.assertIn("alpha", twins)
        self.assertTrue({"beta", "beta-r"} & twins)

    def test_multiple_planning_cycles(self):
        self.assertGreaterEqual(self.c.control_state()["planning_cycles"], 2)

    def test_worker_failure_recovered(self):
        self.assertTrue(self.has("injected_worker_kill"))
        self.assertTrue(self.has("lease_expired"))
        self.assertTrue(self.has("task_recovered"))
        t1 = self.c.get("tasks", "TASK-001")
        self.assertEqual(t1["status"], "done")

    def test_director_restart_resumed(self):
        self.assertTrue(self.has("campaign_resumed"))
        self.assertNotEqual(self.r["hash_at_pause"], self.r["final_hash"])

    def test_model_fallback(self):
        self.assertTrue(self.has("model_fallback"))

    def test_twin_reproduced_and_rehomed(self):
        self.assertTrue(self.has("twin_recovered"))
        self.assertTrue(any(x.get("kind") == "reproduce_ok" for x in self.c._read_all("recoveries")))

    def test_cells_reallocated_and_retired(self):
        self.assertTrue(self.has("cell_reallocated"))
        self.assertTrue(self.has("cell_retired"))

    def test_dedup_and_replication(self):
        self.assertGreater(sum(1 for e in self.c._read_all("events")
                               if e.get("event") == "duplicate_suppressed"), 0)
        self.assertTrue(any(int(t.get("replica_index", 0)) > 0 for t in self.c.fold("tasks")))

    def test_all_model_and_tool_activity_recorded_and_tagged(self):
        mc = self.c._read_all("model_calls")
        tcalls = self.c._read_all("tool_calls")
        self.assertGreater(len(mc), 0)
        self.assertGreater(len(tcalls), 0)
        for t in tcalls:
            self.assertIn("task_id", t)
            self.assertIn("twin", t)

    def test_decorrelated_non_self_verification(self):
        v = Views(self.c, self.r["manifest"]).results()
        self.assertEqual(v["self_verifications"], 0)
        self.assertGreater(v["decorrelated_verifications"], 0)

    def test_kill_switch(self):
        ks = proof.run_killswitch_proof(self.home)
        self.assertEqual(ks["stop_reason"], "kill_switch")

    def test_budget_enforced(self):
        bg = proof.run_budget_proof(self.home)
        self.assertEqual(bg["stop_reason"], "action_budget_exhausted")


if __name__ == "__main__":
    unittest.main()
