# Implementation reconciliation with CODEX_HANDOFF

This report covers the assigned P0.5 API schema and P0.8 Ledger process changes, plus the generator-side P0.1 rollout default. It reconciles `TWIN_SPEC/CODEX_HANDOFF.md` with current package files and approved local repository copies. Other handoff items remain owned by their implementation workstreams; this report does not claim their completion.

## Changes and classification

| Item | Change | Classification | Verification ceiling |
|---|---|---|---|
| P0.5 | Canonical API schema has the 19 tables from `TWIN_SPEC/schema/apidb-subset.mysql.sql`, with per-object source references and confidence labels | Substitute fidelity | Offline shape checks pass; clean replay 6 executed schema and seed loading successfully; production parity remains bounded by per-object labels |
| P0.5 | Use plural `fund_transfer_attempts`, `banking_accounts`, `payouts_status_details`; remove previous singular inventions | Substitute fidelity | Exact table-name constraints covered by tests |
| P0.5 | `payouts_details` has primary key `payout_id`, no separate `id`, and no `beneficiary_bank_code` | Substitute fidelity | Matches the inspected migration and monolith dual-write exclusion |
| P0.5 | `reversals` uses `fee`, `entity_id`, `entity_type`; no service-only `fees` or `payout_id` columns | Substitute fidelity | Matches the inspected monolith migration |
| P0.5 | `features` uniquely indexes `(name, entity_id)`; `balance` follows source columns and foreign key | Substitute fidelity | Keys, columns and FK order covered by tests |
| P0.5 | Generator creates synthetic merchant parents before balance rows; removes invented `balance.primary` field | Synthetic fixture | Generated INSERT columns and FK parent ordering covered by tests |
| P0.5 | Remove obsolete `seeds/mysql/apidb_seed.json` and `seeds/mysql/xbalances_seed.sql` | Synthetic fixture | Current generated fixtures are authoritative |
| P0.5 | Schema-patch entrypoint sources the mounted canonical DDL and rejects incompatible old-volume shapes | Runtime wiring | MySQL 8 clean-boot execution passed in replay 6; old-volume rejection remains an offline contract check |
| P0.8 | Explicit queue selection on five Ledger workers: account create, balance update, journal create, entry details create, and PG entry details create | Runtime wiring | All five workers reported healthy in replay 6; real journal, async recovery and queued-payout tests passed; see acceptance evidence below |
| P0.8 | `LEDGER_SCHEDULER_COMMAND=split-account-balance-update` | Runtime wiring | Exact source command/override semantics inspected; replay 6 records scheduler startup; recurring cadence remains unclaimed |
| P0.1 support | Generated Splitz fallback is `off`; explicit experiment/merchant assignments are marked ASSUMED | Synthetic fixture | Existing variants expand to every generated merchant |
| Explorer | Ledger process group now lists all five workers and the scheduler with the one-shot boundary | Explanatory asset | Metadata matches current Compose definitions |

No service behavior patch or repository-clone edit was introduced in this assignment. Generated live-bound seed outputs and running databases were not modified by this workstream; the root workstream coordinates regeneration and the clean boot.

## Source-backed reconciliation decisions

**Foreign-key creation order.** The supplied API schema lists `idempotency_keys` before its `merchants` parent. The canonical boot DDL moves `merchants` first. The generator likewise inserts each merchant before its balance row. This preserves the foreign key rather than disabling checks.

**Features uniqueness and width.** `api/database/migrations/2016_10_24_081731_create_features_table.php` declares a unique key over `name, entity_id`, non-null identity fields and name width 25. The current reservation feature string has 29 characters. The TWIN_SPEC subset already labels its width 255 representative. The implementation retains that width as an explicit ASSUMED compatibility extension and adds the exact source unique key. Required evidence: schema-only `SHOW CREATE TABLE features` plus schema revision; no rows or credentials are needed. This must not be described as exact production DDL.

**Balance representation.** `api/database/migrations/2014_07_12_083930_create_balance.php` and `app/Models/Merchant/Balance/Entity.php` establish `type`, `credits`, `fee_credits`, `reward_fee_credits`, `refund_credits` and the merchant foreign key. `primary` is a value of `type`, not a separate column. Generated banking balances use `type='banking'`; the fabricated boolean column and value were removed. Amounts remain synthetic integer minor units.

**Beneficiary bank code.** The monolith intentionally removes `beneficiary_bank_code` before writing `payouts_details`; source: `api/app/Models/Payout/DualWrite/PayoutDetails.php`. The CI-only `ps_payout_details` migration's CHAR(4) is not evidence of the production Payouts service's physical column type. That service-side type remains UNKNOWN. This assignment does not modify the service schema patch or promote the CI declaration into production-schema certainty.

**Partial schema objects remain partial.** The API `payouts`, banking-account, contact, beneficiary, merchant, key and source subsets carry the supplied representative/unknown labels. A table's presence is not proof that every monolith query or full dual-write payload is supported. In particular, the current partial API `payouts` DDL is not advertised as a complete monolith payout model.

**Ledger selection is per process.** `ledger/pkg/config/config.go` derives automatic `LEDGER_*` environment names and applies those overrides to literal TOML values. Therefore Compose's `LEDGER_WORKER_QUEUENAME` overrides the old shared template value `account_create` without copying or mutating the service source. `ledger/internal/boot/boot.go` uses `Config.Scheduler.Command`; `internal/common/constant.go` defines `split-account-balance-update`. The production CronJob template and worker templates use these same environment keys. Existing local queue configuration and LocalStack initialization already declare all five queue names.

**Scheduler cadence is not invented.** The Compose scheduler executes once. Its command now matches the production CronJob command, but a recurring production schedule is not reproduced or claimed. Local repeats require a documented trigger; the cadence remains ASSUMED.

**Direct accounting acceptance needs source interpretation.** Handoff language asking for a Direct `payout_processed` Journal must not be read as a requirement to add Shared-style journal behavior to Direct payouts. The inspected Payouts path skips that accounting until the statement/BAS path. Current-account Ledger discovery fixtures can be tested independently; real Direct statement accounting remains a separate acceptance family. No product behavior was changed to force a journal where source does not call one.

## Evidence revisions

| Repository | Commit | Principal references |
|---|---|---|
| api | `2d665f918b60e917ec92be648fb5816d247f72b1` | `app/Constants/Table.php`; migrations cited per object in `seeds/mysql/apidb-ddl/00_init.sql`; `app/Models/Payout/DualWrite/PayoutDetails.php`; `app/Models/Merchant/Balance/Entity.php` |
| ledger | `471ff4d5321b6b99a7965882d18f572a1adf5194` | `pkg/config/config.go`; `internal/boot/boot.go`; `internal/common/constant.go`; `config/prod-live.toml` |
| kube-manifests | `9226a892fd767b0e31dd4282b0b4919298f53fda` | `templates/ledger/templates/split-account-balance-update-cronjob-live.yaml`; `balance-update-live-worker.yaml`; `templates/ledger/values.yaml` |
| payouts | `4bf3dbf9239feadea6d65ca90c893a988e116173` | `pkg/ledger/ledger_journal_create.go`; merchant feature and Direct accounting consumers |

## Verification

`python3 -m unittest discover -s ENV2_COMPOSE/seeds/generator -p 'test_*.py' -v` passes **18 tests** in the [final offline run](offline-final/ENV2_COMPOSE-seeds-generator.log), within **136 total offline checks** ([summary](offline-final/summary.json)). New checks cover the exact table set, monolith versus service-only field differences, unique keys, merchant-first FK order, generated API columns, and Ledger queue/process selection. Existing tests cover deterministic output, synthetic identities, signed Ledger references, CFA consistency and route-profile hashes. These are offline contract checks, not the local acceptance verifier count.

The original assignment ended at offline validation. Root-coordinated clean boots and real accounting tests have since supplied the following evidence:

| Check | Completed evidence | Remaining boundary |
|---|---|---|
| API schema and fixture load | [Replay 6 startup](runs/replay6/up.log) completed canonical DDL, schema guards, migrations and generated seeds; its startup exit is 0. The 19-table identities, exact keys and merchant-first order are covered by the generator tests. | This is local execution evidence, not a production schema export or proof of every omitted column. |
| Five Ledger queue processes | The same startup log reports `ledger-worker`, `ledger-worker-balance-update`, `ledger-worker-journal-create`, `ledger-worker-entry-details-create` and `ledger-worker-entry-details-create-pg` healthy. Their distinct queue settings remain source/Compose-backed. | Process health does not independently prove every queue message type or every production retry/DLQ behavior. |
| Real money accounting | [Replay 6 JUnit](runs/replay6/full/junit.xml) has 26 passes and no skips, including V05 initiated journal/exact debit, V06 processed journal uniqueness and balanced entries, V07 failure/reversal accounting and V11 synchronous merchant debit. | These assertions cover their exact entries/states, not every possible asynchronous non-merchant account update. |
| Queue-driven recovery | Replay 6 passed both V17 Ledger delay/outage recovery checks and queued/scheduled execution checks. [Final supplemental routes](runs/final-supplemental/route/route-results.json) separately passed actual queued/scheduled terminal completion, Direct success/failure, Shared failure and explicit returned reconciliation. | Later bank return is API-authenticated admin verify/safe_update with an independent second bank check, not natural post-terminal polling. |
| Scheduler | Replay 6 records the Ledger scheduler starting with the configured split-account command. | Recurring cadence and an independently captured one-shot completion remain outside this report's evidence. |
| Explorer | [Browser verification](EXPLORER_VERIFICATION.md) rendered all 128 illustrative hops, then passed the authenticated live action and saved static trace. | Illustrations remain labeled; their render success is not a runtime assertion of every route. |

Clean replay 3 and replay 6 each passed all 26 live assertions. Replay 6's [simultaneous egress audit](runs/replay6/full/egress.json) passed with command exit 0 and zero outside packets. The [six final bank cases](runs/final-supplemental/bank/bank-effects.json) also pass. These results replace the original blanket “next clean boot” pending status, while preserving the narrower limits above.

The final repeatability gate is **not yet complete**. [Replay 7](runs/replay7/full/junit.xml) recorded 25 passes and a V06 processed-journal wait timeout. Root investigation found actual mutex contention followed by the configured retry creating the journal about 31 seconds later; the verifier's wait is being corrected to source timing, with no core patch. Another clean pair and the final effective-config/environment/log/store audit remain pending. This report does not turn the retained timeout into a passing capture.

## Deferred P2 scope

Real Workflow/Cadence, full monolith ingress and dashboard/OTP middleware, Batch, full XAS/reconciliation/BAS repair, role-separated monolith modules and UPS expansion are **not implemented in this assignment**. Their contracts and unknowns remain visible; fixture presence must not promote them to verified runtime behavior. Engine versions, queue visibility/DLQ behavior, production rollout assignments and recurring scheduler cadence likewise retain their documented confidence limits.
