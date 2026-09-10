# Lane 09 — Merchant webhooks (Stork), Kafka status propagation, proto contracts

## Scope and sources read

Repos opened (pristine shallow clones under `scratchpad/rzp-payouts-architecture/`):
- `stork`: `internal/webhook/{server,core,model}.go`, `internal/webhookch/{client,middlewares,queue}.go`,
  `internal/channel/policy.go`, `internal/config/config.go`, `internal/migrations/0000{1,2,3,4}_*.go`,
  `internal/crypto/*`, `configs/{default,prod}.toml`.
- `proto`: `proto/stork/webhook/v1/{webhook,webhook_api}.proto`, `proto/fts/protocol/*.proto`.
- `payouts`: `pkg/stork/{client,base,config,process_event}.go`, `rpc/stork/webhook/v1/webhook_api.twirp.go`,
  `internal/app/payouts/{webhooks,status,state_machine,core}.go` (core.go partial, ~lines 1400-2250, 5600-5760),
  `internal/app/fundAccountValidation/webhooks.go`, `internal/app/downtimev2/webhooks.go`,
  `internal/app/reversals/reversalViaLedgerService.go`, `internal/app/payouts/processor/payoutViaLedgerService.go`,
  `internal/app/common/appConstants/{webhooks,states,constants,events}.go`, `internal/job/webhook_event.go`,
  `internal/taskHandlers/fts_status_updates{,_retry}.go`, `internal/config/config.go`,
  `config/{default,prod}.toml`.
- `fts`: `internal/transfer/service.go` (~1055-1290, `FireTransferStatusWebhook`, `GetMapFromTransfer`),
  `internal/config/{splitz,kafka}.go`, `internal/providers/kafka/provider.go`,
  `config/env.{default,prod-live,devstack,itf}.toml`.
- `api` (monolith): `app/Models/Merchant/WebhookV2/{Stork,Service}.php`, `app/Http/Controllers/WebhookV2Controller.php`,
  `app/Http/Route.php` (webhook route table, ~lines 943-966).
- `kube-manifests`: `prod/kafka-entity/values.yaml` (topic partitions/replicas, ACLs), `templates/fts1/templates/fts1-live-worker-fire-transfer-status-webhook-deployment.yaml`,
  `templates/{payouts,api,api-workers}/templates/*kafka-fts-status-update*`.
- ENV2_COMPOSE twin: `docker-compose.yml`, `substitutes/stork-capture/server.py`, `substitutes/merchant-webhook-sink/server.py`,
  `substitutes/splitz-stub/CONTRACT.md`, `config/templates/base/{payouts/arena.toml,fts/env.arena.toml}`,
  `verifier/verifiers/test_v21_webhook_completeness.py`, `verifier/verifiers/test_v15_fts_webhook_state_guard.py`,
  `build/apply-arena-patches.sh`, `build/arena-patches/README.md`.
- Prior reports read for corrections: `raw-findings/25_stork_mozart.md` (§A in full), `raw-findings/13_fts_payouts_status_path.md` (in full),
  `UNRESOLVED_QUESTIONS.md` (items 19, 26, 29).

Not opened this pass (out of budget / not needed): `stork/internal/webhookch/{queue,processor}.go` bodies beyond what §A.4
of report 25 already covers (trusted as CONFIRMED-by-prior-pass, spot-checked one claim — backoff formula — below),
`mozart` (out of lane), `dashboard`/`frontend-x`/`x` (RazorpayX dashboard webhook-settings UI — not needed since `api`
backend route is confirmed as the actual Stork caller), `stork/internal/kafka/producers` (Stork's own analytics/DB-writer
Kafka path — not in the payouts/FTS status-propagation path this lane covers).

## Production behaviour

### 1. Merchant-visible events Payouts Service (PS) emits

| Event | Trigger (transition) | Emission mechanism | Fail handling |
|---|---|---|---|
| `payout.initiated` | status enters `created` (semantic: internal status "created" → merchant-facing event name "initiated") | `state_machine.go:18-26` `Enter` hook on `StateCreated` → `FireWebhookEventAsyncForPayout` | fire-and-forget goroutine, 30s ctx timeout, errors only logged (`webhooks.go:20-38,104-130`) |
| `payout.queued` | status enters `queued` OR `on_hold` (both map to same event name) | `state_machine.go:74-81` (`StateQueued`), `:88-97` (`StateOnHold`) | same as above |
| `payout.pending` | status enters `pending` | `state_machine.go:108-115` | same |
| `payout.rejected` | status enters `rejected` | `state_machine.go:99-106` | same |
| `payout.processed` | status enters `processed` (only reachable from `initiated`, `state_machine.go:226`) | `state_machine.go:44-53` | same |
| `payout.reversed` | status enters `reversed` (reachable from 11 different from-states, `state_machine.go:251-254`) | `state_machine.go:55-63` | same |
| `payout.failed` | status enters `failed` (reachable from 9 from-states, `state_machine.go:189-199`) | `state_machine.go:65-72` | same |
| `payout.updated` | UTR changed or status-details/reason changed on an FTS details-update (no payout-status change) | `core.go:2049,2218` (`FireWebhookEventAsyncForPayout(ctx, payout, appConstants.UPDATED)`), gated by `sendWebhookUpdate` bool set at `core.go:2020-2029` (UTR diff) | same |
| `transaction.created` | ledger journal created for a `processed` payout or a `reversed`/reversal | `PushTransactionEvent` (`payoutViaLedgerService.go:273-322`) and reversal equivalent (`reversalViaLedgerService.go:225-303`) | **SYNC primary path**: direct `PushStorkWebhookEvent` call (`core.go:5699-5716`, calls `ProcessEvent` inline); **on failure only**, falls back to `job.QueuePushForWebhookEvent` (async queue, see below) |
| `fund_account.validation.completed` / `.failed` | FAV entity reaches `completed`/`failed` | `fundAccountValidation/webhooks.go:21-33,35-79`, `GetWebhookEventCorrespondingToStatus` (own status→event map in that package) | fire-and-forget goroutine, 30s timeout, fail-open |
| `payout.downtime.started` / `.updated` / `.resolved` | bank/mode downtime state-machine v2 transitions, gated additionally by a Splitz evaluation per merchant | `downtimev2/webhooks.go:20-32,34-...,159-171` (event name = `DowntimeEntityName + "." + eventType`, `DowntimeEntityName="payout.downtime"`, `appConstants/downtimev2.go:27`) | fire-and-forget, 30s timeout, Splitz-eval failure also logged and drops the send (`webhooks.go:88`) |

No `fund_account.created`, `contact.created`, or `payout_link.*` emitters were found in `payouts`; those entities are owned
by other services (fund accounts/contacts by `api`/`x-*`, payout links by `payout-links`) — **UNKNOWN/out of PS scope**,
not a gap in this lane.

**Trigger/guard for `payout.*` map** — `appConstants/webhooks.go:14-24`:
```go
var StatusToWebhookEventMap = map[string]string{
  CREATED: WebhookEventCreated,   // "created"  -> "payout.initiated"
  PROCESSED: WebhookEventProcessed, REVERSED: WebhookEventReversed, FAILED: WebhookEventFailed,
  UPDATED: WebhookEventUpdated, QUEUED: WebhookEventQueued, REJECTED: WebhookEventRejected,
  PENDING: WebhookEventPending, ONHOLD: WebhookEventQueued,
}
```
Statuses **not** in this map (`initiated`, `scheduled`, `batch_submitted`, `cancelled`, `create_request_submitted`,
`ledger_response_awaited`) never fire a merchant webhook — `GetWebhookEventCorrespondingToStatus` returns `""` and
`FireWebhookEventForPayout` returns early (`webhooks.go:70-78`, logs `PayoutEventDoesNotExists`). Confirmed against the
state machine: `StateCancelled.Enter` (`state_machine.go:83-86`) has no `FireWebhookEventAsyncForPayout` call at all.

**Sync-before-fire guard**: `FireWebhookEventForPayout` first blocks on a `chan bool` (`payout.GetIsDBUpdated()`) to confirm
the DB row committed before building the payload; if the channel signals `false` or times out on `ctx.Done()`, the
webhook is **not** sent (`webhooks.go:41-68`). This means the emission is asynchronous relative to the HTTP response but
still gated on DB-commit success — not a pure fire-and-forget-before-commit.

**Payload builder** (`payout.*` family): `dtos.PayoutWebhookResponse.Build()` (`internal/app/dtos/payoutWebhookResponse.go:39-79`)
— fields `id, entity("payout"), fund_account_id, amount, currency, notes, fees, tax, purpose, status(public), utr,
mode, reference_id, narration, batch_id, failure_reason, created_at, error?, status_details?{reason,description},
debit_account_number?` (last one gated by a per-merchant additional-fields context flag). This struct, optionally
encrypted (`payloadcrypt.EncryptInterfaceIfEnabled`, `webhooks.go:92-93` — wraps as `{"data":"<encrypted>"}`), becomes
`entityPayload` in the outer envelope below.

**Outer envelope sent to Stork** — `createProcessEventRequest` (`pkg/stork/process_event.go:113-149`):
```go
type ProcessEventPayload struct {
  Entity    string            `json:"entity"`     // literal "event"
  AccountID string            `json:"account_id"` // "acc_" + merchantID
  Event     string            `json:"event"`      // e.g. "payout.processed"
  Contains  []string          `json:"contains"`    // e.g. ["payout"] or ["transaction"]
  Payload   map[string]Entity `json:"payload"`     // {"payout": {"entity": <PayoutWebhookResponse>}}
  CreatedAt int32             `json:"created_at"`  // time.Now().Unix()
}
```
This whole struct is `json.Marshal`-ed to a **string** and placed in `webhookv1.Event.Payload` (`process_event.go:132-141`)
— i.e. Stork treats the payload as an opaque string it signs but never parses (confirmed at proto level,
`proto/stork/webhook/v1/webhook.proto:64` `string payload = 8;`, and in `stork/internal/webhook/core.go:295-301` which
HMACs `e.Payload` directly).

For `transaction.created`: `PayoutTransactionEventBody`/`ReversalTransactionEventBody` (`process_event.go:47-97`) —
`id, entity("transaction"), account_number, amount, currency, credit, debit, balance, created_at,
source:{id, entity("payout"|"reversal"), fund_account_id|payout_id, amount, notes, fees, tax, status, utr, mode,
created_at}`.

### 2. Stork `WebhookAPI/ProcessEvent` — exact request, wire format, auth, timeout

- Route: `POST {stork.httpClient.host}/twirp/rzp.stork.webhook.v1.WebhookAPI/ProcessEvent`.
- Proto (`proto/stork/webhook/v1/webhook_api.proto:109-115`): `ProcessEventRequest{Event event=1}` →
  `ProcessEventResponse{Event event=1}`; `Event` (`webhook.proto:56-67`): `id, created_at, service, owner_id, owner_type,
  context(map<string,string>), name, payload(string), entity_id`.
- **Wire format = protobuf binary**, not JSON. `payouts/pkg/stork/client.go:105`:
  `webhookClient := webhookv1.NewWebhookAPIProtobufClient(config.Host, requestClient)` — this is the ONLY client the
  pristine `NewClient()` constructs; there is no JSON-client branch in the unmodified repo (`rpc/stork/webhook/v1/webhook_api.twirp.go:101` `NewWebhookAPIProtobufClient` vs `:470` `NewWebhookAPIJSONClient` both exist in generated code, but only Protobuf is wired). **CONFIRMED: production payouts→Stork traffic is protobuf-over-HTTP Twirp**, matching `raw-findings/25_stork_mozart.md` §A.2's conclusion.
- Auth: HTTP Basic, `Authorization: Basic base64(key:secret)`, injected per-request via `twirp.WithHTTPRequestHeaders`
  (`pkg/stork/base.go:27-38`). Prod credentials: `config/prod.toml:118-127` — `stork.httpClient.auth.username="payout"`,
  password from `env|PAYOUTS_STORK_HTTPCLIENT_AUTH_PASSWORD`. Stork server side validates via reflection against
  `boot.Config.Auth.PayoutPassword` (per `raw-findings/25` §A.8, not re-derived here).
- Host: `stork.httpClient.host` — prod `https://stork.razorpay.com` (`config/prod.toml:124`), default/staging
  `https://stork.dev.razorpay.in` (`config/default.toml:174`).
- Timeout: `stork.httpClient.timeout = "30s"` (`config/default.toml:175`), not overridden in `prod.toml` → **30s** in
  prod. (`pkg/stork/config.go:12-14` also sets a Go-level `DefaultTimeout = 30*time.Second` fallback if unset.)
- Service identity sent: `stork.serviceName = "rx-live"` (`config/prod.toml:119`, `default.toml:157`) → this is `Event.service`.

### 3. Stork webhook subscription registration and delivery (production)

**Who calls `Create`**: not `payouts` (no caller found in that repo, confirming `raw-findings/25`'s "gap, not verified" —
now resolved). `api` monolith: route table `app/Http/Route.php:946` `POST webhooks → WebhookV2Controller@create` →
`Service::createForMerchant` (`app/Models/Merchant/WebhookV2/Service.php:156-180`) → `Stork::create()`
(`app/Models/Merchant/WebhookV2/Stork.php:82-99`) → Twirp route `WK_CREATE_ROUTE =
/twirp/rzp.stork.webhook.v1.WebhookAPI/Create` (`Stork.php:59,89`). This is the shared merchant-webhook-settings
endpoint used by both Payments and RazorpayX dashboards (product distinguished by `$product`/`service` field, not a
separate route) — the RazorpayX-specific dashboard frontend was not opened (not needed; `api` is the confirmed caller).
Related routes in the same table (`Route.php:946-958`): edit (`PUT webhooks/{id}`), delete, get, list, analytics,
fetch-events — all through the same `Stork.php` wrapper.

`Create` request fields (proto `webhook_api.proto:50-56` + `webhook.proto:13-34`): `webhook:{service, owner_id,
owner_type, url, secret(5-255 chars per api's PHP-side validation, stork itself: 0-255 via `RawSecret` length rule
`stork/internal/webhook/model.go:66-71`), subscriptions:[{eventmeta:{name}}], request_headers:[{header_key,header_value}],
alert_email, context}`. **One `Subscription` row per (webhook, event_name) pair** — confirmed at both proto
(`webhook.proto:37-41`) and migration level (`00002_create_subscriptions_table.go:15-27`, `webhook_id`,
`event_name VARCHAR(255)`, no compound-unique constraint visible, so duplicate identical subscriptions are not
DB-prevented — CONFIRMED, corroborating `raw-findings/25` §A.3).

**MySQL tables** (`stork/internal/migrations/`):
- `webhooks` (00001): `id CHAR(14) PK, created_at/updated_at/deleted_at INT(11), service VARCHAR(255), owner_id
  CHAR(14), owner_type VARCHAR(255), context TEXT, disabled TINYINT(4) DEFAULT 0, disabled_at INT(11), url
  VARCHAR(255), secret VARBINARY(2048)` (AES-encrypted at rest, `internal/crypto/crypto.go`), indexed on
  `(updated_at)`, `(deleted_at)`, `(service,owner_id)`.
- `subscriptions` (00002): `id CHAR(14) PK, created_at/updated_at/deleted_at, webhook_id CHAR(14) NULL, event_name
  VARCHAR(255)`, indexed on `webhook_id`.
- `messages` (00003): `id CHAR(14) PK, created_at, updated_at, service, owner_id, owner_type, event_id CHAR(14)
  DEFAULT NULL (no unique constraint), context TEXT, backoff_policy JSON`.
- `message_channels` (00003): `id CHAR(14) PK, backoff_policy JSON, request JSON, channel_type CHAR(25) DEFAULT
  "WEBHOOK", status CHAR(255) DEFAULT "CREATED", next_attempt_at INT(11), last_queued_at INT(11), message_id CHAR(14)
  FK→messages`, indexed on `next_attempt_at`, `created_at`, `status`.
- `attempts` (00004): `id CHAR(14) PK, request JSON, response JSON, payload JSON, response_code INT(5), delay
  INT(8), duration INT(8), channel_type CHAR(25) DEFAULT "WEBHOOK", message_channel_id CHAR(14) FK→message_channels`,
  indexed on `message_channel_id`, `channel_type`.

**Delivery request** (`stork/internal/webhook/core.go:235-330`, `getMessage`/`getWebhookRequestHeaders`):
- `Method: POST`, `Url: w.URL` (merchant's registered URL), `Payload: e.Payload` (the raw JSON string built by the
  caller, verbatim — Stork does not re-serialize it).
- Headers (always): `User-Agent: Razorpay-Webhook/v1`, `Content-Type: application/json`, `X-Razorpay-Event-Id: {event.id}`
  (Stork-generated id, not caller-supplied), `Request-Id: {event.id}`.
- Header (conditional on webhook having a secret): `X-Razorpay-Signature: hex(HMAC-SHA256(secret, payloadForSignature))`
  where `payloadForSignature` = `e.Payload` by default, optionally slash-unescaped for a hardcoded merchant allowlist
  (`core.go:288-309`, `shouldSanitizePayloadForSignatureGeneration`). **CONFIRMED**: signature is over the raw delivered
  body, not `timestamp.body` (unlike some other Razorpay webhook families).
- Then any merchant-configured custom `request_headers` are appended, cannot override the reserved names
  (`model.go:23-42` `reservedRequestHeaders` set: `accept, accept-*, cache-control, connection, content-length,
  content-md5, content-type, dnt, expect, host, origin, referer, user-agent, x-forwarded-for, x-razorpay-event-id,
  x-razorpay-signature, request-id`).

**Retry / backoff** — exponential, `BackoffPolicy` proto attached per-message (`core.go:764-775`); values from
`stork/configs/default.toml:961-970` (not overridden in `prod.toml`, confirmed by grep): `DefaultFactor=5,
DefaultMaxTime=86400 (24h, seconds), DefaultMaxAttempt=15`. Formula, read directly from `stork/internal/channel/policy.go:65-79`:
```go
backoffSeed = 2^attemptedCount / 2   // exponential
delay = duration * backoffSeed * factor
nextAttemptAt = now + delay
```
capped by `maxAttempts`/`allowedTimestamp` (message's `created_at + maxTime`); once either bound is exceeded, status
→ `EXCEEDED` (terminal, no further attempts) rather than `FAILED` (retry scheduled). This **CONFIRMS** (does not
correct) `raw-findings/25_stork_mozart.md` §A.4's stated formula. Extended-retry owner IDs get
`ExtendedRetryMaxTime=259200 (72h), ExtendedRetryMaxAttempt=23`, switching from exponential to a fixed
`ExtendedRetryFixedInterval=21600 (6h)` schedule once `ExtendedRetryFixedIntervalStartTime=86400 (24h)` elapses
(`policy.go:93-109`); owner IDs themselves are real merchant ids and are **redacted** per hard rule 2 (type: merchant ID
list, `stork/configs/default.toml:967`).

**Dedupe: none.** No unique constraint on `messages.event_id`; grepped `internal/webhook`, `internal/webhookch` for
`dedup*`/`idempoten*` in this pass — none found, consistent with prior report. If a caller (payouts) invokes
`ProcessEvent` twice for logically the same business event (e.g. the async-queue fallback retry after a sync call that
actually succeeded server-side but whose response was lost — see §1, `transaction.created` fallback), Stork has no
mechanism to detect the duplicate; two independent deliveries fire with two different Stork-generated
`X-Razorpay-Event-Id` values.

**Ordering: none guaranteed** for regular webhook delivery (non-FIFO SQS `p0/p1/p2` per `raw-findings/25` §A.4/A.9, not
re-derived from `stork/internal/channel/queue.go` in this pass — trusted).

**Disable rule** (`stork/internal/webhook/core.go:360-506`) — **not** a live failure-counter in the hot delivery path; it
is two separate **daily cron jobs** (`DisableMany`, `DisableLowVolumeWebhooks`, both proto RPCs
`webhook_api.proto:44-45`) that read pre-computed lists of failing `(webhook_id, owner_id)` from **Trino** tables
(`retrieveFailedWebhookIdsByTable`, `core.go:332-357`) populated by an **Airflow DAG** (comment, `core.go:360-362,400-402`):
one table for "24h all-failed" webhooks, one (`webhook_event_low_volume`) for "persistent low-volume failures". For each
candidate, `disableWebhookWithConditon` (`core.go:456-477`) skips disabling if: disabled in the last 24h, created in the
last 24h, or already disabled (comments document exactly these three reasons, `core.go:442-455`). If disabled, an email
is sent via `apiservice.SendEmail(ctx,"deactivate",...)` (`core.go:479-503`) to `webhook.alert_email` and the webhook is
persisted `Disabled=true, DisabledAt=now`.

### 4. Kafka: FTS→PS status propagation

**Selector — Kafka vs HTTP webhook, per transfer, mutually exclusive** (`fts/internal/transfer/service.go:1136-1141`,
inside `FireTransferStatusWebhook`):
```go
splitzResponse := splitZGet(ctx, transferModel.MerchantID, config.GetConfig().Splitz.Experiments.FireStatusUpdateKafka)
if splitzResponse {
    producerName := config.GetConfig().KafkaProducers.FireTransferStatus.Name
    key := fmt.Sprintf("%v", transferMap[AttributeSourceID])   // = payout id
    go kafka.PushMessageWithKey(ctx, producerName, transferMap, key)
    return ierr    // <-- early return: HTTP webhook code below never executes
}
```
This directly answers `UNRESOLVED_QUESTIONS.md` item 19 ("mutually exclusive?") — **yes, exclusive per transfer**,
gated by a per-merchant Splitz experiment `fire_status_update_kafka` evaluated at delivery time (not per-topic
fan-out to both). Which merchants get which path is a live Splitz-assignment fact **not derivable from any repo**
(Splitz experiment/variant DB rows are runtime data) — see Cannot-derive list.

**Message body**: `transferMap` = `service.GetMapFromTransfer()` (`service.go:1306-1346`) — the same map used to build
the HTTP webhook body: `ConvertStructToStringMap(transferModel)` (all `Transfer` ORM fields) plus computed
`extra_info, fund_transfer_id(=id), gateway_ref_no, status_details, status(external-mapped), bank_account_type`.
`PushMessageWithKey` (`fts/internal/providers/kafka/provider.go:104-134`) JSON-marshals this map as the Kafka value and
sets the **partition key = source_id** (the payout id) via `producer.SendWithKey(jsonMsg, key)`.

**Topic**: `rx-fts-status-update-events` (FTS producer, prod: `fts/config/env.prod-live.toml:315-331`
`[kafka_producers.fire_transfer_status]` `enabled=true`, `topic="rx-fts-status-update-events"`,
`brokers=["prod-noncde-kafka.razorpay.com:9090"]`, TLS on, `compression_type="snappy"`, `max_retry=10`; consumed by
payouts, `config/prod.toml:311-317` `[kafka_consumers.status_update_consumer]` `Topics=["rx-fts-status-update-events"]`,
`ConsumerGroup="rx-payouts-fts-status-update-consumer-group"`, `RetryBackoff=1, MaxRetry=1`, TLS on). Kube ACL/topology
(`kube-manifests/prod/kafka-entity/values.yaml:8725-8727`): **2 partitions, 3 replicas**; ACL grantee `payouts-rx-user`
(`:5206-5220`) has Read/Write on the topic and on both consumer-group name prefixes; `fts-rx-user` (`:5171-5186`) has
Read/Write/Describe/Create on both topics (producer side). A separate `api` monolith consumer
(`kube-manifests/templates/api/templates/workers/api-kafka-fts-status-update-consumer.yaml`, deployment
`api-kafka-fts-status-update-consumer`) exists on presumably the same topic family for non-PS-origin (monolith-created)
transfers — not traced into api's own consumer-group name in this pass (would double-process if same group as payouts';
almost certainly a distinct group, consistent with FTS's dual `origin_service` routing in report 13 finding 15 — **not
independently re-verified here**, flag as unresolved).

**Retry topic**: `rx-fts-status-update-retry-events` (`kube-manifests/…:8728-8730`, same 2 partitions/3 replicas).
Payouts consumer group `rx-payouts-fts-status-update-retry-consumer-group`, `RetryBackoff=30, MaxRetry=2`
(`config/prod.toml:318-326`). Payouts is BOTH a consumer (retry re-processing) and, per its own
`[topics].fts_status_updates_retry = "rx-fts-status-update-retry-events"` config key
(`internal/config/config.go:41,370-372`, `config/prod.toml:362-363`) and `provider.GetKafkaProducerProvider(ctx).PublishWithKey(...)`
call (`internal/taskHandlers/fts_status_updates.go:105-108`), the **producer** onto this same retry topic — i.e. the
retry topic is populated by payouts itself when its own consumer's `HandleFailedMessage` fires (main consumer
`MaxRetry=1`, so essentially any processing error routes straight to the retry topic with the original message
re-keyed by `reqInput.SourceID`), not by FTS. Kafka producer defaults used for this: `config/prod.toml:341-349`
`[kafka_producer]` `Partitioner="random", MaxMessages=100, CompressionType="snappy"`.

**Consumer dedupe/idempotency**: none at the Kafka layer itself (no message-id tracking found in
`taskHandlers/fts_status_updates{,_retry}.go`); idempotency instead comes from the **payout-status guard** shared with
the HTTP path (`AllowedStateTransitionForTransferWebhook`/terminal-repeat no-op, per `raw-findings/13` findings 4-5,
which the Kafka consumer reaches via the same `PayoutKafkaHandler.HandlePayoutStatusUpdateViaFTS` / legacy switch,
`fts_status_updates.go:64,75`).

**"Drops reversed/failed" — CONFIRMED with exact lines**, identical logic in both consumers:
```go
// payouts/internal/taskHandlers/fts_status_updates.go:58-62
inputStatus := strings.ToLower(reqInput.Status)
// api code has very tight coupling between api, payouts service and ledger for handling reversed updates.
// So currently api will be the source of truth for reversed status.
if inputStatus == appConstants.StateReversed || inputStatus == appConstants.StateFailed {
    return nil
}
```
and identically at `fts_status_updates_retry.go:58-64`. `return nil` acks the Kafka message (no redelivery) with zero
side effects — the transition is silently skipped. `processed`, `pending`, `initiated`, etc. all flow through normally
to `HandlePayoutDetailsUpdateViaFTS`/`HandlePayoutStatusUpdateViaFTS` (the same legacy handler the HTTP-relay path uses,
per `raw-findings/13`).

**Proto messages**: `proto/fts/protocol/protocol.proto`, `protocol/v1/{single_request,single_response}.proto`,
`protocol/v2/{bulk_request,bulk_response}.proto`, `protocol/common/common.proto` exist in the `proto` repo but were
**not** found referenced by the Kafka producer/consumer code read in this pass — the actual Kafka message on
`rx-fts-status-update-events` is a **plain JSON map** (`json.Marshal(transferMap)`, no protobuf envelope), consumed via
`json.Unmarshal([]byte(msg), reqInput)` into `dtos.StatusUpdateRequest`/`DetailsUpdateRequest` structs
(`taskHandlers/fts_status_updates.go:44-56`) — **CORRECTION-flavored note**: the `proto/fts/protocol/*` messages are a
different (gRPC/single-transfer synchronous request-response) contract, not the Kafka status-update wire format; do not
conflate the two when building a substitute.

## Corrections to existing reports

| Report | Claim | What source says | Evidence |
|---|---|---|---|
| `raw-findings/25_stork_mozart.md` opening line ("Twirp RPC (JSON-over-HTTP…)") | Reads as if the wire format is JSON | Twirp supports both; production payouts client is the **Protobuf** twirp client (`webhookv1.NewWebhookAPIProtobufClient`) — the report's own §A.2/§A.8 later correctly says this, so this is a wording ambiguity in the opening summary, not a factual error in the body. No correction needed to the report's substantive claims, but flagging so a reader doesn't stop at the opening line. | `payouts/pkg/stork/client.go:105` |
| `raw-findings/25_stork_mozart.md` §A.3 | "No caller of `Create`/`CreateWebhookRequest` exists in the `payouts` repo… presumably in `api` monolith… **Gap, not verified.**" | **Resolved, not a gap**: `api/app/Http/Route.php:946` → `WebhookV2Controller@create` → `WebhookV2/Service::createForMerchant` → `WebhookV2/Stork::create()` → Twirp `Create`. | `api/app/Models/Merchant/WebhookV2/{Stork.php:59,82-99,Service.php:156-180}`, `api/app/Http/Route.php:946-958` |
| `UNRESOLVED_QUESTIONS.md` item 19 | "Are `FireStatusUpdateKafka` and the HTTP webhook mutually exclusive per transfer, and which merchants are on each?" | **Resolved (first half)**: yes, mutually exclusive — Splitz-gated early return before any HTTP-request code runs. "Which merchants" is genuinely a live-data question, not resolvable from repos (Cannot-derive list). | `fts/internal/transfer/service.go:1136-1141` |
| `raw-findings/13_fts_payouts_status_path.md` Q4 | "Did not verify whether `FireStatusUpdateKafka` and direct-HTTP-webhook are mutually exclusive per transfer…" | Same resolution as above — closes Q4. | same as above |
| `raw-findings/13_fts_payouts_status_path.md` §2a sequence diagram | Shows `FTS->>PS: Kafka: rx-fts-status-update-events` as an "Alternative delivery" alongside the HTTP path in the same diagram flow, without stating exclusivity | Now confirmed as **either/or per transfer**, not "also" — diagram wording is consistent with this reading but doesn't say so explicitly; no factual error, just under-specified. | as above |
| None found | — | Backoff formula, max-30-webhooks, disable-cron mechanics, dedupe/ordering claims in `raw-findings/25` §A.3/A.4 all **independently re-derived and CONFIRMED** in this pass (see §"Production behaviour" 3 above) — no corrections needed. | see citations above |

## Twin comparison

| Component | Production mechanism | Twin mechanism (file) | Verdict | What differs | Controls |
|---|---|---|---|---|---|
| Stork `WebhookAPI` (subscriptions + delivery) | Go service, MySQL+Redis+SQS+Trino+Kafka, protobuf Twirp, HMAC-SHA256 signed, exponential backoff (15 attempts/24h), fan-out via async SQS workers | `substitutes/stork-capture/server.py` (235 lines) — in-process dict store for webhooks, synchronous inline delivery on `ProcessEvent`, JSON Twirp routes, real HMAC-SHA256 signing over the raw payload string, real header names (`X-Razorpay-Event-Id`, `X-Razorpay-Signature`), `MAX_WEBHOOKS_PER_OWNER=30` matching prod's documented cap | **CONTRACT-FAITHFUL SUBSTITUTE** | No retry/backoff (delivers once, no re-attempt on non-2xx), no dedupe simulation by default, no per-owner disable cron, no MySQL persistence (in-memory, lost on restart), sync delivery vs prod's async-queue delivery (explicitly documented in the file's own docstring) | merchant-visible state (webhook delivery), not auth/tenant/ledger |
| Stork wire protocol (protobuf vs JSON twirp) | Protobuf-encoded Twirp body | stork-capture parses `json.loads(body)` only — no protobuf decoding | **twin requires the `ARENA_STORK_JSON=1` patch to `payouts/pkg/stork/client.go`** (swaps to `NewWebhookAPIJSONClient`) — **patch is NECESSARY, not a superfluous deviation**: without it, payouts sends binary protobuf bytes that stork-capture's `json.loads` cannot parse (immediate 400/decode error on every call) | Patched client uses `webhookv1.NewWebhookAPIJSONClient` (generated, exists in the same `rpc/stork/webhook/v1/webhook_api.twirp.go:470`, so this is Twirp's own supported alternate content-type, not a hand-rolled shim) | delivery correctness only; does not affect payload field shape, since both clients marshal the same Go struct, just to a different content-type |
| Stork ordering/reordering simulation | No FIFO guarantee, no documented deliberate-reorder knob (production ordering is "accidental/rare" per report 25 §A.4) | `STORK_REORDER=1` env flag delays every even-indexed delivery per owner_id by `STORK_REORDER_DELAY_SEC` (default 0.3s) so odd-indexed lands first (`server.py:28-35,174-182`) | **REPRESENTATIVE SUBSTITUTE** (deliberately reproduces a real-but-rare production condition, on demand) | Production's lack-of-ordering is probabilistic/SQS-driven; twin's is deterministic and index-parity-based — good enough to exercise V21-style tests, not a byte-for-byte model of SQS concurrency | idempotency/reversal (tests merchant-side dedupe handling) |
| Stork subscription `Create`/`List` | MySQL `webhooks`/`subscriptions` tables, proto `Webhook`/`Subscription` shapes | `_create_webhook`/`_list_webhooks` (`server.py:89-121`) build the same field names (`id, service, owner_id, owner_type, url, secret, subscriptions[event_name...], disabled`), return `secret_exists` bool like prod's `toProto()` (never echoes raw secret in `_list_webhooks`, but `_create_webhook` also strips it, matching prod convention) | **CONTRACT-FAITHFUL SUBSTITUTE** | No DB persistence, no `request_headers`, no `alert_email`, no per-owner max-30 enforcement beyond a literal count check (matches prod's *behavior*, not its *code path*) | merchant-visible subscription registration |
| Merchant HTTP endpoint receiving deliveries | Arbitrary merchant server, verifies `X-Razorpay-Signature` itself | `substitutes/merchant-webhook-sink/server.py` — computes `hmac.new(WEBHOOK_SECRET, body, sha256)` and compares (`hmac.compare_digest`) against the received `X-Razorpay-Signature`, logs every delivery with headers+parsed body to `/data/deliveries.jsonl` | **CONTRACT-FAITHFUL SUBSTITUTE** for the "merchant" side of the contract | Path shape `/webhook/{merchant_id}` is a twin convention (`server.py:33-37` task-spec), not derived from a real merchant integration guide (merchants choose their own URL in production) | merchant-visible state (verification correctness testable) |
| Kafka broker | AWS-managed Kafka cluster (per env), TLS, 2 partitions/3 replicas for the fts-status topics | `apache/kafka:3.8.0` KRaft single-node, no TLS, `KAFKA_AUTO_CREATE_TOPICS_ENABLE=true` (`docker-compose.yml:280-297`) | **REAL** (same broker software, real wire protocol) — not a substitute at all, just unscaled/untenanted/no-TLS | Single partition topology in practice (auto-create defaults, not explicitly 2-partition/3-replica like prod) — does not change consumer-group semantics for a single-node dev topology, but a multi-payout-in-flight ordering test needs to know prod uses 2 partitions (same-partition-key ordering only guaranteed within a partition) | payout/transfer status state (Kafka path) |
| Payouts Kafka consumers (`fts_status_updates`, `fts_status_updates_retry`) | Real Go binary `payouts-kafka-consumer`, same topic/group/backoff config as prod (scaled per env) | `docker-compose.yml:857-905` runs the **real** `payouts-kafka-consumer` binary against the arena Kafka broker, config copied from `config/devstack.toml` with TLS off (`config/templates/base/payouts/arena.toml:1047-1084`) | **REAL** | None functionally — same code, same topic names, same consumer-group names, same reversed/failed-drop logic | payout status state, idempotency (via state-transition guard, not Kafka-level) |
| FTS→PS Kafka **producer** side | `fts` service, Splitz-gated `FireStatusUpdateKafka`, real `fire_transfer_status` Kafka producer | `[kafka_producers.fire_transfer_status].enabled = false` in the twin's FTS config (`config/templates/base/fts/env.arena.toml:156-158`), and splitz-stub returns `off` for `FireStatusUpdateKafka` (`substitutes/splitz-stub/CONTRACT.md:70`) | **MISSING / INERT** — the Kafka consumer infrastructure is REAL and running, but **nothing produces onto `rx-fts-status-update-events` in the twin today**; the FTS→PS status path in the twin always takes the HTTP-webhook branch (`[webhook.payout.transfer_status]` → `monolith-stub` → `payouts-api PATCH /v1/payouts/update_payouts_with_fts`, "Flow I", `env.arena.toml:171-172`) | Twin cannot currently exercise the Kafka status-propagation path end-to-end (FTS side) even though the consumer side is real and correctly wired — this is a genuine fidelity gap for any Kafka-path verifier | payout/transfer status state (Kafka leg untestable as-is) |
| V21 verifier (webhook completeness) | n/a (test code) | `verifier/verifiers/test_v21_webhook_completeness.py` — hits `stork_capture_client.get("/v1/captured")`, marked `BLOCKED_BY_FIDELITY_GAP`, docstring claims stork-capture "documents only SMS/email capture, not a Twirp WebhookAPI/ProcessEvent (payout.*) capture endpoint" | **STALE relative to current stork-capture** | Current `server.py` (this pass's read) DOES implement `_process_event` at the real Twirp route and exposes captured payout-webhook deliveries at `/_captured/events` and `/_arena/events?merchant=` (`server.py:210-221,224-232`) — the verifier is calling the wrong path (`/v1/captured` instead of `/_captured/events`) and its skip/degrade reasoning predates the current stub. This verifier should be revisited/fixed rather than trusted as still `BLOCKED_BY_FIDELITY_GAP`. | merchant-visible webhook completeness (test correctness) |

## Recommendation: real vs substitute

| Component | Real or substitute | Why |
|---|---|---|
| Stork (`WebhookAPI`, delivery workers, disable-crons) | **SUBSTITUTE** (current: `stork-capture`) | Real Stork needs MySQL + Redis + SQS + SNS + Trino coordinator (`trino-adhoc-coordinator.de.razorpay.com`, an internal-network-only host, `stork/configs/default.toml:1082`) + Kafka + Splitz — too many AWS/internal-only deps for a local arena. Contract: implement `POST /twirp/rzp.stork.webhook.v1.WebhookAPI/{Create,List,ProcessEvent}` (JSON body while `ARENA_STORK_JSON=1` is set, else must decode protobuf), persist `{id,service,owner_id,owner_type,url,secret,subscriptions[],disabled}`, on `ProcessEvent` match `event.name` against each owner's active, non-disabled subscriptions and `POST` the raw `event.payload` string to `webhook.url` with headers `Content-Type: application/json`, `X-Razorpay-Event-Id`, `Request-Id` (both = a stub-generated event id), and, if `webhook.secret` set, `X-Razorpay-Signature: hex(hmac_sha256(secret, raw_payload_bytes))`. Status codes: 200 on success (even if downstream delivery fails — Stork's own RPC succeeds independently of delivery outcome), 4xx with a JSON error body on validation failure. No timing/retry fidelity required unless a test specifically needs it (current stub doesn't retry). |
| Stork SMS/Email `Send` | **SUBSTITUTE** (current: `stork-capture`, same file) | Same reasoning; contract is `POST /twirp/rzp.stork.{sms,email}.v1.{SMSAPI,EmailAPI}/Send` → `{message_id, service, owner_id, owner_type, context}`, capture-only (no real SMS/email delivery needed for PS-behaviour tests). |
| Merchant webhook receiver | **SUBSTITUTE** (current: `merchant-webhook-sink`) | There is no "real" merchant to run; a receiver that verifies HMAC and logs deliveries is the correct and only sensible fidelity target. Contract: `POST /webhook` (or `/webhook/{merchant_id}`), verify `X-Razorpay-Signature` against a known `WEBHOOK_SECRET` via `hmac.compare_digest`, always `200 {"received": true}`, log every delivery for assertion. |
| Kafka broker | **REAL** (current: `apache/kafka:3.8.0`) | Already real; no reason to substitute — Kafka wire protocol is exactly what production speaks, and a real broker is cheap to run locally. |
| Payouts Kafka consumers | **REAL** (current: real binary) | Already real; this is the actual production Go code compiled and run against the arena broker — highest fidelity possible for this leg. |
| FTS→Kafka producer leg | **Currently missing — should be REAL, not substituted** | FTS's own Kafka-producer code (`fts/internal/providers/kafka/provider.go`) is already vendored into the twin's FTS binary (whichever binary composes `docker-compose.yml`'s `fts-*` services); the gap is purely **config** (`enabled=false` + splitz-stub returning `off`). Recommendation: add a config profile / splitz-stub fixture that flips `FireStatusUpdateKafka → on` for a designated test merchant so the twin can exercise the Kafka leg end-to-end using the already-real FTS and payouts binaries — this needs no new substitute code, only a config/seed change (out of this lane's read-only scope to implement, but the "how" is fully specified here). |

## Synthetic data

| family/table | field | source evidence | type+length | constraints | allowed values | FK/relationships | state rules | distribution matters? | generation rule | tier |
|---|---|---|---|---|---|---|---|---|---|---|
| `webhooks` | id | `stork/internal/migrations/00001_create_webhooks_table.go:16` | CHAR(14) | PK, `wh_` + random per Stork's `uniqueid` lib convention (not verified in this pass) | any 14-char id | — | — | no | `wh_` + 14 random alnum (twin already does `wh_ARENA%06d`) | EXACT (schema), REPRESENTATIVE (id format) |
| `webhooks` | owner_id | `:20` | CHAR(14) | required, `IsRzpID` validated (`webhook/model.go:66`) | merchant id format | logically FK to `payouts`/merchant | — | no | reuse seeded merchant id | EXACT |
| `webhooks` | service | `:18` | VARCHAR(255) | required | e.g. `rx-live` (`payouts/config/prod.toml:119`) | — | — | no | literal `rx-live` for PS-origin fixtures | EXACT |
| `webhooks` | url | `:24` | VARCHAR(255) | required, `IsURLValidAndHostPublic` (`model.go:67`) | any public-looking URL | points at `merchant-webhook-sink` in twin | — | no | `http://merchant-webhook-sink:PORT/webhook/<owner_id>` | ASSUMED (twin convention, not a real merchant URL contract) |
| `webhooks` | secret | `:25` | VARBINARY(2048), AES-encrypted at rest in prod | 5-255 chars raw (api-side rule, `raw-findings/25` §A.3) / 0-255 (stork-side, `model.go:69`) | any string | — | — | no | random 32-hex string, e.g. `10000000000000` style obviously-fake token | REPRESENTATIVE |
| `subscriptions` | event_name | `00002_create_subscriptions_table.go:22` | VARCHAR(255) | required | one of the `payout.*`/`transaction.created`/`fund_account.validation.*`/`payout.downtime.*` names in §"Production behaviour" 1 | logically must match `Event.name` sent by caller for a delivery to match | one row per (webhook, event_name) | no | pick from the exact event-name list above (do not invent event names) | EXACT |
| `Event` (wire, not a table) | id, service, owner_id, owner_type, context, name, payload, entity_id | `proto/stork/webhook/v1/webhook.proto:56-67` | proto message | `payload` is a JSON string, opaque to Stork | `owner_type` observed value: `"merchant"` (`payouts/pkg/stork/process_event.go:22,137`) | `owner_id` = merchant id | — | no | build per the `ProcessEventPayload`/`PayoutTransactionEventBody` shapes documented above | EXACT (shape), REPRESENTATIVE (values) |
| Kafka msg on `rx-fts-status-update-events` | (flat JSON map, no envelope) | `fts/internal/providers/kafka/provider.go:121-126`, `fts/internal/transfer/service.go:1306-1346` | JSON object, key = `ConvertStructToStringMap(fts.Transfer)` fields | consumed into `payouts/internal/app/dtos.StatusUpdateRequest{SourceID,Status,FailureReason,BankStatusCode,FtsFundAccountId,FtsAccountType,FtsStatus}` + `DetailsUpdateRequest` (per `raw-findings/13` finding 22) — both unmarshalled from the **same** message | `status` ∈ {`processed`,`failed`,`reversed`,`initiated`,...} (external-mapped, `getExternalTransferStatus`) | `source_id` = payout id (no `pout_` prefix per FTS's internal convention — verify against `raw-findings/13`'s DTO before assuming) | consumer drops `reversed`/`failed` unconditionally | yes — a substitute/seed generator exercising V21/V15-adjacent Kafka tests must be able to emit both dropped and non-dropped statuses | mirror `dtos.StatusUpdateRequest` field names exactly; use fake but well-formed `source_id`/`utr` | REPRESENTATIVE (full DTO field list is in report 13, not re-derived line-by-line here) |
| Kafka partition key | = `SourceID`/payout id (Kafka) | `fts/internal/transfer/service.go:1140`, `fts/internal/providers/kafka/provider.go:104,126` | string | — | — | — | same-key messages land on the same partition (2 partitions in prod) → same-payout ordering preserved within a partition, cross-payout ordering not guaranteed | yes, for ordering tests | key generator: reuse the same `source_id` for all messages about one payout, vary it across payouts to exercise multi-partition interleaving | EXACT |
| Extended-retry owner allowlist | `ExtendedRetryOwnerIDs` | `stork/configs/default.toml:967` | `[]string`, 4 entries | real production merchant ids | — | — | — | no | **do not reproduce these ids** — use synthetic ids for any extended-retry-schedule test fixture | type: real merchant id list, REDACTED (hard rule 2) |

## Cannot be derived from repositories

1. **Which merchants have `FireStatusUpdateKafka` (Splitz experiment `fire_status_update_kafka`) turned on, and what
   fraction of prod traffic uses the Kafka vs HTTP-webhook leg.** Why it matters: determines whether the Kafka
   consumer-drop-on-reversed/failed behaviour is a live production concern for a material share of merchants, or a
   rarely-exercised code path. Likely owner: FTS/Payouts platform team, or whoever owns the Splitz experiment
   dashboard. Minimal request: a Splitz experiment export (variant assignment %, not per-merchant PII) for
   `fire_status_update_kafka`, or confirmation it is 0%/100%/staged-rollout today.
2. **`api` monolith's Kafka consumer-group name for `api-kafka-fts-status-update-consumer`.** Why it matters: to
   confirm it does NOT share a consumer group with `rx-payouts-fts-status-update-consumer-group` (if it did, the two
   services would split partition assignment and silently miss half of each other's relevant messages — a correctness
   bug, not by design). Likely owner: API/FTS platform team. Minimal request: the `api` repo's kafka-consumer config
   file (same shape as `payouts/config/prod.toml`'s `[kafka_consumers]` block) — sanitized config export is sufficient.
3. **Trino table schemas** `hive.stork.<webhook_disable_table>` and `hive.stork.webhook_event_low_volume` (names not
   fully resolved in this pass — `boot.Config.WebhookDisabler.Trino.TableName`/`LowVolumeTableName` are config keys,
   literal table names not found in the `stork/configs/*.toml` files read). Why it matters: without the exact columns
   (`webhook_id, owner_id` confirmed via the `SELECT` in `retrieveFailedWebhookIdsByTable`, but nothing else), a
   substitute cannot faithfully seed "this webhook should get auto-disabled" test fixtures. Likely owner: Stork/data
   platform team (Airflow DAG owner). Minimal request: schema-only DDL or a `SELECT * LIMIT 0` column list for both
   tables, plus the Airflow DAG's population query (to know what "failing" means quantitatively — e.g. 0 successes in
   24h vs a success-rate threshold).
4. **`WebhookCh.GetHTTPTimeout()`/`GetTickerHTTPTimeout()`/`GetRemindersHTTPTimeout()` numeric defaults** — flagged
   already in `raw-findings/25` §A.4 as not located in the TOML in the time available; not re-derived in this pass
   either (out of this lane's core scope, but relevant to a substitute's delivery-timeout fidelity). Likely owner:
   Stork team. Minimal request: the relevant `[webhookCh]`/`[ticker]`/`[reminders]` TOML block from `stork/configs/prod.toml`
   or `default.toml` (a sanitized config export is sufficient, no secrets involved).
5. **Real per-partition assignment behaviour for `rx-fts-status-update-events` under the 2-partition/3-replica prod
   topology** when both `api` and `payouts` consumer groups are active simultaneously — this is a live cluster fact
   (rebalance behaviour, lag per partition), not derivable from static config. Likely owner: Kafka/platform SRE
   (`#gandalf` per `UNRESOLVED_QUESTIONS.md` item 29's suggested channel). Minimal request: Grafana/Prometheus lag
   dashboard link or a snapshot of consumer-group lag by partition for both groups.

## Fidelity tier verdicts

- Stork `WebhookAPI` (Create/List/ProcessEvent contract) — **CONTRACT-FAITHFUL SUBSTITUTE**: `stork-capture` correctly implements route shapes, HMAC-SHA256 signing, header names, and the 30-webhooks-per-owner cap; misses retry/backoff/disable-cron entirely (acceptable — those aren't PS-behaviour-relevant for most tests) and requires the `ARENA_STORK_JSON` client patch to bridge protobuf-vs-JSON, which is a justified, documented, necessary deviation.
- Merchant webhook receiver — **CONTRACT-FAITHFUL SUBSTITUTE**: `merchant-webhook-sink` correctly re-derives and checks the HMAC signature the same way a real merchant would.
- Kafka broker + payouts Kafka consumers (`fts_status_updates`, `fts_status_updates_retry`) — **REAL**: actual broker software and actual compiled payouts consumer binaries, config mirrors prod topic/group names with TLS off.
- FTS→Kafka producer leg (the other half of the same pipe) — **UNKNOWN-BLOCKED / MISSING** in current twin wiring: real code exists (already vendored in the FTS binary) but is configured `enabled=false` and gated further by a splitz-stub fixture returning `off`; the single most important reason this is not yet REAL end-to-end is a config/seed gap, not a code gap — flipping two config values (`fts` producer `enabled=true` + a splitz-stub variant override) would make it REAL with no new substitute code.
- Reversed/failed-drop behaviour in the Kafka consumer — **REAL** (twin runs the actual `payouts-kafka-consumer` binary containing this exact logic) but currently **untestable end-to-end** because nothing produces onto the topic (see above) — a substitute/test harness must inject messages directly (e.g. via a Kafka producer test client) to exercise this path until the FTS producer leg is turned on.
- Ordering/duplicate-delivery injection for V21/V15-style tests — **REPRESENTATIVE SUBSTITUTE**: `STORK_REORDER` env flag gives a deterministic, on-demand reordering knob that stands in for production's genuinely nondeterministic non-FIFO SQS behaviour; sufficient to test merchant-side (or PS-side) tolerance of out-of-order/duplicate webhook delivery, not a statistical model of it.
- V21 verifier itself — **STALE relative to the current twin** (calls the wrong capture endpoint, `/v1/captured` instead of `/_captured/events`); should be fixed rather than trusted as `BLOCKED_BY_FIDELITY_GAP`.
