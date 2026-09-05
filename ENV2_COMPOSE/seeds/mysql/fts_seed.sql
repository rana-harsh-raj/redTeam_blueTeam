-- seeds/mysql/fts_seed.sql
-- FTS source_accounts + routing rules for the 3 synthetic merchants. Run
-- AFTER fts-migrate (scripts/up.sh's seed step).
--
-- TODO (explicit): FTS's real schema (fts/internal/migrations or
-- equivalent) was not read in this pass -- table/column names below are a
-- best-effort skeleton matching the concept names used throughout
-- fts/config/env.default.toml ("source_accounts", per-gateway routing) and
-- findings/25_stork_mozart.md's gateway/version naming (rbl v1, etc), NOT
-- verified against a real migration file. Confirm before relying on this.

-- TODO: confirm real table name and columns.
INSERT INTO source_accounts (id, merchant_id, account_number, channel, gateway, gateway_version, status, created_at)
VALUES
  ('sa_ARENA_M1', 'ARENA_M1', '111000000000001', 'rbl', 'rbl', 'v1', 'active', UNIX_TIMESTAMP()),
  ('sa_ARENA_M2', 'ARENA_M2', '222000000000002', 'rbl', 'rbl', 'v1', 'active', UNIX_TIMESTAMP()),
  ('sa_ARENA_M3', 'ARENA_M3', '333000000000003', 'rbl', 'rbl', 'v1', 'active', UNIX_TIMESTAMP())
ON DUPLICATE KEY UPDATE status = VALUES(status);

-- TODO: confirm real routing-rule table name/shape -- this is a guess at
-- "which gateway/version handles which merchant+mode" resolution FTS must
-- do somewhere before calling mozart. Every arena merchant routes to the
-- same rbl/v1 gateway for both IMPS/NEFT modes for simplicity; scenario
-- variation should come from the amount-keyed mozart-sim/mozart-mock
-- fixtures (see substitutes/mozart-sim/CONTRACT.md), not from routing.
INSERT INTO routing_rules (id, merchant_id, mode, gateway, gateway_version, priority)
VALUES
  ('rr_ARENA_M1_imps', 'ARENA_M1', 'IMPS', 'rbl', 'v1', 1),
  ('rr_ARENA_M1_neft', 'ARENA_M1', 'NEFT', 'rbl', 'v1', 1),
  ('rr_ARENA_M2_imps', 'ARENA_M2', 'IMPS', 'rbl', 'v1', 1),
  ('rr_ARENA_M2_neft', 'ARENA_M2', 'NEFT', 'rbl', 'v1', 1),
  ('rr_ARENA_M3_imps', 'ARENA_M3', 'IMPS', 'rbl', 'v1', 1),
  ('rr_ARENA_M3_neft', 'ARENA_M3', 'NEFT', 'rbl', 'v1', 1)
ON DUPLICATE KEY UPDATE priority = VALUES(priority);
