-- Canonical API DB subset is mounted in this MySQL container by Compose.
-- Source objects and confidence: seeds/mysql/apidb-ddl/00_init.sql.
-- This creates missing objects on an empty arena, not speculative per-column patches.
SOURCE /docker-entrypoint-initdb.d/00_init.sql;

-- Fail clearly if old volumes retain the incompatible previous draft. The
-- supported migration is scripts/down.sh followed by up.sh from empty volumes.
CREATE TEMPORARY TABLE arena_api_schema_guard (valid TINYINT NOT NULL CHECK (valid = 1));
INSERT INTO arena_api_schema_guard
SELECT CASE WHEN
  (SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name IN ('fund_transfer_attempt','banking_account','payout_status_details')) = 0
  AND (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='payouts_details' AND column_name IN ('id','beneficiary_bank_code')) = 0
  AND (SELECT COUNT(*) FROM information_schema.key_column_usage WHERE table_schema=DATABASE() AND table_name='payouts_details' AND constraint_name='PRIMARY' AND column_name='payout_id') = 1
  AND (SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='features' AND index_name='features_name_entity_id_unique' AND non_unique=0) = 2
THEN 1 ELSE 0 END;
DROP TEMPORARY TABLE arena_api_schema_guard;
