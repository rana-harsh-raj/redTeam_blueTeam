-- Arena schema patch for payouts (applied by scripts/up.sh after the repo migrations; idempotent).
-- Columns that the payouts code writes but that NO migration in payouts/internal/database/migrations
-- (34 files, goose max 20260720000001) creates -- i.e. production schema drift applied out-of-band.
-- Evidence: internal/app/payoutDetails/model.go (BeneficiaryBankCode) vs `SHOW COLUMNS FROM payout_details`
-- after a clean migration run (8 columns, no beneficiary_bank_code); create failed with MySQL 1054.
SET @col_exists := (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'payout_details' AND column_name = 'beneficiary_bank_code');
SET @ddl := IF(@col_exists = 0, 'ALTER TABLE payout_details ADD COLUMN beneficiary_bank_code VARCHAR(50) NULL AFTER additional_info', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
