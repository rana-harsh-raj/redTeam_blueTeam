"""M8 acceptance: machine-computed gates over Git, the committed snapshot store and the code. No docker.

    python3 -m archkit acceptance   -> reports/implementation/M8_ACCEPTANCE.json (exit 0 iff accepted)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from . import paths, __version__
from .canon import content_id, sha256_file, scan_volatile
from .store import SnapshotStore
from .query import Query, CAPABILITIES

OUT = paths.IMPL / "M8_ACCEPTANCE.json"
HASHES = paths.IMPL / "M8_ARTIFACT_HASHES.json"
HISTORICAL_TAGS = {"assurance-m4.1-reproducible": "ef4ba7264df224e4b782f2671ce8b1eb3079b3d0", "assurance-m5-autonomous-discovery": "2613f383297c7c3b5c0831c26c5c7472494b6e7b",
                   "red-loop-m3.1": "3a044f83ed0eda6ead71585f07c450af2ed7978f", "red-loop-m4": "3ae177328341ec4512bdb7de8ce454ce67a35513",
                   "s2p-acceptance-closure-m7": "4505e62dce58561793429c62174c2941c97f6eb0", "twin-m6-complete-domain": "76024aed5ae8474d29bd2b0b2e48c97c429eda52",
                   "twin-m7-shared-ingress-integration": "37ac47db1e630c44e54707694aa84f44301f0b19", "twin-v1.0": "78def24eb57112c0dc39a6ae9062b1f0d1c711fb"}
M7_FILES = {"reports/architecture/M7_CANONICAL_SNAPSHOT.json": "d88a26342e10eea4c18971f2765ecc2e05364d7716cc113733f7d76233709651",
            "reports/implementation/M7_ACCEPTANCE.json": "13d7ef0b83512429dc8a1b59c96305b99fd2155b65cfc7d3f8a8ad5e40b203d6",
            "reports/implementation/M7_ARTIFACT_HASHES.json": "f97e1b27e5c41f5417c7c34fa7eb6ee9f4cc6fd988348a02977c380f3f1b791f"}
DOCS = ["reports/implementation/M8_FINAL_REPORT.md", "reports/implementation/M8_RUNBOOK.md", "archkit/SCHEMA.md"]


def git(*a):
    return subprocess.run(["git", "-C", str(paths.REPO)] + list(a), capture_output=True, text=True).stdout.strip()


def run(cmd, cwd=None, env=None, timeout=900):
    return subprocess.run(cmd, cwd=str(cwd or paths.REPO), capture_output=True, text=True, timeout=timeout, env=env)


def build_hashes(store, sid):
    files = {}
    d = store.dir(sid)
    for p in sorted(d.rglob("*.json")):
        files[paths.rel(p)] = sha256_file(p)
    for p in sorted((paths.REPO / "archkit").rglob("*.py")) + [paths.REPO / "archkit/SCHEMA.md", paths.REPO / "reports/implementation/M8_FINAL_REPORT.md", paths.REPO / "reports/implementation/M8_RUNBOOK.md", paths.REPO / "reports/implementation/m8-bench.json"]:
        if p.is_file() and "__pycache__" not in p.parts:
            files[paths.rel(p)] = sha256_file(p)
    reg = store.root / "REGISTRY.json"
    files[paths.rel(reg)] = sha256_file(reg)
    doc = {"algorithm": "sha256", "snapshot_id": sid, "self_excluded": paths.rel(HASHES), "files": dict(sorted(files.items())), "count": len(files)}
    HASHES.write_text(json.dumps(doc, indent=2) + "\n")
    return doc


def verify_hashes():
    doc = json.loads(HASHES.read_text())
    bad = [r for r, h in doc["files"].items() if not (paths.REPO / r).is_file() or sha256_file(paths.REPO / r) != h]
    return {"passed": not bad, "changed_or_missing": bad, "count": len(doc["files"])}


def main(argv=None):
    gates = []

    def g(gid, desc, ok, detail=""):
        gates.append({"id": gid, "description": desc, "passed": bool(ok), "status": "PASS" if ok else "FAIL", "detail": str(detail)[:700]})

    head = git("rev-parse", "HEAD"); branch = git("rev-parse", "--abbrev-ref", "HEAD")
    store = SnapshotStore()
    # 01 historical tags
    now = {t: git("rev-list", "-n1", t) for t in HISTORICAL_TAGS}
    g("M8-01", "Historical milestone tags unchanged (incl. the M7 tag)", now == HISTORICAL_TAGS, {t: v[:10] for t, v in now.items()})
    # 02 ancestry
    anc = subprocess.run(["git", "-C", str(paths.REPO), "merge-base", "--is-ancestor", HISTORICAL_TAGS["twin-m7-shared-ingress-integration"], head]).returncode == 0
    g("M8-02", "Branch descends from the M7 tag", anc and branch.startswith("milestone-8"), {"branch": branch, "head": head[:10]})
    # 03 M7 files untouched
    m7ok = {p: sha256_file(paths.REPO / p) == h for p, h in M7_FILES.items()}
    g("M8-03", "M7 artifacts unchanged (canonical snapshot, acceptance, hash manifest)", all(m7ok.values()), m7ok)
    # 04 current snapshot exists, id recomputes, manifest verifies
    try:
        sid = store.resolve("current")
        v = store.verify(sid)
        g("M8-04", "Current architecture snapshot verifies (snapshot_id recomputes from the body; manifest hashes match)", v["id_recomputes"] and v["manifest_ok"], v)
    except Exception as e:  # noqa: BLE001
        sid = None
        g("M8-04", "Current architecture snapshot verifies", False, repr(e))
    body = store.load(sid)["body"] if sid else {}
    # 05 determinism: compile 3x into temp stores, byte-identical to the committed file
    tmp = Path(tempfile.mkdtemp(prefix="m8-acc-"))
    committed = (store.root / sid / "snapshot.json").read_bytes() if sid else b""
    same = []
    for i in range(3):
        r = run([sys.executable, "-m", "archkit", "--store", str(tmp / ("s%d" % i)), "compile"])
        p = tmp / ("s%d" % i) / (sid or "x") / "snapshot.json"
        same.append(r.returncode == 0 and p.is_file() and p.read_bytes() == committed)
    g("M8-05", "Three fresh compiles are byte-identical to the committed snapshot", all(same), {"runs": same, "snapshot_id": (sid or "")[:12]})
    # 06 volatile scan
    hits = scan_volatile(body) if body else [("", "no body", "")]
    txt = json.dumps(body, sort_keys=True)
    g("M8-06", "Static body carries no wall clock, host path, docker status, boot id or journey result", not hits and "/Users/" not in txt and "boot_id" not in txt, hits[:5])
    # 07 populations / coverage explicit
    cov = body.get("coverage", {})
    okc = bool(cov) and all(all(k in v for k in ("numerator", "denominator", "population", "pct", "definition")) and v["denominator"] == body["populations"][v["population"]]["count"] for v in cov.values() if isinstance(v, dict))
    g("M8-07", "Every coverage measure names numerator, denominator and population", okc, {k: "%s/%s %s" % (v["numerator"], v["denominator"], v["population"]) for k, v in cov.items() if isinstance(v, dict)})
    # 08 M7 import
    imps = store.imports(sid) if sid else {}
    proj = (imps.get("m7") or {}).get("projection") or {}
    ok8 = proj.get("node_ids_equal") is True and proj.get("edges_only_in_m7") == [] and all(d["reason"] != "unclassified delta" for d in proj.get("fidelity_deltas", [])) \
        and proj.get("m7_files", {}).get("reports/architecture/M7_CANONICAL_SNAPSHOT.json") == M7_FILES["reports/architecture/M7_CANONICAL_SNAPSHOT.json"]
    g("M8-08", "M7 import projects onto the snapshot (same node ids, no lost edges, every fidelity delta classified, M7 sha bound)", ok8,
      {k: proj.get(k) for k in ("node_ids_equal", "fidelity_delta_count", "label_delta_count")} | {"edges_only_in_static": len(proj.get("edges_only_in_static", []))})
    # 09 separated records
    inst, bundle, rec = (imps.get("m7") or {}).get("runtime_instance") or {}, (imps.get("m7") or {}).get("evidence_bundle") or {}, (imps.get("m7") or {}).get("acceptance_record") or {}
    ok9 = bool(inst) and bundle.get("runtime_instance_id") == inst.get("runtime_instance_id") and rec.get("evidence_bundle_id") == bundle.get("evidence_bundle_id") and rec.get("accepted") is True \
        and content_id({k: v for k, v in rec.items() if k != "acceptance_record_id"}) == rec.get("acceptance_record_id") and bundle.get("result_histogram") == {"EXPECTED_FAILURE": 1, "PASS": 110}
    g("M8-09", "RuntimeInstance, EvidenceBundle and AcceptanceRecord are separate, id-bound documents chained to each other", ok9,
      {"boot_id": inst.get("boot_id"), "bundle_files": bundle.get("file_count"), "acceptance": "%s/%s" % (rec.get("gates_passed"), rec.get("gates_total"))})
    # 10 unknown registry
    reg = body.get("production_unknowns", {})
    ids = [e["id"] for e in reg.get("entries", [])]
    ok10 = ids[:10] == ["PU-%d" % i for i in range(1, 11)] and len([i for i in ids if i.startswith("S2P:")]) == 4 and all("file" in e["origin"] and "original_id" in e["origin"] for e in reg.get("entries", [])) \
        and all(a in body["labels"] for e in reg.get("entries", []) for a in e["affects"])
    g("M8-10", "Production unknowns consolidated (PU-1..10 + 4 Source-to-Pay) with original id/file kept and every affected node resolving", ok10, {"count": reg.get("count"), "unresolved": [(e["id"], e["affects_unresolved"]) for e in reg.get("entries", []) if e.get("affects_unresolved")]})
    # 11 recipes
    idx = store.recipes(sid) if sid else None
    ok11 = bool(idx) and idx["count"] == 98 and all("runtime.image_digest" in r["unknowns"] for r in idx["recipes"]) and all(store.recipe(r["id"], sid).get("recipe_id") == content_id({k: v for k, v in store.recipe(r["id"], sid).items() if k != "recipe_id"}) for r in idx["recipes"][:98])
    g("M8-11", "98 normalized service recipes with explicit unknowns and content ids", ok11, {"count": idx and idx["count"], "by_category": idx and idx["by_category"]})
    # 12 queries: every capability answers; canonical queries never load the detail namespace
    try:
        st2 = SnapshotStore()
        q = Query(st2, sid)
        calls = {"snapshot": {}, "node": {"id": "svc:payouts-api"}, "edges": {"id": "svc:payouts-api"}, "search": {"q": "ingress"}, "service_card": {"id": "sub:api-ingress"},
                 "neighbors": {"id": "svc:fts-web", "depth": 2}, "dependencies": {"id": "svc:payouts-api", "transitive": "true"}, "shortest_path": {"a": "identity:merchant-api-key", "b": "table:payouts/payouts"},
                 "paths": {"a": "sub:api-ingress", "b": "svc:fts-web"}, "identity_reachability": {"identity": "identity:ingress-app-secret"}, "data_flow": {"id": "table:payouts/payouts"},
                 "families": {}, "family": {"id": "family:cross-domain-s2p"}, "journeys": {"family": "family:shared-ingress"}, "journey": {"id": "journey:cross-domain-s2p/success"},
                 "fidelity_gaps": {}, "unknowns": {}, "uncovered_trust_boundaries": {}, "evidence": {}, "imports": {}, "recipe": {"service": "payouts-api"}, "recipes": {},
                 "compare": {"other": sid}, "affected": {"nodes": "sub:api-ingress"}, "context_packet": {"subject": "svc:payouts-api"}, "verify": {}, "snapshots": {}}
        answered = {}
        for cap in CAPABILITIES:
            if cap == "s2p_detail":
                continue
            r = getattr(q, cap)(**calls[cap])
            answered[cap] = "items" in r and ("snapshot_id" in r or cap == "snapshots")
        no_detail = st2.s2p_detail_loads == 0
        d = q.s2p_detail(prefix="vp.", limit=3)
        ok12 = all(answered.values()) and no_detail and st2.s2p_detail_loads == 1 and d["detail_nodes_total"] == 27994
        g("M8-12", "All query capabilities answer through the library; canonical queries never load the Source-to-Pay detail namespace; explicit detail expansion works", ok12,
          {"capabilities": len(answered), "failed": [c for c, v in answered.items() if not v], "detail_loaded_by_canonical_queries": not no_detail})
    except Exception as e:  # noqa: BLE001
        g("M8-12", "All query capabilities answer", False, repr(e))
    # 13 tests: archkit + scripts/snapshot + RED_LOOP
    results = {}
    for name, cmd in (("archkit", [sys.executable, "-m", "unittest", "archkit.tests.test_archkit"]), ("snapshot", [sys.executable, "-m", "unittest", "discover", "-s", "scripts/snapshot/tests"]),
                      ("red_loop", [sys.executable, "-m", "unittest", "discover", "RED_LOOP/tests"])):
        r = run(cmd, timeout=1200)
        m = re.search(r"Ran (\d+) tests", r.stderr)
        results[name] = {"rc": r.returncode, "tests": int(m.group(1)) if m else 0, "ok": r.returncode == 0 and "OK" in r.stderr}
    g("M8-13", "archkit, scripts/snapshot and RED_LOOP test suites pass", all(v["ok"] for v in results.values()), results)
    # 14 clean-checkout reproduction: fresh worktree of HEAD (no .local, no runs) compiles the identical snapshot + recipes + import
    wt = tmp / "clean"
    run(["git", "worktree", "add", "--detach", str(wt), head])
    env = dict(os.environ); env["ARCHKIT_REPO"] = str(wt); env["ARCHKIT_STORE"] = str(tmp / "clean-store")
    r = run([sys.executable, "-m", "archkit", "build"], cwd=wt, env=env, timeout=1200)
    cs = tmp / "clean-store" / (sid or "x")
    same_snap = cs.is_file() or (cs / "snapshot.json").is_file() and (cs / "snapshot.json").read_bytes() == committed
    rec_same = (cs / "recipes/INDEX.json").is_file() and json.loads((cs / "recipes/INDEX.json").read_bytes()).get("recipe_set_id") == (idx or {}).get("recipe_set_id")
    imp_same = (cs / "imports/m7/projection.json").is_file() and (cs / "imports/m7/projection.json").read_bytes() == (store.dir(sid) / "imports/m7/projection.json").read_bytes()
    run(["git", "worktree", "remove", "--force", str(wt)])
    g("M8-14", "Fresh clean checkout of HEAD (no .local, no run dirs) reproduces the snapshot, recipes and M7 import byte-for-byte", r.returncode == 0 and same_snap and rec_same and imp_same,
      {"rc": r.returncode, "snapshot_identical": same_snap, "recipes_identical": rec_same, "import_identical": imp_same, "tail": r.stderr[-300:]})
    # 15 no docker / subprocess / secret or clone reads in the knowledge layer (acceptance.py is the only module allowed to shell out: git + tests)
    offenders = []
    for p in sorted((paths.REPO / "archkit").rglob("*.py")):
        if "tests" in p.parts or p.name == "acceptance.py":
            continue
        t = p.read_text()
        reads_local = any(".local/" in l and ("Path(" in l or "open(" in l or 'REPO / "' in l) for l in t.splitlines())
        if re.search(r"^\s*(import subprocess|from subprocess)", t, re.M) or re.search(r"""[\[(]\s*['"]docker['"]""", t) or reads_local \
           or re.search(r"secrets/(merchant-keys|[a-z_]+\.txt)", t) or ("rzp-payouts-clones" in t and p.name != "canon.py"):
            offenders.append(paths.rel(p))
    g("M8-15", "archkit never shells out, invokes docker or reads secrets/clones (compilation and queries are pure repository reads)", not offenders, {"offenders": offenders})
    # 16 HTTP service smoke
    try:
        from .service import serve
        import threading, urllib.request
        httpd = serve(store, 0); port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
        with urllib.request.urlopen("http://127.0.0.1:%d/v1/latest/node?id=svc:payouts-api" % port, timeout=10) as resp:
            ok16 = resp.status == 200 and json.loads(resp.read())["snapshot_id"] == sid
        httpd.shutdown()
    except Exception as e:  # noqa: BLE001
        ok16 = False; port = repr(e)
    g("M8-16", "Read-only loopback HTTP service answers the query envelope", ok16, {"port": port})
    # 17 docs present, no parity phrases
    prohibited = [r"(?i)identical to production", r"(?i)matches production exactly", r"(?i)\d+(\.\d+)?%\s*identical to production"]
    hits17 = []
    for d in DOCS:
        p = paths.REPO / d
        if p.is_file():
            for pat in prohibited:
                hits17 += ["%s: %s" % (d, m.group(0)) for m in re.finditer(pat, p.read_text())]
    g("M8-17", "Final report, runbook and schema present; no single 'percent identical to production' claim", all((paths.REPO / d).is_file() for d in DOCS) and not hits17, {"missing": [d for d in DOCS if not (paths.REPO / d).is_file()], "hits": hits17[:3]})
    # 18 snapshot bench recorded
    bench = paths.IMPL / "m8-bench.json"
    bd = json.loads(bench.read_text()) if bench.is_file() else {}
    g("M8-18", "Performance measurements recorded for this snapshot (index build, lookups, paths, detail expansion)", bd.get("snapshot_id") == sid and "queries_ms" in bd, {k: bd.get(k) for k in ("index_build_ms", "rss_mb_after_index")})
    # 19 hash manifest
    try:
        hv = verify_hashes() if HASHES.is_file() else {"passed": False, "changed_or_missing": ["manifest missing"]}
    except Exception as e:  # noqa: BLE001
        hv = {"passed": False, "changed_or_missing": [repr(e)]}
    g("M8-19", "M8 artifacts hash-bound (M8_ARTIFACT_HASHES.json verifies: snapshot store, imports, recipes, archkit sources, reports)", hv.get("passed"), {"count": hv.get("count"), "changed": (hv.get("changed_or_missing") or [])[:5]})
    # 20 tree clean
    status = [l for l in git("status", "--porcelain").splitlines() if not l.endswith(("M8_ACCEPTANCE.json", "M8_ARTIFACT_HASHES.json"))]
    g("M8-20", "Tracked tree clean (excluding the acceptance + hash manifest this evaluation writes)", not status, {"dirty": status[:8]})
    shutil.rmtree(tmp, ignore_errors=True)
    accepted = all(x["passed"] for x in gates)
    doc = {"milestone": "M8", "accepted": accepted, "evaluated_at": datetime.now(timezone.utc).isoformat(), "head": head, "branch": branch, "snapshot_id": sid,
           "archkit_version": __version__, "gates_passed": sum(x["passed"] for x in gates), "gates_total": len(gates), "gates": gates,
           "artifact_manifest_sha256": sha256_file(HASHES) if HASHES.is_file() else None,
           "evaluation_scope": "20 hard gates over Git state, the committed snapshot store, fresh compiles (in temp stores and a fresh worktree), the three test suites and the query library; no docker, no clones, no secrets."}
    OUT.write_text(json.dumps(doc, indent=2) + "\n")
    for x in gates:
        print("[%s] %s %s" % (x["status"], x["id"], x["description"][:100]))
    print("%d/%d gates accepted=%s -> %s" % (doc["gates_passed"], doc["gates_total"], accepted, paths.rel(OUT)))
    return 0 if accepted else 1


if __name__ == "__main__":
    sys.exit(main())
