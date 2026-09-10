# Lane 07 — Ledger, x-balances, CFA, banking-accounts (money authority + account/beneficiary data)

## Scope and sources read

Repos (pristine shallow clones under `$REPOS`, current master unless noted):
`ledger` (Go, Twirp+gRPC, Postgres), `ledger-sdk` (Go, HTTP client), `payouts` (Go, MySQL),
`fts` (Go, MySQL), `cfa` (Go, gRPC+REST, MongoDB), `x-balances` (Go, gRPC+REST, MySQL),
`banking-accounts` (Go, Gin, MySQL), `virtual-account` (cross-ref only), `proto`/`rpc` (cross-ref only),
`kube-manifests` (CronJob manifests only).

Files opened directly (line numbers cited inline): `ledger/internal/account_discovery/core.go`,
`ledger/internal/journal/ledger_config/seed_data/{shared_account_x.go,direct_account_x.go}`,
`ledger/internal/account/seed_data/shared_account_x.go`, `ledger/internal/account/onboarding/pool_account_onboarding/core.go`,
`ledger/internal/journal/ledger_config/server.go`, `ledger/internal/common/constant.go`,
`ledger/internal/account/account_detail/account_category/category.go`,
`ledger/internal/database/rx_migrations/20210810170948_create_ledger_config.go`,
`ledger-sdk/common/constant.go`, `ledger-sdk/dto/{api.go,struct.go}`,
`payouts/pkg/ledger/{ledger_journal_create.go,client.go}`,
`payouts/internal/app/payouts/{core.go,fts_transfer_status_webhook.go,status.go,model.go,asyncFailureHandlingHelper.go,processor/payoutViaLedgerService.go}`,
`payouts/internal/app/reversals/{core.go,reversalViaLedgerService.go}`,
`payouts/internal/app/bankingAccount/model.go`, `payouts/pkg/bankingAccountService/fetch_merchant_details.go`,
`payouts/internal/app/common/appConstants/entity.go`,
`fts/internal/account/{source_account.go,service.go,ledger_request.go}`, `fts/internal/tasks/ledger_account_create.go`,
`cfa/internal/fund_accounts/model.go`, `cfa/internal/contacts/model.go`, `cfa/internal/hashlookup/model.go`,
`kube-manifests/templates/ledger/templates/{split-account-balance-update-cronjob-live.yaml,split-account-balance-update-cronjob-pg-live.yaml,verify-warm-storage-cronjob-live.yaml,verify-warm-storage-cronjob-test.yaml}`.

Twin: `ENV2_COMPOSE/seeds/s4/{ledger.sql,ledger_accounts_via_api.sh,xbalances.sql,cfa.js,payouts.sql,fts.sql}`,
`ENV2_COMPOSE/config/templates/base/{ledger,xbalances,cfa}/{default,arena}.toml`, `ENV2_COMPOSE/scripts/up.sh`,
`ENV2_COMPOSE/substitutes/bankingaccounts-stub/server.py`, `ENV2_COMPOSE/verifier/verifiers/test_v0{4,5,6,7,8}_*.py`,
`ENV2_COMPOSE/docker-compose.yml`, `reports/raw-findings/{04_ledger.md,05_cfa.md,06_balances_banking_accounts.md,24_shared_libs_proto.md,31_env2_bringup_notes.md}`,
`reports/raw-findings/env2-logs/{golden-run-final.log,verifier-run28.log}`.

Prior reports were treated as hypotheses; every load-bearing claim re-derived below carries its own
`path:line` citation, independent of the prior report's citation of the same fact.

---

## Production behaviour

### 1. Ledger API surface PS uses

**`JournalAPI/Create`** (Twirp-over-JSON via `ledger-sdk`, not gRPC/protobuf-binary) —
`ledger-sdk/common/constant.go:599` `EndpointJournalCreate = "/twirp/rzp.ledger.journal.v1.JournalAPI/Create"`.
Full URL = `config.APIConfig.Hostname + Endpoint`, always `POST`. CONFIRMED.

Request body (`ledger-sdk/dto/struct.go:630-643`, field list, all required unless noted):

| Field | Type | Notes |
|---|---|---|
| `merchant_id` | string | |
| `currency` | string | ISO code, e.g. `INR` |
| `transactor_id` | string | signed payout id (`payout.GetSignedID()`), or `reversal.GetLedgerSignedID()` for failed/reversed |
| `transactor_event` | string | e.g. `payout_initiated`, `payout_processed` |
| `transaction_date` | int64 (unix) | payout creation time, or `time.Now()` for processed/charge-collections-debit-processed (`payouts/pkg/ledger/ledger_journal_create.go:183-186`) |
| `notes` | map[string]interface{} | `{balance_id: payout.GetBalanceID()}` |
| `api_transaction_id` | string | optional, unused by PS |
| `additional_params` | map[string]interface{} | `{fee_accounting: "reward"}` when reward-fee payout (`:172-177`) |
| `money_params` | map[string]string | `{amount, base_amount, commission, tax}` — all decimal-string paise (`:171-176`) |
| `identifiers` | map[string]interface{} | `{banking_account_id: bankingAccount.GetSignedID()}` always; **+ `{fts_fund_account_id, fts_account_type}` added only for `payout_processed`/`payout_reversed`/charge-collections processed/reversed** (`:210-218`) |
| `dynamic_money_params` | array | unused by PS for standard payouts |
| `sync_retry_attempts` | int | `provider.GetConfig().LedgerClient.HttpRetryAttempts` |

`identifiers.fts_account_type` is **lower-cased** by PS: `strings.ToLower(extraInfo.FtsAccountType)` (`ledger_journal_create.go:217`). CONFIRMED.

Other endpoints (`ledger-sdk/common/constant.go:598-613`), all POST, all Twirp-style JSON:
`JournalAPI/CreateInBulk`, `JournalAPI/FetchById`, `JournalAPI/FetchByTransactor`,
`AccountAPI/CreateOnEvent`, `AccountAPI/UpdateByEntitiesAndMerchantID`, `AccountAPI/FetchByMerchantID`,
`AccountAPI/FetchByEntitiesAndMerchantID`, `AccountAPI/FetchInBulkByEntitiesAndMerchantID`,
`AccountAPI/Deactivate`, `AccountAPI/Activate`, `AccountAPI/Archive`,
`DashboardAPI/FetchPGMerchantAccounts`, `DashboardAPI/FetchPGMerchantBalances`.
**PS itself calls none of the `AccountAPI/*` endpoints** — no `AccountCreateOnEvent`/`CreateOnEvent` reference anywhere in `payouts/` (grep, zero hits outside `.agents/`). CONFIRMED (negative grep).
**FTS** is a confirmed caller of `AccountAPI/CreateOnEvent` for the FTS-side nodal/current accounts — see §3. `LedgerConfigAPI/CreateInBulk` (`rzp.ledger.ledger_config.v1.LedgerConfigAPI/CreateInBulk`) is called with body `{"ledger_config_data_identifier": "shared_account_x"|"direct_account_x"|"account_pg"}` (`ledger/internal/journal/ledger_config/server.go:183-187`); this is what the twin's `scripts/up.sh:138-144` calls post-core.

**Auth**: HTTP Basic (`req.SetBasicAuth(a.Username, a.Password)`, `ledger-sdk/dto/api.go` `APIClient.Run`), header `Ledger-Tenant: X` always sent (`common.HeaderLedgerTenant`). Payouts config (`payouts/config/default.toml`, per `24_shared_libs_proto.md:684-703`, re-verified): `[ledger] host, timeout=200ms, httpretryattempts=3, [ledger.auth] username="payouts_key"`, Hystrix-style resiliency (`maxconcurrentrequests=100, requestvolumethreshold=20, circuitbreakersleepwindow=5000ms, errorpercentthreshold=50, circuitbreakertimeout=10000ms`). Ledger-side, `[auth.payouts]`/`[auth.fts]`/`[auth.xbalances]` are the matching inbound Basic-Auth credential blocks in `ledger/config/default.toml` (usernames only in this list; passwords are env/Credstash-injected — not derivable from this repo). CONFIRMED (config file + `04_ledger.md:41,65`).

**Async retry path on ledger failure** — CONFIRMED, traced end to end:
- `payout_initiated`: synchronous, in the create request path. On failure, `errorParsingForCreateTransactionViaLedger` (`payoutViaLedgerService.go:235-271`) classifies the Twirp error (`IsInsufficientBalanceErrorMsg` substring match on `BAD_REQUEST_INSUFFICIENT_BALANCE`, or `IsNonRetryableErrorMsg`) and the payout is left in `ledger_response_awaited` state (set just before the call, `payoutStateProcessingBeforeLedgerServiceCall`, `payoutViaLedgerService.go:198-233`). Recovery is `AsyncProcessingLedgerInitiatedEventFailure` (`asyncFailureHandlingHelper.go:190-245`), driven by SQS job type on the **`payout_create_failure_handling`** worker: it calls `checkIfLedgerAlreadyCreatedOrNot` (Ledger `FetchByTransactor`) — if a journal already exists it recovers the transaction id and continues (`ExecutePostCreate`); if not, it re-drives the whole create flow.
- `payout_processed` (success path, non-failure): **is itself asynchronous** — `ledgerProcessingForPayoutProcessed` (`payouts/internal/app/payouts/core.go:2532-2598`) is called from the FTS-processed-status-webhook handler (`fts_transfer_status_webhook.go:851`), stores `{status, fund_account_id, account_type}` from the webhook into `payout_meta_temporary` (key `FtsInfoMeta`), and pushes job `job.LedgerProcessedEventFailureType` ("`ledger_processed_event`") onto the **`payout_update_failure_handling`** queue — comment: *"payout_processed event does not require sync processing so we push it into the same queue... not exactly a failure"* (`core.go:2579-2597`). **Gated to Shared banking accounts only**: `if !strings.EqualFold(bankAcc.GetAccountType(), appConstants.Shared) { return nil }` (`core.go:2575`) — Direct (CA) merchants never get a `payout_processed`/`payout_reversed` call into this path.
- `payout_failed`/`payout_reversed`: synchronous attempt inside `transactionProcessingForPayoutReversal` (`reversals/core.go:281-330`), same `Direct`-skip guard (`reversals/core.go:296-298`: *"skip ledger processing for CA payouts // will be done after BAS linking"*); on failure the same info is persisted to `payout_meta_temporary` and retried via `payout_update_failure_handling` (`asyncFailureHandlingHelper.go:582-666`, job types `LedgerReversedEventFailureType`/credits-reversal/free-payout-reversal).
- `ledger_response_awaited` resolves either (a) forward, once `Journal.Create` returns 2xx and `payout.OnEvent(StateCreated)` fires (`payoutViaLedgerService.go:95-105`), or (b) via the `payout_create_failure_handling` retry job re-checking `FetchByTransactor` and resuming.

Both queues are real production SQS queues (arena runs them as `payouts-worker-payout-create-failure-handling`/`payouts-worker-payout-update-failure-handling`, confirmed in the registered-worker-name list, `reports/raw-findings/31_env2_bringup_notes.md:8`).

### 2. Ledger config model for tenant X

`ledger_config` table (`ledger/internal/database/rx_migrations/20210810170948_create_ledger_config.go:14-35`), CONFIRMED DDL:

```sql
CREATE TABLE ledger_config (
  id CHAR(14) PRIMARY KEY,
  tenant VARCHAR(50) NOT NULL,
  transactor_event_name VARCHAR(255) NOT NULL,
  rule jsonb NOT NULL,
  config jsonb NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  deleted_at INTEGER
);
CREATE INDEX ON ledger_config USING gin (rule jsonb_path_ops);
CREATE UNIQUE INDEX ON ledger_config(tenant, transactor_event_name) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX ON ledger_config(tenant, rule) WHERE deleted_at IS NULL;
```

Production config **is in the repo**, as Go seed data (not a SQL export), loaded via `LedgerConfigAPI/CreateInBulk` with `ledger_config_data_identifier ∈ {shared_account_x, direct_account_x, account_pg}` (`ledger/internal/journal/ledger_config/server.go:183-187`). Source: `ledger/internal/journal/ledger_config/seed_data/shared_account_x.go` (X, shared/Lite balances, 1184 lines, ~34 configs incl. legacy) and `direct_account_x.go` (X, Direct/CA, 864 lines, ~23 configs). This is the same mechanism the twin now uses (`ENV2_COMPOSE/scripts/up.sh:138-144`); no separate DB export is needed for the *rule shape* — only real per-tenant/per-merchant **account rows** (§3/§4) are what a raw export would add value for.

Payout event table, **shared_account_x** (`shared_account_x.go`, CONFIRMED verbatim, tenant=`X`):

| Transactor event (`rule.transactor_event`) | Config ID | Entries (account / entry_type / formula) |
|---|---|---|
| `payout_initiated` | `XPayoutInitiatedV2` (:16) | debit MerchantBalance `amount+commission`; credit VendorPayable `amount`; credit CommissionIncome `commission-tax`; credit OutputGST `tax` (lines ~284-311) |
| `payout_processed` | `XPayoutProcessedV2` (:17) | debit VendorPayable `amount`; credit FtsPayable `amount` (lines ~315-332) |
| `payout_reversed` | `XPayoutReversedV2` (:19) | debit FtsReceivable `amount`; credit MerchantBalance `amount+commission`; debit CommissionIncome `commission-tax`; debit OutputGST `tax` (lines ~335-362) |
| `payout_failed` | `XPayoutFailedV2` (:18) | debit VendorPayable `amount`; credit MerchantBalance `amount+commission`; debit CommissionIncome `commission-tax`; debit OutputGST `tax` (lines ~365-392) |
| `fav_initiated`/`fav_processed`/`fav_reversed`/`fav_failed` | `XFav*V2` (:20-23) | commission-only variants of the above (FTS-account discovery config reused) |
| `fund_loading_processed` | `XFundLoadingProcessedV2` (:24) | credits MerchantBalance from FTS receivable |
| `positive_adjustment_processed`/`negative_adjustment_processed` | `XPositive/NegativeAdjustmentProcessedV2` (:25-26) | debit/credit `AdjustmentLiabilityAccount` (identifier-less, discovered as a plain sub-account with `fund_account_type=adjustment`) vs credit/debit MerchantBalance — this is the mechanism the twin uses to top merchants up (`31_env2_bringup_notes.md:116`) |
| reward/inter-account/VA-to-VA/MY payout variants | `XReward*V2`, `XInterAccountPayout*V2`, `XVaToVa*V2`, `XMYPayoutInitiatedV2` (:27-41) | parallel patterns, not re-verified line-by-line |
| Legacy back-compat | `*LegacyV2` (:44-51) | pre-migration data shape |

Account discovery identifiers used across these entries (`shared_account_x.go:54-130`):
`MerchantIdentifier = {banking_account_id: "$banking_account_id"}`; `FtsIdentifier = {fts_fund_account_id: "$fts_fund_account_id"}`; `TerminalIdentifier = {terminal_id: "$terminal_id"}`. `MerchantBalanceAccount`/`VendorPayableAccount`/`CommissionIncomeAccount`/`OutputGSTAccount` all key off `MerchantIdentifier` with a **fixed** `fund_account_type` (`merchant`/`vendor`/`merchant`/`gst` respectively) and a **fixed** `account_type` (`payable`/`payable`/`cash`/`payable`). `FtsPayableAccount`/`FtsReceivableAccount` key off `FtsIdentifier` with a **fixed** `account_type` (`payable`/`receivable`) but a **variable** `fund_account_type = "$fts_account_type"` (`:98-109`) — this variable is the crux of §3 below.

`direct_account_x.go` (Direct/CA) mirrors this with its own identifier `DAMerchantIdentifier = {banking_account_stmt_detail_id: "$..."}` (`:48-50`) — **Direct's `payout_processed`/`payout_reversed` (`XDAPayoutProcessed`/`XDAPayoutReversed`, `:220-260`) debit/credit `DAMerchantBalance`/`DAVendorPayable`/`DACommissionReceivable`/`DACommissionIncome`/`DAOutputGST` — none of these reference `FtsPayableAccount`/`FtsReceivableAccount` at all.** Direct-account ledger postings for processed/reversed are keyed by a **bank-statement reconciliation row id** (`banking_account_stmt_detail_id`), not by the FTS transfer webhook — consistent with PS's own `Direct`-skip guard (§1) and the `"will be done after BAS linking"` comment.

### 3. Account discovery — algorithm and the twin's `payout_processed` failure

Algorithm, `ledger/internal/account_discovery/core.go:51-181` `Core.GetAccountByConfig`, CONFIRMED:
1. If `adc.FundAccountType` is a `$variable` (`common.IsVariable`), resolve it from the request map (`replaceVariable`, strips leading `$`, `request[field]`, error if absent) (`:56-70`).
2. For each `(key, value)` in `adc.Identifiers`: if `value` is a `$variable`, resolve from `request[key]` (with a special-case default `online` for `$channel_type`); the **resolved value becomes `identifiers[key]`** (`:73-113`).
3. Build `entities = {account_type: [adc.AccountType], fund_account_type: [resolvedFundAccountType]} ∪ identifiers` (`:114-121`) — `account_type` here is a **static config value** (`payable`/`receivable`/`cash`), never a variable, for every config seen in this pass.
4. Query `account_details` filtered on `entities` (exact JSONB match), `account_type=SubAccount` (i.e. row has a non-null `parent_account_id`), `tenant`, `currency`, `account_category=adc.AccountCategory`, and `merchant_id` **only if** `merchant_id` was itself one of `adc.Identifiers` (`:123-140`) — for `FtsPayableAccount`/`FtsReceivableAccount` it is not, so the fetch is tenant/currency/category/entities-scoped only, not merchant-scoped.
5. Zero matches → `ACCOUNT_DISCOVERY_ACCOUNT_NOT_FOUND` (`:163-170`); >1 match → `ACCOUNT_DISCOVERY_MULTIPLE_ACCOUNTS_FOUND` (`:172-179`); either aborts the whole `Journal.Create` (all entries of one journal are created/rejected atomically — CONFIRMED by `04_ledger.md:41-43`, `TakeMutexLock`+single DB txn).

**Where `$fts_account_type` comes from, traced fully to source (CONFIRMED, no gaps):**
- FTS's `source_accounts.bank_account_type` column has exactly 3 allowed values: `CURRENT`, `NODAL`, `CORP_CARD` (`fts/internal/account/source_account.go:99-101`, `validation.In(...)` at `:182`).
- On a transfer, FTS includes this literal value as `bank_account_type` in the transfer-status webhook payload (`fts/internal/transfer/service.go:1338-1341,1351`).
- PS reads it into `StatusUpdateRequest.FtsAccountType = transferStatusWebhookRequest.BankAccountType` (`payouts/internal/app/payouts/fts_transfer_status_webhook.go:520`), stores it verbatim in `payout_meta_temporary` (`core.go:2538`), and on the async retry lower-cases it into the ledger request: `Identifiers[fts_account_type] = strings.ToLower(extraInfo.FtsAccountType)` (`pkg/ledger/ledger_journal_create.go:217`) — i.e. the value ledger receives is **`current`, `nodal`, or `corp_card`**, never `pool`/`direct`/`shared`.
- **Production creates the matching `account_details` row with exactly this same lower-cased string**, independently: FTS's own `AccountAPI/CreateOnEvent` caller, `fts/internal/account/service.go:4751-4800` `CreateBulkLedgerAccounts`, builds the request via `getLedgerAccountsRequest(name, merchantID, fundAccountIDStr, strings.ToLower(bankAccountType))` (`:4773-4777`) — i.e. FTS itself lower-cases `bank_account_type` before sending it to ledger as the onboarding event name suffix. On ledger's side, `ledger/internal/account/seed_data/shared_account_x.go:199-231` shows **two separate parent-account templates**, each gated on a distinct onboarding event: `RazorpayNodalReceivable`/`RazorpayNodalPayable` (`OnboardingEvents: [..., EventNodalPoolAccountOnboarding]`, `FundAccountType: FundAccountTypeNodal`) vs `RazorpayCurrentReceivable`/`RazorpayCurrentPayable` (`OnboardingEvents: [..., EventCurrentPoolAccountOnboarding]`, `FundAccountType: FundAccountTypeCurrent`). So a source account whose `bank_account_type=CURRENT` is onboarded via event `current_pool_account_onboarding` and gets `fund_account_type=current` stamped on its `account_details` row — **never `nodal`**.

**Why the twin's `payout_processed`/`payout_reversed` account discovery fails for the Direct (M2) merchant — CONFIRMED, root cause fully nailed:**
`ENV2_COMPOSE/seeds/s4/fts.sql:29,55` seeds M2's dedicated source account (`fund_account_id=900002`) with `bank_account_type='CURRENT'`. Any payout routed through it will therefore have PS send `identifiers.fts_account_type = "current"` to ledger. But `ENV2_COMPOSE/seeds/s4/ledger.sql`'s section 3 seeds the matching `account_details` rows (`ARENAM2NODDT01`/`ARENAM2NODDT02`, the FtsReceivable/FtsPayable pair for `fts_fund_account_id=900002`) with `entities: {..., "fund_account_type":["nodal"]}` — copy-pasted from the shared-pool pair instead of using `"current"`. `fund_account_type=["current"]` (what PS sends) never matches the seeded `["nodal"]` ⇒ `GetAccountByConfig` returns zero rows ⇒ `ACCOUNT_DISCOVERY_ACCOUNT_NOT_FOUND` ⇒ `JOURNAL_CREATE_FAILED account_discovery_failure` for the whole journal (all-or-nothing per §above). The same file's `ledger_accounts_via_api.sh:53-62` (the "recommended" API-based seeding path) has the identical bug at the *source*: it calls the `nodal_pool_account_onboarding` event for M2 instead of `current_pool_account_onboarding`, so even switching to the API-based seeding path reproduces the same wrong `fund_account_type=nodal` stamp — this is a seed-authoring bug, not a raw-SQL-vs-API artifact.

Additionally, **`ledger.sql`'s parent-account section (section 1) is missing 2 of the 8 real production parent accounts**: `ledger/internal/account/seed_data/shared_account_x.go:216-231` defines `Razorpay Current Receivable Account` and `Razorpay Current Payable Account` as real, separate parents (fund_account_type=`current`) alongside the Nodal pair the twin does seed (`ARENAPRACC0005`/`0006`) — the twin has no equivalent for the Current pair at all, so even a corrected M2 sub-account would currently have to be parented under the wrong (Nodal) parent, or under no parent.

Corrected seed rows (SQL, additive — add 2 parents + fix M2's fund_account_type + reparent):

```sql
-- missing parents
INSERT INTO accounts (id, merchant_id, status, balance, min_balance, negative_balance, created_at, updated_at, deleted_at) VALUES
  ('ARENAPRACC0009', 'Gh0YgCurRecv1', 'ACTIVATED', 0, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL), -- Razorpay Current Receivable (parent)
  ('ARENAPRACC0010', 'Gh0YgCurPay01', 'ACTIVATED', 0, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL)  -- Razorpay Current Payable (parent)
ON CONFLICT DO NOTHING;
INSERT INTO account_details (id, account_id, account_name, merchant_id, parent_account_id, currency, account_category, business_category, entities, description, created_at, updated_at, deleted_at, tenant, use_split_accounts) VALUES
  ('ARENAPRDT00009', 'ARENAPRACC0009', 'Razorpay Current Receivable', 'Gh0YgCurRecv1', NULL, 'INR', 'asset',     'real', '{"fund_account_type":["current"],"account_type":["receivable"]}', 'Parent account for Razorpay current receivable accounts', extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0),
  ('ARENAPRDT00010','ARENAPRACC0010', 'Razorpay Current Payable',    'Gh0YgCurPay01', NULL, 'INR', 'liability', 'real', '{"fund_account_type":["current"],"account_type":["payable"]}',    'Parent account for Razorpay current payable accounts',    extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0)
ON CONFLICT DO NOTHING;

-- fix M2 (fts_fund_account_id=900002): reparent + fund_account_type nodal -> current
UPDATE account_details SET parent_account_id = 'ARENAPRACC0009',
  entities = '{"account_type":["receivable"],"fts_fund_account_id":["900002"],"fund_account_type":["current"]}'
  WHERE id = 'ARENAM2NODDT01';
UPDATE account_details SET parent_account_id = 'ARENAPRACC0010',
  entities = '{"account_type":["payable"],"fts_fund_account_id":["900002"],"fund_account_type":["current"]}'
  WHERE id = 'ARENAM2NODDT02';
```

And in `ledger_accounts_via_api.sh`, the M2 call's event name should be `current_pool_account_onboarding`, not `nodal_pool_account_onboarding`.

**Caveat (UNKNOWN, flagged not overclaimed):** the twin's `test_v06_ledger_accounting_processed.py` failure (`"no payout_processed journal found"`, `env2-logs/golden-run-final.log:110-111`) exercises **`merchant_m1`**, the Shared/pool merchant (`fts_fund_account_id=900001`, `bank_account_type=NODAL` per `fts.sql:28`), whose `fund_account_type=["nodal"]` seed **does** match what PS would send (`"nodal"`) by the same static analysis above. So the fix above explains and corrects a real, confirmed class of bug (and is very likely why any *Direct*-merchant `payout_processed`/`payout_reversed`/FAV journal would fail today), but it does not, by itself, fully explain M1's specific test failure — that needs the literal ledger-api error body from a live rerun (see Cannot-derive list) to rule in/out a second, distinct cause (candidates: the async `payout_update_failure_handling` worker/queue not actually delivering the job in the arena; or a currency/merchant-id mismatch not visible from static config reading).

### 4. Account hierarchy for a RazorpayX merchant

Per-merchant/per-banking-account ledger sub-accounts (all children of the 8 fixed parents in `ledger/internal/account/seed_data/shared_account_x.go`, all keyed by `banking_account_id` for Shared, `fts_fund_account_id` for the FTS-side accounts):

| Account (Shared/Lite) | Parent | Keyed by | account_type | fund_account_type |
|---|---|---|---|---|
| MerchantBalance | Merchant Balance Account | `banking_account_id` | payable | merchant |
| VendorPayable | Vendor Payable Account | `banking_account_id` | payable | vendor |
| CommissionIncome | Commission Income Account | `banking_account_id` | cash | merchant |
| OutputGST | Output GST Account | `banking_account_id` | payable | gst |
| FtsReceivable (nodal) | Razorpay Nodal Receivable | `fts_fund_account_id` | receivable | nodal |
| FtsPayable (nodal) | Razorpay Nodal Payable | `fts_fund_account_id` | payable | nodal |
| FtsReceivable (current) | Razorpay Current Receivable | `fts_fund_account_id` | receivable | current |
| FtsPayable (current) | Razorpay Current Payable | `fts_fund_account_id` | payable | current |
| MerchantReward | Merchant Reward Account | `merchant_id` only (DA variant keys same way; Shared reward account per-merchant, cross-banking-account) | payable | reward |

Direct/CA analog (`direct_account_x.go`) mirrors MerchantBalance/VendorPayable/CommissionIncome/OutputGST as `DAMerchantBalance`/`DAVendorPayable`/`DACommissionIncome`/`DAOutputGST`, keyed by `banking_account_stmt_detail_id` instead of `banking_account_id`, plus a `DACommissionReceivable` asset account not present on the Shared side. It does **not** have its own FtsPayable/Receivable — Direct settlement accounting is done against the RBL/ICICI/etc. bank statement row, not the FTS nodal ledger.

**Who calls `AccountAPI/Create*` in production:**
- FTS-side accounts (nodal/current receivable+payable): **FTS**, via `fts/internal/tasks/ledger_account_create.go` → `account.Service.CreateBulkLedgerAccounts` → `AccountAPI/CreateOnEvent`, event name `<lower(bank_account_type)>_pool_account_onboarding`. CONFIRMED.
- Merchant-side accounts (MerchantBalance/VendorPayable/CommissionIncome/OutputGST), onboarding events `shared_account_onboarding`/`shared_merchant_onboarding` (per `shared_account_x.go:142` etc.) and the Direct equivalents (`direct_account_onboarding`/`direct_merchant_onboarding`, `:278,295`): **no caller found in any of `payouts/`, `cfa/`, `x-balances/`, `fts/`, `banking-accounts/`** (grep for `shared_merchant_onboarding`/`AccountCreateOnEvent` across all five repos returns zero hits outside `ledger` itself). This strongly implies the caller is the **API monolith**, on banking-account activation — out of this lane's readable repos. UNKNOWN/not derivable here; see Cannot-derive list.

**Balance cache semantics.** `accounts.balance` is a materialized column, updated by an atomic conditional `UPDATE accounts SET balance = balance + ? WHERE balance + ? >= min_balance` inside the *same DB transaction* as journal/ledger-entry creation for MerchantBalance (X) — confirmed by `04_ledger.md:43` (`internal/account/repo.go:421`), re-derived here as consistent with `test_v05` passing with a real balance decrease. This is why raw `UPDATE accounts SET balance=...` SQL is invisible to any code path that reads via `AccountAPI/FetchByMerchantID` cache or that computes balance from `ledger_entries` — it bypasses the transactional/audit-trail path entirely; the twin's own comment (`31_env2_bringup_notes.md:116`) independently rediscovered this and switched top-ups to a real `positive_adjustment_processed` journal for exactly this reason. No separate Redis/TTL cache layer was found in `ledger/` for the `accounts.balance` column itself — VendorPayable/Commission/Nodal/Current/GST updates are async via the SQS `balance_update` job (`04_ledger.md:44,101`), so a freshly-created journal's non-MerchantBalance ledger-entry balances can lag by the SQS-processing delay, not a TTL.

### 5. Journal dedupe/uniqueness, workers, topics

No DB unique index on `(transactor_id, transactor_event)` — enforced only by app-layer check-then-insert (`ValidateJournalExist`) guarded by a Redis-backed distributed mutex (`TakeMutexLock`/`ReleaseMutexLock`) — CONFIRMED, `04_ledger.md:42,110` (`internal/journal/validation.go`, `internal/journal/core.go:2249`); re-checked, no unique index exists in `internal/database/rx_migrations/20201001011143_create_journal.go` beyond plain btree indexes. `ledger_config` itself has two real unique partial indexes: `(tenant, transactor_event_name)` and `(tenant, rule)`, both `WHERE deleted_at IS NULL` (`20210810170948_create_ledger_config.go`, this pass).

SQS/SNS (topic composed as `topicArn:topicName`, `31_env2_bringup_notes.md:98,117`, i.e. the config value for a pubsub topic is literally the ARN prefix and the topic name joined with `:`):
- Consumed SQS (internal, self-enqueued): `journal_create`, `account_create`, `balance_update`, `ledger_entry_details_create` (`04_ledger.md:78,122`).
- Produced SNS: `journal-created`, whitelisted per `(tenant, transactor_event)` — for X: `payout_initiated`, `payout_failed`, `payout_reversed`, FAV events, fund-loading, adjustment events, inter-account/VA-to-VA — **excludes `payout_processed`** (`04_ledger.md:54,123`, `internal/journal/config.go:87-99`).
- Consumed Kafka: PG-tenant CDC only (Maxwell/Debezium), and X's legacy `makeshift` dual-write queue — neither carries payouts traffic for X (`04_ledger.md:56,75-77,120-121`).

CronJobs (`kube-manifests/templates/ledger/templates/*.yaml`, this pass) — **ledger has exactly 2 real k8s CronJobs and both are suspended in production** (`suspend: true` on all 4 manifests: `-live`/`-pg-live`/`-test` variants):
| CronJob | Schedule | concurrencyPolicy | Purpose |
|---|---|---|---|
| `split-account-balance-update-cronjob-live`/`-pg-live` | every 10 min (`{{ .Values.split_account_balance_update_cronjob_schedule }}`) | Allow, parallelism 2 | recompute closing balance for high-TPS split-account merchants (`04_ledger.md:62`) |
| `verify-warm-storage-cronjob-live`/`-test` | `0 * * * *` (hourly) | Forbid | storage/warm-tier verification |

Scheduler binary (`cmd/scheduler`, non-k8s-CronJob, presumably an in-process ticker) additionally runs: `balance_update_script`, `update_ledger_entry_balance`, `update_merchant_balance_subaccounts`, `journal_created_sns_publish`, `control_tables_sync_mirror`, `penny_expense_account_update`, `verify_warm_storage`, `check_sqs_connection`, `create_merchant_subaccounts`, `create_merchant_split_accounts`, `offboard_merchant_split_accounts`, `account_detail_channel_type_update`, `ledger_entries_channel_type_update` (dir listing, `ledger/internal/scheduled_jobs/*.go`, this pass — cadence for these is env config, not found in-repo).

### 6. x-balances — balance authority

Decision table (source-cited; largely reconfirms `06_balances_banking_accounts.md`, independently re-derived from `internal/enrichment/enricher.go`, `internal/balances/service.go`, and payouts' `internal/app/balance/core.go`):

| Account type | Selection (which account for a payout) | Balance-**number** authority | Balance-**sufficiency check** authority |
|---|---|---|---|
| Direct/current-account | x-balances `ListAccounts` (`payouts/internal/app/balance/core.go:49-90 FetchActiveAccountsFromXBalance`) | x-balances' own polled `balance` column (Mozart bank-poll workers), **falls back** to API-monolith `balance` table read via `APIStore` 2nd DB conn when missing | ledger, via synchronous `payout_initiated` MerchantBalance-equivalent debit — **for Direct this is the DAMerchantBalance account, keyed by bank-statement reconciliation, not FTS** |
| Shared/pool (Lite VA) | same x-balances `ListAccounts` | **overwritten live from Ledger** (`enrichment/enricher.go:35-98`, `AccountAPI/FetchByMerchantID`) — x-balances' own stored number is not trusted | Ledger, `Journal.Create` for `payout_initiated` (synchronous, in-txn `accounts.balance` conditional update) |
| sub_balance | same | overwritten live from Ledger, same enricher | Ledger (same mechanism) |

x-balances' own inbound-auth block names its callers explicitly: `Server.Auth.{API, Cron, Admin, PS, BankingAccounts, Validx, VirtualAccount}` (`06_balances_banking_accounts.md:187-189`, config re-cited) — `PS` = Payout Service, confirmed as the `x_balances` Basic-Auth identity payouts uses (`31_env2_bringup_notes.md:57`: `xbalances-server [Server.Auth.PS] x_balances` ↔ `auth_xbalances_payouts`).

Routes PS calls: `POST /v1/accounts` (`ListAccounts`, account selection/MAR — `payouts/internal/app/balance/core.go`), `GET/POST /v1/balances`,`/v1/balances/{id}` (balance read, Lite VA balance fetch, `internal/app/fundManagement/core.go:617-660`). PS's own `FetchActiveAccountsFromApi` (reads payouts' local DB) has **no live call site** — confirmed dead/legacy code (`06_balances_banking_accounts.md:56`, re-grepped, still true).

### 7. CFA — routes, auth, Mongo shapes

Routes/ACL/auth: unchanged from `05_cfa.md`'s Route Table (re-verified against `internal/server/interceptors.go`, `internal/api_controller/*/server.go` — all citations in that report point at real code and were spot-checked here for `CreateFundAccount`/`GetFundAccountById`/`GetFundAccountsByIds` and hold). Payouts is an ACL client with rights to Create/Update/Get contact+fund-account plus `GetFundAccountsByIds` (batch). Auth = HTTP Basic (`internal/server/interceptor/basicauth.go`), constant-time compare, per-username ACL keyed on full gRPC method name.

MongoDB document shapes (`cfa/internal/fund_accounts/model.go:30-83`, `internal/contacts/model.go:23-XX`, `internal/hashlookup/model.go`), CONFIRMED field-by-field:

`fund_accounts` (`FundAccountModel`): `id`(spine.Model), `contact_id`, `merchant_id`, `account_type` (enum, values seen in code: `bank_account`, `vpa`, `wallet`, `card`, `mobile` — 5 values, not the 4 the brief names), `bank_account{id,ifsc,bank_name,name,account_number,notes[],beneficiary_code,bank_identifier,identifier_type}` (present iff `account_type=bank_account`), `vpa{id,handle,username}` (iff `vpa`), `wallet{id,provider,phone,email,name}` (iff `wallet`, provider hardcoded allow-list of exactly `amazonpay` per `05_cfa.md:69`), `card{...}` (iff `card`, tokenized — no PAN), `mobile{id,number,account_holder_name}` (iff `mobile`), `active` bool, `batch_id` *string, `deleted_at` int64, `hash` string, `linked_number`, `customer_name`, `vendor_id`, `customer_id`.

`contacts` (`ContactModel`): `id`, `name`, `email` *string, `contact` *string (phone), `type` *string (`vendor`/`customer`/other free text — no enum constraint found in the model itself), `reference_id` *string, `notes` map[string]string, `hash` *string, `merchant_id`.

`hash_lookup` (`hashlookup.Model`): `id`, `hash`, `entity_id`, `entity_type` (`contacts`|`fund_accounts`). No unique index on `hash` in any of the 3 collections (`05_cfa.md:67`, re-confirmed — migration files show no `Unique: true`).

`CFA_WORKER_QUEUE_NAME` workers: exactly 2 in production (`kube-manifests/cde/cfa/values.yaml`, per `31_env2_bringup_notes.md:41`): `cfa-worker-fa` (fund-account lazy-load queue) and `cfa-worker-contact` (contact lazy-load queue) — but the **lazy-load trigger itself is commented out** in the read path (`05_cfa.md:60`), so these workers are wired but effectively idle in current code. Dual-write queues (`cfa-*-dual-write-queue`) are produced by CFA and consumed by an external API-monolith worker, not by CFA's own worker process.

`fund_accounts_internal` (monolith): per `31_env2_bringup_notes.md:74`, PS's `POST /v1/payouts` create path fetches `GET /v1/fund_accounts_internal/fa_<id>` from the **monolith** (cached in Redis) for the `fund_account_id` supplied in the create request — **PS uses the monolith's `fund_accounts_internal`, not CFA, on the hot create path**; CFA is the ACL-registered service for `GetFundAccountById`/`GetFundAccountsByIds` but this pass found no direct grep hit of PS calling CFA's `fund_accounts` route by name (payouts' CFA client exists per `05_cfa.md`'s ACL table entry for `Payouts`, but the specific call-site was not re-traced in this pass — same gap the CFA lane itself flagged in its Unresolved Questions #2). Flag as INFERRED (config/ACL says PS is a legitimate CFA caller; the *hot* create path demonstrably goes through the monolith instead, per the twin bring-up notes) — recommend the payouts lane confirm whether CFA is used on any payout code path at all, or only by dashboard/other product surfaces.

### 8. banking-accounts

`GET /payouts/shield/merchant/{id}/details` (`payouts/pkg/bankingAccountService/fetch_merchant_details.go:17-64`): auth header `ApiTokenHeader` + `X-Razorpay-Merchant-Id`; optional `?account_number=`; response `{data:{merchant_id, business_id, business_type, sales_team, account_number}}`. Source table in banking-accounts is out of this lane's readable set (route handler not located in `banking-accounts/` in this pass — the twin's stub hard-codes plausible-looking values, see Twin comparison).

"Banking account" record model (`banking-accounts/internal/database/entities/banking_account.go`, per `06_balances_banking_accounts.md:47`, re-cited): `AccountNumber`, `Status`, `AccountType`, `PartnerBank`, `BalanceID`, `FtsFundAccountID`, `Credentials` (encrypted JSON), `BeneficiaryDetails` (JSON).

**Cross-service identity map**, synthetic shared merchant M1 and synthetic direct merchant M2 (all values from the twin's own seed files, cited, or clearly-synthetic ARENA placeholders):

| Layer | Field | M1 (Shared) | M2 (Direct) |
|---|---|---|---|
| monolith / PS `banking_accounts` | `id` | `ARENABA0000001` | `ARENABA0000002` |
| | `account_type` | `shared` | `direct` |
| | `channel` | `NULL` | `rbl` |
| | `account_number` | `2323230099999999` (shared pool number) | `2323230000000002` (dedicated) |
| | `balance_id` | `ARENABAL000001` | `ARENABAL000002` |
| | `fts_fund_account_id` | `900001` | `900002` |
| x-balances `balance` | `id` | `ARENABAL000001` | `ARENABAL000002` |
| | `account_type` | `pool` | `direct` |
| | `fts_fund_account_id` | `900001` | `900002` |
| FTS `fund_accounts`/`source_accounts` | `id` | `900001` | `900002` |
| | `bank_account_type` | `NODAL` | `CURRENT` |
| | `account_type` (source_accounts) | `POOL` | `DIRECT` |
| ledger `accounts`/`account_details` | MerchantBalance etc. | keyed by `banking_account_id=ARENABA0000001` | (no Shared-style MerchantBalance row — Direct uses `direct_account_x`'s `DAMerchantBalance`, keyed by `banking_account_stmt_detail_id`) |
| | FtsPayable/Receivable | keyed by `fts_fund_account_id=900001`, `fund_account_type=nodal` | keyed by `fts_fund_account_id=900002`, `fund_account_type` **should be** `current` (twin has `nodal` — bug, §3) |
| CFA `fund_accounts` (vendor/beneficiary, unrelated axis) | n/a — same collection serves both merchant types, no shared/direct distinction in CFA's own model | n/a |

---

## Corrections to existing reports

| Report | Claim | What source says | Evidence |
|---|---|---|---|
| `raw-findings/04_ledger.md:172` | "Payouts service — presumed primary caller of `Journal.Create`... not verified" | Confirmed directly this pass: `payouts/pkg/ledger/ledger_journal_create.go` builds the exact `ledgerSdkDto.JournalCreateRequest` and calls `req.Create()` → `Client.CreateJournal` (ledger-sdk HTTP client) | `payouts/pkg/ledger/ledger_journal_create.go:66-93,151-165` |
| `raw-findings/04_ledger.md` (Payout accounting table) | `payout_processed`: "debit VendorPayable (amount), credit FtsPayable (amount)" — states the fact but does not connect it to PS's ledger call being **fully asynchronous and Shared-only** | PS's `payout_processed` journal call is not on the FTS-webhook-response synchronous path at all; it is deferred to the `payout_update_failure_handling` SQS worker and explicitly skipped for Direct/CA banking accounts | `payouts/internal/app/payouts/core.go:2570-2598` |
| `raw-findings/06_balances_banking_accounts.md:57` | "Actual balance-sufficiency checks at payout-processing time go through Ledger... (not read in full — belongs to payouts/FTS lane)" | Confirmed: for MerchantBalance (Shared/Lite), the check is the **synchronous, in-DB-transaction conditional `UPDATE accounts` inside `Journal.Create` for `payout_initiated`**, not a separate "sufficiency check" call; for Direct, the equivalent is `DAMerchantBalance` under the same mechanism but via `direct_account_x` config, decoupled from FTS/nodal accounting entirely | `ledger/internal/journal/ledger_config/seed_data/{shared_account_x.go:284-311,direct_account_x.go:220-253}`, `04_ledger.md:43` (repo.go:421, re-confirmed) |
| `ENV2_COMPOSE/seeds/s4/ledger.sql` header comment (§4, "TODO(confirm) whether it runs automatically on ledger boot") | Implies uncertainty about how `ledger_config` rows get loaded | Confirmed and already fixed by the twin itself: `LedgerConfigAPI/CreateInBulk` with `ledger_config_data_identifier` (`ledger/internal/journal/ledger_config/server.go:183-187`), invoked in `scripts/up.sh:138-144` — this part of the twin is correct and no longer a TODO |
| `ENV2_COMPOSE/seeds/s4/ledger.sql` §3 comment ("completeness/parity... not confirmed to exercise it for every Direct merchant") | Treats the M2 nodal-account seeding as merely unconfirmed/optional | It is not merely unconfirmed — it is confirmed **wrong as seeded** (`fund_account_type` should be `current`, not `nodal`, and the parent-account pair it should hang off does not exist in the twin at all) — see §3 above for full derivation | `fts/internal/account/service.go:4773-4777`, `ledger/internal/account/seed_data/shared_account_x.go:216-231`, `ENV2_COMPOSE/seeds/s4/{fts.sql:29,55, ledger.sql §3}` |

---

## Twin comparison

| Component | Production mechanism | Twin mechanism (file) | Verdict | What differs | Controls |
|---|---|---|---|---|---|
| ledger-api (JournalAPI/AccountAPI/LedgerConfigAPI) | Go/Twirp binary, Postgres, real business logic | Same binary, real image, `postgres-ledger` (`ENV2_COMPOSE/docker-compose.yml`) | **REAL** | schema is the real `rx_migrations`; ledger_config loaded via the real `LedgerConfigAPI/CreateInBulk` RPC using ledger's own seed data | balance/ledger authority, idempotency, tenant |
| ledger_config seed data | Go seed (`shared_account_x.go`/`direct_account_x.go`) loaded via ledger's own migration/admin path | Same Go seed, loaded via the same RPC (`up.sh:138-144`) | **REAL** (for the rule/config shape) | none material | routing of journal entries |
| ledger `accounts`/`account_details` (per-merchant + FTS nodal/current rows) | created via `AccountAPI/CreateOnEvent` by FTS (FTS-side) and (unknown caller, likely monolith) for merchant-side | raw-SQL seed (`ledger.sql`) reproducing the shape by hand | **INCORRECT** for the M2/Direct FTS pair (`fund_account_type=nodal` should be `current`; missing Current parent accounts) — CONTRACT-FAITHFUL for the Shared/M1/M3 pair | see §3 for the exact diff | balance/ledger, routing, payout state (blocks `payout_processed`/`reversed` for any Direct-routed FTS journal) |
| ledger CronJobs/scheduler | `split_account_balance_update`/`verify_warm_storage` CronJobs, both **suspended** in prod; in-process scheduler for the rest | not modeled (no cron container in twin) | **REPRESENTATIVE** (omission matches prod's suspended state for the 2 CronJobs; the in-process scheduler jobs are simply absent) | scheduler jobs (`balance_update_script` etc.) not run in the twin at all | balance/ledger repair paths, not core payout flow |
| x-balances (server+worker) | Go binary, MySQL, real bank-polling workers, Ledger-dependent enricher | Real image, `mysql-xbalances` (`xbalances-server`, `xbalances-worker`) | **REAL** | Mozart bank-poll target is `mozart-sim`, not a live bank; `sub_balance`/`sub_balance_limit` tables not migrated (queries.sql-only in prod too — not a twin gap, `28_build_spike_x-balances.md`) | routing/selection (MAR), Direct balance number |
| x-balances `balance` seed | populated by activation flow (Slice: direct x-balances write; legacy banks: via monolith `bas`) or by the Ledger enricher (Shared/sub_balance) | raw seed row per merchant, all 3 types (`xbalances.sql`) | **REPRESENTATIVE** | all 3 merchants get a stored `balance` value even though Shared/sub_balance's real number is always overwritten from Ledger live — harmless for selection, only matters if a test reads x-balances' raw column for a Shared merchant expecting it to be authoritative | balance/ledger (for Shared, none — correctly superseded), routing (fine) |
| CFA (server+worker) | Go/gRPC+REST, MongoDB, hash-based app-level dedupe | Real image, `mongo-cfa`, `cfa-server`/`cfa-worker-contact`/`cfa-worker-fa` | **REAL** | none material found | account/beneficiary data, no ledger/balance impact |
| CFA seed (contacts/fund_accounts/hash_lookup) | app-created via API, SHA3-256 hash computed by Go `golang.org/x/crypto/sha3` | direct `mongosh` seed with **precomputed** SHA3-256 hashes (Python `hashlib.sha3_256`, same NIST standard) matching the Go input-string format | **CONTRACT-FAITHFUL** (bypasses the API but reproduces its exact invariants: hash algorithm, no unique index reliance) | none material, by the twin's own careful design | none (not ledger/balance-relevant) |
| banking-accounts | Go/Gin, MySQL, ~40+ onboarding/admin/data-fix routes, Mozart/FTS/monolith integrations | `bankingaccounts-stub` (Python, 1 route: `GET /payouts/shield/merchant/{id}/details`) | **SUBSTITUTE, narrow/REPRESENTATIVE for the 1 route it covers** | everything except the one PS-called route is unimplemented — no onboarding, no `balance_id` admin PATCH, no credential management | tenant/account identity for the Direct-account create path only; nothing else |

---

## Recommendation: real vs substitute

| Component | Run REAL? | Notes |
|---|---|---|
| ledger | **REAL** (already done). Repo builds (`cmd/api`, `cmd/migration`), no external deps beyond Postgres/Redis/SQS/SNS which the arena already substitutes (LocalStack). Keep. |
| x-balances | **REAL** (already done). Builds, own migrations minimal (1 table). Keep; Mozart target is already substituted (mozart-sim/mock). |
| cfa | **REAL** (already done). Builds, MongoDB-only + SQS. Keep. |
| banking-accounts | **SUBSTITUTE, recommend staying substitute** — the real service pulls in Mozart, Zoho, UFH, BVS, Hubspot, Segment, Metro, and a 5-bank onboarding state machine (`internal/applications/onboarding/{rbl,axis,icici,idfc,yesbank,yesbank_pobo,slice}`) that is entirely orthogonal to a payout-path arena; buildable in isolation but a large surface for near-zero payout-path benefit. If a future task needs the onboarding/activation flow (e.g. to test how `banking_accounts`/`balance_id`/`fts_fund_account_id` get created in the first place), extend the stub with 2-3 more routes (`GET /banking_account/:id`, the CAUTION-flagged `balance_id` PATCH) rather than running the real service — protocol: HTTP/Gin, Basic-Auth-ish token header (`ApiTokenHeader`), JSON bodies matching `internal/database/entities/banking_account.go`'s fields. |

---

## Synthetic data

| family/table | field | source evidence | type+length | constraints | allowed values | FK/relationships | state rules | distribution matters? | generation rule | tier |
|---|---|---|---|---|---|---|---|---|---|---|
| ledger `accounts` | `id` | `rx_migrations/20201001011117_create_accounts.go` | CHAR(14) | PK | base62 in prod | — | — | no | 14-char `ARENA...` string | REPRESENTATIVE |
| ledger `accounts` | `balance` | same | NUMERIC(26,6) | ledger-managed, conditional update | integer paise (assumed) | — | reservation via `UPDATE...WHERE balance+incr>=min_balance` | yes for balance-sufficiency tests | seed opening balance via a real `positive_adjustment_processed` journal, not raw SQL | ASSUMED (paise-vs-rupee unit not independently re-confirmed this pass either) |
| ledger `account_details` | `entities` | `shared_account_x.go`/`direct_account_x.go` (this pass) | JSONB | must exact-match discovery query's built entities map | `account_type`∈{payable,receivable,cash}, `fund_account_type`∈{merchant,vendor,gst,nodal,current,reward,adjustment,...}, plus one identifier key (`banking_account_id`/`fts_fund_account_id`/`terminal_id`/`banking_account_stmt_detail_id`) | `parent_account_id`→`accounts.id` | discovery: exact JSONB match, `account_type=SubAccount` (has parent), tenant+currency+category scoped | **yes** — this is the field the confirmed twin bug is in | derive `fund_account_type` from the real onboarding-event→type map in `shared_account_x.go:199-265`, never hand-pick | EXACT (once corrected per §3) |
| ledger `ledger_config` | `rule`/`config` | `20210810170948_create_ledger_config.go` + seed_data Go files | JSONB | unique (tenant,rule) & (tenant,transactor_event_name) | — | — | loaded via `LedgerConfigAPI/CreateInBulk`, not hand-authored SQL | yes | always load via the RPC with `shared_account_x`/`direct_account_x`/`account_pg` identifiers | EXACT |
| ledger `journal` | `transactor_id`,`transactor_event` | `04_ledger.md:42` | varchar | app-level uniqueness only (mutex), no DB unique index | one of the seeded event names | `journal.merchant_id`→payout merchant | — | no | — | EXACT |
| x-balances `balance` | `account_type` | `internal/database/model/balance.go` | enum(string) | — | `direct`,`pool`,`sub_balance`,`master` | `fts_fund_account_id`→FTS `source_accounts.id` (string of int) | — | yes (drives selection) | one row per banking_account, `pool` for Shared, `direct` for CA | EXACT |
| FTS `source_accounts` | `bank_account_type` | `fts/internal/account/source_account.go:99-101,182` | enum(string) | `validation.In(CURRENT,NODAL,CORP_CARD)` | `CURRENT`,`NODAL`,`CORP_CARD` | — | drives ledger `fund_account_type` 1:1 (lower-cased) | **yes — this is the field that must be consistent with ledger's seed** | set per real bank-account type, never invent a 4th value | EXACT |
| CFA `fund_accounts` | `account_type` | `cfa/internal/fund_accounts/model.go:66-83` (this pass) | enum(string) | one of 5 sub-doc shapes present | `bank_account`,`vpa`,`wallet`,`card`,`mobile` | `contact_id`→`contacts.id` (app-level, no FK) | `active` bool only lifecycle field | no | pick 1 of 5, populate matching sub-document only | EXACT |
| CFA `fund_accounts` | `hash` | `05_cfa.md:50` + `model.go:289-328` (per report, re-cited) | hex string (SHA3-256, 64 hex chars) | app-level read-then-write dedupe only, no unique index | — | `hash_lookup.hash`+`entity_type` | — | no | SHA3-256 (`golang.org/x/crypto/sha3`, NIST FIPS202) over the documented compact-string/JSON per type | EXACT algorithm, REPRESENTATIVE precomputed values (twin uses Python `hashlib.sha3_256` — same standard) |
| CFA `contacts` | `type` | `model.go:36-37` | *string, no enum in model | free text | `vendor`/`customer` seen in practice | — | — | no | pick `vendor`/`customer` | REPRESENTATIVE |
| banking_accounts (monolith/PS) | `account_type` | `payouts.sql` comment + `banking-accounts/internal/database/entities/banking_account.go` | varchar | — | `shared`,`direct` (PS); `current` etc. (banking-accounts' own richer set, not independently confirmed this pass) | `balance_id`→x-balances, `fts_fund_account_id`→FTS | — | yes | `shared` for pool merchants, `direct` for dedicated-CA merchants | REPRESENTATIVE (banking-accounts' full enum not independently re-confirmed) |

---

## Cannot be derived from repositories

1. **Caller of ledger `AccountAPI/CreateOnEvent` for merchant-side onboarding events** (`shared_merchant_onboarding`/`shared_account_onboarding`/`direct_merchant_onboarding`/`direct_account_onboarding`) — zero hits in `payouts`, `cfa`, `x-balances`, `fts`, `banking-accounts`. Almost certainly the **API monolith**, on banking-account activation. Owner: API-monolith/BAS team. Minimal request: which monolith module calls ledger, and the exact request-body field mapping (does it reuse `banking_account_id` verbatim, signed or unsigned?). A sanitized code excerpt or a one-paragraph confirmation is sufficient — no data export needed.
2. **Literal `ledger-api` error log for the M1 (`merchant_m1`) `test_v06` failure.** Static config reading shows M1's `fund_account_type=nodal` seed matches what PS should send; the residual failure needs the actual `JOURNAL_CREATE_FAILED`/`ACCOUNT_DISCOVERY_*` line (with its `identifiers`/`entities` JSON dump) from a rerun of the golden run with `docker compose logs ledger-api` captured at DEBUG. Owner: whoever operates the arena. Minimal ask: `docker compose logs ledger-api | grep -A5 ACCOUNT_DISCOVERY` during a `test_v06` run.
3. **Whether `banking_account_id` in production ledger `account_details.entities` is stored signed (`bacc_...`) or unsigned.** `payouts/internal/app/bankingAccount/model.go:71` proves PS *sends* the signed form (`GetSignedID()` = `"bacc_" + ID`) for every journal call including `payout_initiated`; the twin's `ledger.sql` stores the *unsigned* form, yet `test_v05` (initiated) passed end-to-end with a real balance debit in the golden run — these two facts are in tension and were not reconciled in this pass (candidates: the golden run's ledger.sql predates the version read here; or account discovery in production genuinely uses the unsigned form and payouts' `GetSignedID()` value is used for a different identifier than `banking_account_id`). Owner: ledger/payouts teams. Minimal ask: one real production `account_details.entities` row export (schema-only field values, no merchant PII) for a MerchantBalance/VendorPayable account, to see the literal stored `banking_account_id` string format.
4. **Production `ledger_config` rows for tenant X, exported (not the Go seed, an actual `SELECT * FROM ledger_config WHERE tenant='X'` dump)** — would let a verifier diff the Go seed's *intended* shape against what's actually live (config drift, e.g. a since-added override not yet reflected in `shared_account_x.go`). Not required for the confirmed §3 fix (that fix is about `account_details`, not `ledger_config`), but valuable for the general "does the twin match prod" question. Owner: ledger team. Schema-only / values-only (no merchant ids) export is sufficient.
5. **banking-accounts' real onboarding/activation route table and its DB schema** (channel/account_type full enum, `balance_id`/`fts_fund_account_id` write path) — out of this lane's cloned repo depth (06's report time-boxed past it). Owner: banking-accounts team (`harshitsidhwa`/`flanker-23` per CODEOWNERS). A schema-only DDL dump of `banking_account`/`banking_account_application` plus the route list would let the substitute stub grow safely if ever needed.
6. **Real Kafka topic/consumer-group names** for ledger's PG-CDC and X's legacy `makeshift` queues, and the scheduler's exact cron cadences (`04_ledger.md:187`) — env-injected, not in-repo. Not payout-relevant (PG/legacy paths only) — low priority.

---

## Fidelity tier verdicts

- **ledger-api / ledger_config load**: REAL — real binary, real schema, real seed data loaded via the real RPC.
- **ledger `account_details` seed for Shared (M1/M3)**: CONTRACT-FAITHFUL — identifiers/fund_account_type match what production code would stamp.
- **ledger `account_details` seed for Direct FTS pair (M2/900002)**: UNKNOWN-BLOCKED→now RESOLVED BY THIS PASS as INCORRECT, with an exact fix given (§3) — reclassify to CONTRACT-FAITHFUL once the corrected rows above are applied.
- **x-balances**: REAL.
- **CFA**: REAL (server+worker); seed is CONTRACT-FAITHFUL (real hash algorithm, real document shapes, deliberately reproduces the "no unique index" invariant).
- **banking-accounts**: REPRESENTATIVE SUBSTITUTE, narrow — 1 of ~40+ routes implemented, contract-faithful for that one route only; single most important reason: the real service's onboarding/credentials/BVS/Mozart surface is disproportionate to what any payout-path arena flow currently calls (only the Shield merchant-details GET).
- **ledger CronJobs/scheduler**: not modeled in the twin — REPRESENTATIVE (matches prod's suspended-CronJob state for the 2 real CronJobs; the in-process scheduler's repair/reconciliation jobs are simply absent, which is acceptable for a payout-happy-path arena but would need explicit modeling for a repair-path arena).
