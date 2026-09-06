"""Offline tests: pricing collisions and validation must precede seeding."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from red_loop import pricing_ids as ids, provisioner


class PricingIdsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.seed = self.root / "seeds" / "generated"
        self.seed.mkdir(parents=True)
        (self.seed / "pricing.json").write_text('{"plans": {}}')

    def reserve(self, run, mid):
        return ids.reserve_plan_id(self.seed, run, mid)

    def test_stable_persistent_reservation(self):
        pid = self.reserve("run-one", "merchant-one")
        self.assertEqual(pid, ids.bounded_plan_id("merchant-one"))
        self.assertEqual(pid, self.reserve("run-one", "merchant-one"))
        self.assertNotEqual(pid, self.reserve("run-two", "merchant-two"))
        doc = json.loads((self.seed / "pricing-id-reservations.json").read_text())
        self.assertEqual(len(doc), 2)

    def test_deliberate_collision_fails_without_overwriting(self):
        with patch.object(ids, "bounded_plan_id", return_value="ARENAPLAN00001"):
            self.reserve("run-one", "merchant-one")
            before = (self.seed / "pricing-id-reservations.json").read_bytes()
            for run in ("run-one", "run-two"):
                with self.assertRaisesRegex(ValueError, "collision"):
                    self.reserve(run, "merchant-two")
            self.assertEqual(before, (self.seed / "pricing-id-reservations.json").read_bytes())

    def test_existing_seed_collision_before_database_or_secret_access(self):
        mid = provisioner._ids_for("test-run", "attacker")["merchant_id"]
        (self.seed / "pricing.json").write_text(json.dumps({"plans": {
            "other-merchant": {"plan_id": ids.bounded_plan_id(mid), "rules": {}}}}))
        with patch.object(provisioner.config, "ENV2", self.root), \
                patch.object(provisioner, "_mysql") as mysql, \
                patch.object(provisioner, "_pw") as secret:
            with self.assertRaisesRegex(ValueError, "collision"):
                provisioner.provision_funded_merchant("test-run")
            mysql.assert_not_called()
            secret.assert_not_called()

    def test_column_boundaries(self):
        self.assertEqual(ids.validate_pricing_id("A" * 14), "A" * 14)
        for invalid in ("A" * 15, "", None, "a'b", "é"):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                ids.validate_pricing_id(invalid)

    def test_real_truncated_hash_collision(self):
        self.assertEqual(ids.bounded_plan_id("ARENAM90001408"),
                         ids.bounded_plan_id("ARENAM90001571"))
        self.reserve("same-run", "ARENAM90001408")
        with self.assertRaisesRegex(ValueError, "collision"):
            self.reserve("same-run", "ARENAM90001571")

    def test_rule_identifier_validation_before_reservation(self):
        (self.seed / "pricing.json").write_text(json.dumps({"plans": {
            "existing": {"plan_id": "PLAN1", "rules": {"IMPS": {"id": "R" * 15}}}}}))
        with self.assertRaises(ValueError):
            self.reserve("run-one", "merchant-one")
        self.assertFalse((self.seed / "pricing-id-reservations.json").exists())

    def test_corrupt_registry_fails_closed(self):
        (self.seed / "pricing-id-reservations.json").write_text("broken")
        with self.assertRaises(ValueError):
            self.reserve("run-one", "merchant-one")

    def test_direct_seed_helpers_reject_overlong_ids(self):
        with self.assertRaises(ValueError):
            provisioner._register_pricing("merchant", "P" * 15, "balance")
        with self.assertRaises(ValueError):
            provisioner._register_monolith_merchant("merchant", {}, "P" * 15)


if __name__ == "__main__":
    unittest.main()
