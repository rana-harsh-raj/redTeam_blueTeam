-- Arena schema + data patch for the API-monolith stub database (api_local), applied by scripts/up.sh after
-- migrations. The DDL skeleton in seeds/mysql/apidb-ddl/00_init.sql was reconstructed from the BOM before the
-- payouts GORM models were read; this patch aligns it with what payouts actually reads over [db.api]
-- (internal/app/settings/model.go Balance -> table `balance`, merchant/feature.go -> `features`,
-- workflowEntityMap, reversals, payouts_details, payouts_status_details, fund_transfer_attempt). Idempotent.
-- Every row is synthetic (ARENA* ids); no production data.
SET @c := (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'balance' AND column_name = 'type');
SET @s := IF(@c = 0, 'ALTER TABLE `balance` ADD COLUMN `type` VARCHAR(32) NULL', 'SELECT 1'); PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;
SET @c := (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'balance' AND column_name = 'primary');
SET @s := IF(@c = 0, 'ALTER TABLE `balance` ADD COLUMN `primary` TINYINT NULL', 'SELECT 1'); PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;
SET @c := (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'balance' AND column_name = 'name');
SET @s := IF(@c = 0, 'ALTER TABLE `balance` ADD COLUMN `name` VARCHAR(255) NULL', 'SELECT 1'); PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;
SET @c := (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'balance' AND column_name = 'on_hold');
SET @s := IF(@c = 0, 'ALTER TABLE `balance` ADD COLUMN `on_hold` BIGINT NULL DEFAULT 0', 'SELECT 1'); PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;
SET @c := (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'balance' AND column_name = 'credits');
SET @s := IF(@c = 0, 'ALTER TABLE `balance` ADD COLUMN `credits` BIGINT NULL DEFAULT 0', 'SELECT 1'); PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;
SET @c := (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'balance' AND column_name = 'fee_credits');
SET @s := IF(@c = 0, 'ALTER TABLE `balance` ADD COLUMN `fee_credits` BIGINT NULL DEFAULT 0', 'SELECT 1'); PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;
SET @c := (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'balance' AND column_name = 'refund_credits');
SET @s := IF(@c = 0, 'ALTER TABLE `balance` ADD COLUMN `refund_credits` BIGINT NULL DEFAULT 0', 'SELECT 1'); PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;
SET @c := (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'balance' AND column_name = 'account_number');
SET @s := IF(@c = 0, 'ALTER TABLE `balance` ADD COLUMN `account_number` VARCHAR(64) NULL', 'SELECT 1'); PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;
SET @c := (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'balance' AND column_name = 'account_type');
SET @s := IF(@c = 0, 'ALTER TABLE `balance` ADD COLUMN `account_type` VARCHAR(32) NULL', 'SELECT 1'); PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;
SET @c := (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'balance' AND column_name = 'channel');
SET @s := IF(@c = 0, 'ALTER TABLE `balance` ADD COLUMN `channel` VARCHAR(32) NULL', 'SELECT 1'); PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;
SET @c := (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'balance' AND column_name = 'locked_balance');
SET @s := IF(@c = 0, 'ALTER TABLE `balance` ADD COLUMN `locked_balance` BIGINT NULL DEFAULT 0', 'SELECT 1'); PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;
SET @c := (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'balance' AND column_name = 'created_at');
SET @s := IF(@c = 0, 'ALTER TABLE `balance` ADD COLUMN `created_at` INT NULL', 'SELECT 1'); PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;
SET @c := (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'balance' AND column_name = 'updated_at');
SET @s := IF(@c = 0, 'ALTER TABLE `balance` ADD COLUMN `updated_at` INT NULL', 'SELECT 1'); PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;
CREATE TABLE IF NOT EXISTS `features` (id CHAR(14) PRIMARY KEY, entity_id CHAR(14), entity_type VARCHAR(32), name VARCHAR(255), created_at INT, updated_at INT, KEY (entity_id, entity_type));
CREATE TABLE IF NOT EXISTS `workflow_entity_map` (id CHAR(14) PRIMARY KEY, workflow_id CHAR(14), entity_id CHAR(14), config_id CHAR(14), entity_type VARCHAR(64), merchant_id CHAR(14), org_id CHAR(14), created_at INT, updated_at INT, KEY (entity_id, entity_type));
CREATE TABLE IF NOT EXISTS `reversals` (id CHAR(14) PRIMARY KEY, merchant_id CHAR(14), entity_id CHAR(14), entity_type VARCHAR(32), payout_id CHAR(14), balance_id CHAR(14), channel VARCHAR(32), currency CHAR(3), notes TEXT, fee BIGINT, fees BIGINT, tax BIGINT, transaction_id CHAR(14), amount BIGINT, utr VARCHAR(64), created_at INT, updated_at INT);
CREATE TABLE IF NOT EXISTS `payouts_details` (id CHAR(14) PRIMARY KEY, payout_id CHAR(14), queue_if_low_balance_flag TINYINT, tds_category_id INT NULL, tax_payment_id CHAR(14) NULL, additional_info TEXT NULL, beneficiary_bank_code VARCHAR(50) NULL, created_at INT, updated_at INT, KEY (payout_id));
CREATE TABLE IF NOT EXISTS `payouts_status_details` (id CHAR(14) PRIMARY KEY, payout_id CHAR(14), status VARCHAR(32), reason VARCHAR(255), description TEXT, mode VARCHAR(32), triggered_by VARCHAR(64), created_at INT, updated_at INT, KEY (payout_id));
CREATE TABLE IF NOT EXISTS `fund_transfer_attempt` (id CHAR(14) PRIMARY KEY, source_id CHAR(14), source_type VARCHAR(32), merchant_id CHAR(14), purpose VARCHAR(64), bank_account_id CHAR(14), vpa_id CHAR(14), card_id CHAR(14), wallet_account_id CHAR(14), batch_fund_transfer_id CHAR(14), channel VARCHAR(32), source_account_id CHAR(14), bank_account_type VARCHAR(32), version VARCHAR(8), bank_status_code VARCHAR(64), bank_response_code VARCHAR(64), mode VARCHAR(16), status VARCHAR(32), utr VARCHAR(64), narration VARCHAR(255), remarks VARCHAR(255), date_time INT, cms_ref_no VARCHAR(64), failure_reason VARCHAR(255), fts_transfer_id BIGINT, created_at INT, updated_at INT, KEY (source_id, source_type));
-- synthetic merchant balances mirrored from x-balances / payouts seeds (ids from SYNTHETIC_FIXTURE_SPEC.md)
INSERT INTO `balance` (id, merchant_id, balance, currency, type, `primary`, name, on_hold, credits, fee_credits, refund_credits, account_number, account_type, channel, locked_balance, created_at, updated_at) VALUES
  ('ARENABAL000001','ARENAM00000001',100000000,'INR','banking',1,'ARENA M1 shared',0,0,0,0,'2323230099999999','shared',NULL,0,UNIX_TIMESTAMP()-90*86400,UNIX_TIMESTAMP()),
  ('ARENABAL000002','ARENAM00000002',100000000,'INR','banking',1,'ARENA M2 direct',0,0,0,0,'2323230000000002','direct','rbl',0,UNIX_TIMESTAMP()-90*86400,UNIX_TIMESTAMP()),
  ('ARENABAL000003','ARENAM00000003',100000000,'INR','banking',1,'ARENA M3 shared',0,0,0,0,'2323230099999999','shared',NULL,0,UNIX_TIMESTAMP()-90*86400,UNIX_TIMESTAMP())
ON DUPLICATE KEY UPDATE id = id;
