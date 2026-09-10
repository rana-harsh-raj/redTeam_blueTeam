import tempfile, unittest
from pathlib import Path
from controlplane.tests import _util  # noqa: F401
from controlplane import proof
from controlplane.views import Views


class TestViews(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = Path(tempfile.mkdtemp(prefix="cp-views-"))
        cls.r = proof.run_full_proof(cls.home)
        cls.v = Views(cls.r["control"], cls.r["manifest"])

    def test_views_render(self):
        for name in ("narrative", "coverage", "allocation", "cost", "results"):
            out = getattr(self.v, name)()
            self.assertTrue(out is not None)

    def test_coverage_has_subjects_and_cycles(self):
        cov = self.v.coverage()
        self.assertGreater(cov["distinct_subjects"], 0)
        self.assertGreaterEqual(cov["planning_cycles"], 2)

    def test_allocation_both_twins(self):
        alloc = self.v.allocation()
        self.assertGreaterEqual(len(alloc["twins"]), 2)

    def test_cost_reports_tokens(self):
        cost = self.v.cost()
        self.assertIn("prompt_tokens", cost)
        self.assertIn("estimated_usd", cost)


if __name__ == "__main__":
    unittest.main()
