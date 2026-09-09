#!/usr/bin/env python3
"""M11: provision payout-approval workflow configurations in the REAL Workflow service through its own Twirp
ConfigAPI (rpc/workflows/config/v1 ConfigAPI/Create, Basic auth over workflows [auth.*] pairs; cmd/api/main.go).

payouts' WfCreate (pkg/workflow/workflow_create.go) sends owner_id = merchant, owner_type = merchant, service = rx_live,
org_id = the merchant's org (payouts merchant config); the service resolves the config with
config repo FindByOwnerDetails(owner_id, owner_type, service, org_id, enabled). ConfigAPI/CreateV2 (the dashboard's
range/steps shape) hard-codes org_id "100000razorpay" (internal/dto/config_model.go), so the arena uses the v1
Create with a full Config whose template follows the service's own generated shape (tests/e2e/dataProvider/configs_data.json):
one amount range 1..20000000000 -> one checker state requiring `required_approvals` approvals by actors whose
actor_property role == `role` -> END_STATE. Meta = payout-approval details (domain payouts, task list payouts-approval,
workflow expire 8760h, decision timeout 10; validation.go ValidTemplate).

  python3 seed_configs.py apply                                  # every merchant of the monolith seed (org from the seed)
  python3 seed_configs.py merchant <mid> <org_id> [--role approver] [--approvals N] [--replace]
"""
import argparse
import base64
import json
import os
import sys
import time
import tomllib
import urllib.error
import urllib.request

WF = os.environ.get("WORKFLOWS_URL", "http://workflows-api:9400")
CONFIG_TOML = os.environ.get("WORKFLOWS_CONFIG_TOML", "/app/config/dev.toml")
MERCHANTS = os.environ.get("WORKFLOWS_MERCHANTS_FILE", "/app/seed/monolith_merchants.json")


def auth():
    cfg = tomllib.load(open(CONFIG_TOML, "rb"))
    a = cfg["auth"]["payouts"]
    return "Basic " + base64.b64encode(("%s:%s" % (a["username"], a["password"])).encode()).decode()


def twirp(service, method, body):
    req = urllib.request.Request("%s/twirp/%s/%s" % (WF, service, method), data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": auth()})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else {}
        except ValueError:
            return e.code, {"raw": raw.decode("utf-8", "replace")[:400]}


def wait(timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            st, _ = twirp("rzp.common.health.v1.HealthCheckAPI", "Check", {"service": ""})
            if st == 200:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    raise RuntimeError("workflows api not reachable at %s" % WF)


def template(role, approvals):
    rng = "1-20000000000_workflow"
    chk = "role_%s_1_approval" % role
    return {
        "state_transitions": {
            "START_STATE": {"current_state": "START_STATE", "next_states": [rng]},
            rng: {"current_state": rng, "next_states": [chk]},
            chk: {"current_state": chk, "next_states": ["END_STATE"]},
        },
        "states_data": {
            rng: {"name": rng, "group_name": "0", "type": "between", "rules": {"key": "amount", "min": 1, "max": 20000000000}},
            chk: {"name": chk, "group_name": "1", "type": "checker", "rules": {"actor_property_key": "role", "actor_property_value": role, "count": int(approvals)},
                  "callbacks": {"status": {"in": ["created", "processed"]}}},
        },
        # allowed_actions is keyed by ACTOR TYPE (internal/entities/action/interactor.go ILLEGAL_ACTION_NOT_DEFINED_IN_CONFIG_FOR_ACTOR_TYPE;
        # tests/e2e/dataProvider/configs_data.json uses admin/owner), not by state
        "allowed_actions": {"user": {"actions": ["approved", "rejected"]}, "admin": {"actions": ["approved", "rejected"]}, "owner": {"actions": ["approved", "rejected"]}},
        "meta": {"domain": "payouts", "task_list_name": "payouts-approval", "workflow_expire_time": 8760, "decision_task_timeout": 10},
        # internal/client/manager/helper/factory.go GetWorkflowClient switches on the TEMPLATE type: "approval" | "asl";
        # "payout-approval" is the CONFIG type (tests/e2e/dataProvider/configs_data.json)
        "type": "approval",
    }


def existing(mid, org):
    # ConfigListRequest carries its filters inside `config` (proto workflows/config/v1/config_api.proto); a request without
    # it makes internal/entities/config/server.go List dereference req.Config -> nil pointer panic (500) [F-M11-3]
    st, doc = twirp("rzp.workflows.config.v1.ConfigAPI", "List", {"limit": 10, "offset": 0, "config": {"owner_id": mid, "owner_type": "merchant", "service": "rx_live", "org_id": org}})
    items = doc.get("items") or doc.get("configs") or []
    return [c for c in items if c.get("owner_id") == mid and c.get("enabled") in ("true", True)]


def merchant(mid, org, role="approver", approvals=1, replace=False):
    have = existing(mid, org)
    if have and not replace:
        return {"merchant_id": mid, "status": "exists", "config_id": have[0].get("id")}
    for c in have if replace else []:
        twirp("rzp.workflows.config.v1.ConfigAPI", "Delete", {"id": c["id"]})
    cfg = {"name": "%s - Payout approval workflow" % mid, "template": template(role, approvals), "type": "payout-approval", "version": 1,
           "owner_id": mid, "owner_type": "merchant", "service": "rx_live", "org_id": org, "enabled": "true",
           "context": {"arena": "m11", "role": role, "approvals": str(approvals)}}
    st, doc = twirp("rzp.workflows.config.v1.ConfigAPI", "Create", {"config": cfg})
    return {"merchant_id": mid, "status": st, "config_id": doc.get("id"), "response": None if st == 200 else json.dumps(doc)[:400]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["apply", "merchant"]); ap.add_argument("mid", nargs="?"); ap.add_argument("org", nargs="?")
    ap.add_argument("--role", default="approver"); ap.add_argument("--approvals", type=int, default=1); ap.add_argument("--replace", action="store_true")
    a = ap.parse_args()
    wait()
    if a.cmd == "apply":
        report = []
        replace_all = os.environ.get("WORKFLOWS_SEED_REPLACE") == "1"
        for mid, m in json.load(open(MERCHANTS)).get("merchants", {}).items():
            org = (m.get("merchant") or {}).get("org_id") or "100000razorpay"
            report.append(merchant(mid, org, replace=replace_all))
        bad = [r for r in report if r["status"] not in ("exists", 200)]
        print(json.dumps({"merchants": len(report), "failed": len(bad), "sample": report[:2], "failures": bad[:3]}))
        sys.exit(1 if bad else 0)
    else:
        print(json.dumps(merchant(a.mid, a.org, a.role, a.approvals, a.replace)))
