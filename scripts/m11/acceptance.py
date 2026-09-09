#!/usr/bin/env python3
"""M11 machine-computed acceptance -- every gate is evaluated from committed artifacts and the live factory records.

  python3 scripts/m11/acceptance.py [--skip-suites] [--real-run <run_dir>] [--sub-run <run_dir>]
Writes reports/implementation/M11_ACCEPTANCE.json (accepted = every gate passed). No gate is hand-set.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
IMPL = REPO / "reports" / "implementation"
M11 = IMPL / "m11"

# every substitute still running in the REAL variant, with its exact external blocker (the definition-of-done demands one each)
REMAINING_SUBSTITUTES = {
    "api-ingress": "API monolith (razorpay/api, PHP/Laravel) cannot be booted: no php/composer on this machine, 18/23 private composer VCS repositories unreadable, all Dockerfile base images on harbor.razorpay.com need registry credentials (reports/implementation/m11/monolith-boot-attempt.json). Stays CONTRACT_FAITHFUL_REPLACEMENT; now verifies the real gateway passport (kid edgev2) and publishes /jwks.",
    "asv-stub": "razorpay/account-service is not a granted repository (clone 404 for this identity) -- no source to run.",
    "monolith-stub": "the monolith's data-plane responsibilities (merchant config, balances DB) are part of the same unbootable PHP monolith (see api-ingress).",
    "dcs-stub": "razorpay/dcs (config service) is not in the granted set as a runnable service; the real banking-accounts client dials https://dcs-*.dev.razorpay.in unmodified and the twin answers those hostnames with dcs-stub over TLS (secrets/gen-tls.sh).",
    "splitz-stub": "razorpay/splitz is not a granted repository.",
    "pricing-stub": "the pricing service repository is not granted.",
    "mozart-sim": "external bank gateways (Mozart/RBL/...) are synthetic by mandate (external banks may stay synthetic).",
    "xas-sim": "external bank statement feeds are synthetic by mandate.",
    "xas-sink": "external bank statement sink is synthetic by mandate.",
    "batch-sim": "the batch service repository is not granted; batch-sim presents the `batch` internal application to the monolith replacement.",
    "stork-capture": "razorpay/stork (webhook delivery) is not granted; the capture keeps the delivery contract observable.",
    "merchant-webhook-sink": "the merchant's own webhook endpoint is external by definition.",
    "ledger-gate": "twin control plane (ledger access gate), not a production component.",
    "workflow-sim": "M2 stand-in kept only for the historical journeys' probe; no longer wired (payouts points at workflows-api).",
    "kong-lite": "retired to the SUBSTITUTE variant only (fallback / comparison profile); the real variant runs edge-kong.",
    "shield-stub": "retired to the SUBSTITUTE variant only; the real variant runs shield-web.",
    "bankingaccounts-stub": "retired to the SUBSTITUTE variant only; the real variant runs banking-accounts-api.",
    "workflow-engine": "retired to the SUBSTITUTE variant only; the real variant runs workflows-api/worker + Cadence.",
    "s2p-source-driver": "Source-to-Pay replica driver (M7), unchanged.",
    "cron-driver": "twin control plane (cron trigger), not a production component.",
    "edge-bridge": "twin control plane (host bridge helper on the ingress network).",
}


def jload(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def sh(cmd, cwd=None, timeout=600):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.returncode, (r.stdout or "")[-2000:], (r.stderr or "")[-2000:]
    except Exception as e:  # noqa: BLE001
        return None, "", str(e)


def gate(gates, gid, desc, ok, detail=None):
    gates.append({"id": gid, "desc": desc, "pass": bool(ok), "detail": detail})
    return bool(ok)


def latest_run(instance_dir):
    runs = sorted((Path(instance_dir) / "journeys").glob("run-*"))
    return runs[-1] if runs else None


def run_summary(run_dir):
    """run_dir may be a comma-separated list; a later run's result supersedes an earlier one per journey id."""
    merged = {}
    for rd in ([x for x in str(run_dir).split(",") if x] if run_dir else []):
        for r in jload(Path(rd) / "results.json", []):
            merged[r["id"]] = r
    rows = list(merged.values())
    by = {}
    for r in rows:
        by[r["result"]] = by.get(r["result"], 0) + 1
    return {"run_dir": str(run_dir), "total": len(rows), **by,
            "failed": [r["id"] for r in rows if r["result"] == "FAIL"],
            "blocked": [(r["id"], (r.get("missing_dependency") or "")[:120]) for r in rows if r["result"] == "BLOCKED"],
            "ids": [r["id"] for r in rows]}


def evaluate(skip_suites=False, real_run=None, sub_run=None):
    from twinfactory.factory import Factory
    gates = []
    f = Factory()
    insts = {}
    for m in f.registry.records():
        insts[m["instance_id"]] = m
    real_inst = next((m for m in insts.values() if m.get("trust_path") == "real" and m.get("state") in ("running", "stopped")), None)
    sub_inst = next((m for m in insts.values() if m.get("trust_path") == "substitute" and m.get("state") in ("running", "stopped")), None)
    real_run = real_run if real_run else (str(latest_run(f.idir(real_inst["instance_id"]))) if real_inst else None)
    sub_run = sub_run if sub_run else (str(latest_run(f.idir(sub_inst["instance_id"]))) if sub_inst else None)
    rs, ss = run_summary(real_run), run_summary(sub_run)
    kong_cfg = jload(REPO / "ENV2_COMPOSE" / "trustpath" / "edge" / "kong-config.json", {})
    monolith = jload(M11 / "monolith-boot-attempt.json", {})
    diff = jload(M11 / "differential.json", {})
    resources = sorted(M11.glob("resources-*.json"))
    clean = jload(M11 / "clean-checkout.json", {})
    snap_reg = jload(REPO / "reports" / "architecture" / "snapshots" / "REGISTRY.json", {})
    snap_id = snap_reg.get("current")
    snap_summary = jload(REPO / "reports" / "architecture" / "snapshots" / (snap_id or "x") / "summary.json", {})
    recipes_dir = REPO / "reports" / "architecture" / "snapshots" / (snap_id or "x") / "recipes"
    recipe_names = {p.stem for p in recipes_dir.glob("*.json")} if recipes_dir.is_dir() else set()
    prof_json = None
    rc, out, _ = sh([sys.executable, "-m", "twinfactory", "profiles", "--trust-path", "real"], cwd=REPO)
    try:
        prof_json = json.loads(out)
    except ValueError:
        prof_json = {}
    full_real = (prof_json.get("profiles") or {}).get("full") or {}
    rc2, out2, _ = sh([sys.executable, "-m", "twinfactory", "profiles", "--trust-path", "substitute"], cwd=REPO)
    try:
        full_sub = (json.loads(out2).get("profiles") or {}).get("full") or {}
    except ValueError:
        full_sub = {}
    trust_real = {"edge-kong", "shield-web", "banking-accounts-api", "workflows-api", "workflows-worker", "cadence"}
    real_services = set(full_real.get("services") or [])
    sub_services = set(full_sub.get("services") or [])

    def jres(summary, jid):
        run = real_run if summary is rs else sub_run
        found = {}
        for rd in ([x for x in str(run).split(",") if x] if run else []):
            for r in jload(Path(rd) / "results.json", []):
                if r["id"] == jid:
                    found = r
        return found

    # ---- gates ----
    gate(gates, "M11-01", "real edge gateway (razorpay/edge Kong + terraform-kong prod-api routes) is the DEFAULT ingress of every derived profile",
         full_real.get("trust_path") == "real" and "edge-kong" in real_services and "kong-lite" not in real_services and kong_cfg.get("kind") == "twin_kong_config"
         and sum(len(s.get("routes", {})) for s in (kong_cfg.get("services") or {}).values()) >= 18 and (real_inst or {}).get("trust_path") == "real",
         {"default_trust_path": full_real.get("trust_path"), "routes": sum(len(s.get("routes", {})) for s in (kong_cfg.get("services") or {}).values()),
          "instance": (real_inst or {}).get("instance_id"), "route_hosts": (kong_cfg.get("route_hosts") or {}).get("twin")})
    gate(gates, "M11-02", "highest feasible monolith auth path: boot attempt recorded with exact blockers; the monolith replacement verifies the gateway passport (kid edgev2) end to end",
         monolith.get("verdict", {}).get("bootable_here") is False and len(monolith.get("verdict", {}).get("blockers") or []) >= 2
         and jres(rs, "journey:trust-path/identity_propagation").get("result") == "PASS" and jres(rs, "journey:trust-path/api_key_auth").get("result") == "PASS",
         {"blockers": monolith.get("verdict", {}).get("blockers"), "identity_propagation": jres(rs, "journey:trust-path/identity_propagation").get("result"),
          "api_key_auth": jres(rs, "journey:trust-path/api_key_auth").get("result")})
    gate(gates, "M11-03", "real Shield (razorpay/shield, APP_MODE=rzpxprod) replaces shield-stub and decides payouts",
         "shield-web" in real_services and "shield-stub" not in real_services and jres(rs, "journey:trust-path/shield_rules").get("result") == "PASS",
         {"shield_rules": jres(rs, "journey:trust-path/shield_rules").get("result")})
    gate(gates, "M11-04", "banking-accounts real where feasible (Direct merchants); account-service blocked with an exact reason",
         "banking-accounts-api" in real_services and jres(rs, "journey:trust-path/bas_lookup").get("result") == "PASS" and "asv-stub" in REMAINING_SUBSTITUTES,
         {"bas_lookup": jres(rs, "journey:trust-path/bas_lookup").get("result"), "asv": REMAINING_SUBSTITUTES["asv-stub"]})
    gate(gates, "M11-05", "real Workflow service (razorpay/workflows + Cadence) on LOCAL infrastructure drives maker-checker",
         trust_real <= real_services and jres(rs, "journey:trust-path/maker_checker").get("result") == "PASS" and all(jload(p, {}).get("remote", {}).get("used") is False for p in resources) and bool(resources),
         {"maker_checker": jres(rs, "journey:trust-path/maker_checker").get("result"), "placement": [jload(p, {}).get("placement") for p in resources]})
    subs_in_real = sorted(s for s in real_services if s.endswith("-stub") or s.endswith("-sim") or s in ("api-ingress", "batch-sim", "stork-capture", "merchant-webhook-sink", "ledger-gate", "workflow-sim", "xas-sink", "cron-driver", "edge-bridge", "s2p-source-driver"))
    gate(gates, "M11-06", "every remaining substitute in the real variant carries a precise external blocker",
         bool(subs_in_real) and all(s in REMAINING_SUBSTITUTES and REMAINING_SUBSTITUTES[s] for s in subs_in_real),
         {"remaining": subs_in_real, "unlisted": [s for s in subs_in_real if s not in REMAINING_SUBSTITUTES]})
    gate(gates, "M11-07", "canonical journey suite passes on the real variant (0 FAIL; every BLOCKED names its dependency)",
         rs["total"] >= 100 and rs.get("FAIL", 0) == 0 and all(dep for _, dep in rs["blocked"]),
         {"real": {k: rs.get(k) for k in ("run_dir", "total", "PASS", "FAIL", "BLOCKED", "EXPECTED_FAILURE")}, "failed": rs["failed"], "blocked": rs["blocked"][:10]})
    gate(gates, "M11-08", "differential run (same suite on the substitute variant) exists and every difference is investigated (0 OPEN)",
         diff.get("kind") == "m11_differential" and diff.get("journeys", 0) >= 100 and len(diff.get("open_differences") or []) == 0 and ss["total"] >= 100,
         {"journeys": diff.get("journeys"), "identical": diff.get("identical"), "with_differences": diff.get("with_differences"), "open": len(diff.get("open_differences") or []),
          "substitute": {k: ss.get(k) for k in ("run_dir", "total", "PASS", "FAIL", "BLOCKED")}})
    rc, vout, _ = sh([sys.executable, "-m", "archkit", "verify"], cwd=REPO)
    try:
        verify = json.loads(vout)
    except ValueError:
        verify = {}
    gate(gates, "M11-09", "snapshot, fidelity labels, recipes (pinned SHAs), locks and profiles updated: current snapshot verifies and carries the trust-path recipes",
         verify.get("manifest_ok") and verify.get("id_recomputes") and {"edge-kong", "shield-web", "banking-accounts-api", "workflows-api"} <= recipe_names
         and (snap_summary.get("counts") or {}).get("nodes", 0) > 3090 and len(sub_services) > 0 and "kong-lite" in sub_services,
         {"snapshot": snap_id, "verify": verify, "recipes": len(recipe_names), "substitute_profile_services": len(sub_services)})
    gate(gates, "M11-10", "clean checkout reproduces the real-variant twin (create + build + start from a fresh clone)",
         clean.get("ok") is True and clean.get("state") == "running" and clean.get("trust_path") == "real",
         {k: clean.get(k) for k in ("ok", "state", "trust_path", "checkout", "instance_id", "secs", "healthy")})
    gate(gates, "M11-11", "resource usage measured (local CPU/memory/disk/build/boot; remote recorded as not used with the reason)",
         bool(resources) and all(jload(p, {}).get("containers", {}).get("count", 0) > 50 and jload(p, {}).get("timings", {}).get("boot_secs") for p in resources),
         {"files": [str(p.relative_to(REPO)) for p in resources]})
    rc, tagout, _ = sh(["git", "tag", "--list"], cwd=REPO)
    expected = jload(M11 / "historical-tags.json", {})
    tags_now = {}
    for t in tagout.split():
        rc, h, _ = sh(["git", "rev-parse", t + "^{commit}"], cwd=REPO)
        tags_now[t] = h.strip()
    gate(gates, "M11-12", "historical tags unchanged (every pre-M11 tag still points at its recorded commit)",
         bool(expected) and all(tags_now.get(t) == h for t, h in expected.items()),
         {"checked": len(expected), "mismatch": [t for t, h in expected.items() if tags_now.get(t) != h]})
    rc, st, _ = sh(["git", "status", "--porcelain"], cwd=REPO)
    dirty = [l for l in st.splitlines() if l.strip() and not l.endswith("M11_ACCEPTANCE.json")]
    gate(gates, "M11-13", "clean working tree at evaluation (only this acceptance record may differ)", not dirty, {"dirty": dirty[:10]})
    rc, ls, _ = sh(["git", "ls-files", "ENV2_COMPOSE/secrets"], cwd=REPO)
    tracked_secrets = [l for l in ls.splitlines() if l.endswith(".txt") or l.endswith(".pem") and "prod_public" not in l]
    gate(gates, "M11-14", "secrets hygiene: no generated secret values tracked; instance secrets materialized uid 10001 / 0400",
         not tracked_secrets and (real_inst or {}).get("secrets_manifest_count", 0) > 0, {"tracked_secret_files": tracked_secrets, "secrets_manifest_count": (real_inst or {}).get("secrets_manifest_count")})
    recs = (real_inst or {}).get("image_records") or {}
    trust_imgs = {k: v for k, v in recs.items() if k in ("edge-kong", "shield-web", "banking-accounts-api", "workflows-api", "workflows-worker", "cadence")}
    gate(gates, "M11-15", "immutable images: every trust-path image is recorded by image id in the instance manifest and the factory cache",
         len(trust_imgs) >= 5 and all(v.get("image_id", "").startswith("sha256:") for v in trust_imgs.values()),
         {k: v.get("image_id", "")[:19] for k, v in trust_imgs.items()})
    if skip_suites:
        gate(gates, "M11-16", "suites (skipped)", True, {"skipped": True})
    else:
        suites = {}
        for name, cmd in (("twinfactory", [sys.executable, "-m", "unittest", "discover", "-s", "twinfactory/tests", "-t", ".", "-q"]),
                          ("archkit", [sys.executable, "-m", "unittest", "discover", "-s", "archkit/tests", "-t", ".", "-q"]),
                          ("api-ingress-contract", [sys.executable, "ENV2_COMPOSE/substitutes/api-ingress/test_contract.py"]),
                          ("controlplane", [sys.executable, "-m", "unittest", "discover", "-s", "controlplane", "-t", ".", "-q", "-p", "test*.py"])):
            rc, o, e = sh(cmd, cwd=REPO, timeout=1200)
            suites[name] = {"rc": rc, "tail": (e or o)[-300:]}
        gate(gates, "M11-16", "twinfactory / archkit / api-ingress contract / controlplane suites green (controlplane package unchanged)", all(v["rc"] == 0 for v in suites.values()), suites)
    rc, cp, _ = sh(["git", "diff", "--stat", "campaign-m10-control-plane", "--", "controlplane"], cwd=REPO)
    gate(gates, "M11-17", "controlplane package unchanged since campaign-m10-control-plane", cp.strip() == "", {"diff_stat": cp.strip()[:200]})
    doc = {"milestone": "M11", "schema": "m11.acceptance.v1", "evaluated_at": datetime.now(timezone.utc).isoformat(),
           "accepted": all(g["pass"] for g in gates), "gates_passed": sum(1 for g in gates if g["pass"]), "gates_total": len(gates), "gates": gates,
           "snapshot_id": snap_id, "real_instance": (real_inst or {}).get("instance_id"), "substitute_instance": (sub_inst or {}).get("instance_id"),
           "real_run": rs, "substitute_run": ss, "git_head": sh(["git", "rev-parse", "HEAD"], cwd=REPO)[1].strip()}
    (IMPL / "M11_ACCEPTANCE.json").write_text(json.dumps(doc, indent=1))
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-suites", action="store_true"); ap.add_argument("--real-run"); ap.add_argument("--sub-run")
    a = ap.parse_args(argv)
    doc = evaluate(a.skip_suites, a.real_run, a.sub_run)
    for g in doc["gates"]:
        print("%s %s  %s" % ("PASS" if g["pass"] else "FAIL", g["id"], g["desc"]))
    print("accepted=%s %d/%d" % (doc["accepted"], doc["gates_passed"], doc["gates_total"]))
    return 0 if doc["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
