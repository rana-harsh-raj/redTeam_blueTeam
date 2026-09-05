-- x-balances (MySQL 8) seed for the Env2 arena.
-- Ground truth: x-balances/internal/database/migrations/20250127224555_balance.go.
--
-- IMPORTANT: this migration creates exactly ONE table, `balance`. There is no `sub_balance` /
-- `sub_balance_limits` migration anywhere in x-balances/internal/database/migrations/, despite
-- sub_balance.go/sub_balance_limit.go existing as Go model files -- the real DDL for those two
-- tables lives only in the unwired x-balances/queries.sql (confirmed by the research pass and by
-- findings/28_build_spike_x-balances.md: "2 tables -- balance, goose_db_version"). Not seeded here.
--
-- All 3 merchants get a `balance` row, not just the Direct one (M2): per
-- findings/06_balances_banking_accounts.md, payouts selects its source account/balance by calling
-- x-balances' ListAccounts (account SELECTION), for every account type -- it is only the live
-- BALANCE NUMBER that differs by type (Direct: x-balances' own polled value; pool/shared and
-- sub_balance: overwritten in real time from Ledger by x-balances' own enricher). So M1/M3 (Shared)
-- still need a row here for routing/selection to work, even though ledger.sql is the number's
-- source of truth for them.
--
-- account_type values confirmed in x-balances/internal/database/model/balance.go + queries.sql:
-- 'direct', 'pool', 'sub_balance', 'master'. The informal "shared (Lite VA)" terminology used in
-- findings/06 maps to the literal enum value 'pool' used here for M1/M3.

INSERT IGNORE INTO balance
  (id, created_at, updated_at, status, merchant_id, account_number, account_type, channel,
   currency, balance, priority, last_change_at, last_fetched_at, metadata,
   last_attempted_at, fts_fund_account_id)
VALUES
  ('ARENABAL000001', UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), 'activated', 'ARENAM00000001',
   '2323230099999999', 'pool',   'RBL', 'INR', 10000000, 0,
   UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), JSON_OBJECT('seed', 'env2-arena'), 0, '900001'),
  ('ARENABAL000002', UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), 'activated', 'ARENAM00000002',
   '2323230000000002', 'direct', 'RBL', 'INR', 10000000, 0,
   UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), JSON_OBJECT('seed', 'env2-arena'), 0, '900002'),
  ('ARENABAL000003', UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), 'activated', 'ARENAM00000003',
   '2323230099999999', 'pool',   'RBL', 'INR', 10000000, 0,
   UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), JSON_OBJECT('seed', 'env2-arena'), 0, '900001');
