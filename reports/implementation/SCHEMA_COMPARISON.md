# Schema comparison

Static CREATE/ALTER declaration inventory for every generated INSERT column. Dynamic SQL, down migrations and later ALTER semantics require effective-schema validation; not a proof of final physical DDL.

Compared 202 seeded columns across 20 tables, using 119 migration files.

| Service | Table | Located columns | Unresolved columns |
|---|---|---:|---|
| fts | account_type_mappings | 7/7 | none |
| fts | bank_accounts | 15/15 | none |
| fts | channel_information_status | 11/11 | none |
| fts | direct_account_routing_rules | 10/10 | none |
| fts | fund_accounts | 8/8 | none |
| fts | preferred_routing_weights | 7/7 | none |
| fts | source_account_mappings | 18/18 | none |
| fts | source_accounts | 11/11 | none |
| ledger | account_details | 15/15 | none |
| ledger | accounts | 9/9 | none |
| ledger | journal | 11/11 | none |
| ledger | ledger_entries | 10/10 | none |
| payouts | bank_accounts | 6/6 | none |
| payouts | banking_accounts | 12/12 | none |
| payouts | counters | 7/7 | none |
| payouts | fund_accounts | 5/5 | none |
| payouts | payout_purpose | 5/5 | none |
| payouts | workflow_config | 8/8 | none |
| payouts | workflow_state_map | 11/11 | none |
| x-balances | balance | 16/16 | none |

## Model and migration discrepancies

- `payouts/internal/app/payoutDetails/model.go`: String field in service model; absent from service migrations. Monolith ps_payout_details sibling migration explicitly declares CHAR(4); that does not establish the service physical schema.
- `x-balances/internal/database/model/sub_balance.go`: Model and standalone queries.sql exist; no wired migration creates this table. Not seeded or declared supported.
- `x-balances/internal/database/model/sub_balance_limit.go`: Model and standalone queries.sql exist; no wired migration creates this table. Not seeded or declared supported.

## Information required

- Schema-only `SHOW CREATE TABLE payout_details` from the approved service schema revision, including column nullability/default/index metadata and migration revision. No rows or credentials are needed. The monolith sibling CHAR(4) declaration narrows the question, but does not prove the service schema.
- Approved migration registration/configuration for x-balances sub-balance tables if those paths enter scope; the unwired queries.sql file alone cannot establish deployment behavior.
- API table identities are already source-confirmed and aligned in the 19-table local subset: `fund_transfer_attempts` (plural), `payouts_details`, `payouts_status_details`, `balance`, `features`, `reversals` and `workflow_entity_map`. API `payouts_details` is distinct from the service `payout_details` above. See [the P0.5 reconciliation](IMPLEMENTATION_SPEC_RECONCILIATION.md) and `ENV2_COMPOSE/seeds/mysql/apidb-ddl/00_init.sql` for per-table source and fidelity. This API subset is separate from the service INSERT-column inventory in this report.
- Remaining API schema evidence: schema-only `SHOW CREATE TABLE features` from the approved effective revision to resolve its name width (the source migration declares 25, while the arena uses an explicitly assumed 255 to accommodate the current 29-character reservation flag). Effective DDL for the other explicitly representative API subsets is needed only if broader physical-schema parity enters scope. No rows, credentials or production database access are requested.

The JSON companion contains every compared column, located type/constraints, repository commit and source hash. Unresolved scanner entries are visible limitations, not silently inferred columns.
