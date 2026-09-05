# SCHEMA_PROVENANCE — where every twin schema comes from (2026-09-05)

DDL files: `TWIN_SPEC/schema/{payouts.mysql.sql, fts.mysql.sql, ledger.postgres.sql, cfa.mongo.json, x-balances.mysql.sql, apidb-subset.mysql.sql}` (derived from repository migrations and models only; no rows). Full evidence: `reports/fidelity/raw/05_schema_provenance.md`.

## 1. Provenance classes per datastore

| Datastore | Engine (prod / twin) | DDL source | Drift found | Class |
|---|---|---|---|---|
| payouts | MySQL / MySQL 8 | 34 goose migrations `payouts/internal/database/migrations` (commit history 51 commits, complete via `gh api`) | `payout_details.beneficiary_bank_code` written by code, never migrated; monolith dual-write explicitly excludes it (`api/app/Models/Payout/DualWrite/PayoutDetails.php:10-13,63-64`); `source_request_id_mapping` migration-only dead table | (c) out-of-band DDL **by design** for one column; twin schema patch `seeds/schema-patches/payouts.sql` stays, type `VARCHAR(50) NULL` INFERRED |
| fts | MySQL / MySQL 8 | 55 goose migrations (`00013` missing) + untracked `internal/migrations/queries.txt` | enum drift: `WALLET_TRANSFER` mode in code not in tracked ENUM; `INDUSIND` channel in ENUM not in code | (a)/(c) enum-level; patch only if wallet scenarios needed |
| ledger | **PostgreSQL** / postgres 15 | 51 `pg_migrations` (rx_migrations = RX reader DB); makeshift MySQL disabled | none column-level; journal has **no** unique index on (transactor_id, transactor_event) — preserve | (a) |
| cfa | AWS DocumentDB / MongoDB | Go models + 4 Mongo migration files; zero unique indexes, zero `$jsonSchema`; json tags (not bson) define documents | `mobile` fund-account type not creatable; wallet providers only `amazonpay` | (a) with engine substitution |
| x-balances | MySQL / MySQL 8 | one migration (`balance`); `sub_balances`/`sub_balance_limits` only in `queries.sql` (PR #147), stale vs code (`name`, `deleted_at` missing) | out-of-band | (c) |
| API monolith subset | MySQL / MySQL stub | Laravel migrations (`api/database/migrations`), table names from `api/app/Constants/Table.php` | twin DDL had wrong names/columns (see §3) | hand-maintained substitute |

## 2. Key constraints that carry invariants

| Table | Constraint | Source |
|---|---|---|
| payouts.idempotency_keys | UNIQUE(idempotency_key, merchant_id) | `20221004002554_create_idempotency_keys_table.go:23` |
| payouts.bulk_idempotency_keys | UNIQUE(idempotency_key, merchant_id) | `20240830164301_bulk_idempotency_keys.go:15-27` |
| payouts.payouts | UNIQUE gateway_ref_no; status VARCHAR(255) no enum | `20260305000000_add_gateway_ref_no_to_payouts.go:20`; `20200615182810:33` |
| payouts.payout_logs | no index | `20210524161952:13-23` |
| fts.transfers | UNIQUE(source_type, source_id) | `00005_transfers_table_create.go:62` |
| fts.attempts_unique_keys | PK gateway_ref_no | `00021:14-18` |
| fts.transfer_meta | no unique on (request_id, origin_service) | `00045` |
| ledger.journal | index transactor_id only; **no unique** | `20201001011143_create_journal.go:39` |
| ledger.ledger_config | UNIQUE(tenant, transactor_event_name), UNIQUE(tenant, rule) where deleted_at null | `20210810170948:14-35` |
| ledger.account_details | exact-JSONB entity discovery; parent_account_id for sub-accounts | `account_discovery/core.go:51-181` |
| cfa.* | none (duplicates possible; `1739361599_identify_duplicates.go`) | — |
| api.idempotency_keys | UNIQUE(idempotency_key, merchant_id) | `2020_03_13_162700_create_idempotency_keys_table.php` |
| api.features | UNIQUE(name, entity_id) | `2016_10_24_081731_create_features_table.php:19-33` |
| api.payouts_details | PK payout_id, no id, no beneficiary_bank_code | `2021_06_08_195420_create_payouts_details_table.php:20-49` |
| api.reversals | entity_id/entity_type polymorphic; fee singular; no payout_id | `2016_12_19_110546_create_reversals.php:23-91` |

## 3. Twin DDL corrections required

| File | Change |
|---|---|
| `seeds/schema-patches/apidb.sql` | rename `fund_transfer_attempt` → `fund_transfer_attempts`; drop `payouts_details.id` (PK = payout_id); drop `reversals.payout_id` and `fees`, add `customer_id, transaction_type, initiator_id, customer_refund_id`; drop `balance.primary` (use `type`), add `reward_fee_credits`; add `features` UNIQUE(name, entity_id), `name VARCHAR(25)`, `entity_type VARCHAR(255)`; `workflow_entity_map.entity_type VARCHAR(255)`; add `fund_transfer_attempts.is_fts`, `version VARCHAR(3)` |
| `seeds/mysql/apidb-ddl/00_init.sql` | rename `payout_status_details` → `payouts_status_details` (remove the duplicate), `banking_account` → `banking_accounts` |
| `seeds/mysql/apidb_seed.json`, `seeds/mysql/xbalances_seed.sql` | delete (dead) |
| new | second read-only DSN from monolith-stub into `mysql-payouts` (prod `payout_service_database`) |
| x-balances | if sub-balance flows are tested, apply the code-required shape (with `name`, `deleted_at`) as a documented patch |

## 4. External artifacts that would raise INFERRED/ASSUMED to EXACT

| Artifact | Owner | Form |
|---|---|---|
| `SHOW CREATE TABLE payouts.payout_details` (prod) | Payouts platform | schema-only |
| `SHOW CREATE TABLE api.fund_transfer_attempts, api.payouts_details, api.banking_accounts` | API-DB owners | schema-only |
| `SHOW CREATE TABLE sub_balances, sub_balance_limits` | x-balances | schema-only |
| `getIndexes()` for cfa `hash_lookup`, `tokenized_iin`, `iins`; DocumentDB engine version | CFA/infra | index list, version |
| ledger `SELECT id, tenant, transactor_event_name, rule, config FROM ledger_config WHERE tenant='X'` | Ledger | values-only (no balances) |
| one prod `account_details.entities` row shape (signed vs unsigned banking_account_id) | Ledger | redacted row |
