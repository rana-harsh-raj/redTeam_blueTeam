"""Thin HTTP client + provisioning helper for the workflow engine.

Shared by the scenario suite, the blind benchmark, and the autonomous campaign
so they all speak to the engine through one audited surface. Stdlib only.

Two planes:
  * AdminClient  — holds the admin token; provisions orgs/actors/policies.
                   Used by the operator/verifier, NEVER handed to campaign workers.
  * ActorClient  — holds a single actor bearer token; the only surface a
                   campaign worker is given.
"""
import base64
import json
import urllib.error
import urllib.request


def _req(method, url, *, headers=None, body=None, basic=None, timeout=10):
    h = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    if basic:
        h["Authorization"] = "Basic " + base64.b64encode(
            f"{basic[0]}:{basic[1]}".encode()).decode()
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return None, {"error": "transport", "message": str(e)}


class AdminClient:
    def __init__(self, base, admin_token, service_user="workflow",
                 service_pass="workflow"):
        self.base = base.rstrip("/")
        self.admin = admin_token
        self.service = (service_user, service_pass)

    def _h(self):
        return {"X-Admin-Token": self.admin}

    def create_org(self, name):
        return _req("POST", f"{self.base}/admin/orgs", headers=self._h(),
                    body={"name": name})[1]

    def create_actor(self, org_id, name, roles):
        return _req("POST", f"{self.base}/admin/actors", headers=self._h(),
                    body={"org_id": org_id, "name": name, "roles": roles})[1]

    def set_policy(self, org_id, **kw):
        kw["org_id"] = org_id
        return _req("POST", f"{self.base}/admin/policies", headers=self._h(),
                    body=kw)[1]

    def create_workflow(self, *, org_id, entity_id, creator_id, owner_id=None,
                        amount=1000, callback_details=None, idempotency_key=None):
        body = {"workflow": {
            "org_id": org_id, "entity_id": entity_id, "creator_id": creator_id,
            "owner_id": owner_id or org_id, "amount": amount,
            "callback_details": callback_details or {}}}
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        return _req("POST",
                    f"{self.base}/twirp/rzp.workflows.workflow.v1.WorkflowAPI/Create",
                    body=body, basic=self.service)

    def all_workflows(self):
        return _req("GET", f"{self.base}/admin/workflows", headers=self._h())[1]

    def expire_sweep(self):
        return _req("POST", f"{self.base}/admin/expire-sweep", headers=self._h())[1]


class ActorClient:
    """The only surface a campaign worker receives: a base URL + one bearer
    token. The worker does not know its counterpart's tokens, the admin token,
    the policy, or which environment this is."""

    def __init__(self, base, token):
        self.base = base.rstrip("/")
        self.token = token

    def _h(self):
        return {"Authorization": f"Bearer {self.token}"}

    def create(self, entity_id=None, amount=1000, idempotency_key=None):
        b = {"amount": amount}
        if entity_id:
            b["entity_id"] = entity_id
        if idempotency_key:
            b["idempotency_key"] = idempotency_key
        return _req("POST", f"{self.base}/v1/workflows", headers=self._h(), body=b)

    def approve(self, wf_id, version=None):
        b = {"version": version} if version is not None else {}
        return _req("POST", f"{self.base}/v1/workflows/{wf_id}/approve",
                    headers=self._h(), body=b)

    def reject(self, wf_id, version=None):
        b = {"version": version} if version is not None else {}
        return _req("POST", f"{self.base}/v1/workflows/{wf_id}/reject",
                    headers=self._h(), body=b)

    def cancel(self, wf_id, version=None):
        b = {"version": version} if version is not None else {}
        return _req("POST", f"{self.base}/v1/workflows/{wf_id}/cancel",
                    headers=self._h(), body=b)

    def get(self, wf_id):
        return _req("GET", f"{self.base}/v1/workflows/{wf_id}", headers=self._h())

    def audit(self, wf_id):
        return _req("GET", f"{self.base}/v1/workflows/{wf_id}/audit",
                    headers=self._h())


def wait_health(base, timeout=15):
    import time
    end = time.time() + timeout
    while time.time() < end:
        s, _ = _req("GET", f"{base}/health", timeout=2)
        if s == 200:
            return True
        time.sleep(0.2)
    return False
