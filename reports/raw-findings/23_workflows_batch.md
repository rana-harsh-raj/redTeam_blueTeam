# Workflow Service (razorpay/workflows) and Batch Service (razorpay/batch)

Repos:
- `razorpay/workflows` @ (see `git -C workflows rev-parse HEAD`) — "Razorpay Workflow Service", Go
  Clone: `/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture/workflows`
- `razorpay/batch` @ (see `git -C batch rev-parse HEAD`) — "Razorpay Batch service", Java/Spring Boot/Gradle
  Clone: `/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture/batch`

Both repos were absent from the scratchpad at task start (not previously cloned in this audit) and were shallow-cloned fresh for this lane. Cross-references below to "payouts-side" facts are from `reports/raw-findings/12_payouts_approval_queue_bulk.md` sections A and D (razorpay/payouts repo), already-closed context, not re-derived here.

---

## A. WORKFLOW SERVICE (razorpay/workflows)

### A.1 Stack & deployables

Claim: Go module `github.com/razorpay/workflows`, `go 1.25` (`go.mod:1-5`; README says "Go ≥1.15" — stale). Key deps: `github.com/twitchtv/twirp v8.1.3` (Twirp — **not classic gRPC**, JSON+HTTP/1.1 or protobuf+HTTP/1.1), `go.uber.org/cadence v0.13.4` + `go.uber.org/yarpc v1.42.0`, `github.com/jinzhu/gorm` + `github.com/go-sql-driver/mysql`, `github.com/go-redis/redis v6.15.9`, `github.com/gojektech/heimdall/v6` (outbound HTTP client), `github.com/pressly/goose` (migrations).

Three deployable binaries:
- **`cmd/api/main.go`** — HTTP server exposing four Twirp services (`ConfigAPI`, `WorkflowAPI`, `ActionAPI`, `CommentAPI`, plus `HealthCheckAPI` and a DCS config-set API) on one `http.ServeMux` (`cmd/api/main.go:84-116`), plus a metrics/pprof mux.
- **`cmd/workers/main.go`** — a **Cadence worker** process; registers config/workflow/state/action handlers then `job.StartWorkers(ctx, workflowType, domains)` (`cmd/workers/main.go:100-106`) — this is the process that actually executes Cadence workflows/activities (the state-machine engine itself), selected via `-WORKFLOW_TYPE`/`-WORKFLOW_DOMAINS` flags.
- **`cmd/migration/main.go`** — `goose`-based DB migration runner.

Datastores:
- **MySQL** (via `gorm`) is the primary DB — `internal/database/migrations/*.go` (e.g. `20200702202525_create_actions.go`), config in `pkg/db`/`config/*.toml` `[db]`.
- **Redis** (`pkg/cache`, `go-redis/redis`) exists as a cache layer, not deeply investigated (no callback/approval logic found there).
- **Uber Cadence — confirmed central to the whole engine.** Every approval-workflow instance is a live, long-running Cadence workflow execution; every state transition or human action is delivered to it as a Cadence Signal (A.4/A.5/A.7).
- **No Kafka/SQS/message-bus in production use** — `pkg/queue/queue.go` is a bare empty `type Queue interface {}` with zero implementations; `pkg/worker/queue/broker/{redis,sqs}` is unreferenced dead library code (`grep -rln "worker/queue" internal cmd` → no hits).
File: `go.mod:1-5`; `cmd/api/main.go:84-116`; `cmd/workers/main.go:100-106`; `cmd/migration/main.go:70`; `pkg/queue/queue.go`.
Confidence: confirmed.

### A.2 Twirp API surface

Claim: Proto files are **not vendored** in this repo — `.gitignore` excludes both `proto` and `rpc` directories, and `Makefile`/`buf.yaml` fetch/generate them from the external `razorpay/proto` repo (`PROTO_GIT_URL`, modules `workflows/{action,comment,config,workflow}/v1`) at build time; no `.pb.go` files exist in this clone. The full request/response surface below was reconstructed from the Go server implementations and DTOs, which reference every field of the generated protos exhaustively.

Four Twirp services (`cmd/api/main.go:84-115`), all wrapped by one shared auth hook allow-listing these caller credentials: `credentials.{API, Payouts, Relay, Growth, PayoutLinks, Splitz, VendorPayments, Xperience, VendorExperience, Abacus, OffersEngine, PaymentsBankTransfer}` — i.e. no per-RPC restriction, any allow-listed caller can call all four services.

**`WorkflowAPI`** (`internal/entities/workflow/server.go`): `Create(WorkflowTriggerRequest{Workflow,Input *structpb.Struct}) → Workflow`; `Recreate`; `Get`; `ListByIds`; `ListPending`; `List`; `Terminate`; `AddAssignee`; `RemoveAssignee`; `AdminAction`. Full `Workflow` message and nested `Callback`/`CallbackDetails` schema in **C.6**.

**`ActionAPI`** (`internal/entities/action/server.go`): `Create(ActionCreateRequest{Action}) → Action`; `CreateWithEntityId(ActionOnEntityCreateRequest{EntityId,EntityType,ConfigId,Action,ActorId,ActorType,ActorPropertyKey,ActorPropertyValue,ActorMeta,Service,OwnerId,OwnerType,Data,Comment}) → ActionListResponse`; `Get`; `List`; `CreateDirectOnWorkflow(ActionDirectCreateRequest{Action}) → Action`. Full `Action` schema in **C.6** — this is the approve/reject RPC family (A.9).

**`ConfigAPI`** (`internal/entities/config/server.go`): `CreateV2`/`UpdateV2`/`DeleteV2` (the caller-friendly range/steps/roles form, request `ConfigCreateRequestV2{OwnerId,ConfigType,Version,Dimensions,IsPendingCheckRequired,ConfigTemplate []*RangeConfig}`); `Create`/`Get`/`List`/`Update`/`Delete` (raw-template v1 form). `RangeConfig{Range "min-max", Steps[]{Step, Op "AND"|"OR", Roles[]{RoleName,RoleId,ApprovalCount}}}` is exactly what a caller like payouts sends (concrete example in A.3).

**`CommentAPI`** (`internal/entities/comment/server.go`): `Create(Comment{WorkflowId,CommentorId,CommentorType,Comment,OwnerId,OwnerType,Service}) → Comment`; `Get`; `List`.
File: `internal/entities/{workflow,action,config,comment}/server.go`; `cmd/api/main.go:84-116`; `.gitignore:19-22`; `Makefile:22-23,80-90`; `buf.yaml:1-4`; `scripts/proto_modules`.
Confidence: confirmed.

### A.3 Config types (payout approval, purchase-order-approval, etc.)

Claim: Config templates are keyed by a `Type` string, resolved via `internal/dto/config_model.go: ConfigTypeToDetailsMap` (`config_model.go:86-122`):

| Config type | value sent by caller | Cadence domain | task list | outbound ServiceName |
|---|---|---|---|---|
| `ConfigTypePayoutApproval` | `payout-approval` | `payouts` | `payouts-approval` | `rx_live` |
| `ConfigTypePurchaseOrderApproval` | `purchase-order-approval` | `purchase_orders` | `purchase_order_approval` | `vendor_payments` |
| `ConfigTypeGoodsReceivedNoteApproval` | `goods-received-note-approval` | `goods_received_note` | `goods_received_note_approval` | `vendor_payments` |
| `ConfigTypeVendorPaymentV2Approval` | `vendor-payment-v2-approval` | `vendor_payments_v2` | `vendor_payments_v2_approval` | `vendor_payments` |
| `ConfigTypeVendorOnboardingApproval` | `vendor-onboarding-approval` | `vendor_onboarding` | `vendor_onboarding_approval` | `vendor_experience` |

File: `internal/constants/constants.go:36-40`; `internal/dto/config_model.go:87-121`.
Confidence: confirmed.

Concrete real `ConfigAPI.CreateV2` request for payout approval (`internal/entities/config/server_get_payload.go:8-44`, used directly by a passing functional test):
```json
{
  "owner_id": "BOq6IGm4wVivek",
  "is_pending_check_required": true,
  "config_type": "payout-approval",
  "config_template": [{
    "range": "1-20000000000",
    "steps": [
      {"step": "1", "op": "AND", "roles": [
        {"role_name": "role", "role_id": "finance_l1", "approval_count": "1"},
        {"role_name": "role", "role_id": "admin", "approval_count": "1"}
      ]},
      {"step": "2", "op": "OR", "roles": [
        {"role_name": "role", "role_id": "admin", "approval_count": "1"},
        {"role_name": "role", "role_id": "owner", "approval_count": "1"}
      ]}
    ]
  }]
}
```
`(owner_id, config_type[, dimensions])` behaves as a uniqueness key (duplicate create → `twirp error invalid_argument: Config Already Exists`); `DeleteV2` soft-deletes (`Config.Enabled` flips `"true"→"false"`, a **string**, not a bool).
File: `internal/entities/config/server_get_payload.go:8-44`; `tests/functional/config_server_test.go:44-71,171`.
Confidence: confirmed.

Claim: `Template` (`internal/dto/template_model.go:56-74`) = `{Type "approval", StateTransitions map[string]{CurrentState,NextStates[]}, StatesData map[string]StateData, AllowedActions map[string]{Actions[]}, Meta{Domain, TaskListName, WorkflowExpireTime (hours), DecisionTaskTimeout (seconds)}}`. `StateData{Name, GroupName, Type ("checker"|"between"|"merge_states"), Rules{Key,ActorPropertyKey,ActorPropertyValue,Count,Min,Max,States[]}, Callbacks}`. `ConvertToTemplate` (`config_model.go:362-486`) is the compiler turning a caller's simple `RangeConfig[]` into this full state graph — `"AND"` steps insert a `merge_states` join node, `"OR"` steps fan out independently to `END_STATE`. Default `AllowedActions` (`config_model.go:555-581`): `admin`→[`update_data`,`rejected`], `owner`→[`rejected`], `user`→[`approved`,`rejected`], plus service-name-keyed reject-only entries.
File: `internal/dto/template_model.go:56-74`; `internal/dto/config_model.go:362-486,555-581`.
Confidence: confirmed.

Claim: ASL ("Amazon States Language") is a **second, distinct config-template kind** on the same `Config.TemplateKind` oneof (`AslTemplate{Type "asl", States map[string]IAslState, Meta, StartAt}`), used for non-approval workflow automation (e.g. a Splitz-experiment-activation workflow seen in a functional test, not payout-approval-specific) — `WaitState`, `TaskState` (`Resource`,`Parameters`,`ResultPath`,`TimeoutSeconds`,`HeartBeatSeconds`,`Async`), `MapState` (`ItemsPath`,`Iterator`,`ResultSelector`), `ChoiceState` (`Choices[]{Expr,Next}`,`Default`), all JSONPath-driven (`pkg/jsonpath`) — genuinely AWS-Step-Functions-shaped.
File: `internal/dto/asl_state_model.go`, `internal/dto/asl_template_model.go:12-17`; `tests/functional/workflow_server_test.go:30-117`.
Confidence: confirmed.

### A.4 State machine

Claim: **Two coexisting mechanisms.** (a) `pkg/transition` is a generic DB-persisted status/audit FSM (every transition logged to a `StateChangeLog` row inside the same DB transaction) used by three entities with their own graphs: **Workflow** (`creation_in_progress→created→initiated→processed`, plus `failed`/`init_failed`/`terminated` branches — `internal/entities/workflow/state_machine.go:12-137`), **State** (`creation_in_progress→created→pending_action→processed`, plus `terminated` — `internal/entities/state/state_machine.go:11-90`), **Action** (`creation_in_progress→created→initiated→processed`, plus `init_failed`/`failed` — `internal/entities/action/state_machine.go:12-126`). This is audit-trail bookkeeping, not business logic.

(b) The **business-logic engine** is the Cadence-workflow-executed graph driven by `Template.StateTransitions`/`StatesData` (approval) or `AslTemplate.States`/`StartAt` (ASL): `ClientManager.StartWorkflow` (`internal/client/types/approval/workflow.go:64-112`) begins at `START_STATE`, each `checker` state requires `Rules.Count` matching approvals before `hasStateCompleted` (`workflow.go:523-566`) advances via `StateTransitions[state].NextStates`, terminating at the literal `"END_STATE"` which fires the outbound callback keyed by `DomainStatus` (approved/rejected/closed) and marks the workflow `processed`. Separately from `pkg/transition`'s `Status`, **`Workflow.DomainStatus`** carries the actual business outcome, set by `markWorkflowAsProcessed` (`workflow.go:629-639`) to one of `"approved"|"rejected"|"closed"` — this is exactly what payouts' registered `CallbackDetails.WorkflowCallbacks.Processed.DomainStatus["approved"/"rejected"]` map (see A.7) keys off of.

Concrete real payout-approval `Template` JSON (from a functional-test fixture, matches this schema 1:1):
```json
{
  "meta": {"domain": "payouts", "task_list_name": "payouts-approval", "workflow_expire_time": 0, "decision_task_timeout": 0},
  "type": "approval",
  "states_data": {
    "L1_Approval": {"name":"L1_Approval","type":"checker",
      "rules":{"key":"","max":0,"min":0,"count":1,"states":null,"actor_property_key":"role","actor_property_value":"razorx_approvers"},
      "callbacks":{}, "group_name":"0"}
  },
  "allowed_actions": {"user": {"actions": ["approved","rejected"]}},
  "state_transitions": {
    "L1_Approval": {"next_states": ["END_STATE"], "current_state": "L1_Approval"},
    "START_STATE": {"next_states": ["L1_Approval"], "current_state": "START_STATE"}
  }
}
```
A two-level variant chains `L1_Approval → L2_Approval → END_STATE` — the concrete realization of "multi-level approval."
File: `internal/client/types/approval/workflow.go:64-112,523-566,629-639`; `internal/entities/{workflow,state,action}/state_machine.go`; `tests/functional/workflow_server_test.go:118-200,1777-1816`.
Confidence: confirmed.

### A.5 Multi-level approvals / approver-set evaluation

Claim: **No Passport, no AuthZ-service call, anywhere in the approve/reject path** — repo-wide `grep -rniE "authz|passport|merchant_user"` (case-insensitive, `.go` files) returns **zero hits**. `MerchantUser{Id,Role,RoleName}` (no underscore) appears in exactly one small helper: `FetchUserRoleFromUserIdAndOwnerId` (`internal/client/types/common/helper.go:29-47`) calls the **API monolith** (`rx_live` client, `https://api.razorpay.com/v1`, Basic Auth — not Passport) at `merchants/%s/internal-users`, used **only** to support the `skip_approval_if_creator_is_checker` auto-approve feature (checking if the payout's creator also holds the approver role, to auto-approve) — not general authorization on every approve call.

Claim: **General approver matching is pure string-equality trust of caller-supplied identity — no independent role-verification call.** `validateActionOnState` (`internal/entities/action/interactor.go:300-333`):
```go
actorPropertyValueMatches := slices.Contains([]string{action.GetActorPropertyValue(), action.GetActorId()}, stateRules.GetActorPropertyValue())
if stateRules.GetActorPropertyKey() != action.GetActorPropertyKey() ||
    stateRules.GetCount() == 0 || !actorPropertyValueMatches {
    return ...StateIsNotPendingOnCurrentActor
}
```
WFS checks that the `ActorPropertyKey`/`ActorPropertyValue` **the caller sent in the request** matches what's stored on the pending `State.Rules` (set at config-creation time from `role_name`/`role_id`). WFS never independently re-derives "does user X actually hold role Y" — that verification is implicitly delegated to whichever upstream caller (dashboard/API monolith/Batch) populated `actor_property_key`/`actor_property_value`. **This is a trust boundary**: any authenticated caller (any allow-listed service credential, A.8) can submit an action claiming any `ActorPropertyValue`. `validateIfActionIsAllowed` (`interactor.go:361-410`) additionally gates by `actorType` (admin/user/owner/service) against `Config.Template.AllowedActions[actorType].Actions` (A.3 defaults).

Claim: `Assignee{EntityId,EntityType,Name,Email,Metadata}` (`internal/dto/assignee_model.go`) is a separate concept — dashboard "assign this workflow to me for triage" bookkeeping (`WorkflowAPI.AddAssignee/RemoveAssignee`) — unrelated to approval-permission evaluation. `PendingOn`/`PendingOnUser` lookups similarly just string-match the *querying* caller's `ActorPropertyKey/Value` against the currently-pending state's rules, no external role lookup.
File: `internal/client/types/common/helper.go:29-47`; `internal/entities/action/interactor.go:300-333,361-410`; `internal/dto/assignee_model.go`.
Confidence: confirmed.

### A.6 OTP / `enable_approval_via_oauth`

Claim: **Absence-confirmed inside WFS's production Go code.** `grep -rniE "otp|oauth|approval_via_oauth|2fa|mfa"` excluding test files → zero hits in `internal/`, `pkg/`, `cmd/`. The only hits at all are `Otp`/`Token` fields on **e2e test models** (`tests/e2e/models/configs.go:8,18,33`) that appear to model an *external* call through the API monolith's config-management endpoints (which front WFS's `ConfigAPI` and themselves may require merchant 2FA for config *changes*) — WFS's real `ConfigCreateRequestV2`/`ConfigDeleteRequestV2` messages have no otp/token field consumed anywhere in `internal/entities/config/*.go`. WFS's entire DCS feature-flag surface (`internal/dcs/features/features.go:8-24`) is exactly two flags — `skip_approval_if_creator_is_checker` and `disable_wf_config_dimensions_for_s2p` — **`enable_approval_via_oauth` does not exist in WFS at all.**
File: `tests/e2e/models/configs.go:8,18,33`; `internal/dcs/features/features.go:8-24`.
Confidence: confirmed (absence). **This closes the payouts-side open question**: if `enable_approval_via_oauth` gates OTP/2FA on the approval action itself, it must be enforced entirely upstream of WFS (dashboard/API monolith, before `ActionAPI` is ever called) — WFS has no OTP verification code path.

### A.7 Callback delivery — service-name resolution, retry policy, timeouts, idempotency, auth

Claim: **`payouts_live` (and any logical service name) resolves via a static config table, not dynamic service discovery.** `getBasicCallbackDetails` (`internal/client/types/common/callback.go:69-99`):
```go
client, found := boot.Config.Clients[cb.GetService()]
callback.Url = strings.TrimSpace(client.Host + cb.GetUrlPath())
callback.Auth = Auth{Username: client.Username, Password: client.Password}
```
`boot.Config.Clients` is `map[string]ClientDetails{Host,Username,Password,Stub}` populated from `config/*.toml` `[clients.<name>]` blocks. Confirmed prod entries:
```toml
[clients.payouts_live]
    host = "https://payouts.razorpay.com"
    username = "payouts_live"
    password = "WORKFLOWS_CLIENTS_PAYOUTS_LIVE_PASSWORD"
[clients.payouts_test]
    host = "https://payouts.razorpay.com"
    username = "payouts_live"
    password = "WORKFLOWS_CLIENTS_PAYOUTS_TEST_PASSWORD"
```
File: `internal/client/types/common/callback.go:69-99`; `internal/config/config.go:14,58-63`; `config/prod.toml:128-143`.
Confidence: confirmed.

Claim: **Callbacks are delivered as genuine Cadence Activities** (`common.MakeApiCallAndProcessResponseActivity`, `internal/client/types/common/callback.go:121-196`, invoked via `cw.ExecuteActivity`) — Cadence itself owns retry/backoff/timeout bookkeeping, durably, surviving worker crashes/restarts. Exact retry policy for approval-type workflows (`internal/client/types/approval/workflow.go:24-45`):
```go
ScheduleToStartTimeout: 365 * 24h
StartToCloseTimeout:    30s              // per-attempt timeout
RetryPolicy: { InitialInterval: 2s, BackoffCoefficient: 5.0, MaximumInterval: 1h, ExpirationInterval: 30 days,
                NonRetriableErrorReasons: [constants.NonRetryableError] }
```
(ASL workflows use `InitialInterval 1s, BackoffCoefficient 2.0, MaximumAttempts 50`.) `HandleCallbackResponse` (`callback.go:101-113`) treats any status not in the per-callback `SuccessStatusCodes []int32` (config-defined, not hardcoded to `200`) as a **retryable** Cadence error — matching the payouts-side comment that WFS retries non-2xx and stops on 200. Per-callback HTTP request timeout defaults to `60s` if unset on the `Callback.Timeout` field (nested inside the 30s Cadence `StartToCloseTimeout` — a latent inconsistency worth flagging, since the outer Cadence attempt could time out before an overridden longer per-callback HTTP timeout completes).
File: `internal/client/types/common/callback.go:39-40,93-96,101-113,121-196`; `internal/client/types/approval/workflow.go:24-45,67`; `internal/client/types/asl/workflow.go:17-44`.
Confidence: confirmed.

Claim: **No idempotency key/header is added to the outbound callback request.** `grep -rn "idempotency_key|IdempotencyKey"` → only an e2e test model for an unrelated call, and a DB column `idempotency_key char(30)` on the `actions` table used for inbound action-creation dedup, not outbound callback headers. `Callback.Headers` is exactly whatever the config author put in `dto.Callback.Headers map[string]string` — no automatic idempotency field. Payouts must handle repeated callback deliveries (e.g. Cadence redelivering after a timeout it perceives as failure but which actually succeeded) using its own idempotency/state-check logic — which the payouts-side lane already confirmed exists (`ApprovePayout`/`RejectPayout` are safe to call again per the "WFS won't retry if 200 is sent" comment, implying the endpoint itself is expected to be safely re-callable).
File: `internal/database/migrations/20200702202525_create_actions.go:28`; `internal/client/types/common/callback.go` (no idempotency header construction found).
Confidence: confirmed (absence).

Claim: Outbound HTTP client is `github.com/gojektech/heimdall/v6/httpclient`; every callback request gets **HTTP Basic Auth** — `request.SetBasicAuth(username, password)` sourced from `boot.Config.Clients[service].Username/Password` (for `payouts_live`: exactly `username: "payouts_live"`, matching what the payouts-side lane found as `cred.Workflow`), `Content-Type: application/json` always set, and only `POST`/`PATCH` methods are supported (any other method → `"callback method isn't supported"` error).
File: `internal/client/types/common/callback.go:141-206`.
Confidence: confirmed.

### A.8 Expiry

Claim: Expiry is **bounded purely by Cadence's `ExecutionStartToCloseTimeout`**, set from the config's `Meta.WorkflowExpireTime` (hours) at workflow-start time (`internal/entities/workflow/starter.go:62-74,160-192`, `getWorkflowExpiryTime`): if `WorkflowExpireTime == 0`, defaults to **10 years** (effectively never). Validation bounds on this field for approval-type configs (`internal/entities/config/validation.go:357`): `Min = 4 months (24*365*10 max, 24*30*4 min)` — i.e. **if set at all, the minimum configurable expiry is ~4 months**, directly contradicting an hours-scale "pending payout auto-rejected after N hours" hypothesis.
File: `internal/entities/workflow/starter.go:62-74,160-192`; `internal/entities/config/validation.go:357`; `internal/entities/config/asl_validation.go:423` (ASL allows down to unset/0, max 10y).
Confidence: confirmed.

Claim: `grep -rn "NewTimer" internal pkg` → **zero hits** — there is no explicit Cadence timer/select construct inside `ClientManager.StartWorkflow` watching for approaching expiry to proactively fire a reject callback before the hard Cadence timeout. `ExecutionStartToCloseTimeout` is purely a Cadence-server-enforced kill switch; no WFS application code was found reacting to a `TimedOut` execution status to call `WorkflowCallbacks.Processed.DomainStatus["rejected"]` or any other callback.
File: absence across `internal/client/types/approval/*.go` (no `NewTimer`/`TimedOut`-handling code found).
Confidence: probable (absence claim — a genuine gap, not merely an unproven assumption): **on true Cadence-level expiry, the workflow execution likely just times out server-side with no callback fired to payouts, and WFS's own DB `Workflow.Status` row likely remains `"initiated"` forever.** This should be flagged back to the payouts/WFS owning teams as an operational risk, not treated as confirmed safe behavior.

### A.9 Events published

Claim: **Absence-confirmed** — no Kafka producer, no SQS publish call, no message-bus "event" terminology tied to state transitions anywhere in `internal/` (ties to A.1's `pkg/queue` dead-code finding). State-transition "eventing" for other systems is entirely the outbound HTTP callback mechanism (A.7) plus Prometheus counters/histograms (`internal/entities/{workflow,state,action}/metric.go`) for internal observability — there is no message-bus fan-out for other consumers to subscribe to independent of being a registered callback target.
Confidence: confirmed (absence).

### A.10 Inbound auth

Claim: Plain **HTTP Basic Auth**, credential-allowlist membership check — no JWT/Passport/mTLS. `hooks.Auth(creds ...config.BasicAuth)` (`internal/boot/hooks/auth.go:47-72`) is a Twirp `RequestRouted` hook doing exact `username == cred.Username && password == cred.Password` against the passed-in list; on failure, `twirp.Unauthenticated`. `credentials.Payouts` = `[auth.payouts]` in `config/prod.toml:185-192`: `username = "payouts"`, secret-templated password — **distinct from** the outbound-callback credential (`[clients.payouts_live]`, `username = "payouts_live"`): inbound calls **from** payouts authenticate as `payouts`/`<secret>`; outbound calls WFS makes **to** payouts authenticate as `payouts_live`/`<secret>` (or `payouts_test`) — two separate credential pairs for the two directions. All four Twirp services share one allow-list (A.2) — no per-RPC restriction.
File: `internal/boot/hooks/auth.go:26-82`; `config/prod.toml:185-192`.
Confidence: confirmed.

### A.11 How the dashboard/API monolith triggers approve/reject

Claim: The relevant RPCs are `ActionAPI.Create` (single action on a known `state_id`), `CreateWithEntityId` (resolve pending state(s) by `entity_id`/`entity_type`/`config_id` — the natural "approve this payout" call when the caller doesn't know the internal `state_id`), or `CreateDirectOnWorkflow` (act on a known `workflow_id`). Approver identity is carried entirely via plain request fields (`ActorId`, `ActorType`, `ActorPropertyKey`, `ActorPropertyValue`) — **no Passport/JWT claim parsing**; the Twirp auth layer (A.10) authenticates only the *calling service*, never the end human user — the dashboard/API monolith is trusted to have already authenticated/authorized the human and pass along accurate `Actor*` fields (same trust boundary as A.5).

Claim: Dispatch is `createEntitiesAndSignalWorkflow` (`internal/entities/action/interactor.go:570-596`) — persists `Action`/`Comment` rows in a DB transaction, then `signalAction.SignalWorkflow` (`internal/entities/action/signal.go:23-60`):
```go
adapter, _ := cadence.GetAdapterForDomain(ctx, domain)
adapter.CadenceClient.SignalWorkflow(ctx, workflow.GetExecutionId(), "", common.SignalName, a.Action)
```
i.e. the human approve/reject click becomes a **Cadence Signal** delivered to the already-running workflow execution, processed asynchronously (`startProcessingAction`, `workflow.go:661-693`) and **re-validated server-side** against `State.Rules` (A.5) before any state mutation or callback fires. `ActionAPI.Create*` is thus "fire a signal and return quickly" — a `SignalWorkflow` transport error marks the action `init_failed` synchronously, but the actual approval logic's success/failure happens asynchronously inside the Cadence workflow (not reflected in the RPC's immediate response).
File: `internal/entities/action/interactor.go:570-596`; `internal/entities/action/signal.go:23-60`; `internal/client/types/approval/workflow.go:123-160,661-693`.
Confidence: confirmed.

### A.12 Bulk

Claim: **Absence-confirmed, repo-wide.** `grep -rni "bulk"` (all files, including tests/comments) → **zero hits anywhere in `razorpay/workflows`.** There is no bulk-approve/bulk-action RPC, endpoint, or helper function with "bulk" in its name. **This closes the payouts-side open question definitively**: any "select-all-and-approve" flow (dashboard or Batch-driven) must be issuing repeated individual `ActionAPI.Create`/`CreateWithEntityId`/`CreateDirectOnWorkflow` calls — one Cadence Signal per payout/workflow — from Batch service or the API monolith. WFS itself provides no server-side fan-out primitive. This is fully consistent with and now cross-confirms section B.8's finding that Batch's own `payout_approval` batch type talks directly to Payouts Service's `payouts/bulk_approve` HTTP endpoint, not to WFS at all.
Confidence: confirmed (absence).

### A.13 Docs

`README.md` confirms Cadence is required local infrastructure (docker-compose for `uber/cadence`, ports 7933 tchannel/7833 grpc/8088 cadence-web/3000 grafana) and documents the `make proto-fetch && make proto-refresh` external-proto workflow (A.2). No `AGENTS.md`/`CLAUDE.md`/`.claude/` rules directory exists in this repo (absence-confirmed). `docs/` is `.gitignore`'d and empty in this checkout.
Confidence: confirmed.

### A.14 Functional tests as spec — additional concrete examples

`CommentAPI.Create` request/response (`tests/functional/comment_server_test.go:217-227`): `commentv1.Comment{WorkflowId, CommentorId:"100000Razorpay", CommentorType:"user", Comment:"test comment", OwnerId:"100000Razorpay", OwnerType:"user", Service:"workflows"}` echoes back all fields plus `Id`/`CreatedAt`. Validation-failure surfaces as `twirp error internal: commenter_id: cannot be blank.` (note: `Internal`, not `InvalidArgument`, for this specific RPC).

Concrete DB fixture of a real payout-approval workflow post-`Create` (`tests/functional/workflow_server_test.go:1777-1816`): `Workflow.Status: "initiated"`, `ExecutionId` a UUID-shaped Cadence workflow id; paired `State{Name:"L1_Approval", Status:"pending_action", Type:"checker", Rules.ActorPropertyKey:"role", Rules.ActorPropertyValue:"razorx_approvers"}`.
Confidence: confirmed.

### A.15 Summary — direct answers to the payouts-side open questions

1. **`payouts_live` resolution**: static config table (`config/prod.toml:137-140`), not dynamic discovery (A.7).
2. **Retry policy on callbacks**: Cadence Activity retry, `InitialInterval 2s, BackoffCoefficient 5.0, MaximumInterval 1h, ExpirationInterval 30 days`, per-attempt `StartToCloseTimeout 30s`, HTTP timeout default 60s, Basic Auth `payouts_live`/secret (A.7).
3. **Expiry**: bounded purely by Cadence `ExecutionStartToCloseTimeout` = `Meta.WorkflowExpireTime` hours (min ~4 months if set at all, default 10 years/never) — **no evidence any reject-callback fires on expiry**; a real gap (A.8).
4. **`enable_approval_via_oauth`**: does not exist anywhere in WFS — must be enforced entirely upstream, before `ActionAPI` is called (A.6).
5. **Bulk approval**: WFS has zero bulk RPC — Batch/API-monolith issues one `ActionAPI` call per payout (A.12), consistent with Batch's own `payouts/bulk_approve` HTTP path found independently in section B.

---

## B. BATCH SERVICE (razorpay/batch)

### B.0 Orientation documents (read first, quoted verbatim where load-bearing)

`ARCHITECTURE.md` (repo root) is the authoritative overview:

> In total there are 7 deployments. The Deployments are.,
> 1. batch-web
> 2. batch-sqs
> 3. batch-sqs-art-dual-write
> 4. batch-sqs-art-prs
> 5. batch-sqs-reconciliation
> 6. batch-sqs-sftp-notification
> 7. batch-sqs-payment-links
>
> There is one postgres database used for persisting the data `prod-batch-live`
>
> ... Any user or service can upload a csv file with necessary details to Batch Service. Batch then processes the files and provides the output. For example, Batch service is used to perform Bulk Payouts.
>
> The whole architecture can be divided into 3 sections. They are., 1. Staging 2. Processing 3. Reporting
>
> ### Web Processing — "When a client hits Batch API, Batch will immediately start processing the request."
> ### Worker Processing — "When a client hits Batch API, Batch pushes the message to a queue and a worker polls the message and processes the request."

File: `ARCHITECTURE.md:1-45`. Confidence: confirmed (doc; cross-checked against code below).

`.claude/rules/*.md` (repo-authored orientation, cross-checked below against actual code — one claim found **stale**, flagged in B.9):

- `batch-domain.md`: "Input file max size: 61MB (API gateway limit)"; Settings field is JSONB, consumer-specific config at batch create; "Schedule minimum is 1 hour in the future, hourly granularity only."
- `batch-engine.md`: "Spring Batch is wrapped by custom JobEngine/JobExecutionService/JobLifeCycleManager"; "Retry is handled by Spring RetryTemplate — two strategies: fixed (5 attempts, 5s) and exponential"; "Continuous 5xx → JobFailureException unless failOnServerError=false"; "429 (throttled) triggers retry with backoff."
- `batch-entry-domain.md`: "Idempotency key is sent with every API call to consumer service via Record.id"; "~90+ ApiProcessor implementations exist (one per batch type)."
- `batch-type-domain.md`: "Batch type behavior defined entirely by JSON config files in src/main/resources/ — not code"; "BatchType.id must exactly match the batch type ID in consumer API codebase"; "maxThreads in data processing step controls parallelism (up to 10)."
- `file-store-domain.md`: BU-level S3 bucket routing; all files under `batch/` prefix; `BatchFileType` enum INPUT/OUTPUT.
- `src/main/java/com/razorpay/batch/core/AGENTS.md`: "**Distributed mutex** in `BatchCore` — Redis lock prevents concurrent processing of same batch." **This claim is not found in code** — see B.9.

File: `.claude/rules/batch-domain.md`, `batch-engine.md`, `batch-entry-domain.md`, `batch-type-domain.md`, `file-store-domain.md`; `src/main/java/com/razorpay/batch/core/AGENTS.md`. Confidence: confirmed (doc content); mutex claim confidence: probable-false (see B.9).

### B.1 Stack & deployables

Claim: Spring Boot / Gradle, Java. Deployable split is **7 Kubernetes deployments** sharing one codebase, differentiated by entrypoint/queue-consumer config: `batch-web` (synchronous HTTP-triggered processing) and 6 `batch-sqs*` worker variants (`batch-sqs`, `batch-sqs-art-dual-write`, `batch-sqs-art-prs`, `batch-sqs-reconciliation`, `batch-sqs-sftp-notification`, `batch-sqs-payment-links`) that poll SQS queues and run the same Spring Batch job engine.
File: `ARCHITECTURE.md:9-16`; `k8s/stage/deployment.yaml`; `src/main/java/com/razorpay/batch/queue/sqs/SqsWorkerController.java`.
Confidence: confirmed (doc + presence of SQS worker controller and separate deployment manifests).

Claim: Datastore — one Postgres DB (`prod-batch-live`) persists `Batch`, `BatchEntry`, `BatchType`, `FileStore` entities (JPA/Hibernate); Redis used both as a general cache (`StringCacheRepository`) and for the Redis-mutex-lock idempotency mechanism (B.4); S3 (via `DocumentStore`/`S3DocumentStore`) for input/output file storage under a `batch/` prefix, with BU-level bucket routing.
File: `ARCHITECTURE.md:17`; `src/main/java/com/razorpay/batch/entity/{Batch,BatchEntry,BatchType,FileStore}.java`; `.claude/rules/file-store-domain.md`; `src/main/java/com/razorpay/batch/cacherepository/impl/{StringCacheRepositoryImpl,RedisMutexLock}.java`.
Confidence: confirmed.

Claim: Outbound HTTP client is Spring's `RestTemplate` (`RestClientUtils`, `restClientUtils.postResource(...)`), with retry via Spring `RetryTemplate` (`ApiCallDataProcessorHelper`), not a service-mesh/gRPC client — every downstream call (including to Payouts Service) is a plain templated JSON-over-HTTP POST.
File: `src/main/java/com/razorpay/batch/utils/RestClientUtils.java`; `src/main/java/com/razorpay/batch/batchengine/item/processor/ApiCallDataProcessorHelper.java:48-95`.
Confidence: confirmed.

### B.2 Batch types relevant to payouts

Config-driven job definitions live as JSON files in `src/main/resources/*.json`, one per `BatchType.name` (`.claude/rules/batch-type-domain.md`: "BatchType.name must exactly match the JSON config filename"). Payout-relevant files found:

| Batch type (config filename) | Purpose | Endpoint called | ApiProcessor Java class |
|---|---|---|---|
| `payout.json` | Classic bulk-payout create (CSV upload of payouts) | `payouts/bulk` | `PayoutApiProcessorImpl` (`@JsonTypeName("PayoutApiProcessor")`) |
| `payout_approval.json` | Bulk approve/reject of already-pending payouts | `payouts/bulk_approve` | `PayoutApprovalProcessorImpl` (`@JsonTypeName("payoutApprovalProcessor")`) |
| `payout_link_bulk.json` | Bulk Payout Links create | `payout-links/batch` | `PayoutLinkBulkApiProcessorImpl` (`@JsonTypeName("payoutLinkBulkApiProcessor")`) |
| `payout_link_bulk_v2.json` | v2 variant of bulk Payout Links (routed by `version` flow-control to `/v2/` config dir) | `payout-links/batch` (v2 config) | `PayoutLinkBulkApiProcessorImpl` |
| `v2/payouts_amazonpay_bene_id_process.json`, `v2/payouts_amazonpay_bene_details_process.json`, `v2/payouts_bank_transfer_bene_id_process.json`, `v2/payouts_bank_transfer_bene_details_process.json`, `v2/payouts_upi_bene_id_process.json`, `v2/payouts_upi_bene_details_process.json` | Newer "v2" bulk-payout flows keyed by beneficiary id vs full beneficiary details, per payout mode (Amazon Pay wallet / bank transfer / UPI) | `payouts/bulk` (same endpoint as classic `payout.json`) | shared processor(s), registered under `PayoutConstant.getPayoutBatchTypes()` |
| `tally_payout.json` | Tally-accounting-software-originated payout batch | (Tally-specific) | `TallyPayoutApiProcessorImpl` |
| `fund_account.json`, `fund_account_v2.json` | Bulk fund-account (beneficiary) creation, a prerequisite for payouts | (fund-account create endpoint, not traced in depth — out of this lane's scope) | not traced in depth |

File: `src/main/resources/{payout,payout_approval,payout_link_bulk,payout_link_bulk_v2,tally_payout}.json`; `src/main/resources/v2/payouts_*.json`; `src/main/java/com/razorpay/batch/batchengine/item/processor/apiprocessor/{PayoutApiProcessorImpl,PayoutApprovalProcessorImpl,PayoutLinkBulkApiProcessorImpl,TallyPayoutApiProcessorImpl}.java`; `src/main/java/com/razorpay/batch/constants/PayoutConstant.java:63-96`.
Confidence: confirmed.

Claim: There is **no `payout_validate.json`** file (the `<template>_validate` naming convention from the task brief does not apply to the bulk-payout-create flow). The genuine `_validate`/`_process` two-phase suffix convention exists in this repo but is used by a different family — `vendor_payments_*_batch_validate` (`VendorPaymentsConstant.java:11-15`) — and is implemented generically in `BatchCore.processV2Batch()` per `src/main/java/com/razorpay/batch/core/AGENTS.md:15` ("**v2 two-phase flow** in `BatchCore.processV2Batch()` — `_validate` vs `_process` batch type suffix") and `src/main/java/com/razorpay/batch/core/BatchCore.java:147-156` (`if (!StringUtils.endsWith(batchTypeId, "_validate")) batchTypeId.append("_validate"); ... int validateSuffixIndex = batchTypeId.lastIndexOf("_validate");`). Payout's own pre-flight validation instead goes through a *different*, generic mechanism — see B.3.
File: `ls src/main/resources/*.json` (absence); `src/main/java/com/razorpay/batch/core/BatchCore.java:147-156`; `src/main/java/com/razorpay/batch/constants/VendorPaymentsConstant.java:11-15`.
Confidence: confirmed (absence) / confirmed (mechanism exists, but for a different consumer).

### B.3 Row validation rules — confirmed absent at the field-semantics level; Batch validates structure only

Claim: Batch has **no IFSC-format, account-number-format, amount-range, or mode/purpose-enum validators** anywhere in its Java source for the payout batch types. `grep -rniE "ifsc" src/main/java` returns zero hits tied to payout logic (only unrelated NACH/emandate/auth-link/recurring-charge code touches an "ifsc" column, for different batch types). `payout.json`'s step list is `DataStaging → DataProcessing → OutputCreation → Notification` — **no `dataValidationStepProcessor` step** is present (contrast: 23 *other* batch-type JSON configs, e.g. `accounting_integrations_bulk_ack_items.json`, do declare one).
File: `src/main/resources/payout.json:4-260` (no `"type": "dataValidationStepProcessor"` in the step list); `grep -rniE "ifsc" src/main/java` (no payout hits).
Confidence: confirmed (absence).

Claim: The one pre-flight validation endpoint Batch itself owns — `POST /direct/validate` (`ApiController`, see B.7) → `BatchValidationService.validateBatch()` — validates only: (1) file MIME/extension is supported (`ValidateConstant.SUPPORTED_FILE_TYPES`), (2) uploaded file's header row contains every `columnNames` entry declared in the batch type's staging-step JSON config (structural header match, not per-cell semantics), (3) total row count ≤ `BatchLimits` (`DEFAULT_LIMIT = 100000`, no payout-specific override), and (4) a coarse per-row "processable" tally where `isValidEntry()` only checks a `merchant_id` column is non-empty (a column `payout.json`'s own header list does **not** contain — so this generic check is effectively inert for the payout template specifically).
File: `src/main/java/com/razorpay/batch/monolith/BatchValidationService.java:57-95` (`validateBatch`), `123-163` (`findMissingHeaders`), `219-233` (`validateLimits`), `410-424` (`isValidEntry`); `src/main/java/com/razorpay/batch/constants/BatchLimits.java:9-24`.
Confidence: confirmed.

**Conclusion**: real field-level row validation for payouts (amount range, IFSC checksum/format, account-number format, mode ∈ {IMPS,NEFT,RTGS,UPI}, purpose code, idempotency-key presence/uniqueness) happens **entirely downstream, inside Payouts Service** at `/v1/payouts/bulk` (and its pre-flight `/v1/payouts/bulk/validate`, per the payouts-side lane) — Batch forwards rows largely as-is, decorated with a machine-generated idempotency key (B.4), and only rejects a file outright for structural reasons (wrong file type, missing header column, too many rows).

There **is** a generic, config-driven JSON-Schema row validator available in the engine (`GenericDataValidatorImpl`, `@JsonTypeName("genericDataValidator")`, backed by `com.github.fge.jsonschema`, wired via a `dataValidationStepProcessor` step with a `schema` field) — used by 23 other batch types — but not by `payout.json` or `payout_approval.json`.
File: `src/main/java/com/razorpay/batch/batchengine/item/processor/GenericDataValidatorImpl.java:26-79`.
Confidence: confirmed.

### B.4 Processing pipeline — chunking, concurrency, idempotency-key derivation, and the two-tier idempotency design

`payout.json`'s `DataProcessing` step (`src/main/resources/payout.json:49-166`):
```json
"maxThreads": 10, "chunkSize": 1, "enableBulk": true, "bulkSize": 5,
"dataReader": { "type": "batchEntryDataReader", "batchEntryStatusList": ["CREATED"], "saveState": false, "pageSize": 1000 },
"dataProcessor": { "type": "bulkApiCallDataProcessor", "apiProcessor": { "type": "payoutApiProcessor", ... "idempotentKey": "idempotency_key" }, "endpoint": "payouts/bulk", "httpMethod": "post", "failOnServerError": false }
```
- **Row read**: `batchEntryDataReader` pages 1000 `BatchEntry` rows at a time with `status = CREATED` (no `saveState`, i.e. no Spring Batch execution-context checkpoint of reader position between restarts — restart instead resumes "from the failed row using sequence numbers," per `.claude/rules/batch-domain.md`).
- **Outbound-call grouping ("chunk size" in the task's sense)**: `enableBulk: true` + `bulkSize: 5` — up to **5 rows per outbound `POST payouts/bulk` call**.
- **Worker concurrency**: `maxThreads: 10` — up to **10 concurrent threads** within one job execution each independently building and sending its own 5-row bulk call. (`.claude/rules/batch-type-domain.md`: "maxThreads in data processing step controls parallelism (up to 10)".)
File: `src/main/resources/payout.json:49-68`.
Confidence: confirmed.

**Idempotency-key derivation — two different mechanisms coexist, with materially different safety, and `payout.json` uses the weaker one:**

1. **Default / classic path** (`bulkApiCallDataProcessor`, used by `payout.json` and `payout_approval.json`): `BulkApiCallDataProcessorImpl.process()` derives the key purely as `idempotentKeyPrefix + record.getId()` where `idempotentKeyPrefix = "batch_"` (a class field default) and `record.getId()` is the `BatchEntry.id` — a Hibernate-generated globally-unique primary key assigned **once**, at row-insert time during the Staging step (`BatchEntry` constructor: `this.setId(record.getId())`, and `Record.id` is populated from the persisted `BatchEntry.id` when the row is (re-)read). **No Redis lock, no cross-row/cross-file duplicate-content check, no distributed coordination of any kind** — the key is stable for a given `BatchEntry` row across retries (same row, same key, which is safe for straightforward per-row Spring Batch retry), but nothing in Batch prevents two different execution contexts (e.g. a resumed/duplicated Spring Batch step execution, or two pods both reading page 1 of `CREATED` rows if they are ever pointed at the same batch) from both picking up the same `BatchEntry` row concurrently and firing two outbound `payouts/bulk` calls carrying the **identical** `idempotency_key` at the same time.
   File: `src/main/java/com/razorpay/batch/batchengine/item/processor/BulkApiCallDataProcessorImpl.java:106,163-166`; `src/main/java/com/razorpay/batch/entity/BatchEntry.java:29-31,55-58` (`@GeneratedValue` id; `id` set once from `Record`); `src/main/java/com/razorpay/batch/batchengine/Record.java:15`.
   Confidence: confirmed.

2. **Newer / hardened path** (`cacheIdempotencyEnabledBulkApiCallDataProcessor`, used by `payout_link_bulk.json`, `payout_link_bulk_v2.json`, and all six `v2/payouts_*_bene_*_process.json` types — **but not** `payout.json`): `CacheIdempotencyEnabledBulkApiCallDataProcessorImpl.generateIdempotencyKey()` computes a SHA-1 hash of a batch-type-specific set of business columns (`CacheBasedIdempotencyHelper.generateUniqueHash`, columns from `PayoutConstant.getPayoutIdempotencyColumns()` — fund-account id, amount, reference id, phone/account number, IFSC, mode, UPI id), looks it up in Redis under key `payouts:%s:%s` (merchant, hash), and — on a cache miss — acquires a **`RedisMutexLock`** (`payouts:mtx:%s:%s`, TTL `PAYOUT_IDEMPOTENCY_MUTEX_LOCK_DURATION_SECONDS = 300`s) before minting a fresh UUID idempotency key and caching it for `PAYOUT_IDEMPOTENCY_TTL_HOURS = 24`h; a concurrent second attempt on the same row-hash either sees the cached key (and is short-circuited as a duplicate, once the first attempt's response updates the cache to `<key>:<payout_id>`) or fails to acquire the mutex and is explicitly rejected with `MUTEX_ACQUIRE_FAILED_ERROR_MESSAGE`.
   File: `src/main/java/com/razorpay/batch/batchengine/item/processor/CacheIdempotencyEnabledBulkApiCallDataProcessorImpl.java:104-172`; `src/main/java/com/razorpay/batch/batchengine/item/processor/idempotency/CacheBasedIdempotencyHelper.java:64-76`; `src/main/java/com/razorpay/batch/constants/PayoutConstant.java:48-53`.
   Confidence: confirmed.

**This is the concrete, code-level "known race on duplicate idempotency keys across pods"**: the classic bulk-payout-**create** flow (`payout.json`, still the default/primary payout batch type per `ARCHITECTURE.md`'s "Batch service is used to perform Bulk Payouts" framing) relies solely on `BatchEntry.id` stability and Payouts Service's own server-side `(merchant_id, idempotency_key)` uniqueness/mutex (established in the payouts-side lane) to arbitrate duplicates — there is **no Batch-side distributed lock** protecting it, unlike the newer v2 beneficiary-process types and Payout Links, which both call structurally-identical or adjacent endpoints (`payouts/bulk`, `payout-links/batch`) but were hardened with the Redis-mutex mechanism. Whether this gap has ever caused a real double-payout in production was not determined from static code (would require incident/metrics evidence outside this repo).
Confidence: confirmed (mechanism gap); probable (this being the literal incident root cause — not proven from code alone).

Claim: Batch **does not implement its own batch-level distributed lock** to prevent two pods from concurrently processing the same `Batch`/`BatchEntry` rows, despite `src/main/java/com/razorpay/batch/core/AGENTS.md:28` explicitly asserting "**Distributed mutex** in `BatchCore` — Redis lock prevents concurrent processing of same batch." `grep -rn "RedisMutexLock\|getMutexLock" src/main/java` matches only `CacheIdempotencyEnabledBulkApiCallDataProcessorImpl.java` (the per-row idempotency lock in B.4.2) and the cache-repository implementation classes themselves — **zero matches in `BatchCore.java`** or anywhere else in `core/` or `batchengine/`. `Batch.java` also has no `@Version` optimistic-locking column on its `status` field.
File: `src/main/java/com/razorpay/batch/core/AGENTS.md:28` (claim); `grep -rn "RedisMutexLock\|getMutexLock" src/main/java` (evidence of absence outside the idempotency processor); `src/main/java/com/razorpay/batch/entity/Batch.java:100` (`status` field, no `@Version`).
Confidence: confirmed (absence in code) contradicting a repo-authored orientation doc — this AGENTS.md claim should be treated as **stale/inaccurate**, not as ground truth.

Retry policy on the outbound `payouts/bulk` call (applies to all bulk-API-call batch types unless a JSON config sets `"retryStrategy": "exponential"` — `payout.json` does not, so it gets the fixed-backoff template):
- `RetryTemplate` chosen by `ApiCallDataProcessorHelper.chooseRetryTemplateTypeBasedOnInput` — default = **fixed backoff**, `resttemplate.retry.max-attempts=5` attempts, `resttemplate.read.back-off-period=5000`ms (5s) between attempts (exponential alternative: `initialInterval=1500ms, multiplier=2, maxInterval=18000ms`, same 5-attempt cap).
- Retryable exceptions: `SocketTimeoutException`, `ResourceAccessException`, `HttpRetryException`, plus any status code mapped in via `StatusCodeUtil.getErrorClassesFromStatusCodes` (i.e. configured `excludedStatusFromRetry`/retryable-status lists) and `ThrottledClientException` (429).
- On continuous 5xx after exhausting retries: `failOnServerError` (explicitly `false` in `payout.json`) controls whether the whole job fails (`JobFailureException`) or the affected rows are just marked failed and the batch continues.
- Outbound HTTP read timeout: `resttemplate.read.timeout=60000`ms (60s) default (a longer 120s override exists only for the `reconciliation` batch type).
File: `src/main/java/com/razorpay/batch/batchengine/item/processor/ApiCallDataProcessorHelper.java:48-95`; `src/main/resources/application.properties:199-202,239-241`; `src/main/java/com/razorpay/batch/configuration/AppConfiguration.java:138-148`; `src/main/resources/payout.json:124` (`"failOnServerError": false`).
Confidence: confirmed.

### B.5 The call into Payouts Service `payouts/bulk` — headers, auth, base path, request/response shape

Claim: The **default** outbound base path (used by `payout.json`, `payout_approval.json`, and any batch type not special-cased in `BulkApiCallDataProcessorImpl.getBasePath()`) is `appConfiguration.getApiBasePath()`, configured as `api.basepath=https://api.razorpay.com/v1/` in prod (`application-prod-rzpx.properties:27`; templated as `${API_BASE_PATH}` in the generic `application.properties:72`). This means Batch calls `POST https://api.razorpay.com/v1/payouts/bulk` — **the public API-monolith edge domain**, not a direct internal Payouts-Service host — consistent with UNRESOLVED_QUESTIONS #9's open question about `payouts-proxy-cutover` Kong routing on `api.razorpay.com` deciding whether this actually lands on the monolith or is proxied straight to Payouts Service.
File: `src/main/java/com/razorpay/batch/batchengine/item/processor/BulkApiCallDataProcessorImpl.java:211-280` (`getBasePath`, falls through every special case to `return appConfiguration.getApiBasePath();` at line 280); `src/main/resources/application-prod-rzpx.properties:27`; `src/main/java/com/razorpay/batch/configuration/AppConfiguration.java:15`.
Confidence: confirmed (base path + routing surface); probable (whether the monolith or Kong ultimately forwards to the Payouts microservice — not re-derived here, see UNRESOLVED_QUESTIONS #9).

Claim: Auth is **HTTP Basic**, not Passport/JWT as the payouts-side lane inferred for the *inbound* side of `/v1/payouts/bulk` — reconciling the two: Batch authenticates as if it *were* the merchant's own API key pair, using a scheme the edge/monolith must translate into a genuine Passport-authenticated internal call before it ever reaches Payouts Service's `PassportAuthentication`-guarded route. Concretely: `RestCallUtils.computeAuthHeader` builds `Authorization: Basic base64(rzp_<mode>_<entityId>:<apiSecret>)` where `entityId` = the batch's `X-Entity-Id` (merchant/account id) and `apiSecret` = `appConfiguration.getApiSecret()` = `api.secret` = `${BATCH_API_SECRET:secret}` (a single shared secret configured for the whole Batch service, **not** a per-merchant key_secret) — for `authType = internal` the username instead drops the entity id (`rzp_<mode>`). `payout.json` does not override `authType` in its `dataProcessor`, so it uses the class default `authType = AuthTypeConstant.PROXY = "proxy"` (i.e. the merchant-impersonation form, not `"internal"`).
File: `src/main/java/com/razorpay/batch/utils/RestCallUtils.java:21-69` (`setDefaultHeaderInformation`, `setDefaultHeaders`, `computeAuthHeader`); `src/main/java/com/razorpay/batch/batchengine/item/processor/BulkApiCallDataProcessorImpl.java:84` (`authType = AuthTypeConstant.PROXY` default), `364-368` (default-branch header construction using `appConfiguration.getApiSecret()`); `src/main/resources/application-prod-rzpx.properties:155` (`api.secret=${BATCH_API_SECRET:secret}`); `src/main/java/com/razorpay/batch/constants/AuthTypeConstant.java`.
Confidence: confirmed.

Claim: Headers sent on every outbound bulk call (`RestCallUtils.setDefaultHeaders`): `Authorization: Basic ...`, `Content-Type: application/json`, **`X-Batch-Id: <jobExecutionService.getJobId()>`** (this is the Batch entity's own id — the exact header name `X-Batch-Id` is a byte-for-byte match with the payouts-side lane's documented `constants.HeaderBatchID` expectation), `X-Creator-Id`, `X-Creator-Type`, `X-Entity-Id: <merchant/account id>`.
File: `src/main/java/com/razorpay/batch/constants/HeaderConstant.java:9-19`; `src/main/java/com/razorpay/batch/utils/RestCallUtils.java:38-53`.
Confidence: confirmed.

Claim: Outbound request body for `payout.json` is a **raw JSON array** (not wrapped in an envelope object) — `BulkApiCallDataProcessorImpl.getRetryContext`'s default `else` branch does `restClientUtils.postResource(url, requestString, httpHeaders)` where `requestString` is a `List<Map<String,Object>>` built by `BaseApiProcessor.buildRequestBody(Map<String,BulkApiContextDTO>)`, one element per row in the current 5-row bulk group, each with the configured `idempotentKey` field name (`idempotency_key`) injected with the derived key. Exact per-item shape (from `payout.json:125-155`'s `parameters` template, `##Column Header##` placeholders substituted from the CSV row, optional/settings fields merged in by `PayoutApiProcessorImpl.passSettingsIfRequired`):

See full schema in section **C.2** below.

File: `src/main/java/com/razorpay/batch/batchengine/item/processor/BulkApiCallDataProcessorImpl.java:120-193,581-582` (default-branch send); `src/main/java/com/razorpay/batch/batchengine/item/processor/apiprocessor/BaseApiProcessor.java:235-253` (`buildRequestBody`); `src/main/resources/payout.json:125-155`.
Confidence: confirmed.

Claim: Expected response — a single HTTP response for the whole 5-row (or fewer) group, JSON object with an array field named `items` (configurable via `bulkApiResponseKey`, default `"items"`) whose elements are matched back to the original per-row context by the `idempotentKey` field name (`idempotency_key`, echoed) — `BaseApiProcessor.processBulkApiResponse` reads `item[idempotentKey]`, looks up the matching `BulkApiContextDTO`, and copies the **entire item map** onto `record.responseColumns` (so any field an item carries — e.g. `id` (payout id), `http_status_code`, `error.code`, `error.description` — becomes directly available to the `OutputCreation` step, which for `payout.json` explicitly emits `id`, `error.code`, `error.description` as extra output columns). If overall HTTP status is not in `successStatusCode` (`[200]`), every row in the group is marked failed using the whole response body as shared error context. `handleBulkApiFailure` additionally inspects each item's `http_status_code`; if any item's code is in `retryAbleStatusCode` (`[500]` default), it throws `HttpRetryException`, driving the outer Spring `RetryTemplate` to retry the **entire group**.
File: `src/main/java/com/razorpay/batch/batchengine/item/processor/apiprocessor/BaseApiProcessor.java:328-404` (`processBulkApiResponse`, `handleBulkApiFailure`); `src/main/resources/payout.json:204-206` (output columns `id`, `error.code`, `error.description`).
Confidence: confirmed.

### B.6 Status polling / progress tracking

Claim: Batch does **not** poll Payouts Service (`GET /v1/payouts?batch_id=...`) for progress. Because `payout.json`'s `DataProcessing` step is fully synchronous within the Spring Batch step execution (blocking HTTP calls per B.4/B.5), Batch already has the per-row outcome the moment each `payouts/bulk` HTTP response returns and writes it straight to `BatchEntry.responseData`/status via `batchEntryDataWriter` (`PROCESSED`, per `payout.json:157-162`). Progress for **Batch's own caller** (dashboard/API monolith) is instead exposed via Batch's **own** edge route `GET /batch/{id}` (`BatchController.getById`), which returns the `Batch` entity carrying `status`, `totalCount`, `processedCount`, `failureCount` (updated incrementally by `BatchEntryDataWriterImpl` during processing, per `.claude/rules/batch-domain.md`). No evidence of Batch calling out to Payouts Service `GET /v1/payouts?batch_id=` was found (`grep` for that path/pattern in `src/main/java` returns nothing tied to payouts).
File: `src/main/java/com/razorpay/batch/api/BatchController.java:96-107` (`getById`); `.claude/rules/batch-domain.md` (amounts updated by `BatchEntryDataWriterImpl`); `src/main/resources/payout.json:157-166`.
Confidence: confirmed (Batch's own status surface); confirmed (absence of GET-by-batch_id polling call to Payouts Service in Batch's source).

### B.7 "Batch Edge Routes"

Claim: The concept maps onto two concrete Spring `@RestController` route groups:

1. **`/batch/*`** (`BatchController`, `@RequestMapping("/batch")`) — the primary caller-facing (dashboard/API-monolith-facing) surface:
   - `POST /batch` → `BatchService.create(BatchCreateDTO)` — create + immediately start (or queue) a batch. Request schema in **C.1**.
   - `PUT /batch` → `BatchService.process(ProcessBatchDTO)`.
   - `POST /batch/{id}/trigger?filestore_id=` → start a previously-`SCHEDULED` batch (used by the reminders/scheduler service callback).
   - `PATCH /batch/{id}/settings` → `BatchService.updateSettings` (mutate the JSONB `settings` field, e.g. to set `user_comment`/`scheduled_at` before processing).
   - `GET /batch/{id}` → fetch one `Batch` (status/counts — the status-polling surface, B.6).
   - `GET /batch/getBatches/{ids}`, `GET /batch` (search), `GET /batch/pageable`, `GET /batch/{batchId}/download` (signed output-file URL), `GET /batch/validateFileName`.
   - `POST /batch/{batchId}/{action}` and `POST /batch/{batchId}/{action}/force` → lifecycle actions (start/pause/restart/cancel/complete — action strings validated by `JobValidator` per `.claude/rules/batch-domain.md`).
   File: `src/main/java/com/razorpay/batch/api/BatchController.java:1-220`.

2. **`/direct/*`** (`ApiController`, `@RequestMapping("/direct")`) — explicitly documented in-code as the monolith-migration compatibility surface: *"This controller is a specific design to migrate API Monolith code to batch Service WHY: API transform batch response and send response in new structure to upstream Service."* Includes `POST /direct/validate` → `BatchValidationService.validateBatch` (the pre-flight structural validator, B.3), plus file-store/signed-URL transform endpoints.
   File: `src/main/java/com/razorpay/batch/api/ApiController.java:1-108` (comment at lines 35-38).

Confidence: confirmed.

### B.8 Approval integration (`BATCH_SERVICE_PAYOUT_APPROVAL_BULK`) — found, and it resolves an open question from the payouts-side lane

Claim: The literal symbol `BATCH_SERVICE_PAYOUT_APPROVAL_BULK` does **not** appear anywhere in this repo's source (`grep -rn "BATCH_SERVICE_PAYOUT_APPROVAL_BULK" src` — zero hits) — so it is not a Batch-side constant/config key; if it exists at all it must be a Payouts-Service- or API-monolith-side symbol naming *this* integration from the other side. **The integration itself is real and fully config-driven**: `payout_approval.json` is a first-class batch type whose `DataProcessing` step calls `POST payouts/bulk_approve` via `payoutApprovalProcessor` (`PayoutApprovalProcessorImpl`), separate from and structurally parallel to `payout.json`'s `payouts/bulk` create flow — `maxThreads: 6`, `bulkSize: 5`, `idempotentKey: "idempotency_key"`, using the same default (non-cache-hardened) `bulkApiCallDataProcessor` mechanism as B.4.1, same auth/header construction as B.5.
File: `src/main/resources/payout_approval.json:1-179`; `src/main/java/com/razorpay/batch/batchengine/item/processor/apiprocessor/PayoutApprovalProcessorImpl.java`; `grep -rn "BATCH_SERVICE_PAYOUT_APPROVAL_BULK" src` (absence).
Confidence: confirmed (integration exists); confirmed (absence of the literal constant name in this repo).

Claim: Request shape for `payouts/bulk_approve` (per-row, one array element per approve/reject decision — same array-body / `idempotency_key`-keyed-response pattern as B.5):
```json
{
  "idempotency_key": "batch_<BatchEntry.id>",
  "razorpayx_account_number": "<account_number (do not edit)>",
  "payout_update_action": "A" | "R",
  "user_comment": "<batch.settings.user_comment>",
  "payout": {
    "id": "<payout_id (do not edit)>",
    "amount": "<amount(Rupees) (do not edit)>",
    "currency": "<currency (do not edit)>",
    "mode": "<mode (do not edit)>",
    "purpose": "<purpose (do not edit)>",
    "narration": "<payout notes (do not edit)>",
    "status": "<status (do not edit)>"
  },
  "fund": { "id": "<fund_account_id (do not edit)>" },
  "contact": { "name": "<contact_name (do not edit)>", "id": "<contact_id (do not edit)>" }
}
```
The **input CSV for this batch type is itself the output of a prior export** (a "pending approvals" report with `(do not edit)`-suffixed read-only columns plus one editable column, `Approve (A) / Reject (R) payout`) — i.e. the operator flow is: export pending payouts → mark A/R per row in the sheet → re-upload as a `payout_approval` batch. This is the concrete mechanism behind "bulk approvals with Batch."
File: `src/main/resources/payout_approval.json:13-30` (staging column list), `60-88` (request template).
Confidence: confirmed.

Claim: `PayoutApprovalProcessorImpl.passSettingsIfRequired` only injects `scheduled_at` from batch settings when `payout` is present in params, otherwise a no-op transform. It does not independently re-derive an idempotency key or perform any approval-specific business validation client-side (mirrors `PayoutApiProcessorImpl`'s pattern from B.4).
File: `src/main/java/com/razorpay/batch/batchengine/item/processor/apiprocessor/PayoutApprovalProcessorImpl.java`.
Confidence: confirmed.

### B.9 Known race on duplicate idempotency keys across pods — synthesis

Restating and consolidating B.4's evidence directly against the task's framing:

1. **No batch-level distributed lock exists in code** to stop two pods/executions from concurrently working the same `Batch`. The one repo-authored claim to the contrary (`core/AGENTS.md:28`, "Distributed mutex in BatchCore — Redis lock prevents concurrent processing of same batch") is **not backed by any `RedisMutexLock`/`getMutexLock` usage in `BatchCore.java` or elsewhere in `core/`/`batchengine/`** — confirmed absent by full-repo grep. Treat that specific line of `core/AGENTS.md` as stale documentation, not as evidence of a real safeguard.
2. **Row-level idempotency-key derivation is a genuine two-tier design**, and the classic/default `payout` batch type (`payout.json`, the one `ARCHITECTURE.md` singles out as *the* example use case — "Batch service is used to perform Bulk Payouts") sits on the **weaker tier**: `idempotency_key = "batch_" + BatchEntry.id`, no Redis mutex, no cross-attempt coordination — while newer sibling flows calling the *same* `payouts/bulk` endpoint (the six `v2/payouts_*_bene_*_process` types) and the adjacent Payout Links flow were hardened with a SHA-1-row-hash + `RedisMutexLock` (300s TTL) + 24h idempotency-key cache.
3. Because `BatchEntry.id` is a stable, once-assigned DB primary key, a **single** row being retried by Spring Batch's own retry machinery is safe (same key every time — Payouts Service's own per-`(merchant_id, idempotency_key)` uniqueness, established in the payouts-side lane, simply returns the existing payout). The residual risk is specifically **concurrent** processing of the *same* `BatchEntry` row from two independent execution contexts (e.g., a step resumed after a crash/redeploy racing against a not-yet-terminated prior attempt, given `saveState: false` on the reader) — in that scenario both attempts would send the **identical** `idempotency_key` to Payouts Service at overlapping times, and safety then depends entirely on Payouts Service's own per-`(batch_id, idempotency_key)` mutex having no time-of-check/time-of-use gap — something this repo cannot attest to and which the payouts-side lane did not characterize as a distributed lock (it described it only as "a per-(batch_id, idempotency_key) mutex", mechanism internals not traced there either).
Confidence: confirmed (mechanism-level gap, and inconsistency between `payout.json` and its v2/Payout-Links siblings); probable (this being the literal explanation for any specific "known race" incident referenced outside this repo — not verifiable from static code alone).

### B.10 Functional/integration test evidence (spec-by-example)

`PayoutApprovalProcessorImplTest` (two variants exist: `src/test/java/.../batchengine/item/processor/PayoutApprovalProcessorImplTest.java` and `.../item/processor/apiprocessor/PayoutApprovalProcessorImplTest.java`) exercises `passSettingsIfRequired(parameters, settings)` with concrete `parameters`/`settings` maps, confirming the `scheduled_at`-from-settings injection path described in B.8 — did not surface additional request/response fields beyond what the JSON config already specifies.
File: `src/test/java/com/razorpay/batch/batchengine/item/processor/PayoutApprovalProcessorImplTest.java`; `src/test/java/com/razorpay/batch/batchengine/item/processor/apiprocessor/PayoutApprovalProcessorImplTest.java`.
Confidence: confirmed (test exists and exercises the documented transform); no additional schema fields discovered beyond B.8.

`docs/openapi/openapi.json` exists at the repo root's `docs/openapi/` directory but was not parsed in depth for this lane (the JSON-config-driven contract in B.4/B.5/B.8, derived directly from the executing code path, is more authoritative for the payouts-bulk integration than the Swagger annotations on `BatchController`/`ApiController`, which describe Batch's own public API, not its outbound call to Payouts Service). Flagged as a remaining unknown (see bottom of document).

---

## C. SUBSTITUTE CONTRACTS (for stub-server construction)

### C.1 Batch Service's own edge API — `POST /batch` (create a batch)

Request (`multipart/form-data` or form-encoded, `BatchCreateDTO`):

| Field | Type | Notes |
|---|---|---|
| `name` | String | |
| `entity_id` | String | merchant/account id |
| `mode` | String | `live` \| `test` |
| `creator_id` | String | |
| `creator_type` | String | |
| `batch_type_id` | String, required | must match a `BatchType.id` row (e.g. `payout`, `payout_approval`) |
| `multipartFile` | file | input CSV/XLSX, OR |
| `s3SignedUrl` | String | alternative to file upload |
| `settings` | String (JSON, ≤4999 chars) | consumer-specific config, e.g. `{"scheduled_at": ..., "user_comment": "..."}` |
| `storeHandler` | String (JSON) | |
| `schedule` | Long (epoch ms) | must be ≥ 1h in future, hourly granularity |
| `version` | String | flow-control marker, drives `/v2/` config lookup |
| `rzpctx-dev-serve-user` | String (header, mapped to `devserveHeader`) | devstack routing label |

Response: `Batch` entity — `id`, `status` (∈ `{CREATED, SCHEDULED, STAGING, VALIDATING, PROCESSING, OUTPUT, PAUSED, FAILED, COMPLETED, ...}` per `.claude/rules/batch-domain.md`'s running-status list), `totalCount`, `processedCount`, `failureCount`, `batchTypeId`, `entityId`, timestamps.

File: `src/main/java/com/razorpay/batch/domain/BatchCreateDTO.java`; `src/main/java/com/razorpay/batch/api/BatchController.java:38-55`; `.claude/rules/batch-domain.md`.

### C.2 Batch → Payouts Service: `POST {api.basepath}payouts/bulk` (bulk payout create, `payout.json`)

Request headers:
```
Authorization: Basic base64("rzp_<mode>_<entity_id>:<BATCH_API_SECRET>")
Content-Type: application/json
X-Batch-Id: <batch id>
X-Creator-Id: <creator id>
X-Creator-Type: <creator type>
X-Entity-Id: <merchant/account id>
```

Request body — **JSON array**, up to 5 elements per call (config `bulkSize`), each element:
```json
{
  "idempotency_key": "batch_<BatchEntry.id>",
  "razorpayx_account_number": "<string>",
  "payout": {
    "amount": "<string, numeric, paise>",
    "amount_in_rupees": "<string, numeric, rupees>",
    "currency": "<string, e.g. INR>",
    "mode": "<string: IMPS|NEFT|RTGS|UPI>",
    "purpose": "<string>",
    "narration": "<string>",
    "scheduled_at": "<epoch seconds or null, from batch.settings.scheduled_at>",
    "reference_id": "<string>"
  },
  "fund": {
    "id": "<string, fund_account id>",
    "account_type": "<string>",
    "account_name": "<string>",
    "account_IFSC": "<string>",
    "account_number": "<string>",
    "account_vpa": "<string>",
    "account_phone_number": "<string, optional>",
    "account_email": "<string, optional>"
  },
  "contact": {
    "type": "<string, optional>",
    "name": "<string>",
    "email": "<string, optional>",
    "mobile": "<string, optional>",
    "reference_id": "<string, optional>"
  },
  "notes": { "<dynamic key from CSV notes[...] columns>": "<string>", "...": "..." }
}
```
(Only fields with non-empty values are typically populated by `##Column##` substitution; `notes` is an object, not the array shown as a placeholder in the raw template — populated by `BaseApiProcessor.generateNotesColumn`.)

Expected response (HTTP 200, `successStatusCode=[200]`):
```json
{
  "items": [
    {
      "idempotency_key": "batch_<BatchEntry.id>",
      "http_status_code": 200,
      "id": "<payout id, e.g. pout_XXXXXXXXXXXXXX>",
      "error": { "code": null, "description": null }
    }
  ]
}
```
On a per-item failure the same envelope is expected, with that item's `http_status_code` ≠ 200 and `error.code`/`error.description` populated; the whole-array response is still 200 unless the entire request itself failed (in which case the raw response body, whatever shape it is, is copied onto every row's error context and the overall non-200 status is what drives the outer retry).

A stub server for this endpoint needs to: accept a POST at `{basepath}payouts/bulk`, parse the incoming array, and — for each item — return a corresponding entry in `items[]` keyed by the exact `idempotency_key` value it was sent (the client matches strictly on this field; an unmatched or missing key throws a client-side `LogicException("UNHANDLED FLOW")` inside Batch, per `BaseApiProcessor.processBulkApiResponse`).

File: `src/main/resources/payout.json:56-166`; `src/main/java/com/razorpay/batch/batchengine/item/processor/apiprocessor/{PayoutApiProcessorImpl,BaseApiProcessor}.java`.

### C.3 Batch → Payouts Service: `POST {api.basepath}payouts/bulk_approve` (bulk approve/reject, `payout_approval.json`)

Same headers/envelope pattern as C.2 (array request, `items[]` response keyed by `idempotency_key`), request element shape given in full in **B.8**. `payout_update_action` is the single-character discriminator (`"A"` or `"R"`), sourced verbatim from the operator-edited CSV column.

### C.4 Batch → Payouts Service (or an adjacent payout-links service): `POST {api.basepath}payout-links/batch` (Bulk Payout Links, `payout_link_bulk*.json`)

Same array/`idempotency_key`-keyed-response envelope, but using the **hardened** idempotency path (C.2/C.3's `idempotency_key` values are `"batch_" + <UUID>` minted fresh per unique row-hash rather than `"batch_" + BatchEntry.id`, per B.4.2) — `bulkSize: 3`, `maxThreads: 5`. Full request-field enumeration for `payout-links/batch` was not extracted line-by-line in this pass (out of the payouts-focused scope of this lane); flagged as a remaining unknown.

### C.5 Batch's own pre-flight structural validator — `POST /direct/validate`

Request (`multipart/form-data`, `BatchValidationDTO`): file + `type` (batch type name) at minimum (exact field list not fully enumerated in this pass — inferred from `BatchValidationService` usage, see B.3).

Response (`BatchValidationResponseDTO`):
```json
{
  "error": { "message": "<string, present only on failure>" } ,
  "processableCount": 0,
  "errorCount": 0,
  "parsedEntries": [ { "<column>": "<value>", "...": "..." } ]
}
```
`parsedEntries` is capped at `ValidateConstant.MAX_PARSED_ENTRIES` (a small preview, not the full file). Note this endpoint does **not** perform payout-semantic validation (B.3) — a stub only needs to check file-type/header-match/row-count to be behaviorally accurate for this route.

File: `src/main/java/com/razorpay/batch/domain/{BatchValidationDTO,BatchValidationResponseDTO}.java`; `src/main/java/com/razorpay/batch/monolith/BatchValidationService.java:57-95`.

### C.6 Workflow Service substitute contracts

All four Twirp services are mounted under `/twirp/rzp.workflows.<pkg>.v1.<Service>/<Method>`, `Content-Type: application/json` (Twirp JSON) or `application/protobuf`, inbound auth = HTTP Basic (A.10: for a payouts-shaped caller, `Authorization: Basic base64("payouts:<WORKFLOWS_AUTH_PAYOUTS_PASSWORD>")`).

#### C.6.1 `POST /twirp/rzp.workflows.workflow.v1.WorkflowAPI/Create`

Request (`WorkflowTriggerRequest`):
```json
{
  "workflow": {
    "config_id": "<string, from a prior ConfigAPI.CreateV2 response>",
    "type": "<string>",
    "entity_id": "<string, e.g. payout id>",
    "entity_type": "<string, e.g. \"payout\">",
    "owner_id": "<string, merchant id>",
    "owner_type": "<string>",
    "service": "<string, caller service name>",
    "org_id": "<string, optional>",
    "title": "<string, optional>",
    "description": "<string, optional>",
    "creator_id": "<string>",
    "creator_type": "<string>",
    "requester_id": "<string, optional>",
    "callback_details": {
      "state_callbacks": {
        "created": {"type": "...", "method": "post", "url_path": "/v1/workflow/state", "service": "<caller's own service name>", "timeout": 60000, "headers": {}, "payload": {}, "response_handler": {"type": "...", "success_status_codes": [200]}},
        "processed": {"method": "post", "url_path": "/v1/workflow/state/%s", "service": "<caller's own service name>", ...}
      },
      "workflow_callbacks": {
        "processed": {
          "domain_status": {
            "approved": {"method": "post", "url_path": "/v1/payouts/payouts_internal/{payoutId}/approve", "service": "payouts_live", "timeout": 60000, "response_handler": {"success_status_codes": [200]}},
            "rejected": {"method": "post", "url_path": "/v1/payouts/payouts_internal/{payoutId}/reject", "service": "payouts_live", ...}
          }
        }
      }
    }
  },
  "input": { "<arbitrary structpb.Struct, e.g. amount for a \"between\" state's range check>": "..." }
}
```
Response (`Workflow`): full message per A.2/A.4 — `id`, `config_id`, `status` (`creation_in_progress|created|initiated|init_failed|processed|failed|terminated`), `domain_status` (business outcome, populated once `processed`), `states` (map of per-level `State`), plus everything echoed from the request.

A stub server for this endpoint should: validate `config_id` resolves to a config it knows about (or accept any), synthesize an `id`, set `status: "initiated"`, and be prepared to receive `ActionAPI` calls referencing this workflow's states next (C.6.2), eventually firing the registered `workflow_callbacks.processed.domain_status.{approved,rejected}` callback per B.7's schema below.

#### C.6.2 `POST /twirp/rzp.workflows.action.v1.ActionAPI/CreateWithEntityId` (the realistic dashboard/Batch approve-or-reject call)

Request (`ActionOnEntityCreateRequest`):
```json
{
  "entity_id": "<payout id>",
  "entity_type": "payout",
  "config_id": "<string, optional if resolvable from entity>",
  "action": "approved" | "rejected",
  "actor_id": "<string, e.g. dashboard user id>",
  "actor_type": "user" | "admin" | "owner" | "service" | "system",
  "actor_property_key": "role",
  "actor_property_value": "<role id, e.g. \"finance_l1\", must match the pending State.Rules.ActorPropertyValue exactly>",
  "actor_meta": {"<optional key>": "<value>"},
  "service": "<caller service name>",
  "owner_id": "<merchant id>",
  "owner_type": "<string>",
  "data": {"<optional structpb.Struct>": "..."},
  "comment": "<optional string, persisted via CommentAPI internally>"
}
```
Response (`ActionListResponse`): `{"entity": "action", "count": <int32>, "items": [{"id": "<14-char action id>", "workflow_id": "...", "state_id": "...", "action_type": "approved"|"rejected", "status": "created"|"initiated"|"init_failed", "actor_id": "...", "actor_type": "...", "actor_property_key": "role", "actor_property_value": "...", "created_at": <epoch>}]}`.

Server-side validation a stub should replicate to be behaviorally accurate: reject (return the WFS `StateIsNotPendingOnCurrentActor` error, or simply accept-anything for a permissive stub) unless `actor_property_key == State.Rules.ActorPropertyKey` and `actor_property_value ∈ {action.actor_property_value, action.actor_id}` matches `State.Rules.ActorPropertyValue` for the currently-pending state (A.5) — but note the real server does **not** independently verify the caller actually holds that role; a stub is free to accept unconditionally without being "more wrong" than production.

Effect: fires a Cadence Signal internally (A.11) — a stub can simply synchronously advance its own in-memory state and, once the configured approval `count` for the current level is met and no further levels remain (`END_STATE` reached), issue the outbound callback per C.6.3.

#### C.6.3 Callback WFS → Payouts Service (or any registered `service`)

Request WFS sends (per A.7):
```
POST <client.host><url_path>          e.g. POST https://payouts.razorpay.com/v1/payouts/payouts_internal/{payoutId}/approve
Authorization: Basic base64("payouts_live:<WORKFLOWS_CLIENTS_PAYOUTS_LIVE_PASSWORD>")
Content-Type: application/json
<any headers configured on the Callback.Headers map>

<Callback.Payload structpb.Struct — content is whatever the config/workflow-creation caller set; for the approve/reject payouts callbacks the payouts-side lane found these are simple/empty-bodied POSTs to a path that already carries the payout id>
```
Expected response for WFS to consider the callback successful: HTTP status ∈ the callback's configured `response_handler.success_status_codes` (defaults effectively to needing an explicit list from the config — no universal default of `[200]` was found; a stub replicating the payouts side should return `200`). Any other status (or a transport error) is treated as retryable and redelivered per the Cadence retry schedule in A.7 (2s → ×5.0 backoff → capped at 1h interval → up to 30 days). **No idempotency key is sent** — a stub playing the role of Payouts Service must be idempotent on its own (e.g. by payout id + target state) to behave correctly under WFS's retries.

#### C.6.4 `POST /twirp/rzp.workflows.config.v1.ConfigAPI/CreateV2`

See the concrete real request/response pair in **A.3** — reproduced verbatim there, sufficient for a stub to accept and echo back a synthesized `id`.

#### C.6.5 `POST /twirp/rzp.workflows.comment.v1.CommentAPI/Create`

See **A.14** for the concrete request/response pair.

---

## UNRESOLVED_QUESTIONS closed

### #12 — "Repository name, Create/action contract, retry policy on callbacks, expiry handling (no expiry event in PS), where OTP/`enable_approval_via_oauth` is enforced, bulk-approval integration with Batch."

**Fully closed**, all six parts:

- **Repository name**: `razorpay/workflows` ("Razorpay Workflow Service"). (A.1)
- **Create/action contract**: `WorkflowAPI.Create` (register a workflow + callbacks) and `ActionAPI.Create`/`CreateWithEntityId`/`CreateDirectOnWorkflow` (approve/reject) — full schemas in A.2/A.11 and substitute-contract form in C.6.1/C.6.2.
- **Retry policy on callbacks**: Cadence-Activity-managed retry — `InitialInterval 2s, BackoffCoefficient 5.0, MaximumInterval 1h, ExpirationInterval 30 days`, per-attempt `StartToCloseTimeout 30s`, default per-callback HTTP timeout 60s, HTTP Basic Auth (`payouts_live`/secret). (A.7)
- **Expiry handling**: bounded purely by Cadence's `ExecutionStartToCloseTimeout` = config's `Meta.WorkflowExpireTime` hours (minimum ~4 months if set at all; defaults to 10 years i.e. effectively never) — and **no code path was found that fires any callback on expiry** (no `NewTimer`/`TimedOut`-handling code exists). This explains why Payouts Service has no expiry event: **WFS itself doesn't generate one either** — a pending workflow that hits its Cadence timeout appears to simply vanish from Cadence's perspective with WFS's own DB row left at `status="initiated"` forever, and payouts is never told. This is a genuine cross-system gap, not merely an artifact of payouts not implementing something WFS sends. (A.8)
- **Where OTP/`enable_approval_via_oauth` is enforced**: **not in WFS** — the flag/concept does not exist anywhere in `razorpay/workflows` (confirmed by exhaustive grep). It must be enforced entirely upstream, in the dashboard or API monolith, before `ActionAPI` is ever called — WFS trusts the caller's `actor_*` fields unconditionally (A.5/A.6).
- **Bulk-approval integration with Batch**: **closed, and clarified as two independent, non-overlapping mechanisms**: (1) WFS itself has **zero** bulk RPC (A.12, exhaustive repo-wide grep for "bulk" → no hits) — so a Batch-to-WFS bulk path does not exist. (2) The actual bulk-approval integration is Batch calling **Payouts Service directly**, bypassing WFS entirely: a first-class Batch batch type (`payout_approval`, B.8) calls `POST payouts/bulk_approve` on the Payouts-Service edge path (same auth/header/base-path mechanism as bulk create, C.2/C.3) — not Payouts Service's single-payout `/v1/payouts/payouts_internal/{id}/approve` route (that route, per the payouts-side lane, is reserved for WFS's own callback, C.6.3). The operator flow is export-pending-approvals → mark A/R per row → re-upload as `payout_approval`. The literal symbol `BATCH_SERVICE_PAYOUT_APPROVAL_BULK` is not present in either `razorpay/batch`'s or `razorpay/workflows`'s source, so it is not either repo's own name for the integration (likely a Payouts-Service- or monolith-side config/feature-flag name for the same thing — not confirmed here, and not needed to be, since the actual mechanism is now fully traced end-to-end).

### #17 — "Which of `CreateBulkPayouts` vs `CreateBulkPayoutsConcurrent` is wired to `/v1/payouts/bulk`; identity of the caller ("Batch service") and its polling mechanism."

- **Which handler is wired**: not re-derived in this lane (payouts-side code, already covered/open in `12_payouts_approval_queue_bulk.md` D.4.3 — still unresolved as of this lane; the Batch side gives no signal either way since it only sees the HTTP contract, not the handler implementation).
- **Identity of the caller**: **closed.** It is confirmed, from the caller's own source, to be `razorpay/batch`'s `payout.json` (and sibling `v2/payouts_*_bene_*_process.json`) batch types, calling `POST {api.basepath}payouts/bulk` with headers `X-Batch-Id`, `X-Creator-Id`, `X-Creator-Type`, `X-Entity-Id` and HTTP Basic auth `rzp_<mode>_<entity_id>:<BATCH_API_SECRET>` (B.5) — matching byte-for-byte the header name (`X-Batch-Id` / `HeaderBatchID`) the payouts-side lane found being read into request context.
- **Polling mechanism**: **closed, and the payouts-side lane's inference was wrong.** Batch does **not** poll `GET /v1/payouts?batch_id=...` on Payouts Service — the call is synchronous (B.4/B.6), so Batch already has each row's outcome from the `payouts/bulk` HTTP response itself. Batch's *own* caller (dashboard/API monolith) polls Batch's own `GET /batch/{id}` edge route instead (B.6/B.7).

---

## Remaining unknowns

1. **Cross-system expiry gap** (A.8/#12 closure): confirmed that neither WFS nor Payouts Service appears to generate a "workflow expired" event/callback — if a config ever sets a finite `WorkflowExpireTime`, what actually happens to the payout and to WFS's own DB row when a Cadence execution times out was not traceable further from static code in either repo (would need a Cadence-server-side behavior check, e.g. whether `cadence-web`/admin tooling shows these as `TimedOut` and whether any *separate* reconciliation job elsewhere sweeps them — no such job was found in either repo).
2. Whether `enable_approval_via_oauth` is a genuinely load-bearing flag anywhere (dashboard/API monolith) or is vestigial — confirmed absent from WFS and from payouts (per the prior payouts-side lane), so its actual enforcement point (if any) remains unlocated across all three repos examined so far.
3. WFS's Redis usage (`pkg/cache`) was not investigated in depth (A.1) — not tied to any callback/approval/expiry logic found, but not exhaustively ruled out either.
4. WFS's ASL engine internals (`internal/client/types/asl/{task_state,wait_state,choice_state,map_state}.go`) were confirmed to exist and shape-matched against the DTO schema (A.3) but not read line-by-line — irrelevant to payout-approval specifically (payout approval uses the plain "approval" template type, not ASL) but would matter if any payout-adjacent automation (e.g. purchase-order approval) turns out to use ASL states for anything beyond simple checker chains.
5. Which of Payouts Service's `CreateBulkPayouts` vs `CreateBulkPayoutsConcurrent` is actually wired to `/v1/payouts/bulk` — unresolved in the payouts repo itself (not addressable from either `workflows` or `batch`).
6. Whether the Batch-side idempotency-key weakness for `payout.json` (B.4/B.9) has ever manifested as a real duplicate-payout incident — would require production incident/metrics evidence outside all repos examined.
7. Full `payout-links/batch` request-field enumeration (C.4) — not extracted line-by-line in this pass.
8. `docs/openapi/openapi.json` in `razorpay/batch` was not parsed for this lane — may contain additional/conflicting schema detail for Batch's own public routes (not the outbound payouts-bulk call, which is JSON-config-driven and was read directly from the executing code path instead).
9. Whether `api.razorpay.com/v1/payouts/bulk` (the address Batch actually calls, B.5) is served by the API monolith directly or proxied through Kong straight to Payouts Service — ties into UNRESOLVED_QUESTIONS #9 (`payouts-proxy-cutover`), not re-resolved here.
10. Full request-field enumeration for `POST /direct/validate` (`BatchValidationDTO`) — inferred from usage, not read from the DTO class itself in this pass.
11. SQS queue-routing details (`BatchCore.pushBatchIntoQueue`, "5 queues... by batchTypeId prefix" per `core/AGENTS.md`) were not traced in depth — which of the 6 `batch-sqs*` deployments actually consumes `payout`/`payout_approval` batches was not confirmed (plausibly the generic `batch-sqs` queue, since payout types aren't named in any of the specialized queue names — reconciliation/art-dual-write/art-prs/sftp-notification/payment-links — but this is inferred, not confirmed).
12. Exact `Callback.Payload`/`Headers` content WFS actually sends on the payouts approve/reject callback (C.6.3) was not observed as a literal fixture (unlike the config/comment examples) — the schema is confirmed from the `Callback` DTO definition, but a concrete real payload for *this specific* callback (as opposed to the ASL/Splitz example found in a functional test) was not located.
