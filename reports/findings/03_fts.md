# FTS (Funds Transfer Service) — Architecture Findings

Repo: `razorpay/fts` (Funds Transfer Service), default branch `master`, commit `2a09e763116db47a2f28553c677ad73ef8133ebf`.
Lane: FTS — fund transfer execution, bank/provider integration, status propagation.
Analysis mode: read-only, static analysis (grep/read only, no code executed).

---

## 1. Repo Summary

- **Type**: Go monolith-per-domain service ("single point of interaction with Nodal Accounts" — README.md:2).
- **Stack**: Go 1.24 (go.mod), Gin (`gin-gonic/gin`) HTTP framework, GORM (`jinzhu/gorm`, MySQL driver `go-sql-driver/mysql`), Redis (`go-redis/redis`) as machinery broker, `RichardKnop/machinery v1.9.1` as the async task-queue framework, `Shopify/sarama` for Kafka, `go-co-op/gocron` for in-process recurring cron, AWS SDK, Sentry, Jaeger.
- **Deployable processes** (from `scripts/entrypoint.sh` + `deployments/*`):
  - `web` — Gin HTTP API server (`cmd/web/main.go` → `bootstrap.InitializeRouter`). K8s `deployments/{prod,stage,func,perf}/deployment-web.yaml`, container arg `["web"]`.
  - worker — `cmd/worker/main.go` → `bootstrap.InitializeWorker`, invoked with a `--command` flag (machinery consumer name). K8s `deployments/*/deployment-default-worker.yaml`, container arg `["default-workers"]`. `scripts/init.prod.sh:167 start_worker()` → `start_fts_worker $1`.
  - `migration` — `cmd/migration/main.go`, run via entrypoint arg `run_migrations`.
  - `mock-server` — `cmd/mock-server/main.go`, a local bank/Mozart simulator (not deployed to prod; test-only).
  - Recurring cron jobs run **inside the worker/web process** via `internal/cron` + `go-co-op/gocron` + leader-election (`internal/leaderelection`), gated by `os.Getenv("INSTANCE_TYPE") == "canary"` for some jobs (e.g. stuck-payouts). No k8s `CronJob` manifests found — scheduling is in-process, not k8s-native.
- **Docker/build**: `build/Dockerfile` (prod multi-stage, `ENTRYPOINT ["/usr/bin/dumb-init","--single-child","/scripts/entrypoint.sh"]`), `build/Dockerfile.slit`, `build/Dockerfile.cov`, dev Dockerfiles under `build/dev/`.
- **CI**: `.github/workflows/` present (not deep-dived); legacy `.deprecated_drone.yml` also present.
- **Config**: single large per-env TOML files under `config/env.*.toml` (no separate bank-config repo found in this checkout) — see §10.

---

## 2. Inbound API (route table)

Source: `internal/routing/router/route_list.go`, auth via `internal/routing/middleware/auth.go` (`BasicAuth`), route registration `internal/bootstrap/bootstrap.go`.

**Auth mechanism**: HTTP **Basic Auth** only (no JWT/mTLS at the Gin layer). `middleware.BasicAuth(routeApps...)` reads `ctx.Request.BasicAuth()`, looks up per-caller-app credentials from config (`[users.*]` in TOML, values sourced from env vars), and does a constant-time `secureCompare` (auth.go:31-32). 401 responses include `WWW-Authenticate: Basic`.

**Caller identity constants** (`internal/constants/authusers.go`):
| Const | Value | Meaning |
|---|---|---|
| APIAuth | "API" | API monolith |
| PayoutsService | "PS" | Payouts Service |
| WalletService | "WALLET" | Wallet service |
| SettlementAuth | "SETTLEMENT" | Settlements service |
| ScroogeAuth | "SCROOGE" | Scrooge (recon/settlement engine) |
| ARTAuth | "ART" | recon service |
| ValidxAuth | "VALIDX" | Validx |
| AlertAuth | "ALERT" | Alert manager caller |
| CapitalCardsAuth | "CAPITALCARDS" | Capital Cards |
| XperienceAuth | "XPERIENCE" | Xperience |

Corresponding credentials block: `config/env.default.toml:5505` `[users]` (`users.api`, `users.ps`, `users.wallet`, `users.settlement`, `users.scrooge`, `users.art`, `users.capitalcards`, `users.validx`, `users.xperinece`) — each `USERNAME`/`PASSWORD` sourced from env vars (e.g. `USERS_PS_USERNAME`), confirming **both the API monolith and Payouts Service call FTS directly**, with distinct credentials.

| Method | Path | Controller.Handler | Auth apps allowed | Notes |
|---|---|---|---|---|
| GET | `/` | `App.Get` | none | health/root |
| POST/GET | `/v1/holiday`, `/v1/holiday/query` | `Holiday.*` | API | |
| POST/GET | `/v1/account` | `FundAccount.Post/Get` | API, SETTLEMENT, CAPITALCARDS | fund-account registration |
| GET | `/v1/account/status` | `AccountRegister.Get` | API, SETTLEMENT, CAPITALCARDS | |
| POST/PUT | `/v1/account/register`, `/reconcile` | `AccountRegister.*` | API, SETTLEMENT | |
| GET/POST/DELETE | `/v1/source_account*` | `SourceAccount.*` | API, CAPITALCARDS, SETTLEMENT | source (nodal/current) account CRUD, balance fetch |
| POST | `/v1/source_account/copy` | `SourceAccount.PostSourceAccountCopy` | API | |
| PATCH | `/v1/source_accounts/:action` | `SourceAccount.Patch` | API | |
| POST | `/v1/direct_source_accounts` | `SourceAccount.PostDirectSourceAccounts` | API | |
| GET/POST/PATCH/DELETE | `/v1/source_account_mappings*` | `SourceAccountMapping.*` | API, SCROOGE | |
| POST/DELETE/GET | `/v1/direct_account_routing_rules` | `DirectAccountRoutingRule.*` | API | |
| **POST** | **`/v1/transfer`** | `Transfer.Post` | **API, SETTLEMENT, VALIDX, PS (PayoutsService), WALLET** | **transfer initiate** |
| PUT | `/v1/transfer` | `Transfer.Put` | same | no-op stub (returns 200, nothing done) |
| GET | `/v1/transfer` | `Transfer.Get` | same | transfer status/list query |
| GET | `/v1/transfer/status` | `TransferAction.Get` | same | attempt list |
| POST | `/v1/transfer/:transfer_id/:action` | `TransferAction.Post` | same | actions: retry, publish, check(via internal action), etc. |
| PATCH | `/v1/transfer/fail` | `TransferAction.Patch` | same | force-fail a transfer (ops/manual) |
| POST/GET/PATCH | `/v1/transfers/:action` | `TransferBulkAction.*` | API, SETTLEMENT, VALIDX | bulk transfer actions |
| PATCH/POST/PUT | `v1/attempts/:action`, `/reconcile` | `AttemptBulkAction.*` | API, ART | recon service touches attempts here |
| POST/PATCH/DELETE | `v1/channel_health_events/*` | `HealthController.*` | API, ALERT | channel/bank health event ingestion |
| GET | `v1/modes/supported` | `FundAccount.GetSupportedModes` | SCROOGE | |
| GET/POST/DELETE | `/v1/preferred_routing_weights` | `PreferredRoutingWeight.*` | API | |
| GET/POST/DELETE | `/v1/account_type_mappings` | `AccountTypeMapping.*` | API | |
| GET | `/v1/cron_jobs_dashboard/status_with_entity` | `CronJobs.GetStatusWithEntity` | API | |
| PATCH | `/v1/manual_queries/update` | `ManualQueries.Patch` | API | ops manual DB updates |
| GET/POST/PATCH | `/v1/routing/*` | `Routing.*` | **API, PS** | routing/mode-selection endpoints callable by Payouts Service directly |
| POST/GET/PATCH/DELETE | `/v1/merchant_configurations` | `MerchantConfiguration.*` | API | |
| POST | `/v1/alert_manager` | `AlertManagerController.HandleAlert` | API, ALERT | |
| GET/POST/PATCH | `/v1/key_value_store*` | `KeyValueStoreController.*` | API | |
| POST | `/v1/otp/send` | `OTPRequestController.Post` | API | |
| PATCH | `/v1/one_off_db_migrate` | `DbMigration.OneOffDbMigrate` | API | |
| GET | `/v1/downtimes`, `/:id` | `DowntimeV2Controller.*` | XPERIENCE | |

**Idempotency** (`internal/transfer/service.go:322` `CreateNewTransfer`): dedupe key = `(source_id, source_type)`. Guarded by a distributed mutex `mutex.Provider.AcquireAndRelease(ctx, "fund_transfer_create_resource_%s_%s", ...)`. If a transfer already exists, `Post` returns HTTP 200 (not 201) with the existing transfer's id/status instead of creating a duplicate (`controllers/transfer.go:150-152`). Special-case: if the existing transfer is `USER_PENDING` and the retry payload carries an OTP, it re-triggers processing (`HandleUserActionForTransfer`).

**Confidence**: High (route table, middleware and dedupe logic read directly).

---

## 3. Transfer / Attempt State Machine

Source: `internal/transfer/state_machine.go` (uses generic `pkg/transition` FSM engine).

**States**: `NEW(StateLess)` → `CREATED` → `INITIATED` → `PROCESSED` → `REVERSED`; also `FAILED`, `RETRY`, `USER_PENDING`, `FAILED_INTERNAL` (attempt-only).

### Transfer transitions
| From | Event | To |
|---|---|---|
| NEW | event_created | CREATED |
| CREATED, RETRY, USER_PENDING | event_initiated | INITIATED |
| INITIATED | event_processed | PROCESSED |
| INITIATED, CREATED, RETRY, USER_PENDING | event_failed | FAILED |
| PROCESSED | event_reversed | REVERSED |
| FAILED, INITIATED | event_retry | RETRY |
| INITIATED | event_user_pending | USER_PENDING |

### Attempt transitions
| From | Event | To |
|---|---|---|
| NEW | event_created | CREATED |
| CREATED, RETRY | event_initiated | INITIATED |
| INITIATED | event_processed | PROCESSED |
| INITIATED, CREATED | event_failed | FAILED |
| PROCESSED | event_reversed | REVERSED |
| FAILED | event_retry | RETRY |
| CREATED | event_failed_internal | FAILED_INTERNAL |

`getEventToTrigger()` (state_machine.go:136) maps a Mozart response's boolean meta flags (`AttributeMozartStatusProcessed/Failed/Reversed`, `AttributeMetaStatusRetry`, `AttributeMetaStatusUserPending`, `AttributeMetaStatusFailedInternal`) directly to FSM events — i.e. the bank/Mozart response *is* the state-machine input.

**"Ambiguous"/indeterminate handling** (no literal "ambiguous" term used in code — modeled as UTR-presence gating and duplicate-txn skip logic):
- `attempt_processor.go:2924` `skipAttemptUpdateForIndeterminateResponses`: if an attempt has been re-initiated ≥ `MaxReInitiateAttemptCount` times and the bank's transfer-init status code was `mozart.StatusDuplicateTxn` while the current check says "failed", the update is **skipped** (attempt is *not* failed) — explicit comment: "to ensure even if there is a slight possibility of attempt going into processed state later on, merchant doesn't retry the payout which can lead to money loss."
- UTR-presence gates around specific bank error codes keep the attempt in a non-terminal ("pending") state instead of marking it failed when a UTR is already present, e.g. `isCBS188WithUTR`, `isProxy401WithUTR`, `isCBS7161WithUTR` (attempt_processor.go:6465-6516) vs. their `WithoutUTR` counterparts which do mark failed (e.g. `isNSE404WithoutUTR`).
- `status_check_policies.go` defines named policies (`pending_error_code_policy`, `er028_no_utr_policy`) used to decide which config-driven Mozart steps to run for a status re-check depending on the last bank error code.

**Duplicate response / re-init handling**: `transfer_processor.go:610` — `FailTransfer(FtsDuplicateGatewayRefNo, nil)` explicitly fails a transfer if a duplicate `gateway_ref_no` is detected from the bank.

**Reversal handling**: `REVERSED` is reachable only from `PROCESSED` (funds returned after success — e.g. NEFT/RTGS return). Driven by Mozart status-check meta flag `reversed=true`; also surfaced through manual-update handlers (`manual_update_handlers_factory.go:85` — ops can force a transfer to REVERSED).

**UTR capture**: stored on `Attempt.Utr` / `Transfer.Utr` (+ `ReturnUtr` for reversed/returned funds) — set from Mozart response fields inside `attempt_processor.go` (large file, not fully enumerated). UTR presence is checked (`utils.IsEmpty(attempt.Utr)`) throughout ambiguous-status handling above.

**Confidence**: High (state machine table and code excerpts read directly).

---

## 4. Status Propagation to Payouts Service / API monolith — **directly relevant to today's incident**

Source: `internal/transfer/service.go:1071` `FireTransferStatusWebhook`, task wrapper `internal/tasks/fire_transfer_status_webhook.go`.

**Mechanism**: direct outbound synchronous **HTTP POST** (via `pkg/request`), **not** a callback FTS exposes — FTS is the caller, Payouts Service/API monolith are the callees. Optionally Kafka (see below). No SQS usage found.

**Destination selection logic** (service.go:1126-1208), in order:
1. If `AlternateURLFlow.EnabledMerchants` contains the merchant, override URL with `AlternateURLFlow.AlternateBaseURL` (used for high-volume/critical merchants routed to a separate API instance).
2. If splitz experiment `Splitz.Experiments.FireStatusUpdateKafka` is enabled for the merchant → **push to Kafka instead of HTTP** (topic `fire_transfer_status` producer, see §8) and **return early** — no HTTP call made at all in this branch.
3. Else, if splitz experiment `Splitz.Experiments.CreateTransferMetaRollout` is enabled for the merchant:
   - Look up `transfer_meta` row for the transfer (`GetTransferMetaForTransfer`) to read `origin_service`.
   - **If `origin_service == "payouts"`** (and PS webhook config non-empty) → call `config.PayoutsService.UpdateFtsFundTransfer` = `payouts_service.update_fts_fund_transfer` → prod URL `https://payouts.razorpay.com/v1/payouts/transfer_status_webhook` (config/env.prod-live.toml:269-273), Basic-Auth'd with `payouts_service.auth` (`PAYOUTS_SERVICE_AUTH_KEY/SECRET`).
   - **Else** → falls back to the legacy per-product `[webhook.<product>.transfer_status]` URL — for most products (`payout`, `payout_refund`, `refund`, `penny_testing`, `es_on_demand`, `ca_payout`, `customer_wallet`) this is `https://prod-api-int.razorpay.com/v1/update_fts_fund_transfer` (**API monolith**, not Payouts Service), Basic-Auth'd with the product's `productConfig.Auth`.
4. If `CreateTransferMetaRollout` is **disabled**, it *always* uses the legacy per-product `[webhook.*]` URL (API monolith), regardless of who actually created the transfer.

**`origin_service` provenance** (`internal/transfer/service.go:1889` `extraTransferMetaFromCtx`): set from an `X-Origin` request **header** captured into gin context at transfer-creation time (`constants.XOrigin`); **if the header is absent, it silently defaults to `"API"`** (service.go:1908-1910, `constants.APIAuth` = "API"). `transfer_meta.origin_service` is validated to be one of `"API"` or `"payouts"` (`transfer_meta.go:22-23`).

> **Incident-relevant observation**: a transfer created by Payouts Service that (a) is missing/loses its `X-Origin` header en route to FTS, or (b) is not covered by the `CreateTransferMetaRollout` splitz rollout, will have its status webhook sent to the **API monolith's** `update_fts_fund_transfer` endpoint instead of directly to Payouts Service's own `transfer_status_webhook` endpoint. If Payouts Service expects/relies on the direct webhook (rather than being updated transitively via the monolith), this is a plausible mechanism for "FTS says FAILED, Payouts Service still shows processing/initiated."

**Retry policy on webhook delivery failure** (service.go:1253-1268):
- Retries on any non-2xx response (`responseStatusCode/100 != 2`), **including 0** (connection failure).
- Max **3 attempts** (`WebhookRetryMaxCount = 3`, service.go:99), spaced **200 seconds apart** (`WebhookRetryInterval = 200`, comment: "the mutex lock at API<>PS integration is 180 seconds" — service.go:98), re-published via the same machinery task (`service.Queue.Publish(..., WebhookRetryInterval*time.Second, transferID, attemptCount+1)`).
- **After exhausting retries, FTS gives up**: only logs `TransferWebhookUpdateFailure` and increments a metric `TransferWebhookUpdateFailureCount(status, product)`. **No DLQ, no compensating persistence, no alert call is made in this code path** — the task returns `nil` (success) regardless of whether the webhook ultimately succeeded (`return nil` at service.go:1274, with an explicit comment that HTTP failures are intentionally not propagated as task errors).
- A hang-detection check logs/metrics if the HTTP call itself takes >3x its configured timeout (`metrics.WorkerTaskStuckCount`), useful for diagnosing a stuck worker but does not change outcome.
- `WebhookRetryMaxCount`×`WebhookRetryInterval` ≈ 10 minutes total retry window before FTS abandons updating the caller.

**Ordering guarantees**: none evident — each retry is an independent async task publish; no sequence number/version check against the caller. If two webhook tasks for the same transfer are in flight concurrently (e.g. a retry fires around the same time a newer status update is queued), whichever HTTP call lands last at the receiver wins; FTS does not enforce ordering.

**Trigger points for firing the webhook task** (i.e. what causes a status push): `transfer_processor.go:590,902,942`, `attempt_processor.go:2989,3224,5003`, `retry_preprocessor_service.go:499` — essentially on every transfer/attempt terminal-ish state transition (processed/failed/reversed/user_pending) and via `retry_preprocessor_service` when preprocessor-driven retries update state.

**Skip condition**: if the transfer is currently in `RETRY` state, the webhook fire is skipped entirely (service.go:1104-1111) — "webhook job skipped" log — meaning a transfer sitting in RETRY produces no outbound status update until it moves out of that state.

**Confidence**: High — read the full `FireTransferStatusWebhook` function body and the config for both env.default.toml and env.prod-live.toml.

---

## 5. Provider/Bank Routing

- `internal/routingv2` — `Service.GetSelectedModeByAction`, `GetSelectedRoute`, `GetPriorityIntervals` (exposed at `/v1/routing/mode_selection`, `/route_selection`, `/priority_route` — callable by Payouts Service directly per §2). Depends on `IAccountService`, `IModeSelectionFactory`, `IChannelService`, `IMultiAccountRoutingRuleService`.
- `internal/attemptrouting` — `account_type_mapping.go`, `preferred_routing_weight.go` — weight/preference-based source-account selection, config surfaced at `/v1/preferred_routing_weights`, `/v1/account_type_mappings`.
- `internal/multiaccountrouting` — "Multi Account Routing Rules", exposed at `/v1/routing/multi_account_routing_rules` (POST to fetch, PATCH to modify).
- `internal/channel` — bank/channel health tracking: `fail_fast_status.go`, `channel_information_status.go`, `trigger_status.go`, `schedule.go` (bank operating-hour schedules), `test_transactions.go` (canary/synthetic health-check transactions) — these feed into routing decisions (down/unhealthy channels get skipped). Exposed under `/v1/channel_health_events/*` and `/v1/routing/*_status_logs`, `/v1/routing/channel_health_stats`.
- `internal/congestion` — `queue_size.go`, `threshold.go` — per-mode congestion-based routing throttling (config section `[routing_feature_selector.congestion_calculator.UPI]`).
- `internal/downtime` / `internal/downtimeV2` — planned/detected bank downtime, notifications (`internal/downtimeV2/notification.go`, `publisher.go`), exposed at `/v1/downtimes`.
- `internal/product` — per-product config wrapper (`product.GetProductConfig`) used to resolve the webhook URL/auth for a given `Transfer.Product` (payout, refund, settlement, etc.).
- Source/nodal account selection: `internal/account/source_account_selector.go` (referenced from `attempt_processor.go:4037` comment: "duplicated at internal/account/source_account_selector.go:1050, please make changes there as well" — logic duplication flagged in-code).
- **No external "routing service" repo reference found** in this checkout — routing/config lives in-repo (DB tables `source_account_mappings`, `preferred_routing_weights`, `direct_account_routing_rules`, `multi_account_routing_rules`, `account_type_mappings` per migrations list, §9) plus the large `config/env.*.toml` per-bank/mode matrix (§10).

**Confidence**: Medium — package/file inventory and route wiring confirmed; internal selection algorithm not read line-by-line (large files, time-boxed).

---

## 6. Bank/Provider Adapters — **Mozart is a separate microservice, not in-process code**

Key finding: FTS does **not** talk to banks directly for the bulk of channels. It talks to an internal microservice called **Mozart** (`internal/providers/mozart/*`), which is a **config-driven generic HTTP client** — FTS assembles a `RequestData` (source account, attempt, merchant info) and calls one of 8 generic Mozart operations; Mozart itself resolves the bank-specific protocol on its side. A handful of banks/rails are integrated **directly in-repo** instead (`internal/integrations/*`).

### Mozart client (`internal/providers/mozart`)
- `provider.go`, `executor.go`, `builder.go`, `bulk_*.go`, `request.go`, `response.go`, `error_code.go` (818 lines of bank/vault/gateway error-code catalog), `validation.go`.
- Generic operations dispatched (`executor.go:17-42`): `transfer_init`, `transfer_status`, `gateway_auth`, `gateway_session`, `beneficiary_verify`, `beneficiary_register`, `fetch_account_balance`, `create_otp`.
- Base config (`config/env.default.toml:761-816` `[mozart]`):
  - `BASE_URL` — dev: `http://localhost:8085/fts`; stage: `https://beta-mozart.stage.razorpay.in/fts`; prod: `https://mozart.razorpay.com/fts`.
  - Auth: `[mozart.auth] KEY/SECRET` from env vars `MOZART_AUTH_KEY`/`MOZART_AUTH_SECRET` — applied as **HTTP Basic Auth** on the outbound request (`pkg/request/rest.go:345 req.SetBasicAuth(r.Auth().Key, r.Auth().Secret)`).
  - Per-operation config: URL path (e.g. `/transfer_init`), `TIMEOUT = 180` (seconds, uniform across all 8 ops in default config), `METHOD = "POST"`, `Content-Type: application/json`.
- Protocol: REST/JSON over HTTPS, POST only.
- Auth per-call from FTS→Mozart: static Basic Auth (service-level), **plus** a separate per-bank `gatewayAuth`/`gatewaySession` mechanism (`internal/gateway/auth.go`) that fetches and caches bank-session tokens (cache key = `channel + mozart_identifier + mode + operation`, TTL via `step.TokenTTL` minutes) — this is a second, bank-specific auth layer executed *through* Mozart, not FTS talking to the bank directly.

### Bank/mode adapter matrix (from `config/env.default.toml` `[integration.api.*]`, keyed by `MOZART_URLPART_GATEWAY`)
Distinct banks/providers configured (12): **amazon_pay, axis, citi, hdfc, icici, idfc, m2p, mcs, ocbc, rbl, slice, yesbank**.

| Bank/Provider | Versions × Modes configured | Sample `MOZART_URLPART_GATEWAY` |
|---|---|---|
| amazon_pay | v1: wallet_transfer | `amazonpay/v1` |
| axis | v1: balance, beneficiary, ift, imps, neft, rtgs, upi | `axis/v1` |
| citi | v1: ift, imps, neft, rtgs | `citi/v1` |
| hdfc | v2: balance, beneficiary, imps, neft, rtgs | `hdfc_escrow/v1`, `hdfc_escrow/v2` |
| icici | v1/v2/v3: balance, beneficiary, ift, imps, neft, rtgs, upi, otp | `icici_imps/v1`, `icici/v1`, `icici/v2`, `icici/v3` |
| idfc | v1: balance, ift, imps, neft, rtgs, upi | `idfc/v1`, `idfc_upi/v1` |
| m2p | v1: ct (card transfer) | `m2p/v1` |
| mcs | v1: ct | `mc_send/v1` |
| ocbc | v1: duitnow (Malaysia real-time payments) | (own section `integration.api.ocbc.v1.duitnow`) |
| rbl | v1–v5: balance, beneficiary, ift, imps, neft, rtgs, upi | `rbl/v1`…`rbl/v5` |
| slice | v1/v2: balance, imps, neft, rtgs, upi | (own section) |
| yesbank | v1–v3: balance, beneficiary, ift, imps, neft, rtgs, upi | `yesbank/v1`, `yesbank/v2`, `yesbank/v3`, `yesbank_upi/v1` |

Each `[integration.api.<bank>.<version>.<mode>]` block additionally carries (examples at `config/env.default.toml:857-897`): `AMOUNT_LOWER_THRESHOLD`/`AMOUNT_UPPER_THRESHOLD`, `TPS` (rate limit), `RETRY_SLA`, per-request-type `OPERATION` mapping (`transfer_init`/`transfer_status`), a `[...retry]` block with staged **exponential-ish backoff** (`[[...retry.backoff]] INTERVAL=…, COUNT=…` — e.g. amazon_pay: 60s×2, 10s×2, 30s×1, 60s×1, 900s×2, 3600s×1, 10800s×3, 43200s×3), per-bank-error-code `[...pool.transfer_retry.<ERROR_CODE>.retry]` overrides (e.g. `BANK_CBS_OFFLINE_FAILURE`, `FROZEN_ACCOUNT`, `INSUFFICIENT_FUND`, `INVALID_VPA`, `TXN_REJECTED`), and `[...congestion_threshold]`, `[...gateway_errors.*]` per-mode UPI gateway error mappings (e.g. `axis.v1.upi.gateway_errors.upi_111_NO_UTR`).

### Direct (non-Mozart) integrations (`internal/integrations/`)
| Provider | Path | Protocol notes |
|---|---|---|
| Axis (bulk/SFTP file flow) | `internal/integrations/axis/axis.go`, `constants.go` | Pushes files to a remote **SFTP** server (`axis.go:143` "push it to remote sftp provided by axis"); corp codes `RZPXSFTP` / `RAZORPAYSFTP` (constants.go:15,18) |
| ICICI OPGSP | `internal/integrations/icici_opgsp/` | separate module from the Mozart-routed ICICI IMPS/NEFT/RTGS flows (likely forex/OPGSP-specific) |
| JPMC transfer pricing | `internal/integrations/jpmc_transfer_pricing/jpmc_transfer_pricing.go` | Uses **Beam** (Razorpay internal file-relay service, `internal/providers/beam/beam.go`, base URL `https://beam.razorpay.com/push`) to move an uploaded file from S3 to **JPMC's SFTP server** (comment at line 185) |
| RBL multicurrency | `internal/integrations/rbl_multicurrency/` | separate module from the Mozart-routed RBL flows |

### Simulators/mocks
- `cmd/mock-server/main.go` — standalone HTTP server mimicking Mozart's per-bank endpoints for local dev/tests: routes `/fts/icici_imps/v1/{transfer_init,transfer_status}`, `/fts/rbl/v1/*`, `/fts/rbl/v3/{transfer_init,transfer_status}` (bulk), `/fts/yesbank/v1/{beneficiary_register,beneficiary_verify,transfer_init,transfer_status}`, `/fts/rbl/v1/{gateway_session,gateway_auth}`, balance-check endpoints for RBL v1/v2, ICICI, Yesbank, and ICICI v3 transfer init/status.
- `internal/controllers/mocks/mozart_httpmock.go` — httpmock-based Mozart stub for Go unit tests.
- `test/mock/` — additional test fixtures (not enumerated in depth).

**Confidence**: High for Mozart architecture and config matrix (read directly). Medium for direct integrations (grepped, not fully read line-by-line — large files).

---

## 7. Databases and Key Tables

**Engine**: MySQL (via GORM + `go-sql-driver/mysql`), Redis (machinery broker + cache + mutex + distributed lock, `internal/providers/queue/redis.go`, `internal/providers/mutex`, `internal/providers/cache`).

Migrations: `internal/migrations/*.go` (55 files, sequential numbered `NNNNN_<name>.go`, runner at `internal/migrations/runner.go`, executed via `cmd/migration/main.go` / entrypoint `run_migrations`).

| Table (from migration filenames / `TableName()`) | Model | Notes |
|---|---|---|
| `transfers` | `transfer.Transfer` (transfer.go:24) | core transfer row: amount, mode, channel, status, source_id/type, utr/return_utr, preferred_mode/channel/source_account_id, request_meta (JSON), retry flag |
| `attempts` | `transfer.Attempt` (attempt.go:13) | per-attempt row: status, utr, return_utr, gateway_ref_no, gateway_error_code(_init), bank_status_code, re_initiate_count, batch_id |
| `attempts_unique_keys` | `transfer.attempts_unique_keys.go` | dedupe/uniqueness support for attempts |
| `transfer_meta` | `transfer.Meta` (transfer_meta.go:9) | `origin_service` ("API"/"payouts"), `request_id`, `source_id` — **the field that decides where the status webhook goes**, see §4 |
| `bank_accounts`, `vpas`, `fund_accounts`, `non_saved_cards`, `cards`, `wallets`, `remitter_accounts` | `internal/account` | fund-account (beneficiary) entities by type |
| `beneficiary_status`, `bank_account_meta` | `internal/account` | beneficiary registration/verification status with bank |
| `source_accounts`, `source_account_mappings`, `direct_account_routing_rules`, `preferred_routing_weights`, `multi_account_routing_rules`, `account_type_mappings` | routing | nodal/current account inventory + routing rule tables |
| `channel_health_events`, `channel_information_status`, `channel_information_status_logs`, `channel_metrics`, `bene_health`, `fail_fast_status`, `fail_fast_status_logs`, `trigger_status`, `trigger_status_logs` | `internal/channel` | bank/channel health & fail-fast state, feeding routing |
| `downtimes`, `downtime_state_change_logs` | `internal/downtimeV2` | planned/detected downtime |
| `schedules` | `internal/channel` | bank operating-hour schedules |
| `batches` | batch processing (`internal/batch`) | |
| `otp_requests`, `otp_information`, `otp_request_logs`, `otp_verification_logs` | 2FA/OTP flows | |
| `key_value_store`, `key_value_store_logs` | `internal/keyvalue` | generic KV store (used by leader-election / stuck-payouts cron state per §8) |
| `merchant_configuration` | `internal/merchants` | per-merchant config overrides |
| `merchant_communication_details` | | |
| `audit_logs` | `internal/auditLogs` | |
| `entity_meta` | `internal/entitymeta` | |
| `event_tags` | `internal/events` | |
| `cron_jobs` | `internal/cron` / `internal/cronjobsDashboard` | cron execution bookkeeping, exposed at `/v1/cron_jobs_dashboard/status_with_entity` |
| `state_change_log` | generic state-transition audit log |

**Confidence**: High (migration filenames + model structs read directly).

---

## 8. Worker / Cron / Polling / Reconciliation

**Task-queue architecture**: `RichardKnop/machinery` over Redis (`internal/providers/queue`, `internal/providers/queue/redis.go`). All async work is a named **task** (`internal/tasks/*.go`, registry `internal/constants/worker.go`) published with an optional delay — used both as a work queue *and* as a poller (self-republishing tasks with increasing delay = polling).

Full task registry (task name → purpose), `internal/constants/worker.go`:

| Task | Purpose |
|---|---|
| `initiate_transfer`(.v2) | send transfer_init to Mozart/bank |
| `process_transfer`(.v2) | orchestrate a transfer through its lifecycle |
| `process_attempt`(.v2), `process_attempt_update`(.v2) | per-attempt processing / status write-back |
| `check_transfer_status`(.v2), `verify_transfer_status`(.v2) | **status polling** against Mozart |
| `check_bulk_transfer_status`(.v2) | bulk/file-based status polling |
| `check_file_pending_transfers`(.v2) | sweep for transfers stuck in a file-processing pipeline |
| `check_recon_status` | reconciliation status polling (`internal/tasks/check_recon_status.go`), mutex-guarded per attempt (`MutexProcessReconRequestResource`) |
| `fire_transfer_status_webhook`(.v2) | outbound status push, §4 |
| `fire_bene_status_webhook`(.v2) | beneficiary status push |
| `fire_batch_status_webhook` | batch status push |
| `retry_transfer`(.v2), `retry_transfer_preprocessor`(.v2), `retry_preprocessor_status_update`(.v2), `retry_transfer_source_update`(.v2) | retry orchestration (mode/source-account fallback) |
| `register_beneficiary`(.v2), `verify_beneficiary`(.v2), `process_beneficiary`(.v2), `beneficiary_processed_event`(.v2) | beneficiary registration lifecycle |
| `process_file_beneficiary`(.v2), `register_file_beneficiary`(.v2), `process_file_transfer`(.v2) | **forward file** (bulk/batch) processing |
| `process_attempt_bulk`(.v2), `initiate_attempt_bulk`(.v2) | bulk attempt processing |
| `fetch_account_balance`(.v2), `fetch_healthy_balance`(.v2) | nodal account balance polling |
| `ledger_account_create`(.v2) | Ledger service integration (§12) |
| `create_otp`(.v2), `create_otp_enhanced.v2` | 2FA OTP flows |
| `send_notification`(.v2), `send_slack_message`(.v2) | ops alerting |
| `one_off_db_processing`(.v2) | ad-hoc DB fix scripts, exposed via `/v1/one_off_db_migrate` |
| `stuck_payouts` | stuck-transfer/attempt repair, see below |
| `test_transactions` | synthetic/canary bank health-check transactions |
| `check_wallet_transfer_status.v2` | wallet-specific status check |

**Stuck-payout repair** (`internal/stuckpayouts/cron.go`, `internal/transfer/stuck_payouts.go`):
- Gated to run **only** when `os.Getenv("INSTANCE_TYPE") == "canary"` (cron.go:47) and `config.CronConfig.StuckPayoutsCron.Enable`.
- Uses **leader election** (`internal/leaderelection.StuckPayoutsCronElection`) so only one pod instance runs it, scheduled via `internal/cron` (gocron wrapper, `recurring.JobFactory`) at `ExecutionInterval`/`StartTime` minutes from config.
- Two handlers: `StuckTransfersProcessor` and `StuckAttemptsProcessor` (`internal/transfer/stuck_payouts.go`), each independently toggleable (`StuckPayoutsReInitiate.StuckTransfers.Enabled` / `StuckAttempts.Enabled`), each with its own configured status/time window (`StatusCreated.{Enabled,From,To}` — stuck_payouts.go:168-170,222-224).

**File-based / SFTP forward-reverse processing**: Axis integration pushes bulk files to a remote SFTP endpoint (`internal/integrations/axis/axis.go:143`); JPMC integration pushes files via **Beam** (Razorpay's internal file-relay/queue service, `internal/providers/beam`) from S3 to JPMC's SFTP server (`jpmc_transfer_pricing.go:185`). `[relay]` config section (`config/env.default.toml:9063`, `relay.poll.interval = 15`) suggests a separate polling integration with a service called "relay" (not further explored — time-boxed).

**Confidence**: High for task registry and stuck-payouts gating (read directly). Medium for exact cron schedule values (not read from all env TOMLs) and Axis/JPMC file mechanics (grepped only).

---

## 9. Events (Kafka)

- Producer only (`internal/providers/kafka/producer.go`, `provider.go` — **no consumer.go found**, FTS does not appear to consume Kafka topics itself in this repo).
- Config `[kafka_producers.fire_transfer_status]` (`config/env.default.toml:8450`):
  - `enabled = true`
  - topic: **stage** `beta-fts-status-update-events`; **prod** `rx-fts-status-update-events` (`config/env.prod-live.toml:321`)
  - `brokers`, `enable_tls = true`, mTLS-style `user_certificate`/`user_key`/`ca_certificate` (paths only — no secret values captured), `compression_type = "snappy"`, `max_retry = 10`, `retry_backoff = 2`.
- **Producer trigger**: inside `FireTransferStatusWebhook` (service.go:1136-1144), gated by splitz experiment `Splitz.Experiments.FireStatusUpdateKafka` — when enabled for a merchant, the status update is published to this Kafka topic **instead of** the HTTP webhook call (`go kafka.PushMessageWithKey(ctx, producerName, transferMap, key)`, fire-and-forget goroutine, key = `source_id`). This is presumably how the API monolith/Payouts Service consume FTS status updates at scale for rolled-out merchants, as an alternative to the HTTP path in §4.
- `internal/events/` package (`event_tags.go`, `service.go`, `repo.go`) — appears to be a generic internal event-tagging table (`event_tags`), not a Kafka abstraction.

**Confidence**: High for producer/topic/config. Medium on downstream consumer identity (inferred, not verified — that lives outside this repo).

---

## 10. Config / Feature Flags

- Config loading: `internal/config` (`LoadConfigFromFile`, `config.GetConfig()`), single TOML per environment under `config/env.*.toml` (`env.default.toml` ~9200 lines + many env-specific overlays: dev, func, stage, prod-live/prod-test/prod-dark, itf, e2e, slit, bvt, perf, automation, ephemeral, devstack). A comment in `internal/config/config.go:3,152` states "vault file content will override the config available" — indicates a secrets-vault overlay mechanism layered on top of TOML (metadata only; no vault client code inspected beyond this comment and unrelated card-tokenization "Vault" error codes in `mozart/error_code.go`).
- **Splitz** (`internal/providers/splitz`) — feature-flagging/experimentation provider (`splitz.Provider.IsExperimentEnabled(ctx, merchantID, experimentKey)`), config section `[feature]`/experiment keys under `config.Splitz.Experiments.*`, notably:
  - `CreateTransferMetaRollout` — gates the new Payouts-Service-direct webhook path (§4).
  - `FireStatusUpdateKafka` — gates HTTP-vs-Kafka status push (§9).
  - `ShadowModeHeaders` — adds an `x-shadow-merchant-id` header on webhook calls (shadow-testing a new consumer without switching traffic).
- **Razorx** (`[razorx]` config section, `internal/providers/razorx` not separately inventoried) — likely another Razorpay flagging system alongside splitz; both present in config.
- No references to "DCS" found.
- **Relay** — `[relay]` config + `internal/providers/relay` (poll interval 15s), auth via `RELAY_AUTH_KEY/SECRET` env vars — an internal Razorpay service dependency, purpose not fully traced (time-boxed).
- No secret values were read or printed; only env-var names and config key paths recorded.

**Confidence**: Medium — config keys enumerated exhaustively via grep; not every flag's call-site was traced.

---

## 11. Tests / Fixtures / Docs

- `e2e/` — end-to-end test suite (`e2e/tests`, `e2e/commonflows`, `e2e/provider`, `e2e/repo`, `e2e/dbassert`, `e2e/database`, `e2e/bootstrap`, `e2e/data`) — substantial, dedicated E2E harness against a real/mocked stack.
- `test/mock/`, `test/test_data/` — Go unit-test fixtures.
- `cmd/mock-server/` — bank/Mozart simulator, §6.
- `slit/` — appears to be a code-coverage/SLIT (a Razorpay-internal "self-learning integration test"?) tooling directory with its own coverage output (`slit/coverage`), referenced heavily via `//slit:ignore` comments throughout the codebase (visible in nearly every excerpt above) marking lines as intentionally unreachable in that harness.
- No `graphify-out/graph.json` found in this checkout.
- No dedicated `docs/` or `ADR` files found; `README.md` (installation + a sample curl for `/v1/transfer` showing the Basic Auth pattern) and a large `AGENTS.md` (27KB — appears to be repo-specific AI-agent operating instructions, not architecture docs) are the only top-level documentation.

**Confidence**: High (directory listing).

---

## 12. Dependencies on Other Repos/Services

| Service | Evidence | Purpose |
|---|---|---|
| **Mozart** | `[mozart]` config, `internal/providers/mozart/*` | Bank gateway abstraction — actual bank/UPI/IMPS/NEFT/RTGS connectivity, §6 |
| **Payouts Service** | `[payouts_service]` config, `constants.PayoutsService="PS"` inbound auth, `internal/transfer/service.go` origin-service branch | Caller of `/v1/transfer`, `/v1/transfers`, `/v1/routing/*`; callee of the direct status webhook (§4); also `payouts_service.notify_channel_status`, `notify_downtime`, `retry_source_update` — FTS pushes channel health/downtime notifications and retry-source-update calls back to Payouts Service |
| **API monolith ("api")** | `constants.APIAuth="API"`, `[api_service]`, `[webhook.*.transfer_status]` legacy URLs to `prod-api-int.razorpay.com` | Primary/legacy caller & legacy status-webhook receiver; also called by FTS for `fetch_bene_data` (`fetch_nodal_beneficiary_code`) and `validate_vpa` |
| **Ledger** | `[ledger]` config, `internal/tasks/ledger_account_create.go`, `internal/transfer` twirp client | Twirp RPC (`/twirp/rzp.ledger.account.v1.AccountAPI/CreateOnEvent`) — FTS creates ledger accounts/events |
| **XAS (x-accounts/x-balances)** | `internal/providers/xas/*` (client, get_accounts, fetch_by_multiple_references), used in `internal/transfer/retry_preprocessor_service.go` | Balance/account lookups during retry preprocessing |
| **Settlements** | `[webhook.settlement.transfer_status]` → twirp endpoint `rzp.settlements.transfer.v1.TransferService/StatusUpdate` | Settlement-originated transfers get their own status-webhook contract (twirp, not the generic JSON POST) |
| **Wallet service** | `constants.WalletService="WALLET"` inbound auth; `[webhook.customer_wallet.transfer_status]` → `wallet.dev.razorpay.in` (dev) / `api.razorpaywallet.com` (prod, **2s timeout** — notably tight) | Caller + status-webhook receiver |
| **Beam** | `[beam]` config, `internal/providers/beam/beam.go`, base URL `beam.razorpay.com/push` | Internal file-relay service used for JPMC SFTP file pushes |
| **Relay** | `[relay]` config, `internal/providers/relay` | Unidentified internal dependency, poll-based |
| **Scrooge, ART (recon), Validx, Capital Cards, Xperience, Alert manager** | inbound auth constants + dedicated routes | Various internal callers of specific FTS endpoints (recon/attempts, settlement, capital cards fund accounts, downtime notifications, alerting) |

**Confidence**: High for the inbound/outbound service names (directly evidenced by auth constants + config URLs). Medium on exact purpose of Relay (not traced beyond config).

---

## Capability Tags Summary (per finding, using the requested taxonomy)

| Area | Capability |
|---|---|
| `/v1/transfer` POST, idempotent create | authorize (auth check) + persist (transfer row) |
| State machine transitions | transform (status) |
| Mozart calls (transfer_init/status) | route + transform (external call, response mapped to FSM event) |
| `FireTransferStatusWebhook` | observe (reads transfer) + route (destination selection) — network side-effect (HTTP push) |
| Kafka producer path | route (alternate transport for same observation) |
| Stuck-payouts cron | observe + reverse-repair (re-drives stuck state) |
| Reversal handling | reverse |
| Basic-auth middleware | authorize |
| Manual `/v1/transfer/fail`, manual_update_handlers | transform (ops override) |

---

## Unresolved Questions (flagged for cross-repo/ops follow-up, not resolvable from this repo alone)

1. **Does Payouts Service actually rely on the direct `/v1/payouts/transfer_status_webhook` push, or does it also poll/reconcile independently?** If it has no independent reconciliation against FTS's `/v1/transfer` GET endpoint, the §4 gap (silent give-up after 3 webhook retries, or fallback-to-monolith routing on missing `X-Origin`/un-rolled-out splitz) is a real stuck-state mechanism. This repo shows FTS's side only.
2. **Rollout state of `CreateTransferMetaRollout` and `FireStatusUpdateKafka` splitz experiments** for the merchant/transfer involved in today's incident — not knowable from static config (splitz decisions are runtime, per-merchant, likely stored in an external experimentation service).
2b. Whether Payouts Service reliably sets the `X-Origin` header on every `/v1/transfer` call, and whether anything downstream (LB, service mesh, monolith proxy) could strip custom headers.
3. **What (if anything) alerts on-call when `TransferWebhookUpdateFailureCount` fires** — no in-repo alerting/PagerDuty hook was found triggered directly from the exhausted-retry path; only a metric increment and a log line.
4. Exact behavior of `internal/providers/relay` and its role (if any) in status propagation — not traced beyond config presence.
5. Whether `/v1/transfer/status` (GET, `TransferAction.Get`) or an equivalent is polled by Payouts Service as a fallback/reconciliation mechanism — not verifiable from FTS side.
6. Full list of Mozart-side bank error codes and their FTS-side classification (`internal/providers/mozart/error_code.go` is 818 lines; only a sample was read) — relevant if the incident's specific bank error code needs to be checked against the UTR-presence/ambiguous-handling rules in §3.
