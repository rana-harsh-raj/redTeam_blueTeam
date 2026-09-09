"""M11: the journey framework's view of the promoted trust path.

Two things live here:
  1. `trust()` -- which implementation the instance runs (ENV2_COMPOSE/.env.arena written by twinfactory):
     ARENA_TRUST_PATH real|substitute, ARENA_INGRESS_IMPL edge-kong|kong-lite, ARENA_WORKFLOW_HOST.
  2. `RealWorkflows` -- the REAL Workflow service (razorpay/workflows Twirp API) behind the same `wfe_*` surface the
     journeys used against the M5 reconstruction (workflow-engine):
        wfe_health / wfe_pending / wfe_workflows / wfe_for_payout   -> WorkflowAPI List/Get
        wfe_decide(pid, decision)                                    -> ActionAPI/CreateWithEntityId by an arena checker
        wfe_create_actor / wfe_set_policy                            -> local actor registry / ConfigAPI (approval count)
        wfe(POST /v1/workflows | GET /v1/workflows/{id} | .../audit) -> WorkflowAPI/Create, Get, ActionAPI/List
        wfe_actor(token, action, id) / wfe_audit(token, id)          -> ActionAPI/Create, ActionAPI/List
     Every call is recorded on the journey's evidence with BOTH the real Twirp status and the engine-shaped mapping,
     so a behavioural difference between the reconstruction and the real service is visible, never hidden.
     Mapping (documented in reports/implementation/M11_DIFFERENTIAL.md):
        engine `state` pending|approved|rejected  <- Workflow.domain_status (created -> pending; approved/rejected = the action taken)
        engine 404/409 (no pending / terminal)     <- Twirp error on an action against a non-pending workflow (real status kept)
        engine `callback` {ok,url,status_code}    <- observed: workflow domain_status turned terminal + the payout row moved;
                                                     url = callback_details.workflow_callbacks.processed.domain_status.<decision>.url_path
     What the real service does NOT have: actor identities/tokens (actor_id is a caller-supplied claim), eligible-approver
     sets, maker != checker separation (only a DCS-gated auto-approve when maker role == checker role). Those are
     recorded as source-supported corrections to the M5 reconstruction.
"""
import base64
import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path


def cname(service):
    """container name inside the instance's compose project (twinfactory sets ARENA_COMPOSE_PROJECT)."""
    return "%s-%s-1" % (os.environ.get("ARENA_COMPOSE_PROJECT", "env2_compose"), service)

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS_API = "http://workflows-api:9400"


def env2():
    return Path(os.environ.get("ARENA_ENV2_ROOT") or (REPO / "ENV2_COMPOSE")).resolve()


def trust():
    out = {"ARENA_TRUST_PATH": "substitute", "ARENA_INGRESS_IMPL": "kong-lite", "ARENA_WORKFLOW_HOST": "http://workflow-engine:8093"}
    try:
        for line in (env2() / ".env.arena").read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() in out:
                    out[k.strip()] = v.strip()
    except OSError:
        pass
    for k in out:
        if os.environ.get(k):
            out[k] = os.environ[k]
    out["real"] = out["ARENA_TRUST_PATH"] == "real"
    out["real_gateway"] = out["ARENA_INGRESS_IMPL"] == "edge-kong"
    out["real_workflows"] = "workflows-api" in out["ARENA_WORKFLOW_HOST"]
    return out


def bare(pid):
    return pid.split("_", 1)[1] if pid and pid.startswith("pout_") else (pid or "")


class RealWorkflows:
    """wfe_* surface over the real Workflow service. `arena` is the framework Arena (recording + arena HTTP)."""

    def __init__(self, arena):
        self.a = arena
        self.actors = {}          # token -> {actor_id, name, roles, org}
        self.policies = {}        # org -> {required_approvals, role}
        self._auth = None

    # -- transport -----------------------------------------------------------------------------------------------
    def _basic(self):
        if self._auth is None:
            pw = (env2() / "secrets" / "auth_payouts_workflows.txt").read_text().strip()
            self.a.hide(pw)
            self._auth = "workflow:" + pw
        return self._auth

    def twirp(self, service, method, body, note=None, identity=None):
        # X-User-Email is the caller identity the service records as triggered_by on every state transition
        # (internal/boot/hooks/auth.go WithBasicAuth -> common.GetTriggeredBy; blank => "triggered_by: cannot be blank")
        headers = {"X-User-Email": identity or "arena-operator@arena.invalid"}
        st, txt = self.a.http("POST", "%s/twirp/%s/%s" % (WORKFLOWS_API, service, method), body, headers, basic=self._basic(),
                              note=note or ("real Workflow service %s/%s" % (service.split(".")[-1], method)))
        try:
            doc = json.loads(txt) if txt else {}
        except ValueError:
            doc = {"raw": (txt or "")[:400]}
        return st, doc

    # -- mapping --------------------------------------------------------------------------------------------------
    @staticmethod
    def map_workflow(w):
        if not w:
            return None
        ds = w.get("domain_status") or w.get("domainStatus") or ""
        # razorpay/workflows: domain_status starts as "created" (interactor.go:385) and becomes the action taken
        # ("approved" | "rejected", approval/workflow.go:636 SetDomainStatus); the workflow's own `status` walks
        # creation_in_progress -> created -> initiated -> processed | failed | terminated (state_machine.go).
        state = {"created": "pending", "pending": "pending", "approved": "approved", "rejected": "rejected"}.get(ds, ds or w.get("status"))
        if w.get("status") in ("failed", "terminated", "init_failed") and state == "pending":
            state = w.get("status")
        cb = (((w.get("callback_details") or {}).get("workflow_callbacks") or {}).get("processed") or {}).get("domain_status") or {}
        return {"workflow_id": w.get("id"), "payout_id": "pout_" + (w.get("entity_id") or ""), "entity_id": w.get("entity_id"),
                "state": state, "status": w.get("status"), "domain_status": ds, "org_id": w.get("owner_id"), "owner_id": w.get("owner_id"),
                "creator_id": w.get("creator_id"), "config_id": w.get("config_id"), "service": w.get("service"),
                "callback_urls": {k: (v or {}).get("url_path") for k, v in cb.items()},
                "required_approvals": (self_required := None) or None, "raw_status": w.get("status")}

    def _list(self, **filt):
        body = {"limit": 100, "offset": 0}
        body.update(filt)
        st, doc = self.twirp("rzp.workflows.workflow.v1.WorkflowAPI", "List", body)
        items = (doc.get("items") or doc.get("workflows") or []) if isinstance(doc, dict) else []
        return st, [self.map_workflow(w) for w in items]

    def _get(self, wid):
        st, doc = self.twirp("rzp.workflows.workflow.v1.WorkflowAPI", "Get", {"id": wid})
        return st, (self.map_workflow(doc) if st == 200 else None), doc

    # -- wfe_* surface -------------------------------------------------------------------------------------------
    def wfe_health(self):
        st, doc = self.twirp("rzp.common.health.v1.HealthCheckAPI", "Check", {"service": ""}, note="real Workflow service liveness")
        return {"/health": {"status": st, "body": doc}, "/_arena/health": {"status": st, "body": {"impl": "workflows-api (razorpay/workflows)", "health": doc}}}

    def wfe_workflows(self):
        return self._list()[1]

    def wfe_pending(self):
        return [w for w in self._list()[1] if w["state"] == "pending"]

    def wfe_for_payout(self, pid):
        st, items = self._list(entity_ids=[bare(pid)])
        for w in items:
            if w["entity_id"] == bare(pid):
                if w["org_id"] in self.policies:
                    w["required_approvals"] = self.policies[w["org_id"]]["required_approvals"]
                return w
        return None

    def callback_from_worker_log(self, entity_id, since="30m"):
        """The real API does not expose callback details on a Workflow; the Cadence worker logs every delivery
        (approval/workflow.go MakeApiCallAndProcessResponse: "callback response" / "callback failure" with url + statusCode).
        Returns {ok, status_code, url, observed} for the entity's approve/reject callback, read from the worker's log."""
        try:
            out = subprocess.run(["docker", "logs", "--since", since, cname("workflows-worker")], capture_output=True, text=True, timeout=60)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "status_code": None, "url": None, "observed": "worker log unavailable: %s" % e}
        hit = None
        for line in (out.stdout + out.stderr).splitlines():
            if entity_id in line and ("callback response" in line or "callback failure" in line) and ("/approve" in line or "/reject" in line):
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                hit = {"ok": d.get("statusCode") in (200, 201, 409), "status_code": d.get("statusCode"), "url": (d.get("url") or "").split(" ", 1)[-1],
                       "observed": "workflows-worker %s: %s -> %s" % (d.get("message"), d.get("url"), d.get("statusCode"))}
        return hit or {"ok": False, "status_code": None, "url": None, "observed": "no approve/reject callback for %s in the worker log" % entity_id}

    def _checker_role(self, org):
        return (self.policies.get(org) or {}).get("role", "approver")

    def _wait_terminal(self, pid, timeout=40):
        t0 = time.time()
        w = None
        while time.time() - t0 < timeout:
            w = self.wfe_for_payout(pid)
            if w and w["state"] in ("approved", "rejected"):
                return w
            time.sleep(2)
        return w

    def wfe_decide(self, pid, decision, queue_if_low_balance=None, actor=None):
        """Operator-plane equivalent: an arena checker (role from the org's policy) acts on the payout's workflow."""
        before = self.wfe_for_payout(pid)
        if not before:
            return 404, {"error": "no_workflow_for_payout", "impl": "workflows-api"}
        if before["state"] != "pending":
            return 409, {"error": "terminal_state", "state": before["state"], "impl": "workflows-api", "real_status": "not_called"}
        # actor ids must be 14-character RZP ids (internal/entities/action/model.go rule.IsRZPID)
        actor = actor or {"actor_id": "ARENACHK" + before["org_id"][-6:], "roles": [self._checker_role(before["org_id"])]}
        body = {"entity_id": before["entity_id"], "entity_type": "payout", "config_id": before.get("config_id") or "",
                "action": "approved" if decision == "approve" else "rejected", "comment": "arena decision %s" % decision,
                "actor_id": actor["actor_id"], "actor_type": "user", "actor_property_key": "role", "actor_property_value": actor["roles"][0],
                "actor_meta": {"name": actor["actor_id"], "email": actor["actor_id"] + "@arena.invalid"},
                "owner_id": before["org_id"], "owner_type": "merchant", "service": "rx_live",
                "data": ({"queue_if_low_balance": bool(queue_if_low_balance)} if queue_if_low_balance is not None else {})}
        st, doc = self.twirp("rzp.workflows.action.v1.ActionAPI", "CreateWithEntityId", body, note="real ActionAPI: %s by %s (role %s)" % (decision, actor["actor_id"], actor["roles"][0]),
                             identity=actor["actor_id"] + "@arena.invalid")
        if st != 200:
            mapped = 409 if before["state"] != "pending" else (403 if (doc.get("code") in ("permission_denied",)) else 409)
            return mapped, {"error": doc.get("msg") or doc.get("code") or "action_refused", "real_status": st, "real_body": doc, "impl": "workflows-api"}
        after = self._wait_terminal(pid)
        expected = {"approve": "approved", "reject": "rejected"}[decision]
        ok = bool(after and after["state"] == expected)
        payout_row = self.a.payout(bare(pid))
        cb = self.callback_from_worker_log(before["entity_id"])
        cb["workflow_domain_status"] = (after or {}).get("state")
        cb["payout_status_after"] = (payout_row or {}).get("status")
        self.a._rec("http", {"transport": "observed (workflows-worker log)", "method": "POST", "url": cb.get("url"), "status": cb.get("status_code"),
                             "response": cb.get("observed"), "note": "the real Workflow service's callback into payouts (clients.payouts_live)"})
        return 200, {"workflow_id": before["workflow_id"], "state": (after or before)["state"], "decision": decision, "impl": "workflows-api",
                     "real_status": st, "action": doc.get("items", [None])[0] if isinstance(doc.get("items"), list) else doc,
                     "callback": cb}

    def wfe_create_actor(self, org_id, name, roles):
        actor_id = "act" + hashlib.sha256(("%s|%s" % (org_id, name)).encode()).hexdigest()[:11]
        token = uuid.uuid4().hex
        self.a.hide(token)
        self.actors[token] = {"actor_id": actor_id, "name": name, "roles": list(roles), "org": org_id}
        self.a._rec("http", {"transport": "local", "method": "POST", "url": "workflows-api actor registry (local)", "request": {"org_id": org_id, "name": name, "roles": roles},
                             "status": 200, "response": {"actor_id": actor_id}, "auth_plane": "n/a",
                             "note": "the real Workflow service has no actor identities: actor_id/role are caller-supplied claims on every action (proto workflows/action/v1 Action)"})
        return 200, {"actor_id": actor_id, "token": token, "roles": list(roles), "org_id": org_id, "impl": "workflows-api (claims, not identities)"}

    def wfe_set_policy(self, org_id, required_approvals=1, separation=1, eligible_approvers=None, expiry_seconds=86400, role="approver"):
        """Policy = the merchant's payout-approval Config (checker state count). Replaced through the ConfigAPI."""
        cmd = ["docker", "run", "--rm", "--network", os.environ.get("ARENA_NETWORK", "rzp-arena" + os.environ.get("ARENA_SUFFIX", "")), "--user", "10001:10001",
               "-v", "%s:/app/seed_configs.py:ro" % (env2() / "trustpath" / "workflows" / "seed_configs.py"),
               "-v", "%s:/app/config:ro" % ("rzp-arena-config-workflows" + os.environ.get("ARENA_SUFFIX", "")),
               "-e", "WORKFLOWS_URL=" + WORKFLOWS_API, "-e", "WORKFLOWS_CONFIG_TOML=/app/config/dev.toml",
               "python:3.12-alpine", "python3", "/app/seed_configs.py", "merchant", org_id, self._org_of(org_id), "--role", role,
               "--approvals", str(int(required_approvals)), "--replace"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        try:
            doc = json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            doc = {"raw": (r.stdout + r.stderr)[-400:]}
        ok = r.returncode == 0 and doc.get("status") in (200, "exists")
        self.policies[org_id] = {"required_approvals": int(required_approvals), "role": role}
        self.a._rec("http", {"transport": "arena-network", "method": "POST", "url": WORKFLOWS_API + "/twirp/rzp.workflows.config.v1.ConfigAPI/Create", "request": {"org": org_id, "approvals": required_approvals, "role": role},
                             "status": 200 if ok else 500, "response": doc, "auth_plane": "service",
                             "note": "real ConfigAPI: payout-approval config with a checker state of count=%d for role %s" % (int(required_approvals), role)})
        pol = {"org_id": org_id, "entity_type": "payout", "required_approvals": int(required_approvals), "role": role,
               "separation": "not_enforced_by_workflow_service (approveStateIfApplicable auto-approves only behind the DCS feature; no maker!=checker rule in razorpay/workflows)",
               "eligible_approvers": "not_a_concept (any actor presenting actor_property role=%s)" % role, "config_id": doc.get("config_id"), "impl": "workflows-api"}
        return (200 if ok else 500), pol

    def _org_of(self, mid):
        """The org_id payouts sends in WfCreate = merchant config org (monolith merchant seed)."""
        try:
            doc = json.loads((env2() / "seeds" / "generated" / "monolith" / "merchants.json").read_text())
            org = ((doc.get("merchants") or {}).get(mid) or {}).get("merchant", {}).get("org_id")
            if org:
                return org
        except (OSError, ValueError):
            pass
        return "100000razorpay"

    def wfe(self, method, path, body=None, token=None, admin=False, note=None):
        """Actor/admin-plane emulation over the real API (what the engine's HTTP planes did)."""
        actor = self.actors.get(token or "")
        if method == "POST" and path == "/v1/workflows":
            if not actor:
                return 401, {"error": "unknown_actor_token"}
            # every id is validated as a 14-character RZP id (internal/entities/workflow/model.go rule.IsRZPID) and the
            # Diff (old/new structs) is required -- the M5 engine accepted free-form ids and no diff
            raw = bare((body or {}).get("entity_id") or "")
            entity = raw if len(raw) == 14 and raw.isalnum() else ("ARENA" + uuid.uuid4().hex[:9].upper())
            amount = int((body or {}).get("amount") or 5000)
            req = {"workflow": {"entity_id": entity, "entity_type": "payout", "title": "arena maker workflow", "description": "raised by %s" % actor["name"],
                                "config_version": "1", "creator_id": actor["actor_id"], "creator_type": "user", "owner_id": actor["org"], "owner_type": "merchant",
                                "service": "rx_live", "org_id": self._org_of(actor["org"]),
                                "diff": {"old": {"amount": None, "merchant_id": None}, "new": {"amount": amount, "merchant_id": actor["org"]}},
                                "callback_details": {"workflow_callbacks": {"processed": {"domain_status": {
                                    "approved": {"type": "basic", "method": "post", "service": "payouts_live", "url_path": "/v1/payouts/payouts_internal/%s/approve" % entity, "headers": {}, "payload": {}, "response_handler": {"type": "success_status_codes", "success_status_codes": [200, 201, 409]}},
                                    "rejected": {"type": "basic", "method": "post", "service": "payouts_live", "url_path": "/v1/payouts/payouts_internal/%s/reject" % entity, "headers": {}, "payload": {}, "response_handler": {"type": "success_status_codes", "success_status_codes": [200, 201, 409]}}}}}}}}
            st, doc = self.twirp("rzp.workflows.workflow.v1.WorkflowAPI", "Create", req, note=note or "real WorkflowAPI/Create raised by the maker (creator_id = actor)",
                                 identity=actor["actor_id"] + "@arena.invalid")
            return st, (self.map_workflow(doc) | {"id": doc.get("id")} if st == 200 else doc)
        if method == "GET" and path.startswith("/v1/workflows/") and path.endswith("/audit"):
            return 200, {"events": self.wfe_audit(token, path.split("/")[3])}
        if method == "GET" and path.startswith("/v1/workflows/"):
            st, w, raw = self._get(path.split("/")[3])
            return st, (w | {"id": w["workflow_id"]} if w else raw)
        return 404, {"error": "unsupported_engine_path", "path": path}

    def wfe_actor(self, token, action, wf_id, version=None):
        actor = self.actors.get(token or "")
        if not actor:
            return 401, {"error": "unknown_actor_token"}
        st, w, raw = self._get(wf_id)
        if not w:
            return 404, {"error": "no_workflow", "real": raw}
        if w["state"] != "pending":
            return 409, {"error": "terminal_state", "state": w["state"], "impl": "workflows-api"}
        # ActionAPI/Create needs the pending state's id; CreateWithEntityId resolves the current state from the entity
        # (the same call payouts' operator plane uses), so an actor acting "on the workflow" maps to it
        role = (actor["roles"] or ["approver"])[-1]
        body = {"entity_id": w["entity_id"], "entity_type": "payout", "config_id": w.get("config_id") or "",
                "action": "approved" if action == "approve" else "rejected", "comment": "%s by %s" % (action, actor["name"]),
                "actor_id": actor["actor_id"], "actor_type": "user", "actor_property_key": "role", "actor_property_value": role,
                "actor_meta": {"name": actor["name"], "email": actor["actor_id"] + "@arena.invalid"},
                "owner_id": w["org_id"], "owner_type": "merchant", "service": "rx_live", "data": {}}
        st, doc = self.twirp("rzp.workflows.action.v1.ActionAPI", "CreateWithEntityId", body, note="real ActionAPI/CreateWithEntityId: %s by %s (role %s)" % (action, actor["actor_id"], role),
                             identity=actor["actor_id"] + "@arena.invalid")
        if st != 200:
            return 403, {"error": doc.get("msg") or doc.get("code") or "action_refused", "real_status": st, "real_body": doc, "impl": "workflows-api"}
        time.sleep(3)
        st2, w2, _ = self._get(wf_id)
        audit = self.wfe_audit(token, wf_id)
        approvals = len({a["actor_id"] for a in audit if a.get("action") == "approved" and a.get("status") in (None, "created", "processed", "approved")})
        return 200, {"id": wf_id, "state": (w2 or w)["state"], "approvals": approvals, "real_status": st, "impl": "workflows-api", "action": doc}

    def wfe_audit(self, token, wf_id):
        st, doc = self.twirp("rzp.workflows.action.v1.ActionAPI", "List", {"limit": 100, "action": {"workflow_id": wf_id}}, note="real ActionAPI/List (the workflow's actions = audit trail)")
        items = doc.get("items") or []
        return [{"action": a.get("action_type"), "actor_id": a.get("actor_id"), "status": a.get("status"), "state_id": a.get("state_id"), "created_at": a.get("created_at"), "actor_property_value": a.get("actor_property_value")} for a in items]
