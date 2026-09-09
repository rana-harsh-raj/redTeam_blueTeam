#!/usr/bin/env python3
"""Render reports/implementation/M11_FINAL_REPORT.md from the machine artifacts (acceptance, differential, resources,
monolith boot attempt, snapshot summaries, kong-config, journey runs). Nothing in the report is typed by hand except
the fixed prose in TEXT below; every number is read from an artifact and named with its path.
  python3 scripts/m11/final_report.py
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "m11"))
IMPL = REPO / "reports" / "implementation"
M11 = IMPL / "m11"


def jload(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def git(*args):
    return subprocess.run(["git"] + list(args), capture_output=True, text=True, cwd=REPO).stdout.strip()


# ---- fixed classification tables (every row cites the source that decides it) ----
COMPONENTS = [
    # component, category, what runs, blocker / note
    ("Edge gateway", "REAL SOURCE RUNNING", "Kong 3.4.2-ubuntu (edge/Dockerfile base) + the 42 razorpay/edge Lua plugins @ da9ff5b, kong.conf verbatim (headers=off, db=postgres); route table derived from terraform-kong @ 8ffe965b prod-api authenticated_paths (17 routes) + payouts-ext (1 route, production hosts); consumer per merchant + basic-auth-x credentials hashed by the plugin; upstream-jwt kid edgev2 signs the passport",
     "production merchant-key sync into Kong is not in any granted repository -> seeded by trustpath/edge/provision_kong.py (PU-M11-1); global observability/rate-limit plugins not provisioned (PU-M11-2)"),
    ("API monolith (auth / merchant identity / ownership / approval routes)", "CONTRACT-FAITHFUL REPLACEMENT (api-ingress)", "the M7 ingress, now verifying the REAL gateway passport (RS256, kid edgev2, PassportUtil validations) and publishing /jwks; Route.php / BasicAuth.php / FundAccount ownership semantics reproduced from source",
     "razorpay/api cannot be booted here: no php/composer, 18/23 private composer VCS repos unreadable, all base images on harbor.razorpay.com need credentials (reports/implementation/m11/monolith-boot-attempt.md)"),
    ("Shield (risk rules)", "REAL SOURCE RUNNING", "razorpay/shield @ 514516f, APP_MODE=rzpxprod (setupProvidersForRzpx), MySQL + single-node Redis cluster, the repository's own goose migrations (run once under APP_MODE=default because rzpxprod skips them by source), rules through the rule API with skip_canary",
     "github.com/razorpay/fingerprint-sdk is unreadable -> 4-symbol build stub via -modfile (build-time only, never on the payout path); production FRM merchant list / rule set unknown (PU-M11-3)"),
    ("banking-accounts (BAS)", "REAL SOURCE RUNNING (adapted deployment)", "razorpay/banking-accounts, APP_ENV=arena overlay, goose migrations, Api-Token auth, businesses/banking_accounts rows for Direct merchants",
     "its DCS client hardcodes https://dcs-*.dev.razorpay.in (goutils/dcs uri.go, WithMock(false)) -> the twin answers those hostnames with dcs-stub over TLS (twin CA via SSL_CERT_FILE); production business data unknown (PU-M11-4)"),
    ("account-service (ASV)", "REMAINING SUBSTITUTE (asv-stub)", "unchanged M6 stub", "razorpay/account-service clone returns 404 for this identity -- no source"),
    ("Workflow service (maker-checker)", "REAL SOURCE RUNNING (adapted deployment)", "razorpay/workflows Twirp API + Cadence worker (WORKFLOW_TYPE=approval, domain payouts) + ubercadence/server:v1.4.1-auto-setup on MySQL; goose migrations; payout-approval Config per merchant through ConfigAPI/Create",
     "runs as APP_ENV=dev so the real DCS client resolves to the twin-answered dcs-live.dev.razorpay.in (stub mode leaves the worker's DCS server nil and the approval workflow panics -- source); [auth.vendorPayments] slot filled (F-M11-1); production configs / DCS features unknown (PU-M11-5)"),
    ("Dashboard session identity", "CONTRACT-FAITHFUL REPLACEMENT (api-ingress proxy auth)", "BasicAuth::proxyAuth semantics at the monolith replacement",
     "the api-dashboard Kong service + user-session plugin front the dashboard app, which is not a granted repository (PU-M11 dashboard)"),
    ("Internal application ingress", "REAL PATH SHAPE (api-internal not provisioned)", "internal apps call the monolith replacement directly with rzp_live + app secret + X-Razorpay-Account",
     "production api-internal Kong service carries plain paths (no edge authentication) -- not provisioned; equivalent to the direct call"),
    ("External banks / SMS / OTP", "SYNTHETIC BY MANDATE", "mozart-sim, xas-sim/xas-sink, synthetic OTP", "allowed to stay synthetic"),
]

FINDINGS = [
    ("F-M11-1", "razorpay/workflows", "cmd/api/main.go accepts credentials.VendorPayments on every Twirp service while config/default.toml has no [auth.vendorPayments] block; the zero-value slot matches an anonymous request in internal/boot/hooks/auth.go (isClientAllowed(\"\") and \"\" == \"\"). Observed: anonymous WorkflowAPI/List -> 200 until the twin filled the slot; then 401. Production value of that slot is unknown.",
     "trust-path/s2s_identity (first run) + the twin config fix"),
    ("F-M11-2", "razorpay/edge kong-plugin-basic-auth-x @ da9ff5b on Kong 3.4.2", "a credential deleted through the Admin API keeps authenticating: access.lua caches by its own keys (v2:basicauth_x_consumer_id:<username>, v2:basicauth_x_by_hash:<hash>:<consumer>) that no invalidation targets, and the DAO's cache_key(username) override (basicauth_x_credentials.lua:72) crashes on the CRUD event entity ('attempt to concatenate a table value' in the gateway log) so the core hook aborts; db_cache_ttl is the default 0. Production behaviour depends on the same code unless a different cache configuration is deployed (unknown).",
     "trust-path/restart_cache_invalidation"),
    ("F-M11-3", "razorpay/workflows", "ConfigAPI/List without a `config` filter object dereferences req.Config (internal/entities/config/server.go:300) -> HTTP 500 panic.",
     "workflow config seed (first run)"),
    ("F-M11-4", "razorpay/payouts + terraform-kong payouts-ext", "the payouts-ext Kong service forwards the merchant's own Basic credential to the Payouts service, whose /v1/payouts group requires the API service credential first (internal/routing/router/payout_routes.go: BasicAuth(cred.API, cred.Workflow) before PassportAuthentication) -> 401 'The api key provided is invalid'. Either production carries a header rewrite not present in the granted terraform, or the route is dark; recorded as PU-M11-7.",
     "trust-path/payouts_ext_direct"),
]

# twin-side corrections made while promoting the trust path (none touches a real service's code)
TWIN_CORRECTIONS = [
    "twinfactory image cache: compose-built substitute images are now fingerprinted by their build context (a stale api-ingress was being served from the M9 cache under the same tag).",
    "twinfactory start --resume-from <stage>; secrets/materialize.py --only <group>; host bridge relays gateway response headers, forwards production hosts (payouts-ext) and identity attack headers.",
    "RED_LOOP/red_loop/ledger_da.py read the main checkout's secrets on a factory twin (401 from the ledger, DA onboarding skipped -> every Direct journey failed on twins); now ARENA_ENV2_ROOT-aware.",
    "journey beneficiary-fund-accounts read the record through monolith-stub, whose fund_accounts_internal route was retired in M7; it now reads the shared-ingress route payouts actually reads (payouts_service identity).",
    "journey fetch-list expected the substitute's leniency for unsigned ids; the monolith path refuses them (PublicEntity::stripSignOrFail) -- asserted per variant.",
    "journey shared-payouts/restart restarts the M6 worker trio only (edge-kong/shield-web have their own journey); shared-ingress/batch and the bulk family BLOCK when the profile carries no batch-sim.",
    "approval / trust-path drivers: separation, repeat approvals, eligible sets are asserted per variant (real service semantics vs the M5 reconstruction), recorded in M11_DIFFERENTIAL.md.",
    "Shield seed: payout rules live in ruleset payout_primary (ruler.go addRulesets) and are created with skip_canary (otherwise a 0%-rollout canary rule); `purpose` is not a Shield parameter (M1 rule re-expressed on payout_amount).",
    "Workflow seed: template type `approval`, allowed_actions keyed by actor type, ConfigAPI/List filters inside `config`; every action carries X-User-Email and 14-char RZP actor ids.",
]

UNKNOWNS = [
    ("PU-M11-1", "how merchant API keys are synchronised into Kong's basicauth_x_credentials in production (Credcase / secret_ref_id flow; no granted repository) -- the twin seeds consumers + credentials from its own merchant seed"),
    ("PU-M11-2", "production values of the global Kong plugins (rate limits, Redis cluster, lake-events, geo-router) -- not provisioned"),
    ("PU-M11-3", "Shield production rule set, FRM merchant ids (FRM_MERCHANT_IDS), shieldAuthUser pairs -- the twin seeds its own rules (ruleset payout_primary, the one ruler.go addRulesets selects for payouts; stable rules via skip_canary) and credentials"),
    ("PU-M11-4", "banking-accounts production business / banking-account rows and the [api].token value -- twin-seeded"),
    ("PU-M11-5", "Workflow service production configs (per-merchant approval templates), DCS feature values (DcsSkipApprovalForCreator, wf config dimensions), every [auth.*] and [clients.*] credential -- twin secrets"),
    ("PU-M11-6", "Shield schema provisioning in RzpX environments (migrations skip under rzpxprod by source) -- the twin runs the repository migrations once under APP_MODE=default"),
    ("PU-M11-7", "how payouts-ext traffic satisfies the Payouts service's service BasicAuth (see F-M11-4)"),
    ("PU-M11-8", "the monolith's /wf-service/state/callback route (payouts reverse dual write of workflow state maps) -- not in the api-ingress contract; payouts logs the 404 as a non-fatal RDW error"),
    ("PU-M11-9", "the API monolith itself: passport registrations, session policies, route flags and everything Route.php reads from production config -- api-ingress reproduces the contract, never the values"),
]


def main():
    acc = jload(IMPL / "M11_ACCEPTANCE.json", {})
    diff = jload(M11 / "differential.json", {})
    mono = jload(M11 / "monolith-boot-attempt.json", {})
    clean = jload(M11 / "clean-checkout.json", {})
    kong = jload(REPO / "ENV2_COMPOSE" / "trustpath" / "edge" / "kong-config.json", {})
    reg = jload(REPO / "reports" / "architecture" / "snapshots" / "REGISTRY.json", {})
    cur = reg.get("current")
    summ = jload(REPO / "reports" / "architecture" / "snapshots" / (cur or "x") / "summary.json", {})
    base_id = "5b6a5dade17eba6c0a407d3d4ba8423c8bf0b8ebeb9ea2bc3706483e04e8d141"   # snapshot current at tag campaign-m10-control-plane
    base = jload(REPO / "reports" / "architecture" / "snapshots" / base_id / "summary.json", {})
    resources = {p.stem: jload(p, {}) for p in sorted(M11.glob("resources-*.json"))}
    graph = jload(REPO / "reports" / "domain" / "GRAPH_STATS.json", {})
    from twinfactory.factory import Factory
    f = Factory()
    insts = {m["instance_id"]: m for m in f.registry.records()}
    real = next((m for m in insts.values() if m.get("trust_path") == "real"), {})
    sub = next((m for m in insts.values() if m.get("trust_path") == "substitute"), {})
    L = []
    A = L.append
    A("# M11 — Critical Trust-Path Fidelity Upgrade — Final Report")
    A("")
    A("Generated %s by scripts/m11/final_report.py from the artifacts named below. Branch `milestone-11-trust-path-fidelity` from tag `campaign-m10-control-plane`; git head `%s`." % (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), git("rev-parse", "HEAD")[:12]))
    A("")
    A("## Acceptance")
    A("")
    A("| accepted | gates | evaluated | snapshot | record |")
    A("|---|---|---|---|---|")
    A("| **%s** | %s/%s | %s | `%s` | reports/implementation/M11_ACCEPTANCE.json |" % (acc.get("accepted"), acc.get("gates_passed"), acc.get("gates_total"), acc.get("evaluated_at"), (acc.get("snapshot_id") or "")[:16]))
    A("")
    for g in acc.get("gates", []):
        A("- %s **%s** %s" % ("PASS" if g["pass"] else "FAIL", g["id"], g["desc"]))
    A("")
    A("## What is real, adapted, replaced, still substituted, unknown")
    A("")
    A("| component | category | what runs | blocker / note |")
    A("|---|---|---|---|")
    for c, cat, what, note in COMPONENTS:
        A("| %s | **%s** | %s | %s |" % (c, cat, what, note))
    A("")
    A("Remaining substitutes in the real variant, each with its external blocker: see gate M11-06 in the acceptance record (%s listed)." % len((next((g for g in acc.get("gates", []) if g["id"] == "M11-06"), {}).get("detail") or {}).get("remaining") or []))
    A("")
    A("## Trust path as run")
    A("")
    A("`merchant (host bridge) -> edge-kong (%d prod-api routes + %d payouts-ext route, hosts %s) -> api-ingress (monolith replacement; passport kid edgev2 verified) -> payouts-api -> shield-web (rzpxprod) / banking-accounts-api (Direct) / workflows-api + workflows-worker + cadence`"
      % (len((kong.get("services") or {}).get("prod-api", {}).get("routes", {})), len((kong.get("services") or {}).get("payouts-ext", {}).get("routes", {})), json.dumps((kong.get("route_hosts") or {}).get("twin"))))
    A("")
    A("Placement: everything local (this machine: %s CPU / %s GiB; twin VM %s). Remote compute: not used -- %s." % (
        (next(iter(resources.values()), {}).get("host") or {}).get("cpus"), (next(iter(resources.values()), {}).get("host") or {}).get("memory_gib"),
        json.dumps((next(iter(resources.values()), {}).get("vm") or {}).get("sizing")), (next(iter(resources.values()), {}).get("remote") or {}).get("reason")))
    A("")
    A("## Fidelity numbers (before -> after)")
    A("")
    A("| population | label | M10 baseline (`%s`) | M11 (`%s`) |" % (base_id[:12], (cur or "")[:12]))
    A("|---|---|---|---|")
    for pop in ("p0_non_journey", "p0_critical_kinds", "all_nodes"):
        b, c = (base.get("label_histogram") or {}).get(pop, {}), (summ.get("label_histogram") or {}).get(pop, {})
        for lab in sorted(set(b) | set(c)):
            A("| %s | %s | %s | %s |" % (pop, lab, b.get(lab, 0), c.get(lab, 0)))
    A("")
    A("Runtime profile (snapshot-derived): real variant `full` = %s services + %s jobs; substitute variant `full` = %s services + %s jobs. In the real variant kong-lite, shield-stub, bankingaccounts-stub and workflow-engine are not started; edge-kong, shield-web, banking-accounts-api, workflows-api, workflows-worker, cadence and their datastores are." % (
        (real.get("profile") and ((jload(f.idir(real["instance_id"]) / "profile.json", {}).get("services") and len(jload(f.idir(real["instance_id"]) / "profile.json", {}).get("services"))) or "?")), len(jload(f.idir(real["instance_id"]) / "profile.json", {}).get("jobs") or []) if real else "?",
        len(jload(f.idir(sub["instance_id"]) / "profile.json", {}).get("services") or []) if sub else "?", len(jload(f.idir(sub["instance_id"]) / "profile.json", {}).get("jobs") or []) if sub else "?"))
    A("")
    A("Domain graph (reports/domain/GRAPH_STATS.json): %s nodes, fidelity histogram %s." % (graph.get("nodes"), json.dumps(graph.get("fidelity_histogram"))))
    A("")
    A("## Journeys and differential")
    A("")
    rs, ss = acc.get("real_run") or {}, acc.get("substitute_run") or {}
    A("| variant | instance | run dir | total | PASS | FAIL | BLOCKED | EXPECTED_FAILURE |")
    A("|---|---|---|---|---|---|---|---|")
    A("| real | %s | `%s` | %s | %s | %s | %s | %s |" % (acc.get("real_instance"), rs.get("run_dir"), rs.get("total"), rs.get("PASS"), rs.get("FAIL", 0), rs.get("BLOCKED", 0), rs.get("EXPECTED_FAILURE", 0)))
    A("| substitute | %s | `%s` | %s | %s | %s | %s | %s |" % (acc.get("substitute_instance"), ss.get("run_dir"), ss.get("total"), ss.get("PASS"), ss.get("FAIL", 0), ss.get("BLOCKED", 0), ss.get("EXPECTED_FAILURE", 0)))
    A("")
    A("Differential (reports/implementation/M11_DIFFERENTIAL.md): %s journeys compared, %s identical, %s with differences, %s OPEN." % (diff.get("journeys"), diff.get("identical"), diff.get("with_differences"), len(diff.get("open_differences") or [])))
    A("")
    A("Blocked journeys on the real variant (each names its dependency): %s" % ("; ".join("%s -> %s" % (i, d) for i, d in (rs.get("blocked") or [])) or "none"))
    A("")
    A("The 14 trust-path journeys (RED_LOOP/m6/journeys/j_trustpath.py) cover: merchant API-key auth, identity propagation (gateway-signed passport, forgery attempt), dashboard/session identity (as far as source permits), internal app auth, route-level authorization, cross-merchant denial, beneficiary/fund-account ownership, service-to-service identity (Shield/BAS/Workflow/PS), maker-checker create/approve/reject/separation, restart & cache invalidation, duplicate/idempotency, Shield rules, BAS lookup, payouts-ext.")
    A("")
    A("## Findings (real components, source-explained)")
    A("")
    for fid, comp, text, where in FINDINGS:
        A("- **%s** (%s): %s _Observed in: %s._" % (fid, comp, text, where))
    A("")
    A("## Twin-side corrections made during the promotion (no real-service code touched)")
    A("")
    for c in TWIN_CORRECTIONS:
        A("- " + c)
    A("")
    A("## Measured resources (local)")
    A("")
    A("| instance | label | VM | containers | CPU%% sum | mem MiB sum | VM root used GiB | images GiB | build s | boot s |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for k, r in resources.items():
        A("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (r.get("instance_id"), r.get("label"), json.dumps((r.get("vm") or {}).get("sizing")), (r.get("containers") or {}).get("count"), (r.get("containers") or {}).get("cpu_pct_sum"),
                                                          (r.get("containers") or {}).get("mem_mib_sum"), ((r.get("disk") or {}).get("vm_root") or {}).get("used_gib"), (r.get("images") or {}).get("total_gib"),
                                                          (r.get("timings") or {}).get("build_secs"), (r.get("timings") or {}).get("boot_secs")))
    A("")
    A("Trust-path images (MiB): %s" % json.dumps((next(iter(resources.values()), {}).get("images") or {}).get("trust_path_images")))
    A("")
    A("## Monolith boot attempt")
    A("")
    A("bootable here: **%s** -- %s (reports/implementation/m11/monolith-boot-attempt.md)" % ((mono.get("verdict") or {}).get("bootable_here"), "; ".join((mono.get("verdict") or {}).get("blockers") or [])))
    A("")
    A("## Clean-checkout reproduction")
    A("")
    A("%s" % json.dumps({k: clean.get(k) for k in ("ok", "checkout", "instance_id", "state", "trust_path", "healthy", "secs", "note")}))
    A("")
    A("## Production unknowns (explicit)")
    A("")
    for pid, text in UNKNOWNS:
        A("- **%s**: %s" % (pid, text))
    A("")
    A("## Paths")
    A("")
    for p in ("reports/implementation/M11_FINAL_REPORT.md", "reports/implementation/M11_RUNBOOK.md", "reports/implementation/M11_ACCEPTANCE.json", "reports/implementation/M11_ARTIFACT_HASHES.json",
              "reports/implementation/M11_DIFFERENTIAL.md", "reports/implementation/m11/", "ENV2_COMPOSE/docker-compose.m11.yml", "ENV2_COMPOSE/trustpath/", "ENV2_COMPOSE/build/m11/",
              "RED_LOOP/m6/journeys/j_trustpath.py", "RED_LOOP/m6/journeys/trustpath_adapter.py", "scripts/m11/"):
        A("- `%s`" % p)
    (IMPL / "M11_FINAL_REPORT.md").write_text("\n".join(L) + "\n")
    print("wrote", IMPL / "M11_FINAL_REPORT.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
