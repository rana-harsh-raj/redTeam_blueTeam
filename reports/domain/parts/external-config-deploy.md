# Lane D5 — external integrations, config and deployment

Part file: `reports/domain/parts/external-config-deploy.json` (406 nodes / 318 edges / 6 families,
`build_graph.py --strict` → 0 schema violations, 0 dangling endpoints).

Pinned read-only clones (`$CLONES` = `/private/tmp/claude-502/-Users-rana-singh/cca012eb-.../scratchpad/rzp-payouts-architecture`):
`stork@800b719`, `dcs@fca59e3`, `config-proto@a6b2010`, `splitz@fa6f4c7`, `governor@875eaba`,
`governor-executor@1bd640c`, `charge-collections@412816c`, `shield-sdk@1a4cbf7`, `ValidX@40f181b`,
`payments-upi@b23ec7e`, `payout-links@2c81a4d`, `vendor-payments@20c4f4d`, `virtual-account@437f398`,
`accounting-integrations@fc13a0b`, `kube-manifests@9226a892`, `alert-rules@c372bea`, `spinacode@da46a52`,
`proto@5268257`, plus the callers `payouts@4bf3dbf` and `api@2d665f9`. No secret values are recorded — env var
**names** only.

---

## 1. Contracts as seen from callers

### Stork — merchant webhook fan-out (P0, `family:webhooks`)

| Path | Purpose |
|---|---|
| `POST /twirp/rzp.stork.webhook.v1.WebhookAPI/ProcessEvent` | fan out one `Event` to every matching `Subscription` |
| `POST .../WebhookAPI/Create` \| `/List` \| `/Update` \| `/Delete` \| `/GetWithSecret` \| `/InvalidateCache` \| `/ProcessEventByEventIds` | webhook CRUD, cache, replay |
| `POST /twirp/rzp.stork.sms.v1.SMSAPI/Send`, `POST /twirp/rzp.stork.email.v1.EmailAPI/Send` | SMS / email |

- **Identity**: HTTP Basic, one username per calling service; Stork resolves the password field *by reflection*
  (`username foo → Auth.FooPassword`, `stork/internal/hooks/auth.go:44-56`). Payouts' prod username is `payout`
  with `Event.Service = "rx-live"` (`payouts/config/prod.toml:118-127,157`); payout-links uses `payout-links`.
- **Request** (`proto/stork/webhook/v1/webhook.proto:56-67`):
  `Event{id, created_at, service, owner_id, owner_type, context, name, payload, entity_id}`. `payload` is an
  **opaque JSON string** Stork never inspects beyond signing it. Payouts builds it as
  `{entity:"event", account_id:"acc_"+merchantID, event, contains:[entityName], payload:{entityName:{...}}, created_at}`
  (`payouts/pkg/stork/process_event.go:113-149`).
- **Behaviour that matters**:
  - *Retry*: exponential, `delay = (2^attempt / 2) * factor`, `Factor=5, MaxTime=86400s (24h), MaxAttempt=15`
    (`stork/configs/default.toml:960-970`, formula `stork/internal/channel/policy.go:65-93`). Allow-listed owners get
    "extended retry": exponential for 24h then a fixed 6h interval to 72h, 23 attempts. Email has its own, much
    smaller policy (`Factor=1, MaxTime=360s, MaxAttempt=3`) — do not conflate.
  - *Delivery timeout*: 6s (`webhookCh.worker.httptimeout=6`, `stork/configs/default.toml:76`).
  - *Signature*: `X-Razorpay-Signature = hex(HMAC-SHA256(webhook.secret, raw Event.Payload))`, alongside
    `X-Razorpay-Event-Id`, `Request-Id`, `User-Agent: Razorpay-Webhook/v1`
    (`stork/internal/webhook/core.go:269-330`). The signature covers the **raw delivered body**, not
    `timestamp+body`.
  - *Dedupe*: none. `messages.event_id` has no unique constraint
    (`stork/internal/migrations/00003_create_messages_table.go:15-26`). At-least-once only — merchants must dedupe
    on `X-Razorpay-Event-Id`.
  - *Ordering*: none. Delivery runs on plain (non-FIFO) SQS `p0/p1/p2` consumed by concurrently autoscaled workers;
    the only `.fifo` queue in Stork is the internal ticker flow.
  - *Channel FSM*: `CREATED → QUEUED → {COMPLETED | FAILED(retry scheduled) | EXCEEDED(terminal) | TRASHED}`.
  - Max 30 webhooks per owner; disabled webhooks still get a persist-only record for 14 days (replay).
- **payout.\* event names** (`payouts/internal/app/common/appConstants/webhooks.go:4-11`): `payout.initiated`,
  `payout.processed`, `payout.reversed`, `payout.failed`, `payout.updated`, `payout.queued`, `payout.rejected`,
  `payout.pending` (internal `ONHOLD` maps to the public `payout.queued`). Also through the same path:
  `transaction.created`, `fund_account.validation.completed/.failed`, `payout.downtime.started/.updated/.resolved`
  (Splitz-gated), and from payout-links `payout_link.{pending,rejected,issued,processing,attempted,processed,cancelled,expired}`.
- **Build/health**: `make build` (`stork/Makefile:100`); API listens on `:6060`
  (`stork/deployments/prod/Dockerfile.api`); health = `POST /twirp/rzp.common.health.v1.HealthCheckAPI/Check`
  (`stork/deployments/probe.sh`).

### DCS — dynamic config (P0, `family:feature-flags-experiments`)

gRPC + grpc-gateway (**not** Twirp). `POST /v1/auth/login` (→ ~6h bearer JWT), then `POST /v1/kv/{get,evaluate,put,patch,audit,entities}`
(`proto/dcs/kv/v1/kv.proto`; ports gRPC `:8080`, HTTP `:8081`, internal `:8082`, `dcs/internal/server/server.go:15-19`).
Health: `dcs.health.v1.HealthService` → `GET /ready`, `GET /live`.

**Key shape**: `Key{namespace, entity, entity_id, domain, object_name}`, flattened as
`{namespace}/{entity}[/{entity_id}]/{domain}/{object_name}` — e.g. `rzp/x` + `merchant` + `<mid>` +
`payouts/direct_accounts` + `Configs`. `KeyValue.value` is **protobuf bytes** of the named message. Per-slot
`Fieldmasks` are applied as a *redaction after the read* (`dcs/internal/kv/helper.go` `redaction.apply()`,
`dcs/internal/kv/service.go:436`) — an empty fieldmask returns the whole object.

Payouts' client keys 8 objects (`payouts/pkg/dcs/features/features.go:12-25`). Every field is a proto3 zero-value
default (`bool→false`, `int64→0`, `repeated/map→empty`); the full inventory scanned from `config-proto` is encoded
as `flag:dcs/<Message>.<field>` nodes (112 flag nodes total, of which the payouts-relevant objects are):

| Object | Fields (all default false unless noted) |
|---|---|
| `payouts/Workflows` | `enable_payout_workflow`, `skip_approval_workflow_for_api`, `skip_workflow_for_dashboard`, `skip_workflow_for_payroll`, `enable_approval_via_oauth` |
| `payouts/FundTransfer` | `enable_payouts`, `payouts_to_fts_async_processing`, `increase_per_payout_amount_limit`, `payouts_blocked_via_lite_account`, `enable_payouts_queue_buffer`, **`queue_payout_bal_buffer` (int64, default 0)**, `block_va_payouts` |
| `payouts/ApiInterface` | `payout_service_enabled`, `payout_idem_key_required`, `enable_http_encryption`, `below_rupee_payouts`, `enable_beneficiary_name_in_response`, `enable_null_narration`, `bene_name_in_payout`, `rbl_ca_upi`, `enable_ip_whitelist_fetch`, **`fmp_config` (map<string,string>, default empty)** |
| `payouts/Cfa` | `skip_ifsc_lookup` |
| `payouts/direct_accounts/Configs` | `in_flight_reservation_enabled` |
| `payouts/direct_accounts/PayoutModeConfig` | **`allowed_upi_channels` (repeated string, default empty)** |
| `payouts/IntelligentPayouts` | `enable_intelligent_payouts`, `disallow_traffic_for_test_transactions` |
| `payouts/SubAccountRoles` | `assume_master_account`, `assume_sub_account`, `allow_limit_transfer_to_sub_merchant` |
| `accounting/IntegrationSettings` | `sync_payouts` |
| `payouts/{Batch,BusinessWallet,Cards,Communication,FundLoading,HighTps,Internal,PayrollPayouts,Routing}` | 15 more fields that exist in `config-proto` but are **not** wired into `payouts/pkg/dcs`'s `Key()`/`MerchantFeatures()` |
| `account_validation/{AccountValidation,FavService,PayrollSourceAccountValidation}`, `validx/{Cache,Stork}`, `account_statement/bank/{RBL,ICICI}` | the FAV / statement-side objects (`fav_service_enabled`, `skip_cache`, `enable_stork`, `ftp_fetch_enabled`, …) |

**Client behaviour**: BigCache, 15s eviction, 8MB, 16 shards, key
`mode/namespace/entity/entityId/domain/objectName/-fieldmasks`. **Fail-off**: on any fetch error
`payouts/internal/app/merchant/core.go:312-320` substitutes an **empty FeatureMap** in a non-blocking goroutine, so
every DCS flag silently resolves to absent/false.

### Splitz — experiments (P1)

`POST /twirp/rzp.splitz.evaluate.v1.EvaluateAPI/{Evaluate,EvaluateBulk,FetchDecisionContext,AllowCors}`, HTTP Basic
(`[splitz.auth]` username `payouts`). Request `{id (= merchant id at every payouts call site), experiment_id,
experiment_name, request_data, track_impression}`; response `{id, project_id, experiment{...},
variant{id,name,variables:[{key,value}],experiment_id,weight,region}, Reason, steps}` with the convention
`Variable{Key:"result", Value:"on"|"off"}` (`payouts/pkg/splitz/client.go:149-161`).

Payouts declares **37 experiments** in `[splitz_experiment_list]` (`payouts/config/prod.toml`) — not 8; the
"8 named ids" in earlier findings were a subset. Production runs `client_side_eval = false`
(`payouts/config/prod.toml:442`), i.e. the wire `Evaluate` call *is* the production path; `FetchDecisionContext`
+ local MurmurHash bucketing is only used where `client_side_eval = true` (the `default.toml` value, overridden in
prod). `GetVariantOrDefault` returns `{Name:"default", Variables:[{result:"off"}]}` on any nil variant, so
**Splitz fails off**. Cache: in-memory BigCache keyed by experiment id/name (not per merchant), 5-minute TTL,
10-minute fallback TTL on error, negative caching.

The payouts-gating experiments include `merchant_config_via_asv_and_dcs_experiment`,
`payout_workflows_dcs_name_experiment`, `use_api_db_for_payouts_experiment`,
`use_api_db_for_payout_status_details_experiment`, `forDuplicatePayoutEvaluate` (enforce vs shadow-log),
`forChargeCollectionEvaluate` / `charge_collections_call_experiment` / `charge_collections_call_canary_experiment`
(fee source), `free_payouts_xbalances_experiment`, `deduct_credits_experiment`,
`fts_request_from_payouts_service_experiment`, `partner_bank_on_hold_payouts_experiment`,
`account_statement_source_event`, `source_updater_sns_experiment`, `ikey_auto_enforcement_ramp_up`,
`fetch_balance_entity_from_x_balance[_async]`, `skip_balance_cache_force_x_balances`, `fmp_initiate_experiment`,
`xas_source_event_utr_and_grn_match_experiment`, `fetch_from_microservices_experiment`. Every one is a
`flag:splitz/<name>` node carrying its prod id and the arena id it was replaced with.

### Governor / Charge Collections — fee resolution (P0, `family:pricing-free-payouts`)

- **Governor** `POST /v1/namespaces/:namespace_id/execute` (`governor/app/controllers/governor.go:371-414`), Basic
  auth scoped by app identity. Request: a dynamic `EvaluatingEntity{Name, Data}` + `SupportingEntities`; response
  `ExecutionResponse{ExecutionSummary[{ChainsExecuted[{RulesetName, ExecutedRules, ErrorRules, CumulativeScore}]}], Success, Error}`.
  Governor is a rule **selection/scoring** engine — its schema has no fee/amount fields at all. Rules live in a
  MySQL `rules` table as namespaced, versioned JSON with `govaluate` condition strings
  (e.g. `product == "banking" && mode == "IMPS" && amount > 25000`). Payouts configures 5 chain ids
  (`basic`/`product`/`bank`/`mode`/`amount`, `payouts/pkg/ccSdk/config.go:22-29`) but never dials Governor itself —
  the vendored `charge-collections-sdk` does. `governor-executor` is an in-process library (imported by both
  governor and charge-collections), not a service. Health: `GET status`, `GET ping`, `GET metrics`, `GET commit.txt`.
- **Charge Collections** `GET /v1/mdr/pricing/plans/fees_calculation/{id}` (`GetPricingPlanForFeesCalculation`),
  whose `Rule` message carries `percent_rate, fixed_rate, min_fee, max_fee, percent_rate_scale_factor,
  amount_range_min/max, payouts_filter, fee_bearer, …` (37 fields, modelled on the legacy `pricing` table).
  CC itself configures a `GovernorConfig` (`charge-collections/config/*.toml
  [chargeCollectionsSDKConfig.GovernorConfig]`), confirming the CC→Governor direction.
- **Which path actually runs**: the default is a Redis cache pre-populated by the API monolith, falling back to a
  live `POST /payouts_service/fetch_pricing_info` (`api/app/Http/Route.php:4818`); the ccSdk/Governor path only runs
  when the `charge_collections_call` experiment is on. **`fee_type == free_payout` always uses the monolith call.**
- **Free-payout counters**: the counter lives in a `counters` table keyed by `balance_id`, with
  `free_payouts_consumed` (int, default 0) and `free_payouts_consumed_last_reset_at` — in *both* payouts
  (`payouts/internal/database/migrations/20220526144631_create_counters_table.go:17-18`) and the monolith
  (`api/database/migrations/2020_06_29_171820_create_counters_table.php`). The monolith increments inside a DB
  transaction with `lockForUpdate` (`api/app/Models/Payout/CounterHelper.php`) and resets to 0 on the first of the
  calendar month **in IST** (`api/app/Models/Counter/Entity.php:106-112`). Payouts mirrors *decrements* (reversal /
  failure / non-eligible) to the monolith via `POST /payouts_service/decrement_free_payouts`
  `{balance_id, merchant_id, payout_id}` → `{balance_id, free_payouts_consumed, free_payouts_consumed_last_reset_at}`
  (`payouts/pkg/api/update_api_free_payouts.go:13-23`, `api/app/Http/Route.php:4827`).

### Shield — risk (P0)

`POST /v1/rules/evaluate/payout` (`shield-sdk/rule_evaluation/service.go:88-126`,
`constants/rule_evaluation.go:4`), HTTP Basic `[shield.auth]`. Request `PayoutEvaluateRequest{merchant_id,
entity_type="payout", entity_id, rulesets (always nil from payouts), skip_default_execution (always false),
input:{PayoutID, Mode, AccountNumber, MerchantID, Purpose, …}}`; response `{action, triggered_rules, status_code}`.
`action ∈ {"allow", "block", "review"}` exactly (`shield-sdk/constants/http.go:13-15`). **Payouts branches only on
`action == "block"`** (`payouts/internal/app/payouts/core.go:6883`) — `review` is treated identically to `allow`,
there is no review-hold path. 200ms timeout, Hystrix 20-req/50%-error/5s-sleep, **fail-open** on error or timeout;
`purpose == "rzp_fees"` payouts skip Shield entirely. shield-sdk is a library (no server, no Dockerfile); the Shield
service repo (~6.7 GB) is not cloned, so the server-side rule catalogue is unreadable.

### ValidX — fund-account validation / FAV (P1, `family:fund-account-validation`)

**Payouts never calls ValidX** (zero `validx` references in the payouts clone). The caller is the PHP monolith
(`api/app/Services/FavService/CreateV2.php`, Razorx-gated `FAV_SERVICE_V2_ROUTING_ENABLED`). Every ValidX route is
`/v1/internal/...`; ingress identities are exactly three — `api`, `cron`, `admin` — Basic auth with two rotating
passwords plus a per-username gRPC-method ACL (`ValidX/internal/server/interceptor/{basicauth.go:97-139,authz.go:76-100}`).

| RPC / HTTP | Note |
|---|---|
| `CreateValidation` `POST /v1/internal/validations` | implemented (`server.go:42`) |
| `CreateValidationV2` `POST /v2/internal/validations` | **declared in the proto, server handler NOT implemented in the pinned clone** (`validation_api_grpc.pb.go:169`) |
| `GetById` `GET /v1/internal/validations/{id}` | implemented (`server.go:390`) |
| `FTSWebhookUpdate` `POST /v1/internal/validations/webhook/fts` | implemented (`server.go:208`) |
| `CitiWebhookUpdate` `POST .../webhook/citi` | **declared, not implemented** |
| `.../webhook/citi/payment-acceptance`, `.../webhook/citi/refund` | implemented (`server.go:706,848`) |
| `ManualAction` `POST /v1/internal/validations/manual-action` | implemented (`server.go:536`) |
| `RoutingAPI` CRUD `/v1/internal/routing/rules[/{id}]` | gateway-routing-rule management |

Flows: validation types `pennydrop | penniless | paisadrop | upi_intent (RPD) | cache` over `bank_account`/`vpa`;
gateways `fts | citi | api | slice`, resolved DB-first from `gateway_routing_rules` and falling back to hardcoded
defaults (bank-account penniless→citi, pennydrop/paisadrop→fts, VPA pennydrop/paisadrop→fts, RPD→citi,
`ValidX/internal/constant/validation.go:223-230`). State machine `created → initiated → completed | failed`
(`:10-18`) with no expiry/TTL/freshness column anywhere. Outcome delivery is a double hop: gateway → ValidX webhook
→ job → either Stork `fund_account.validation.completed/.failed` (when the DCS `Stork.enable_stork` flag is on) or
`POST /v1/fund_accounts/validations_internal/webhook` back to the monolith
(`ValidX/internal/validation/statemanager/manager.go:132-133`), **plus** an SQS dual-write to
`prod-api-validation-dual-write-live` on create and on every state change. Fees are resolved through
Charge-Collections `CalculateFees` on create and a fee error **fails validation creation** (fail-closed).
Build: `make go-build` (`BINS = server worker migration console`); health `GET /ready`/`GET /live` (gRPC
`common.health.v2.HealthService`), ports `:8080` gRPC / `:8081` HTTP / `:8082` metrics.

### UPS (payments-upi) — VPA validate (P2)

`VpaService`: `POST /v1/vpa/validate` (legacy), `POST /v1/payments/validate/vpa` (real UPI-network validation via
Mozart / Integrations-UPI, `paytm` handles short-circuited invalid), and the one payouts actually uses:
`POST /v1/payments/validate/account` — `{entity, value}` → `{vpa, success, customer_name}`
(`payouts/pkg/upiService/vpa_mapper.go:18-83`), HTTP Basic (`[ups.auth]` username `payouts`) with header
`X-Origin: payouts`, which is special-cased server-side to include the **plaintext** VPA
(`payments-upi/internal/app/validate/processor/response.go:64`). Exposed to payouts callers as the internal route
`GET /mapped_vpa/:upi_number`. FTS does **not** call UPS — it calls the monolith's own `/v1/payment/validate/vpa`
with a `validate_vpa_internal_merchant` credential.

### payout-links / vendor-payments / virtual-account / accounting-integrations

| Service | Creates a payout by | Learns the status from | Own states |
|---|---|---|---|
| **payout-links** | `POST {api}/v1/payouts_internal` with header `X-Payout-Idempotency`, Basic `routeKey = rzp_live` (`payout-links/internal/payout/core.go:42-52`, `config/routes/routesprod.toml [PayoutConfig.CreatePayout]`); also `GET /v1/payouts_internal[/{id}]`, `POST /v1/payouts/purpose/validate`, `PATCH /v1/payouts_internal/attachments` | monolith push `twirp/payoutlinks.Payoutlinks/UpdatePayoutLinkStatus` (`api/app/Services/PayoutLinks.php:109`), mapped by `PAYOUT_TO_PAYOUT_LINK_STATUSES` (FAILED/REVERSED/REJECTED/CANCELLED→attempted, CREATED/INITIATED/PROCESSING/QUEUED/PENDING→processing, PROCESSED→processed); payouts also publishes to SNS `payout-updates-payout-links` | `pending, issued, processing, attempted, processed, cancelled, expired, rejected` + the public alias `attempted→issued` |
| **vendor-payments** | `POST {api}/v1/payouts_internal/` with `X-Dashboard-User-Id` + `X-Payout-Idempotency`; `POST /v1/payouts/2fa/create_internal` for the 2FA flow; `POST /v1/payouts_internal/{id}/cancel`; `PATCH /v1/payouts_internal/{id}/tax-payment-id` (`internal/payout/core.go:83-86,108-131`, `internal/taxpayments/apicaller.go:21`) | monolith push `twirp/vendorpayments.Vendorpayments/PayoutStatusChange` `{merchant_id, payout_id, payout_status, source_id, source_type}` (`api/app/Services/VendorPayments/Service.php:32,130,482`); handled under a Redis lock and a duplicate push is rejected with "payout webhook duplicate request" (`internal/payments/core.go:120-175`) | payment `RequestStatus {Processing, Success}` + the mirrored payout status |
| **virtual-account** | n/a (funds *in*) | n/a | emits `virtual_account.created` / `.closed` through Stork; Twirp `rzp.x.virtualaccount.v1.VirtualAccountService/{Create,Close,Get,GetByAccountNumber,GetByVPAAddress,GetByOrderID,CreateForBanking,GetPublic}` |
| **accounting-integrations** | n/a | consumes the Kafka topic `prod.x.vendor-payments.accounting-payouts.status-update` in `VendorPaymentUpdateListener` (`internal/service/job/vp_updates_listener.go:29-90`, payload `{MessageType, VendorPaymentId, VendorPaymentStatus, PaymentId, MerchantId}`), gated by DCS `accounting/IntegrationSettings.sync_payouts` | — |

Build/health: payout-links `docker build .` → `deployments/entrypoint.sh {api|migration|webhook}`, health `GET /status`
and `GET /commit.txt` on `[core].port`; vendor-payments `make build-vendor-payments|build-worker|build-ocr-worker|build-ap-worker`
(`Makefile:209-237`) → `deployments/entrypoint.sh {api|worker-v2|ocr|tds|email-int|ap|cron_accounting_payouts|fund-account-verification|contact-updated|auto-invoice-processing|migration_up[_accounting_payouts]}`;
virtual-account `make build` (`BINS = server worker migration console`); accounting-integrations
`go build ./cmd/{api,worker,task,cadence}`, health `GET /status`, `GET /ping`, `GET /commit.txt`.

---

## 2. Prod deployment inventory (kube-manifests @ 9226a892)

Charts live at `templates/<svc>/templates/`; resolved values at `cde/<svc>/values.yaml` (CDE cluster) **or**
`prod/<svc>/values.yaml` — never both. All 13 requested charts are present.

| Service | values env | Deployments / workers (prod) | CronJobs | Twin status |
|---|---|---|---|---|
| **payouts** | `cde/payouts` | 39 Deployments: `payouts` (args `api`, 10 replicas, HPA 12/25) + `-baseline`/`-canary`; 2 Kafka consumers (`PAYOUTS_CONSUMER_TASK_NAME=fts_status_updates`, `…_retry`, 1 replica, HPA 1/5); 25 SQS workers keyed by `PAYOUTS_WORKER_NAME` with `args = worker sqs-<name>-{live,test}` — largest are `fts_async_processing` (30, HPA 30/50), `fts_async_hv_processing` (10, 10/20), `transaction_create` (10, 10/20), `data_consistency_checker` (5, 5/12); scaled to **0** in prod: `payout_source_updater`, `mail_and_sms_event`, `update_source_event`, every `-test` twin | **none** (`/v1/cron/*` is an HTTP route inside the `api` binary, driven by the external FastCron SaaS) | 15 of 25 workers + api + both Kafka consumers run in `ENV2_COMPOSE` at 1 replica each; `bulk_payouts`, `batch_submitted_merchants`, `data_consistency_*`, `payout_usage_event_processing`, `fund_management_payout_{check,initiate}`, `mail_and_sms_event`, `update_source_event` are **not** in the twin |
| **shadow-payouts** | `cde/shadow-payouts` | 18 Deployments — structural mirror at reduced scale (`api` 3 replicas HPA 3/5; workers `async-dual-write`, `batch-submitted-merchants`, `bulk-payouts`, `fts-async-processing` (2, HPA 2/5), `generic-processing`, `on-hold-processing`, `partner-bank-hold-payouts`, `queued-processing`, `schedule-processing`, `transaction-create`; args prefixed `shadow-sqs-*`, same `PAYOUTS_WORKER_NAME` values) | none | **not represented** |
| **fts** | `prod/fts` | 475 rendered Deployments = **170 distinct worker identities** × `{-live, -live-baseline, -live-canary}`. `fts-live` (`web`, 11); core workers `initiate_transfer` (4, HPA 20/20), `check_transfer_status` (2, 9/15), `fire_transfer_status_webhook` (7, 8/17), `hv::fire_transfer_status_webhook` (1, 9/19), `register_beneficiary` (1, 5/20), `retry_transfer[_preprocessor|_source_update]`, `check_recon_status`; per-rail fleets AXIS 12, ICICI 14, IDFC 26 (`idfc::upi::hv::initiate_transfer` HPA 25/45), RBL 24, YESBANK 24, SLICE 16, HDFC 2, AMAZON_PAY 3, OCBC 1, MCS 1. Worker identity is entirely `args[1]` (`bank::product::action`) — no queue-name env var | none | 12 fts workers run in the twin (`ENV2_COMPOSE` fts-worker-*); the rest are graph-only (owned by lane `fts-mozart`) |
| **ledger** | `prod/ledger` | 153 rendered = 55 distinct. `ledger-live[-pg|-mirror|-pg-mirror]` (2 each; HPA 5/30, 10/40, 2/6, 2/6); `{account-create, balance-update, entry-details-create, journal-create}` workers × `{live,test,pg-*,*-mirror}`; **16 per-team `journal-create-<team>-worker-pg-{live,test}`** Kafka-CDC consumers (api 13, ups 13, cps 8, emandate 8 replicas). Two scaling mechanisms: 8 HPAs + 6 `SqsAutoScaler` CRDs (`aws.uswitch.com/v1`, SQS-depth based) | **4, all `suspend: true` hardcoded**: `verify-warm-storage-cronjob-{live,test}` (`0 * * * *`; its `{{- if }}` guard key is absent from prod values so it **does not render at all**), `split-account-balance-update-cronjob-{live,pg-live}` (`*/10 * * * *`, concurrency Allow/Forbid) | 7 ledger containers run in the twin (`ledger-api`, `ledger-scheduler`, and 5 workers); **no cron equivalent** |
| **cfa** | `cde/cfa` | `web` 8 (autoscaling 8/10, targetCPU 65) + `web-canary`/`web-baseline` (1 each); 2 workers: `cfa-worker-fa` (`CFA_WORKER_QUEUE_NAME=prod-api-rx-fund-account-lazy-loading-live`, 1, HPA 1/3) and `cfa-worker-contact` (`…-contact-lazy-loading-live`, 1, HPA 1/3) | `crons: []` — none active (only a commented example) | `cfa-server` + both workers run in the twin |
| **x-balances** | `prod/x-balances` | `web` 4 (+canary/baseline, autoscaling 2/10); 8 workers on `WORKER_NAME`: `balance_fetch_{rbl (8, HPA 8/20, has a priority queue), icici, yesbank, axis, idfc, slice}_worker`, `payout_events_worker` (HPA 4/20), `account_activation_events_worker` (HPA 2/10) | `crons: []` | `xbalances-server` + 1 generic worker run in the twin |
| **banking-account** | `prod/banking-account` | 3 Deployments (`banking-account` args `api`, 2 replicas; `-baseline`/`-canary` 1 each); no HPA resource renders | none | `sub:bankingaccounts-stub` (2 routes only) |
| **x-account-statements** | `prod/x-account-statements` | `web` 2 (+canary/baseline, autoscaling 2/8); 13 workers on `X-ACCOUNT-STATEMENTS_WORKER_QUEUE_NAME` — `fetch` (4, HPA 4/8), `{ybl,idfc,axis,icici,slice}-fetch`, `statement-save`, `enrichment`, `source-event`, `statement-cdc` (10, HPA 10/15, Kafka), `accounts-cdc` (10, 10/15, Kafka), `data-migration`, `account-activation-events` | `crons: []` | `sub:xas-sim` |
| **workflows** | `prod/workflows` | 3 Deployments: `workflows` (web, 5, HPA 5/25), `workflows-worker-asl` (2, HPA 2/6), `workflows-worker-wd` (5, HPA 10/40); env `TOPIC="LOC_WD_PP"`. **The Cadence cluster it depends on has no manifest here** | none | `sub:workflow-sim` |
| **batch** | `prod/batch` | 8 Deployments: `batch-web` (`web`, 10), `batch-sqs` (`sqs`, 5, HPA 8/15), `batch-ingress-sqs`, `batch-sqs-art-dual-write` (6), `batch-sqs-art-prs` (3), `batch-sqs-payment-links` (2), `batch-sqs-reconciliation` (2), `batch-sqs-sftp-notification` (1) | **1, `suspend: false`** — `batch-shutdown-recovery-cronjob`, `*/15 * * * *`, `bash batch_deployment.sh shutdown-recovery`, concurrency Forbid. **The only un-suspended CronJob in the whole payouts domain** | not represented |
| **stork** | `prod/stork` | 25 distinct Deployments (+`-baseline`/`-canary` mostly at 0). `stork` web 10 (HPA 10/72); `stork-webhook-worker` 10 (SqsAutoScaler **20/54** on `prod-stork-webhook`), `-p1` 10 (15/54), `-p2` 7 (10/17); `stork-db-writer` 10; `stork-email-worker` 6 (2/11); `stork-sms-worker` 4 (2/11); `stork-whatsapp-worker` 3; `stork-ticker-webhook-worker-p1` 3, `…-fifo-worker-p1` 2; `stork-scheduler-worker` 1; `stork-push-notification-worker` 1; reminders workers and every `-dlq`/`-service-explicit` variant at **0** | **3**: `stork-scheduler` (`*/3 * * * *`), `stork-webhook-disabler` (`*/30 * * * *`), `stork-partitioner` (schedule key not set in prod values). Their `*_cron_suspended` keys are absent from prod values, so the `suspend` field renders falsy | `sub:stork-capture` — **no worker, no scheduler, no queue** |
| **validx** | `cde/validx` | `web` 12 (autoscaling 12/24, CPU 65 / mem 75) + baseline/canary; 11 workers on `X-VALIDX_WORKER_QUEUE_NAME`, biggest are `slice-penniless-{initiate,inquiry}` at **40 replicas each (HPA 40/50)**; `pennydrop-initiate` scaled to **0** | `crons: []` | not represented |
| **mozart** | `cde/mozart` | `mozart` web **70** (HPA 50/120), `-baseline`/`-canary` 7 each (HPA 1/27), `mozart-worker` 0 | none | `sub:mozart-sim` (lane `fts-mozart`) |

**IngressRoutes / the cron whitelist.** `templates/payouts/templates/ing-v2.yaml:102-149` defines three routes:
`payouts-internal` (`Host(payouts.razorpay.com)||Host(payouts-ext.razorpay.com) && PathPrefix(/)`, middleware
`headers-common`), `payouts-statuscake` (`/status`), and `payouts-external`, which has two rules — **only** the
`PathPrefix(/v1/cron/)` rule carries the Traefik `payouts-fastcron-ip-whitelist` Middleware (`ipWhiteList`,
`depth: 1`, 7 hardcoded FastCron CIDRs); its sibling `PathPrefix(/commit.txt)` rule is unprotected.
`shadow-payouts` mirrors this with `shadow-payouts-fastcron-ip-whitelist`. The only other IP allow-lists in the
whole domain are `stork-karix-ip-allowlist` (on `/email/callback/karix` only, of 22 Stork ingresses) and
`mandatehq-mozart-whitelist` (on `mozart-ext`). Ledger, cfa, x-balances, xas, workflows, batch, banking-account and
validx have **no** IP whitelist on any route.

**Env var names pointing at queues/topics/hosts** (values redacted; the queue/topic *names* below are architecture,
not secrets): `PAYOUTS_WORKER_NAME`, `PAYOUTS_CONSUMER_TASK_NAME`, `LEDGER_WORKER_QUEUENAME`,
`LEDGER_QUEUEKAFKA_KAFKA_TOPIC`, `LEDGER_SCHEDULER_COMMAND`, `CFA_WORKER_QUEUE_NAME`, `WORKER_NAME` (x-balances,
plus `queue_name` / `priority_queue_name` values), `X-ACCOUNT-STATEMENTS_WORKER_QUEUE_NAME`,
`X-VALIDX_WORKER_QUEUE_NAME`, `STORK_QUEUEWORKER_MSGCONSUMERPOOLSIZE` / `…MSGFETCHERPOOLSIZE` / `QUEUE_ID` /
`STORK_WEBHOOKCH_QUEUE_P0_CONSUMER_DEQUEUEBATCHSIZE`, `TOPIC` (workflows), `GATEWAY_LATENCY_EVENT_BROKER` /
`GATEWAY_LATENCY_KAFKA_TOPIC` (mozart), `mozart_client_host` / `api_host` / `fts_host` / `*_stub`
(banking-account), `APP_MODE` / `APP_ENV`, `JAEGER_HOSTNAME` / `*_TELEMETRY_EXPORTERHOST` (node-derived),
`envFrom.secretRef` (`payouts`, `shadow-payouts`, `fts-live`/`fts-test`, `ledger-live`/`ledger-test`,
`stork-secrets`, `workflows`, `banking-account`) and `envFrom.configMapRef` (ledger only).

**Spinnaker (spinacode @ da46a52).** Every service above has a `v3/<svc>/<env>/<region>/` pipeline set (`app.json`
RBAC gate, `deploy-to-a-region.json`, `canary-config.json` with a NetflixACAJudge-v1.0 config, rollback/scale/rotate
stages) **except `workflows`, which has no Spinnaker pipeline in either generation.** No pipeline stage renders app
config — TOML is baked into the image or read from the k8s Secret/ConfigMap at pod start.

---

## 3. Substitute fidelity

| Substitute | Implements | Verdict | Top divergences from source |
|---|---|---|---|
| `sub:stork-capture` | `svc:stork` | **high_fidelity_replacement** (wire) / **behavioural_placeholder** (delivery) | 1. **No retry at all** — delivery is synchronous and single-attempt; production runs 15 attempts over 24h with `(2^n/2)*5s` backoff, a `CREATED→QUEUED→FAILED→EXCEEDED` FSM and a scheduler CronJob every 3 min. 2. No SQS, no p0/p1/p2 priority split, no DLQ, no `stork-db-writer`, no Trino-driven auto-disable. 3. At-least-once / no-ordering are only reproducible *on demand* via `STORK_DUPLICATE_TERMINAL` and `STORK_REORDER` (both default off) instead of arising naturally from queue concurrency. 4. `ProcessEvent` never resolves partner/affected owners (`FindAffectedOwnersViaCache`, Ezetap sub-entity injection). 5. Production wire is **Protobuf** Twirp; the twin patches payouts to a JSON client via `ARENA_STORK_JSON=1` on every container (DEV-121), so the Protobuf path is never exercised. Header set, HMAC-over-raw-body and the 8 `payout.*` names are exact. |
| `sub:merchant-webhook-sink` | `ext:merchant-webhook-endpoint` | **high_fidelity_replacement** | Verifies `X-Razorpay-Signature` exactly as a real merchant verifier would and records `signature_valid`; always returns 200 (never exercises Stork's non-2xx retry path — which the sender wouldn't honour anyway). |
| `sub:dcs-stub` | `svc:dcs` | **high_fidelity_replacement** | Real proto3 wire encoding with zero-value omission **and** production-faithful fieldmask redaction (fixed in DEV-172 after over-returning `queue_payout_bal_buffer` crashed `payouts-api` with `cannot convert int64 to bool`). Remaining gaps: `Evaluate` is identical to `Get` (single storage level, no org/merchant level combination); `audit` always empty; no DynamoDB, no Kafka change stream; the `/_arena/legacy/{merchant_id}` alias table is a stub-only convenience, not a DCS API (the real legacy-interop mechanism is the Proxy service's `DCSFeature{write_via_client, read_enabled_via_dcs, dual_write_enabled}`). Reachability depends on the `ARENA_DCS_URL` build patch (DEV-120/122) — a pristine binary resolves its host from a hardcoded env→hostname map. Also unknown whether x-balances' own DCS wrapper takes the same branch. |
| `sub:splitz-stub` | `svc:splitz` | **high_fidelity_replacement** (shape) / **behavioural_placeholder** (values) | 1. **Only 3 of 37 payouts experiment ids match production** — 34 were replaced with `arena_ps_*` names or different real-looking ids (DEV-025), so no experiment-id-level claim transfers to prod. 2. Variants are fixed per-merchant fixtures; **no percentage rollout exists**, so a partially-rolled-out gate cannot be reproduced. 3. `FetchDecisionContext` returns `{experiments: []}`, so the client-side bucketing path (MurmurHash 113/919 + BigCache) is unreachable — harmless because prod also runs `client_side_eval = false`. 4. `DEFAULT_VARIANT = "off"` correctly matches `GetVariantOrDefault`, but the stub's `CONTRACT.md` still documents the default as `"on"` — stale doc. 5. Ledger resolves its experiments from `[splitz].mock = true` while payouts resolves from the live stub (DEV-062) — the two services can answer the same experiment differently. |
| `sub:shield-stub` | `svc:shield` | **high_fidelity_replacement** | Endpoint, request DTO and the `allow|block|review` enum are exact. Verdicts come from 4 scripted first-match-wins rules in `seeds/shield/rules.json` instead of Shield's server-side catalogue, so no real rule, ruleset or velocity signal is modelled. `SHIELD_LATENCY_MS` faithfully exercises the 200ms-timeout fail-open. Note the preserved source gap: payouts treats `review` exactly like `allow`. |
| `sub:monolith-stub` | `svc:api-monolith` (pricing + free payouts) | **high_fidelity_replacement** (routes) / **behavioural_placeholder** (pricing) | 1. `fetch_pricing_info` returns a flat synthetic tariff per `(merchant, mode)` from `seeds/pricing.json` (Rs 2 + 18% GST for IMPS, marked `ASSUMED synthetic tariff`) — **no Charge-Collections `Rule` evaluation, no Governor rule chains, no percent/min/max/fee-bearer/amount-range logic at all**, so nothing about real fee selection is testable. 2. `decrement_free_payouts` keeps an in-memory counter (lost on restart) with **no monthly IST reset and no row lock**, versus the monolith's `counters` table with `lockForUpdate` and `Carbon::now(IST)->firstOfMonth()` reset. 3. `actor_info_internal` and `users_internal` are best-effort fixtures whose route names were never confirmed in `Route.php`. 4. `update_fts_fund_transfer` relays exactly once with no retry (faithful) but commits no FTA row. |
| `sub:pricing-stub` | `svc:charge-collections` | **behavioural_placeholder** | Not wired to any client: `[ccSdk] mock = true` and the arena template renders no `[ccSdk]` host, so nothing can dial it. Kept only so a future patched binary would get numbers consistent with `monolith-stub`. |
| `sub:kong-lite` | `svc:edge-kong` | **high_fidelity_replacement** | Merchant Basic-Auth → RS256 passport mint (claims per findings/21 §2.2(a), `kid = arena-passport-1`) → strips the client credential → adds the service `Authorization` → proxies. Not Kong: no rate limiting, no plugin chain, and — most relevant to this lane — **no `ipWhiteList` middleware**, so the FastCron network boundary that guards `/v1/cron/*` in production is absent. |
| `sub:cron-driver` | `ext:fastcron` | **behavioural_placeholder** | Correct routes, correct FastCron Basic credential, but **every cadence is an assumption** (DEV-170) — the real schedules live in the FastCron SaaS dashboard and are in no readable artefact. The BAS-fetch job also raises `inactive_duration_limit` from the source default of 3600s to 10 years so synthetic accounts are never classified inactive. |
| `sub:bankingaccounts-stub` | `svc:banking-accounts` | **behavioural_placeholder** | Only 2 of the service's routes exist (`payouts/shield/merchant/{id}/details`, `…/credentials`); `fetch_account`, `fetch_banking_accounts` and the rest return an object-shaped 404. Credentials are synthetic and mozart-sim ignores them (DEV-168). |
| `sub:asv-stub` | `ext:account-service` | **behavioural_placeholder** | Documented dead end: payouts' ASV client is real gRPC constructed with an unconditional `EstablishConnectionWithConfig`, never reads a `Mock` flag, and cannot be answered by a stdlib JSON server. Useful for inspection only. |

**No substitute exists** for `svc:validx`, `svc:ups`, `svc:governor`, `svc:charge-collections`, `svc:payout-links`,
`svc:vendor-payments`, `svc:virtual-account` or `svc:accounting-integrations`. Governor, ccSdk and UPS are
blackholed to `http://127.0.0.1:1` in `arena.toml` (DEV-010); the rest are simply absent, so
`family:fund-account-validation`, `family:payout-links` and `family:vendor-payments` are `graph_only`.

---

## 4. Queue / topic inventory

| Name | Kind | Producer | Consumer |
|---|---|---|---|
| `prod-stork-webhook`, `-p0`, `-p1`, `-p2` | SQS (standard) | `svc:stork` `ProcessEvent` | `worker:stork/stork-webhook-worker[-p1|-p2]` (SqsAutoScaler 20/54, 15/54, 10/17) |
| `prod-stork-webhook-dlq` | SQS | Stork delivery failure | a `-dlq` worker variant exists in the chart at 0 replicas |
| `prod-stork-reminders-webhook-p1/-p2` | SQS | Stork reminders | reminders workers (0 replicas, autoscaler disabled) |
| `prod-stork-ticker-webhook-p1`, `prod-stork-ticker-webhook-fifo-p1.fifo` | SQS (the `.fifo` one is Stork's **only** FIFO queue) | Stork ticker | ticker workers |
| `prod-stork-sms`, `prod-stork-email`, `prod-stork-whatsapp[-inbound]`, `prod-stork-pushn`, `prod-stork-service-explicit-sms` | SQS | Stork | per-channel workers |
| `stork.webhooks.v1`, `stork.reminders.webhooks.v1`, `partner_webhook_callback_events`, `{sms,whatsapp,email}-delivery-callbacks` | Kafka | Stork | `stork-db-writer` and callback consumers |
| `prod-validation-{citi-penniless-initiate, citi-penniless-inquiry, slice-penniless-initiate, slice-penniless-inquiry, pennydrop-initiate, failure-handling, cache-initiate, citi-rpd-inquiry, rpd-refund-inquiry, webhook-process}`, `prod-account-validation-fts-pennydrop-processor` | SQS | ValidX | the 11 `validx-*-worker` Deployments |
| `prod-api-validation-dual-write-live` | SQS | ValidX (on create and every state change) | api monolith (keeps its FAV tables in sync) |
| `payout-updates-{xpayroll, payout-links, settlements, refunds, vendor-payments, charge-collections, capital-collections, cross-border, petty-cash, generic-accounting}` | **SNS** | payouts `payout_source_updater` worker (`[source_update_topics]`, Splitz `source_updater_sns_experiment`) | the owning product service (`payout-updates-vendor-payments` is shared by `vendor_payments`, `tax_payments`, `vendor_settlements`, `vendor_advance`; `payout-updates-cross-border` by `cross_border` + `ica_transfer`) |
| `prod.x.vendor-payments.accounting-payouts.status-update` | Kafka | vendor-payments | `svc:accounting-integrations` `VendorPaymentUpdateListener` (GroupID `accounting-integrations`) |
| `prod-vendor-advance-event`, `prod-vendor-entity-updates-event` | Kafka | vendor-payments | accounting-integrations |
| `prod-items-update-event`, `prod-file-handler-event`, `prod-initiate-report`, `prod-zoho-invoice-fetch` | Kafka | accounting-integrations (own work queues) | accounting-integrations |
| `prod-gateway-latency-event` | Kafka | mozart (`GATEWAY_LATENCY_KAFKA_TOPIC`) | platform observability |
| `prod-api-rx-{fund-account,contact}-lazy-loading-live` | SQS | api monolith | `cfa-worker-fa` / `cfa-worker-contact` |
| `x-balances-{rbl,icici,yesbank,axis,idfc,slice}-balance-update-live`, `x-balances-rbl-priority-balance-update-live`, `x-balances-payout-event`, `x-balances-account-activation-event` | SQS | x-balances / payouts | the 8 x-balances workers |
| `prod-x-account-statements-{fetch,fetch-ybl,fetch-idfc,fetch-axis,fetch-icici,fetch-slice,save-and-enrich,enrichment,migration,account-activation-event}-sqs`, `prod-x-account-statement-source-event` | SQS | XAS / payouts | the 13 XAS workers |
| `mysql_cdc_events_prod_rx_account_statements_{account_statements,accounts}` | Kafka CDC | Debezium | XAS `statement-cdc` / `accounts-cdc` (10 replicas each, HPA 10/15) |
| `prod-ledger-{journal-create,account-create,balance-update,entry-details-create}-{live,test,pg-live,pg-test,live-mirror,pg-live-mirror}` | SQS (16 names) | ledger producers | ledger workers (6 via `SqsAutoScaler`) |
| `prod-internal_api_api_ledger_outbox`, `mysql_cdc_events_payments_upi_live_ledger_outbox`, + 14 more per-team outbox topics | Kafka CDC | each team's `ledger_outbox` table | the 16 `journal-create-<team>-worker-pg-*` consumers |
| `prod-api-ledger-x-journal-created-live` | SQS | api monolith | ledger (alerted at >25 000 visible messages) |
| `api-ledger-journal-create-live` | **SNS** | api monolith / `sub:monolith-stub` DA ledger emitter | fans out to the ledger `journal_create` SQS queue |
| `rx-fts-status-update-events`, `rx-fts-status-update-retry-events` | Kafka | FTS (Splitz `fire_status_update_kafka`) | payouts Kafka consumers |

---

## 5. Alerts relevant to payouts / FTS stuck-or-failed

Encoded as `ext:alert/<name>` nodes (kind `external`, `owner_domain: platform`, label prefixed `alert:`).

| Alert | Expression (summary) | Sev | File:line |
|---|---|---|---|
| SQS oldest-message age, `prod-payouts-queued-processing-live` | `aws_sqs_approximate_age_of_oldest_message_sum{...} > 600` | critical / 10m | `payouts_rules.yaml:89` |
| Same for `…payout-create-failure-handling-live` / `…payout-update-failure-handling-live` | `> 180` | critical / 10m | `payouts_rules.yaml:243, 265` |
| Process-queued-payout worker errors | `rate(payouts_service_process_queued_payout_error_total[1m]) by (error) > 0` | critical | `payouts_rules.yaml:998` |
| Payout source-updater job failures | `rate(api_payout_source_updater_job_failures_count[5m]) > 20` | critical / 5m | `payouts_rules.yaml:2048` |
| Stuck payouts IDFC IMPS | `sum(payouts_service_stuck_payouts_count{channel="IDFC",mode="IMPS",status="non_terminal"}) > 5` | critical / 15m | `payouts_rules.yaml:2224` (duplicated at `fts_rules.yaml:4250`) |
| Payroll payouts stuck non-terminal | `…{source="xpayroll",status="non_terminal"} > 0` | critical / 1h | `payouts_rules.yaml:2247` |
| Payout SLA breach % (IMPS/UPI, NEFT, settlements) | `payouts_service_payout_sla_breach_percentage > 55`, gated on SLA-monitor cron freshness | info / 15m | `payouts_rules.yaml:1894,1920,1948,1974` |
| FTS→payouts transfer-update webhook failure | `sum(increase(fts_transfer_webhook_update_failure_count{status=~"PROCESSED\|FAILED\|REVERSED"}[15m])) by (status) > 50` | — / 10m | `fts_rules.yaml:2335` |
| FTS failed to publish task | `increase(fts_task_publish_error[1m]) by (task_name,error) > 1` | critical / 1m | `fts_rules.yaml:2424` |
| FTS internal task failure P0 | `increase(fts_internal_task_failure_count{...}[5m]) by (error_type,task) > 0` | critical / 1m | `fts_rules.yaml:2448` |
| FTS stuck transfer rate, AmazonPay | stuck/initiate ratio ≥ 50% over 15m (min volume 30) | info / 15m | `fts_rules.yaml:111` |
| X-Ledger messages stuck in queue | `aws_sqs_approximate_number_of_messages_visible_sum{queue_name="prod-api-ledger-x-journal-created-live"} > 25000` (>50000 in the 08:00–10:30 IST window) | critical / 15m | `ledger_x_create_journal_rules.yaml:5` |
| X-Ledger `JOURNAL_CREATE_FAILED` (+ ~30 `LEDGER_ENTRY_*_FAILED` variants) | per-operation failure counters | — | `ledger_x_create_journal_rules.yaml:60`, `ledger_rules_app.yaml:454-1423` |
| Workflow failed | `increase(workflows_cadence_workflows_failed{env="prod"}[5m]) > 0` | critical / 5m | `workflow_service_rules.yaml:563` |
| Batch failure / created-not-launched | `increase(BATCH_BATCH_BATCH_STATUS_UPDATE_total{status="FAILED"}[5m]) > 1`; `CREATED − Launched > 10` (ART / non-ART / PL-v2) | warning, business_impact p0 / 5m | `batch_metrices_rules.yaml:334, 355, 377, 399` |

**Gap worth naming**: there is **no** alert on a single payout stuck in `initiated`. The only FTS→payouts
status-webhook-loss signal is the aggregate `>50 per 15m` threshold above, and the SLA monitor excludes the
`initiated` status — so a low-volume loss of terminal status updates is invisible to production alerting.

---

## 6. Build / health commands (feed for service recipes)

| Service | Build | Health |
|---|---|---|
| stork | `make build` (`Makefile:100`) → `cmd/api`, `cmd/workers/{webhook,sms,email,whatsapp,pushnotification}`, `cmd/scheduler`, `cmd/dbwriter/webhook`, `cmd/partitioner`, `cmd/migration`, `cmd/replay` | `POST :6060/twirp/rzp.common.health.v1.HealthCheckAPI/Check` (`deployments/probe.sh`) |
| dcs | `go build ./cmd/{server,admin-server,consumer,migrator}` | `GET /ready`, `GET /live` (`dcs.health.v1.HealthService`); grpc `:8080`, http `:8081`, internal `:8082` |
| splitz | `go build ./cmd/...`; local stack `deployments/dev/docker-compose.yml`, `make slit-local` | grpc `common.health.v1.HealthService/Check` |
| governor | `go build .` (`main.go`, flags `-base_path`, `-env`) | `GET status`, `GET ping`, `GET metrics`, `GET commit.txt` |
| charge-collections | `go build ./cmd/...`; `docker-compose.slit-in-process.yml` | `GET /health` (public, no-auth) |
| shield-sdk | `go test ./...` — library, no server | n/a |
| ValidX | `make go-build` → `BINS = server worker migration console`; `make proto-generate` | `GET /ready`, `GET /live`; `:8080` grpc / `:8081` http / `:8082` metrics |
| payments-upi (UPS) | `make build-info pre-build docker-build` | `GET /health` (`HealthQuery.Check`) |
| payout-links | `docker build .` → `ENTRYPOINT deployments/entrypoint.sh {api|migration|webhook}` | `GET /status`, `GET /commit.txt` on `[core].port`; metrics on `[core].metricport` |
| vendor-payments | `make build-vendor-payments|build-worker|build-ocr-worker|build-ap-worker|build-migration` (`Makefile:209-237`) | `GET /status` (web listener) |
| virtual-account | `make build` (`BINS = server worker migration console`, `Makefile:11`) | `common.health.v2` via the twirp/gateway server |
| accounting-integrations | `go build ./cmd/{api,worker,task,cadence,migration}` (`Makefile:150`) | `GET /status`, `GET /ping`, `GET /commit.txt` |

---

## 7. Open questions

1. Production Splitz rollout percentages and per-merchant variants for all 37 payouts experiments exist in no
   readable artefact (DEV-025); the twin resolves each to a fixed fixture, and 34 of 37 ids differ from prod.
2. Every production FastCron cadence for the 9 PS `/v1/cron/*` jobs is unknown (FastCron SaaS); the twin's
   intervals are declared assumptions (DEV-170).
3. `workflows` has no Spinnaker pipeline in `spinacode` and its Cadence cluster has no manifest in
   `kube-manifests` — its production deployment mechanism is visible in neither repo.
4. Every ledger CronJob is committed `suspend: true`, and `verify-warm-storage` does not render at all in prod;
   whether any is un-suspended at runtime is not decidable from the repos.
5. No production alert fires on a single payout stuck in `initiated`.
6. `CreateValidationV2` and the plain `CitiWebhookUpdate` RPC are declared in ValidX's proto but have no server
   handler in the pinned clone — a live contract gap for whoever owns FAV correctness.
7. Whether x-balances' own DCS client wrapper takes the same single-`Modes` branch that made a pristine
   payouts/cfa binary unable to reach a configured `ServerURL` was never traced.
8. The exact wire request `charge-collections-sdk` makes to Governor's `/execute` is unreadable (vendored
   go-module-cache path outside the sandbox), so the fee→rule-chain mapping is inferred from config, not read.
