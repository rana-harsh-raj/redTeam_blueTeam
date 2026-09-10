# Ledger Service — Payouts Architecture Findings

Repo: `razorpay/ledger` @ `471ff4d5321b6b99a7965882d18f572a1adf5194` (master)
Language: Go 1.23. RPC framework: Twirp. DB: PostgreSQL (via `spine`/gorm raw SQL). Queueing: SQS (internal async jobs) + Kafka (CDC ingestion for PG tenant, and a separate "makeshift" migration queue). Pub/sub: SNS (`journal-created` topic) via an outbox pattern.

## Repo Summary

Ledger is Razorpay's shared double-entry accounting/general-ledger service used by **multiple tenants/products**, primarily:
- **X (RazorpayX)** — banking/payouts product. Tenant constant `TenantX`.
- **PG (Payment Gateway / api-monolith)** — legacy MySQL/Postgres-backed accounting migrating into Ledger. Tenant constant `TenantPG`.

For **X/Payouts**, Ledger is the **primary, live system of record**: the Payouts service (and FTS — Fund Transfer Service) call Ledger's `Journal.Create` Twirp API **synchronously over HTTP** with per-client HTTP Basic Auth (`auth.payouts`, `auth.fts`, `auth.xbalances` in config), tagged with a `Ledger-Tenant: X` header. Merchant balance debit/credit happens **inside the same DB transaction** as journal + ledger-entry creation (synchronous authorization), so a payout can be rejected for insufficient balance at Journal.Create time.

For **PG**, Ledger runs in **"reverse-shadow" migration mode**: it is NOT yet the source of truth. It consumes CDC (Maxwell for MySQL-backed services, Debezium for Postgres-backed services) events off Kafka from many api-monolith sub-services (settlements, growth, UPS, PCP, NBS, route, affordability, emandate, CBImport, BTS, dispute-service, partnerships, APM, CPS, optimizer, offers) and shadow-writes journals; a web-pod cron retries via the same idempotency path. This is orthogonal to the X/Payouts flow and does not carry payout traffic.

Deployable processes (separate Dockerfiles under `build/docker/prod/`, one Go binary per `cmd/*`):
- `cmd/api` — Twirp HTTP API server (Account, AccountDetail, Journal, LedgerEntry, Dashboard, LedgerConfig APIs), boots via `internal/boot.InitApi`.
- `cmd/worker` — SQS-based async worker (balance_update, journal_create, ledger_entry_details_create, account_create jobs), `boot.InitWorker`.
- `cmd/worker_kafka` — Kafka worker for **PG reverse-shadow CDC ingestion** (Maxwell/Debezium journal creation) + outbox job registration, `boot.InitKafkaWorker`.
- `cmd/worker_makeshift_txn` — Kafka worker for the "makeshift" dual-write migration path (X's older `makeshift` MySQL ledger), `boot.InitMakeshiftTxnWorker`.
- `cmd/outbox_relay` — polls `outbox_jobs` table and publishes to Kafka/SNS (`boot.InitOutboxRelay`), tenant-aware DB selection (PG vs default).
- `cmd/scheduler` — cron/scheduled jobs (`internal/scheduled_jobs/*`).
- `cmd/migration` / `cmd/makeshift_migration` — goose DB migrations (`internal/database/rx_migrations`, `pg_migrations`, `internal/makeshift/database/makeshift_migrations`).
- `cmd/idempotency_partition_manager` / `cmd/idempotency_scheduler` — manage/rotate partitions of the `idempotency` table.
- `cmd/data_backfill`, `cmd/data_comparator`, `cmd/data_comparator_rx_da` — reconciliation/backfill/replay tooling.
- `cmd/merchant_offboard` — admin data-deletion tool.

No Helm/K8s manifests live in this repo (only `deployment/dev/docker-compose*.yml` for local dev, `devspace.yaml` referencing namespace `ledger`); real k8s config presumably lives in a separate infra/deploy repo.

CODEOWNERS: `* @razorpay/ledger-team` (repo-wide); `.github/CODEOWNERS` file itself owned by `@gurshid @ndayal1`.

There is also a large pre-existing AI-generated documentation set at `.agents/skills/repo-skill/` (SKILL.md + `modules/**`) that was used as a navigation aid; every specific claim used from it below was independently verified against source in this pass.

---

## Findings

| Claim | File path + symbol | Evidence excerpt | Confidence | Capability |
|---|---|---|---|---|
| Ledger runs two DB pools, `rx` (X) and `pg` (PG), plus a legacy `makeshift` MySQL pool, all independently enable/disable-able | `config/default.toml` `[db.rx]`, `[db.pg]`, `[db.makeshift]` | `db.rx.isEnabled=true`, `db.pg.isEnabled=true`, `db.makeshift.isEnabled=false` (per-env) | High | persist |
| Journal.Create is a synchronous Twirp HTTP endpoint; caller identified via Basic Auth client creds (`payouts`, `fts`, `xbalances`, etc.) | `internal/journal/server.go:38` `Server.Create`; `config/default.toml` `[auth.payouts]`,`[auth.fts]`,`[auth.xbalances]`; `internal/boot/hooks/auth.go:18` `Auth()` | `func (s *Server) Create(ctx, request *journalv1.JournalCreateRequest)` guarded by `TakeMutexLock` then `s.core.Create` | High | authorize/persist |
| Duplicate transactor events are rejected at app layer via a check-then-insert plus a distributed mutex on `(transactor_event, transactor_id)`; **no DB unique constraint** backs this | `internal/journal/validation.go` `ValidateJournalExist`; `internal/journal/core.go:2249` `TakeMutexLock`; `internal/database/rx_migrations/20201001011143_create_journal.go` (no unique index on transactor_id+transactor_type, only plain indexes) | `"a combination of transactor_event and transactor_id is unique... mutex key of the two"` (server.go comment); `ErrRecordAlreadyExistFailure` on existing journal | High | reject/persist |
| Merchant balance is a materialized column on `accounts.balance`, updated via an atomic conditional `UPDATE ... WHERE balance+increment >= min_balance` inside the same DB txn as journal/ledger-entry creation — this is the payout-initiated authorization/reservation mechanism | `internal/account/repo.go:421` `Repo.UpdateBalance`; `internal/database/rx_migrations/20201001011117_create_accounts.go` | `"UPDATE accounts SET balance = balance + ? ... AND (balance + ? >= ?) RETURNING balance"`; 0 rows affected ⇒ `ErrInsufficientBalanceFailure` | High | authorize/reserve/persist |
| Whether a given account's balance updates synchronously (in-txn) or asynchronously (via SQS) is decided per `(account_category, account_type, fund_account_type)`; for X, **MerchantBalanceAccount is NOT in the async list ⇒ it's always synchronous**; VendorPayable/Commission/Nodal/Current/GST accounts ARE async for X | `internal/journal/config.go:27` `updateBalanceModeAsyncConfigForX`; `internal/journal/core.go:1369` `updateBalanceInLedgerEntry` | `updateBalanceModeAsyncConfigForX[Liability][AccountPayable]` contains `Nodal, Current, M2P, AmazonPay, GST, Vendor` — **not** `Merchant` | High | authorize/persist |
| A Splitz experiment flag exists to move merchant-balance updates to async in future | `internal/journal/config.go:187` `evaluateMerchantBalanceAsyncUpdateExperiment`; `internal/common/constant.go:1192` `MerchantBalanceAsyncUpdateExperimentKey = "merchantBalanceAsyncUpdateExperiment"` | function present but I did not find its call-site wired into `updateBalanceInLedgerEntry` gating (may be used elsewhere/rollout-pending) | Medium | route |
| Payout transactor events and their debit/credit account templates are defined as Go seed data (not DB rows at rest until seeded), keyed by `Tenant + TransactorEventName` | `internal/journal/ledger_config/seed_data/shared_account_x.go` lines 16-51 (event-name consts), 73-108 (account discovery configs), 284-403 (payout_initiated/processed/reversed/failed `LedgerConfigCreateRequest`) | `XPayoutInitiatedConfigId = "XPayoutInitiatedV2"` etc.; `LedgerEntries: [...]` per event | High | route/transform |
| `payout_initiated`: debit MerchantBalance (amount+commission), credit VendorPayable (amount), credit CommissionIncome (commission-tax), credit OutputGST (tax) | `shared_account_x.go:284-311` | see block starting `xPayoutInitiated := &ledgerConfigv1.LedgerConfigCreateRequest{...}` | High | transform |
| `payout_processed`: debit VendorPayable (amount), credit FtsPayable (amount) — i.e. liability moves from "owed to vendor" to "owed to FTS/bank rail", no merchant-balance or fee movement | `shared_account_x.go:315-332` | `xPayoutProcessed` config block | High | transform |
| `payout_reversed`: debit FtsReceivable (amount), credit MerchantBalance (amount+commission), debit CommissionIncome (commission-tax), debit OutputGST (tax) — mirror-image reversal of `payout_initiated` | `shared_account_x.go:335-362` | `xPayoutReversed` config block | High | reverse/transform |
| `payout_failed`: identical entries to `payout_reversed` (debit VendorPayable, credit MerchantBalance+commission, debit Commission/GST) — modeled as its own event, same net effect as a reversal | `shared_account_x.go:365-392` | `xPayoutFailed` config block | High | reverse/transform |
| Reward payouts, inter-account payouts, VA-to-VA payouts, FAV (fund-account-validation?) each have their own parallel initiated/processed/reversed/failed config IDs | `shared_account_x.go:29-41` (`XRewardPayout*`, `XInterAccountPayout*`, `XVaToVaPayout*`, `XMYPayoutInitiated` for Malaysia) | const block | High | transform |
| Legacy/back-compat configs exist for pre-migration data (`*LegacyV2` IDs) | `shared_account_x.go:44-51` | `XPayoutInitiatedLegacyConfigId` etc. | Medium | transform |
| Account discovery is identifier + type based: MerchantBalance/VendorPayable/CommissionIncome/OutputGST accounts are keyed by `banking_account_id`; FTS accounts by `fts_fund_account_id` | `shared_account_x.go:52-108` | `MerchantIdentifier` uses `common.BankingAccountID`; `FtsIdentifier` uses `common.FTSFundAccountID` | High | route |
| Ledger publishes a `journal-created` SNS event for a whitelisted set of (tenant, transactor_event) pairs — for X this includes `payout_initiated`, `payout_failed`, `payout_reversed` but **not `payout_processed`** | `internal/journal/config.go:87-99` `journalCreatedPublishConfig`; `internal/journal/core.go:1573` `publishJournalCreatedEventToSNS` | map lists `PayoutInitiated, PayoutFailed, PayoutReversed, FavInitiated, FavFailed, FundLoadingProcessed, ...` under `TenantX` — no `PayoutProcessed` | High | observe |
| Journal create failures (validation or business-rule) are recorded to a `journal_rejected_events` table asynchronously, independent of the main create flow — acts as a soft DLQ for audit/replay | `internal/journal/server.go` calls to `CreateJournalRejectedEventsAsync`; `internal/journal/journal_rejected_events/model.go` | `go s.CreateJournalRejectedEventsAsync(childCtx, requestID, tenant, err..., request)` on validation and create failure | High | observe/reject |
| PG-tenant journal creation via Kafka is CDC-based (Maxwell for MySQL sources, Debezium for Postgres sources), one job type per client, all run under `ReverseShadowIntegrationMode` | `internal/job/job_kafka/base.go:80-107` `RegisterKafkaJobConfigs` | switch on `jobConfKafka.JournalCreatePGWorkerName` picks `JournalCreateMaxwellPGKafka` for API/Growth/UPS/PCP/NBS/Route/... vs `JournalCreateDebeziumPGKafka` for Settlements/CPS/Optimizer/Offers | High | route/persist |
| "reverse-shadow" and "shadow" are named integration-mode constants controlling whether/how a tenant's writes get pushed to `outbox_jobs` for downstream propagation | `internal/common/constant.go:1142-1145`; `internal/journal/config.go:104-108,165-183` `CheckIfOutboxPushEnabled`, `pushToOutboxJobsConfig` | `pushToOutboxJobsConfig[TenantPG][ReverseShadowIntegrationMode]` only — X tenant not present in this map (X does not run through outbox/kafka ingestion at all) | High | route |
| Comment in code explicitly documents PG's dual-path retry semantics for reverse shadow: first attempt via Kafka worker, retries via web-pod cron, both routed through the same idempotency-aware server (`JournalWorkerPG.Create`) | `internal/boot/handler.go` (`RegisterAppHandler`) comment above `job_kafka.RegisterJournalServerPG(journalServer)` | `"Initializing journal server in web because in case of PG reverse shadow, 1st request will come on kafka workers and subsequent retry request will come on web pods through cron."` | High | route/reverse(retry) |
| `idempotency` table is a separate, generic HTTP-idempotency-key cache (request/response/error hash keyed by client-supplied `idempotency_key`), range-partitioned by `created_at`, distinct from the transactor_id/transactor_event business-uniqueness check | `internal/database/rx_migrations/20210527121244_create_idempotency.go`; `cmd/idempotency_partition_manager`, `cmd/idempotency_scheduler` | `CREATE TABLE idempotency (... idempotency_key ..., request_hash, error_hash ...) PARTITION BY RANGE(created_at)` | High | observe/persist |
| Ledger enforces min-balance / negative-balance rules per account, with request-level overrides (`OverrideMinBalanceCheck`, `EnableNegativeLimitCheck`, `EnableReserveBalanceCheck`) evaluated per ledger-entry config | `internal/journal/core.go:1369-1409` `updateBalanceInLedgerEntry`; `internal/account/core.go:770-807` `UpdateBalance`, `getAccountMinBalance` | `"For PG tenant: take minimum of account's non-nil min_balance field, reserve account's balance field and merchant_balance_limit money param"` | High | authorize |
| A `FundAccountTypeMerchantReserveBalance` account type exists and is used as an input to min-balance computation for PG — a reserve/hold-style account, but this is a standing reserve concept, not a per-payout hold/reservation ledger entry | `internal/account/core.go:1019-1022,1670` | `common.FundAccountType: []string{common.FundAccountTypeMerchantReserveBalance}` | Medium | authorize |
| Split-account balances (for high-TPS merchants) are updated by a separate scheduled job rather than inline, and journal-created SNS publish is deferred/forced from that job | `internal/journal/core.go:1444` `pushBalanceAndLedgerEntryDetailsToSQS`, `internal/journal/core.go:2035` `UpdateBalancesForSplitAccounts`; `internal/scheduled_jobs/split_account_balance_update.go`, `split_account_balance_update_pg.go` | comment: `"For split account case, not publishing the journal to ledger SNS. This journal will be published by the balance update job..."` | Medium | persist/observe |
| Dashboard/admin API can bulk-delete merchant journal/account data by `(event, tenant)` — an admin mutation surface | `internal/dashboard/core.go:968` `DeleteMerchantsByEventAndTenant` | `func (c *Core) DeleteMerchantsByEventAndTenant(ctx, req *DeleteMerchantsByEventAndTenantRequest) (...)` | High | persist |
| "Makeshift" is a separate legacy MySQL-based mini-ledger for X predating full Ledger migration, with its own migrations, Kafka worker (`worker_makeshift_txn`), replay tooling, and dual-write jobs | `internal/makeshift/**`; `cmd/worker_makeshift_txn`; `internal/scheduled_jobs/makeshift_replay_cls_journal_transactions.go`, `makeshift_replay_cls_journal_bulk_transactions.go` | dir listing + filenames | Medium | route/reverse(replay) |
| Auth config lists Ledger's known API clients, indicating breadth of integrations (only `payouts`/`fts`/`xbalances` are payouts-relevant) | `config/default.toml` `[auth]` block, lines ~143-210+ | `auth.payouts`, `auth.fts`, `auth.xbalances`, `auth.settlements`, `auth.scrooge`, `auth.pgRouter`, `auth.growth`, `auth.cps`, `auth.ups`, `auth.pcp`, ... | High | authorize |

---

## Inbound API / Consumer Table

| Path | Mechanism | Auth | Tenant | Idempotency/Ordering | Notes |
|---|---|---|---|---|---|
| `Journal.Create` (Twirp, `rpc/ledger/journal/v1`) | Synchronous HTTP (Twirp RPC over JSON/protobuf) | HTTP Basic Auth per-client (`auth.payouts`,`auth.fts`,`auth.xbalances`, etc.), `Ledger-Tenant` header | X (Payouts) primary path; also PG (direct calls, not via CDC) | Distributed mutex on `(transactor_event, transactor_id)` + check-then-insert (`ValidateJournalExist`) | Main payout ingestion path. `internal/journal/server.go:38` |
| `Journal.CreateInBulk` | Twirp | same | both | same per-item | `internal/journal/server.go:467` |
| Kafka `worker_kafka` (Maxwell CDC) | Kafka consumer, single topic/group from env (`LEDGER_QUEUEKAFKA_KAFKA_TOPIC/GROUPID`) | n/a (internal CDC pipe) | PG only (API, Growth, UPS, PCP, NBS, Route, Affordability, Emandate, CBImport, BTS, DisputeService, Partnerships, APM) | Routed through same `JournalServerPG.Create` idempotency path | `internal/job/job_kafka/journal_create_maxwell_pg.go` |
| Kafka `worker_kafka` (Debezium CDC) | same queue, different job impl | n/a | PG only (Settlements, CPS, Optimizer, Offers) | same | `internal/job/job_kafka/journal_create_debezium_pg.go` |
| Kafka `worker_makeshift_txn` | Kafka consumer, `queueMakeshiftTxn` topic/group from env | n/a | X (legacy makeshift dual-write) | via makeshift job/mutex framework | `cmd/worker_makeshift_txn`, `internal/makeshift/jobs` |
| SQS `worker` (internal jobs) | SQS, jobs: `journal_create`, `account_create`, `balance_update`, `ledger_entry_details_create` | n/a (internal, enqueued by the API/worker itself) | both | dedupe via mutex + job framework | Not an external inbound path — used for the async-balance-update half of the flow (`EnqueueBalanceUpdateRequest`, `internal/journal/core.go:1231`) |

**Note:** No evidence found that Payouts service or FTS publish payout lifecycle events onto a Kafka topic that Ledger consumes directly for X. All X-side payout accounting is driven by synchronous Twirp `Journal.Create` calls (Payouts/FTS are the presumed callers based on `auth.payouts`/`auth.fts` client credentials — the actual calling service code lives outside this repo and was not verified here).

## Payout Transactor-Event / Accounting Table (X tenant, `internal/journal/ledger_config/seed_data/shared_account_x.go`)

| Transactor Event | Config ID | Debit | Credit |
|---|---|---|---|
| payout_initiated | `XPayoutInitiatedV2` | MerchantBalance (amount+commission) | VendorPayable (amount), CommissionIncome (commission−tax), OutputGST (tax) |
| payout_processed | `XPayoutProcessedV2` | VendorPayable (amount) | FtsPayable (amount) |
| payout_reversed | `XPayoutReversedV2` | FtsReceivable (amount), CommissionIncome (commission−tax), OutputGST (tax) | MerchantBalance (amount+commission) |
| payout_failed | `XPayoutFailedV2` | VendorPayable (amount), CommissionIncome (commission−tax), OutputGST (tax) | MerchantBalance (amount+commission) |
| fav_initiated / processed | `XFavInitiatedV2`/`XFavProcessedV2` | MerchantBalance (commission) | CommissionIncome, OutputGST | (fund-account-validation fee flow) |
| reward_payout_* , inter_account_payout_*, va_to_va_payout_* | parallel `XReward*`, `XInterAccountPayout*`, `XVaToVa*` configs | analogous patterns (not individually re-verified line-by-line) | — |

Account discovery keys: MerchantBalance/VendorPayable/CommissionIncome/OutputGST resolved by `banking_account_id`; FtsPayable/FtsReceivable resolved by `fts_fund_account_id` + a `$fts_account_type` dynamic fund-account-type placeholder (`shared_account_x.go:98-108`).

## Balance Exposure Table

| Surface | Path | Notes |
|---|---|---|
| `accounts.balance` column | `internal/database/rx_migrations/20201001011117_create_accounts.go` | Materialized running balance per account row; also `min_balance`, `negative_balance` |
| Synchronous update | `internal/account/repo.go:421 UpdateBalance` | Atomic conditional `UPDATE` in the same txn as journal create, for MerchantBalance (X) and most PG accounts |
| Async update | SQS `balance_update` job (`internal/job/job_sqs/balance_update.go`, `balance_update_pg.go`) fed by `Core.EnqueueBalanceUpdateRequest` (`internal/journal/core.go:1231`) | Used for VendorPayable/Commission/Nodal/etc. on X, and non-whitelisted accounts on PG |
| Dashboard read APIs | `internal/dashboard/core.go:1143 FetchMerchantBalanceAccountEntryByID`, `:1193 FetchMerchantBalanceAccountEntries`, `fetchMerchantAccountsResponse.GetMerchantBalance()` (line 1252) | Admin/dashboard balance visibility |
| Force-set (admin repair) | `internal/account/core.go:838 ForceUpdateBalance` / `internal/account/repo.go:458` | Bypasses min-balance check entirely — repair tool surface |
| Split-account balances | `internal/scheduled_jobs/split_account_balance_update.go`, `..._pg.go`, `internal/journal/core.go:2035 UpdateBalancesForSplitAccounts` | For high-TPS merchants, closing balance computed by a periodic job instead of inline |

No "shadow vs live" *balance* mode found beyond the `ReverseShadowIntegrationMode`/`ShadowIntegrationMode` journal-integration constants (`internal/common/constant.go:1142-1145`), which gate whether PG-tenant journals get pushed to the outbox for downstream propagation — not whether X balances are trustworthy (X is always live/primary in this repo).

## Idempotency / Ordering Handling

- Business-level dedupe: `(transactor_event, transactor_id)` uniqueness enforced by app-layer check (`ValidateJournalExist`, `internal/journal/validation.go`) guarded by a short-lived distributed mutex (`TakeMutexLock`/`ReleaseMutexLock`, `internal/journal/core.go:2249-2272`, backed by `goutils/mutex`, Redis-based per `pkg/redis` and `internal/provider/mutex`). **No DB unique index** backs this — a race outside the mutex TTL could theoretically double-insert (not verified against DB isolation level).
- HTTP-level idempotency-key cache: separate `idempotency` table (`idempotency_key`, `request_hash`, `error_hash`, partitioned by `created_at`), managed by `cmd/idempotency_partition_manager` and `cmd/idempotency_scheduler`.
- **Out-of-order handling (e.g., `payout_processed` before `payout_initiated`)**: no explicit sequence/state-machine gate was found in `journal/core.go` or `ledger_config` that requires a prior event to exist before processing a later one — each transactor event is validated independently for `(transactor_id, transactor_event)` non-existence, and account discovery/balance rules (e.g., VendorPayable debit in `payout_processed`) would simply be applied against whatever the account's current balance is; if `payout_processed` runs first, VendorPayable could go negative unless `min_balance` rules prevent it. **This is an inference, not directly observed** — no test or code comment confirming/denying explicit ordering enforcement was found; flagged as an unresolved question.
- Retries: worker/job framework has `MaxRetries: 1` per job (`internal/job/job_kafka/base.go` `NewBase`, `worker.Job{MaxRetries: 1, Timeout: 40*time.Second}`); PG reverse-shadow retries additionally happen via a web-pod cron hitting the same idempotent Create path (see handler.go comment above).
- DLQ/replay: `journal_rejected_events` table captures failed Create attempts (validation or business errors) for audit; `internal/scheduled_jobs/makeshift_replay_cls_journal_transactions.go` and `makeshift_replay_cls_journal_bulk_transactions.go` provide replay tooling for the legacy makeshift path; `cmd/data_comparator`/`cmd/data_comparator_rx_da` provide reconciliation between Ledger and source-of-truth systems.

## Event / Topic Table

| Direction | Mechanism | Name/Topic | Producer/Consumer group | Notes |
|---|---|---|---|---|
| Consumed | Kafka | env `LEDGER_QUEUEKAFKA_KAFKA_TOPIC` / `LEDGER_QUEUEKAFKA_KAFKA_GROUPID` | Maxwell/Debezium CDC from api-monolith DBs | PG-only, `config/default.toml:367-381` |
| Consumed | Kafka | env `LEDGER_QUEUEMAKESHIFTTXN_KAFKA_TOPIC/GROUPID` | makeshift dual-write pipeline | X legacy, `config/default.toml:383-395` |
| Consumed | SQS | internal jobs: `journal_create`, `account_create`, `balance_update`, `ledger_entry_details_create` | self-enqueued | `config/default.toml:397-400` `[job]` |
| Produced | SNS | `journal-created` (`arn:aws:sns:...:journal-created`) | `pkg/pubsub` via `Core.publishJournalCreatedEventToSNS` | X: `payout_initiated/failed/reversed`, `fav_*`, `fund_loading_processed`, adjustment events, inter-account/VA-to-VA events (NOT `payout_processed`) — `internal/journal/config.go:87-99` |
| Produced (outbox) | outbox_jobs table → Kafka | topic from context (`contextkeys.KafkaTopic`) | `cmd/outbox_relay` polls `outbox_jobs` and republishes | PG reverse-shadow only (`CheckIfOutboxPushEnabled`) |

## Worker / Cron / Admin Table

| Component | Purpose | Path |
|---|---|---|
| `cmd/scheduler` + `internal/scheduled_jobs/*` | balance_update_script, update_ledger_entry_balance, update_merchant_balance_subaccounts, split_account_balance_update(_pg), journal_created_sns_publish, control_tables_sync_mirror, penny_expense_account_update, verify_warm_storage, check_sqs_connection | recompute/repair/reconciliation crons — some can mutate balances/journals |
| `cmd/idempotency_partition_manager`, `cmd/idempotency_scheduler` | manage partitions of `idempotency` table | |
| `cmd/data_backfill`, `cmd/data_comparator`, `cmd/data_comparator_rx_da` | backfill/reconcile Ledger vs source system data | |
| `cmd/merchant_offboard` | offboarding/data-deletion tool | |
| Dashboard admin API | `DeleteMerchantsByEventAndTenant`, ledger-config CRUD, account form-field lookups | `internal/dashboard/core.go`, `internal/journal/ledger_config/server.go` — ledger_config (accounting rules) is itself an admin-editable API surface, not just static seed data |
| `ForceUpdateBalance` | bypasses min-balance check to force-set an account balance | `internal/account/core.go:838`, `internal/account/repo.go:458` — high-risk repair primitive |

## Datastore Table

| Table | Purpose | Migration |
|---|---|---|
| `journal` | header row per transactor event (merchant_id, amount, transactor_id, transactor_type/event, currency) | `20201001011143_create_journal.go`, renamed transactor_type→transactor_event `20210925113658` |
| `accounts` | chart-of-accounts sub-accounts with materialized `balance`, `min_balance`, `negative_balance` | `20201001011117_create_accounts.go` |
| `ledger_entries` | per-account debit/credit lines under a journal | `20201001011149_create_ledger_entries.go`, balance columns added later |
| `ledger_entry_details` | denormalized detail/notes per entry, created async | `20201001011240_create_ledger_entry_details.go` |
| `account_details` | entity/identifier mapping for account discovery (banking_account_id, fts_fund_account_id, etc.) | `20201022184734_create_account_details.go` |
| `idempotency` | HTTP idempotency-key cache, range-partitioned by `created_at` | `20210527121244_create_idempotency.go` |
| `idempotency_partition_info` | partition bookkeeping for idempotency table | `20220502170054_create_idempotency_partition_info.go` |
| `ledger_config` | transactor-event → debit/credit account rule definitions (the seed data above is loaded into this table) | `20210810170948_create_ledger_config.go` |
| `state_change_logs` | audit of account state transitions | `20201027234816_create_state_change_logs.go` |
| `journal_rejected_events` | soft-DLQ of failed journal create attempts | (model file located; migration not individually confirmed by name in this pass) |
| Two migration dirs: `rx_migrations` (X/RX DB) and `pg_migrations` (PG DB) | tenant-separated schema/DB per `db.rx` / `db.pg` config | `internal/database/{rx,pg}_migrations` |
| Makeshift MySQL schema | legacy X ledger being phased out | `internal/makeshift/database/makeshift_migrations` |

## Config / Flag Table

| Key | Purpose |
|---|---|
| `app.isMirror`, `app.isMakeshift` | mode flags read into context (`contextkeys.IsMirror`) gating SNS publish suppression for mirrored requests (`CheckIfJournalCreatedPublishEnabled`) |
| `db.rx.isEnabled`, `db.pg.isEnabled`, `db.makeshift.isEnabled` | per-tenant DB toggles |
| `db.enableTrafficSplit` | read-replica / traffic split toggle |
| `auth.payouts`, `auth.fts`, `auth.xbalances`, `auth.settlements`, ... | per-client Basic Auth credentials, one block per calling service |
| `queueKafka.kafka.topic/groupID` (env-injected) | PG CDC ingestion topic |
| `queueMakeshiftTxn.kafka.topic/groupID` (env-injected) | X makeshift dual-write topic |
| `pubSub.journal_created` / `snsOutboxer.pubSub.journal_created` | SNS topic config for `journal-created` events |
| `outbox.useOutboxJobsSync`, `Outbox.ProcessorEnabled` | outbox relay toggles |
| `merchantBalanceAsyncUpdateExperiment` (Splitz key) | experiment flag to move merchant-balance updates to async (X) |
| `job.journal_create/account_create/balance_update/ledger_entry_details_create` | SQS internal job name config |

## Dependencies (inferred from client-auth / provider config)

- **Payouts service** — presumed primary caller of `Journal.Create` for X (via `auth.payouts` creds); not verified in this repo (out of scope, lives in a different service).
- **FTS (Fund Transfer Service)** — presumed caller for payout_processed/reversed confirmations (`auth.fts` creds; `FtsPayable`/`FtsReceivable` accounts keyed by `fts_fund_account_id`).
- **X Balances** service — reader of balances (`auth.xbalances` creds).
- **api-monolith sub-services** (Settlements, Growth, UPS, PCP, NBS, Route, Affordability, Emandate, CBImport, BTS, DisputeService, Partnerships, APM, CPS, Optimizer, Offers) — CDC producers for PG reverse-shadow, not payouts-relevant.
- **WDA** (`internal/provider/wda`, `[wda]` config, `wda-service.concierge...razorpay.in`) — external provider dependency, purpose not investigated (out of lane).
- **Splitz** — experimentation service (`journalCoreDependencies.Splitz.GetVariant`).
- Redis — distributed mutex backing (`pkg/redis`, `internal/provider/mutex`).
- SNS/SQS/Kafka (AWS) — messaging infra as described above.

## Unresolved Questions

1. **Out-of-order event handling** (e.g., `payout_processed` arriving before `payout_initiated`) — no explicit sequence-gate/state-machine found in `journal/core.go` or `ledger_config`; each transactor event is validated independently. Whether `min_balance`/negative-balance checks on VendorPayable effectively prevent or merely mask an out-of-order processed event was not verified (would need a functional test or a call-site of `EnableNegativeLimitCheck`/`OverrideMinBalanceCheck` for VendorPayable specifically).
2. Whether Payouts/FTS actually call Ledger directly (vs. through an intermediary) could not be confirmed from this repo alone — inferred from the existence of `auth.payouts`/`auth.fts` client credentials and the `FtsPayable`/`FtsReceivable`/`fts_fund_account_id` account-discovery wiring.
3. The pre-existing `.agents/skills/repo-skill/modules/integration/internal/payout-service/*.md` doc describes payout ingestion as **Kafka event consumption** via `cmd/worker_kafka` — this conflicts with what was directly verified in code (`worker_kafka`'s kafka jobs are wired only for PG-tenant CDC clients like API/Growth/UPS/PCP/Settlements/etc., not for a "payouts" or "X" kafka job). Treat that doc's inbound-mechanism claim as **likely stale/incorrect**; the synchronous Twirp `Journal.Create` path is the verified mechanism for X.
4. Whether there is a DB-level safeguard (serializable isolation, advisory lock, or unique index added in a later migration not surfaced by filename grep) beyond the app-level mutex for the transactor uniqueness check was not exhaustively confirmed — only the initial `journal` table migration and a grep for `UNIQUE` across migration files were checked.
5. `MerchantBalanceAsyncUpdateExperimentKey` — found the Splitz evaluator function but did not trace its call-site into the sync/async balance-update decision path; unclear if/where it's currently wired in.
6. Exact real (non-local) Kafka topic/consumer-group names for `queueKafka` and `queueMakeshiftTxn` are env-injected and not present in this repo (would be in deployment secrets).
