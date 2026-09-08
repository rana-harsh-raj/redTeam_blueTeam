"""HTTP transport for the workflow engine.

Routes
  POST /twirp/rzp.workflows.workflow.v1.WorkflowAPI/Create   (service Basic auth)
      Faithful to payouts' WfCreate call: parks a pending workflow, returns a
      non-empty wfl_ id + domain_status=pending.
  POST /v1/workflows/{id}/approve|reject|cancel              (actor Bearer token)
  GET  /v1/workflows/{id}                                    (actor Bearer, org-scoped)
  GET  /v1/workflows/{id}/audit                              (actor Bearer, org-scoped)
  POST /admin/orgs|actors|policies|expire-sweep              (admin token)
  GET  /admin/workflows                                      (admin token; evidence plane)
  GET  /health | /_arena/health
  GET  /_arena/pending                                       (control plane, M6 arena)
  POST /_arena/decide {payout_id, decision, queue_if_low_balance?}
                                                             (control plane, M6 arena;
                                                              workflow-sim compatible)

Auth planes are strictly separated: an actor Bearer token never grants admin,
and the admin token is never handed to campaign workers.
"""
import base64
import hashlib
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import core as corelib

# M6 arena: the control plane (/_arena/*) is reachable only in-arena and is
# denied to the workload by the attacker broker (RED_LOOP known_gaps
# KG-SUB-WORKFLOW-DECIDE); it mirrors substitutes/workflow-sim's contract so the
# existing tooling (RED_LOOP/surface/m2_surface_acceptance.py) keeps working.
ARENA_DECIDE_WAIT_SECONDS = float(os.environ.get("WFE_ARENA_DECIDE_WAIT", "12"))


def _config_id_for(org_id):
    """Deterministic 14-char synthetic workflow config id per org/merchant --
    payouts stores it in workflow_entity_map.config_id CHAR(14) (nullable).
    The real engine resolves a per-merchant config; this reconstruction mints a
    stable stand-in rather than an empty string (labelled in FIDELITY.md)."""
    return ("wc" + hashlib.sha1(str(org_id or "").encode()).hexdigest())[:14]


def _payouts_create_view(view, wf, body_wf, config_id):
    """Mirror CreateHttpResponse (workflow_create.go:119-135) on top of the
    engine's own view, so payouts reads id/config_id/status/domain_status
    (:217-220) and the M5 clients keep reading id/state/version."""
    out = dict(view)
    out.update({
        "config_id": config_id,
        "entity_type": wf.get("entity_type") or "payout",
        "config_version": body_wf.get("config_version") or "1",
        "creator_type": body_wf.get("creator_type") or "user",
        "owner_type": body_wf.get("owner_type") or "merchant",
        "service": body_wf.get("service") or "rx_live",
        "type": "payout-approval",
        "diff": body_wf.get("diff") or {},
    })
    return out


def _make_handler(engine):
    core = engine["core"]
    identity = engine["identity"]
    admin_token = engine["admin_token"]
    service_user = engine["service_user"]
    service_pass = engine["service_pass"]
    tenant_key = engine.get("tenant_key", "org_id")
    callbacks = engine["callbacks"]
    autoprovision = engine.get("autoprovision")   # None = off (M5 standalone)

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass  # quiet; the engine's own audit log is the record

        # -- io -------------------------------------------------------------
        def _read(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(n) if n else b""
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except Exception:
                return {}

        def _send(self, status, obj):
            data = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _result(self, r):
            self._send(r.status, r.body)

        # -- auth -----------------------------------------------------------
        def _bearer(self):
            h = self.headers.get("Authorization", "")
            if h.startswith("Bearer "):
                return identity.resolve(h[7:].strip())
            return None

        def _is_admin(self):
            h = self.headers.get("Authorization", "")
            tok = h[7:].strip() if h.startswith("Bearer ") else \
                self.headers.get("X-Admin-Token", "")
            return bool(admin_token) and tok == admin_token

        def _service_ok(self):
            h = self.headers.get("Authorization", "")
            if not h.startswith("Basic "):
                return False
            try:
                u, _, p = base64.b64decode(h[6:]).decode().partition(":")
            except Exception:
                return False
            return u == service_user and p == service_pass

        # -- routing --------------------------------------------------------
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/health", "/ping", "/_arena/health"):
                n = core.store.one("SELECT COUNT(*) c FROM workflows WHERE state='pending'")["c"]
                return self._send(200, {"status": "ok", "service": "workflow-engine",
                                        "pending": n})
            if path == "/_arena/pending":
                # control plane / evidence plane (workflow-sim compatible shape)
                rows = core.store.all(
                    "SELECT * FROM workflows WHERE state='pending' ORDER BY created_at")
                items = [_arena_record(dict(r)) for r in rows]
                return self._send(200, {"count": len(items), "pending": items})
            if path == "/_arena/workflows":
                # evidence plane: EVERY workflow (workflow-sim keeps decided
                # records visible in its in-memory map; /_arena/pending drops
                # them once terminal, so this is where a verifier confirms an
                # approve/reject actually landed, without the admin token).
                rows = core.store.all(
                    "SELECT * FROM workflows ORDER BY created_at DESC LIMIT 500")
                items = [_arena_record(dict(r), core.store) for r in rows]
                return self._send(200, {"count": len(items), "workflows": items})
            if path == "/admin/workflows":
                if not self._is_admin():
                    return self._send(403, {"error": "forbidden"})
                rows = core.store.all("SELECT * FROM workflows ORDER BY created_at DESC")
                return self._send(200, {"workflows": [dict(r) for r in rows]})
            m = re.match(r"^/v1/workflows/([^/]+)/audit$", path)
            if m:
                actor = self._bearer()
                if not actor:
                    return self._send(401, {"error": "unauthenticated"})
                return self._result(core.audit_trail(wf_id=m.group(1), actor=actor))
            m = re.match(r"^/v1/workflows/([^/]+)$", path)
            if m:
                actor = self._bearer()
                if not actor:
                    return self._send(401, {"error": "unauthenticated"})
                return self._result(core.get(wf_id=m.group(1), actor=actor))
            return self._send(404, {"error": "no_route"})

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            body = self._read()

            # --- service create (payouts WfCreate) -------------------------
            if path == "/twirp/rzp.workflows.workflow.v1.WorkflowAPI/Create":
                if not self._service_ok():
                    return self._send(401, {"error": "unauthenticated"})
                wf = body.get("workflow", body)
                if not wf.get("entity_id"):
                    return self._send(400, {"error": "missing workflow.entity_id"})
                diff = (wf.get("diff") or {}).get("new") or {}
                amount = wf.get("amount", diff.get("amount", 0))
                owner_id = wf.get("owner_id") or wf.get("org_id")
                # Tenant boundary: payouts sends org_id = the Razorpay org
                # (merchantConfig.GetOrgID(), shared by every merchant) and
                # owner_id = the merchant id. WFE_TENANT_KEY=owner_id (arena
                # default) makes the engine's org == merchant so approvers are
                # scoped per merchant; org_id (standalone default) keeps M5's
                # behaviour where the client sends both equal.
                if tenant_key == "owner_id":
                    org_id = owner_id or wf.get("org_id")
                else:
                    org_id = wf.get("org_id") or owner_id
                # The real engine already knows every merchant; an unknown org
                # here would otherwise fail the workflows.org_id FK. Service
                # plane only -- actors/policies still need admin provisioning.
                identity.ensure_org(org_id, wf.get("owner_type") or "merchant")
                entity_type = wf.get("entity_type", "payout")
                # Default policy binding. The REAL engine resolves a workflow
                # config that an ops/admin flow has already bound to the
                # merchant+org; nothing in the payouts Create call carries one.
                # In the arena a merchant provisioned by RED_LOOP is brand new,
                # so with WFE_AUTOPROVISION_POLICY=1 the first Create for an
                # unknown owner mints the documented default (1 approval, any
                # actor holding `approver` in that org, maker!=checker enforced,
                # 24h expiry). It is a persisted policies row, so POST
                # /admin/policies overrides it at any time. Off by default, so
                # the M5 standalone suite/benchmark are unaffected.
                if autoprovision and not identity.get_policy(org_id, entity_type):
                    identity.set_policy(org_id, entity_type,
                                        autoprovision["required_approvals"],
                                        autoprovision["separation"], [],
                                        autoprovision["expiry_seconds"])
                r = core.create(
                    org_id=org_id,
                    entity_id=wf.get("entity_id"),
                    creator_id=wf.get("creator_id") or owner_id or "",
                    owner_id=owner_id,
                    amount=amount,
                    callback_details=wf.get("callback_details") or {},
                    idempotency_key=(body.get("idempotency_key")
                                     or self.headers.get("Idempotency-Key")),
                    entity_type=entity_type,
                )
                if r.ok:
                    config_id = wf.get("config_id") or _config_id_for(org_id)
                    r.body = _payouts_create_view(r.body, r.body, wf, config_id)
                return self._result(r)

            # --- arena control plane (workflow-sim compatible) --------------
            if path == "/_arena/decide":
                return self._result(_arena_decide(core, callbacks, body))

            # --- admin plane ----------------------------------------------
            if path.startswith("/admin/"):
                if not self._is_admin():
                    return self._send(403, {"error": "forbidden"})
                if path == "/admin/orgs":
                    return self._send(200, identity.create_org(
                        body.get("name", "org"), org_id=body.get("org_id")))
                if path == "/admin/actors":
                    try:
                        return self._send(200, identity.create_actor(
                            body["org_id"], body.get("name", "actor"),
                            body.get("roles", ["requester"])))
                    except KeyError as e:
                        return self._send(400, {"error": str(e)})
                if path == "/admin/policies":
                    return self._send(200, identity.set_policy(
                        body["org_id"], body.get("entity_type", "payout"),
                        body.get("required_approvals", 1),
                        body.get("separation", 1),
                        body.get("eligible_approvers", []),
                        body.get("expiry_seconds", 86400)))
                if path == "/admin/expire-sweep":
                    return self._send(200, {"swept": _expire_sweep(core)})
                return self._send(404, {"error": "no_admin_route"})

            # --- requester-initiated create (actor Bearer, requester role) --
            if path == "/v1/workflows":
                actor = self._bearer()
                if not actor:
                    return self._send(401, {"error": "unauthenticated"})
                if "requester" not in actor["roles"]:
                    return self._send(403, {"error": "not_requester",
                                             "message": "actor lacks requester role"})
                r = core.create(
                    org_id=actor["org_id"],
                    entity_id=body.get("entity_id") or ("pout_" + os.urandom(4).hex()),
                    creator_id=actor["actor_id"],
                    owner_id=actor["org_id"],
                    amount=body.get("amount", 1000),
                    callback_details=body.get("callback_details") or {},
                    idempotency_key=(body.get("idempotency_key")
                                     or self.headers.get("Idempotency-Key")),
                    entity_type=body.get("entity_type", "payout"),
                )
                return self._result(r)

            # --- actor actions --------------------------------------------
            m = re.match(r"^/v1/workflows/([^/]+)/(approve|reject|cancel)$", path)
            if m:
                actor = self._bearer()
                if not actor:
                    return self._send(401, {"error": "unauthenticated"})
                wf_id, action = m.group(1), m.group(2)
                ev = body.get("version", body.get("expected_version"))
                fn = getattr(core, action)
                return self._result(fn(wf_id=wf_id, actor=actor, expected_version=ev))

            return self._send(404, {"error": "no_route"})

    return H


def _arena_record(wf, store=None):
    """workflow-sim-shaped pending record (substitutes/workflow-sim/server.py
    _parse_create) plus the engine's own fields. When `store` is given the
    recorded callback-delivery outcome is attached too (evidence plane)."""
    try:
        cd = json.loads(wf.get("callback_details") or "{}")
    except Exception:
        cd = {}
    decision = None
    decision_result = None
    if wf.get("state") in ("approved", "rejected", "cancelled", "expired"):
        decision = "approve" if wf["state"] == "approved" else "reject"
    if store is not None:
        row = store.one("SELECT kind,attempts,last_status,delivered FROM callbacks"
                        " WHERE workflow_id=?", (wf["id"],))
        if row:
            decision_result = {"ok": bool(row["delivered"]),
                               "status_code": row["last_status"],
                               "attempts": row["attempts"], "kind": row["kind"]}
    return {
        "payout_id": wf["entity_id"],
        "workflow_id": wf["id"],
        "entity_type": wf.get("entity_type"),
        "owner_id": wf.get("owner_id"),
        "org_id": wf.get("org_id"),
        "creator_id": wf.get("creator_id"),
        "amount": wf.get("amount"),
        "config_id": _config_id_for(wf.get("org_id")),
        "state": wf.get("state"),
        "version": wf.get("version"),
        "required_approvals": wf.get("required_approvals"),
        "approvals_count": wf.get("approvals_count"),
        "created_at": wf.get("created_at"),
        "expires_at": wf.get("expires_at"),
        "callback_details": cd,
        "decided_at": wf.get("decided_at"),
        "decision": decision,
        "decision_result": decision_result,
    }


def _arena_decide(core, callbacks, body):
    """Control-plane decision: POST /_arena/decide {payout_id, decision,
    queue_if_low_balance?}. Bypasses actor/policy evaluation BY DESIGN (it is
    the operator/verifier plane, exactly like workflow-sim's /_arena/decide --
    never a merchant capability), moves the workflow to its terminal state,
    audits it as an arena decision, and fires the REAL payouts callback through
    the engine's durable delivery path. Returns the callback outcome
    synchronously (waits up to WFE_ARENA_DECIDE_WAIT seconds) in workflow-sim's
    response shape."""
    payout_id = str(body.get("payout_id") or "")
    decision = str(body.get("decision") or "").lower()
    if decision not in ("approve", "reject"):
        return corelib.err(400, "bad_decision", "decision must be 'approve' or 'reject'")
    with core.store.tx() as conn:
        row = conn.execute(
            "SELECT * FROM workflows WHERE (entity_id=? OR entity_id=?) AND state='pending'"
            " ORDER BY created_at DESC LIMIT 1",
            (payout_id, payout_id.replace("pout_", ""))).fetchone()
        if not row:
            return corelib.err(404, "no_pending_workflow",
                               "no pending workflow for payout_id", payout_id=payout_id)
        wf = dict(row)
        wf = core._maybe_expire(conn, wf)
        if wf["state"] != "pending":
            return corelib.err(409, "terminal_state", f"workflow is {wf['state']}",
                               state=wf["state"], payout_id=payout_id)
        try:
            cd = json.loads(wf.get("callback_details") or "{}")
        except Exception:
            cd = {}
        if body.get("queue_if_low_balance", None) is not None:
            cd["_arena_override"] = {"queue_if_low_balance": bool(body["queue_if_low_balance"])}
            conn.execute("UPDATE workflows SET callback_details=? WHERE id=?",
                         (json.dumps(cd), wf["id"]))
        new_state = "approved" if decision == "approve" else "rejected"
        conn.execute(
            "UPDATE workflows SET state=?,terminal=1,version=version+1,decided_at=?,"
            "approvals_count=CASE WHEN ?='approved' THEN required_approvals ELSE approvals_count END"
            " WHERE id=?", (new_state, time.time(), new_state, wf["id"]))
        conn.execute(
            "INSERT INTO audit_log(workflow_id,org_id,actor_id,action,detail,at)"
            " VALUES(?,?,?,?,?,?)",
            (wf["id"], wf["org_id"], "arena-control-plane", "arena_" + decision,
             json.dumps({"payout_id": payout_id}), time.time()))
        wf2 = core._wf(conn, wf["id"])
    kind = "approve" if decision == "approve" else "reject"
    callbacks.enqueue(wf2, kind, reason="arena_decide")
    delivery_id = f"cbk_{wf2['id']}_{kind}"
    deadline = time.time() + ARENA_DECIDE_WAIT_SECONDS
    cb = None
    while time.time() < deadline:
        cb = core.store.one("SELECT * FROM callbacks WHERE delivery_id=?", (delivery_id,))
        if cb and (cb["delivered"] or (cb["attempts"] or 0) >= callbacks.max_attempts):
            break
        time.sleep(0.2)
    cb = dict(cb) if cb else {}
    url, method, _h, _p = callbacks._target(wf2, kind)
    status = cb.get("last_status")
    result = {
        "ok": bool(cb.get("delivered")),
        "status_code": status if status and status > 0 else None,
        "attempts": cb.get("attempts", 0),
        "url": url, "method": method,
        "delivery_id": delivery_id,
        "pending_delivery": not bool(cb.get("delivered")) and
                            (cb.get("attempts") or 0) < callbacks.max_attempts,
    }
    return corelib.ok(200, payout_id=payout_id, decision=decision,
                      workflow_id=wf2["id"], state=wf2["state"], callback=result)


def _expire_sweep(core):
    rows = core.store.all(
        "SELECT id FROM workflows WHERE state='pending' AND expires_at<=?",
        (time.time(),))
    n = 0
    for r in rows:
        with core.store.tx() as conn:
            wf = core._wf(conn, r["id"])
            if wf:
                core._maybe_expire(conn, wf)
                n += 1
    return n


def build_engine(db_path, *, admin_token, service_user="workflow",
                 service_pass="workflow", callback_sink=None,
                 callback_user="rzp_live", callback_pass="workflow-secret",
                 deliver_callbacks=True, ps_api_url=None, tenant_key="org_id",
                 autoprovision=None):
    from .store import Store
    from .identity import Identity
    from .callbacks import Callbacks
    store = Store(db_path)
    identity = Identity(store)
    callbacks = Callbacks(store, default_sink=callback_sink,
                          callback_user=callback_user, callback_pass=callback_pass,
                          deliver=deliver_callbacks, ps_api_url=ps_api_url)
    core = corelib.Core(store, identity, callbacks)
    callbacks.start()
    return {"store": store, "identity": identity, "callbacks": callbacks,
            "core": core, "admin_token": admin_token,
            "service_user": service_user, "service_pass": service_pass,
            "tenant_key": tenant_key if tenant_key in ("org_id", "owner_id") else "org_id",
            "autoprovision": autoprovision}


def serve(engine, host="127.0.0.1", port=8093):
    handler = _make_handler(engine)
    httpd = ThreadingHTTPServer((host, port), handler)
    return httpd
