-- Ledger (Postgres 16, tenant X) seed for the Env2 arena.
-- Ground truth: ledger/internal/database/rx_migrations/*.go (accounts, account_details, ledger_config,
-- journal, ledger_entries) and ledger/internal/account/seed_data/shared_account_x.go +
-- ledger/internal/journal/ledger_config/seed_data/shared_account_x.go.
--
-- IMPORTANT: production bootstraps ledger accounts exclusively via the AccountAPI.CreateOnEvent Twirp
-- RPC (POST /twirp/rzp.ledger.account.v1.AccountAPI/CreateOnEvent, header Ledger-Tenant: X), never by
-- raw SQL. See ledger_accounts_via_api.sh for that path, which is the RECOMMENDED way to seed this
-- service (it exercises the same account-discovery/matching logic real payouts/fts traffic will use).
-- This file is the pure-SQL fallback, for environments where the ledger `api` binary cannot be booted
-- before seeding. If you use ledger_accounts_via_api.sh, do NOT also run this file (it would create a
-- second, conflicting set of parent accounts).
--
-- 14-char CHAR(14) id convention: base62 Razorpay-style ids (goutils/uniqueid) in production; this
-- arena substitutes clearly-synthetic fixed-width "ARENA..." strings of the same 14-char length. All
-- ids below were counted by hand to be exactly 14 characters -- do not edit without recounting, a
-- CHAR(14) column silently truncates a too-long string, which can create ID collisions.
--
-- Amount unit: TODO(confirm) -- accounts.balance/journal.amount/ledger_entries.amount are
-- NUMERIC(26,6). The ledger_accounts_via_api.sh worked example (functional test fixture) shows
-- merchant_balance_opening_balance:"1050" for currency INR without stating whether "1050" means
-- paisa or rupees. This file assumes PAISA-as-integer (consistent with payouts.amount/fees/tax and
-- pricing_fixtures.json elsewhere in this arena), written into the NUMERIC(26,6) column as a plain
-- integer value. Confirm against ledger/internal/account/core.go's money.Money handling before
-- trusting exact-balance assertions in a verifier.

BEGIN;

-- ============================================================================
-- 1. Fixed "parent" (system) accounts -- REAL production constants from
--    ledger/internal/account/seed_data/shared_account_x.go / internal/common/constant.go.
--    These are not secrets; they are well-known fixed merchant_ids the ledger codebase itself uses
--    to own the shared category-level parent accounts. Reusing them here (rather than inventing
--    ARENA-prefixed ids) matters IF any ledger-side account-discovery/lookup code matches by these
--    literal ids (not independently confirmed either way in this pass) -- using the real constants is
--    the safe choice either way, since real code will resolve against them by definition.
-- ============================================================================

INSERT INTO accounts (id, merchant_id, status, balance, min_balance, negative_balance, created_at, updated_at, deleted_at) VALUES
  ('ARENAPRACC0001', 'Gg614JldVI2nJi', 'ACTIVATED', 0, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL), -- Merchant Balance Account (parent)
  ('ARENAPRACC0002', 'Gg6I8KieFph8kj', 'ACTIVATED', 0, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL), -- Vendor Payable Account (parent)
  ('ARENAPRACC0003', 'Gg6I8IF5vrmp6k', 'ACTIVATED', 0, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL), -- Commission Income Account (parent)
  ('ARENAPRACC0004', 'Gg6I8GsKzz6CBl', 'ACTIVATED', 0, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL), -- Output GST Account (parent)
  ('ARENAPRACC0005', 'Gh0YfwUewxUqdm', 'ACTIVATED', 0, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL), -- Razorpay Nodal Receivable Account (parent)
  ('ARENAPRACC0006', 'Gh0YfqKiXRdkCo', 'ACTIVATED', 0, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL)  -- Razorpay Nodal Payable Account (parent)
ON CONFLICT DO NOTHING;

INSERT INTO account_details (id, account_id, account_name, merchant_id, parent_account_id, currency, account_category, business_category, entities, description, created_at, updated_at, deleted_at, tenant, use_split_accounts) VALUES
  ('ARENAPRDT00001', 'ARENAPRACC0001', 'Merchant Balance Account',   'Gg614JldVI2nJi', NULL, 'INR', 'liability', 'nominal', '{"fund_account_type":["merchant_va"],"account_type":["payable"]}', 'Parent account for per-merchant balance sub-accounts (X tenant)', extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0),
  ('ARENAPRDT00002', 'ARENAPRACC0002', 'Vendor Payable Account',     'Gg6I8KieFph8kj', NULL, 'INR', 'liability', 'nominal', '{"fund_account_type":["merchant_va_vendor"],"account_type":["payable"]}', 'Parent account for per-merchant vendor-payable sub-accounts', extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0),
  ('ARENAPRDT00003', 'ARENAPRACC0003', 'Commission Income Account',  'Gg6I8IF5vrmp6k', NULL, 'INR', 'revenue',   'nominal', '{"fund_account_type":["merchant_va"],"account_type":["cash"]}', 'Parent account for per-merchant commission-income sub-accounts', extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0),
  ('ARENAPRDT00004', 'ARENAPRACC0004', 'Output GST Account',         'Gg6I8GsKzz6CBl', NULL, 'INR', 'liability', 'nominal', '{"fund_account_type":["va_gst"],"account_type":["payable"]}', 'Parent account for per-merchant output-GST sub-accounts', extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0),
  ('ARENAPRDT00005', 'ARENAPRACC0005', 'Razorpay Nodal Receivable',  'Gh0YfwUewxUqdm', NULL, 'INR', 'asset',     'nominal', '{"fund_account_type":["nodal"],"account_type":["receivable"]}', 'Parent account for FTS nodal receivable sub-accounts', extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0),
  ('ARENAPRDT00006', 'ARENAPRACC0006', 'Razorpay Nodal Payable',     'Gh0YfqKiXRdkCo', NULL, 'INR', 'liability', 'nominal', '{"fund_account_type":["nodal"],"account_type":["payable"]}', 'Parent account for FTS nodal payable sub-accounts', extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0)
ON CONFLICT DO NOTHING;

-- ============================================================================
-- 2. Per-merchant sub-accounts, tenant X.
--    M1 (ARENAM00000001) and M3 (ARENAM00000003) are Shared -- get the full 4-account set
--    (MerchantBalance/VendorPayable/CommissionIncome/OutputGST), keyed by their own banking_account_id.
--    M2 (ARENAM00000002) is Direct -- per findings/13_fts_payouts_status_path.md finding #6
--    ("CA (Direct) payouts skip ledger entirely here... done after BAS linking"), Direct merchants'
--    authoritative balance is x-balances (see xbalances.sql), NOT this MerchantBalance pattern. M2
--    intentionally has NO MerchantBalance/VendorPayable/CommissionIncome/OutputGST rows here.
-- ============================================================================

-- M1 (Shared) sub-accounts, banking_account_id = ARENABA0000001
INSERT INTO accounts (id, merchant_id, status, balance, min_balance, negative_balance, created_at, updated_at, deleted_at) VALUES
  ('ARENAM1ACC0001', 'ARENAM00000001', 'ACTIVATED', 10000000, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL), -- MerchantBalance: opening Rs 1,00,000.00 (10,000,000 paisa)
  ('ARENAM1ACC0002', 'ARENAM00000001', 'ACTIVATED', 0, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL),          -- VendorPayable
  ('ARENAM1ACC0003', 'ARENAM00000001', 'ACTIVATED', 0, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL),          -- CommissionIncome
  ('ARENAM1ACC0004', 'ARENAM00000001', 'ACTIVATED', 0, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL)           -- OutputGST
ON CONFLICT DO NOTHING;

INSERT INTO account_details (id, account_id, account_name, merchant_id, parent_account_id, currency, account_category, business_category, entities, description, created_at, updated_at, deleted_at, tenant, use_split_accounts) VALUES
  ('ARENAM1DT00001', 'ARENAM1ACC0001', 'Merchant Balance Account - ARENAM00000001',  'ARENAM00000001', 'ARENAPRACC0001', 'INR', 'liability', 'real', '{"account_type":["payable"],"banking_account_id":["ARENABA0000001"],"fund_account_type":["merchant_va"]}', NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0),
  ('ARENAM1DT00002', 'ARENAM1ACC0002', 'Vendor Payable Account - ARENAM00000001',    'ARENAM00000001', 'ARENAPRACC0002', 'INR', 'liability', 'real', '{"account_type":["payable"],"banking_account_id":["ARENABA0000001"],"fund_account_type":["merchant_va_vendor"]}', NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0),
  ('ARENAM1DT00003', 'ARENAM1ACC0003', 'Commission Income Account - ARENAM00000001', 'ARENAM00000001', 'ARENAPRACC0003', 'INR', 'revenue',   'real', '{"account_type":["cash"],"banking_account_id":["ARENABA0000001"],"fund_account_type":["merchant_va"]}', NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0),
  ('ARENAM1DT00004', 'ARENAM1ACC0004', 'Output GST Account - ARENAM00000001',        'ARENAM00000001', 'ARENAPRACC0004', 'INR', 'liability', 'real', '{"account_type":["payable"],"banking_account_id":["ARENABA0000001"],"fund_account_type":["va_gst"]}', NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0)
ON CONFLICT DO NOTHING;

-- M3 (Shared, workflow-enabled) sub-accounts, banking_account_id = ARENABA0000003
INSERT INTO accounts (id, merchant_id, status, balance, min_balance, negative_balance, created_at, updated_at, deleted_at) VALUES
  ('ARENAM3ACC0001', 'ARENAM00000003', 'ACTIVATED', 10000000, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL), -- MerchantBalance: opening Rs 1,00,000.00
  ('ARENAM3ACC0002', 'ARENAM00000003', 'ACTIVATED', 0, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL),
  ('ARENAM3ACC0003', 'ARENAM00000003', 'ACTIVATED', 0, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL),
  ('ARENAM3ACC0004', 'ARENAM00000003', 'ACTIVATED', 0, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL)
ON CONFLICT DO NOTHING;

INSERT INTO account_details (id, account_id, account_name, merchant_id, parent_account_id, currency, account_category, business_category, entities, description, created_at, updated_at, deleted_at, tenant, use_split_accounts) VALUES
  ('ARENAM3DT00001', 'ARENAM3ACC0001', 'Merchant Balance Account - ARENAM00000003',  'ARENAM00000003', 'ARENAPRACC0001', 'INR', 'liability', 'real', '{"account_type":["payable"],"banking_account_id":["ARENABA0000003"],"fund_account_type":["merchant_va"]}', NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0),
  ('ARENAM3DT00002', 'ARENAM3ACC0002', 'Vendor Payable Account - ARENAM00000003',    'ARENAM00000003', 'ARENAPRACC0002', 'INR', 'liability', 'real', '{"account_type":["payable"],"banking_account_id":["ARENABA0000003"],"fund_account_type":["merchant_va_vendor"]}', NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0),
  ('ARENAM3DT00003', 'ARENAM3ACC0003', 'Commission Income Account - ARENAM00000003', 'ARENAM00000003', 'ARENAPRACC0003', 'INR', 'revenue',   'real', '{"account_type":["cash"],"banking_account_id":["ARENABA0000003"],"fund_account_type":["merchant_va"]}', NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0),
  ('ARENAM3DT00004', 'ARENAM3ACC0004', 'Output GST Account - ARENAM00000003',        'ARENAM00000003', 'ARENAPRACC0004', 'INR', 'liability', 'real', '{"account_type":["payable"],"banking_account_id":["ARENABA0000003"],"fund_account_type":["va_gst"]}', NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0)
ON CONFLICT DO NOTHING;

-- ============================================================================
-- 3. FTS nodal receivable/payable accounts, keyed by fts_fund_account_id (NOT per-merchant --
--    these track Razorpay's own nodal bank balance). One pair for the M1/M3 shared pool
--    (fts_fund_account_id = '900001'), one pair for M2's dedicated Direct account
--    (fts_fund_account_id = '900002'). See fts.sql for the corresponding FTS-side rows.
-- ============================================================================

INSERT INTO accounts (id, merchant_id, status, balance, min_balance, negative_balance, created_at, updated_at, deleted_at) VALUES
  ('ARENAPOOLACC01', 'Gh0YfwUewxUqdm', 'ACTIVATED', 0, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL), -- FtsReceivable, pool (900001)
  ('ARENAPOOLACC02', 'Gh0YfqKiXRdkCo', 'ACTIVATED', 0, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL), -- FtsPayable, pool (900001)
  ('ARENAM2NODAC01', 'Gh0YfwUewxUqdm', 'ACTIVATED', 0, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL), -- FtsReceivable, M2 direct (900002)
  ('ARENAM2NODAC02', 'Gh0YfqKiXRdkCo', 'ACTIVATED', 0, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL)  -- FtsPayable, M2 direct (900002)
ON CONFLICT DO NOTHING;

INSERT INTO account_details (id, account_id, account_name, merchant_id, parent_account_id, currency, account_category, business_category, entities, description, created_at, updated_at, deleted_at, tenant, use_split_accounts) VALUES
  ('ARENAPOOLDT001', 'ARENAPOOLACC01', 'FTS Nodal Receivable - pool 900001', 'Gh0YfwUewxUqdm', 'ARENAPRACC0005', 'INR', 'asset',     'real', '{"account_type":["receivable"],"fts_fund_account_id":["900001"],"fund_account_type":["nodal"]}', 'Shared RBL pool nodal account (M1, M3)', extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0),
  ('ARENAPOOLDT002', 'ARENAPOOLACC02', 'FTS Nodal Payable - pool 900001',    'Gh0YfqKiXRdkCo', 'ARENAPRACC0006', 'INR', 'liability', 'real', '{"account_type":["payable"],"fts_fund_account_id":["900001"],"fund_account_type":["nodal"]}', 'Shared RBL pool nodal account (M1, M3)', extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0),
  ('ARENAM2NODDT01', 'ARENAM2NODAC01', 'FTS Nodal Receivable - M2 900002',   'Gh0YfwUewxUqdm', 'ARENAPRACC0005', 'INR', 'asset',     'real', '{"account_type":["receivable"],"fts_fund_account_id":["900002"],"fund_account_type":["nodal"]}', 'Direct RBL current account (M2)', extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0),
  ('ARENAM2NODDT02', 'ARENAM2NODAC02', 'FTS Nodal Payable - M2 900002',      'Gh0YfqKiXRdkCo', 'ARENAPRACC0006', 'INR', 'liability', 'real', '{"account_type":["payable"],"fts_fund_account_id":["900002"],"fund_account_type":["nodal"]}', 'Direct RBL current account (M2)', extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0)
ON CONFLICT DO NOTHING;

-- ============================================================================
-- 4. ledger_config -- journal accounting rules (double-entry recipes per transactor_event).
--    Full 34-config set (26 primary + 8 legacy) is defined in Go in
--    ledger/internal/journal/ledger_config/seed_data/shared_account_x.go, returned by
--    GetXSharedAccountingLedgerConfigs(). That function is what production calls to populate this
--    table (invocation site not traced by the research pass -- TODO(confirm) whether it runs
--    automatically on ledger boot/migration or must be invoked manually). Reproducing all 34 rows'
--    exact jsonb `rule`/`config` shape by hand here would be a high-risk transcription exercise; the
--    two most Env2-relevant rows (XPayoutInitiatedV2, XPayoutProcessedV2) are given below verbatim
--    per the entries documented by the research pass. RECOMMENDATION: prefer calling
--    GetXSharedAccountingLedgerConfigs() through whatever ledger init path production uses (a Go
--    one-off using the same function, or an internal seed RPC if one exists -- TODO(confirm)) over
--    hand-transcribing the remaining 32 rows into SQL.
-- ============================================================================

-- ARENA NOTE: ledger_config rows below are superseded by LedgerConfigAPI/CreateInBulk (shared_account_x, direct_account_x) in scripts/up.sh;
-- the hand-transcribed shape ({"entries":[...]}) matched the payout_initiated rule first and produced ZERO ledger entries.
/*
INSERT INTO ledger_config (id, tenant, transactor_event_name, rule, config, created_at, updated_at, deleted_at) VALUES
  ('ARENALCFG00001', 'X', 'XPayoutInitiatedV2',
   '{"transactor_event": "payout_initiated"}',
   '{"entries": [
      {"account": "VendorPayableAccount",    "entry_type": "credit", "formula": "amount"},
      {"account": "MerchantBalanceAccount",  "entry_type": "debit",  "formula": "commission + amount"},
      {"account": "CommissionIncomeAccount", "entry_type": "credit", "formula": "commission - tax"},
      {"account": "OutputGSTAccount",        "entry_type": "credit", "formula": "tax"}
    ]}',
   extract(epoch from now())::int, extract(epoch from now())::int, NULL),
  ('ARENALCFG00002', 'X', 'XPayoutProcessedV2',
   '{"transactor_event": "payout_processed"}',
   '{"entries": [
      {"account": "VendorPayableAccount", "entry_type": "debit",  "formula": "amount"},
      {"account": "FtsPayableAccount",    "entry_type": "credit", "formula": "amount"}
    ]}',
   extract(epoch from now())::int, extract(epoch from now())::int, NULL)
ON CONFLICT (tenant, transactor_event_name) WHERE deleted_at IS NULL DO NOTHING;
*/

-- ============================================================================
-- 5. Opening-balance journal for M1/M3's MerchantBalance accounts.
--    GAP/TODO(confirm): sections 2's `accounts.balance` INSERTs above already set ARENAM1ACC0001/
--    ARENAM3ACC0001 to 10000000 directly (this table is a denormalized running balance per the
--    build-spike/model read, not derived from ledger_entries at query time) -- so a verifier reading
--    `accounts.balance` alone works without this section. This section exists ONLY so a verifier that
--    instead reads `journal`/`ledger_entries` (the double-entry audit trail) also sees a real posting,
--    per the task's "reset/invariant section: ... how verifiers derive expected values" requirement.
--    The real shared_account_x.go opening-balance/positive-adjustment transactor_event_name and its
--    exact entry recipe (which account funds the credit) were NOT independently confirmed in this
--    pass -- 'ARENAOpeningBalanceV2' below is an invented, clearly-synthetic event name (not a real
--    ledger constant), and crediting MerchantBalance against a debit to the matching PARENT account
--    (ARENAPRACC0001, "Merchant Balance Account") is a best-effort modeling choice consistent with the
--    parent/child pattern this file already uses elsewhere -- NOT verified against real code. Prefer
--    ledger_accounts_via_api.sh's CreateOnEvent flow (which drives the real onboarding+funding path)
--    over trusting this synthetic journal's transactor_event_name to mean anything to real ledger code.
-- ============================================================================

-- ARENA NOTE: ledger_config rows below are superseded by LedgerConfigAPI/CreateInBulk (shared_account_x, direct_account_x) in scripts/up.sh;
-- the hand-transcribed shape ({"entries":[...]}) matched the payout_initiated rule first and produced ZERO ledger entries.
/*
INSERT INTO ledger_config (id, tenant, transactor_event_name, rule, config, created_at, updated_at, deleted_at) VALUES
  ('ARENALCFG00003', 'X', 'ARENAOpeningBalanceV2',
   '{"transactor_event": "arena_seed_opening_balance", "_note": "synthetic, arena-only -- see Sec 5 comment above"}',
   '{"entries": [
      {"account": "MerchantBalanceAccount",       "entry_type": "credit", "formula": "amount"},
      {"account": "MerchantBalanceParentAccount",  "entry_type": "debit",  "formula": "amount"}
    ]}',
   extract(epoch from now())::int, extract(epoch from now())::int, NULL)
ON CONFLICT (tenant, transactor_event_name) WHERE deleted_at IS NULL DO NOTHING;
*/

INSERT INTO journal (id, merchant_id, amount, base_amount, currency, transactor_id, transactor_event, transaction_date, created_at, updated_at, tenant) VALUES
  ('ARENAJRN00001', 'ARENAM00000001', 10000000, 10000000, 'INR', 'ARENABA0000001', 'ARENAOpeningBalanceV2', extract(epoch from now())::int, extract(epoch from now())::int, extract(epoch from now())::int, 'X'),
  ('ARENAJRN00002', 'ARENAM00000003', 10000000, 10000000, 'INR', 'ARENABA0000003', 'ARENAOpeningBalanceV2', extract(epoch from now())::int, extract(epoch from now())::int, extract(epoch from now())::int, 'X')
ON CONFLICT DO NOTHING;

INSERT INTO ledger_entries (id, merchant_id, journal_id, account_id, amount, base_amount, type, currency, created_at, updated_at) VALUES
  ('ARENALED00001', 'ARENAM00000001', 'ARENAJRN00001', 'ARENAM1ACC0001',  10000000, 10000000, 'credit', 'INR', extract(epoch from now())::int, extract(epoch from now())::int),
  ('ARENALED00002', 'ARENAM00000001', 'ARENAJRN00001', 'ARENAPRACC0001', 10000000, 10000000, 'debit',  'INR', extract(epoch from now())::int, extract(epoch from now())::int),
  ('ARENALED00003', 'ARENAM00000003', 'ARENAJRN00002', 'ARENAM3ACC0001',  10000000, 10000000, 'credit', 'INR', extract(epoch from now())::int, extract(epoch from now())::int),
  ('ARENALED00004', 'ARENAM00000003', 'ARENAJRN00002', 'ARENAPRACC0001', 10000000, 10000000, 'debit',  'INR', extract(epoch from now())::int, extract(epoch from now())::int)
ON CONFLICT DO NOTHING;

COMMIT;

-- Adjustment payable counter-account: the X config for positive_adjustment_processed debits
-- {account_type: payable, fund_account_type: adjustment} (no identifiers) and credits the merchant's
-- MerchantBalance; the verifier tops balances up through this real ledger event (SQL updates bypass ledger caches).
INSERT INTO accounts (id, merchant_id, status, balance, min_balance, negative_balance, created_at, updated_at, deleted_at) VALUES
  ('ARENAPRACC0007', 'Gh0YfqKiXRdkCo', 'ACTIVATED', 0, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL)
ON CONFLICT DO NOTHING;
INSERT INTO account_details (id, account_id, account_name, merchant_id, parent_account_id, currency, account_category, business_category, entities, description, created_at, updated_at, deleted_at, tenant, use_split_accounts) VALUES
  ('ARENAPRDT00007', 'ARENAPRACC0007', 'Adjustment Payable Account', 'Gh0YfqKiXRdkCo', 'ARENAPRACC0008', 'INR', 'liability', 'nominal', '{"account_type": ["payable"], "fund_account_type": ["adjustment"]}', 'arena: counter-account for positive_adjustment_processed top-ups', extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0)
ON CONFLICT DO NOTHING;

-- ledger account discovery for identifier-less config entries searches SUB-accounts (parent_account_id NOT NULL):
-- account_discovery/core.go GetAccountByConfig -> AccountType: SubAccount. So the adjustment account needs a parent.
INSERT INTO accounts (id, merchant_id, status, balance, min_balance, negative_balance, created_at, updated_at, deleted_at) VALUES
  ('ARENAPRACC0008', 'Gh0YfqKiXRdkCo', 'ACTIVATED', 0, 0, NULL, extract(epoch from now())::int, extract(epoch from now())::int, NULL)
ON CONFLICT DO NOTHING;
INSERT INTO account_details (id, account_id, account_name, merchant_id, parent_account_id, currency, account_category, business_category, entities, description, created_at, updated_at, deleted_at, tenant, use_split_accounts) VALUES
  ('ARENAPRDT00008', 'ARENAPRACC0008', 'Adjustment Payable (parent)', 'Gh0YfqKiXRdkCo', NULL, 'INR', 'liability', 'nominal', '{"account_type": ["payable"], "fund_account_type": ["adjustment_parent"]}', 'arena: parent of the adjustment payable sub-account', extract(epoch from now())::int, extract(epoch from now())::int, NULL, 'X', 0)
ON CONFLICT DO NOTHING;

-- The adjustment payable account is the DEBIT side of every top-up journal and ledger enforces its min-balance
-- (override_min_balance_check=false in the X config), so it carries a synthetic 10^14 paise float.
UPDATE accounts SET balance = 100000000000000 WHERE id IN ('ARENAPRACC0007', 'ARENAPRACC0008');
