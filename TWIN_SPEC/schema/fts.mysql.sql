-- fts.mysql.sql
-- fts (Fund Transfer Service) core schema, derived MECHANICALLY from goose migrations under
-- fts/internal/migrations/*.go. Only the tables the twin's own seeds/s4/fts.sql seeds, plus the
-- transfer/attempt "core" tables named in the lane brief, are reproduced in full. Other 40+ fts
-- tables (cards, wallets, otp_*, schedules, downtimes, ...) exist but are out of this twin's scope;
-- see fts/internal/migrations/ for their DDL if ever needed. NO DATA ROWS.
--
-- ENUM DRIFT WARNING (confirmed against both migrations and code, see report body):
--  - `WALLET_TRANSFER` is a code-required value for transfers.mode/attempts.mode (required for the
--    AMAZON_PAY channel: internal/channel/channel.go:90, internal/common/constants.go:10) but is
--    ABSENT from every numbered goose migration's mode ENUM (00005:20,29; 00006:25). It only appears
--    in the untracked internal/migrations/queries.txt:346-347,354 (a hand-maintained ALTER log never
--    wired into goose). Included below as a 9th value so the twin does not reject it; production's
--    real tracked-migration schema would reject it (MySQL ENUM-insert behavior depends on sql_mode).
--  - `INDUSIND` is present in the migration-tracked channel ENUM since 00047 but has ZERO references
--    in fts Go code (internal/channel/channel.go's channel list omits it) — i.e. the DB would accept
--    it but application code can never produce or validate it. Included in the ENUM below for
--    schema-fidelity; do not expect fts code to ever read/write this value.

CREATE TABLE IF NOT EXISTS `bank_accounts` ( -- 00001_bank_accounts_table_create.go:14 (INT PK, not CHAR(14) — differs from payouts/x-balances convention)
  `id` INT(11) NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `ifsc` VARCHAR(20) NULL,
  `account_number` VARCHAR(40) NULL,
  `beneficiary_name` VARCHAR(255) NULL,
  `created_at` INT(11) NOT NULL,
  `updated_at` INT(11) NOT NULL
) ENGINE=InnoDB;
-- NOTE: only the subset of columns the twin's seeds/s4/fts.sql inserts is guaranteed accurate here;
-- the full bank_accounts DDL was not independently re-verified column-by-column in this pass (fts
-- lane focused on transfers/attempts per the brief) — treat other bank_accounts columns as
-- REPRESENTATIVE, not EXACT, and confirm against 00001_bank_accounts_table_create.go before relying
-- on them.

CREATE TABLE IF NOT EXISTS `fund_accounts` ( -- 00003_fund_accounts_table_create.go:14 -- fts's OWN fund_accounts (generic account_type+account_id pointer), distinct from cfa's Mongo fund_accounts and payouts-service's own fund_accounts table
  `id` INT(11) NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `account_type` VARCHAR(50) NULL,   -- e.g. BANK_ACCOUNT
  `account_id` INT(11) NULL,
  `merchant_id` CHAR(14) NULL,
  `created_at` INT(11) NOT NULL,
  `updated_at` INT(11) NOT NULL
) ENGINE=InnoDB;
-- Same caveat as bank_accounts above — column list REPRESENTATIVE (twin's fts.sql use verified, full
-- migration not re-transcribed here).

CREATE TABLE IF NOT EXISTS `transfers` ( -- 00005_transfers_table_create.go:14-63 (verbatim CREATE, minor ENUM extension noted above)
  `id` INT(11) NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `product` ENUM('PAYOUT','PAYOUT_REFUND','SETTLEMENT','REFUND','PENNY_TESTING','ES_ON_DEMAND','CA_PAYOUT','CUSTOMER_WALLET','CUSTOMER_PAYOUT') DEFAULT NULL,
  `merchant_id` CHAR(14) NOT NULL,
  `source_type` VARCHAR(50) NOT NULL,
  `source_id` VARCHAR(14) NOT NULL,
  `preferred_mode` ENUM('IMPS','NEFT','RTGS','IFT','UPI','CT','WALLET_TRANSFER','DUITNOW','IBG') DEFAULT NULL, -- WALLET_TRANSFER added here per header warning; tracked migration (00005) omits it
  `preferred_channel` ENUM('AXIS','CITI','HDFC','ICICI','RBL','YESBANK','M2P','AMAZON_PAY','INDUSIND','MCS','IDFC','OCBC','SLICE','JPMC_INTL_BANK_TRANSFER') DEFAULT NULL, -- final state after 00047/00049/00050
  `preferred_source_account_id` INT(11) DEFAULT NULL,
  `fund_account_id` INT(11) NOT NULL,
  `attempts` INT(11) NOT NULL DEFAULT 0,
  `channel` ENUM('AXIS','CITI','HDFC','ICICI','RBL','YESBANK','M2P','AMAZON_PAY','INDUSIND','MCS','IDFC','OCBC','SLICE','JPMC_INTL_BANK_TRANSFER') DEFAULT NULL,
  `source_account_id` INT(11) DEFAULT NULL,
  `source_account_primary_id` INT(11) UNSIGNED DEFAULT NULL,
  `amount` BIGINT(20) UNSIGNED NOT NULL,
  `mode` ENUM('IMPS','NEFT','RTGS','IFT','UPI','CT','WALLET_TRANSFER','DUITNOW','IBG') DEFAULT NULL,
  `status` ENUM('CREATED','INITIATED','PROCESSED','FAILED','REVERSED','RETRY','USER_PENDING') NOT NULL DEFAULT 'CREATED',
  `utr` VARCHAR(255) DEFAULT NULL,
  `return_utr` VARCHAR(255) DEFAULT NULL,
  `narration` VARCHAR(255) DEFAULT NULL,
  `remarks` VARCHAR(255) DEFAULT NULL,
  `bank_status_code` VARCHAR(100) DEFAULT NULL,
  `failure_reason` VARCHAR(255) DEFAULT NULL,
  `bank_processed_time` VARCHAR(255) DEFAULT NULL,
  `initiate_at` INT(11) NOT NULL,
  `transfer_by` INT(11) DEFAULT NULL,
  `extra_info` TEXT DEFAULT NULL,
  `retry` TINYINT(1) NOT NULL DEFAULT 0,
  `created_at` INT(11) NOT NULL,
  `updated_at` INT(11) NOT NULL,
  `gateway_error_code` VARCHAR(255) DEFAULT NULL,
  `is_batch` TINYINT(1) NOT NULL DEFAULT 0,
  `type` ENUM('FILE','API') DEFAULT NULL,
  `batch_id` VARCHAR(14) DEFAULT NULL,
  `credited_at` VARCHAR(255) DEFAULT NULL,
  `is_credited` TINYINT(1) NOT NULL DEFAULT 0,
  `is_debited` TINYINT(1) NOT NULL DEFAULT 0,
  `request_meta` JSON DEFAULT NULL,
  KEY `transfers_merchant_id_product_index` (`merchant_id`,`product`),
  KEY `transfers_fund_account_id_index` (`fund_account_id`),
  KEY `transfers_source_account_primary_id_index` (`source_account_primary_id`),
  KEY `transfers_source_id_source_type_index` (`source_id`,`source_type`),
  KEY `transfers_status_index` (`status`),
  KEY `transfers_channel_index` (`channel`),
  KEY `transfers_retry_index` (`retry`),
  KEY `transfers_created_at_index` (`created_at`),
  KEY `transfers_updated_at_index` (`updated_at`),
  UNIQUE KEY `unique_source` (`source_type`,`source_id`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `attempts` ( -- 00006_attempts_table_create.go:14-60 (verbatim, + WALLET_TRANSFER added to mode ENUM per header warning)
  `id` INT(11) NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `product` ENUM('PAYOUT','PAYOUT_REFUND','SETTLEMENT','REFUND','PENNY_TESTING','ES_ON_DEMAND','CA_PAYOUT','CUSTOMER_WALLET','CUSTOMER_PAYOUT') DEFAULT NULL,
  `merchant_id` CHAR(14) NOT NULL,
  `transfer_id` INT(11) NOT NULL,
  `channel` ENUM('AXIS','CITI','HDFC','ICICI','RBL','YESBANK','M2P','AMAZON_PAY','INDUSIND','MCS','IDFC','OCBC','SLICE','JPMC_INTL_BANK_TRANSFER') NOT NULL,
  `source_account_id` INT(11) NOT NULL,
  `source_account_primary_id` INT(11) UNSIGNED DEFAULT NULL,
  `amount` BIGINT(20) UNSIGNED NOT NULL,
  `fund_account_id` INT(11) NOT NULL,
  `bank_status_code` VARCHAR(100) DEFAULT NULL,
  `mode` ENUM('IMPS','NEFT','RTGS','IFT','UPI','CT','WALLET_TRANSFER','DUITNOW','IBG') NOT NULL,
  `status` ENUM('CREATED','INITIATED','PROCESSED','FAILED','REVERSED','RETRY','FAILED_INTERNAL') NOT NULL DEFAULT 'CREATED',
  `utr` VARCHAR(255) DEFAULT NULL,
  `return_utr` VARCHAR(255) DEFAULT NULL,
  `gateway_ref_no` VARCHAR(50) DEFAULT NULL,
  `narration` VARCHAR(255) DEFAULT NULL,
  `remarks` VARCHAR(255) DEFAULT NULL,
  `failure_reason` VARCHAR(255) DEFAULT NULL,
  `bank_processed_time` VARCHAR(255) DEFAULT NULL,
  `cms_ref_no` VARCHAR(255) DEFAULT NULL,
  `initiate_at` INT(11) NOT NULL,
  `acknowledged_at` INT(11) DEFAULT 0,
  `transfer_response` TEXT DEFAULT NULL,
  `status_response` TEXT DEFAULT NULL,
  `extra_info` TEXT DEFAULT NULL,
  `created_at` INT(11) NOT NULL,
  `updated_at` INT(11) NOT NULL,
  `gateway_error_code` VARCHAR(255) DEFAULT NULL,
  `type` ENUM('FILE','API') DEFAULT NULL,
  `batch_id` VARCHAR(14) DEFAULT NULL,
  `gateway_error_code_init` VARCHAR(255) DEFAULT NULL,
  `credited_at` VARCHAR(255) DEFAULT NULL,
  `is_credited` TINYINT(1) NOT NULL DEFAULT 0,
  `is_debited` TINYINT(1) NOT NULL DEFAULT 0,
  `re_initiate_count` TINYINT NOT NULL DEFAULT 0,
  UNIQUE KEY `gateway_ref_no_unique` (`gateway_ref_no`),
  KEY `attempts_merchant_id_index` (`merchant_id`),
  KEY `attempts_transfer_id_index` (`transfer_id`),
  KEY `attempts_source_account_primary_id_index` (`source_account_primary_id`),
  KEY `attempts_status_index` (`status`),
  KEY `attempts_channel_index` (`channel`),
  KEY `attempts_bank_status_code_index` (`bank_status_code`),
  KEY `attempts_created_at_index` (`created_at`),
  KEY `attempts_updated_at_index` (`updated_at`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `source_accounts` ( -- 00007_source_accounts_table_create.go:14 -- column list REPRESENTATIVE, only twin-seeded columns confirmed
  `id` INT(11) NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `merchant_id` CHAR(14) NULL,
  `channel` VARCHAR(50) NULL,
  `account_number` VARCHAR(40) NULL,
  `created_at` INT(11) NOT NULL,
  `updated_at` INT(11) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `attempts_unique_keys` ( -- 00021_attempts_unique_keys_table_create.go:14-18 -- dedup table: gateway_ref_no IS the primary key (single-column, no composite)
  `gateway_ref_no` VARCHAR(50) NOT NULL PRIMARY KEY COMMENT 'unique payment end to end id for gateway/bank',
  `created_at` INT(11) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `transfer_meta` ( -- 00045_transfer_meta_table_create.go:14-28 -- NOTE: no UNIQUE on (request_id, origin_service) despite looking dedup-shaped; dedup (if any) is app-level, not found in internal/transfer/transfer_meta.go
  `id` INT(11) NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `transfer_id` INT(11) NOT NULL,
  `request_id` VARCHAR(255) NOT NULL,
  `origin_service` VARCHAR(255) NOT NULL,
  `source_id` VARCHAR(255) NOT NULL,
  `created_at` INT(11) NOT NULL,
  `updated_at` INT(11) NOT NULL,
  KEY `transfer_id_index` (`transfer_id`),
  KEY `request_id_index` (`request_id`),
  KEY `origin_service_index` (`origin_service`),
  KEY `source_id_index` (`source_id`),
  KEY `created_at_index` (`created_at`)
) ENGINE=InnoDB;

-- account_type_mappings, preferred_routing_weights, source_account_mappings: seeded by the twin
-- (seeds/s4/fts.sql references 00011/00017/00018) but full column DDL was not re-verified in this
-- pass beyond what the twin's own seed comments assert — REPRESENTATIVE only, confirm against
-- 00011_source_account_mappings_table_create.go / 00017_preferred_routing_weights_table_create.go /
-- 00018_account_type_mappings_table_create.go before trusting column names beyond the seed's own use.
