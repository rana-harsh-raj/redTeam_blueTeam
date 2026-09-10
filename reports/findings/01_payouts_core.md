# RazorpayX Payouts Service — Entry, Routing, Auth, State Machine, Persistence

Repo: `razorpay/payouts` @ `4bf3dbf9239feadea6d65ca90c893a988e116173` (branch `master`, shallow clone at
`/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture/payouts`)

## Repo summary

- **Type**: deployable backend service (not a library). Go module `github.com/razorpay/payouts` (`go.mod`).
- **Stack**: Go, Gin HTTP router (`gin-gonic/gin`), MySQL 8.0 (via `pressly/goose` migrations, `spine` ORM-ish layer), Redis, Kafka, Elasticsearch, TiDB (via internal WDA client), Splitz (Razorpay's experimentation service), `razorpay/goutils` shared libs (passport v3 JWT SDK, telemetry, splitz client), buf/protobuf+Twirp codegen (generated into `.gitignore`d `rpc/` from `github.com/razorpay/proto`), gorilla/goose migrations.
- **Processes** (`cmd/`):
  - `cmd/api/main.go` → `boot.API{}.Init` — HTTP API server (Gin), build-tagged `// +build boot`.
  - `cmd/workers/main.go` → `boot.Worker{}.Init` — background worker process.
  - `cmd/kafkaConsumers/main.go` → `boot.KafkaConsumer{}.Init` — Kafka consumer process (`internal/boot/boot_kafka_consumer.go`).
  - `cmd/migration/main.go` → `boot.Migrations{}.Init` — goose DB migration runner.
- **Deploy artifacts**: no Helm/K8s manifests found in-repo (likely externalized). Dockerfiles under `build/docker/prod/`: `Dockerfile.api`, `Dockerfile.worker`, `Dockerfile.kafkaConsumer`, `Dockerfile.migration`, plus `Dockerfile.ci`, `Dockerfile.ci-base`, `Dockerfile.ci-shard`, `Dockerfile.drone`; devspace-specific under `build/docker/devspace/`; dev/e2e under `build/docker/dev/` and `build/docker/e2e/`. `devspace.yaml` present (Razorpay's internal dev/staging orchestration tool) — no raw k8s yaml checked in. CI: `.github/workflows/` — `build.yml`, `e2e.yml`, `master_e2e.yml`, `ci-devspace.yml`, `buf-lint-breaking.yml` (proto), `api-breaking-change-detection.yml`, `gatekeeper.yml`, `central_security_checks.yml`, `slit-in-process.yml`.
- **Dev stack**: `deployment/dev/docker-compose.yml` — `mysql:8.0`, `redis:3-alpine`, `jaegertracing/all-in-one`, `confluentinc/cp-zookeeper`/`cp-kafka` (x2).
- **Commit**: `4bf3dbf9239feadea6d65ca90c893a988e116173`.

---

## Findings

### 1. HTTP transport, no active Twirp/gRPC server surface found

Claim: Service is a Gin-based REST HTTP service; Twirp/protobuf codegen exists (`buf.gen.yaml` generates Go+Twirp into `rpc/`) but `rpc/` is gitignored and no `twirp.` server registration was found in `internal/` — the generated types appear to be used for message/webhook payload shapes (e.g. `internal/app/payouts/webhooks.go` imports `payouts/rpc`), not as an exposed Twirp endpoint.
Repo/service: payouts
File: `buf.gen.yaml`; `.gitignore:30` (`rpc`); `internal/app/payouts/webhooks.go`
Confidence: probable
Capability: route / transform

### 2. Proxy cutover / shadow gateway framework (API monolith ⇄ Payouts Service)

Claim: A first-class "shadow gateway" framework (`internal/routing/shadowgateway/`) implements config-and-Splitz-driven cutover of individual routes between the API monolith and Payouts Service (PS), with four modes: `native` (PS serves), `proxy` (API monolith serves, PS streams request through — fail-safe default), `shadow_api` (both run, monolith response honored, PS is async shadow), `shadow_ps` (both run, PS response honored, monolith is async shadow). It is registered as the **first** middleware in the Gin chain, before all PS response/serialization middleware.
Repo/service: payouts
File: `internal/routing/shadowgateway/mode.go:18-33` (Mode enum), `internal/routing/shadowgateway/middleware.go:37-143` (Gateway.handle dispatch), `internal/routing/router/router.go:55-59` (registered first)
Evidence:
```go
const (
    ModeNative Mode = "native"
    ModeProxy Mode = "proxy"          // fail-safe default
    ModeShadowHonorAPI Mode = "shadow_api"
    ModeShadowHonorPS Mode = "shadow_ps"
)
```
Confidence: confirmed
Capability: route

### 3. Mode resolution is per-route, per-merchant, via Splitz experiment; fails safe to proxy

Claim: `resolveMode()` looks up route config (keyed `"method /gin/pattern"`) in `config.ShadowGateway.Routes`; if the route carries a Splitz `experiment_id`, it resolves merchant identity pre-auth (Edge passport JWT `consumer.id`, else impersonation claim, else `x-merchant-id` header fallback), calls Splitz `GetVariant`, and maps the variant's `mode` variable (or variant name) to a `Mode`. ANY failure (no merchant id, no splitz client, splitz error/timeout, unknown variant, shadow mode on non-GET route) resolves to `ModeProxy`.
Repo/service: payouts
File: `internal/routing/shadowgateway/mode.go:58-117` (`resolveMode`), `:155-200` (`merchantIdentity`/`merchantIdentityFromPassport`)
Confidence: confirmed
Capability: route / authorize (routing classification only, not real auth — explicitly documented)

### 4. Shadow gateway loop guard + internal-consumer bypass

Claim: Every request PS proxies/shadows to the monolith is tagged `X-PS-Proxied`; if PS itself receives a request already carrying that header, it never re-intercepts (breaks accidental API→PS→API loops). Additionally, requests identified as coming from internal consumers (the API monolith's own hop, FTS, other internal services — via `isInternalConsumer(c.Request, cfg.Auth)`) always run native, since the monolith can't authenticate PS-internal service credentials.
Repo/service: payouts
File: `internal/routing/shadowgateway/middleware.go:18` (`HeaderPSProxied`), `:85-113`
Confidence: confirmed
Capability: route

### 5. Shadow gateway config-only route onboarding; currently disabled in prod, piloted in devstack

Claim: Onboarding a route to the cutover framework is config-only (no code change) — add `[shadow_gateway.routes."get /v1/payouts/:id"]` with `experiment_id`. `enabled=false` in both `config/default.toml` and `config/prod.toml` (master kill switch off in prod as of this commit — "go-live is the one-line flip of `enabled`"). `config/devstack.toml` has it `enabled=true` for a single pilot route `GET /v1/payouts/schedule/timeslots` behind Splitz dev experiment `TUnTsUB8Os2kX0` (project name in comment: `payouts_shadow_gateway_scheduled_time_slots`, project id `RQDeYaM0u0QPxe`), default bucket `proxy` at weight 100 (i.e. inert today even though enabled).
Repo/service: payouts
File: `config/prod.toml:857-878`, `config/devstack.toml:707-720`, `internal/config/config.go:96-134` (`ShadowGatewayConfig`/`ShadowGatewayRouteConfig` structs)
Confidence: confirmed
Capability: route / observe

### 6. `payoutDashboardGetRoutes` — a second auth path specifically added for the shadow-gateway pilot route

Claim: `GET /v1/payouts/schedule/timeslots` is split into its own route group (`payoutDashboardGetRoutes`) using `ServiceBasicAuthOrPassport` instead of strict `BasicAuth`, because Edge-direct requests under the shadow-gateway cutover arrive without service BasicAuth credentials (the Edge-minted passport is the authenticator of record) while the API monolith's internal hop still presents service creds. Code comment explicitly calls this the "Edge -> PS proxy cutover" and references a parked branch `feat/payouts-proxy-cutover/masking-only`.
Repo/service: payouts
File: `internal/routing/router/payout_routes.go:105-133`
Confidence: confirmed
Capability: route / authorize

### 7. `ServiceBasicAuthOrPassport` — auth middleware built for the cutover: falls through to passport if no service creds present

Claim: New middleware admits a request on valid service BasicAuth; if no BasicAuth header at all, defers to `PassportAuthentication` (merchant-key/passport is authenticator); if BasicAuth is present with a *known* service username but wrong password, rejects loudly (won't silently treat a misconfigured internal caller as a merchant).
Repo/service: payouts
File: `internal/routing/middleware/auth.go:69-108`
Confidence: confirmed
Capability: authorize

### 8. Standard payout routes: strict service BasicAuth + Passport JWT, both required

Claim: The primary `/v1/payouts` route group requires **both** a valid service BasicAuth credential (`cred.API`/`cred.Workflow` from `config/*.toml [auth]`) AND a Passport JWT (`X-Passport-JWT-V1` header) whose legacy auth type is one of `private`, `proxy`, `privilege`. GET `/v1/payouts/:id` and POST `` (create) additionally run through `RequestDecrypter()`/`ResponseEncrypter()` payload-crypto middleware.
Repo/service: payouts
File: `internal/routing/router/payout_routes.go:17-103`
Confidence: confirmed
Capability: authorize / route

### 9. Idempotency: header-and-DB-backed, with a distributed-mutex race guard, currently soft-enforced behind two flags

Claim: Idempotency is driven by header `X-Payout-Idempotency` (via `EntityToHeaderMap[Payout]`, resolved to `constants.HeaderXPayoutIdempotency`), scoped per `(idempotency_key, merchant_id)`. On a request carrying the header, PS acquires a distributed mutex on `idempotencyKey+merchantID` (20-min timeout, `controllers.BaseService.AcquireMutex`), looks up an existing `idempotency_keys` row by `(idempotency_key, source_type, merchant_id)`, and — if found — presumably short-circuits to the earlier response (request-hash mismatch handling continues past what was read). If the request has **no** idempotency key at all, enforcement is currently optional: gated by `Features.IKeyAutoEnforcement` config flag AND a Splitz ramp-up experiment `SplitzExperimentList.IKeyAutoEnforcementRampUp`; only if both pass does PS check a per-merchant `IdempotencyKeyExclusion` list to decide whether to hard-fail with `MissingIdempotencyKeyInRequest`.
Repo/service: payouts
File: `internal/routing/middleware/idempotency_key.go:59-207`
Confidence: confirmed
Capability: reject / persist

### 10. Idempotency table + unique constraint

Claim: `idempotency_keys` table has a unique key on `(idempotency_key, merchant_id)`, plus a `request_hash` column used to detect a re-used key with a different request body.
Repo/service: payouts
File: `internal/database/migrations/20221004002554_create_idempotency_keys_table.go:14-27`
Evidence:
```sql
UNIQUE KEY idempotency_keys_idempotency_key_merchant_id_unique (idempotency_key,merchant_id),
```
Confidence: confirmed
Capability: persist / reject

### 11. Payout state machine — explicit finite state machine library (`pkg/tranisiton`, sic)

Claim: Payout status transitions are enforced through a dedicated state-machine package (note repo typo: `pkg/tranisiton`), initialized once in `internal/app/payouts/state_machine.go` via `transition.InitializeTransition(&Payout{}, appConstants.StateCreateRequestSubmitted)`. Each state has an `Enter` hook (side effects: `SetStatus`, `FireWebhookEventAsyncForPayout`), each event has `Before`/`After` hooks (structured logging). This is the single enforcement point for legal status transitions — code calling `Fire(Event...)` on a `Payout` will error if the current state isn't in the event's declared `.From(...)` set.
Repo/service: payouts
File: `internal/app/payouts/state_machine.go:1-16`
Confidence: confirmed
Capability: transform / reject

### 12. Full internal status set and internal→public status mapping

Claim: Internal statuses: `create_request_submitted`, `created`, `pending`, `pending_on_otp`, `scheduled`, `batch_submitted`, `queued`, `on_hold`, `ledger_response_awaited`, `initiated`, `processed`, `reversed`, `failed`, `cancelled`, `rejected`. These collapse to 6 public-facing statuses via `InternalToPublicStatusMap`: `processing` (created/initiated/ledger_response_awaited/create_request_submitted/batch_submitted), `queued` (on_hold/queued), `pending` (pending_on_otp/pending), `processed`, `reversed`, `rejected`, `cancelled`, `failed`.
Repo/service: payouts
File: `internal/app/common/appConstants/states.go:3-32`, `internal/app/common/appConstants/status.go:3-19`
Confidence: confirmed
Capability: transform / observe

### 13. Explicit transition table (from → to)

See **State machine table** below, extracted from `sm.Event(...).To(...).From(...)` calls.
File: `internal/app/payouts/state_machine.go` (line numbers in table)
Confidence: confirmed

### 14. Maker-checker / workflow approval gating

Claim: Whether a payout requires approval workflow is decided by `BasePayoutProcessor.IsWorkflowApplicable`, which short-circuits to "no workflow" for: fee-recovery payouts (`RzpFees` purpose) unless `EnableWorkflowForInternalContact`; internal contacts (same flag); merchant feature flag `PayoutWorkflows` off (checked via Splitz-gated dual naming: `appConstants.PayoutWorkflows` vs `features.PayoutWorkflows`, chosen by `IsPayoutWorkflowsDcsNameExperimentEnabled`); explicit `SkipWorkflow` request flag from `PAYOUT_LINKS` app; requests from `XPERIENCE` app; bulk/batch requests (`BatchID` present) with `SkipWorkflow`. Otherwise workflow feature is tagged `PAYOUT_WORKFLOWS` and (implied further down, not fully read) an approval record is created.
Repo/service: payouts
File: `internal/app/payouts/processor/baseHelper.go:107-155`
Confidence: confirmed
Capability: authorize / reject

### 15. Workflow state routes (approve/reject entrypoints)

Claim: `/v1/workflow/state` (POST create) and `/v1/workflow/state/:id` (PATCH update) are exposed to internal callers with `BasicAuth(cred.Workflow, cred.API)`, handled by `controllers.WorkflowService.{CreateWorkflowStateMap,UpdateWorkflowStateMap}`. Separately, `/v1/payouts_internal/approve_payout` and `/v1/payouts_internal/reject_payout`-style endpoints exist as `controllers.PayoutService.{ApprovePayout,RejectPayout}` under `payout_internal_routes.go` (BasicAuth-gated, multi-credential: API/Workflow/Xperience/FTS/VendorPayments/Settlements/Irctc).
Repo/service: payouts
File: `internal/routing/router/workflow_routes.go:12-32`, `internal/routing/router/payout_internal_routes.go:13-90` (paths inferred from grep; exact string literals not individually re-verified — see Unresolved Questions)
Confidence: probable
Capability: authorize / transform

### 16. queue_if_low_balance is a first-class per-payout flag driving deferred processing

Claim: `QueueIFLowBalance`/`QueueIfLowBalanceFlag` is carried on the payout-create request DTO and payout-details entity, consulted in `core.go` (multiple call sites) and via `IProcessorFactory.HandleIfQueueIfLowBalanceTrue`, and factors into the async-failure-handling retry path (`AsyncProcessingLedgerInitiatedEventFailure`). Cron endpoint `POST /v1/cron/...` `ProcessInitiateForQueuedPayouts` (via `controllers.PayoutService.ProcessInitiateForQueuedPayouts`) periodically re-attempts queued payouts.
Repo/service: payouts
File: `internal/app/payouts/core.go:525,3348,3498,3665`; `internal/app/payouts/processorFactory.go:47`; `internal/app/payouts/asyncFailureHandlingHelper.go:62-65`; `internal/routing/router/cron_routes.go` (ProcessInitiateForQueuedPayouts)
Confidence: probable
Capability: transform / route

### 17. Scheduled & bulk/batch payouts have dedicated cron + route surfaces

Claim: `POST /v1/cron/...InitiateScheduledPayouts` and `POST /v1/cron/...InitiateBatchSubmittedPayouts` (both `controllers.PayoutService.*`) drive time-based transitions out of `StateScheduled`/`StateBatchSubmitted`. Bulk payouts have their own route group `/v1/payouts/bulk` (`payout_bulk_routes.go`, BasicAuth(cred.API) + Passport `proxy` auth) hitting `controllers.PayoutService.PayoutsBulkValidate`, backed by `internal/app/bulkPayoutsProcessor/` and `internal/app/payoutsBulk/`.
Repo/service: payouts
File: `internal/routing/router/cron_routes.go:45-56,87-92`; `internal/routing/router/payout_bulk_routes.go:13-32`
Confidence: confirmed
Capability: route / transform

### 18. Payout cancellation and reversal — explicit endpoints and state-machine events

Claim: `POST /v1/payouts/cancel_payout/:payout_id` (`controllers.PayoutService.CancelPayout`) drives the `EventCancelled` transition (from `queued`/`scheduled`/`on_hold` only — cannot cancel an already-initiated/processed payout). Reversal is driven by `EventReversed`, which — unusually — is legal from almost every non-terminal AND even from `processed`/`failed` (i.e. reversal can happen post-facto after money movement failure/success), enforced in `internal/app/reversals/`.
Repo/service: payouts
File: `internal/routing/router/payout_routes.go:69-77`; `internal/app/payouts/state_machine.go:251-256` (EventReversed .From list)
Confidence: confirmed
Capability: reverse / route

### 19. Persistence: MySQL as system-of-record, TiDB as a secondary/analytical store accessed via internal WDA client

Claim: Primary datastore is MySQL 8.0 (`config/default.toml [db.master]/[db.replica]`, dialect `mysql`, goose migrations under `internal/database/migrations/`). A separate `internal/app/tidb/` package accesses TiDB tables (`payouts`, `payouts_status_details`, `reversals`, `payout_sources`) through a Razorpay-internal `wda` (write-data-access?) client (`github.com/razorpay/goutils/wda/wda-client`), used for query patterns like fuzzy/non-fuzzy search and dashboard fetch (see `internal/config/config.go:479-530` — `StorageType`/`OrchestratorConfig`/`IteratorConfig` with `FallbackStorages`).
Repo/service: payouts
File: `internal/app/tidb/core.go:1-40`; `internal/config/config.go:12` (`[db]` struct), `config/default.toml:11-45`
Confidence: confirmed
Capability: persist / observe

### 20. Direct read connection to the API monolith's own database (`db.api`)

Claim: Config defines a fourth DB connection, `[db.api]`, pointed (via env vars `PAYOUTS_DB_API_URL`/`PAYOUTS_DB_API_NAME`/`PAYOUTS_DB_API_USERNAME`/`PAYOUTS_DB_API_PASSWORD`) at what is almost certainly the API monolith's MySQL database — small pool (5 max, 2 idle conns) suggesting a low-volume, opportunistic-read use, not a hot path. Confirmed by `StorageType.ApiDB = "api_db"` constant and a currently-hardcoded-off gate `helpers.IsApiDbEnabledForPayouts` guarding `Core.FetchAllPayoutsFromApiDb` — i.e. code exists to read payouts directly from the API monolith's DB but is disabled today.
Repo/service: payouts
File: `config/default.toml:35-45`; `internal/config/config.go:482-487`; `internal/app/payouts/core_fetch_apidb_gate_test.go:17-21,46`
Confidence: confirmed
Capability: observe / route

### 21. Dual-write reconciliation between PS and API monolith DB (both directions)

Claim: Two named mechanisms exist for keeping PS's own payout data and the API monolith's payout data consistent, both feature-flagged and both queue-driven (not synchronous dual-write):
- **Forward dual write** (`internal/app/payouts/forward_dual_write.go`): gated by `Features.ForwardDualWrite`; on trigger, pushes a `{action:"reverse_dual_write", entity_id, entity_type:"ps_payout"}` JSON payload onto a queue (`Job.DataConsistencyChecker`) for asynchronous reconciliation.
- **Reverse dual write** (`internal/app/reverseDualWrite/`): a full sync-engine (`pkg/dataSync/engine`) with config-driven handlers, ES syncer adapter, and its own cron trigger `POST /v1/cron/...ReverseDualWrite` (`controllers.ReverseDualWrite.ReverseDualWrite`) and `PayoutsDualWriteFailureProcessing` cron endpoint.
Also `Features.SkipCADualWrite` config flag exists to skip current-account-specific dual write.
Repo/service: payouts
File: `internal/app/payouts/forward_dual_write.go:24-48`; `internal/app/reverseDualWrite/core.go:1-50`; `internal/routing/router/cron_routes.go:93-102`; `internal/config/config.go:470-477` (`Features` struct)
Confidence: confirmed
Capability: persist / transform

### 22. Auth: Passport JWT is the primary identity vehicle; merchant/actor context flows via request context, not headers, post-auth

Claim: `PassportAuthentication` middleware requires header `X-Passport-JWT-V1`, verifies+parses it via `provider.GetPassportHandler`, stores the parsed passport object in request context (`appConstants.ContextKeyPassport`), extracts `authType`/`authenticated`/`identified`/`mode`/`roles`/`consumer`/`impersonation` claims for logging, and rejects (401) if the resolved legacy auth type isn't in the route's `supportedAuths` allowlist (e.g. `private`/`proxy`/`privilege`/`admin` depending on route group).
Repo/service: payouts
File: `internal/routing/middleware/passport.go:23-81,157-173`
Confidence: confirmed
Capability: authorize

### 23. Internal service-to-service allowlist (app-name based, not header-trust)

Claim: A fixed allowlist of internal caller "app names" is defined for privilege-auth callers: `X_PAYROLL`, `VENDOR_PAYMENTS`, `PAYOUT_LINKS`, `SCROOGE`, `SETTLEMENTS_SERVICE`, `CAPITAL_COLLECTIONS_CLIENT`, `ChargeCollectionsInternal`, `FTS`, `XPERIENCE`, `ACCOUNTS_RECEIVABLE`, `BUSINESS_REPORTING`, `CROSS_BORDER_IMPORT`, `CAPITAL_EARLY_SETTLEMENTS`, checked via `auth.IsAllowedInternalApp` for privilege-auth requests.
Repo/service: payouts
File: `internal/auth/headers.go:11-25,76-80`
Confidence: confirmed
Capability: authorize

### 24. GET requests routed to DB replica automatically

Claim: `middleware.DatabaseConnection()` inspects HTTP method; for GET, stamps request context with `db.Replica` connection selector; all other methods use default (master) connection. Applied on nearly every route group.
Repo/service: payouts
File: `internal/routing/middleware/database.go:12-23`
Confidence: confirmed
Capability: route / persist

### 25. Admin routes require `admin` legacy auth type

Claim: `/v1/admin/*` (get-entity-by-id, get-entity-multiple, free-payout attribute update/get, `PayoutDetailsSync`) require `BasicAuth(cred.API)` + `PassportAuthentication([LegacyAuthTypeAdmin])`.
Repo/service: payouts
File: `internal/routing/router/payout_admin_routes.go:14-49`
Confidence: confirmed
Capability: authorize / route

### 26. Cron routes use a distinct FastCron service credential, not the general API credential

Claim: `/v1/cron/*` group authenticates with `middleware.BasicAuth(cred.FastCron)` only (no passport requirement) — these are trusted-scheduler-only endpoints (Redis key set, banking-statement fetch trigger, queued-payout processing, batch/scheduled payout initiation, on-hold dispatch, in-flight reservation reconciliation, dual-write failure reprocessing, SLA breach checks, load test runner, merchant-configuration CRUD).
Repo/service: payouts
File: `internal/routing/router/cron_routes.go:13-128`
Confidence: confirmed
Capability: authorize / route

---

## Route table (method, path, handler, auth, notes)

| Method | Path | Handler | Auth | Notes |
|---|---|---|---|---|
| GET | `/v1/payouts/:id` | `PayoutService.GetPayout` | BasicAuth(API,Workflow) + Passport(private/proxy/privilege) | Request/response payload crypto |
| POST | `/v1/payouts` | `PayoutService.PostFundAccountPayout` (via `IdempotencyKey`) | same | Payload crypto; idempotency-key wrapped |
| POST | `/v1/payouts/internal_contact_payout` | `PayoutService.PostFundAccountPayout` (via `IdempotencyKey`) | same | |
| POST | `/v1/payouts/payouts_internal` | `PayoutService.PostFundAccountPayout` (via `IdempotencyKey`) | same | |
| GET | `/v1/payouts` | `PayoutService.GetPayouts` | same | list/fetch-multiple |
| POST | `/v1/payouts/cancel_payout/:payout_id` | `PayoutService.CancelPayout` | same | drives `EventCancelled` |
| GET | `/v1/payouts/free_payout/:balance_id` | `PayoutService.GetFreePayoutAttributes` | same | |
| GET | `/v1/payouts/payouts_status_reason_map` | `PayoutService.GetPayoutStatusReasonMapping` | same | |
| GET | `/v1/payouts/_meta/summary` | `PayoutService.GetDashboardSummary` | same | |
| POST | `/v1/payouts/translate_account_number_to_balance_id` | `PayoutService.TranslateAccountNumberToBalanceId` | same | |
| GET | `/v1/payouts/schedule/timeslots` | `PayoutService.GetTimeSlotsForScheduledPayouts` | `ServiceBasicAuthOrPassport(API,Workflow)` + Passport | **Shadow-gateway pilot route** — proxy-cutover onboarded in devstack |
| PATCH | `/v1/payouts/:payout_id/attachments` | `PayoutService.UpdateAttachmentsForPayout` | BasicAuth(API) + Passport(proxy) | "routes that get jwt token in their header" |
| POST | `/v1/payouts/bulk/...` | `PayoutService.PayoutsBulkValidate` | BasicAuth(API) + Passport(proxy) | bulk/batch payouts |
| POST | `/v1/admin/...` (get_entity, get_entity_multiple, free_payout update/get, payout_details_sync) | `AdminClient.*` | BasicAuth(API) + Passport(admin) | admin/ops tooling |
| POST/PATCH | `/v1/payouts_internal/...` (manual_action, fts status/details update, credit-transfer update, InitiateScheduledPayouts, balance-change event, RetryPayouts, RetryPayoutSourceUpdate, ApprovePayout, RejectPayout, GetPayoutAnalytics, bene-bank status update, ProcessDispatchForOnHoldPayouts, ...) | `AdminClient.ManualAction` / `PayoutService.*` | BasicAuth(API,Workflow,Xperience,FTS,VendorPayments,Settlements,Irctc) | internal/inter-service surface; workflow approve/reject here |
| POST | `/v1/workflow/state` | `WorkflowService.CreateWorkflowStateMap` | BasicAuth(Workflow,API) | maker-checker state map |
| PATCH | `/v1/workflow/state/:id` | `WorkflowService.UpdateWorkflowStateMap` | BasicAuth(Workflow,API) | |
| POST | `/v1/notify/...` | `HealthNotification.HealthStatusUpdate`, `DowntimeV2Notification.NotifyDowntime` | BasicAuth(FTS) | |
| POST | `/v1/payloadcrypt/encrypt`,`/decrypt` | `PayloadCrypt.Encrypt/Decrypt` | BasicAuth(API) | |
| POST | `/v1/cron/...` (redis_key_set, banking-statement fetch, ProcessQueuedPayouts, InitiateBatchSubmittedPayouts, ProcessDispatchForOnHoldPayouts, ProcessInitiateForQueuedPayouts, ProcessReconciliationForInFlightReservations, InitiateScheduledPayouts, PayoutsDualWriteFailureProcessing, ReverseDualWrite, RunLoadTest, MerchantConfiguration create/update, CheckSLABreaches) | multiple controllers | BasicAuth(FastCron) | scheduler-only |
| GET | `/v1/fund_accounts/validations` | `FundAccountValidationService.PostFundAccountValidation`/`GetFav` | BasicAuth(API,Workflow) + Passport(private) | FAV subsystem, own state machine (`internal/app/fundAccountValidation/state_machine.go`) |
| POST/PATCH | `/v1/merchant/...` | `MerchantService.UpdateMerchantSlas`/`UpdateMerchantFeatureInCache` | BasicAuth(API) | |
| GET | `/`, `/health` (exact paths not re-verified) | `Health.GetCommitHash`, `Health.GetHealthStatus` | none (public) | k8s-style health probes |
| ANY | shadow-gateway-onboarded routes | proxied to `monolith_base_url` (`https://prod-api-int.razorpay.com` in prod) | N/A (delegates to monolith's own auth) | See Findings #2-6 |

(Full literal path strings for `payout_internal_routes.go` and `cron_routes.go` were read via handler-name grep, not every literal path string individually re-verified — flagged in Unresolved Questions.)

---

## State machine table

Initial state: `create_request_submitted` (`appConstants.StateCreateRequestSubmitted`).

| Event | To | Valid From | Enforcing symbol |
|---|---|---|---|
| `created` | `created` | create_request_submitted, pending, scheduled, queued, on_hold, ledger_response_awaited, batch_submitted | `state_machine.go:128` |
| `ledger_response_awaited` | `ledger_response_awaited` | create_request_submitted, pending, scheduled, queued, on_hold, batch_submitted | `state_machine.go:153` |
| `initiated` | `initiated` | created, initiated (self-loop allowed for FTS async retries) | `state_machine.go:164` |
| `failed` | `failed` | create_request_submitted, created, pending, scheduled, queued, on_hold, ledger_response_awaited, batch_submitted, initiated | `state_machine.go:189` |
| `processed` | `processed` | initiated | `state_machine.go:226` |
| `reversed` | `reversed` | create_request_submitted, ledger_response_awaited, pending, on_hold, queued, scheduled, processed, failed, initiated, created, batch_submitted | `state_machine.go:251` |
| `pending` | `pending` | create_request_submitted | `state_machine.go:280` |
| `schedule` | `scheduled` | create_request_submitted, pending | `state_machine.go:305` |
| `batch_summit` (sic) | `batch_submitted` | create_request_submitted, pending, scheduled | `state_machine.go:330` |
| `queued` | `queued` | create_request_submitted, ledger_response_awaited, pending, scheduled, on_hold | `state_machine.go:355` |
| `cancelled` | `cancelled` | queued, scheduled, on_hold | `state_machine.go:380` |
| `on_hold` | `on_hold` | create_request_submitted, pending, batch_submitted, scheduled | `state_machine.go:407` |
| `rejected` | `rejected` | pending | `state_machine.go:432` |

Terminal states (no further transitions modeled here): `processed`, `reversed`, `failed`, `cancelled`, `rejected` (though `reversed` is itself reachable *from* `processed`/`failed`, making reversal a post-terminal correction, not a true DAG leaf).

Public status collapse: `internal/app/common/appConstants/status.go:3-19` (`InternalToPublicStatusMap`).

---

## Dependencies discovered

| Dependency | Nature | Client file |
|---|---|---|
| API monolith (razorpay/api) | Proxy target (shadow gateway) + direct DB reads + dual-write reconciliation target | `internal/routing/shadowgateway/monolith_client.go`; `internal/app/payouts/fetch_payouts_details_from_api.go`; `internal/app/payouts/forward_dual_write.go`; `internal/app/reverseDualWrite/`; `config/prod.toml` `monolith_base_url = "https://prod-api-int.razorpay.com"`; `[db.api]` in `config/default.toml` |
| Splitz (experimentation) | Route mode resolution, workflow feature gating, idempotency ramp-up | `internal/routing/shadowgateway/mode.go`; `internal/routing/middleware/splitz.go`; `provider.GetSplitzClient` |
| Passport SDK (`goutils/passport/v3`) | JWT auth, S2S identity | `internal/auth/passport.go`, `internal/routing/middleware/passport.go` |
| MySQL (own DB `payouts`) | System of record | `config/default.toml [db.master]/[db.replica]` |
| MySQL (API monolith DB, `db.api`) | Direct cross-service DB read (currently gated off) | `config/default.toml [db.api]`; `internal/app/payouts/core_fetch_apidb_gate_test.go` |
| TiDB | Secondary store, accessed via internal WDA client | `internal/app/tidb/core.go` |
| Redis | Idempotency mutex, log-sampler rates, caching | `internal/routing/middleware/idempotency_key.go` (mutex), `internal/boot/handler.go:178` (`loadLogSamplerRatesFromRedis`) |
| Kafka | Consumer process; queue names via `config.Job.*` (e.g. `DataConsistencyChecker` queue for forward dual write) | `cmd/kafkaConsumers/main.go`, `internal/boot/boot_kafka_consumer.go` |
| Elasticsearch | Payout document search/sync (`internal/app/reverseDualWrite/es_syncer_adapter.go`, `internal/app/payouts/backfill_payouts_es_doc.go`) | `internal/config/config.go` `ESSyncConfig` |
| FTS (Fund Transfer Service) | Inbound status/details webhooks, allowlisted internal app | `internal/routing/router/payout_internal_routes.go` (`HandlePayoutStatusUpdateViaFTS`), `internal/auth/headers.go` |
| Ledger service | Payout debit/credit reservation, `ledger_response_awaited` state | `internal/app/reversals/reversalViaLedgerService.go`, `internal/app/fundAccountValidation/fav_ledger.go` |
| DCS (Dynamic Config Service?) | Config client | `internal/config/config.go:66` `DCSClientConfig dcs.Config` |
| HVault | Secrets | `internal/config/config.go:83` `HVault hvault.Config` |
| XAS, CFA, XBalances, UPS, Account Service, Raven, CCSDK | Various internal HTTP clients | `internal/config/config.go:65-84` |

---

## Feature flags / config keys

| Key (mapstructure) | Effect | File |
|---|---|---|
| `shadow_gateway.enabled` | Master kill switch for proxy-cutover framework (false in prod/default, true in devstack) | `config/prod.toml:864`, `config/devstack.toml:713` |
| `shadow_gateway.routes."<method> <path>".experiment_id` | Per-route Splitz experiment driving native/proxy/shadow_api/shadow_ps | `config/devstack.toml:719-720` |
| `shadow_gateway.monolith_base_url` | API monolith base URL for proxy/shadow legs | `config/prod.toml:867` (`prod-api-int.razorpay.com`) |
| `features.ikey_auto_enforcement` (`Features.IKeyAutoEnforcement`) | Gates hard idempotency-key enforcement | `internal/config/config.go:471` |
| `splitz_experiment_list.ikey_auto_enforcement_ramp_up` (inferred field name `IKeyAutoEnforcementRampUp`) | Ramp-up experiment for idempotency enforcement | `internal/routing/middleware/idempotency_key.go:107` |
| `features.reverse_dual_write` | Enables reverse dual-write sync engine | `internal/config/config.go:472` |
| `features.forward_dual_write` | Enables forward dual-write queue push | `internal/config/config.go:473`, `internal/app/payouts/forward_dual_write.go:26-28` |
| `features.skip_ca_dual_write` | Skip current-account dual write | `internal/config/config.go:474` |
| `features.source_updater_sns_enabled` | Toggle SNS source-updater path | `internal/config/config.go:475` |
| `features.db_route_label_enabled` | DB routing label toggle | `internal/config/config.go:476` |
| `PayoutWorkflows` merchant feature (dual key name via `IsPayoutWorkflowsDcsNameExperimentEnabled` Splitz gate) | Enables maker-checker workflow for merchant | `internal/app/payouts/processor/baseHelper.go:124-133` |
| `helpers.IsApiDbEnabledForPayouts` | Gates direct read from API monolith DB (hardcoded off per test comment) | `internal/app/payouts/core_fetch_apidb_gate_test.go:18` |
| `db.api.*` (env `PAYOUTS_DB_API_URL/NAME/USERNAME/PASSWORD`) | Cross-service DB connection to API monolith DB | `config/default.toml:35-45` |
| `orchestrator.*` / `StorageType` (`app_db`,`api_db`,`tidb`,`elasticsearch`) + `FallbackStorages` | Multi-store fetch orchestration/fallback | `internal/config/config.go:479-530` |

---

## Unresolved questions

1. Exact literal path strings for every endpoint in `payout_internal_routes.go` and `cron_routes.go` were inferred from handler-name context via grep, not fully re-read line-by-line — recommend a follow-up direct read of those two files if exact URL strings are needed for e.g. an API contract diff.
2. Whether `ModeShadowHonorAPI`/`ModeShadowHonorPS` have ever been exercised outside devstack (prod config currently only defines the kill switch + monolith URL, no `[shadow_gateway.routes.*]` entries were found under `config/prod.toml` in the excerpt read — worth confirming prod has zero onboarded routes today).
3. The full downstream effect of `db.api` reads (`FetchAllPayoutsFromApiDb`) — is this a fallback-on-miss pattern, a migration-verification pattern, or dead/parked code? The test name ("FlagOff_SkipsApiDb... currently hardcoded to always return false") suggests it's scaffolded but inactive; not verified against a broader search for any config-driven activation path other than the hardcoded helper.
4. `internal/routing/shadowgateway/monolith_client.go` (the actual HTTP proxy implementation, timeouts, header stripping/forwarding rules) was located but not read in detail — relevant if a caller wants transport-level specifics (e.g., which headers are stripped/forwarded to the monolith, response streaming behavior).
5. `SplitzExperimentList` struct has ~15+ fields (`ForDuplicatePayoutEvaluate`, `ForPayoutPropertiesEventEvaluate`, `PartnerBankOnHoldPayoutsExperiment`, `ForDualWriteDirectPushToAPI`, `ForChargeCollectionEvaluate`, `MerchantConfigurationExperiment`, `ForProcessingFilterEvaluate`, plus more not captured in the read window) — only partially enumerated; a full flag inventory would need `internal/config/config.go` lines ~396-470 read completely.
6. Twirp/gRPC: confirmed codegen exists but no server registration found in `internal/boot/*` in the areas read; did not exhaustively grep every file for `twirp.New*Server` — treat "no active Twirp server" as probable, not confirmed.
