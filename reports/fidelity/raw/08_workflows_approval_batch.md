# Lane 08 — Approval and workflow paths (Workflow Service, DCS `Workflows`, Batch)

Repos at HEAD (shallow clone, this pass): `payouts@4bf3dbf9239feadea6d65ca90c893a988e116173`,
`workflows@080d71a51b777c89af586c92ac4b5f683a473a7c`, `batch@3839dd77d061422c144b7409af401415d598c54b`,
`api@2d665f918b60e917ec92be648fb5816d247f72b1` (monolith, opened only for the specific bulk-approve/OTP
chain this lane's brief asks for — full monolith audit is lane/finding 20's job, not repeated here).

## Scope and sources read

- `payouts` (Go): `internal/app/payouts/processor/{base.go,baseHelper.go}`, `pkg/workflow/{workflow_create.go,base.go,config.go,client.go,fetch_workflow.go}`, `internal/provider/workflow_client.go`, `internal/app/workflow/stateMap/{core.go,repo.go,model.go}`, `internal/app/workflowConfig/{core.go,model.go,repo.go}`, `internal/routing/router/{workflow_routes.go,payout_internal_routes.go,payout_internal_routes_with_passport.go,payout_bulk_routes.go}`, `internal/app/bulkPayoutsProcessor/{core.go,validation.go,model.go}`, `internal/database/migrations/2021082411073*_create_workflow_*.go`, `internal/app/common/appConstants/features.go`, `pkg/dcs/features/features.go`.
- `workflows` (Go/Twirp/Cadence): `go.mod`, `README.md`, `cmd/{api,workers,migration}/main.go`, `internal/entities/{workflow,action,config,comment}/server.go`, `internal/entities/workflow/interactor.go` (`CreateInteractorWithoutConfig`/`CreateInteractor`), `internal/entities/config/{repo.go,core.go,validation.go}`, `internal/entities/action/interactor.go` (`validateActionOnState`), `internal/client/types/approval/workflow.go` (state machine, retry policy, `approveStateIfApplicable` self-approval logic), `internal/client/types/common/{callback.go,helper.go}`, `internal/dto/{config_model.go,template_model.go}`, `internal/database/migrations/*.go` (all 10), `pkg/validation/rule/rules.go`, `internal/dcs/features/features.go`.
- `batch` (Java/Spring): `ARCHITECTURE.md`, `.claude/rules/*.md`, `src/main/resources/{payout.json,payout_approval.json}`, `src/main/java/com/razorpay/batch/batchengine/item/processor/{BulkApiCallDataProcessorImpl.java,BaseApiProcessor.java,apiprocessor/PayoutApprovalProcessorImpl.java}`, `src/main/java/com/razorpay/batch/entity/{Batch.java,BatchEntry.java,BatchType.java}`, `src/main/java/com/razorpay/batch/enums/{BatchStatus.java,BatchEntryStatus.java}`, `src/main/java/com/razorpay/batch/commons/CustomIdGenerator.java`, `src/main/java/com/razorpay/batch/constants/{AppConstant.java,HeaderConstant.java}`, `src/main/java/com/razorpay/batch/utils/RestCallUtils.java`.
- `api` (PHP monolith, targeted): `app/Http/Route.php` (`payout_bulk_approve`, `payout_approve_bulk` route defs + permissions), `app/Http/Controllers/PayoutController.php`, `app/Models/Payout/Service.php` (`approveFundAccountPayout`, `bulkApproveFundAccountPayouts`, `approveBulkPayout`, `processEntryForBulkPayoutApproval`, `approvePayoutsFromBatchService`/`rejectPayoutFromBatchService`), `app/Models/Payout/Core.php` (`approvePayout`, `processWorkflowActionOnPayout`, `shouldCallWorkflowService`, `processActionOnPayoutViaWorkflowService`, `approveWorkflowViaWorkflowService`/`rejectWorkflowViaWorkflowService`, `retryPayoutWorkflow`), `app/Models/Payout/Validator.php` (`validatePayoutForApprovalViaOAuth`, `MAX_BULK_PAYOUTS_LIMIT`, `validateBatchId`), `app/Models/Feature/Constants.php`, `app/Http/OAuthScopes.php`, `app/Trace/TraceCode.php`.
- `config-proto`: `rzp/x/merchant/payouts/workflows.proto` (the `Workflows` message DCS actually stores).
- Twin: `ENV2_COMPOSE/docker-compose.yml`, `config/templates/base/payouts/{default,arena,e2e}.toml`, `seeds/s4/{dcs_fixtures.json,payouts.sql}`, `seeds/schema-patches/apidb.sql`, `substitutes/*/server.py` (existence/absence check), `SYNTHETIC_FIXTURE_SPEC.md`.
- Prior reports treated as hypotheses and checked, not as ground truth: `reports/raw-findings/23_workflows_batch.md`, `12_payouts_approval_queue_bulk.md`, `ARCHITECTURE_DELTA.md` (§W5, §C51–C54, §"Confirmed-1").

The single biggest addition this pass makes over `23_workflows_batch.md`/`ARCHITECTURE_DELTA.md`: those reports concluded "Batch calls `payouts/bulk_approve` directly on Payouts Service" from Batch's own JSON config alone, without ever opening the monolith. Opening `api` shows `payouts/bulk_approve` **is a monolith route, not a Payouts-Service route** (confirmed absent from the `payouts` repo, §Corrections below), and that it forwards into **either** WFS **or** the monolith's own legacy checker system depending on a per-payout flag — a materially different picture from "bypasses WFS entirely."

---

## Production behaviour

### 1. PS side — when a payout enters `pending` (workflow required)

**Gate: `BasePayoutProcessor.IsWorkflowApplicable`** (`payouts/internal/app/payouts/processor/baseHelper.go:107-193`), evaluated in `CreatePayout` (`processor/base.go:254-300`) before any bank dispatch. Exact short-circuit order (first match wins):

| # | Condition | Result | Evidence |
|---|---|---|---|
| 1 | `purpose == "rzp_fees"` and request `enable_workflow_for_internal_contact != true` | skip, `SKIP_FOR_INTERNAL_PAYOUT` | `baseHelper.go:107-116` |
| 2 | `payout.Internal == true` and same flag false | skip, `SKIP_FOR_INTERNAL_PAYOUT` | `baseHelper.go:118-121` |
| 3 | DCS `Workflows.enable_payout_workflow` false (checked via legacy `appConstants.PayoutWorkflows` OR, if Splitz experiment `PayoutWorkflowsDcsNameExperiment` on, also `features.PayoutWorkflows`) | skip | `baseHelper.go:123-131`; `internal/app/payouts/service.go:1475-1489` |
| 4 | caller app == `payout_links` and request `skip_workflow=true` | skip, `SKIP_WF_FOR_PAYOUT_LINK` | `baseHelper.go:139-144` |
| 5 | caller app == `xperience` | skip, `SKIP_WF_FOR_XPERIENCE` | `baseHelper.go:146-150` |
| 6 | request carries a non-empty `BatchID` header **and** request `skip_workflow=true` | skip, `SKIP_WF_FOR_BULK_PAYOUT` | `baseHelper.go:152-158` (comment: batch-vs-header detection is a known TODO/hack) |
| 7 | request explicitly sets `skip_workflow` (any value) | requires DCS `Workflows.skip_workflow_for_dashboard` true, else **400** `BadRequestValidationFailureException`; if true and `skip_workflow=true` → skip, `SKIP_WF_AT_PAYOUTS` | `baseHelper.go:160-169` |
| 8 | authType == `passport.LegacyAuthTypePrivate` (merchant's own API key, not dashboard/proxy) **and** DCS `Workflows.skip_approval_workflow_for_api` true | skip, `SKIP_WORKFLOWS_FOR_API` | `baseHelper.go:171-183` |
| 9 | caller app == `x_payroll` and DCS `Workflows.skip_workflow_for_payroll` true | skip, `SKIP_WF_FOR_PAYROLL` | `baseHelper.go:185-190` |
| else | — | **workflow applicable, true** | `baseHelper.go:193` |

**Correction / clarification not in prior reports**: condition 8 means a merchant with `enable_payout_workflow=true` and `skip_approval_workflow_for_api=true` (e.g. the twin's M3 fixture, see §Twin comparison) only gets routed into workflow when the payout is created via **non-Private auth** (dashboard/Proxy/Privilege) — a payout created with the merchant's own API key on that same merchant sails straight through, workflow never applicable. A twin test harness exercising M3's approval path must create the payout as a dashboard/proxy call, not a plain API-key call.

DCS `Workflows` proto (`config-proto/rzp/x/merchant/payouts/workflows.proto:1-32`, package `rzp.x.merchant.payouts`), exact fields, all `bool`, proto3 zero-value default `false`:

| Field | Tag | Semantics (from the proto's own doc comment) |
|---|---|---|
| `enable_approval_via_oauth` | 1 | **Not** an OTP/2FA switch — see §"OTP/enable_approval_via_oauth" below; controls whether an OAuth bearer token can authenticate calls to the approve/reject routes at all. |
| `skip_workflow_for_dashboard` | 2 | gates condition 7 above |
| `skip_workflow_for_payroll` | 3 | gates condition 9 above |
| `skip_approval_workflow_for_api` | 4 | gates condition 8 above |
| `enable_payout_workflow` | 5 | master on/off switch (condition 3) |

If workflow is applicable, `HandleWorkflowsIfApplicable` (`baseHelper.go:29-105`) calls `CreateWorkflow` → on success sets `payout.OnEvent(StatePending)` and persists; **on any error from the WFS `Create` call (including a plain connect-refused), the whole payout-create request fails** with `errorclass.WorkflowCreationBadRequestError` (`ErrWorkflowCreationFailure`, a 400-class public error) — `CreatePayout` returns `nil, err` (`processor/base.go:285-289`; `baseHelper.go:37-46`). There is **no silent-skip-workflow fallback** if WFS is unreachable.

**`workflow_entity_map` in API-DB / feature flags**: `payouts` writes its own `workflow_entity_map` (id, workflow_id, entity_id, config_id, entity_type, merchant_id, org_id — `internal/database/migrations/20210824110739_create_workflow_entity_map_table.go:14-24`) at workflow-creation time (write site not fully traced in this pass — plausibly in `pkg/workflow`/`CreateWorkflow`'s caller, not re-derived line-by-line here); this is distinct from the **monolith's own copy** of the same table (`api.workflow_entity_map`, used by `EntityMap\Repository`/`EntityMap\Core`, see §5 below) — two separate physical tables in two separate databases with the same name and same purpose (per-payout "is there an open workflow" lookup), kept in sync by the dual-write path (§stateMap below) and by the monolith's own `retryPayoutWorkflow` write (`api/app/Models/Payout/Core.php:3699-3706`, `(new EntityMap\Core)->create($response, $payout)`).

There is **no separate feature flag** gating whether `WorkflowAPI.Create` is called once `IsWorkflowApplicable` is true — the call is unconditional on that gate.

### 2. The exact `WorkflowAPI/Create` request PS sends

Route: `POST /twirp/rzp.workflows.workflow.v1.WorkflowAPI/Create` (`payouts/pkg/workflow/workflow_create.go:17`). Body built by `createWorkflowCreateRequestBody` (`workflow_create.go:225-262`):

```json
{
  "workflow": {
    "entity_id": "<payout id>",
    "entity_type": "<payout.EntityName(), i.e. \"payout\">",
    "title": "",
    "description": "",
    "config_id": "",
    "config_version": "1",
    "diff": {"old": {"merchant_id": "", "amount": 0}, "new": {"merchant_id": "<merchant id>", "amount": <payout amount>}},
    "creator_id": "<userID if present, else merchant_id>",
    "creator_type": "user" | "merchant",
    "owner_id": "<merchant id>",
    "owner_type": "merchant",
    "service": "rx_live",
    "org_id": "<merchantConfig.GetOrgID()>",
    "callback_details": { "...": "see below" }
  }
}
```

**Correction to `23_workflows_batch.md` §C.6.1**: PS never sends `config_id` (it is always the empty string — the field exists on the Go struct but `createWorkflowCreateRequestBody` never assigns it) and never sends any `config_type` field at all — the `CreateWorkflowRequestBody` struct has no such field. The prior report's substitute-contract example showing a populated `config_id` "from a prior `ConfigAPI.CreateV2` response" is not what PS's own code does.

`callback_details` (`getCallbackDetails`, `workflow_create.go:264-338`), constants: `StateCallBackCreatedPath="/v1/workflow/state"`, `StateCallBackProcessedPath="/v1/workflow/state/%s"`, `WfCallbackPath="/v1/payouts/payouts_internal/"`:

| Callback | method | url_path | service | headers | payload | success codes |
|---|---|---|---|---|---|---|
| `state_callbacks.created` | POST | `/v1/workflow/state` | `payouts_live` | `{x-creator-id, X-Razorpay-Account: <merchant id>}` | `{queue_if_low_balance: bool}` | `getCallbackResponseHandler()` |
| `state_callbacks.processed` | PATCH | `/v1/workflow/state/%s` | `payouts_live` | same | same | same |
| `workflow_callbacks.processed.domain_status.approved` | POST | `/v1/payouts/payouts_internal/<payoutId>/approve` | `payouts_live` | same | same | same |
| `workflow_callbacks.processed.domain_status.rejected` | POST | `/v1/payouts/payouts_internal/<payoutId>/reject` | `payouts_live` | same | same | same |

`getCallbackResponseHandler()` (`workflow_create.go:320-325`): `{"type": "success_status_codes", "success_status_codes": [200, 201, 409]}` — **correction to `23_workflows_batch.md` §C.6.3**, which speculated "no universal default of [200] was found" and guessed the payouts side "should return 200" — PS's actual registered success set is `[200, 201, 409]` (409 included, presumably so a re-delivered callback that hits an already-processed/duplicate state is still treated as success, not retried).

There is no explicit expiry field sent by PS (`Meta.WorkflowExpireTime` lives entirely in the WFS-side `Config.Template`, set at config-creation time, not per-workflow — confirmed absent from `CreateWorkflowRequestBody`).

**How WFS resolves which config to use** (since PS sends no `config_id`): `Server.Create` (`workflows/internal/entities/workflow/server.go:56-60`) branches on `w.GetConfigId() == ""` → `CreateInteractorWithoutConfig` (`interactor.go:290-317`), which calls `configCore.FindByOwnerDetails({OwnerId, OwnerType, Service, OrgId})`. The repo query (`workflows/internal/entities/config/repo.go:93-104`):
```go
Where("owner_id = ?", ...).Where("owner_type = ?", ...).Where("service = ?", ...).
Where("org_id = ?", ...).Where("enabled = ?", "true").Order("created_at desc").First(config)
```
**This filters on `(owner_id, owner_type, service, org_id, enabled)` only — `config_type` is not part of the lookup at all.** Practically: a given merchant can have at most one *effective* enabled config for `service="rx_live"` (whatever its `config_type` was set to at `ConfigAPI.CreateV2` time); if two enabled configs exist for the same 4-tuple, the most-recently-created wins silently. This is new detail not in either prior report — it is the actual mechanism behind "how does WFS know this is a payout-approval config," and it means the twin (or any substitute) does not need to replicate `config_type` matching logic at workflow-create time, only at the human-facing `ConfigAPI` admin surface.

### 3. The `ActionAPI` approve/reject request PS's counterpart sends

Not sent by `payouts` itself (PS is only the **callback target**, never the approver caller in-repo) — but two callers exist and are traced in full in §5 (monolith) below: the monolith's `workflowService->createActionOnEntity($payout, $input)` (dashboard single-approve) and `workflowService->createDirectAction($payout, $input)` (bulk-reject-as-owner). Confirmed request fields from WFS's own server-side contract (`workflows/internal/entities/action/interactor.go`, `dto/action_model.go` — schema reproduced from `23_workflows_batch.md §C.6.2`, spot-checked against `validateActionOnState`, unchanged): `entity_id, entity_type, action ("approved"|"rejected"), actor_id, actor_type, actor_property_key, actor_property_value, actor_meta, service, owner_id, owner_type, data, comment`.

### 4. WFS `POST /v1/workflow/state` / `/v1/workflow/state/:id` — PS's own inbound route

`payouts/internal/routing/router/workflow_routes.go:12-30`: group `/v1/workflow`, `middleware.BasicAuth(cred.Workflow, cred.API)` (write-only — no GET route exists at all, confirmed absent). `CreateWorkflowStateMap`/`UpdateWorkflowStateMap` (`internal/controllers/workflowController.go`) persist into PS's own `workflow_state_map` table (`workflowStateMapCreateInput.Rules.ActorPropertyValue` → `ActorRole`, `.Rules.Count` → `CountOfApprovalsNeeded` — `internal/app/workflow/stateMap/core.go:44-53`) and then best-effort forward the same payload to the API monolith (`c.ForwardStateCreateCallbackToAPI`, `stateMap/core.go:97-100`, error swallowed by design). **This table is write-only telemetry/audit — nothing in PS reads `workflow_state_map` back to decide approval eligibility** (no GET route, no caller of `workflowConfig.GetConfigByConfigTypeAndMerchantId` found anywhere outside its own tests — see §Corrections, this is a genuinely dead read path in the `payouts` repo despite being wired into DI).

### 5. Approval identity / authorization, and the actual bulk-approval mechanism (monolith-verified)

**No independent role re-derivation anywhere in WFS.** `validateActionOnState` (`workflows/internal/entities/action/interactor.go:300-333`) checks only:
```go
actorPropertyValueMatches := slices.Contains([]string{action.GetActorPropertyValue(), action.GetActorId()}, stateRules.GetActorPropertyValue())
if stateRules.GetActorPropertyKey() != action.GetActorPropertyKey() || stateRules.GetCount()==0 || !actorPropertyValueMatches { return ...StateIsNotPendingOnCurrentActor }
```
i.e. pure string-equality against whatever `actor_property_key/value` the caller (monolith) sent — WFS never calls out to authz/passport/merchant-users to verify the caller actually holds that role for *this* action (confirmed: `grep -rniE "authz|passport" workflows/internal` → zero hits, re-confirmed this pass).

**Self-approval is not prevented — the opposite exists as an opt-in auto-approve.** `WorkflowManager.approveStateIfApplicable` (`workflows/internal/client/types/approval/workflow.go:230-263`), invoked on every state creation when `stateData.Rules.ActorPropertyKey=="role"`: fetches DCS feature `DcsSkipApprovalForCreator` for the workflow's `owner_id`; if enabled **and** `makerRole == checkerRole` (the payout creator's own role, fetched via `common.FetchUserRoleFromUserIdAndOwnerId`, equals the pending state's required role) **and** `workflowType == "PayoutApproval"`, WFS synthesizes an internal `Action{ActorId: creator_id, ActorType: creator_type, Action:"approved", ActorMeta:{name:"system", email:"auto-approved@razorpay.com"}}` and auto-processes it (`workflow.go:264-280`). When this flag is **off** (default), there is still no block on the creator later manually approving their own payout through the normal `ActionAPI` path — WFS compares only role strings, never `actor_id` against the workflow's `creator_id`. **Self-approval prevention, if it exists at all, must live entirely upstream** (dashboard UI hiding the approve button for the payout's own creator, or an authz-service rule not visible from either repo) — not found in WFS, payouts, or (for the paths traced) the monolith.

`FetchUserRoleFromUserIdAndOwnerId` (`workflows/internal/client/types/common/helper.go:27-38`): `GET merchants/%s/internal-users` (path `merchants/<ownerId>/internal-users`) via the `rx_live` client (API monolith, Basic Auth) — parses response into `[]MerchantUser{Id, Role, RoleName}`, matches `creatorId` by `Id`, returns `Role`.

**The actual bulk-approval mechanism — corrects both prior reports.** `23_workflows_batch.md §B.8`/`ARCHITECTURE_DELTA.md "Confirmed-1"` state as settled fact that "Batch's `payout_approval` batch type calls `POST payouts/bulk_approve` directly on Payouts Service, bypassing WFS entirely." Opening the monolith shows this is wrong on two counts:

1. **`payouts/bulk_approve` is not a route in the `payouts` (Go microservice) repo at all.** `grep -rn "bulk_approve" payouts/` → zero hits in routes/controllers (only unrelated SMS-action-name string constants `bulk_payout_approve`/`bulk_approve_payout` in `pkg/raven/send_sms.go:57-58`, already noted in `12_payouts_approval_queue_bulk.md §A.7`). It **is** a route in the **API monolith**: `api/app/Http/Route.php:2319`, `'payout_bulk_approve' => ['post', 'payouts/bulk_approve', 'PayoutController@approvePayoutBulk']`, permission `APPROVE_PAYOUT_BULK` (`Route.php:12074`).
2. **The monolith handler does not talk to Payouts-Service's `/payouts_internal/{id}/approve` at all — it calls WFS directly, per-payout, when the payout is workflow-backed.** Full chain, all confirmed by direct read:

```
Batch (payout_approval.json) --POST payouts/bulk_approve, Basic Auth, X-Batch-Id header-->
  api/PayoutController@approvePayoutBulk (Route.php:2319)
    -> Payout\Service::approveBulkPayout($input)                                    [Service.php:3539]
       - validateBulkPayoutCount: max 15 rows/call                                  [Validator.php:1521-1531, MAX_BULK_PAYOUTS_LIMIT=15]
       - validateBatchId($batchId): 400 if X-Batch-Id header missing/empty          [Base/Validator.php:83-88]
       - per row: processEntryForBulkPayoutApproval($item)                          [Service.php:3629-3650]
         - action = strtoupper(item['payout_update_action'])  # "A" or "R"
         - "A" -> approvePayoutsFromBatchService(input)   -> Payout\Core::approvePayout($payout,$input)   [Service.php:3652-3666]
         - "R" -> rejectPayoutFromBatchService(input)     -> Payout\Core::rejectPayout($payout,$input)    [Service.php:3668-3681]
    -> Payout\Core::approvePayout()  -> processWorkflowActionOnPayout($payout, true, $input)  [Core.php:3123-3128, 3232]
       if payout.getIsPayoutService()==true  OR  shouldCallWorkflowService($payout)==true:
           -> processActionOnPayoutViaWorkflowService()  -> approveWorkflowViaWorkflowService()
              -> $this->workflowService->createActionOnEntity($payout, $input)      [Core.php:8035]
              (this is the monolith's OWN PHP Twirp client hitting WFS's ActionAPI/CreateWithEntityId
               directly -- NOT a call into the payouts Go microservice)
       else (legacy, no workflow-service-backed workflow found for this payout):
           -> processWorkflowActionOnPayoutBase(): creates a Workflow\Action\Checker row against the
              MONOLITH'S OWN LOCAL pre-WFS maker-checker tables ("API workflow system")               [Core.php:3290-3335]
```
`shouldCallWorkflowService` (`Core.php:3589-3604`): `(new EntityMap\Repository)->isPresent('payout', $payout->getId())` — a lookup against the monolith's **own** `workflow_entity_map` table (schema seeded in the twin at `seeds/schema-patches/apidb.sql:34`) — i.e. exactly the dual-write target of PS's `stateMap.Core.ForwardStateCreateCallbackToAPI` (§4 above) and of the monolith's own `retryPayoutWorkflow` write path.

3. **`processEntryForBulkPayoutApproval` fetches the payout via the monolith's own local Eloquent model** (`$this->repo->payout->findByPublicId(...)`, `Service.php:3658,3674`) — i.e. **the monolith's local `payouts` table**, the one Slack (#potential_outages, 2026-09-03, per `ARCHITECTURE_DELTA.md`'s root-agent correction) reports is **dropped in prod**. This means: today, in prod, Batch's `payouts/bulk_approve` call cannot even locate the payout row before it gets anywhere near the workflow-vs-legacy branch — it fails at `findByPublicId` with the same `Table 'api.payouts' doesn't exist` class of error already documented for `ApprovedPayoutProcessor`. **This is a new, concrete instance of the same production outage, specific to Batch-driven bulk-approve — not previously connected to it by either prior report.** Whether this is mitigated by a still-populated read-replica, a different table name, or a merchant cohort still legitimately on the monolith's own payout path was not determined from static code (would need the same production-DB evidence the Slack thread already supplies for the cron job).

4. **Two distinct bulk-approve entry points exist in the monolith, only one of which Batch calls** — a genuinely separate discovery from anything in the prior reports:

| Route | Controller/Service method | Caller / auth shape | Field shape | OTP? |
|---|---|---|---|---|
| `POST payouts/bulk_approve` | `PayoutController@approvePayoutBulk` → `Service::approveBulkPayout` (`Route.php:2319`) | **Batch service** — requires `X-Batch-Id` header (else 400), Basic Auth `rzp_<mode>_<entity_id>:<secret>` | array of `{payout_update_action:"A"\|"R", payout:{id,amount,currency,mode,purpose,narration,status}, fund:{id}, contact:{name,id}, idempotency_key, razorpayx_account_number, user_comment}`, max 15 rows | **No** — `validateInput('batch_approve'\|'batch_reject', $input)` only, no `verifyOtp` call anywhere in this path |
| `POST payouts/approve/bulk` | `PayoutController@bulkApproveFundAccountPayouts` → `Service::bulkApproveFundAccountPayouts` (`Route.php:2328`) | **Dashboard/merchant self-serve** — no `X-Batch-Id` | `{payout_ids:[...], otp, token}` | **Yes** — `$this->user->validateInput('verify_otp', ...)` then `(new User\Core)->verifyOtp($input+['action'=>'approve_payout_bulk'], ...)` (`Service.php:1506-1547`) before any per-payout `approvePayout()` call |

Batch's `payout_approval.json` calls the **first** route (path literally `payouts/bulk_approve`, matches Batch's own JSON config verbatim, `batch/src/main/resources/payout_approval.json`) — the **non-OTP** one. The prior reports' framing ("Bulk approvals via Batch bypass WFS entirely, no OTP question applies") is correct in outcome for the *no-OTP* part but wrong on the *bypasses WFS* part per point 2 above: Batch-driven bulk-approve *does* reach WFS whenever the target payout is workflow-service-backed.

### 6. Where OTP / `enable_approval_via_oauth` is actually enforced — fully closed, in the monolith, not in WFS or PS

Confirmed absent from `workflows` (repo-wide grep, `internal/dcs/features/features.go:8-24` lists only `skip_approval_if_creator_is_checker`-equivalent `DcsSkipApprovalForCreator` and `disable_wf_config_dimensions_for_s2p`) and absent from `payouts` (declared as a dead feature constant, `internal/app/common/appConstants/features.go:46`, zero other references). **It lives in, and is fully load-bearing in, the API monolith's *single-payout* dashboard approve path**:

`Payout\Service::approveFundAccountPayout` (`api/app/Models/Payout/Service.php:1165-1226`):
- `isXPartnerApproval()` (`Service.php:6914-6932`): true iff the caller authenticated with an OAuth access token (`$auth->getAccessTokenId() !== null`), that token's scopes include `rx_partner_read_write` (`OAuthScopes::RX_PARTNER_READ_WRITE`, `api/app/Http/OAuthScopes.php:32`), **and** the merchant has feature `enable_approval_via_oauth` enabled (`Features::ENABLE_APPROVAL_VIA_OAUTH`, `api/app/Models/Feature/Constants.php:2110`).
- If **true**: `validatePayoutForApprovalViaOAuth()` (`Validator.php:2389-2407`) only checks the payout's `merchant_id` matches the OAuth token's merchant — **no OTP call at all**. This is a **partner-integration bypass**, not a general 2FA relaxation: it lets a Razorpay-partner app holding a scoped OAuth token approve a payout on the merchant's behalf without the merchant's human operator entering an OTP.
- If **false** (the default for essentially all merchants — this is what a normal dashboard "Approve" click hits): `$payoutValidator->validateInput(Validator::APPROVE_PAYOUT_RULES, $input)` then **`$this->user->validateInput('verifyOtp', ...)` + `(new User\Core)->verifyOtp($input + ['action'=>'approve_payout','payout_id'=>$id], $this->merchant, $this->user)`** (`Service.php:1216-1220`) — **OTP is mandatory** before `Core::approvePayout` is ever called.

So the proto's own doc comment (§2 table) is the accurate description, and both `12_payouts_approval_queue_bulk.md`'s and `23_workflows_batch.md`'s framing of this flag as "presumably OTP/2FA-related, enforced somewhere upstream, not located" should be read as: **located — in the monolith — and it is the inverse of an OTP gate (it's the one condition under which OTP is *skipped*, for OAuth-authenticated partner callers only); OTP itself is unconditional on every other single-payout approve call.** Whether the bulk dashboard path (`payouts/approve/bulk`, §5 table) or the Batch CSV path (`payouts/bulk_approve`) is subject to the same OAuth-bypass was not checked (only the two call sites cited above were read); the Batch CSV path has no OTP call to begin with, so the OAuth-bypass flag is moot for it either way.

### 7. Payout state transitions on approved/rejected/expired

Unchanged from `12_payouts_approval_queue_bulk.md §A.4` (re-verified, `payouts/internal/app/payouts/core.go:5108-5163`, `state_machine.go:432-434`, no `EventExpired` anywhere): approve re-enters normal post-create processing (no dedicated "approved" event); reject is `pending → rejected` via `EventRejected`; no expiry event/transition exists in PS's state machine.

---

## WFS internals — running it REAL

### Twirp API surface (confirmed unchanged from `23_workflows_batch.md §A.2`, spot-checked)

Four services on one mux (`cmd/api/main.go:84-116`), one shared HTTP-Basic-Auth allowlist, no per-RPC restriction:

| Service | Methods |
|---|---|
| `WorkflowAPI` | `Create, Recreate, Get, ListByIds, ListPending, List, Terminate, AddAssignee, RemoveAssignee, AdminAction` |
| `ActionAPI` | `Create, CreateWithEntityId, Get, List, CreateDirectOnWorkflow` |
| `ConfigAPI` | `CreateV2, UpdateV2, DeleteV2, Create, Get, List, Update, Delete` |
| `CommentAPI` | `Create, Get, List` |

Inbound auth: HTTP Basic, exact-match allowlist (`internal/boot/hooks/auth.go:47-72`), no per-RPC scoping. Proto files not vendored (`.gitignore` excludes `proto`/`rpc`; fetched at build time via `make proto-fetch` from `razorpay/proto`).

### Config templates

`ConfigTypeToDetailsMap` (`internal/dto/config_model.go:86-122`) — 5 known config types, only one payout-relevant:

| `config_type` string | Cadence domain | task list | callback service |
|---|---|---|---|
| `payout-approval` | `payouts` | `payouts-approval` | `rx_live` |
| `purchase-order-approval` | `purchase_orders` | `purchase_order_approval` | `vendor_payments` |
| `goods-received-note-approval` | `goods_received_note` | `goods_received_note_approval` | `vendor_payments` |
| `vendor-payment-v2-approval` | `vendor_payments_v2` | `vendor_payments_v2_approval` | `vendor_payments` |
| `vendor-onboarding-approval` | `vendor_onboarding` | `vendor_onboarding_approval` | `vendor_experience` |

Caller-facing `ConfigAPI.CreateV2` request (`RangeConfig{Range:"min-max", Steps:[{Step,Op:"AND"|"OR", Roles:[{RoleName,RoleId,ApprovalCount}]}]}`) is compiled by `ConvertToTemplate` (`config_model.go:362-486`) into the full `Template{StateTransitions, StatesData, AllowedActions, Meta{Domain,TaskListName,WorkflowExpireTime,DecisionTaskTimeout}}` state graph executed by Cadence. Validation bounds (`internal/entities/config/validation.go`, this pass's own read):

| Field | Rule | Line |
|---|---|---|
| `owner_id`, `owner_id` (ConfigV2) | required, exactly 14 runes | `:78,90,303` |
| `Meta.WorkflowExpireTime` | hours; `Max = 24*365*10` (10y), `Min = 24*30*4` (~4mo) | `:357` |
| `Meta.DecisionTaskTimeout` | seconds; `Max=Min=10` (fixed at 10) | `:358` |
| `Meta.TaskListName` | required, 3–255 runes | `:356` |
| `RoleConfig.RoleName` | required, ∈ `ValidRoleNamesForV1={role,user_id,email}` or `ValidRoleNamesForV2={role,user_id,email,manager,group_head}` per config `Version` | `:177`, `pkg/validation/rule/rules.go:37-49` |
| `RoleConfig.RoleId` | required (free-form string; only cross-checked against `ValidGroupRoles` when `RoleName=="group_head"`, requiring prefix ∈ `{requester_group_type, group, creator_group_type}`) | `:178-180`, `rules.go:51-53,283-291` |
| `RoleConfig.ApprovalCount` | required, must parse as integer | `:179`, `rules.go:189-198` |
| `StepConfig.Op` | required, ∈ `{"AND","OR"}` | `rules.go:221-230` |
| duplicate `manager`/`group_head` role ids within one config | rejected (`AreRolesConfigured`) | `rules.go:290-308` |
| `Config.Name` | required, 3–255 runes | `:304` |
| `Config.OwnerType` | required, ∈ `{User, Merchant}` | `:298` |
| `Config.OrgId` | required, exactly 14 runes | `:299` |
| `Config.Enabled` | ∈ `{"true","false"}` (**string**, not bool) | `:300` |

A second, unrelated template kind (`AslTemplate`, Amazon-States-Language-shaped, for non-approval automations e.g. Splitz-experiment workflows) exists on the same oneof — irrelevant to payout approval, not re-traced this pass.

### MySQL migrations (10 files, `internal/database/migrations/`) — full schema, this pass's own read

| Table | Key columns | Purpose |
|---|---|---|
| `configs` | `id CHAR(14) PK, type, version int, name, template JSON, owner_id CHAR(14), owner_type, service, org_id CHAR(14) NULL, context JSON NULL, enabled CHAR(10), created_at/updated_at/deleted_at` | config templates (`20200612161348`) |
| `workflows` | `id CHAR(14) PK, execution_id varchar (Cadence workflow id), config_id CHAR(14), config_version int, type, entity_id CHAR(14), entity_type, status, domain_status, creator_id/type, owner_id/type, org_id CHAR(14), service, title, description, context JSON NULL, diff JSON, callback_details JSON NULL, created_at/updated_at, assignee_id CHAR(14) NULL` (+ index on `assignee_id`) | one row per triggered workflow (`20200712201722`) |
| `states` | `id CHAR(14) PK, workflow_id CHAR(14), status, name, group_name, type, rules JSON, callbacks JSON NULL, owner_id/type, service, created_at, processed_at NULL, updated_at, deleted_at NULL` | one row per approval level/step (`20200704083621`) |
| `actions` | `id CHAR(14) PK, workflow_id, state_id, actor_id CHAR(14), actor_type, actor_property_key, actor_property_value, actor_meta JSON NULL, state_group NULL, action, data JSON NULL, status, failure_reason LONGTEXT, idempotency_key CHAR(30) NULL, owner_id/type, service, created_at/updated_at` | one row per approve/reject click (`20200702202525`) |
| `comments` | `id CHAR(14) PK, workflow_id, action_id CHAR(14) NULL, commenter_id, commenter_type, comment text, owner_id/type, service, created_at/updated_at, deleted_at NULL` | (`20200707080613`) |
| `state_change_logs` | `id BIGINT AUTO_INCREMENT, refer_table varchar(64), refer_id CHAR(14), event, from_status, to_status, mode ENUM(MANUAL,SYSTEM), triggered_by NULL, created_at/updated_at` | generic audit trail for the `pkg/transition` FSM (`20200612155608`) |
| `assignee` | (dashboard "assign to me" bookkeeping) | `20221020231335` |
| index-only migrations | `20221020095849` (list-view indexes), `20231030231335` (adds config dimensions), `20241126231335` (adds `current_group_name` to `workflows`) | schema evolution, no new tables |

### Cadence dependency

`go.uber.org/cadence v0.13.4` + `go.uber.org/yarpc v1.42.0` (`go.mod`). **Not vendored/bundled** — `README.md:36-49` instructs cloning `uber/cadence` separately and running **its own** `docker-compose up` (brings up Cassandra + Prometheus + Grafana as Cadence's own dependencies), exposing tchannel `7933`, grpc `7833`, cadence-web `8088`, grafana `3000`. WFS itself ships **no docker-compose of its own** (confirmed absent, this pass). Three WFS binaries: `cmd/api/main.go` (Twirp HTTP server), `cmd/workers/main.go` (the actual Cadence worker — registers config/workflow/state/action handlers, `job.StartWorkers(ctx, workflowType, domains)`, selected via `-WORKFLOW_TYPE`/`-WORKFLOW_DOMAINS` flags), `cmd/migration/main.go` (goose runner).

**Minimal REAL runbook**: (1) Cassandra + Cadence server (from `uber/cadence`'s own compose) — heaviest single new dependency in this lane; (2) MySQL 8 + `goose` migration run (10 files above); (3) WFS `cmd/api` (Twirp HTTP, needs `[db]`, `[clients.payouts_live]`, `[auth.*]` allowlist config); (4) WFS `cmd/workers` **once per Cadence domain/task-list combination actually exercised** (at minimum `payouts`/`payouts-approval` for payout approval) — the worker process is what runs the state-graph/callback logic, without it `Create` will insert DB rows and start a Cadence execution but nothing will ever advance or fire a callback. Redis (`pkg/cache`) exists but is not load-bearing for approval/callback/expiry logic (not traced further, per `23_workflows_batch.md`'s own caveat, re-confirmed no new evidence this pass).

**Feasibility/complexity, honestly**: the Go/MySQL/Twirp half is straightforward (small schema, ordinary REST-ish JSON-over-HTTP contract, `docker-compose`-able). The Cadence half is the real cost — Cassandra+Cadence-server is a non-trivial multi-container stack with its own storage/schema bootstrap, on top of which WFS's *own* two extra processes (api + workers, per domain) must run and be configured to point at it. This is buildable in an environment that already tolerates heavier infra (it is a real, non-mocked distributed workflow engine), but it is the single heaviest addition of any component examined in this lane — **meaningfully more infrastructure than the rest of ENV2_COMPOSE's arena combined** (arena today is MySQL×4 + Postgres + Mongo + Redis + Kafka + LocalStack, no Cassandra, no Cadence).

### `[clients.<name>]` callback config table, Basic auth users, retry/backoff, expiry (re-confirmed unchanged from `23_workflows_batch.md §A.7/§A.8/§A.10`, spot-checked against `internal/client/types/common/callback.go` and `internal/entities/config/validation.go:357` this pass)

- Callback target resolution: `boot.Config.Clients[cb.Service]` (`callback.go:69-99`), static `[clients.<name>]` TOML table, not service discovery. Prod: `[clients.payouts_live] host="https://payouts.razorpay.com", username="payouts_live"`.
- Callbacks execute as genuine Cadence Activities (durable across worker restarts). Approval-type retry policy (`internal/client/types/approval/workflow.go:24-45`): `ScheduleToStartTimeout=365d, StartToCloseTimeout=30s (per attempt), RetryPolicy{InitialInterval=2s, BackoffCoefficient=5.0, MaximumInterval=1h, ExpirationInterval=30d}`. Default per-callback HTTP timeout 60s if unset.
- Inbound Basic-Auth allowlist (`internal/boot/hooks/auth.go`, `config/prod.toml:185-192`): `credentials.Payouts = {username:"payouts", password:<secret>}` (distinct from the outbound `payouts_live`/`payouts_test` credential pair).
- Expiry: bounded purely by Cadence's `ExecutionStartToCloseTimeout = Meta.WorkflowExpireTime` hours; if unset defaults to 10 years; if set, minimum ~4 months (`validation.go:357`, re-confirmed this pass). **No `NewTimer`/`TimedOut`-handling code anywhere in `internal/client/types/approval`** (re-confirmed absent this pass) — a genuine Cadence timeout fires no callback to PS; WFS's own DB `workflows.status` row is left at whatever it last was (`"initiated"` in practice).

### Minimal runbook summary

| Piece | Needed | Port/notes |
|---|---|---|
| Cassandra | yes (Cadence's own dependency) | from `uber/cadence` repo's compose |
| Cadence server | yes | tchannel 7933, grpc 7833 |
| cadence-web | optional (debugging only) | 8088 |
| MySQL 8 | yes (WFS's own) | 10 migrations, `goose` |
| WFS `cmd/api` | yes | Twirp HTTP, needs `[clients.payouts_live]`, `[auth.*]` |
| WFS `cmd/workers` | yes, ≥1 per domain/task-list actually exercised | `-WORKFLOW_TYPE`, `-WORKFLOW_DOMAINS=payouts` at minimum |
| Redis | optional (cache only, not load-bearing for approval) | |

---

## Batch internals (re-verified this pass against source, unchanged in substance from `23_workflows_batch.md §B` — spot-check evidence below; full field-by-field contracts in that report's §C.1-C.6 remain accurate for the routes it covers)

- `payout.json` `DataProcessing` step, verbatim (`batch/src/main/resources/payout.json:49-68`): `maxThreads=10, chunkSize=1, enableBulk=true, bulkSize=5`, reader `batchEntryDataReader{batchEntryStatusList:["CREATED"], saveState:false, pageSize:1000}`, processor `bulkApiCallDataProcessor{apiProcessor:{type:"payoutApiProcessor", idempotentKey:"idempotency_key", successStatusCode:[200]}, endpoint:"payouts/bulk", failOnServerError:false}`.
- Idempotency key derivation, verbatim (`BulkApiCallDataProcessorImpl.java:106,163-166`): `idempotentKeyPrefix="batch_"`, key = `idempotentKeyPrefix + record.getId()`. `BatchEntry.id`/`Batch.id` are **not** DB auto-increment integers — both use `@GeneratedValue(generator="rzp_unique_id")` → `CustomIdGenerator` (`commons/CustomIdGenerator.java:13-53`): base62(nanoTime) + 4 random base62 digits, asserted `length==14` — the same CHAR(14) convention as every other Razorpay entity id (payouts, WFS). No Redis mutex anywhere in this path (re-confirmed: `grep -rn "RedisMutexLock" batch/src/main/java` matches only `CacheIdempotencyEnabledBulkApiCallDataProcessorImpl` and the cache-repo impl, never `BulkApiCallDataProcessorImpl`/`BatchCore`).
- `payout_approval.json` header/config, verbatim (`batch/src/main/resources/payout_approval.json:1-30`): staging columns are an export-then-reupload sheet (`"Approve (A) / Reject (R) payout"` + 15 `(do not edit)` columns); processor `payoutApprovalProcessor`.
- `Batch`/`BatchEntry`/`BatchType` entity fields (`entity/Batch.java`, `entity/BatchEntry.java`, `entity/BatchType.java`, this pass's own read): `Batch{id, entityId, name, batchTypeId, mode, creatorId, creatorType, scheduled bool, uploadCount, processedCount, failureCount, totalCount, successCount, attempts, status:BatchStatus, settings:jsonb, amount:long}`; `BatchEntry{id, batchId, seqNumber:int, rowData:text, responseData:text, status:BatchEntryStatus}`; `BatchType{id, name}`.
- Enums, verbatim: `BatchStatus{CREATED,SCHEDULED,STAGING,VALIDATING,PROCESSING,OUTPUT,PAUSED,CANCELLED,FAILED,COMPLETED,VALIDATED,VALIDATION_FAILED,RESUMED}` (running = `{STAGING,VALIDATING,PROCESSING,OUTPUT}`); `BatchEntryStatus{CREATED,VALIDATED,FAILED,PROCESSED}`.
- Outbound-call headers/auth, retry policy, response envelope: unchanged from `23_workflows_batch.md §B.5` (`X-Batch-Id`, `X-Creator-Id`, `X-Creator-Type`, `X-Entity-Id`, Basic `rzp_<mode>_<entity_id>:<BATCH_API_SECRET>`; fixed-backoff retry 5 attempts×5s default; `payouts.json` `failOnServerError:false`).
- **`BATCH_SERVICE_PAYOUT_APPROVAL_BULK` — the literal symbol both prior reports declared absent from *both* `batch` and `workflows`** is real, but lives in the **monolith**, spelled `BATCH_SERVICE_PAYOUT_APPROVAL_BULK_RESPONSE` (`api/app/Trace/TraceCode.php`, referenced `Service.php:3559,3617`) plus siblings `BATCH_SERVICE_BULK_BAD_REQUEST`/`BATCH_SERVICE_BULK_EXCEPTION` (`TraceCode.php:7970,8002`) — this is simply the monolith's own trace-code name for the `approveBulkPayout` handler in §5 above, not a separate mechanism.

---

## Corrections to existing reports

| Report | Claim | What source actually says | Evidence |
|---|---|---|---|
| `23_workflows_batch.md §C.6.1`; `ARCHITECTURE_DELTA.md` (implicitly, via the substitute contract) | PS sends `config_id` "from a prior `ConfigAPI.CreateV2` response" in `WorkflowAPI.Create` | PS's `createWorkflowCreateRequestBody` never sets `ConfigID` (stays `""`); WFS resolves the config purely via `FindByOwnerDetails(owner_id, owner_type, service, org_id, enabled)` — `config_type` is not even part of that lookup | `payouts/pkg/workflow/workflow_create.go:225-262`; `workflows/internal/entities/workflow/server.go:56-60`; `workflows/internal/entities/workflow/interactor.go:290-317`; `workflows/internal/entities/config/repo.go:93-104` |
| `23_workflows_batch.md §B.8`; `ARCHITECTURE_DELTA.md §"Confirmed-1"` | "Batch's `payout_approval` batch type calls `POST payouts/bulk_approve` directly on Payouts Service, bypassing WFS entirely" — stated as a closed, settled fact | `payouts/bulk_approve` is a **monolith** route (`Route.php:2319`), absent from the `payouts` (Go microservice) repo entirely (confirmed by grep, matches `12_...md §A.7`'s independent absence-finding for that repo). The monolith handler branches per-payout: if `EntityMap\Repository::isPresent('payout', id)` is true, it calls **WFS directly** (`$this->workflowService->createActionOnEntity(...)`) — it does *not* bypass WFS in that case. It only falls back to a **legacy monolith-local checker system** (not WFS, not the Go payouts microservice) when no WFS-backed workflow is found for that payout | `api/app/Http/Route.php:2319`; `api/app/Models/Payout/Service.php:3539-3681`; `api/app/Models/Payout/Core.php:3123-3128,3232-3335,3589-3604,3614-3648,8010-8035` |
| `12_payouts_approval_queue_bulk.md §A.7`; `23_workflows_batch.md §A.15 item 5`; both master unresolved-question lists | `BATCH_SERVICE_PAYOUT_APPROVAL_BULK` confirmed absent from both `payouts` and `batch`/`workflows` — presumed name from "elsewhere," not located | It **is** located: `BATCH_SERVICE_PAYOUT_APPROVAL_BULK_RESPONSE` (+ `_BAD_REQUEST`/`_EXCEPTION` siblings) in the API monolith's `TraceCode.php`, naming exactly the `approveBulkPayout` handler described above | `api/app/Trace/TraceCode.php:7970,8002`; `api/app/Models/Payout/Service.php:3539,3559,3617,3586,3603` |
| `12_payouts_approval_queue_bulk.md §A.9 item 2`; `23_workflows_batch.md §A.15 item 4`, `Remaining unknowns` item 2 | `enable_approval_via_oauth` "presumably enforced in WFS/dashboard, not located"; framed alongside OTP/2FA as if it *is* (or gates) the OTP mechanism | It is located, in the monolith, and is the **opposite** of an OTP gate: it's a per-merchant opt-in that lets an OAuth-token-authenticated **partner app** (scope `rx_partner_read_write`) approve a payout *without* OTP; OTP itself (`verifyOtp`, action `approve_payout`) is unconditional on every other single-payout dashboard approve call, gated by nothing else | `api/app/Models/Payout/Service.php:1165-1226,6914-6932`; `api/app/Models/Payout/Validator.php:2389-2407`; `api/app/Models/Feature/Constants.php:2110`; `api/app/Http/OAuthScopes.php:32`; proto doc `config-proto/rzp/x/merchant/payouts/workflows.proto:8-13` |
| (new finding, not a prior-report claim, but corrects the lane brief's framing) | Lane brief asks to check whether OTP/2FA "gates approval" broadly | OTP gates the **single-payout dashboard approve** path unconditionally except for the OAuth-partner bypass above; the **Batch CSV bulk-approve** path (`payouts/bulk_approve`) has **no OTP call anywhere**; a **separate, distinct** monolith route (`payouts/approve/bulk`, dashboard multi-select bulk-approve) **does** require OTP | `api/app/Models/Payout/Service.php:1216-1220` (single) vs `:3539-3627` (batch, no otp) vs `:1506-1547` (dashboard bulk, otp) |
| none prior (net-new, self-consistency check) | — | `payouts`'s own `workflow_config`/`GetConfigByConfigTypeAndMerchantId` (the table the twin seeds, `seeds/s4/payouts.sql:89-96`) has **zero callers anywhere in `internal/app/payouts` or `internal/controllers`** outside its own package's unit tests — it is wired into the DI container (`internal/boot/handler.go`) but dead on every real request path found | `payouts/internal/app/workflowConfig/core.go` (definition); `grep -rln "workflowConfig\." internal/ pkg/` → only `boot/handler.go` and the package's own tests |
| none prior (net-net, lane-brief item "check ENV2 config for `[workflows]`/`[wfs]` client block") | Lane brief states "payouts arena config has no `[workflows]`/`[wfs]` client block" | It **does** have one — `[workflow]` in both `config/templates/base/payouts/default.toml:275-291` and `arena.toml:299-315` — but in `arena.toml` it is deliberately pointed at `host = "http://127.0.0.1:1"`, an unroutable loopback port, i.e. present-but-dead-on-purpose, not absent | `ENV2_COMPOSE/config/templates/base/payouts/arena.toml:299-316` |

---

## Twin comparison

| Component/path | Production mechanism | Twin mechanism (file) | Verdict | What differs | Controls |
|---|---|---|---|---|---|
| Workflow Service (WFS) itself | Go/Twirp + Cadence, 3 binaries, MySQL, real callback/retry engine (§WFS internals) | **Absent.** `docker-compose.yml` has no `workflows*` service; `[workflow]` client in `arena.toml:299-316` points at `http://127.0.0.1:1` (dead loopback) | **MISSING** | Every field/mechanism above (Create contract, config resolution, callback delivery, retry, expiry, self-approval) | approval, routing |
| PS→WFS `WorkflowAPI.Create` call | Real Twirp POST, `[200,201,409]` success set, config resolved server-side by owner tuple | Call attempted against `127.0.0.1:1` → connection refused → `HandleWorkflowsIfApplicable` returns `WorkflowCreationBadRequestError` (400) → **entire payout-create request fails**, no payout persisted for any merchant that reaches the workflow-applicable branch (`processor/base.go:285-289`; `baseHelper.go:37-46`) | **INCORRECT** (as a representation of the golden-path M3 flow; correct as a representation of "WFS down") | Prod: payout enters `pending`, waits for approval. Twin today: payout-create call itself errors out for M3 whenever `IsWorkflowApplicable` is true (i.e. whenever created via non-API auth) | payout/transfer state, merchant-visible state |
| DCS `Workflows` config, M3 fixture | `enable_payout_workflow`, `skip_workflow_for_dashboard`, `skip_workflow_for_payroll`, `skip_approval_workflow_for_api`, `enable_approval_via_oauth` — proto in §2 table | `seeds/s4/dcs_fixtures.json` M3 row: `enable_payout_workflow:true, skip_workflow_for_dashboard:false, skip_workflow_for_payroll:true, skip_approval_workflow_for_api:true, enable_approval_via_oauth:false` — field names/types match the proto exactly | **CONTRACT-FAITHFUL** (values are internally consistent with `IsWorkflowApplicable`'s logic — see caveat) | `skip_approval_workflow_for_api:true` means M3's workflow is only reachable via non-Private auth (see §1 correction) — worth flagging in the seed comments if not already understood by whoever drives the golden flow | routing, approval |
| `payouts`-side `workflow_config`/`workflow_state_map` tables | `workflow_config`: dead read path in real code (never queried outside tests). `workflow_state_map`: write-only mirror of WFS's `POST /v1/workflow/state` callback, dual-forwarded to monolith, never read back | Twin pre-seeds both tables directly (`seeds/s4/payouts.sql:89-100`) with a synthetic finance_l1/finance_l2/owner 3-step config, matching the real schema exactly | **REPRESENTATIVE** (schema-correct, but not load-bearing — see Corrections; these rows will never be read by real PS code, only ever visible via direct DB inspection) | Real PS never derives approval eligibility from these local tables — the state graph and role rules live entirely in WFS's `configs`/`states` tables, which don't exist in the twin either | none (cosmetic) |
| monolith `workflow_entity_map` (API-DB stub) | Real per-payout "is a WFS workflow open" lookup, read by `shouldCallWorkflowService` before every monolith-mediated approve/reject | `seeds/schema-patches/apidb.sql:34` creates the table (empty, populated at runtime per `SYNTHETIC_FIXTURE_SPEC.md`) | **CONTRACT-FAITHFUL** (schema matches `EntityMap\Repository`'s expectations) | Needs a row inserted at workflow-creation time for the twin's monolith-stub to route correctly — not confirmed whether `monolith-stub/server.py` does this (grep found zero workflow/batch handling in it at all — see below) | routing (approval vs legacy-checker branch) |
| PS inbound `/v1/workflow/state`, `/v1/workflow/state/:id` | Real BasicAuth-gated write route, persists + dual-forwards | Route exists in the real `payouts` binary the twin runs (this is PS's own code, unchanged) — but nothing in the twin ever calls it, since there is no WFS to originate the callback | **MISSING** (mechanism exists in the binary; nothing drives it) | No fixture/traffic exercises this path | observe only |
| Batch service itself | Java/Spring, 7 k8s deployments, Postgres, `payout.json`/`payout_approval.json` JSON-config engine | **Absent.** No `batch*` service in `docker-compose.yml`; no `substitutes/batch*` | **MISSING** | Entire bulk-create/bulk-approve-via-CSV flow | route, idempotency, approval |
| `payouts/bulk_approve` (the route Batch calls) | Monolith route → WFS-or-legacy-checker branch (§5) | `substitutes/monolith-stub/server.py` has **zero** workflow/batch handling (`grep -n -i "workflow\|batch"` → no output) | **MISSING** | Whole chain | approval, routing |
| `payouts/bulk` (Batch's create-side call, and PS's own `/v1/payouts/bulk`) | PS route exists and is real (`payout_internal_routes_with_passport.go`), documented in `12_...md §D` | PS's own route exists unchanged in the twin's `payouts-api` container; only the **Batch caller** is missing (no traffic generator hits it with Batch's headers/idempotency-key shape) | **REAL** (server side) / **MISSING** (caller) | Twin can exercise this endpoint directly (curl with `X-Batch-Id` etc.) even with no Batch substitute — genuinely different from the approve side, which needs the monolith-stub to do real branching work | idempotency, payout/transfer state |
| Cadence | Long-running distributed workflow engine, durable retry/backoff | Absent (follows from WFS being absent) | **MISSING** | — | — |

---

## Recommendation: real vs substitute

**Workflow Service (WFS): REAL is feasible but is the single most expensive addition in this lane.** The repo builds (`go.mod`, Go 1.25, ordinary `goose` migrations), and the Twirp/callback contract is fully specified from source (§Production behaviour, §WFS internals). The blocker is Cadence: WFS delegates *all* state-machine execution, retry, and durability to a Cadence server, which itself needs Cassandra (`uber/cadence`'s own compose). This is buildable — nothing here is a black box — but it roughly doubles the arena's infra footprint for one component. If REAL: minimal footprint is Cassandra + Cadence server + WFS MySQL + WFS `cmd/api` + WFS `cmd/workers` (≥1, domain=`payouts`) — no Redis needed (cache-only, not load-bearing).

**If SUBSTITUTE instead**, the contract a stub server must implement (all fields/behavior confirmed from source this pass and the prior lane, consolidated):

1. `POST /twirp/rzp.workflows.workflow.v1.WorkflowAPI/Create` — accept the exact body in §2; synthesize an id; return `{id, config_id, status:"initiated", domain_status:"created", ...echoed fields}`; **do not require `config_id`** (real WFS resolves it server-side, so a stub is free to ignore config resolution and just accept any/no config_id).
2. `POST /twirp/rzp.workflows.action.v1.ActionAPI/CreateWithEntityId` (and `Create`/`CreateDirectOnWorkflow`) — accept `{entity_id, entity_type, action, actor_id, actor_type, actor_property_key, actor_property_value, ...}`; a permissive stub may accept unconditionally (real WFS's own role-check is pure string-match with no independent verification, so accepting-anything is not "more wrong" than production, per prior lane's own note, re-confirmed).
3. On the configured approval count being met for a step, fire the outbound callback: `POST <client.host><url_path>` (for payouts: `.../v1/payouts/payouts_internal/{payoutId}/approve` or `.../reject`) with HTTP Basic Auth `payouts_live:<secret>`, `Content-Type: application/json`; **the stub calling PS must accept any of `{200,201,409}`** as PS's own registered success codes (§2) — i.e. if the stub is testing PS's callback handler, sending back `200` is sufficient and matches what `23_workflows_batch.md §C.6.3` already assumed; if PS is the one calling a stub-WFS's create endpoint, the stub's `Create` response just needs `status:200` to satisfy PS's `ReadResponse` check (`workflow_create.go:190`).
4. **No expiry callback needs to be implemented for behavioral fidelity** — real WFS fires none either (this is a confirmed production gap, not a twin gap to "fix").
5. Batch-approval path: since real Batch calls the **monolith's** `payouts/bulk_approve`, not WFS, a workflow substitute alone does not enable that flow — the monolith-stub also needs the branching logic in §5 (or a deliberately simplified always-call-WFS-substitute version of it) plus a `workflow_entity_map`-equivalent lookup.

**Batch service: SUBSTITUTE, not REAL — the effort/value ratio is poor for this lane's goals.** Batch's actual behavioral surface that matters here is entirely config-JSON-driven HTTP-array calls, none of which involve business logic beyond envelope construction (§Batch internals) — running the real 7-deployment Spring/Postgres/SQS stack buys almost no additional fidelity over a thin script that: (a) accepts a CSV/JSON upload, (b) chunks it into groups of ≤5 (create) or ≤15 (approve, monolith-side limit) with the exact idempotency-key derivation `"batch_"+<14-char id>`, (c) POSTs to the configured endpoint with `X-Batch-Id`/`X-Creator-Id`/`X-Creator-Type`/`X-Entity-Id` + Basic Auth, (d) parses the `items[]`-by-`idempotency_key` response envelope. Substitute contract (create side, already given exactly in `23_workflows_batch.md §C.2`, re-confirmed unchanged this pass) plus the approve-side contract newly derived here:

```
POST {monolith_base}/v1/payouts/bulk_approve
Headers: Authorization: Basic base64("rzp_<mode>_<entity_id>:<secret>"), X-Batch-Id: <14-char id>, X-Creator-Id, X-Creator-Type, X-Entity-Id, Content-Type: application/json
Body: JSON array, ≤15 elements, each:
  {"payout_update_action":"A"|"R", "idempotency_key":"batch_<BatchEntry.id>", "user_comment":"...",
   "payout":{"id","amount","currency","mode","purpose","narration","status"},
   "fund":{"id"}, "contact":{"name","id"}, "razorpayx_account_number":"..."}
Response (stub choice): either the monolith's real per-call semantics (single response, no items[] envelope —
  Service::approveBulkPayout accumulates a $payoutBatch array and returns it as one JSON response containing
  either the updated payout or a per-row {batch_id, idempotency_key, error:{description, public_error_code},
  http_status_code} on failure — this is NOT the same items[]-by-idempotency_key shape as the bulk-create
  endpoint's C.2 envelope, a genuine asymmetry between create and approve worth preserving if fidelity to the
  monolith's exact response shape matters) — 400 if X-Batch-Id missing, 400 if >15 rows, per-row 4xx/5xx wrapped
  inline rather than surfaced as the call's own HTTP status.
```

---

## Synthetic data

| Family/table | Field | Source evidence | Type/length | Constraints | Allowed values | FK/relationships | State rules | Distribution matters? | Generation rule | Exactness |
|---|---|---|---|---|---|---|---|---|---|---|
| WFS `configs` | `id` | migration `20200612161348` | `CHAR(14)` | PK | base62-ish 14-char id (match Razorpay convention) | referenced by `workflows.config_id`, PS's `workflow_config.config_id` | — | no | `ARENAWFC` + 6-digit seq | EXACT (schema) |
| WFS `configs` | `type` | same | `varchar(255)` | required | `"payout-approval"` for this lane (see 5-type table) | — | — | no | fixed string | EXACT |
| WFS `configs` | `template` | same + `config_model.go:362-486` | `JSON` | compiler-generated from `RangeConfig[]` | see Template schema in §WFS internals | — | must contain `START_STATE`→...→`END_STATE` reachable chain | yes — range/step/role shape drives which merchants see 1 vs 2-level approval | build via `ConvertToTemplate`-equivalent from a hand-written `RangeConfig` (example in `23_workflows_batch.md:63-83`, re-confirmed unchanged) | REPRESENTATIVE (real template compiler not re-implemented) |
| WFS `configs` | `enabled` | same, `validation.go:300` | `char(10)` | `∈{"true","false"}`, **string not bool** | — | — | `FindByOwnerDetails` filters `enabled="true"` literally | no | `"true"` | EXACT |
| WFS `configs` | `owner_id`,`owner_type`,`service`,`org_id` | `repo.go:93-104` | `CHAR(14)`/`varchar` | resolution key — at most one enabled row should exist per 4-tuple, else most-recent wins silently | `owner_type="merchant"`, `service="rx_live"` | `owner_id`=merchant id | — | no | mirror the merchant fixture id | EXACT |
| WFS `workflows` | `entity_id`,`entity_type` | migration `20200712201722` | `CHAR(14)`/`varchar` | — | `entity_type="payout"` | `entity_id`=payout id | `status∈{creation_in_progress,created,initiated,init_failed,processed,failed,terminated}` (from `23_...md §A.4`, re-confirmed unchanged) | no | mirror real payout id | EXACT (schema); REPRESENTATIVE (status taxonomy — not re-derived from source list literal this pass, taken from prior lane) |
| WFS `workflows` | `domain_status` | same + `workflow.go:629-639` | `varchar` | separate from `status` | `∈{"approved","rejected","closed"}`, plus `"created"` pre-processing | — | this is what PS's registered `DomainStatus.approved/rejected` callback keys off | no | — | EXACT |
| WFS `states` | `rules` | migration `20200704083621` | `JSON` | `{Key,ActorPropertyKey,ActorPropertyValue,Count,Min,Max,States[]}` | `ActorPropertyKey="role"` typical; `ActorPropertyValue`∈ merchant's configured role ids (`finance_l1`,`finance_l2`,`owner`, per twin's own M3 convention) | `workflow_id` FK | `type∈{"checker","between","merge_states"}` | yes — `Count` (approvals needed) drives multi-approver fixtures | — | REPRESENTATIVE |
| WFS `actions` | `idempotency_key` | migration `20200702202525` | `CHAR(30)` NULL | inbound-dedup only, not outbound | — | — | — | no | — | EXACT (schema) |
| WFS role names | `RoleName` | `pkg/validation/rule/rules.go:37-49` | string | `∈ ValidRoleNamesForV1={role,user_id,email}` or `ValidRoleNamesForV2` (+`manager`,`group_head`) depending on config `Version` | — | — | `manager`/`group_head` must not repeat `RoleId` within one config | no | pick `"role"` for a plain approval-role fixture | EXACT |
| Payouts `workflow_entity_map` | all columns | migration `20210824110739` | `CHAR(14)`×4 + `varchar` + 2×`int` | populated at real workflow-create time, not a fixture the twin should hand-seed (runtime table, per `SYNTHETIC_FIXTURE_SPEC.md`'s own convention for `payouts`/etc.) | — | `workflow_id`,`entity_id`(=payout id),`config_id` | — | — | leave empty in seed, populate via traffic | EXACT (schema; not seeded, correctly) |
| Payouts `workflow_config`/`workflow_state_map` | as seeded | `seeds/s4/payouts.sql:89-100` | matches migrations exactly | — | — | — | — | no | current seed is schema-correct but functionally inert (see Corrections) — safe to leave as-is, not worth removing, but should not be relied on to "prove" the approval path works | EXACT (schema) / cosmetic (function) |
| Monolith `workflow_entity_map` (apidb stub) | all columns | `seeds/schema-patches/apidb.sql:34` | matches `EntityMap\Repository`'s expected shape | — | — | — | this table's presence/absence for a given payout id is the literal `shouldCallWorkflowService` branch | yes — controls whether the monolith-stub, if extended, should call WFS-substitute or the legacy-checker branch | if extending monolith-stub to branch correctly, insert a row here at workflow-create time | REPRESENTATIVE |
| Batch `Batch`/`BatchEntry` ids | `id` | `commons/CustomIdGenerator.java:13-53` | `CHAR(14)` | base62(nanotime)+4 random base62 digits, asserted len==14 | — | `BatchEntry.batchId`→`Batch.id` | — | no | any 14-char string is fine for a substitute; exact generator not necessary to reproduce | REPRESENTATIVE |
| Batch idempotency key (create) | `idempotency_key` | `BulkApiCallDataProcessorImpl.java:106,163` | string | `"batch_" + BatchEntry.id` | — | — | stable across retries of the same row (no cross-attempt lock) | no | `"batch_" + <14-char id>` | EXACT |
| Batch→monolith bulk-approve row | `payout_update_action` | `payout_approval.json:13` | 1-char string | discriminator | `∈{"A","R"}` | — | — | yes if testing both approve and reject paths | — | EXACT |
| Batch enums | `Batch.status` | `enums/BatchStatus.java` | enum | — | `{CREATED,SCHEDULED,STAGING,VALIDATING,PROCESSING,OUTPUT,PAUSED,CANCELLED,FAILED,COMPLETED,VALIDATED,VALIDATION_FAILED,RESUMED}` | — | running subset `{STAGING,VALIDATING,PROCESSING,OUTPUT}` | no | — | EXACT |
| Batch enums | `BatchEntry.status` | `enums/BatchEntryStatus.java` | enum | — | `{CREATED,VALIDATED,FAILED,PROCESSED}` | — | — | no | — | EXACT |
| Monolith bulk-approve limit | request row count | `Validator.php:73,1521-1531` | int | max 15 | — | — | 400 if exceeded | no | keep synthetic batches ≤15 rows to match real validation | EXACT |

---

## Cannot be derived from repositories

1. **Production WFS `Config` template values per real merchant** (the actual range/steps/roles JSON any live merchant has configured) — DCS/WFS runtime data, not in any repo. Owner: Payouts/WFS platform team. Minimal request: a sanitized export of one or two real `configs.template` rows (config_type=`payout-approval`) with merchant-identifying fields redacted.
2. **Cadence server version/image actually run in prod** — WFS's `go.mod` pins the **client SDK** (`go.uber.org/cadence v0.13.4`), not the server. `README.md` only says "clone `uber/cadence`," no pinned tag. Owner: WFS/infra team. Minimal request: the exact `uber/cadence` server image tag/version used in prod's Cadence cluster (or confirmation that any recent compatible tag is fine for local dev, which the SDK compatibility usually tolerates).
3. **Whether Batch's `payouts/bulk_approve` is currently functional in prod at all**, given the monolith's own `payouts` table is reportedly dropped (`ARCHITECTURE_DELTA.md`'s Slack-sourced root-agent correction) and `processEntryForBulkPayoutApproval` fetches via `$this->repo->payout->findByPublicId` (the monolith's own Eloquent model). Owner: Payments-Payouts/monolith on-call. Minimal request: confirmation (or the actual runtime error/outcome) of a real `payouts/bulk_approve` call in the current prod environment.
4. **Whether the monolith's Basic-Auth resolution treats Batch's `rzp_<mode>_<entity_id>` credential as "Proxy" auth** for the purposes of `approveWorkflowViaWorkflowService`'s `"Auth is not proxy for payout approval"` gate (`api/app/Models/Payout/Core.php:8023-8030`) — not traced into the monolith's `BasicAuth` class this pass (out of this lane's primary scope; belongs to finding 20/21 territory). If Batch's calls do **not** resolve to Proxy auth, the entire WFS-backed branch of Batch's bulk-approve would 400 for every workflow-backed payout in prod, which would be a significant additional finding. Owner: API-monolith/edge team. Minimal request: either a read of `BasicAuth::isProxyAuth()`'s resolution logic for the `rzp_<mode>_<id>` username pattern, or a sanitized prod trace of one real Batch-driven `payouts/bulk_approve` call against a workflow-backed payout.
5. **`enable_approval_via_oauth`'s actual adoption** — which (if any) real partner merchants have this feature enabled, and whether any partner integration actually uses the `rx_partner_read_write` OAuth scope for payout approval in practice. Owner: Partnerships/OAuth platform team. A schema-only confirmation of feature-flag adoption counts (no PII) would suffice.
6. **The dashboard-side UI/UX around self-approval** (whether the "Approve" button is hidden for the payout's own creator) — this repo trio has no frontend code; if self-approval prevention exists anywhere, it is client-side or in a repo outside this lane's four. Owner: RazorpayX Dashboard/Frontend-X team.

---

## Fidelity tier verdicts

- **Workflow Service (WFS)**: **UNKNOWN-BLOCKED in the current twin** (component entirely MISSING; `[workflow]` client points at a dead loopback host). Feasibility if pursued: F3-capable (real Go+MySQL+Twirp builds; Cadence/Cassandra is the one heavy external dependency, not a blocker, just expensive).
- **PS↔WFS `WorkflowAPI.Create`/callback contract**: **CONTRACT fully specified from source** (this pass closes the `config_id` and success-status-code gaps) — ready for either a REAL WFS or a thin substitute; substitute contract given in full above.
- **PS's own `/v1/workflow/state` inbound route and `workflow_state_map`/`workflow_config` local tables**: **REAL** (unmodified PS binary) but **functionally inert** without a real or substitute WFS driving traffic into it — current twin seed data for these tables is schema-correct decoration, not behavior.
- **DCS `Workflows` proto/fixture for M3**: **CONTRACT-FAITHFUL**, with one behavioral caveat now documented (`skip_approval_workflow_for_api=true` narrows the auth types that actually reach the workflow-applicable branch).
- **Batch service**: **MISSING** in the twin; recommended tier if added is **CONTRACT-FAITHFUL SUBSTITUTE** (thin script), not REAL — the real Spring/Postgres/SQS stack adds infra cost with no behavioral upside for this lane's goals.
- **`payouts/bulk_approve` (Batch's actual approve integration point)**: **MISSING**, and its real-world mechanism is now known to route through the **monolith**, not directly through WFS or the payouts microservice — any substitute for this path must live in (or alongside) a monolith-stub, not a WFS-stub, and must implement the A/R-discriminated, ≤15-row, no-OTP contract given above, distinct in response shape from the bulk-**create** contract.
- **OTP/`enable_approval_via_oauth`**: **CLOSED** — fully located in the monolith (`Payout\Service::approveFundAccountPayout`), not a WFS or PS concern at all; not represented anywhere in the twin (no monolith-stub approve/reject handling exists), but also not needed for the Batch-driven bulk-approve path specifically, since that path never calls OTP either in production.
- **Self-approval prevention**: **CLOSED as "does not exist" in WFS**, PS, or the monolith code paths traced this pass — UNKNOWN whether it exists in the dashboard frontend (out of repo scope, see Cannot-derive #6).
