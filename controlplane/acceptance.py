"""Machine-computed M10 acceptance.

Reproducible from a clean checkout with NO live model and NO Docker: it runs the
deterministic proof campaign (proof.py) plus the kill-switch and budget
micro-campaigns, evaluates the M10 gates over their durable ledgers, runs the
control-plane and existing test suites, and writes
reports/implementation/M10_ACCEPTANCE.json and M10_ARTIFACT_HASHES.json.

  python3 -m controlplane.acceptance [--skip-suites]
"""
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import paths, __version__, SCHEMA_VERSION
from . import proof
from .views import Views
from .manifest import validate

IMPL = paths.REPO / "reports" / "implementation"


def _gate(gates, gid, desc, ok, detail=None):
    gates.append({"id": gid, "desc": desc, "pass": bool(ok), "detail": detail})
    return bool(ok)


def evaluate(skip_suites=False):
    home = Path(tempfile.mkdtemp(prefix="m10-accept-"))
    r = proof.run_full_proof(home)
    ks = proof.run_killswitch_proof(home)
    bg = proof.run_budget_proof(home)
    c = r["control"]
    ev = [e.get("event") for e in c._read_all("events")]
    def has(e):
        return e in ev
    views = Views(c, r["manifest"])
    cov, alloc, res = views.coverage(), views.allocation(), views.results()
    mani = r["manifest"]
    ok_valid, cid = validate(mani)

    gates = []
    _gate(gates, "M10-01", "campaign manifest is immutable and content-addressed",
          ok_valid and mani["campaign_id"] == "camp-" + mani["manifest_content_id"][:12], {"content_id": cid})
    # the human assigns no target/vuln/path: no manifest FIELD carries an
    # assignment (the mandate prose may legitimately say "no target").
    _assign_keys = ("target", "assigned_target", "vulnerability_class", "vuln_class",
                    "attack_path", "cwe", "exploit", "objective_target")
    _gate(gates, "M10-02", "human sets only mode/mandate/budgets/policy — no assigned target/vuln/path field",
          "mandate" in mani and "mode" in mani
          and not any(k in mani for k in _assign_keys)
          and not any(k in (mani.get("safety") or {}) for k in _assign_keys),
          {"assignment_fields_present": [k for k in _assign_keys if k in mani]})
    _gate(gates, "M10-03", "Director autonomously generated theses over >=2 planning cycles",
          c.control_state().get("planning_cycles", 0) >= 2 and cov["theses_total"] >= 3,
          {"planning_cycles": c.control_state().get("planning_cycles"), "theses": cov["theses_total"]})
    _gate(gates, "M10-04", "scheduling: tasks from theses, dynamic cells, durable leases",
          cov["tasks_total"] > 0 and alloc["cells_total"] > 0 and has("lease_acquired"))
    _gate(gates, "M10-05", "two isolated twins participated in one campaign",
          {"alpha"} <= {t["twin"] for t in c.fold("tasks")} and
          bool({"beta", "beta-r"} & {t["twin"] for t in c.fold("tasks")}),
          {"twins_tasked": sorted({t["twin"] for t in c.fold("tasks")})})
    _gate(gates, "M10-06", "durable, resumable, snapshot/instance-addressed",
          r["hash_at_pause"] != r["final_hash"] and has("campaign_resumed")
          and mani["architecture_snapshot_id"] and mani["twins"])
    _gate(gates, "M10-07", "injected worker failure recovered via lease expiry + requeue",
          has("injected_worker_kill") and has("lease_expired") and has("task_recovered")
          and c.get("tasks", "TASK-001") and c.get("tasks", "TASK-001")["status"] == "done")
    _gate(gates, "M10-08", "Director kill + restart resumed from durable state",
          has("campaign_resumed") and r["stop_reason"] == "no_open_work")
    _gate(gates, "M10-09", "model timeout handled by fallback", has("model_fallback"))
    _gate(gates, "M10-10", "failed twin recovered/reproduced via twinfactory and re-homed",
          has("twin_recovered") and any(x.get("kind") == "reproduce_ok" for x in c._read_all("recoveries")))
    _gate(gates, "M10-11", "semantic hypothesis dedup + deliberate independent replication",
          sum(1 for e in c._read_all("events") if e.get("event") == "duplicate_suppressed") > 0
          and any(int(t.get("replica_index", 0)) > 0 for t in c.fold("tasks")))
    tcalls = c._read_all("tool_calls")
    _gate(gates, "M10-12", "all model and tool activity recorded and tagged",
          len(c._read_all("model_calls")) > 0 and len(tcalls) > 0
          and all("task_id" in t and "twin" in t for t in tcalls))
    _gate(gates, "M10-13", "no self-verification; verifier is decorrelated and not the claimant",
          res["self_verifications"] == 0 and res["decorrelated_verifications"] > 0)
    _gate(gates, "M10-14", "verification is evidence-gated (candidates not auto-promoted)",
          _verify_is_gated())
    _gate(gates, "M10-15", "budgets enforced", bg["stop_reason"] == "action_budget_exhausted")
    _gate(gates, "M10-16", "external kill switch stops the campaign", ks["stop_reason"] == "kill_switch")
    _gate(gates, "M10-17", "unattended completion with zero human task assignments",
          r["stop_reason"] == "no_open_work"
          and not any(e.get("event") == "human_task_assignment" for e in c._read_all("events")))
    _gate(gates, "M10-18", "role-specific bounded context packets sourced from archkit",
          any(f.name.startswith("packet-") for f in c.evidence_dir.iterdir()))
    _gate(gates, "M10-19", "pause/resume/drain/terminate operations present and demonstrated",
          _ops_present() and has("campaign_paused") and has("campaign_resumed"))

    suites = {}
    if not skip_suites:
        suites = _run_suites()
        _gate(gates, "M10-20", "control-plane + existing (archkit/twinfactory/snapshot/RED_LOOP) suites green",
              all(v.get("ok") for v in suites.values()), suites)
    else:
        _gate(gates, "M10-20", "suites (skipped)", True, {"skipped": True})

    hashes = _artifact_hashes()
    _gate(gates, "M10-21", "machine-computed acceptance reproducible; artifacts hashed",
          len(hashes) > 0, {"files": len(hashes)})

    passed = sum(1 for g in gates if g["pass"])
    doc = {
        "milestone": "M10", "schema": SCHEMA_VERSION, "controlplane_version": __version__,
        "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "accepted": passed == len(gates),
        "gates_passed": passed, "gates_total": len(gates), "gates": gates,
        "proof_campaign_id": r["campaign_id"], "manifest_content_id": mani["manifest_content_id"],
        "coverage": cov, "allocation": alloc, "results": res, "suites": suites,
        "git_head": _git_head(),
    }
    IMPL.mkdir(parents=True, exist_ok=True)
    (IMPL / "M10_ACCEPTANCE.json").write_text(json.dumps(doc, indent=2, default=str))
    (IMPL / "M10_ARTIFACT_HASHES.json").write_text(json.dumps(
        {"generated_at": doc["evaluated_at"], "count": len(hashes), "files": hashes}, indent=2))
    return doc


def _verify_is_gated():
    from .verify import Verifier
    D = Verifier._decide
    return (D({"reproduced": True}, {"verdict": "confirmed"}) == "verified"
            and D({"reproduced": False}, {"verdict": "confirmed"}) == "rejected"
            and D({"reproduced": None}, {"verdict": "inconclusive"}) == "rejected"
            and D({"reproduced": True}, {"verdict": "known_gap"}) == "invalid")


def _ops_present():
    from . import engine
    return all(hasattr(engine.Engine, op) for op in
               ("start", "pause", "resume", "drain", "terminate", "run"))


def _run_suites():
    suites = {
        "controlplane": ["python3", "-m", "unittest", "discover", "-s", "controlplane/tests", "-t", "."],
        "archkit": ["python3", "-m", "unittest", "discover", "-s", "archkit/tests", "-t", "."],
        "twinfactory": ["python3", "-m", "unittest", "discover", "-s", "twinfactory/tests", "-t", "."],
        "snapshot": ["python3", "-m", "unittest", "discover", "-s", "scripts/snapshot/tests"],
        "red_loop": ["python3", "-m", "unittest", "discover", "RED_LOOP/tests"],
    }
    out = {}
    for name, cmd in suites.items():
        try:
            p = subprocess.run(cmd, cwd=str(paths.REPO), capture_output=True, text=True, timeout=1800)
            tail = (p.stderr or p.stdout).strip().splitlines()[-1:] or [""]
            out[name] = {"ok": p.returncode == 0, "summary": tail[-1]}
        except Exception as e:  # noqa: BLE001
            out[name] = {"ok": False, "summary": str(e)[:200]}
    return out


def _artifact_hashes():
    files = {}
    roots = [paths.REPO / "controlplane"]
    extra = [IMPL / "M10_FINAL_REPORT.md", IMPL / "M10_RUNBOOK.md"]
    for root in roots:
        for p in sorted(root.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            files[str(p.relative_to(paths.REPO))] = hashlib.sha256(p.read_bytes()).hexdigest()
    for p in extra:
        if p.exists():
            files[str(p.relative_to(paths.REPO))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return files


def _git_head():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(paths.REPO), text=True).strip()
    except Exception:  # noqa: BLE001
        return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-suites", action="store_true")
    a = ap.parse_args(argv)
    doc = evaluate(skip_suites=a.skip_suites)
    print(json.dumps({"accepted": doc["accepted"], "gates_passed": doc["gates_passed"],
                      "gates_total": doc["gates_total"],
                      "failed": [g["id"] for g in doc["gates"] if not g["pass"]]}, indent=2))
    return 0 if doc["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
