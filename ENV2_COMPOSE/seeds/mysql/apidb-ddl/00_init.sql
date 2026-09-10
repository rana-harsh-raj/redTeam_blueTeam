-- API monolith schema subset, Payouts Twin v1.
-- Base: TWIN_SPEC/schema/apidb-subset.mysql.sql (2026-09-05).
-- Repository: api@2d665f918b60e917ec92be648fb5816d247f72b1.
-- Names resolved from api/app/Constants/Table.php. No entity records copied.
-- Reordered merchants before foreign keys; balance/features reconciled with
-- their actual migrations. Remaining representative/unknown scopes are listed
-- per object below. This boot DDL targets empty volumes; it is not an in-place
-- migration for an old, differently shaped arena database.
-- API payouts_details intentionally has no id or beneficiary_bank_code.
-- Source: api/app/Models/Payout/DualWrite/PayoutDetails.php columnsToUnset.
-- The service payout_details bank-code type remains UNKNOWN; CI ps_* tables
-- are not evidence of production physical schema and are not created here.

-- Object: merchants
-- Source: api/database/migrations/2014_03_20_204851_create_merchants.php
-- Fidelity: REPRESENTATIVE subset; parent key for balance/idempotency foreign keys
CREATE TABLE IF NOT EXISTS `merchants`     (`id` CHAR(14) NOT NULL PRIMARY KEY, `name` VARCHAR(255) NULL, `live` TINYINT(1) NOT NULL DEFAULT 0, `activated` TINYINT(1) NOT NULL DEFAULT 1, `created_at` INT NOT NULL) ENGINE=InnoDB;

-- Object: payouts
-- Source: api/database/migrations/2016_06_16_081431_create_payouts_table.php
-- Fidelity: REPRESENTATIVE partial column subset; not sufficient to claim full monolith dual-write fidelity
CREATE TABLE IF NOT EXISTS `payouts` (

  `id`              CHAR(14)     NOT NULL PRIMARY KEY,
  `merchant_id`     CHAR(14)     NOT NULL,
  `customer_id`     CHAR(14)     NULL,
  `fund_account_id` CHAR(14)     NULL,
  `method`          VARCHAR(255) NOT NULL,
  `reference_id`    VARCHAR(255) NULL,
  `balance_id`      CHAR(14)     NULL,
  `destination_id`  CHAR(14)     NULL,
  `destination_type` CHAR(20)    NULL,
  `user_id`         CHAR(14)     NULL,
  `status`          VARCHAR(255) NOT NULL,
  `created_at`      INT          NOT NULL,
  `updated_at`      INT          NOT NULL






) ENGINE=InnoDB;

-- Object: payouts_details
-- Source: api/database/migrations/2021_06_08_195420_create_payouts_details_table.php
-- Fidelity: EXACT transcribed columns; payout_id is the primary key
CREATE TABLE IF NOT EXISTS `payouts_details` (

  `payout_id`                 CHAR(14) NOT NULL PRIMARY KEY,
  `queue_if_low_balance_flag` TINYINT  NOT NULL DEFAULT 0,
  `created_at`                INT      NOT NULL,
  `updated_at`                INT      NOT NULL,
  `tax_payment_id`            VARCHAR(255) NULL DEFAULT NULL,
  `tds_category_id`           INT UNSIGNED NULL DEFAULT NULL,
  `additional_info`           JSON     NULL DEFAULT NULL,
  KEY `payouts_details_tds_category_id_index` (`tds_category_id`),
  KEY `payouts_details_tax_payment_id_index` (`tax_payment_id`)


) ENGINE=InnoDB;

-- Object: payouts_status_details
-- Source: api/database/migrations/2021_11_22_093451_create_payouts_status_details_table.php
-- Fidelity: EXACT transcribed columns
CREATE TABLE IF NOT EXISTS `payouts_status_details` (

  `id`           CHAR(14)     NOT NULL PRIMARY KEY,
  `payout_id`    CHAR(14)     NOT NULL,
  `status`       VARCHAR(255) NOT NULL,
  `reason`       VARCHAR(255) NULL,
  `description`  VARCHAR(255) NULL,
  `mode`         VARCHAR(255) NOT NULL,
  `triggered_by` VARCHAR(255) NULL,
  `created_at`   INT NOT NULL, `updated_at` INT NOT NULL,
  KEY `payouts_status_details_payout_id_index` (`payout_id`)
) ENGINE=InnoDB;

-- Object: reversals
-- Source: api/database/migrations/2016_12_19_110546_create_reversals.php
-- Fidelity: EXACT transcribed columns; fee and entity_id are monolith fields
CREATE TABLE IF NOT EXISTS `reversals` (

  `id`               CHAR(14)     NOT NULL PRIMARY KEY,
  `merchant_id`      CHAR(14)     NOT NULL,
  `customer_id`      VARCHAR(14)  NULL,
  `entity_id`        CHAR(14)     NOT NULL,
  `entity_type`      CHAR(255)    NOT NULL,
  `balance_id`       VARCHAR(14)  NULL,
  `amount`           INT UNSIGNED NOT NULL,
  `fee`              INT UNSIGNED NULL DEFAULT 0,
  `tax`              INT UNSIGNED NULL DEFAULT 0,
  `currency`         CHAR(3)      NOT NULL,
  `channel`          VARCHAR(255) NULL,
  `utr`              VARCHAR(255) NULL,
  `notes`            TEXT         NOT NULL,
  `transaction_id`   CHAR(14)     NULL,
  `transaction_type` VARCHAR(255) NULL,
  `initiator_id`     VARCHAR(14)  NULL,
  `customer_refund_id` VARCHAR(14) NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL,
  KEY `reversals_created_at_index` (`created_at`),
  KEY `reversals_updated_at_index` (`updated_at`),
  KEY `reversals_entity_id_index` (`entity_id`),
  KEY `reversals_utr_index` (`utr`),
  KEY `reversals_merchant_id_created_at_index` (`merchant_id`,`created_at`)
) ENGINE=InnoDB;

-- Object: workflow_entity_map
-- Source: api/database/migrations/2020_08_13_104459_create_workflow_entity_mapping_table.php
-- Fidelity: EXACT transcribed columns
CREATE TABLE IF NOT EXISTS `workflow_entity_map` (

  `id`          CHAR(14)     NOT NULL PRIMARY KEY,
  `workflow_id` CHAR(14)     NOT NULL,
  `config_id`   CHAR(14)     NOT NULL,
  `entity_id`   CHAR(14)     NOT NULL,
  `entity_type` VARCHAR(255) NOT NULL,
  `merchant_id` CHAR(14)     NOT NULL,
  `org_id`      CHAR(14)     NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL,
  KEY `workflow_entity_map_workflow_id_index` (`workflow_id`),
  KEY `workflow_entity_map_entity_type_entity_id_index` (`entity_type`,`entity_id`)
) ENGINE=InnoDB;

-- Object: fund_transfer_attempts
-- Source: api/database/migrations/2017_02_20_135839_create_fund_transfer_attempts_table.php
-- Fidelity: Source-derived schema subset; plural table name
CREATE TABLE IF NOT EXISTS `fund_transfer_attempts` (

  `id`                     CHAR(14)     NOT NULL PRIMARY KEY,
  `source_type`            VARCHAR(255) NOT NULL,
  `source_id`              CHAR(14)     NOT NULL,
  `merchant_id`            CHAR(14)     NOT NULL,
  `purpose`                VARCHAR(32)  NOT NULL,
  `bank_account_id`        CHAR(14)     NULL,
  `vpa_id`                 CHAR(14)     NULL,
  `card_id`                CHAR(14)     NULL,
  `wallet_account_id`      CHAR(14)     NULL,
  `channel`                VARCHAR(8)   NOT NULL,
  `version`                VARCHAR(3)   NOT NULL,
  `bank_status_code`       VARCHAR(30)  NULL,
  `bank_response_code`     VARCHAR(30)  NULL,
  `mode`                   CHAR(30)     NULL,
  `is_fts`                 TINYINT      NOT NULL DEFAULT 0,
  `status`                 VARCHAR(255) NOT NULL,
  `utr`                    VARCHAR(255) NULL,
  `narration`              VARCHAR(255) NULL,
  `remarks`                VARCHAR(255) NULL,
  `failure_reason`         VARCHAR(255) NULL,
  `date_time`              VARCHAR(255) NULL,
  `cms_ref_no`             VARCHAR(255) NULL,
  `batch_fund_transfer_id` VARCHAR(14)  NULL,
  `initiate_at`            INT          NOT NULL,
  `created_at`             INT          NOT NULL,
  `updated_at`             INT          NOT NULL,
  `fts_transfer_id`        INT          NULL,
  `gateway_ref_no`         VARCHAR(255) NULL,
  UNIQUE KEY `fund_transfer_attempts_gateway_ref_no_unique` (`gateway_ref_no`),
  KEY `fund_transfer_attempts_source_id_source_type_index` (`source_id`,`source_type`),
  KEY `fund_transfer_attempts_status_index` (`status`),
  KEY `fund_transfer_attempts_channel_index` (`channel`),
  KEY `fund_transfer_attempts_initiate_at_index` (`initiate_at`),
  KEY `fund_transfer_attempts_created_at_index` (`created_at`),
  KEY `fund_transfer_attempts_fts_transfer_id_index` (`fts_transfer_id`),
  KEY `fund_transfer_attempts_card_id_index` (`card_id`),
  KEY `fund_transfer_attempts_bank_account_id_index` (`bank_account_id`),
  KEY `fund_transfer_attempts_vpa_id_index` (`vpa_id`)
) ENGINE=InnoDB;

-- Object: idempotency_keys
-- Source: api/database/migrations/2020_03_13_162700_create_idempotency_keys_table.php
-- Fidelity: EXACT transcribed columns and uniqueness
CREATE TABLE IF NOT EXISTS `idempotency_keys` (

  `id`              CHAR(14)     NOT NULL PRIMARY KEY,
  `idempotency_key` VARCHAR(255) NOT NULL,
  `merchant_id`     CHAR(14)     NOT NULL,
  `source_id`       CHAR(14)     NULL,
  `source_type`     VARCHAR(255) NULL,
  `request_hash`    VARCHAR(255) NULL,
  `created_at` INT NOT NULL, `updated_at` INT NOT NULL,
  UNIQUE KEY `idempotency_keys_idempotency_key_merchant_id_unique` (`idempotency_key`,`merchant_id`),
  KEY `idempotency_keys_created_at_index` (`created_at`),
  KEY `idempotency_keys_source_id_index` (`source_id`),
  KEY `idempotency_keys_merchant_id_index` (`merchant_id`),
  FOREIGN KEY (`merchant_id`) REFERENCES `merchants`(`id`) ON DELETE RESTRICT
) ENGINE=InnoDB;

-- Object: features
-- Source: api/database/migrations/2016_10_24_081731_create_features_table.php
-- Fidelity: Exact keys/nullability; ASSUMED name width255: migration25 conflicts with the current29-character reservation flag; request schema-only SHOW CREATE TABLE features
CREATE TABLE IF NOT EXISTS `features` (
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `name` VARCHAR(255) NOT NULL,
  `entity_id` CHAR(14) NOT NULL,
  `entity_type` VARCHAR(255) NOT NULL,
  `created_at` INT NOT NULL,
  `updated_at` INT NOT NULL,
  UNIQUE KEY `features_name_entity_id_unique` (`name`,`entity_id`),
  KEY `features_entity_id_index` (`entity_id`),
  KEY `features_entity_type_index` (`entity_type`),
  KEY `features_created_at_index` (`created_at`)
) ENGINE=InnoDB;

-- Object: balance
-- Source: api/database/migrations/2014_07_12_083930_create_balance.php
-- Fidelity: EXACT migration columns; credits/reward_fee_credits names resolved from Merchant/Balance/Entity.php; no separate primary column
CREATE TABLE IF NOT EXISTS `balance` (
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `merchant_id` CHAR(14) NULL,
  `type` VARCHAR(255) NULL,
  `currency` CHAR(3) NULL,
  `name` VARCHAR(255) NULL,
  `balance` BIGINT NOT NULL DEFAULT 0,
  `locked_balance` BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `on_hold` BIGINT NOT NULL DEFAULT 0,
  `credits` BIGINT NOT NULL DEFAULT 0,
  `fee_credits` BIGINT NOT NULL DEFAULT 0,
  `reward_fee_credits` BIGINT NOT NULL DEFAULT 0,
  `refund_credits` BIGINT NOT NULL DEFAULT 0,
  `account_number` VARCHAR(255) NULL,
  `account_type` VARCHAR(255) NULL,
  `channel` VARCHAR(255) NULL,
  `created_at` INT NOT NULL,
  `updated_at` INT NOT NULL,
  CONSTRAINT `balance_merchant_id_foreign` FOREIGN KEY (`merchant_id`) REFERENCES `merchants`(`id`) ON DELETE RESTRICT,
  KEY `balance_created_at_index` (`created_at`),
  KEY `balance_merchant_id_index` (`merchant_id`),
  KEY `balance_account_number_index` (`account_number`),
  KEY `balance_channel_index` (`channel`),
  KEY `balance_merchant_id_type_updated_at_index` (`merchant_id`,`type`,`updated_at`)
) ENGINE=InnoDB;

-- Object: banking_accounts
-- Source: api/database/migrations/2019_05_28_105812_create_banking_account_table.php
-- Fidelity: REPRESENTATIVE partial column subset; plural name
CREATE TABLE IF NOT EXISTS `banking_accounts` (

  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `merchant_id` CHAR(14) NOT NULL,
  `account_ifsc` CHAR(11) NULL,
  `account_number` VARCHAR(255) NULL,
  `status` VARCHAR(255) NULL,
  `sub_status` VARCHAR(64) NULL,
  `bank_internal_status` VARCHAR(255) NULL,
  `channel` VARCHAR(255) NOT NULL,
  `pincode` VARCHAR(255) NULL,
  `fts_fund_account_id` CHAR(14) NULL,
  `balance_id` CHAR(14) NULL,
  `gateway_balance` BIGINT NULL,
  `bank_internal_reference_number` VARCHAR(255) NULL,
  `beneficiary_name` VARCHAR(255) NULL


) ENGINE=InnoDB;

-- Object: contacts
-- Source: api/database/migrations/2018_11_25_100031_create_contacts.php
-- Fidelity: REPRESENTATIVE partial column subset
CREATE TABLE IF NOT EXISTS `contacts`      (`id` CHAR(14) NOT NULL PRIMARY KEY, `merchant_id` CHAR(14) NOT NULL, `name` VARCHAR(255) NULL, `email` VARCHAR(255) NULL, `contact` VARCHAR(20) NULL, `type` VARCHAR(50) NULL, `active` TINYINT(1) NOT NULL DEFAULT 1, `created_at` INT NOT NULL) ENGINE=InnoDB;

-- Object: fund_accounts
-- Source: api/database/migrations/2018_11_29_220246_create_fund_accounts.php
-- Fidelity: REPRESENTATIVE partial column subset
CREATE TABLE IF NOT EXISTS `fund_accounts` (`id` CHAR(14) NOT NULL PRIMARY KEY, `merchant_id` CHAR(14) NOT NULL, `contact_id` CHAR(14) NULL, `account_type` VARCHAR(30) NOT NULL, `active` TINYINT(1) NOT NULL DEFAULT 1, `created_at` INT NOT NULL) ENGINE=InnoDB;

-- Object: bank_accounts
-- Source: api/database/migrations/2014_12_16_082034_create_bank_accounts.php
-- Fidelity: REPRESENTATIVE subset from TWIN_SPEC; table identity and migration file verified, remaining column correspondence not established
CREATE TABLE IF NOT EXISTS `bank_accounts` (`id` CHAR(14) NOT NULL PRIMARY KEY, `fund_account_id` CHAR(14) NOT NULL, `ifsc_code` VARCHAR(11) NOT NULL, `bank_name` VARCHAR(255) NULL, `account_number` VARCHAR(40) NOT NULL, `beneficiary_name` VARCHAR(255) NOT NULL) ENGINE=InnoDB;

-- Object: vpas
-- Source: api/database/migrations/2016_11_29_193757_create_vpas.php
-- Fidelity: REPRESENTATIVE subset from TWIN_SPEC; table identity and migration file verified, remaining column correspondence not established
CREATE TABLE IF NOT EXISTS `vpas`          (`id` CHAR(14) NOT NULL PRIMARY KEY, `fund_account_id` CHAR(14) NOT NULL, `address` VARCHAR(255) NOT NULL) ENGINE=InnoDB;

-- Object: merchant_details
-- Source: api/database/migrations/2016_09_14_164527_create_merchant_details_table.php
-- Fidelity: UNKNOWN additional columns; identity only
CREATE TABLE IF NOT EXISTS `merchant_details` (`merchant_id` CHAR(14) NOT NULL PRIMARY KEY) ENGINE=InnoDB;

-- Object: merchant_users
-- Source: api/database/migrations/2017_03_28_104101_create_merchant_users_table.php
-- Fidelity: REPRESENTATIVE subset
CREATE TABLE IF NOT EXISTS `merchant_users`   (`id` CHAR(14) NOT NULL PRIMARY KEY, `merchant_id` CHAR(14) NOT NULL, `user_id` CHAR(14) NOT NULL) ENGINE=InnoDB;

-- Object: keys
-- Source: api/database/migrations/2014_04_23_221828_create_keys.php
-- Fidelity: REPRESENTATIVE identity subset; no key material seeded here
CREATE TABLE IF NOT EXISTS `keys`             (`id` CHAR(14) NOT NULL PRIMARY KEY, `merchant_id` CHAR(14) NOT NULL) ENGINE=InnoDB;

-- Object: payout_sources
-- Source: api/database/migrations/2020_08_25_192420_create_payout_sources_table.php
-- Fidelity: REPRESENTATIVE subset; distinct from service-owned payout_sources
CREATE TABLE IF NOT EXISTS `payout_sources`   (`id` CHAR(14) NOT NULL PRIMARY KEY, `payout_id` CHAR(14) NOT NULL, `source_id` CHAR(14) NOT NULL, `source_type` VARCHAR(255) NULL, `priority` INT NULL, `created_at` INT NOT NULL, `updated_at` INT NOT NULL) ENGINE=InnoDB;
