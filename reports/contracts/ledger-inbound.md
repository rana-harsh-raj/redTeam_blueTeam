# Ledger (tenant X) — inbound contract (ledger@471ff4d, ledger-sdk)

## Transport and auth
Twirp JSON over HTTP, `POST /twirp/rzp.ledger.<svc>.v1.<API>/<Method>`; HTTP Basic `[auth.payouts]` / `[auth.fts]` / `[auth.xbalances]` (usernames `payouts_key`, `fts`, …); header `Ledger-Tenant: X`. Source `ledger-sdk/common/constant.go:598-613`, `ledger-sdk/dto/api.go`.

## JournalAPI/Create
Body (`ledger-sdk/dto/struct.go:630-643`): `merchant_id, currency, transactor_id (signed payout id or reversal ledger id), transactor_event, transaction_date (unix), notes{balance_id}, api_transaction_id?, additional_params{fee_accounting: reward}?, money_params{amount, base_amount, commission, tax} (decimal strings, paise, unsigned), identifiers{banking_account_id (signed bacc_…); fts_fund_account_id, fts_account_type (lower-cased) for payout_processed/payout_reversed}, dynamic_money_params[], sync_retry_attempts`.
Behaviour: config lookup by `(tenant, transactor_event)`; account discovery per entry (exact JSONB match on `entities` = `{account_type:[static], fund_account_type:[resolved], <identifier>:[value]}`, sub-accounts only, tenant/currency/category scoped; 0 → `ACCOUNT_DISCOVERY_ACCOUNT_NOT_FOUND`, >1 → `MULTIPLE`); journal atomic; MerchantBalance debit via `UPDATE accounts SET balance=balance+? WHERE balance+?>=min_balance`; insufficient → `BAD_REQUEST_INSUFFICIENT_BALANCE` (PS reclasses to `PayoutNotEnoughBalanceViaLedger`); duplicate `(transactor_id, transactor_event)` rejected by app-level check under Redis mutex (no DB unique).
Other: `JournalAPI/CreateInBulk`, `FetchById`, `FetchByTransactor` (used by PS retry job).

## AccountAPI
`CreateOnEvent` (FTS: `nodal_pool_account_onboarding` / `current_pool_account_onboarding`; merchant-side `shared_account_onboarding`, `shared_merchant_onboarding`, `direct_*` — caller not in readable repos), `UpdateByEntitiesAndMerchantID`, `FetchByMerchantID` (x-balances enricher), `FetchByEntitiesAndMerchantID`, `FetchInBulkByEntitiesAndMerchantID`, `Deactivate`, `Activate`, `Archive`.

## LedgerConfigAPI/CreateInBulk
`{ledger_config_data_identifier: shared_account_x | direct_account_x | account_pg}` loads the Go seed configs (`internal/journal/ledger_config/seed_data/*.go`). Payout events and entries: see `TWIN_SPEC/state-machines.yaml#ledger_journal`.

## Accounts and identifiers per merchant (shared_account_x)
MerchantBalance/VendorPayable/CommissionIncome/OutputGST keyed `banking_account_id` (fund_account_type merchant/vendor/merchant/gst; account_type payable/payable/cash/payable); FtsReceivable/FtsPayable keyed `fts_fund_account_id` with `fund_account_type = $fts_account_type` ∈ nodal|current|corp_card under the matching parent; MerchantReward keyed merchant_id; AdjustmentLiability (fund_account_type adjustment) for top-ups. Direct: DA* accounts keyed `banking_account_stmt_detail_id`.

## Messaging
SQS `journal_create`, `account_create`, `balance_update`, `ledger_entry_details_create`; SNS `journal-created` (whitelisted events, excludes payout_processed; topic string `topicArn:topicName`). CronJobs suspended in prod.
