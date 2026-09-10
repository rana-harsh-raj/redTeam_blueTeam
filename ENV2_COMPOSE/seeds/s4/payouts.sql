-- Payouts service (MySQL 8) seed for the Env2 arena.
-- Ground truth: payouts/internal/database/migrations/*.go. Column names/types below are verbatim from
-- those migrations. All ids are exactly 14 chars (CHAR(14) columns truncate silently past 14 -- do not
-- edit an id string without recounting).
--
-- Not seeded here (populated at runtime by golden-flow traffic, not fixture data):
--   payouts, payout_details, payout_status_details, payout_sources, payout_attempts,
--   idempotency_keys, bulk_idempotency_keys, idempotency_key_exclusions, counter_transaction,
--   source_request_id_mapping, event_outbox, banking_account_statement(_details).
-- Not seeded (no confirmed real usage pattern for this newer table found in the research pass;
-- left empty rather than fabricate plausible-looking config keys): merchant_configurations,
-- duplicate_prevention_config, settings, payout_meta_temporary, payout_meta_permanent.

-- ============================================================================
-- banking_accounts (internal/database/migrations/20200922102712_create_banking_accounts_table.go)
-- Columns: id, merchant_id, balance_id, channel, status, account_number, account_type,
--          fts_fund_account_id, payout_service_enabled, counter_migrated, created_at, updated_at.
-- fts_fund_account_id is CHAR(14) here but FTS's own fund_accounts.id is an INT AUTO_INCREMENT
-- (see fts.sql) -- stored as the string form of that int, e.g. '900001'. Confirmed cross-service
-- type mismatch, not a bug in this seed.
--
-- CORRECTION applied to this file (was previously uppercase 'RBL' for all three rows): payouts'
-- own channel constants are lowercase (payouts/internal/app/common/appConstants/constants.go:61-70
-- -- AXIS="axis", YESBANK="yesbank", ICICI="icici", RBL="rbl", IDFC="idfc"; confirmed as the value
-- actual write paths assign, e.g. bankingAccount.Channel = appConstants.YESBANK at
-- internal/app/bankingAccount/core.go:140/468). FTS's OWN channel column is a separate, uppercase
-- DB enum ('RBL') -- that asymmetry is real and preserved in fts.sql. Also set M1/M3 (Shared/pool)
-- channel to NULL rather than a bank code: the column is nullable
-- (`channel varchar(255) DEFAULT NULL`), and a Shared/Lite merchant's banking_accounts row is not
-- itself bank-specific -- only the underlying FTS-side pool nodal account (fts.sql) carries a
-- channel. M2 (Direct) keeps 'rbl' since it genuinely is a single-bank current account.
-- ============================================================================

-- RECONCILIATION NOTE (cross-checked against the final xbalances.sql/ledger.sql/README.md): id is
-- ARENABA000000N (matches ledger.sql's account_details.entities.banking_account_id and
-- ledger_accounts_via_api.sh's request bodies). balance_id ALWAYS points at x-balances' balance.id
-- (ARENABAL00000N) for all three merchants, not just the Direct one -- per xbalances.sql's own
-- header note, x-balances performs account SELECTION for every account type (payouts calls its
-- ListAccounts to resolve balance_id), while Ledger remains the balance-NUMBER source of truth for
-- Shared merchants (M1/M3) underneath that same x-balances row. account_number mirrors xbalances.sql
-- exactly: M1/M3 share the pool account number (they route through the same RBL nodal pool, see
-- fts.sql), M2 has its own dedicated current-account number.
INSERT INTO banking_accounts
  (id, merchant_id, balance_id, channel, status, account_number, account_type, fts_fund_account_id, payout_service_enabled, counter_migrated, created_at, updated_at)
VALUES
  ('ARENABA0000001', 'ARENAM00000001', 'ARENABAL000001', NULL,  'activated', '2323230099999999', 'shared', '900001', 1, 1, UNIX_TIMESTAMP(), UNIX_TIMESTAMP()),
  ('ARENABA0000002', 'ARENAM00000002', 'ARENABAL000002', 'rbl', 'activated', '2323230000000002', 'direct', '900002', 1, 1, UNIX_TIMESTAMP(), UNIX_TIMESTAMP()),
  ('ARENABA0000003', 'ARENAM00000003', 'ARENABAL000003', NULL,  'activated', '2323230099999999', 'shared', '900001', 1, 1, UNIX_TIMESTAMP(), UNIX_TIMESTAMP())
ON DUPLICATE KEY UPDATE id = id;

-- ============================================================================
-- counters (internal/database/migrations/20220526144631_create_counters_table.go)
-- Columns: id, balance_id, free_payouts_consumed_last_reset_at, free_payouts_consumed,
--          account_type, created_at, updated_at.
-- free_payouts_consumed = 0 per task requirement ("free_payout counter 0").
-- ============================================================================

INSERT INTO counters
  (id, balance_id, free_payouts_consumed_last_reset_at, free_payouts_consumed, account_type, created_at, updated_at)
VALUES
  ('ARENACTR000001', 'ARENABAL000001', UNIX_TIMESTAMP(), 0, 'shared', UNIX_TIMESTAMP(), UNIX_TIMESTAMP()),
  ('ARENACTR000002', 'ARENABAL000002', UNIX_TIMESTAMP(), 0, 'direct', UNIX_TIMESTAMP(), UNIX_TIMESTAMP()),
  ('ARENACTR000003', 'ARENABAL000003', UNIX_TIMESTAMP(), 0, 'shared', UNIX_TIMESTAMP(), UNIX_TIMESTAMP())
ON DUPLICATE KEY UPDATE id = id;

-- ============================================================================
-- payout_purpose (internal/database/migrations/20210617163439_create_payout_purpose_table.go)
-- Columns: id, merchant_id, purpose_type (json), created_at, updated_at.
-- purpose_type values below are Razorpay's publicly-documented standard payout purposes
-- (refund, cashback, vendor_bill, utility bill, salary) -- not sensitive/internal data.
-- ============================================================================

INSERT INTO payout_purpose (id, merchant_id, purpose_type, created_at, updated_at) VALUES
  ('ARENAPP0000001', 'ARENAM00000001', '["refund","cashback","vendor_bill","utility bill","salary"]', UNIX_TIMESTAMP(), UNIX_TIMESTAMP()),
  ('ARENAPP0000002', 'ARENAM00000002', '["refund","cashback","vendor_bill","utility bill","salary"]', UNIX_TIMESTAMP(), UNIX_TIMESTAMP()),
  ('ARENAPP0000003', 'ARENAM00000003', '["refund","cashback","vendor_bill","utility bill","salary"]', UNIX_TIMESTAMP(), UNIX_TIMESTAMP())
ON DUPLICATE KEY UPDATE id = id;

-- ============================================================================
-- workflow_config / workflow_state_map (M3 only -- finance_l1/finance_l2/owner two-step approval).
-- Columns per internal/database/migrations/20210824110700_create_workflow_config_table.go and
-- 20220912025953_create_workflow_state_map_table.go.
-- TODO(confirm): config_type/type/group_name/state_status string taxonomy is BEST-EFFORT --
-- the research pass confirmed schema (column names/types) but not the exact free-text values
-- production writes into config_type/type/state_status (these are plain varchar, not DB enums,
-- per the schema extraction). Adjust if a real workflow-service payload sample becomes available.
-- ============================================================================

INSERT INTO workflow_config (id, config_id, config_type, enabled, merchant_id, org_id, created_at, updated_at) VALUES
  ('ARENAWC0000001', 'ARENAWC0000001', 'payout_workflow', 1, 'ARENAM00000003', NULL, UNIX_TIMESTAMP(), UNIX_TIMESTAMP())
ON DUPLICATE KEY UPDATE id = id;

INSERT INTO workflow_state_map
  (id, workflow_id, actor_role, state_id, state_status, group_name, type, count_of_approvals_needed, merchant_id, created_at, updated_at)
VALUES
  ('ARENAWS0000001', 'ARENAWC0000001', 'finance_l1', 'ARENAWST000001', 'pending', 'approvers_l1', 'approval', 1, 'ARENAM00000003', UNIX_TIMESTAMP(), UNIX_TIMESTAMP()),
  ('ARENAWS0000002', 'ARENAWC0000001', 'finance_l2', 'ARENAWST000002', 'pending', 'approvers_l2', 'approval', 1, 'ARENAM00000003', UNIX_TIMESTAMP(), UNIX_TIMESTAMP()),
  ('ARENAWS0000003', 'ARENAWC0000001', 'owner',      'ARENAWST000003', 'pending', 'approvers_owner', 'approval', 1, 'ARENAM00000003', UNIX_TIMESTAMP(), UNIX_TIMESTAMP())
ON DUPLICATE KEY UPDATE id = id;

-- ============================================================================
-- payouts-local fund_accounts / bank_accounts (internal/database/migrations/20220120161944 and
-- 20220120172736). These are thin pointer/cache rows -- account_id references the CFA-side entity by
-- its external "id" field (see cfa.js). account_type: 'bank_account' | 'vpa'.
-- Mirrors the 4 CFA fund accounts (2 bank, 1 VPA, 1 inactive) from cfa.js exactly: ARENAFAX000001
-- (M1 active bank, G1 beneficiary), ARENAFAX000002 (M2 active bank, G2 beneficiary), ARENAFAX000003
-- (M3 active VPA), ARENAFAX000004 (M1's INACTIVE bank fund account, acct ...9999 -- doubles as the
-- G6 inactive-fund-account-reject fixture and, since Shield's scripted block matches ANY beneficiary
-- account ending 9999 regardless of merchant, the G8 Shield-block fixture too).
-- ============================================================================

INSERT INTO fund_accounts (id, account_type, account_id, created_at, updated_at) VALUES
  ('ARENAFA0000001', 'bank_account', 'ARENAFAX000001', UNIX_TIMESTAMP(), UNIX_TIMESTAMP()), -- M1's active bank fund account
  ('ARENAFA0000002', 'bank_account', 'ARENAFAX000002', UNIX_TIMESTAMP(), UNIX_TIMESTAMP()), -- M2's active bank fund account
  ('ARENAFA0000003', 'vpa',          'ARENAFAX000003', UNIX_TIMESTAMP(), UNIX_TIMESTAMP()), -- M3's active VPA fund account
  ('ARENAFA0000004', 'bank_account', 'ARENAFAX000004', UNIX_TIMESTAMP(), UNIX_TIMESTAMP())  -- M1's INACTIVE bank fund account (ends 9999)
ON DUPLICATE KEY UPDATE id = id;

INSERT INTO bank_accounts (id, ifsc_code, bank_identifier, identifier_type, created_at, updated_at) VALUES
  ('ARENABK0000001', 'ARNA0000001', NULL, 'IFSC', UNIX_TIMESTAMP(), UNIX_TIMESTAMP()), -- for ARENAFA0000001
  ('ARENABK0000002', 'ARNA0000001', NULL, 'IFSC', UNIX_TIMESTAMP(), UNIX_TIMESTAMP()), -- for ARENAFA0000002
  ('ARENABK0000004', 'ARNA0000001', NULL, 'IFSC', UNIX_TIMESTAMP(), UNIX_TIMESTAMP())  -- for ARENAFA0000004 (inactive)
ON DUPLICATE KEY UPDATE id = id;
