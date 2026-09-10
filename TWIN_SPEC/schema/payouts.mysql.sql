-- payouts.mysql.sql
-- Reconstructed schema for the `payouts` service's own MySQL database, derived MECHANICALLY from
-- repository goose migrations only: payouts/internal/database/migrations/*.go (34 files,
-- 20200615182810_create_payouts_table.go .. 20260720000001_alter_fts_transfer_id_to_bigint.go).
-- Cross-checked column-by-column against GORM models under payouts/internal/app/**/model.go.
-- NO DATA ROWS. Types/lengths transcribed from the goose migration Go string literals; where a
-- migration used an unqualified type without explicit length, the length shown is the migration's own.
--
-- KNOWN OUT-OF-BAND COLUMN (provenance NOT a repository migration):
--   payout_details.beneficiary_bank_code  VARCHAR(50) NULL
--   Declared: payouts/internal/app/payoutDetails/model.go:29,43. Used: core.go:222, payouts/repo.go:412.
--   No `internal/database/migrations/*.go` file (including 20210902153752_create_payout_details_table.go,
--   the table's own CREATE) ever adds this column. Test bootstrap code explicitly ALTERs it in at
--   least 9 files (e.g. payouts/slits/internal/app/bulkPayoutsProcessor/setup_test.go:152-154) with the
--   comment "beneficiary_bank_code is required by the service but was never added to the migration
--   (20210902153752)". `gh api repos/razorpay/payouts/commits?path=internal/database/migrations`
--   (full history, 51 commits) shows no commit ever touching this column. Included below because the
--   twin's own seeds/schema-patches/payouts.sql already patches it in as VARCHAR(50) NULL; type is
--   INFERRED from Go `string` usage, not confirmed against production.
--
-- ALSO SCHEMA-PRESENT BUT CODE-DEAD (migration-only, kept for completeness, not required by any code
-- path): source_request_id_mapping (20251106120000_create_source_request_id_mapping_table.go) — zero
-- references anywhere else in the repo; safe to omit from a twin unless another service is found to
-- depend on it.

CREATE TABLE IF NOT EXISTS `payouts` ( -- 20200615182810_create_payouts_table.go:14-55; ALTERed by 20260305000000 (gateway_ref_no) and 20260720000001 (fts_transfer_id widen)
  `id`                    CHAR(14)      NOT NULL PRIMARY KEY,
  `reference_id`          VARCHAR(255)  NULL,
  `narration`             VARCHAR(255)  NULL,
  `merchant_id`           CHAR(14)      NOT NULL,
  `balance_id`            CHAR(14)      NULL,
  `method`                VARCHAR(255)  NOT NULL,
  `mode`                  VARCHAR(255)  NULL,
  `fund_account_id`       CHAR(14)      NULL,
  `batch_id`              CHAR(14)      NULL,
  `idempotency_key`       VARCHAR(255)  NULL,
  `user_id`               CHAR(14)      NULL,
  `purpose`               VARCHAR(255)  NOT NULL,
  `purpose_type`          VARCHAR(255)  NULL,
  `amount`                BIGINT UNSIGNED NOT NULL,
  `currency`              VARCHAR(10)   NOT NULL,
  `notes`                 TEXT          NULL,
  `fees`                  INT UNSIGNED  NULL DEFAULT 0,
  `tax`                   INT UNSIGNED  NULL DEFAULT 0,
  `status`                VARCHAR(255)  NOT NULL,        -- enum enforced only in code, see report §Production behaviour / payouts
  `fts_transfer_id`       BIGINT        NULL,             -- widened int->bigint by 20260720000001 (irreversible per migration comment)
  `transaction_id`        CHAR(14)      NULL,
  `channel`               VARCHAR(255)  NULL,
  `utr`                   VARCHAR(255)  NULL,
  `failure_reason`        VARCHAR(255)  NULL,
  `cancellation_user_id`  CHAR(14)      NULL,
  `status_code`           VARCHAR(255)  NULL,
  `remarks`               VARCHAR(255)  NULL,
  `created_at`            INT           NOT NULL,
  `updated_at`            INT           NOT NULL,
  `payout_link_id`        CHAR(14)      NULL,
  `pricing_rule_id`       CHAR(14)      NULL,
  `scheduled_at`          BIGINT        NULL,
  `fee_type`              VARCHAR(255)  NULL,
  `registered_name`       VARCHAR(255)  NULL,
  `workflow_feature`      TINYINT(1)    NULL,
  `queued_reason`         VARCHAR(255)  NULL,
  `queued_at`             BIGINT        NULL,
  `on_hold_at`            BIGINT        NULL,
  `origin`                TINYINT(1)    NOT NULL DEFAULT 1,
  `status_details_id`     CHAR(14)      NULL,
  `gateway_ref_no`        VARCHAR(255)  CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL, -- 20260305000000:14
  UNIQUE KEY `idx_gateway_ref_no` (`gateway_ref_no`)      -- 20260305000000:20
  -- NOTE: destination_id, destination_type, initiated_at exist as Go fields (model.go:78-82) with
  -- gorm:"-" — explicitly NOT persisted (pre-staged for a future migration). Do not add these columns.
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `state_change_logs` ( -- 20200826172412_create_state_change_log_table.go:13-28
  `id`          INT           NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `refer_table` VARCHAR(255)  NOT NULL,
  `refer_id`    CHAR(14)      NOT NULL,
  `event`       VARCHAR(255)  NOT NULL,
  `from`        VARCHAR(255)  NOT NULL,
  `to`          VARCHAR(255)  NOT NULL,
  `mode`        ENUM('system','manual') NOT NULL,
  `triggered_by` VARCHAR(255) NULL,
  `created_at`  INT NOT NULL,
  `updated_at`  INT NOT NULL,
  KEY `refer_id_idx` (`refer_id`),
  KEY `created_at_idx` (`created_at`),
  KEY `updated_at_idx` (`updated_at`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `banking_accounts` ( -- 20200922102712_create_banking_accounts_table.go:13-26
  `id`                     CHAR(14)     NOT NULL PRIMARY KEY,
  `merchant_id`            CHAR(14)     NOT NULL,
  `balance_id`             CHAR(14)     NULL,
  `channel`                VARCHAR(255) NULL,
  `status`                 VARCHAR(255) NOT NULL,
  `account_number`         CHAR(40)     NULL,
  `account_type`           VARCHAR(255) NOT NULL,
  `fts_fund_account_id`    CHAR(14)     NULL,
  `payout_service_enabled` TINYINT(1)   NULL,
  `counter_migrated`       TINYINT(1)   NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `reversals` ( -- 20201113232006_create_reversals_table.go:14-29 -- NOTE: different columns from the API monolith's own `reversals` table (see apidb-subset.mysql.sql)
  `id`             CHAR(14)  NOT NULL PRIMARY KEY,
  `merchant_id`    CHAR(14)  NOT NULL,
  `payout_id`      CHAR(14)  NOT NULL,
  `balance_id`     CHAR(14)  NULL,
  `amount`         BIGINT UNSIGNED NOT NULL,
  `fees`           INT UNSIGNED NULL DEFAULT 0,
  `tax`            INT UNSIGNED NULL DEFAULT 0,
  `currency`       VARCHAR(10) NOT NULL,
  `channel`        VARCHAR(255) NULL,
  `notes`          TEXT NULL,
  `transaction_id` CHAR(14) NULL,
  `utr`            VARCHAR(255) NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `payout_sources` ( -- 20210304093129_create_payout_sources_table.go:14-22
  `id`         CHAR(14) NOT NULL PRIMARY KEY,
  `payout_id`  CHAR(14) NOT NULL,
  `source_id`  CHAR(14) NOT NULL,
  `source_type` VARCHAR(255) NULL,
  `priority`   INT NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `payout_logs` ( -- 20210524161952_create_payout_logs_table.go:13-23 -- NO secondary index at all (not even payout_id) -- CONFIRMED
  `id`         CHAR(14) NOT NULL PRIMARY KEY,
  `payout_id`  CHAR(14) NOT NULL,
  `event`      VARCHAR(255) NOT NULL,
  `from`       VARCHAR(255) NOT NULL,
  `to`         VARCHAR(255) NOT NULL,
  `mode`       VARCHAR(20) NOT NULL,
  `triggered_by` VARCHAR(255) NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `payout_purpose` ( -- 20210617163439_create_payout_purpose_table.go:14-20
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `merchant_id` CHAR(14) NOT NULL,
  `purpose_type` JSON NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `workflow_config` ( -- 20210824110700_create_workflow_config_table.go:14-23
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `config_id` CHAR(14) NOT NULL,
  `config_type` VARCHAR(255) NOT NULL,
  `enabled` TINYINT(1) NOT NULL DEFAULT 0,
  `merchant_id` CHAR(14) NOT NULL,
  `org_id` CHAR(14) NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `workflow_entity_map` ( -- 20210824110739_create_workflow_entity_map_table.go:14-24 -- payouts-service's OWN copy; distinct from api monolith's workflow_entity_map (apidb-subset.mysql.sql)
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `workflow_id` CHAR(14) NOT NULL,
  `entity_id` CHAR(14) NOT NULL,
  `config_id` CHAR(14) NULL,
  `entity_type` VARCHAR(255) NOT NULL,
  `merchant_id` CHAR(14) NOT NULL,
  `org_id` CHAR(14) NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `payout_details` ( -- 20210902153752_create_payout_details_table.go:15-24 (+ OUT-OF-BAND beneficiary_bank_code, see header)
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `payout_id` CHAR(14) NOT NULL,
  `queue_if_low_balance_flag` TINYINT NOT NULL DEFAULT 0,
  `tds_category_id` INT UNSIGNED NULL,
  `tax_payment_id` VARCHAR(30) NULL,
  `additional_info` JSON NULL,
  `beneficiary_bank_code` VARCHAR(50) NULL,  -- OUT-OF-BAND, see file header; type INFERRED
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL,
  KEY `payout_id_idx` (`payout_id`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `payout_status_details` ( -- 20211026104754_create_payout_status_details_table.go:13-24
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `payout_id` CHAR(14) NOT NULL,
  `status` VARCHAR(15) NOT NULL,
  `reason` VARCHAR(55) NOT NULL,
  `description` VARCHAR(255) NOT NULL,
  `mode` VARCHAR(20) NOT NULL,
  `triggered_by` VARCHAR(255) NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL,
  KEY `payout_status_details_payout_id_index` (`payout_id`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `fund_accounts` ( -- 20220120161944_create_fund_accounts_table.go:13-19 (payouts-service's own thin pointer table, NOT cfa's Mongo fund_accounts)
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `account_type` VARCHAR(255) NOT NULL,
  `account_id` CHAR(14) NOT NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `bank_accounts` ( -- 20220120172736_create_bank_accounts_table.go:13-20
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `ifsc_code` CHAR(11) NOT NULL,
  `bank_identifier` VARCHAR(255) NULL,
  `identifier_type` VARCHAR(255) NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `counters` ( -- 20220526144631_create_counters_table.go:14-23
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `balance_id` CHAR(14) NOT NULL,
  `free_payouts_consumed_last_reset_at` INT UNSIGNED NOT NULL,
  `free_payouts_consumed` INT NULL DEFAULT 0,
  `account_type` VARCHAR(255) NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL,
  KEY `counters_balance_id_index` (`balance_id`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `settings` ( -- 20220608002625_create_settings_table.go:14-24
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `entity_type` VARCHAR(100) NOT NULL,
  `entity_id` CHAR(14) NOT NULL,
  `module` VARCHAR(100) NOT NULL,
  `config_key` VARCHAR(255) NOT NULL,
  `config_value` TEXT NOT NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL,
  KEY `settings_entity_id_index` (`entity_id`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `payout_meta_temporary` ( -- 20220616173226_create_payout_meta_temporary_table.go:15-25
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `payout_id` CHAR(14) NOT NULL,
  `meta_name` VARCHAR(255) NULL,
  `meta_value` JSON NULL,
  `created_at` INT UNSIGNED NOT NULL, `updated_at` INT UNSIGNED NOT NULL, `deleted_at` INT UNSIGNED NULL,
  KEY `payout_id_idx` (`payout_id`), KEY `meta_name_idx` (`meta_name`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `payout_meta_permanent` LIKE `payout_meta_temporary`; -- 20220616173230_create_payout_meta_permanent_table.go:15-25, identical shape

CREATE TABLE IF NOT EXISTS `counter_transaction` ( -- 20220822132631_create_counter_transactions_table.go:13-22
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `counter_id` CHAR(14) NOT NULL,
  `payout_id` CHAR(14) NOT NULL,
  `payout_status` VARCHAR(255) NOT NULL,
  `transaction_type` VARCHAR(32) NOT NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL,
  KEY `payout_id_index` (`payout_id`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `workflow_state_map` ( -- 20220912025953_create_workflow_state_map_table.go:14-27
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `workflow_id` CHAR(14) NOT NULL,
  `actor_role` VARCHAR(255) NOT NULL,
  `state_id` CHAR(14) NOT NULL,
  `state_status` VARCHAR(255) NOT NULL,
  `group_name` VARCHAR(255) NOT NULL,
  `type` VARCHAR(255) NOT NULL,
  `count_of_approvals_needed` INT NOT NULL DEFAULT 1,
  `merchant_id` CHAR(14) NOT NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL,
  KEY `state_id_idx` (`state_id`), KEY `workflow_id_idx` (`workflow_id`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `idempotency_keys` ( -- 20221004002554_create_idempotency_keys_table.go:14-27 -- the payouts-service's OWN table (compare api monolith's, same logical shape, apidb-subset.mysql.sql)
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `idempotency_key` VARCHAR(255) NOT NULL,
  `merchant_id` CHAR(14) NOT NULL,
  `source_id` CHAR(14) NULL,
  `source_type` VARCHAR(64) NULL,
  `request_hash` VARCHAR(255) NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL,
  UNIQUE KEY `idempotency_keys_idempotency_key_merchant_id_unique` (`idempotency_key`,`merchant_id`),
  KEY `idempotency_keys_created_at_index` (`created_at`),
  KEY `idempotency_keys_merchant_id_index` (`merchant_id`),
  KEY `idempotency_keys_source_id_index` (`source_id`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `payout_attempts` ( -- 20230124225130_create_payout_attempts_table.go:14-25 -- all indexes NON-unique, unlike payouts.gateway_ref_no
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `payout_id` CHAR(14) NOT NULL,
  `gateway_ref_no` VARCHAR(255) NULL,
  `cms_ref_no` VARCHAR(255) NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL,
  KEY `payout_attempts_payout_id_index` (`payout_id`),
  KEY `payout_attempts_created_at_index` (`created_at`),
  KEY `payout_attempts_gateway_ref_no_index` (`gateway_ref_no`),
  KEY `payout_attempts_cms_ref_no_index` (`cms_ref_no`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `bulk_idempotency_keys` ( -- 20240830164301_bulk_idempotency_keys.go:15-27
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `batch_id` CHAR(14) NULL,
  `idempotency_key` VARCHAR(255) NOT NULL,
  `merchant_id` CHAR(14) NOT NULL,
  `source_id` CHAR(14) NULL,
  `source_type` VARCHAR(64) NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL,
  UNIQUE KEY `bulk_idempotency_keys_idempotency_key_merchant_id_unique` (`idempotency_key`,`merchant_id`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `duplicate_prevention_config` ( -- 20241126054126_duplicate_prevention_config.go:15-27
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `payout_source` VARCHAR(255) NOT NULL,
  `payout_type` VARCHAR(255) NOT NULL,
  `merchant_id` CHAR(14) NOT NULL,
  `detection_period` BIGINT NULL,
  `enabled` TINYINT(1) NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL,
  UNIQUE KEY `payout_source_type_merchant_id_unique` (`payout_source`,`payout_type`,`merchant_id`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `merchant_configurations` ( -- 20250311053125_create_merchant_configurations_table.go:15-28
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `entity_type` VARCHAR(255) NOT NULL,
  `entity_id` CHAR(14) NOT NULL,
  `name` VARCHAR(255) NOT NULL,
  `status` VARCHAR(255) NULL,
  `updated_by` VARCHAR(255) NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL, `deleted_at` INT NULL,
  UNIQUE KEY `unique_entity_config` (`entity_type`,`entity_id`,`name`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `idempotency_key_exclusions` ( -- 20250528222337_create_idempotency_key_exclusions_table.go:14-26
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `entity_id` CHAR(14) NOT NULL,
  `entity_type` VARCHAR(255) NOT NULL,
  `enabled` TINYINT(1) NULL,
  `created_by` VARCHAR(255) NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL
) ENGINE=InnoDB;

-- Dead table (migration exists, zero code references it anywhere in the repo). Include only if a
-- consuming service/PR is later found; otherwise safe to omit from a minimal twin.
CREATE TABLE IF NOT EXISTS `source_request_id_mapping` ( -- 20251106120000_create_source_request_id_mapping_table.go:15-25
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `source_type` VARCHAR(255) NOT NULL,
  `source_id` CHAR(14) NOT NULL,
  `request_id` VARCHAR(255) NOT NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL,
  KEY `idx_source_type_source_id` (`source_type`,`source_id`),
  KEY `idx_request_id` (`request_id`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB;

-- payouts_temp / fund_transfer_attempts_temp: reverse-dual-write staging mirrors (superset schemas).
-- Only needed if the twin exercises internal/app/reverseDualWrite. Full column lists:
-- 20250115000001_create_payouts_temp_table.go:14-86 (payouts_temp, superset of `payouts`, adds
-- customer_id, destination_id, destination_type, payment_id, transaction_type, attempts, return_utr,
-- scheduled_on, processed_at, pending_at, reversed_at, failed_at, rejected_at, cancelled_at,
-- initiated_at, transferred_at, settled_on, batch_submitted_at, create_request_submitted_at, type,
-- is_payout_service, on_demand, deleted_at, deleted_reason, service_tax, batch_settlement_id,
-- vendor_payment_id) and 20250115000003_create_fund_transfer_attempts_temp_table.go:14-45 (25 cols,
-- matches internal/app/reverseDualWrite/models/fund_transfer_attempt.go 1:1). Omitted here for
-- brevity — not required unless the twin runs the reverse-dual-write path.
