#!/usr/bin/env python3
"""M11 resource measurement for a twin instance: host, VM sizing, container CPU/memory (docker stats), disk (docker system df +
VM root), image sizes (factory cache), and the recorded build/boot/journey timings. Remote compute is recorded as
NOT measured with the reason (none was used: everything ran on this machine).

  python3 scripts/m11/measure_resources.py <instance_id> [--label <text>]
Output: reports/implementation/m11/resources-<instance_id>.json (and appends a row to resources.md)
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from twinfactory.factory import Factory  # noqa: E402


def sh(cmd, env=None, timeout=120):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:  # noqa: BLE001
        return None, "", str(e)


def main():
    iid = sys.argv[1]
    label = sys.argv[sys.argv.index("--label") + 1] if "--label" in sys.argv else ""
    f = Factory(); m = f.manifest(iid)
    env = dict(os.environ, DOCKER_HOST="unix://%s/.colima/%s/docker.sock" % (Path.home(), m["backend"]["profile"]))
    doc = {"kind": "m11_resources", "instance_id": iid, "label": label, "measured_at": datetime.now(timezone.utc).isoformat(),
           "placement": "local (this machine)", "remote": {"used": False, "reason": "remote compute not available in this session (gcloud needs an interactive re-auth); every service fits the local machine"},
           "host": {}, "vm": {}, "containers": {}, "disk": {}, "images": {}, "timings": m.get("timings", {})}
    rc, out, _ = sh(["sysctl", "-n", "hw.ncpu", "hw.memsize"])
    parts = out.split()
    doc["host"] = {"cpus": int(parts[0]) if parts else None, "memory_gib": round(int(parts[1]) / 2**30, 1) if len(parts) > 1 else None, "os": sh(["sw_vers", "-productVersion"])[1].strip()}
    rc, out, _ = sh(["colima", "list", "--json"])
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("name") == m["backend"]["profile"]:
            doc["vm"] = {k: d.get(k) for k in ("name", "status", "arch", "cpus", "runtime")}
            doc["vm"]["memory_gib"] = round((d.get("memory") or 0) / 2**30, 1)     # colima list --json reports bytes
            doc["vm"]["disk_gib"] = round((d.get("disk") or 0) / 2**30, 1)
    doc["vm"]["sizing"] = m.get("sizing")
    rc, out, _ = sh(["ps", "-A", "-o", "%cpu=,rss=,comm="])
    vmproc = [l for l in out.splitlines() if "colima" in l.lower() or "limactl" in l.lower() or "qemu" in l.lower() or "vz" in l.lower()]
    doc["vm"]["host_process_rss_mib"] = round(sum(float(l.split()[1]) for l in vmproc if len(l.split()) > 1) / 1024, 1) if vmproc else None
    rc, out, _ = sh(["docker", "stats", "--no-stream", "--format", "{{json .}}"], env=env, timeout=180)
    rows = [json.loads(l) for l in out.splitlines() if l.strip().startswith("{")]

    def mib(s):
        s = s.strip()
        mm = re.match(r"([\d.]+)\s*([KMG]i?B)", s)
        if not mm:
            return 0.0
        v, u = float(mm.group(1)), mm.group(2)
        return v * {"KiB": 1 / 1024, "KB": 1 / 1024, "MiB": 1, "MB": 1, "GiB": 1024, "GB": 1024}[u]
    stats = []
    for r in rows:
        used = r.get("MemUsage", "").split("/")[0]
        try:
            cpu = float((r.get("CPUPerc") or "0%").rstrip("%"))
        except ValueError:
            cpu = 0.0          # a container that is not running reports "--"
        stats.append({"name": r.get("Name"), "cpu_pct": cpu, "mem_mib": round(mib(used), 1)})
    stats.sort(key=lambda x: -x["mem_mib"])
    doc["containers"] = {"count": len(stats), "cpu_pct_sum": round(sum(s["cpu_pct"] for s in stats), 1), "mem_mib_sum": round(sum(s["mem_mib"] for s in stats), 1),
                         "top_memory": stats[:12],
                         "trust_path": [s for s in stats if any(k in (s["name"] or "") for k in ("edge-kong", "shield", "banking-accounts", "workflows", "cadence", "postgres-kong", "mysql-bas", "mysql-shield", "mysql-workflows", "redis-shield"))]}
    rc, out, _ = sh(["docker", "system", "df", "--format", "{{json .}}"], env=env)
    doc["disk"]["docker_system_df"] = [json.loads(l) for l in out.splitlines() if l.strip().startswith("{")]
    for mount_label, path in (("vm_root", "/"), ("vm_docker_data", "/var/lib/docker")):
        rc, out, _ = sh(["colima", "ssh", "-p", m["backend"]["profile"], "--", "df", "-B1", path], timeout=60)
        lines = out.strip().splitlines()
        if len(lines) >= 2:
            p = lines[-1].split()
            doc["disk"][mount_label] = {"mount": p[-1], "size_gib": round(int(p[1]) / 2**30, 1), "used_gib": round(int(p[2]) / 2**30, 1), "avail_gib": round(int(p[3]) / 2**30, 1)}
    idx = json.loads((Path.home() / ".twin-factory" / "images" / "index.json").read_text())["images"]
    sizes = {}
    for svc, rec in (m.get("image_records") or {}).items():
        e = idx.get(rec.get("image"), {})
        sizes[rec.get("image")] = e.get("size_bytes")
    doc["images"] = {"count": len(sizes), "total_gib": round(sum(v or 0 for v in sizes.values()) / 2**30, 2),
                     "trust_path_images": {k: round((v or 0) / 2**20) for k, v in sizes.items() if any(x in k for x in ("edge-kong", "shield", "banking-accounts", "workflows", "cadence", "kong"))}}
    out = REPO / "reports" / "implementation" / "m11"
    out.mkdir(parents=True, exist_ok=True)
    (out / ("resources-%s.json" % iid)).write_text(json.dumps(doc, indent=1))
    md = out / "resources.md"
    if not md.is_file():
        md.write_text("# M11 measured resources (local machine; no remote compute used)\n\n| instance | label | VM cpus/mem/disk | containers | CPU% sum | mem MiB sum | VM root used GiB | images GiB | build s | boot s |\n|---|---|---|---|---|---|---|---|---|---|\n")
    with md.open("a") as fh:
        fh.write("| %s | %s | %s/%s/%s | %d | %s | %s | %s | %s | %s | %s |\n" % (
            iid, label, doc["vm"].get("cpus"), doc["vm"].get("memory_gib"), doc["vm"].get("disk_gib"), doc["containers"]["count"], doc["containers"]["cpu_pct_sum"], doc["containers"]["mem_mib_sum"],
            (doc["disk"].get("vm_docker_data") or doc["disk"].get("vm_root") or {}).get("used_gib"), doc["images"]["total_gib"], doc["timings"].get("build_secs"), doc["timings"].get("boot_secs")))
    print(json.dumps({k: doc[k] for k in ("containers", "disk", "images")}, indent=1)[:1500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
