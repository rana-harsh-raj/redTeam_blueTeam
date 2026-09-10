-- seeds/postgres/ledger_seed.sql
-- Ledger accounts + ledger_config for the 3 synthetic merchants (BOM §1:
-- "Ledger balances must be re-seeded (accounts + ledger_config)" -- this is
-- also what scripts/reset.sh re-runs between scenarios, not just scripts/up.sh).
-- Run AFTER ledger-migrate.
--
-- TODO (explicit): ledger's real schema (ledger/internal/**/migrations,
-- postgres) was not read in this pass -- table/column names below are a
-- best-effort skeleton based on the domain concepts BOM/findings name
-- ("accounts", "ledger_config"), NOT verified against a real migration
-- file. Confirm before relying on this to actually seed a usable balance.

-- TODO: confirm real table name/columns (`accounts` guessed; ledger is a
-- double-entry system so this almost certainly needs an accompanying
-- opening-balance TRANSACTION/journal-entry row too, not just a bare
-- balance column -- not modeled here, flagged as a gap).
INSERT INTO accounts (id, merchant_id, account_type, currency, balance, status, created_at)
VALUES
  ('acc_ARENA_M1', 'ARENA_M1', 'merchant_shared', 'INR', 100000000, 'active', now()),
  ('acc_ARENA_M3', 'ARENA_M3', 'merchant_shared', 'INR', 100000000, 'active', now())
ON CONFLICT (id) DO UPDATE SET balance = EXCLUDED.balance;
-- ARENA_M2 intentionally has NO ledger account -- it's the x-balances-mirrored
-- "Direct" merchant (BOM §1), ledger is not authoritative for it.

-- TODO: confirm real table name/columns for per-merchant ledger config
-- (idempotency window, negative-balance policy, etc -- concept named in
-- BOM §1 only, no field list available in this pass).
INSERT INTO ledger_config (merchant_id, config_key, config_value)
VALUES
  ('ARENA_M1', 'allow_negative_balance', 'false'),
  ('ARENA_M3', 'allow_negative_balance', 'false')
ON CONFLICT (merchant_id, config_key) DO UPDATE SET config_value = EXCLUDED.config_value;
