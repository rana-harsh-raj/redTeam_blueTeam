"""Worker subprocess entrypoint (live per-twin process isolation).

  python3 -m controlplane.worker_main <campaign_id> <worker_id>

Reads its task (with a precomputed archkit packet) from
workers/<worker_id>/task.json, builds a REAL typed broker whose twin coordinates
come entirely from this process's environment (DOCKER_HOST, KONG_LITE_HOST_URL,
ARENA_* — injected by the engine, exactly like the M9 journey runner), and runs
the bounded task, writing to the shared durable ledgers under a cross-process
lock. When CONTROLPLANE_FAKE_MODEL=1 it uses the deterministic model policy
(model-free "declared action budget" run) instead of the gateway.
"""
import json
import os
import sys
from pathlib import Path


def _build_broker(manifest):
    # attacker identity is the ordinary merchant provisioned on this twin; the
    # RED_LOOP broker reads the twin's kong URL from the environment.
    from red_loop import provisioner
    campaign_id = manifest["campaign_id"]
    attacker = provisioner.provision_funded_merchant(campaign_id, role="attacker")
    from red_loop.broker import Broker
    return Broker(attacker, action_sink=lambda rec: None)


def main(argv=None):
    argv = argv or sys.argv[1:]
    campaign_id, worker_id = argv[0], argv[1]
    from .store import ControlStore
    from .models import ModelRouter
    control = ControlStore(campaign_id)
    manifest = control.read_manifest()
    task_path = control.workers_dir / worker_id / "task.json"
    payload = json.loads(task_path.read_text())
    task = payload["task"]
    packet = payload.get("packet")
    router = ModelRouter(manifest["model_pool"], store=control)

    if os.environ.get("CONTROLPLANE_FAKE_MODEL") == "1":
        from .fakes import fake_client_factory
        find = set((os.environ.get("CONTROLPLANE_FAKE_FINDERS") or "").split(",")) - {""}
        client_factory = fake_client_factory(find_candidate_for=find or None)
        broker_fx = json.loads(os.environ.get("CONTROLPLANE_FAKE_BROKER") or "{}")
        from .fakes import FakeBroker
        broker = FakeBroker(merchant_id="acc_" + task.get("twin", "twin"),
                            fixtures={("GET", k): v for k, v in broker_fx.items()})
    else:
        from red_loop import llm
        def client_factory(model):
            return llm.ChatClient(model)
        broker = _build_broker(manifest)

    from . import worker as worker_mod
    from .twins import TwinHandle
    handle = TwinHandle(task.get("twin"), task.get("runtime_instance_id"),
                        dict(os.environ), os.environ.get("KONG_LITE_HOST_URL", ""))
    result = worker_mod.run_task(control, task, manifest, None, broker, client_factory,
                                 router, handle, worker_id=worker_id,
                                 cell_id=task.get("cell_id"), packet=packet)
    (control.workers_dir / worker_id / "result.json").write_text(json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
