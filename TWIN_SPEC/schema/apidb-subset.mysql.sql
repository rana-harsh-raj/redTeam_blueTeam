-- apidb-subset.mysql.sql
-- The subset of the API-monolith's own MySQL database that the `payouts` Go service reads directly
-- over a `db.API` connection (internal/helpers/apidb.go, internal/app/**/fetch_orchestrator/fetchers/
-- api_db_fetcher.go), PLUS the tables the monolith itself owns for the same logical entities.
-- Table names and every column below are transcribed from api/database/migrations/*.php (Laravel/
-- Lumen migrations, real table names resolved via api/app/Constants/Table.php constants — NOT
-- guessed from BOM/struct-field reconstruction). NO DATA ROWS.
--
-- REAL TABLE LIST actually read by payouts' Go code (grep of internal/app/**/model.go TableName()
-- methods that are used against db.API context, cross-checked against api/app/Constants/Table.php):
--   payouts, payouts_details, payouts_status_details, reversals, workflow_entity_map,
--   fund_transfer_attempts, idempotency_keys, features, balance
-- (banking_accounts, contacts, fund_accounts, merchants, merchant_details, merchant_users, keys,
-- payout_sources, vpas, bank_accounts also exist in the monolith and are read by OTHER call paths /
-- other services per BOM §1; included below for completeness since the lane brief named them.)
--
-- *** CRITICAL, PRODUCTION-CONFIRMED FACT: the `payouts` table itself IS the API-DB payouts table —***
-- *** api/app/Constants/Table.php:31 `const PAYOUT = 'payouts'` and payouts/internal/app/payouts/  ***
-- *** model.go:168 `TablePayout = "payouts"` are the SAME STRING. There is no separate "ps_payouts"  ***
-- *** table in production. The `ps_*`-prefixed tables (ps_payouts, ps_payout_details, ps_reversals,  ***
-- *** ps_workflow_entity_map, ps_payout_sources, ps_idempotency_keys, ps_payout_logs, ...) found in   ***
-- *** api/database/migrations/2022_07_0*_create_ps_*.php are CI-ONLY test scaffolding — the first    ***
-- *** migration's own doc-comment says verbatim: "This table doesn't exist on prod. It only exists   ***
-- *** on CI. This is only to run test cases related to data migration of Payouts."                   ***
-- *** (2022_07_01_095954_create_ps_payouts.php:12-14). Production's monolith->payouts-service dual-  ***
-- *** write reconciliation (api/app/Models/Payout/DualWrite/*.php) connects DIRECTLY to the payouts   ***
-- *** service's OWN database via a distinct connection `payout_service_database`                     ***
-- *** (api/database/Connection.php:37, api/config/database.php:608-623, env-var driven — see          ***
-- *** api/app/Base/Repository.php:1454-1471 getPayoutsServiceConnection()) — ONLY in test/CI env does ***
-- *** it fall back to querying local `ps_<table>` tables instead (api/app/Models/PayoutsDetails/      ***
-- *** Repository.php:91-100). A faithful twin of the monolith<->payouts dual-write path therefore     ***
-- *** needs the monolith stub to reach payouts' OWN `payout_details`/`payouts` tables over a second   ***
-- *** connection, not read a `ps_payout_details` shadow table.                                        ***
--
-- CONFIRMED SCHEMA-DIVERGENCE BY DESIGN (not a migration gap): api/app/Models/Payout/DualWrite/
-- PayoutDetails.php:10-13,63-64 explicitly `unset()`s `beneficiary_bank_code` before writing
-- payouts-service data down into this table, with the comment "unsetting the columns as it present
-- only in Payouts Service's payout_details not in API Monolith". Do NOT add beneficiary_bank_code to
-- `payouts_details` below — its permanent absence here is intentional, first-party-documented monolith
-- behaviour, confirmed independently of the payouts-service-side drift documented in payouts.mysql.sql.
-- Other DualWrite columnsToUnset (also confirmed, i.e. these payouts-service-only columns never reach
-- this DB): Payout.php:19-23 -> gateway_ref_no, destination_id, destination_type.
-- Reversal.php:12-15 column renames applied on dual-write: payouts-service `fees`->API `fee`,
-- payouts-service `payout_id`->API `entity_id` (+ implicit entity_type='payout').

CREATE TABLE IF NOT EXISTS `payouts` ( -- 2016_06_16_081431_create_payouts_table.php (Table::PAYOUT = 'payouts'); NOT independently re-transcribed column-by-column in this pass beyond confirming table identity — see payouts.mysql.sql for the payouts-service's own (near-identical, evolved) copy. Treat as REPRESENTATIVE for now.
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
  -- Full column list not re-verified beyond this point; confirm remaining ~30 columns against
  -- 2016_06_16_081431_create_payouts_table.php before trusting this table for anything beyond
  -- table-name/PK/primary-FK fidelity.
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `payouts_details` ( -- 2021_06_08_195420_create_payouts_details_table.php:20-49, verbatim. PK is payout_id ITSELF -- there is NO separate `id` column.
  `payout_id`                 CHAR(14) NOT NULL PRIMARY KEY,
  `queue_if_low_balance_flag` TINYINT  NOT NULL DEFAULT 0,
  `created_at`                INT      NOT NULL,
  `updated_at`                INT      NOT NULL,
  `tax_payment_id`            VARCHAR(255) NULL DEFAULT NULL,
  `tds_category_id`           INT UNSIGNED NULL DEFAULT NULL,
  `additional_info`           JSON     NULL DEFAULT NULL,
  KEY `payouts_details_tds_category_id_index` (`tds_category_id`),
  KEY `payouts_details_tax_payment_id_index` (`tax_payment_id`)
  -- NO beneficiary_bank_code column -- see header, this is permanent/by-design, not an omission.
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `payouts_status_details` ( -- 2021_11_22_093451_create_payouts_status_details_table.php:21-47, verbatim
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

CREATE TABLE IF NOT EXISTS `reversals` ( -- 2016_12_19_110546_create_reversals.php:23-91, verbatim. NOTE: no `payout_id` column -- polymorphic entity_id/entity_type only. Column is `fee` (singular), not `fees`.
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

CREATE TABLE IF NOT EXISTS `workflow_entity_map` ( -- 2020_08_13_104459_create_workflow_entity_mapping_table.php:19-48
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

CREATE TABLE IF NOT EXISTS `fund_transfer_attempts` ( -- 2017_02_20_135839_create_fund_transfer_attempts_table.php:24-131 -- NOTE plural name; twin's prior draft used singular `fund_transfer_attempt`, which is WRONG
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

CREATE TABLE IF NOT EXISTS `idempotency_keys` ( -- 2020_03_13_162700_create_idempotency_keys_table.php:19-61, verbatim
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

CREATE TABLE IF NOT EXISTS `features` ( -- 2016_10_24_081731_create_features_table.php -- column list REPRESENTATIVE (table identity confirmed via Table::FEATURE='features', full DDL not re-transcribed this pass)
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `entity_id` CHAR(14) NULL,
  `entity_type` VARCHAR(32) NULL,
  `name` VARCHAR(255) NULL,
  `created_at` INT NULL, `updated_at` INT NULL,
  KEY `entity_idx` (`entity_id`,`entity_type`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `balance` ( -- 2014_07_12_083930_create_balance.php -- column list REPRESENTATIVE; x-balances.APIBalance (x-balances/internal/database/model/balance.go:194-210) documents the fields it actually maps: merchant_id, type, currency, name, balance, on_hold, credits, fee_credits, refund_credits, account_number, account_type, channel, locked_balance
  `id` CHAR(14) NOT NULL PRIMARY KEY,
  `merchant_id` CHAR(14) NOT NULL,
  `type` VARCHAR(32) NULL,
  `currency` CHAR(3) NOT NULL DEFAULT 'INR',
  `name` VARCHAR(255) NULL,
  `balance` BIGINT NOT NULL DEFAULT 0,
  `on_hold` BIGINT NULL DEFAULT 0,
  `credits` BIGINT NULL DEFAULT 0,
  `fee_credits` INT NULL DEFAULT 0,
  `refund_credits` BIGINT UNSIGNED NULL DEFAULT 0,
  `account_number` VARCHAR(64) NULL,
  `account_type` VARCHAR(32) NULL,
  `channel` VARCHAR(32) NULL,
  `locked_balance` BIGINT UNSIGNED NULL DEFAULT 0,
  `created_at` INT NULL, `updated_at` INT NULL,
  KEY `idx_merchant_id` (`merchant_id`)
) ENGINE=InnoDB;

-- Below: named in the lane brief, exist in the monolith, but full column DDL not independently
-- re-transcribed this pass (table identity/PK/real name confirmed via api/app/Constants/Table.php
-- only). REPRESENTATIVE, not EXACT — confirm against the cited migration before trusting columns.
CREATE TABLE IF NOT EXISTS `banking_accounts` ( -- Table::BANKING_ACCOUNT='banking_accounts' (plural!); 2019_05_28_105812_create_banking_account_table.php:20-60+ (truncated read)
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
  -- additional columns exist beyond what this pass read (file truncated at line 60); do not treat as complete
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `contacts`      (`id` CHAR(14) NOT NULL PRIMARY KEY, `merchant_id` CHAR(14) NOT NULL, `name` VARCHAR(255) NULL, `email` VARCHAR(255) NULL, `contact` VARCHAR(20) NULL, `type` VARCHAR(50) NULL, `active` TINYINT(1) NOT NULL DEFAULT 1, `created_at` INT NOT NULL) ENGINE=InnoDB; -- 2018_11_25_100031_create_contacts.php, REPRESENTATIVE
CREATE TABLE IF NOT EXISTS `fund_accounts` (`id` CHAR(14) NOT NULL PRIMARY KEY, `merchant_id` CHAR(14) NOT NULL, `contact_id` CHAR(14) NULL, `account_type` VARCHAR(30) NOT NULL, `active` TINYINT(1) NOT NULL DEFAULT 1, `created_at` INT NOT NULL) ENGINE=InnoDB; -- 2018_11_29_220246_create_fund_accounts.php, REPRESENTATIVE
CREATE TABLE IF NOT EXISTS `bank_accounts` (`id` CHAR(14) NOT NULL PRIMARY KEY, `fund_account_id` CHAR(14) NOT NULL, `ifsc_code` VARCHAR(11) NOT NULL, `bank_name` VARCHAR(255) NULL, `account_number` VARCHAR(40) NOT NULL, `beneficiary_name` VARCHAR(255) NOT NULL) ENGINE=InnoDB; -- Table::BANK_ACCOUNT='bank_accounts' (plural), REPRESENTATIVE
CREATE TABLE IF NOT EXISTS `vpas`          (`id` CHAR(14) NOT NULL PRIMARY KEY, `fund_account_id` CHAR(14) NOT NULL, `address` VARCHAR(255) NOT NULL) ENGINE=InnoDB; -- Table::VPA='vpas', REPRESENTATIVE
CREATE TABLE IF NOT EXISTS `merchants`     (`id` CHAR(14) NOT NULL PRIMARY KEY, `name` VARCHAR(255) NULL, `live` TINYINT(1) NOT NULL DEFAULT 0, `activated` TINYINT(1) NOT NULL DEFAULT 1, `created_at` INT NOT NULL) ENGINE=InnoDB; -- 2014_03_20_204851_create_merchants.php, REPRESENTATIVE
CREATE TABLE IF NOT EXISTS `merchant_details` (`merchant_id` CHAR(14) NOT NULL PRIMARY KEY) ENGINE=InnoDB; -- 2016_09_14_164527_create_merchant_details_table.php, table identity only, columns UNKNOWN this pass
CREATE TABLE IF NOT EXISTS `merchant_users`   (`id` CHAR(14) NOT NULL PRIMARY KEY, `merchant_id` CHAR(14) NOT NULL, `user_id` CHAR(14) NOT NULL) ENGINE=InnoDB; -- 2017_03_28_104101_create_merchant_users_table.php, REPRESENTATIVE
CREATE TABLE IF NOT EXISTS `keys`             (`id` CHAR(14) NOT NULL PRIMARY KEY, `merchant_id` CHAR(14) NOT NULL) ENGINE=InnoDB; -- 2014_04_23_221828_create_keys.php, REPRESENTATIVE
CREATE TABLE IF NOT EXISTS `payout_sources`   (`id` CHAR(14) NOT NULL PRIMARY KEY, `payout_id` CHAR(14) NOT NULL, `source_id` CHAR(14) NOT NULL, `source_type` VARCHAR(255) NULL, `priority` INT NULL, `created_at` INT NOT NULL, `updated_at` INT NOT NULL) ENGINE=InnoDB; -- 2020_08_25_192420_create_payout_sources_table.php, REPRESENTATIVE (monolith's own copy, distinct from payouts-service's own payout_sources)

-- CI-only test-fixture tables (production doc-comment: "This table doesn't exist on prod. It only
-- exists on CI." — api/database/migrations/2022_07_01_095954_create_ps_payouts.php:12-14). NOT part
-- of a production-faithful twin; listed here only so their existence is not re-discovered as a
-- surprise. Omit entirely unless specifically reproducing CI/data-migration test behaviour:
--   ps_payouts, ps_payout_details, ps_payout_status_details, ps_reversals, ps_workflow_entity_map,
--   ps_payout_sources, ps_idempotency_keys, ps_payout_logs, ps_payout_meta_temporary,
--   ps_payout_meta_permanent, ps_bulk_idempotency_keys, ps_banking_accounts,
--   ps_banking_account_statement(_details)
