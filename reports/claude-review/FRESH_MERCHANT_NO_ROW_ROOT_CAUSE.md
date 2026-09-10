# Fresh-merchant `no_row_affected` — root cause, evidence, and fix contract

**Status: RESOLVED.** A freshly provisioned merchant now completes the full
Shared-payout lifecycle to terminal `processed`. Root cause was a one-character
column overflow introduced by the M3.1 provisioner, masked by a repository guard.

---

## 1. Exact failing statement and bound arguments

The pre-Ledger state transition (`payouts/internal/app/payouts/processor/
payoutViaLedgerService.go:224`, `payoutStateProcessingBeforeLedgerServiceCall`)
issues, via spine `Repo.Update` → gorm v1 full-model update:

```sql
UPDATE `payouts` SET
  `amount`=5000, `balance_id`='ARENABAL962226', `channel`='yesbank',
  `created_at`=1788700083, `currency`='INR', `fees`=200,
  `fund_account_id`='ARENAFAX962226', `id`='TYlJFrWyMEdmpi', `idempotency_key`='',
  `merchant_id`='ARENAM90962226', `method`='fund_transfer', `mode`='IMPS',
  `narration`='Arena Fresh ARENAM90962226 Fun', `notes`=..., `origin`=1,
  `pricing_rule_id`='ARENAPLAN962226',          -- << 15 chars
  `purpose`='payout', `purpose_type`='settlement',
  `status`='ledger_response_awaited', `tax`=36, `updated_at`=1788700083
WHERE `payouts`.`id` = 'TYlJFrWyMEdmpi';
```

- Table: `payouts`. WHERE: `id = 'TYlJFrWyMEdmpi'` (primary key only — no other
  predicate, no `deleted_at`).
- Intended state change: `status` `create_request_submitted` → `ledger_response_awaited`.
- payout_id `TYlJFrWyMEdmpi`, merchant_id `ARENAM90962226`, balance_id
  `ARENABAL962226`, pricing_rule_id `ARENAPLAN962226`.

## 2. Does the target row exist before the update?

Yes. The `INSERT INTO payouts` (status `create_request_submitted`) **committed** at
`13:08:03.072923` on connection (thread) 38; the failing UPDATE runs later on the
**same connection** (thread 38) inside `START TRANSACTION` at `03.094194`. The row is
committed and visible. `SELECT ... FROM payouts WHERE id='TYlJFrWyMEdmpi'` returns it.

## 3. Working (M1) vs failing (fresh) payout records

The two `payouts` rows are byte-identical except identifiers and one cosmetic field:

| column | M1 (works) | fresh (failed) |
|---|---|---|
| `pricing_rule_id` | `ARENAPLAN00001` (**14** chars) | `ARENAPLAN962226` (**15** chars) |
| `narration` | `Arena Merchant One Fund Transf` | `Arena Fresh ARENAM90962226 Fun` |
| id / merchant_id / balance_id / fund_account_id | M1's | fresh's |

`information_schema` confirms `payouts.pricing_rule_id` is **`char(14)`**. M1's value
fits; the fresh value is one character too long.

## 4. `no_row_affected` — no match, or matched-but-unchanged?

**Neither.** It is a **masked write error.** The driver DSN
(`spine/db/db.go:28`) sets no `clientFoundRows`, so `RowsAffected` counts *changed*
rows (verified directly on this DB: `UPDATE … SET status=status` → `ROW_COUNT()=0`;
a real change → `1`). But here the UPDATE does not merely change nothing — it
**errors**:

```
ERROR 1406 (22001): Data too long for column 'pricing_rule_id' at row 1
```

Replaying the captured statement verbatim against a stuck fresh row reproduces the
1406 error; a `status`-only update on the same row returns `ROW_COUNT()=1`. On a
failed statement the driver reports `RowsAffected = 0`, and spine checks that first:

```go
// pkg/spine/repository.go:85
q = q.Update(receiver)
if q.RowsAffected == 0 {
    return NoRowAffected.New(errNoRowAffected).Wrap(...)   // fires before GetDBError(q)
}
return GetDBError(q)                                       // the real 1406 never surfaces
```

So the true error (`Data too long`) is swallowed and re-reported as
`no_row_affected: no rows have been updated`. The transaction then rolls back
(observed `ROLLBACK` at `03.094898`), so `updated_at == created_at` (no write ever
persisted) and the payout stays `create_request_submitted`.

**Verification through a direct matched-row query** (same DB, live):
`updated_at−created_at` = **0** for the fresh payout vs **1** for M1;
`ROW_COUNT()` = 0 for the verbatim app UPDATE (1406) vs 1 for a fitting update.

## 5. First material difference and where it originates

The earliest control-flow-changing difference is the **value of `pricing_rule_id`**
returned by the pricing response and written on the pre-Ledger update. It originates
in **runtime provisioning**, not identity translation, payout creation, or the
transition logic itself: the M3.1 provisioner built the plan id as
`"ARENAPLAN" + mid[-6:]` (`provisioner.py:341`), yielding 15 characters, and
monolith-stub's `_fetch_pricing_info` returns that plan id as `pricing_rule_id`
(no rule-level id is seeded). Everything upstream — provisioning steps, merchant
identity, pricing fee/tax resolution (200/36), payout INSERT — is correct; the
failure is entirely the over-long id hitting a `char(14)` column at the first
persisted transition.

## 6. Minimal fix

**Runtime-provisioning fix only. The repository guard is left untouched** (it
faithfully reproduces real payouts behaviour; weakening it would hide real errors).

`RED_LOOP/red_loop/provisioner.py`:
```python
# pricing_rule_id is char(14); keep plan_id <= 14 chars (M1 uses ARENAPLAN00001).
plan_id = ("ARENAPLAN" + hashlib.sha256(mid.encode()).hexdigest()[:5].upper())[:14]
```
This yields a unique, ≤14-char plan id (e.g. `ARENAPLANC456A`) that fits the column.

**Result (proven live):** fresh merchant `ARENAM92817724`, plan `ARENAPLANC456A`,
payout `TYldb8PNEHaXZ8` → API `processing` → DB terminal **`processed`**, fees 200,
tax 36, UTR assigned. Full e2e suite `reports/implementation/m31-fresh-merchant.json`
now **8/8** (success, bank-failure reversal, insufficient-balance, idempotency).

## 7. Post-fix assertions that the fresh merchant reused no static-merchant state

For the successful fresh payout `TYldb8PNEHaXZ8` (merchant `ARENAM92817724`):

- **Ownership**: `payouts` row `merchant_id='ARENAM92817724'`, `balance_id=
  'ARENABAL817724'`, `pricing_rule_id='ARENAPLANC456A'` — all fresh, none of M1's.
- **Ledger**: the `payout_processed` journal's `ledger_entries` reference **only**
  `ARENA24AC0001‑0004` (this merchant's four accounts, `merchant_id=
  'ARENAM92817724'`) plus the shared nodal pool `ARENAPOOLACC02` — never M1's
  `ARENAM1ACC*`. Debit on `ARENA24AC0001` (merchant_va).
- **Balance**: x-balances row `ARENABAL817724` (own); `0` fresh payouts reference
  M1's `ARENABAL000001`.
- **Pricing**: resolved from `pricing.json.plans['ARENAM92817724']` (its own plan),
  fee/tax 200/36 from the fresh plan, not M1's.
- **FTS**: routed via this merchant's own `source_account_mappings`/
  `account_type_mappings` rows (seeded per-merchant by the provisioner), Shared/NODAL
  source account 900001 (the declared shared pool, as for every Shared merchant).

## 8. Future Direct (current-account) path — records that differ from the Shared provisioner

The Direct path is selected when `bankingAccount.GetAccountType() == "direct"`
(`appConstants.Direct = "direct"`, `constants.go:188`). A Direct provisioner must
differ from the Shared one in:

- **Banking / balance account type**: `payouts.banking_accounts.account_type` and
  x-balances/apidb balance `account_type` = `direct`/`current` (Shared uses
  `shared`/`pool`).
- **FTS source account**: a **CURRENT** source account keyed to the merchant's own
  current account (e.g. M2's `900002 … 'CURRENT' … 2323230000000002`), not the shared
  `NODAL` pool `900001`; plus the matching `account_type_mappings` (`CURRENT`) and
  `source_account_mappings` per mode.
- **FTS route / channel**: the Direct partner-bank route (ICICI Direct in source;
  RBL-direct FTS workers `fts-worker-rbl-direct-imps-*` exist in the arena), distinct
  from the Shared RBL pool route.
- **Ledger accounting config**: the `direct_account_x` LedgerConfigAPI identifier
  (Shared uses `shared_account_x`); Direct account_details roles differ (a
  current-account structure rather than merchant_va/vendor/commission/gst payable set).
- **Ledger journals on failure — expected ABSENCE**: per verifier v08
  (`test_payout_failed_direct_account_no_reversal_no_ledger`), a **failed** Direct
  payout produces **no reversal entity and no Ledger journal** (money moves from the
  merchant's own current account), unlike Shared, which reverses with a balanced
  reversal/failed journal. A Direct provisioner and its assertions must expect no
  reversal row and no reversal journal on failure.
- **Balance-refresh behaviour**: Direct payouts additionally enqueue
  `QueuePushForXBalancesPayoutEventProcessingJob` (`base.go:403`) → the
  x-balances-balance-refresh worker reconciles the current-account balance; Shared
  does not take this branch.
- **Reservation configuration**: Direct current-account reservations are tracked
  against the merchant's own account rather than the shared pool reservation counters;
  the reservation-readiness seed must include the Direct account.

## 9. Evidence index

- Failing SQL + bound values, transaction/connection ordering, 1406 replay, ROW_COUNT
  semantics: live `mysql.general_log` (Execute rows) and `information_schema` on
  `env2_compose-mysql-payouts-1`, captured 2026-09-06.
- Fix + green lifecycle: `RED_LOOP/red_loop/provisioner.py` (plan_id),
  `reports/implementation/m31-fresh-merchant.json` (8/8).
- No-reuse: postgres-ledger `ledger_entries`/`journal`, mysql-payouts `payouts`,
  mysql-xbalances `balance` queries above.
