#!/usr/bin/env python3
"""End-to-end workflow scenario suite (passing-metric #2).

Boots a REAL workflow-engine process + a payout-sink process, provisions fresh
orgs/actors, and exercises every required scenario over HTTP against the
running services — including a genuine process restart mid-flow. Emits
reports/implementation/m5-workflow-scenarios.json.

Scenarios:
  approval_success, rejection, cancellation, expiry, duplicate_approval,
  duplicate_callback, stale_decision, concurrent_decisions, restart_recovery,
  cross_org_denial, maker_checker_separation, callback_identity
No scenario is allowed to "pass" by being skipped: each asserts concrete
runtime effects and is marked failed on any assertion miss.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ENGINE_DIR = REPO / "ENV2_COMPOSE" / "substitutes" / "workflow-engine"
sys.path.insert(0, str(HERE))
from wfclient import AdminClient, ActorClient, wait_health, _req  # noqa: E402

ADMIN = "admin-scenario-token"
SVC = ("workflow", "workflow")
CB_USER, CB_PASS = "rzp_live", "workflow-secret"


class Proc:
    def __init__(self, name, argv, env):
        self.name = name
        self.argv = argv
        self.env = env
        self.p = None

    def start(self):
        self.p = subprocess.Popen(self.argv, env=self.env,
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)

    def stop(self):
        if self.p:
            self.p.terminate()
            try:
                self.p.wait(timeout=5)
            except Exception:
                self.p.kill()


def boot(db_path, engine_port, sink_port, sink_log):
    base_env = dict(os.environ)
    sink_env = dict(base_env, SINK_HOST="127.0.0.1", SINK_PORT=str(sink_port),
                    SINK_USER=CB_USER, SINK_PASS=CB_PASS, SINK_LOG=sink_log)
    sink = Proc("payout-sink",
                [sys.executable, str(ENGINE_DIR / "payout_sink.py")], sink_env)
    sink.start()
    eng_env = dict(base_env, WFE_DB=db_path, WFE_HOST="127.0.0.1",
                   WFE_PORT=str(engine_port), WFE_ADMIN_TOKEN=ADMIN,
                   WFE_SERVICE_USER=SVC[0], WFE_SERVICE_PASS=SVC[1],
                   WFE_CALLBACK_SINK=f"http://127.0.0.1:{sink_port}",
                   WFE_CALLBACK_USER=CB_USER, WFE_CALLBACK_PASS=CB_PASS)
    eng = Proc("workflow-engine",
               [sys.executable, str(ENGINE_DIR / "run_engine.py")], eng_env)
    eng.start()
    ebase = f"http://127.0.0.1:{engine_port}"
    sbase = f"http://127.0.0.1:{sink_port}"
    assert wait_health(ebase), "engine did not become healthy"
    assert wait_health(sbase), "sink did not become healthy"
    return eng, sink, ebase, sbase, eng_env


def provision(admin, *, required=1, separation=1, expiry=3600, n_approvers=2):
    org = admin.create_org("org-" + os.urandom(3).hex())["org_id"]
    maker = admin.create_actor(org, "maker", ["requester"])
    approvers = [admin.create_actor(org, f"chk{i}", ["approver"])
                 for i in range(n_approvers)]
    admin.set_policy(org, required_approvals=required, separation=separation,
                     eligible_approvers=[], expiry_seconds=expiry)
    return org, maker, approvers


def run():
    results = []

    def rec(name, ok, detail):
        results.append({"scenario": name, "passed": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    tmp = tempfile.mkdtemp(prefix="m5-scn-")
    db = os.path.join(tmp, "wfe.db")
    sink_log = os.path.join(tmp, "sink.jsonl")
    eng, sink, ebase, sbase, eng_env = boot(db, 8093, 8097, sink_log)
    admin = AdminClient(ebase, ADMIN, *SVC)
    try:
        # 1. approval_success (+ callback reaches sink)
        org, maker, aps = provision(admin, required=1)
        s, wf = admin.create_workflow(org_id=org, entity_id="pout_ok",
                                      creator_id=maker["actor_id"])
        c0 = ActorClient(ebase, aps[0]["token"])
        s1, b1 = c0.approve(wf["id"])
        time.sleep(0.6)
        deliveries = _req("GET", f"{sbase}/_deliveries")[1]["deliveries"]
        got_cb = any(d["path"].endswith("pout_ok") and d["authed"] for d in deliveries)
        rec("approval_success",
            s1 == 200 and b1["state"] == "approved" and got_cb,
            f"approve={s1} state={b1.get('state')} callback_authed={got_cb}")

        # 2. rejection
        org, maker, aps = provision(admin, required=1)
        s, wf = admin.create_workflow(org_id=org, entity_id="pout_rej",
                                      creator_id=maker["actor_id"])
        s1, b1 = ActorClient(ebase, aps[0]["token"]).reject(wf["id"])
        rec("rejection", s1 == 200 and b1["state"] == "rejected",
            f"reject={s1} state={b1.get('state')}")

        # 3. cancellation (by maker)
        org, maker, aps = provision(admin, required=1)
        s, wf = admin.create_workflow(org_id=org, entity_id="pout_can",
                                      creator_id=maker["actor_id"])
        mk = admin.create_actor(org, "maker-actor", ["requester"])
        # give the maker an identity token equal to creator: create via that actor
        s, wf2 = admin.create_workflow(org_id=org, entity_id="pout_can2",
                                       creator_id=mk["actor_id"])
        s1, b1 = ActorClient(ebase, mk["token"]).cancel(wf2["id"])
        rec("cancellation", s1 == 200 and b1["state"] == "cancelled",
            f"cancel={s1} state={b1.get('state')}")

        # 4. expiry
        org, maker, aps = provision(admin, required=1, expiry=1)
        s, wf = admin.create_workflow(org_id=org, entity_id="pout_exp",
                                      creator_id=maker["actor_id"])
        time.sleep(1.3)
        s1, b1 = ActorClient(ebase, aps[0]["token"]).approve(wf["id"])
        s2, b2 = ActorClient(ebase, aps[0]["token"]).get(wf["id"])
        rec("expiry",
            s1 == 409 and b1.get("error") == "terminal_state" and b2["state"] == "expired",
            f"approve_after_expiry={s1}/{b1.get('error')} state={b2.get('state')}")

        # 5. duplicate_approval (idempotent, count stays at 1 of 2)
        org, maker, aps = provision(admin, required=2)
        s, wf = admin.create_workflow(org_id=org, entity_id="pout_dup",
                                      creator_id=maker["actor_id"])
        c0 = ActorClient(ebase, aps[0]["token"])
        r1 = c0.approve(wf["id"]); r2 = c0.approve(wf["id"])
        rec("duplicate_approval",
            r1[1].get("approvals") == 1 and r2[1].get("approvals") == 1
            and r2[1]["state"] == "pending",
            f"count1={r1[1].get('approvals')} count2={r2[1].get('approvals')} state={r2[1].get('state')}")

        # 6. duplicate_callback (receiver is idempotent -> 409 on replay)
        org, maker, aps = provision(admin, required=1)
        s, wf = admin.create_workflow(org_id=org, entity_id="pout_cbk",
                                      creator_id=maker["actor_id"])
        ActorClient(ebase, aps[0]["token"]).approve(wf["id"])
        time.sleep(0.5)
        idem = f"cbk_{wf['id']}_approve"
        rA = _req("POST", f"{sbase}/callback/approve/pout_cbk",
                  headers={"Idempotency-Key": idem, "x-creator-id": maker["actor_id"],
                           "X-Razorpay-Account": org},
                  body={}, basic=(CB_USER, CB_PASS))
        rec("duplicate_callback", rA[0] == 409,
            f"replay_status={rA[0]} (409=already_applied, engine-safe)")

        # 7. stale_decision (versioned optimistic concurrency)
        org, maker, aps = provision(admin, required=2)
        s, wf = admin.create_workflow(org_id=org, entity_id="pout_stale",
                                      creator_id=maker["actor_id"])
        c0 = ActorClient(ebase, aps[0]["token"]); c1 = ActorClient(ebase, aps[1]["token"])
        c0.approve(wf["id"], version=1)  # version -> 2
        st_stale = c1.approve(wf["id"], version=1)  # stale
        st_fresh = c1.approve(wf["id"], version=2)  # correct
        rec("stale_decision",
            st_stale[0] == 409 and st_stale[1].get("error") == "stale_version"
            and st_fresh[1]["state"] == "approved",
            f"stale={st_stale[0]}/{st_stale[1].get('error')} fresh_state={st_fresh[1].get('state')}")

        # 8. concurrent_decisions (approve vs reject -> exactly one terminal)
        org, maker, aps = provision(admin, required=1)
        s, wf = admin.create_workflow(org_id=org, entity_id="pout_race",
                                      creator_id=maker["actor_id"])
        c0 = ActorClient(ebase, aps[0]["token"]); c1 = ActorClient(ebase, aps[1]["token"])
        out = {}
        def _appr(): out["a"] = c0.approve(wf["id"])
        def _rej(): out["r"] = c1.reject(wf["id"])
        t1 = threading.Thread(target=_appr); t2 = threading.Thread(target=_rej)
        t1.start(); t2.start(); t1.join(); t2.join()
        codes = sorted([out["a"][0], out["r"][0]])
        final = c0.get(wf["id"])[1]["state"]
        rec("concurrent_decisions",
            codes == [200, 409] and final in ("approved", "rejected"),
            f"codes={codes} final={final} (exactly one winner)")

        # 9. restart_recovery: kill engine mid-flow, restart on same DB
        org, maker, aps = provision(admin, required=2)
        s, wf = admin.create_workflow(org_id=org, entity_id="pout_restart",
                                      creator_id=maker["actor_id"])
        ActorClient(ebase, aps[0]["token"]).approve(wf["id"])  # 1 of 2
        eng.stop()
        eng2 = Proc("workflow-engine", eng.argv, eng_env)
        eng2.start(); assert wait_health(ebase), "engine restart unhealthy"
        eng = eng2
        after = ActorClient(ebase, aps[0]["token"]).get(wf["id"])[1]
        s2, b2 = ActorClient(ebase, aps[1]["token"]).approve(wf["id"])
        rec("restart_recovery",
            after["state"] == "pending" and after["approvals_count"] == 1
            and b2["state"] == "approved",
            f"post_restart_state={after['state']} count={after['approvals_count']} then={b2.get('state')}")

        # 10. cross_org_denial
        orgA, makerA, apsA = provision(admin, required=1)
        orgB, makerB, apsB = provision(admin, required=1)
        s, wf = admin.create_workflow(org_id=orgA, entity_id="pout_xorg",
                                      creator_id=makerA["actor_id"])
        s1, b1 = ActorClient(ebase, apsB[0]["token"]).approve(wf["id"])  # B on A
        rec("cross_org_denial",
            s1 == 403 and b1.get("error") == "cross_org",
            f"foreign_approve={s1}/{b1.get('error')}")

        # 11. maker_checker_separation (maker WITH approver role still blocked)
        org = admin.create_org("org-sep")["org_id"]
        dual = admin.create_actor(org, "maker+approver", ["requester", "approver"])
        admin.set_policy(org, required_approvals=1, separation=1,
                         eligible_approvers=[], expiry_seconds=3600)
        s, wf = admin.create_workflow(org_id=org, entity_id="pout_sep",
                                      creator_id=dual["actor_id"])
        s1, b1 = ActorClient(ebase, dual["token"]).approve(wf["id"])
        rec("maker_checker_separation",
            s1 == 403 and b1.get("error") == "separation_violation",
            f"self_approve={s1}/{b1.get('error')}")

        # 12. callback_identity (forged/absent auth rejected by receiver)
        rF = _req("POST", f"{sbase}/callback/approve/pout_forge",
                  headers={"Idempotency-Key": "forge1"}, body={})  # no basic auth
        rG = _req("POST", f"{sbase}/callback/approve/pout_good",
                  headers={"Idempotency-Key": "good1"}, body={}, basic=(CB_USER, CB_PASS))
        rec("callback_identity",
            rF[0] == 401 and rG[0] == 200,
            f"forged={rF[0]} authed={rG[0]}")

    finally:
        eng.stop(); sink.stop()

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    out = {
        "milestone": "M5",
        "suite": "workflow-scenarios",
        "engine": "workflow-engine (high-fidelity reconstruction)",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "passed": passed, "total": total,
        "all_passed": passed == total,
        "results": results,
    }
    dest = REPO / "reports" / "implementation" / "m5-workflow-scenarios.json"
    dest.write_text(json.dumps(out, indent=2))
    print(f"\n{passed}/{total} scenarios passed -> {dest}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(run())
