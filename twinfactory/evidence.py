"""Collect committed M9 evidence from the factory home: instance manifests/runtime records/profiles/events (no secrets
are ever in these), the isolation proof, host limits and measurements -> reports/implementation/m9/."""
import json
import platform
import shutil
import subprocess
from pathlib import Path

from . import paths
from .util import sh, now, write_json, read_json

OUT = paths.IMPL / "m9"


def host_limits():
    def q(*a):
        r = sh(list(a), check=False, timeout=30)
        return (r["stdout"] or "").strip()
    mem = q("sysctl", "-n", "hw.memsize"); ncpu = q("sysctl", "-n", "hw.ncpu"); model = q("sysctl", "-n", "hw.model")
    df = q("df", "-k", str(Path.home()))
    free_kb = None
    for line in df.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4:
            free_kb = int(parts[3])
    vms = []
    r = sh(["colima", "list", "--json"], check=False, timeout=60)
    for line in (r["stdout"] or "").splitlines():
        if line.strip().startswith("{"):
            try:
                v = json.loads(line); vms.append({k: v.get(k) for k in ("name", "status", "cpus", "memory", "disk", "arch", "runtime")})
            except ValueError:
                pass
    return {"at": now(), "platform": platform.platform(), "model": model, "cpu_count": int(ncpu or 0), "memory_bytes": int(mem or 0),
            "home_free_kib": free_kb, "colima_version": q("colima", "version").splitlines()[0] if q("colima", "version") else None,
            "docker_compose_version": q("docker", "compose", "version"), "vms": vms}


def collect(factory, instance_ids, proof=None):
    OUT.mkdir(parents=True, exist_ok=True)
    rec = {"collected_at": now(), "instances": {}}
    for iid in instance_ids:
        d = factory.idir(iid)
        dst = OUT / "instances" / iid
        dst.mkdir(parents=True, exist_ok=True)
        for name in ("manifest.json", "runtime_instance.json", "profile.json", "events.jsonl", "workspace.json"):
            if (d / name).is_file():
                shutil.copy2(d / name, dst / name)
        ev = d / "evidence"
        if ev.is_dir():
            (dst / "evidence").mkdir(exist_ok=True)
            for p in sorted(ev.glob("*.json")):
                if p.name == "secrets-manifest.json":
                    continue   # names + sha256 of credential files: kept in the instance directory only
                shutil.copy2(p, dst / "evidence" / p.name)
        logs = d / "logs"
        if logs.is_dir():
            (dst / "logs").mkdir(exist_ok=True)
            for p in sorted(logs.glob("*.json")):
                shutil.copy2(p, dst / "logs" / p.name)
            steps = []
            for p in sorted(logs.glob("*.log")):
                head = p.read_text(errors="replace").splitlines()[:2]
                # only the step name and rc/timing line: command lines can carry datastore credentials (mysql -p...)
                steps.append({"log": p.name, "rc_line": head[1][:80] if len(head) > 1 else None})
            write_json(dst / "logs" / "INDEX.json", steps)
        m = read_json(d / "manifest.json", {})
        rec["instances"][iid] = {"state": m.get("state"), "profile": m.get("profile"), "runtime_instance_id": m.get("runtime_instance_id"),
                                 "architecture_snapshot_id": m.get("architecture_snapshot_id"), "timings": m.get("timings")}
    if proof and Path(proof).is_file():
        shutil.copy2(proof, OUT / "isolation-proof.json")
    write_json(OUT / "host-limits.json", host_limits())
    write_json(OUT / "registry-snapshot.json", factory.registry.load())
    write_json(OUT / "image-cache-index.json", factory.cache.index())
    write_json(OUT / "COLLECTION.json", rec)
    return rec
