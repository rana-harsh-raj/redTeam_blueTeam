# M7 — Canonical architecture integration and shared API ingress: final report

| | |
|---|---|
| Canonical branch / commit | `milestone-7-shared-ingress-integration` @ `6fc53bd4e3114077b8779b16d5ff5e63339975a7` (tracked tree DIRTY: A  reports/implementation/m7-s2p-post-integration-acceptance.json
A  reports/implementation/m7-s2p-post-integration-isol) |
| M7 acceptance | **20/22 gates, accepted=False** (`reports/implementation/M7_ACCEPTANCE.json`, evaluated 2026-09-08T09:33:31.232944+00:00) |
| Source-to-Pay closure (Stage A) | isolated run 20/20 accepted=true at `d87591000f359f1a58fc805c9667d84dc1871d09`; historical 18/20 (accepted=false, gates 02/03) at `b6e4976f9baebfe770dfa30794f6f61ea08ccb1b` preserved; tag `s2p-acceptance-closure-m7` |
| Source-to-Pay acceptance from the integrated branch | True (20/20 gates, clean runs 3/3) at `6fc53bd4e3114077b8779b16d5ff5e63339975a7` |
| Graph | 3065 nodes / 5266 edges / 45 families; Source-to-Pay namespace 27994 nodes / 34744 edges referenced by hash, 930 projected |
| Mapping coverage | 364/364 P0 components (100.0%) |
| Executable coverage | 265/364 (72.8%) |
| Actual-source runtime coverage | 242 P0 nodes (46.1%), 796 of 3065 nodes |
| Contract-faithful replacement coverage | 147 P0 nodes (28.0%), 355 of all |
| Actual source services running (canonical boot) | 97 compose services up; real: payouts-api + 25 workers + 2 Kafka consumers, fts-web + 27 FTS workers, ledger-api + workers, cfa-server + workers, xbalances-server + worker, s2p-vp-source (pinned vendor-payments slice) |
| Replacements remaining | api-ingress (new), monolith-stub (callback group only), kong-lite, batch-sim, workflow-engine, mozart-sim, dcs/splitz/shield/pricing/asv/bankingaccounts stubs, stork-capture, merchant-webhook-sink, xas-sim, ledger-gate, cron-driver, S2P source driver, S2P standalone boundary (standalone acceptance only) |
| Cross-domain journey | PASS (24/24); three clean connected runs: True (('s2p-connected-1', 'PASS'), ('s2p-connected-2', 'PASS'), ('s2p-connected-3', 'PASS')) |
| Beneficiary ownership | **REPRODUCED_AND_RESOLVED_BY_MISSING_INGRESS_CHECK** — `M7_OWNERSHIP_INVESTIGATION.md`; M6 D-7 journey now PASS |
| Production unknowns | PU-1..PU-10 in `M7_PRODUCTION_UNKNOWNS.md` (route selection, PS-direct ownership path, tag-back scoping, app auth mode, sessions/OTP, SourceUpdater transport, S2P deployment, batch approval hop, dashboard approve variant, purposes/permissions) |
| Artifacts | `M7_ACCEPTANCE.json`, `M7_ARTIFACT_HASHES.json` (142 files), `M7_RUNBOOK.md`, `M7_FIDELITY_MATRIX.md`, `M7_OWNERSHIP_INVESTIGATION.md`, `M7_INTEGRATION_TOPOLOGY.md`, `M7_PRODUCTION_UNKNOWNS.md`, `reports/architecture/M7_CANONICAL_SNAPSHOT.json` |

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
5. **Journeys.** 110 PASS / 0 FAIL / 1 EXPECTED_FAILURE / 0 BLOCKED across 111 journeys in the canonical clean-boot run `RED_LOOP/runs/m6-journeys-20260908T085132Z` (boot `6cdf3d73-c39c-4ff7-aafb-856774ed2fd0`, 98 containers, 97 healthy).

| M7 journey | Result | Checks |
|---|---|---|
| journey:cross-domain-s2p/async_state | PASS | 25/25 |
| journey:cross-domain-s2p/failure | PASS | 13/13 |
| journey:cross-domain-s2p/idempotency | PASS | 8/8 |
| journey:cross-domain-s2p/success | PASS | 24/24 |
| journey:shared-ingress/admin | PASS | 6/6 |
| journey:shared-ingress/approval | PASS | 9/9 |
| journey:shared-ingress/async_state | PASS | 9/9 |
| journey:shared-ingress/batch | PASS | 6/6 |
| journey:shared-ingress/direct | PASS | 5/5 |
| journey:shared-ingress/failure | PASS | 9/9 |
| journey:shared-ingress/idempotency | PASS | 6/6 |
| journey:shared-ingress/restart | PASS | 6/6 |
| journey:shared-ingress/success | PASS | 7/7 |
| journey:shared-ingress/tenant_isolation | PASS | 6/6 |

6. **Architecture outputs.** `reports/architecture/M7_CANONICAL_SNAPSHOT.json` (Payouts graph embedded, S2P patch by
   hash + projection, ingress routes/identities/trust boundaries/ownership edges, connected-journey components,
   runtime overlay of running services, six-class labels, separate coverage measures, production-unknown
   annotations). Label histogram: ACTUAL_SOURCE_RUNNING 796, BEHAVIORAL_STUB 78, CONTRACT_FAITHFUL_REPLACEMENT 355, GRAPH_ONLY 461, PRODUCTION_STATE_UNKNOWN 3, SOURCE_MAPPED_NOT_RUNNING 1372; P0: ACTUAL_SOURCE_RUNNING 242, BEHAVIORAL_STUB 14, CONTRACT_FAITHFUL_REPLACEMENT 147, GRAPH_ONLY 83, SOURCE_MAPPED_NOT_RUNNING 39.
7. **Refresh.** Daily snapshot machinery now hashes the ingress contract (routes.json, CONTRACT.md, ownership schema
   version) and the Source-to-Pay inputs (boundary contract, spec, source lock, graph patch, driver, fixtures,
   overlay) and maps changes to journeys through the graph. Demo: change `DOMAIN_REPLICAS/source_to_pay/integration/payouts-boundary-contract.yaml` → detected=True,
   affected services=[], reran exactly ['journey:cross-domain-s2p/async_state', 'journey:cross-domain-s2p/failure', 'journey:cross-domain-s2p/idempotency', 'journey:cross-domain-s2p/success'] (results {'journey:cross-domain-s2p/success': 'PASS', 'journey:cross-domain-s2p/failure': 'PASS', 'journey:cross-domain-s2p/idempotency': 'PASS', 'journey:cross-domain-s2p/async_state': 'PASS'}), 107 unaffected journeys not rerun, reverted=True, canonical evidence preserved=True.
   Weekly clean rebuild: passed=True (98 containers, suite {"PASS": 110, "FAIL": 0, "EXPECTED_FAILURE": 1, "BLOCKED": 0}, graph regenerated=True).
8. **Reset.** 52 mutable ingress rows → 0 after reset, seed ownership kept=True, replay after reset is new=True, S2P volume removed by stack down=True; arena `down -v` proven by the clean boot from empty state.

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
| M7-01 | PASS | Historical milestone tags unchanged |
| M7-02 | PASS | Canonical branch has documented ancestry (M6 tag + S2P closure tag are ancestors; merge commit recorded) |
| M7-03 | PASS | Source-to-Pay has a new isolated acceptance (20/20) and the historical 18/20 result is preserved verbatim |
| M7-04 | PASS | M6 acceptance passes after integration (re-evaluated now) |
| M7-05 | FAIL | Source-to-Pay acceptance passes after integration (3 clean runs from the integrated branch) |
| M7-06 | PASS | Shared ingress boots from clean state (empty volumes + fresh secrets; api-ingress healthy with a passport sign |
| M7-07 | PASS | Shared ingress contract inventory is source-evidenced (every served route has Route.php evidence; pinned api s |
| M7-08 | PASS | No selected boundary is implemented in both the shared ingress and the old Source-to-Pay/M6 replacement withou |
| M7-09 | PASS | Merchant, internal-service and admin identities are separated (journeys failure + admin, every separation chec |
| M7-10 | PASS | Fund-account ownership is explicitly represented (ownership records, ownership edges in the snapshot, every ge |
| M7-11 | PASS | Cross-tenant fund-account negative control produces a decisive result (refused, no row, no balance movement, d |
| M7-12 | PASS | All required journeys execute and pass |
| M7-13 | PASS | Connected Source-to-Pay journey passes three clean runs (reset ingress + fresh S2P stack each time) |
| M7-14 | PASS | Duplicate and replay controls pass (shared-ingress/idempotency, cross-domain-s2p/idempotency) |
| M7-15 | PASS | At least one connected async journey survives a service restart (api-ingress restarted between Pay and the cal |
| M7-16 | PASS | Reset removes mutable state (ingress tables emptied, seed ownership kept; S2P volume removed by stack down; ar |
| M7-17 | PASS | Daily incremental refresh proof (change detected, affected components + journeys selected, only those rerun, c |
| M7-18 | PASS | Weekly clean rebuild passes (down -> empty state -> up -> S2P stack -> full M6+M7 suite; graph regenerated; ev |
| M7-19 | PASS | All runtime and report artifacts are hash-bound (M7_ARTIFACT_HASHES.json verifies) |
| M7-20 | PASS | No production connection, credential or customer data (arena network internal, gitleaks 0 findings on M7 sourc |
| M7-21 | PASS | No unsupported production-fidelity claim (no parity phrases in the M7 reports; ownership classification is one |
| M7-22 | FAIL | Final tracked tree is clean (excluding the acceptance + hash manifest this evaluation writes) |

Secret scan of the M7 sources and reports: gitleaks exit 0, 0 findings. Arena networks are `internal: true`;
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
