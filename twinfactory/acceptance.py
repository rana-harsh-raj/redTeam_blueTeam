"""M9 acceptance: machine-computed gates over Git, the committed M9 evidence, the code and a live clean-checkout
provisioning of a FULL twin (colima backend).

    python3 -m twinfactory acceptance   -> reports/implementation/M9_ACCEPTANCE.json (exit 0 iff accepted)
Set TWIN_ACCEPTANCE_SKIP_LIVE=1 to skip the live clean-checkout gate (it is then recorded as FAIL, not as skipped)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import paths, __version__
from .util import sh, now, write_json, read_json, sha256_file
from .evidence import OUT as EVIDENCE

OUTP = paths.IMPL / "M9_ACCEPTANCE.json"
HASHES = paths.IMPL / "M9_ARTIFACT_HASHES.json"
PROOF = paths.IMPL / "m9-isolation-proof.json"
HISTORICAL_TAGS = {"assurance-m4.1-reproducible": "ef4ba7264df224e4b782f2671ce8b1eb3079b3d0", "assurance-m5-autonomous-discovery": "2613f383297c7c3b5c0831c26c5c7472494b6e7b",
                   "red-loop-m3.1": "3a044f83ed0eda6ead71585f07c450af2ed7978f", "red-loop-m4": "3ae177328341ec4512bdb7de8ce454ce67a35513",
                   "s2p-acceptance-closure-m7": "4505e62dce58561793429c62174c2941c97f6eb0", "twin-m6-complete-domain": "76024aed5ae8474d29bd2b0b2e48c97c429eda52",
                   "twin-m7-shared-ingress-integration": "37ac47db1e630c44e54707694aa84f44301f0b19", "twin-v1.0": "78def24eb57112c0dc39a6ae9062b1f0d1c711fb",
                   "arch-m8-snapshot-query": "a84a668"}
M7_M8_FILES = {"reports/architecture/M7_CANONICAL_SNAPSHOT.json": "d88a26342e10eea4c18971f2765ecc2e05364d7716cc113733f7d76233709651",
               "reports/implementation/M7_ACCEPTANCE.json": "13d7ef0b83512429dc8a1b59c96305b99fd2155b65cfc7d3f8a8ad5e40b203d6",
               "reports/implementation/M7_ARTIFACT_HASHES.json": "f97e1b27e5c41f5417c7c34fa7eb6ee9f4cc6fd988348a02977c380f3f1b791f"}
M8_SNAPSHOT = "5b6a5dade17eba6c0a407d3d4ba8423c8bf0b8ebeb9ea2bc3706483e04e8d141"
DOCS = ["reports/implementation/M9_FINAL_REPORT.md", "reports/implementation/M9_RUNBOOK.md", "twinfactory/SCHEMA.md"]


def git(*a):
    return subprocess.run(["git", "-C", str(paths.REPO)] + list(a), capture_output=True, text=True).stdout.strip()


def build_hashes():
    files = {}
    for p in sorted((paths.REPO / "twinfactory").rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            files[paths.rel(p)] = sha256_file(p)
    for root in (EVIDENCE,):
        for p in sorted(root.rglob("*")):
            if p.is_file():
                files[paths.rel(p)] = sha256_file(p)
    for extra in ("reports/implementation/M9_FINAL_REPORT.md", "reports/implementation/M9_RUNBOOK.md", "reports/implementation/m9-isolation-proof.json",
                  "ENV2_COMPOSE/docker-compose.twin.yml", "DOMAIN_REPLICAS/source_to_pay/runtime/source-driver/boot.go", "RED_LOOP/m6/journeys/run.py",
                  "RED_LOOP/m6/journeys/framework.py", "RED_LOOP/m6/journeys/j_s2p.py", "RED_LOOP/red_loop/config.py", "Makefile"):
        p = paths.REPO / extra
        if p.is_file():
            files[extra] = sha256_file(p)
    doc = {"algorithm": "sha256", "self_excluded": paths.rel(HASHES), "files": dict(sorted(files.items())), "count": len(files)}
    HASHES.write_text(json.dumps(doc, indent=2) + "\n")
    return doc


def verify_hashes():
    doc = json.loads(HASHES.read_text())
    bad = [r for r, h in doc["files"].items() if not (paths.REPO / r).is_file() or sha256_file(paths.REPO / r) != h]
    return {"passed": not bad, "changed_or_missing": bad, "count": len(doc["files"])}


def live_clean_checkout_gate(head):
    """Fresh git worktree of HEAD -> twinfactory (from the worktree) provisions a FULL twin on a fresh colima boundary,
    boots it, passes health + a quick journey, then destroys it. Uses the real factory home (image/input caches)."""
    if os.environ.get("TWIN_ACCEPTANCE_SKIP_LIVE") == "1":
        return {"passed": False, "skipped": True, "reason": "TWIN_ACCEPTANCE_SKIP_LIVE=1"}
    wt = Path(tempfile.mkdtemp(prefix="m9-clean-")) / "wt"
    iid = "m9-clean-" + head[:6]
    env = dict(os.environ)
    env["TWIN_FACTORY_REPO"] = str(wt)
    env["ARCHKIT_REPO"] = str(wt)
    rep = {"worktree": str(wt), "instance_id": iid, "steps": []}
    t0 = time.time()
    try:
        sh(["git", "-C", str(paths.REPO), "worktree", "add", "--detach", str(wt), head], timeout=300)
        rep["worktree_local_dir_absent"] = not (wt / ".local").exists()
        rep["worktree_generated_absent"] = not any((wt / "ENV2_COMPOSE" / "secrets").glob("*.txt")) and not (wt / "ENV2_COMPOSE" / "generated").exists()

        def step(name, args, timeout=1800, check=True):
            r = sh([sys.executable, "-m", "twinfactory"] + args, cwd=wt, env=env, check=False, timeout=timeout)
            rep["steps"].append({"step": name, "rc": r["rc"], "secs": r["secs"], "tail": (r["stdout"] or "")[-400:] + (r["stderr"] or "")[-400:]})
            if check and r["rc"] != 0:
                raise RuntimeError("%s failed rc=%s: %s" % (name, r["rc"], (r["stderr"] or "")[-800:]))
            return r
        step("destroy-stale", ["destroy", iid, "--purge"], check=False)
        step("create", ["create", iid, "--profile", "full", "--seed", "acceptance-" + head[:6], "--backend", "colima", "--force"])
        step("build", ["build", iid])
        step("start", ["start", iid], timeout=2400)
        h = step("health", ["health", iid])
        hd = json.loads(h["stdout"])
        rep["health"] = {"healthy": hd["healthy"], "containers": hd.get("containers")}
        j = step("journey", ["journeys", iid, "--only", "journey:shared-payouts/success"], timeout=2400, check=False)
        try:
            jd = json.loads(j["stdout"]); rep["journey"] = {"summary": jd["summary"], "attributed": jd["attributed"]}
        except ValueError:
            rep["journey"] = {"error": j["stderr"][-400:]}
        from .factory import Factory
        f = Factory()
        m = f.manifest(iid)
        rep["manifest"] = {k: m.get(k) for k in ("architecture_snapshot_id", "recipe_set_id", "profile", "seed", "inputs_hash", "runtime_instance_id", "state")}
        rep["manifest"]["image_count"] = len(m.get("image_digests") or {})
        rep["manifest"]["migrations_source"] = (m.get("workspace") or {}).get("migrations", {}).get("source_kind")
        rep["exec_dir_outside_worktree_and_repo"] = not m["exec_dir"].startswith(str(wt)) and not m["exec_dir"].startswith(str(paths.REPO))
        rep["timings"] = m.get("timings")
        rep["passed"] = bool(hd["healthy"] and rep["manifest"]["architecture_snapshot_id"] == M8_SNAPSHOT and rep["manifest"]["image_count"] > 0
                             and (rep.get("journey", {}).get("summary") or {}).get("pass", 0) >= 1 and (rep.get("journey", {}).get("summary") or {}).get("fail", 0) == 0
                             and rep["exec_dir_outside_worktree_and_repo"] and rep["worktree_local_dir_absent"])
    except Exception as e:  # noqa: BLE001
        rep["passed"] = False
        rep["error"] = str(e)[:1500]
    finally:
        try:
            r = sh([sys.executable, "-m", "twinfactory", "destroy", iid], cwd=wt, env=env, check=False, timeout=1200)
            rep["destroy"] = {"rc": r["rc"], "secs": r["secs"]}
        except Exception as e:  # noqa: BLE001
            rep["destroy"] = {"error": str(e)[:300]}
        sh(["git", "-C", str(paths.REPO), "worktree", "remove", "--force", str(wt)], check=False, timeout=120)
        sh(["git", "-C", str(paths.REPO), "worktree", "prune"], check=False, timeout=60)
    rep["secs"] = round(time.time() - t0, 1)
    return rep


def main(argv=None):
    gates = []

    def g(gid, desc, ok, detail=""):
        gates.append({"id": gid, "description": desc, "passed": bool(ok), "status": "PASS" if ok else "FAIL", "detail": str(detail)[:900]})

    head = git("rev-parse", "HEAD"); branch = git("rev-parse", "--abbrev-ref", "HEAD")
    now_tags = {t: git("rev-list", "-n1", t) for t in HISTORICAL_TAGS}
    g("M9-01", "Historical milestone tags unchanged (M3.1..M8)", all(now_tags[t].startswith(v) for t, v in HISTORICAL_TAGS.items()), {t: v[:10] for t, v in now_tags.items()})
    anc = subprocess.run(["git", "-C", str(paths.REPO), "merge-base", "--is-ancestor", "arch-m8-snapshot-query", head]).returncode == 0
    g("M9-02", "Branch descends from the M8 tag", anc and branch.startswith("milestone-9"), {"branch": branch, "head": head[:10]})
    ok = {p: sha256_file(paths.REPO / p) == h for p, h in M7_M8_FILES.items()}
    from archkit.store import SnapshotStore
    store = SnapshotStore()
    v = store.verify(M8_SNAPSHOT)
    body = store.load(M8_SNAPSHOT)["body"]
    inputs_ok = {i["path"]: sha256_file(paths.REPO / i["path"]) == i["sha256"] for i in body["inputs"] if (paths.REPO / i["path"]).is_file()} if isinstance(body["inputs"], list) and body["inputs"] and isinstance(body["inputs"][0], dict) else {}
    g("M9-03", "M7 artifacts and the accepted M8 snapshot (id recomputes, manifest ok, inputs byte-identical) unchanged", all(ok.values()) and v["id_recomputes"] and v["manifest_ok"] and all(inputs_ok.values()) and store.resolve("current") == M8_SNAPSHOT,
      {"m7": ok, "m8_verify": {k: v[k] for k in ("id_recomputes", "manifest_ok")}, "inputs_changed": [k for k, x in inputs_ok.items() if not x]})
    # 04 profiles derive from the snapshot + recipes
    from .snapshot import ArchitectureInputs
    from . import profiles as P
    inp = ArchitectureInputs(store, M8_SNAPSHOT)
    full = P.derive(inp, "full"); foc = P.derive(inp, "critical-payouts"); s2p = P.derive(inp, "focused-s2p")
    traced = all(v["reasons"] and re.match(r"^R\d+b?: ", v["reasons"][0]) for v in foc["services"].values())
    g("M9-04", "Runtime profiles are derived from the M8 snapshot + recipes (full = every recipe; focused = traced closure, no handwritten list)",
      len(full["services"]) == len(inp.recipes) == 98 and 20 < len(foc["services"]) < 98 and traced and set(foc["services"]) <= set(inp.recipes) and s2p["s2p"],
      {"snapshot": inp.snapshot_id[:12], "recipes": len(inp.recipes), "full": full["counts"], "critical-payouts": foc["counts"], "focused-s2p": s2p["counts"]})
    # 05 evidence present: instances manifests
    manifests = {p.parent.name: read_json(p) for p in sorted(EVIDENCE.glob("instances/*/manifest.json"))}
    full_ids = [i for i, m in manifests.items() if m.get("profile") == "full"]
    foc_ids = [i for i, m in manifests.items() if str(m.get("profile", "")).startswith("focused:")]
    binds = {i: all(m.get(k) for k in ("architecture_snapshot_id", "recipe_set_id", "profile", "seed", "inputs_hash", "rendered_config_hash", "secrets_manifest_digest", "image_digests", "runtime_instance_id"))
             and m["architecture_snapshot_id"] == M8_SNAPSHOT and m["recipe_set_id"] == inp.recipe_set_id for i, m in manifests.items()}
    g("M9-05", "Instance manifests bind snapshot id, recipe set, profile, seed, config hashes, secrets digest, image digests and a RuntimeInstance id",
      bool(full_ids) and bool(foc_ids) and all(binds.values()), {"instances": binds, "full": full_ids, "focused": foc_ids})
    # 06 image digests recorded for every service of both instances
    img_ok = {}
    for i, m in manifests.items():
        prof = read_json(EVIDENCE / "instances" / i / "profile.json") or {}
        recs = m.get("image_records") or {}
        missing = [s for s in list(prof.get("services", [])) + list(prof.get("jobs", [])) if s not in recs or not recs[s].get("image_id", "").startswith("sha256:")]
        img_ok[i] = {"services": len(prof.get("services", {})), "images": len(m.get("image_digests") or {}), "missing": missing}
    g("M9-06", "Every service of every instance resolved to an immutable image id (sha256) recorded in the manifest", bool(img_ok) and all(not v["missing"] for v in img_ok.values()), img_ok)
    # 07 isolation proof
    proof = read_json(PROOF)
    phases = (proof or {}).get("phases", {})
    g("M9-07", "Two instances ran concurrently on separate daemons/VMs (proof P1-P4: boundaries, namespaces, filesystem, reachability)",
      proof and all(phases.get(p, {}).get("passed") for p in ("P1", "P2", "P3", "P4")) and proof["a"] in full_ids and proof["b"] in foc_ids,
      {p: phases.get(p, {}).get("passed") for p in ("P1", "P2", "P3", "P4")} | {"a": (proof or {}).get("a"), "b": (proof or {}).get("b")})
    g("M9-08", "A full-profile journey and a focused-profile journey passed, attributed to the right instance (proof P5)", phases.get("P5", {}).get("passed"),
      {k: (phases.get("P5", {}).get("detail") or {}).get(k, {}).get("summary") for k in ("a", "b")})
    g("M9-09", "Restart, reset, state snapshot/restore and destroy isolation controls passed (proof P6-P9)", all(phases.get(p, {}).get("passed") for p in ("P6", "P7", "P8", "P9")),
      {p: phases.get(p, {}).get("passed") for p in ("P6", "P7", "P8", "P9")})
    rep = ((phases.get("P9", {}).get("detail") or {}).get("reproducible") or {})
    g("M9-10", "Image and configuration reproducibility: the destroyed instance was recreated from its manifest with identical inputs hash, profile digest, seed epoch and image ids",
      rep.get("inputs_hash") and rep.get("profile_digest") and rep.get("seed_epoch") and rep.get("image_ids") and all(rep["image_ids"].values()), rep and {k: rep[k] for k in ("inputs_hash", "profile_digest", "seed_epoch")} | {"image_ids_equal": sum(1 for x in rep.get("image_ids", {}).values() if x), "images": len(rep.get("image_ids", {}))})
    # 11 no hardcoded M7 instance names in the factory; journey runner env-driven
    src = "\n".join((p.read_text()) for p in sorted((paths.REPO / "twinfactory").glob("*.py")) if p.name != "acceptance.py")   # the scanner's own regex is not a hardcoded name
    code_lines = [l for l in src.splitlines() if not l.strip().startswith("#") and '"""' not in l]
    hard = [l.strip()[:120] for l in code_lines if re.search(r"env2_compose|\"rzp-arena\"|'rzp-arena'|18080", l)]
    runner_env = all(tok in (paths.REPO / "RED_LOOP/red_loop/config.py").read_text() for tok in ("ARENA_ENV2_ROOT", "TWIN_RUNS_DIR")) and "TWIN_INSTANCE_ID" in (paths.REPO / "RED_LOOP/m6/journeys/run.py").read_text()
    g("M9-11", "No hardcoded M7 instance names (env2_compose / rzp-arena / 18080) in twinfactory; journey runner is instance-aware through the environment", not hard and runner_env, {"hardcoded": hard[:5], "runner_env": runner_env})
    # 12 generated state outside the checkout
    outside = all(not m.get("exec_dir", "").startswith(str(paths.REPO)) for m in manifests.values())
    status = git("status", "--porcelain")
    gen_in_tree = [l for l in status.splitlines() if "ENV2_COMPOSE/secrets/" in l or "ENV2_COMPOSE/generated" in l or "seeds/generated" in l or "ENV2_COMPOSE/.runtime" in l]
    g("M9-12", "Generated secrets/config/seeds/runtime files live outside the source checkout", outside and not gen_in_tree, {"exec_dirs": [m.get("exec_dir") for m in manifests.values()], "generated_in_tree": gen_in_tree})
    # 13 S2P instance-configurable
    bg = (paths.REPO / "DOMAIN_REPLICAS/source_to_pay/runtime/source-driver/boot.go").read_text()
    s2p_env = all(k in bg for k in ("S2P_DB_URL", "S2P_DB_PASSWORD", "S2P_REDIS_HOST", "S2P_BOUNDARY_URL", "S2P_API_SECRET"))
    twin_overlay = (paths.REPO / "ENV2_COMPOSE/docker-compose.twin.yml").is_file() and "S2P_API_SECRET" in (paths.REPO / "ENV2_COMPOSE/docker-compose.twin.yml").read_text()
    s2p_inst = (phases.get("P2", {}).get("detail") or {}).get("s2p_secret_instance_specific")
    g("M9-13", "Source-to-Pay endpoints and secrets are instance-configurable (driver env overrides, twin overlay, per-instance secret observed)", s2p_env and twin_overlay and s2p_inst is True, {"driver_env": s2p_env, "overlay": twin_overlay, "instance_specific_secret": s2p_inst})
    # 14 durable registry
    reg = read_json(EVIDENCE / "registry-snapshot.json") or {}
    g("M9-14", "Durable instance registry with schema, live records and destroyed history", reg.get("kind") == "twin_instance_registry" and "instances" in reg and bool(reg.get("destroyed")), {"instances": sorted(reg.get("instances", {})), "destroyed": len(reg.get("destroyed", []))})
    # 15 measurements + host limits
    meas = (proof or {}).get("measurements", {})
    host = read_json(EVIDENCE / "host-limits.json") or {}
    tim = all(all(k in (m.get("timings") or {}) for k in ("build_secs", "boot_secs")) for m in manifests.values())
    g("M9-15", "Measured memory, disk, build, boot, reset, stop and destroy times and the host limits are recorded", bool(meas) and host.get("memory_bytes") and tim and any("reset_secs" in (m.get("timings") or {}) for m in manifests.values()),
      {"measured_instances": sorted(meas), "host_memory_gib": round((host.get("memory_bytes") or 0) / 2**30, 1), "cpus": host.get("cpu_count")})
    # 16 tests
    tests = {}
    for name, cmd in (("twinfactory", [sys.executable, "-m", "unittest", "twinfactory.tests.test_factory", "twinfactory.tests.test_isolation"]),
                      ("archkit", [sys.executable, "-m", "unittest", "archkit.tests.test_archkit"]),
                      ("snapshot", [sys.executable, "-m", "unittest", "discover", "-s", "scripts/snapshot/tests"]),
                      ("red_loop", [sys.executable, "-m", "unittest", "discover", "RED_LOOP/tests"])):
        r = sh(cmd, cwd=paths.REPO, check=False, timeout=1800)
        m = re.search(r"Ran (\d+) tests", r["stderr"] + r["stdout"])
        tests[name] = {"rc": r["rc"], "ran": int(m.group(1)) if m else 0, "ok": r["rc"] == 0 and "OK" in (r["stderr"] + r["stdout"])}
    g("M9-16", "twinfactory + isolation tests, archkit (M8), snapshot and RED_LOOP test suites all pass", all(t["ok"] for t in tests.values()) and tests["twinfactory"]["ran"] >= 30, tests)
    # 17 docs + hash manifest
    docs_ok = {d: (paths.REPO / d).is_file() and (paths.REPO / d).stat().st_size > 2000 for d in DOCS}
    g("M9-17", "Final report, runbook and schema present", all(docs_ok.values()), docs_ok)
    # 18 live clean-checkout provisioning of a FULL twin
    live = live_clean_checkout_gate(head)
    g("M9-18", "A clean checkout (fresh git worktree, no .local, no generated state) provisions, boots and passes a journey in a FULL twin from the M8 snapshot on its own colima boundary",
      live.get("passed"), {k: live.get(k) for k in ("instance_id", "health", "journey", "manifest", "timings", "secs", "error", "worktree_local_dir_absent", "exec_dir_outside_worktree_and_repo")})
    # 19 hash manifest binds evidence + code
    hashes = build_hashes()
    g("M9-19", "Hash manifest binds twinfactory, the M9 evidence, the isolation proof and the changed runtime files", hashes["count"] > 40, {"count": hashes["count"]})
    # 20 clean tree
    status = git("status", "--porcelain")
    dirty = [l for l in status.splitlines() if not any(x in l for x in ("M9_ACCEPTANCE.json", "M9_ARTIFACT_HASHES.json"))]
    g("M9-20", "Working tree clean at evaluation time (only the acceptance outputs may differ)", not dirty, {"dirty": dirty[:10]})

    passed = sum(1 for x in gates if x["passed"])
    doc = {"milestone": "M9", "title": "Isolated Twin Factory", "evaluated_at": now(), "git_head": head, "branch": branch, "twinfactory_version": __version__,
           "architecture_snapshot_id": M8_SNAPSHOT, "gates": gates, "passed": passed, "total": len(gates), "accepted": passed == len(gates)}
    OUTP.write_text(json.dumps(doc, indent=2) + "\n")
    for x in gates:
        print("%s %s  %s" % (x["status"], x["id"], x["description"][:110]))
    print("accepted=%s %d/%d -> %s" % (doc["accepted"], passed, len(gates), paths.rel(OUTP)))
    return 0 if doc["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
