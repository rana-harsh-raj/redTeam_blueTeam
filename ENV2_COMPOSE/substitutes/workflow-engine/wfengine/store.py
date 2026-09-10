"""Durable SQLite store for the workflow engine.

The database file is the single source of truth: every state transition is
committed here, so the service recovers its full state on restart. WAL mode +
a short busy timeout let the threaded HTTP server and the expiry sweeper share
the connection safely under concurrency.
"""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


class Store:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        # One connection guarded by a lock; SQLite under WAL handles our
        # modest concurrency, and the lock keeps multi-statement transactions
        # atomic across the engine's request threads.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False,
                                     timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    # --- low level ---------------------------------------------------------
    def tx(self):
        return _Tx(self)

    def exec(self, sql, args=()):
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur

    def one(self, sql, args=()):
        with self._lock:
            cur = self._conn.execute(sql, args)
            return cur.fetchone()

    def all(self, sql, args=()):
        with self._lock:
            cur = self._conn.execute(sql, args)
            return cur.fetchall()

    # --- audit -------------------------------------------------------------
    def audit(self, workflow_id, org_id, actor_id, action, detail=None):
        self.exec(
            "INSERT INTO audit_log(workflow_id,org_id,actor_id,action,detail,at)"
            " VALUES(?,?,?,?,?,?)",
            (workflow_id, org_id, actor_id, action,
             json.dumps(detail or {}), time.time()),
        )


class _Tx:
    """A short critical section holding the store lock, for read-modify-write
    sequences (the approve/reject path) that must be atomic against concurrent
    decisions on the same workflow."""

    def __init__(self, store):
        self.store = store

    def __enter__(self):
        self.store._lock.acquire()
        return self.store._conn

    def __exit__(self, *a):
        try:
            if a[0] is None:
                self.store._conn.commit()
            else:
                self.store._conn.rollback()
        finally:
            self.store._lock.release()
        return False
