# 08 — Downstream Lifecycle & Admin (Account Statements, Bank Sync, Accounting, Analytics, Admin Dashboard)

Scope: lane = downstream of payout execution — statement reconciliation, accounting sync, analytics consumption,
and admin actions capable of mutating payout/transfer state. Read-only investigation, shallow clones as listed
in the task. Commit hashes as given by the task (not independently re-verified beyond `git log -1` sanity where noted).

---

## 1. Per-repo summary

| Repo | Type / Stack | Processes (cmd/) | Deploy | CODEOWNERS | Relevance classification |
|---|---|---|---|---|---|
| **x-account-statements** (XAS) | Go 1.24, Foundation gRPC+HTTP-gateway, MySQL (GORM), SQS (12 queues) + Kafka (1-2 CDC topics), Elasticsearch | `cmd/server` (gRPC :8080/HTTP :8081/internal :8082), `cmd/worker`, `cmd/migration` | Kubernetes, DevSpace for local | `* @razorpay/xas-recon-devs` (`.github/CODEOWNERS`; a second line scopes `.github/CODEOWNERS` itself to 3 individuals) | **Mutates a narrow slice of payout-adjacent state**: writes `EnrichmentUpdate`/`StatementDualWrite` calls to the API monolith that link a bank-statement row to a payout/reversal source event (entity_id/entity_type), and owns/mutates its own `account_statements`/`accounts` tables. Does **not** appear to change a payout's own status field (pending/processing/processed/reversed) — it observes payout/reversal events and annotates statement rows. |
| **x-bank-statement-sync** | Empty/stub repo — only `README.md` + CI workflow YAML, no source code | — | — | none found | **Unrelated / not yet built.** README says "Bank Transactions Sync to Accounting tools based on Accounting Integrations for RX-Neobanking Unit" but there is no implementation to inspect. |
| **accounting-integrations** | Go 1.24, custom Provider/Container DI, MySQL (GORM v1), Redis, Kafka (goutils/worker v2), Uber Cadence workflows, Viper/TOML | `cmd/api`, `cmd/worker`, `cmd/cadence/worker`, `cmd/migration`, `cmd/task`, `cmd/custom_fields_migration` | Kubernetes (implied by devspace/deployments dir) | `* @razorpay/mandatoryxreviewers` | **Mutates a specific field on vendor-payments (accounting sync status), not payout execution state.** Consumes payout status via Kafka from **vendor-payments**, not from the core `payouts` service directly, and writes back only `sync_status` for bank transactions via a Twirp RPC. |
| **self-serve-analytics** | Data platform: Spark/Iceberg (EMR) → dbt → ClickHouse serving layer, DataHub metadata catalog, MCP servers (ClickHouse/DataHub/Trino) for NL→SQL | N/A (no service processes; Airflow DAGs + dbt models) | Airflow-orchestrated batch pipelines; DataHub ingestion via GitHub Actions | none inspected (internal analytics repo) | **Observes only.** Consumes payout data via realtime Hudi/CDC-derived lake tables (`realtime_hudi_api.payouts`, `realtime_scrooge_live.payouts`, `realtime_fts_live.transfers`/`attempts`/`fund_accounts`), not live DB/Kafka connections to the payouts service itself. No write path back into payouts found. |
| **admin-dashboard** | JS/React internal admin UI (Blade components, styled-components), Node build via `admin.js`/`base.config.js` | Browser SPA calling `/admin/api/<mode>/...` on the **API monolith** | Standard FE deploy (build.sh/Dockerfile.e2e for e2e) | Per-directory CODEOWNERS only for a few subtrees (reports, SAV, cases, InstrumentRequests, DCS, rekyc, banking_accounts); no blanket payouts owner found | **Mutates payout/transfer state directly** via numerous permissioned "admin actions" that POST/PATCH to API-monolith admin routes (force status update, reject, cancel/clear pending, retry, bulk manual actions, fund-transfer initiate/update, fee recovery, manual statement-to-payout linking). |
| **data-mcp** | Empty template repo (`# template — Used for creating new repo's`) | — | — | — | **Unrelated.** No payouts-relevant content; boilerplate scaffold only. |
| **memoir** | Go — generic eBPF+TLS-MITM traffic-recording sidecar (records HTTP/HTTPS/gRPC as YAML mocks) | `cmd/` sidecar binary | Kubernetes sidecar (`k8s/`) | not inspected | **Unrelated to payouts domain specifically** — generic platform/observability tooling usable by any service including payouts, but no payouts-specific code in the repo itself. |
| **tejas** | Empty template repo (`# template — Used for creating new repo's`) | — | — | — | **Unrelated.** Boilerplate scaffold only. |

---

## 2. Findings (Claim | Repo | File | Evidence | Confidence | Capability)

| # | Claim | Repo | File path + symbol | Evidence excerpt | Confidence | Capability |
|---|---|---|---|---|---|---|
| 1 | XAS fetches statements from 5 partner banks via Mozart gateway, stores in MySQL, links to payout/reversal source events by UTR/GRN/CMS ref | x-account-statements | `AGENTS.md` (repo root, "Key Flows") | "Cron triggers fetch -> bank adapter downloads statements via Mozart -> dedup + save to MySQL -> enrich by matching to source events -> dual-write to BAS" | High | observe / transform |
| 2 | XAS enrichment matches a bank statement row to a payout source event and, if none found, marks the statement `external`; no payout status field is touched | x-account-statements | `internal/account_statements/enrich/service/service.go` — `EnrichWithStatement`, `markAsExternal` | "if len(sourceEvents) < 1 { // Mark as external if event not found; return s.markAsExternal(...) }" | High | observe |
| 3 | XAS's API-monolith client only sends `StatementDualWrite`, `EnrichmentUpdate`, `SourceEventFetch`, `AccountsDualWrite` — no payout-status-mutating RPC exists in this client | x-account-statements | `internal/gateway/api/interfaces.go` — `Service` interface | `StatementDualWrite(...)`, `EnrichmentUpdate(...)`, `SourceEventFetch(...)`, `AccountsDualWrite(...)` | High | transform/persist (statement linkage only) |
| 4 | XAS `EnrichmentUpdate` payload carries `bas_id`, `entity_id`, `entity_type`, `utr`/`grn`/`cms_ref_no` — i.e. it patches the *statement*'s link to an entity, not the entity's own status | x-account-statements | `internal/gateway/api/params.go` — `EnrichmentUpdateRequest` | `BasID`, `EntityID`, `EntityType`, `UTR`, `GRN`, `CMSRefNo` fields, no `status` field | High | persist |
| 5 | XAS consumes payout/reversal events off SQS using the shared `goutils/telemetry/rzpconv/payouts` converter, confirming payouts→XAS is one-way/event-driven | x-account-statements | `internal/job/source_processing/source_processing.go` | `import "github.com/razorpay/goutils/telemetry/rzpconv/payouts"` | Medium-High | observe |
| 6 | XAS exposes `/v1/statement/delete`, `/v1/statement/dual_write`, `/v1/statement/enrich_statements`, `/v2/account_statements`, `/v1/accounts`, `/v1/accounts/update`, `/v1/banking_account_statement/fetch/{initiate,manual}`, `/v1/source_event/fetch`, `/v1/trigger_migration` — all scoped to XAS's own entities | x-account-statements | `rpc/x/x-account-statements/*/v1/*.swagger.json` | paths extracted via swagger JSON (see §3 Inbound APIs) | High | observe/persist (own domain only) |
| 7 | XAS's CDC/Kafka topic is its own MySQL binlog (`account_statements`, `accounts` tables) shipped to the API monolith for dual-write, not a topic it produces for other consumers to react to for payout mutation | x-account-statements | `config/prod.toml:83-90`; `internal/cdc_events/table_names.go` | `Topic = "mysql_cdc_events_prod_rx_account_statements_account_statements"`; `AccountStatementsTable`, `AccountsTable` consts | High | observe/persist (internal sync) |
| 8 | payouts service has an outbound client dedicated to XAS (`xas_client.go`) and an `accountStatement` domain package, confirming a live bidirectional integration between payouts and XAS | payouts (cross-check) | `internal/provider/xas_client.go`; `internal/app/accountStatement/core.go` | `const XasClient = "xas"`; `cfg := value.(*config.Config).XasClient` | High | observe (from payouts' perspective, it queries XAS) |
| 9 | accounting-integrations consumes payout status from **vendor-payments** (not the `payouts` core service) via a dedicated Kafka topic | accounting-integrations | `config/prod.toml:196` | `Topic = "prod.x.vendor-payments.accounting-payouts.status-update"` | High | observe |
| 10 | accounting-integrations can write back a `sync_status` field onto vendor-payments bank transactions via a Twirp RPC — a real mutation, but scoped to accounting-sync bookkeeping, not payout execution status | accounting-integrations | `internal/service/vendorpayment/core.go` — `UpdateBankTransactions` | `UpdateBankTransactions = "twirp/accountingpayouts.Accountingpayouts/UpdateBankTransactions"`; body `{merchant_id, transaction_ids, sync_status}` | High | persist |
| 11 | self-serve-analytics ingests payout data exclusively from realtime Hudi/CDC-lake tables, not from a live DB connection or Kafka topic owned by `payouts` | self-serve-analytics | `datamart/non_payments_raw_table_inventory.md` | `realtime_hudi_api.payouts` — "Primary payout data — id, mode, channel, status, amount..."; `realtime_scrooge_live.payouts`; `realtime_fts_live.transfers` (source_type='payout') | High | observe |
| 12 | admin-dashboard "Force Update Payout Status" (single) issues a raw PATCH that can set an arbitrary payout status string, gated only by permission `payout_status_update_manually` | admin-dashboard | `js/admin/adminActions/actionModals/ForceUpdatePayoutStatus.js` | `adminPatch({ url: `live/payouts/${payout_id}/manual/status`, data:{status, fts_fund_account_id, fts_account_type} })` | High | **mutate/reverse-capable** |
| 13 | admin-dashboard "Force Update Payout Status Bulk" does the same across a comma-separated batch | admin-dashboard | `js/admin/adminActions/actionModals/ForceUpdatePayoutStatusBulk.js` | `adminPatch({ url: 'live/payouts/manual/status_update/batch', data:{payout_ids, status, ...} })` | High | **mutate/reverse-capable (bulk)** |
| 14 | admin-dashboard "Reject Payout" and "Clear Pending Payouts" (bulk force-reject) directly reject/cancel payouts | admin-dashboard | `RejectPayout.js`, `ClearPendingPayouts.js` | `adminPost({url:'live/admin/payouts/${payout_id}/reject'})`; `adminPost({url:'live/admin/payouts/cancel', data:{payout_ids, force_reject:true}})` | High | reject/reverse |
| 15 | admin-dashboard "Retry Payouts" resubmits payouts for processing | admin-dashboard | `RetryPayout.js` | `adminPost({url: '${mode}/payouts/retry', data:{ids}})` | High | route/retry |
| 16 | admin-dashboard "Payouts Manual Actions" panel exposes an even broader set of raw manual actions incl. moving payouts processed→processing, dual-write, manual workflow approve/reject, forcing bank-transfer processing, and raw **Redis get/set** — single permission `payouts_manual_action` gates the whole panel | admin-dashboard | `js/admin/payouts/PayoutsManualActions.js`; `js/admin/payouts/constants.js` | `static permission = 'payouts_manual_action'`; `adminPost({url:'live/payouts/manual_action', ...})`; `MANUAL_ACTIONS = {ACTION_MOVE_PROCESSED:'processed_to_processing', ACTION_DUAL_WRITE, ACTION_MANUAL_APPROVE:'approve_workflow_payouts', ACTION_MANUAL_REJECT:'reject_workflow_payouts', ACTION_PROCESS_BANK_TRANSFER, ACTION_REDIS_GET, ACTION_REDIS_SET}` | High | **mutate/reverse/authorize/route (very broad — highest-risk surface found)** |
| 17 | admin-dashboard can manually (re)link a bank statement to a payout/source-event — the human-in-the-loop counterpart to XAS's automatic enrichment | admin-dashboard | `js/admin/adminActions/actionModals/RblAccountStatementManualLinking.js` | `permission = 'manually_link_rbl_account_statement'`; `url: 'live/banking_account_statement/source/update'` (+ a `/validate` variant) | High | transform (statement↔payout linkage) |
| 18 | admin-dashboard "Fund Transfer Update/Initiate" mutates FTS fund-transfer-attempt state directly, gated by `settlement_bulk_update` | admin-dashboard | `FundTransferUpdate.js`, `FundTransferInitiate.js` | `url:'live/fund_transfer_attempts'`; `url:'${mode}/fund_transfer_attempts/initiate_action/${channel}'` | High | mutate (FTS layer) |
| 19 | admin-dashboard "Process CA Fee Recovery" / "Fee Recovery Retry" trigger payout-fee-recovery flows, gated by `process_fee_recovery` | admin-dashboard | `ProcessFeeRecovery.js`, `FeeRecoveryRetry.js` | `url:'live/payouts/fee_recovery'`; `url:'live/payouts/fee_recovery_retry/manual'` | High | mutate/route |
| 20 | admin-dashboard "Payout Links Admin" hits a separate payout-links admin surface, gated by `tax_payment_admin_auth_execute` (permission name looks mismatched/legacy vs. feature) | admin-dashboard | `PayoutLinkAdminApi.js` | `url:'live/payout-links/admin'`; `permission = 'tax_payment_admin_auth_execute'` | Medium (permission-name/feature mismatch worth flagging) | mutate |
| 21 | Admin auth/permission model is centrally managed via API-monolith `live/permissions*`/`live/orgs`/roles endpoints; each admin-action React component declares a static `.permission` string checked client-side via `<ShowWhen permission=…>` before the corresponding API call is even attempted (server-side enforcement presumed but not verified in this repo) | admin-dashboard | `js/admin/permissions/List.js`, `js/admin/permissions/Entity.js` | `adminFetch('live/permissions-multiple')`; `adminFetch('live/permissions/{id}/roles')`; `<ShowWhen permission="edit_permission">` | Medium (client-side gating confirmed; server-side enforcement not verified from this repo alone) | authorize (RBAC) |
| 22 | payouts service itself owns the server-side admin routes admin-dashboard calls (manual_action, reject, bene_bank_status_update) | payouts (cross-check) | `internal/routing/router/payout_internal_routes.go` | routes: `"/manual_action"`, `"/payouts_internal/:payout_id/reject"`, `"/bene_bank_status_update"`; comment: "force-release endpoint is a rejected footgun, and the cron reconcile is the [preferred path]" | High | confirms admin-dashboard → API-monolith → payouts service admin surface exists and is treated internally as risky ("footgun") |
| 23 | accounting-integrations receives vendor-payment (not core-payout) data over Kafka from **vendor-payments**, with additional non-payout event topics (advance events, file-handler, item updates, invoice fetch) | accounting-integrations | `config/prod.toml:196-384` | `Topic = "prod.x.vendor-payments.accounting-payouts.status-update"`, `"prod-vendor-advance-event"`, `"prod-file-handler-event"`, `"prod-items-update-event"`, `"prod-vendor-entity-updates-event"`, `"prod-initiate-report"`, `"prod-zoho-invoice-fetch"` | High | observe |

---

## 3. Event/topic table

| Topic / Queue | Direction | Producer → Consumer | Repo | Notes |
|---|---|---|---|---|
| `mysql_cdc_events_{env}_rx_account_statements_account_statements` (Kafka) | XAS's own MySQL binlog → itself/dual-write consumer | XAS DB → XAS worker → API monolith (dual-write) | x-account-statements | `config/prod.toml:83-90` |
| 12x SQS job queues (`pkgworker.Jobs.*`) | internal async jobs | XAS server → XAS worker | x-account-statements | see §4 cron/worker table |
| SQS payout/reversal source events (consumed by `source_processing` job, using `goutils/telemetry/rzpconv/payouts`) | payouts service → XAS | payouts → SQS → XAS | x-account-statements | inbound-only for XAS |
| `prod.x.vendor-payments.accounting-payouts.status-update` (Kafka) | vendor-payments → accounting-integrations | vendor-payments → accounting-integrations | accounting-integrations | payout status observed, not sourced from core `payouts` |
| `prod-vendor-advance-event`, `prod-file-handler-event`, `prod-items-update-event`, `prod-vendor-entity-updates-event`, `prod-initiate-report`, `prod-zoho-invoice-fetch` (Kafka) | vendor-payments/file-handler/item systems → accounting-integrations | various → accounting-integrations | accounting-integrations | not payout-specific |
| Realtime Hudi/Iceberg lake tables: `realtime_hudi_api.payouts`, `realtime_scrooge_live.payouts`, `realtime_fts_live.transfers`/`attempts`/`fund_accounts` | payouts/scrooge/FTS DB CDC → data lake → self-serve-analytics | (upstream CDC not owned by this repo) → Spark/dbt → ClickHouse | self-serve-analytics | pure read/reporting path |

## 4. Inbound/outbound client tables

**XAS inbound APIs** (callers: API Monolith, FastCron, Admin Service per README architecture diagram; admin-dashboard confirmed as a caller via `banking_account_statement/source/update`):

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/account_statements`, `/v2/account_statements` | query statements |
| GET | `/v1/account_statements/fetch_multiple_by_reference_numbers` | multi-ref lookup |
| POST | `/v1/statement/delete` | delete a statement (XAS-owned data) |
| POST | `/v1/statement/dual_write` | dual-write statement to/from monolith |
| POST | `/v1/statement/enrich_statements` | trigger enrichment |
| GET/POST | `/v1/accounts`, `/v1/accounts/update` | manage tracked accounts |
| POST | `/v1/banking_account_statement/fetch/initiate`, `/fetch/manual` | trigger bank statement fetch |
| POST | `/v1/source_event/fetch` | on-demand fetch of payout/reversal source events (limit 50/call, `internal/api_controller/source_event/server/server.go`) |
| POST | `/v1/trigger_migration` | merchant migration tool trigger |

**XAS outbound clients:**

| Client | Package | Target | Purpose |
|---|---|---|---|
| Mozart | `internal/gateway/mozart` | Mozart bank gateway | Fetch statements from ICICI/AXIS/RBL/IDFC/YesBank |
| Banking Accounts Service (BAS) | `internal/gateway/banking_accounts` | BAS | `FetchBankingCreds` (credentials only) |
| API monolith | `internal/gateway/api` | API monolith | `StatementDualWrite`, `EnrichmentUpdate`, `SourceEventFetch`, `AccountsDualWrite` |

**accounting-integrations outbound client:**

| Client | Package | Target | Purpose |
|---|---|---|---|
| vpclient | `internal/service/vendorpayment/vpclient` | vendor-payments service | `GetTDSCategories`, `GetBankTransactionSyncStatus`, `UpdateBankTransactions` (writes `sync_status`), `BulkCreateItems/UpdateItems/DeactivateItems`, `ListItems`, `GetVendorPaymentInvoice` |

## 5. Cron/worker table (XAS)

| Job (queue const, `pkgworker.Jobs.*`) | File | Purpose |
|---|---|---|
| `AccountStatementDualWrite` | `internal/job/account_statement_cdc/account_statement_cdc.go` | CDC → dual-write statement rows to monolith |
| `AccountsDualWrite` | `internal/job/account_cdc/account_cdc.go` | CDC → dual-write account rows to monolith |
| `APIToXasMerchantMigration` | `internal/job/api_to_xas_merchant_migration/migration_worker.go` | monolith→XAS merchant migration |
| `FetchAxisStatementsForAccount` / `FetchIDFCStatementsForAccount` / `FetchICICIStatementsForAccount` / `FetchSliceStatementsForAccount` / `FetchYesBankStatementsForAccount` / `FetchStatementsForAccount` | `internal/job/fetch_*` | per-bank statement fetch |
| `NewMerchantOnboarding` | `internal/job/new_merchant_onboarding/` | account onboarding |
| `SaveStatementsPushForEnrichment` | `internal/job/save_statements_push_for_enrichment/` | queue statements for enrichment |
| `StatementEnrichment` | `internal/job/statement_enrichment/` | run enrichment matching |
| `SourceProcessing` | `internal/job/source_processing/source_processing.go` | process inbound payout/reversal source events (uses `goutils/telemetry/rzpconv/payouts`) |

## 6. Admin action table (admin-dashboard → payout/transfer-relevant)

| Action | UI file | API method + path | Backend (inferred) | Permission |
|---|---|---|---|---|
| Force Update Payout Status | `js/admin/adminActions/actionModals/ForceUpdatePayoutStatus.js` | PATCH `live/payouts/{payout_id}/manual/status` | API monolith → payouts service | `payout_status_update_manually` |
| Force Update Payout Status (Bulk) | `.../ForceUpdatePayoutStatusBulk.js` | PATCH `live/payouts/manual/status_update/batch` | API monolith → payouts service | `payout_status_update_manually` |
| Reject Payout | `.../RejectPayout.js` | POST `live/admin/payouts/{payout_id}/reject` | API monolith → payouts service (`payouts_internal/:payout_id/reject` server route confirmed in `payouts` repo) | `reject_payout` |
| Clear Pending Payouts (bulk force-reject/cancel) | `.../ClearPendingPayouts.js` | POST `live/admin/payouts/cancel` (`force_reject:true`) | API monolith → payouts service | `reject_payout_bulk` |
| Retry Payouts | `.../RetryPayout.js` | POST `{mode}/payouts/retry` | API monolith → payouts service | `retry_settlement` |
| Payouts Manual Actions (move processed→processing, dual-write, workflow approve/reject, force bank-transfer, Redis get/set) | `js/admin/payouts/PayoutsManualActions.js`, `js/admin/payouts/constants.js` | POST `live/payouts/manual_action` | API monolith → payouts service | `payouts_manual_action` |
| RBL Account Statement Manual Linking | `.../RblAccountStatementManualLinking.js` | POST `live/banking_account_statement/source/update` (+ `/validate`) | API monolith → XAS enrichment path | `manually_link_rbl_account_statement` |
| Fund Transfer Update | `.../FundTransferUpdate.js` | (method not confirmed) `live/fund_transfer_attempts` | API monolith → FTS | `settlement_bulk_update` |
| Fund Transfer Initiate | `.../FundTransferInitiate.js` | POST `{mode}/fund_transfer_attempts/initiate_action/{channel}` | API monolith → FTS | `settlement_bulk_update` |
| Process CA Fee Recovery | `.../ProcessFeeRecovery.js` | POST `live/payouts/fee_recovery` | API monolith → payouts service | `process_fee_recovery` |
| Fee Recovery Retry | `.../FeeRecoveryRetry.js` | POST `live/payouts/fee_recovery_retry/manual` | API monolith → payouts service | `process_fee_recovery` |
| Payout Links Admin | `.../PayoutLinkAdminApi.js` | POST `live/payout-links/admin` | API monolith → payout-links service | `tax_payment_admin_auth_execute` (name looks mismatched to feature — flag for review) |
| Add Free Payout Count / Update Free Payout / Free Payout Migration | `.../AddFreePayoutCount.js`, `merchants/entity/entityModals/UpdateFreePayout.js`, `.../FreePayoutMigration.js` | not read in detail | API monolith | not read in detail (listed for completeness, not deep-dived) |
| Payout File Conversion | `.../PayoutFileConvertion/*` | not read in detail | API monolith | not read in detail |

All admin routes are called relative to `live/` or `{mode}/`, i.e. against the **API monolith's admin surface** (`common/fetch.js` `adminPost/adminPatch`), which per the `payouts` repo cross-check (`internal/routing/router/payout_internal_routes.go`, `internal/controllers/adminClientController.go`) proxies through to internal payouts-service routes such as `/manual_action`, `/payouts_internal/:payout_id/reject`, `/bene_bank_status_update`. A code comment in that router file explicitly calls a related force-release endpoint "a rejected footgun," corroborating that this admin surface is recognized internally as high-risk.

---

## Unresolved questions

1. **Server-side authorization enforcement**: admin-dashboard only proves *client-side* permission gating (`ShowWhen permission=...`). Whether the API monolith / payouts service independently re-validates the permission string server-side (defense in depth) was not verified — would need the API monolith / payouts repo's admin-auth middleware.
2. **`FundTransferUpdate.js` HTTP method**: path (`live/fund_transfer_attempts`) was captured but the verb (`adminPost` vs `adminPut`/`adminPatch`) wasn't confirmed in the grep excerpt — worth a follow-up read of the full file.
3. **`tax_payment_admin_auth_execute` permission on Payout Links Admin**: naming suggests this permission may be shared/reused from a tax-payment feature rather than being payout-links-specific; worth confirming with the admin-dashboard owners whether this is intentional or legacy debt.
4. **x-bank-statement-sync**: repo is empty (README + CI workflow only) — cannot assess architecture; if it's meant to be "live" per its description, either the clone is stale/wrong branch or the service hasn't been built yet. Recommend re-checking with the owning team.
5. **XAS "delete statement" (`/v1/statement/delete`) blast radius**: confirmed it operates only on XAS's own `account_statements` table (no evidence of cascading payout mutation), but the handler internals (`internal/api_controller/account_statement/server/server.go`) were not fully read line-by-line — worth a deeper pass if statement deletion is a compliance/audit concern.
6. **accounting-integrations "Payouts" domain module** referenced in `CLAUDE.md`'s skill index (`modules/domain/payout/`) was not opened — may contain additional payout-adjacent logic beyond what `vendorpayment/core.go` shows.
7. **memoir usage on payouts/XAS pods**: not confirmed whether memoir is actually deployed as a sidecar on any payouts-lane service (its repo contains no payouts-specific config); if it is, it would pose a distinct read-capture-of-secrets risk (TLS MITM) worth noting for a security review, independent of this architecture task.
