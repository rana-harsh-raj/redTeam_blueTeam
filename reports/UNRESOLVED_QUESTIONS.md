# UNRESOLVED_QUESTIONS — Payouts architecture

Status after the 2026-09-04 access-expansion pass (details and evidence in `ARCHITECTURE_DELTA.md` §(g)): **15 CLOSED, 8 PARTIAL, 14 OPEN** of 37. Items still OPEN after exhausting GitHub (now including api, edge, authz, workflows, batch, stork, mozart, dcs, splitz, recon, kube-manifests, alert-rules, goutils), Slack and in-repo docs are marked below; each needs a direct ask to the named team.

| # | Status | Answer | Evidence |
|---|---|---|---|
| 1 | CLOSED | No retry on the monolith's `/v1/update_fts_fund_transfer` → PS relay call (single attempt, re-throws); FTA row committed to local DB before the relay attempt. | 20 §4 |
| 2 | PARTIAL | IP allowlist (`MerchantIpFilter`) and OTP mechanics confirmed; but no `merchant_users` DB membership check exists inside `api` at all — the monolith trusts the upstream Passport JWT, so "which headers/claims get forwarded" is answered (Passport + actor headers, finding 21) but the membership check's actual location is still unconfirmed. | 20 §3; 21 §2 |
| 3 | CLOSED | No FAV-freshness gate exists anywhere in the monolith's Payout module — exhaustive grep, one non-gate hit (an admin bulk tool). | 20 §6 |
| 4 | CLOSED | `AdminAccess::policyChecker` — local, DB-backed admin→role→permission enforcement; the only AuthZ client found lives under `Services/Mock`, not confirmed production. | 20 §7 |
| 5 | CLOSED (reconciled) | Dual-write is queue-backed and functions as *code* (5 retries, direction PS→monolith local table); the prod failure is external to the code — physical table absence (Slack, 2026-09-03), matching `ApprovedPayoutProcessor`'s live error. | 20 §8; catalog C42 |
| 6 | OPEN | `PAYOUTS_FEATURES_FORWARD_DUAL_WRITE`/`REVERSE_DUAL_WRITE` current values not found in kube-manifests (confirmed not static k8s config) nor retrieved from payouts' own app-config/DB this pass. | 22 §"#6" |
| 7 | OPEN | Read-path fallback for `is_payout_service=0` legacy rows post table-deletion not traced this pass. | — |
| 8 | PARTIAL | Full monolith route inventory now cross-references cleanly against the ~25 `pkg/api` call sites from the baseline (E28) — same routes on both sides — but live-traffic-by-path (APM) evidence was not obtained. | 20 §1; E28 |
| 9 | CLOSED | Full Kong route/plugin inventory; `payouts-proxy-cutover` is devstack-only; prod fully undiverted; `payouts_with_otp` excluded from the diverted set even in devstack. | 21 §1, §6 |
| 10 | CLOSED | Full passport claim schema (v3 static-key / v4 JWKS-host), `GetLegacyAuthType` minting recipe, no `authType` claim, `apiv1`/`edgev1` explained as payouts' own v3 config identifiers (not an SDK concept). | 21 §2; 24 §1 |
| 11 | PARTIAL | Mechanism confirmed (literal-string org match, no hierarchy); `non_lms` string not found anywhere in `authz`; new independent lead: prod's `watchResourceGroups` excludes `x_platform` entirely. Not resolvable from repo alone. | 21 §3.3, §6 |
| 12 | CLOSED | Repo name, Create/action contract, Cadence retry policy, expiry mechanics (none fire), OTP/`enable_approval_via_oauth` location (upstream, not in WFS), bulk mechanism (Batch→PS directly) — all six sub-parts closed. | 23 §A.15 |
| 13 | OPEN | Not re-addressed by this pass; remains "probable" per baseline E11 (mechanism confirmed, causality unproven). | E11 (unchanged) |
| 14 | PARTIAL | Mechanism fully resolved: FastCron SaaS ingress, not k8s CronJob; queued/scheduled/on_hold is continuous worker-draining, not cron; real k8s CronJob schedules (ledger/cfa/batch/stork) now exact. Literal FastCron per-endpoint schedule strings remain genuinely outside every cloned repo. | 22 §2, §"#14" |
| 15 | CLOSED (definitively "no repo answer") | Splitz stores all 8 target experiment IDs/names exclusively in its own MySQL — zero definitions in `splitz`, `proto/splitz`, or `config-proto`. Architectural, not a clone gap. Deterministic stub seed data synthesized instead. | 26 §B |
| 16 | OPEN | Writer of the beneficiary-bank-down Redis map / balance cache not re-addressed this pass. | — |
| 17 | PARTIAL | Caller identity and polling mechanism closed (Batch's `payout.json`/v2 types call `payouts/bulk`; no GET-polling — synchronous); which of `CreateBulkPayouts`/`CreateBulkPayoutsConcurrent` is wired remains open (payouts-repo internal, not re-derived from either `workflows` or `batch`). | 23 §"#17" |
| 18 | OPEN | Unchanged — code read (baseline) says no consistency job compares FTS truth to PS truth for `initiated` payouts; not re-addressed this pass. | — |
| 19 | OPEN | Not re-addressed this pass. | — |
| 20 | CLOSED | FTS webhook-failure alerting is aggregate-only (>10/>50 per 15 min by status); a single stuck payout does not page; stuck-payout alerts exist only for IDFC/IMPS and xpayroll. | 22b |
| 21 | OPEN | Not re-addressed this pass. | — |
| 22 | OPEN | Not re-addressed this pass. | — |
| 23 | OPEN | Not re-addressed this pass. | — |
| 24 | OPEN | Not re-addressed this pass. | — |
| 25 | PARTIAL | Export mechanism confirmed as the Proxy dual-write/authority-gating system, not push/Kafka; best-match "two flags" = `DCSFeature.write_via_client`/`read_enabled_via_dcs`; a third, related flag (`dual_write_enabled` + Splitz `dcs_kv_dual_write_enabled`) governs reverse sync. No single doc names a canonical pair — inferred from code behavior. | 26 §A |
| 26 | CLOSED | Stork proto at `proto/stork/webhook/v1/`; no ordering guarantee (non-FIFO SQS); no dedupe (`messages.event_id` unconstrained) — at-least-once only. Also closes the proto-location sub-question (payouts already consumes it via its own buf pipeline). | 25 §A.4; 24 §4 |
| 27 | CLOSED | Mozart's FTS-facing contract (8 ops, `{data,error,external_trace_id,mozart_id,success,next}` envelope) plus the built-in `-mock` simulator with hundreds of existing fixtures — preferred over a new synthetic build. | 25 §B.4, §C.1 |
| 28 | CLOSED | Private Go module access confirmed working (`go mod download` succeeded for payouts/ledger/fts/cfa/x-balances); proto/rpc generation mechanism for payouts fully documented (sparse-checkout + `buf generate`, exact make targets). | ACCESS_DELTA.md; 24 §4 |
| 29 | OPEN | Kafka topic/ACL inventory not re-addressed this pass. | — |
| 30 | CLOSED | RKG bundle exists (`knowledge-base/knowledge/domains/payouts/`, 143 nodes/232 edges, 7 service nodes matching our closure set); cross-verified against `PAYOUTS_SERVICE_GRAPH.json`, no contradictions, several complementary gaps identified both directions. | 27 §B |
| 31 | CLOSED | PS (not XAS, not a monolith self-call) is the sole caller of `bas_recon_payout_update`, allowlisted only under the `payouts_service` credential; reversal fires only for a local (non-PS) payout currently `FAILED`. Separately confirmed: recon (ART) does **not** touch this path at all — a different mechanism. | 20 §9; 27 §A.3 |
| 32 | CLOSED (mechanism) | Recon can and does alter FTS attempt/transfer status via `PATCH/POST /v1/attempts/:action` and `PUT /v1/attempts/reconcile`, `ARTAuth`-authenticated, human-gated by FinOps re-upload of an RX Workflow file. Exact literal endpoint strings live in a runtime `workflow_configs` DB table, not the repo — not fully closed at that level of detail. | 27 §A.4 |
| 33 | OPEN | Not re-addressed this pass. | — |
| 34 | OPEN | Not re-addressed this pass. | — |
| 35 | OPEN | Not re-addressed this pass. | — |
| 36 | CLOSED (as a non-answer) | `devstack` provides no merchant/gateway/payments-domain functionality at all — it does not solve the test-merchant/mock-gateway self-serve gap. The underlying gap itself remains open; look elsewhere (e.g. a Test Data Manager service). | 27 §D |
| 37 | OPEN (unchanged) | `e2e-test-orchestrator` and `qa-tools` remain inaccessible (confirmed absent from `ACCESS_DELTA.md`'s newly-readable list); no new access gained. | 27 §"#37"; ACCESS_DELTA.md |

---

## Original question list (2026-09-03) with owning teams

## API monolith / Payouts team (razorpay/api — not readable)
1. (repo) Exact behaviour of `/v1/update_fts_fund_transfer` → PS relay: retry policy, error handling when PS returns 4xx/5xx, whether FTA is updated before relay. **Fidelity**: Env 4 failure mode D; today's stuck-payout incidents may originate here.
2. (repo) Dashboard `authenticate` semantics (IP allowlist, merchant_users membership, OTP for payouts_with_otp) and which headers/claims it forwards to PS. **Fidelity**: Env 1 authentication is BLOCKED_BY_FIDELITY_GAP without it.
3. (repo) Where the "FAV freshness" gate on payouts lives (not in payouts, CFA or ValidX). **Fidelity**: beneficiary-validation control may be entirely missing from every environment.
4. (repo) Server-side enforcement of admin permission strings (`payout_status_update_manually`, `payouts_manual_action`, …) and of dashboard role permissions. **Fidelity**: admin blast radius and role enforcement unverifiable.
5. Is the legacy unconditional HTTP `POST /payouts_service/dual_write` failing in prod since the `payouts` table deletion (errors swallowed)? Ask for `ExternalServiceCallResponse` metrics by path. **Fidelity**: decides whether the sink substitute should 404.
6. Current values of `PAYOUTS_FEATURES_FORWARD_DUAL_WRITE` / `REVERSE_DUAL_WRITE` and the reverse-dual-write trigger cadence. **Fidelity**: Env 3 sync behaviour.
7. Where `is_payout_service=0` legacy rows are served from post table deletion (RCA dated 2026-09-02 referenced in `payouts/internal/app/tidb/mock.go:57-58`). **Fidelity**: read-path fallback chain.
8. Which monolith endpoints in `payouts/pkg/api` are still receiving traffic (APM by path) versus dead. **Fidelity**: size of the monolith substitute.

## Spine-Edge (terraform-kong, edge plugins, authz)
9. Effective Kong routes/plugins for `/v1/payouts*` and `/merchant/api/{mode}/*`, current state of `payouts-proxy-cutover` (which routes diverted, rollout knob values after PR 11047). **Fidelity**: Env 3 routing; PS auth-type allowlists.
10. Passport claim schema (goutils/passport v3/v4) and how `impersonation.consumer` is minted for dashboard proxy traffic. **Fidelity**: every environment's identity substitute.
11. Is the `100000razorpay::banking::non_lms` authz org consulted for merchant-dashboard traffic (open in PR 2577 thread, 2026-08-31)? **Fidelity**: role enforcement in PS pilot.

## Workflow Service owners (repo name unknown)
12. Repository name, Create/action contract, retry policy on callbacks, expiry handling (no expiry event in PS), where OTP/`enable_approval_via_oauth` is enforced, bulk-approval integration with Batch. **Fidelity**: Flow C and Env 5.

## Payouts Service team
13. Is the 30-min MerchantConfig Redis cache + no-op `UpdateMerchantFeatureInCache` the confirmed root cause of the recurring `payout_workflows`-ignored incidents (Jun–Aug 2026)? **Fidelity**: whether Env 1 must reproduce the staleness or the fix.
14. Production cron cadence for every `/v1/cron/*` endpoint (kube-manifests/spinacode). **Fidelity**: queued/scheduled/on_hold/reservation timing.
15. Live Splitz variants for: shadow-gateway routes, `fts_request_from_payouts_service_experiment`, `merchant_config_via_asv_and_dcs_experiment` (Qnc6b4fg1Hi36k), `for_duplicate_payout_evaluate`, `charge_collections_call_experiment`, `use_api_db_for_payout_status_details_experiment`, `IKeyAutoEnforcementRampUp`. **Fidelity**: default paths in every environment.
16. Writer of the beneficiary-bank-down Redis map and of the balance cache keyed by balance_id. **Fidelity**: on_hold and Direct balance gate seeding.
17. Which of `CreateBulkPayouts` vs `CreateBulkPayoutsConcurrent` is wired to `/v1/payouts/bulk`; identity of the caller ("Batch service") and its polling mechanism. **Fidelity**: Env 5.
18. Does any consistency job (`InitiateDataConsistencyChecker`, `PayoutsDualWriteFailureProcessing`) compare FTS truth to PS truth for `initiated` payouts? Code read says no. **Fidelity**: Env 4 expected outcome (no automated repair).

## FTS team
19. Are `FireStatusUpdateKafka` and the HTTP webhook mutually exclusive per transfer, and which merchants are on each? **Fidelity**: Env 4 Kafka-drop scenario.
20. What alerts on `TransferWebhookUpdateFailureCount`? **Fidelity**: whether abandonment is silent in production.
21. Relay's role in FTS runtime config (which keys are patched). **Fidelity**: FTS routing behaviour drift.

## Ledger team
22. Is there any DB-level safeguard for `(transactor_id, transactor_event)` uniqueness beyond the app-level mutex, and any guard against `payout_processed` arriving before `payout_initiated`? **Fidelity**: invariant tests in Env 2/4.
23. Is `MerchantBalanceAsyncUpdateExperiment` wired into the sync/async decision today? **Fidelity**: balance authorization semantics.
24. Formal confirmation that X payouts use the same `razorpay/ledger` deployment under tenant X (vs a separate instance). **Fidelity**: ledger deployment topology.

## Platform (DCS, Splitz, Stork, Mozart, Datastores, DevEx)
25. DCS export path for `rzp/x/merchant/payouts/*` and whether both legacy and DCS flag names are populated for real merchants. **Fidelity**: approval decisions.
26. Stork Twirp proto (from `razorpay/proto`) and whether ordering/dedupe was ever added after the 2026-06-09 request. **Fidelity**: merchant-visible webhook capture.
27. Mozart request/response schemas per gateway and availability of ITF mock-gateway fixtures. **Fidelity**: bank simulator.
28. Private Go module access (GOPRIVATE proxy or vendored cache) and buf.build org read. **Fidelity**: hard build blocker for all F3 services.
29. Kafka topic/ACL inventory and consumer groups for the payouts platform (via #gandalf). **Fidelity**: async paths.
30. RKG export for the 6 payouts services (knowledge-base PR #157) — useful to cross-verify the graph. **Fidelity**: completeness check.

## Recon / XAS
31. Who calls PS `/v1/payouts/banking_account_statement/payout_update` (XAS directly or monolith BAS module) and under what conditions it creates reversals. **Fidelity**: Env 4 Direct-account reversal path.
32. Recon/ART repository and its write paths into FTS attempts. **Fidelity**: whether recon can alter transfer state.

## ValidX / CFA
33. Is the ValidX cache dedupe age-bounded anywhere (no predicate in repo)? **Fidelity**: FAV environment semantics.
34. ValidX webhook durability (errors discarded in state manager) and whether the monolith reconciles misses. **Fidelity**: FAV environment.
35. CFA ownership (no CODEOWNERS) and whether `FundAccountsFetchMultiple` will move off API MySQL. **Fidelity**: list queries in environments need API MySQL DDL.

## Testing / DevStack
36. Self-serve path to obtain payouts test merchants with FTS + mock-gateway wired (Test Data Manager variants). **Fidelity**: fixture realism.
37. Access to `e2e-test-orchestrator` and `qa-tools` repos. **Fidelity**: reuse of existing E2E.

## Addendum 2026-09-04 (closed or reframed by the Env 2 build pass)

- **Closed from repos** (see `raw-findings/31_env2_bringup_notes.md`): payouts worker/consumer process model; how PS hands a payout to FTS (via the monolith `create_fta`, not directly); monolith FTA→payout status map; FTS worker/queue model, mozart identifier and credential conventions; ledger config seeding RPC and account discovery identifiers; the FTS status-webhook target for monolith-created FTAs.
- **New, repo-derived, needs an owner**: schema provenance — `payout_details.beneficiary_bank_code` exists in production but in no migration (ARCHITECTURE_DELTA W10); the arena patches it, production's history for it is unknown.
- **Still open**: production values behind `USERS_*`/`PAYOUTS_FTS_AUTH_*` identities; FastCron cadences; the monolith `balance` sync that feeds the low-balance dequeue (the arena substitutes it with a ledger read); whether the DCS key shape for in-flight reservations matches what PS reads (V12).
