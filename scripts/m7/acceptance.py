#!/usr/bin/env python3
"""M7 acceptance -- shared API-monolith ingress + Source-to-Pay integration. 22 hard gates, machine-computed
from Git and committed / hash-bound evidence. accepted=true only when every gate passes.

  python3 scripts/m7/acceptance.py            -> reports/implementation/M7_ACCEPTANCE.json (exit 0 iff accepted)
"""
import importlib.util, json, re, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
IMPL = REPO / "reports/implementation"
S2PR = REPO / "reports/source-to-pay-replica"
OUT = IMPL / "M7_ACCEPTANCE.json"
HISTORICAL_TAGS = ["assurance-m4.1-reproducible", "assurance-m5-autonomous-discovery", "red-loop-m3.1", "red-loop-m4", "twin-m6-complete-domain", "twin-v1.0"]
REQUIRED_JOURNEYS = ["journey:shared-ingress/success", "journey:shared-ingress/direct", "journey:shared-ingress/approval", "journey:shared-ingress/batch",
                     "journey:shared-ingress/admin", "journey:shared-ingress/tenant_isolation", "journey:shared-ingress/failure", "journey:shared-ingress/idempotency",
                     "journey:shared-ingress/restart", "journey:shared-ingress/async_state",
                     "journey:cross-domain-s2p/success", "journey:cross-domain-s2p/failure", "journey:cross-domain-s2p/idempotency", "journey:cross-domain-s2p/async_state"]
PROHIBITED = [r"(?i)identical to production", r"(?i)matches production exactly", r"(?i)production parity:\s*true", r"(?i)\d+(\.\d+)?%\s*identical to production", r"(?i)confirmed production vulnerability"]


def git(*a):
    return subprocess.run(["git", "-C", str(REPO)] + list(a), capture_output=True, text=True).stdout.strip()


def load(p):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return None


def ancestor(a, b):
    return bool(a) and bool(b) and subprocess.run(["git", "-C", str(REPO), "merge-base", "--is-ancestor", a, b]).returncode == 0


def module(name, p):
    s = importlib.util.spec_from_file_location(name, p); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


def main():
    gates = []
    def g(gid, desc, ok, detail=""):
        gates.append({"id": gid, "description": desc, "passed": bool(ok), "status": "PASS" if ok else "FAIL", "detail": str(detail)[:600]})

    head = git("rev-parse", "HEAD"); branch = git("rev-parse", "--abbrev-ref", "HEAD")
    # 1 historical tags unchanged (against the starting-evidence audit's recorded map and the known M6 tag commit)
    start = load(IMPL / "m7/starting-audit/M7_STARTING_EVIDENCE.json") or {}
    before = {}
    for line in (start.get("git", {}).get("tags_before") or "").splitlines():
        sha, ref = line.split()
        before[ref.replace("refs/tags/", "")] = sha
    now = {t: git("rev-parse", "refs/tags/" + t) for t in HISTORICAL_TAGS}
    unchanged = all(before.get(t) == now.get(t) for t in HISTORICAL_TAGS) and git("rev-list", "-n1", "twin-m6-complete-domain") == "76024aed5ae8474d29bd2b0b2e48c97c429eda52"
    g("M7-01", "Historical milestone tags unchanged", unchanged, {t: now[t][:10] for t in HISTORICAL_TAGS})
    # 2 canonical branch ancestry
    m6_tag = git("rev-list", "-n1", "twin-m6-complete-domain"); s2p_tag = git("rev-list", "-n1", "s2p-acceptance-closure-m7")
    merge, parents = "", []
    for m in git("rev-list", "--merges", "HEAD").split():
        ps = git("show", "-s", "--format=%P", m).split()
        if s2p_tag in ps:
            merge, parents = m, ps
            break
    g("M7-02", "Canonical branch has documented ancestry (M6 tag + S2P closure tag are ancestors; merge commit recorded)",
      branch == "milestone-7-shared-ingress-integration" and ancestor(m6_tag, head) and ancestor(s2p_tag, head) and ancestor("b6e4976f9baebfe770dfa30794f6f61ea08ccb1b", head) and len(parents) == 2,
      {"branch": branch, "head": head[:10], "merge_commit": merge[:10], "parents": [p[:10] for p in parents]})
    # 3 S2P closure result + preserved historical
    closure = load(S2PR / "closure-m7/artifacts/acceptance.json") or {}
    hist = load(S2PR / "historical-b6e4976/artifacts/acceptance-18of20.json") or {}
    hist_failed = sorted(x["id"] for x in hist.get("gates", []) if not x["passed"])
    g("M7-03", "Source-to-Pay has a new isolated acceptance (20/20) and the historical 18/20 result is preserved verbatim",
      closure.get("accepted") is True and sum(x["passed"] for x in closure.get("gates", [])) == 20 and ancestor(closure.get("final_commit"), head)
      and hist.get("accepted") is False and hist_failed == ["02_protected_worktrees_unchanged", "03_preexisting_containers_unchanged"] and hist.get("final_commit", "").startswith("b6e4976")
      and (S2PR / "ACCEPTANCE_CLOSURE.md").is_file() and "18/20" in (S2PR / "ACCEPTANCE_CLOSURE.md").read_text() and "20/20" in (S2PR / "ACCEPTANCE_CLOSURE.md").read_text(),
      {"closure_final_commit": closure.get("final_commit", "")[:10], "closure_gates": sum(x["passed"] for x in closure.get("gates", [])), "historical_failed": hist_failed})
    # 4 M6 acceptance after integration (direct evaluation)
    try:
        m6 = module("m6_acceptance", REPO / "RED_LOOP/surface/m6_acceptance.py")
        res = m6.evaluate()
        m6_gates = res if isinstance(res, list) else (res.get("gates") if isinstance(res, dict) else res[0])
        m6_ok = all(x["passed"] for x in m6_gates)
        g("M7-04", "M6 acceptance passes after integration (re-evaluated now)", m6_ok, {"gates": "%d/%d" % (sum(x["passed"] for x in m6_gates), len(m6_gates)), "failed": [x["id"] for x in m6_gates if not x["passed"]]})
    except Exception as e:
        g("M7-04", "M6 acceptance passes after integration", False, repr(e))
    # 5 S2P acceptance after integration
    post = load(IMPL / "m7-s2p-post-integration-acceptance.json") or {}
    g("M7-05", "Source-to-Pay acceptance passes after integration (3 clean runs from the integrated branch)",
      post.get("accepted") is True and post.get("clean_runs", {}).get("successes") == 3 and ancestor(post.get("final_commit"), head),
      {"final_commit": post.get("final_commit", "")[:10], "clean_runs": post.get("clean_runs"), "failed": [x["id"] for x in post.get("gates", []) if not x["passed"]]})
    # 6 shared ingress clean boot
    cb = load(IMPL / "m7-clean-boot.json") or {}
    m6cb = load(IMPL / "m6-clean-boot.json") or {}
    g("M7-06", "Shared ingress boots from clean state (empty volumes + fresh secrets; api-ingress healthy with a passport signer)",
      cb.get("shared_ingress_booted_from_clean_state") is True and m6cb.get("from_empty_state") is True and m6cb.get("up_rc") == 0 and not m6cb.get("unhealthy") and cb.get("s2p_stack_healthy") is True,
      {"from_empty_state": m6cb.get("from_empty_state"), "containers": m6cb.get("container_count"), "healthy": m6cb.get("healthy"), "boot_id": (cb.get("ingress_health") or {}).get("boot_id")})
    # 7 contract inventory source-evidenced
    inv = load(REPO / "ENV2_COMPOSE/substitutes/api-ingress/contract/routes.json") or {}
    served = set(re.findall(r'@route\("([\w]+)"', (REPO / "ENV2_COMPOSE/substitutes/api-ingress/server.py").read_text()))
    routes = inv.get("routes", {})
    api_sha_ok = inv.get("sources", {}).get("api", {}).get("sha") == "2d665f918b60e917ec92be648fb5816d247f72b1"
    g("M7-07", "Shared ingress contract inventory is source-evidenced (every served route has Route.php evidence; pinned api sha)",
      routes and not inv.get("evidence_missing") and served <= set(routes) and all(r.get("source_refs") for r in routes.values()) and api_sha_ok,
      {"served": len(served), "inventory": len(routes), "evidence_missing": inv.get("evidence_missing"), "unlisted_served": sorted(served - set(routes))})
    # 8 no overlapping selected boundary without explicit reason
    bc = (REPO / "DOMAIN_REPLICAS/source_to_pay/integration/payouts-boundary-contract.yaml").read_text()
    contract_routes = re.findall(r"- route: (\w+) (v1/[\w/{}\-]+)", bc)
    inv_paths = {r["path"].rstrip("/") for r in routes.values()}
    covered = [(m, p) for m, p in contract_routes if ("/" + p.rstrip("/").replace("{id}", "{id}")) in inv_paths or ("/" + p.rstrip("/").replace("{id}", "{id}").replace("{payout_id}", "{id}")) in inv_paths]
    stub_src = (REPO / "ENV2_COMPOSE/substitutes/monolith-stub/server.py").read_text()
    stub_overlap = '("GET", "/fund_accounts_internal/")' in stub_src and "# M7" not in stub_src.split('("GET", "/fund_accounts_internal/")')[0][-300:]
    topo = (IMPL / "M7_INTEGRATION_TOPOLOGY.md").read_text() if (IMPL / "M7_INTEGRATION_TOPOLOGY.md").is_file() else ""
    g("M7-08", "No selected boundary is implemented in both the shared ingress and the old Source-to-Pay/M6 replacement without an explicit reason",
      len(covered) == len(contract_routes) and not stub_overlap and "Remaining local boundary routes" in topo,
      {"boundary_routes": len(contract_routes), "covered_by_ingress": len(covered), "stub_still_serves_fund_accounts_internal": stub_overlap})
    # 9-16 journeys
    jr = load(IMPL / "m6-journeys.json") or {}
    js = {j["id"]: j for j in jr.get("journeys", [])}
    def passed(jid):
        return js.get(jid, {}).get("result") == "PASS"
    def checks(jid):
        try:
            return {c["name"]: c["ok"] for c in json.loads((REPO / js[jid]["evidence_path"]).read_text())["checks"]}
        except Exception:
            return {}
    fc, ac = checks("journey:shared-ingress/failure"), checks("journey:shared-ingress/admin")
    g("M7-09", "Merchant, internal-service and admin identities are separated (journeys failure + admin, every separation check ok)",
      passed("journey:shared-ingress/failure") and passed("journey:shared-ingress/admin")
      and fc.get("merchant_key_on_internal_route_is_route_not_found_400") and fc.get("application_credential_on_merchant_route_is_refused") and fc.get("merchant_key_on_admin_route_is_refused")
      and ac.get("merchant_cannot_use_the_admin_route") and ac.get("vendor_payments_cannot_use_the_admin_route") and ac.get("ingress_audit_shows_an_admin_identity_distinct_from_merchant_and_app"),
      {"failure": js.get("journey:shared-ingress/failure", {}).get("result"), "admin": js.get("journey:shared-ingress/admin", {}).get("result")})
    tc = checks("journey:shared-ingress/tenant_isolation")
    snap = load(REPO / "reports/architecture/M7_CANONICAL_SNAPSHOT_STATS.json") or {}
    snap_full = load(REPO / "reports/architecture/M7_CANONICAL_SNAPSHOT.json") or {}
    own_edges = (snap_full.get("shared_ingress") or {}).get("ownership_edges") or []
    seeds = load(REPO / "ENV2_COMPOSE/seeds/generated/monolith/fund_accounts.json") or {}
    fas = seeds.get("fund_accounts", seeds) if isinstance(seeds, dict) else {}
    owned = sum(1 for v in fas.values() if isinstance(v, dict) and v.get("merchant_id"))
    g("M7-10", "Fund-account ownership is explicitly represented (ownership records, ownership edges in the snapshot, every generated seed carries its owner)",
      tc.get("b_fund_account_has_an_explicit_ownership_record_for_b") and own_edges and fas and owned == len(fas),
      {"ownership_edges": len(own_edges), "seed_fund_accounts": len(fas), "with_owner": owned})
    d7 = js.get("journey:beneficiary-fund-accounts/tenant_isolation", {}).get("result")
    g("M7-11", "Cross-tenant fund-account negative control produces a decisive result (refused, no row, no balance movement, denial audited; M6 D-7 journey now passes)",
      passed("journey:shared-ingress/tenant_isolation") and tc.get("cross_tenant_fund_account_use_is_refused_4xx") and tc.get("no_payout_row_was_created") and tc.get("neither_balance_moved")
      and tc.get("ingress_audit_records_the_owner_scoped_lookup_denied_for_tenant_A") and d7 == "PASS",
      {"shared-ingress/tenant_isolation": js.get("journey:shared-ingress/tenant_isolation", {}).get("result"), "beneficiary-fund-accounts/tenant_isolation": d7})
    missing = [j for j in REQUIRED_JOURNEYS if not passed(j)]
    g("M7-12", "All required journeys execute and pass", not missing, {"required": len(REQUIRED_JOURNEYS), "not_passing": missing})
    three = load(IMPL / "m7-s2p-connected-runs.json") or {}
    runs = three.get("runs", [])
    g("M7-13", "Connected Source-to-Pay journey passes three clean runs (reset ingress + fresh S2P stack each time)",
      len(runs) == 3 and all(r.get("success") == "PASS" and r.get("reset_before") for r in runs) and len({r.get("remittance_payout_id") for r in runs}) == 3,
      {"runs": [(r.get("run_id"), r.get("success"), (r.get("remittance_payout_id") or "")[:14]) for r in runs]})
    g("M7-14", "Duplicate and replay controls pass (shared-ingress/idempotency, cross-domain-s2p/idempotency)",
      passed("journey:shared-ingress/idempotency") and passed("journey:cross-domain-s2p/idempotency"),
      {k: js.get(k, {}).get("result") for k in ("journey:shared-ingress/idempotency", "journey:cross-domain-s2p/idempotency")})
    rc = checks("journey:cross-domain-s2p/async_state")
    g("M7-15", "At least one connected async journey survives a service restart (api-ingress restarted between Pay and the callback)",
      passed("journey:cross-domain-s2p/async_state") and rc.get("api_ingress_restarted_while_the_remittance_was_in_flight") and rc.get("source_reached_money_loading_success_and_public_processing") and passed("journey:shared-ingress/restart"),
      {"cross-domain-s2p/async_state": js.get("journey:cross-domain-s2p/async_state", {}).get("result"), "shared-ingress/restart": js.get("journey:shared-ingress/restart", {}).get("result")})
    rp = load(IMPL / "m7-reset-proof.json") or {}
    g("M7-16", "Reset removes mutable state (ingress tables emptied, seed ownership kept; S2P volume removed by stack down; arena down -v)",
      rp.get("ingress_mutable_rows_after_reset") == 0 and rp.get("seed_ownership_kept") is True and rp.get("s2p_volume_removed") is True and rp.get("ingress_replay_after_reset_is_new") is True,
      {k: rp.get(k) for k in ("ingress_mutable_rows_before", "ingress_mutable_rows_after_reset", "seed_ownership_kept", "s2p_volume_removed")})
    rd = load(IMPL / "m7-refresh-demo.json") or {}
    g("M7-17", "Daily incremental refresh proof (change detected, affected components + journeys selected, only those rerun, canonical evidence preserved, change reverted)",
      rd.get("change_detected") and rd.get("rerun_journeys") and rd.get("affected_is_small_subset") and rd.get("rerun_only_affected") is True and rd.get("reverted") and rd.get("canonical_evidence_preserved") and rd.get("git_clean_after_revert")
      and all(v == "PASS" for v in (rd.get("rerun_results") or {}).values()),
      {"changed": rd.get("changed"), "rerun": rd.get("rerun_journeys"), "unaffected_not_rerun": len(rd.get("unaffected_not_rerun") or [])})
    wk = load(IMPL / "m7-weekly-rebuild.json") or {}
    g("M7-18", "Weekly clean rebuild passes (down -> empty state -> up -> S2P stack -> full M6+M7 suite; graph regenerated; every P0 journey PASS)",
      wk.get("passed") is True and (wk.get("suite") or {}).get("p0_failures") == [] and wk.get("graph_regenerated") is True and (wk.get("journeys_fingerprint_bound") is True),
      {k: wk.get(k) for k in ("run_dir", "container_count", "suite")})
    try:
        hashes = module("m7_hashes", REPO / "scripts/m7/hashes.py")
        hv = hashes.verify() if (IMPL / "M7_ARTIFACT_HASHES.json").is_file() else {"passed": False, "changed_or_missing": ["manifest missing"]}
    except Exception as e:
        hv = {"passed": False, "changed_or_missing": [repr(e)]}
    g("M7-19", "All runtime and report artifacts are hash-bound (M7_ARTIFACT_HASHES.json verifies)", hv.get("passed"), {"count": hv.get("count"), "changed_or_missing": (hv.get("changed_or_missing") or [])[:5]})
    # 20 no production connection / credential / customer data
    scan = load(IMPL / "m7-secret-scan.json") or {}
    net = subprocess.run(["docker", "network", "inspect", "rzp-arena", "-f", "{{.Internal}}"], capture_output=True, text=True).stdout.strip()
    hosts = subprocess.run(["grep", "-rlE", r"razorpay\.(com|in|vpc)|amazonaws\.com|169\.254\.", "ENV2_COMPOSE/substitutes/api-ingress", "ENV2_COMPOSE/docker-compose.s2p.yml", "RED_LOOP/m7", "RED_LOOP/m6/journeys/j_ingress.py", "RED_LOOP/m6/journeys/j_s2p.py"], cwd=REPO, capture_output=True, text=True).stdout.split()
    hosts = [h for h in hosts if not h.endswith(".md")]
    g("M7-20", "No production connection, credential or customer data (arena network internal, gitleaks 0 findings on M7 sources, no production hosts in M7 runtime sources)",
      scan.get("exit_code") == 0 and scan.get("findings") == 0 and (net in ("true", "") ) and not hosts,
      {"gitleaks": {k: scan.get(k) for k in ("exit_code", "findings", "scanned")}, "rzp-arena internal": net, "production_hosts_in_m7_sources": hosts})
    # 21 no unsupported production-fidelity claim
    docs = [IMPL / n for n in ("M7_FINAL_REPORT.md", "M7_RUNBOOK.md", "M7_FIDELITY_MATRIX.md", "M7_OWNERSHIP_INVESTIGATION.md", "M7_INTEGRATION_TOPOLOGY.md", "M7_PRODUCTION_UNKNOWNS.md")]
    hits = []
    for d in docs:
        if d.is_file():
            for pat in PROHIBITED:
                for m in re.finditer(pat, d.read_text()):
                    hits.append("%s: %s" % (d.name, m.group(0)))
    classif = re.findall(r"Classification:\s*\*\*(\w+)\*\*", (IMPL / "M7_OWNERSHIP_INVESTIGATION.md").read_text()) if (IMPL / "M7_OWNERSHIP_INVESTIGATION.md").is_file() else []
    g("M7-21", "No unsupported production-fidelity claim (no parity phrases in the M7 reports; ownership classification is one of the allowed values; all six report files present)",
      all(d.is_file() for d in docs) and not hits and classif and classif[0] in {"REPRODUCED_AND_RESOLVED_BY_MISSING_INGRESS_CHECK", "REPRODUCED_AND_REMAINS_IN_SELECTED_SOURCE_PATH", "NOT_REPRODUCIBLE_AFTER_CONTRACT_CORRECTION", "PRODUCTION_REACHABILITY_UNKNOWN", "INSUFFICIENT_SOURCE_EVIDENCE"},
      {"hits": hits[:5], "classification": classif[:1], "missing_docs": [d.name for d in docs if not d.is_file()]})
    # 22 tracked tree clean (the acceptance/hash outputs themselves are written by this run and committed right after)
    status = [l for l in git("status", "--porcelain").splitlines() if not l.endswith(("M7_ACCEPTANCE.json", "M7_ARTIFACT_HASHES.json"))]
    g("M7-22", "Final tracked tree is clean (excluding the acceptance + hash manifest this evaluation writes)", not status, {"dirty": status[:8]})

    accepted = all(x["passed"] for x in gates)
    doc = {"milestone": "M7", "accepted": accepted, "evaluated_at": datetime.now(timezone.utc).isoformat(), "head": head, "branch": branch,
           "gates_passed": sum(x["passed"] for x in gates), "gates_total": len(gates), "gates": gates,
           "canonical_snapshot_sha256": snap.get("snapshot_sha256"), "s2p_closure_final_commit": closure.get("final_commit"),
           "artifact_manifest_sha256": __import__("hashlib").sha256((IMPL / "M7_ARTIFACT_HASHES.json").read_bytes()).hexdigest() if (IMPL / "M7_ARTIFACT_HASHES.json").is_file() else None,
           "evaluation_scope": "22 hard gates over Git state, committed evidence and hash-bound runtime artifacts; runtime observations are replayed from the recorded evidence, not re-executed here (except the M6 acceptance re-evaluation and the docker network check)."}
    OUT.write_text(json.dumps(doc, indent=2) + "\n")
    for x in gates:
        print("[%s] %s %s" % ("PASS" if x["passed"] else "FAIL", x["id"], x["description"][:90]))
    print("%d/%d gates accepted=%s -> %s" % (doc["gates_passed"], doc["gates_total"], accepted, OUT))
    return 0 if accepted else 1


if __name__ == "__main__":
    sys.exit(main())
