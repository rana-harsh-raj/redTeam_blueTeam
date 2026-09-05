# SYNTHETIC_FIXTURE_SPEC — Env 2 arena (public API payout, idempotency, webhooks)

Companion to `INITIAL_PAYOUTS_ENVIRONMENT_BOM.md` Sec 2 (Environment 2). Defines the concrete
synthetic entity model — 3 merchants, their identities, balances, routing, and every
external-dependency stub's seed data — needed to run Env 2's golden flows. Every id is clearly
synthetic: `ARENA`-prefixed, or a real Razorpay id *shape* with an obviously fake body. Nothing here
is a real merchant, bank account, or credential. Loadable seed files live in `env2-seeds/` (see that
directory's `README.md` for load order and how to apply each file).

Ground truth for every claim below was read directly from migration files and seed-data Go source in
the read-only repo clones, not guessed. Every place a real value could not be confirmed is marked
`TODO(confirm)` rather than filled with an invented-but-plausible value.

## 0. ID scheme and hard schema constraints

The task brief's example ids (`pout_ARENA00000001`, `merchant ids ARENAM0000000001..3`, etc.) don't
all fit the real column widths once you read the migrations: `payouts.merchant_id`,
`banking_accounts.id`, `fts.source_accounts.merchant_id`, and ledger's `accounts.id`/`merchant_id`
are all fixed-width `CHAR(14)`. A too-long string silently truncates in MySQL/Postgres `CHAR(n)`,
which can create id collisions across otherwise-distinct rows. This spec keeps the *spirit* of the
requested shapes but hand-counts every `CHAR(14)`-bound id to exactly 14 characters.

| Entity | Column | Type | Id pattern used here |
|---|---|---|---|
| Merchant | `merchant_id` (everywhere) | `CHAR(14)` | `ARENAM` + 8-digit seq, e.g. `ARENAM00000001` |
| Platform pool-owner (pseudo-merchant) | fts `fund_accounts.merchant_id` | `CHAR(14)` | `ARENAM00000000` |
| Finance user (M3) | passport `consumer.id` | free | `ARENAU` + 8-digit seq |
| payouts `banking_accounts.id` | `CHAR(14)` | | `ARENABA` + 7-digit seq |
| payouts `counters.id` | `CHAR(14)` | | `ARENACTR` + 6-digit seq |
| payouts `payout_purpose.id` | `CHAR(14)` | | `ARENAPP` + 7-digit seq |
| payouts `workflow_config.id`/`workflow_state_map.id` | `CHAR(14)` | | `ARENAWC`/`ARENAWS` + 7-digit seq |
| payouts-local `fund_accounts.id`/`bank_accounts.id` | `CHAR(14)` | | `ARENAFA`/`ARENABK` + 7-digit seq |
| x-balances `balance.id` | `VARCHAR(14)` | | `ARENABAL` + 6-digit seq |
| ledger `accounts.id`/`account_details.id`/`ledger_config.id` | `CHAR(14)` (Postgres) | | see Sec 6 (mix of `ARENAM1ACC…`/`ARENAM3ACC…` per-merchant and the real production parent-account constants for shared "parent" rows) |
| CFA `contacts.id`/`fund_accounts.id` (app-level, not Mongo `_id`) | free string | | `ARENACO`/`ARENAFAX` + 7/6-digit seq |
| CFA `hash_lookup.id` | free string | | `ARENAHL` + 7-digit seq |
| FTS `fund_accounts.id`/`bank_accounts.id`/`source_accounts.id`/`source_account_mappings.id` | `int(11) AUTO_INCREMENT` | **integer, not a string** | fixed block `900001`/`900002` (well clear of default auto-increment start at 1) |

Deviation from the task's literal example: `merchant ids ARENAM0000000001..3` is 16 characters (would
truncate/collide in `CHAR(14)`) — this spec uses `ARENAM00000001`/`…002`/`…003` (14 chars exactly)
instead. All other prefixed public-facing shapes (`pout_…`, `acc_…`, `fa_…`, `cont_…`, `rzp_test_…`)
are presentation-layer concatenations added by the real API/SDK, never stored in a DB column — this
spec follows that convention: the bare 14-char id lives in the database; a consumer that needs the
prefixed public form prepends it, e.g. CFA fund account `ARENAFAX000001` → `fa_ARENAFAX000001`.

Amount unit: paisa-as-integer throughout (payouts `amount`/`fees`/`tax` are unambiguously paisa;
ledger's `NUMERIC(26,6)` columns are ambiguous between paisa and rupees — this spec assumes paisa for
internal consistency and flags it `TODO(confirm)`, see Sec 6).

## 1. Merchants

| | M1 | M2 | M3 |
|---|---|---|---|
| Merchant id | `ARENAM00000001` | `ARENAM00000002` | `ARENAM00000003` |
| Model | Shared/Lite, ledger-backed balance | Direct, current account, channel RBL, x-balances-mirrored balance | Shared, workflow-enabled, ledger-backed balance |
| `banking_accounts.account_type` | `shared` | `direct` | `shared` |
| `banking_accounts.channel` | `NULL` (pool account, column is nullable) | `rbl` | `NULL` |
| DCS profile | all off (baseline) | `in_flight_reservation_enabled=true`, `payout_service_enabled=true` | `enable_payout_workflow=true`, `block_va_payouts=true` |
| Golden-flow role | Shared processed, insufficient-balance queue/fail | Direct processed, Shield block (beneficiary suffix 9999) | workflow / approval, DCS dual-name-flag matrix |

Both M1 and M3 use `shared`: RazorpayX Shared/Lite-balance merchants pool through a shared RBL nodal
account. Each still gets its own ledger `MerchantBalance` sub-account (Sec 6, keyed by their own
`banking_account_id`) — the pooling happens at the FTS/bank layer (Sec 7, shared `source_account_id
900001`), not at the ledger layer, where every merchant's balance is tracked separately regardless of
whether they share a physical bank account.

**Channel casing note**: payouts' own channel constants are lowercase
(`payouts/internal/app/common/appConstants/constants.go:61-70` — `RBL="rbl"`, `AXIS="axis"`,
`ICICI="icici"`, `YESBANK="yesbank"`, `IDFC="idfc"`), confirmed as the value real write paths assign
(`internal/app/bankingAccount/core.go:140,468`). FTS's *own* `channel` column is a separate MySQL
`ENUM` storing the uppercase form (`'RBL'`, confirmed in `fts/internal/migrations/00007…`). This
casing asymmetry between the two services is real, not a typo in this spec — `payouts.sql` uses
lowercase, `fts.sql` uses uppercase, each matching its own service's real convention.

## 2. Users / roles (M3 only)

Payouts has no local `users` table (`payouts.user_id` references the API-monolith's user store, which
is unreadable/out of scope). These ids are therefore consumed only by the passport and monolith stub
substitutes — nothing to seed into payouts' own MySQL for them.

| Role | User id | Passport shape |
|---|---|---|
| `finance_l1` | `ARENAU00000001` | proxy + impersonation, `roles: ["finance_l1"]` |
| `finance_l2` | `ARENAU00000002` | proxy + impersonation, `roles: ["finance_l2"]` |
| `owner` | `ARENAU00000003` | proxy + impersonation, `roles: ["owner"]` |

Full claim bodies in `env2-seeds/passport_fixtures.json`. AuthZ *enforcement* of these roles is
`BLOCKED_BY_FIDELITY_GAP` per the BOM (the real authz/api-monolith repos are unreadable) — these
fixtures let PS-side branching (if any) be exercised, not real per-route permission checks.

## 3. API keys per merchant

Payouts has no local API-key table either — `private`-mode auth is verified upstream (Kong/monolith)
and arrives as a passport claim. These are the credential identifiers the Kong-lite/monolith
substitute must recognize and turn into a `private` passport claim for M1/M2:

| Merchant | Key id | Key secret (placeholder, never real) |
|---|---|---|
| M1 | `rzp_test_ARENAM0000001` | `arena_secret_m1_0000000000000001` |
| M2 | `rzp_test_ARENAM0000002` | `arena_secret_m2_0000000000000002` |
| M3 | `rzp_test_ARENAM0000003` | `arena_secret_m3_0000000000000003` (rarely used directly — M3's finance users' proxy/impersonation claims are the primary path) |

## 4. `banking_accounts` (payouts MySQL)

Schema (`payouts/internal/database/migrations/20200922102712_create_banking_accounts_table.go`):
`id, merchant_id, balance_id, channel, status, account_number, account_type, fts_fund_account_id,
payout_service_enabled, counter_migrated, created_at, updated_at`. No later `ALTER TABLE` exists for
this table — the above is the complete, current schema. Full INSERTs in `payouts.sql`.

| Field | M1 (`ARENABA0000001`) | M2 (`ARENABA0000002`) | M3 (`ARENABA0000003`) |
|---|---|---|---|
| `merchant_id` | `ARENAM00000001` | `ARENAM00000002` | `ARENAM00000003` |
| `balance_id` | `ARENABAL000001` | `ARENABAL000002` | `ARENABAL000003` |
| `channel` | `NULL` | `rbl` | `NULL` |
| `status` | `activated` | `activated` | `activated` |
| `account_number` | `2323230099999999` (shared pool number, matches `fts.sql`/`xbalances.sql`) | `2323230000000002` | `2323230099999999` (same pool as M1) |
| `account_type` | `shared` | `direct` | `shared` |
| `fts_fund_account_id` | `'900001'` | `'900002'` | `'900001'` |
| `payout_service_enabled` | `1` | `1` | `1` |
| `counter_migrated` | `1` | `1` | `1` |

**Confirmed cross-service type mismatch**: `banking_accounts.fts_fund_account_id` is `CHAR(14)` in
payouts' own schema, but the row it references — FTS's `fund_accounts.id` — is a plain
`int(11) AUTO_INCREMENT`. This spec stores the stringified integer (`'900001'`) in the `CHAR(14)`
column, which is under-length so no truncation risk; the exact write-side code that populates this
column on a real activation was not traced in this pass — treat the mapping as schema-consistent but
not verified against a real write path. `balance_id` is polymorphic by design: for `shared` merchants
(M1, M3) it points at the x-balances `balance` row used for account **selection** (Sec 5) even though
the ledger account (Sec 6) is the number's source of truth; for `direct` (M2) x-balances is also the
number's source of truth.

## 5. `balance` rows (x-balances MySQL) — and a real schema gap found

Migration `x-balances/internal/database/migrations/20250127224555_balance.go` creates **exactly one
table, `balance`**. There is no `sub_balance`/`sub_balance_limits` migration anywhere in
`x-balances/internal/database/migrations/` — `internal/database/model/sub_balance.go` and
`sub_balance_limit.go` are Go model files with no corresponding `CREATE TABLE`; their DDL exists only
in the unwired `x-balances/queries.sql` at repo root. Independently confirmed by
`findings/28_build_spike_x-balances.md`: a freshly-migrated x-balances database has exactly 2 tables
(`balance`, `goose_db_version`). **`sub_balance` cannot be seeded** — the table does not exist in this
codebase's actual migrated state; `xbalances.sql` seeds `balance` only.

`balance` columns: `id, created_at, updated_at, status, merchant_id, account_number, account_type,
channel, currency, balance, priority, last_change_at, last_fetched_at, metadata, last_attempted_at,
fts_fund_account_id`. `account_type` confirmed values: `direct`, `pool`, `sub_balance`, `master`
(`internal/database/model/balance.go` + `queries.sql`) — this spec uses `pool` for M1/M3's Shared/Lite
accounts (the informal "shared (Lite VA)" language in `findings/06_balances_banking_accounts.md` maps
to this literal enum value).

**All three merchants get a row, not just M2**: per `findings/06_balances_banking_accounts.md`,
payouts selects its source account/balance for a payout by calling x-balances' `ListAccounts`
(account **selection**) regardless of account type — only the live *number* differs by type (Direct:
x-balances' own polled value is authoritative; pool/shared: x-balances' own enricher overwrites the
stored number in real time from Ledger at read time, per `internal/enrichment/enricher.go`). So M1/M3
need a `balance` row here for selection/routing to work, even though ledger.sql is the number's
source of truth for them.

| Field | M1 (`ARENABAL000001`) | M2 (`ARENABAL000002`) | M3 (`ARENABAL000003`) |
|---|---|---|---|
| `merchant_id` | `ARENAM00000001` | `ARENAM00000002` | `ARENAM00000003` |
| `account_number` | `2323230099999999` (pool) | `2323230000000002` | `2323230099999999` (pool) |
| `account_type` | `pool` | `direct` | `pool` |
| `channel` | `RBL` | `RBL` | `RBL` |
| `currency` | `INR` | `INR` | `INR` |
| `balance` | `10000000` (paisa; overwritten at read time from ledger for pool type) | `10000000` (paisa; authoritative here) | `10000000` (paisa; overwritten at read time from ledger) |
| `fts_fund_account_id` | `'900001'` | `'900002'` | `'900001'` |

## 6. Ledger accounts (Postgres, tenant X) — bootstrap mechanism and opening balances

**Real production bootstrap mechanism**: ledger accounts are created via the `AccountAPI.CreateOnEvent`
Twirp RPC (`POST /twirp/rzp.ledger.account.v1.AccountAPI/CreateOnEvent`, header `Ledger-Tenant: X`),
confirmed from the actual on-call scripts `ledger/scripts/oncall/create_merchant_account/main.go` and
`create_fts_account/main.go`, plus a functional-test worked example
(`ledger/test/functional/testdata/account.go:1211-1262`) that shows the request/response shape,
**including a supported `merchant_balance_opening_balance` request field** — a fresh account *can* be
created with a non-zero opening balance in the same call, it is not a separate adjustment step. Full
request bodies for M1/M3 and the shared/dedicated nodal pool accounts are in
`env2-seeds/ledger_accounts_via_api.sh`, which is the **recommended** seeding path. `env2-seeds/ledger.sql`
is a schema-correct pure-SQL fallback for when the ledger container isn't reachable at seed time.

**Account-discovery model** (`ledger/internal/journal/ledger_config/seed_data/shared_account_x.go`):
each sub-account type is matched by `(AccountCategory, AccountType, FundAccountType)` plus an
`Identifiers` map that names the request-entity field the account is keyed on:
`MerchantBalanceAccount`/`VendorPayableAccount`/`CommissionIncomeAccount`/`OutputGSTAccount` are keyed
by `banking_account_id`; `FtsPayableAccount`/`FtsReceivableAccount` are keyed by `fts_fund_account_id`.
Every sub-account is created under one of a small set of **fixed, real production parent accounts**
(`internal/account/seed_data/shared_account_x.go`'s `getXSharedParentAccountRequestData()`), owned by
fixed system merchant-ids baked into the ledger codebase itself (not secrets — publicly visible in the
readable repo): Merchant Balance (`Gg614JldVI2nJi`), Vendor Payable (`Gg6I8KieFph8kj`), Commission
Income (`Gg6I8IF5vrmp6k`), Output GST (`Gg6I8GsKzz6CBl`), Nodal Receivable (`Gh0YfwUewxUqdm`), Nodal
Payable (`Gh0YfqKiXRdkCo`). `ledger.sql` creates these parent rows first (a fresh arena Postgres has
none of them), using the real constants rather than inventing synthetic ones, since real ledger code
may resolve against them by these literal values.

Seeded per-merchant accounts (M1, M3 — **M2 intentionally has none of these**; see below):

| Account | M1 `accounts.id` | M3 `accounts.id` | Category | Opening balance (paisa) |
|---|---|---|---|---|
| MerchantBalance | `ARENAM1ACC0001` | `ARENAM3ACC0001` | liability | `10000000` (Rs 1,00,000.00) each |
| VendorPayable | `ARENAM1ACC0002` | `ARENAM3ACC0002` | liability | `0` |
| CommissionIncome | `ARENAM1ACC0003` | `ARENAM3ACC0003` | revenue | `0` |
| OutputGST | `ARENAM1ACC0004` | `ARENAM3ACC0004` | liability | `0` |

Plus FTS nodal receivable/payable, keyed by `fts_fund_account_id` (not per-merchant — these track
Razorpay's own nodal bank balance): one pair for the M1/M3 shared pool (`fts_fund_account_id='900001'`,
`accounts.id` `ARENAPOOLACC01`/`02`), one pair for M2's dedicated Direct account
(`fts_fund_account_id='900002'`, `accounts.id` `ARENAM2NODAC01`/`02`) — included for completeness even
though M2's authoritative balance lives in x-balances, not ledger.

**Why M2 has no MerchantBalance/VendorPayable/CommissionIncome/OutputGST rows**: per
`findings/13_fts_payouts_status_path.md` finding #6, "CA (Direct) payouts skip ledger entirely here...
done after BAS linking" — Direct merchants' authoritative balance and fee bookkeeping is x-balances
(Sec 5), not this ledger pattern.

**`ledger_config`**: the full accounting-rule set is 34 rows (26 primary + 8 legacy transactor-event
configs), returned by `GetXSharedAccountingLedgerConfigs()` in
`ledger/internal/journal/ledger_config/seed_data/shared_account_x.go`. Hand-transcribing all 34 into
SQL risked transcription error; `ledger.sql` seeds the two most Env2-relevant rows verbatim
(`XPayoutInitiatedV2`: credit VendorPayable(amount), debit MerchantBalance(commission+amount), credit
CommissionIncome(commission-tax), credit OutputGST(tax); `XPayoutProcessedV2`: debit
VendorPayable(amount), credit FtsPayable(amount)) and documents that the full set should ideally be
generated by calling the real Go function directly rather than re-typing the remaining 32.

`ledger.sql` additionally posts a synthetic double-entry opening-balance journal (`journal`/
`ledger_entries` rows, event name `ARENAOpeningBalanceV2` — an invented, clearly-arena-only event, not
a real ledger constant) crediting each merchant's `MerchantBalance` against a debit to the matching
parent account, purely so a verifier reading the `journal`/`ledger_entries` audit trail (not just
`accounts.balance`, which this spec sets directly) also sees a real posting. This is a best-effort
modeling choice, not verified against real ledger code — prefer `ledger_accounts_via_api.sh`'s
`CreateOnEvent` flow if the exact real opening-balance/positive-adjustment event shape matters.

**Amount unit — `TODO(confirm)`**: `accounts.balance`/`journal.amount`/`ledger_entries.amount` are
`NUMERIC(26,6)`, precise enough for either paisa-as-integer or rupees-with-decimals. The functional
test's worked example shows `"merchant_balance_opening_balance": "1050"` for currency INR without
stating the unit. This spec assumes **paisa as a whole number**, consistent with `payouts.amount`
(unambiguously paisa) and every other amount fixture in this arena — confirm against
`ledger/internal/account/core.go`'s `money.Money` handling before trusting exact-balance assertions.

## 7. FTS source_accounts / mappings / routing (so `/v1/transfer` resolves for channel RBL, mode IMPS/NEFT)

Ground truth: `fts/internal/migrations/00001` (bank_accounts), `00003` (fund_accounts), `00007`
(source_accounts), `00011` (source_account_mappings), `00017` (preferred_routing_weights), `00018`
(account_type_mappings) — confirmed against the 45-table live list in
`findings/28_build_spike_fts.md` (MySQL 8.0.46, goose version 50). All PKs here are
`int(11) AUTO_INCREMENT`, not `CHAR(14)` — this spec fixes them at `900001`/`900002`.

**Modeling note**: `ARENAM00000000` is a synthetic platform/pool-owner pseudo-merchant, not one of the
3 seeded merchants — it exists only so `fund_accounts.merchant_id` (`NOT NULL`) has a value to own the
shared RBL pool nodal bank account that M1 and M3 route through.

FTS-local `bank_accounts` (the actual `account_number`/`ifsc_code` identity):

| `id` | `merchant_id` | `account_type` | `account_number` | `ifsc_code` | `beneficiary_name` |
|---|---|---|---|---|---|
| `900001` | `ARENAM00000000` | `NODAL` | `2323230099999999` | `ARNA0000001` | ARENA RZPX POOL NODAL ACCOUNT |
| `900002` | `ARENAM00000002` | `CURRENT` | `2323230000000002` | `ARNA0000001` | ARENA MERCHANT TWO PRIVATE LIMITED |

FTS-local `fund_accounts` (points a `bank_accounts` row into the generic fund-account abstraction —
this is **not** the same entity as CFA's `fund_accounts`, which is the payout's *beneficiary*; this is
FTS's internal representation of Razorpay's own bank account used for routing):

| `id` | `merchant_id` | `account_type` | `account_id` | `default_channel` |
|---|---|---|---|---|
| `900001` | `ARENAM00000000` | `BANK_ACCOUNT` | `900001` | `RBL` |
| `900002` | `ARENAM00000002` | `BANK_ACCOUNT` | `900002` | `RBL` |

`source_accounts` (the routable RZP-side channel/bank source; `mozart_identifier` is `TODO(confirm)`
— the exact string `fts/internal/providers/mozart` expects was not independently confirmed, only the
`/{namespace}/{gateway}/{version}/{action}` URL shape; `'rbl_v1'` is a plausible placeholder):

| `id` | `channel` | `fund_account_id` | `mozart_identifier` | `bank_account_type` | `account_type` |
|---|---|---|---|---|---|
| `900001` | `RBL` | `900001` | `rbl_v1` | `NODAL` | `POOL` |
| `900002` | `RBL` | `900002` | `rbl_v1` | `CURRENT` | `DIRECT` |

`source_account_mappings` — the row `/v1/transfer` actually resolves against (`merchant_id` + `channel`
+ `mode` + `product` + `operation='TRANSFER'`, `routing_enabled=1`, one row per merchant per mode so
both IMPS and NEFT resolve):

| `merchant_id` | `source_account_id` | `source_account_type` | `account_type` | `mode` | `priority` |
|---|---|---|---|---|---|
| `ARENAM00000001` | `900001` | `NODAL` | `POOL` | `IMPS` / `NEFT` | `1` |
| `ARENAM00000003` | `900001` | `NODAL` | `POOL` | `IMPS` / `NEFT` | `1` |
| `ARENAM00000002` | `900002` | `CURRENT` | `DIRECT` | `IMPS` / `NEFT` | `1` |

`account_type_mappings` (merchant/product/mode → NODAL for M1/M3, CURRENT for M2) and
`preferred_routing_weights` (weight 100 on the single eligible `source_account_id` per mode — a
single-candidate arena, so the exact weight value is inert but required by the `NOT NULL` column) round
out the resolution chain. Full INSERTs in `fts.sql`.

## 8. CFA contacts + fund accounts

Ground truth: `cfa/internal/fund_accounts/model.go` (`GenerateHash`), `cfa/internal/contacts/service.go`
(`generateContactHash`), all 4 CFA migration files (confirms **zero unique indexes** exist on any of
`contacts`/`fund_accounts`/`hash_lookup` — dedup is entirely application-code-enforced, not
DB-enforced).

**Verdict: direct MongoDB seeding (`cfa.js`) is correct and sufficient** — no live CFA API call is
required — provided (a) hash values are computed with the exact real algorithm, and (b)
`hash_lookup` rows are written by hand alongside each doc (nothing else prevents duplicates). Both
hashes are **SHA3-256** (NIST FIPS 202, `golang.org/x/crypto/sha3` — **not** Keccak-256), hex-encoded,
no secret salt, so fully reproducible outside Go:

- Contact hash: `SHA3-256(json_compact_sorted({contact, email, merchantID, name, referenceID, type}))`
  — Go's `json.Marshal` on a map sorts keys alphabetically with no extra whitespace.
- Fund-account hash (bank account): `SHA3-256("<merchantID>|contact|<contactID>|bank_account|<accountNumber stripped>|<IFSC upper stripped>|<name, stripped via [^a-zA-Z0-9-&'._()/]+>")`.
- Fund-account hash (VPA): `SHA3-256(lowercase("<username>|<handle stripped>"))` prefixed the same way
  — note the real username-stripping regex (`/[^a-zA-Z0-9.-]+/`) has a bug (literal `/` delimiters
  baked into the pattern) that makes it almost never match, so a VPA username with dots is preserved
  as-is in practice; reproduced faithfully in `cfa.js`'s precomputed hashes.

`cfa.js` hardcodes these hashes as literal precomputed values (via Python's `hashlib.sha3_256`, the
same standard) rather than relying on mongosh's JS sandbox to compute SHA3-256 itself, since that
runtime doesn't reliably expose Node's crypto module.

IDs: CFA's app-level `id` field (never Mongo's own `_id`, which nothing in the app queries by) is a
14-char string from `goutils/uniqueid` in production, with no server round-trip or counter-service
dependency — any unique string works functionally; this spec uses 14-char `ARENA…` ids for
recognizability.

Contacts (one per fund account, since fund accounts are the more natural unit here — 4 contacts, not
one per merchant):

| Contact | `id` | `merchant_id` | `name` | Owns fund account |
|---|---|---|---|---|
| C1 | `ARENACO0000001` | `ARENAM00000001` | Arena Vendor One | FA1 (M1's active bank account) |
| C2 | `ARENACO0000002` | `ARENAM00000002` | Arena Vendor Two | FA2 (M2's active bank account) |
| C3 | `ARENACO0000003` | `ARENAM00000003` | Arena Vendor Three VPA | FA3 (M3's active VPA) |
| C4 | `ARENACO0000004` | `ARENAM00000001` | Arena Vendor Inactive | FA4 (M1's inactive account) |

Fund accounts (2 bank + 1 VPA + 1 inactive, as required):

| Fund account | `id` | `merchant_id` | `account_type` | Details | `active` |
|---|---|---|---|---|---|
| FA1 | `ARENAFAX000001` | `ARENAM00000001` | `bank_account` | acct `2323230000000101`, IFSC `ARNA0000001`, name "Arena Vendor One" | `true` |
| FA2 | `ARENAFAX000002` | `ARENAM00000002` | `bank_account` | acct `2323230000000201`, IFSC `ARNA0000001`, name "Arena Vendor Two" | `true` |
| FA3 | `ARENAFAX000003` | `ARENAM00000003` | `vpa` | `arena.user@arenabank` (username `arena.user`, handle `arenabank`) | `true` |
| FA4 | `ARENAFAX000004` | `ARENAM00000001` | `bank_account` | acct `2323230000009999`, IFSC `ARNA0000001`, name "Arena Vendor Inactive" | **`false`** |

FA4 is deliberately **both** inactive **and** ends in account-number suffix `9999` — it doubles as the
Shield block-rule beneficiary (Sec 11) and the inactive-fund-account-reject case, so a payout attempt
against it should be rejected on two independent grounds (FAV inactive-account check first; if that
were bypassed, Shield's suffix rule catches it too). Precomputed hashes for all 4 contacts + 4 fund
accounts, and the matching `hash_lookup` rows, are in `cfa.js`.

## 9. Mozart mock scenarios

Real Mozart ships a stateless, exact-string-match built-in simulator (`mozart -mock`,
`mozart/app/mock/mappings.go`): `transfer_init` keys on `entities.attempt.amount`, `*_status` actions
key on `entities.attempt.gateway_ref_no`. Confirmed directly against real fixture files under
`mozart/app/testdata/fts/rbl/v1/{transfer_status,transfer_init,account_balance}/` (e.g. the real
`9999999151`/`9999999154` PENDING/FAILED examples), not just the finding doc's description.

| Amount (paisa) / gateway_ref_no | Scenario | `bank_status_code` | UTR present |
|---|---|---|---|
| `100100` / `ARENAUTR0000001` | processed | `SUCCESS` | yes |
| `100200` / `ARENAUTR0000002` | failed, insufficient funds | `INSUFFICIENT_FUND` | no |
| `100300` / `ARENAUTR0000003` | pending → processed after 2 polls | `PENDING` ×1, `SUCCESS` on poll 2+ | poll 2+ only |
| `100400` / `ARENAUTR0000004` | `CBS:188` ambiguous, WITH UTR | `CBS:188` | yes |
| `100500` / `ARENAUTR0000005` | duplicate txn | `DUPLICATE_TXN` | no |
| `100600` / `ARENAUTR0000006` | reversed (succeeds, then a later poll reports RETURNED) | `SUCCESS` then `RETURNED` | yes, then a separate `return_utr` |

**Real limitation flagged**: Mozart's built-in `-mock` is stateless exact-match — it cannot "return
PENDING on poll 1 and SUCCESS on poll 2 for the same key." The pending→processed and reversed rows
therefore need per-call-count state, only expressible by a lightweight stateful substitute
(`mozart-sim`), not real Mozart `-mock` out of the box. `mozart_scenarios.json` documents this gap
explicitly and encodes both approaches (a `poll_sequence` array for the stateful substitute; the real
`-mock` fixture-file shape for the other 4 single-shot scenarios, which can be added as new
`mozart/app/testdata/fts/rbl/v1/{transfer_init,transfer_status}/<id>/` directories if wiring real
Mozart `-mock` is preferred over the stateful substitute for those).

## 10. DCS values per merchant

Ground truth: `config-proto/rzp/x/merchant/payouts/{workflows,fund_transfer,api_interface,cfa}.proto`
and `direct_accounts/{configs,payout_mode_config}.proto`. Key shape:
`{namespace:"rzp/x", entity:"merchant", entity_id, domain:"payouts"[/"direct_accounts"], object_name}`.
Full field-by-field values (all fields from all 6 relevant proto messages, not just the headline ones)
are in `dcs_fixtures.json`.

| Field | M1 | M2 | M3 |
|---|---|---|---|
| `Workflows#enable_payout_workflow` | `false` | `false` | **`true`** |
| `FundTransfer#queue_payout_bal_buffer` | `0` | `50000` | `100000` |
| `FundTransfer#block_va_payouts` | `false` | `false` | **`true`** |
| `direct_accounts.Configs#in_flight_reservation_enabled` | `false` | **`true`** | `false` |
| `Cfa#skip_ifsc_lookup` | `false` | `true` | `false` |
| `ApiInterface#payout_service_enabled` | `true` | `true` | `true` |

**"Legacy" flag names**: no literal second string name per flag was found — the real legacy-interop
mechanism (`proto/dcs/proxy/v1/proxy.proto:40-49`, `DCSFeature{write_via_client, read_enabled_via_dcs,
dual_write_enabled}`) gates whether DCS or the legacy API-monolith MySQL `Feature` table is
authoritative for a given flag *name*, per-flag — it is not a second name. Since this arena's API
monolith is itself a stub (Sec 13), the "legacy name" surface for `payout_service_enabled` and
`in_flight_reservation_enabled` is represented in `monolith_fixtures.json`'s `features` list instead,
kept in sync by hand rather than via a live dual-write mechanism. `dcs_fixtures.json` additionally
seeds the `DCSFeature` proxy config for these two flags in case the arena's dcs-stub also serves that
endpoint.

## 11. Splitz variant table (8 experiment ids)

None of these 8 ids exist in the `splitz` repo itself — Splitz experiments are DB-only (created via
its own admin Twirp API, never in git), so the arena needs either real MySQL rows in Splitz's own
schema or a stub server that returns this table directly for `Evaluate` calls (the simpler,
recommended path — `splitz_fixtures.json` is shaped for it).

| Experiment id | What it gates | M1 | M2 | M3 |
|---|---|---|---|---|
| `TUnTsUB8Os2kX0` | shadow-gateway timeslots pilot (Env 3 concern) | `proxy` | `proxy` | `proxy` |
| `Qnc6b4fg1Hi36k` | merchant config via ASV+DCS vs legacy | `on` | `on` | `on` |
| `Sg7tEnMoxCbTzM` | payout workflows named via DCS | `on` | `on` | `on` |
| `TMp5VcIFYOHK2m` | `payout_status_details` from API-DB vs legacy | `off` | `off` | `off` |
| `PEVE4sUaVG6Drw` | Xperience bulk-payouts rounding (Env 5 concern) | `off` | `off` | `off` |
| `ORHtoFyuKLWUSw` | ledger RX↔PG dual-write switch | `off` | `off` | `off` |
| `CreateTransferMetaRollout` | FTS transfer-meta/webhook routing rollout | `on` | `on` | `on` |
| `FireStatusUpdateKafka` | FTS pushes status updates to Kafka | `off` | `off` | `off` |

The last two are config-field-name strings in non-prod envs (`fts/internal/config/splitz.go:22` etc)
rather than opaque Splitz ids — kept literal to match what non-prod config actually reads. Matches the
BOM's own Env2 F2-substitutes row (`CreateTransferMetaRollout=on, FireStatusUpdateKafka=off`).

## 12. Shield scripted blocks

Contract: `POST /v1/rules/evaluate/payout`, HTTP Basic auth, 200ms timeout, **fail-open** on any
error/timeout. Payouts only branches on the literal string `"block"` — `"review"` is silently treated
identically to `"allow"` by current Payouts code (a confirmed real gap, reproduced faithfully rather
than "fixed" in this fixture: `payouts/internal/app/payouts/core.go:6883`).

```json
{
  "rules": [
    {"match": {"field": "input.AccountNumber", "op": "ends_with", "value": "9999"},
     "response": {"action": "block", "triggered_rules": {"velocity_check": "blocked_beneficiary_account_suffix"}, "status_code": 200}}
  ],
  "default": {"action": "allow", "triggered_rules": {}, "status_code": 200}
}
```

This is the rule that makes CFA fund account FA4 (Sec 8, account ending `9999`) doubly useful — see
Sec 8. `purpose=rzp_fees` payouts skip Shield entirely
(`payouts/config/default.toml:330-333`); `shield_fixtures.json` documents this exclusion even though
it's a payouts-side config, not data the Shield stub itself serves.

## 13. Pricing rules

Governor is a rule-*selection* engine (boolean + score, no fee numbers); Charge Collections' `Rule`
message (`proto/charge_collections/pricing/v1/pricing_api.proto:14-49`) is the actual fee schema. Per
the task's explicit requirement — flat fee Rs 2 + 18% GST for IMPS — independent of the SDK's own
built-in mock default (`fee=500,tax=90` for `method=fund_transfer&&mode=IMPS`,
`payouts/pkg/ccSdk/mock.go`):

- `fixed_rate=200` (paisa, i.e. Rs 2.00), `percent_rate=0`, `min_fee=200`, `product=banking`,
  `feature=payout`, `channel=RBL`, `currency=INR`.
- GST is not a native `Rule` field — it's computed downstream and populated separately into
  `payouts.tax`: `tax=36` paisa (18% of 200), giving `fee=200, tax=36` on a qualifying IMPS payout.
- `free_payout` counter: `counters.free_payouts_consumed=0` for every merchant's row at seed time, per
  the task's explicit "free_payout counter 0" requirement — `payouts.sql`'s `counters` INSERTs.

**Flagged deviation**: this does not match the in-repo `MockPayoutCalculator`'s hardcoded default
(`500/90`). If the arena boots with `ccsdk.mock=true` for stability (as the build spike needed to),
real fee/tax on an IMPS payout will be `500/90`, not `200/36` — record which mock mode the arena
actually runs with alongside any verifier assertion. Full rule/plan JSON in `pricing_fixtures.json`.

## 14. Monolith-stub merchant config

Confirmed endpoints/routes/auth-group (`findings/20_api_monolith.md`, `Route.php` grep): `GET
/internal/merchants/{id}`, `POST /payouts_service/fetch_pricing_info`, `POST
/merchant/on_hold_slas_internal`. **Not found** in the findings doc, despite being asked for in the
task: `users_internal`, `actor_info`, `purposes` — the closest real analogue for identity is Passport
JWT + `X-Payout-Actor-Id`/`X-Payout-Actor-Type` headers, not a dedicated endpoint; `purposes` is
instead served locally from payouts' own `payout_purpose` table (Sec 4's neighbor, seeded in
`payouts.sql`), not from the monolith. Field-level request/response DTOs for the 3 confirmed routes
were also not traced in the source pass (route/controller/auth only) — `monolith_fixtures.json` marks
every unconfirmed body `TODO(confirm)` rather than presenting it as verified.

```json
{
  "ARENAM00000001": {"activated": true, "hold_funds": false, "live": true, "features": ["payout_service_enabled", "banking"]},
  "ARENAM00000002": {"activated": true, "hold_funds": false, "live": true, "features": ["payout_service_enabled", "banking"]},
  "ARENAM00000003": {"activated": true, "hold_funds": false, "live": true, "features": ["payout_service_enabled", "banking", "enable_approval_via_oauth"]}
}
```

## 15. Stork subscriptions

Schema: `Webhook{service, owner_id, owner_type, url, secret, subscriptions[]}`, one
`Subscription{eventmeta:{name}}` per `(webhook, event)` pair. All 3 merchants subscribe the same sink
to the full `payout.*` family (`payout.initiated/processed/reversed/failed/updated/queued/rejected/pending`,
`payouts/internal/app/common/appConstants/webhooks.go:4-11`).

| Merchant | `owner_id` | `url` |
|---|---|---|
| M1 | `ARENAM00000001` | `http://merchant-webhook-sink:8090/webhook-receiver` |
| M2 | `ARENAM00000002` | `http://merchant-webhook-sink:8090/webhook-receiver` |
| M3 | `ARENAM00000003` | `http://merchant-webhook-sink:8090/webhook-receiver` |

Endpoint: `POST /twirp/rzp.stork.webhook.v1.WebhookAPI/ProcessEvent`. Signature:
`X-Razorpay-Signature = hex(HMAC-SHA256(secret, raw payload JSON))`. Full event envelope and payload
templates in `stork_subscriptions.json`.

## 16. Passport claim fixtures

Payouts uses passport v3 (static pubkey-per-`kid`, not the JWKS-host v4 model): config keys
`passport.api.identifier="apiv1"`, `passport.edge.identifier="edgev1"`. Header: `X-Passport-JWT-V1`.

- **Private** (M1, M2 API-key auth): `identified=true, authenticated=true, consumer={type:"merchant",
  id:"ARENAM00000001"}`, no `oauth`/`impersonation` block.
- **Proxy + impersonation** (M3 finance users): `identified=true, authenticated=true,
  consumer={type:"user", id:"ARENAU00000001"}, roles:["finance_l1"],
  impersonation:{"Type":"user_merchant","Consumer":{"id":"ARENAM00000003","type":"merchant"}}`.

**Real upstream bug reproduced faithfully**: `ImpersonationClaims` has no JSON tags in the real SDK —
the wire keys are literally `Type`/`Consumer` (capitalized), not `type`/`consumer`.
`passport_fixtures.json` uses the capitalized form so a minting script matches real wire behavior
rather than the "obviously correct" lowercase form. Signing algorithm: `TODO(confirm)` — claim shape
and header name are confirmed, the exact signing algorithm was not independently re-verified in this
pass.

## 17. FastCron-driver schedule (assumed, flagged UNVERIFIED)

Payouts' own cron cadence was not found in readable config. `cron_schedule.yaml` carries forward the
BOM's own stated assumption (`INITIAL_PAYOUTS_ENVIRONMENT_BOM.md` Sec 1) verbatim, not independently
re-verified: queued-dequeue/scheduled-release/on-hold-sweep/reservation-reconcile every 5 min,
dual-write-failure retry every 15 min. FTS's own crons run in-process (`INSTANCE_TYPE=canary`), not
via external HTTP cron — their job list/cadence was not enumerated in the source findings used here.

## 18. Reset / invariant section

**Per-scenario reset**: restore each datastore from a snapshot taken immediately after this seed set
is applied (payouts/fts/x-balances MySQL, ledger Postgres, CFA Mongo), flush Redis, purge any
queue/SQS-substitute. If a Postgres snapshot restore isn't available, re-running
`ledger_accounts_via_api.sh` is **not** a safe substitute for a snapshot: `CreateOnEvent` is
onboarding-idempotent (it won't duplicate an existing account) but does not reset an already-created
account's balance back to its opening value — a genuine reset needs either a snapshot restore or a
fresh `TRUNCATE`+reseed of the ledger tables.

**Expected opening balances** (assert immediately after seeding, before any scenario executes):

| Merchant | Balance source | Expected value (paisa) |
|---|---|---|
| M1 | ledger `accounts.balance` WHERE `id='ARENAM1ACC0001'` | `10000000` |
| M2 | x-balances `balance.balance` WHERE `id='ARENABAL000002'` | `10000000` |
| M3 | ledger `accounts.balance` WHERE `id='ARENAM3ACC0001'` | `10000000` |

**Derivation rule for verifiers during a scenario**: after a processed IMPS payout of amount `A` with
fee `F=200` and tax `T=36` (Sec 13) for a ledger-backed merchant (M1/M3), the expected post-payout
`MerchantBalance` is `opening - A - F - T`, with `VendorPayable` showing a credit of `A`,
`CommissionIncome` a credit of `F-T`, `OutputGST` a credit of `T` (per the `XPayoutInitiatedV2`/
`XPayoutProcessedV2` rules, Sec 6) — the net movement across a payout's `journal_id` should sum to
zero, the cheapest invariant check available without recomputing the fee rule. For M2 (x-balances),
the equivalent check is simpler: `balance.balance` decreases by exactly `A + F + T`, with no
sub-ledger to cross-check (`sub_balance` doesn't exist as a real table, Sec 5).

**Free-payout counter invariant** (Sec 13): `counters.free_payouts_consumed=0` at seed time for every
merchant's `balance_id`; after `N` free payouts in a scenario it should read `N`.

**Idempotency-key invariant**: `payouts.sql` seeds no `idempotency_keys`/`bulk_idempotency_keys` rows
(created by the flow under test, not fixture setup) — a reset must ensure no stale row exists for an
`(idempotency_key, merchant_id)` pair a test is about to reuse.

## 19. Summary of open TODO(confirm) items

1. FTS `source_accounts.mozart_identifier` exact string format (`'rbl_v1'` used, not independently
   confirmed against `fts/internal/providers/mozart`).
2. Ledger amount unit (paisa vs rupees) — `NUMERIC(26,6)` is ambiguous; this spec assumes paisa.
3. `ledger_config`'s remaining 32 of 34 real transactor-event configs are not hand-transcribed; only
   `XPayoutInitiatedV2`/`XPayoutProcessedV2` are seeded verbatim.
4. `workflow_config.config_type`/`workflow_state_map.type`/`state_status` free-text taxonomy for M3 is
   best-effort (schema confirmed, real value taxonomy not confirmed).
5. Monolith-stub field-level DTOs for `fetch_pricing_info`/`on_hold_slas_internal` (route/auth
   confirmed, request/response body not traced); `users_internal`/`actor_info`/`purposes` as literal
   monolith endpoints were not found at all in the source findings doc.
6. Passport signing algorithm not independently re-verified (claim shape and header name are).
7. FastCron cadence table is an unverified assumption carried forward from the BOM, not read from a
   real crontab/FastCron config.
8. `payouts.fund_account_id`'s exact write-path linkage to a CFA fund-account id was not traced.

## Addendum 2026-09-04 (post golden-run): seed corrections proven in the arena

| Store | Change | Why (evidence in raw-findings/31) |
|---|---|---|
| API-DB stub (`api_local`) | `seeds/schema-patches/apidb.sql`: extra columns on `balance` (type, created_at, …), tables `features`, `workflow_entity_map`, `reversals`, `payouts_details`, `payouts_status_details`, `fund_transfer_attempt`; 3 `balance` rows ARENABAL000001..3 | payouts reads the monolith DB over `[db.api]` (free-payout slab needs `balance.created_at`; empty result panics) |
| payouts MySQL | `seeds/schema-patches/payouts.sql`: `payout_details.beneficiary_bank_code` | schema drift (no migration creates it) |
| ledger | `ledger.sql` config inserts retired; configs loaded via `LedgerConfigAPI/CreateInBulk` (`shared_account_x`, `direct_account_x`); `account_details.entities.banking_account_id` = signed `bacc_…` ids | hand-transcribed config shape produced zero entries; PS sends signed identifiers |
| fts MySQL | `source_accounts.mozart_identifier = 'v1'`, `credentials`/`configuration` JSON objects; `source_account_mappings.credentials = '{}'`; `direct_account_routing_rules` for M2; `channel_information_status` UP rows | version key, JSON unmarshal at gateway time, direct routing, channel-health filter |
| monolith fixtures | `merchants.json`: `account_type`, `channel`, `balance_id`; `pricing`: plan ids 14 chars (`ARENAPLAN00001`); fund-account ids in verifier env are the monolith ids `ARENAFAX…` | `pricing_rule_id` is CHAR(14); payouts fetches fund accounts from the monolith by its id |
| ledger (top-ups) | `ARENAPRACC0007` adjustment payable sub-account under `ARENAPRACC0008` (entities payable/adjustment) | identifier-less discovery searches sub-accounts only; the verifier tops merchants up with `positive_adjustment_processed` journals |
| fts | `channel_information_status` matrix incl. (CURRENT, POOL)/(CURRENT, DIRECT); monolith relay sends `preferred_source_account_id` for direct merchants | health query keys on bank-account type + source-account type; DIRECT marker |
| stork | List returns Subscription objects (`{"eventmeta":{"name":…}}`) | proto shape |
