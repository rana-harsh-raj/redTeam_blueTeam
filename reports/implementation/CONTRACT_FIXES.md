# Contract fixes — implementation evidence

These changes affect the isolated synthetic twin. No private application repository was modified in this workstream. Baseline completion was reported by the implementation owner before edits began. Runtime acceptance remains a separate check; adapter tests do not establish end-to-end correctness.

## Source revisions

| Repository | Commit |
|---|---|
| payouts | `4bf3dbf9239feadea6d65ca90c893a988e116173` |
| api | `2d665f918b60e917ec92be648fb5816d247f72b1` |
| ledger | `471ff4d5321b6b99a7965882d18f572a1adf5194` |
| fts | `2a09e763116db47a2f28553c677ad73ef8133ebf` |
| config-proto | `a6b201039f22cc543efb740eba9dd72fd29474b0` |

## Implemented corrections

| Change / classification | Confirmed source contract | Local implementation / limits |
|---|---|---|
| Deterministic merchant resolution — substitute fidelity | `api/app/Models/Payout/Repository.php:5313` reads the Payouts database by payout primary key; `Core.php:8461,8474` derives merchant identity from that row. | `substitutes/monolith-stub/server.py:_ps_get_payout` executes one parameterized SELECT, logs the resulting ID relationship, and no longer mints/probes merchant passports. Database failure is an error, never an alternate tenant search. |
| Database access — safety patch / runtime wiring | Monolith's cross-database read is an actual production coupling, above. | New monolith-specific Dockerfile installs pinned PyMySQL. `scripts/provision-monolith-db.sh` grants SELECT only on `payouts.payouts`; separate API mirror identity gets SELECT and UPDATE only on balance/updated_at columns of `api_local.balance`. No database root secret is mounted in monolith. Setup uses MySQL 8 native-password authentication for this local-only scoped identity. |
| FTA guard — substitute fidelity | `api/app/Models/Payout/Processor/Base.php:4687` requires created/initiated and either transaction_id or Direct account; missing Ledger entry has an explicit error. | Same prerequisite in `_create_fta`; fixture-specific source and fund-account IDs prevent cross-test source account reuse. FTA entity persistence, mutex and async fallback remain representative. |
| Processed / reversed Ledger discovery — substitute fidelity | `api/app/Models/Payout/Core.php:949` forwards FTA **source_account_id**, bank_account_type; `payouts/pkg/ledger/ledger_journal_create.go:216` lowercases type. `ledger/internal/journal/ledger_config/seed_data/shared_account_x.go:65,98` discovers FTS payable/receivable by fts_fund_account_id and fund_account_type. | Relay uses source_account_id instead of beneficiary fund_account_id and does not force nodal. Shared source 900001 is nodal; Direct source 900002 is current in corrected `seeds/s4/ledger.sql`. Current parents use new IDs 0009/0010, preserving existing adjustment parents 0007/0008. Generated fixtures expand these IDs per scenario. |
| Direct feature visibility — synthetic fixture | `payouts/internal/app/merchant/feature_repo.go:27` selects features by entity_id and entity_type=merchant; `feature.go:63` presence means enabled. DCS key in `pkg/dcs/features/features.go:23,168` is `rzp/x/merchant/payouts/direct_accounts/Configs`; `config-proto/.../direct_accounts/configs.proto:15` is bool field 1. | Add correctly shaped Direct feature row to API DB and legacy merchant response. Existing DCS key/encoding was already correct; it was not moved to FundTransfer. Disabled feature is absent from API DB, not a false-valued row. |
| Queued balance mirror — substitute fidelity | `payouts/internal/app/balance/repo.go:26` reads **API DB balance.updated_at** within six hours. `helperQueuedPayouts.go:300` fetches Shared balances through API and Direct balances through account-statement/x-balances logic. `api/app/Services/PayoutService/QueuedInitiate.php:11` posts balance_ids to `/payouts/balance_update_event`; `Payout/Core.php:4083` invokes it. | GET internal_balances_queued reads API mirror; it no longer fabricates a large Direct balance. POST `/_arena/balance-sync` reads Ledger truth, commits balance + current updated_at to API mirror, then emits the real balance event. It does not mint money. Direct requests return 422 and must use x-balances. The control represents the consumer's observable side effects, not the actual production journal consumer or its timing. |
| Cron paths / bodies — runtime wiring | `payouts/internal/routing/router/cron_routes.go:56,77,94`: process_beneficiary_bank_on_hold_payouts, process_inflight_reservation_reconciliation, payouts_dual_write_failure_processing. FastCron Basic authentication. `dtos/payoutCronRequests.go:5` and queued DTO use optional balance_ids/balance_ids_not; v2 queued route uses type. | Correct all three route names. Partner-bank job sends type=partner_bank_downtime. Optional CRON_BALANCE_IDS now applies to low-balance and scheduled jobs. Reconcile handler binds no request (controller:848); empty JSON is harmless. Scheduler cadences remain representative. |
| Relay failure reporting — substitute fidelity | `api/app/Services/PayoutService/Status.php` propagates request errors; `fts/internal/transfer/service.go:1252` schedules retries on every non-2xx result. | Details/status transport failure now returns 502 instead of always acknowledging 200; subsequent FTS retries remain visible. |
| Per-payout relay controls — synthetic fixture | Fault controls have no claim of being production API routes. | Authenticated arena endpoint supports deliver/drop/delay/duplicate/reorder and explicit ordered release. Defaults preserve normal delivery. Queue/control state is process-local and resets on restart. Replay results, delayed errors and status requests are captured in arena log. |
| Splitz variables — substitute fidelity | `fts/internal/providers/splitz/splitz.go:254` requires variable enabled=true; Payouts uses result=on and, for one route, a particular variant name. | Splitz response includes both typed string variables. An on name alone previously never enabled FTS gates. Route profiles set explicit synthetic experiments; no runtime production experiment state is claimed. |

## Controls and commands

All control requests use monolith's existing synthetic Basic authentication on the isolated network.

- `POST /_arena/balance-sync`: `{"balance_ids":["ARENABAL000001"],"deliver_event":true}`. First perform a real synthetic Ledger top-up. `deliver_event:false` leaves the mirror update available to the real cron, for recovery tests.
- `POST /_arena/relay`: `{"payout_id":"<bare-or-pout-prefixed-id>","mode":"deliver|drop|delay|duplicate|reorder","delay_ms":1000,"copies":2}`. Delay is bounded to 300 seconds; duplicates to 2–5 copies. Reorder holds messages.
- `POST /_arena/relay/release`: `{"payout_id":"<id>","order":[1,0]}`. Order must contain every currently held zero-based message index exactly once. Omitting order releases arrival order. Failed release returns an error.
- `bash ENV2_COMPOSE/scripts/provision-monolith-db.sh` after migrations/API schema setup, before substitutes.
- `python3 ENV2_COMPOSE/substitutes/monolith-stub/test_contract.py` runs the isolated adapter behavior tests.

## Verification

Nine isolated adapter tests passed (log: `contract-adapter-tests.txt`): deterministic tenant lookup, source-account identity translation, failed relay status, mirror read, commit-before-event ordering, Direct balance rejection, exact reorder permutation and per-payout drop scope, and merchant-specific Direct FTS source selection. Source checks independently matched all six cron paths, DCS bool field 1, API source-account mapping, and 44 distinct Ledger account/account-detail primary keys. Results are recorded in `contract-source-check.json`. All four route profiles also passed rendered-TOML parsing/idempotence checks. Python compilation and shell syntax checks passed.

These checks use fake transport/database boundaries and do not claim the real five-service verifier passed. Docker image build, MySQL grants, full runtime relay and all bank scenarios must be verified by local acceptance.

## Real-service practicality assessment

All requested source repositories are locally readable. Source availability is not build/runtime acceptance.

| Component / observed commit | Evidence and current assessment |
|---|---|
| API, above | `composer.json:9–12` requires PHP 8.2, Laravel 11 and private Spine; many private packages and PHP extensions follow. Its payout DB/FTA/pricing/transaction coupling is material. Existing substitute stays **representative** until real build isolation and those behaviors are demonstrated. No build was attempted in this lane. |
| Mozart `bf4688300d4896500fb9e9f422ee28d80d81b6e6` | `go.mod:28,54,55` requires orchestrator, integrations-go, integrations-utils; these sibling clones are absent. Prior build evidence reports module access failure. `app/mock/mappings.go` exposes selected RBL/ICICI/Yesbank mock scenarios, not every gateway fixture directory. Prefer built-in mock once approved dependencies exist; current simulator is representative. No new network/build probe was attempted. |
| Stork `800b719030aa5051c76630fa7c2020aa2a1f8348` | `go.mod:3,29,30,55` shows Go 1.25, Redis/MySQL and Kafka SDK; repository includes build and slit compose packages. Candidate for a real local build; there is no current evidence it is impossible. Delivery/retry fidelity remains representative until real Stork acceptance. |
| Workflows `080d71a51b777c89af586c92ac4b5f683a473a7c` | `go.mod:3,13,14` shows Go1.25, Redis/MySQL. Readable source and build package; not boot-verified. Approval is a separate material state boundary; skipping workflow for API requests does not prove approval behavior. |
| XAS `e73fd5ae271dea104cea71781f4b644e52d414c3` | `go.mod:3,40` shows Go1.24 and MySQL; slit compose available. Candidate real service for statement/reconciliation expansion; current sink cannot establish repair/reconciliation correctness. |
| AuthZ `8687f48f0b760b6bab6c711133079d4b534d391b`; Edge `da9ff5b22b7d0e4dbecfba5d3305ff8469c60de0` | AuthZ README describes Casbin/Consul policy deployment; Edge Dockerfile:2 requires custom Kong3.4.2 base and repo contains Lua plugins. Existing fidelity investigation (`reports/fidelity/raw/03_edge_kong_authz_identity.md`) finds public payout API enforcement shadowed, dashboard/OTP enforced. This lane did not revalidate every policy file: treat that narrow conclusion as inherited evidence, not a new blanket authorization guarantee. Real bootstrap remains untested. |

There is no behavior-changing arena patch to a core binary in this workstream. Synthetic timing, controlled delivery faults, fixture account mappings and the manually triggered Ledger mirror substitute can hide actual production queue delay/order and outage behavior; they must remain labeled representative.

## P0 handoff follow-up

The monolith now returns dual-write `status=success`, returns subscriber types from status-details updates, filters merchant responses to the parsed merchant/detail DTOs, selects only explicit pricing rules by supported dimensions, supports free-payout overrides and body-level pricing errors, maintains idempotent synthetic reward-credit deduction/reversal state, and decreases free-payout counters by balance ID. `create_fta` uses a one-second FTS timeout, keyed locks and completed-transfer reuse, with bounded asynchronous retries in default swallow mode and an explicit error mode. Unknown FTS statuses cause no Payouts call; repeated terminal states are no-ops. Auxiliary create-ledger/free-rollback routes are labelled sink behavior; banking-statement updates forward the source contract to Payouts.

Splitz defaults to off and the Payouts template uses remote evaluation (`client_side_eval=false`). Three offline Splitz checks pass. Monolith adapter checks:16 pass. Bank HTTP control checks:6 pass.

Remaining fidelity limit: FTA retry/idempotency and reward-credit state are process-local; this substitute does not claim durable API-DB FTA or credit transactions. The API-DB schema has the real plural FTA table, but the scoped monolith credentials currently permit only Payouts reads and API balance updates. Retry timing/count and wildcard dimensions in legacy fee plans are explicitly synthetic assumptions.


## Replay 1 status normalization correction

Actual Shared bank decline reached FTS FAILED but the substitute sent uppercase `fts_status` to Payouts, which rejected reversal processing. PHP `FundTransfer/Attempt/Core.php:496` lowercases incoming status before constructing the downstream update; Payouts reversal processing expects `failed`/`reversed`. The relay now lowercases both `fts_status` and `fta_status` while preserving the distinct mapped payout status (`reversed` for Shared FAILED). A regression verifies the real details-before-status ordering and all three values. The 17 isolated monolith adapter tests pass; clean live replay is pending. No core source changed.


## Direct-create beneficiary account type

The first direct-transport golden failed FTS validation because its beneficiary fixture supplied API `account_type=savings`. This is a vocabulary mismatch, not a case issue: FTS `internal/controllers/validation.go:40` uses case-insensitive validation for SAVING/CURRENT/NODAL (`pkg/validator/rules.go:419`), while API `app/Models/BankAccount/AccountType.php:7` defines plural `savings`. API's internal Payouts response forwards that nonempty value (`app/Services/PayoutService/Create.php:581`), and Payouts forwards it unchanged (`pkg/fts/transfer_init_create.go:399`).

The Shared baseline now uses an unspecified/null beneficiary account type, which API's validator explicitly permits (`app/Models/BankAccount/Validator.php:71`). Payouts then applies its existing singular `saving` default; M2's valid current type is unchanged. This is a source-faithful nullable fixture choice, not a normalization patch. The nonempty plural savings path remains an explicit unresolved cross-service contract limitation. A generated-fixture regression checks API-valid values and compatibility with the existing Payouts default/FTS vocabulary; all18generator tests pass. Live direct transport replay is pending.
