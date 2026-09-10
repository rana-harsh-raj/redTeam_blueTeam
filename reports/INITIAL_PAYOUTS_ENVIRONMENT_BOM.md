# INITIAL_PAYOUTS_ENVIRONMENT_BOM — first executable Payouts environments

Derived from `PAYOUTS_CLOSURE.yaml`, `PAYOUTS_FLOW_CATALOG.md` and `CONTROL_AND_INVARIANT_CATALOG.md`. Version 2026-09-04.v2 (post-access delta; implementation package in `../ENV2_COMPOSE/`, build/boot evidence in `ENV2_BUILD_STATUS.md`).

## v2 changes (2026-09-04)
- **Build blocker removed**: all private Go modules download; payouts, ledger, fts, cfa, x-balances compile (linux/arm64) and migrate; ledger/fts/cfa boot clean; payouts and x-balances need the documented 3-line `ARENA_DCS_URL` patch in repo copies because `goutils/dcs` ignores `Mock` and dials a hardcoded host at construction (`ENV2_BUILD_RUNBOOK.md` §2).
- **API monolith substitute is now specified from source** (findings/20): ~30 endpoints incl. `update_fts_fund_transfer` relay (single attempt, FTA committed first), `dual_write` (PS→monolith local table), `fetch_pricing_info`, `internal/merchants/{id}`; dashboard auth = IP allowlist + OTP, no merchant_users check. Env 1 is no longer BLOCKED_BY_FIDELITY_GAP on *knowledge*, only on effort.
- **Kong-lite spec finalised** (findings/21): Basic auth → passport RS256 300 s, claims without any `authType`; no rate limiting to reproduce; devstack-only cutover template.
- **Workflow Service is real and buildable** (Go/Twirp/Cadence, `razorpay/workflows`) — F2 stub for Env 2, F3 candidate for Env 1; **Batch** readable — bulk approve bypasses WFS.
- **Mozart**: `-mock` mode exists but cannot be built (private deps `integrations-utils/-go`, `orchestrator`); `mozart-sim` (Python, real envelope + `bank_status_code` scenarios) is primary.
- **DCS/Splitz/Shield/Governor contracts confirmed**; Splitz experiment definitions exist only in Splitz MySQL (stub fixtures synthesised); Shield has `allow|block|review`, PS honours only `block`.
- **Recon (ART) added at F2 for Env 4** (human-gated write path into FTS attempts).
- **Cron**: PS `/v1/cron/*` is driven by the external FastCron SaaS; arena uses `cron-driver` with assumed cadences.
- **Stork**: exponential backoff 5×, 15 attempts, HMAC-SHA256 signature, no dedupe — reproduced by `stork-capture` + `merchant-webhook-sink`.


## 0. Why these environments

The closure has one hard structural fact: the **API monolith is in-path for every merchant-originated request and for the legacy FTS status relay**. Since 2026-09-04 it is readable (findings/20), so its substitute can be specified from source, but it is a 1.4 GB PHP monolith and is not built in the arena. Everything else on the money path (Payouts Service, Ledger, FTS, CFA, x-balances) is readable and now builds and boots (private Go modules resolved). The recommended first set therefore separates (a) flows whose material controls all live in accessible code and can be END_TO_END_CONFIRMED once a small set of monolith endpoints is faithfully substituted, from (b) flows whose control lives in the monolith/Kong and stay CODE_CANDIDATE or BLOCKED_BY_FIDELITY_GAP until `razorpay/api` and `terraform-kong` are readable.

Recommended order: **Env 2 (public API) → Env 4 (failure/reversal/async status) → Env 1 (dashboard + approval) → Env 3 (migration/proxy) → Env 5 (bulk)**. Env 2 has the smallest monolith surface; Env 4 targets the dominant production failure mode; Env 1 and 3 depend on a faithful monolith/Kong substitute (now specifiable from `api`/`terraform-kong`/`edge` source, not yet built); Env 5 has a different closure (Xperience + Batch).

## 1. Common substrate (all environments)

| Item | Real vs substitute | Notes |
|---|---|---|
| Build prerequisites | private Go modules `goutils`, `ledger-sdk`, `config-proto`, `rpc`, `proto`, `error-mapping-module`, `governor-executor`, `pg-sdk`, `shield-sdk`, `rzpconv`, `bvs-sdk`, `charge-collections-sdk` | **Resolved 2026-09-04**: all download with `GOPRIVATE=github.com/razorpay/*` + `gh auth setup-git`; base images replaced by public `golang`/`alpine`. Still blocked: mozart's `integrations-utils`, `integrations-go`, `orchestrator`. |
| Datastores | MySQL 8 (payouts, fts, x-balances, banking-accounts, xas, validx), Postgres (ledger rx), MongoDB (cfa), Redis (payouts, fts, ledger, x-balances), Elasticsearch (payouts index; optional), TiDB → MySQL stand-in for `payouts_temp` and `[db.api]` | Schemas from in-repo goose/GORM/Mongo migrations. API-monolith MySQL tables needed by fallbacks (`balance`, `contacts`, `fund_accounts`, `bank_accounts`, `payout_status_details`) recreated from struct definitions in cfa/x-balances/payouts (DDL only; no data). |
| Messaging | LocalStack SQS + SNS with the exact queue/topic names (payouts ≈25 jobs, cfa dual-write, x-balances refresh/payout_events, xas 12 queues, ledger jobs, validx 9); Kafka (`rx-fts-status-update-events`, `fts_status_updates_retry`, `add-tds-entry`, `payout-kafka-topic`) | `payouts/deployment/dev/docker-compose.yml` already provides MySQL/Redis/Kafka/Jaeger. |
| Identity | Synthetic service Basic-Auth pairs with the **same usernames** as config; own RSA keypair; kong-lite mints `X-Passport-JWT-V1` (RS256, 300 s) with the exact claim set from `edge/kong-plugin-upstream-jwt` (`nbf/exp/jti/payloadhash/iat/iss/aud/mode/org/product/identified/authenticated/credential/consumer/impersonation/oauth/roles`; **no authType claim**); payouts verifies with static per-identifier public keys from its config (`passport.api/edge`) | Confirmed from `goutils/passport` v3 and `edge` (findings/21, 24). |
| Config substitutes | DCS stub (goutils/dcs server contract; seeded with both legacy and DCS flag names), Splitz stub (Twirp EvaluateAPI; deterministic variant table keyed by experiment id + merchant), Relay off | Values must come from an export (see `NON_REPOSITORY_ACCESS_MATRIX.csv`). |
| Cron driver | External scheduler hitting PS `/v1/cron/*` with FastCron creds; FTS crons in-process (set `INSTANCE_TYPE=canary` on one FTS worker) | PS cadence unknown; assume queued 5 min, scheduled 5 min, on_hold 5 min, reservation reconcile 5 min (code constant), dual-write failure 15 min — **flag as unverified**. |
| Observability | Prometheus scrape (PS `observability.yaml`, ledger metrics), OTel collector, structured logs; Grafana dashboard `payouts/docs/observability/inflight-reservation-dashboard.json` | Coralogix not needed. |
| Reset/snapshot | DB snapshots per service after seeding; Redis flush; queue purge; reservation reconcile run before each scenario | Ledger balances must be re-seeded (accounts + ledger_config). |
| Synthetic entities | 3 merchants: M1 Shared/Lite balance (ledger-backed), M2 Direct/current account RBL (x-balances mirror), M3 workflow-enabled with finance_l1/l2 users; contacts + fund accounts (bank, VPA, one inactive); banking_account rows with balance_id + fts_fund_account_id; ledger accounts per merchant; FTS source_accounts + routing rules; DCS features per merchant; Splitz variants | Test-data manager merchants from DevStack are preferable if accessible. |

## 2. Environment 2 — Public API payout with idempotency and webhooks (recommended first)

| Aspect | Content |
|---|---|
| Repositories (F3) | payouts, ledger, fts, cfa, x-balances (+ banking-accounts seeded rows, F2) |
| Deployables | payouts api + **one worker process per job** (`PAYOUTS_WORKER_NAME`; 25 jobs in prod manifests, 14 payout-path jobs in the arena) + **two** Kafka consumers (`PAYOUTS_CONSUMER_TASK_NAME=fts_status_updates|fts_status_updates_retry`); ledger api/worker/scheduler(one-shot); fts web/default-workers; cfa server/worker; x-balances server/worker — see ARCHITECTURE_DELTA W9 |
| F2 substitutes | Kong-lite (`substitutes/kong-lite`: Basic auth → consumer → RS256 passport → service BasicAuth to payouts-api); **monolith-stub** (`substitutes/monolith-stub`, fixture-driven, `PS_RELAY_MODE=relay|drop` for the FTS relay) for: `GET /internal/merchants/{id}`, `POST /payouts_service/fetch_pricing_info`, `deduct_credits`, `reverse_credits`, `source_update`, `status_details_source_update`, `mail_and_sms`, `fund_accounts_internal/{id}` fallback, `payouts/purposes/{mid}`, `users_internal`, `actor_info_internal`, `merchant/on_hold_slas_internal`, `payouts_service/dual_write` (sink), `banking_account_statement/payout_update` (sink), FAV `validations_internal/*`; DCS stub; Splitz stub (fts_request_from_payouts_service=on, CreateTransferMetaRollout=on, FireStatusUpdateKafka=off, ikey enforcement on, duplicate evaluate shadow, charge_collections off); Shield stub (allow; scripted block); Governor/CC stub or F3 charge-collections; Stork Twirp capture; Mozart simulator (from `fts/cmd/mock-server` + error catalogue); ASV stub (activated=true, hold_funds=false); XAS queue sink |
| Databases | as substrate; TiDB stand-in |
| Topics/queues | full PS SQS set (32 queues + 1 SNS topic created by the LocalStack ready-hook); Kafka `rx-fts-status-update-events` / `rx-fts-status-update-retry-events` consumed by the two payouts consumers |
| Identities/roles | merchant key pairs for M1/M2; passport private claims |
| Feature flags | DCS: payout_workflows off, enable_payout_workflow off, in_flight_reservation_enabled on for M2, skip_ifsc_lookup off; Splitz as above |
| Flows supported | B (create → processed; queue_if_low_balance → queued → cron dequeue; cancel from queued; idempotent retry with same/different body; status fetch; payout.* webhooks captured) |
| Functional suite | reuse `payouts/e2e/` (needs DEVSTACK_LABEL rework), `fts/e2e/` + `itf-e2e`; add golden flows: G1 Shared processed, G2 Direct processed, G3 insufficient ledger balance ⇒ failed/queued, G4 idempotent replay, G5 request-hash mismatch, G6 inactive fund account reject, G7 amount/mode caps, G8 Shield block |
| Unresolved fidelity gaps | monolith substitute behaviour for merchant config and pricing (recorded values needed); passport claim schema; cron cadence; Splitz live variants |
| Infra | ~9 service containers + 6 datastore containers + LocalStack + Kafka; 16–24 GB RAM class |

## 3. Environment 4 — Failure, retry, reversal and asynchronous status propagation

Builds on Env 2 and adds the components that determine final state when the bank outcome is not a clean success.

| Aspect | Content |
|---|---|
| Additional F3 | x-account-statements (SQS source-event consumer, statement enrichment, `VerifyPayoutFailedTransaction` inputs) |
| Additional F2 | Mozart simulator scripted scenarios: delayed success after failure code, `CBS:188` with/without UTR, duplicate txn, NEFT return ⇒ reversed, user_pending/OTP, gateway timeout; API-monolith relay substitute for `/v1/update_fts_fund_transfer` that forwards to PS `/update_payouts_with_fts` and `/update_payouts_details_with_fts` (contract from `payouts/internal/app/dtos/payoutUpdate.go`); admin contract substitute for `live/payouts/{id}/manual/status`, `/admin/payouts/{id}/reject`, `/payouts/retry`, `/payouts/manual_action` mapped to PS internal routes; Stork capture with timing; Kafka path enabled (FireStatusUpdateKafka on) for a cohort |
| Flows | F, I, H (webhook ordering), plus the four failure modes from `findings/13` table: A (webhook retries exhausted), B (Kafka drops reversed/failed), C (payout not in allowed fromState ⇒ silent 200), D (relay path lost inside monolith substitute), E (post-commit ledger failure on reversed) |
| Expected outcomes | reproduce "FTS terminal, payout initiated" with no automated repair; verify reversal ledger mirror; verify Shared FTS-failed ⇒ payout reversed remap; verify reservation release on terminal events; verify XAS debit-statement match blocks failed transition |
| Reset | FTS Redis/machinery purge between scenarios; ledger re-seed |
| Gaps | monolith-side relay retry semantics unknown; Recon/ART not included (F0); bene-bank-down Redis writer unknown (seed manually) |

## 4. Environment 1 — Dashboard single payout with roles and approval

| Aspect | Content |
|---|---|
| Additional F3 | x SPA (served via nginx; Playwright suite `e2e/payouts.spec.ts`); Workflow Service **F2** (Twirp Create + callbacks; retry on non-2xx; expiry) |
| Additional F2 | Kong-lite with dashboard-proxy behaviour: cookie session → monolith substitute `authenticate` (IP allowlist, merchant_users, OTP) → passport proxy with impersonation claims; AuthZ policy bundle for finance_l1/l2/l3/owner/view_only; monolith substitute routes `/merchant/api/live/payouts_with_otp`, `/payouts/{id}/approve|reject`, `/payouts/approve/bulk`, `/payouts/{id}/cancel`, `/payouts`, `/payouts/_meta/summary`, `/payouts/schedule/timeslots` proxying to PS with `LegacyAuthTypeProxy` |
| Flows | A, C (pending → approve/reject; DCS dual-name flag matrix; merchant-config cache staleness), scheduled payouts, on_hold via FTS health webhook |
| Status ceiling | **BLOCKED_BY_FIDELITY_GAP** for authentication/role enforcement until `razorpay/api` and `authz` are readable; PS-side behaviour can reach CROSS_SERVICE_CONFIRMED |
| Gaps | OTP/2FA enforcement location; server-side permission enforcement; WFS internals; Batch bulk approvals |

## 5. Environment 3 — Migration / proxy-cutover path

| Aspect | Content |
|---|---|
| F3 | payouts with `shadow_gateway.enabled=true` and onboarded routes (`get /v1/payouts/:id`, `get /v1/payouts`, `post /v1/payouts`) bound to Splitz stub variants native/proxy/shadow_api/shadow_ps; fts with both webhook targets |
| F2 | Kong with `payouts-proxy-cutover` template semantics (regex_priority shadow route, upstream-override by ctx, identification-only passport case); monolith substitute exposing proxy leg + `authenticate` + `update_fts_fund_transfer` relay + `dual_write` sink + `payouts_temp` MySQL |
| Flows | G: route mode resolution per merchant, fail-safe proxy on Splitz timeout, `X-PS-Proxied` loop guard, internal-consumer bypass, parity differ output, dual-write failure processing cron, reverse-dual-write to `payouts_temp`, legacy relay vs direct webhook per `fts_request_from_ps` |
| Status ceiling | CODE_CANDIDATE → CROSS_SERVICE_CONFIRMED for PS/FTS side; production-representative only with terraform-kong + api access |

## 6. Environment 5 — Bulk payouts

| Aspect | Content |
|---|---|
| F3 | + xperience (needs buf.build protos), x SPA bulk pages |
| F2 | Batch service (row validation per `<template>_validate`, ProcessBatch → PS `/v1/payouts/bulk` chunks with `X-Batch-Id`, FetchRows), Workflow Service bulk approve/reject, monolith batch CRUD proxy |
| Flows | D: upload → validate → approve → process → per-row idempotency races → NEFT/RTGS batch_submitted → cron release → per-row status via `GET /v1/payouts?batch_id` |
| Gaps | Batch row-validation rules unknown; concurrency race reproduction requires ≥2 Batch workers |

## 7. Protocol substitute specifications (F2)

| Substitute | Accepted requests | Validation | State transitions | Errors | Retry | Duplicates | Callbacks | Invariants | Behaviour source |
|---|---|---|---|---|---|---|---|---|---|
| Mozart simulator | 8 ops: transfer_init, transfer_status, gateway_auth, gateway_session, beneficiary_verify, beneficiary_register, fetch_account_balance, create_otp (`/fts/{gateway}/{version}/{op}`) | Basic Auth; body per `fts/internal/providers/mozart/request.go` | scenario table keyed by amount/beneficiary suffix → processed / failed(code) / retry / user_pending / reversed-on-status-check / delayed | codes from `error_code.go` (CBS:188, 7161, NSE404, ER028, DUPLICATE_TXN, INSUFFICIENT_FUND…) | none (FTS retries) | same gateway_ref_no on repeat init ⇒ duplicate txn | none (FTS polls) | UTR present iff processed or ambiguous-with-UTR | `fts/cmd/mock-server`, `config/env.default.toml` matrix, ITF mock-gateway |
| API monolith substitute | endpoints listed in findings/14 §1 + dashboard proxy + `update_fts_fund_transfer` + `authenticate` | Basic `rzp_live`; cookie session for dashboard | relay: on FTS webhook → PATCH PS `/update_payouts_with_fts` then POST details; `dual_write`: 200 sink | 401/403 for bad creds; 404 for unknown merchant | relay retry policy **unknown** (parameterise) | idempotent sinks | to PS via cred.API | merchant config stable per test | client code in `payouts/pkg/api`, `cfa/internal/apiservice`, `x-balances/internal/gateway/api_service`, Slack parity thread |
| Workflow Service substitute | Twirp `WorkflowAPI/Create`, action endpoints used by dashboard substitute | schema from `payouts/pkg/workflow` | created → pending → approved/rejected/expired; multi-step per workflow_state_map | Twirp errors | callback retry on non-2xx (PS returns 200 always) | one workflow per entity | POST/PATCH `/v1/workflow/state`, `/payouts_internal/{id}/approve|reject` with cred.Workflow | expiry ⇒ reject callback (assumption) | `payouts/pkg/workflow/*`, `internal/app/workflow/stateMap` |
| DCS stub | goutils/dcs get/patch | namespace `rzp/x/merchant/payouts/*` | static per merchant; PatchConfig from onboarding | — | — | — | — | both flag name sets served | `payouts/pkg/dcs`, `cfa/internal/dcsservice`, findings/12 A.6 |
| Splitz stub | Twirp EvaluateAPI/Evaluate, GetVariant | experiment id + merchant id | deterministic table; optional latency/timeout injection to test fail-safe proxy | — | — | — | — | variant stable within a run | `payouts/pkg/splitz`, shadowgateway/mode.go |
| Stork capture | Twirp `WebhookAPI/ProcessEvent`; SMS/email templates | Basic | store events with receive time; optional reorder/duplicate injection | 5xx injection | PS job retries 3 | — | — | none | `payouts/pkg/stork` |
| Shield stub | `pkg/shield` evaluate | Basic | allow; scripted block by beneficiary/amount; latency injection > 200 ms to prove fail-open | — | — | — | — | — | findings/15 §3.2 |
| Kong-lite | route table for `/v1/payouts*`, `/merchant/api/{mode}/*`, `/xperience*` | basic-auth-x, cookie | mint passport; upstream monolith-substitute or PS per route/merchant | 401/429 | — | — | — | claims match goutils/passport schema | findings/11 B; passport middleware in PS/xperience |
| ASV stub | gRPC account fetch | Basic | static | — | — | — | — | — | `payouts/internal/app/merchant/core.go` |
| Batch substitute (Env 5) | create/validate/process/fetch rows | template headers | validating → validated/failed → processing | per-row errors | chunk retry (to reproduce race) | duplicate idempotency_key per row | PS `/v1/payouts/bulk` | rows never re-submitted after success | xperience batch client |

Promotion rule: a substitute must be promoted to real execution (F3) when a test outcome depends on its internal ordering, retry, or authorization semantics — concretely: API monolith (dashboard auth, relay retries, admin authz), Workflow Service (OTP, expiry, multi-level), Kong (identity minting), Batch (row validation), Mozart (bank-specific ambiguity handling beyond the recorded catalogue).

## 8. Validation policy (downstream-control blind spots)

| Status | Definition | Applied when |
|---|---|---|
| CODE_CANDIDATE | Supported by source analysis in one repository; not verified across the adjacent services that can reject or transform it | any claim derived from a single lane without cross-service execution |
| CROSS_SERVICE_CONFIRMED | Reproduced across the actual adjacent F3 services that can reject/transform (e.g. PS + Ledger + FTS + CFA + x-balances) | Env 2/4 golden flows executed against real services |
| END_TO_END_CONFIRMED | Reaches an independently observed final synthetic outcome (DB state in PS + Ledger + FTS, captured merchant webhook) across the full selected workflow, with **no F1 or unknown component on the path** | Env 2 flow B; Env 4 flows F/I once Mozart simulator and monolith relay substitute are validated against recorded fixtures |
| PRODUCTION_REPRESENTATIVE | Effective DCS/Splitz values, Kong routes, authz policies, cron cadence and runtime traces have also been checked against exports | requires items in `NON_REPOSITORY_ACCESS_MATRIX.csv` categories A–C, H |
| BLOCKED_BY_FIDELITY_GAP | A missing or simplified dependency could materially alter the conclusion | Flow A/C authentication and role enforcement; Flow G Kong routing; admin server-side authz; monolith relay retry; FAV freshness gate (not found in code) |

Rules: any result depending on an F1 component or an `unknown` control is capped at CODE_CANDIDATE. Every environment run must record which substitutes were active and their fixture versions. A control marked `deployed`/`unknown` in `CONTROL_AND_INVARIANT_CATALOG.md` blocks PRODUCTION_REPRESENTATIVE for flows that traverse it.
