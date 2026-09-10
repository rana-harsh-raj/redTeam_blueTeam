import tempfile, unittest, threading
from pathlib import Path
from controlplane.tests import _util  # noqa: F401
from controlplane.store import ControlStore


class TestStore(unittest.TestCase):
    def _s(self):
        return ControlStore("camp-t", root=Path(tempfile.mkdtemp()) / "camp-t")

    def test_append_fold_lww(self):
        s = self._s()
        s.put("tasks", {"task_id": "T1", "status": "ready"})
        s.update("tasks", "T1", status="done")
        self.assertEqual(s.get("tasks", "T1")["status"], "done")
        self.assertEqual(len(s.fold("tasks")), 1)

    def test_manifest_immutable(self):
        s = self._s()
        s.write_manifest({"campaign_id": "camp-t", "x": 1})
        with self.assertRaises(RuntimeError):
            s.write_manifest({"campaign_id": "camp-t", "x": 2})

    def test_state_hash_changes_on_decision(self):
        s = self._s()
        h0 = s.state_hash()
        s.put("theses", {"thesis_id": "TH1"})
        self.assertNotEqual(h0, s.state_hash())

    def test_concurrent_appends_have_unique_seqs(self):
        s = self._s()
        def w(n):
            for i in range(100):
                s.event("ping", w=n, i=i, blob="x" * 2000)
        ts = [threading.Thread(target=w, args=(n,)) for n in range(5)]
        [t.start() for t in ts]; [t.join() for t in ts]
        rows = s._read_all("events")
        self.assertEqual(len(rows), 500)
        self.assertEqual(len({r["seq"] for r in rows}), 500)

    def test_checkpoint_and_resume(self):
        s = self._s()
        s.put("theses", {"thesis_id": "TH1"})
        ck = s.checkpoint("test")
        self.assertEqual(ck["state_hash"], s.state_hash())
        self.assertIsNotNone(s.latest_checkpoint())


if __name__ == "__main__":
    unittest.main()
