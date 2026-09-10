"""Cooperative task leases over the durable campaign store (M4).

A lease binds a hypothesis to a worker/context ``owner`` for a bounded TTL.
Leases are append-only in ``leases.jsonl`` (folded last-writer-wins by
``lease_id``) so they survive a process kill and are recovered on resume. An
expired lease frees its hypothesis for reassignment and emits a ``lease_expired``
event; reassignment writes a structured handoff record.

No network, no credentials.
"""
import time
import uuid


def _utc(ts=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class LeaseError(RuntimeError):
    pass


class LeaseManager:
    KIND = "leases"
    HANDOFF_KIND = "handoffs"

    def __init__(self, store, clock=time.time):
        self.store = store
        self.store.ensure_kind(self.KIND)
        self.store.ensure_kind(self.HANDOFF_KIND)
        self._clock = clock

    # -- acquire / heartbeat / release -------------------------------------
    def acquire(self, hypothesis_id, owner, ttl=300):
        now = self._clock()
        live = self._active_for_hypothesis(hypothesis_id, now)
        if live and live.get("owner") != owner:
            raise LeaseError("hypothesis %s already leased by %s (until %s)"
                             % (hypothesis_id, live.get("owner"), live.get("expires_at")))
        if live and live.get("owner") == owner:
            # idempotent re-acquire by same owner == heartbeat
            return self.heartbeat(live["lease_id"], ttl=ttl)
        lease_id = "L-" + uuid.uuid4().hex[:10]
        rec = {
            "lease_id": lease_id,
            "hypothesis_id": hypothesis_id,
            "owner": owner,
            "acquired_at": _utc(now),
            "acquired_epoch": now,
            "expires_epoch": now + ttl,
            "expires_at": _utc(now + ttl),
            "heartbeat_epoch": now,
            "heartbeat_at": _utc(now),
            "released_at": None,
            "expired": False,
        }
        self.store._append(self.KIND, rec)
        self.store.event("lease_acquired", lease_id=lease_id,
                         hypothesis_id=hypothesis_id, owner=owner, ttl=ttl)
        return rec

    def heartbeat(self, lease_id, ttl=300):
        cur = self._by_id().get(lease_id)
        if cur is None:
            raise LeaseError("unknown lease %s" % lease_id)
        if cur.get("released_at") or cur.get("expired"):
            raise LeaseError("lease %s is not live" % lease_id)
        now = self._clock()
        upd = {"lease_id": lease_id, "_update": True,
               "heartbeat_epoch": now, "heartbeat_at": _utc(now),
               "expires_epoch": now + ttl, "expires_at": _utc(now + ttl)}
        self.store._append(self.KIND, upd)
        merged = dict(cur)
        merged.update(upd)
        return merged

    def release(self, lease_id, reason="completed"):
        cur = self._by_id().get(lease_id)
        if cur is None:
            raise LeaseError("unknown lease %s" % lease_id)
        now = self._clock()
        self.store._append(self.KIND, {"lease_id": lease_id, "_update": True,
                                       "released_at": _utc(now), "release_reason": reason})
        self.store.event("lease_released", lease_id=lease_id, reason=reason)

    # -- expiry / reassignment ---------------------------------------------
    def reap_expired(self, now=None):
        """Mark every live-but-expired lease expired; return the freed lease
        records. Emits a ``lease_expired`` event per lease."""
        now = self._clock() if now is None else now
        freed = []
        for lease in self._by_id().values():
            if lease.get("released_at") or lease.get("expired"):
                continue
            if lease.get("expires_epoch", 0) <= now:
                self.store._append(self.KIND, {"lease_id": lease["lease_id"],
                                               "_update": True, "expired": True,
                                               "expired_at": _utc(now)})
                self.store.event("lease_expired", lease_id=lease["lease_id"],
                                 hypothesis_id=lease.get("hypothesis_id"),
                                 owner=lease.get("owner"))
                merged = dict(lease)
                merged["expired"] = True
                freed.append(merged)
        return freed

    def reassign(self, hypothesis_id, new_owner, ttl=300, from_owner=None,
                 reason="expired_lease", next_best_action=None, state_at_handoff=None):
        """Record a handoff and acquire a fresh lease for ``new_owner``. Any live
        lease on the hypothesis must already be expired/released (call
        reap_expired first)."""
        now = self._clock()
        handoff_id = "HO-" + uuid.uuid4().hex[:8]
        self.store._append(self.HANDOFF_KIND, {
            "handoff_id": handoff_id, "hypothesis_id": hypothesis_id,
            "from_owner": from_owner, "to_owner": new_owner, "reason": reason,
            "state_at_handoff": state_at_handoff, "next_best_action": next_best_action,
            "ts": _utc(now)})
        self.store.event("task_reassigned", hypothesis_id=hypothesis_id,
                         from_owner=from_owner, to_owner=new_owner, reason=reason)
        lease = self.acquire(hypothesis_id, new_owner, ttl=ttl)
        return handoff_id, lease

    def recover_on_resume(self, now=None):
        """Restart-safe recovery: reap any lease whose TTL elapsed while the
        process was dead. Returns the freed leases (candidates for reassignment).
        """
        freed = self.reap_expired(now=now)
        if freed:
            self.store.event("leases_recovered_on_resume", count=len(freed),
                             hypothesis_ids=[l.get("hypothesis_id") for l in freed])
        return freed

    # -- reads --------------------------------------------------------------
    def active_leases(self, now=None):
        now = self._clock() if now is None else now
        out = []
        for lease in self._by_id().values():
            if lease.get("released_at") or lease.get("expired"):
                continue
            if lease.get("expires_epoch", 0) > now:
                out.append(lease)
        return out

    def owner_of(self, hypothesis_id, now=None):
        live = self._active_for_hypothesis(hypothesis_id, self._clock() if now is None else now)
        return live.get("owner") if live else None

    def handoffs(self):
        return self.store._read_all(self.HANDOFF_KIND)

    def summary(self):
        expired = reassigned = 0
        for lease in self._by_id().values():
            if lease.get("expired"):
                expired += 1
        reassigned = len(self.handoffs())
        return {"expired": expired, "reassigned": reassigned,
                "active": len(self.active_leases())}

    # -- internals ----------------------------------------------------------
    def _by_id(self):
        merged = {}
        for rec in self.store._read_all(self.KIND):
            lid = rec.get("lease_id")
            if lid is None:
                continue
            merged.setdefault(lid, {}).update({k: v for k, v in rec.items() if k != "_update"})
        return merged

    def _active_for_hypothesis(self, hypothesis_id, now):
        for lease in self._by_id().values():
            if lease.get("hypothesis_id") != hypothesis_id:
                continue
            if lease.get("released_at") or lease.get("expired"):
                continue
            if lease.get("expires_epoch", 0) > now:
                return lease
        return None
