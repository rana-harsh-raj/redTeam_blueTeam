-- seeds/mysql/xbalances_seed.sql
-- x-balances balance rows for the 3 synthetic merchants (BOM §1 "Synthetic
-- entities": M2 is the "Direct/current account RBL, x-balances mirror"
-- merchant -- the one whose balance is actually authoritative in
-- x-balances rather than mirrored from ledger). Run AFTER xbalances-migrate
-- (scripts/up.sh's seed step, via
-- `docker compose exec -T mysql-xbalances mysql -uroot -p... rx_balances_local < this file`).
--
-- TODO (explicit): table/column names below are a best guess at what
-- x-balances' real goose/gorm migrations create (not read in this pass --
-- x-balances' `internal/database/migrations` wasn't inspected). Confirm
-- against the actual migration files before trusting these INSERTs to
-- succeed unmodified; this file is a skeleton, not a verified fixture.

-- TODO: confirm real table name (guessing `balances`, singular `balance`
-- elsewhere in this codebase's api-monolith DDL -- x-balances' own service
-- may use either convention).
INSERT INTO balances (id, merchant_id, account_number, account_type, channel, balance, currency, status, created_at)
VALUES
  ('bal_ARENA_M1', 'ARENA_M1', '111000000000001', 'shared',  NULL,   100000000, 'INR', 'activated', UNIX_TIMESTAMP()),
  ('bal_ARENA_M2', 'ARENA_M2', '222000000000002', 'direct',  'rbl',  100000000, 'INR', 'activated', UNIX_TIMESTAMP()),
  ('bal_ARENA_M3', 'ARENA_M3', '333000000000003', 'shared',  NULL,   100000000, 'INR', 'activated', UNIX_TIMESTAMP())
ON DUPLICATE KEY UPDATE balance = VALUES(balance);
