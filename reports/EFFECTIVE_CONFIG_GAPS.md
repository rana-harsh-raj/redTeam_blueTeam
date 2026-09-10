# Effective Config Gaps — Non-Repository Information Inventory

Compiled 2026-09-04, after the access-expansion pass (`ACCESS_DELTA.md`: 221→252 listable repos,
29→60 private-visible). This supersedes `NON_REPOSITORY_ACCESS_MATRIX.csv` (22 rows, baseline
2026-09-03) for every item that pass has since resolved, partially resolved, or reclassified.
Read-only investigation — no repository was written to.

**Inputs**: `NON_REPOSITORY_ACCESS_MATRIX.csv` (baseline), `ARCHITECTURE_DELTA.md` §(g)
(UNRESOLVED_QUESTIONS closure table), `ACCESS_DELTA.md`, and scratchpad findings 20–29
(`findings/20_api_monolith.md` … `findings/29_env2_scaffold.md`), plus `raw-findings/10` and
`raw-findings/11` for ownership/Slack attribution.

**Columns**: category · artifact · internal system (exact) · specific object/key · why needed ·
owner (team + Slack channel) · export/query method (exact API/CLI/UI) · minimum permission ·
safe synthetic substitute · fidelity lost · status.

**Status legend**: `RESOLVED-BY-REPO` (now fully answerable from a cloned repo, no export needed) ·
`EXPORT-NEEDED` (a real data export/API read from an internal system is still required) ·
`SYNTHETIC-OK` (a protocol-faithful synthetic substitute exists and is what the arena should
actually run) · `BLOCKED` (neither repo access nor a synthetic substitute closes the gap —
needs an owning-team ask or further access). Several rows carry two statuses where the
*mechanism* is resolved/synthesizable but the *real values* remain an external ask — this is
noted explicitly rather than picking one label.

---

## A. Effective runtime configuration

### A1. DCS per-merchant payout flags (both flag-name sets)

| Field | Value |
|---|---|
| Internal system | DCS (Dynamic Config Service) — `KVService`, primary store **AWS DynamoDB** (single-table, `PK="entity#merchant#<id>"`, `SK="kv/<namespace>/<entity>/<domain>/<object_name>"`), secondary **Aurora MySQL** mirror for the legacy-Proxy migration leg |
| Specific object/key | Key = `namespace/entity/entity_id/domain/object_name`. Confirmed fields: `rzp/x/merchant/payouts/Workflows{enable_payout_workflow, skip_workflow_for_dashboard, skip_workflow_for_payroll, skip_approval_workflow_for_api, enable_approval_via_oauth}`; `ApiInterface{payout_service_enabled, enable_http_encryption, payout_idem_key_required, below_rupee_payouts, ...}`; `Cfa{skip_ifsc_lookup}`; `FundLoading{skip_whitelisted_source_accounts}`; `direct_accounts/Configs{in_flight_reservation_enabled}`; `direct_accounts/PayoutModeConfig{allowed_upi_channels}`. (Note: bare name `payout_workflows` does not exist — the real field is `enable_payout_workflow`.) |
| Why needed | Approval applicability, in-flight reservation gating, IFSC-lookup skip, payload encryption, and UPI-channel allowlisting all branch on these live values — they are the recurring root cause class for prod cache/propagation bugs. |
| Owner | Common Platforms team (Shikhar Garg et al.) — `#tech_infra_dcs`, `#dcs-integrations`, `#tech-infra-common-platforms` |
| Export/query method | `POST {DCS_HOST}/v1/kv/get` (grpc-gateway REST/JSON over `dcs.kv.v1.KVService.Get`), body `{queries:[{key:{namespace,entity,entity_id,domain,object_name}, fieldmasks:[...]}]}`, auth = `POST /v1/auth/login {username,password}` → 6h bearer JWT. Batched read across multiple keys in one call; `Evaluate` variant resolves level-combining expressions. Write-side is maker-checker via admin-dashboard "DCS config update-requests" — irrelevant for a read-only export. |
| Minimum permission | Read-only DCS service-account credentials scoped to `rzp/x/merchant/payouts/*` for a named synthetic-merchant set (or admin-dashboard viewer role). |
| Safe synthetic substitute | `goutils/dcs/server/mock.Client` (in-repo, no network) seeded with the exact schema above; deterministic 3-merchant seed set (`merchant_test_001/002/003`) already authored in finding 26§A, keyed identically to the real `Key` structure. |
| Fidelity lost | Real merchant-cohort distribution and current production values are unknown; only the schema and defaults (proto3 zero-values) are confirmed. Workflow-bypass / cache-propagation bug class is not reproducible without real values. |
| Status | **EXPORT-NEEDED** (real values) / **SYNTHETIC-OK** (schema + stub mechanism fully resolved by repo) |

### A2. DCS export-gating flag pair (legacy↔DCS authority)

| Field | Value |
|---|---|
| Internal system | DCS Proxy v1/v2 — Aurora MySQL `Feature` table + DynamoDB, `proto/dcs/proxy/v1/proxy.proto` |
| Specific object/key | `DCSFeature.write_via_client` (field 3), `DCSFeature.read_enabled_via_dcs` (field 4) — best-match candidate for "the two flags" gating whether DCS or the legacy monolith Aurora table is authoritative for a given flag name; `dual_write_enabled` (field 8) + Splitz experiment `dcs_kv_dual_write_enabled` is a related but distinct third, reverse-sync gate. `PAYOUTS_FEATURES_FORWARD_DUAL_WRITE`/`REVERSE_DUAL_WRITE` current values were not found as static config anywhere (UNRESOLVED #6, still **OPEN**). |
| Why needed | Determines which system of record governs a given legacy feature flag during the DCS migration; directly implicated in the "forward/reverse dual write to `payouts_temp`" bug class reported in Slack (`razorpay/payouts#1946`). |
| Owner | Common Platforms — `#tech_infra_dcs`, `#dcs-integrations` |
| Export/query method | `POST /v1/proxy/features/config/update` (write); no single bulk-read API was found in-repo for current values — read via the DCS admin dashboard or its MCP tool surface (`dcs/internal/adminserver/mcp/mcp_dcs.go`). |
| Minimum permission | DCS admin-dashboard viewer role, or direct read-replica access to the Aurora `Feature` table. |
| Safe synthetic substitute | Not needed for the arena — the synthetic environment treats DCS as sole source of truth and does not run the legacy Proxy dual-write path at all. |
| Fidelity lost | n/a for arena; matters only if reproducing the legacy dual-write bug class specifically. |
| Status | **BLOCKED** (which flag pair is canonical, and their current values, is not resolvable from any repo — inferred from code behavior only) / **SYNTHETIC-OK** for arena purposes (path not exercised) |

### A3. Splitz experiment definitions + variant assignments (8 target IDs)

| Field | Value |
|---|---|
| Internal system | Splitz (`splitz.razorpay.com`) — MySQL `experiments`/`variants` tables (schema confirmed via migrations; content is DB-only) |
| Specific object/key | `TUnTsUB8Os2kX0` (`payouts_shadow_gateway_scheduled_time_slots`, devstack only), `Qnc6b4fg1Hi36k` (`merchant_config_via_asv_and_dcs_experiment`), `Sg7tEnMoxCbTzM` (`payout_workflows_dcs_name_experiment`), `TMp5VcIFYOHK2m` (`use_api_db_for_payout_status_details_experiment`), `PEVE4sUaVG6Drw` (xperience `BulkPayoutsExperiment`), `ORHtoFyuKLWUSw` (ledger `ledgerMakeshiftDualWriteExperiment`, RX↔PG dual-write switch); FTS's config-field names `CreateTransferMetaRollout`/`FireStatusUpdateKafka` resolve in prod to real IDs `ROyjRWsBowWdWY`/`PYGTRQEzO39PfB` (distinct from the 8 named IDs, also DB-only). **None of the 8 appear anywhere in the `splitz` repo itself** — exhaustive search of Go source, migrations, fixtures, `.github/` (finding 26§B). |
| Why needed | Routing (monolith vs Payouts Service), FTS webhook destination (direct vs relay), Kafka vs HTTP status path, RX↔PG ledger dual-write direction, and paise-rounding behavior all depend on per-merchant variant assignment for these experiments. |
| Owner | No single Splitz-platform-owning Slack channel identified; experiment *owners* are the consuming teams themselves — `#x-payouts-reliability` (payouts/fts), `#fin_infra_ledger` (ledger). Ask `#x-payouts-reliability` who owns the Splitz platform for a read grant. |
| Export/query method | Twirp `POST {SPLITZ_HOST}/twirp/rzp.splitz.evaluate.v1.EvaluateAPI/Evaluate` (single), `/EvaluateBulk`, or `/FetchDecisionContext` (full experiment definition — needed because payouts runs client-side evaluation and caches whole experiment objects). HTTP Basic auth. No admin CLI/UI export path was found in any cloned repo — only the runtime Evaluate API. |
| Minimum permission | Splitz service-account Basic Auth credentials, read-scoped to Evaluate/FetchDecisionContext for the 8 named experiment IDs — or direct read access to Splitz's own `experiments`/`variants` MySQL tables (admin-only). |
| Safe synthetic substitute | Fully specified deterministic stub already synthesized (finding 26§B): per-merchant variant assignments for `merchant_test_001/002/003` across all 8 IDs, served via a stub `Evaluate`/`EvaluateBulk`/`FetchDecisionContext` implementation matching the real Twirp JSON schema exactly. |
| Fidelity lost | Real traffic-split percentages, audience-rule targeting, and rule chains are unknown; only qualitative on/off/variant semantics inferred from consumer-repo call sites are preserved. |
| Status | **SYNTHETIC-OK** (stub built and usable today) / **BLOCKED** (real definitions are DB-only — UNRESOLVED #15 closed as "not resolvable from any repo") |

### A4. FastCron schedules (`payouts /v1/cron/*`)

| Field | Value |
|---|---|
| Internal system | FastCron (external SaaS) — reached via a Traefik `IngressRoute`/`Middleware` (`payouts-fastcron-ip-whitelist`) IP-restricted to FastCron's published source ranges; **not** a k8s CronJob (confirmed absent from `kube-manifests`) |
| Specific object/key | Per-endpoint cadence for `payouts_process_queued`, `payouts_auto_cancel_on_expiry`, `payouts_dispatch_stuck`, `payouts_pending_push_notification_cron`, `ca_check_fund_management_payout_cron`, `payouts_dual_write_failure_processing_cron`, `payouts_create/update_failure_processing_cron` — all exist as HTTP routes in the monolith/PS, but their invocation frequency is configured only inside the FastCron dashboard. |
| Why needed | Timing-dependent flows (auto-expiry sweep, stuck-payout dispatch, dual-write-failure reprocessing) cannot be timed correctly without the real cadence. Separately and importantly: **queued/scheduled/on_hold/reservation processing is NOT cron-driven at all** — it's ~25 always-on SQS/Kafka worker `Deployment`s (kube-manifests, finding 22§2) — so this gap is narrower than it first appears. |
| Owner | Payouts team (`#xp-payouts-service`, `#x-payouts-reliability`); FastCron itself is a third-party SaaS with no internal owning channel identified. |
| Export/query method | FastCron dashboard export (UI only — no API endpoint found in any cloned repo). Ask the Payouts EM (`kumar.ayush@razorpay.com`) or `#x-payouts-reliability` for dashboard access or a schedule dump. |
| Minimum permission | FastCron dashboard read access for the payouts job group. |
| Safe synthetic substitute | Arena runs the ~25 SQS/Kafka worker Deployments continuously (already the real mechanism for the bulk of "cron-like" behavior); the handful of genuine `/v1/cron/*` HTTP-triggered endpoints can be driven on an arbitrary fixed interval (e.g. every 60s) without matching prod cadence. |
| Fidelity lost | Exact sweep timing vs prod is approximate; low-severity since these are idempotent sweep jobs, not the primary payout-processing path. |
| Status | **EXPORT-NEEDED** (literal cadence strings) — delivery mechanism itself is **RESOLVED-BY-REPO** |

### A5. Kong rollout-knob values per environment

| Field | Value |
|---|---|
| Internal system | Kong / `terraform-kong` (declarative HCL, git-tracked — **not** a runtime export gap) |
| Specific object/key | `base/payouts-proxy-cutover/config.tf`: `payout_create_upstream_override_conditions` (1 named consumer `C0uw3CXseZPwmJ`, 100%, devstack only), `dashboard_override_conditions` (timeslots route, 100% for live+test, devstack only), `ip_restrictions_rollout=0` (off), no `rate-limiting` plugin on any payout route, `authz-enforcer` shadow-mode flags per surface (§1.3 table). |
| Why needed | Confirms which upstream (monolith vs Payouts Service) actually serves prod traffic today — closes UNRESOLVED #9. |
| Owner | Spine-Edge team — `#platform_spine_edge_oncall`, `#tech_infra_edge` |
| Export/query method | Already fully readable from the `terraform-kong` repo (cloned, `8ffe965b`). A live drift-check (only needed to catch config changes since this snapshot) would use `deck dump` against the real Kong admin API for stage/prod. |
| Minimum permission | n/a for baseline; Kong-admin read only if verifying drift. |
| Safe synthetic substitute | n/a — real values known. Kong-lite spec (finding 21§5) for a local reverse-proxy substitute is fully written. |
| Fidelity lost | None (fully resolved); only risk is drift between this commit and current prod state. |
| Status | **RESOLVED-BY-REPO** |

### A6. Governor / Charge Collections fee rules

| Field | Value |
|---|---|
| Internal system | Governor — MySQL `rules` table (namespaced, versioned JSON blobs, govaluate expression language), `POST /v1/namespaces/:namespace_id/execute`; Charge Collections — Pricing `Rule` proto/DB, `GET /v1/mdr/pricing/plans/fees_calculation/{id}` |
| Specific object/key | Payouts' 5 Governor rule-chain IDs (`payouts/pkg/ccSdk/config.go` `PayoutRuleChainCollection{BasicRuleChainID, ProductRuleChainID, BankingAccountRuleChainID, PayoutModeRuleChainID, AmountRangeRuleChainID}`) — one per fee-rule dimension. Actual production rule-chain-ID values are config-injected under `[ccSdkConfig.governor.rules]`, not located/read in this pass. |
| Why needed | Real fee/tax amounts and which pricing rule a payout matches. |
| Owner | Not identified in Slack evidence for Governor/Charge-Collections specifically — ask via `#tech_it`, or the payouts team (`payouts/pkg/ccSdk` is the consuming/config-owning package). |
| Export/query method | Governor admin API (`GET/POST /v1/rule_engine/rule/:namespace/...`, Basic Auth "admin" app) for rule content; Charge Collections `GetPricingPlansSummary`/`GetPricingPlan` for plan/rule content. |
| Minimum permission | Governor read-only Basic Auth "app" credential for the payouts namespace; Charge Collections read on payouts' plan IDs. |
| Safe synthetic substitute | `payouts/pkg/ccSdk` already ships a complete, deterministic in-repo mock: `CCSDKConfig.Mock=true` → `MockPayoutCalculator.GetFees`, keyed on `Purpose/Method/Mode/Amount/FeeType`, zero external calls — this is the arena's actual mechanism (confirmed to boot cleanly in the build spike, finding 28_build_spike_payouts.md). |
| Fidelity lost | Real fee-rule content (percent/fixed/min/max per bank×mode×amount-range) is not reproduced — the mock uses 5 hardcoded synthetic rules. |
| Status | **SYNTHETIC-OK** (arena uses payouts' own in-repo Mock — no external call needed) / **EXPORT-NEEDED** only if higher-fidelity real rule content is later wanted |

### A7. Shield fraud/risk rules

| Field | Value |
|---|---|
| Internal system | Shield — own DB, service not cloned (~6.7GB); contract fully reconstructed from `shield-sdk` + `payouts/pkg/shield` |
| Specific object/key | Named rule definitions for `entity_type=payout`; Payouts always requests Shield's **default** ruleset (`RuleSets=nil`, `SkipDefaultExecution=false`) — never a named subset. |
| Why needed | Payout blocking on fraud/risk signals. |
| Owner | Not identified in available Slack evidence — likely a Risk/Trust-adjacent team; ask via `#tech_it` for the `shield` repo/service owner. |
| Export/query method | No admin CRUD path was inspected (service not cloned); would require a direct ask to Shield's owning team for rule content, or a stage Shield sandbox credential. |
| Minimum permission | Read-only Shield rule-listing (existence unconfirmed) or a stage Shield instance credential. |
| Safe synthetic substitute | Fully specified 3-merchant stub (finding 26§D): `POST /v1/rules/evaluate/payout`, response `{action: allow\|block\|review, triggered_rules, status_code}`. Payouts' own code only branches on `"block"` (fail-open on error, timeout, or `"review"` — `review` is currently indistinguishable from `allow` in Payouts today, a real gap worth flagging to the payouts team). Configured timeout is 200ms with a Hystrix circuit breaker (opens at ≥20 requests/≥50% error rate, 5s sleep window) — replicate this in the stub for realistic fail-open behavior. |
| Fidelity lost | Real rule logic (velocity checks, beneficiary risk scoring) is not reproduced; only three canned verdicts. |
| Status | **SYNTHETIC-OK** (contract-faithful stub spec complete) / **BLOCKED** (real rule content — service repo not cloned) |

---

## B. Gateway / edge identity

### B1. Passport JWT signing key / JWKS

| Field | Value |
|---|---|
| Internal system | Edge `kong-plugin-upstream-jwt` — file-mounted RSA keypair (`/ssl/JWT_PRIVATE_KEY_V2`/`/ssl/JWT_PUBLIC_KEY_V2`, `key_id="edgev2"` prod / `"edgev1"` dev). **No Vault/JWKS integration found** in Edge's own runtime path — key bytes are read from a mounted file path and cached forever. |
| Specific object/key | Full claim schema documented exactly (finding 21§2.1): `nbf, exp(+300s hardcoded), jti, payloadhash, iat, iss="edge", aud, mode, org, product, identified, authenticated, credential.*, merchant.id, consumer.{id,type}, impersonation.*, oauth.*, roles`. **Correction**: there is no `authType` claim in production — don't add one to a local signer. |
| Why needed | Payouts Service, ledger, and xperience validate `X-Passport-JWT-V1` against this key/claim shape. |
| Owner | Spine-Edge — `#platform_spine_edge_oncall` |
| Export/query method | Not needed — the arena is its own trust root and generates its own keypair (per the task's own framing). |
| Minimum permission | None required. |
| Safe synthetic substitute | Arena generates its own RSA keypair and self-signs passports with the exact documented claim shape and header (`{typ:"JWT",alg:"RS256",kid:"edgev2"}` or any local `kid`, consistent between mint and verification) — fully specified in finding 21§5 ("Kong-lite spec"). |
| Fidelity lost | None for claim shape/mechanics; the real prod signing key is (by design) never reproduced. |
| Status | **RESOLVED-BY-REPO** / **SYNTHETIC-OK** |

### B2. AuthZ policy bundle (CSV) — resolved

| Field | Value |
|---|---|
| Internal system | `razorpay/authz` — Consul-synced Casbin policies, standard CSV import format |
| Specific object/key | `scripts/policies/xplatform.csv` (owner/admin/finance_l1-3/operations/view_only × create/approve/reject/cancel/fetch), `payout_approve_reject_view` role (the "CAC" policy set — a second, independently-evolved policy set for largely the same capability), PR 2577's 14-role timeslots CSV. Full excerpt already extracted verbatim in finding 21§3.5. |
| Why needed | Role→permission mapping for X dashboard payout actions (create/approve/reject/cancel/view). |
| Owner | Spine-Edge / AuthZ — `#platform_spine_edge_oncall` |
| Export/query method | n/a — CSVs are git-tracked in `razorpay/authz`, already read in full. |
| Minimum permission | n/a |
| Safe synthetic substitute | Load the CSV directly into a `goutils/authz/v2`-compatible Casbin enforcer, OR skip real enforcement in Env 2 entirely — prod's own `authz-enforcer` Kong plugin runs in shadow mode **and** the public-API payout-create route is explicitly whitelisted, so it never blocks in prod today either. |
| Fidelity lost | None for the bundle itself. |
| Status | **RESOLVED-BY-REPO** |

### B3. Consul runtime state for authz (`watchResourceGroups`, org scoping)

| Field | Value |
|---|---|
| Internal system | authz enforcer's live Consul KV, `{consul.key_prefix}/{resource_group}` (blocking long-poll, not gRPC streaming) |
| Specific object/key | Whether prod's authz-enforcer config actually watches the `x_platform` resource group (the repo's own `config/prod.toml` **excludes** it, while stage/bvt/slit/dev-serve include it); whether PR 2577's policy-seeding pipeline used `POLICY_ORG_ID`/`POLICY_SUBPRODUCT` = `100000razorpay::banking::non_lms`, `::pg::`, or something else (org scoping is a literal-string match with no hierarchy, and no CSV carries an org column). |
| Why needed | Determines whether authz actually enforces for merchant-dashboard "banking" traffic in prod today — UNRESOLVED #11, still **PARTIAL**. |
| Owner | Spine-Edge / AuthZ — `#platform_spine_edge_oncall` |
| Export/query method | No export mechanism found in-repo — requires directly asking the authz team (a) what prod's actual enforcer config is (may differ from the repo's committed `config/prod.toml`), and (b) what org string PR 2577's pipeline run actually used. |
| Minimum permission | n/a — informational ask, not a data export. |
| Safe synthetic substitute | Not needed for the arena — B2's CSV-load approach doesn't depend on Consul at all. This gap only matters for verifying real prod enforcement behavior, not for building the arena. |
| Fidelity lost | n/a for arena; matters only to the audit's own open question. |
| Status | **BLOCKED** |

---

## C. Identity / merchant metadata

### C1. ASV (Account Service) merchant metadata

| Field | Value |
|---|---|
| Internal system | `razorpay/asv` / `razorpay/account-service` — **both still return 404** to this identity (ACCESS_DELTA.md). gRPC client contract (`goutils/account-service`) is fully known even though the service repo is not. |
| Specific object/key | Merchant `activated`/`hold_funds` flags, resolved via `X-DB-Source` header selecting `ApiMaster` vs `AsvReader` backing store. |
| Why needed | Gates certain payout paths on merchant activation state. |
| Owner | Not identified from available evidence — repo access unresolved. |
| Export/query method | n/a — repo inaccessible; request read access to `razorpay/asv` or `razorpay/account-service` via `#tech_it` (same request path as `api`/`authz`/`mozart`, per `#tech_it` process documented in raw-findings/11). |
| Minimum permission | Read on the ASV/account-service repo. |
| Safe synthetic substitute | `payouts/pkg/account/mock.go` (already shipped, production code, not a new build) — `account.NewMockClient().WithFailure(err)/.WithNilResponse()/.WithCustomResponse(*dto.AccountResponse)`; returns `{activated:true, hold_funds:false}` by default for all synthetic merchants. Verified in the build spike to correctly gate real gRPC construction when `Mock=true`. |
| Fidelity lost | No negative-path (deactivated / hold_funds) merchant behavior is exercisable unless the mock is explicitly extended per-test. |
| Status | **SYNTHETIC-OK** (arena mechanism complete) / **BLOCKED** (real repo access) |

---

## D. Data definitions

### D1. Monolith DB rows: merchants, features, balance, merchant_users, admin roles/permissions

| Field | Value |
|---|---|
| Internal system | API monolith's own MySQL (`razorpay/api`, now readable — cloned `2d665f918b60`) |
| Specific object/key | `merchants`; `features` (46 `PAYOUT`/`FUND_ACCOUNT`-prefixed constants confirmed, e.g. `PAYOUT_SERVICE_ENABLED`, `LEDGER_REVERSE_SHADOW`, `ENABLE_APPROVAL_VIA_OAUTH`, `SKIP_HOLD_FUNDS_ON_PAYOUT`, `BLOCK_VA_PAYOUTS`, `ICICI_2FA`); `balance` (confirmed the monolith's own source-of-truth for banking/X balances, read even by PS via `internal_balances_queued`); `merchant_users` (**no DB-backed membership check found inside `api` at all** — the monolith trusts the upstream Passport JWT; the check plausibly lives in Edge/dashboard-api, out of this repo's scope); local `payouts` table (confirmed **schema-present, not deleted** — still read/written by approve/reject, BAS-recon fallback, manual batch status update, IRCTC-Axis flows, and is a physically distinct table from PS's own same-named `payouts` table reached via `Connection::PAYOUT_SERVICE_DATABASE`); admin→role→permission tables backing `AdminAccess::policyChecker` (confirmed **local, DB-backed**, not a remote AuthZ call for this auth type). |
| Why needed | Which merchants are PS-migrated vs legacy; per-merchant feature gates; balance-of-record for legacy-path merchants; admin permission enforcement for the ~10 admin-dashboard payout actions. |
| Owner | Payouts team + core API owners — `#xp-payouts-service`, `#tech_it` (repo access already granted) |
| Export/query method | Schema-only DDL dump of the referenced tables — no live-row export requested (data-sensitive). Columns are independently enumerable from cfa's `APIBalance`/`APIStore` structs, x-balances' model, and the monolith's own migration files (`database/migrations/2016_06_16_081431_create_payouts_table.php`, etc. — all now readable). |
| Minimum permission | Schema-only (DDL) read on the listed `api` DB tables — request via `#tech_it` / `#x-payouts-reliability`. |
| Safe synthetic substitute | Reconstruct DDL from the Go/PHP model structs above; seed synthetic rows for merchants with `payout_service_enabled=1` (PS path — fully testable) and `=0` (legacy path — degraded fidelity, since legacy business logic itself lives in the ~23,000-line `Route.php` + `Payout/Service.php` and isn't independently re-executable outside the monolith). |
| Fidelity lost | Legacy (non-PS) path behavior only approximately reproducible without running the actual monolith; real admin-role membership data unavailable. |
| Status | **RESOLVED-BY-REPO** (route/model logic) / **EXPORT-NEEDED** (DDL confirmation, optional) / **SYNTHETIC-OK** (seed data) |

### D2. TiDB `payouts_temp` DDL

| Field | Value |
|---|---|
| Internal system | TiDB, reached from the monolith via `Connection::PAYOUT_SERVICE_DATABASE` (PS's own DB) |
| Specific object/key | **No table literally named `payouts_temp` was found anywhere in `razorpay/api`** (exhaustive case-insensitive search). Closest analogue: `payout_meta_temporary` — the dual-write bookkeeping watermark table, which itself lives in PS's own database (consistent with a TiDB-backed store). Slack evidence (`razorpay/payouts#1946`) independently describes a "forward dual write to `payouts_temp` (TiDB/Data Lake)" feeding Dashboard/reports, Splitz-gated. |
| Why needed | Confirms/denies the forward-dual-write bug class referenced in Slack incident threads. |
| Owner | Payouts team — `#x-payouts-reliability` |
| Export/query method | Direct ask to the Payouts team for the actual schema; alternatively `wda-service`'s Twirp API (`goutils/wda` client — HTTP Twirp, no direct TiDB connection string in any SDK) may expose a read path if `payouts_temp` is one of its indexed tables. |
| Minimum permission | WDA Service read credentials scoped to the relevant table, or a direct schema ask to the Payouts team. |
| Safe synthetic substitute | MySQL stand-in for TiDB (`goutils/spine` supports the MySQL dialect directly) seeded with `payout_meta_temporary`-shaped rows. |
| Fidelity lost | TiDB-specific behaviors (distributed transactions, HTAP) not reproduced by a MySQL stand-in. |
| Status | **BLOCKED** (exact table/schema under this literal name not confirmed to exist) / **SYNTHETIC-OK** as a MySQL approximation |

### D3. DynamoDB-backed secret store (`secret_cloner`) — names only

| Field | Value |
|---|---|
| Internal system | `kube-manifests/tools/hooks/secret_cloner` (Go, `controllers/dynamodb.go`) — Credstash-style DynamoDB (+ presumably KMS) backing plain k8s `Secret`s for payouts/fts/cfa/x-balances (ledger additionally uses `ConfigMap`s for non-secret config). **No Vault** is used for these five services; no `ExternalSecret` CRDs or `vault.hashicorp.com/agent-inject*` annotations found anywhere. |
| Specific object/key | k8s Secret names only (`payouts`, `ledger-live`, `ledger-test`, `fts-live`, etc.); env-var **names** fully enumerated from Helm templates (`APP_MODE`, `APP_ENV`, `JAEGER_HOSTNAME`, `PAYOUTS_TELEMETRY_EXPORTERHOST`, per-worker `PAYOUTS_WORKER_NAME`, etc.). Secret **values** never captured or requested. |
| Why needed | Building a synthetic env file for the arena requires knowing every var name a service expects at boot. |
| Owner | Infra/Platform — `#tech_it`; the DynamoDB secret store itself has no dedicated owning channel identified. |
| Export/query method | `kubectl get secret <name> -o yaml` for the operational path (values still base64-only, still requires access — not requested); for names-only, `kube-manifests`' own Helm templates already enumerate every `secretKeyRef`/`envFrom` reference (already read in full, finding 22§1). |
| Minimum permission | Read on `kube-manifests` (already granted) is sufficient for names; no DynamoDB/KMS access needed or requested. |
| Safe synthetic substitute | Arena's own `.env`/TOML files populate each named var with a synthetic value — exact key→real-host→arena-value mapping already produced per service in the build spike (`findings/28_build_spike_{payouts,fts,ledger,cfa,x-balances}.md`). |
| Fidelity lost | None for arena purposes — names fully enumerated, values synthetic by design. |
| Status | **RESOLVED-BY-REPO** (names) / **SYNTHETIC-OK** (values) |

---

## E. Event infrastructure

### E1. Kafka topic/ACL inventory

| Field | Value |
|---|---|
| Internal system | MSK Kafka (`prod-noncde-kafka.razorpay.com:9090`) |
| Specific object/key | Topic names, consumer groups, READ/WRITE/DESCRIBE ACLs for `rx-fts-status-update-events`, `fts_status_updates_retry`, `payout-kafka-topic`, `add-tds-entry`, vendor-payments topics, etc. |
| Why needed | Workers need matching topic/consumer-group names to run against a local Kafka/LocalStack. |
| Owner | Datastores team — `#gandalf` (self-serve bot: provide cluster, region, env, topic name, consumer group, exact permissions) |
| Export/query method | `#gandalf` bot ticket/PR — structured Slack request, self-serve. |
| Minimum permission | DESCRIBE-only suffices for a names/ACL inventory export; no READ/WRITE needed since the arena runs its own local Kafka. |
| Safe synthetic substitute | Local Kafka/LocalStack with identical topic and consumer-group names (a docker-compose for this already exists at `payouts/deployment/dev`). |
| Fidelity lost | None if names/partitions are preserved. |
| Status | **EXPORT-NEEDED** — still **OPEN** per `ARCHITECTURE_DELTA.md` item #29 (not re-addressed in the latest pass) |

### E2. Alert thresholds — resolved

| Field | Value |
|---|---|
| Internal system | `razorpay/alert-rules` (now cloned, `c372beabedf8`) |
| Specific object/key | `fts_transfer_webhook_update_failure_count` (>50/>10 per 15min, `for: 10m`), `payouts_service_stuck_payouts_count{channel="IDFC",mode="IMPS"} > 5`, `{source="xpayroll"} > 0`, Payout SLA Breach % rules for IMPS/UPI/NEFT/settlements, `Payout Source Updater job failures > 50/15min`. |
| Why needed | Confirms FTS webhook-failure alerting is aggregate-only — a single stuck payout does not page; stuck-payout alerts exist only for IDFC/IMPS and xpayroll. Closes UNRESOLVED #20. |
| Owner | Payouts/FTS on-call — `#x-payouts-reliability`, `#payout-service-alerts`, `#stuck-payouts-alerts` |
| Export/query method | n/a — already read in full from the cloned repo. |
| Minimum permission | n/a |
| Safe synthetic substitute | n/a — informs arena alerting parity if ever built, not itself a config dependency. |
| Fidelity lost | None. |
| Status | **RESOLVED-BY-REPO** |

### E3. SQS/SNS queue inventory + Kubernetes ServiceAccounts/IAM roles

| Field | Value |
|---|---|
| Internal system | AWS SQS/SNS; kube-manifests IAM (`fts kube2iam role prod-fts`; other services use IRSA) |
| Specific object/key | ~25 payouts SQS/Kafka worker `Deployment`s with queue names visible as container args (`payouts-worker-queued-processing-live`, `-on-hold-processing-live`, `-schedule-processing-live`, `-data-consistency-checker-processing-live`, `-fts-async-processing-live`, `-bulk-payouts-live`, `-webhook-event-live`, `-kafka-fts-status-updates-consumer`); cfa dual-write queues; x-balances `balance_refresh`/`payout_events`; xas 12 queues; ledger jobs; SNS `source_update_topics`, `journal-created` |
| Why needed | Workers consume these queues directly; the arena needs matching queue names and equivalent IAM/ACL permissions |
| Owner | Infra/Datastores — `#gandalf` |
| Export/query method | Queue **names** now `RESOLVED-BY-REPO` (visible as worker container args in `kube-manifests`, already read); IAM role/ACL grants still via `#gandalf` (cluster, region, env, queue, READ/WRITE/DESCRIBE) |
| Minimum permission | List-only IAM/ACL read, no data access |
| Safe synthetic substitute | LocalStack/ElasticMQ, same queue names preserved (arena already has this wired) |
| Fidelity lost | None material if names/partitions preserved |
| Status | **RESOLVED-BY-REPO** (names) / **EXPORT-NEEDED** (ACL/IAM grants) |

### E4. CDC/outbox topology

| Field | Value |
|---|---|
| Internal system | Debezium/Maxwell, Hudi lake, XAS CDC topic |
| Specific object/key | `mysql_cdc_events_prod_rx_account_statements_*`, `realtime_hudi_api.payouts`, `payout-kafka-topic` (charge-collections producer unknown) |
| Why needed | Determines downstream observers; none mutate payout state in core flows, so this is low-priority |
| Owner | Data platform/Datastores — `#gandalf` |
| Export/query method | Topic inventory via `#gandalf` |
| Minimum permission | n/a |
| Safe synthetic substitute | Omit in Env 1 (F0); observe-only |
| Fidelity lost | Analytics/billing not represented |
| Status | **EXPORT-NEEDED** (low priority, F0-deferred) |

---

## F. Bank & third-party integrations

### F1. Mozart bank credentials/certs

| Field | Value |
|---|---|
| Internal system | Mozart's Vault-backed secret store (`[credentials.vault]`, username `vault`, password from env var `LUMBERJACK_SECRET`) + per-gateway `[secrets.<gateway>]` TOML blocks (`rbl`, `icici`, `icici_tpv`, `yesbank`, `yesbank_upi`, `idfc_upi`, `citi`, `m2p`, `m2p_capital`, `atos`, `netbanking_*` — structure/names only, no values captured). |
| Specific object/key | Real bank signing certs/credentials per gateway. |
| Why needed | Only for real bank connectivity — explicitly **out of scope** for the arena (Env 1/2, `-mock` mode suffices). |
| Owner | Mozart / NB+ team — `#payments_nbplus` |
| Export/query method | Not requested — arena does not need real bank credentials. |
| Minimum permission | None required. |
| Safe synthetic substitute | `mozart -mock` — a built-in, first-class simulator (`mozart/app/mock/`, CLI flag) that runs the **identical production config/routing pipeline** against canned JSON fixtures under `mozart/app/testdata/fts/**` (hundreds already exist: RBL v1-v5, ICICI v1-v3/imps, Yes Bank v1/v2/UPI, Axis v1, IDFC UPI, M2P, Kotak, Citi, Amazon Pay). No Vault, no real credentials, no bank network calls. This closes UNRESOLVED #27. |
| Fidelity lost | None for wire-contract/error-code fidelity (same config pipeline as prod); real bank latency/edge-case distribution beyond what's captured in fixtures is not reproduced. |
| Status | **SYNTHETIC-OK** (`mozart -mock` is the arena's actual mechanism, not a new build) |

### F2. Stork merchant webhook subscriptions + secrets

| Field | Value |
|---|---|
| Internal system | Stork's own MySQL (`webhooks`, `subscriptions` tables); `webhooks.secret VARBINARY(2048)` AES-encrypted at rest via `stork/internal/crypto/crypto.go` |
| Specific object/key | Per-`(webhook, event_name)` `Subscription` rows for real merchants; `webhook.secret` (the HMAC-SHA256 key used to sign `X-Razorpay-Signature`, over the raw payload). No caller of `WebhookAPI/Create` was found in the `payouts` repo — merchant-facing registration is presumably in the API monolith, unconfirmed. |
| Why needed | Real merchant webhook-subscription state is otherwise invisible from payouts' own repo. |
| Owner | Stork team — no dedicated Slack channel confirmed (`#tech_infra_stork` has one unanswered onboarding-SOP question, 2026-06-15). |
| Export/query method | `POST /twirp/rzp.stork.webhook.v1.WebhookAPI/List`, filtered by `owner_id`/event, for a named set of test merchants. No bulk DB export path found in-repo. |
| Minimum permission | Stork service-credential Basic Auth, read-scoped to `List`/`Get` only (no `Create`/`Delete` needed). |
| Safe synthetic substitute | Full "Stork capture" substitute already specified (finding 25§A.10): a drop-in Twirp-compatible server implementing `Create`/`List`/`ProcessEvent`/SMS/Email `Send`, replaying the exact HMAC-SHA256 signature scheme (so real merchant-side signature-verification code tests unmodified against it), with a documented seed-subscription payload covering all 8 `payout.*` events. Deliberately **does not** implement dedupe on `event.id` — matches real Stork's at-least-once semantics. |
| Fidelity lost | Real registered-merchant subscription data is not reproduced by design — synthetic merchants register their own test subscriptions against the capture service instead. |
| Status | **SYNTHETIC-OK** (capture-service spec fully built) — real subscription data is **EXPORT-NEEDED** only if reproducing actual prod merchant webhook configs is ever required (not needed for the arena) |

### F3. UPS (payments-upi) VPA validation contract

| Field | Value |
|---|---|
| Internal system | `razorpay/payments-upi` — now cloned |
| Specific object/key | `POST /v1/payments/validate/account` (`ValidateAccount`, `internal/app/validate/server.go`). Request `{entity, value}`, response `{vpa, success, customer_name}`; HTTP Basic auth + `X-Origin: payouts` header (server-side special-cases payouts specifically to receive the plaintext VPA — other callers may get it masked). |
| Why needed | UPI beneficiary validation on the payout-creation path |
| Owner | Payments-UPI team (not separately identified in available Slack evidence) |
| Export/query method | n/a — fully resolved by repo read |
| Minimum permission | n/a |
| Safe synthetic substitute | ITF fixture `payments-upi/tests/slit/testdata/vpa_validate.go` — `Input:{Entity:"vpa",Value:"test.cust@icici"}`, `ExpectedResponse:{"vpa":"test.cust@icici","success":true}`, plus real-error-code failure shapes (`BAD_REQUEST_PAYMENT_UPI_INVALID_VPA`, `PGUP000078`) — sufficient to build a protocol-faithful mock without Mozart/Splitz/Terminal-service/Redis/MySQL |
| Fidelity lost | None |
| Status | **RESOLVED-BY-REPO / SYNTHETIC-OK** |

---

## G. Runtime evidence

### G1. Coralogix / HyperDx traces

| Field | Value |
|---|---|
| Internal system | Coralogix (`razorpay-prod.app.coralogix.in`), HyperDx, Grafana/Prometheus |
| Specific object/key | `WORKFLOW_CREATE_PAYOUT_FLOW`, `IsWorkflowApplicable`, `INFLIGHT_GATE_DECISION`, `PAYOUT_UTR_UPDATED_FROM_TRANSFER_STATUS_WEBHOOK`, `TransferWebhookUpdateFailureCount`, `StuckPayoutsCount` trace events; incident threads for specific stuck-payout IDs. |
| Why needed | Confirms which real-traffic path (direct vs monolith-relay, Kafka vs HTTP status update) and timing is actually exercised in prod — a fact no repo read can supply. |
| Owner | Payouts on-call — `#x-payouts-reliability`, `#payout-service-alerts` |
| Export/query method | Read-only Coralogix access for stage/prod payouts/fts/ledger — request via `#tech_it`. |
| Minimum permission | Read-only log/trace query role, scoped to the payouts/fts/ledger namespaces. |
| Safe synthetic substitute | None — this category is fundamentally about validating real traffic, not something the arena can synthesize. |
| Fidelity lost | `PRODUCTION_REPRESENTATIVE` status is unattainable without this. |
| Status | **EXPORT-NEEDED** |

---

## H. Testing / fixtures

### H1. ITF mock-gateway fixtures vs `mozart -mock` fixtures — resolved

| Field | Value |
|---|---|
| Internal system | ITF mock-gateway (payouts CI) and `mozart -mock` (finding 25§B.4) |
| Specific object/key | Choice of canonical bank-response-simulation fixture source. |
| Why needed | Both exist; the audit needed to determine which to prefer for a sandbox bank simulator. |
| Owner | Payouts (Preetham M R) — `#x-payouts-reliability` |
| Export/query method | n/a — both already fully characterized. |
| Minimum permission | n/a |
| Safe synthetic substitute | `mozart -mock` is preferred (byte-identical prod routing/config pipeline, hundreds of pre-existing fixtures) over building a new synthetic simulator from scratch; FTS's own simpler `cmd/mock-server` (amount-keyed convention) is a documented secondary precedent for teams that can't stand up full Mozart. |
| Fidelity lost | None. |
| Status | **RESOLVED-BY-REPO** |

### H2. Test Data Manager merchants / self-serve test-merchant provisioning

| Field | Value |
|---|---|
| Internal system | Test Data Manager (`/v1/testdata/merchant`) — the only self-serve test-merchant path found in Slack evidence. `devstack` itself was confirmed to provide **zero** merchant/gateway/payments-domain functionality — it is generic internal-cluster DevX tooling (Helmfile+Devspace+Traefik+LocalStack), not a fixture system (finding 27§D, closes UNRESOLVED #36 as a non-answer). |
| Specific object/key | A pooled test merchant with FTS + mock-gateway already wired. |
| Why needed | Avoids hand-building fixtures from scratch; reuses Razorpay's own golden E2E flows. |
| Owner | Platform-DevStack (`#platform-devstack`) / Payouts (`#x-payouts-reliability`) — no definitive single owner found. |
| Export/query method | Direct ask in `#platform-devstack` or `#x-payouts-reliability` for `/v1/testdata/merchant` API docs/access. `e2e-test-orchestrator` and `qa-tools` repos remain **404** to this identity (UNRESOLVED #37, still open — no new access gained this pass). |
| Minimum permission | Read/invoke access to the Test Data Manager endpoint, or repo read on `e2e-test-orchestrator`/`qa-tools`. |
| Safe synthetic substitute | Arena builds its own fixtures from payouts' `e2e/dataProvider` — the fallback the baseline audit already recommended. |
| Fidelity lost | Risk of drift from Razorpay's own golden E2E flows over time. |
| Status | **BLOCKED** (repo/API access) / **SYNTHETIC-OK** (fallback exists) |

### H3. Recon `workflow_configs` (runtime DB table)

| Field | Value |
|---|---|
| Internal system | `recon-postgres` (ART's own Postgres RDS) — `workflow_configs` table |
| Specific object/key | Endpoint template strings (e.g. `/v1/internal/{0}/reconcile/`) that recon's Kafka-driven `WorkflowProcessor` dispatches to FTS's `PATCH/POST /v1/attempts/:action` and `PUT /v1/attempts/reconcile` (Basic auth, `ARTAuth` — `fts/internal/constants/authusers.go:6`), against `RECON_FTS_LIVE_BASE_URL = https://fts-live.razorpay.com/v1`. |
| Why needed | Recon **can and does** mutate FTS attempt/transfer/payout status via a human-gated (FinOps re-upload of an "RX Workflow" file) path — mechanism and FTS-side authorization are fully confirmed by repo (`fts/internal/routing/router/route_list.go:172-181`), but the literal endpoint templates currently configured are runtime DB rows, not in git. Recon should be pulled into the Env 4 (failure/retry/reversal/async-status) closure at tier F2, not left at blanket F0 (finding 27§A.7). |
| Owner | `recon-dev` team, tier **T1** — `#fin_infra_recon` (CODEOWNERS: `@razorpay/recon-dev`; note the `servicetree.yaml` EM field is flagged "TERMINATED" — escalate via L2, `vivek.agarwal@razorpay.com`) |
| Export/query method | Read of the `workflow_configs` table (recon-postgres) or a documented list from the recon team — no admin API for this was confirmed. Ask via `#fin_infra_recon`. |
| Minimum permission | Read-only on `workflow_configs` for the FTS-facing rows. |
| Safe synthetic substitute | For Env 4 testing: seed a synthetic `workflow_configs` row directly and drive the Kafka message manually (`{end_point, service_name:"FTS", method, payload}`, JSON-schema-validated per row) — full mechanism already reverse-engineered (finding 27§A.4); the real Spark/EMR 3-way-matching engine that produces the RX Workflow file is not needed for this. |
| Fidelity lost | Exact endpoint templates currently in prod use are approximated, not reproduced exactly; the matching-engine behavior itself is not reproduced at all — acceptable per the audit's own F2-tier recommendation. |
| Status | **EXPORT-NEEDED** (literal DB rows) / **SYNTHETIC-OK** (mechanism, sufficient for Env 4) |

---

## I. Approval / batch config — resolved

### I1. Workflow Service (WFS) config templates

| Field | Value |
|---|---|
| Internal system | `razorpay/workflows` — `internal/dto/config_model.go`, `template_model.go` |
| Specific object/key | `ConfigTypeToDetailsMap` (5 config types incl. `ConfigTypePayoutApproval` → outbound `ServiceName="rx_live"`); full `Template{StateTransitions, StatesData, AllowedActions, Meta}` JSON schema; real functional-test fixture JSON reproduced verbatim in finding 23§A.3-A.4 (a concrete two-step `AND`/`OR` payout-approval config). ASL (Amazon-States-Language) is a second, distinct template kind for non-approval workflow automation. |
| Why needed | Approval state-machine structure for multi-level payout approvals. |
| Owner | Not explicitly identified for the `workflows` repo in available Slack evidence (image-naming convention strongly implies it backs `wf-service`); ask `#x-payouts-reliability`. |
| Export/query method | n/a — fully resolved by repo read. |
| Minimum permission | n/a |
| Safe synthetic substitute | n/a — real schema used directly. |
| Fidelity lost | None. |
| Status | **RESOLVED-BY-REPO** |

### I2. Batch batch-type JSON definitions

| Field | Value |
|---|---|
| Internal system | `razorpay/batch` — `src/main/resources/*.json` |
| Specific object/key | `payout.json` (classic bulk-create → `payouts/bulk`), `payout_approval.json` (bulk approve/reject → `payouts/bulk_approve`), `payout_link_bulk[.v2].json`, `v2/payouts_{amazonpay,bank_transfer,upi}_bene_{id,details}_process.json`, `tally_payout.json`, `fund_account[.v2].json`. `BatchType.name` must exactly match the JSON filename. Pipeline: `DataStaging → DataProcessing → OutputCreation → Notification`, `maxThreads=10, chunkSize=1, bulkSize=5`. Confirmed: **no field-level row validation** (IFSC format, amount range, etc.) happens in Batch — it's all downstream in Payouts Service; Batch only validates file structure/headers/row-count. |
| Why needed | Bulk payout create/approve batch definitions and the exact endpoints they call. |
| Owner | Batch service centrally owned; individual product teams (incl. Payouts) own their own dashboard migration to it — `#api_decomposition` |
| Export/query method | n/a — fully resolved by repo read. |
| Minimum permission | n/a |
| Safe synthetic substitute | n/a — real JSON configs used directly. |
| Fidelity lost | None. |
| Status | **RESOLVED-BY-REPO** |

---

## J. Service catalog and ownership — resolved

### J1. Service→repo→team→on-call mapping

| Field | Value |
|---|---|
| Internal system | `.github/repo_owners.json` (every repo in scope), `razorpay/knowledge-base` RKG, Slack ownership table |
| Specific object/key | payouts/cfa/fts/x-balances/relay → EM `kumar.ayush@razorpay.com` (Business Banking pod), Slack `#xp-payouts-service`, CODEOWNERS `@razorpay/razorpayx_payouts_be`; xperience/x-account-statements/payout-links → EM `abhishek.sk@razorpay.com` (Payouts Platform pod); vendor-payments/virtual-account → EM `apurva.shivkumar@razorpay.com` (Co-brand + Onboarding); ledger → EM `aditya.jalan@razorpay.com` (Platforms/Recon, tier **T1**, `servicetree.yaml`, `#platform-ledger-alerts`). **fts has no service-catalog file of any kind** (no `servicetree.yaml`/`catalog-info.yaml`/`repo_owners.json`-equivalent) — a genuine ownership-documentation gap, not an access gap. |
| Why needed | Route access requests and unresolved questions to the right team |
| Owner | DevEx/Discover team — `#tech_it`, `#discover-knowledge-base` |
| Export/query method | Repo read on `knowledge-base` (done) and per-repo `.github/repo_owners.json` (done) |
| Minimum permission | n/a |
| Safe synthetic substitute | n/a |
| Fidelity lost | fts's ownership gap remains real regardless of access — no file exists to read |
| Status | **RESOLVED-BY-REPO** |

### J2. RKG (Razorpay Knowledge Graph) cross-check

| Field | Value |
|---|---|
| Internal system | `knowledge-base/knowledge/domains/payouts/` (`razorpay/knowledge-base` — now cloned) |
| Specific object/key | `bundle.yaml` (`bundle.payouts`, confidence `medium`, `last_verified: 2026-08-12`), `registry/{nodes,edges}.yaml` — **143 nodes, 232 edges** (7 service, 35 failure_mode, 27 flow, 25 entity, 23 invariant, 12 runbook, 7 decision, 5 contract) |
| Why needed | Independent cross-check of ownership/invariants/flows against this audit's own `PAYOUTS_SERVICE_GRAPH.json` |
| Owner | Discover platform team (Nikhilesh Chamarthi et al.) — `#discover-knowledge-base` |
| Export/query method | CLI `rkg` (query/viz/doctor) or direct YAML read — repo already cloned, no external export needed |
| Minimum permission | n/a |
| Safe synthetic substitute | n/a |
| Fidelity lost | None — cross-checked, no contradictions found; several complementary gaps identified in both directions (e.g. RKG has an `fts DEPENDS_ON x-account-statements` edge missing from our graph; our graph has `virtual-account → x-balances` and `fts calls payouts` edges missing from RKG) |
| Status | **RESOLVED-BY-REPO** |

---

## K. SDK flags that do NOT prevent egress

These are config gaps of a different kind: not missing *values*, but shared-library flags that look like a local/mock switch and silently do not behave as one. Confirmed empirically in the buildability spike (`findings/28_build_spike.md`) across payouts, ledger, fts, cfa, x-balances.

| Flag / mechanism | Package | What it actually does | Confirmed real egress caught | Arena mitigation |
|---|---|---|---|---|
| `config.Config.Mock` (`Mock=true`) | `goutils/dcs` (every version encountered: v1.5.2, v1.7.1, v1.7.3) | Field is stored but **never read** at the `dcs.New()`/`Client.New()` client-construction step — the client performs a real `POST {real-host}/v1/auth/login` unconditionally, regardless of `Mock`. The SDK's only override path (`ServerURL`) is unreachable for services with exactly one `Mode` (all three affected services). | cfa → `POST https://dcs-live.dev.razorpay.in/v1/auth/login` (live `403`); x-balances → same host (live `404`) | Stub at the login endpoint: override the SDK's exported `SetContextUrl()` before calling `dcs.New()`, pointed at a local server implementing `POST /v1/auth/login → {"access_token": "<any string>"}`. (A weaker fallback — setting an invalid `dcs.Env` so `GetEnv()` fails locally before dialing — avoids the call but does not complete boot for services with a fatal, unconditional DCS dependency, e.g. x-balances `cmd/server`.) |
| Plain `"sqs"` queue driver | `goutils/worker` (v2 and v3) | The `Endpoint` config field is **silently ignored** by the plain `"sqs"` dialect — it always dials a real regional AWS endpoint regardless of what `Endpoint` is set to. Only the separate `"sqs_local"` dialect (`sqs.NewWithEndpoint`) honors `Endpoint`. | cfa's worker made repeated real `ReceiveMessage` calls to `sqs.us-east-1.amazonaws.com` (403/AccessDenied each time) before being caught | Set `QUEUE_DRIVER=sqs_local` (driver id `sqs.LocalDialect`) with `Endpoint` pointed at a local SQS emulator/LocalStack — or use the first-class `"inmemory"` driver where available (x-balances' approach, sidesteps the whole bug class). |
| `docker network create --internal` | n/a (infra-level, not an SDK flag) | Blocks egress only for **containers placed on that network** — does nothing for a Go binary run directly on the host, which is how every service ran in the spike. | x-balances' first `cmd/server` boot made a real TLS connection to `dcs-live.dev.razorpay.in` (live `404`) despite the `--internal` network existing | Process-local proxy blackhole as defense-in-depth, set **before** any boot attempt: `HTTP_PROXY=http://127.0.0.1:1`, `HTTPS_PROXY=http://127.0.0.1:1`, `NO_PROXY=127.0.0.1,localhost,127.0.0.0/8,::1`. Go's default `http.Transport` honors these; any call that missed a config rewrite fails closed (`connection refused`) instead of reaching a real host. **Gotcha**: a service's own grpc-gateway self-dial on a bare `:PORT` (no host) isn't excluded by `NO_PROXY`'s host-matching and gets blackholed too — fix with an explicit `127.0.0.1:PORT`, never by dropping the proxy. |

**Takeaway** (finding 28_build_spike.md's own framing): a `Mock=true`-looking config flag does not reliably prevent real outbound calls from shared internal SDKs. Static config review (grepping for `Mock`/`razorpay.in` strings) is not sufficient on its own — live boot-log inspection under a defense-in-depth network block is required, and should be standard practice for any future service onboarded into the arena.

---

## Summary

28 non-repository line items were assessed against the expanded access (23 from the original DCS/Splitz/Kong/monolith/event/recon/testing pass, plus 5 added on reconciliation: UPS/payments-upi contract, SQS/SNS+IAM queue inventory, CDC/outbox topology, service→team→on-call mapping, and the RKG cross-check). **13 are `RESOLVED-BY-REPO`** (Kong rollout knobs, passport claim schema, authz policy CSVs, alert thresholds, ITF-vs-mozart-mock fixture choice, WFS config templates, Batch batch-type JSONs, monolith route/permission logic, DynamoDB secret *names*, SQS/SNS queue *names*, the UPS contract, service-ownership mapping, and the RKG cross-check) — these no longer require any external export. **14 have a `SYNTHETIC-OK`** protocol-faithful substitute the arena should actually run (DCS stub, Splitz stub, Governor/CC in-repo Mock, Shield stub, `mozart -mock`, Stork capture service, ASV mock, passport self-signing, recon Env-4 seeding, MySQL-for-TiDB, secret-name-driven `.env` generation) — several of these are paired with a real-value export that remains open for higher fidelity. **9 are `EXPORT-NEEDED`** and require a genuine external ask: real DCS merchant values, real Splitz experiment definitions, FastCron cadence strings, Kafka topic/ACL inventory (still open per architecture-delta #29), SQS/SNS IAM grants, CDC/outbox topic inventory, Coralogix/HyperDx trace access, recon's live `workflow_configs` rows, and (optionally) real Governor/Charge-Collections rule content. **7 are `BLOCKED`**, needing either owning-team confirmation that cannot be resolved from any repo (DCS's canonical export-flag pair, Consul org-scoping for authz, whether Splitz's 8 experiment definitions can ever be read outside its DB) or repo access still denied (ASV/account-service, e2e-test-orchestrator/qa-tools). Separately, three shared-library flags (`goutils/dcs` `Mock`, `goutils/worker`'s plain `sqs` driver, and `docker --internal` networking) look like local/mock switches but do not prevent egress — all three have confirmed, empirically-tested mitigations (login-endpoint stub, `sqs_local` dialect, proxy blackhole) documented in §K.

## L. Addendum after the arena bring-up (2026-09-04, late)

Resolved from repositories while making every core process healthy (details: `raw-findings/31_env2_bringup_notes.md`):

| Item | Was | Now known | Arena handling |
|---|---|---|---|
| Payouts worker process model | assumed one `workers` binary serving all jobs | one process per job; job chosen by `PAYOUTS_WORKER_NAME`; 25 worker Deployments in `kube-manifests/templates/payouts/templates/` | 14 `payouts-worker-<job>` services (payout path); the other 11 can be added by copying a block |
| Payouts Kafka consumer task | unknown how `fts_status_updates` vs retry is chosen | `PAYOUTS_CONSUMER_TASK_NAME` env per Deployment; `[kafka_consumers]/[task]/[topics]` live only in `config/devstack.toml` | two consumer services, sections copied with arena brokers |
| SQS driver | `[queue] driver="sqs"` with LocalStack prefix assumed to work | plain `sqs` dialect ignores `Endpoint` → real AWS; `sqs_local` honours it | `sqs_local` + `endpoint` + synthetic AWS env |
| Queue/topic provisioning | none | names enumerated from generated configs (32 queues, 1 SNS topic) | LocalStack ready.d hook |
| viper env overrides | assumed global | only keys present in TOML are overridable (`AutomaticEnv` semantics) | `[worker]` block added to arena template |

| Inter-service credentials | assumed one generated secret per pair was enough | every callee's inbound auth section (payouts `[auth.*]`, ledger `[auth.*]`, cfa `[Server.Auth.Payouts]`, x-balances `[Server.Auth.PS]`, fts `[users.*]` env placeholders) had to be bound to the same secret as the caller; fts resolves users by reflection on the route's auth key | all inbound sections now reference the pair's `SECRET.auth_*` token (finding 31) |
| payouts → FTS client | assumed in `default.toml` | `[fts]` exists only in env-specific TOMLs (`devstack.toml`); host + `env\|PAYOUTS_FTS_AUTH_*` | block copied into the arena template with `fts-web` host |
| FTS payout status webhook | assumed FTS → payouts-api direct | devstack targets the **monolith** `/v1/update_fts_fund_transfer` with the monolith identity; monolith relays to PS `update_payouts_with_fts` | arena: `monolith-stub` relay (`PS_RELAY_MODE=relay`) |
| Schema drift | assumed repo migrations reproduce the production schema | payouts `payout_details.beneficiary_bank_code` is written by code but created by no migration (out-of-band DDL in prod); likely not the only case | `seeds/schema-patches/payouts.sql` (idempotent) after migrations; type inferred |
| fts-web bind address | — | `LISTEN_IP = 127.0.0.1` by default (nginx in front in prod) | `0.0.0.0` in the arena |

Still not resolvable from repositories: production values of `PAYOUTS_WORKER_MAXCONCURRENCY` per job, Kafka topic partition counts, the FastCron cadences (see §A/§E), and the real per-environment usernames behind `USERS_*`/`PAYOUTS_FTS_AUTH_*` (arena uses `payouts`/`rzp_live`).
