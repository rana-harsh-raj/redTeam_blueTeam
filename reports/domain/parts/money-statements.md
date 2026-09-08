# Lane D3 — money, balances, beneficiaries and statements (M6 discovery)

Graph part: `reports/domain/parts/money-statements.json` (schema `reports/domain/SCHEMA.md`).
Generated from the pinned read-only clones under
`/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture/`:
`ledger@471ff4d5`, `ledger-sdk`, `x-balances@1a21c0f9`, `banking-accounts@c3fc1fe8`,
`x-account-statements@e73fd5a`, `recon@6974fec`, `cfa@d488558e`, `virtual-account@437f398`,
`kube-manifests@9226a892`, `proto@5268257`, cross-referenced against `payouts@4bf3dbf9`,
`fts@2a09e763`, `api@2d665f91`.

Twin state is read from `ENV2_COMPOSE/docker-compose.yml`, `ENV2_COMPOSE/scripts/up.sh`,
`ENV2_COMPOSE/config/templates/base/*/arena.toml`, `ENV2_COMPOSE/seeds/**`,
`ENV2_COMPOSE/substitutes/*/CONTRACT.md` and `ENV2_COMPOSE/config/declared-deviations.yaml`.

## 0. Counts

| metric | value |
|---|---|
| nodes | 446 |
| edges | 526 |
| families | 5 (`accounting`, `balance-refresh-reservations`, `statement-reconciliation`, `beneficiary-fund-accounts` = P0; `virtual-account-payouts` = P1) |
| journeys | 13 (all P0 families covered) |
| nodes by kind | route 124, event 56, worker 44, table 38, queue 34, state 31, cron 30, service 15, external 15, journey 13, flag 12, datastore 9, repository 8, topic 6, identity 6, substitute 5 |
| fidelity | `real_source_running` 185, `real_source_mapped_not_running` 135, `graph_only` 61, `high_fidelity_replacement` 48, `behavioural_placeholder` 17, `blocked_missing_access` 0 |
| validator | `python3 scripts/domain/build_graph.py --parts reports/domain/parts --out /tmp/gcheck --strict` → 0 schema violations, 0 dangling endpoints |

## 1. Twin fidelity of this lane at a glance

| Production component | Twin | Fidelity |
|---|---|---|
| ledger `api` | `ledger-api` (compose:970), reached by payouts through `ledger-gate` (compose:1634), by FTS/monolith directly | `real_source_running` |
| ledger `worker` × 5 queues | `ledger-worker` (account_create), `-balance-update`, `-journal-create`, `-entry-details-create`, `-entry-details-create-pg` (compose:994-1113) | `real_source_running` |
| ledger `scheduler` | `ledger-scheduler`, one-shot, `LEDGER_SCHEDULER_COMMAND=split-account-balance-update` (compose:1114) | `real_source_running` (single run, no cadence) |
| ledger `worker_kafka`, `worker_makeshift_txn`, `outbox_relay`, `idempotency_*` | absent | `real_source_mapped_not_running` |
| ledger prod CronJobs (4: `split-account-balance-update-live`, `…-pg-live`, `verify-warm-storage-live`, `…-test`) | absent; **all four deployed manifests carry `suspend: true`** | `real_source_mapped_not_running` |
| ledger PostgreSQL (rx pool) / Redis mutex / SNS `journal-created` | `postgres-ledger`, `redis`, LocalStack SNS topic created but **no subscriber** | running / running / `behavioural_placeholder` |
| x-balances `server` | `xbalances-server` (compose:1544) | `real_source_running` |
| x-balances `worker` (9 job names: 6 per-bank fetch + payout_events + account_activation + pocworker) | one `xbalances-worker` container (compose:1570); `[Queue] Driver = "inmemory"` (DEV-081), Mozart+banking-accounts `Mock=true` (DEV-082) | `real_source_running` binary, **functionally inert** |
| CFA `server` + `worker` ×2 | `cfa-server`, `cfa-worker-contact` (`contact-lazy-load`), `cfa-worker-fa` (`fund-account-lazy-load`) (compose:1466-1543); Vault/BIN/Token/ApiService mocked (DEV-092) | `real_source_running` |
| banking-accounts (BAS) | **not built**; `bankingaccounts-stub` (compose:1789) serves only the shield-details and RBL-credentials routes | `behavioural_placeholder` |
| x-account-statements (XAS) | **not built**; `xas-sim` (compose:1871), `substitutes/xas-sim/CONTRACT.md` | `high_fidelity_replacement` |
| recon (ART) | absent entirely | `graph_only` |
| virtual-account | absent entirely | `graph_only` |
| API monolith BAS/DA-ledger modules | `monolith-stub` `payout_update` relay + DA journal emitter (DEV-163..166) | `high_fidelity_replacement` |
| payouts money-lane workers | `payouts-worker-x-balances-balance-refresh` (788), `-rbl-banking-account-statement` (817), `-payout-source-updater` (851) | `real_source_running` |

## 2. Ledger

### 2.1 Twirp surface (complete — 8 services, 46 RPCs)

| Service (`/twirp/rzp.…/`) | RPCs |
|---|---|
| `ledger.journal.v1.JournalAPI` | Create, CreateInBulk, CreateJournalRejectedEvents, CreateLedgerEntryDetails, FetchById, FetchByTransactor, UpdateBalance |
| `ledger.account.v1.AccountAPI` | Create, CreateInBulk, CreateOnEvent, CreateMerchantSubAccounts, CreateSplitAccounts, Update, UpdateByEntitiesAndMerchantID, FetchById, FetchByMerchantID, FetchByEntitiesAndMerchantID, FetchInBulkByEntitiesAndMerchantID, Activate, Archive, Deactivate |
| `ledger.account_detail.v1.AccountDetailAPI` | Create, Update, FetchById, FetchByAccountId |
| `ledger.ledger_config.v1.LedgerConfigAPI` | Create, CreateInBulk, Update, Delete |
| `ledger.ledger_entry.v1.LedgerEntryAPI` | FetchById, UpdateNotes |
| `ledger.dashboard.v1.DashboardAPI` | DeleteMerchants, ReplayJournalRejectedEvents, FetchFilter, FetchMerchantAccounts, FetchMerchantLedgerEntries, FetchMerchantLedgerEntryByID, FetchPGMerchantAccounts, FetchPGMerchantBalances, FetchAccountTypes, FetchAccountFormFieldOptions, FetchJournalFormFieldOptions, FetchLedgerConfigFormFieldOptions |
| `common.health.v1.HealthCheckAPI` | Check |
| `common.dashboard.v1.Dashboard` | Fetch, FetchMultiple |

Source: `ledger/rpc/**/**.twirp.go` `PathPrefix` + method dispatch. Auth: HTTP Basic per client
(`ledger/config/default.toml [auth.payouts|fts|xbalances|settlements|…]`, `internal/boot/hooks/auth.go:18`)
plus the `Ledger-Tenant` header. Payouts reaches ledger through `ledger-gate` in the twin; the gate
forwards **all** headers except hop-by-hop (`substitutes/ledger-gate/CONTRACT.md`, DEV-001) so
`idempotency-key` survives — without it `pkg/idempotency/interceptor.go:115-118` silently disables
request idempotency for that leg.

`cmd/scheduler` carries 22 named commands (`ledger/internal/common/constant.go:905-926`,
dispatch `internal/scheduled_jobs/base.go:264-330`) — repair/backfill primitives such as
`ledger-entry-balance-update`, `balance-update`, `journal-created-sns-publish` and the makeshift
replay family. Only **four** of them are wired to a Kubernetes CronJob, and **all four are
`suspend: true`** (`split-account-balance-update-cronjob-live` `*/10 * * * *`, `…-pg-live`,
`verify-warm-storage-cronjob-live` and `-test` `0 * * * *`). Ledger has no `kube-manifests/cde/ledger`
path — manifests live under `prod/ledger/values.yaml` + `templates/ledger/`. Production cadence for
every other command is therefore unknown.

Twin exercises only: `JournalAPI/Create`, `JournalAPI/FetchByTransactor`, `AccountAPI/CreateInBulk`,
`AccountAPI/CreateOnEvent`, `AccountAPI/Activate`, `AccountAPI/FetchByEntitiesAndMerchantID`,
`LedgerConfigAPI/CreateInBulk`, `HealthCheckAPI/Check` (`ENV2_COMPOSE/scripts/up.sh:149-173`).

### 2.2 Accounting matrix — X tenant `ledger_config` (56 rules)

Selection is by **rule**, not by config name: `{transactor_event[, fee_accounting][, data_movement_strategy]}`
(`ledger/internal/journal/ledger_config/seed_data/{shared,direct}_account_x.go`). `transactor_event_name`
in the `ledger_config` row is the *config id*; `UNIQUE(tenant, transactor_event_name)`.

Shared-balance (`shared_account_x.go`) — the payouts path:

| transactor_event | config id | Debit | Credit |
|---|---|---|---|
| payout_initiated | XPayoutInitiatedV2 | MerchantBalance(amount+commission) | VendorPayable(amount), CommissionIncome(commission−tax), OutputGST(tax) |
| payout_processed | XPayoutProcessedV2 | VendorPayable(amount) | FtsPayable(amount) |
| payout_reversed | XPayoutReversedV2 | FtsReceivable(amount), CommissionIncome(commission−tax), OutputGST(tax) | MerchantBalance(amount+commission) |
| payout_failed | XPayoutFailedV2 | VendorPayable(amount), CommissionIncome(commission−tax), OutputGST(tax) | MerchantBalance(amount+commission) |
| payout_* + `fee_accounting=reward` | XRewardPayout*V2 | as above but MerchantBalance(amount) and MerchantReward(commission) split the fee leg | — |
| fav_initiated | XFavInitiatedV2 | MerchantBalance(commission) | CommissionIncome(commission−tax), OutputGST(tax) |
| fav_processed | XFavProcessedV2 | PennyControl(amount), VendorPayable(amount), PennyExpense(amount) | VendorPayable(amount), FtsPayable(amount), PennyControl(amount) |
| fav_reversed | XFavReversedV2 | PennyControl(amount), FtsReceivable(amount), VendorPayable(amount) | PennyExpense(amount), VendorPayable(amount), PennyControl(amount) |
| fav_failed | XFavFailedV2 | CommissionIncome(commission−tax), OutputGST(tax) | MerchantBalance(commission) |
| fund_loading_processed | XFundLoadingProcessedV2 | TerminalReceivable(amount) `$terminal_account_type` | MerchantBalance(amount) |
| fund_loading_processed + reward | XRewardFundLoadingProcessedV2 | Reward(amount) | MerchantReward(amount) |
| fund_loading_expired + reward | XRewardFundLoadingExpiredV2 | MerchantReward(amount) | Reward(amount) |
| positive_adjustment_processed | XPositiveAdjustmentProcessedV2 | AdjustmentLiability(amount) | MerchantBalance(amount) |
| negative_adjustment_processed | XNegativeAdjustmentProcessedV2 | MerchantBalance(amount) | AdjustmentLiability(amount) |
| inter_account_payout_initiated | XInterAccountPayoutInitiatedV2 | MerchantBalance(amount) | InterAccountPayable(amount) |
| inter_account_payout_processed | XInterAccountPayoutProcessedV2 | InterAccountPayable(amount) | FtsPayable(amount) |
| inter_account_payout_reversed | XInterAccountPayoutReversedV2 | FtsReceivable(amount) | MerchantBalance(amount) |
| inter_account_payout_failed | XInterAccountPayoutFailedV2 | InterAccountPayable(amount) | MerchantBalance(amount) |
| inter_account_credit_processed | XInterAccountCreditProcessedV2 | InterAccountReceivable(amount) | AdjustmentLiability(amount) |
| va_to_va_payout_initiated | XVaToVaPayoutInitiatedV2 | MerchantBalance(amount+commission) | AdjustmentLiability(amount), CommissionIncome(commission−tax), OutputGST(tax) |
| va_to_va_payout_failed | XVaToVaPayoutFailedV2 | AdjustmentLiability(amount), CommissionIncome(commission−tax), OutputGST(tax) | MerchantBalance(amount+commission) |
| va_to_va_credit_processed | XVaToVaCreditProcessedV2 | AdjustmentLiability(amount) | MerchantBalance(amount) — receiving merchant |
| (Malaysia) XMYPayoutInitiatedV2, and 8 `*Legacy` configs mapping to `CashAccountLegacy` | | | |

**There is no `va_to_va_payout_reversed` config** — payouts maps both `failed` and `reversed`
onto `va_to_va_payout_failed` (`payouts/internal/app/payouts/status.go:60-65`).

Direct-account (`direct_account_x.go`, 21 rules) — statement-driven, identified by
`{"banking_account_stmt_detail_id": "$banking_account_stmt_detail_id"}`:

| transactor_event | config id | Debit | Credit |
|---|---|---|---|
| da_payout_processed | XDAPayoutProcessed | DAMerchantBalance(amount), DACommissionReceivable(commission) | DAVendorPayable(amount), DACommissionIncome(commission−tax), DAOutputGST(tax) |
| da_payout_processed_recon | XDAPayoutProcessedRecon | DAVendorPayable(amount) | DAMerchantCash(amount) |
| da_payout_reversed / _recon | XDAPayoutReversed(Recon) | mirrors | mirrors |
| da_ext_debit | XDAExtDebit | DAMerchantBalance(amount) | DAMerchantCash(amount) |
| da_ext_credit | XDAExtCredit | DAMerchantCash(amount) | DAMerchantBalance(amount) |
| da_ext_payout_processed / _reversed | XDAExtToPayout* | 7-leg re-classification external → payout | |
| da_fee_payout_* / da_ext_fee_payout_* | XDAFeePayout*, XDAExtToFeePayout* | DAMerchantBalance ↔ DACommissionReceivable | |
| + reward and `da_legacy_data_migration` variants | | | |

**No `da_payout_initiated` and no `da_payout_failed` exist** — a Direct payout produces zero
journals until a bank statement is matched, and a Direct failure produces none at all.

### 2.3 Balance authorization, idempotency, events

- Merchant balance is a **materialised column** `accounts.balance`, updated by an atomic conditional
  `UPDATE accounts SET balance = balance + ? … AND (balance + ? >= ?) RETURNING balance`
  (`internal/account/repo.go:421`). 0 rows affected ⇒ `ErrInsufficientBalanceFailure`. For tenant X,
  `MerchantBalanceAccount` is **not** in `updateBalanceModeAsyncConfigForX`
  (`internal/journal/config.go:27`) ⇒ the debit runs **inside the journal transaction**, i.e. Journal.Create
  is the payout's balance authorization. VendorPayable/Commission/Nodal/Current/GST/M2P/AmazonPay are async
  via the `balance_update` SQS job.
- Idempotency has three layers: (1) HTTP `idempotency-key` cache in the partitioned `idempotency`
  table (skipped entirely when the header is empty, `pkg/idempotency/interceptor.go:115-118`);
  (2) business uniqueness on `(transactor_event, transactor_id)` enforced by
  `ValidateJournalExist` under a Redis mutex (`internal/journal/core.go:2249`) — **no DB unique index**;
  (3) SQS `journal_create` job drops data errors without retry (`job_sqs/journal_create.go:224-285`).
- `journal-created` SNS is published only for the whitelisted `(tenant, transactor_event)` pairs in
  `internal/journal/config.go:87-99` — for X: `payout_initiated`, `payout_failed`, `payout_reversed`,
  `fav_*`, `fund_loading_processed`, adjustments, inter-account and VA-to-VA — **not `payout_processed`**.
  Split-account journals defer the publish to the balance-update job (`core.go:1444`).
- Failed Creates land in `journal_rejected_events` (soft DLQ) and are replayable through
  `DashboardAPI/ReplayJournalRejectedEvents`.

## 3. x-balances

| Route | RPC | Purpose |
|---|---|---|
| `GET /v1/balances/{id}` | BalancesAPI.GetBalanceById | the Direct-payout gate's balance read; **no status filter**; API-DB fallback `type='banking'` |
| `POST /v1/balances` | BalancesAPI.BatchGetBalances | batch |
| `GET /v1/accounts` | AccountsAPI.ListAccounts | payouts' source-account selection. Queries the x-balances DB **and the API-monolith DB in parallel** and dedups with x-balances winning; if both fail it returns an empty array, not an error |
| `POST /v1/balance/create` | AccountsAPI.CreateBalance | idempotent on (merchant_id, channel); used by banking-accounts (Slice only) |
| `POST /v1/balance/fetch` | BalanceFetchAPI.TriggerManualBalanceFetch | synchronous bank fetch |
| `POST /v1/cron/balance/{channel}/fetch` | BalanceFetchAPI.InitiateBalanceFetch | per-channel dispatcher (priority / transacting-ZSET / least-recent) |
| `GET /v1/cron/balance/stale-entries/report` | BalanceFetchAPI.ReportBalanceStaleEntries | staleness alerting (300 s / 900 s) |
| `POST|GET|PATCH /v1/sub_balance*` | SubBalanceAPI ×5 | sub-balance CRUD + spend limits |
| `POST /v1/admin/admin-actions` | AdminActionsAPI.AdminAction | **raw SQL execution** against the x-balances DB |

**"MAR" is not a term in this codebase.** An exhaustive case-insensitive grep across `x-balances`,
`virtual-account`, `banking-accounts` and `proto/x` finds no `MAR` / "Merchant Account Registration".
The two RPCs that fit the description are `AccountsAPI/ListAccounts` (source-account selection, what
payouts calls) and `AccountsAPI/CreateBalance` + the `account_activation` SQS event (registering a
merchant's bank account into the `balance` table).

Bank polling: **six** channels — `rbl, axis, icici, yesbank, idfc, slice`
(`internal/enum/channel/channel.go:11-42`; no HDFC). One job per channel, all `MaxRetries:1 /
Timeout:60s`, each consuming `{APP_ENV}-x-balances-<channel>-balance-update-live`; **RBL alone has a
second priority queue** `…-rbl-priority-balance-update-live` and is the only channel routed to it when
`is_priority_fetch` is set. Every job handler deliberately returns `nil` on error so SQS does not
requeue. Credentials come from banking-accounts; the balance comes from Mozart:

| channel | Mozart namespace / gateway / version / action |
|---|---|
| rbl | `razorpayx / rbl / v1 / account_balance` |
| icici | `fts / icici / v2 / account_balance` |
| axis | `fts / axis / v1 / account_balance` |
| yesbank | `fts / yesbank / v1 / account_balance` |
| idfc | `fts / idfc / v1 / account_balance` |
| slice | `fts / slice / v2 / account_balance` |

x-balances has **no Kubernetes CronJobs** (and no `kube-manifests/cde/x-balances` path) — the
`/v1/cron/...` routes are driven by an external scheduler under the `Cron` ACL principal.
**The twin runs one `xbalances-worker` container**, its queue driver is in-memory, and
Mozart/banking-accounts are mocked — so no bank poll ever happens.

`BalanceRefreshEvent{balance_id, merchant_id, channel, gateway_balance, balance_last_fetched_at,
gateway_balance_change_at}` is published on **every successful fetch** (even when unchanged) iff DCS
`in_flight_reservation_enabled(merchant)` (5-min memo), onto `{env}-x-balances-balance-refresh-live`
(`balance_fetch/service.go:378-457`). Payouts' `x_balances_balance_refresh` worker consumes it and calls
`ReleaseHoldsAfterBalanceRefresh`.

In-flight reservations live **entirely in payouts' Redis, not in x-balances**: counter
`payouts:in_flight:{mid:balID}`, item hash `payouts:in_flight_items:{mid:balID}` (`amount:dispatchedAt:state`),
tracked set, heartbeat; states `live` and `awaiting_balance_refresh`; TTL 6 h; admission Lua
`((gatewayBalance − counter) − amt) >= threshold`. x-balances' `balance` column is **never** debited by
a payout; the only writer is a bank fetch.

API-DB fallback: `internal/balances/service.go:43-153` reads the API monolith's own `balance` table via a
second connection (`[APIStore]`) when the id is missing locally (always pinning `type='banking'`); for
`sub_balance`/`shared` rows the stored number is overwritten in real time from Ledger
(`internal/enrichment/enricher.go:184-223`). The worker binary does **not** open the API DB.

**x-balances is itself a ledger producer.** Its `sub_balance_limits` service posts
`va_to_va_credit_processed` (`pkg/ledger/sub_balance_limit_journal.go:33-79`,
`internal/sub_balance_limits/service.go:191,217,256`) with `transactor_id = ct_<limit id>`,
`notes.balance_id = bal_<id>` and `identifiers.banking_account_id = bacc_<id>` resolved from payouts
`GET /v1/internal/banking_account/{balance_id}`; the limit row moves to `processed`, or `markFailed`.
No fund movement happens at limit creation — the real bank debit is the master CA's FTS payout later
(`internal/database/model/sub_balance_limit.go:20-23`).

## 4. banking-accounts (BAS)

`banking_account{account_number, status, account_type, partner_bank, balance_id, fts_fund_account_id,
credentials JSON, beneficiary_details JSON}` is the join point between the bank account, the
x-balances/monolith `balance`, and the FTS `fund_accounts` row.

- Onboarding per bank: `internal/applications/onboarding/{rbl,axis,icici,idfc,yesbank,yesbank_pobo,slice}`.
  Balance creation is **split**: Slice → `POST v1/balance/create` on x-balances (`pkg/balances/client.go:38`);
  every legacy bank → monolith `POST v1/bas/merchant/{mid}/banking_accounts`
  (`internal/applications/onboarding/balance_creation.go:29-49`). For `CA_DIRECT` it then publishes the
  x-balances account-activation event (`balance_creation.go:89-124`).
- The credentials route `GET /merchant/{mid}/banking_account_by_account_number/{acct}/credentials`
  is what payouts' RBL statement worker, XAS's fetch workers and x-balances' fetch worker all call;
  the twin serves it from `bankingaccounts-stub`.
- **BAS is the publisher of both activation events** (this closes a previously open question):
  `{env}-x-balances-account-activation-event` and `{env}-x-account-statements-account-activation-event`
  are both produced from `internal/applications/onboarding/balance_creation.go:113,142`, with the
  archive variants at `internal/services/application_aggregate_service.go:5375-5388`. Slice onboarding
  additionally calls XAS `POST /v1/accounts` directly (`pkg/accountstatements/client.go:36-37`).
- BAS has **no worker binary, no cron binary and no CronJob template** — a single `main.go` Gin service.
  It also has **no gRPC/Twirp server and no statement routes at all**; `fts_fund_account_id` is created
  by calling FTS during each bank's `pre_activation` and stored on `banking_accounts`.
- Admin/data-fix surface: `PATCH /banking_account/{id}/balance_id` (explicitly CAUTION-flagged,
  `internal/bootstrap/routes.go:283-303`), `POST /admin/business/{bid}/applications/{aid}/activate_account`,
  application purge/sub-status routes.
- `payout_update` recon: BAS is **not** on that path — the `payout_update` chain is XAS → monolith → PS.

## 5. x-account-statements (XAS)

Ingestion: `POST /v1/banking_account_statement/fetch/initiate {channel,…}` and `…/fetch/manual` →
per-bank SQS queues (`prod-x-account-statements-fetch-sqs`, `-fetch-ybl/-idfc/-axis/-icici/-slice-sqs`)
→ Mozart `razorpayx/<bank>/v2/account_statement` → base64 CSV → dedupe tuple
`{account_number, posted_date, type, bank_serial_number, amount, channel, bank_transaction_id}` →
`account_statements` rows → one `statement_enrichment` job per saved row.

Source events: payouts is the **only** producer of `x_account_statement_source_event`
(`payouts/internal/job/x_account_statement_source_event.go`), gated on Direct account type, terminal
status ∈ {processed, failed, reversed}, Splitz `account_statement_source_event`, and ≥1 of
utr/grn/cms. XAS persists them with `UNIQUE(event_id, entity_type)`. **Known schema mismatch:** PS emits
`event_created_timestamp`, XAS reads `event_create_timestamp` ⇒ stored 0 (functionally inert; reproduced,
not fixed, by `xas-sim`).

Matching (`enrich/service/service.go`): identifiers tried in order **utr → gateway_ref_number →
cms_ref_number (= statement `bank_transaction_id`)**; statements filtered by `account_number AND amount`,
source events by `balance_id AND amount`; **no merchant_id, currency, mode or date window**. Guards:
0 matches → no-op; >2 → `TooManyStmtsForUtr`; two of the same `type` → `UnexpectedStmtType`; no source
event → `entity_type = external`. `PayoutEnricher` maps credit → `payout_reversal`, debit → `payout`, and
refuses to relink a statement already bound to a different non-external entity.

Dual-write: after an enrichment write whose `entity_type ∈ {payout, payout_reversal}`, the Kafka CDC
`Update` handler POSTs the 9-field `payout_update {bas_id, entity_id, entity_type, merchant_id,
transaction_date, converted_from_external, utr, grn, cms_ref_no}` to the monolith, which forwards it to
PS `UpdatePayoutAfterBASRecon`. `POST /v1/statement/dual_write` re-sends unconditionally.

`xas-sim` coverage (`ENV2_COMPOSE/substitutes/xas-sim/CONTRACT.md`):

| XAS area | in `xas-sim`? |
|---|---|
| SQS source-event consumer, validation, `(event_id, entity_type)` dedup, schema mismatch | yes |
| matching key order + amount/account/balance filters + external / >2 / dual-type guards + relink refusal | yes |
| 9-field outbound `payout_update` and `POST /v1/statement/dual_write` | yes (synchronous, not via Kafka CDC — DEV-161) |
| `GET /v1/account_statements/fetch_multiple_by_reference_numbers` (string-serialised int64s) | yes |
| bank ingestion workers (RBL/AXIS/ICICI/IDFC/YESBANK/SLICE) | **no** — store is a mirror of the real PS `banking_account_statement` table (DEV-160) |
| Kafka CDC dual-write, Elasticsearch sync, API-DB legacy fallback, inbound auth verification, MySQL persistence | **no** (DEV-160..162) |
| `POST /v1/source_event/fetch` | returns 501 (needs a monolith route the stub does not serve) |

## 6. recon (ART)

FinOps downloads a post-match "RX Workflow" file, edits it and re-uploads it. The upload endpoint
`POST /workflow_file` is registered in recon's **public routes**, i.e. the auth decorator is bypassed
(`app/web/routing/public_routes.py:25`, `routes.py:35`, `auth.py:153`); an edge-JWT-protected twin
`POST /workflow_file_upload` exists separately (`api_edge_routes.py:167`) and the monolith proxies
`recon/service/workflow_file_upload` (`api/app/Http/Route.php:4910`). Accepted rows (`approved = yes`)
have their `workflow_payload` column pushed **verbatim** to Kafka topic **`prod-recon-workflow`**
(`app/web/services/workflow_file_processing.py:82-105,549-551`; `app/constants/kafka.py:16`;
`app/config/common.env.toml:41`). That payload is built upstream from the per-workspace
`workflow_configs` rows as `{end_point, service_name, method, payload}`
(`app/post_recon_service/workflow.py:70-121`). `recon-workflow-kafka-consumer`
(`app/workflow_kafka_consumer/processor/{reconciliation_workflow.py,base.py}`, group
`kafka-recon-consumer-group`, auto-commit off) dispatches a fully generic HTTP request with 5 retries.
`service_name ∈ {FTS, API, PRS}` — and **that three-entry list is the only allow-list**: `end_point`
and `method` are free-form runtime data (`app/providers/contracts/worklflow_config_templates.py:11`).

For FTS this is a first-class authorized write path: `fts/internal/routing/router/route_list.go:172-181`
puts `PATCH|POST /v1/attempts/{action}` and `PUT /v1/attempts/reconcile` behind
`BasicAuth(APIAuth, ARTAuth)`, and `ARTAuth` is documented as recon's credential
(`fts/internal/constants/authusers.go:6`). Actions `update` / `safe_update` enqueue
`ProcessAttemptUpdate`, which cascades attempt → transfer → payout status. Every other FTS group
(`/v1/transfer`, `/v1/transfers`, `/v1/account`, `/v1/source_account`, `/v1/routing`) excludes ART.

Towards the **API monolith**, the `recon` app credential is allow-listed to exactly 13 route names
(`api/app/Http/Route.php:19555-19569`), including `internal_fail` (`POST internal/{id}/fail`),
`internal_reconcile` (`PATCH internal/{id}/reconcile`), `internal_receive`, `recon_update_data` and
`payment_card_recon_create_transaction`. Notably **`banking_account_statement/payout_update` is NOT in
that list** — that route is the BAS-recon path (XAS → monolith → PS), not the ART path. The third
target, `PRS`, is recon's own post-recon-service (`/v1/admin-dashboard-prs/...`, credential
`recon_prs_user`), which carries `force_recon_status_update` (hard-limited to target status
`RECONCILED`) and the transactional-workflow events keyed on `payouts_id`.

Towards payouts the path is `POST /v1/banking_account_statement/process/batch`
(`recon_status ∈ Reconciled | Reversed | Reconciled_Reversed | Unreconciled`), reached through the
monolith's `batch` app group. No ledger write path exists in recon. recon's only Kubernetes CronJob is
`recon-mysql-archival-service`, `0 0 * * *`, `suspend: false` — the one non-suspended CronJob anywhere
in this lane. Nothing of recon is in the twin.

## 7. CFA — beneficiary matrix

| `fund_account.account_type` | validation in CFA | downstream |
|---|---|---|
| `bank_account` | `ifsc` 11 chars/alnum (+ static `IFSC.json` branch lookup unless DCS `skip_ifsc_lookup`) or `bic` for MY; `account_number` 5–35 alnum, **immutable after create**; beneficiary name 3–120, strict or relaxed regex | FTS `bank_accounts`; penny-drop/FAV is ValidX's, not CFA's |
| `vpa` | `username@handle` regexes, length bounds, no consecutive/leading/trailing dots; no handle allow-list, no UPI directory check | FTS `vpas` |
| `wallet_account` | provider must be exactly `amazonpay`; phone via phonebook lib; email regex; name bounds | FTS `wallets` |
| `card` | `ValidateCard()` is a **no-op**; correctness delegated to Vault tokenise → BIN/IIN → (Token service) → monolith `RegisterBeneForCardFundAccount`; only `vault_token`/`global_fingerprint`/`last4`/`iin`/network/issuer/expiry persisted | monolith card beneficiary registration |
| `mobile` | declared in the proto and model (`PhoneNumberRequest`, `MobileDetails`) but `ValidateWithConfig` has **no branch** for it, so a create falls to the default arm and returns `ErrInvalidAccountType` — effectively unusable at this SHA | none (rejected) |

Contact types: `customer`, `employee`, `vendor`, `self` (merchant-creatable) and internal
`rzp_fees`, `rzp_tax_pay`, `rzp_xpayroll`, `rzp_capital_collections`, `rzp_charge_collections`
(`cfa/internal/contacts/types.go:8-63`, restricted per calling app).

Creation from the monolith is **Splitz-gated** (`cfa_service_control_experiment_id`) and, on *any*
CFA error, the monolith **silently falls back** to legacy monolith fund-account creation
(`FALLBACK_TO_API_FA_CREATION_AFTER_CFA_FAILURE`, `api/app/Models/FundAccount/Core.php:172-194`) — so a
CFA outage degrades to divergent writes rather than a visible failure. CFA's own ACL is default-deny
per gRPC method; note that `internal_app_name` inside caller-supplied `merchant_enabled_configs` can
override the authenticated origin service for internal-contact-type authorization
(`cfa/internal/fund_accounts/validate.go:50-58`).

There is **no verification state on a fund account** — `active` (bool) is the only lifecycle flag, so CFA
cannot block an unvalidated beneficiary from a payout. Dedup is app-level (SHA3-256 hash in `hash_lookup`)
with **no unique index** on `hash` — and the `hash_lookup` collection has **zero indexes at all**
(`1715709458_create_hash_lookup_collection.go:28-62`). Writes go to Mongo first, then an in-memory event → SQS
(`cfa-*-dual-write-queue`, 30 s delay) → an API-monolith worker; `FundAccountsFetchMultiple` still reads
**only** the monolith MySQL unless `EnableCFADB`/`EnableTiDB` are on.

## 8. virtual-account

**Scope correction (verified negative).** The `razorpay/virtual-account` repo is **inbound-collections
only**. A case-insensitive grep for `payout|va_to_va|sub_balance|subva|credit_transfer` across
`internal/` and `pkg/` (excluding tests) returns **zero** hits: it initiates no payouts, performs no
VA-to-VA transfer, and emits no ledger journal (its ledger client exposes only `GetJournalByID`,
`pkg/clients/ledger/service.go:66`).

- Store is **DynamoDB**, not MySQL (`Store.Choice = "dynamodb"`, prefix `dev_va_`/`prod_va_`,
  `config/default.toml:34-57`): `virtual_accounts`, `credits`, `bank_accounts`, `vpas`,
  `gateway_activations`, `order_va_mapping`, `offline_challans`. A read-only monolith-MySQL
  `[APIReaderDB]` exists but is `Enabled = false`.
- **The VA "balance" is the `credits` table** — `receive_limit`, `net_balance`, `amount_refunded`,
  mutated by atomic DynamoDB `ADD` (`internal/credit/service/service.go:206-277`). It is not
  x-balances and not ledger.
- For `type = banking` VAs the parent CA's `balance_id` is resolved from x-balances
  `GET /v1/accounts` (`accounts[0].id`, `internal/integrations/xbalance/service/service.go:64-81`);
  x-balances grants the `VirtualAccount` principal **only** `ListAccounts`.
- `TriggerVACreditedWebhook` is declared in the service descriptor but has **no server
  implementation**, consistent with there being no `virtual_account.credited` Stork payload builder
  (only `.created` / `.closed`).
- The worker binary registers **no job handlers**; `crons: []` and the workers block is commented out
  in `kube-manifests/cde/virtual-account/values.yaml`. Its only queue is the API-monolith dual-write
  `prod-api-virtual-account-dual-write-live` (`va_created|va_updated|va_receiver_added|
  va_allowed_payer_added|va_allowed_payer_deleted|va_closed`), publish failures logged and swallowed.

The VA-sourced *payout* money movement therefore lives elsewhere: payouts posts
`va_to_va_payout_initiated` / `va_to_va_payout_failed` when `banking_account.account_type ==
sub_balance` (or `IsVaToVaPayoutThroughCreditTransfer`), and **x-balances**, not the monolith, posts
`va_to_va_credit_processed` from its `sub_balance_limits` service. `fund_loading_processed` (the VA
credit leg into a shared balance) is the monolith's.

## 9. Accounting invariants (machine-checkable)

Each invariant is stated so a validator can evaluate it from ledger PostgreSQL, payouts MySQL,
x-balances MySQL and Redis. `J(e, t)` = journals with `transactor_event = e AND transactor_id = t`.

**A. Double entry**
- A1. For every journal `j`: `Σ ledger_entries.amount WHERE type='debit' AND journal_id=j` = `Σ … type='credit'`.
  Enforced at config load by `ledger/internal/journal/ledger_config/validation.go` (Σdebit=Σcredit per rule).
- A2. Every `ledger_entries.account_id` resolves to an `accounts` row whose `account_details.entities`
  contains the identifier the rule demanded (`banking_account_id` for shared roles,
  `fts_fund_account_id` + `$fts_account_type` for FTS roles, `banking_account_stmt_detail_id` for DA roles).
  A miss is `ErrAccountDiscoveryFailure` and the `journal_create` SQS message is dropped without retry
  (`internal/job/job_sqs/journal_create.go:224-285`).

**B. Shared-balance payout lifecycle** (`banking_account.account_type = 'shared'`)
- B1. `status='created'|'processing'` ⇒ exactly one `J(payout_initiated, pout_<id>)`.
- B2. `status='processed'` ⇒ exactly one `J(payout_initiated, …)` **and** exactly one
  `J(payout_processed, …)`; net MerchantBalance delta = −(amount + commission).
- B3. `status='failed'` ⇒ `J(payout_initiated)` + `J(payout_failed)`; net MerchantBalance delta = 0.
- B4. `status='reversed'` ⇒ `J(payout_initiated)` + `J(payout_processed)` + `J(payout_reversed)`;
  net MerchantBalance delta = 0; `reversals.transaction_id` = the reversal journal id.
- B5. There is never a `J(payout_processed)` without a preceding `J(payout_initiated)` **by construction of
  the caller only** — ledger itself has no sequence gate (open question OQ-6).
- B6. Re-posting the same `(transactor_event, transactor_id)` returns `ErrRecordAlreadyExistFailure`
  and leaves exactly one `journal` row (verifier V04).

**C. Balance authorization**
- C1. `accounts.balance` for the MerchantBalance account = `Σ credit − Σ debit` over its `ledger_entries`
  (materialised column must equal the derived sum) — modulo split-account merchants, where the closing
  balance is recomputed by `split_account_balance_update` (suspended in prod, one-shot in the twin).
- C2. A Journal.Create that would drive `balance + increment < min_balance` affects 0 rows and returns
  `ErrInsufficientBalanceFailure`; no `journal`, `ledger_entries` or balance change is persisted
  (single DB transaction). The payout then goes `queued` or `failed` (verifiers V09/V10).
- C3. For tenant X the MerchantBalance leg is always synchronous: no `balance_update` SQS message is
  produced for it (`updateBalanceModeAsyncConfigForX` has no `Merchant` entry).

**D. Direct-account (DA) payouts** (`account_type='direct'`)
- D1. A Direct payout in any status has **zero** ledger journals until a bank statement is matched
  (no `da_payout_initiated`, no `da_payout_failed` config exists). Verifier V08 asserts a zero delta on FAILED.
- D2. A matched **debit** statement ⇒ exactly **two** journals, `da_payout_processed` then
  `da_payout_processed_recon`, both keyed `transactor_id = pout_<id>` and carrying
  `identifiers.banking_account_stmt_detail_id = basd_<14>`; combined net effect
  Dr MerchantBalance(amount) / Cr MerchantCash(amount) plus the fee legs.
- D3. A matched **credit** statement on a reversal ⇒ `da_payout_reversed` + `da_payout_reversed_recon`.
- D4. An unmatched statement linked as `external` ⇒ exactly one `da_ext_debit` (debit row) or
  `da_ext_credit` (credit row), `transactor_id = bas_<14>`.
- D5. `payouts.transaction_id` for a Direct payout is set **only** by BAS recon and equals the matched
  `banking_account_statement.id`.

**E. Statement ↔ entity linkage**
- E1. `banking_account_statement.entity_type ∈ {payout, payout_reversal, external}`; a row with a non-empty,
  non-`external` `entity_type` is never relinked to a different `entity_id`.
- E2. `type='credit' ⇒ entity_type ∈ {payout_reversal, external}`; `type='debit' ⇒ {payout, external}`.
- E3. At most one debit **and** at most one credit statement may be linked to one payout; a third match
  is refused (`TooManyStmtsForUtr`).
- E4. Statement dedup: no two `banking_account_statement` rows share
  `{account_number, posted_date, type, bank_serial_number, amount, channel, bank_transaction_id}`.
- E5. XAS source-event dedup: `UNIQUE(event_id, entity_type)`; a re-emitted event with a **new** `event_id`
  legitimately creates a second row (this is the known duplicate vector).

**F. In-flight reservations** (payouts Redis; Direct + `in_flight_reservation_enabled`)
- F1. `GET payouts:in_flight:{mid:bal}` == `Σ amount` over `HGETALL payouts:in_flight_items:{mid:bal}`.
- F2. `payout.status='processed'` ⇒ its item state is `awaiting_balance_refresh` and the counter is unchanged.
- F3. `payout.status ∈ {failed, reversed, cancelled, rejected}` ⇒ its item is absent and the counter was
  decremented by exactly its amount.
- F4. Releasing an absent item is a no-op (`{0,0}`); the counter never goes below 0 (floors with a `drift` flag).
- F5. An `awaiting_balance_refresh` item is released **only** when
  `dispatched_at + lag < x-balances.balance.last_fetched_at` — never on wall-clock time.
- F6. Without the reconciliation heartbeat key the gate fails **closed**
  (`queued_reason=low_balance`, `INFLIGHT_GATE_DECISION reason=heartbeat_missing`).
- F7. Every `live` item has a payout in `(created, initiated, batch_submitted, create_request_submitted)`;
  every `awaiting_balance_refresh` item has `status='processed'`.
- F8. `x-balances.balance.balance` is never changed by any payout event — it changes only when
  `last_fetched_at` changes.

**G. Beneficiaries**
- G1. Creating the same fund account twice for a merchant returns the existing id with `is_created:false`
  (hash dedup) — but there is **no unique index**, so a concurrent double-create is possible and a
  migration exists purely to report such duplicates.
- G2. A card fund account never stores a PAN: `card.vault_token` and `global_fingerprint` present,
  raw number absent.
- G3. `bank_account.account_number` never changes across updates.

## 10. Open questions

1. Who posts `da_payout_processed`/`_recon` in production for PS-owned payouts when Splitz
   `payout_service_txn_recon` is on: the PS emitter is commented out (`payouts/…/core.go:7284,7356`)
   and the monolith defers to PS — the code says *nobody*.
2. Which RBL statement fetcher is actually live (PS `rbl_banking_account_statement`, monolith
   `RblBankingAccountStatement`, or XAS) and the FastCron cadences for the three `fetch/initiate`
   routes plus x-balances `/v1/cron/balance/{channel}/fetch`. Every kube `crons:` list in this lane is
   empty; XAS's `x-account-statements-cron-fetch-statement` (`0 6,18 * * *`) is commented out.
3. The consumer of the ledger `journal-created` SNS topic (monolith balance mirror) was not read;
   the twin has no subscriber, so monolith-side balance mirroring is unmodelled.
4. `ledger` `outbox_relay`, `idempotency_scheduler` and `idempotency_partition_manager` have no
   deployment templates under `kube-manifests/templates/ledger` — are they run at all? Only 4 CronJobs
   exist and all are suspended, so the production cadence of the other 18 scheduler commands is unknown.
5. Is `merchantBalanceAsyncUpdateExperiment` wired into `updateBalanceInLedgerEntry`? If it ever turns
   on, MerchantBalance debits stop being synchronous authorization and invariant C3 breaks.
6. Out-of-order journals (`payout_processed` before `payout_initiated`): no sequence gate found in
   ledger; whether VendorPayable min-balance/negative checks prevent or merely mask it is unverified.
7. The x-balances twin worker's `WORKER_NAME` (one of 9: six `balance_fetch_<channel>_worker`,
   `payout_events_worker`, `account_activation_events_worker`, `pocworker`) is set in
   `generated/xbalances/xbalances.env` and was not inspected; combined with `[Queue] Driver = inmemory`
   and the `arena-` vs `stage-` prefix mismatch (DEV-024/DEV-081) the fetch → refresh-event → release
   chain is severed in the twin.
8. **Resolved this pass:** banking-accounts is the publisher of both
   `{env}-x-balances-account-activation-event` and `{env}-x-account-statements-account-activation-event`
   (`internal/applications/onboarding/balance_creation.go:113,142`). Still open: banking-accounts'
   datalake Kafka topic is `events.banking-account.v0.live` but its consumer was not traced.
9. **"MAR" does not exist** in `x-balances`, `virtual-account`, `banking-accounts` or `proto/x` at
   these SHAs. If the brief meant a specific RPC it is either `AccountsAPI/ListAccounts`
   (source-account selection) or `AccountsAPI/CreateBalance` + the `account_activation` event
   (merchant-account registration) — worth confirming with whoever coined the term.
10. `recon POST /workflow_file` is a **public, auth-bypassed** route while its edge twin
    `POST /workflow_file_upload` requires a passport JWT. Whether the public path is reachable from
    outside the cluster is an ingress/edge question no repository can answer.
11. ART has **no endpoint allow-list**: `end_point` and `method` in the Kafka message are free-form
    runtime data from `workflow_configs`. Containment rests entirely on the 3-entry
    `ALLOWED_EXTERNAL_SERVICES` plus each downstream credential ACL (FTS grants ART only the
    `v1/attempts` group; the monolith grants the `recon` app 13 named routes). The configured rows
    themselves are not derivable from source.
12. CFA fund-account creation from the monolith is Splitz-gated and **silently falls back** to legacy
    monolith creation on any CFA error — the live experiment value decides whether Mongo or MySQL is
    authoritative per merchant, and it is in no repo.
13. CFA `hash_lookup` has **zero indexes** and `contacts`/`fund_accounts` `hash` indexes are explicitly
    non-unique, so beneficiary idempotency is read-then-write only. The concurrency window is real; the
    production duplicate rate is unknown (a dedicated `identify_duplicates` migration exists).
14. `virtual-account`'s Ledger integration reads journals only; the VA credit-accounting producers
    (`fund_loading_processed`, and the monolith side of VA credits) live in the API monolith and were
    not traced here.
15. x-balances `sub_balances` / `sub_balance_limits` DDL exists only in the unwired `queries.sql`;
    the production shape is unknown.
