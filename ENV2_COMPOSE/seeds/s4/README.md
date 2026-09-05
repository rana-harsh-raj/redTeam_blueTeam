# Env2 arena seed data

Twin v1 treats this directory as versioned generator input. `scripts/up.sh` runs
[the generator](../generator/README.md) and loads `seeds/generated/s4/`, including
isolated merchant namespaces and a provenance manifest. Use the
[current execution guide](../../../TWIN_V1_README.md); the manual commands below
describe the earlier scaffold and are retained for context.

Companion seed files for `reports/SYNTHETIC_FIXTURE_SPEC.md`. Read that document first -- it is the
entity model and rationale; this directory is the executable/loadable data.

## Files

| File | Target | How to apply |
|---|---|---|
| `payouts.sql` | payouts MySQL 8 | `mysql -h <host> payouts_db < payouts.sql` |
| `fts.sql` | fts MySQL 8.0 | `mysql -h <host> fts_db < fts.sql` |
| `xbalances.sql` | x-balances MySQL 8 | `mysql -h <host> xbalances_db < xbalances.sql` |
| `ledger.sql` | ledger Postgres 16 (tenant X) | `psql -h <host> -d ledger_db -f ledger.sql` -- FALLBACK path, see below |
| `ledger_accounts_via_api.sh` | ledger Postgres, via the `api` service | **PREFERRED** over `ledger.sql` -- calls the real `AccountAPI.CreateOnEvent` Twirp RPC |
| `cfa.js` | cfa MongoDB 7.0 | `mongosh <connection-string> cfa.js` -- **recommended** (direct insert is confirmed correct, see spec Sec 8) |
| `cfa_via_api.sh` | cfa MongoDB 7.0, via the `cfa-server` API | alternative to `cfa.js` if the arena's policy requires provisioning only through service APIs |
| `dcs_fixtures.json` | dcs-stub substitute | seed data for the DCS KV-get responses |
| `splitz_fixtures.json` | splitz-stub substitute | seed data for Twirp EvaluateAPI/Evaluate responses |
| `shield_fixtures.json` | shield-stub substitute | seed data for `POST /v1/rules/evaluate/payout` |
| `pricing_fixtures.json` | pricing-stub / charge-collections substitute | fee/tax + free-payout-counter fixtures |
| `monolith_fixtures.json` | api-monolith substitute | merchant config, pricing, on_hold_slas stub responses |
| `stork_subscriptions.json` | stork-capture substitute | webhook subscription rows + event/payload templates |
| `mozart_scenarios.json` | mozart-mock / mozart-sim substitute | 6 scripted bank-outcome scenarios |
| `passport_fixtures.json` | Kong-lite / passport signer | JWT claim fixtures per merchant/user |
| `cron_schedule.yaml` | cron-driver substitute | assumed cadence table (UNVERIFIED, see file header) |

## Load order

Datastore schema (goose/GORM/Mongo migrations) must already be applied by each service's own
migration binary before running these files -- this directory seeds DATA, not schema.

1. `ledger_accounts_via_api.sh` (preferred) or `ledger.sql` (fallback) -- ledger must be seeded before
   payouts/fts traffic that expects `banking_account_id`/`fts_fund_account_id` to already resolve.
2. `payouts.sql`
3. `fts.sql`
4. `xbalances.sql`
5. `cfa.js`
6. The `*_fixtures.json`/`cron_schedule.yaml` files are loaded into their respective stub substitutes'
   own seed-data volumes/config, per each substitute's own `CONTRACT.md` (not part of this task's
   scope -- these are data files, not stub server code).

All SQL is idempotent (`ON DUPLICATE KEY UPDATE id = id` / `INSERT IGNORE` for MySQL, `ON CONFLICT ...
DO NOTHING` for Postgres) and safe to re-run against a partially-seeded database.

## Entity id quick reference

| Entity | M1 (Shared) | M2 (Direct/RBL) | M3 (Shared, workflow) |
|---|---|---|---|
| merchant_id | `ARENAM00000001` | `ARENAM00000002` | `ARENAM00000003` |
| payouts `banking_accounts.id` | `ARENABA0000001` | `ARENABA0000002` | `ARENABA0000003` |
| x-balances `balance.id` | `ARENABAL000001` | `ARENABAL000002` | `ARENABAL000003` |
| payouts `counters.id` | `ARENACTR000001` | `ARENACTR000002` | `ARENACTR000003` |
| ledger MerchantBalance `accounts.id` | `ARENAM1ACC0001` | n/a (uses x-balances, not ledger -- see spec) | `ARENAM3ACC0001` |
| fts `fund_account_id` (routing) | `900001` (shared pool) | `900002` (dedicated) | `900001` (shared pool) |

Full registry (all tables, all ids) is in `SYNTHETIC_FIXTURE_SPEC.md`'s ID-scheme section.

## API keys / Basic-Auth identities (not stored in any of these seed files)

Per `INITIAL_PAYOUTS_ENVIRONMENT_BOM.md` Sec 1, merchant API keys and inter-service Basic-Auth pairs
are not payouts/fts/ledger/x-balances/cfa database rows -- they belong to the identity substrate
(Kong-lite + the arena's own secrets generation), which is out of this task's scope. Suggested key-id
shapes for whoever wires that up: `rzp_test_ARENAM0001xx` (M1), `rzp_test_ARENAM0002xx` (M2),
`rzp_test_ARENAM0003xx` (M3), matching the task's requested `rzp_test_ARENAxxxxxxxx` shape.

## Known gaps (see SYNTHETIC_FIXTURE_SPEC.md for full detail)

- `merchant_configurations` (both payouts and fts versions) intentionally left unseeded -- no
  confirmed real usage/taxonomy was found for either table in the research pass.
- x-balances `sub_balances`/`sub_balance_limits` tables do not exist in this codebase's actual
  migrated schema (DDL only in the unwired `x-balances/queries.sql`) -- not seeded, not applicable.
- `ledger_config` seed in `ledger.sql` covers only 2 of the real 34 transactor-event configs
  (`XPayoutInitiatedV2`, `XPayoutProcessedV2`) as a hand-transcribed sample; prefer generating the
  full set from `ledger/internal/journal/ledger_config/seed_data/shared_account_x.go`'s
  `GetXSharedAccountingLedgerConfigs()` directly if more event types are needed.
- CFA `fund_accounts`/`contacts`/`hash_lookup` have zero DB-level uniqueness constraints (confirmed);
  `cfa.js`'s hand-computed hashes are the only thing preventing duplicate inserts on re-seed with
  different data -- re-running with the same data is safe (upsert on `id`).
