"""Durable, idempotent, retrying callback delivery to the payout side.

When a workflow reaches a terminal decision the engine must notify the payout
service (approve -> .../approve, reject/cancel/expire -> .../reject). Faithful
to the real payouts contract:
  * HTTP Basic (rzp_live + shared secret), headers x-creator-id /
    X-Razorpay-Account;
  * success = HTTP 200, 201 or 409 (409 = payout already transitioned, which is
    idempotent-safe);
  * each delivery carries a stable Idempotency-Key (delivery_id) so a retry or a
    post-restart resend is de-duplicated by the receiver.

Deliveries are persisted (callbacks table, UNIQUE(workflow,kind)) before the
first attempt, so an interrupted engine resumes undelivered callbacks on
startup — matching the real engine's at-least-once durability.
"""
import base64
import json
import threading
import time
import urllib.error
import urllib.request

from .identity import new_id

SUCCESS_CODES = (200, 201, 409)


class Callbacks:
    def __init__(self, store, *, default_sink=None, callback_user="rzp_live",
                 callback_pass="workflow-secret", max_attempts=5,
                 backoff=0.5, deliver=True):
        self.store = store
        self.default_sink = default_sink
        self.user = callback_user
        self.password = callback_pass
        self.max_attempts = max_attempts
        self.backoff = backoff
        self.deliver = deliver
        self._q = []
        self._cv = threading.Condition()
        self._stop = False
        self._worker = None

    def start(self):
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()
        self.resume_pending()

    def stop(self):
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2.0)

    # --- public ------------------------------------------------------------
    def enqueue(self, wf, kind, reason=None):
        """Record (idempotently) a pending delivery and wake the worker."""
        delivery_id = f"cbk_{wf['id']}_{kind}"
        now = time.time()
        self.store.exec(
            "INSERT INTO callbacks(delivery_id,workflow_id,kind,attempts,delivered,"
            "created_at,updated_at) VALUES(?,?,?,0,0,?,?)"
            " ON CONFLICT(workflow_id,kind) DO NOTHING",
            (delivery_id, wf["id"], kind, now, now))
        with self._cv:
            self._q.append((wf["id"], kind, delivery_id))
            self._cv.notify_all()

    def resume_pending(self):
        """On startup, re-queue any callback that was recorded but not delivered."""
        rows = self.store.all(
            "SELECT c.delivery_id,c.workflow_id,c.kind FROM callbacks c"
            " WHERE c.delivered=0")
        with self._cv:
            for r in rows:
                self._q.append((r["workflow_id"], r["kind"], r["delivery_id"]))
            self._cv.notify_all()

    # --- worker ------------------------------------------------------------
    def _run(self):
        while True:
            with self._cv:
                while not self._q and not self._stop:
                    self._cv.wait(timeout=1.0)
                if self._stop and not self._q:
                    return
                item = self._q.pop(0) if self._q else None
            if item:
                self._deliver(*item)

    def _target(self, wf, kind):
        cd = {}
        try:
            cd = json.loads(wf["callback_details"]) if wf.get("callback_details") else {}
        except Exception:
            cd = {}
        node = cd.get(kind) or {}
        url = node.get("url")
        if not url and self.default_sink:
            url = f"{self.default_sink.rstrip('/')}/callback/{kind}/{wf['entity_id']}"
        return url, node

    def _deliver(self, wf_id, kind, delivery_id):
        wf = self.store.one("SELECT * FROM workflows WHERE id=?", (wf_id,))
        if not wf:
            return
        wf = dict(wf)
        url, node = self._target(wf, kind)
        if not url or not self.deliver:
            # No sink configured (pure engine-unit mode): mark as delivered=0
            # but record the intent; scenario tests assert on the callbacks row.
            self.store.exec(
                "UPDATE callbacks SET attempts=attempts+1,updated_at=? WHERE delivery_id=?",
                (time.time(), delivery_id))
            return
        body = json.dumps({"queue_if_low_balance": True} if kind == "approve" else {}).encode()
        auth = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Basic {auth}",
            "Idempotency-Key": delivery_id,
            "x-creator-id": wf["creator_id"],
            "X-Razorpay-Account": wf["owner_id"],
        }
        row = self.store.one("SELECT attempts FROM callbacks WHERE delivery_id=?",
                             (delivery_id,))
        attempts = row["attempts"] if row else 0
        while attempts < self.max_attempts:
            attempts += 1
            status = None
            try:
                req = urllib.request.Request(url, data=body, headers=headers,
                                             method="POST")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    status = resp.status
            except urllib.error.HTTPError as e:
                status = e.code
            except Exception:
                status = None
            delivered = 1 if status in SUCCESS_CODES else 0
            self.store.exec(
                "UPDATE callbacks SET attempts=?,last_status=?,delivered=?,updated_at=?"
                " WHERE delivery_id=?",
                (attempts, status if status else -1, delivered, time.time(), delivery_id))
            if delivered:
                self.store.audit(wf_id, wf["org_id"], None, "callback_delivered",
                                 {"kind": kind, "status": status, "attempts": attempts})
                return
            time.sleep(self.backoff * attempts)
        self.store.audit(wf_id, wf["org_id"], None, "callback_exhausted",
                         {"kind": kind, "attempts": attempts})
