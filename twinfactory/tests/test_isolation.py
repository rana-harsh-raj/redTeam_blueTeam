"""Isolation-proof tests. Offline part: the recorded proof (reports/implementation/m9-isolation-proof.json) must be
internally consistent (every phase present and passed, distinct daemons/ports/subnets/secrets, attributed journeys,
reproducible image ids). Live part (TWIN_LIVE=1 with two RUNNING instances A/B named in TWIN_LIVE_A/TWIN_LIVE_B): the
cheap read-only phases P1-P4 are re-executed against the live boundaries."""
import json
import os
import unittest
from pathlib import Path

from twinfactory import paths

PROOF = paths.IMPL / "m9-isolation-proof.json"


@unittest.skipUnless(PROOF.is_file(), "no recorded isolation proof yet")
class TestRecordedProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(PROOF.read_text())

    def test_all_phases_passed(self):
        self.assertTrue(self.doc.get("passed"))
        self.assertEqual(sorted(self.doc["phases"]), ["P%d" % i for i in range(1, 10)])
        for pid, ph in self.doc["phases"].items():
            self.assertTrue(ph["passed"], pid)

    def test_distinct_boundaries(self):
        d = self.doc["phases"]["P1"]["detail"]
        self.assertNotEqual(d["a"]["identity"]["daemon_id"], d["b"]["identity"]["daemon_id"])
        self.assertNotEqual(d["a"]["identity"]["docker_host"], d["b"]["identity"]["docker_host"])
        self.assertTrue(d["a"]["boundary"] and d["b"]["boundary"])
        self.assertNotEqual(d["a"]["profile"], d["b"]["profile"])

    def test_namespaces_disjoint(self):
        d = self.doc["phases"]["P2"]["detail"]
        self.assertEqual(d["shared_names"], [])
        self.assertEqual(d["b_resources_visible_on_a"], [])
        self.assertEqual(d["a_resources_visible_on_b"], [])
        self.assertNotEqual(d["ports"]["a"], d["ports"]["b"])
        self.assertNotEqual(d["subnets"]["a"], d["subnets"]["b"])
        self.assertNotEqual(d["secrets_manifest_digest"]["a"], d["secrets_manifest_digest"]["b"])
        self.assertEqual(d["inputs_hash"]["a"], d["inputs_hash"]["b"])

    def test_filesystem_and_network(self):
        p3 = self.doc["phases"]["P3"]["detail"]
        self.assertTrue(all(v["visible"] is False for v in p3.values()))
        p4 = self.doc["phases"]["P4"]["detail"]
        for k, v in p4.items():
            if k == "host_bridge_pids":
                self.assertNotEqual(v[self.doc["a"]], v[self.doc["b"]])
                continue
            self.assertEqual(v["own_payouts_api"]["http"], "200")
            self.assertNotEqual(v["other_container_by_name"]["http"], "200")
            self.assertNotEqual(v["other_host_port_from_vm"]["http"], "200")

    def test_journeys_attributed(self):
        d = self.doc["phases"]["P5"]["detail"]
        for side in ("a", "b"):
            self.assertTrue(d[side]["attributed"])
            self.assertGreaterEqual(d[side]["summary"]["pass"], 1)
            self.assertEqual(d[side]["summary"]["fail"], 0)
        self.assertTrue(d["distinct_merchants"])

    def test_reset_and_reproduce(self):
        p7 = self.doc["phases"]["P7"]["detail"]
        self.assertEqual(p7["a_rows_before"], p7["a_rows_after"])
        self.assertEqual(p7["a_rendered_before"], p7["a_rendered_after"])
        p9 = self.doc["phases"]["P9"]["detail"]["reproducible"]
        self.assertTrue(p9["inputs_hash"] and p9["profile_digest"] and p9["seed_epoch"])
        self.assertTrue(all(p9["image_ids"].values()))


@unittest.skipUnless(os.environ.get("TWIN_LIVE") == "1", "live isolation checks need TWIN_LIVE=1 and two running instances")
class TestLiveBoundaries(unittest.TestCase):
    def test_live_p1_to_p4(self):
        from twinfactory.factory import Factory
        from twinfactory.isolation import Proof
        import tempfile
        f = Factory()
        out = Path(tempfile.mkdtemp()) / "proof.json"
        p = Proof(f, os.environ["TWIN_LIVE_A"], os.environ["TWIN_LIVE_B"], out, False, None, None, None)
        self.assertTrue(p.p1() and p.p2() and p.p3() and p.p4())


if __name__ == "__main__":
    unittest.main()
