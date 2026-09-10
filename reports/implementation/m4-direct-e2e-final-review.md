# M4 Direct + Reconciliation — Final Critic Review (T23)

Adversarial review of every Milestone 4 implementation claim against the accepted source corpus
(`.local/twin-repos/accepted/{payouts,fts,ledger,x-balances,cfa}`) and the runtime evidence artifacts.
Implementation reports were treated as **claims to verify**, not as truth. Author: T23 (Fidelity and
Documentation Critic, workstream 17). Branch `milestone-4-direct-reconciliation`. Read-only except this
file and the T23 handoff; no source or config was modified.

Every substantive statement cites `file:line` in the accepted source or `artifact:field` in
`reports/implementation/`. Where I disagree with a label or believe a gate is weaker than its pass
suggests, I say so plainly.

---

## 1. Executive summary

M4 stood up a Direct-account payout twin on the accepted binaries of payouts-service (PS), FTS, Ledger,
x-balances and CFA, and drove the complete Direct lifecycle end to end: create → FTS transfer → bank
(Mozart substitute) → RBL statement ingestion by the **real** PS `rbl_banking_account_statement` worker →
matching (real PS ART route + an XAS-shaped substitute `xas-sim`) → the **real** PS
`UpdatePayoutAfterBASRecon` repair → Direct-account (DA) journals written into the **real** Ledger by a
monolith-shaped substitute emitter → status webhook. Seven Direct journeys (A–G), six invariants with
negative controls, four transport routes, and a tenant-isolation/idempotency boundary suite all pass on
the live arena (`m4-direct-journeys.json`, `m4-invariant-results.json`, `m4-route-coverage.json`,
`m4-boundary-results.json`). One genuine cross-tenant IDOR in the accepted payouts source was verified to
the mandate's bar and independently reproduced on fresh IDs (F-M4-001). The autonomous assurance harness
was proven mechanically (self-tests, recovery matrix, a calibration lifecycle that reaches `accepted`),
but the live model campaign itself surfaced **zero** hypotheses. The milestone acceptance is still
**DRY-RUN** (58/74 gates pass, 16 pending, 0 fail — `m4-dashboard.md`/T22). The build is a faithful twin
of Direct reconciliation and accounting, with the honest caveats catalogued below; it is not a claim
about production behaviour.

---

## 2. What is REAL vs SUBSTITUTE vs FIXTURE

Cross-checked against `m4-fidelity-matrix.csv` and `ENV2_COMPOSE/config/declared-deviations.yaml`.
Disagreements flagged in the last column.

| Component / route / journey | Layer | Fidelity | What the substitute preserves / where it stands in for production | Critic note |
|---|---|---|---|---|
| payouts-api (PS core) | real service | **REAL** | accepted binary; the whole Direct create/recon/status path is real code | confirmed |
| fts-web | real service | **REAL** | accepted binary | confirmed |
| ledger-api | real service | **REAL** | accepted binary; DA journals really recorded and balanced (`m4-xas-ledger.json:checks`) | confirmed; DEV-001/DEV-002 (header forwarding, mutex ttl) declared |
| x-balances | real service | **REAL** | accepted binary; balance authority | confirmed |
| cfa-server | real service | **REAL** binary, **substitute behaviour** on fund-account resolution | resolves fund accounts without a merchant filter — see H-D4 | matrix labels it `real`; the H-D4 200-not-400 is a CFA fidelity gap (T19). Fair, but the fund-account-resolution behaviour is NOT production-faithful |
| kong-lite (hardened edge) | substitute | **SUBSTITUTE** | mints the passport; enforces the route allowlist (`KONG_ENFORCE_ROUTE_POLICY=1`, C-001). Stands in for the production edge/monolith ingress | confirmed; this is exactly why F-M4-001 production reachability is UNKNOWN (§4) |
| PS worker `rbl_banking_account_statement` | real service | **REAL worker / substitute bank** | real ingestion binary; bank boundary faked | confirmed (`m4-bas-ingest.json:all_pass=true`) |
| mozart-sim (RBL v2 `account_statement`) | substitute | **SUBSTITUTE** | base64 CSV, Success/"No Records Found", next_key paging. Mozart itself unbuildable (private modules). **Direct vs Pool indistinguishable at the bank boundary — credentials ignored** | confirmed; L-003 |
| bankingaccounts-stub (RBL creds route) | substitute | **SUBSTITUTE** | credentials route only | confirmed; L-003 |
| BASD rows (`banking_account_statement_details`) | fixture | **SUBSTITUTE (provisioner-inserted)** | in prod the monolith writes these into the PS DB at CA onboarding (`api …/Details/Core.php:219-237`); PS never creates them. Provisioner mirrors that insert | confirmed; L-006 |
| PS ART route `/v1/banking_account_statement/process/batch` | real service | **REAL** | matcher path (a) | confirmed |
| xas-sim (XAS enrichment, matcher b) | substitute | **SUBSTITUTE (contract-faithful)** | utr→grn→cms match, external marking, ≤2/dual-type guards, `(event_id,entity_type)` dedup, `fetch_multiple_by_reference_numbers`. Real XAS Kafka CDC dual-write not reproducible; outbound `payout_update` issued directly | confirmed; L-005, C-011/C-013/C-014 |
| PS `x_account_statement_source_event` queue | real service | **REAL** | real producer | confirmed |
| PS `UpdatePayoutAfterBASRecon` (repair route) | real service | **REAL** | real code sets `transaction_id`; reversal branch at `payouts core.go:7297-7318` | confirmed |
| DA ledger journal emitter (`processLedgerPayoutForDirect`) | substitute | **SUBSTITUTE** | monolith-stub reproduces `da_payout_processed(+_recon)`; **PS itself posts no Direct journal at this HEAD** (`payouts core.go:7284,7356` commented — verified §4) | confirmed; L-004, C-004/C-010; production reachability UNKNOWN |
| DA journals `da_payout_processed(+_recon)` | real service | **EXECUTED** (real Ledger rows) | rows exist, balanced, on the merchant's 6 DA sub-accounts | confirmed; L-016 |
| monolith-stub (create_fta relay + emitter) | substitute | **SUBSTITUTE** | relays create; emits DA journals; lacks the statement-first guard (F-T10-1). Monolith ingress `X-Razorpay-Account` scoping unmodelled | confirmed; L-013 |
| dcs-stub | substitute | **SUBSTITUTE** | feature/config; now honours the query fieldmask (DEV-172, L-018 fix) | confirmed |
| splitz-stub | substitute | **SUBSTITUTE** | experiment flags fully on/off, not percentage-rolled (DEV-025) | confirmed |
| Route F (FTS→Kafka→PS status) | real service | matrix: **independently_reproduced** | transport observed (FTS publishes, real PS consumer receives) | **DISAGREE — overclaim of label**: the Direct FAILED/REVERSED *drop* (EF-002/003) was **source-inferred, not executed**; only the transport was observed and it failed to reach terminal state for an unrelated bootstrap reason (§5, §6). "independently_reproduced" overstates what was run |
| SLICE Direct channel | fixture | **DELIBERATELY SIMPLIFIED** | excluded; RBL/IMPS only | confirmed; L-012 |
| Direct pricing | fixture | **DELIBERATELY SIMPLIFIED** | flat twin plan, no slabs | confirmed; L-017 |

**Matrix vs deviations cross-check:** the 42-row `m4-fidelity-matrix.csv` and `declared-deviations.yaml`
agree on every substitute except the Route F label above. The `cfa-server` row reads `real` while its
material behaviour (fund-account resolution) is substitute-grade; the finding text (H-D4) is honest about
this even though the matrix row is not — I would qualify the matrix cell as `real-binary/substitute-resolution`.

---

## 3. EXECUTED vs SOURCE-INFERRED vs INDEPENDENTLY-REPRODUCED — the 8 north-star distinctions

| Facet | Implemented | Executed | Indep. reproduced | Source-inferred | Substitute-represented | Deliberately simplified | Unresolved | Prod-reachability |
|---|---|---|---|---|---|---|---|---|
| Direct journey (create→FTS→bank→statement→recon→ledger→webhook) | ✓ | ✓ (A–G PASS, `m4-direct-journeys.json`) | — | — | bank/XAS/DA-emitter | SLICE, pricing | — | UNKNOWN (matched-DA path) |
| Reconciliation (BAS ingest + matching + repair) | ✓ | ✓ (`m4-bas-ingest.json:all_pass`, `m4-xas-ledger.json:all_green`) | — | — | mozart/xas-sim | — | — | which fetcher/cron is live (L-007) |
| Accounting (DA journals in real Ledger) | ✓ | ✓ (real rows, balanced) | — | PS-posts-nothing asserted from source (verified §4) | DA emitter | — | — | UNKNOWN (L-004) |
| Routes (R1 FTS-direct, R2 relay, R3 Kafka, R-remap) | ✓ | R1/R2/R-remap ✓ | R3 transport only | R3 Direct-drop EF-002/003 | R2 relay | — | R3 terminal not reached | mixed |
| Autonomous runtime (harness) | ✓ | self-tests + recovery ✓ | calibration IDOR (gpt-5.5) | — | fixture lifecycle | — | **live campaign found 0 hypotheses** | n/a |
| Isolation (tenant A/B, idempotency) | ✓ | ✓ (`m4-boundary-results.json`, G34/G45–G52) | — | — | — | — | — | known |
| Replay / findings | ✓ | ✓ (deterministic oracle) | F-M4-001 on 2nd fresh pair via Broker path | — | — | — | — | F-M4-001 UNKNOWN |

Headline reading: the **Direct journey, reconciliation, accounting, routes (except R3), isolation and
replay were genuinely EXECUTED** on the live arena. **Independent reproduction** is real for two distinct
things — the F-M4-001 IDOR (a *second* fresh A/B pair through a *different* mechanism, the attacker Broker
gateway path, deterministic oracle, no model — `m4-direct-e2e-replay.json:candidates[H-D3].independent_reproduction`)
and a **calibration-only** cross-provider rediscovery on a loopback fixture (`m4-lifecycle-validation.json`,
gpt-5.5). The R3 Kafka Direct-drop and the "PS posts no Direct journal" assertion are **source-inferred**
(the latter I verified directly, §4; the former was not run, §5).

---

## 4. The verified finding F-M4-001 — restated and challenged

**Restatement.** Broken object-level authorization (cross-tenant information disclosure) on
`GET /v1/payouts/free_payout/{balance_id}`. `GetFreePayoutAttributes(ctx, balanceId)` resolves the banking
account by `balance_id` alone and returns that balance's free-payout counter/count/supported-modes, with no
check that the authenticated merchant owns the `balance_id`.

**Challenge 1 — is the unauthorized effect real? CONFIRMED against source.** I re-read the entire call
chain in the accepted clone:
- Route `payouts/internal/routing/router/payout_routes.go:78-82` — `GET /free_payout/:balance_id` with
  `nil` extra middleware (no ownership guard).
- Controller `payouts/internal/controllers/payoutController.go:1157-1183` — reads **only** `request.BalanceID`
  from the path; it never reads the passport merchant from context.
- Service `payouts/internal/app/payouts/service.go:1115-1126` — passes `balanceId` straight through.
- Core `payouts/internal/app/freePayout/core.go:678-739` — `GetBankingAccountByBalanceID(ctx, balanceId)`,
  `GetFreePayoutsConsumed(ctx, balanceId, …)`, `GetFreePayoutCount(ctx, balanceId, …)` — **every lookup is
  keyed by `balance_id` alone; the authenticated merchant is never referenced.**
- DB `payouts/internal/app/bankingAccount/core.go:391-…` — `GetBankingAccountByBalanceID` filters by
  balance id, no merchant predicate.
The IDOR is real in the accepted source. **CONFIRM.**

**Challenge 2 — is the observed effect a genuine leak, not a caller-scoped no-op?** The replay
(`m4-direct-e2e-replay.json:candidates[H-D3].arms`) uses a **divergent-config canary**: attacker A and
victim B are seeded to *different* non-default counter values. A (authenticated as A; wrong/empty secret
both 401 → `authenticated_as_A=true`, `no_victim_credential_used=true`) reads B's `balance_id` and receives
**B's distinct value** (`victim_distinct_value_present=true`, `equals_attacker_own_value=false`,
`equals_canned_default=false`, `deterministic_repeat_equal=true`). Sound.

**Challenge 3 — is the negative control sound? YES.** `negative_control_victim_undivergent`: the identical
cross-tenant call while B still holds the *default* counter returns 200 but **no distinctive value**
(`distinctive_value_observed=false`) → the distinctive value is causally B's divergence, not an artifact of
the call. `expected_denial_control`: the scoped `GET /v1/payouts/{id}` route **denies** the analogous
cross-tenant fetch (400, `scoped_route_denied_cross_tenant=true`) — the gap is specific to `free_payout`.
`state_observation`: reads do not mutate B (`victim_row_mutated=false`). This is a well-formed causal design.

**Challenge 4 — is production reachability genuinely UNKNOWN? YES, and correctly stated.** In the twin,
kong-lite mints the passport and there is no production edge/monolith ingress. Whether a production ingress
scopes `balance_id` to the caller *before* this handler is **out of the pinned corpus** and was not
established. The finding text (`m4-findings.md` F-M4-001) states this explicitly and says "Do NOT present
this as a confirmed production vulnerability." Correct and not overclaimed.

**Challenge 5 — anything overclaimed?** No. The finding is scoped to "VERIFIED in twin; production
reachability UNKNOWN," money/state isolation is *not* claimed to be broken (only disclosure), and the
independent reproduction is a genuinely different mechanism on fresh IDs. One chronology note for future
readers: `m4-boundary-results.json` shows the H-D3 node as **xfailed** — that is the *pre-promotion*
candidate state (2026-09-06T19:26Z); promotion to VERIFIED happened later via the dedicated replay driver
(20:18Z) and D-011. Not a contradiction.

**Verdict: F-M4-001 CONFIRMED** as a verified twin finding with UNKNOWN production reachability. No downgrade.

---

## 5. Overclaims and risks found in the handoffs

1. **Route F "independently_reproduced" (fidelity-matrix) — DOWNGRADE to "transport executed; drop
   source-inferred."** `KAFKA_ROUTE_EVIDENCE.md` is explicit: FTS published and the real PS Kafka consumer
   *received* INITIATED/PROCESSED messages (transport observed), but PS stayed `initiated` because ordinary
   jobs are not registered in the Kafka bootstrap (`internal/boot/handler.go:776-785`; a real source
   structural fact), and "**Consumption alone does not establish terminal failure/reversal processing.**"
   The Direct FAILED/REVERSED **drop** (EF-002/EF-003, `fts_status_updates.go:57-63`,
   `fts_status_updates_retry.go:60-66`) is **asserted from source, not run**. The matrix label
   "independently_reproduced" is stronger than the evidence; the `m4-route-coverage.json` R3 note is more
   honest ("assert_drop … NOT a pass of delivery"). This is a label-precision overclaim, not a false claim.

2. **F-T10-1 non-reproduction, T17 vs T19 — a real non-determinism the reports handle honestly.** T19 (and
   T10 run `m4-bas-ingest-20260906T194947Z`) observed the monolith-relay route moving a `transaction_id`-linked
   payout to `failed` (guard absent on the relay path). **T17's journey E did NOT reproduce this** — the
   relay left the payout `initiated`, and T17 records "the relay outcome varies with timing of the relayed
   status" and does *not* assert a direction. Both classify F-T10-1 as a **source-faithful non-finding**, and
   the source scoping is correct (I verified: `VerifyPayoutFailedTransaction` is called *only* from
   `fts_transfer_status_webhook.go:612`; the relay/legacy path `HandlePayoutStatusUpdateViaFTS`
   `core.go:1465` has no such guard). No overclaim — but the **timing-dependent relay outcome is itself a
   limitation** worth flagging (§8): F-T10-1's "relay moves to failed" is not deterministically reproducible.

3. **L-018 DCS panic — correctly classed as a substitute bug, not source fragility.** T16 shows the
   payouts-api panic (`features.go:252` `.Bool()` over a non-bool field) was caused by dcs-stub returning the
   int64 `queue_payout_bal_buffer` under a bool-only fieldmask that **production DCS redacts** (confirmed
   against the pinned DCS clone with a dedicated test; DEV-172). Fix in the stub, payouts source untouched.
   Classification sound. Minor caveat: the fix was verified on a byte-identical merchant (ARENAD83156710),
   not the original crasher (ARENAD80422548, whose ephemeral kong secret was not persisted) — acceptable,
   since the stub-layer projection proves the trigger field is dropped.

4. **H-D4 benign classification — CONFIRMED sound.** Quoting B's `fund_account_id` on create returns 200 but
   binds to A and draws A's balance; B untouched (`m4-direct-e2e-replay.json:candidates[H-D4].oracle.unauthorized_effect=false`).
   The 200-instead-of-400 is a **CFA substitute** fidelity gap (source expects a merchant-scoped resolution),
   not a payouts bug. Correctly reported as benign, not upgraded.

5. **Autonomous-discovery narrative — the biggest risk of over-reading (see §6, §9).** The live campaign
   (`m4-assurance-run.json`) produced **0 hypotheses** across all four policies (every context stopped on
   `emergency_turn_ceiling`, `new_hypotheses:0`, replan reason `recon_only_loop`). F-M4-001 was found by a
   **human-designed** hypothesis (T06/T14 H-D3 candidate) and verified by a **deterministic** replay driver —
   **not** by the autonomous model loop. No handoff claims otherwise, but a casual reader of "autonomous
   assurance" could infer the model found the IDOR. It did not.

---

## 6. Substitutions, approximations, unknowns, and NON-CLAIMS (gate G74)

**Production-configuration unknowns (not derivable from the corpus; fixed per-merchant in the twin — L-008):**
`in_flight_reservation_enabled`, `da_ledger_journal_writes`, `payout_service_txn_recon`,
`enable_payouts_buffer`, `fetch_balance_entity_from_x_balance`, `account_statement_source_event`,
`xas_source_event_utr_and_grn_match_experiment`, `fts_request_from_payouts_service`,
`create_transfer_meta_rollout`, `fire_status_update_kafka`; **which RBL fetcher/cron is live** (PS worker vs
monolith `RblBankingAccountStatement` vs XAS multi-bank; FastCron cadence) (L-007); **the production
monolith ingress** (`X-Razorpay-Account` partner/sub-merchant scoping, SHARED-sub-on-Direct-master
`create_fta`) (L-013).

**Source-inferred-but-UNEXECUTED paths:**
- Kafka Direct FAILED/REVERSED **drop** EF-002/EF-003 — asserted from `fts_status_updates.go:57-63` /
  `_retry.go:60-66`, **not run** (§5.1); the DTO `fts_*` mismatch is source-confirmed but no Ledger-discovery
  failure from it was observed (`KAFKA_ROUTE_EVIDENCE.md`).
- **Real XAS build not done** — Kafka CDC dual-write (Maxwell) not reproducible; `payout_update` issued
  directly (L-005).
- PS external-path `da_ext_debit` journal (Unreconciled ART) **publishes to SNS which is blackholed** for
  payouts-api (no `AWS_ENDPOINT_URL_SNS`); deliberately not wired (D-010, DEV-171). The matched Direct
  success accounting is proven via the monolith-stub emitter instead.
- "PS posts **no** Direct journal at this HEAD" — I verified the two call sites are commented
  (`payouts core.go:7284`, `core.go:7356`: `//err = c.SendLedgerEventPostBasLinking(...)`) and the CA skip
  (`core.go:2575-2577`: `if !strings.EqualFold(bankAcc.GetAccountType(), appConstants.Shared) { return nil }`).
  This is source-confirmed, but *whether any live merchant still receives DA journals* is UNKNOWN (L-004).

**Deliberate simplifications:** SLICE Direct excluded (L-012); **one arena per host** (11.65 GiB;
per-merchant namespaces, not concurrent arenas — D-002, L-001/L-011); **BASD rows inserted by the
provisioner** (L-006); Direct pricing flat (L-017); FTS one source account per merchant, v1/v2 default-rule
selection never exercised (L-014); `BalanceRefreshEvent` (reservation-release trigger A) not producible —
tests use SQS injection or `last_fetched_at` + reconciler (trigger B) (L-009); FTS→PS health notification
never emitted, on-hold exercised by a direct call (L-010).

**NON-CLAIMS (explicit):**
- This is a **twin**, not production. No statement here is a claim about production behaviour.
- **F-M4-001 production reachability is UNKNOWN** — not a confirmed production vulnerability.
- **Substitute behaviours are not production behaviours**: mozart-sim (Direct/Pool indistinguishable at the
  bank boundary), xas-sim (no real Kafka CDC), the DA emitter (monolith-shaped, not the monolith),
  bankingaccounts/dcs/splitz/kong-lite/monolith-stub, and the CFA fund-account resolution.
- The **autonomous campaign made no finding**; the harness *machinery* is demonstrated, the *discovery* is
  not attributable to the model (§5.5, §9).
- The DA-journal path for PS-recon merchants and the SNS external/unmatched path were **not** proven to be
  production-reachable.

---

## 7. Contradictions (C-001..C-015)

15 logged in `m4-contradictions.md`; **9 resolved, 6 open** (matches T22's dashboard count).

| ID | Topic | Status | Resolution / why still open |
|---|---|---|---|
| C-001 | M3.1 "enforcement restored to 0" vs live `KONG_ENFORCE_ROUTE_POLICY=1` | resolved | live state + `m3-1-acceptance.json` win; the report line was an intermediate state |
| C-002 | acceptance evaluator hardcodes M3.1 branch names | resolved | evaluator-scope; G03 re-evaluates commit checks branch-independently |
| C-003 | `direct_account_routing_rules` seed gap vs one-row-never-read | resolved | source wins; one source account ⇒ rule is a tie-breaker only |
| C-004 | "no Direct ledger until BAS linking" vs commented call sites | resolved | **verified §4**: PS never posts a Direct journal at this HEAD |
| C-005 | FTS `account_type='DIRECT'` seed vs lowercase `direct` code | **open** | runtime worker-route observation for M2 vs fresh lowercase merchant; M2 seed untouched (L-015) |
| C-006 | Direct selector `service.go:549-552` vs `transfer_processor.go:964` | resolved | 549-552 is a metrics label; real selector elsewhere; spec to be corrected |
| C-007 | x-balances seed enum `pool` vs code `shared` | **open** | fresh Shared reached `processed`, so PS must not read x-balances account_type on that path — decisive check pending (L-015) |
| C-008 | x-balances seed `status='activated'` vs code `active` | **open** | how PS's Direct create precondition resolved for M2 vs fresh `active` — pending (L-015) |
| C-009 | DEV-024 "minor" vs BalanceRefreshEvent trigger A can never fire | **open** | trigger B executable; trigger A requires substitute event injection (L-009) |
| C-010 | spec DA-via-ledger keyed by BASD id vs commented PS calls | resolved | D-006: DA journals modelled as monolith-substitute emitter into real Ledger; reachability UNKNOWN |
| C-011 | xas-sink "payouts/ledger/FTS enqueue to XAS" vs only PS produces | resolved | contract rewritten for xas-sim |
| C-012 | "xas 12 queues" vs 11 SQS + 2 Kafka | resolved | documentation only |
| C-013 | matching scoped to XAS alone vs three matchers | resolved | D-006 exercises PS ART (real) + XAS-shaped substitute |
| C-014 | PS `event_created_timestamp` vs XAS `event_create_timestamp` | **open (preserved)** | field mismatch reproduced faithfully by xas-sim (schema mismatch logged, `m4-xas-ledger.json:checks`) |
| C-015 | reservation released by PS terminal `OnEvent` hook vs 5-min reconciler | **open** | on the relay route the hook never fires; the reconciler (trigger B) converges — decisive hook-vs-reconciler experiment pending |

The 6 open contradictions are all **honestly parked** as runtime-observation or preserved-mismatch items,
not silently closed. C-005/C-007/C-008 are seed-casing drifts left in the untouched M2 fixture (fresh
merchants use code values); C-014 is a preserved production field mismatch; C-009/C-015 are
substitute-trigger limitations. None is a blocker; all are correctly reflected in known-limits.

---

## 8. Residual gaps and honest limitations

1. **Milestone acceptance is DRY-RUN**: 58/74 gates pass, **16 pending**, 0 fail, `accepted=False` (T22).
   Pending gates include G70 egress audit, G71 tree-clean, G72 auditor, G73 tag, and G74 (this document's
   gate). The Direct/recon/accounting *content* is proven; the *acceptance ceremony* is not yet complete.
2. **The autonomous campaign found nothing** (§5.5, §9). The harness is proven mechanically only.
3. **F-T10-1 relay outcome is timing-dependent** — reproduced by T10/T19, not by T17 (§5.2). The
   "relay-moves-to-failed" behaviour is not deterministic; only the FTS-direct guard refusal and the
   link-before-terminal precondition are deterministic.
4. **Route F (Kafka) never reached terminal state** for either transport or drop — transport observed but
   blocked by an unregistered-jobs bootstrap fact; the Direct-drop is source-inferred (§5.1).
5. **DA-journal production reachability UNKNOWN** — the whole accounting proof rests on a monolith-shaped
   substitute emitter into the real Ledger; whether production still emits these for PS-recon merchants is
   not derivable (L-004, C-010).
6. **Bank boundary cannot distinguish Direct from Pool** (mozart-sim ignores credentials, L-003) — a class
   of bank-side fidelity is out of reach.
7. **Recovery matrix has 3 Docker scenarios blocked** (service/queue/database restart require
   `M4_DOCKER_RECOVERY=1`, not run — `m4-recovery-matrix.json`); 7 non-Docker scenarios pass.
8. **Single-tenant arena** means DEV-002 mutex/concurrency fixes "cannot be validated by concurrency alone"
   (DEV-064 note); idempotency G52 passes but under structurally serialized consumers.
9. **Seed-casing contradictions (C-005/C-007/C-008) remain open** pending runtime observation.

---

## 9. Verdict from the critic's chair

**The Direct + reconciliation + accounting claim is SUPPORTED by the evidence.** The lifecycle was genuinely
executed on accepted binaries: real PS create/recon/repair, real statement-ingest worker, real Ledger DA
journals (balanced, correctly scoped to the merchant's DA sub-accounts), seven journeys and six
negative-controlled invariants green, tenant isolation and idempotency proven, and the two highest-stakes
source assertions (the IDOR at `freePayout/core.go:678`; PS posting no Direct journal at
`core.go:7284/7356/2575-2577`) verified directly against source. The substitute seams are declared,
matrix-tracked, and — with the two exceptions below — labelled at the right fidelity. **F-M4-001 is
confirmed**, correctly bounded to the twin with production reachability UNKNOWN.

**Gates I judge weaker than their pass suggests:**
- **The autonomous-assurance gate (hypothesis-lifecycle) passes VACUOUSLY.** `m4-hypothesis-lifecycle.json`
  shows `gate.passed=true` with `total_hypotheses=0` — it passes because there are no *open* hypotheses, not
  because the campaign discovered and closed anything. The live model campaign produced 0 hypotheses
  (`m4-assurance-run.json`). The finding that matters (F-M4-001) came from a human hypothesis + deterministic
  replay. The harness's *capability* is demonstrated only by a **calibration-only fixture**
  (`m4-lifecycle-validation.json`, explicitly "NOT a Razorpay finding") and by self-tests/recovery matrix.
  The milestone should not present "autonomous assurance" as having *autonomously discovered* the Direct-path
  finding — it did not. This is a wording/gate-strength caveat, not a correctness failure.
- **Route F / R3 coverage** counts the Kafka route as covered on transport-observed evidence while its
  defining behaviour (the Direct FAILED/REVERSED drop) is source-inferred. I would relabel it
  "transport executed; drop source-inferred" (§5.1) rather than "independently_reproduced."

Neither weakness undermines the core M4 claim; both are honesty-of-labelling points the coordinator should
reflect before flipping acceptance from DRY-RUN to LIVE. With those two labels tightened and the pending
acceptance gates (G70–G74) run, the milestone's Direct + reconciliation + accounting body of work is, in my
judgement as critic, **sound and honestly represented.**

---

*Authored by T23. Read-only review; source corpus and runtime artifacts cited inline. No source, config, or
evidence file was modified. Companion: `reports/implementation/m4-subagent-handoffs/T23.md`.*

## Git references (finalized 2026-09-07)
- Base (unaltered): `red-loop-m3.1` -> 3a044f83ed0eda6ead71585f07c450af2ed7978f
- Tested commit: 5bde798168ea47174542085ec25824d1fc2bc31f
- Evidence commit / annotated tag: `red-loop-m4` -> 3ae177328341ec4512bdb7de8ce454ce67a35513
- Acceptance: reports/implementation/m4-direct-e2e-acceptance.json (accepted:true, 74/74). evidence_commit is recorded as null by design (self-reference avoided); the annotated tag `red-loop-m4` is the authoritative evidence commit and G73 verifies it.
- Note on tag name: `red-loop-m4` follows the repo's `red-loop-m3.1` convention (the mandate's proposed `payouts-twin-m4-direct-e2e` is the same artifact under a different name).
