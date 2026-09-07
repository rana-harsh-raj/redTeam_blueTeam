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
  GET  /health

Auth planes are strictly separated: an actor Bearer token never grants admin,
and the admin token is never handed to campaign workers.
"""
import base64
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import core as corelib


def _make_handler(engine):
    core = engine["core"]
    identity = engine["identity"]
    admin_token = engine["admin_token"]
    service_user = engine["service_user"]
    service_pass = engine["service_pass"]

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
            if path == "/health":
                return self._send(200, {"status": "ok", "service": "workflow-engine"})
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
                diff = (wf.get("diff") or {}).get("new") or {}
                amount = wf.get("amount", diff.get("amount", 0))
                r = core.create(
                    org_id=wf.get("org_id") or wf.get("owner_id"),
                    entity_id=wf.get("entity_id"),
                    creator_id=wf.get("creator_id"),
                    owner_id=wf.get("owner_id") or wf.get("org_id"),
                    amount=amount,
                    callback_details=wf.get("callback_details") or {},
                    idempotency_key=(body.get("idempotency_key")
                                     or self.headers.get("Idempotency-Key")),
                    entity_type=wf.get("entity_type", "payout"),
                )
                return self._result(r)

            # --- admin plane ----------------------------------------------
            if path.startswith("/admin/"):
                if not self._is_admin():
                    return self._send(403, {"error": "forbidden"})
                if path == "/admin/orgs":
                    return self._send(200, identity.create_org(body.get("name", "org")))
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
                 deliver_callbacks=True):
    from .store import Store
    from .identity import Identity
    from .callbacks import Callbacks
    store = Store(db_path)
    identity = Identity(store)
    callbacks = Callbacks(store, default_sink=callback_sink,
                          callback_user=callback_user, callback_pass=callback_pass,
                          deliver=deliver_callbacks)
    core = corelib.Core(store, identity, callbacks)
    callbacks.start()
    return {"store": store, "identity": identity, "callbacks": callbacks,
            "core": core, "admin_token": admin_token,
            "service_user": service_user, "service_pass": service_pass}


def serve(engine, host="127.0.0.1", port=8093):
    handler = _make_handler(engine)
    httpd = ThreadingHTTPServer((host, port), handler)
    return httpd
