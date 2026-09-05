# Lane 04 — Payouts Service (razorpay/payouts, Go) core behaviour

Repo: `razorpay/payouts` @ `4bf3dbf9239feadea6d65ca90c893a988e116173` (branch `master`, shallow clone,
`/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture/payouts`).
All line numbers below are against this commit. `payouts/path:line` provenance is used throughout;
paths from other repos are prefixed with that repo's name (`ledger/…`, `kube-manifests/…`).

## Scope and sources read

- **Direct reads this pass** (by me): `internal/app/payouts/state_machine.go` (full, 465 lines),
  `internal/app/common/appConstants/{states,status,events,features,constants,entity}.go`,
  `internal/app/payoutLog/{model,core}.go`, `internal/app/payouts/model.go` (`OnEvent`, lines 1398‑1449),
  `internal/database/migrations/{20200615182810_create_payouts_table,20210902153752_create_payout_details_table,
  20211026104754_create_payout_status_details_table,20201113232006_create_reversals_table,
  20220616173226_create_payout_meta_temporary_table,20221004002554_create_idempotency_keys_table,
  20240830164301_bulk_idempotency_keys,20250311053125_create_merchant_configurations_table,
  20220526144631_create_counters_table}.go`, `internal/app/payoutDetails/model.go`,
  `internal/app/reversals/reversalViaLedgerService.go` (full), `pkg/ledger/{client.go,ledger_journal_create.go}`,
  `internal/app/payouts/status.go`, `internal/app/common/appConstants/events.go`,
  `internal/app/payouts/processor/queueingstrategy/strategy.go` (full, 52 lines),
  `internal/app/payouts/validation.go` (partial — `Validate`, `IsValidQueuedPayout`, `ValidateCancel`),
  `internal/app/dtos/payoutCreate.go` (`FundAccountPayoutRequest`), `internal/app/payouts/processor/payout_pricing.go`
  (pricing-source selection, lines ~200‑300), `internal/app/payouts/onHoldHelper.go` (SLA fetch, lines 100‑160),
  `pkg/api/{fetch_pricing.go,fts_create.go,fetch_balances.go,fetch_merchant_slas.go}`,
  `internal/app/payouts/helperQueuedPayouts.go`, `internal/app/balance/repo.go` (6‑hour filter),
  `internal/job/fts_async_processing.go`, `internal/app/payouts/core.go` (`FtsAsyncProcessing` 5809‑5891,
  `HandleBalanceChangeEvent` 4314‑4363), `internal/routing/router/payout_internal_routes.go` (balance_update_event route).
- **Fork sub-investigations** (same repo, read-only, no writes; each returned findings as text, folded in below with
  their own file:line citations preserved): merchant-config/DCS/Splitz deep dive; cron routes + worker/Kafka-consumer
  inventory (cross-checked against `kube-manifests`); ledger journal bodies + FTS Kafka ingestion + Stork events;
  bulk/admin routes + outbound-client contract table (cross-checked against `config/prod.toml`); twin/arena
  config-and-seed comparison (`ENV2_COMPOSE`).
- **Prior lane reports treated as hypotheses and checked** (per brief): `raw-findings/01_payouts_core.md`,
  `02_payouts_integrations.md`, `12_payouts_approval_queue_bulk.md`, `13_fts_payouts_status_path.md`,
  `15_validation_risk_limits.md`, `CONTROL_AND_INVARIANT_CATALOG.md`, `ARCHITECTURE_DELTA.md` (§W9).
- **Twin artifacts read** (via fork): `ENV2_COMPOSE/config/templates/base/payouts/{default,arena,e2e}.toml`,
  `ENV2_COMPOSE/seeds/s4/{payouts.sql,dcs_fixtures.json,splitz_fixtures.json,ledger.sql,fts.sql,monolith_fixtures.json,
  mozart_scenarios.json}`, `ENV2_COMPOSE/scripts/cron-driver/driver.py`, `ENV2_COMPOSE/substitutes/{dcs-stub,splitz-stub,
  monolith-stub}/server.py`, `ENV2_COMPOSE/verifier/verifiers/*` (V04,V06,V10,V12,V18,V24), `ENV2_BUILD_STATUS.md`.
- **Not re-cloned/re-read this pass**: `ledger`, `fts`, `kube-manifests` were only spot-checked via the forks for
  cross-reference (topic names, worker Deployment names, account seeding), not exhaustively re-audited — their own
  lane reports (`03_fts.md`, `04_ledger.md`, `22_kube_spinnaker_alerts.md`) remain authoritative for those repos.

---

## Production behaviour

### 1. Payout status enum and transition table

**Full internal status set** (CONFIRMED, `payouts/internal/app/common/appConstants/states.go:4-18`):
`create_request_submitted`, `created`, `pending`, `pending_on_otp`, `scheduled`, `batch_submitted`, `queued`,
`on_hold`, `ledger_response_awaited`, `initiated`, `processed`, `reversed`, `failed`, `cancelled`, `rejected`.
Fifteen statuses total — matches the brief's list exactly, `pending_on_otp` is the one not explicitly named in the
brief (present in code, but no transition event targets it in `state_machine.go` — dead/legacy status value, see
Corrections).

**Initial state**: `create_request_submitted` (`transition.InitializeTransition(&Payout{}, appConstants.StateCreateRequestSubmitted)`,
`payouts/internal/app/payouts/state_machine.go:18`).

**Transition table** (from `sm.Event(...).To(...).From(...)` calls, `state_machine.go`, full file read — this
supersedes prior lane 01's table with exact line numbers and confirms it is complete, no additional events exist):

| Event (string) | To | Valid From | Enter-hook side effects on `To` state | Enforcing line |
|---|---|---|---|---|
| `created` | `created` | create_request_submitted, pending, scheduled, queued, on_hold, ledger_response_awaited, batch_submitted | `FireWebhookEventAsyncForPayout(StateCreated)` → Stork `payout.initiated` | `:129` (event), `:19-27` (Enter) |
| `ledger_response_awaited` | `ledger_response_awaited` | create_request_submitted, pending, scheduled, queued, on_hold, batch_submitted | `entity.SetStatus(...)` only, no webhook | `:151` (event), `:28-34` (Enter) |
| `initiated` | `initiated` | created, **initiated** (self-loop, explicit comment: allows FTS async retries to re-fire without erroring — "matches API monolith behaviour, Status.php") | `SetStatus` only | `:177` (event), `:36-41` (Enter) |
| `failed` | `failed` | create_request_submitted, created, pending, scheduled, queued, on_hold, ledger_response_awaited, batch_submitted, **initiated** | `FireWebhookEventAsyncForPayout(StateFailed)` → Stork `payout.failed` | `:205` (event), `:65-72` (Enter) |
| `processed` | `processed` | **initiated only** | `SetStatus` + `FireWebhookEventAsyncForPayout(StateProcessed)` → Stork `payout.processed` | `:243` (event), `:44-53` (Enter) |
| `reversed` | `reversed` | create_request_submitted, ledger_response_awaited, pending, on_hold, queued, scheduled, **processed, failed**, initiated, created, batch_submitted | `SetStatus` + Stork `payout.reversed` | `:266` (event), `:55-63` (Enter) |
| `pending` | `pending` | create_request_submitted only | Stork `payout.pending` | `:297` (event), `:105-112` (Enter) |
| `schedule` | `scheduled` | create_request_submitted, pending | no Enter hook registered (`sm.State(StateScheduled)` bare, `:117`) — no webhook on entering `scheduled` | `:319` (event) |
| `batch_summit` [sic, typo preserved from source] | `batch_submitted` | create_request_submitted, pending, scheduled | `SetStatus` only, no webhook | `:344` (event), `:119-125` (Enter) |
| `queued` | `queued` | create_request_submitted, ledger_response_awaited, pending, scheduled, on_hold | Stork `payout.queued` | `:368` (event), `:74-81` (Enter) |
| `cancelled` | `cancelled` | queued, scheduled, on_hold | no-op Enter (`:83-85`) — **no Stork webhook fires on cancel** (confirmed absent from `StatusToWebhookEventMap`) | `:392` (event) |
| `on_hold` | `on_hold` | create_request_submitted, pending, batch_submitted, scheduled | `SetStatus` + `SetOnHoldAt(now)` + Stork `payout.queued` (on_hold and queued share the same webhook event) | `:419` (event), `:87-95` (Enter) |
| `rejected` | `rejected` | pending only | Stork `payout.rejected` | `:444` (event), `:97-103` (Enter) |

Terminal states (no further `.To(...)` targets them from any state other than themselves): `processed`, `failed`,
`cancelled`, `rejected` — but `reversed` is reachable *from* `processed` and `failed`, making reversal a genuine
post-terminal correction, not a DAG leaf (CONFIRMED, `:266-269`).

**Public status collapse** (`InternalToPublicStatusMap`, `payouts/internal/app/common/appConstants/status.go:3-18`):
`processing` ← {created, initiated, ledger_response_awaited, create_request_submitted, batch_submitted};
`queued` ← {on_hold, queued}; `pending` ← {pending_on_otp, pending}; `processed`←processed; `reversed`←reversed;
`rejected`←rejected; `cancelled`←cancelled; `failed`←failed.

**Status → Ledger-event map** (`payouts/internal/app/payouts/status.go:52-58`, `StatusToLedgerEventMapping`):
`ledger_response_awaited`→`payout_initiated`, `created`→`payout_initiated`, `processed`→`payout_processed`,
`reversed`→`payout_reversed`, `failed`→`payout_failed`. Parallel maps exist for VA-to-VA (`:60-65`,
`va_to_va_payout_initiated`/`va_to_va_payout_failed` — **no distinct VA-to-VA "processed"/"reversed" event**, both
collapse to the same `_failed` name on reversal) and Charge-Collections payouts (`:67-73`, `cc_debit_initiated
/_processed/_reversed/_failed`).

**Status → Event map** used to drive `OnEvent` from a target status name (`payouts/internal/app/payouts/status.go:10-21`,
`StatusToEventMap`) — a 1:1 mirror of the event names above, used by callers that only know the desired status string.

**`payout_status_details` sub-model** — CORRECTION: the brief and prior reports call this `payouts_status_details`;
the actual table name is **`payout_status_details`** (singular `payout`), migration
`payouts/internal/database/migrations/20211026104754_create_payout_status_details_table.go:13-24`:
```sql
CREATE TABLE payout_status_details (
  id char(14) PRIMARY KEY, payout_id char(14) NOT NULL,
  status varchar(15) NOT NULL, reason varchar(55) NOT NULL, description varchar(255) NOT NULL,
  mode varchar(20) NOT NULL, triggered_by varchar(255) DEFAULT NULL,
  created_at int NOT NULL, updated_at int NOT NULL,
  INDEX payout_status_details_payout_id_index (payout_id)
);
```
One row is written per FTS/webhook status update (not per state-machine transition) — it is FTS's own
status/reason/description/mode narrative, distinct from `payout_logs`' from→to transition ledger.

**`payout_logs`** (CONFIRMED): model `payouts/internal/app/payoutLog/model.go:8-16` —
`{spine.Model; Event, From, To, Mode, TriggeredBy string}`, table `payout_logs`. It is **not written by a DB insert
inside `state_machine.go`'s hooks** — those hooks only call the logger (structured log lines), not a repo write.
The actual `payout_logs` row is appended in-memory inside `Payout.OnEvent` (`payouts/internal/app/payouts/model.go:1411-1419`):
```go
log := payoutLog.PayoutLog{Event: eventName, From: stateWas, To: p.GetStatus(),
    Mode: transition.ModeSystem, TriggeredBy: transition.TriggerSystem}
p.PayoutLog = append(p.PayoutLog, log)
```
`OnEvent` is confirmed the single choke point for every status change (comment at `model.go:1440-1444`); the
in-memory slice is presumably flushed to the DB as a GORM association on the subsequent `repo.Update`/`repo.Create`
call (association-save mechanics not traced further this pass). `Mode`/`TriggeredBy` are **always** the literal
strings `"system"` (`transition.ModeSystem`/`transition.TriggerSystem`) for every transition recorded this way —
there is no per-actor (human/API-key) attribution captured in `payout_logs` itself.

**No migration or model for a table named `inflight_reservations`, `payouts_status_details`, or `fund_transfer_attempts`
exists anywhere in this repo** (grep across `internal/database/migrations`, confirmed absent) — the in-flight
reservation mechanism is pure Redis (see §4); "FTA" (fund transfer attempt) is an FTS/monolith-owned entity, not a
Payouts Service table.

---

### 2. Create pipeline, in order

Entry: `POST /v1/payouts` → `PayoutService.PostFundAccountPayout` (wrapped in `IdempotencyKey` middleware) →
`BasePayoutProcessor.CreatePayout` (`payouts/internal/app/payouts/processor/base.go:83`) →
`Core.FetchDependenciesAndCreatePayout` (`payouts/internal/app/payouts/core.go:1064`).

**Request DTO validation** (binding tags, `payouts/internal/app/dtos/payoutCreate.go:11-39`,
`FundAccountPayoutRequest`, CONFIRMED — supersedes prior partial reads):

| Field | Binding rule | Notes |
|---|---|---|
| `purpose` | `required,max=30` | |
| `amount` | `required` (int64, paise) | zero/negative rejected downstream by `ValidateModeAndAmount`, not by binding |
| `currency` | `required,oneof=INR MYR USD EUR GBP SGD AED,len=3` | **7 currencies accepted at the DTO level**; channel-level rule (`mode.go`) then restricts non-INR/MYR to RBL only (prior lane 15 row 4) — the DTO is broader than the effective business rule |
| `merchant_id` | `required,len=14` | |
| `account_number` | `omitempty` | |
| `reference_id` | `omitempty,max=40` | |
| `narration` | `omitempty,alphanum,max=30` | **alphanumeric only** — no spaces/punctuation accepted at bind time (this is stricter than "free text narration" assumptions in some prior narratives) |
| `idempotency_key` | `omitempty` (in-body; header `X-Payout-Idempotency` is the primary carrier per lane 01 finding 9) | |
| `fund_account_id` | `required_without=FundAccount` | either an id or an inline composite `fund_account.contact` object |
| `source_details` | `omitempty,dive` (`SourceID,SourceType required; Priority required,min=1`) | multi-account routing hint |
| `scheduled_at`, `batch_id`, `skip_workflow`, `tds`, `attachments`, `remitter_details`, `subtotal_amount` | all `omitempty` | |

`Payout.Validate()` (`payouts/internal/app/payouts/validation.go:29-41`) additionally runs `ValidateModeAndAmount`
(amount tiers, mode caps — prior lane 15 rows 3,5 CONFIRMED, min ₹1/100 paise, max ₹10cr default/₹50cr with
`IncreasePayoutLimit`/₹300cr settlements-cross-border, RTGS≥₹2L/IMPS≤₹5L/UPI≤₹1L/AmazonPay≤₹10k) and
`ValidateScheduledAt`. Uniqueness for idempotency: `idempotency_keys` UNIQUE(`idempotency_key`,`merchant_id`)
(§6 synthetic data below); duplicate-payout hash detection is a **separate, Splitz-shadow-or-enforce** mechanism,
not a DB constraint (prior lane 15 row 15, `base.go:199-237,1540-1590`).

**Pipeline order** (from `base.go`/`core.go`, cross-checked against prior lanes 15 §2 and this pass's own reads):

1. Dependency fetch (fund account, contact, purpose, banking account, merchant config) — errgroup, `core.go:4754-4818`.
2. Business-banking-enabled + activated checks from merchant config (`core.go:1087-1094`).
3. Amount/mode/currency/channel validation (`Payout.Validate()`, `mode.go`).
4. Fund-account/contact active checks, internal-contact protection, VPA blacklist.
5. Merchant funds-on-hold check (Shared accounts).
6. Duplicate-payout hash check (Splitz-gated shadow-or-enforce).
7. **Shield** risk evaluation (`base.go:239-253` → `core.go:6858-6885`) — binary `block`/else-proceed, fail-open on
   error/200ms timeout (prior lane 15 row 16, §3.2 — unchanged, re-confirmed).
8. **Pricing** — see dedicated subsection below; this pass newly traces the exact selection order.
9. Approval-workflow gate (`IsWorkflowApplicable`) — if triggered, `CreatePayout` returns early at `pending` with no
   ledger/FTS dispatch (prior lane 12 §A.1, re-confirmed).
10. Queue-if-required check (direct accounts) / ledger authorization call (Shared/VA accounts) — see §4.
11. On success, event `created` fires (or `queued`/`on_hold`/`scheduled`/`batch_submitted` as applicable) →
    async job `fts_async_processing` is queued.
12. `fts_async_processing` worker (`payouts/internal/job/fts_async_processing.go:18-27`, `MaxRetries:5, Timeout:120s`)
    → `Core.FtsAsyncProcessing` (`core.go:5809-5891`, CONFIRMED, full function read this pass):
    ```go
    if c.IsFTSRequestFromPayoutsService(ctx, &payout) {
        return c.FundTransferServiceProcessing(ctx, &payout, merchantConfig)   // direct pkg/fts call
    }
    return c.processorFactory.GetProcessor(...).CreateFTS(&payout)             // legacy: monolith POST /payouts_service/create_fta/
    ```
    Confirmed monolith route string: `payouts/pkg/api/fts_create.go:16`, `FTSCreate = "/payouts_service/create_fta/"`.

**Pricing selection — which is live, and what selects it** (CONFIRMED, `payouts/internal/app/payouts/processor/payout_pricing.go:200-300`,
read directly this pass — resolves prior lane 15/catalog C27's open "which is live" question):
1. If `SplitzExperimentList.ChargeCollectionsCallExperiment` is ON for the merchant → **Governor+ChargeCollections
   SDK is the sole pricing source** (`fetchPricingUsingChargeCollections`, `:200-218`) — the legacy monolith path is
   skipped entirely for this merchant.
2. Else, if `fee_type != free_payout`: try a **Redis-cached pricing rule** set earlier in the same request by the API
   monolith (`FetchPricingRuleInfo(payout, "redis")`, `:230-244`) — gated further by
   `SplitzExperimentList.PerformanceOptimizationExperiment` OR `account_type==Direct`; if the cached rule's inputs
   still match the current payout and a `pricing_rule_id` is present, it is used as-is (`:249-262`). If
   `ChargeCollectionsCallCanaryExperiment` is also ON, a **fire-and-forget shadow call** to CC-SDK runs in a goroutine
   purely for comparison telemetry (`:271-273`, `go b.executeChargeCollectionsCall(payout)`).
3. Else (Redis miss/mismatch, or `fee_type==free_payout`): **blocking call** `FetchPricingRuleInfo(payout, "api")`
   (`:283`) → monolith route `payouts/pkg/api/fetch_pricing.go:16`, `FetchPricing = "/payouts_service/fetch_pricing_info"`.
   Any error here fails `Process()` and the payout create is rejected — **fail-closed** (prior lane 15 §3.3,
   re-confirmed).

So the **default/most common live path in an unconfigured merchant is the monolith's `fetch_pricing_info`**
(via a Redis pre-fetch cache, with a blocking API fallback on miss); Governor+ChargeCollections is the Splitz-gated
alternative primary source, and is also used in canary/shadow mode alongside the Redis path independently of which
is primary.

**Ledger authorization request body** (`payout_initiated`, CONFIRMED — `payouts/pkg/ledger/ledger_journal_create.go:155-234`,
`createLedgerJournalRequest`, the single builder shared by all four events):

| `ledgerSdkDto.JournalCreateRequest` field | Value / source | Line |
|---|---|---|
| `MerchantID` | `payout.GetMerchantID()` | `:163` |
| `Currency` | `payout.GetCurrency()` | `:164` |
| `TransactorID` | `payout.GetSignedID()` (create path); overridden to `reversal.GetLedgerSignedID()` for failed/reversed/CC-debit-failed/-reversed/VaToVa-failed | `:165`, override `:204` |
| `TransactorEvent` | the resolved ledger-event string (`payout_initiated`/`payout_processed`/`payout_reversed`/`payout_failed`, or the VA-to-VA/CC-debit families) | `:166` |
| `TransactionDate` | `payout.GetCreatedAt()`; → `time.Now().Unix()` for `payout_processed`/cc_debit_processed; → `reversal.GetCreatedAt()` for failed/reversed | `:167,194,205` |
| `Notes.balance_id` | `payout.GetBalanceID()` | `:169` |
| `MoneyParams.amount` / `.base_amount` | `strconv.FormatInt(payout.GetAmount(),10)` (both identical, paise, **unsigned** — Ledger decides debit/credit sign from `TransactorEvent`, not from Payouts) | `:173-174` |
| `MoneyParams.commission` | `payout.GetFee()` | `:175` |
| `MoneyParams.tax` | `payout.GetTax()` | `:176` |
| `Identifiers.banking_account_id` | `bankingAccount.GetSignedID()` — **always present**, resolved via `GetBankingAccountByMerchantIdAndBalanceID(merchantID, balanceID)` | `:179`, `payouts/internal/app/reversals/reversalViaLedgerService.go:49-52` |
| `Identifiers.fts_fund_account_id`, `Identifiers.fts_account_type` | **only for `payout_processed`/`payout_reversed`/cc_debit_processed/cc_debit_reversed** — see §5 for provenance (FTS-webhook-supplied, not stored) | `:216-217` |
| `Identifiers.product_id`, `AdditionalParams.account_type` | only if `purpose==charge_collections`, from `payout.Notes` | `:223-230` |
| `AdditionalParams.fee_accounting=reward` | only if `payout.GetFeeType()==reward` | `:185-189` |
| `SyncRetryAttempts` | `LedgerClient.HttpRetryAttempts` config value | `:181` |

**Balance sources** (CONFIRMED, three genuinely distinct mechanisms, no unification):
- **Ledger** (Shared/VA payouts): the `Journal.Create payout_initiated` call itself is the balance authorization —
  a ledger-side atomic `UPDATE ... WHERE balance+inc>=min_balance`; insufficient balance surfaces as a
  recognized-message ledger error, reclassed to `PayoutNotEnoughBalanceViaLedger`
  (`payouts/internal/app/payouts/processor/payoutViaLedgerService.go:240-245`).
- **Monolith VA balance** (queued-payout dequeue, Direct/CA path — see §4): read via cross-service `db.api`
  connection (`balance` table) plus a live HTTP call to the monolith, **not** Ledger.
- **"MerchantBalance"**: this term (used in `CONTROL_AND_INVARIANT_CATALOG.md` C15/C19/C16) does **not appear
  anywhere in the `payouts` repo** (grep confirmed zero hits) — it is a Ledger-side account-type concept belonging
  to the `ledger` repo's own accounting model, not a Payouts Service abstraction. Payouts only ever talks to Ledger
  via the generic `JournalCreateRequest`/`CreateJournal` interface above; it has no local notion of "MerchantBalance."

**Queueing strategy selection** — `payouts/internal/app/payouts/processor/queueingstrategy/strategy.go`, full file
(52 lines), quoted in full because the brief asks for it exactly:
```go
package queueingstrategy

type Decision struct {
    Queue      bool
    Compensate func()
}

type Gate interface {
    Evaluate(payout *payouts.Payout, payoutAmount int64, merchantBalance dtos.DirectAccountBalanceInfo) (Decision, errors.IError)
}

func Select(ctx context.Context, payout *payouts.Payout, payoutsCore payouts.ICore) Gate {
    if payout.MerchantConfig != nil &&
        payout.MerchantConfig.IsFeatureEnabled(features.InFlightReservationEnabled) {
        return reservationGate{ctx: ctx, payoutsCore: payoutsCore}
    }
    return balanceBufferGate{ctx: ctx}
}
```
This applies **only to Direct/CA-rail payouts** — the gate answers "queue vs. dispatch to FTS," selected per
merchant on the DCS flag `in_flight_reservation_enabled` (routed under DCS key `rzp/x/merchant/payouts/direct_accounts/Configs`,
per §3). Shared/VA payouts never reach this gate — their queue/no-queue decision is made by Ledger's response to
the `payout_initiated` journal call (see Balance sources above and §4).

**Workflow trigger**: `BasePayoutProcessor.IsWorkflowApplicable` (`payouts/internal/app/payouts/processor/baseHelper.go:107-155`)
— unchanged from prior lane 12 §A, re-confirmed: skips for `rzp_fees` purpose, internal contacts (unless
`EnableWorkflowForInternalContact`), `PayoutWorkflows` feature off (dual-name-gated by
`PayoutWorkflowsDcsNameExperiment`, see §3), `SkipWorkflow` from PAYOUT_LINKS, XPERIENCE app, batch+`SkipWorkflow`.

---

### 3. Merchant config resolution

**`MerchantConfig` struct** (CONFIRMED, `payouts/internal/app/merchant/model.go:11-38`):
```go
type MerchantConfig struct {
    Id, Name, Email, BillingLabel, OrgID, Mcc, CountryCode, Category,
    BusinessType, BusinessName, CompanyPan,
    BusinessRegisteredAddress[, L2, City, State, Country, Pin],
    PricingPlanId, PurposeCode, IecCode string
    Activated, Live, BusinessBanking, HoldFunds bool
    Feature   map[string]int   // 1 = enabled; there is no separate "Feature" struct type
    CreatedAt int64
}
```
`IsFeatureEnabled(name) bool` ⇔ `mc.Feature[name] == 1` (`model.go:64-66`).

**Source selection** — gated by Splitz `SplitzExperimentList.MerchantConfigViaAsvAndDcs`
(`payouts/internal/app/merchant/core.go:1077-1084`, catalog C28's "`Qnc6b4fg1Hi36k`"-style gate; the real Splitz
experiment ID is not in any repo — Splitz-MySQL-only per `ARCHITECTURE_DELTA.md` §15). Any Splitz error/timeout
→ hardcoded `false` → **legacy monolith path**.

**(a) Legacy path (experiment off)** — `GET {api.host}/internal/merchants/{merchantId}`
(`payouts/pkg/api/merchant_config.go:14-42,69-89`, `MerchantURIPrefix = "/internal/merchants/"`):
```go
type MerchantConfigFetchResponse struct { Merchant Merchant; MerchantDetails MerchantDetail }
type Merchant struct {
    Id, Name, Email, BillingLabel, OrgID, Mcc, CountryCode, Category, PricingPlanId, PurposeCode string
    Activated, Live, BusinessBanking, HoldFunds bool
    Features []string
    CreatedAt int64
}
type MerchantDetail struct {
    BusinessType, BusinessName, CompanyPan,
    BusinessRegisteredAddress[, L2, City, State, Country, Pin], IecCode string
}
```
Every string in `Merchant.Features` becomes an enabled key (`Feature[name]=1`, `core.go:223-226`) — no distinction
between DCS-migrated and never-migrated flag names.

**(b) ASV+DCS path (experiment on)** — `payouts/internal/app/merchant/core.go:237-342`, three concurrent fetches:
- **ASV** (Account Service, gRPC) — **blocking**, failure aborts the whole fetch (`errorclass.AsvFetchError`).
  Field-mask requests: `account.{id,name,email,activated,live,billing_label,business_banking,hold_funds,org_id,
  created_at,category,country_code,category2,pricing_plan_id,purpose_code}`,
  `account_detail.{business_type,business_name,business_registered_address.{line1,line2,line3,city,country,zipcode}},iec_code}`
  (`core.go:34-59`).
- **API-DB `features` table** — non-blocking, failure → empty map, logged only. Table literal `"features"`
  (`payouts/internal/app/merchant/feature.go:13-23`), queried on the **monolith's own MySQL via payouts' `[db.api]`
  cross-service connection** (not payouts' own DB), `WHERE entity_id=? AND entity_type='merchant'`
  (`feature_repo.go:24-44`). No fixed enum of name strings lives in this repo — it is a free-text row set the
  monolith owns; the "which names PS checks" answer is the consumer-side list in §(e) below.
- **DCS features** — non-blocking, failure → empty map, logged only.
Merge: DCS **overrides** API-DB on key collision (`mergeFeatures`, `core.go:458-469`). Cache: **both branches** write
the same Redis key `{payouts_merchant_config_key}_%s` (merchant ID), TTL `30*time.Minute`
(`core.go:30-31`, re-confirmed). `UpdateMerchantFeatureInCache` on a cold/expired key (`RedisNilError`) silently
no-ops — no populate-on-miss, no pub/sub invalidation anywhere in `internal/app/merchant` or `pkg/dcs`
(`core.go:784-852`, re-confirmed unchanged from prior lane 12 §A.6).

**(c) DCS key format and field routing** (CONFIRMED, `payouts/pkg/dcs/features/features.go`, full file):
storage keys are `rzp/x/merchant/payouts/<Object>` (`PayoutApiInterfaceKey`, `PayoutFundTransferKey`,
`PayoutWorkflowsKey`, `PayoutIntelligentPayoutsKey`, `PayoutSubAccountRolesPayoutsKey`,
`PayoutDirectAccountsConfigsKey` = `rzp/x/merchant/payouts/direct_accounts/Configs`,
`PayoutDirectAccountModeConfigSettingsKey` = `.../direct_accounts/PayoutModeConfig`,
`AccountingIntegrationSettingsKey` = `rzp/x/merchant/accounting/IntegrationSettings`). Each field is a **boolean
field on the proto message stored at that key** (reflection-based extraction, `features.go:241-259`) — proto3
zero-value default = off/absent. A load-bearing in-source comment (`features.go:20-24`) documents that
`in_flight_reservation_enabled` (config-proto #1599) lives under `PayoutDirectAccountsConfigsKey`
("direct_accounts/Configs"), **not** `FundTransfer` where the design originally assumed — a documented past bug
class ("`Key()` must agree with where DCS actually stores the field or `IsFeatureEnabled` never sees it").

Full DCS field list (28 constants, `features.go:28-73`) is reproduced verbatim in the merchant-config fork's table
(not repeated here for space) — key ones: `InFlightReservationEnabled`→`in_flight_reservation_enabled`,
`PayoutWorkflows`→`enable_payout_workflow`, `QueuePayoutBalBuffer`→`queue_payout_bal_buffer`,
`EnablePayoutQueueBuffer`→`enable_payouts_queue_buffer`, `EnableApprovalViaOauth`→`enable_approval_via_oauth`.
`MerchantFeatures()` (`features.go:261-297`) is the fixed list of 18 (of ~28) DCS names actually fetched per
merchant-config load by default.

**(d) API-DB `features` table** — see (b) above; no fixed name enum in this repo (monolith-owned free-text rows).

**(e) Legacy `appConstants` feature-name constants** — the full consumer-side list PS checks against the merged
`Feature` map, **45 constants**, `payouts/internal/app/common/appConstants/features.go:1-58` (full file, CONFIRMED):
`payout`, `payout_service_enabled`, `payout_process_async`, `payout_process_async_lp`, `allow_va_to_va_payouts`,
`skip_hold_funds_on_payout`, `new_banking_error`, `disable_x_amazonpay`, `payouts_on_hold`, `skip_test_txn_for_dmt`,
`apps_status_update_via_ps`, `bene_name_in_payout`, `null_narration_allowed`, `handle_va_to_va_payout`,
`increase_payout_limit`, `below_rupee_payouts`, `high_tps_composite_payout`, `da_ledger_journal_writes`,
`enable_http_encryption`, `icici_baas`, `payout_workflows`, `skip_wf_at_payouts`, `skip_workflow_for_api`,
`skip_for_internal_payout`, `skip_wf_for_payroll`, `skip_for_pg_payout`, `skip_wf_for_payout_link`,
`skip_wf_for_xperience`, `bulk_payout_workflow`, `blocklist_for_wf_service`, `rbl_ca_upi`, `enabled`, `disabled`,
`fts_request_notes`, `alternate_payout_fr`, `payout_idem_key_required`, `payouts_to_fts_async_processing`,
`payouts_blocked_on_lite`, `enable_approval_via_oauth`, `allow_non_saved_cards`, `enable_smart_routing`,
`hide_rx_payroll_payouts`, `block_va_payouts`, `assume_sub_account`, `disable_tpv_flow`.

**Dual-naming mismatch table** (CONFIRMED, `payouts/pkg/dcs/features/features.go:75-133`, `ApiName`/`DcsName` pairing
read literally — this is **broader than the known workflow-flag bug**, a genuine new finding this pass):

| Go identifier | legacy/API-DB string | DCS string | Match? |
|---|---|---|---|
| BeneNameInPayout | `bene_name_in_payout` | `bene_name_in_payout` | match |
| NullNarrationAllowed | `null_narration_allowed` | `enable_null_narration` | **MISMATCH** |
| PayoutIdemKeyRequired | `payout_idem_key_required` | `payout_idem_key_required` | match |
| BelowRupeePayouts | `below_rupee_payouts` | `below_rupee_payouts` | match |
| HttpEncryptionEnabled | `enable_http_encryption` | `enable_http_encryption` | match |
| PayoutServiceEnabled | `payout_service_enabled` | `payout_service_enabled` | match |
| PayoutsEnabled | `payout` | `enable_payouts` | **MISMATCH** |
| IncreasePayoutLimit | `increase_payout_limit` | `increase_per_payout_amount_limit` | **MISMATCH** |
| PayoutsToFtsAsyncProcessing | `payouts_to_fts_async_processing` | `payouts_to_fts_async_processing` | match |
| PayoutsBlockedViaLiteAccount | `payouts_blocked_on_lite` | `payouts_blocked_via_lite_account` | **MISMATCH** |
| EnableApprovalViaOauth | `enable_approval_via_oauth` | `enable_approval_via_oauth` | match |
| SkipWfAtPayout | `skip_wf_at_payouts` | `skip_workflow_for_dashboard` | **MISMATCH** |
| SkipWfForPayroll | `skip_wf_for_payroll` | `skip_workflow_for_payroll` | **MISMATCH** |
| SkipWorkflowForApi | `skip_workflow_for_api` | `skip_approval_workflow_for_api` | **MISMATCH** |
| PayoutWorkflows | `payout_workflows` | `enable_payout_workflow` | **MISMATCH** (already known) |
| PayoutProcessAsync | `payout_process_async` | `enable_intelligent_payouts` | **MISMATCH** |
| SkipTestTxnForDMT | `skip_test_txn_for_dmt` | `disallow_traffic_for_test_transactions` | **MISMATCH** |
| RblCaUpi | `rbl_ca_upi` | `rbl_ca_upi` | match |
| AssumeSubAccount | `assume_sub_account` | `assume_sub_account` | match |
| InFlightReservationEnabled | (no legacy equivalent — DCS-only) | `in_flight_reservation_enabled` | n/a |

Only `PayoutWorkflowsDcsNameExperiment` (Splitz) is confirmed to OR both name-sets together
(`payouts/internal/app/payouts/service.go:1475-1489`, `processor/baseHelper.go:126-135`) — **no equivalent
reconciliation exists for any of the other 9 mismatched pairs**: e.g. `payouts_blocked_on_lite` (API-DB row) and
`payouts_blocked_via_lite_account` (DCS field) are read as two entirely independent keys by any caller checking one
name or the other, with no OR-gate.

**Splitz experiment inventory** — `SplitzExperimentList` struct, **38 fields**
(`payouts/internal/config/config.go:396-435`, CONFIRMED — supersedes prior "~15 fields" partial reads). Generic
evaluation mechanics (`pkg/splitz/splitz.go:102-161`): Splitz returns `Variant{Variables:[{Key:"result",
Value:"on"|"off"}]}`; `checkIfVariantEnabled` ⇔ `result=="on"`. **Any client error/timeout → hardcoded `false`**
(fails to the pre-existing/disabled behavior at every call site read). A second variant,
`IsSplitzExperimentOnForVariable(...)`, checks an arbitrary `{key:value}` pair (e.g. duplicate-payout's
`{enable,action}` pair). Of the 38 fields, ~10 were traced to full branch semantics this pass and by prior lanes
(`MerchantConfigViaAsvAndDcs`, `PayoutWorkflowsDcsNameExperiment`, `ForDuplicatePayoutEvaluate`,
`ExperimentForFtsRequestFromPayoutsService`, `IKeyAutoEnforcementRampUp`, `ForDualWriteDirectPushToAPI`,
`ChargeCollectionsCallExperiment`, `ChargeCollectionsCallCanaryExperiment`, `BulkPayoutConcurrencyExperiment` — this
one newly resolved this pass, see §7, `FetchFromMicroservicesExperiment`); the remaining ~28 are CONFIRMED as real
call sites (file:line found by grep) but their variant-branch semantics were not read this pass — flagged as an
incompleteness, not a gap in existence.

---

### 4. Queued/low-balance dequeue; scheduled; on-hold; reservation

**Queued/low-balance dequeue mechanism** (CONFIRMED, full call chain read this pass):
1. Cron `POST /v1/cron/process_queued_low_balance_payouts` → `PayoutService.ProcessInitiateForQueuedPayouts`
   (`payouts/internal/routing/router/cron_routes.go:60-69`).
2. `GetBankingBalanceIdsForVirtualAccountWhereBalanceUpdatedRecently` — filters candidate `balance_id`s to those
   whose `balance.updated_at >= now()-6h`, queried via the **cross-service `db.api` connection** (i.e. against the
   API monolith's own MySQL `balance` table, not Payouts' DB, not Ledger) —
   `payouts/internal/app/balance/repo.go:26-51`, CONFIRMED:
   ```go
   ctx = context.WithValue(ctx, db.ContextKeyDatabaseConnection, appConstants.AttributeAPI)
   sixHoursAgo := time.Now().Add(-6 * time.Hour).Unix()
   q := r.GetConnection(ctx, db.API).Table(TableBalance).
       Where("id IN (?)", balanceIds).Where("updated_at >= ?", sixHoursAgo).Pluck("id", &filteredBalanceIds)
   ```
3. For the surviving balance IDs, `FetchBalancesFromApi` (`payouts/internal/app/payouts/helperQueuedPayouts.go:91-102`)
   calls the monolith **live** (not the 6h-stale DB column) via `GET /internal_balances_queued`
   (`payouts/pkg/api/fetch_balances.go:15,42`, `BalancesFetchBulk = "/internal_balances_queued"`), body
   `{balance_ids: [...]}`, response `{balances: {balance_id: amount}}`.
4. `FindQueuedPayoutsForBalanceId` fetches queued payouts per qualifying balance, limit 5000, Redis-cached
   pagination offset, **no explicit `ORDER BY`** (prior lane 12 §B.3, re-confirmed unchanged).
5. `filterApplicableQueuedPayouts` (`helperQueuedPayouts.go:18-47`) greedily admits payouts while the fetched live
   balance still affords `amount+fee`, decrementing a running total — first-fit-by-list-order, not amount-sorted.
6. Each admitted payout is dispatched via the **`queued_payout` async worker job**
   (`payouts/internal/job/process_queued_payout.go`), not synchronously.

**`process_queued_payouts` cron body types** — CONFIRMED: the `/v1/cron/process_queued_payouts` endpoint (note:
distinct from `/process_queued_low_balance_payouts` above despite the similar name) is
**`ProcessQueuedPayouts.ProcessQueuedPayouts`** (`cron_routes.go:30-39`), which per prior lane 12 §C.3 drives
**partner-bank-on-hold** recovery (`PartnerBankHoldPayouts.ProcessQueuedPayouts`,
`payouts/internal/app/payouts/queued/partner_bank_hold_payouts.go:41-83`), **not** low-balance dequeue. The
`QueuedReason` constants that distinguish the different "queued"/"on_hold" causes
(`payouts/internal/app/common/appConstants/constants.go:276-279,520`, CONFIRMED):
`BeneBankDown = "beneficiary_bank_down"`, `LowBalance = "low_balance"`, `GatewayDegraded = "gateway_degraded"`,
`PartnerBankDown = "partner_bank_down"`. These four map to the brief's "`partner_bank_downtime`, `low_balance`, ..."
family; each has its own dedicated cron+job pair (see §6 cron table) — there is no single generic
"process_queued_payouts" that drains all reasons; the name is a historical artifact of the partner-bank-hold path
specifically.

**`balance_update_event` route** — CONFIRMED, `POST /v1/payouts_internal/balance_update_event` →
`PayoutService.HandleBalanceChangeEvent` (`payouts/internal/routing/router/payout_internal_routes.go:52-53`), same
internal-service BasicAuth as the rest of that route group (API/Workflow/Xperience/FTS/VendorPayments/
Settlements/Irctc — any of those callers can hit it). Body: `{balance_id string, balance int64}`
(`Core.HandleBalanceChangeEvent(ctx, balanceID, balance, ...)`, `core.go:4314-4363`). This is the **event-driven
complement** to the 6-hour cron sweep — it runs the *same* pagination/greedy-pack dequeue logic
(`filterApplicableQueuedPayouts`) immediately for one balance ID the instant a caller (presumably the monolith, on
detecting a top-up) reports a new balance, rather than waiting for the next cron pass. `HandleBalanceChangeEventTimeout = 60` seconds (`core.go:213`).

**Scheduled payouts**: 4 fixed IST slots {9,13,17,21} within 3 months (`payouts/internal/app/payouts/schedule.go:23,55`,
re-confirmed). `EventSchedule` fires at create time direct to `scheduled`, bypassing ledger/FTA at create
(`processor/scheduled_payout.go:11`). Cron `POST /v1/cron/process_scheduled_payouts` →
`PayoutService.InitiateScheduledPayouts` (`cron_routes.go:81-91`) selects `scheduled_at < now()` AND
status IN (`pending`,`scheduled`) (`repo.go:164-183`), dispatches one `schedule_payout` job per id
(`MaxRetries:5, Timeout:300s`, per cron/worker fork). Still-`pending` at slot time (workflow-blocked) is
auto-rejected (`core.go:2772-2867`).

**On-hold — two independent triggers** (re-confirmed, prior lane 12 §C.2):
- **Beneficiary-bank-down** (IMPS only): merchant feature `PayoutsOnHold`, Redis-cached IFSC-prefix bank-down map,
  probabilistic sampling so some traffic still probes uptime (`onHoldHelper.go:16,89`). **Writer of this Redis map
  is not in this repo** — reader-side only (`onHoldHelper.go:30-80`) — UNRESOLVED, see Cannot-derive list (brief's
  "UNRESOLVED #16").
- **Partner-bank/gateway-down** (Direct accounts): `HealthService.IsChannelDown(...,PartnerBankHealth)`, populated
  by inbound FTS webhook `POST /v1/notify/health/update` (BasicAuth cred.FTS) writing Redis hash
  `partner_bank_health` (`fundAccountPayoutDirect.go:577-604`, `internal_routes.go:11-22`).

**SLA source for on-hold auto-cancel** — CONFIRMED this pass, `payouts/internal/app/payouts/onHoldHelper.go:117-153`
(`GetPayoutIdsToCancel`): fetches merchant IDs with ≥1 on-hold payout, then calls
`merchantConfigCore.GetMerchantSlas(merchantIds, defaultSLA)` → monolith
`GET /merchant/on_hold_slas_internal` (`payouts/pkg/api/fetch_merchant_slas.go:14`,
`FetchMerchantSlas = "/merchant/on_hold_slas_internal"`), with a **Redis-cached default SLA fallback**
(`GetDefaultOnHoldSlaFromRedis`, writer not traced in this pass) used for merchants absent from the monolith
response. Breached-SLA on-hold payouts are force-`failed` by `cancelOnHoldPayout`
(`onHoldHelper.go:309-341`, prior lane 12 §C.3, re-confirmed).

**In-flight reservation (Redis-only, no DB table)** — re-confirmed unchanged from prior lane 12 §B.2:
package `payouts/internal/app/payouts/reservation` (`store.go`, `scripts.go`); keys hash-tagged per
`{merchant_id:balance_id}`; counter key `<prefix>:in_flight:{mid:balID}`, items key
`<prefix>:in_flight_items:{mid:balID}` mapping `payoutID→"<amount>:<dispatchedAtUnix>:<state>"`
(`live`|`awaiting_balance_refresh`); default TTL 6h, max 2000 items/balance
(`store.go:150,160,227-241,395-400`). Reservation taken atomically via a Lua script (`Reserve`,
`reservation_gate.go:170`, `store.go:249`) before dispatch. Release centralized in
`internal/app/payouts/reservation_hook.go:34-90` on `Payout.OnEvent`:
`processed`→`awaiting_balance_refresh` (held until debit visibly reflects), `failed`/`reversed`/`cancelled`/
`rejected`→released outright. Reconciler cron `POST /v1/cron/process_inflight_reservation_reconciliation` →
`ProcessReconciliationForInFlightReservations` (`cron_routes.go:70-80`) rebuilds Redis state from DB truth every
5 minutes and refreshes a fail-closed heartbeat (TTL 12.5min = 2.5× cadence;
`inflight_reservation_reconciler.go:51-56`) — the gate queues (fails closed) if the heartbeat is missing. A
read-only inspection endpoint `GET /v1/inflight_reservations` → `PayoutService.InspectInFlightReservations`
exists (`payout_internal_routes.go:197-211`, internal-service auth); code comment explicitly documents that a
force-release endpoint was a **rejected design** ("a force-release endpoint is a rejected footgun, and the cron
reconcile is the only repair mechanism") — CONFIRMED, this is a deliberate absence, not an oversight.

---

### 5. FTS status ingestion

**Inbound routes** (all under `/v1/payouts_internal/*` or `/v1/payouts/*`, shared internal-service BasicAuth
`cred.API,Workflow,Xperience,FTS,VendorPayments,Settlements,Irctc` — no passport, `payout_internal_routes.go:12-18`,
TODO comment at line 15 acknowledging the auth gap, unchanged):

| Route | Handler | Notes |
|---|---|---|
| `POST /transfer_status_webhook` | `HandleTransferStatusWebhookForPayout` | direct FTS→PS path (modern) |
| `PATCH /update_payouts_with_fts` | `HandlePayoutStatusUpdateViaFTS` | legacy monolith-relay path |
| `POST /update_payouts_details_with_fts` | `HandlePayoutDetailsUpdateViaFTS` | legacy monolith-relay, detail/UTR update |
| `PATCH /update_credit_transfer_payout` | `HandlePayoutUpdateFromCreditTransfer` | VA-to-VA credit-transfer variant |

**Body structs** (CONFIRMED): `StatusUpdateRequest{SourceID, Status, FailureReason *string, BankStatusCode *string,
FtsFundAccountId, FtsAccountType, FtsStatus}` (`payouts/internal/app/dtos/payoutUpdate.go:8-16`);
`DetailsUpdateRequest{Utr, Remarks, FailureReason, Channel, ReturnUtr, SourceID, Mode, FtsTransferId(int64,required),
BeneficiaryName, BankStatusCode, FtaStatus, StatusDetails{BeneficiaryBank,Reason,ProcessedByTime,Mode},
CmsRefNumber, GatewayRefNumber, BankProcessedTime}` (`:34-50`); direct-webhook DTO
`TransferStatusWebhookForPayoutRequest{SourceAccountId int64, BankAccountType string, ...}`
(`payouts/internal/app/dtos/transfer_status_webhook_request.go:24-25` — the two fields that become
`fts_fund_account_id`/`fts_account_type`, see below).

**FTS→Payout status map** (`payouts/internal/app/common/appConstants/states.go:64-128`, `FtsToPayoutStatusMap`,
quoted for the two account families that differ):
```go
"default"/Shared: { created→created, initiated→initiated, reversed→reversed, failed→REVERSED, processed→processed }
Direct: { RBL, ICICI, AXIS, YESBANK, IDFC, SLICE: { ..., failed→FAILED, processed→processed } }  // all identical 1:1
```
So **FTS `failed` on a Shared/default account is remapped to Payouts `reversed`**, not `failed`; only Direct-rail
channels map `failed→failed` literally (re-confirmed unchanged).

**Guards** (re-confirmed, prior lane 13, unchanged this pass):
- `AllowedStateTransitionForTransferWebhook` (`states.go:37-54`) only has entries for fromState ∈
  {initiated, processed, reversed, failed} — any payout in a pre-initiated state receives HTTP 200
  "webhook update skipped due to invalid state transition" and is left untouched, only an Info log (no alert).
- Terminal-repeat short-circuit: `payout.GetStatus()==status && status terminal` → 200 idempotent no-op.
- Per-payout Redis mutex `AcquireResource(ResourcePayout+SourceID, 30s)` guards both entry points identically —
  they serialize against each other, not just against themselves.

**Reversal creation path**: `reversals.Core.ReverseMerchantPayout` — `CreateReversalEntity` (DB write) always
precedes `transactionProcessingForPayoutReversal` (ledger call) — re-confirmed, prior lane 12 §F.2.

**Ledger `payout_processed`/`payout_reversed`/`payout_failed` identifiers — why the twin's account discovery
fails (THE CENTRAL MECHANISM for this section, CONFIRMED)**: `Identifiers.fts_fund_account_id` and
`Identifiers.fts_account_type` are **never derived or stored by Payouts from its own DB** — they arrive fresh in
every FTS status-update payload and are passed straight through with no re-derivation:
- Wire origin: `TransferStatusWebhookForPayoutRequest.SourceAccountId` (int64) /
  `.BankAccountType` (string) (`transfer_status_webhook_request.go:24-25`).
- Copied verbatim into `StatusUpdateRequest.FtsFundAccountId`/`FtsAccountType` in all three status handlers at
  `payouts/internal/app/payouts/fts_transfer_status_webhook.go:515-521,575-581,629-635`:
  `strconv.FormatInt(transferStatusWebhookRequest.SourceAccountId,10)` and `.BankAccountType` copied
  as-is — **no lookup, no validation against any stored value**.
- For `processed` on Shared accounts, these are persisted to a `payout_meta_temporary` row
  (`core.go:2532-2552`, key `FtsInfoMeta`) and the actual ledger call is deferred to the async worker job
  `LedgerProcessedEventFailureType`, which re-reads that meta row
  (`asyncFailureHandlingHelper.go:471-513,499`, `payoutMetaTemporary.GetMetaValue()["fund_account_id"]`/
  `["account_type"]`) and passes it into `CreateTransactionViaLedger` (`:504-510`).
- For `reversed`, same FTS-supplied values used identically; for `failed`, empty strings are passed
  (`asyncFailureHandlingHelper.go:600-604`) — consistent with `createLedgerJournalRequest` only populating
  `Identifiers.fts_*` for the processed/reversed/cc-debit event families (§2).
- Before being sent, `FtsAccountType` is **lower-cased** (`strings.ToLower(extraInfo.FtsAccountType)`,
  `ledger_journal_create.go:217`).

**Conclusion (load-bearing for twin fidelity)**: Ledger must resolve/have pre-registered an account whose entity
keys match `(banking_account_id=<signed bank account>, fts_fund_account_id=<value>, fts_account_type=<lowercased
value>)` for the FTS-payable leg of the `payout_processed`/`payout_reversed` journal. If a twin's FTS-stub/mozart-sim
returns a `source_account_id`/`bank_account_type` that was never seeded as a matching Ledger account entity, Ledger's
account-discovery for that leg fails at journal-create time — this is the exact mechanism the ENV2_BUILD_STATUS V06
class of failure would hit **if** the seeding were wrong. Twin comparison (below) found the arena's `ledger.sql`
actually gets this specific alignment right (900001/900002 `fts_fund_account_id` values match `fts.sql`'s seeded
`source_account_id`s, and the lowercase `"nodal"` matches `strings.ToLower`) — so this exact failure mode is **not**
what's causing V06 in the current arena; the mechanism itself is confirmed correct and load-bearing for any *future*
reseed.

**Stork events per status** (CONFIRMED, `payouts/internal/app/common/appConstants/webhooks.go:4-24`,
`StatusToWebhookEventMap`, fired via `FireWebhookEventAsyncForPayout` from state-machine `Enter` hooks):
`created`→`payout.initiated`, `processed`→`payout.processed`, `reversed`→`payout.reversed`, `failed`→`payout.failed`,
`queued`/`on_hold`→`payout.queued` (both share one event), `rejected`→`payout.rejected`, `pending`→`payout.pending`.
Generic detail/UTR updates fire `payout.updated` directly (`core.go:2049,2218`), not through the state machine.
`cancelled` is **absent** from the map — cancel produces no merchant Stork webhook at all (confirmed by grep
absence). A separate event, `transaction.created` (`appConstants.EventTransactionCreated`,
`payouts/internal/app/common/appConstants/events.go:11`), fires from `PushTransactionEvent`
(`reversalViaLedgerService.go:215-312`) after every successful `failed`/`reversed` ledger journal — a distinct
"money moved" notification, not the payout-lifecycle webhook.

**Kafka consumers** (topic names CONFIRMED from `config/prod.toml`, cross-checked against `config/devstack.toml`):

| Consumer task (`PAYOUTS_CONSUMER_TASK_NAME`) | Kafka topic | Consumer group | MaxRetry/Backoff (prod) | MaxRetry/Backoff (devstack) |
|---|---|---|---|---|
| `fts_status_updates` | `rx-fts-status-update-events` | `rx-payouts-fts-status-update-consumer-group` | 1 / 1s | 1 / 1s |
| `fts_status_updates_retry` | `rx-fts-status-update-retry-events` | `rx-payouts-fts-status-update-retry-consumer-group` | 2 / 30s | 2 / 5s |

(`config/prod.toml:311-342,362-363`; `config/devstack.toml:275-305` — devstack's retry backoff, 5s, differs from
prod's 30s; a minor environment-specific config difference, not a contradiction.) `[consumer_task] name="fts",
MaxConcurrency=3` (`devstack.toml:307-309`) is a **separate, older config axis** (`ConsumerTask` struct) coexisting
in config with the `Task`/`[kafka_consumers]` block that `consumer_provider.go` actually reads for topic/group
resolution — its live purpose was not fully resolved (flag in Cannot-derive list).

**Message shape**: both `StatusUpdateRequest` and `DetailsUpdateRequest` are unmarshaled from the **same** Kafka
message body (`internal/taskHandlers/fts_status_updates.go:42-54`) — one message carries both structs' fields
simultaneously.

**Which statuses are dropped and why**: lower-cased status ∈ {`reversed`,`failed`} → `return nil` immediately
(`fts_status_updates.go:56-60`), comment: *"api code has very tight coupling between api, payouts service and ledger
for handling reversed updates. So currently api will be the source of truth for reversed status."* A
`RecordNotFound` from the details-update sub-call is swallowed (`:67-70`, "ensure for api payouts we don't push in
retry topic") — any other error triggers the retry-republish below.

**Retry-topic semantics**: on handler failure, `HandleFailedMessage` (`fts_status_updates.go:92-113`) republishes the
**entire raw message**, keyed by `SourceID`, to `Topics.FTSStatusUpdatesRetry` — **no DLQ, no cap** on how many
times a message keeps cycling beyond the consumer-level MaxRetry counts above.

**Dedupe mechanism**: **no DB/idempotency-flag dedupe** in the Kafka path itself — it relies on the same per-payout
Redis mutex (`AcquireResource(ResourcePayout+SourceID,30s)`) used by the HTTP webhook path, so the two entry points
serialize against each other rather than deduping independently.

---

### 6. Cron routes and worker/consumer inventory

**Full `/v1/cron/*` route table** (`payouts/internal/routing/router/cron_routes.go:1-202`, full file read,
`BasicAuth(cred.FastCron)` only, no passport — 24 endpoints, confirmed exhaustive):

| # | Method | Path | Handler | What it drains/does | Payout-path critical? |
|---|---|---|---|---|---|
|1|POST|`/redis_key_set`|`AdminClient.RedisKeySet`|Admin utility, arbitrary Redis key set|No|
|2|POST|`/banking_account_statement/fetch/initiate`|`BankingAccountStatementService.InitiateBankingAccountStatementFetch`|Triggers `rbl_banking_account_statement` job|No (recon)|
|3|POST|`/process_queued_payouts`|`ProcessQueuedPayouts.ProcessQueuedPayouts`|Partner-bank-on-hold recovery (not low-balance, see §4)|**Yes**|
|4|POST|`/process_batch_submitted_payouts`|`PayoutService.InitiateBatchSubmittedPayouts`|Releases NEFT/RTGS bulk rows parked in `batch_submitted`|**Yes**|
|5|POST|`/process_beneficiary_bank_on_hold_payouts`|`PayoutService.ProcessDispatchForOnHoldPayouts`|Bene-bank on-hold dispatch + SLA-breach cancels|**Yes**|
|6|POST|`/process_queued_low_balance_payouts`|`PayoutService.ProcessInitiateForQueuedPayouts`|Low-balance dequeue (6h sweep, §4)|**Yes**|
|7|POST|`/process_inflight_reservation_reconciliation`|`PayoutService.ProcessReconciliationForInFlightReservations`|Rebuilds Redis reservation state, refreshes heartbeat|**Yes**|
|8|POST|`/process_scheduled_payouts`|`PayoutService.InitiateScheduledPayouts`|Dispatches due scheduled payouts|**Yes**|
|9|POST|`/payouts_dual_write_failure_processing`|`PayoutService.PayoutsDualWriteFailureProcessing`|Retries PS→monolith dual-write pushes|No (migration)|
|10|POST|`/reverse_dual_write`|`ReverseDualWrite.ReverseDualWrite`|Pulls monolith-side changes into PS DB|No (migration)|
|11|POST|`/load_test/run`|`LoadTestService.RunLoadTest`|Synthetic payout creation (non-prod)|No|
|12|POST|`/merchant_configuration`|`MerchantConfigurationService.CreateMerchantConfiguration`|Creates a `merchant_configurations` row|No (config admin)|
|13|POST|`/payouts_sla_breach_monitor`|`SLAMonitorService.CheckSLABreaches`|TiDB query, Prometheus gauges only, no repair|No (observe)|
|14|PUT|`/merchant_configuration`|`MerchantConfigurationService.UpdateMerchantConfiguration`|Updates a `merchant_configurations` row|No|
|15|POST|`/query_db`|`InternalActions.ExecuteSelectQuery`|Raw SELECT-only|No|
|16|POST|`/tidb_data_consistency_check`|`InternalActions.CheckTiDbDataConsistency`|Observe-only|No|
|17|POST|`/elasticsearch/backfill_index`|`PayoutService.BackfillElasticsearchIndex`|ES index backfill|No|
|18|GET|`/payouts/fetch_multiple`|`PayoutService.FetchMultiplePayouts`|Read-only|No|
|19|POST|`/elasticsearch/query`|`ElasticsearchService.ExecuteQuery`|Raw ES query|No|
|20|PUT|`/elasticsearch/mapping`|`ElasticsearchService.ExecutePutMapping`|Set ES mapping|No|
|21|GET|`/elasticsearch/mapping`|`ElasticsearchService.ExecuteGetMapping`|Read ES mapping|No|
|22|PUT|`/log_sampling`|`LogSampler.SetLogSamplingRates`|Log-sampling config|No|
|23|GET|`/log_sampling`|`LogSampler.GetLogSamplingRates`|Read log-sampling config|No|
|24|POST|`/fund_management_payouts/check`|`FundManagementPayout.CronCheck`|Dispatches `fund_management_payout_check` per merchant|No (FMP top-up, not payout-dispatch)|

Confirms Confirmed-2 in `ARCHITECTURE_DELTA.md` (FastCron SaaS, not k8s CronJob) — this repo carries no cadence
values for any of the 24; the schedule is external (Cannot-derive list).

**25 worker job configs vs. 24 deployed workers — a genuine discrepancy** (CONFIRMED): `internal/job/*.go` has 26
files minus `base.go` = **25 job configs**, one `worker.Job{Name,QueueName,MaxRetries,Timeout}` each, matching
`internal/config/config.go:260-291`'s 25-field `Job` struct. One worker process serves exactly one job, selected by
env `PAYOUTS_WORKER_NAME` (`internal/boot/handler.go:808`, unregistered name panics). `kube-manifests/templates/payouts/templates/`
has only **24** `payouts-worker-*-live.yaml` Deployments. Reconciling the two lists:

| Job name (in code) | Queue config key | MaxRetries/Timeout | Purpose | Critical? |
|---|---|---|---|---|
|`transaction_create`|`TransactionCreate`|5/120s|Post-create dispatch orchestration|**Critical**|
|`schedule_payout`|`SchedulePayout`|5/300s|Dispatch one due scheduled payout|**Critical**|
|`queued_payout`|`QueuedPayout`|5/120s|Dequeue/retry one low-balance-queued payout|**Critical**|
|`on_hold_payout`|`OnHoldPayout`|5/120s|Process/cancel one bene-bank on-hold payout|**Critical**|
|`partner_bank_hold_payouts`|`PartnerBankOnHoldPayout`|5/120s|Process one partner-bank/gateway-down on-hold payout|**Critical**|
|`payout_create_failure_handling`|`PayoutCreateFailureHandling`|3/120s|Async retry, create-path failures|**Critical**|
|`payout_update_failure_handling`|`PayoutUpdateFailureHandling`|3/120s|Async retry, status-update ledger failures|**Critical**|
|`batch_submitted_merchants`|`BatchSubmittedMerchants`|5/120s|Release one batch-submitted (NEFT/RTGS) group|**Critical** (bulk)|
|`bulk_payouts`|`BulkPayouts`|5/120s|Bulk row processing|Bulk (secondary)|
|`webhook_event`|`WebhookEvent`|3/120s|Deliver one merchant webhook to Stork|Peripheral|
|`source_updater` (registered name `payout_source_updater`)|`SourceUpdater`|5/120s|Push SNS source-type broadcast|Peripheral|
|`data_consistency_event`|`DataConsistencyEvent`|5/120s|ES sync/consistency event|Peripheral|
|`data_consistency_checker`|`DataConsistencyChecker`|3/30s|Batch consistency comparison|Peripheral (observe)|
|`async_dual_write`|`AsyncDualWrite`|5/120s|PS→monolith dual-write push|Peripheral (migration)|
|`rbl_banking_account_statement`|`RblBankingAccountStatement`|7/1800s|Fetch/process RBL bank statement|Peripheral (recon)|
|`payout_usage_event_processing`|`PayoutUsageEventProcessing`|5/120s|Usage/billing event|Peripheral|
|`fts_async_processing`|`FtsAsyncProcessing`|5/120s|Async FTS follow-up / `create_fta` call site|**Critical**|
|`fts_async_hv_processing`|`FtsAsyncHvProcessing`|5/120s|Same handler, isolated high-value queue|**Critical**|
|`generic_processing`|`GenericProcessing`|5/120s|Generic catch-all|Peripheral|
|`x_balance_payouts_event`|`XBalancePayoutsEvent`|5/120s|x-balances-originated payout event (CORRECTION: singular "balance" in the constant, prior lane 02 wrote `x_balances_payouts_event` plural)|Peripheral|
|`api_queue_for_async_dual_write_direct_push`|`ApiQueueForAsyncDualWriteDirectPush`|5/120s|Splitz-gated direct-push dual-write|Peripheral (migration)|
|`x_account_statement_source_event`|`XAccountStatementSourceEvent`|5/120s|Account-statement source ingestion|Peripheral (recon)|
|`fund_management_payout_check`|`FundManagementPayoutCheck`|3/300s|Evaluate FMP top-up need|Peripheral (FMP)|
|`fund_management_payout_initiate`|`FundManagementPayoutInitiate`|3/100s|Create one FMP payout chunk|Peripheral (FMP)|
|`x_balances_balance_refresh`|`XBalancesBalanceRefresh`|3/30s|Release in-flight reservation holds|**Critical** (reservation release)|

**Deployed-but-not-in-code** (present as `-live.yaml` Deployments, `PAYOUTS_WORKER_NAME` values with **no matching
`Name:` constant found anywhere in `internal/job/*.go` this pass**): `mail_and_sms_event`, `update_source_event`.
**In-code-but-not-deployed**: `x_balance_payouts_event`, `x_account_statement_source_event`,
`api_queue_for_async_dual_write_direct_push`. Net 24 deployed vs. 25 registered — not a clean 1:1, contrary to
`ARCHITECTURE_DELTA.md` §W9's implied match. Could not resolve whether this is kube-manifests drift (renamed/retired
jobs at a different commit) or something not visible to static grep — flagged in Cannot-derive list.

---

### 7. Bulk, admin/repair, and approval routes

**Bulk payout wiring — resolves prior lanes' open question definitively**: `POST /v1/payouts/bulk` →
`BulkPayoutsProcessorService.CreateBulkPayouts` (`payout_internal_routes_with_passport.go:23-27`, auth
`BasicAuth(cred.API,Workflow)` + `PassportAuthentication([]string{})`) → `bulkPayoutsController.go:39` →
`bulkPayoutsProcessor.Service.CreateBulkPayouts` (`service.go:32`). **Both `CreateBulkPayouts` (sequential) and
`CreateBulkPayoutsConcurrent` (goroutine pool) are live — selected per-request** via Splitz
`SplitzExperimentList.BulkPayoutConcurrencyExperiment` (`service.go:59-86,152-162`,
`isBulkPayoutConcurrencyEnabled`, evaluated per merchant): `true`→Concurrent, `false`→sequential. This is not a
build-time choice; it is Splitz-gated per request. `MaxBulkPayoutsLimit = 15` rows/request
(`bulkPayoutsProcessor/constants.go:6`) — this is the row cap on **this** endpoint, far below the ≤50,000-row file
upload limit from lane 15 row 28 (xperience chunks a file into ≤15-row calls here).

**`bulk_approve` — CONFIRMED ABSENT from this repo.** Grepped every `"/bulk` literal and every `approve` occurrence
in `internal/routing/router/*.go`: only `payout_bulk_routes.go`'s `/v1/payouts/bulk/validate` (validation only) and
the single-payout `POST /v1/payouts/payouts_internal/:payout_id/approve` exist. `ARCHITECTURE_DELTA.md`'s claim that
"Batch's `payout_approval` batch type calls `POST payouts/bulk_approve` directly on Payouts Service" (sourced from
the `batch` repo) has **no corresponding route here** — see Corrections table.

**Admin/repair routes — full contract-level table** (all files read completely this pass):

`/v1/admin/*` (`payout_admin_routes.go:13-52`, `BasicAuth(cred.API)`+`Passport(LegacyAuthTypeAdmin)`):

| Method | Path | Handler | Mutation |
|---|---|---|---|
| POST | `/:entity/:id` | `AdminClient.GetEntityById` | generic cross-entity read |
| POST | `/:entity` | `AdminClient.GetEntityMultiple` | generic cross-entity read, multi |
| POST | `/free_payout/:balance_id` | `AdminClient.UpdateFreePayoutAttributes` | free-payout counter admin write |
| GET | `/payouts/:balance_id/free_payout` | `AdminClient.GetFreePayoutAttributes` | read |
| PATCH | `/payouts_sync` | `AdminClient.PayoutDetailsSync` | force-sync payout_details from source |

`/v1/admin/internal_actions/query_db` (same admin auth): POST → `InternalActions.ExecuteSelectQuery`, SELECT-only.
`/v1/fund-management-payout/balance-config/merchants/:merchant_id` (`BasicAuth(cred.API,FastCron)`, **no Passport**):
GET/PUT `FundManagementPayout.GetConfig`/`SetConfig` — comment confirms caller is the API monolith admin dashboard.

`/v1/payouts_internal/*` and internal `/v1/payouts/*` (`payout_internal_routes.go`, full 216-line file,
`BasicAuth(cred.API,Workflow,Xperience,FTS,VendorPayments,Settlements,Irctc)`, **no Passport** — TODO comment
unchanged):

| Method | Path | Handler |
|---|---|---|
| POST | `/manual_action` | `AdminClient.ManualAction` — **exactly 3 cases, confirmed exhaustive this pass** (see below) |
| PATCH | `/update_payouts_with_fts` | `PayoutService.HandlePayoutStatusUpdateViaFTS` |
| POST | `/update_payouts_details_with_fts` | `PayoutService.HandlePayoutDetailsUpdateViaFTS` |
| PATCH | `/update_credit_transfer_payout` | `PayoutService.HandlePayoutUpdateFromCreditTransfer` |
| POST | `/scheduled/process` | `PayoutService.InitiateScheduledPayouts` |
| POST | `/balance_update_event` | `PayoutService.HandleBalanceChangeEvent` |
| POST | `/retry` | `PayoutService.RetryPayouts` |
| POST | `/retry/source_update` | `PayoutService.RetryPayoutSourceUpdate` |
| POST | `/payouts_internal/:payout_id/approve` | `PayoutService.ApprovePayout` |
| POST | `/payouts_internal/:payout_id/reject` | `PayoutService.RejectPayout` |
| GET | `/analytics` | `PayoutService.GetPayoutAnalytics` |
| POST | `/bene_bank_status_update` | `PayoutService.HandleBeneBankStatusUpdate` |
| POST | `/on_hold/process` | `PayoutService.ProcessDispatchForOnHoldPayouts` |
| POST | `/free_payout_migration` | `AdminClient.MigrateFreePayout` |
| POST | `/consistency_checker` | `PayoutService.InitiateDataConsistencyChecker` |
| POST | `/batch/process` | `PayoutService.InitiateBatchSubmittedPayouts` |
| POST | `/shield/evaluate` | `PayoutService.EvaluatePayoutShieldRules` |
| POST | `/duplicate_payout_evaluate` | `PayoutService.DuplicatePayoutEvaluate` |
| POST | `/banking_account_statement/payout_update` | `PayoutService.UpdatePayoutAfterBASRecon` |
| POST | `/rzp_fees_payout` | `PayoutService.PostFundAccountPayout` |
| POST | `/set_pricing_rule_info` | `PayoutService.SetRedisKeyForPricingRule` |
| GET | `/mapped_vpa/:upi_number` | `PayoutService.FetchMappedVpa` |
| POST | `/transfer_status_webhook` | `PayoutService.HandleTransferStatusWebhookForPayout` |
| GET | `xperience_get_payout/:id` (literal string as-written, missing leading `/`) | `PayoutService.GetPayout` |
| GET | `/fetch_multiple` | `PayoutService.FetchMultiplePayouts` |
| POST | `/elasticsearch/backfill_index` | `PayoutService.BackfillElasticsearchIndex` |
| GET | `/payouts_internal/:payout_id` | `PayoutService.GetPayoutByIdInternal` |
| POST | `/payout_internal` | `IdempotencyKey(PayoutService.FundAccountPayout)` |
| GET | `/inflight_reservations` | `PayoutService.InspectInFlightReservations` (read-only, see §4) |

`/v1/payouts/:payout_id/attachments` (`payout_proxy_routes.go`, `BasicAuth(cred.API)`+`Passport(LegacyAuthTypeProxy)`):
PATCH → `PayoutService.UpdateAttachmentsForPayout`.

**`ManualAction` switch — CONFIRMED exactly 3 cases, no more** (`internal/controllers/adminClientController.go:267-356`,
full function read): `"processed_to_processing"`, `"approve_workflow_payouts"`, `"reject_workflow_payouts"`,
`default`→400 "unsupported action". No hidden 4th+ case exists — **no admin action forces a stuck payout from
`initiated` to `failed`/`reversed`**; the repair-surface gap flagged in prior lane 13 is confirmed exhaustive, not
partial.

**Approval routes** (unchanged from prior lane 12 §A, re-confirmed): `POST /v1/payouts/payouts_internal/:payout_id/approve|reject`
(`payout_internal_routes.go:71-79`) same internal-service BasicAuth, no OTP/2FA in this repo. Workflow-callback
registration targets these two URLs by logical service name `payouts_live`
(`pkg/workflow/workflow_create.go:264-314`). `RejectPendingPayout`/`GetPayoutByPayoutId` fetch via **unscoped**
`repo.FindByID` (no merchant_id filter) — tenant-isolation caveat re-confirmed, prior lane 12 §G.

---

### 8. Outbound calls table

Confirmed from `config/prod.toml` (preferred) with client-code cross-check:

| Client | Route(s)/Host | Auth | Timeout | Retry | Circuit breaker (Hystrix) | Fail-open/closed | Evidence |
|---|---|---|---|---|---|---|---|
| API monolith | `https://api.razorpay.com/v1` (+ internal `prod-api-int.razorpay.com`) | Basic, env-sourced | not confirmed in `[api]` block | not shown | not shown | **Closed** for merchant-config/pricing fetch; masked-open for CFA-fallback | `prod.toml:111-116` |
| FTS | `https://fts-live.razorpay.com/v1` | Basic, env-sourced | 1000ms | 3, window 60ms, jitter 2ms | maxconcurrent 100, threshold 50%, CB timeout 10000ms | Closed (create failure fails payout create) | `prod.toml:590-606` |
| Ledger | `https://ledger-live.razorpay.com` | Basic `payouts_key` | **200ms** | 3 | maxconcurrent 100, threshold 50%, CB timeout 10000ms | **Closed** | `prod.toml:390-405` |
| CFA | `https://cfa-live-int.razorpay.com` | Basic, env-sourced | 10000ms | 3, window 60ms, jitter 2ms | maxconcurrent 100, threshold 50%, CB timeout 10000ms | **Masked-open** (falls back to monolith under `IsFetchFromMicroservicesBulkExperimentEnabled`) | `prod.toml:691-708` |
| x-balances | `https://x-balances-ext.razorpay.com` | Basic, env-sourced | 30s | 3, window 1s, jitter 100ms | maxconcurrent 100, threshold 50%, **CB timeout 1000ms** (far below the 30s call timeout) | Not re-traced this pass | `prod.toml:773-790` |
| Banking Account Service | `https://banking-account.razorpay.in/v0.2` | Basic `payouts` | 2000ms | 2, window 60ms, jitter 2ms | maxconcurrent 100, threshold 50%, CB timeout 10000ms | Not re-traced this pass | `prod.toml:252-269` |
| Stork | `https://stork.razorpay.com` (+ Twirp `serviceName=rx-live`) | Basic, env-sourced | not in this block | not in this block | not in this block | N/A (webhook delivery, never gates the payout) | `prod.toml:118-127` |
| Shield | `https://shield-payout-int.razorpay.com` | Basic, env-sourced | 200ms | not shown | maxconcurrent 100, threshold 50%, CB timeout **30000ms** | **Open** (any error/non-block → proceed) | `prod.toml:408-423` |
| DCS | SDK-internal per-env host resolution (no host in `[dcs]` prod block) | Basic `payouts` | n/a | n/a | n/a | `Mock=false` in prod | `prod.toml:549-553` |
| Splitz | `https://splitz.razorpay.com` | Basic, env-sourced | 200ms | not shown | maxconcurrent 100, threshold 50%, CB timeout 10000ms | **Open by construction** (every `IsSplitzExperimentOn` caller treats error as `false`/off) | `prod.toml:437-455` |
| Governor (ccsdk) | `https://governor.razorpay.com` | Basic, env-sourced | 1000ms | not shown | not shown | **Closed** (pricing failure fails `Process()`) | `prod.toml:617-627` |
| Charge Collections (ccsdk) | `https://charge-collections-live.razorpay.com` | Basic `payouts_key` | conn-pool 2000ms | 3, backoff 200ms, jitter 100ms | maxconcurrent 100, threshold **75%**, sleep 5000ms, CB timeout 30000ms | Closed (same pricing path as Governor) | `prod.toml:635-660` |
| UPS (payments-upi) | `https://payments-upi.razorpay.com` | Basic `payouts` | 3000ms | 3, window 100ms, jitter 2ms | maxconcurrent 50, threshold 50%, CB timeout 10000ms | Not re-traced this pass | `prod.toml:292-308` |
| Workflow Service | `https://workflows.razorpay.com` | Basic `payouts` | **100ms** | 3, window 60ms, jitter 2ms | maxconcurrent 100, threshold 50%, CB timeout 10000ms | Not re-traced this pass | `prod.toml:273-288` |
| Account Service (ASV, gRPC) | `asv-grpc.razorpay.com:443` | Basic, env-sourced | 30s | 3, window 1s, jitter 100ms | maxconcurrent 100, threshold 50%, CB timeout 10000ms | **Closed** for merchant-config-via-ASV path (ASV failure aborts the whole config fetch) | `prod.toml:670-688` |
| XAS (x-account-statements) | `https://x-account-statements-ext.razorpay.com` — CORRECTION: prior lane 02 flagged this client as "not found"; it exists, config key `[xas]` | Basic, env-sourced | 30s | 3, window 1s, jitter 100ms | maxconcurrent 100, threshold 50%, CB timeout 1000ms | Not gating (statement-match/source-event retry only) | `prod.toml:570-589` |
| Mail/SMS | No distinct client — handled entirely via Stork (`[stork.email]` templates) | — | — | — | — | N/A | `prod.toml:120-121`; prior lane 02 §26,28 |

---

## Corrections to existing reports

| Report | Claim | What source says | Evidence |
|---|---|---|---|
| Lane brief / prior reports | Sub-model table named "`payouts_status_details`" | Table is **`payout_status_details`** (singular) | `payouts/internal/database/migrations/20211026104754_create_payout_status_details_table.go:13` |
| `raw-findings/12_payouts_approval_queue_bulk.md` §B.3 | "No hard max-retry/expiry ceiling found for indefinitely-queued payouts" (flagged speculative) | Code for a 3-month queued→failed auto-expiry **exists** (`Payout.IsValidQueuedPayout`, checks `queuedAt+3mo < now` → fires `EventFailed`) but has **zero production call sites** anywhere in the repo outside its own unit test — it is dead/unreachable code, not merely absent | `payouts/internal/app/payouts/validation.go:45-70`; grep confirms only `validation_test.go:50,79` reference it |
| `ARCHITECTURE_DELTA.md` §(d), Flow C / Confirmed-1 | "Batch's `payout_approval` batch type calls `POST payouts/bulk_approve` directly on Payouts Service" | No `bulk_approve`/`/bulk_approve` route exists anywhere in `payouts` at this commit (exhaustive grep of every route file) — either the `batch` repo's config references a dead/renamed route, or Batch loops the single-payout `/payouts_internal/:payout_id/approve` route (consistent with the delta's own "5-row chunks" detail) | `payouts/internal/routing/router/*.go` (full grep, this pass) |
| `raw-findings/12_payouts_approval_queue_bulk.md` §A.6 (dual-naming bug) | Dual-naming mismatch limited to `PayoutWorkflows`/`SkipWorkflowForApi`/`SkipWfForPayroll` | The mismatch affects **at least 10 of ~20** dual-tracked flags across the ApiInterface/FundTransfer/Workflows DCS objects (see §3 table) — a materially broader class of bug than previously scoped, with no reconciliation experiment for 9 of the 10 pairs | `payouts/pkg/dcs/features/features.go:75-133` |
| `raw-findings/12_payouts_approval_queue_bulk.md` §D.4 | "Two near-duplicate implementations exist... which is actually wired to the live route was not confirmed" | **Both** are live, selected per-request by Splitz `BulkPayoutConcurrencyExperiment` (per-merchant evaluation) — not a fixed choice | `payouts/internal/app/bulkPayoutsProcessor/service.go:59-86,152-162` |
| `raw-findings/02_payouts_integrations.md` unresolved #7 / event table | XAS (x-account-statements) client "not read in depth"/location unconfirmed | Client is confirmed present, config key `[xas]`, host `x-account-statements-ext.razorpay.com` | `payouts/config/prod.toml:570-589` |
| `raw-findings/02_payouts_integrations.md` §3 event table | Job/queue named `x_balances_payouts_event` (plural "balances") | Actual registered job name is **`x_balance_payouts_event`** (singular) | `payouts/internal/job/x_balances_payouts_event.go` (constant, per cron/worker fork read) |
| `ARCHITECTURE_DELTA.md` §W9 | "kube-manifests carries 25 worker Deployments (one per job)" implying a clean 1:1 with the 25 job configs in code | kube-manifests has **24** `-live.yaml` Deployments; 2 deployed names (`mail_and_sms_event`, `update_source_event`) have no matching job constant in this payouts clone, and 3 in-code jobs (`x_balance_payouts_event`, `x_account_statement_source_event`, `api_queue_for_async_dual_write_direct_push`) have no matching Deployment — not a clean match | `kube-manifests/templates/payouts/templates/*-live.yaml` (cron/worker fork cross-check) |
| `CONTROL_AND_INVARIANT_CATALOG.md` C15/C16/C19 | Uses the term "MerchantBalance" as if it were a Payouts-Service-visible concept | The term does not appear anywhere in the `payouts` repo (grep confirmed) — it is a Ledger-repo accounting concept; Payouts only interacts via the generic `JournalCreateRequest` | Payouts repo-wide grep, this pass |
| `raw-findings/13_fts_payouts_status_path.md` finding 24 | "No admin action was found that forces a stuck payout to failed/reversed" (partial, "only 3 named actions read; a broader dispatcher may exist") | Confirmed **exhaustive**: the full `ManualAction` switch has exactly 3 cases, `default` is a 400 error — no broader dispatcher exists | `payouts/internal/controllers/adminClientController.go:267-356`, full function read |

---

## Twin comparison

| Component | Production mechanism | Twin mechanism (file) | Verdict | What differs | Controls |
|---|---|---|---|---|---|
| Status enum/transition table | 13-event FSM in `pkg/tranisiton`, `state_machine.go` | Not separately re-verified this pass against arena's payouts binary (same binary/image is run — the compiled state machine is identical code) | **REAL** | None expected — same Go binary | payout/transfer state |
| `payout_logs`/`payout_status_details` | Generated by `OnEvent` + FTS webhook handlers | Same code path; twin's golden run V14 ("no illegal transitions in payout_logs") **PASSED** | **REAL** | None found | payout state, idempotency |
| Merchant config source | Splitz-gated monolith-vs-ASV+DCS, 30-min Redis cache | `arena.toml` seeds DCS fixtures with **correct key format** (`rzp/x/merchant/payouts/{Workflows,FundTransfer,ApiInterface,direct_accounts/Configs,...}`), 3 merchant profiles (M1 shared, M2 direct/RBL w/ `in_flight_reservation_enabled=true`, M3 shared/workflow) | **CONTRACT-FAITHFUL** | Only 3 merchants seeded vs. the real merged monolith+ASV+DCS+API-DB-features 4-way merge; `merchant_config_via_asv_and_dcs_experiment` is seeded as a literal string ID, not the real (unknown) Splitz experiment ID — cosmetic, resolved by name in the stub | merchant config, routing |
| Splitz experiments | 38-field `SplitzExperimentList`, error/timeout → hardcoded `false` (fail to "off") | `splitz_fixtures.json` seeds only **8** named experiments; **`splitz-stub`'s own default for any UNSEEDED experiment ID/name is `"on"`** (`DEFAULT_VARIANT="on"`) | **INCORRECT** (for the ~30 unseeded experiments) | Production's fail-safe on error is `off`; the twin's fail-safe on *absence* (not error) is `on` — a materially different default that will silently enable ~30 unramped features/experiments (`ForDuplicatePayoutEvaluate`, `GoRoutineCappingExperiment`, etc.) that are off in prod | routing, approval, all Splitz-gated behavior |
| DCS reachability | gRPC+grpc-gateway `KVService`, `pkg/dcs/client.go` `GenerateOptions` (no `ServerURL` override support in the underlying `goutils/dcs@v1.7.3`) | `dcs-stub` is spec-complete but **an unpatched payouts binary cannot reach it** — `goutils/dcs`'s login-host resolution ignores `ServerURL` when exactly one Mode is configured; arena applies a build-time `ARENA_DCS_URL` module-replace patch to work around this (`ENV2_BUILD_STATUS.md` safety note #1) | **REPRESENTATIVE SUBSTITUTE, partially broken** | The patch's coverage is incomplete — V12 (in-flight reservation, which reads `in_flight_reservation_enabled` via DCS) still fails in the golden run, consistent with either (a) the patch not covering this exact code path, or (b) the reservation-reconciler cron never running (next row) | balance/reservation, routing |
| In-flight reservation | Redis-only counter+items keys, Lua `Reserve`, 5-min reconciler cron rebuilding from DB truth + fail-closed heartbeat | `GET /v1/inflight_reservations` inspection route exists and is called directly by verifier V12; but the **reconciler cron is never driven** by the twin's cron-driver (path mismatch, see below) | **INCORRECT** (missing scheduled behavior) | V12 fails; root cause is *either* DCS-flag unreachability *or* the reconciler never running *or both* — not resolved to a single cause from static reading | reservation, balance |
| Cron routes | 24 exact `/v1/cron/*` paths (§6 table) | `scripts/cron-driver/driver.py` drives most of them but with **3 confirmed path mismatches** and **1 missing** | **INCORRECT** (partial) | `driver.py` calls `/v1/cron/process_on_hold_payouts` (real: `/process_beneficiary_bank_on_hold_payouts`), `/v1/cron/reservation_reconcile` (real: `/process_inflight_reservation_reconciliation`), `/v1/cron/process_dual_write_failures` (real: `/payouts_dual_write_failure_processing`) — all 404 against the real binary and silently never fire; `/process_batch_submitted_payouts` is not driven at all, so NEFT/RTGS bulk rows never leave `batch_submitted` via cron in the arena | on-hold recovery, reservation reconcile, dual-write, bulk/batch dispatch |
| Queued low-balance dequeue | 6h `updated_at` filter on monolith's `balance` table (via `db.api`) + live `GET /internal_balances_queued` | V24's cron-endpoint-direct-call (bypassing cron-driver) still fails: "PS's dequeue reads the monolith VA balance (stub→ledger) and API-DB `balance.updated_at`" per `ENV2_BUILD_STATUS.md` | **INCORRECT** | The monolith-stub's balance response and/or the `balance.updated_at` freshness are not kept in step with what a real top-up would produce — twin-specific gap, separate from the cron-driver mismatches above | balance, payout state |
| Ledger `payout_processed`/`payout_reversed` account discovery (`fts_fund_account_id`/`fund_account_type`) | Identifiers sourced verbatim from the FTS webhook payload, must match a pre-registered Ledger account | `ledger.sql` **correctly** pre-seeds FTS-nodal accounts keyed by `fts_fund_account_id` 900001 (shared M1/M3) / 900002 (direct M2), matching `fts.sql`'s seeded `source_account_id`s; lowercase `"nodal"` matches `strings.ToLower` | **CONTRACT-FAITHFUL** (this specific identifier-alignment concern is NOT the cause of V06/V18 failures) | ENV2_BUILD_STATUS's own attribution (verifier races the live bank path / needs balance headroom) is the better-supported cause for V06/V10/V18 flakiness; the `ledger_config` rule rows themselves (`XPayoutInitiatedV2`/`XPayoutProcessedV2`) were noted in-seed as producing zero entries and were bypassed via `LedgerConfigAPI/CreateInBulk` in `scripts/up.sh` instead — that substitution path was not re-verified this pass | balance/ledger |
| FTS Kafka ingestion | Two consumers, exact topic names `rx-fts-status-update-events`/`rx-fts-status-update-retry-events` | Not independently re-verified against the arena's Kafka container topics this pass (out of this fork's scope) | UNKNOWN | — | payout/transfer state |
| Bulk/admin/approval routes | 15-row-limit `/v1/payouts/bulk`, 3-case `ManualAction`, no `bulk_approve` | Not exercised by the golden run's 24 verifiers (none target bulk/admin) | UNKNOWN (untested in twin) | — | approval, repair |
| Cron cadence | FastCron SaaS, external, not in any repo | `seeds/s4/cron_schedule.yaml` presumably encodes an assumed cadence for `cron-driver`'s own scheduling — not the real FastCron cadence, which cannot be reconstructed from any accessible repo | **REPRESENTATIVE SUBSTITUTE** (necessarily assumed) | — | timing-sensitive behavior (queue dwell time, SLA breach) |

---

## Recommendation: real vs substitute

| Component | Recommendation | Rationale |
|---|---|---|
| Payouts Service binary itself (API + 25 workers + 2 Kafka consumers) | **REAL** | Repo builds (`ENV2_BUILD_STATUS.md` confirms clean cross-compile + migrations); this is the system under test, not a dependency to substitute |
| Ledger | **REAL** | Already running in the arena; the specific `fts_fund_account_id`/`fund_account_type` identifier contract is now precisely documented (§5) — any reseed must keep `ledger.sql` account rows aligned with whatever FTS-stub `source_account_id`/`bank_account_type` values are used, in the exact lower-cased form |
| FTS / Mozart | **REAL** (mozart-sim) with fixed cron-driver paths | The remaining gap is orchestration (cron-driver path mismatches), not the FTS/Mozart substitute's fidelity |
| DCS | **REAL binary + patch**, patch coverage needs auditing | `dcs-stub`/real DCS reachability is only as good as the `ARENA_DCS_URL` module-replace patch's coverage — V12's failure suggests at least one code path (possibly the `in_flight_reservation_enabled` read specifically) still bypasses it |
| Splitz | **SUBSTITUTE, but fix the default-variant polarity** | Change `splitz-stub`'s default for an *unseeded* experiment ID/name from `"on"` to `"off"`, to match production's fail-safe-to-off semantics for both errors *and* absence — this is a one-line, high-leverage fix that currently makes ~30 unramped Splitz-gated behaviors silently active in the arena |
| Cron scheduling | **SUBSTITUTE (cron-driver)**, fix 3 path literals + add the missing batch-submitted trigger | Exact protocol: `POST` with `BasicAuth(FastCron)` to the 4 corrected literal paths in §6's table; no request body needed per the routes read |
| Monolith (balance/pricing/merchant-config legacy path) | **SUBSTITUTE**, contract now fully specified | Routes needed: `GET /internal/merchants/{id}`, `GET /internal_balances_queued`, `GET /merchant/on_hold_slas_internal`, `POST /payouts_service/fetch_pricing_info`, `POST /payouts_service/create_fta/` — all confirmed exact strings this pass; response shapes for the first three are in §3/§4 above |
| Workflow Service, Batch, Recon | Unchanged from prior lanes' recommendations (WFS **F3-capable real**, Batch **F0/F2**, Recon **F0/F2**) — this lane did not re-investigate them beyond confirming Payouts-side call sites | — |

---

## Synthetic data

| Family/table | Field | Source evidence | Type/length | Constraints | Allowed values | FK/relationships | State rules | Distribution matters? | Generation rule | Tier |
|---|---|---|---|---|---|---|---|---|---|---|
| `payouts` | `id` | `20200615182810_create_payouts_table.go:15` | char(14) | PK | 14-char alnum id, sign prefix `pout_` at app layer | — | — | No | random 14-char id per row | EXACT |
| `payouts` | `merchant_id` | same:18 | char(14) | NOT NULL | 14-char id | FK→merchant (external) | — | Yes — must match seeded merchant profiles (M1/M2/M3) | reuse fixed test merchant ids | EXACT |
| `payouts` | `balance_id` | same:19 | char(14) | nullable | — | FK→balance | must have a corresponding `balance` row with `updated_at` fresh enough for queued tests | Yes | seed alongside banking_account | EXACT |
| `payouts` | `method`,`mode` | same:20-21 | varchar(50) | method NOT NULL | mode ∈ {IMPS,NEFT,RTGS,UPI,AMAZONPAY,CARD,...} per `mode.go` maps | — | mode caps enforced at create (§2) | Yes — need one row per mode to exercise cap rules | one row per (mode,amount-at-boundary) | EXACT |
| `payouts` | `fund_account_id` | same:22 | char(14) | nullable | — | FK→CFA fund_account | active/inactive gate at create | Yes | one active + one inactive fund account | EXACT |
| `payouts` | `idempotency_key` | same:24 | varchar(255) | nullable | free text | — | uniqueness enforced via separate `idempotency_keys` table, not a column constraint here | No | random or omitted | REPRESENTATIVE |
| `payouts` | `amount` | same:28 | bigint(20) unsigned | NOT NULL | paise; min 100 (₹1), tiered max (§2) | — | mode caps, tier caps | Yes — boundary values needed | 1, 99, 100, 500000(IMPS boundary), 1000000000000(₹10cr) | EXACT |
| `payouts` | `currency` | same:29 | char(50) | NOT NULL | DTO allows INR/MYR/USD/EUR/GBP/SGD/AED; channel rule restricts non-INR/MYR to RBL | — | — | Yes | mostly INR, one non-INR/RBL combination | EXACT |
| `payouts` | `status` | same:33 | varchar(255) | NOT NULL | 15-value enum §1 | — | FSM in §1 | Yes — need at least one row per reachable status | one row per status | EXACT |
| `payouts` | `fts_transfer_id` | same:34 | int(11) | nullable | FTS-side id | FK→fts transfer (external) | — | No | sequential test ids | REPRESENTATIVE |
| `payouts` | `channel` | same:36 | varchar(255) | nullable | RBL/ICICI/AXIS/YESBANK/IDFC/SLICE for Direct; empty/Shared for VA | — | FtsToPayoutStatusMap keyed on this (§5) | Yes — need ≥1 Direct-RBL row and ≥1 Shared row to exercise the failed-remap difference | EXACT |
| `payouts` | `utr` | same:37 | varchar(255) | nullable | write-once-by-inequality (not strict) | — | set by FTS details-update | No | synthetic UTR string, clearly fake | REPRESENTATIVE |
| `payouts` | `queued_reason` | same:50 | varchar(255) | nullable | `beneficiary_bank_down`\|`low_balance`\|`gateway_degraded`\|`partner_bank_down` | — | §4 | Yes — need one row per reason to test each cron | EXACT |
| `payouts` | `status_details_id` | same:54 | char(14) | nullable | — | FK→`payout_status_details` | — | No | — | EXACT |
| `payout_details` | `queue_if_low_balance_flag` | `20210902153752_...go:18` | tinyint | default 0 | 0/1 | — | drives §4 gate | Yes | mix of 0/1 | EXACT |
| `payout_details` | `beneficiary_bank_code` | `payoutDetails/model.go:29` (NOT in the migration — schema drift, `ARCHITECTURE_DELTA.md` W10) | string (app-level only) | — | IFSC-like | — | — | No | — | **ASSUMED** — column must be added out-of-band (`ENV2_COMPOSE/seeds/schema-patches/payouts.sql` per W10) since no migration creates it |
| `payout_status_details` | `status`,`reason`,`description`,`mode`,`triggered_by` | `20211026104754_...go:13-24` | varchar(15/55/255/20/255) | status/reason/description/mode NOT NULL | FTS status strings, free-text reason/description | FK→payouts (`payout_id`) | one row per FTS status-update received | Yes — need multiple per payout to show history | EXACT |
| `payout_logs` | `event`,`from`,`to`,`mode`,`triggered_by` | `payoutLog/model.go:8-16` | varchar (untyped in Go, migration not read this pass) | — | event/from/to ∈ FSM vocabulary (§1); mode/triggered_by always literal `"system"` | FK→payouts | one row per `OnEvent` call | Yes | derive from the transition table directly — every row must be a legal (from,to) pair per §1 | EXACT |
| `idempotency_keys` | `idempotency_key`,`merchant_id`,`source_id`,`source_type`,`request_hash` | `20221004002554_...go:14-27` | varchar(255)/char(14)/char(14)/varchar(64)/varchar(255) | UNIQUE(idempotency_key,merchant_id) | — | `source_id`→payout id | — | Yes — need a duplicate-key-same-hash and duplicate-key-different-hash pair | EXACT |
| `bulk_idempotency_keys` | `idempotency_key`,`merchant_id`,`batch_id`,`source_id`,`source_type` | `20240830164301_bulk_idempotency_keys.go:15-27` | varchar(255)/char(14)/varchar(255)/char(14)/varchar(255) | UNIQUE(idempotency_key,merchant_id) | — | `batch_id`→batch | one row per bulk-create row | Yes | mirror `idempotency_keys` pattern per-row | EXACT |
| `reversals` | `payout_id`,`balance_id`,`amount`,`fees`,`tax`,`currency`,`channel`,`transaction_id`,`utr` | `20201113232006_...go:14-29` | char(14)/char(14)/bigint/int/int/char(50)/varchar(255)/char(14)/varchar(255) | NOT NULL on merchant_id/payout_id/amount/currency | `transaction_id` = ledger journal id, set post-hoc | FK→payouts, FK→ledger journal (external) | created before ledger call (§5); `transaction_id` null until ledger succeeds | Yes — need a row with `transaction_id` still null (failure-injected) | EXACT |
| `payout_meta_temporary` | `payout_id`,`meta_name`,`meta_value` | `20220616173226_...go:15-25` | char(14)/varchar(255)/JSON | — | `meta_name` ∈ {`FtsInfoMeta`,`dual_write_retry_exhaust`,...} | FK→payouts | transient — read once by async-failure-handling job then presumably deleted (deletion not traced) | No | seed one `FtsInfoMeta` row per in-flight-async-retry test case, JSON `{fund_account_id,account_type}` | REPRESENTATIVE |
| `merchant_configurations` | `entity_type`,`entity_id`,`name`,`status` | `20250311053125_...go:15-28` | varchar(50)/char(14)/varchar(255)/varchar(10) | UNIQUE(entity_type,entity_id,name); status default `'disabled'` | status ∈ enabled/disabled (only 2 values seen) | `entity_id`→merchant | admin-CRUD only (§6 cron routes 12,14) | No | one row per merchant per config name under test | EXACT |
| `counters` | `balance_id`,`free_payouts_consumed_last_reset_at`,`free_payouts_consumed`,`account_type` | `20220526144631_...go:14-23` | char(14)/int/int/varchar(255) | — | monthly IST reset | FK→balance | never rejects, only changes fee_type (lane 15 row 20) | No | one row per balance under free-payout test | REPRESENTATIVE |
| In-flight reservation (Redis only) | counter key, items key `payoutID→"amount:dispatchedAt:state"` | `reservation/store.go:227-241,150,160` | Redis string/hash | TTL 6h, max 2000 items/balance | state ∈ live\|awaiting_balance_refresh | keyed `{merchant_id:balance_id}` | released on terminal OnEvent (§4) | Yes | seed live Redis state directly for the reconciler test, not a DB row (**no DB table exists** — do not invent one in a twin) | EXACT (mechanism), no DB table |

---

## Cannot be derived from repositories

| Missing artifact | Why it matters | Likely owner | Minimal request | Schema-only/sanitized sufficient? |
|---|---|---|---|---|
| FastCron per-endpoint cadence (all 24 `/v1/cron/*` routes) | Twin's cron-driver interval is entirely assumed; wrong cadence changes queue-dwell-time-sensitive test outcomes (SLA breach, reservation reconcile staleness) | Platform/FastCron SaaS owner | Export of the FastCron job definitions for the `payouts` service (cron expression per endpoint) | Yes, cadence values only |
| Real Splitz variant values for all 38 `SplitzExperimentList` experiments in prod | Twin cannot know true on/off ramp state; currently defaults wrongly to "on" for unseeded ones (Twin comparison table) | Splitz admin / Payouts team | A sanitized export of `{experiment_id/name: current_variant}` for the 38 fields, or confirmation that "off" is the safe universal default to seed | Yes, values only |
| Real DCS values for the 28 payout-relevant fields, per representative merchant segment | Twin's 3 seeded merchant profiles are illustrative, not measured | DCS admin / Payouts team | Sanitized DCS `Get` dump for 3-5 representative real merchant ids across the 28 fields in §3 | Yes |
| Writer of the beneficiary-bank-down/up Redis map (IFSC-prefix keyed) | Reader-only code found in this repo; on-hold trigger cannot be reproduced faithfully without knowing the writer's key format/TTL/update cadence | Likely a bank-uptime-detector service, owner unknown | Identify the writer service, get its key format and write frequency | Sanitized contract description sufficient |
| `[consumer_task] name="fts", MaxConcurrency=3` vs. the `[kafka_consumers]`/`Task` block — which one is actually authoritative at runtime | Ambiguous dual config axis found in `config/devstack.toml`; could affect topic/group resolution in a reseeded environment | Payouts team (config owner) | A one-line confirmation of which struct (`ConsumerTask` vs `Task`) is read in the deployed prod config | Yes |
| kube-manifests 24-vs-25 worker / 2 unmatched Deployment names (`mail_and_sms_event`, `update_source_event`) vs 3 unmatched job configs | Cannot tell if this is drift (renamed/retired) or a real gap; affects which of the 25 job types the twin should actually run | Payouts team | `git blame`/history on `internal/job/` and `kube-manifests/templates/payouts/templates/` for those 5 names | Commit history (no data needed) |
| Exact `batch` repo literal for its "bulk approve" call (does it call a dead `bulk_approve` route, or loop `/approve`?) | Needed to resolve the `ARCHITECTURE_DELTA.md` correction definitively | Batch team | One grep of `batch` repo's payout-approval-batch-type HTTP call construction | Yes, code excerpt only |
| Real `ledger_config` rule rows for `payout_initiated`/`payout_processed`/etc (which entries actually decide debit/credit sign and account routing) | The twin's hand-transcribed `ledger_config` rows were abandoned in-seed ("produced ZERO ledger entries") in favor of a different bulk-load path not re-verified this pass | Ledger team | Schema-only export of `ledger_config` rows for the 4-6 payout-relevant transactor events | Yes, schema+redacted values |
| Monolith `fetch_pricing_info`/`internal/merchants/{id}` response full JSON shape (beyond the field names already confirmed) | Needed to build a fully contract-faithful monolith substitute | API monolith team | Sanitized sample response for 2-3 representative merchants/purposes | Yes |
| GORM association-save mechanics for `p.PayoutLog` slice → `payout_logs` DB rows | This pass confirmed the in-memory append but did not trace the actual persistence call (likely `repo.Update`'s association-save, or an explicit separate write) | Payouts team (or deeper repo read) | A grep of `payoutLogsCore`/`PayoutLog` write call sites in `internal/app/payouts/core.go` around `repo.Update`/`repo.Create` | No — derivable from repo alone with more budget; flagged only because this pass did not complete it |

---

## Fidelity tier verdicts

- **Payout state machine (FSM, statuses, transitions)**: REAL — same compiled binary runs in the twin; golden-run V14 (illegal-transition check) passed.
- **`payout_logs`/`payout_status_details` sub-models**: REAL — generated by the same code path as production.
- **Create pipeline validation (amount/mode/currency/contact)**: REAL — same code; verifier fixes (min 100 paise, real slots) already applied per `ENV2_BUILD_STATUS.md`.
- **Pricing (Governor/CC-SDK vs monolith `fetch_pricing_info`)**: CONTRACT-FAITHFUL SUBSTITUTE — `pricing_fixtures.json` stub exists but full order-of-precedence (§2) not independently re-verified against the stub's behavior this pass.
- **Merchant config resolution**: CONTRACT-FAITHFUL SUBSTITUTE — DCS key format and 3-merchant seed are correct in shape; real production values are UNKNOWN-BLOCKED (Splitz-MySQL-only, DCS admin-only).
- **Splitz experiment evaluation**: REPRESENTATIVE SUBSTITUTE, with a **known-wrong default polarity** (unseeded→"on" vs prod's fail-to-"off") — single highest-value fix identified.
- **DCS reachability**: REPRESENTATIVE SUBSTITUTE, partially broken — the `ARENA_DCS_URL` patch does not (yet, per V12) cover every code path that reads a DCS-backed feature flag.
- **In-flight reservation**: INCORRECT (in current arena state) — reconciler cron never fires (cron-driver path bug) and/or DCS flag unreachable; mechanism itself (Redis keys, Lua reserve, release hook) is REAL code, just not exercised end-to-end.
- **Queued/low-balance dequeue**: INCORRECT — monolith-stub balance freshness not aligned with what a real top-up produces (V24 failure, independent of the cron-driver bug).
- **Scheduled payouts**: REAL — golden-run V24 (scheduled-payout dequeue) passed with real slots + time travel.
- **On-hold (bene-bank / partner-bank)**: UNKNOWN-BLOCKED for the bene-bank-down write path (writer not in any accessible repo); partner-bank path is REAL (FTS health webhook exercised elsewhere in this pass's fork evidence).
- **Ledger `payout_processed`/`payout_reversed` account discovery (`fts_fund_account_id`/`fund_account_type`)**: CONTRACT-FAITHFUL SUBSTITUTE — the exact identifier contract is now fully documented and the twin's current seed is correctly aligned to it; this was previously a suspected root cause and is now cleared.
- **FTS Kafka ingestion (topic names, retry semantics)**: REAL topic names now confirmed from `config/prod.toml`; twin-side Kafka topic wiring UNKNOWN (not independently re-verified in the arena's own Kafka containers this pass).
- **Cron routes / cadence**: cron **routes** are REAL (24 exact paths documented); cron **driver** (arena orchestration) is INCORRECT for 3 of them (wrong paths) and missing 1; cron **cadence** itself is UNKNOWN-BLOCKED (FastCron SaaS, no repo has it).
- **Worker job inventory**: REAL in code (25 configs, all traced); kube-manifests deployment list has a 24-vs-25 discrepancy that is UNKNOWN-BLOCKED without commit history.
- **Bulk payouts (`/v1/payouts/bulk`, Concurrent-vs-sequential)**: REAL — both code paths traced and their Splitz-gated selection resolved; not exercised by any current golden-run verifier (UNKNOWN as tested, but CODE-CONFIRMED as documented).
- **`bulk_approve`**: does not exist in this repo — any twin/monolith substitute claiming to call it is building against a route that isn't real; UNKNOWN-BLOCKED pending the `batch` repo's actual call construction.
- **Admin/repair routes (`ManualAction`, `/v1/admin/*`)**: REAL, and now confirmed exhaustive (3-case switch, no hidden repair path) — not exercised by any golden-run verifier.
- **Outbound client contracts (timeouts/retries/CB/fail-open-closed)**: REAL, all values read directly from `config/prod.toml` for every listed client except Stork/DCS/Splitz/Governor/UPS/Workflow/x-balances/BAS's specific failure-path behavior, which was not independently re-traced into the call sites this pass (config-only CONFIRMED, behavior INFERRED from adjacent lanes).
