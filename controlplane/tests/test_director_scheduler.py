import tempfile, unittest
from pathlib import Path
from controlplane.tests import _util  # noqa: F401
from controlplane.store import ControlStore
from controlplane.manifest import build_manifest
from controlplane.models import ModelRouter
from controlplane.director import Director
from controlplane.cells import CellManager
from controlplane.scheduler import Scheduler
from controlplane.fakes import FakeWorld


def _mani():
    return build_manifest("broad mandate", "snap",
        [{"instance_id": "alpha", "seed": "s1", "starting_access_profile": "merchant_ordinary"},
         {"instance_id": "beta", "seed": "s2", "starting_access_profile": "merchant_fresh"}],
        ["claude-opus-4-8", "gpt-5.5"], budgets={"planning_cycles_min": 2})


class TestDirectorScheduler(unittest.TestCase):
    def setUp(self):
        self.mani = _mani()
        self.c = ControlStore(self.mani["campaign_id"], root=Path(tempfile.mkdtemp()) / self.mani["campaign_id"])
        self.c.write_manifest(self.mani)
        self.router = ModelRouter(self.mani["model_pool"], store=self.c)
        self.world = FakeWorld()
        self.director = Director(self.c, self.world, self.router)
        self.cells = CellManager(self.c)
        self.sched = Scheduler(self.c, self.cells)

    def test_director_generates_theses(self):
        ids = self.director.plan_cycle(self.mani, max_theses=4)
        self.assertGreaterEqual(len(ids), 3)
        self.assertEqual(self.c.control_state()["planning_cycles"], 1)

    def test_thesis_dedup_by_fingerprint(self):
        # plan over ALL subjects, then again: every subject is covered and every
        # re-proposal collides on fingerprint -> no new theses.
        self.director.plan_cycle(self.mani, max_theses=99)
        before = len(self.c.fold("theses"))
        # force a duplicate proposal of an existing thesis -> suppressed by fingerprint
        existing = self.c.fold("theses")[0]
        tid = self.director._propose_thesis(
            {"subject": existing["subject"], "claim": existing["claim"],
             "subject_kind": existing.get("subject_kind")}, cycle=9)
        self.assertIsNone(tid)
        self.director.plan_cycle(self.mani, max_theses=99)  # all covered -> none new
        self.assertEqual(len(self.c.fold("theses")), before)
        self.assertTrue(any(e.get("event") == "thesis_duplicate_suppressed"
                            for e in self.c._read_all("events")))

    def test_scheduler_spreads_across_twins_and_replicates(self):
        self.director.plan_cycle(self.mani, max_theses=5)
        self.sched.schedule_theses(self.mani, max_new=20)
        tasks = self.c.fold("tasks")
        twins = {t["twin"] for t in tasks}
        self.assertEqual(twins, {"alpha", "beta"})
        self.assertTrue(any(int(t.get("replica_index", 0)) > 0 for t in tasks))
        # cells created
        self.assertTrue(self.cells.active())

    def test_no_duplicate_active_task(self):
        self.director.plan_cycle(self.mani, max_theses=3)
        self.sched.schedule_theses(self.mani, max_new=20)
        n1 = len(self.c.fold("tasks"))
        self.sched.schedule_theses(self.mani, max_new=20)  # theses now 'scheduled' -> no new
        self.assertEqual(len(self.c.fold("tasks")), n1)

    def test_requeue_then_block_after_max_attempts(self):
        self.director.plan_cycle(self.mani, max_theses=1)
        self.sched.schedule_theses(self.mani, max_new=1)
        tid = self.c.fold("tasks")[0]["task_id"]
        self.assertTrue(self.sched.requeue(tid, "x"))
        self.assertTrue(self.sched.requeue(tid, "x"))
        self.assertFalse(self.sched.requeue(tid, "x"))  # 3rd -> blocked
        self.assertEqual(self.c.get("tasks", tid)["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
