# Stork & Mozart — architecture, contracts, and sandbox-substitute specs

Repos: `stork` (razorpay/stork), `mozart` (razorpay/mozart), both fully cloned (`git status` clean on `master`) under
`scratchpad/rzp-payouts-architecture/{stork,mozart}`. Cross-referenced: `payouts/pkg/stork`, `fts/internal/providers/mozart`,
`fts/internal/config/mozart.go`, `fts/cmd/mock-server`, `x-balances/internal/{gateway,transformer}/mozart`,
`x-account-statements/internal/gateway/mozart`, `ValidX/internal/{gateway,integrations}/mozart`.

`UNRESOLVED_QUESTIONS.md` was **not present** in this scratchpad (searched full tree, no match) — items 26/27 are answered
below from the task's paraphrase, not the original file text.

---

## A. Stork

### A.1 Stack

- Go monorepo, **Twirp** RPC (JSON-over-HTTP, package `rzp.stork.webhook.v1` etc.). Entrypoints under `stork/cmd/`:
  `api` (Twirp server), `workers/{webhook,sms,email,whatsapp,whatsapp_inbound,pushnotification}` (queue consumers),
  `scheduler` (re-enqueues FAILED/SCHEDULED channels), `dbwriter/webhook` (async DB writer off Kafka), `partitioner`,
  `migration`, `replay`.
- **MySQL** (gorm/goose migrations use MySQL types — `VARCHAR`, `INT(11)`, `TINYINT`;
  `stork/internal/migrations/00001_create_webhooks_table.go`, `00002_create_subscriptions_table.go`,
  `00003_create_messages_table.go`; dialect switch also supports Postgres, `stork/pkg/db/db.go:21-69`, but prod schema is MySQL).
- **Redis**: webhook cache (`stork/internal/webhook/server.go:375`), suppression lists (email/SMS), scheduler distributed mutex
  (`scheduler-run:{channelType}`), and a **dual queue backend** — `pkg/queue/queue.go:NewQueueBroker()` selects Redis
  (list + `RPOPLPUSH`, no TTL visibility, dev/staging) or **SQS** (prod, real visibility timeout + DLQ) per channel's
  `Driver` config field.
- **SQS**: three standard (non-FIFO) priority queues `p0/p1/p2` (`stork/internal/channel/queue.go:12-14`) for webhook/SMS/email
  delivery; separate `ticker`/`reminder` queues, with a distinct **FIFO** queue (`fifotickerp1`) only for the internal
  "ticker" use case (`stork/cmd/workers/webhook/main.go:41-42`) — confirms **regular webhook/merchant-channel delivery has
  no FIFO/ordering guarantee**.
- **Kafka**: async write path for successful webhook attempts (`writeMessageAsync()` → DB-writer consumer) and always used
  for Reminders/Ticker services (`stork/internal/channel/core.go`, described in
  `stork/.agents/skills/repo-skill/modules/technical/technical-patterns.md`).
- **Trino**: source for the webhook auto-disable crons (`DisableMany()`, `DisableLowVolumeWebhooks()`) reading
  low-success/low-volume tables populated by Airflow (`stork/internal/webhook/core.go:332-358`).
- **S3**: email HTML templates + attachments (legacy path), Redis-cached.
- Crypto: `stork/internal/crypto/crypto.go` (AES via `cryptopasta`) encrypts webhook `secret` at rest and SMS `Text` at rest.

### A.2 `WebhookAPI/ProcessEvent` — Twirp schema

Proto: `proto/stork/webhook/v1/webhook_api.proto:15` (`service WebhookAPI`), `:26-27`:
```proto
rpc ProcessEvent(ProcessEventRequest) returns (ProcessEventResponse);
```
```proto
// webhook_api.proto:109-115
message ProcessEventRequest  { Event event = 1; }
message ProcessEventResponse { Event event = 1; }
```
`Event` (`proto/stork/webhook/v1/webhook.proto:57-68`):
```proto
message Event {
  string id = 1;
  google.protobuf.Timestamp created_at = 2;
  string service = 3;
  string owner_id = 4;
  string owner_type = 5;
  map<string, string> context = 6;
  string name = 7;      // event name, must match Subscription.eventmeta.name
  string payload = 8;    // JSON-encoded string, NOT a nested message — Stork does not inspect it beyond HMAC-signing
  string entity_id = 9;  // optional, caller's id for the business entity (used for replay)
}
```
Endpoint: `POST {host}/twirp/rzp.stork.webhook.v1.WebhookAPI/ProcessEvent`, confirmed via three independent paths: proto
package name, `payouts/pkg/stork/sms.go:75` using the identical `/twirp/rzp.stork.{pkg}.v1.{Service}/Send` shape for the
sibling SMS API, and `stork/docs/api-reference.md:394,423` (curl example for the sibling `FetchWebhookEvents` RPC on the
same service). Note: the same doc file also shows a **stale/wrong** `ProcessEvent` response shape
(`webhooks_dispatched/failed/not_subscribed` counters, `docs/api-reference.md:442-466`) that does not match the real
proto (`ProcessEventResponse{Event}` only) — do not trust that block.

Handler: `stork/internal/webhook/server.go:297-329` → `core.processEvent()` (`stork/internal/webhook/core.go:170-219`):
resolve "affected owners" (primary + partner owners via `apiservice.FindAffectedOwnersViaCache()`, plus an Ezetap
sub-entity injected when `context["omni_enabled"]` is set and `owner_type=="merchant"`, `core.go:221-233`) → fetch
active/subscribed webhooks per owner (cached) → build one delivery `Message` per matching webhook
(`getMessage()`/`createWebhookMessageRequestsEvent()`, `core.go:235-267`) → enqueue.

**Payouts' actual call shape** (`payouts/pkg/stork/process_event.go:113-149`, `createProcessEventRequest`):
```go
event := &webhookv1.Event{
  Service:   serviceName,             // e.g. "rx-live" (stork.serviceName in payouts/config/default.toml:156)
  OwnerId:   merchantID,
  OwnerType: "merchant",
  Context:   map[string]string{},
  Name:      eventName,                // e.g. "payout.processed"
  Payload:   json(ProcessEventPayload{
    Entity:    "event",
    AccountID: "acc_" + merchantID,
    Event:     eventName,
    Contains:  []string{entityName},   // e.g. "transaction"
    Payload:   map[string]Entity{ entityName: {Entity: entityPayload} },
    CreatedAt: unixNow,
  }),
}
```
`payout.*` event-name constants: `payouts/internal/app/common/appConstants/webhooks.go:4-11` —
`payout.initiated, payout.processed, payout.reversed, payout.failed, payout.updated, payout.queued, payout.rejected,
payout.pending` (`ONHOLD` internal status maps to public `payout.queued`).
Entity bodies: `PayoutTransactionEventBody` / `ReversalTransactionEventBody`
(`payouts/pkg/stork/process_event.go:47-97`) — `id, entity, account_number, amount, currency, credit, debit, balance,
created_at, source:{id, entity, fund_account_id|payout_id, amount, notes, fees, tax, status, utr, mode, created_at}`.

Auth on the call: HTTP Basic, `Authorization: Basic base64(key:secret)` set per-request
(`payouts/pkg/stork/base.go:27-38`), via the generated Twirp client
(`webhookv1.NewWebhookAPIProtobufClient(host, httpClient)`, `payouts/pkg/stork/client.go:105`).

### A.3 Merchant webhook subscription storage/registration

`Webhook` proto message (`proto/stork/webhook/v1/webhook.proto:14-31`): `id, created_at, updated_at, service, owner_id,
owner_type, context, disabled, disabled_at, url, secret, subscriptions[], request_headers[], alert_email, created_by,
updated_by, secret_exists`. `Subscription{id, created_at, eventmeta:{id, name}}` (`webhook.proto:34-42`) — **one row per
(webhook, event_name) pair**, so a merchant subscribes per-event by creating one `Subscription` per `payout.*` name they
want.

DB tables: `webhooks` (`stork/internal/migrations/00001_create_webhooks_table.go:15-30`, keyed `service, owner_id`;
`secret VARBINARY(2048)` encrypted via `crypto.EncryptString` in the `BeforeSave()` GORM hook,
`stork/internal/webhook/model.go`) and `subscriptions` (`00002_create_subscriptions_table.go:15-27`, `webhook_id`,
`event_name VARCHAR(255)`).

**Registration RPC**: `WebhookAPI/Create` (`webhook_api.proto:17`, request/response at `:50-56`). Documented shape
(`stork/docs/api-reference.md:279-324`, cross-checked against the proto — trustworthy): POST
`service, owner_id, owner_type, url, secret (5-255 chars), subscriptions:[{eventmeta:{name}}], request_headers[],
alert_email, context`; the response never echoes the raw secret (`secret_exists:true` only — `toProto()` vs.
`toProtoWithSecret()` distinction, only specific admin paths use the latter).

No caller of `Create`/`CreateWebhookRequest` exists in the `payouts` repo in this scratchpad — merchant-facing webhook
registration (dashboard/API) is presumably in Razorpay's `api` monolith, not cloned here. **Gap, not verified.**

Constraints (from `stork/.agents/skills/repo-skill/modules/domain/webhook.md` and `webhook-channel.md`, cross-checked
against `core.go`): max **30 webhooks per owner** (`create()` → `countByOwner`); disabled webhooks still get a
persist-only (COMPLETED, no delivery attempt) channel record if disabled within **14 days**
(`processEventWKDisabledSinceMaxSec`), enabling replay — older-disabled webhooks get no record at all; reserved header
names (`x-razorpay-signature, x-razorpay-event-id, request-id, content-type, user-agent`, …) cannot be set as custom
`request_headers`.

### A.4 Delivery worker — retries, backoff, timeouts, ordering, dedupe

**Backoff**: exponential. Config struct `BackoffPolicy` (`stork/internal/config/config.go:883-928`); live prod-config
defaults (`stork/configs/default.toml:961-964`): `DefaultFactor=5, DefaultMaxTime=86400` (24h, seconds),
`DefaultMaxAttempt=15`. Formula (from `internal/channel/policy.go`, corroborated by
`repo-skill/modules/domain/webhook-channel.md`): `delay = (2^attemptCount / 2) * factor * unit`. Per-owner extended
schedule exists (`ExtendedRetryOwnerIDs`, `ExtendedRetryMaxTime=259200` (72h), `ExtendedRetryMaxAttempt=23`,
`ExtendedRetryFixedIntervalStartTime=86400` — exponential for 24h then fixed 6h interval until 72h, 23 total attempts;
`stork/configs/default.toml:965-970`, applied in `webhook/core.go:764-776`). A **separate, smaller** `[email.backoffPolicy]`
block exists for email (`Factor=1, MaxTime=360s, MaxAttempt=3`, `configs/default.toml:698-702`) — do not conflate the two.
Note: `stork/docs/flows/webhook-flow.md` states different (stale) numbers for webhook backoff — trust `configs/default.toml`.

**Timeouts**: per-queue-class HTTP client timeout, `boot.Config.WebhookCh.GetHTTPTimeout()` /
`GetTickerHTTPTimeout()` / `GetRemindersHTTPTimeout()` (`stork/cmd/workers/webhook/main.go:59-71`,
`client_store.go:101` `client.Timeout = f.httpTimeout`). Exact numeric TOML default not located in the time available
(doc claims 30s, unverified against TOML — **flagged**).

**Ordering**: standard (non-FIFO) SQS `p0/p1/p2`, consumed by concurrently-scaled workers →
**no per-merchant or per-event-type ordering guarantee** for regular webhook delivery. FIFO SQS exists only for the
internal Ticker workflow queue family, not merchant webhooks.

**Dedupe**: grepped `internal/webhook`, `internal/channel`, `internal/webhookch` for `dedup*`/`idempoten*` — **zero
matches**. `messages` table has `event_id CHAR(14) DEFAULT NULL` with **no unique constraint**
(`stork/internal/migrations/00003_create_messages_table.go:15-26`). `EventID` exists to *correlate* fan-out copies of one
business event across owners (per `repo-skill/modules/domain/message.md`), not to prevent duplicate delivery. If a
caller (payouts) calls `ProcessEvent` twice for the same logical event, Stork fans out two independent deliveries with
two `X-Razorpay-Event-Id` values equal to each `Event.id` (Stork-generated per request, not caller-supplied) —
**merchants must dedupe on `X-Razorpay-Event-Id` and/or their own entity id in the payload**, Stork provides
at-least-once delivery per channel, not exactly-once.

**Sync-retry-on-non-2xx fast path**: gated by 4-5 simultaneous conditions (`WebhookCh.Worker.SyncRetryOnNon2XX=true`,
`SyncRetryMaxRetries>0`, queue is P0/P1 only, response code in `{408,409,425,429,500,502,503,504}`, Splitz experiment
`stork_webhook_sync_retry_non_2xx` enabled) — else falls through to the standard backoff schedule
(`stork/internal/webhookch/processor.go:47-59`, `shouldRetryImmediatelyOnNon2XX()`).

Status FSM: `CREATED → QUEUED → {COMPLETED | FAILED(retry scheduled) | EXCEEDED(terminal) | TRASHED(DLQ/manual)}`.
Only `CREATED/FAILED/SCHEDULED/QUEUED` are ever re-enqueued (`channel/status.go:queueableStatuses`).

### A.5 Signature header

`stork/internal/webhook/core.go:269-330` (`getWebhookRequestHeaders`):
- Always set: `User-Agent: Razorpay-Webhook/v1`, `Content-Type: application/json`,
  `X-Razorpay-Event-Id: {event.id}`, `Request-Id: {event.id}`.
- If the webhook has a secret configured: decrypt it (`getDecryptedSecret()`), compute
  **`HMAC-SHA256(secret, payloadForSignature)`**, hex-encode, set as **`X-Razorpay-Signature`**.
  `payloadForSignature` is the raw `Event.Payload` JSON string by default — i.e. **signature is over the raw body**,
  not `timestamp+body`. A hardcoded per-merchant whitelist (`whitelistedMerchantIdsForPayloadSanitization`, despite a
  stale comment claiming "controlled by Splitz Experiment") can pre-sanitize `\/`→`/` escaping before signing
  (`shouldSanitizePayloadForSignature`).
- Custom `request_headers` (from the `Webhook` record) are appended after the security headers and cannot override the
  reserved names listed in A.3.

### A.6 Payload encryption option

Two distinct mechanisms — do not conflate:
1. **Secret-at-rest encryption** (always on): AES via `stork/internal/crypto/crypto.go`, encrypts only the `Webhook.secret`
   DB column.
2. **Delivery-payload encryption** (opt-in, ops-configured, not merchant self-service): JOSE/JWE, wired as an HTTP
   middleware — `stork/pkg/http/middleware.go:68-92` (`encryptionMiddleware.Apply` → `joseutil.Encrypt`, sets
   `Content-Type: text/plain`), config `RequestEncryptionConfig{ServerCertificate, KeyAlgorithm, EncryptionAlgorithm}`.
   A separate JWS request-signing middleware and response-verification middleware also exist (`middleware.go:94-130`).
   Selection is a **static Go map** keyed by `config.Owner{Service, OwnerType, OwnerID}`
   (`stork/internal/webhookch/middlewares.go:38-58`, `ownerSpecifiableMiddlewareStore.GetWebhookMiddlewares`), gated by
   Splitz flag `stork_custom_middleware` — there is no API/DB field a merchant can flip themselves.

### A.7 SMS/email template API used by payouts

- `SMSAPI/Send`: `proto/stork/sms/v1/sms_api.proto:42-56` —
  `SendRequest{service, owner_id, owner_type, org_id, context, sender, destination, template_name, template_namespace,
  language, content_params (google.protobuf.Struct), preferred_gateway, delivery_callback_requested}` →
  `SendResponse{message_id, service, owner_id, owner_type, context}`.
- `EmailAPI/Send`: `proto/stork/email/v1/email_api.proto:14-26` (same family + `to/cc/bcc/attachments/subject`).
- Payouts calls SMS via a **hand-rolled JSON HTTP client**, not the generated Twirp stub:
  `payouts/pkg/stork/sms.go:73-95`, `POST {host}/twirp/rzp.stork.sms.v1.SMSAPI/Send`, `SetBasicAuth`, body is a local
  `SMSRequest` struct mirroring the proto fields (`sms.go:16-29`). Email goes through the generated protobuf-style client
  wrapper (`payouts/pkg/stork/email.go:93` → `POST {host}/twirp/rzp.stork.email.v1.EmailAPI/Send`).
- Confirmed literal template identifiers used by payouts, with the config that supplies them
  (`payouts/config/default.toml:157-171`):
  ```toml
  [stork.sms]
      namespace = "razorpayx_payouts_core"
      payout_processed_contact_communication = "sms.payout.payout_processed_contact_communication_v2"
      sender = "RZPAYX"
  [stork.email.templates]
      namespace = "razorpayx_payouts_core"
      payout_reversed = "emails.transaction.payout_reversed"
      payout_processed_contact_communication = "emails.payout.processed_contact_communication"
      payout_transaction_processed = "emails.transaction.payout_processed"
  ```
  Go structs: `EmailTemplateConfig{Namespace, PayoutReversed, PayoutProcessedContactCommunication,
  PayoutTransactionProcessed}` / `SMSConfig{Namespace, PayoutProcessedContactCommunication, Sender, Source, Service}`
  (`payouts/pkg/stork/config.go:59-73`). Params builders per template:
  `payouts/pkg/stork/email_templates.go:20-262` (`PayoutReversedBuilder`, `PayoutProcessedContactCommunicationBuilder`,
  `PayoutTransactionProcessedBuilder`), each mandating specific `metadata` keys (e.g. `PayoutReversedBuilder` requires
  `failure_reason`, `account_number`, `source_id` in `data.Metadata` — `email_templates.go:32-51`).

Stork-side rendering: `emailch` supports two template paths — legacy S3+Mustache (`TemplateBucket`+`TemplateURL`,
Redis-cached) and a newer templating-service call (POST to `templateservice.EmailRenderTemplatePath` with
`namespace, name, orgId, merchantId, placeholderData, channel`) — selected via `Metadata.TemplateMetadata.IsPreProcessorRequired`
or Splitz flag `TEMPLATING_SERVICE_FEATURE_FLAG`
(`stork/.agents/skills/repo-skill/modules/domain/email-channel.md`).

### A.8 Inbound auth

HTTP Basic Auth, enforced by a Twirp `ServerHooks.RequestRouted` interceptor:
`stork/internal/hooks/auth.go:25-42` reads the `authUser`/`authPass` set by `WithAuth` middleware (`auth.go:65-76`,
Go's `r.BasicAuth()`). `NewInternalServiceAuthHooks()` (`auth.go:44-56`) resolves the expected password **by reflection**:
username `foo` → looks up config field `FooPassword` on `boot.Config.Auth` via `structs.New(...).FieldOk(StudlyCase(user))`.
`Auth` struct (`stork/internal/config/config.go:930-960`) enumerates ~30 registered caller identities, including
`PayoutPassword`, `PayoutLinksPassword`, `BankingMethodsPassword`, etc. — i.e. **one shared username/password pair per
calling service**, not per-merchant. `docs/api-reference.md:26-36` separately (and incorrectly) documents a
`Bearer <internal_service_token>` scheme — that paragraph is stale; trust the code + the working curl example a few
hundred lines later in the same doc file (`docs/api-reference.md:421-434`).

### A.9 Datastores/queues recap

MySQL (webhooks, subscriptions, messages, message_channels, per-channel tables) · Redis (cache, suppression lists,
scheduler mutex, queue backend for dev) · SQS (p0/p1/p2 delivery queues, DLQ, ticker/reminder queues, some FIFO) ·
Kafka (async success-attempt writes, domain events) · Trino (disable-cron source, Airflow-populated) · S3 (email
templates/attachments).

### A.10 "Stork capture" substitute spec

A deterministic drop-in replacement for Stork in a sandbox, matching the wire contracts above:

**Endpoints to implement** (all `POST`, Twirp-style JSON-over-HTTP, HTTP Basic auth accepted for any configured
service-name credential pair — mirror A.8 by keying on the caller's username, no need to replicate the reflection trick):

| Path | Purpose | Notes |
|---|---|---|
| `POST /twirp/rzp.stork.webhook.v1.WebhookAPI/Create` | Register a synthetic merchant webhook | persist `{id, service, owner_id, owner_type, url, secret, subscriptions[]}`; enforce max 30/owner to match prod |
| `POST /twirp/rzp.stork.webhook.v1.WebhookAPI/List` | List webhooks for owner | filter by `owner_id`, `owner_type`, `event` |
| `POST /twirp/rzp.stork.webhook.v1.WebhookAPI/ProcessEvent` | Fan-out + deliver | core simulated behavior, see below |
| `POST /twirp/rzp.stork.webhook.v1.WebhookAPI/Get` `/Delete` `/Update` | CRUD | optional, for completeness |
| `POST /twirp/rzp.stork.sms.v1.SMSAPI/Send` | SMS capture | just log/store, return `{message_id}` |
| `POST /twirp/rzp.stork.email.v1.EmailAPI/Send` | Email capture | same |

**`ProcessEvent` request/response** — reuse the real proto shape verbatim (A.2): `{event:{id, created_at, service,
owner_id, owner_type, context, name, payload, entity_id}}` in, `{event}` echoed back out. The substitute should:
1. Look up all non-deleted, non-disabled `Subscription`s for `owner_id` where `eventmeta.name == event.name`.
2. For each match, synchronously (no real queue needed in a capture/sandbox) build and store a delivery record and
   **actually POST** to the registered `url` with headers `Content-Type: application/json`,
   `X-Razorpay-Event-Id: {generated_id}`, `Request-Id: {generated_id}`, and if a `secret` is set,
   `X-Razorpay-Signature: hex(HMAC_SHA256(secret, event.payload))` — this reproduces A.5 exactly, so real merchant
   signature-verification code can be tested against the capture service unmodified.
3. Do **not** implement dedupe on `event.id` — this matches real Stork (A.4) and lets tests exercise merchant-side
   idempotency handling.
4. Return `202`-equivalent success even if a downstream POST fails (Stork itself always returns success for
   `ProcessEvent`; delivery failure is async) — or optionally expose a `GET /_captured/events` debug endpoint listing
   what was sent, for test assertions.

**Registering a synthetic merchant subscription** — call `Create` with:
```json
{
  "webhook": {
    "service": "rx-live",
    "owner_id": "<test_merchant_id>",
    "owner_type": "merchant",
    "url": "https://sandbox.example/webhook-receiver",
    "secret": "test_secret_min_5_chars",
    "subscriptions": [
      {"eventmeta": {"name": "payout.processed"}},
      {"eventmeta": {"name": "payout.reversed"}},
      {"eventmeta": {"name": "payout.failed"}},
      {"eventmeta": {"name": "payout.pending"}},
      {"eventmeta": {"name": "payout.rejected"}},
      {"eventmeta": {"name": "payout.queued"}},
      {"eventmeta": {"name": "payout.initiated"}},
      {"eventmeta": {"name": "payout.updated"}}
    ]
  }
}
```
Then drive events via `ProcessEvent` using the exact envelope payouts sends (A.2) — `entity:"event"`,
`account_id:"acc_"+merchantID`, `contains:["transaction"]`, `payload:{"transaction":{"entity": <PayoutTransactionEventBody
or ReversalTransactionEventBody JSON>}}`.

---

## B. Mozart

### B.1 Framework model

Routing is a single, large static map keyed **namespace → gateway → version → action → config-file-path**
(`mozart/app/routes/available_routes.go:1-2955`; lookup helpers `mozart/app/routes/gateway.go:44-95`,
`GetActions`/`GetDefaultConfig`). Inbound HTTP path: **`/:namespace/:gateway/:version/:action`**, registered with
`gin.BasicAuth(gin.Accounts(credentials))` in both the prod router (`mozart/app/router/router.go:200,309,336,369,382`)
and the mock router (`mozart/app/mock/router.go:14-66`).

Multiple **namespaces** exist per calling service, confirmed directly across client repos:
- `fts` — used by FTS itself and, for some gateways, by x-balances/XAS balance calls too
  (`fts/config/env.default.toml` `MOZART_URLPART_GATEWAY` values like `"rbl/v1"`, `"icici_imps/v1"`;
  `x-balances/internal/transformer/mozart/params.go:19` `FTSNamespace = "fts"`).
- `razorpayx` — x-balances' primary namespace for RBL balance/statement
  (`x-balances/internal/transformer/mozart/params.go:7`, `rbl_transformer.go:171`) and XAS's namespace for account
  statements (`x-account-statements/internal/gateway/mozart/params.go:9`, `RazorpayXNamespace = "razorpayx"`).
- `xas` — XAS-specific statement-fetch namespace (`x-account-statements/internal/gateway/mozart/params.go:11`,
  `XASNamespace = "xas"`, actions `account_statement`/`statement_fetch`/`session_token`).
- `validx` — ValidX's namespace (`ValidX/internal/integrations/mozart/transformer/params.go:7`,
  `ValidxNamespace = "validx"`).

FTS client-side URL construction: `mozartConfig.BaseURL + "/" + operationConfig.MozartURLPartGateway + endpoint.URL`
(`fts/internal/providers/mozart/provider.go:218,1905`), e.g. `BASE_URL=http://localhost:8085/fts` (dev,
`fts/config/env.default.toml:762`) + `MOZART_URLPART_GATEWAY="rbl/v1"` + `endpoint.URL="/transfer_init"` →
`http://localhost:8085/fts/rbl/v1/transfer_init`. x-balances builds the same shape generically:
`GenericURL = "/%s/%s/%s/%s"` = `/{namespace}/{gateway}/{version}/{action}`
(`x-balances/internal/gateway/mozart/service/service.go:26,69-73`), with `SetBasicAuth` per client-level
`EgressBasicAuth` config (`x-balances/internal/config/config.go:122-128`).

**FTS's 8 operations** map 1:1 to Mozart action names, defined at `fts/internal/config/mozart.go:4-13`:
```go
OperationLogin = "login"
OperationGatewayAuth = "gateway_auth"
OperationGatewaySession = "gateway_session"
OperationTransferInit = "transfer_init"
OperationTransferStatus = "transfer_status"
OperationBeneficiaryVerify = "beneficiary_verify"
OperationBeneficiaryRegister = "beneficiary_register"
OperationFetchAccountBalance = "account_balance"   // fetch_account_balance
OperationCreateOTP = "create_otp"
```
Mozart config (`fts/internal/config/mozart.go:15-26`, `fts/config/env.default.toml:761-812`): per-operation `Endpoint{URL,
Method, Timeout=180s, Headers}`, inbound auth `[mozart.auth] KEY = env|MOZART_AUTH_KEY, SECRET = env|MOZART_AUTH_SECRET`.
Actual Basic-auth send: `request.SetAuth(mozartConfig.Auth)` → `req.SetBasicAuth(auth.Key, auth.Secret)`
(`fts/internal/providers/mozart/provider.go:945`, `fts/pkg/request/rest.go:345`).

**Gateway/version coverage** (from `mozart/app/routes/available_routes.go:2513-2622`, `fts` namespace block):
- **RBL**: v1 (account_balance, transfer_init/status NEFT, gateway_session/auth, validate), v2 (same set), v3
  (transfer_init/status_imps, bulk-oriented — no auth/session), v4 (transfer_init/status, beneficiary_register), v5
  (transfer_init/status, batch-oriented).
- **ICICI**: `icici_imps` v1 (transfer_init/status only); `icici` v1 (transfer_init/status), v2 (beneficiary_register,
  transfer_init/status, account_balance), v3 (transfer_init/status, create_otp).
- **Yes Bank**: `yesbank` v1 (transfer_init/status, beneficiary_verify/register, account_balance), v2 (transfer_init/status);
  `yesbank_upi` v1 (transfer_init/status).
- **Axis**: v1 only (beneficiary_register, transfer_init/status, account_balance).
- **IDFC**: **only `idfc_upi` v1 exists** (transfer_init/status) — there is **no plain "idfc" NEFT/IMPS gateway** in
  Mozart's `fts` namespace (confirmed by full-file grep). The task's premise of "IDFC v1" for bank transfers does not
  hold; IDFC integration in Mozart is UPI-only.

Channel-specific behaviors documented in FTS's own integration doc
(`fts/.agents/skills/repo-skill/modules/integration/internal/mozart/outbound.md:311-327`): ICICI VAP-transfer detection
by IFSC list + mode, BaaS merchant-id filtering, Recon-vs-non-Recon status check keyed on UTR presence
(`fts/internal/providers/mozart/request.go:164-202`); RBL two-step UPI auth (session+auth token) with Redis-cached
multi-step auth and distributed mutex (`fts/internal/gateway/auth.go:109-234`); Axis NEFT remarks enhancement
(experiment-gated) and compliance remitter-details injection.

### B.2 Response schema FTS keys on

Wire contract every gateway/version must satisfy — `fts/internal/providers/mozart/response.go:3-113`:
```go
type Response struct {
  Data struct {
    Raw string `json:"_raw"`                    // full raw bank response, JSON-encoded string
    BeneficiaryName, Remarks, Utr, BankStatusCode, GatewayErrorCode string
    CmsRefNo, FailureReason, BankRequestID, BankProcessedTime string
    GatewayAuth, GatewaySession gatewayAuth      // {token, token_type, validity_duration}
    ReturnUtr, Ponum, Balance, AccountBalance string  // AccountBalance json:"accountBalanceAmount"
    CreditedAt string; IsCredited, IsDebited bool
    BeneficiaryCode, BeneficiaryIfsc, BeneficiaryAccNo, BeneficiaryBank,
    BeneficiaryAccType, BeneficiaryValidationMode string
  } `json:"data"`
  Error mozartError `json:"error"`   // description, gateway_error_code, gateway_error_description, gateway_status_code, internal_error_code
  ExternalTraceID, MozartID string
  Success bool
  Meta meta `json:"meta"`            // error_type, merchant_error, critical_error, retriable_error, pending, processed, failed, reversed — COMPUTED BY FTS, see below
  ExtraInfo ExtraInfo
}
```
**Important**: the `meta`/`processed`/`failed`/`reversed`/`pending` flags are **not sent by Mozart on the wire** — real
Mozart mock fixtures (`fts/cmd/mock-server/testdata/icici_imps/v1/*.json`, e.g. `StatusWithTransactionSuccess.json`) only
carry `{data, error, external_trace_id, mozart_id, success, next}` (`x-balances`/XAS's local `Response` struct,
`x-account-statements/internal/gateway/mozart/params.go:44-51`, matches this — no `meta` field either). FTS computes
`meta` **locally** post-hoc from `bank_status_code` via `fillMeta()` (`fts/internal/providers/mozart/provider.go`),
looking the code up in two Go maps in `fts/internal/providers/mozart/error_code.go`:
- `defaultErrorCodes` (`error_code.go:229-952`, ~140 entries) — general lookup, e.g.:
  ```go
  StatusDuplicateTxn:     "DUPLICATE_TXN"     → {Pending:true, ErrorType:PBANK}          (error_code.go:26,355-358)
  StatusInsufficientFund: "INSUFFICIENT_FUND" → {RetriableError:true, Failed:true, ErrorType:INTERNAL} (error_code.go:35,395-399)
  StatusSuccess:          "SUCCESS"           → {Processed:true, ErrorType:UNKNOWN}      (error_code.go:145,247-250)
  StatusDeemedSuccess:    "DEEMED_SUCCESS"    → {Pending:true, ErrorType:BBANK}           (error_code.go:114,754-757)
  StatusReturned:         "RETURNED"          → {Failed:true, ErrorType:BBANK}            (error_code.go:46,438-441)
  ```
  Any unmapped `bank_status_code` defaults to `{Pending:true, ErrorType:UNMAPPED}`.
- `transferErrorCodes` (`error_code.go:217-226`) — overrides applied only during `transfer_init` (e.g.
  `GATEWAY_ERROR_VAULT_FAILURE` is `Failed` during transfer_init but `Pending` during status_check).

**Raw bank codes surfaced verbatim** (not normalized, handled as special-case guards in FTS's attempt processor, not via
the generic map) — constants at `error_code.go:126-130`:
```go
RawCBS188BankCode  = "CBS:188"    // idfc
RawProxy401BankCode = "PROXY_401" // idfc
RawCBS7161BankCode = "CBS:7161"   // idfc
RawNSE404BankCode  = "ns:E404"    // yesbank
```
Cutoff-window handling config (`fts/config/env.default.toml:8399-8436`, `[gateway_error_handling]`):
```toml
[gateway_error_handling.channel.idfc.status."CBS:188"]
    cut_off_time = 8100
    final_status = 'FAILED'
[gateway_error_handling.channel.idfc.status."CBS:188".transfer."CBS:188"]
    cut_off_time = 8100
    final_status = 'FAILED'
[gateway_error_handling.channel.idfc.status."PROXY_401".transfer."CBS:188"]
    cut_off_time = 8100
    final_status = 'FAILED'
[gateway_error_handling.channel.idfc.status."CBS:7161".transfer."CBS:188"]
    cut_off_time = 8100
    final_status = 'FAILED'
[gateway_error_handling.channel.yesbank.status."ns:E404"]
    cut_off_time = 5400
    final_status = 'FAILED'
```
Semantics (comments in the TOML + `isCBS188WithUTR`/`isNSE404WithoutUTR` guards in FTS code): if the status-check error
code is `CBS:188` (or `ns:E404`) **and UTR is null**, mark FAILED only after `cut_off_time` seconds elapsed since attempt
creation; if UTR **is present**, treat as pending/deemed regardless of cutoff (handled in code, not TOML). RBL's `ER028`
(`IMPS_ER028_NO_UTR`) is handled entirely inside Mozart's own pipeline config
(`mozart/app/config/fts/rbl/v2/base.json`), not in FTS.

### B.3 Balance-fetch (x-balances) / statement-fetch (XAS)

Same `/namespace/gateway/version/action` pattern. x-balances RBL balance:
```go
// x-balances/internal/transformer/mozart/rbl/rbl_transformer.go:169-179
RequestAttributes{Namespace: mozart.RazorpayXNamespace, Gateway: "rbl", Version: mozart.VersionV1, Action: mozart.AccountBalanceAction}
```
parses the raw bank payload nested in `Response.Data["PayGenRes"]` → `models.PayGenRes.Body.BalAmt.AmountValue` (string)
→ `BalanceFetchResponse{Amount int64}` (`rbl_transformer.go:112-166`). Other gateways (ICICI/YesBank/Axis/IDFC/Slice) use
`Namespace: FTSNamespace` for their balance transformers (`icici_transformer.go:140`, `yesbank_transformer.go:108`,
`axis_transformer.go:110`, `idfc_transformer.go:110`, `slice_transformer.go:114`) — i.e. **RBL balance goes through the
`razorpayx` namespace while every other bank's balance call goes through the `fts` namespace**, a real asymmetry to
replicate in a simulator.

XAS statement-fetch (`x-account-statements/internal/gateway/mozart/params.go:1-51`): namespaces `razorpayx` (generic) and
`xas` (XAS-specific), gateways `rbl/yesbank/idfc/axis/icici/slice`, actions `account_statement` / `statement_fetch` /
`session_token`, `Response{Data map[string]interface{}, Error, ExternalTraceID, MozartID, Success}` — same envelope as
the transfer APIs, no `meta`.

### B.4 Built-in mock/sandbox mode — closes UNRESOLVED_QUESTIONS #27

**Mozart ships a first-class built-in simulator.** Launched via a CLI flag: `mozart -mock`
(`mozart/main.go:442-457`) → `mock.StartMockApp()` (`mozart/app/mock/mock.go:120-146`), a standalone listener that makes
**no real bank network calls**, using the identical route shape and Basic auth as prod
(`mozart/app/mock/router.go:14-66`, `/:namespace/:gateway/:version/:action`).

**Scenario selection**: `mozart/app/mock/mappings.go` maps each `namespace/gateway/version/action` route to **one
specific JSON-path field in the (flattened) inbound request** — e.g. `fts/rbl/v1/transfer_init` keys on
`entities.fund_account.bank_account.beneficiary_mobile`; `fts/icici/v2/transfer_init`, `fts/citi/v1/transfer_init`,
`fts/yesbank/v1/transfer_init`, `fts/m2p/v1/transfer_init` key on `entities.attempt.amount`; the matching `*_status`
actions key on `entities.attempt.gateway_ref_no` instead (`mappings.go:103-155`). **The literal value of that field is
the scenario ID** (exact string match, not a suffix/pattern rule — `mock/controller.go:100-114`, `getScenarioId`).
`ProcessMockControllerRequest` (`controller.go:30-98`) resolves
`app/testdata/{namespace}/{gateway}/{version}/{action}/{scenarioID}/` and replays the fixture through Mozart's **real
production config pipeline** (`router.RunTest`) — so request-mapping/error-handling logic runs identically to prod; only
the outbound HTTP call to the bank is swapped for a canned fixture.

Fixture files per scenario directory (`mock/mock.go:44-95`): `.gatewayReqValidator` (request validation rules),
`.gatewayRespMock` (templated raw bank response, `{{.field}}` placeholders resolved from
`router/test_registry.go`'s `gatewayResponseGeneratedFields`), `.vaultResp` (canned Vault token response — no secret
values). Separately, integration-test fixtures use `.golden` (final Mozart `Response` JSON) + `.gatewayResp` (raw bank
JSON) pairs; hundreds already exist under `mozart/app/testdata/fts/**` for RBL v1-v5, ICICI v1-v3/imps, Yes Bank
v1/v2/UPI, Axis v1, IDFC UPI, M2P, Kotak, Citi, Amazon Pay. Confirmed concrete example: RBL v1 transfer_status pending
scenario `9999999151` (full `PayGenRes` raw + normalized `bank_status_code:"PENDING"`); Axis v1 `account_balance`
success scenario `100`.

A separate `mozart/app/router/mock_gateway_server.go` (`httptest`-based fake bank server, 528 lines) exists only for
Mozart's own Go integration test suite — not externally reachable, not usable as a standalone sandbox dependency.

`docker-compose.dev.yml` (`mozart/docker-compose.dev.yml:1-42`) runs real Postgres + Redis + the real Mozart binary —
**no stub-bank container**. Local dev without hitting real banks means running Mozart itself with `-mock`, not an
external fake-bank service.

### B.5 Inbound auth & config sources

`gin.BasicAuth(gin.Accounts(credentials))` on every route (`mozart/app/router/router.go:200,309,336,369,382`),
credentials built once at boot (`bootstrapAuthCredentials`, `mozart/main.go:460-471`) — one username/password pair per
calling client (FTS, x-balances, XAS, ValidX each get their own). FTS/x-balances send it via standard HTTP Basic
(`SetBasicAuth`, confirmed both sides — A.2-equivalent pattern, B.1 above).

Bank credentials: per-gateway `[secrets.<gateway>]` TOML blocks (`mozart/conf/env.sample.toml:166-382` — gateway names
present: `rbl`, `icici`, `icici_tpv`, `yesbank`, `yesbank_upi`, `idfc_upi`, `citi`, `m2p`, `m2p_capital`, `atos`,
`netbanking_*`, …; **no secret values captured, structure/names only**), backed by **Vault**
(`[credentials.vault]` block, `env.sample.toml:37-70` — username `vault`, password sourced from env var
`LUMBERJACK_SECRET`; no secret values captured).

---

## C. "Mozart simulator" spec

Two complementary layers, matching what Razorpay itself already runs:

### C.1 Preferred: run real Mozart in `-mock` mode

Since Mozart already ships this (B.4), the highest-fidelity simulator for FTS/x-balances/XAS/ValidX integration testing
is **Mozart itself, started with `-mock`**, backed by the existing fixture tree under `mozart/app/testdata/fts/**`.
Endpoints, auth, and response envelope are byte-identical to prod because the same config pipeline runs. To add a new
scenario: drop a new `app/testdata/{namespace}/{gateway}/{version}/{action}/{scenarioID}/{.golden,.gatewayResp}` pair and
drive it by setting the mapped field (per `mock/mappings.go`) to that exact scenario-ID string in the request.

### C.2 Lightweight standalone substitute (when Mozart itself is unavailable)

For teams that cannot stand up the full Mozart binary + Postgres/Redis, a smaller HTTP server reproducing the wire
contract is sufficient — this is exactly the pattern FTS's own `cmd/mock-server` already uses (a **pre-existing,
simpler precedent** in this codebase, `fts/cmd/mock-server/`, distinct from Mozart's real `-mock`):

**Endpoints** (mirror B.1's real paths, one process per `{namespace}` is unnecessary — a single server can multiplex):
```
POST /{namespace}/{gateway}/{version}/transfer_init
POST /{namespace}/{gateway}/{version}/transfer_status
POST /{namespace}/{gateway}/{version}/gateway_auth
POST /{namespace}/{gateway}/{version}/gateway_session
POST /{namespace}/{gateway}/{version}/beneficiary_verify
POST /{namespace}/{gateway}/{version}/beneficiary_register
POST /{namespace}/{gateway}/{version}/account_balance
POST /{namespace}/{gateway}/{version}/create_otp
```
Auth: accept any HTTP Basic credentials (or match a configured allowlist per `{namespace}`) — do not attempt to
replicate Mozart's per-client credential-name reflection.

**Response envelope** (all endpoints) — match B.2 exactly, **no `meta` field**:
```json
{
  "data": { "...action-specific fields..." },
  "error": null,
  "external_trace_id": "SIM_TRACE_ID",
  "mozart_id": "SIM_MOZART_ID",
  "success": true,
  "next": {}
}
```

**Scenario selection rules** — combine two conventions, both already precedented in this codebase:

1. **Amount-keyed** (exactly what `fts/cmd/mock-server/controllers/service/mozart.go:12-59,69-114` already does for
   `transfer_init`/`transfer_status`/`gateway_session`/`gateway_auth`, reading `entities.attempt.amount` from the
   flattened request):
   | `entities.attempt.amount` | transfer_init scenario | transfer_status scenario |
   |---|---|---|
   | `1` | min-amount success | min-amount success |
   | `100` | success | **rejected** (`bank_status_code`, `internal_error_code:"FAILED"`) |
   | `200000` | max-amount success | — |
   | `200005` | invalid-amount failure (`internal_error_code:"INVALID_AMT"`) | — |
   | `300` | validation error (`internal_error_code:"VALIDATION_ERROR"`) | — |
   | `500` | — | transaction-not-found |
   | `800` | gateway error (`internal_error_code:"GATEWAY_ERROR_UNKNOWN_ERROR"`) | — |
   | `1000` | — | Vault bad-request (`internal_error_code:"INTERNAL_SERVER_ERROR"`) |
   | `2000` | — | gateway-session success |
   | `3000` | — | gateway-auth success |
   | any other | — | generic success |
2. **Beneficiary-account/name-keyed** for `beneficiary_register`/`beneficiary_verify` (mirrors
   `service/mozart.go:154-187,217-242`, which switches on `beneficiary_name`; a synthetic account-number-suffix
   convention is the drop-in generalization):
   | account-number suffix (last 2 digits) | register | verify |
   |---|---|---|
   | `00` | duplicate (`StatusBeneRecordAlreadyExists`, `success:false`, pending) | — |
   | `01` | failed | failed |
   | default | success | success |

**Stateful transitions for repeated `transfer_status` polls** — requires an in-memory (or Redis) map keyed on
`gateway_ref_no` (the field Mozart's own `-mock` uses for `*_status` actions too, per B.4) storing `{scenario, poll_count}`:

- **Immediate processed**:
  ```json
  {"data":{"utr":"902217736815","bank_status_code":"SUCCESS","remarks":"Transaction Successful"},
   "error":null,"success":true,"mozart_id":"SIM1","external_trace_id":"SIM1"}
  ```
- **Failed with code** (e.g. `INSUFFICIENT_FUND`):
  ```json
  {"data":{"utr":null,"bank_status_code":"INSUFFICIENT_FUND"},
   "error":{"description":"Insufficient funds in source account","gateway_error_code":"INSUFFICIENT_FUND",
            "gateway_error_description":"Insufficient funds","gateway_status_code":200,
            "internal_error_code":"INSUFFICIENT_FUND"},
   "success":false,"mozart_id":"SIM2","external_trace_id":"SIM2"}
  ```
- **Pending, then processed after N polls** (poll_count < N → pending; poll_count >= N → success + UTR):
  ```json
  // poll 1..N-1
  {"data":{"utr":null,"bank_status_code":"PENDING"},"error":null,"success":true,"mozart_id":"SIM3"}
  // poll N
  {"data":{"utr":"902217736999","bank_status_code":"SUCCESS"},"error":null,"success":true,"mozart_id":"SIM3"}
  ```
- **`CBS:188` with UTR present** (idfc — stays pending regardless of cutoff, per B.2's `isCBS188WithUTR` guard):
  ```json
  {"data":{"utr":"IDFCUTR12345","bank_status_code":"PENDING"},
   "error":{"description":"Record processing","gateway_error_code":"CBS:188",
            "gateway_error_description":"CBS record not found, awaiting confirmation","gateway_status_code":200,
            "internal_error_code":"CBS_188_RECORD_NOT_FOUND"},
   "success":false,"mozart_id":"SIM4"}
  ```
- **Duplicate transaction** (`DUPLICATE_TXN`, pending/PBANK per `error_code.go:355-358`):
  ```json
  {"data":{"utr":null,"bank_status_code":"DUPLICATE_TXN"},
   "error":{"description":"Duplicate transaction request detected","gateway_error_code":"DUPLICATE_TXN",
            "gateway_error_description":"","gateway_status_code":200,"internal_error_code":"DUPLICATE_TXN"},
   "success":false,"mozart_id":"SIM5"}
  ```
- **Reversed after processed** (poll_count 1..N → success; poll_count > N → reversed):
  ```json
  // after reversal window
  {"data":{"utr":"902217736815","bank_status_code":"RETURNED","return_utr":"RET902217736815"},
   "error":{"description":"Transaction returned by beneficiary bank","gateway_error_code":"RETURNED",
            "internal_error_code":"RETURNED"},
   "success":false,"mozart_id":"SIM1"}
  ```
- **`user_pending` / OTP required** (`create_otp` action or a transfer requiring in-band OTP):
  ```json
  {"data":{"bank_status_code":"TXN_ACK"},
   "error":null,"success":true,"mozart_id":"SIM6"}
  // followed by transfer_status: pending-user-action until create_otp is called with the correct OTP
  ```

This spec is intentionally aligned with real fixtures already checked into the repos (`fts/cmd/mock-server/testdata/**`
for the amount-keyed convention, `mozart/app/testdata/fts/**` for the exact-field-match convention) so that a synthetic
simulator built from it can be validated byte-for-byte against existing golden files.

---

## UNRESOLVED_QUESTIONS closed

**#26 — Stork ordering/dedupe**: **Closed.** No ordering guarantee for regular webhook delivery (standard, non-FIFO SQS
`p0/p1/p2`, concurrent workers; FIFO exists only for the internal Ticker queue family). No dedupe on event id — `messages.event_id`
has no unique constraint, and code-wide grep for `dedup*`/`idempoten*` in the webhook/channel packages returns zero
matches. Stork provides **at-least-once delivery per channel**, not exactly-once; merchants must dedupe on
`X-Razorpay-Event-Id` (or their own entity id embedded in the payload). See A.4.

**#27 — Mozart contract, mock-gateway fixtures**: **Closed.** Mozart's FTS-facing contract is the `Response{data, error,
external_trace_id, mozart_id, success, next}` envelope (B.2) plus the 8 named actions (B.1) routed via
`/{namespace}/{gateway}/{version}/{action}` with Basic auth (B.5). Mozart has a **built-in, production-config-driven
mock mode** (`-mock` flag, `mozart/app/mock/`) with exact-field-match scenario selection and hundreds of pre-existing
`.golden`/`.gatewayResp` fixtures under `app/testdata/fts/**` (B.4) — this is the canonical mock-gateway fixture set, and
should be preferred over building a new synthetic simulator from scratch (C.1). A simpler, amount-keyed convention also
already exists as a second, independent precedent at `fts/cmd/mock-server/` (C.2).

## Unknowns / gaps for follow-up

1. **Stork HTTP delivery timeout** — exact numeric default not located in `stork/configs/default.toml` in the time
   available (only the `GetHTTPTimeout()`-style config accessors were found); a doc claims 30s but is unverified against
   the live TOML.
2. **Stork webhook registration caller** — no caller of `WebhookAPI/Create` exists in the `payouts` repo in this
   scratchpad; the merchant-facing registration UI/API is presumably in Razorpay's `api` monolith, not cloned here.
3. **`stork/docs/api-reference.md` has two stale/wrong sections** — a `Bearer <token>` auth-scheme description
   (contradicts the Basic-auth code path) and a fabricated `ProcessEvent` response shape with dispatch counters
   (contradicts the proto). Both are called out inline in A.2/A.8; do not use them as ground truth.
4. **IDFC bank-transfer gateway does not exist in Mozart's `fts` namespace** — only `idfc_upi` v1 was found. The task's
   assumption of an "IDFC v1" NEFT/IMPS gateway does not hold; flagged in B.1.
5. **XAS statement-fetch and ValidX beneficiary verify/register field-level schemas** were confirmed for URL/auth/
   envelope shape but not exhaustively extracted per-bank (time-boxed); a follow-up pass could pull the exact
   `x-account-statements/internal/transformer` and `ValidX/internal/integrations/mozart/transformer` response structs
   if a finer-grained XAS/ValidX simulator is needed.
6. **Mozart's outbound request-side JSON Schema per gateway** (the actual bank-facing HTTP call) is a multi-step
   config-driven pipeline (RequestMapper/JsonTemplate steps), not a flat template — not extracted per-gateway; the
   FTS-side `Request`/`Response` Go structs (B.2) are the stable, already-documented contract used throughout this
   report instead.
7. **Stork's "payload encryption option"** exists but is JOSE/JWE-based, ops-configured per `{service, owner_type,
   owner_id}` tuple behind a Splitz flag — **not a merchant-self-service toggle**, which may differ from the task's
   framing of it as a per-merchant option (A.6).
8. A dev-environment `crypto.encryptionKey` default value is present in `stork/configs/default.toml` (non-prod config)
   — value intentionally not reproduced here per the redaction instruction; treat as a placeholder dev key, not a
   production secret.
