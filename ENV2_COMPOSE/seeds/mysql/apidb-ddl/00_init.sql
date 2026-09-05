-- seeds/mysql/apidb-ddl/00_init.sql
-- Mounted read-only at /docker-entrypoint-initdb.d/ in the mysql-apidb-stub
-- container (see docker-compose.yml) -- runs ONCE, on first container
-- creation, as the official mysql image's own initdb.d convention.
--
-- DDL-ONLY skeleton for the api-monolith tables payouts ([db.api]) and
-- x-balances ([APIStore.Sql]) read directly (INITIAL_PAYOUTS_ENVIRONMENT_BOM.md
-- §1: "API-monolith MySQL tables needed by fallbacks (balance, contacts,
-- fund_accounts, bank_accounts, payout_status_details) recreated from
-- struct definitions in cfa/x-balances/payouts (DDL only; no data)").
--
-- TODO (explicit, per the task brief's own framing): every column list
-- below is a best-effort reconstruction from Go struct field names seen in
-- client code (payouts/pkg/api, x-balances/internal/gateway/api_service),
-- NOT from the monolith's own real migrations (not in this clone set).
-- Confirm column names/types/nullability against a real schema export
-- before trusting any query executed directly against this table (as
-- opposed to going through monolith-stub's HTTP JSON responses, which are
-- the primary path in this scaffold -- see substitutes/monolith-stub/CONTRACT.md).

CREATE TABLE IF NOT EXISTS merchants (
    id            VARCHAR(20)  NOT NULL PRIMARY KEY,
    name          VARCHAR(255) NOT NULL,
    live          TINYINT(1)   NOT NULL DEFAULT 0,
    activated     TINYINT(1)   NOT NULL DEFAULT 1,
    category      VARCHAR(50)  DEFAULT NULL,
    created_at    BIGINT       NOT NULL,
    activated_at  BIGINT       DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS contacts (
    id           VARCHAR(20)  NOT NULL PRIMARY KEY,
    merchant_id  VARCHAR(20)  NOT NULL,
    name         VARCHAR(255) NOT NULL,
    email        VARCHAR(255) DEFAULT NULL,
    contact      VARCHAR(20)  DEFAULT NULL,
    type         VARCHAR(50)  DEFAULT NULL,
    active       TINYINT(1)   NOT NULL DEFAULT 1,
    created_at   BIGINT       NOT NULL,
    KEY idx_merchant_id (merchant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS fund_accounts (
    id             VARCHAR(20)  NOT NULL PRIMARY KEY,
    merchant_id    VARCHAR(20)  NOT NULL,
    contact_id     VARCHAR(20)  NOT NULL,
    account_type   VARCHAR(30)  NOT NULL,  -- 'bank_account' | 'vpa'
    active         TINYINT(1)   NOT NULL DEFAULT 1,
    created_at     BIGINT       NOT NULL,
    KEY idx_merchant_id (merchant_id),
    KEY idx_contact_id (contact_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS bank_accounts (
    id              VARCHAR(20)  NOT NULL PRIMARY KEY,
    fund_account_id VARCHAR(20)  NOT NULL,
    ifsc_code       VARCHAR(11)  NOT NULL,
    bank_name       VARCHAR(255) DEFAULT NULL,
    account_number  VARCHAR(40)  NOT NULL,
    beneficiary_name VARCHAR(255) NOT NULL,
    KEY idx_fund_account_id (fund_account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS vpas (
    id              VARCHAR(20)  NOT NULL PRIMARY KEY,
    fund_account_id VARCHAR(20)  NOT NULL,
    address         VARCHAR(255) NOT NULL,
    KEY idx_fund_account_id (fund_account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Legacy per-merchant credit/wallet balance (x-balances is authoritative in
-- Env 2 -- this table is only for call paths that haven't migrated,
-- per BOM §1).
CREATE TABLE IF NOT EXISTS balance (
    id           VARCHAR(20)  NOT NULL PRIMARY KEY,
    merchant_id  VARCHAR(20)  NOT NULL,
    balance      BIGINT       NOT NULL DEFAULT 0,  -- paise
    currency     VARCHAR(3)   NOT NULL DEFAULT 'INR',
    KEY idx_merchant_id (merchant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Legacy payout status mirror (findings/29 / BOM §7 "status_details_source_update").
CREATE TABLE IF NOT EXISTS payout_status_details (
    id           VARCHAR(20)  NOT NULL PRIMARY KEY,
    payout_id    VARCHAR(20)  NOT NULL,
    status       VARCHAR(30)  NOT NULL,
    updated_at   BIGINT       NOT NULL,
    KEY idx_payout_id (payout_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- banking_account: BOM §1 "banking_account rows with balance_id +
-- fts_fund_account_id" -- TODO confirm which service actually owns this
-- table (payouts' own DB vs. this apidb; kept here as a best guess since
-- it's read alongside the other legacy/monolith-adjacent tables above,
-- NOT confirmed).
CREATE TABLE IF NOT EXISTS banking_account (
    id                    VARCHAR(20)  NOT NULL PRIMARY KEY,
    merchant_id           VARCHAR(20)  NOT NULL,
    account_number        VARCHAR(40)  NOT NULL,
    account_type          VARCHAR(30)  NOT NULL,  -- 'shared' | 'direct'
    channel               VARCHAR(30)  DEFAULT NULL,  -- e.g. 'rbl'
    balance_id            VARCHAR(20)  DEFAULT NULL,  -- FK-ish -> x-balances balance row
    fts_fund_account_id   VARCHAR(20)  DEFAULT NULL,
    status                VARCHAR(30)  NOT NULL DEFAULT 'activated',
    created_at            BIGINT       NOT NULL,
    KEY idx_merchant_id (merchant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
