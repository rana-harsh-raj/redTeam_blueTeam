#!/usr/bin/env python3
"""M4 soak coordinator tests (T08 §3.5). No network, no docker."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "RED_LOOP"))
from red_loop.state import CampaignStore  # noqa: E402
from red_loop.soak import Soak, StopReason  # noqa: E402
from red_loop.hypotheses import HypothesisManager  # noqa: E402
from red_loop.leases import LeaseManager  # noqa: E402


def _store(name="soak"):
    d = tempfile.mkdtemp(prefix="soak-")
    return CampaignStore(campaign_id=name, root=Path(d) / name)


class TestSoak(unittest.TestCase):
    def test_checkpoint_each_cycle(self):
        store = _store()
        mgr = HypothesisManager(store)
        counter = [0]

        def runner(policy, cycle_idx, ctx):
            counter[0] += 1
            mgr.propose("claim %d" % counter[0], target_assets=[{"asset": "a%d" % counter[0]}])
            return {"produced_work": True, "new_hypotheses": 1, "experiments_executed": 1}

        soak = Soak(store, cycle_runner=runner)
        summary = soak.run(max_cycles=3, checkpoint_every=1)
        self.assertEqual(summary["checkpoints"], 3)
        self.assertEqual(summary["stop_reason"], StopReason.CYCLE_CAP)

    def test_duplicate_suppressed(self):
        store = _store()

        def runner(policy, cycle_idx, ctx):
            return {"produced_work": True, "duplicates_suppressed": 2, "experiments_executed": 1}

        soak = Soak(store, cycle_runner=runner)
        summary = soak.run(max_cycles=2)
        self.assertEqual(summary["duplicates_suppressed"], 4)

    def test_expired_task_reassigned(self):
        store = _store()
        mgr = HypothesisManager(store)
        hid, _ = mgr.propose("stuck task", target_assets=[{"asset": "payouts"}])
        clock = [1000.0]
        soak = Soak(store, cycle_runner=lambda p, i, c: {"produced_work": True,
                                                         "experiments_executed": 1},
                    clock=lambda: clock[0])
        # acquire a lease that will already be expired at soak time
        soak.leases.acquire(hid, "worker-A", ttl=10)
        clock[0] += 100
        summary = soak.run(max_cycles=1, lease_ttl=60)
        self.assertGreaterEqual(summary["leases_reassigned"], 1)
        self.assertEqual(soak.leases.owner_of(hid), "soak:reassigned")

    def test_machine_readable_summary_shape(self):
        store = _store()

        def runner(policy, cycle_idx, ctx):
            return {"produced_work": True, "experiments_executed": 1}

        soak = Soak(store, cycle_runner=runner)
        summary = soak.run(max_cycles=2)
        for key in ("start", "end", "contexts", "turns", "hypotheses_by_status",
                    "experiments_executed", "leases_expired", "leases_reassigned",
                    "checkpoints", "stop_reason", "cycles"):
            self.assertIn(key, summary)
        out = store.root / "m4-direct-e2e-soak.json"
        self.assertTrue(out.exists())
        self.assertEqual(json.loads(out.read_text())["stop_reason"], summary["stop_reason"])

    def test_no_productive_work_stops_early(self):
        store = _store()
        # runner never produces work and no hypotheses -> must stop, not idle-loop
        soak = Soak(store, cycle_runner=lambda p, i, c: {"produced_work": False})
        summary = soak.run(max_cycles=1000)
        self.assertEqual(summary["stop_reason"], StopReason.NO_PRODUCTIVE_WORK)
        self.assertLess(len(summary["cycles"]), 5)

    def test_scenario_rotation(self):
        store = _store()
        seen = []
        soak = Soak(store, cycle_runner=lambda p, i, c: (seen.append(c["scenario"])
                                                         or {"produced_work": True,
                                                             "experiments_executed": 1}))
        soak.run(max_cycles=4)
        self.assertGreaterEqual(len(set(seen)), 2)


if __name__ == "__main__":
    unittest.main()
