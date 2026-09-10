# Lane 05 — Schema provenance and drift

Covers every datastore the twin runs: payouts (MySQL), fts (MySQL), ledger (Postgres), cfa (Mongo),
x-balances (MySQL), and the API-monolith DB subset payouts reads directly. Companion DDL/JSON files
(derived from migrations/models only, no data rows) are at `TWIN_SPEC/schema/`:
`payouts.mysql.sql`, `fts.mysql.sql`, `ledger.postgres.sql`, `cfa.mongo.json`, `x-balances.mysql.sql`,
`apidb-subset.mysql.sql`.

## 1. Scope and sources read

| Repo | Paths opened |
|---|---|
| `payouts` | `internal/database/migrations/*.go` (34 files); `internal/app/**/model.go` (~30 in-scope structs); `internal/app/payouts/state_machine.go`; `internal/app/common/appConstants/states.go`; `internal/app/payouts/fts_transfer_status_webhook.go`; `internal/helpers/apidb.go`; `internal/app/payouts/core.go` (FetchAllPayoutsFromApiDb); `internal/app/payoutStatusDetails/core.go`, `fetch_orchestrator/core_adapter.go`, `fetch_orchestrator/fetchers/api_db_fetcher.go`; `slits/**/setup_test.go` (beneficiary_bank_code workaround, 9 files) |
| `fts` | `internal/migrations/*.go` (55 files, 00001–00050 + gap at 00013); `internal/migrations/queries.txt`/`update_queries.txt`/`spinnaker_update_queries.txt`; `internal/transfer/{transfer,attempt,attempts_unique_keys,transfer_meta,state_machine}.go`; `internal/channel/{channel,validation}.go`; `internal/common/constants.go` |
| `ledger` | `internal/database/pg_migrations/*.go` (51 files); `internal/database/rx_migrations/*.go`; `internal/makeshift/database/makeshift_migrations/*.go`; `config/default.toml`; `cmd/migration/main.go`; `internal/journal/{model,validation,repo}.go`; `internal/journal/ledger_entry/{model,repo}.go`; `internal/account/{model,repo,status}.go`; `internal/account/account_detail/model.go`; `go.mod` |
| `cfa` | `internal/contacts/{model,types}.go`; `internal/fund_accounts/{model,constants,validate}.go`; `internal/fund_accounts/card/model.go`; `internal/hashlookup/model.go`; `internal/database/migrations/*.go` (4 files); `pkg/storage/mongodb/{mongo,transform,repository}.go`; `rpc/x/x-cfa/fund_account/v1/fund_account.pb.go`; `internal/eventsystem/subscribers/fund_account_dual_write.go` |
| `x-balances` | `internal/database/migrations/20250127224555_balance.go`; `internal/database/model/{balance,sub_balance,sub_balance_limit}.go`; `internal/sub_balances/repo/repo.go` (+ test); `internal/sub_balance_limits/*.go`; `queries.sql` (repo root); `cmd/migration/main.go` |
| `api` (monolith) | `database/migrations/*.php` (~120 files grepped, ~25 read in full: payouts, payouts_details, payouts_status_details, reversals, workflow_entity_mapping, fund_transfer_attempts, idempotency_keys, features, balance, ps_payouts, banking_account, contacts, fund_accounts, merchants, keys, bank_account_table); `app/Constants/Table.php`; `app/Models/Payout/DualWrite/*.php` (7 files); `app/Models/PayoutsDetails/Repository.php`; `app/Base/Repository.php` (getPayoutsServiceConnection); `database/Connection.php`; `config/database.php` |
| Twin (`ENV2_COMPOSE`) | `seeds/schema-patches/{apidb,payouts}.sql`; `seeds/mysql/apidb-ddl/00_init.sql`; `seeds/mysql/{apidb_seed.json,xbalances_seed.sql,fts_seed.sql}`; `seeds/s4/{xbalances,fts,ledger,payouts}.sql`; `seeds/mongo/cfa_seed.js`; `scripts/{up,reset}.sh` |
| Existing reports | `UNRESOLVED_QUESTIONS.md`, `ARCHITECTURE_DELTA.md`, `EFFECTIVE_CONFIG_GAPS.md`, `ENV2_BUILD_RUNBOOK.md`, `ENV2_BUILD_STATUS.md`, `SYNTHETIC_FIXTURE_SPEC.md`, `raw-findings/{01_payouts_core,20_api_monolith,31_env2_bringup_notes,32_independent_audit_env2}.md` |
| GitHub history (`gh api`, read-only GET only) | `repos/razorpay/payouts/commits?path=internal/database/migrations` (51 commits, full history); `search/code?q=beneficiary_bank_code+repo:razorpay/payouts`; `repos/razorpay/payouts/commits?path=.../20251106120000_create_source_request_id_mapping_table.go`; `repos/razorpay/x-balances/commits?path=internal/database/migrations`; `repos/razorpay/x-balances/commits?path=queries.sql`; `repos/razorpay/x-balances/pulls/147`; `search/code?q=WALLET_TRANSFER+repo:razorpay/fts`; `repos/razorpay/fts/commits?path=internal/migrations`. All succeeded; no auth/rate-limit failures encountered, so no calls were abandoned after 2 attempts. |

Method used throughout (per lane brief): payouts/fts/ledger/x-balances mechanical column diffs were
each done by a dedicated agent pass grepping every migration file against every GORM/model struct
field; results were spot-checked by re-reading the cited source. cfa (Mongo, no migrations in the SQL
sense) was reconstructed from Go model/validation code plus its own versioned index-migration files.
The API-monolith subset and the x-balances out-of-band tables were investigated directly by me,
including the `gh api` commit-history checks.

---

## 2. Production behaviour

### 2.1 payouts (MySQL, goose, `internal/database/migrations`)

34 migrations (`20200615182810` .. `20260720000001`), ~30 in-scope tables. Full column-by-column
CREATE TABLE reconstruction: `TWIN_SPEC/schema/payouts.mysql.sql`. Highlights:

**Confirmed drift #1 — `payout_details.beneficiary_bank_code`, code-only, no migration ever creates it.**
- Model: `payouts/internal/app/payoutDetails/model.go:29,43` (field on both `PayoutDetails` and
  `APIPayoutDetails`). Used: `internal/app/payoutDetails/core.go:222`; live SQL predicate
  `internal/app/payouts/repo.go:412` (`q.Where("payout_details.beneficiary_bank_code NOT IN (?)", ...)`).
- Migration side: `grep -rn "payout_details" internal/database/migrations/` → only 2 lines, both in
  `20210902153752_create_payout_details_table.go` (CREATE at `:15`, DROP in down-migration at `:30`);
  the CREATE body (`:15-24`) never lists this column. No `ALTER TABLE payout_details` exists anywhere.
- Test-infra explicitly documents the gap: `payouts/slits/internal/app/bulkPayoutsProcessor/setup_test.go:152-154`
  — *"NOTE: beneficiary_bank_code is required by the service but was never added to the migration
  (20210902153752). We add it via ALTER TABLE when missing..."* — repeated verbatim in 8 more
  `slits/**/setup_test.go` files.
- **gh api history (CONFIRMED, definitive negative):** `repos/razorpay/payouts/commits?path=internal/database/migrations`
  returns the full 51-commit history of the migrations directory back to repo inception (`3b868a1a`,
  "Payout structure (#6)", 2021-02-26). The column-creating commit `bd96c12a` ("add payout details
  table (#210)", 2021-09-03) matches the migration file exactly; no commit message in the full history
  mentions "beneficiary"/"bank_code", and `search/code?q=beneficiary_bank_code+repo:razorpay/payouts`
  returns hits only in Go struct/constant/test files (`internal/app/payoutDetails/core.go`,
  `internal/app/payouts/repo.go`, `internal/app/common/appConstants/{entity,constants}.go`, and
  `slits/**` test files) — never in `internal/database/migrations`. **The column was never added by
  any migration in this repo's tracked history**, on any commit that touched the migrations path.
- **What it actually is (new, resolves `UNRESOLVED_QUESTIONS.md:109`'s open item):** the monolith's own
  dual-write code (`api/app/Models/Payout/DualWrite/PayoutDetails.php:10-13,63-64`) explicitly
  `unset()`s this exact column before writing payouts-service data into its own `payouts_details`
  table, with the comment *"unsetting the columns as it present only in Payouts Service's
  payout_details not in API Monolith"*. This is first-party, contemporaneous evidence that
  `beneficiary_bank_code` is intentional payouts-service-only schema, not a forgotten migration —
  classification (c) **out-of-band DDL, by design**, not (d) incomplete migration sequence. It was
  most likely added directly (`ALTER TABLE`) to the payouts-service production DB by whoever built the
  monolith-side exclusion, with no corresponding migration ever checked in. `raw-findings/31_env2_bringup_notes.md:82`'s
  speculation "gh-ost/manual" is unconfirmed either way but the "why" (permanent, intentional
  exclusion from the monolith) is now confirmed, correcting the framing of `ARCHITECTURE_DELTA.md:89`
  from "schema provenance is not fully reconstructible" to "one column's *migration* provenance is
  unreconstructible, but its *architectural* provenance (deliberately payouts-service-only) is now
  fully documented".

**Confirmed drift #2 — `source_request_id_mapping`, migration-only, dead table.**
`20251106120000_create_source_request_id_mapping_table.go:15-25` creates it (added by PR #1234/#1235,
2025-06-11, confirmed via `gh api repos/razorpay/payouts/commits?path=...`); zero references anywhere
else in the repo (no model, repo, core, or test code). Classify (d)-adjacent: migration exists but
nothing consumes it — either a forward-looking migration for an unshipped feature or a table another
service/branch owns the Go side of. Safe to omit from a minimal twin.

**Idempotency, indexes (verbatim), state machine:**

| Table | Constraint | DDL | Citation |
|---|---|---|---|
| `idempotency_keys` | UNIQUE | `(idempotency_key, merchant_id)` | `20221004002554_create_idempotency_keys_table.go:23` |
| `payouts` | UNIQUE | `gateway_ref_no` | `20260305000000_add_gateway_ref_no_to_payouts.go:20` |
| `payout_logs` | none | no secondary index at all, not even `payout_id` | `20210524161952_create_payout_logs_table.go:13-23` |
| `payout_attempts.gateway_ref_no` | non-unique KEY (×4, incl. this) | contrast with `payouts.gateway_ref_no` (UNIQUE) — a payout can have multiple attempts | `20230124225130_create_payout_attempts_table.go:21-24` |

`payouts.status` is `VARCHAR(255) NOT NULL` with **no CHECK/ENUM** at the DB level
(`20200615182810_create_payouts_table.go:33`); the full state machine is registered and quoted in
full in `internal/app/payouts/state_machine.go:15-435` (13 states, `Event→To→From[...]` transition
map — see companion `payouts.mysql.sql` header for the value list). A narrower, independent guard for
FTS webhook-driven transitions exists at `internal/app/common/appConstants/states.go:37-54`
(`AllowedStateTransitionForTransferWebhook`, 4 states only: `initiated/processed/reversed/failed`).
`StatePendingOnOtp` is declared (`states.go:10`) but never used as a transition target — vestigial.

**API-DB access from payouts' own Go code (new finding, not in prior reports):**
- `helpers.IsApiDbEnabledForPayouts` (`internal/helpers/apidb.go:23-25`) is **hardcoded `return false`**
  — the entire `payouts` API-DB read path (`FetchAllPayoutsFromApiDb`, `internal/app/payouts/core.go:9239-9243`)
  is dead code in this snapshot: it always short-circuits to an empty result before ever touching the
  API DB connection.
- `helpers.IsApiDbEnabledForPayoutStatusDetails` (`apidb.go:41-53`) is a **real** Splitz-gated flag
  (`use_api_db_for_payout_status_details_experiment`), fail-open to `true` if Splitz/config is
  unavailable. The live call site is `fetch_orchestrator/core_adapter.go:42-51`
  (`FetchAllPayoutStatusDetailsFromApiDb`), reused by the fetch-orchestrator's `ApiDBFetcher`
  (`fetch_orchestrator/fetchers/api_db_fetcher.go`). A second, **older** call site for the same purpose
  in `internal/app/payoutStatusDetails/core.go:193-214` is entirely commented out (`/* ... */`) — dead.

### 2.2 fts (MySQL, goose, `internal/migrations`)

55 migration files (00001–00050, **00013 missing** — numbering gap, cause UNKNOWN). Full DDL for
`transfers`/`attempts`/`attempts_unique_keys`/`transfer_meta`: `TWIN_SPEC/schema/fts.mysql.sql`.
**Zero column-presence drift** — every migration column in these 4 tables maps 1:1 to a gorm-tagged
struct field and vice versa (`internal/transfer/{transfer,attempt,attempts_unique_keys,transfer_meta}.go`).
Drift instead shows up at the **enum-value** level:

| # | Type | Value | Migration side | Code side |
|---|---|---|---|---|
| 1 | CONFIRMED-DRIFT | `WALLET_TRANSFER` mode | absent from tracked ENUM (`00005_transfers_table_create.go:20,29`, `00006_attempts_table_create.go:25`) | required for `AMAZON_PAY` channel (`internal/channel/channel.go:59,90`), validated into `Attempt.Validate()` (`internal/transfer/attempt.go:106-109`); only appears in untracked `internal/migrations/queries.txt:346-347,354` |
| 2 | CONFIRMED-DRIFT | `INDUSIND` channel | present in tracked ENUM since `00047_update_channel_enum_add_slice_channels.go:15,27,39` | zero references in `internal/channel/channel.go`'s channel list — DB accepts it, code can never produce/validate it |

`internal/migrations/queries.txt` (386 lines) is a hand-maintained, unnumbered ALTER log **not wired
into goose** (no `goose.AddMigration` reference) documenting schema states (e.g. `WALLET_TRANSFER`,
pre-00047 `AMAZON_PAY`/`IDFC` channels) that predate the numbered chain. `gh api search/code?q=WALLET_TRANSFER+repo:razorpay/fts`
plus `commits?path=internal/migrations` shows the code-side `WALLET_TRANSFER`/`AMAZON_PAY` support
landed via "Onboard Wallet service on FTS ISS-2462445 (#4165)" (2026-06-01) — a commit that touched
`internal/common/constants.go`/`internal/channel/channel.go` but **not** `internal/migrations`,
confirming the ENUM was never updated to match.

Dedup/uniqueness: `attempts_unique_keys.gateway_ref_no` is a **single-column PRIMARY KEY**
(`00021_attempts_unique_keys_table_create.go:14-18`); `attempts.gateway_ref_no_unique` is a separate
UNIQUE KEY on `attempts` itself; `transfers.unique_source (source_type, source_id)` is the third. **No
`(transfer_id, attempt_number)` or `(merchant_id, request_id)` unique constraint exists anywhere** —
confirmed by exhaustive `UNIQUE` grep across all 55 files. `transfer_meta` (request_id/origin_service
dedup metadata) likewise has **no** unique constraint on `(request_id, origin_service)`, only
non-unique KEYs — its `Validate()` (`internal/transfer/transfer_meta.go:26-38`) checks format only.

### 2.3 ledger (**PostgreSQL**, confirmed — not MySQL)

**CORRECTED FROM THE BRIEF'S OPEN QUESTION**: ledger's core schema (`accounts`, `account_details`,
`journal`, `ledger_entries`, `ledger_config`, `idempotency`, `split_accounts`, `state_change_logs`,
`onboarding_events`) is 100% Postgres, via goose migrations under
`ledger/internal/database/pg_migrations/` (`internal/database/migrations/` is a byte-identical,
CI-generated copy — `.github/workflows/review_ci.yml:34-35`). MySQL is real in the repo
(`go-sql-driver/mysql`, `gorm.io/driver/mysql` in `go.mod`) but scoped entirely to: (a) a
disabled-by-default (`isEnabled=false`, `config/default.toml:63-80`) shadow-transactions subsystem
`internal/makeshift/*` with its own MySQL-flavored migrations (unrelated tables:
`transactions`/`balance`/`credit_transaction`), and (b) external `apiDb`/`apiTiDb` connections to the
monolith, not ledger-owned. **"rx_migrations" is the RX (warm-storage/reader) Postgres database's own
migration directory** (`internal/database/rx_migrations/`, same Postgres-flavored DDL), not a
migrations-tracking table — a naming coincidence the lane brief's phrasing invited misreading.

Full DDL: `TWIN_SPEC/schema/ledger.postgres.sql`. **Zero column-presence drift** on the 4 core tables —
every model field maps to a migration column and vice versa (a handful of fields are explicitly
`sql:"-" gorm:"-"` transient, correctly non-persisted).

**Most significant finding, this lane: the journal dedup key has NO database constraint at all.**
Exhaustive `UNIQUE` grep across all 51 `pg_migrations/*.go` returns exactly 4 hits — on
`account_details` (×2), `ledger_config` (×2), `onboarding_events` — **none on `journal`**. The only
index touching the dedup columns is a plain `CREATE INDEX ON journal(transactor_id);`
(`20201001011143_create_journal.go:39`) — no index on `transactor_event` at all, alone or composite.
Dedup for `(transactor_id, transactor_event)` is a pure **application-layer check-then-insert**:
`internal/journal/validation.go:353-369` (`ValidateJournalExist` → `SELECT ... WHERE transactor_id = ?
AND transactor_event = ? AND tenant = ?`, `internal/journal/repo.go:235-260`) followed by an `INSERT`
with nothing in Postgres to reject a concurrent duplicate. **This is a real race window in
production**, not merely a twin-fidelity nuance — a faithful twin must NOT add a unique constraint
here or it will be stricter than production and mask this exact class of bug.

Enum pattern: only `state_change_logs.*` and `split_accounts.entry_type` use real Postgres native
`ENUM` types (`20221128052500_alter_state_change_logs_table.go:16-53`,
`20221211114222_create_split_accounts.go:14-20`); every other "enum" (`journal.transactor_event`,
`ledger_entries.type` debit/credit, `accounts.status`) is a plain unconstrained `VARCHAR` enforced only
via Go `ozzo-validation`. Notably, `journal.transactor_event`'s ~30-value closed list
(`internal/journal/model.go:180-219`) is enforced **only in the backfill validator**
(`internal/journal/data_backfill/validation.go:33`), **not** on the live journal-create path
(`ValidateCreateRequest`, `internal/journal/validation.go:31-79`, only checks non-empty) — any
non-empty string can become a live `transactor_event`.

Balance model: `accounts.balance` is a stored, atomically-incremented running balance —
`UPDATE accounts SET balance = balance + ? WHERE id = ? AND balance + ? >= ? RETURNING balance`
(`internal/account/repo.go:417-449`, conditional on `min_balance`); `ledger_entries.balance` is a
per-entry async snapshot (`balance_updated` flag, backlog query at `internal/journal/ledger_entry/repo.go:272-313`),
not a `SUM()` aggregate — no `SUM(` usage found anywhere in either repo file.

### 2.4 cfa (MongoDB, on AWS DocumentDB in production)

No SQL-style migrations; schema reconstructed from Go models + 4 versioned Mongo-migration files
(`internal/database/migrations/`, a genuine migration mechanism with its own tracking collection).
Full field/enum map: `TWIN_SPEC/schema/cfa.mongo.json`. Collections: `contacts`, `fund_accounts`
(polymorphic, 5 sub-types), `hash_lookup`, `tokenized_iin`, `iins`, `migrations`.

**Fidelity fact #1 (architecturally important):** production runs **AWS DocumentDB**, not vanilla
MongoDB (`pkg/storage/mongodb/mongo.go:63-82`, comment "Production AWS DocumentDB: TLS enabled,
SCRAM-SHA-1 auth"). DocumentDB has known engine-behavior gaps vs real MongoDB. Exact DocumentDB engine
version is UNKNOWN from this repo (ops/Terraform-owned).

**Fidelity fact #2 (non-obvious, affects any twin built from `bson` tags):** all reads/writes
round-trip through `encoding/json`, not native bson struct marshalling —
`pkg/storage/mongodb/transform.go:79-247` does `json.Marshal`→`json.Unmarshal`→`bson.M` and back. The
`bson:"..."` struct tags are **decorative**; the `json:"..."` tags (and `omitempty`) determine the
real persisted document shape. One live divergence: `card.Card.ID`/`card.TokenizedIIN.ID` use
`bson:"_id,omitempty"` but `json:"id"` — the actual persisted key for these nested docs is UNKNOWN
without a live sample (`internal/fund_accounts/card/model.go:13,76,136`).

**Enum cross-check (requirement #6), all CONFIRMED:**

| Enum | Declared | Enforced (create-path) | Consistent? |
|---|---|---|---|
| `fund_accounts.account_type` | `bank_account, vpa, wallet_account, card, mobile` (`constants.go:5-10`) | all but `mobile` — `ValidateWithConfig`'s switch (`model.go:164-192`) has no `mobile` case, falls to `default:` error | **NO** — `mobile` fund accounts cannot be created via this path even though `MobileDetails`/proto/response-building fully support the type |
| `fund_accounts.wallet.provider` | `paytm, amazonpay, phonepe, gpay` (`constants.go:50-53`) | `amazonpay` only (`validate.go:225-227`) | **NO** — 3 of 4 declared providers rejected |
| doc claim (`AGENTS.md:70`) says wallet value is `wallet` | actual enforced value is `wallet_account` | stale doc-comment also at `model.go:50` | live compatibility branch `internal/eventsystem/subscribers/fund_account_dual_write.go:319-320` still special-cases the legacy `"wallet"` value with an **empty switch case** (silent no-op if ever hit) — proof of historical rename |
| `hash_lookup.entity_type` | `contacts, fund_accounts` | enforced, rejects anything else | **YES** — only fully-consistent enum found |

**Uniqueness governance gap (high-signal):** `internal/database/migrations/1739361599_identify_duplicates.go`
exists specifically to scan for duplicate `id`/`hash` values and warn *"Run cleanup migrations to
remove duplicates and add unique indexes"* — no later migration ever adds them (only 4 migrations
total, `RegisterMigrations()` in `migration.go:38-45`). **`contacts.id`, `fund_accounts.id`,
`hash_lookup.hash` have NO uniqueness constraint at the database level in production.** A twin that
adds unique indexes "to be safe" would be stricter than production.

**Zero `$jsonSchema` validators are active** on any collection — two exist only as commented-out
drafts (`contacts`: `internal/database/migrations/1715709457_create_contacts_collection.go:31-55`,
validator arg passed as `nil`; `migrations` tracking collection similarly, `cmd/migration/main.go:296-313`).
All schema enforcement is Go-application-code-only.

### 2.5 x-balances (MySQL, goose, `internal/database/migrations`)

**Only one real migration exists**: `20250127224555_balance.go:19-41`, creating exactly the `balance`
table (id, created_at, updated_at, status, merchant_id, account_number, account_type, channel,
currency, balance, priority, last_change_at, last_fetched_at, metadata, last_attempted_at,
fts_fund_account_id — see `TWIN_SPEC/schema/x-balances.mysql.sql` for verbatim DDL and index list).

**`sub_balances` and `sub_balance_limits` (Go models exist: `internal/database/model/sub_balance.go`,
`sub_balance_limit.go`) have NO goose migration anywhere** — their only DDL in the entire repository is
`x-balances/queries.sql` (repo root, **not** under `internal/database/migrations`, **never** wired
into goose — `cmd/migration/main.go` only imports the migrations package). Classification: (c)
out-of-band DDL. `gh api repos/razorpay/x-balances/commits?path=queries.sql` shows this file was added
whole by PR #147 ("ISS-1605887 | ISS-1655924 | feat: add SubBalance decomposition to x-Balances
service", merged 2026-04-02T16:46:02Z); the PR description claims *"Add SubBalance and SubBalanceLimit
DB models, **migrations**, repo, and services"*, but current master's `internal/database/migrations/`
directory contains only the one, earlier (2026-03-31) `balance` migration — **the migration the PR
description claims to have added is not present in this shallow clone of master**. Either it was
reverted, never actually committed as a goose file, or "migrations" in the PR text loosely means
`queries.sql`.

Column-level drift found comparing `queries.sql`'s DDL against the CURRENT code (`internal/database/model/sub_balance.go`,
`internal/sub_balances/repo/repo.go`): the model/repo require `name` and `deleted_at` columns that
**do not exist** in `queries.sql` (`repo.go:54,69` filters `WHERE deleted_at IS NULL`;
`repo_test.go:15-16` lists `name` in its expected column set) — while `queries.sql` still carries
`master_merchant_id`/`sub_merchant_id`/`sub_account_number` columns that the code's own comments say
are **"no longer stored on the sub_balances row"** (`repo.go:76-77,109-112` — resolved via `JOIN
balance` instead). This is a real, self-acknowledged-in-code schema/DDL divergence: `queries.sql`
reflects an earlier, denormalised design; the code was refactored to a normalised one but the only
checked-in DDL was never updated to match.

The twin's own `seeds/s4/xbalances.sql:4-8` already independently discovered and documented "There is
no `sub_balance`/`sub_balance_limits` migration anywhere... the real DDL... lives only in the unwired
`x-balances/queries.sql`" — this investigation corroborates and adds the column-level detail
(name/deleted_at gap) and the PR-history provenance.

### 2.6 API-monolith DB subset (payouts' `db.API` connection)

**Definitive real table list** (grep of `payouts/internal/app/**` for `db.API`/`ContextKeyDatabaseConnection`
usage, cross-checked against `api/app/Constants/Table.php` constants) that payouts' own Go code reads
over its API-DB connection: `payouts`, `payouts_details`, `payouts_status_details`, `reversals`,
`workflow_entity_map`, `fund_transfer_attempts`, `idempotency_keys`, `features`, `balance`. Additional
tables named in the lane brief (`banking_accounts`, `contacts`, `fund_accounts`, `merchants`,
`merchant_details`, `merchant_users`, `keys`, `payout_sources`, `bank_accounts`, `vpas`) exist in the
monolith and are read by other call paths (dashboard/monolith-stub HTTP, cfa, x-balances) rather than
by payouts' own `db.API` code path directly.

**Naming corrections vs. the brief's guessed list (CONFIRMED against `api/app/Constants/Table.php`):**

| Brief's guess | Real table name | Constant |
|---|---|---|
| `ps_payout_details` | `payouts_details` (no `ps_` prefix in production) | `Table::PAYOUTS_DETAILS = 'payouts_details'` (`Table.php:83`) |
| `payouts_status_details` (also brief's own guess, this one right) | `payouts_status_details` | `Table::PAYOUTS_STATUS_DETAILS = 'payouts_status_details'` (`:90`) |
| `fund_transfer_attempt(s)` (brief flagged ambiguity) | `fund_transfer_attempts` (plural) | `Table::FUND_TRANSFER_ATTEMPT = 'fund_transfer_attempts'` (`:180`) |
| — | `workflow_entity_map` (singular "map") | `Table::WORKFLOW_ENTITY_MAP = 'workflow_entity_map'` (`:391`) |
| — | `banking_accounts` (plural) | `Table::BANKING_ACCOUNT = 'banking_accounts'` (`:316`) |

**Critical architectural fact confirmed by reading `api/app/Models/Payout/DualWrite/*.php` and
`api/app/Models/PayoutsDetails/Repository.php:91-100`:** the `ps_*`-prefixed tables
(`ps_payouts`, `ps_payout_details`, `ps_reversals`, `ps_workflow_entity_map`, `ps_payout_sources`,
`ps_idempotency_keys`, `ps_payout_logs`, `ps_payout_meta_temporary`/`permanent`,
`ps_bulk_idempotency_keys`, `ps_banking_accounts`, `ps_banking_account_statement(_details)`, all under
`api/database/migrations/2022_06-2025_07_*create_ps_*.php`) are **CI-only test scaffolding** — the
first such migration's own doc-comment states verbatim: *"This table doesn't exist on prod. It only
exists on CI. This is only to run test cases related to data migration of Payouts."*
(`2022_07_01_095954_create_ps_payouts.php:12-14`). In production, the monolith's dual-write
reconciliation connects **directly to the payouts service's own database** via a distinct Laravel
connection `Connection::PAYOUT_SERVICE_DATABASE = 'payout_service_database'`
(`api/database/Connection.php:37`), configured entirely from env vars (`PAYOUT_SERVICE_LIVE_HOST/PORT/USERNAME/PASSWORD/DATABASE/DRIVER`,
`api/config/database.php:608-623`, no literal secrets in repo) — resolved by
`getPayoutsServiceConnection()` (`api/app/Base/Repository.php:1454-1471`), which falls back to the
local `ps_<table>` names **only** when `app['env']` is `testing`/`testing_docker`
(`api/app/Models/PayoutsDetails/Repository.php:91-100`: `$tableName = 'payout_details'; if (testing)
$tableName = 'ps_' . $tableName;`). This is a **second, opposite-direction cross-service DB dependency**
(monolith → payouts-service DB) not previously documented in the existing reports' API-monolith lane —
a faithful twin of the dual-write path needs the monolith stub to reach payouts' own `payout_details`/
`payouts` tables over a second connection, not a local `ps_*` shadow table.

**Confirmed, first-party-documented dual-write column exclusions/renames** (from
`api/app/Models/Payout/DualWrite/*.php`, `columnsToUnset`/`columnConversions` class properties):

| Entity | Excluded/renamed on write into monolith DB | Citation |
|---|---|---|
| `Payout` | `gateway_ref_no`, `destination_id`, `destination_type` never written down | `Payout.php:19-23` |
| `PayoutDetails` | `id`, `beneficiary_bank_code` never written down (see §2.1 drift #1) | `PayoutDetails.php:10-13` |
| `Reversal` | payouts-service `fees`→API `fee`; payouts-service `payout_id`→API `entity_id` | `Reversal.php:12-15` |

Column-DDL for `payouts_details` (CONFIRMED, `2021_06_08_195420_create_payouts_details_table.php:20-49`):
**primary key is `payout_id` itself — there is no separate `id` column** on this table. Columns:
`payout_id` (PK), `queue_if_low_balance_flag`, `created_at`, `updated_at`, `tax_payment_id`,
`tds_category_id`, `additional_info` (json). No `beneficiary_bank_code` (by design, see above).

Column-DDL for `reversals` (CONFIRMED, `2016_12_19_110546_create_reversals.php:23-91`): **no `payout_id`
column at all** — polymorphic `entity_id`/`entity_type` only, plus `customer_id`, `fee` (singular, not
`fees`), `tax`, `currency`, `channel`, `utr`, `notes`, `transaction_id`, `transaction_type`,
`initiator_id`, `customer_refund_id`.

Column-DDL for `idempotency_keys` (CONFIRMED, `2020_03_13_162700_create_idempotency_keys_table.php:19-61`):
same unique key as the payouts-service's own table, `(idempotency_key, merchant_id)`.

Full reconstructed DDL for all 9 confirmed tables + 10 lighter-touch ones: `TWIN_SPEC/schema/apidb-subset.mysql.sql`.

---

## 3. Corrections to existing reports

| Report | Claim | What source says | Evidence |
|---|---|---|---|
| `UNRESOLVED_QUESTIONS.md:109` | "production's history for [`beneficiary_bank_code`] is unknown" | Now known: `gh api` full commit history of `payouts/internal/database/migrations` (51 commits since repo inception) shows no commit ever adds it; `api/app/Models/Payout/DualWrite/PayoutDetails.php:10-13,63-64` proves it is a **deliberate, permanent, first-party-documented** payouts-service-only column, not an accidental gap | `bd96c12a` (2021-09-03, table creation) is the only relevant commit; `PayoutDetails.php:63` comment |
| `raw-findings/31_env2_bringup_notes.md:82` | "production carries it from out-of-band DDL (gh-ost/manual)" | The *mechanism* (manual ALTER) is plausible and unconfirmed either way, but the *reason* is not drift/oversight — the monolith's own code explicitly treats this as payouts-service-internal schema it must never receive. Framing should shift from "schema drift" to "intentional cross-service schema narrowing" | `PayoutDetails.php:63-64` |
| `ARCHITECTURE_DELTA.md:89` | "schema provenance is not fully reconstructible from git... Only the first such column was found; others may exist" | Confirmed correct that more exist — this lane additionally found `source_request_id_mapping` (payouts, migration-only/dead) and `sub_balances`/`sub_balance_limits` (x-balances, out-of-band via `queries.sql`, PR #147). All three now have full gh-api-sourced provenance | `20251106120000_...go`; `queries.sql`; PR #147 |
| Lane brief's own guessed API-DB table list (`§ task`) | `ps_payout_details`, `payouts_status_details`, `fund_transfer_attempt(s)` (ambiguous) | Real production names: `payouts_details` (no `ps_` prefix — that's CI-only), `payouts_status_details` (brief's guess was right here), `fund_transfer_attempts` (plural, confirmed) | `api/app/Constants/Table.php:83,90,180`; `2022_07_01_095954_create_ps_payouts.php:12-14` |
| `ENV2_COMPOSE/seeds/mysql/apidb-ddl/00_init.sql` header (self-flagged as "best-effort reconstruction... NOT from the monolith's own real migrations") | table names `payout_status_details` (singular "payout"), `banking_account` (singular) | Real names are **plural**: `payouts_status_details`, `banking_accounts` — confirmed via `Table::PAYOUTS_STATUS_DETAILS`/`Table::BANKING_ACCOUNT` constants. `payout_status_details` (singular) as created by `00_init.sql:83` is a table name that does not exist in production and, worse, collides in spirit with the CORRECT `payouts_status_details` table separately created by `seeds/schema-patches/apidb.sql:37` — the twin's stub DB ends up with two differently-named, non-overlapping tables for the same logical entity | see §4 below |
| `ENV2_COMPOSE/seeds/schema-patches/apidb.sql:38` | `fund_transfer_attempt` (singular) | Real name is `fund_transfer_attempts` (plural) | `Table::FUND_TRANSFER_ATTEMPT = 'fund_transfer_attempts'`, `Table.php:180` |
| `ENV2_COMPOSE/seeds/schema-patches/apidb.sql:36` | `payouts_details` has a separate `id CHAR(14) PRIMARY KEY` column | Real table's primary key is `payout_id` itself; there is no separate `id` column | `2021_06_08_195420_create_payouts_details_table.php:20-25` |
| `ENV2_COMPOSE/seeds/schema-patches/apidb.sql:35` | `reversals` has `payout_id`, `fee`, and `fees` columns | Real table has neither `payout_id` (only polymorphic `entity_id`/`entity_type`) nor `fees` (only singular `fee`); also missing `customer_id`, `transaction_type`, `initiator_id`, `customer_refund_id` | `2016_12_19_110546_create_reversals.php:23-91` |
| `ENV2_COMPOSE/seeds/mysql/xbalances_seed.sql` (header self-flags as "a skeleton, not a verified fixture... TODO: confirm real table name (guessing `balances`...)") | table `balances` (plural), columns `account_type` values `shared`/`direct` | Real table is singular `balance`; this file is dead code — `scripts/up.sh`/`reset.sh` both load `seeds/s4/xbalances.sql` instead, which already has the correct name/columns/account_type enum (`direct/pool/sub_balance/master`) | `x-balances/internal/database/migrations/20250127224555_balance.go:19`; `ENV2_COMPOSE/scripts/up.sh:79-80` |
| `raw-findings/01_payouts_core.md:310` | api monolith relationship described as "Proxy target + direct DB reads + dual-write reconciliation target" | Confirmed and extended: the "dual-write reconciliation" direction is a **direct MySQL connection from the monolith into payouts' own database** (`Connection::PAYOUT_SERVICE_DATABASE`), not merely an HTTP call or a read of the monolith's own mirrored tables — this is a second, distinct cross-service DB dependency worth its own row in any dependency matrix | `api/app/Base/Repository.php:1454-1471`; `api/app/Models/PayoutsDetails/Repository.php:91-100` |

---

## 4. Twin comparison

| Component | Production mechanism | Twin mechanism (file) | Verdict | What differs | Controls |
|---|---|---|---|---|---|
| payouts core tables | 34 goose migrations, `payouts` DB | `ENV2_COMPOSE` runs real goose migrations against `mysql-payouts` (per `31_env2_bringup_notes.md`) + `seeds/schema-patches/payouts.sql` for `beneficiary_bank_code` | **REAL** (migrations) + **CONTRACT-FAITHFUL** (the one patched column) | Patch's type (`VARCHAR(50) NULL`) is INFERRED, not confirmed against prod | idempotency, payout state, merchant-visible payout details |
| fts core tables | 55 goose migrations, `fts` DB, 2 known enum gaps | Twin seeds `seeds/s4/fts.sql` directly into fts-local tables (bank_accounts, fund_accounts, source_accounts, ...); does not pre-seed `transfers`/`attempts` (populated at runtime) | **REAL** (migrations) | If the twin ever needs `mode='WALLET_TRANSFER'` or `channel='INDUSIND'` end-to-end, the tracked-migration schema itself would reject/accept inconsistently with fts's own Go code — see `fts.mysql.sql` header | attempt/transfer state, reversal |
| ledger core tables | 51 Postgres goose migrations, no unique constraint on journal dedup key | `seeds/s4/ledger.sql` (SQL fallback) or `ledger_accounts_via_api.sh` (Twirp API, recommended per its own header) | **REAL** | Twin seed file itself flags an open TODO on paise-vs-rupee unit convention (`ledger.sql:18-21`) — orthogonal to schema, not a schema drift | balance, ledger journal, reversal |
| cfa collections | Go-code-only schema, DocumentDB in prod, no unique indexes, no `$jsonSchema` | `seeds/mongo/cfa_seed.js` — direct `mongosh` insert, hand-computed SHA3-256 hashes, explicitly notes "MongoDB enforces zero uniqueness here" | **REAL** for structure; **REPRESENTATIVE** for engine (real MongoDB, not DocumentDB) | DocumentDB vs MongoDB engine-level behavior gaps (see §2.4) are unavoidable in a local twin | fund account type/provider validation, dedup |
| x-balances `balance` table | 1 goose migration, real schema | `seeds/s4/xbalances.sql` (correct, migration-sourced) — `seeds/mysql/xbalances_seed.sql` also exists but is dead/unused (wrong table name, not referenced by any script) | **REAL** | `xbalances_seed.sql` should be deleted or clearly marked superseded to avoid confusion (it is never executed but sits in the tree looking authoritative) | balance/routing selection |
| x-balances `sub_balances`/`sub_balance_limits` | Out-of-band `queries.sql` DDL, itself stale vs current code (missing `name`/`deleted_at`) | Not seeded by the twin (`seeds/s4/xbalances.sql:4-8` explicitly documents the omission and cites this exact finding) | **MISSING** (deliberately, and correctly documented as such) | If SubBalance/SubBalanceLimit flows are ever exercised in the twin, neither the "queries.sql-as-written" nor "current code" shape is fully self-consistent — build against the code-required shape (add `name`/`deleted_at`) | sub-merchant balance routing, limit allocation |
| API-DB subset: `payouts_details` | Real PK is `payout_id`; no `beneficiary_bank_code`; no separate `id` | `seeds/schema-patches/apidb.sql:36` — wrong: adds a spurious `id` PK, and (correctly) includes `beneficiary_bank_code` | **INCORRECT** (PK shape) | Extra PK column is harmless for INSERTs that always supply both, but is not schema-faithful | payout detail merchant-visible fields |
| API-DB subset: `reversals` | `entity_id`/`entity_type` polymorphic, `fee` singular, no `payout_id` | `seeds/schema-patches/apidb.sql:35` — has both `payout_id` (wrong, doesn't exist) and `entity_id`/`entity_type` (correct), both `fee` and `fees` (one is wrong) | **INCORRECT** | Extra/wrong columns don't break inserts that avoid them, but any code path doing `SELECT *` or column introspection would see a shape production never has | reversal state, merchant-visible reversal records |
| API-DB subset: `fund_transfer_attempt(s)` | Real name plural `fund_transfer_attempts` | `seeds/schema-patches/apidb.sql:38` uses singular `fund_transfer_attempt` | **INCORRECT** (table name) | Any query the monolith-stub or payouts code issues against the real plural name would 42S02 against this stub's singular table | fund-transfer status visibility |
| API-DB subset: `banking_account(s)`, `payout_status_details` vs `payouts_status_details` | Real names plural: `banking_accounts`; `payouts_status_details` | `seeds/mysql/apidb-ddl/00_init.sql:83,96` uses wrong singular names for both, self-flagged in its own header as unverified | **INCORRECT** (table names, self-acknowledged) | Creates orphan tables never queried, AND — since `seeds/schema-patches/apidb.sql:37` separately creates the CORRECT `payouts_status_details` — the stub DB ends up with two non-identical tables for one logical entity | banking account status visibility |
| API-DB subset: monolith↔payouts-service dual-write DB link | Second live MySQL connection, `payout_service_database`, monolith reads payouts' own DB directly | Not modeled at all — twin only has the one apidb stub, no second connection from a monolith-stub into payouts' own MySQL | **MISSING** | If the twin's monolith-stub ever needs to reconcile payout state via this path, it currently cannot — see §7 | reconciliation/consistency between monolith and payouts-service views of a payout |

---

## 5. Recommendation: real vs substitute

| Component | Run REAL or SUBSTITUTE | Rationale |
|---|---|---|
| payouts MySQL | **REAL** | Migrations are self-contained Go/goose, buildable with no external deps beyond MySQL itself; already exercised successfully per `31_env2_bringup_notes.md`. Only the one out-of-band column needs a schema patch (already present). |
| fts MySQL | **REAL** | Same — goose migrations are self-contained. If the twin needs `WALLET_TRANSFER`/`INDUSIND` end-to-end, patch the two ENUMs (schema-patch, not a substitute). |
| ledger Postgres | **REAL** | Goose migrations self-contained; recommend seeding via the Twirp `AccountAPI.CreateOnEvent` RPC per the twin's own `ledger_accounts_via_api.sh` header rather than raw SQL, to exercise the same account-discovery logic production traffic uses. |
| cfa Mongo | **REAL schema, SUBSTITUTE engine** | Collections/indexes are trivially replayable in any MongoDB; but production is DocumentDB, which the twin cannot practically reproduce locally — accept this as a documented engine-fidelity gap, not a reason to fake the whole datastore. |
| x-balances `balance` table | **REAL** | Single migration, trivial to run. |
| x-balances `sub_balances`/`sub_balance_limits` | **SUBSTITUTE (schema-patch)**, since no migration exists to "run for real" — apply `TWIN_SPEC/schema/x-balances.mysql.sql`'s reconciled (code-required) shape as a manual patch after the one real migration, exactly as the twin already does for payouts'`beneficiary_bank_code`. |
| API-DB subset | **SUBSTITUTE (schema-patch against corrected DDL)** | The monolith itself is far too large to run for a DB-schema purpose; the twin already runs a `mysql-apidb-stub` — just correct its DDL against `TWIN_SPEC/schema/apidb-subset.mysql.sql` (fix table names `fund_transfer_attempts`/`banking_accounts`/`payouts_status_details`, fix `payouts_details`'s PK, fix `reversals`' columns). |
| monolith↔payouts-service second DB connection (dual-write) | **SUBSTITUTE, new** | Not currently modeled. If the twin's fidelity goal includes monolith-initiated reconciliation, add a second connection string in the monolith-stub pointing at the SAME `mysql-payouts` container/DB the payouts service itself uses (mirrors production's `payout_service_database` connection) — no new datastore needed, just a second credential/DSN into the existing one. |

---

## 6. Synthetic data

Field-level detail is already captured exhaustively in the companion DDL/JSON files
(`TWIN_SPEC/schema/*`) with type/length/constraint/enum per column. Table below covers the
twin-seed-touched families with the fields/constraints that most matter for a synthetic-data
generator, per the required format.

| family/table | field | source evidence | type+length | constraints | allowed values | FK/relationships | state rules | distribution matters? | generation rule | tier |
|---|---|---|---|---|---|---|---|---|---|---|
| payouts.payouts | id | `20200615182810...go:14` | CHAR(14) | PK | Razorpay base62 id | fund_account_id→fund_accounts.id; balance_id→banking_accounts.balance_id | — | no | fixed-width synthetic id, 14 chars | EXACT |
| payouts.payouts | status | `states.go:4-33` | VARCHAR(255) | NOT NULL, no DB enum | 15 named states (§2.1) + parallel Event* set | governs `payout_status_details`, `payout_logs` rows | full transition map in `state_machine.go:15-435` | yes — must cover at least one terminal (processed/failed/reversed) + one queued/on_hold path per merchant to exercise both approval and repair flows | pick from state list per scenario | EXACT |
| payouts.payouts | gateway_ref_no | `20260305000000...go:14,20` | VARCHAR(255) utf8mb4_bin | UNIQUE, nullable | free text | — | — | no | random unique token or NULL | EXACT |
| payouts.payout_details | beneficiary_bank_code | model.go:29 (payouts-service); ABSENT from `payouts_details` in apidb | VARCHAR(50) NULL (INFERRED) | none enforced at DB | bank code strings e.g. `SBIN` (quoted verbatim from `api/tests/Functional/Payout/PayoutTest.php:3630`, an obviously-fake IFSC-style test constant) | — | must be stripped before any apidb-side row is written (dual-write `unset()`) | no | 4-char alpha bank code or NULL | REPRESENTATIVE (type inferred) |
| payouts.idempotency_keys | (idempotency_key, merchant_id) | `20221004002554...go:23` | VARCHAR(255)+CHAR(14) | UNIQUE | any string+valid merchant | merchant_id→payouts.merchant_id | re-use with same request_hash should short-circuit; different hash = conflict | yes — need both a repeat-same-key-same-body and repeat-same-key-different-body case to exercise `01_payouts_core.md:98`'s claimed behavior | one key reused across 2 requests per merchant fixture | EXACT |
| fts.transfers / fts.attempts | mode | `00005:20,29`, `00006:25` | ENUM(8 values, migration) vs 9 values (code, incl. WALLET_TRANSFER) | DB ENUM will reject `WALLET_TRANSFER` unless patched | IMPS/NEFT/RTGS/IFT/UPI/CT/(WALLET_TRANSFER)/DUITNOW/IBG | — | AMAZON_PAY channel requires WALLET_TRANSFER mode | yes if wallet-payout scenarios are in scope | pick from the 8 tracked-safe values unless ENUM patched | EXACT (tracked) / REPRESENTATIVE (if WALLET_TRANSFER needed) |
| fts.attempts_unique_keys | gateway_ref_no | `00021...go:14-18` | VARCHAR(50) | PK (implicit unique) | free text, "unique payment end to end id" | shared namespace with `attempts.gateway_ref_no` | — | no | random unique token | EXACT |
| ledger.accounts | balance | `20201001011117...go:19` widened by `20211020131618...go:15` | NUMERIC(26,6) | default 0, updated only via conditional atomic UPDATE | paise-as-integer assumed (twin's own `ledger.sql:18-21` flags this as unconfirmed) | account_id referenced by `ledger_entries.account_id`, `account_details.account_id` | must stay >= min_balance per the conditional UPDATE guard | yes — need at least one insufficient-balance rejection case | seed opening balance per merchant per twin's `ledger.sql` parent-account pattern | REPRESENTATIVE (unit convention unconfirmed) |
| ledger.journal | (transactor_id, transactor_event) | no DB constraint, `internal/journal/validation.go:353-369` | VARCHAR(50)/VARCHAR(60) | app-only dedup | transactor_event: ~30-value closed list only enforced in backfill (`model.go:180-219`), open at live-create time | — | race window exists — do NOT rely on DB rejecting a duplicate | yes — a concurrency test scenario is meaningful here given the confirmed gap | fixed synthetic transactor_id/event pairs, deliberately duplicate one pair to test the race if the twin does concurrency testing | EXACT (schema) |
| cfa.contacts | hash | SHA3-256 dedup, `AGENTS.md:65`, no unique index (`1739361599_identify_duplicates.go`) | string | none enforced at DB | SHA3-256 hex of a specific compact-JSON serialization (`internal/contacts/service.go:215-235`) | referenced by `hash_lookup.entity_id` when `entity_type=contacts` | duplicates ARE possible in production (no unique index) — a synthetic fixture with 2 contacts sharing a hash is representative, not a bug | yes — twin's own `cfa.js` precomputes real SHA3-256 to match production's exact algorithm | precompute with Python hashlib.sha3_256 against the documented input format (twin's approach, confirmed correct) | EXACT |
| cfa.fund_accounts | account_type | `constants.go:5-10` | string discriminator | validated, but `mobile` rejected at create (`model.go:164-192`) | bank_account / vpa / wallet_account / card / (mobile — cannot actually be created) | — | do not generate `mobile` fund-account fixtures via the normal create path — it will fail | no | pick from the 4 creatable values | EXACT |
| cfa.fund_accounts.wallet | provider | `constants.go:50-53` declares 4, `validate.go:225-227` accepts 1 | string | only `amazonpay` passes create validation | paytm/amazonpay/phonepe/gpay declared; only amazonpay creatable | — | do not generate paytm/phonepe/gpay wallet fixtures via create path | no | `amazonpay` only | EXACT |
| x-balances.balance | account_type | `queries.sql:8`, `model.go:24` | VARCHAR(50) | none at DB | direct / pool / sub_balance / master | sub_balance rows relate to `sub_balances.sub_balance_id` | twin's `xbalances.sql` already maps M1/M3→`pool`, M2→`direct` correctly per BOM | no | as twin already does | EXACT |
| x-balances.sub_balances | name, deleted_at | code-required, absent from `queries.sql` DDL (§2.5) | VARCHAR(255) NOT NULL / BIGINT NULL | code needs both; DDL-as-written has neither | free text / unix ts or NULL | — | `deleted_at IS NULL` filter used on every read (`repo.go:54,69`) | no | if this table is ever seeded, add both columns per the code-required shape, not the literal `queries.sql` text | ASSUMED (code-required shape, not confirmed prod shape) |
| apidb.payouts_details | (no `id` column) | `2021_06_08_195420...php:20-25` | — | PK is `payout_id` | — | payout_id→payouts.id | — | no | never generate a separate `id` value for this table | EXACT |
| apidb.reversals | entity_id/entity_type | `2016_12_19_110546...php:23-91` | CHAR(14)/CHAR(255) | entity_type always `'payout'` in this flow (per `Reversal.php:14` conversion from payouts-service `payout_id`) | — | entity_id→payouts.id when entity_type=payout | — | no | entity_type='payout', entity_id=<payout id> | EXACT |

---

## 7. Cannot be derived from repositories

| Missing artifact | Why it matters | Likely owner | Minimal request | Schema-only sufficient? |
|---|---|---|---|---|
| `SHOW CREATE TABLE payouts.payout_details` (production, payouts-service's own DB) | Confirms `beneficiary_bank_code`'s actual type/length/nullability/default and whether any OTHER out-of-band column exists on this table beyond the one found | Payouts platform team (DB owner for `payouts` service MySQL) | Schema-only `SHOW CREATE TABLE` export, no rows | Yes |
| Full column DDL for `payouts` (API monolith's copy) beyond the first ~14 columns transcribed in `apidb-subset.mysql.sql` | The monolith's `payouts` migration (`2016_06_16_081431_create_payouts_table.php`) is long (~30+ columns after later ALTERs); only the primary-key/FK-relevant prefix was re-verified this pass | API-DB / monolith owners | Either read the rest of the migration file (available in-repo, just not done this pass — cheap to close) or a schema-only export | Yes, and likely resolvable from the repo alone with more time |
| Live `SHOW CREATE TABLE api.banking_accounts` | `2019_05_28_105812_create_banking_account_table.php` was only read to line 60; the table likely has more columns (gateway states, TPV flags, etc. per the migration list seen: `banking_account_state`, `banking_account_details`, `banking_account_tpvs` as SEPARATE tables suggest the base table itself may be narrower than assumed) | API-DB / monolith owners (banking-accounts feature team) | Schema-only export | Yes |
| `x-balances/queries.sql`'s actual applied state in production (was it ever run as written, or already patched to add `name`/`deleted_at`?) | Determines whether `sub_balances`/`sub_balance_limits` in production match the file-as-written or the code-required shape found in this investigation | x-balances service owners | `SHOW CREATE TABLE sub_balances, sub_balance_limits` (schema-only) from any environment where PR #147's feature has shipped | Yes |
| `db.hash_lookup.getIndexes()`, `db.tokenized_iin.getIndexes()`, `db.iins.getIndexes()` (cfa, live DocumentDB) | No index-creation code exists anywhere in the repo for these 3 collections despite `hash_lookup` existing specifically for hash-based lookup and `tokenized_iin` being queried by `token_iin`+`active` — unknown whether indexes were added out-of-band via console/runbook | cfa / DocumentDB ops owner | Schema-only index list export (`getIndexes()` output, no documents) | Yes |
| cfa DocumentDB engine version | Determines which MongoDB-compatible behaviors (aggregation operators, `$jsonSchema` support, TTL indexes) the twin can/cannot faithfully emulate with vanilla MongoDB | cfa / infra / Terraform owner | Terraform var or AWS console value (`engine_version`) | Yes, a single value suffices |
| `payouts`' production migration-application log (has `20260720000001` — the newest migration — actually been applied everywhere, and was `beneficiary_bank_code` applied via a tool that left any trace, e.g. a gh-ost/pt-osc changelog) | Would let us stop guessing "gh-ost/manual" and know definitively how the out-of-band column was applied, and confirm no other out-of-band columns exist on other payouts tables | Payouts platform / DBRE | DBRE runbook or gh-ost changelog table export (schema-only, e.g. `_payout_details_gho` artifacts if any survive) | Likely yes, if such a log exists at all — otherwise UNKNOWN-BLOCKED permanently |
| Full `2019_05_07_142948_create_banking_account_statement_table.php` and related `ps_banking_account_statement*`/`banking_account_statement_pool_{rbl,icici}` DDL | Not investigated this pass at all (out of the lane's core 9-table scope); relevant only if the twin's banking-account-statement flows need API-DB fidelity | API-DB / monolith owners (RX/statement team) | Schema-only export or a follow-up repo read | Yes, resolvable from repo with more time |

---

## 8. Fidelity tier verdicts

- **payouts core schema (30 tables)**: REAL — migrations are self-contained and already proven to run
  (`31_env2_bringup_notes.md`); one column (`beneficiary_bank_code`) needs the existing schema-patch,
  now confirmed as intentional/permanent rather than accidental drift.
- **payouts state machine / idempotency / indexes**: REAL — full transition map and unique constraints
  read verbatim from source; no gaps.
- **fts core schema (transfers/attempts/attempts_unique_keys/transfer_meta)**: REAL — zero
  column-presence drift; two enum-value gaps (`WALLET_TRANSFER`, `INDUSIND`) are schema-patchable if
  those scenarios are in scope, else irrelevant.
- **ledger core schema**: REAL — confirmed Postgres, zero column drift; the missing journal unique
  constraint is a genuine production characteristic to PRESERVE (not fix) for fidelity.
- **cfa schema**: CONTRACT-FAITHFUL SUBSTITUTE — structure/enums are fully reconstructible and the
  twin's own seed already gets the hard part (SHA3-256 hash matching) right; engine identity
  (DocumentDB vs MongoDB) is an accepted, unfixable substitution.
- **x-balances `balance`**: REAL — one migration, already correctly seeded by the twin.
- **x-balances `sub_balances`/`sub_balance_limits`**: UNKNOWN-BLOCKED for exact production shape (no
  migration, `queries.sql` self-contradicts current code) — build to the code-required shape as a
  documented REPRESENTATIVE substitute if these flows are ever exercised; currently correctly omitted.
- **API-monolith DB subset**: REPRESENTATIVE SUBSTITUTE, currently with real, fixable errors — table
  names (`fund_transfer_attempt`→`fund_transfer_attempts`, `banking_account`→`banking_accounts`,
  `payout_status_details`→`payouts_status_details`) and column shapes (`payouts_details`'s spurious
  `id` PK, `reversals`' wrong/missing columns) should be corrected against
  `TWIN_SPEC/schema/apidb-subset.mysql.sql`; the single most important reason this isn't REAL is that
  the monolith itself cannot be run, so its DDL must be hand-maintained and had drifted from the
  now-confirmed source of truth.
- **monolith↔payouts-service dual-write DB link**: UNKNOWN-BLOCKED / MISSING entirely from the twin —
  not a schema question but an architecture gap; the single most important reason is that no second
  DB connection from a monolith-stub into `mysql-payouts` currently exists in `ENV2_COMPOSE`.
