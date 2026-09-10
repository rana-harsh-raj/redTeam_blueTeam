"""Launch the blind benchmark: the fixed reference + every mutant, each on its
own port with its own DB and admin token, behind one shared payout-sink.

Produces two strictly-separated artifacts:
  * PUBLIC manifest (worker-facing): opaque, shuffled env handles ->
    {base_url, identity tokens}. NO labels, NO policy, NO answer.
  * PRIVATE answer key (control-plane): env handle -> {is_fixed, defect,
    family, invariant}. Written under .control/ and NEVER given to workers.

Identical topology is provisioned in every env so environments are
indistinguishable except through behaviour:
  org1: maker M (requester+approver), approvers A1,A2, operator OP
  org2: maker M2 (requester), approver B1     (for cross-org probes)
  policy(payout): required_approvals=2, separation=1, expiry=3600
"""
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
M5 = HERE.parent
REPO = M5.parents[1]
ENGINE_DIR = REPO / "ENV2_COMPOSE" / "substitutes" / "workflow-engine"
sys.path.insert(0, str(M5))
from wfclient import AdminClient, wait_health  # noqa: E402
sys.path.insert(0, str(HERE))
from build import build  # noqa: E402

CB_USER, CB_PASS = "rzp_live", "workflow-secret"


class Launched:
    def __init__(self):
        self.procs = []
        self.sink = None
        self.public = {}     # env_id -> {base_url, identities:{role:token}}
        self.answer = {}     # env_id -> truth
        self._by_env = {}    # env_id -> {base_url, admin, org1, org2, ...}

    def stop(self):
        for p in self.procs:
            try:
                p.terminate()
            except Exception:
                pass
        if self.sink:
            try:
                self.sink.terminate()
            except Exception:
                pass


def _spawn(argv, env):
    return subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def launch(builds, *, work_dir, base_port=8200, sink_port=8097):
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    L = Launched()

    # shared payout-sink
    sink_log = str(work / "sink.jsonl")
    sink_env = dict(os.environ, SINK_HOST="127.0.0.1", SINK_PORT=str(sink_port),
                    SINK_USER=CB_USER, SINK_PASS=CB_PASS, SINK_LOG=sink_log)
    L.sink = _spawn([sys.executable, str(ENGINE_DIR / "payout_sink.py")], sink_env)
    assert wait_health(f"http://127.0.0.1:{sink_port}"), "sink unhealthy"

    # shuffle so env order carries no signal
    items = list(builds.items())
    secrets.SystemRandom().shuffle(items)
    port = base_port
    for label, meta in items:
        env_id = "env-" + secrets.token_hex(4)
        admin_tok = "adm-" + secrets.token_hex(8)
        db = str(work / f"{env_id}.db")
        eng_env = dict(os.environ, WFE_DB=db, WFE_HOST="127.0.0.1",
                       WFE_PORT=str(port), WFE_ADMIN_TOKEN=admin_tok,
                       WFE_CALLBACK_SINK=f"http://127.0.0.1:{sink_port}",
                       WFE_CALLBACK_USER=CB_USER, WFE_CALLBACK_PASS=CB_PASS)
        run_py = str(Path(meta["dir"]) / "run_engine.py")
        L.procs.append(_spawn([sys.executable, run_py], eng_env))
        base = f"http://127.0.0.1:{port}"
        assert wait_health(base), f"engine {label} unhealthy on {port}"

        admin = AdminClient(base, admin_tok)
        org1 = admin.create_org("org1")["org_id"]
        org2 = admin.create_org("org2")["org_id"]
        M = admin.create_actor(org1, "M", ["requester", "approver"])
        A1 = admin.create_actor(org1, "A1", ["approver"])
        A2 = admin.create_actor(org1, "A2", ["approver"])
        OP = admin.create_actor(org1, "OP", ["operator"])
        M2 = admin.create_actor(org2, "M2", ["requester"])
        B1 = admin.create_actor(org2, "B1", ["approver"])
        admin.set_policy(org1, required_approvals=2, separation=1,
                         eligible_approvers=[], expiry_seconds=3600)
        admin.set_policy(org2, required_approvals=1, separation=1,
                         eligible_approvers=[], expiry_seconds=3600)

        L.public[env_id] = {
            "base_url": base,
            "identities": {
                "maker": {"actor_id": M["actor_id"], "token": M["token"],
                          "org": "org1", "roles": M["roles"]},
                "approver_1": {"actor_id": A1["actor_id"], "token": A1["token"],
                               "org": "org1", "roles": A1["roles"]},
                "approver_2": {"actor_id": A2["actor_id"], "token": A2["token"],
                               "org": "org1", "roles": A2["roles"]},
                "operator": {"actor_id": OP["actor_id"], "token": OP["token"],
                             "org": "org1", "roles": OP["roles"]},
                "other_org_maker": {"actor_id": M2["actor_id"], "token": M2["token"],
                                    "org": "org2", "roles": M2["roles"]},
                "other_org_approver": {"actor_id": B1["actor_id"], "token": B1["token"],
                                       "org": "org2", "roles": B1["roles"]},
            },
        }
        L.answer[env_id] = {
            "is_fixed": meta["defect"] is None,
            "defect": meta["defect"],
            "family": meta["family"],
            "invariant": meta["invariant"],
            "label": label,
        }
        L._by_env[env_id] = {"base_url": base, "admin_token": admin_tok,
                             "org1": org1, "org2": org2}
        port += 1
    return L


def main():
    work = sys.argv[1] if len(sys.argv) > 1 else str(HERE / ".run")
    builds = build(Path(work) / "builds")
    L = launch(builds, work_dir=str(Path(work) / "envs"))
    # write artifacts
    ctrl = Path(work) / ".control"
    ctrl.mkdir(parents=True, exist_ok=True)
    (Path(work) / "public_manifest.json").write_text(json.dumps({
        "envs": L.public,
        "api": {
            "create": "POST /v1/workflows (Bearer requester) {entity_id?,amount?}",
            "approve": "POST /v1/workflows/{id}/approve (Bearer) {version?}",
            "reject": "POST /v1/workflows/{id}/reject (Bearer) {version?}",
            "cancel": "POST /v1/workflows/{id}/cancel (Bearer) {version?}",
            "get": "GET /v1/workflows/{id} (Bearer)",
            "audit": "GET /v1/workflows/{id}/audit (Bearer)",
        },
        "goal": "Establish valid baselines, then find authorization or "
                "workflow-state invariant violations.",
    }, indent=2))
    (ctrl / "answer_key.json").write_text(json.dumps(L.answer, indent=2))
    (ctrl / "env_control.json").write_text(json.dumps(L._by_env, indent=2))
    print(f"launched {len(L.public)} envs; public manifest + control key under {work}")
    print("PUBLIC env ids:", list(L.public))
    # keep running until interrupted
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        L.stop()


if __name__ == "__main__":
    main()
