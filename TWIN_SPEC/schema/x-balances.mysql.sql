-- x-balances.mysql.sql
-- x-balances MySQL schema. `balance` is derived from the ONE real goose migration in this repo:
-- x-balances/internal/database/migrations/20250127224555_balance.go:17-48. `sub_balances` and
-- `sub_balance_limits` are OUT-OF-BAND: their only DDL anywhere in the repository is
-- x-balances/queries.sql (NOT under internal/database/migrations, NEVER wired into goose — confirmed
-- by `cmd/migration/main.go` only importing `internal/database/migrations`). NO DATA ROWS.
--
-- gh api repos/razorpay/x-balances/commits?path=queries.sql shows this file was added whole by PR #147
-- ("ISS-1605887 | ISS-1655924 | feat: add SubBalance decomposition to x-Balances service",
-- merged 2026-04-02T16:46:02Z) — the PR description claims "Add SubBalance and SubBalanceLimit DB
-- models, migrations, repo, and services", but current master's internal/database/migrations/
-- contains no such migration; only the 20250127224555_balance.go migration (from an EARLIER commit,
-- 2026-03-31) exists. Whatever migration the PR added (if any) is not present on this shallow clone
-- of master — treat sub_balances/sub_balance_limits as out-of-band DDL applied by some other means.

CREATE TABLE IF NOT EXISTS `balance` ( -- 20250127224555_balance.go:19-41, verbatim
  `id`                  VARCHAR(14)  NOT NULL PRIMARY KEY,
  `created_at`          BIGINT       NOT NULL DEFAULT 0,
  `updated_at`          BIGINT       NOT NULL DEFAULT 0,
  `status`              VARCHAR(50)  NOT NULL DEFAULT '',
  `merchant_id`         VARCHAR(255) NOT NULL DEFAULT '',
  `account_number`      VARCHAR(255) NOT NULL DEFAULT '',
  `account_type`        VARCHAR(50)  NOT NULL DEFAULT '',  -- enum: direct | pool | sub_balance | master (queries.sql:8 comment + model.go:24)
  `channel`             VARCHAR(50)  NOT NULL DEFAULT '',
  `currency`            VARCHAR(10)  NOT NULL DEFAULT '',
  `balance`              BIGINT      NOT NULL DEFAULT 0,
  `priority`            BIGINT       NOT NULL DEFAULT 0,
  `last_change_at`      BIGINT       NOT NULL DEFAULT 0,
  `last_fetched_at`     BIGINT       NOT NULL DEFAULT 0,
  `metadata`            JSON         DEFAULT NULL,
  `last_attempted_at`   BIGINT       NOT NULL DEFAULT 0,
  `fts_fund_account_id` VARCHAR(255) NOT NULL DEFAULT '',
  KEY `idx_merchant_id` (`merchant_id`),
  KEY `idx_account_number` (`account_number`),
  KEY `idx_channel` (`channel`),
  KEY `idx_merchant_channel` (`merchant_id`,`channel`),
  KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- OUT-OF-BAND (queries.sql:16-33). Go model (internal/database/model/sub_balance.go) and its repo
-- (internal/sub_balances/repo/repo.go) DISAGREE with this DDL:
--  - model.SubBalance / repo.go require `name` and `deleted_at` columns — NEITHER exists in queries.sql.
--  - queries.sql has `master_merchant_id`, `sub_merchant_id`, `sub_account_number` — repo.go's own
--    comments (FindByFilters, FindBySubAccountNumber, repo.go:76-77,109-112) say these fields "are no
--    longer stored on the sub_balances row" and are resolved via JOIN against `balance` instead — i.e.
--    the DDL reflects an EARLIER, denormalised design; the code was refactored to the normalised
--    (master_balance_id/sub_balance_id-only) design but queries.sql was never updated to match.
-- A twin must decide which shape to build for: the DDL as physically documented (below, matches
-- what a real deploy of queries.sql produces) or the shape the CURRENT code needs (add name/deleted_at;
-- master_merchant_id/sub_merchant_id/sub_account_number become redundant but harmless if also present).
CREATE TABLE IF NOT EXISTS `sub_balances` (
    `id`                 VARCHAR(14)  NOT NULL PRIMARY KEY COMMENT 'prefix subbal_',
    `master_balance_id`  VARCHAR(14)  NOT NULL COMMENT 'FK: master merchant balance ID',
    `sub_balance_id`     VARCHAR(14)  NOT NULL COMMENT 'FK: sub-merchant balance ID (unique)',
    `master_merchant_id` VARCHAR(14)  NOT NULL COMMENT 'denormalised; code no longer reads/writes this column (repo.go:76-77)',
    `sub_merchant_id`    VARCHAR(14)  NOT NULL COMMENT 'denormalised; code no longer reads/writes this column (repo.go:76-77)',
    `sub_account_number` VARCHAR(40)  NOT NULL COMMENT 'denormalised; code no longer reads/writes this column (repo.go:109-112)',
    `active`             TINYINT(1)   NOT NULL DEFAULT 1,
    -- `name`             VARCHAR(255) NOT NULL,   -- REQUIRED BY CODE (model.go, repo_test.go subBalCols) but ABSENT from queries.sql — add if building for current code
    -- `deleted_at`       BIGINT       NULL,        -- REQUIRED BY CODE (repo.go:54,69 `WHERE deleted_at IS NULL`) but ABSENT from queries.sql — add if building for current code
    `created_at`         BIGINT       NOT NULL,
    `updated_at`         BIGINT       NOT NULL,
    UNIQUE KEY `uq_sub_balance_id`     (`sub_balance_id`),
    UNIQUE KEY `uq_sub_account_number` (`sub_account_number`),
    KEY `idx_master_balance_id`        (`master_balance_id`),
    KEY `idx_master_merchant_id`       (`master_merchant_id`),
    KEY `idx_sub_merchant_id`          (`sub_merchant_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- OUT-OF-BAND (queries.sql:39-64). Matches model.SubBalanceLimit reasonably closely (status enum
-- created/processed/failed confirmed in both model.go:12-17 and queries.sql:45 comment, though
-- queries.sql's comment also mentions an unused 'processing' value not in the Go const block).
CREATE TABLE IF NOT EXISTS `sub_balance_limits` (
    `id`                 VARCHAR(14)  NOT NULL PRIMARY KEY COMMENT 'prefix ct_',
    `merchant_id`        VARCHAR(14)  NOT NULL COMMENT 'sub-merchant receiving the limit',
    `balance_id`         VARCHAR(14)  NOT NULL,
    `amount`             BIGINT       NOT NULL COMMENT 'paise',
    `currency`           CHAR(3)      NOT NULL DEFAULT 'INR',
    `status`             VARCHAR(20)  NOT NULL DEFAULT 'created', -- created | processed | failed (model.go:12-17); queries.sql comment also lists 'processing' but no such Go const found
    `payer_merchant_id`  VARCHAR(14)  NOT NULL,
    `payer_name`         VARCHAR(255) NULL,        -- present in queries.sql; model.go explicitly says NOT stored (fetched from ASV at response time) — likely vestigial column
    `payer_user_id`      VARCHAR(14)  NULL,
    `payee_account_id`   VARCHAR(14)  NULL,        -- present in queries.sql; model.go explicitly says NOT stored — likely vestigial column
    `payee_account_type` VARCHAR(32)  NULL DEFAULT 'bank_account', -- same as above
    `transaction_id`     VARCHAR(14)  NULL,
    `description`        VARCHAR(255) NULL,
    `processed_at`       BIGINT       NULL,
    `failed_at`          BIGINT       NULL,
    `created_at`         BIGINT       NOT NULL,
    `updated_at`         BIGINT       NOT NULL,
    KEY `idx_merchant_id`       (`merchant_id`),
    KEY `idx_payer_merchant_id` (`payer_merchant_id`),
    KEY `idx_balance_id`        (`balance_id`),
    KEY `idx_status`            (`status`),
    KEY `idx_created_at`        (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
