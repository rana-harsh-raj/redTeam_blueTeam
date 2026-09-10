#!/usr/bin/env python3
"""M11 clean-checkout reproduction: clone the current HEAD into a temporary directory and provision a REAL-variant twin
from it with the factory (create -> build -> start -> health), recording the outcome in
reports/implementation/m11/clean-checkout.json. The factory image cache (~/.twin-factory/images) is shared by design
(content-addressed, immutable ids); everything else -- config, secrets, seeds, Kong provisioning, migrations -- is
produced from the fresh checkout. The instance is destroyed afterwards unless --keep.

  python3 scripts/m11/clean_checkout.py [--profile critical-payouts] [--keep]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "reports" / "implementation" / "m11" / "clean-checkout.json"


def sh(cmd, cwd, timeout=3600, env=None):
    t0 = time.time()
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    return {"cmd": " ".join(str(c) for c in cmd), "rc": r.returncode, "secs": round(time.time() - t0, 1), "tail": (r.stdout + r.stderr)[-600:], "_stdout": r.stdout}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="critical-payouts"); ap.add_argument("--keep", action="store_true"); ap.add_argument("--id", default="m11-clean")
    a = ap.parse_args()
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO).stdout.strip()
    tmp = Path(tempfile.mkdtemp(prefix="m11-clean-"))
    doc = {"kind": "m11_clean_checkout", "started_at": datetime.now(timezone.utc).isoformat(), "head": head, "checkout": str(tmp), "profile": a.profile, "instance_id": a.id, "steps": [], "ok": False}
    steps = doc["steps"]
    steps.append(sh(["git", "clone", "--quiet", "--no-local", str(REPO), str(tmp / "repo")], cwd=REPO))
    co = tmp / "repo"
    steps.append(sh(["git", "checkout", "--quiet", head], cwd=co))
    tracked_secrets = subprocess.run(["git", "ls-files", "ENV2_COMPOSE/secrets"], capture_output=True, text=True, cwd=co).stdout.split()
    doc["tracked_secret_values"] = [p for p in tracked_secrets if p.endswith(".txt")]
    env = dict(os.environ, PYTHONPATH=str(co))
    py = sys.executable
    steps.append(sh([py, "-m", "twinfactory", "create", a.id, "--profile", a.profile, "--seed", "m11clean", "--trust-path", "real", "--force"], cwd=co, env=env))
    if steps[-1]["rc"] == 0:
        steps.append(sh([py, "-m", "twinfactory", "build", a.id], cwd=co, env=env, timeout=5400))
    if steps[-1]["rc"] == 0:
        steps.append(sh([py, "-m", "twinfactory", "start", a.id], cwd=co, env=env, timeout=5400))
    if steps[-1]["rc"] == 0:
        h = sh([py, "-m", "twinfactory", "health", a.id], cwd=co, env=env)
        full = h.pop("_stdout", "")
        steps.append(h)
        try:
            hd = json.loads(full[full.index("{"):]) if "{" in full else {}
        except ValueError:
            hd = {}
        doc["healthy"] = hd.get("healthy")
        doc["state"] = hd.get("state")
        doc["trust_path"] = "real"
        smoke = sh([py, "-m", "twinfactory", "journeys", a.id, "--only", "journey:shared-payouts/success,journey:trust-path/api_key_auth"], cwd=co, env=env, timeout=3600)
        steps.append(smoke)
        doc["smoke_rc"] = smoke["rc"]
        doc["ok"] = bool(hd.get("healthy")) and smoke["rc"] == 0
    for s in steps:
        s.pop("_stdout", None)
    doc["timings"] = {s["cmd"].split(" ")[-1] if "twinfactory" not in s["cmd"] else s["cmd"].split("twinfactory ")[1].split(" ")[0]: s["secs"] for s in steps}
    doc["secs"] = round(sum(s["secs"] for s in steps), 1)
    doc["note"] = "factory image cache shared (content-addressed); config/secrets/seeds/Kong provisioning/migrations produced from the fresh checkout"
    if not a.keep:
        steps.append(sh([py, "-m", "twinfactory", "destroy", a.id, "--purge"], cwd=co, env=env, timeout=1800))
    doc["finished_at"] = datetime.now(timezone.utc).isoformat()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=1))
    print(json.dumps({k: doc.get(k) for k in ("ok", "state", "healthy", "smoke_rc", "secs", "checkout")}))
    return 0 if doc["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
