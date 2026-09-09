"""Worker subprocess entrypoint (live per-twin process isolation).

  python3 -m controlplane.worker_main <campaign_id> <worker_id>

Reads its task (with a precomputed archkit packet) from
workers/<worker_id>/task.json and builds a REAL typed broker whose twin
coordinates come entirely from this process's environment (DOCKER_HOST,
KONG_LITE_HOST_URL, ARENA_* — injected by the engine, exactly like the M9 journey
runner). The attacker merchant is provisioned ONCE per twin per campaign and
cached under twins/<twin>/attacker.json (flock-guarded), then reused by every
worker on that twin.

When CONTROLPLANE_FAKE_MODEL=1 the deterministic model policy drives the run
(model-free "declared action budget"); the broker is still real, so the twin
receives real requests. CONTROLPLANE_FAKE_BROKER=1 (plumbing tests only) swaps in
an in-process fake broker so no twin is required.
"""
import fcntl
import json
import os
import sys
from pathlib import Path


def _real_broker(control, manifest, task):
    twin = task.get("twin") or os.environ.get("TWIN_INSTANCE_ID") or "twin"
    tdir = control.workers_dir.parent / "twins" / twin
    tdir.mkdir(parents=True, exist_ok=True)
    cache = tdir / "attacker.json"
    lock = tdir / ".prov.lock"
    from red_loop import provisioner
    from red_loop.broker import Broker
    with open(lock, "a+") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        try:
            if cache.exists():
                attacker = json.loads(cache.read_text())
            else:
                attacker = provisioner.provision_funded_merchant(
                    manifest["campaign_id"] + "-" + twin, role="attacker")
                cache.write_text(json.dumps(attacker))
                control.event("attacker_provisioned", twin=twin,
                              merchant_id=attacker.get("merchant_id"), key_id=attacker.get("key_id"))
        finally:
            fcntl.flock(lk.fileno(), fcntl.LOCK_UN)
    return Broker(attacker, action_sink=lambda rec: None)


def main(argv=None):
    argv = argv or sys.argv[1:]
    campaign_id, worker_id = argv[0], argv[1]
    from .store import ControlStore
    from .models import ModelRouter
    control = ControlStore(campaign_id)
    manifest = control.read_manifest()
    payload = json.loads((control.workers_dir / worker_id / "task.json").read_text())
    task = payload["task"]
    packet = payload.get("packet")
    router = ModelRouter(manifest["model_pool"], store=control)

    if os.environ.get("CONTROLPLANE_FAKE_MODEL") == "1":
        from .fakes import fake_client_factory
        find = set((os.environ.get("CONTROLPLANE_FAKE_FINDERS") or "").split(",")) - {""}
        client_factory = fake_client_factory(find_candidate_for=find or None)
    else:
        from red_loop import llm
        def client_factory(model):
            return llm.ChatClient(model)

    if os.environ.get("CONTROLPLANE_FAKE_BROKER") == "1":
        from .fakes import FakeBroker
        broker = FakeBroker(merchant_id="acc_" + str(task.get("twin")))
    else:
        broker = _real_broker(control, manifest, task)

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
