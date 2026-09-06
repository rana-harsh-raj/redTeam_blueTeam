#!/usr/bin/env python3
"""M4 lease unit tests (T08 §3.5). No network, no docker."""
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "RED_LOOP"))
from red_loop.state import CampaignStore  # noqa: E402
from red_loop.leases import LeaseManager, LeaseError  # noqa: E402


def _store(name="lease"):
    d = tempfile.mkdtemp(prefix="lease-")
    return CampaignStore(campaign_id=name, root=Path(d) / name)


class TestLeases(unittest.TestCase):
    def _mgr(self, t0=1000.0):
        store = _store()
        clock = [t0]
        mgr = LeaseManager(store, clock=lambda: clock[0])
        return store, mgr, clock

    def test_acquire_release(self):
        store, mgr, clock = self._mgr()
        lease = mgr.acquire("H-001", "owner-A", ttl=100)
        self.assertEqual(mgr.owner_of("H-001"), "owner-A")
        mgr.release(lease["lease_id"])
        self.assertIsNone(mgr.owner_of("H-001"))

    def test_expired_lease_reassignable(self):
        store, mgr, clock = self._mgr()
        mgr.acquire("H-001", "owner-A", ttl=50)
        clock[0] += 60
        freed = mgr.reap_expired()
        self.assertEqual(len(freed), 1)
        events = [e for e in store._read_all("events") if e.get("event") == "lease_expired"]
        self.assertEqual(len(events), 1)
        # now reassignable
        mgr.reassign("H-001", "owner-B", from_owner="owner-A")
        self.assertEqual(mgr.owner_of("H-001"), "owner-B")

    def test_heartbeat_extends(self):
        store, mgr, clock = self._mgr()
        lease = mgr.acquire("H-001", "owner-A", ttl=50)
        first_expiry = lease["expires_epoch"]
        clock[0] += 40
        updated = mgr.heartbeat(lease["lease_id"], ttl=50)
        self.assertGreater(updated["expires_epoch"], first_expiry)
        # not expired after original TTL because heartbeat pushed it
        clock[0] += 20
        self.assertEqual(mgr.owner_of("H-001"), "owner-A")

    def test_double_acquire_blocked(self):
        store, mgr, clock = self._mgr()
        mgr.acquire("H-001", "owner-A", ttl=100)
        with self.assertRaises(LeaseError):
            mgr.acquire("H-001", "owner-B", ttl=100)

    def test_reacquire_by_same_owner_is_heartbeat(self):
        store, mgr, clock = self._mgr()
        mgr.acquire("H-001", "owner-A", ttl=50)
        clock[0] += 10
        again = mgr.acquire("H-001", "owner-A", ttl=50)
        self.assertEqual(again["owner"], "owner-A")

    def test_recover_on_resume(self):
        store, mgr, clock = self._mgr()
        mgr.acquire("H-001", "owner-A", ttl=30)
        clock[0] += 100  # process was dead a while
        freed = mgr.recover_on_resume()
        self.assertEqual(len(freed), 1)


if __name__ == "__main__":
    unittest.main()
