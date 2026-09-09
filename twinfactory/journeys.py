"""Instance-aware journey execution: runs the M6/M7 journey runner (RED_LOOP/m6/journeys/run.py) against ONE instance,
with every arena coordinate supplied through the environment (no default arena is ever addressed) and the output written
under the instance's own journeys/ directory. Evidence attribution is verified: the runner's `instance` block must name
this instance, its compose project and its Docker host."""
import json
import os
import re
from pathlib import Path

from . import paths
from .boot import load_s2p_secrets
from .util import sh, now, stamp, write_json, read_json, sha256_file


def journey_env(factory, m):
    denv = factory.denv(m)
    env2 = Path(m["env2_root"])
    e = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "SHELL", "COLIMA_HOME")}
    e.update(denv)
    e.update({
        "ARENA_ENV2_ROOT": str(env2), "ARENA_COMPOSE_PROJECT": m["compose_project"], "COMPOSE_PROJECT_NAME": m["compose_project"],
        "ARENA_SUFFIX": m["arena_suffix"], "ARENA_NETWORK": m["arena_network"], "ARENA_S2P_NETWORK": m["s2p_network"],
        "ARENA_KONG_SECRETS_VOLUME": "rzp-arena-secrets-kong" + m["arena_suffix"],
        "KONG_LITE_HOST_URL": "http://127.0.0.1:%d" % m["kong_host_port"], "KONG_LITE_HOST_PORT": str(m["kong_host_port"]),
        "TWIN_RUNS_DIR": str(factory.idir(m["instance_id"]) / "journeys"), "TWIN_REPORTS_DIR": str(factory.idir(m["instance_id"]) / "journeys" / "reports"),
        "TWIN_INSTANCE_ID": m["instance_id"], "TWIN_RUNTIME_INSTANCE_ID": m.get("runtime_instance_id") or "",
        "M6_ALLOW_RESTART": "1", "PYTHONDONTWRITEBYTECODE": "1",
    })
    from .boot import trust_env
    e.update(trust_env(m.get("trust_path") == "real"))
    s2p = load_s2p_secrets(env2)
    if s2p.get("S2P_MYSQL_PASSWORD"):
        e["S2P_MYSQL_USER"] = "s2p"
        e["S2P_MYSQL_PASSWORD"] = s2p["S2P_MYSQL_PASSWORD"]
    return e


def run_journeys(factory, m, only=None, family=None, timeout=7200):
    run_dir = factory.idir(m["instance_id"]) / "journeys" / ("run-" + stamp())
    run_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["python3", str(paths.JOURNEY_RUNNER), "--run-dir", str(run_dir), "--instance-id", m["instance_id"]]
    if only:
        cmd += ["--only", ",".join(only) if isinstance(only, (list, tuple)) else only]
    if family:
        cmd += ["--family", ",".join(family) if isinstance(family, (list, tuple)) else family]
    env = journey_env(factory, m)
    rec = sh(cmd, cwd=paths.REPO, env=env, check=False, timeout=timeout)
    (run_dir / "runner.log").write_text(rec["stdout"] + "\n--- stderr ---\n" + rec["stderr"])
    summary = read_json(run_dir / "summary.json", {})
    inst = summary.get("instance") or {}
    docker_host = env.get("DOCKER_HOST")
    attribution = {
        "instance_id_matches": inst.get("instance_id") == m["instance_id"],
        "compose_project_matches": inst.get("compose_project") == m["compose_project"],
        "docker_host_matches": (inst.get("docker_host") or None) == (docker_host or None),
        "runtime_instance_id_matches": (inst.get("runtime_instance_id") or None) == (m.get("runtime_instance_id") or None),
        "run_dir_inside_instance": str(run_dir).startswith(str(factory.idir(m["instance_id"]))),
        "canonical_report_untouched": True,
    }
    canonical = paths.IMPL / "m6-journeys.json"
    if canonical.is_file():
        attribution["canonical_report_untouched"] = (read_json(canonical, {}).get("run_dir") or "") != str(run_dir)
    js = summary.get("journeys", [])
    out = {"instance_id": m["instance_id"], "runtime_instance_id": m.get("runtime_instance_id"), "run_dir": str(run_dir), "rc": rec["rc"], "secs": rec["secs"],
           "selection": {"only": only, "family": family}, "summary": summary.get("summary"), "instance_block": inst,
           "journeys": [{k: j.get(k) for k in ("id", "result", "checks_passed", "checks_total", "duration_s", "merchant_id", "failed_checks", "missing_dependency", "expected_failure_ref", "evidence_path")} for j in js],
           "attribution": attribution, "attributed": all(attribution.values()), "fingerprint_boot_id": (summary.get("fingerprint") or {}).get("boot_id"),
           "boot_id_matches_instance": (summary.get("fingerprint") or {}).get("boot_id") == (m.get("boot") or {}).get("boot_id"), "at": now()}
    write_json(run_dir / "instance-journeys.json", out)
    write_json(factory.idir(m["instance_id"]) / "evidence" / ("journeys-%s.json" % run_dir.name), out)
    m.setdefault("journey_runs", []).append({"run_dir": str(run_dir), "summary": out["summary"], "attributed": out["attributed"], "at": out["at"]})
    factory.save_manifest(m)
    factory.registry.event(m["instance_id"], "journeys", run_dir=str(run_dir), passed=(out["summary"] or {}).get("pass"), failed=(out["summary"] or {}).get("fail"))
    return out
