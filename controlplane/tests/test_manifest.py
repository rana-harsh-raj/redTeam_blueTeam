import unittest
from controlplane.tests import _util  # noqa: F401
from controlplane import manifest as M


class TestManifest(unittest.TestCase):
    def _mani(self, **kw):
        base = dict(mandate="broad mandate, no target",
                    snapshot_id="snap", model_pool=["claude-opus-4-8", "gpt-5.5"],
                    twins=[{"instance_id": "a", "seed": "s1"}, {"instance_id": "b", "seed": "s2"}])
        base.update(kw)
        return M.build_manifest(**base)

    def test_content_addressed_and_stable(self):
        a = self._mani(); b = self._mani(created_at=a["created_at"])
        self.assertEqual(a["campaign_id"], b["campaign_id"])
        self.assertTrue(a["campaign_id"].startswith("camp-"))

    def test_tamper_detected(self):
        m = self._mani()
        ok, _ = M.validate(m); self.assertTrue(ok)
        m["mandate"] = "assign a specific target"
        ok, _ = M.validate(m); self.assertFalse(ok)

    def test_human_gives_no_target(self):
        m = self._mani()
        blob = " ".join(str(k) for k in m).lower()
        self.assertNotIn("target", blob)
        self.assertNotIn("vulnerability_class", m)
        self.assertIn("mandate", m)
        self.assertIn("mode", m)

    def test_requires_twins_and_models(self):
        with self.assertRaises(ValueError):
            M.build_manifest("m", "snap", [], ["x"])
        with self.assertRaises(ValueError):
            M.build_manifest("m", "snap", [{"instance_id": "a"}], [])

    def test_pins_snapshot_profiles_seeds_and_pool(self):
        m = self._mani()
        self.assertEqual(m["architecture_snapshot_id"], "snap")
        self.assertEqual([t["seed"] for t in m["twins"]], ["s1", "s2"])
        self.assertEqual(m["model_pool"], ["claude-opus-4-8", "gpt-5.5"])


if __name__ == "__main__":
    unittest.main()
