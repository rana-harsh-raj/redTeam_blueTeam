# RazorpayX Payouts — Approval/Maker-Checker, Balance-Queue, Scheduled/On-Hold, Bulk, Cancel/Reject, Reversal, Tenant Isolation

Repo: `razorpay/payouts` @ `4bf3dbf9239feadea6d65ca90c893a988e116173`
Clone: `/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture/payouts`
Scope: lane 12 only (approval/maker-checker, low-balance queueing, scheduled/on-hold, bulk, cancel/reject, reversal, tenant isolation). Routes, state machine table, outbound clients, and cron endpoint inventory were mapped by prior lanes (see `01_payouts_core.md`, `02_payouts_integrations.md`) and are not repeated here except where load-bearing for this lane's decision trees.

---

## A. APPROVAL / MAKER-CHECKER

### A.1 Create-path decision tree

| Step | File:Line (symbol) | Note |
|---|---|---|
| 1. Entry | `internal/controllers/payoutController.go:120` (`PostFundAccountPayout`) | HTTP handler |
| 2. Processor | `internal/app/payouts/processor/base.go:83` (`BasePayoutProcessor.CreatePayout`) | orchestrates create |
| 3. Gate | `internal/app/payouts/processor/baseHelper.go:107-155` (`IsWorkflowApplicable`) | decides workflow yes/no |
| 4. If applicable | `internal/app/payouts/processor/base.go:256-300` | calls `HandleWorkflowsIfApplicable`, returns early, no dispatch |
| 5. Pending | `internal/app/payouts/processor/baseHelper.go:83-91` | `payout.OnEvent(ctx, appConstants.StatePending)` |

Claim: `CreatePayout` short-circuits and returns without bank dispatch once a workflow is activated.
File: `internal/app/payouts/processor/base.go:256-300`
Excerpt:
```go
if IsWorkflowApplicable == true {
    isWorkflowActivated, err := b.HandleWorkflowsIfApplicable(payout, *params)
    ...
    } else if isWorkflowActivated == true {
        return payout, nil
    }
```
Confidence: confirmed
Capability: route

### A.2 Workflow service target — separate service, not the API monolith (in real environments)

Claim: The workflow client calls a Twirp RPC namespaced `rzp.workflows.workflow.v1.WorkflowAPI` — a dedicated Workflow Service ("WFS"), distinct from the API monolith.
File: `pkg/workflow/workflow_create.go:18` (`WfCreate`)
Excerpt:
```go
const (
    WfCreate = "/twirp/rzp.workflows.workflow.v1.WorkflowAPI/Create"
```
Confidence: confirmed
Capability: transform

Claim: Host is config-driven per environment (`internal/provider/workflow_client.go:24-49`, `[workflow]` in `config/*.toml`). Prod/stage/devstack/bvt/e2e all point at a distinct `workflows.*razorpay.*` domain — only **local dev** collapses `workflow.host` to the same generic stub (`http://0.0.0.0:28080/v1`) as the `[api]` client, which is a local-dev coincidence, not evidence prod calls the API monolith.
File: `config/prod.toml:274` vs `config/default.toml:134-136,275-276`, `config/sample.toml:218`
Confidence: confirmed
Capability: read

Claim: At workflow-creation time, this repo registers three callback URL/service pairs with WFS: `/v1/workflow/state` and `/v1/workflow/state/%s` (state-map create/update, same service) plus `/v1/payouts/payouts_internal/{payoutId}/approve` and `.../reject`, addressed by logical service name `payouts_live` (WFS resolves the name itself, not a hardcoded host).
File: `pkg/workflow/workflow_create.go:264-314` (`getCallbackDetails`)
Excerpt:
```go
Approved: StateCallbackRequest{
    Method: "post", Service: ServiceCallback + LiveMode,
    UrlPath: WfCallbackPath + payout.GetID() + "/approve", ...},
Rejected: StateCallbackRequest{
    Method: "post", Service: ServiceCallback + LiveMode,
    UrlPath: WfCallbackPath + payout.GetID() + "/reject", ...},
```
Confidence: confirmed
Capability: transform

### A.3 Approve/reject callback endpoints and auth

Claim: `POST /v1/payouts/payouts_internal/:payout_id/approve` → `PayoutService.ApprovePayout`; `.../reject` → `RejectPayout`, guarded by multi-credential BasicAuth (`cred.API, cred.Workflow, cred.Xperience, cred.FTS, cred.VendorPayments, cred.Settlements, cred.Irctc`) — no passport/JWT, no OTP/2FA in this repo.
File: `internal/routing/router/payout_internal_routes.go:12-18,69-80`
Excerpt:
```go
middleware.BasicAuth(cred.API, cred.Workflow, cred.Xperience, cred.FTS, cred.VendorPayments, cred.Settlements, cred.Irctc),
{http.MethodPost, "/payouts_internal/:payout_id/approve", controllers.PayoutService.ApprovePayout, nil},
{http.MethodPost, "/payouts_internal/:payout_id/reject", controllers.PayoutService.RejectPayout, nil},
```
Confidence: confirmed
Capability: route / authorize

Claim: A code comment confirms WFS is the caller and retries on non-2xx — direct in-repo evidence approval genuinely happens in a separate service.
File: `internal/app/payouts/core.go:5156-5159`
Excerpt:
```go
// if we return err from here, it will be retried from WFS and this can lead to processing same payout multiple
// times. Hence not returning err. WFS won't retry if 200 is sent as status.
```
Confidence: confirmed
Capability: observe

Claim: `RejectPendingPayout`/`GetPayoutByPayoutId` fetch the payout via the **unscoped** base `repo.FindByID` (ID only, no `merchant_id` filter) — see section G for the tenant-isolation implication of this internal route group.
File: `internal/app/payouts/core.go:4939-4958`
Confidence: confirmed
Capability: read

### A.4 State transitions on approve / reject / expire

Claim: There is no dedicated "approve" event — approval simply resumes normal create-processing (`ProcessApprovePayout` re-enters `ProcessPostCreatePayout`/`AsynchronousPostPayoutCreateProcessing`, the same processor path used at initial creation).
File: `internal/app/payouts/core.go:5108-5163` (`ProcessApprovePayout`), `3275-3294`
Excerpt:
```go
if c.IsFetchFromMicroservicesEnabled(ctx, payout.GetMerchantID()) {
    err = c.AsynchronousPostPayoutCreateProcessing(ctx, *payout, queueIfLowBalance)
} else {
    err = c.ProcessPostCreatePayout(ctx, *payout, queueIfLowBalance)
}
```
Confidence: confirmed
Capability: transform

Claim: `EventRejected` is a real state-machine event, `pending → rejected` only.
File: `internal/app/payouts/state_machine.go:432-434`
Excerpt:
```go
sm.Event(appConstants.EventRejected).To(appConstants.StateRejected).From(appConstants.StatePending).
    Before(LogBeforePayoutStateChange(...)).After(LogAfterPayoutStateChange(...))
```
Confidence: confirmed
Capability: transform

Claim: No `EventExpired`/expiry transition exists anywhere in `state_machine.go` or `payoutWorkflow.go` — workflow expiry is either handled entirely inside WFS with no callback into this repo, or uses a differently-named event not found by grep.
File: n/a (absence across `internal/app/payouts/state_machine.go`, `internal/app/payouts/processor/payoutWorkflow.go`)
Confidence: probable (absence claim)
Capability: observe

### A.5 Audit / mapping tables written

| Table | Migration file:line | Written by |
|---|---|---|
| `workflow_state_map` | `internal/database/migrations/20220912025953_create_workflow_state_map_table.go:14-27` | `internal/app/workflow/stateMap/core.go:61-102,150-176` (Create/Update), on `POST /v1/workflow/state` / `PATCH /v1/workflow/state/:id` |
| `workflow_entity_map` | `internal/database/migrations/20210824110739_create_workflow_entity_map_table.go:14-24` | on workflow creation |
| `payout_logs` | `internal/app/payoutLog/model.go:21` | every `state_machine.go` event via `Before`/`After` hooks (`LogBeforePayoutStateChange`), incl. reject |

Claim: `stateMap.Core` dual-writes state-map updates forward to the API monolith, swallowing errors from that forward call by design.
File: `internal/app/workflow/stateMap/core.go:181-239` (`ForwardStateCreateCallbackToAPI`)
Confidence: confirmed
Capability: persist

### A.6 pkg/dcs — cache/propagation-bug evidence

Claim: `pkg/dcs/service.go`'s `GetEnabledFeatures`/`IsFeatureEnabled` are live RPC calls with **no local cache/TTL** inside `pkg/dcs` itself.
File: `pkg/dcs/service.go:20-41`
Confidence: confirmed
Capability: read

**DCS / feature-name table** (dual naming: legacy `appConstants` string vs `pkg/dcs/features` DCS storage field name):

| Slack-reported flag | `pkg/dcs/features/features.go` constant | `internal/app/common/appConstants/features.go` constant | Notes |
|---|---|---|---|
| `payout_workflows` | `PayoutWorkflows = "enable_payout_workflow"` | `PayoutWorkflows = "payout_workflows"` | different string values under the same Go identifier name — the actual "dual naming" bug surface |
| `enable_payout_workflow` | (same as above) | — | this is the DCS-side storage name for `PayoutWorkflows` |
| `skip_approval_workflow_for_api` | `SkipWorkflowForApi = "skip_approval_workflow_for_api"` | `SkipWorkflowForApi = "skip_workflow_for_api"` | mismatched strings again |
| `skip_workflow_for_dashboard` | `SkipWfAtPayout = "skip_workflow_for_dashboard"` | `SkipWfAtPayout` (legacy name differs) | |
| `skip_workflow_for_payroll` | `SkipWfForPayroll = "skip_workflow_for_payroll"` | `SkipWfForPayroll = "skip_wf_for_payroll"` | mismatched strings |
| `enable_approval_via_oauth` | `EnableApprovalViaOauth = "enable_approval_via_oauth"` | `EnableApprovalViaOauth = "enable_approval_via_oauth"` | matches |

File: `pkg/dcs/features/features.go:45-49`, `internal/app/common/appConstants/features.go:26-46`. Confidence: confirmed. Capability: read.

Claim: Which name-set `IsWorkflowApplicable` actually checks is itself gated by a Splitz experiment (`SplitzExperimentList.PayoutWorkflowsDcsNameExperiment`, e.g. `config/prod.toml:493 = "Sg7tEnMoxCbTzM"`) — when enabled, both legacy and DCS names are OR'd; otherwise only legacy.
File: `internal/app/payouts/service.go:1475-1489`, `internal/app/payouts/processor/baseHelper.go:126-135`
Confidence: confirmed
Capability: authorize

Claim: **Strongest in-repo candidate for the reported "cache/propagation bug"**: `MerchantConfig` (holding the resolved feature map) is cached in Redis 30 minutes flat (`internal/app/merchant/core.go:30-31`). `UpdateMerchantFeatureInCache` (`POST .../update_merchant_feature_cache`) is meant to push a toggle into the cache, but if the key is absent (cold/expired = `RedisNilError`) it **silently no-ops** rather than populating or invalidating — no pub/sub invalidation path exists anywhere under `internal/app/merchant` or `pkg/dcs`.
File: `internal/app/merchant/core.go:784-844` (`UpdateMerchantFeatureInCache`), route `internal/routing/router/merchant_routes.go:27-28`
Excerpt:
```go
res, err := c.Cache.Get(ctx, fmt.Sprintf(cacheKeyMerchantConfig, params.MerchantId))
if err != nil {
    if err.Error() == errorclass.RedisNilError.Name() {
        return nil  // update is not needed
    }
```
Confidence: probable (mechanism confirmed in-repo; not proven to be the exact cause of Slack-reported incidents)
Capability: reject

### A.7 Bulk approvals via "Batch service"

Claim: `BATCH_SERVICE_PAYOUT_APPROVAL_BULK` and any Batch-service client/host config are **absent** from this repo entirely (repo-wide grep across `internal/`, `pkg/`, `config/`). No bulk-approve HTTP route exists — only bulk create (`POST /v1/payouts/bulk`) and bulk validate.
File: n/a (absence confirmed)
Confidence: confirmed (absence)
Capability: observe

Claim: The only trace of "bulk approval" in this repo is SMS-notification action-name constants, not a trigger mechanism.
File: `pkg/raven/send_sms.go:57-58`
Excerpt:
```go
BulkPayoutApprove PayoutAction = "bulk_payout_approve"
BulkApprovePayout PayoutAction = "bulk_approve_payout"
```
Confidence: confirmed
Capability: observe

**Conclusion**: bulk-approval triggering must live outside this repo (Batch Service or API monolith), presumably calling this repo's existing single-payout `/payouts_internal/{id}/approve` per payout, or talking to WFS directly.

### A.8 Sequence diagram — approval

```mermaid
sequenceDiagram
    participant Client as Merchant/Dashboard/API
    participant PS as payouts (this repo)
    participant WFS as Workflow Service (external, Twirp)
    participant Approver as Approver (dashboard/Batch svc)
    participant Bank as Bank/FTS dispatch

    Client->>PS: POST /v1/payouts (PostFundAccountPayout)
    PS->>PS: CreatePayout -> IsWorkflowApplicable\n(DCS/legacy feature check, 30min-cached MerchantConfig)
    alt workflow applicable
        PS->>WFS: Twirp Create (/twirp/.../WorkflowAPI/Create)\nregisters callbacks: /v1/workflow/state,\n/v1/payouts/payouts_internal/{id}/approve|reject
        PS->>PS: payout.OnEvent(StatePending); persist; workflow_entity_map row
        PS-->>Client: 200, payout status=pending
        Approver->>WFS: approves/rejects (external UI/API,\nown auth incl. enable_approval_via_oauth - not in this repo)
        WFS->>PS: PATCH /v1/workflow/state/:id (UpdateWorkflowStateMap) [per step]
        PS->>PS: update workflow_state_map; dual-write forward to API monolith (best-effort)
        WFS->>PS: POST /v1/payouts/payouts_internal/{id}/approve (BasicAuth cred.Workflow)
        PS->>PS: ApprovePayout -> ProcessApprovePayout ->\nre-enters processor.PostPayoutCreateProcessing
        PS->>Bank: dispatch payout (FTS/bank rails)
        PS-->>WFS: 200 OK
    else no workflow / rejected
        WFS->>PS: POST /v1/payouts/payouts_internal/{id}/reject
        PS->>PS: payout.OnEvent(EventRejected) -> state=rejected; payout_logs write
    end
```

### A.9 Unresolved questions (approval/DCS)

1. Not proven that DCS/merchant-config cache staleness (A.6) is the confirmed root cause of Slack-reported incidents — only a plausible in-repo mechanism.
2. Where `enable_approval_via_oauth` (OTP/2FA) is actually enforced was not found in this repo — likely entirely inside WFS/dashboard before the approve/reject callback ever reaches `payouts`.
3. `BATCH_SERVICE_PAYOUT_APPROVAL_BULK` / bulk-approval trigger confirmed absent from this repo; must live in Batch Service or API monolith.
4. Workflow expiry event/handling not found in this repo.
5. `IsMerchantConfigViaAsvAndDcsEnabled` (`internal/app/merchant/core.go:1077`) is a second, independent migration-experiment axis on top of `PayoutWorkflowsDcsNameExperiment` that was not traced in depth — could compound the naming/cache issue.

---

## B. BALANCE / QUEUE-IF-LOW-BALANCE

### B.1 Which balance is checked — two distinct mechanisms by rail

**Direct/CA payouts** (not ledger-gated):

Claim: `GetLatestDirectAccountBalance` reads a cached-or-live x-balances value with a BASD/gateway-balance fallback — not a ledger response.
File: `internal/app/bankingAccountStatementDetails/core.go:292-311`
Excerpt:
```go
tryCache := !IsSkipBalanceCacheForceXBalances(ctx, merchantID)
if tryCache {
    if cached, cacheErr := c.Cache.Get(ctx, balanceId); cacheErr == nil && cached != "" {
```
Confidence: confirmed
Capability: read

Claim: `FundAccountPayoutDirectProcessor.QueueIfLowBalance` fetches this balance then delegates queue-or-dispatch to a `queueingstrategy.Gate`.
File: `internal/app/payouts/processor/fundAccountPayoutDirect.go:353-368`
Excerpt:
```go
response, err := f.BankingAccountStatementDetailsCore.GetLatestDirectAccountBalance(f.ctx, payout.GetBalanceID(), payout.GetMerchantID())
decision, gateErr := queueingstrategy.Select(f.ctx, payout, f.payoutsCore).Evaluate(payout, payoutAmount, merchantBalance)
```
Confidence: confirmed
Capability: read / transform

Claim: `queueingstrategy.Select` picks between two gates per merchant on DCS flag `in_flight_reservation_enabled`.
File: `internal/app/payouts/processor/queueingstrategy/strategy.go:46-52`
Excerpt:
```go
if payout.MerchantConfig.IsFeatureEnabled(features.InFlightReservationEnabled) {
    return reservationGate{ctx: ctx, payoutsCore: payoutsCore}
}
return balanceBufferGate{ctx: ctx}
```
Confidence: confirmed
Capability: route

Claim: Default gate (flag off) = `amount > gatewayBalance - queue_payout_bal_buffer` (a merchant-configured DCS buffer, `EnablePayoutsBuffer`) — this is the "merchant balance threshold" referenced in the task.
File: `internal/app/payouts/processor/queueingstrategy/balance_buffer_gate.go:63`
Excerpt:
```go
return Decision{Queue: payoutAmount > effectiveBalance}, nil
```
Confidence: confirmed
Capability: reject

**Shared/VA (ledger-backed) payouts** — genuinely separate: the ledger journal-create call itself signals insufficient balance.

Claim: `payoutStateProcessingBeforeLedgerServiceCall` fires `StateLedgerResponseAwaited` immediately before the ledger call.
File: `internal/app/payouts/processor/payoutViaLedgerService.go:198-210`
Excerpt:
```go
newStatus = appConstants.StateLedgerResponseAwaited
if err = payout.OnEvent(ctx, newStatus); err != nil { ... }
```
Confidence: confirmed
Capability: transform / persist

Claim: A ledger journal-create failure recognized as an insufficient-balance message is reclassed to `PayoutNotEnoughBalanceViaLedger`.
File: `internal/app/payouts/processor/payoutViaLedgerService.go:240-245`
Excerpt:
```go
if provider.GetLedgerClient(ctx).IsInsufficientBalanceErrorMsg(err.Internal().Error()) {
    return errorclass.PayoutNotEnoughBalanceViaLedger.New("").Wrap(err)...
}
```
Confidence: confirmed
Capability: reject / transform

Claim: `HandleIfQueueIfLowBalanceTrue` fires `EventQueued` when `payout.QueueFlag` is set — same terminal transition as the direct-account path.
File: `internal/app/payouts/processor/base.go:752-766,1062-1069`
Confidence: confirmed
Capability: reject / route

Claim: State machine confirms `EventQueued` fires from `StateLedgerResponseAwaited` among other pre-states, unifying both flows.
File: `internal/app/payouts/state_machine.go:355`
Excerpt:
```go
sm.Event(appConstants.EventQueued).To(appConstants.StateQueued).From(appConstants.StateCreateRequestSubmitted, appConstants.StateLedgerResponseAwaited, appConstants.StatePending, appConstants.StateScheduled, appConstants.StateOnHold)
```
Confidence: confirmed
Capability: transform

### B.2 In-flight reservation (Redis) design

Package: `internal/app/payouts/reservation` (store.go, scripts.go); wired from `queueingstrategy/reservation_gate.go`; released from `internal/app/payouts/reservation_hook.go`. Spec doc: `docs/observability/inflight-reservation-spec.md`.

Claim: Keys are hash-tagged per `{merchant_id:balance_id}` — a counter key and an items key.
File: `internal/app/payouts/reservation/store.go:227-241`
Excerpt:
```go
func (s *redisReservationStore) counterKey(mid, balID string) string {
    return fmt.Sprintf("%s:in_flight:{%s:%s}", s.keyPrefix, mid, balID)
}
func (s *redisReservationStore) itemsKey(mid, balID string) string {
    return fmt.Sprintf("%s:in_flight_items:{%s:%s}", s.keyPrefix, mid, balID)
}
```
Confidence: confirmed
Capability: persist

Claim: Counter = atomic running total reserved; items hash = `payoutID -> "<amount>:<dispatchedAtUnix>:<state>"` (`live` | `awaiting_balance_refresh`). Default TTL 6h, max 2000 items/balance.
File: `internal/app/payouts/reservation/store.go:150,160,395-400`
Excerpt:
```go
const defaultTTL = 6 * time.Hour
const defaultMaxItemsPerBalance int64 = 2000
```
Confidence: confirmed
Capability: persist

Claim: Reservation is taken **before** dispatch, inside the gate's `Evaluate`, via a single-shot Lua script (`Reserve`) that checks headroom then atomically increments (prevents concurrent-dispatch overdraw).
File: `internal/app/payouts/processor/queueingstrategy/reservation_gate.go:170` (`Evaluate`), `reservation/store.go:249`
Excerpt:
```go
reservedOK, atCapacity, total, reserveErr := store.Reserve(g.ctx, mid, balID, payoutID, gatewayBalance, threshold, amount, time.Now().Unix())
```
Confidence: confirmed
Capability: authorize / persist

Claim: Release is centralized in a single hook on `Payout.OnEvent`: `processed` → hold marked `awaiting_balance_refresh` (kept until debit visibly reflects); `failed`/`reversed`/`cancelled`/`rejected` → released outright.
File: `internal/app/payouts/reservation_hook.go:34-90`
Excerpt:
```go
switch eventName {
case appConstants.EventProcessed, appConstants.EventFailed,
    appConstants.EventReversed, appConstants.EventCancelled,
    appConstants.EventRejected:
```
Confidence: confirmed
Capability: transform / persist

Claim: In-code comments date the reservation gate to a "2026-08-04-inflight-balance-reservation plan" (Task 5) — corroborating the Slack claim this is a recently-added mechanism, unlike the older-reading `balance_buffer_gate.go` (comment: "This change was done for moneylicious…", no plan/date reference). Git history could not independently confirm dates — this clone has only a single squashed commit.
File: `internal/app/payouts/processor/queueingstrategy/reservation_gate.go:29-31`
Confidence: probable
Capability: observe

Claim: A separate reconciler cron rebuilds Redis state from DB truth every 5 minutes and refreshes a fail-closed heartbeat (TTL 12.5 min = 2.5× cadence); the reservation gate fails closed (queues) if the heartbeat is missing.
File: `internal/app/payouts/inflight_reservation_reconciler.go:51-56`; cron route `internal/routing/router/cron_routes.go:69-79` (`/process_inflight_reservation_reconciliation`)
Excerpt:
```go
inFlightReconcilerCadence = 5 * time.Minute
inFlightReconcilerHeartbeatTTL = inFlightReconcilerCadence * 5 / 2
```
Confidence: confirmed
Capability: observe / persist

### B.3 Queued-payout retry selection — `/v1/cron/process_queued_low_balance_payouts`

Claim: Handler `Core.ProcessInitiateForQueuedPayouts` fetches all balance IDs with ≥1 queued payout, filters to balances whose VA or gateway (CA) balance changed within the **last 6 hours (21600s)**, then dispatches per qualifying balance.
File: `internal/routing/router/cron_routes.go:60-68`; `internal/app/payouts/core.go:8568-8645`
Excerpt:
```go
// Filter for balances where balance changed in last 6 hours (21600 seconds)
balanceIdsFilteredOnBalanceUpdate, err := c.balanceCore.GetBankingBalanceIdsForVirtualAccountWhereBalanceUpdatedRecently(ctx, balanceIds)
```
Confidence: confirmed
Capability: read / route

Claim: `FindQueuedPayoutsForBalanceId` fetches queued payouts per balance, limit `QueuedPayoutsFetchLimit = 5000`, Redis-cached pagination offset — **no explicit `ORDER BY`** (order not guaranteed FIFO by the query itself).
File: `internal/app/payouts/repo.go:31,222-233`
Excerpt:
```go
q := r.GetConnection(ctx).Table("payouts").
    Where(appConstants.AttributeBalanceID+" =?", balanceId).
    Where(appConstants.AttributeStatus+" =?", appConstants.StateQueued).
    Limit(QueuedPayoutsFetchLimit).Offset(offset).Find(&payouts)
```
Confidence: confirmed
Capability: read

Claim: Dispatch enqueues a `queued_payout` async worker job per payout (not synchronous), which calls `ExecutePostCreateForQueuedPayout`.
File: `internal/app/payouts/core.go:8375-8385`; `internal/job/process_queued_payout.go:15-23,53-61`
Confidence: confirmed
Capability: route

Claim: No hard max-retry-count or automatic expiry-to-terminal-state was found for a chronically-failing queued payout — it stays `StateQueued` and is re-picked every cron pass while its balance still qualifies.
File: n/a (absence claim)
Confidence: speculative
Capability: observe

### B.4 Duplicate-dispatch protection

Claim: `ExecutePostCreateForQueuedPayout` takes a distributed Redis mutex `payout_<payoutID>` (30s hold) before reloading the payout.
File: `internal/app/payouts/core.go:3046-3051`; `internal/app/base/core/core.go:27-56` (`AcquireResource`); `pkg/mutex/redis.go`
Excerpt:
```go
m, err := c.AcquireResource(ctx, fmt.Sprintf(appConstants.ResourcePayout, payoutID), 30*time.Second)
```
Confidence: confirmed
Capability: authorize

Claim: After reload, `ValidateQueuedPayoutAsynchronousProcessing` re-checks `payout.IsStateQueued()`; if already processed by a prior duplicate delivery, returns a suppressed non-retryable error.
File: `internal/app/payouts/core.go:3067-3072,3106-3151`
Confidence: confirmed
Capability: reject

Claim: The reservation store's `Reserve` Lua script is itself a duplicate-admission guard on the reservation path: a retrying payout with an existing item is "self-exclusion re-checked" rather than double-reserved.
File: `internal/app/payouts/reservation/store.go:56-78`
Confidence: confirmed
Capability: authorize

### B.5 BalanceRefreshEvent from x-balances

Claim: Consumed as a broker-agnostic worker job (`x_balances_balance_refresh`, `MaxRetries: 3`) — not an HTTP webhook route.
File: `internal/job/x_balances_balance_refresh.go:28-36,39-77`
Excerpt:
```go
var xBalancesBalanceRefreshConfig = worker.Job{
    Name: "x_balances_balance_refresh", MaxRetries: 3, Timeout: 30 * time.Second,
}
```
Confidence: confirmed
Capability: route / transform

Claim: `Core.HandleBalanceRefreshEvent` (gated by `in_flight_reservation_enabled`) only **releases** in-flight reservation holds aged past debit-visibility lag — it does **not** itself re-trigger dispatch. Freed headroom is only picked up passively by the next cron pass of `process_queued_low_balance_payouts`.
File: `internal/app/payouts/reservation_hook.go:225-238,349-390`
Excerpt:
```go
// Freed headroom reaches the balance's queued payouts on the next
// queued-payouts cron pass.
```
Confidence: confirmed
Capability: observe / transform

**Conclusion: dequeue/retry is exclusively cron-driven (6h-window sweep), not event-driven.**

### B.6 Sequence diagram — low-balance queue/retry

```mermaid
sequenceDiagram
    participant API as Payout Create API
    participant Proc as FundAccountPayoutDirectProcessor
    participant BalSrc as Balance Source (cache / x-balances / BASD)
    participant Gate as queueingstrategy.Gate
    participant Redis as Reservation Store (Redis)
    participant DB as payouts DB
    participant FTS as FTS / Bank dispatch
    participant Cron as cron: process_queued_low_balance_payouts
    participant Recon as cron: process_inflight_reservation_reconciliation
    participant XBal as x-balances worker job

    API->>Proc: QueueIfLowBalance(payout)
    Proc->>BalSrc: GetLatestDirectAccountBalance
    BalSrc-->>Proc: merchantBalance
    Proc->>Gate: Select(...).Evaluate(payout, amount, balance)
    alt reservation flag ON
        Gate->>Redis: IsStoreTrusted (heartbeat check)
        Gate->>Redis: Reserve(mid,balID,payoutID,...) [Lua, atomic]
        Redis-->>Gate: reserved / atCapacity / notReserved
    else flag OFF
        Gate->>Gate: amount > gatewayBalance - queue_payout_bal_buffer
    end
    Gate-->>Proc: Decision{Queue, Compensate}

    alt Decision.Queue == true
        Proc->>DB: payout.OnEvent(StateQueued)
        Note over Proc,DB: QueuedReason=LowBalance, QueuedAt=now
    else Decision.Queue == false (dispatch)
        Proc->>FTS: dispatch payout
        FTS-->>Proc: processed / failed / reversed
        Proc->>Redis: release hook on terminal OnEvent\n(processed -> awaiting_balance_refresh, others -> release now)
    end

    Note over Cron: every cron interval (external scheduler)
    Cron->>DB: GetBalanceIdsWithAtleastOneQueuedPayout
    Cron->>DB: filter balances changed in last 6h (21600s)
    Cron->>DB: FindQueuedPayoutsForBalanceId (limit 5000)
    Cron->>Cron: enqueue job "queued_payout" per payout
    Cron->>Redis: AcquireResource("payout_<id>") mutex, 30s
    Cron->>DB: reload payout, verify IsStateQueued
    Cron->>Proc: QueueIfLowBalance / dispatch again (same gate path)
    Cron->>Redis: ReleaseResource(mutex)

    Note over Recon: every 5 minutes
    Recon->>Redis: rebuild counters/items from DB truth
    Recon->>Redis: SetReconciliationHeartbeat (TTL 12.5m)

    XBal->>XBal: consume BalanceRefreshEvent (worker job)
    XBal->>Redis: releaseAgedHolds (mark awaiting_balance_refresh items aged out)
    Note over XBal,Cron: No direct retrigger of dispatch;\nfreed headroom only picked up by next Cron pass
```

### B.7 Unresolved questions (balance/queue)

1. Writer of `c.Cache` (balance cache keyed by `balanceId`, `internal/app/bankingAccountStatementDetails/core.go:308`) not located — likely a webhook or write-through-on-fetch not covered in this pass.
2. No explicit max-retry/expiry path found for indefinitely-queued payouts (B.3) — flagged speculative; may exist in an ops tool outside this repo.
3. "Late Aug 2026" dating of the reservation mechanism is comment-only (no git log available in this single-commit clone).
4. Transport (Kafka/SQS/etc.) backing `x_balances_balance_refresh`'s queue was not traced into `pkg/worker`.
5. Whether `FindQueuedPayoutsForBalanceId`'s lack of `ORDER BY` amounts to de facto FIFO via clustered-index scan order was not verified against schema/migrations.

---

## C. SCHEDULED PAYOUTS and ON_HOLD

### C.1 Scheduled payouts — creation and dispatch

Claim: A payout is scheduled by supplying `scheduled_at` in the create request; must fall in one of 4 fixed daily IST slots (9/13/17/21) within the next 3 months.
File: `internal/app/payouts/schedule.go:23` (`GetAllowedTimeSlotsForScheduledPayouts`), `:55` (`ValidateScheduledAtTimeStamp`)
Excerpt:
```go
func GetAllowedTimeSlotsForScheduledPayouts() []int {
    return []int{9, 13, 17, 21}
}
```
Confidence: confirmed
Capability: reject

Claim: `CreateScheduledPayoutIfApplicable` fires `EventSchedule` at create time (before ledger/FTA processing), moving straight to `StateScheduled`, then returns early.
File: `internal/app/payouts/processor/scheduled_payout.go:11`; invoked from `internal/app/payouts/processor/base.go:301-307`
Confidence: confirmed
Capability: transform / persist

Claim: Cron `POST /process_scheduled_payouts` → `InitiateScheduledPayouts`; `GetEligibleScheduledPayouts` selects `scheduled_at < now()` AND status IN (`pending`, `scheduled`).
File: `internal/routing/router/cron_routes.go:88-89`; `internal/app/payouts/repo.go:164-183`
Excerpt:
```go
q := r.GetConnection(ctx).Table("payouts").Select("id").
    Where(appConstants.AttributeScheduledAT+" < ?", time.Now().Unix()).
    Where(appConstants.AttributeStatus+" IN (?)", []string{appConstants.StatePending, appConstants.StateScheduled})
```
Confidence: confirmed
Capability: route / read

Claim: Dispatch is async — one `ScheduledPayout` worker job per payout id.
File: `internal/app/payouts/core.go:4169-4229`; `internal/job/schedule_payout.go:16-61`
Confidence: confirmed
Capability: observe / route

Claim: `ProcessScheduledPayout` re-validates status; a still-`pending` payout (workflow-blocked) is auto-rejected; otherwise it re-enters the normal post-create pipeline (same as approval path in A.4), which can fire `EventCreated`, `EventBatchSubmit`, or `EventOnHold`.
File: `internal/app/payouts/core.go:2772-2867`
Excerpt:
```go
if payout.IsStatePending() == true {
    err := c.ProcessRejectPayout(ctx, payout)
}
if utils.IsEmpty(payout.BatchID) == false {
    return c.HandlePayoutWithBatchID(ctx, payout)
}
```
Confidence: confirmed
Capability: transform / persist

### C.2 ON_HOLD — two independent trigger paths

**A. Beneficiary bank downtime (IMPS only)**

Claim: Gated on merchant feature `PayoutsOnHold`, account type `BANK_ACCOUNT`, mode `IMPS`, and a Redis-cached bene-bank IFSC-prefix status map showing the bank down (probabilistically sampled so some traffic still probes uptime).
File: `internal/app/payouts/processor/onHoldHelper.go:16` (`HoldPayoutIfBeneBankDown`), `:89` (`checkIfPayoutToBeKeptOnHold`)
Excerpt:
```go
onHold := b.checkIfPayoutToBeKeptOnHold(payout)
if onHold == true {
    payout.SetQueuedReason(appConstants.BeneBankDown)
    if eventErr := payout.OnEvent(b.ctx, appConstants.StateOnHold); eventErr != nil {
```
Confidence: confirmed
Capability: reject / transform

Claim: The writer of the bene-bank status Redis map itself was not found in this repo (likely an external bank-uptime detector).
File: `internal/app/payouts/onHoldHelper.go:30-80` (reader side only)
Confidence: probable
Capability: read

**B. Partner bank / gateway down (Direct accounts)**

Claim: `HandlePartnerBankOnHold` calls `HealthService.IsChannelDown(..., channelhealth.PartnerBankHealth)`; if down, `QueuedReason = GatewayDegraded` and `StateOnHold` fires.
File: `internal/app/payouts/processor/fundAccountPayoutDirect.go:577-604`
Excerpt:
```go
isDown, err := f.HealthService.IsChannelDown(f.ctx, input, channelhealth.PartnerBankHealth)
if isDown {
    payout.SetQueuedReason(appConstants.GatewayDegraded)
```
Confidence: confirmed
Capability: reject / transform

Claim: Partner-bank health is populated by an external FTS webhook `POST /v1/notify/health/update` (BasicAuth `cred.FTS`), writing Redis hash `partner_bank_health`, read by `IsChannelDown`.
File: `internal/routing/router/internal_routes.go:11-22`; `internal/app/channelhealth/partner_bank_health_manager.go:17-45`
Confidence: confirmed
Capability: route / observe

### C.3 Moving OUT of on_hold — two crons

Claim: `POST /process_beneficiary_bank_on_hold_payouts` → `ProcessDispatchForOnHoldPayouts` fetches (a) on_hold payouts whose bene bank is now up (`GetPayoutIdsForWhichBeneBankIsUp`) and (b) SLA-breached on_hold payouts (`GetPayoutIdsToCancel`, per merchant SLA), dispatching `OnHoldPayout` jobs with action `Process` or `Cancel`.
File: `internal/routing/router/cron_routes.go:56-57`; `internal/app/payouts/onHoldHelper.go:84-135,265-303`
Confidence: confirmed
Capability: route / observe

Claim: `Cancel` action fires `EventFailed` (terminal failure), setting failure reason from `QueuedReason`.
File: `internal/app/payouts/onHoldHelper.go:309-341` (`cancelOnHoldPayout`)
Excerpt:
```go
failureReason, statusCode := fetchFailureReasonAndStatusCode(payout)
payout.SetFailureReason(&failureReason)
if eventErr := payout.OnEvent(ctx, appConstants.EventFailed); eventErr != nil {
```
Confidence: confirmed
Capability: transform / persist

Claim: `POST /process_queued_payouts` → `ProcessQueuedPayouts` (`PartnerBankHoldPayouts.ProcessQueuedPayouts`) does the mirror image for partner-bank-hold recovery.
File: `internal/routing/router/cron_routes.go:35-37`; `internal/app/payouts/queued/partner_bank_hold_payouts.go:41-83`
Confidence: confirmed
Capability: route / observe

### C.4 Unresolved questions (scheduled/on-hold)

1. Writer of bene-bank-down/up Redis state not located in this repo.
2. Whether an active "test transaction" mechanism (beyond probabilistic sampling) exists to detect bene-bank recovery was not fully traced.

---

## D. BULK PAYOUTS

### D.1 Flow — route, validation, "Batch service" relationship

Claim: `POST /v1/payouts/bulk` → `BulkPayoutsProcessorService.CreateBulkPayouts`, auth `PassportAuthentication` under `cred.API`/`cred.Workflow` — a service-to-service route, not end-user.
File: `internal/routing/router/payout_internal_routes_with_passport.go:13-27`
Confidence: confirmed
Capability: route

Claim: **No "Batch service" client config/SDK exists in this repo** — no host/key in `config/*.toml`, no `BatchService`/`BatchClient` wrapper. The relationship is inbound-only: an external caller (presumed Batch service, not confirmed by name in-repo) posts per-chunk to `/v1/payouts/bulk` passing a batch id via header (`constants.HeaderBatchID`) captured into request context.
File: `internal/controllers/bulkPayoutsController.go:73`; `internal/helpers/helpers.go:61-75`; `internal/routing/middleware/request_context.go:19,38-44`
Excerpt:
```go
batchID := ctx.Request.Header.Get(constants.HeaderBatchID)
if batchID != "" {
    ctx.Request = ctx.Request.WithContext(context.WithValue(ctx.Request.Context(), appConstants.ContextKeyBatchID, batchID))
}
```
Confidence: confirmed (absence of client config); probable (caller identity = "Batch service", inferred not documented)
Capability: route / read

Claim: Separate pre-flight `POST /v1/payouts/bulk/validate` → `PayoutsBulkValidate` parses/validates uploaded CSV headers before per-row submission.
File: `internal/routing/router/payout_bulk_routes.go:13-28`; `internal/app/payoutsBulk/helperPayoutsBulkValidate.go:21`
Confidence: confirmed
Capability: route / reject

### D.2 Per-row create — synchronous, in-process; explicit idempotency key

Claim: Per-row create is **synchronous within the same HTTP request**, not Kafka/worker-queued — `CreateBulkPayoutsConcurrent` runs an in-memory goroutine pool (2–10 workers) calling `HandleBulkPayoutInputForSinglePayout` per row, blocking until all finish.
File: `internal/app/bulkPayoutsProcessor/core.go:88-153,169-185`
Confidence: confirmed
Capability: transform

Claim: `IdempotencyKey` is a required, caller-supplied field per row (`binding:"required"`) — NOT server-derived from `batch_id + row_number` or a content hash. Uniqueness enforced per `(merchant_id, idempotency_key)` via table `bulk_idempotency_keys`, guarded by a per-`(batch_id, idempotency_key)` mutex.
File: `internal/app/bulkPayoutsProcessor/validation.go:22`; `internal/app/bulkPayoutsProcessor/core.go:272-292`; `internal/app/bulkPayoutsProcessor/model.go:8-17` (`TableBulkIdempotencyKeys = "bulk_idempotency_keys"`)
Excerpt:
```go
IdempotencyKey string `json:"idempotency_key" binding:"required"`
mutexResource := fmt.Sprintf(appConstants.ResourceCreateBulkPayout, batchID, item.IdempotencyKey)
```
Confidence: confirmed
Capability: persist / transform

Claim: A duplicate idempotency key within/across calls is **not** a hard failure and does not fail the whole batch — the existing payout is looked up and returned as that row's "success"; other malformed rows produce per-row errors while the rest of the batch continues.
File: `internal/app/bulkPayoutsProcessor/core.go:309-368,52-86`
Excerpt:
```go
if !utils.IsEmpty(existingBulkPayout) {
    metric.BulkDuplicatePayoutCount.WithLabelValues().Inc()
    existingPayout, payoutErr := c.payoutsCore.GetPayoutByPayoutId(ctx, existingBulkPayout.SourceID)
    return existingPayout, nil
}
```
Confidence: confirmed
Capability: read / observe

### D.3 Status reporting

Claim: No dedicated bulk-job-status table/endpoint owned by payouts — `/v1/payouts/bulk` is synchronous, returning success/error arrays per call immediately. Aggregate progress is exposed via `GET /v1/payouts?batch_id=...` (standard list endpoint, `FetchMultiplePayoutsRequest.BatchID`), implying the caller polls by `batch_id` rather than receiving a bulk-level webhook.
File: `internal/app/dtos/fetch_multiple_payout_request.go:30`; `internal/routing/router/payout_routes.go:61-68`
Confidence: probable (endpoint capability confirmed; polling-usage inferred, not documented)
Capability: route / read

Claim: NEFT/RTGS bulk rows are deliberately held in `StateBatchSubmitted` (`EventBatchSubmit`, when `payout.BatchID` non-empty and mode ∈ {NEFT, RTGS}) rather than initiated immediately; a separate cron `POST /process_batch_submitted_payouts` releases them.
File: `internal/app/payouts/processor/bulk_payout_helper.go:11-29`; `internal/app/payouts/constants.go:21-24` (`BatchPayoutsDelayedInitiationModes = [NEFT, RTGS]`); cron `internal/routing/router/cron_routes.go:46-47`
Excerpt:
```go
var BatchPayoutsDelayedInitiationModes = []string{appConstants.NEFT, appConstants.RTGS}
```
Confidence: confirmed
Capability: transform / route

### D.4 Unresolved questions (bulk)

1. Identity of the actual `/v1/payouts/bulk` caller ("Batch service") not confirmed by name in this repo — inferred from header conventions and auth shape only.
2. Whether `GET /v1/payouts?batch_id=` is really the polling mechanism used by that caller is inferred, not documented.
3. Two near-duplicate implementations exist — sequential `CreateBulkPayouts` (`core.go:52`) vs concurrent `CreateBulkPayoutsConcurrent` (`core.go:88`) — which is actually wired to the live route was not confirmed.

---

## E. CANCEL and REJECT

### E.1 Cancel

Claim: `POST /v1/payouts/cancel_payout/:payout_id`, passport-authenticated (Private/Proxy/Privilege), → `Payoutv1.CancelPayout`.
File: `internal/routing/router/payout_routes.go:70-77`; `internal/controllers/payoutController.go:972-1020`
Confidence: confirmed
Capability: route

Claim: `EventCancelled` allowed only `From(StateQueued, StateScheduled, StateOnHold)`.
File: `internal/app/payouts/state_machine.go:380`
Excerpt:
```go
sm.Event(appConstants.EventCancelled).To(appConstants.StateCancelled).
    From(appConstants.StateQueued, appConstants.StateScheduled, appConstants.StateOnHold)
```
Confidence: confirmed
Capability: authorize

Claim: `Core.CancelPayout` fetches merchant-scoped, mutex-guards, fires `EventCancelled`, plain `repo.Update` — **no ledger call anywhere in this function or its call chain**. Consistent with `queued`/`scheduled`/`on_hold` all being pre-ledger states.
File: `internal/app/payouts/core.go:3968-4030`
Confidence: confirmed
Capability: transform / persist

**Answer: cancel from `queued` does NOT create a ledger reversal journal.** No journal, no reversal entity, no `payout_reversed` event — ledger involvement only reaches states cancel can never touch (`initiated`/`processed`/`reversed`/`failed`).

### E.2 Reject

Claim: `POST /v1/payouts_internal/:payout_id/reject`, service BasicAuth only (no passport) → `ProcessRejectPayout` → `RejectPendingPayout`.
File: `internal/routing/router/payout_internal_routes.go:76-79`; `internal/controllers/payoutController.go:1075-1112`; `internal/app/payouts/core.go:5064-5106,5200-5221`
Confidence: confirmed
Capability: route / authorize

Claim: `EventRejected` allowed only `From(StatePending)`; `ProcessRejectPayout` does a plain `repo.Update`, no ledger interaction — consistent with `pending` being pre-ledger.
File: `internal/app/payouts/state_machine.go:432-434`; `internal/app/payouts/core.go:5200-5221`
Confidence: confirmed
Capability: authorize / transform

**Answer: reject never touches ledger** — applies strictly pre-ledger-call. Note: `RejectPendingPayout` fetches via unscoped `FindByID` (no merchant filter) — see section G.

### E.3 Unresolved questions (cancel/reject)

None beyond G's tenant-isolation caveat on `RejectPendingPayout`/`GetPayoutByPayoutId`.

---

## F. REVERSAL

### F.1 Trigger — FTS-driven, no admin path found

Claim: Path 1 — `PATCH /v1/payouts_internal/update_payouts_with_fts` → `HandlePayoutStatusUpdateViaFTS` → `status=="reversed"` → `HandlePayoutReversed` → `reversalCore.ReverseMerchantPayout(...)`.
File: `internal/routing/router/payout_internal_routes.go:26-30`; `internal/app/payouts/core.go:1465,1505-1507,2344-2360`
Excerpt:
```go
case appConstants.StateReversed:
    err = c.HandlePayoutReversed(ctx, payout, request)
_, err := c.reversalCore.ReverseMerchantPayout(ctx, payout, ftsStatusUpdateInfo)
```
Confidence: confirmed
Capability: route / transform

Claim: Path 2 — `POST /v1/payouts/transfer_status_webhook` → `HandleTransferStatusWebhookForPayout` → `HandlePayoutStatusUpdateViaFundTransferService` → `status=="reversed"` → `HandlePayoutReversedViaFundTransferService` → `reversalCore.ReverseMerchantPayoutViaFundTransferServiceReversedWebhook(...)`. Comment: "FTS webhook requests now come directly to the Payouts Service as part of the FTS integration."
File: `internal/routing/router/payout_internal_routes.go:153-158`; `internal/app/payouts/core.go:1579-1584,1605-1608`; `internal/app/payouts/fts_transfer_status_webhook.go:1036-1094`
Confidence: confirmed
Capability: route / transform / persist

Claim: Both internal routes are gated only by shared-secret BasicAuth (`cred.API, cred.Workflow, cred.Xperience, cred.FTS, cred.VendorPayments, cred.Settlements, cred.Irctc`) — no passport/merchant auth — so any internal service holding one of these secrets can trigger reversal creation keyed by `SourceID` in the payload.
File: `internal/routing/router/payout_internal_routes.go:12-18`
Confidence: confirmed
Capability: authorize

Claim: `CreateReversalEntity` is also invoked from `MovePayoutToTerminalStateAsPerWebhookConfig` (backstop "when async retries are exhausted") and `UpdatePayoutAfterBASRecon` (BAS-reconciliation route) — both automated/system flows. **No admin-initiated reversal-creation endpoint found.**
File: `internal/app/payouts/core.go:3806,7203`; `internal/app/payouts/service.go:608-612`
Confidence: confirmed
Capability: observe

### F.2 Ordering vs ledger — DB persist always precedes ledger call

Claim: Inside `reversals.Core.ReverseMerchantPayout`, `CreateReversalEntity` (DB write) runs first (line 62); `transactionProcessingForPayoutReversal` (drives the ledger `payout_reversed` journal) runs after (line 71).
File: `internal/app/reversals/core.go:53-71,221-279,287-339`
Excerpt:
```go
reversal, err = c.CreateReversalEntity(ctx, payout)     // DB persist first
if err != nil { return nil, err }
err = c.transactionProcessingForPayoutReversal(ctx, reversal, payout, ftsStatusUpdateInfo)  // ledger call second
```
Confidence: confirmed
Capability: persist / transform

Claim: `payout_reversed` ledger event constant and call chain confirmed.
File: `internal/app/common/appConstants/events.go:7` (`PayoutReversedLedgerEvent = "payout_reversed"`); `internal/app/reversals/reversalViaLedgerService.go:25-88,133-156`
Confidence: confirmed
Capability: transform

### F.3 Failure handling — bounded async retry, not just logging

Claim: No status/sub-state field exists on the `Reversal` model itself (`internal/app/reversals/model.go:12-26`) — idempotent retry instead relies on `CreateReversalEntity` checking for an existing row before creating, and `CreateTransactionViaLedger` short-circuiting if `TransactionID` already set.
File: `internal/app/reversals/model.go:12-26`
Confidence: confirmed
Capability: observe

Claim: On ledger-call failure, the FTS payload is stashed to a temp meta table (`payoutMetaTemporaryCore`, key `FtsInfoMeta`) and an async retry job pushed (`job.QueuePushForPayoutUpdateFailureHandling`, type `PayoutReversalFailureType` or `LedgerReversedEventFailureType`).
File: `internal/app/reversals/core.go:71-100,329-336`
Excerpt:
```go
if c.IsFTSRequestFromPayoutsService(ctx, payout) {
    err = job.QueuePushForPayoutUpdateFailureHandling(ctx, job.LedgerReversedEventFailureType, payout.GetID())
    return err
}
_ = job.QueuePushForPayoutUpdateFailureHandling(ctx, job.PayoutReversalFailureType, payout.GetID())
```
Confidence: confirmed
Capability: observe / transform

Claim: This is a bounded-retry worker job (`MaxRetries: 3`, 120s timeout) → `PayoutUpdateFailureHandling.Handle` → `PayoutHandler.PayoutUpdateFailureAsyncProcessing` → `asyncProcessingPayoutReversalFailure`/`asyncProcessingPayoutReversalLedgerFailure`, which idempotently re-fetch-or-create the reversal and re-attempt the ledger call.
File: `internal/job/payout_update_failure_handling.go:32-41,69-106`; `internal/app/payouts/asyncFailureHandlingHelper.go:106-155,316-437`
Excerpt:
```go
var payoutUpdateFailureHandlingConfig = worker.Job{
    Name: appConstants.QueuePayoutUpdateFailureHandling, MaxRetries: 3, Timeout: 120 * time.Second,
}
```
Confidence: confirmed
Capability: observe / transform / persist

Claim: `MovePayoutToTerminalStateAsPerWebhookConfig` is documented as a backstop invoked "when async retries are exhausted," forcing the payout to a terminal state (creating the reversal entity if still missing) — not a further automatic ledger retry itself. Beyond the worker's 3 retries and this backstop, no further automated retry (e.g. a dedicated reversal-reconciliation cron re-scanning for missing `transaction_id`) was found — Slack alerting (`slack.SlackAlertForQueuePushFailure`) fires on queue-push failure, implying manual/ops intervention beyond that point.
File: `internal/app/payouts/service.go:608-624`; `internal/app/payouts/core.go:3806,3836,3887`
Confidence: probable
Capability: observe / transform

### F.4 Unresolved questions (reversal)

1. Exact upstream trigger of `MovePayoutToTerminalStateAsPerWebhookConfig`'s "async retries exhausted" condition not fully traced (callers found at `core.go:5338,5535,5576`, `processor/base.go:1227,1281,1464`, but the FTS retry-exhaustion signal feeding those wasn't traced end-to-end).
2. No cron/reconciler specifically re-scanning reversals stuck without a ledger `transaction_id` was found beyond the 3-retry worker job and the Slack-alert backstop.

---

## G. MERCHANT / TENANT ISOLATION

### G.1 Merchant ID resolution order

Claim: `GetMerchantIdFromContext` tries passport claims first, falls back to header-derived context value only if passport-derived ID is empty.
File: `internal/auth/authHelper.go:12-25`
Excerpt:
```go
merchantId, err = getMerchantIdFromPassport(ctx)
if merchantId == "" {
    merchantId, err = getMerchantIdFromHeaders(ctx)
}
```
Confidence: confirmed
Capability: authorize

Claim: `LegacyAuthTypePrivate` (merchant's own JWT) reads `consumer.id` from `ConsumerClaims` directly; `Proxy`/`Privilege` (internal apps acting on behalf of a merchant) read `ImpersonationClaims().Consumer` instead.
File: `internal/auth/passport.go:173-208`
Excerpt:
```go
if authType == passportSdk.LegacyAuthTypePrivate {
    consumer, err = GetConsumerClaims(ctx)
} else {
    impersonation, err = GetImpersonationClaims(ctx)
    if err == nil && impersonation != nil { consumer = impersonation.Consumer }
}
```
Confidence: confirmed
Capability: authorize

Claim: Header fallback reads `x-merchant-id` (`HeaderMerchantID`) set globally by `SetRequestContext` middleware, with `X-Entity-Id` (`HeaderMerchantIDFromBatch`) used for batch-derived merchant context, overridden by `x-merchant-id` if both present. `X-Razorpay-Account` is used only **outbound** by this service (calling other services) — not read inbound for merchant resolution.
File: `internal/auth/headers.go:27-38`; `internal/routing/middleware/request_context.go:18,39-53,69-75`; `internal/constants/headers.go:10,20`; `pkg/api/client.go:25`; `pkg/workflow/workflow_create.go:89`
Confidence: confirmed
Capability: authorize

### G.2 DB-layer enforcement

Claim: The generic base repo (`pkg/spine/repository.go`, embedded by every entity repo) provides an **unscoped** `FindByID` filtering only on `id` — no automatic merchant_id injection anywhere shared.
File: `pkg/spine/repository.go:27-31`
Excerpt:
```go
func (repo Repo) FindByID(ctx context.Context, receiver IModel, id string) errors.IError {
    q := repo.GetConnection(ctx).Where("id = ?", id).First(receiver)
```
Confidence: confirmed
Capability: read

Claim: Spot-check 1 (correctly scoped) — `Payout.FindPayoutByIdAndMerchantId` (public `GET /v1/payouts/:id`, `CancelPayout`) filters both `id` and `merchant_id`.
File: `internal/app/payouts/repo.go:150-162`
Excerpt:
```go
q := r.GetConnection(ctx).Where("id = ?", strings.TrimPrefix(id, Sign)).
    Where(appConstants.AttributeMerchantId+" =?", merchantId).First(payout)
```
Confidence: confirmed
Capability: read

Claim: Spot-check 2 (**not** merchant-scoped) — `Core.GetPayoutByPayoutId` (used by `ApprovePendingPayout`/`RejectPendingPayout` maker-checker flows) uses the unscoped base `repo.FindByID` — no merchant_id filter, fetch by payout ID alone.
File: `internal/app/payouts/core.go:4939-4958`
Excerpt:
```go
func (c Core) GetPayoutByPayoutId(ctx context.Context, payoutId string) (*Payout, errors.IError) {
    var payout Payout
    err := c.repo.FindByID(ctx, &payout, payoutId)
```
Confidence: confirmed
Capability: read

Claim: Spot-check 3 (**not** merchant-scoped) — `reversals.repo.FindReversalWithPayoutID`/`FindReversalsWithPayoutIDs` scope only by `payout_id` (or `entity_id`), never `merchant_id` — relies entirely on the caller having already merchant-verified the associated payout.
File: `internal/app/reversals/repo.go:25-42`
Confidence: confirmed
Capability: read

### G.3 Admin/internal cross-merchant bypass

Claim: The internal routes group (`/v1/payouts_internal/*` — FTS webhooks, maker-checker approve/reject) is gated only by shared-secret BasicAuth, **no passport/merchant JWT, no per-merchant authorization** — and its handlers fetch payouts by ID only (G.2 spot-check 2). A `TODO` comment in the file acknowledges the gap.
File: `internal/routing/router/payout_internal_routes.go:12-18`
Excerpt:
```go
//TODO: add check for internal auth type here after appending Jwt token from api to all these routes
middleware.BasicAuth(cred.API, cred.Workflow, cred.Xperience, cred.FTS, cred.VendorPayments, cred.Settlements, cred.Irctc),
```
Confidence: confirmed
Capability: authorize / reject

Claim: An explicit `/v1/admin/*` route group requires `PassportAuthentication([]string{passportSdk.LegacyAuthTypeAdmin})` plus BasicAuth, exposing generic cross-entity lookups (`AdminClient.GetEntityById`/`GetEntityMultiple`) that take only `entity` + `id` — no merchant parameter — so an authenticated admin caller can fetch any entity by ID across merchants by design.
File: `internal/routing/router/payout_admin_routes.go:13-32`; `internal/controllers/adminClientController.go:33-76`
Confidence: confirmed
Capability: authorize / read

Claim: Passport-level impersonation (Proxy/Privilege → `ImpersonationClaims().Consumer`) is the authorized account-impersonation mechanism (e.g. Dashboard/internal apps acting "as" a merchant) — issuance/validation of the impersonation claim itself lives in the `goutils/passport/v3` SDK, outside this repo; this repo only trusts whatever the SDK hands it.
File: `internal/auth/passport.go:184-205`
Confidence: probable (issuance logic out of repo)
Capability: authorize

### G.4 Unresolved questions (tenant isolation)

1. Where upstream the account-impersonation JWT claim gets minted/authorized (outside this repo) was not verified.
2. Whether internal GET routes reusing `controllers.PayoutService.GetPayout` (normally passport-scoped) behave correctly under the `x-merchant-id` header fallback when passport is absent was not confirmed end-to-end.

---

## Cross-cutting: DB tables touched (this lane)

| Table | Purpose | Migration / model |
|---|---|---|
| `payouts` | core entity, all status transitions | `internal/app/payouts/model.go` |
| `payout_logs` | audit log of every state transition (incl. reject/cancel/reverse) | `internal/app/payoutLog/model.go:21` |
| `workflow_state_map` | per-approval-step state (actor role, approvals needed) | `internal/database/migrations/20220912025953_create_workflow_state_map_table.go:14-27` |
| `workflow_entity_map` | workflow-to-payout entity linkage | `internal/database/migrations/20210824110739_create_workflow_entity_map_table.go:14-24` |
| `bulk_idempotency_keys` | per-row bulk idempotency uniqueness `(merchant_id, idempotency_key)` | `internal/app/bulkPayoutsProcessor/model.go:8-17` |
| `idempotency_keys` | single-payout idempotency `(idempotency_key, merchant_id)` unique | `internal/database/migrations/20221004002554_create_idempotency_keys_table.go:14-27` (mapped in lane 01) |
| reversal entity table (repo pkg `internal/app/reversals`) | reversal record, created before ledger call | `internal/app/reversals/model.go:12-26`, `core.go:221-279` |

## Cross-cutting: DCS / feature-flag names referenced by this lane

See table in **A.6**. Key point: legacy `appConstants` string values and `pkg/dcs/features` DCS-storage string values differ under matching Go identifier names for `PayoutWorkflows`, `SkipWorkflowForApi`, and `SkipWfForPayroll` — reconciled only when `PayoutWorkflowsDcsNameExperiment` is enabled (checks both, OR'd).

## Master unresolved-questions list

1. Whether the 30-min merchant-config cache + no-op `UpdateMerchantFeatureInCache` (A.6) is *the* confirmed cause of Slack-reported `payout_workflows`-ignored incidents — plausible mechanism, not proven from code alone.
2. OTP/2FA enforcement (`enable_approval_via_oauth`) location — not found in this repo; presumed to live in WFS/dashboard.
3. `BATCH_SERVICE_PAYOUT_APPROVAL_BULK` and bulk-approval trigger — confirmed absent from this repo; lives elsewhere (Batch Service / API monolith).
4. Workflow expiry handling — no event found in this repo's state machine.
5. Writer of balance cache (`internal/app/bankingAccountStatementDetails/core.go:308`) and of bene-bank-down/up Redis state — not located.
6. No hard max-retry/expiry ceiling found for indefinitely-`queued` low-balance payouts.
7. "Late Aug 2026" in-flight-reservation dating is comment-only; no git history to independently confirm (single-commit clone).
8. Identity confirmation of the `/v1/payouts/bulk` caller as "Batch service," and whether `GET /v1/payouts?batch_id=` is really its polling mechanism — both inferred, not documented in-repo.
9. Upstream trigger chain for `MovePayoutToTerminalStateAsPerWebhookConfig`'s "async retries exhausted" condition not fully traced.
10. No dedicated reconciliation cron found for reversals stuck without a ledger `transaction_id`, beyond the 3-retry worker job and Slack-alert backstop.
11. Upstream minting/authorization of the passport impersonation claim (account-impersonation bypass) lives outside this repo and was not verified.
