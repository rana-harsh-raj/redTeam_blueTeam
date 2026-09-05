-- FTS (Fund Transfer Service, MySQL 8.0) seed for the Env2 arena.
-- Ground truth: fts/internal/migrations/*.go (00001,00002,00003,00007,00011,00017,00018,00019 +
-- channel-enum-widening 00047/00049). Confirmed against findings/28_build_spike_fts.md (45 tables,
-- goose version 50).
--
-- Integer PKs (bank_accounts, vpas, fund_accounts, source_accounts, source_account_mappings,
-- account_type_mappings, preferred_routing_weights, transfers) are all INT(11) AUTO_INCREMENT in the
-- real schema -- there is no CHAR(14)-style id here, so this seed uses explicit fixed ids in the
-- 900001+ block (well clear of default auto-increment start at 1) to keep them recognisably
-- "arena-owned" without colliding with anything auto-generated at runtime. merchant_id columns are
-- CHAR(14)-compatible strings, reusing the same ARENAM... ids as payouts.sql/ledger.sql.
--
-- Modeling note: ARENAM00000000 is a synthetic PLATFORM/POOL-OWNER pseudo-merchant, not one of the
-- task's 3 seeded merchants -- it exists only so fund_accounts.merchant_id (NOT NULL) has a value to
-- own the shared RBL pool nodal bank account that M1 and M3 route through via
-- source_account_mappings. It is not itself a payouts-service merchant and has no banking_accounts/
-- counters/payout_purpose row of its own.
--
-- Not seeded here (populated at runtime): transfers, merchant_configurations (no confirmed real
-- key/value taxonomy for this generic per-merchant KV table was found in the research pass).

-- ============================================================================
-- fts-local bank_accounts (00001) -- the actual account_number/ifsc_code identity.
-- ============================================================================
INSERT INTO bank_accounts
  (id, product, merchant_id, account_type, account_number, ifsc_code, bank_identifier, identifier_type, is_virtual_account, beneficiary_name, beneficiary_city, beneficiary_state, beneficiary_country, created_at, updated_at)
VALUES
  (900001, 'PAYOUT', 'ARENAM00000000', 'NODAL',   '2323230099999999', 'ARNA0000001', NULL, NULL, 0, 'ARENA RZPX POOL NODAL ACCOUNT', NULL, NULL, NULL, UNIX_TIMESTAMP(), UNIX_TIMESTAMP()),
  (900002, 'PAYOUT', 'ARENAM00000002', 'CURRENT', '2323230000000002', 'ARNA0000001', NULL, NULL, 0, 'ARENA MERCHANT TWO PRIVATE LIMITED', NULL, NULL, NULL, UNIX_TIMESTAMP(), UNIX_TIMESTAMP())
ON DUPLICATE KEY UPDATE id = id;

-- ============================================================================
-- fts-local fund_accounts (00003) -- points bank_accounts row into the generic fund-account
-- abstraction (account_type=BANK_ACCOUNT, account_id -> bank_accounts.id).
-- ============================================================================
INSERT INTO fund_accounts
  (id, product, merchant_id, account_type, account_id, default_channel, created_at, updated_at)
VALUES
  (900001, 'PAYOUT', 'ARENAM00000000', 'BANK_ACCOUNT', 900001, 'RBL', UNIX_TIMESTAMP(), UNIX_TIMESTAMP()),
  (900002, 'PAYOUT', 'ARENAM00000002', 'BANK_ACCOUNT', 900002, 'RBL', UNIX_TIMESTAMP(), UNIX_TIMESTAMP())
ON DUPLICATE KEY UPDATE id = id;

-- ============================================================================
-- source_accounts (00007) -- the routable bank/nodal source, one per fund_account.
-- mozart_identifier: TODO(confirm) -- exact string format fts/internal/providers/mozart expects was
-- not independently confirmed in this pass (only the /{namespace}/{gateway}/{version}/{action} URL
-- shape and testdata directory layout were, from findings/25_stork_mozart.md). 'v1' is a
-- plausible placeholder matching the gateway/version pattern seen in mozart/app/testdata/fts/rbl/v1/.
-- ============================================================================
INSERT INTO source_accounts
  (id, product, channel, fund_account_id, mozart_identifier, bank_account_type, account_type, credentials, configuration, created_at, updated_at)
VALUES
  -- credentials/configuration must be JSON objects: fts unmarshals them at gateway-call time (internal/gateway/service.go:291); synthetic values, mozart-sim ignores them
  (900001, 'PAYOUT', 'RBL', 900001, 'v1', 'NODAL',   'POOL',   '{"client_id":"arena-rbl-client","client_secret":"arena-rbl-secret","corp_id":"ARENACORP","user_id":"arena"}', '{}', UNIX_TIMESTAMP(), UNIX_TIMESTAMP()),
  (900002, 'PAYOUT', 'RBL', 900002, 'v1', 'CURRENT', 'DIRECT', '{"client_id":"arena-rbl-client","client_secret":"arena-rbl-secret","corp_id":"ARENACORP","user_id":"arena"}', '{}', UNIX_TIMESTAMP(), UNIX_TIMESTAMP())
ON DUPLICATE KEY UPDATE id = id;

-- ============================================================================
-- source_account_mappings (00011) -- merchant_id + channel + mode + product + operation=TRANSFER ->
-- source_account_id, with routing_enabled=1. M1/M3 (Shared) both map to the pool (900001); M2
-- (Direct) maps to its own dedicated account (900002). One row per (merchant, mode) so both IMPS and
-- NEFT resolve for /v1/transfer, per the task's "channel RBL mode IMPS/NEFT resolves" requirement.
-- ============================================================================
INSERT INTO source_account_mappings
  (operation, merchant_id, product, channel, mozart_identifier, source_account_id, source_account_type, account_type, mode, title, priority, routing_enabled, created_at, created_by, updated_at, integration_type, creation_reason, deletion_reason)
VALUES
  ('TRANSFER', 'ARENAM00000001', 'PAYOUT', 'RBL', 'v1', 900001, 'NODAL',   'POOL',   'IMPS', 'arena_m1_imps', 1, 1, UNIX_TIMESTAMP(), 'arena_seed', UNIX_TIMESTAMP(), 'API', 'arena env2 seed', NULL),
  ('TRANSFER', 'ARENAM00000001', 'PAYOUT', 'RBL', 'v1', 900001, 'NODAL',   'POOL',   'NEFT', 'arena_m1_neft', 1, 1, UNIX_TIMESTAMP(), 'arena_seed', UNIX_TIMESTAMP(), 'API', 'arena env2 seed', NULL),
  ('TRANSFER', 'ARENAM00000003', 'PAYOUT', 'RBL', 'v1', 900001, 'NODAL',   'POOL',   'IMPS', 'arena_m3_imps', 1, 1, UNIX_TIMESTAMP(), 'arena_seed', UNIX_TIMESTAMP(), 'API', 'arena env2 seed', NULL),
  ('TRANSFER', 'ARENAM00000003', 'PAYOUT', 'RBL', 'v1', 900001, 'NODAL',   'POOL',   'NEFT', 'arena_m3_neft', 1, 1, UNIX_TIMESTAMP(), 'arena_seed', UNIX_TIMESTAMP(), 'API', 'arena env2 seed', NULL),
  ('TRANSFER', 'ARENAM00000002', 'PAYOUT', 'RBL', 'v1', 900002, 'CURRENT', 'DIRECT', 'IMPS', 'arena_m2_imps', 1, 1, UNIX_TIMESTAMP(), 'arena_seed', UNIX_TIMESTAMP(), 'API', 'arena env2 seed', NULL),
  ('TRANSFER', 'ARENAM00000002', 'PAYOUT', 'RBL', 'v1', 900002, 'CURRENT', 'DIRECT', 'NEFT', 'arena_m2_neft', 1, 1, UNIX_TIMESTAMP(), 'arena_seed', UNIX_TIMESTAMP(), 'API', 'arena env2 seed', NULL);

-- ============================================================================
-- account_type_mappings (00018) -- (merchant, product, mode) -> account_type (NODAL for Shared pool,
-- CURRENT for Direct).
-- ============================================================================
INSERT INTO account_type_mappings (mode, product, account_type, merchant_id, created_at, created_by, updated_at) VALUES
  ('IMPS', 'PAYOUT', 'NODAL',   'ARENAM00000001', UNIX_TIMESTAMP(), 'arena_seed', UNIX_TIMESTAMP()),
  ('NEFT', 'PAYOUT', 'NODAL',   'ARENAM00000001', UNIX_TIMESTAMP(), 'arena_seed', UNIX_TIMESTAMP()),
  ('IMPS', 'PAYOUT', 'NODAL',   'ARENAM00000003', UNIX_TIMESTAMP(), 'arena_seed', UNIX_TIMESTAMP()),
  ('NEFT', 'PAYOUT', 'NODAL',   'ARENAM00000003', UNIX_TIMESTAMP(), 'arena_seed', UNIX_TIMESTAMP()),
  ('IMPS', 'PAYOUT', 'CURRENT', 'ARENAM00000002', UNIX_TIMESTAMP(), 'arena_seed', UNIX_TIMESTAMP()),
  ('NEFT', 'PAYOUT', 'CURRENT', 'ARENAM00000002', UNIX_TIMESTAMP(), 'arena_seed', UNIX_TIMESTAMP());

-- ============================================================================
-- preferred_routing_weights (00017) -- weighted selection among source_accounts eligible for a
-- given (mode, product). Only one source_account per (channel, merchant-class) exists in this arena,
-- so weight is nominal (100) rather than meaningfully discriminating.
-- ============================================================================
INSERT INTO preferred_routing_weights (mode, product, source_account_id, preferred_routing_weight, created_at, created_by, updated_at) VALUES
  ('IMPS', 'PAYOUT', 900001, 100, UNIX_TIMESTAMP(), 'arena_seed', UNIX_TIMESTAMP()),
  ('NEFT', 'PAYOUT', 900001, 100, UNIX_TIMESTAMP(), 'arena_seed', UNIX_TIMESTAMP()),
  ('IMPS', 'PAYOUT', 900002, 100, UNIX_TIMESTAMP(), 'arena_seed', UNIX_TIMESTAMP()),
  ('NEFT', 'PAYOUT', 900002, 100, UNIX_TIMESTAMP(), 'arena_seed', UNIX_TIMESTAMP());

-- Direct-account (M2, RBL current account) routing: fts resolves DIRECT source accounts through
-- direct_account_routing_rules (merchant_id + product + channel + mode -> source_account_id); without
-- these rows a direct transfer fails with UNABLE_TO_DETERMINE_TRANSFER_SOURCE_ACCOUNT.
INSERT IGNORE INTO direct_account_routing_rules
  (id, merchant_id, product, channel, mozart_identifier, source_account_id, mode, created_at, created_by, updated_at)
VALUES
  (1, 'ARENAM00000002', 'PAYOUT', 'RBL', 'v1', 900002, 'IMPS', UNIX_TIMESTAMP(), 'arena', UNIX_TIMESTAMP()),
  (2, 'ARENAM00000002', 'PAYOUT', 'RBL', 'v1', 900002, 'NEFT', UNIX_TIMESTAMP(), 'arena', UNIX_TIMESTAMP());

-- merchant-level credentials are merged into the source-account credentials at gateway-call time (fts internal/gateway/service.go:325); must be a JSON object
UPDATE source_account_mappings SET credentials = '{}' WHERE credentials IS NULL;

-- Channel health: fts keeps a source-account mapping only if channel_information_status has an UP row for
-- (channel, mode, mozart_identifier, integration_type) (internal/account/service.go filterSourceAccountMappingByChannelHealth);
-- status 100 = up (internal/channel/service.go GetChannelHealthMapForTransfer). Without rows every mapping is filtered out.
INSERT IGNORE INTO channel_information_status
  (id, mode, status, channel, account_type, mozart_identifier, integration_type, created_at, updated_at, source_id, source_account_type)
VALUES
  (1, 'IMPS', 100, 'RBL', 'POOL',   'v1', 'API', UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), '', 'NODAL'),
  (2, 'NEFT', 100, 'RBL', 'POOL',   'v1', 'API', UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), '', 'NODAL'),
  (3, 'IMPS', 100, 'RBL', 'DIRECT', 'v1', 'API', UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), '', 'CURRENT'),
  (4, 'NEFT', 100, 'RBL', 'DIRECT', 'v1', 'API', UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), '', 'CURRENT'),
  -- the health query keys on (account_type = bank account type, source_account_type = POOL/DIRECT): FETCH_CHANNEL_HEALTHS conditions
  (5, 'IMPS', 100, 'RBL', 'CURRENT', 'v1', 'API', UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), '', 'POOL'),
  (6, 'NEFT', 100, 'RBL', 'CURRENT', 'v1', 'API', UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), '', 'POOL'),
  (7, 'IMPS', 100, 'RBL', 'NODAL',   'v1', 'API', UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), '', 'POOL'),
  (8, 'NEFT', 100, 'RBL', 'NODAL',   'v1', 'API', UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), '', 'POOL'),
  (9, 'IMPS', 100, 'RBL', 'CURRENT', 'v1', 'API', UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), '', 'DIRECT'),
  (10,'NEFT', 100, 'RBL', 'CURRENT', 'v1', 'API', UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), '', 'DIRECT');
