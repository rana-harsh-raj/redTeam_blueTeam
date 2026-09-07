"""Workflow decision core — the enforced maker/checker semantics.

This is the module a benchmark mutant patches to introduce exactly one
authorization or workflow-state defect. Every invariant is enforced at a
clearly-marked check (``# [CHECK:...]``) so a mutation can disable precisely
one property while leaving the valid workflow fully functional.

Invariant families enforced here:
  * org-boundary        — an actor acts only within its own organization
  * maker/checker (SoD) — an approver is never the creator, when separation is on
  * approval-integrity  — N *distinct* eligible approvers are required; a repeat
                          approval by the same actor is idempotent, not counted
  * state-integrity     — terminal workflows are frozen; stale (versioned)
                          decisions are rejected; expired workflows cannot be approved

All transitions bump ``version`` and append to the audit log. The store is the
source of truth, so a restart mid-flow recovers the exact state.
"""
import json
import time

TERMINAL_STATES = ("approved", "rejected", "cancelled", "expired")


class Result:
    __slots__ = ("ok", "status", "body")

    def __init__(self, ok, status, body):
        self.ok = ok
        self.status = status
        self.body = body


def ok(_status, **body):
    return Result(True, _status, body)


def err(_status, code, msg, **extra):
    b = {"error": code, "message": msg}
    b.update(extra)
    return Result(False, _status, b)


class Core:
    def __init__(self, store, identity, callbacks):
        self.store = store
        self.identity = identity
        self.callbacks = callbacks

    # -- helpers ------------------------------------------------------------
    def _wf(self, conn, wf_id):
        cur = conn.execute("SELECT * FROM workflows WHERE id=?", (wf_id,))
        r = cur.fetchone()
        return dict(r) if r else None

    def _maybe_expire(self, conn, wf):
        """Lazily move a past-expiry pending workflow to 'expired' before any
        decision is evaluated (state-integrity)."""
        if wf["state"] == "pending" and time.time() >= wf["expires_at"]:
            conn.execute(
                "UPDATE workflows SET state='expired',terminal=1,version=version+1,"
                "decided_at=? WHERE id=?", (time.time(), wf["id"]))
            conn.execute(
                "INSERT INTO audit_log(workflow_id,org_id,actor_id,action,detail,at)"
                " VALUES(?,?,?,?,?,?)",
                (wf["id"], wf["org_id"], None, "expired", "{}", time.time()))
            wf["state"] = "expired"
            wf["terminal"] = 1
            wf["version"] += 1
            # fire the reject-side callback so the payout leaves pending
            self.callbacks.enqueue(wf, "reject", reason="expired")
        return wf

    def _distinct_approvals(self, conn, wf_id):
        cur = conn.execute(
            "SELECT COUNT(*) c FROM approvals WHERE workflow_id=?", (wf_id,))
        return cur.fetchone()["c"]

    # -- create -------------------------------------------------------------
    def create(self, *, org_id, entity_id, creator_id, owner_id, amount,
               callback_details, idempotency_key=None, entity_type="payout"):
        """Create (or idempotently return) a pending workflow. Mirrors the real
        payouts WfCreate call: a create only parks a pending record and returns
        a non-empty id."""
        with self.store.tx() as conn:
            # [CHECK:dup-request] same (org, idempotency_key) or (org, entity)
            # returns the existing instance instead of creating a duplicate.
            if idempotency_key:
                cur = conn.execute(
                    "SELECT * FROM workflows WHERE org_id=? AND idempotency_key=?",
                    (org_id, idempotency_key))
                r = cur.fetchone()
                if r:
                    return ok(200, **self._view(dict(r)), idempotent=True)
            cur = conn.execute(
                "SELECT * FROM workflows WHERE org_id=? AND entity_id=? AND state='pending'",
                (org_id, entity_id))
            r = cur.fetchone()
            if r:
                return ok(200, **self._view(dict(r)), idempotent=True)

            pol = self.identity.get_policy(org_id, entity_type) or {
                "required_approvals": 1, "separation": 1,
                "eligible_approvers": "", "expiry_seconds": 86400}
            from .identity import new_id
            wf_id = new_id("wfl")
            now = time.time()
            expires = now + int(pol["expiry_seconds"])
            conn.execute(
                "INSERT INTO workflows(id,org_id,entity_type,entity_id,creator_id,"
                "owner_id,amount,state,version,required_approvals,separation,"
                "eligible_approvers,approvals_count,idempotency_key,callback_details,"
                "created_at,expires_at,terminal) VALUES(?,?,?,?,?,?,?,'pending',1,?,?,?,0,?,?,?,?,0)",
                (wf_id, org_id, entity_type, entity_id, creator_id, owner_id,
                 int(amount or 0), int(pol["required_approvals"]),
                 int(pol["separation"]), pol.get("eligible_approvers", ""),
                 idempotency_key, json.dumps(callback_details or {}), now, expires))
            conn.execute(
                "INSERT INTO audit_log(workflow_id,org_id,actor_id,action,detail,at)"
                " VALUES(?,?,?,?,?,?)",
                (wf_id, org_id, creator_id, "created",
                 json.dumps({"entity_id": entity_id, "amount": amount}), now))
            wf = self._wf(conn, wf_id)
        return ok(200, **self._view(wf))

    # -- approve ------------------------------------------------------------
    def approve(self, *, wf_id, actor, expected_version=None):
        with self.store.tx() as conn:
            wf = self._wf(conn, wf_id)
            if not wf:
                return err(404, "not_found", "workflow not found")
            wf = self._maybe_expire(conn, wf)

            # [CHECK:terminal] a terminal workflow is frozen (state-integrity)
            if wf["terminal"] or wf["state"] in TERMINAL_STATES:
                return err(409, "terminal_state",
                           f"workflow is {wf['state']}", state=wf["state"])

            # [CHECK:org] actor must belong to the workflow's org (org-boundary)
            if actor["org_id"] != wf["org_id"]:
                return err(403, "cross_org",
                           "actor organization does not match workflow")

            # [CHECK:role] actor must hold the approver role
            if "approver" not in actor["roles"]:
                return err(403, "not_approver", "actor lacks approver role")

            # [CHECK:eligible] if an eligible set is configured, actor must be in it
            elig = [e for e in (wf["eligible_approvers"] or "").split(",") if e]
            if elig and actor["actor_id"] not in elig:
                return err(403, "not_eligible", "actor is not an eligible approver")

            # [CHECK:separation] approver must not be the creator (maker/checker)
            if wf["separation"] and actor["actor_id"] == wf["creator_id"]:
                return err(403, "separation_violation",
                           "maker cannot approve own request")

            # [CHECK:stale] optimistic-concurrency guard (state-integrity)
            if expected_version is not None and int(expected_version) != wf["version"]:
                return err(409, "stale_version",
                           "workflow version has advanced",
                           expected=int(expected_version), actual=wf["version"])

            # [CHECK:dup-approval] a repeat approval by the same actor is
            # idempotent and MUST NOT increment the distinct-approver count.
            existing = conn.execute(
                "SELECT 1 FROM approvals WHERE workflow_id=? AND approver_id=?",
                (wf_id, actor["actor_id"])).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO approvals(workflow_id,approver_id,org_id,created_at)"
                    " VALUES(?,?,?,?)",
                    (wf_id, actor["actor_id"], actor["org_id"], time.time()))

            count = self._distinct_approvals(conn, wf_id)
            new_version = wf["version"] + 1
            reached = count >= wf["required_approvals"]
            new_state = "approved" if reached else "pending"
            conn.execute(
                "UPDATE workflows SET approvals_count=?,version=?,state=?,"
                "terminal=?,decided_at=? WHERE id=?",
                (count, new_version, new_state, 1 if reached else 0,
                 time.time() if reached else None, wf_id))
            conn.execute(
                "INSERT INTO audit_log(workflow_id,org_id,actor_id,action,detail,at)"
                " VALUES(?,?,?,?,?,?)",
                (wf_id, wf["org_id"], actor["actor_id"], "approved",
                 json.dumps({"count": count,
                             "required": wf["required_approvals"]}), time.time()))
            wf2 = self._wf(conn, wf_id)
        if reached:
            self.callbacks.enqueue(wf2, "approve")
        return ok(200, **self._view(wf2), approvals=count,
                  required=wf2["required_approvals"])

    # -- reject -------------------------------------------------------------
    def reject(self, *, wf_id, actor, expected_version=None):
        with self.store.tx() as conn:
            wf = self._wf(conn, wf_id)
            if not wf:
                return err(404, "not_found", "workflow not found")
            wf = self._maybe_expire(conn, wf)
            if wf["terminal"] or wf["state"] in TERMINAL_STATES:
                return err(409, "terminal_state",
                           f"workflow is {wf['state']}", state=wf["state"])
            if actor["org_id"] != wf["org_id"]:                       # [CHECK:org]
                return err(403, "cross_org", "actor org mismatch")
            if "approver" not in actor["roles"]:                     # [CHECK:role]
                return err(403, "not_approver", "actor lacks approver role")
            elig = [e for e in (wf["eligible_approvers"] or "").split(",") if e]
            if elig and actor["actor_id"] not in elig:               # [CHECK:eligible]
                return err(403, "not_eligible", "not an eligible approver")
            if wf["separation"] and actor["actor_id"] == wf["creator_id"]:  # [CHECK:separation]
                return err(403, "separation_violation",
                           "maker cannot reject as checker")
            if expected_version is not None and int(expected_version) != wf["version"]:
                return err(409, "stale_version", "version advanced",
                           expected=int(expected_version), actual=wf["version"])
            conn.execute(
                "UPDATE workflows SET state='rejected',terminal=1,version=version+1,"
                "decided_at=? WHERE id=?", (time.time(), wf_id))
            conn.execute(
                "INSERT INTO audit_log(workflow_id,org_id,actor_id,action,detail,at)"
                " VALUES(?,?,?,?,?,?)",
                (wf_id, wf["org_id"], actor["actor_id"], "rejected", "{}", time.time()))
            wf2 = self._wf(conn, wf_id)
        self.callbacks.enqueue(wf2, "reject")
        return ok(200, **self._view(wf2))

    # -- cancel -------------------------------------------------------------
    def cancel(self, *, wf_id, actor, expected_version=None):
        with self.store.tx() as conn:
            wf = self._wf(conn, wf_id)
            if not wf:
                return err(404, "not_found", "workflow not found")
            wf = self._maybe_expire(conn, wf)
            if wf["terminal"] or wf["state"] in TERMINAL_STATES:
                return err(409, "terminal_state",
                           f"workflow is {wf['state']}", state=wf["state"])
            if actor["org_id"] != wf["org_id"]:                       # [CHECK:org]
                return err(403, "cross_org", "actor org mismatch")
            # cancel is a maker/operator action: creator or an operator role
            if actor["actor_id"] != wf["creator_id"] and "operator" not in actor["roles"]:
                return err(403, "not_cancellable",
                           "only the creator or an operator may cancel")
            if expected_version is not None and int(expected_version) != wf["version"]:
                return err(409, "stale_version", "version advanced",
                           expected=int(expected_version), actual=wf["version"])
            conn.execute(
                "UPDATE workflows SET state='cancelled',terminal=1,version=version+1,"
                "decided_at=? WHERE id=?", (time.time(), wf_id))
            conn.execute(
                "INSERT INTO audit_log(workflow_id,org_id,actor_id,action,detail,at)"
                " VALUES(?,?,?,?,?,?)",
                (wf_id, wf["org_id"], actor["actor_id"], "cancelled", "{}", time.time()))
            wf2 = self._wf(conn, wf_id)
        self.callbacks.enqueue(wf2, "reject", reason="cancelled")
        return ok(200, **self._view(wf2))

    # -- read ---------------------------------------------------------------
    def get(self, *, wf_id, actor):
        with self.store.tx() as conn:
            wf = self._wf(conn, wf_id)
            if not wf:
                return err(404, "not_found", "workflow not found")
            wf = self._maybe_expire(conn, wf)
            # [CHECK:org-read] cross-org reads are denied (org-boundary)
            if actor["org_id"] != wf["org_id"]:
                return err(403, "cross_org", "actor org mismatch")
        return ok(200, **self._view(wf))

    def audit_trail(self, *, wf_id, actor):
        wf = self.store.one("SELECT org_id FROM workflows WHERE id=?", (wf_id,))
        if not wf:
            return err(404, "not_found", "workflow not found")
        if actor["org_id"] != wf["org_id"]:
            return err(403, "cross_org", "actor org mismatch")
        rows = self.store.all(
            "SELECT actor_id,action,detail,at FROM audit_log WHERE workflow_id=?"
            " ORDER BY seq", (wf_id,))
        return ok(200, workflow_id=wf_id, events=[dict(r) for r in rows])

    # -- view ---------------------------------------------------------------
    def _view(self, wf):
        return {
            "id": wf["id"],
            "org_id": wf["org_id"],
            "entity_id": wf["entity_id"],
            "entity_type": wf["entity_type"],
            "creator_id": wf["creator_id"],
            "owner_id": wf["owner_id"],
            "state": wf["state"],
            "domain_status": "pending" if wf["state"] == "pending" else wf["state"],
            "status": "created",
            "version": wf["version"],
            "required_approvals": wf["required_approvals"],
            "approvals_count": wf["approvals_count"],
            "separation": bool(wf["separation"]),
            "expires_at": wf["expires_at"],
            "terminal": bool(wf["terminal"]),
        }
