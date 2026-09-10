"""Virtual-clock checks for the observed callback/worker mutex retry window."""
from pathlib import Path
import sys
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpers import payouts_flow as pf, wait


class ProcessedJournalWait(unittest.TestCase):
    def run_clock(self, ready_at):
        now = [0.0]
        def sleep(seconds):
            now[0] += seconds
        def journal(*_):
            return {"id": "observed_journal"} if now[0] >= ready_at else None
        with patch.dict("os.environ", {"ARENA_WAIT_SCALE": "1"}), \
             patch.object(wait.time, "monotonic", side_effect=lambda: now[0]), \
             patch.object(wait.time, "sleep", side_effect=sleep), \
             patch.object(pf, "get_ledger_journal_row", side_effect=journal):
            return pf.wait_for_processed_journal(None, "pout_OBSERVED")

    def test_journal_after_first_worker_retry_is_observed(self):
        self.assertEqual(self.run_clock(31)["id"], "observed_journal")

    def test_missing_journal_still_fails_at_explicit_bound(self):
        with self.assertRaisesRegex(wait.WaitTimeout, "after 50.0s"):
            self.run_clock(float("inf"))

    def test_wait_policy_matches_arena_worker_retry_config(self):
        template = Path(__file__).resolve().parents[2] / "config/templates/base/payouts/arena.toml"
        # Other template sections contain unresolved placeholders; the worker
        # section is literal source-supported runtime configuration.
        section = template.read_text().split("[worker]", 1)[1].split("\n[", 1)[0]
        config = tomllib.loads("[worker]" + section)
        self.assertEqual(config["worker"]["retrydelay"], f"{pf.PROCESSED_JOURNAL_RETRY_DELAY_SECONDS}s")


if __name__ == "__main__":
    unittest.main()
