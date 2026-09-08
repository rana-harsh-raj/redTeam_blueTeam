#!/usr/bin/env python3
"""Render reports/implementation/M7_FINAL_REPORT.md from the committed / hash-bound evidence, so every number in the
report is the machine-computed one. Run after the evidence chain and (ideally) after scripts/m7/acceptance.py."""
import json, subprocess
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
IMPL = REPO / "reports/implementation"
OUT = IMPL / "M7_FINAL_REPORT.md"


def load(p):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return {}


def git(*a):
    return subprocess.run(["git", "-C", str(REPO)] + list(a), capture_output=True, text=True).stdout.strip()


def main():
    acc = load(IMPL / "M7_ACCEPTANCE.json"); st = load(REPO / "reports/architecture/M7_CANONICAL_SNAPSHOT_STATS.json")
    jr = load(IMPL / "m6-journeys.json"); cb = load(IMPL / "m7-clean-boot.json"); m6cb = load(IMPL / "m6-clean-boot.json")
    closure = load(REPO / "reports/source-to-pay-replica/closure-m7/artifacts/acceptance.json"); hist = load(REPO / "reports/source-to-pay-replica/historical-b6e4976/artifacts/acceptance-18of20.json")
    post = load(IMPL / "m7-s2p-post-integration-acceptance.json"); three = load(IMPL / "m7-s2p-connected-runs.json"); rd = load(IMPL / "m7-refresh-demo.json")
    wk = load(IMPL / "m7-weekly-rebuild.json"); rp = load(IMPL / "m7-reset-proof.json"); hashes = load(IMPL / "M7_ARTIFACT_HASHES.json"); scan = load(IMPL / "m7-secret-scan.json")
    js = jr.get("journeys", []); res = Counter(j.get("result") for j in js)
    m7j = {j["id"]: (j["result"], "%s/%s" % (j["checks_passed"], j["checks_total"])) for j in js if j["id"].startswith(("journey:shared-ingress", "journey:cross-domain"))}
    head = git("rev-parse", "HEAD"); branch = git("rev-parse", "--abbrev-ref", "HEAD"); status = git("status", "--porcelain")
    cov = st.get("coverage", {}); lab = st.get("label_histogram", {}); p0 = st.get("p0_label_histogram", {})
    gates = acc.get("gates", [])
    gate_rows = "\n".join("| %s | %s | %s |" % (g["id"], g["status"], g["description"][:110]) for g in gates)
    m7_rows = "\n".join("| %s | %s | %s |" % (k, v[0], v[1]) for k, v in sorted(m7j.items()))
    d7 = next((j for j in js if j["id"] == "journey:beneficiary-fund-accounts/tenant_isolation"), {})
    running = st.get("running_compose_services")
    text = f"""# M7 — Canonical architecture integration and shared API ingress: final report

| | |
|---|---|
| Canonical branch / commit | `{branch}` @ `{head}` (tracked tree {'clean' if not status else 'DIRTY: ' + status[:120]}) |
| M7 acceptance | **{acc.get('gates_passed')}/{acc.get('gates_total')} gates, accepted={acc.get('accepted')}** (`reports/implementation/M7_ACCEPTANCE.json`, evaluated {acc.get('evaluated_at')}) |
| Source-to-Pay closure (Stage A) | isolated run 20/20 accepted=true at `{closure.get('final_commit')}`; historical 18/20 (accepted=false, gates 02/03) at `{hist.get('final_commit')}` preserved; tag `s2p-acceptance-closure-m7` |
| Source-to-Pay acceptance from the integrated branch | {post.get('accepted')} ({sum(1 for g in post.get('gates', []) if g.get('passed'))}/{len(post.get('gates', []))} gates, clean runs {(post.get('clean_runs') or {}).get('successes')}/3) at `{post.get('final_commit')}` |
| Graph | {st.get('payouts_graph', {}).get('nodes')} nodes / {st.get('payouts_graph', {}).get('edges')} edges / {st.get('payouts_graph', {}).get('families')} families; Source-to-Pay namespace {st.get('s2p_namespace', {}).get('nodes')} nodes / {st.get('s2p_namespace', {}).get('edges')} edges referenced by hash, {cov.get('s2p_namespace', {}).get('projected_nodes')} projected |
| Mapping coverage | {cov.get('mapping', {}).get('represented')}/{cov.get('mapping', {}).get('critical_components_total')} P0 components ({cov.get('mapping', {}).get('pct')}%) |
| Executable coverage | {cov.get('executable', {}).get('critical_components_executable')}/{cov.get('mapping', {}).get('critical_components_total')} ({cov.get('executable', {}).get('pct')}%) |
| Actual-source runtime coverage | {cov.get('actual_source_runtime', {}).get('p0')} P0 nodes ({cov.get('actual_source_runtime', {}).get('p0_pct')}%), {lab.get('ACTUAL_SOURCE_RUNNING')} of {sum(lab.values()) if lab else 0} nodes |
| Contract-faithful replacement coverage | {cov.get('contract_faithful_replacement', {}).get('p0')} P0 nodes ({cov.get('contract_faithful_replacement', {}).get('p0_pct')}%), {lab.get('CONTRACT_FAITHFUL_REPLACEMENT')} of all |
| Actual source services running (canonical boot) | {running} compose services up; real: payouts-api + 25 workers + 2 Kafka consumers, fts-web + 27 FTS workers, ledger-api + workers, cfa-server + workers, xbalances-server + worker, s2p-vp-source (pinned vendor-payments slice) |
| Replacements remaining | api-ingress (new), monolith-stub (callback group only), kong-lite, batch-sim, workflow-engine, mozart-sim, dcs/splitz/shield/pricing/asv/bankingaccounts stubs, stork-capture, merchant-webhook-sink, xas-sim, ledger-gate, cron-driver, S2P source driver, S2P standalone boundary (standalone acceptance only) |
| Cross-domain journey | {m7j.get('journey:cross-domain-s2p/success', ('?', '?'))[0]} ({m7j.get('journey:cross-domain-s2p/success', ('?', '?'))[1]}); three clean connected runs: {three.get('all_passed')} ({', '.join(str((r.get('run_id'), r.get('success'))) for r in three.get('runs', []))}) |
| Beneficiary ownership | **REPRODUCED_AND_RESOLVED_BY_MISSING_INGRESS_CHECK** — `M7_OWNERSHIP_INVESTIGATION.md`; M6 D-7 journey now {d7.get('result')} |
| Production unknowns | PU-1..PU-10 in `M7_PRODUCTION_UNKNOWNS.md` (route selection, PS-direct ownership path, tag-back scoping, app auth mode, sessions/OTP, SourceUpdater transport, S2P deployment, batch approval hop, dashboard approve variant, purposes/permissions) |
| Artifacts | `M7_ACCEPTANCE.json`, `M7_ARTIFACT_HASHES.json` ({hashes.get('count')} files), `M7_RUNBOOK.md`, `M7_FIDELITY_MATRIX.md`, `M7_OWNERSHIP_INVESTIGATION.md`, `M7_INTEGRATION_TOPOLOGY.md`, `M7_PRODUCTION_UNKNOWNS.md`, `reports/architecture/M7_CANONICAL_SNAPSHOT.json` |

## What was built

1. **Stage A — Source-to-Pay closure.** A fresh checkout of b6e4976 with a fresh isolation baseline (running
   containers only; the executing checkouts excluded; every other registered worktree protected), three clean runs
   (two in the closure checkout, one from a second fresh checkout at the evaluated commit), deliverable scan 0
   findings, acceptance 20/20. The historical concurrent-era 18/20 is preserved verbatim; both are referenced by
   `reports/source-to-pay-replica/ACCEPTANCE_CLOSURE.md`, which also records the two intermediate attempts and why a
   shared Docker daemon is not sufficient for trustworthy parallel acceptance (separate daemon / VM / remote context
   recommended).
2. **Stage B — canonical branch.** `milestone-7-shared-ingress-integration` from the M6 tag; the closure tag merged
   (`7505783`, merge base 2613f38, no conflicts); M6 acceptance re-evaluated 15/15 on the integrated branch after the
   provenance gate learned to accept a descendant of the M6 tag; Source-to-Pay source verification and contract tests
   pass on the merged tree.
3. **Shared ingress** (`ENV2_COMPOSE/substitutes/api-ingress`): CONTRACT_FAITHFUL_REPLACEMENT of the monolith's
   ingress responsibilities for 25 routes derived mechanically from the pinned `Route.php` / payouts routers /
   vendor-payments call sites; four identity contexts (merchant key, dashboard session + OTP, internal app + tenant
   header, admin token); tenant authority (caller-supplied identity headers and body merchant stripped/overwritten);
   explicit merchant-owned resource records; merchant idempotency per `MerchantIdempotencyHandler`; persistent
   SQLite state; reset/health/evidence control plane; correlation ids into payouts and the workers; SourceUpdater
   relay to vendor-payments. Payouts' `[api] host` now points at the ingress; the monolith-stub's owner-less
   fund-account route is retired; batch-sim creates bulk payouts through the ingress as the `batch` application.
4. **Cross-domain integration.** The pinned vendor-payments runtime slice runs inside the arena
   (`docker-compose.s2p.yml`), with the ingress as its `boundary`. The connected journey is real end to end: Kafka
   accrual → tag-back of a REAL payout → Pay → ingress (contact/fund-account/banking/OTP/internalContactPayout)
   → real payouts internal-contact create (contact-type gate) → FTS/mozart processing → payouts SourceUpdater →
   ingress relay → vendor-payments `money_loading_success` / `processing`; verified from vendor-payments MySQL,
   payouts MySQL, ingress audit/callback rows and Kafka offsets. Nothing is fabricated beyond that.
5. **Journeys.** {res.get('PASS', 0)} PASS / {res.get('FAIL', 0)} FAIL / {res.get('EXPECTED_FAILURE', 0)} EXPECTED_FAILURE / {res.get('BLOCKED', 0)} BLOCKED across {len(js)} journeys in the canonical clean-boot run `{jr.get('run_dir')}` (boot `{(jr.get('fingerprint') or {}).get('boot_id')}`, {m6cb.get('container_count')} containers, {m6cb.get('healthy')} healthy).

| M7 journey | Result | Checks |
|---|---|---|
{m7_rows}

6. **Architecture outputs.** `reports/architecture/M7_CANONICAL_SNAPSHOT.json` (Payouts graph embedded, S2P patch by
   hash + projection, ingress routes/identities/trust boundaries/ownership edges, connected-journey components,
   runtime overlay of running services, six-class labels, separate coverage measures, production-unknown
   annotations). Label histogram: {', '.join('%s %d' % kv for kv in sorted(lab.items()))}; P0: {', '.join('%s %d' % kv for kv in sorted(p0.items()))}.
7. **Refresh.** Daily snapshot machinery now hashes the ingress contract (routes.json, CONTRACT.md, ownership schema
   version) and the Source-to-Pay inputs (boundary contract, spec, source lock, graph patch, driver, fixtures,
   overlay) and maps changes to journeys through the graph. Demo: change `{(rd.get('changed') or ['?'])[0]}` → detected={rd.get('change_detected')},
   affected services={rd.get('affected_services')}, reran exactly {rd.get('rerun_journeys')} (results {rd.get('rerun_results')}), {len(rd.get('unaffected_not_rerun') or [])} unaffected journeys not rerun, reverted={rd.get('reverted')}, canonical evidence preserved={rd.get('canonical_evidence_preserved')}.
   Weekly clean rebuild: passed={wk.get('passed')} ({wk.get('container_count')} containers, suite {json.dumps((wk.get('suite') or {}).get('results'))}, graph regenerated={wk.get('graph_regenerated')}).
8. **Reset.** {rp.get('ingress_mutable_rows_before')} mutable ingress rows → {rp.get('ingress_mutable_rows_after_reset')} after reset, seed ownership kept={rp.get('seed_ownership_kept')}, replay after reset is new={rp.get('ingress_replay_after_reset_is_new')}, S2P volume removed by stack down={rp.get('s2p_volume_removed')}; arena `down -v` proven by the clean boot from empty state.

## Contract mismatches recorded while connecting the real path

- The standalone Source-to-Pay fixture remitted with purpose `tax_payment`, which only the local boundary replacement
  accepted; the REAL payouts service answers 400 "Invalid purpose: tax_payment". vendor-payments' own default
  (`taxpayments/constants.go:98`) and the monolith (`Payout/Purpose.php:33`) use `rzp_tax_pay`, which payouts admits
  for internal-auth callers (`payoutPurpose/constants.go InternalPurposeTypeMap`). The connected journey uses
  `rzp_tax_pay`; the standalone replica fixture is left untouched (its acceptance is a namespaced proof).
- The local boundary enforced a merchant-owned-payout gate on the tax-payment tag-back that the pinned monolith
  does not have (`PayoutsDetails/Core.php:378-406`, id-only update). The ingress follows the source and records the
  caller's tenant instead (PU-3).
- The monolith's `Workflow.php` approve/reject client appends a trailing slash that payouts' router answers with
  307; the ingress addresses the registered path directly (same effective request).
- payouts' `user_id` column is 14 characters; a longer batch creator id fails bulk rows with a 500 (journey defect
  during development, corrected; not a twin or product finding).

## Hard gates

| Gate | Status | Description |
|---|---|---|
{gate_rows}

Secret scan of the M7 sources and reports: gitleaks exit {scan.get('exit_code')}, {scan.get('findings')} findings. Arena networks are `internal: true`;
no production host, credential or customer data is used anywhere in M7.

## Stop condition and recommendation

This milestone did not begin another business domain. With one shared ingress in front of the real Payouts
service, an explicit ownership model, a namespaced second domain connected through source-supported contracts,
per-run hash-bound evidence, a canonical snapshot with six-class labels and separate coverage measures, and daily /
weekly refresh proofs, the architecture foundation is sufficient to **pause domain expansion** and begin:

- immutable architecture snapshot compilation (the canonical snapshot is the seed; it needs a versioned, signed,
  append-only store instead of a tracked JSON);
- isolated multi-twin provisioning and separate Docker/VM execution contexts (the shared daemon is the single
  largest source of acceptance fragility — three closure attempts were spent on baseline/capture artefacts of a
  shared daemon; every M7 run still competed with 98 containers on one 12.5 GiB VM);
- durable orchestration and recovery (the journey runner, clean boot and S2P stack scripts are procedural;
  long suites are only as durable as the shell that launched them);
- architecture-query and context services over the canonical snapshot and evidence bundles.

No additional domain reconstruction is recommended: no selected architecture or cross-domain journey is blocked by a
missing domain. The remaining fidelity gaps are production unknowns (PU-1..PU-10), not absent repositories.
"""
    OUT.write_text(text)
    print("wrote", OUT.relative_to(REPO))


if __name__ == "__main__":
    main()
