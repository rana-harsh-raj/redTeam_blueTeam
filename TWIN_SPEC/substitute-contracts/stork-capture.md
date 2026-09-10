# Substitute contract — Stork (merchant webhooks, SMS, email) and merchant receiver

Tier: **CONTRACT-FAITHFUL SUBSTITUTE** (`stork-capture`, `merchant-webhook-sink`). Real Stork needs MySQL, Redis,
SQS/SNS, Kafka and an internal Trino coordinator (`stork/configs/default.toml:1082`) — not runnable in the arena.
Source `razorpay/stork@800b719`, `proto/stork/webhook/v1`.

## Inbound from PS
- `POST /twirp/rzp.stork.webhook.v1.WebhookAPI/ProcessEvent` — production client is **protobuf** Twirp
  (`payouts/pkg/stork/client.go:105`). The twin patches PS to the JSON client (`ARENA_STORK_JSON=1`); a substitute that
  wants to remove that patch must decode protobuf (`webhook_api.twirp.go`). Auth Basic username `payout`; service `rx-live`; timeout 30 s.
- `Event{id, created_at, service, owner_id, owner_type "merchant", context, name, payload (opaque JSON string), entity_id}`.
- `payload` string = `{entity:"event", account_id:"acc_<merchant>", event, contains[], payload{<entity>:{entity}}, created_at}`;
  payout entity fields per `payouts/internal/app/dtos/payoutWebhookResponse.go:39-79`; `transaction.created` body per `pkg/stork/process_event.go:47-97`.
- `Create`, `List`, `Update`, `Delete` (caller in prod: monolith `WebhookV2Controller`, `api/app/Http/Route.php:946`): `webhook{service, owner_id, owner_type, url, secret (5-255), subscriptions[{eventmeta{name}}], request_headers[], alert_email, context}`; max 30 webhooks per owner; one subscription row per event name; never echo the raw secret (`secret_exists`).
- SMS/Email `Send` RPCs: capture only.

## Delivery to the merchant URL
- `POST <url>`, body = raw `payload` string verbatim; headers `User-Agent: Razorpay-Webhook/v1`, `Content-Type: application/json`,
  `X-Razorpay-Event-Id: <stork event id>`, `Request-Id: <same>`, and when a secret exists `X-Razorpay-Signature: hex(HMAC-SHA256(secret, raw body))`.
  Merchant custom headers appended, reserved names cannot be overridden.
- Retry (optional fidelity knob): exponential `delay = duration * (2^attempt / 2) * 5`, max 15 attempts / 24 h → `EXCEEDED`; extended owners 72 h / 23 attempts, fixed 6 h after 24 h.
- No dedupe (two `ProcessEvent` calls = two deliveries with distinct event ids); no ordering guarantee.
- Fault hooks required by verifiers: `STORK_REORDER=1` (exists), add `STORK_DUPLICATE=1` (deliver twice), `STORK_DELAY_MS`, per-event drop.
- Capture endpoints: `/_captured/events`, `/_arena/events?merchant=` (verifier V21 still calls `/v1/captured` — stale).

## Merchant receiver (`merchant-webhook-sink`)
`POST /webhook/{merchant_id}`; verify `X-Razorpay-Signature` with `hmac.compare_digest`; always `200 {"received": true}`; append every delivery (headers + body) to `/data/deliveries.jsonl`.

## Events PS emits (subscribe fixtures to exactly these names)
`payout.initiated, payout.queued, payout.pending, payout.rejected, payout.processed, payout.reversed, payout.failed, payout.updated, transaction.created, fund_account.validation.completed, fund_account.validation.failed, payout.downtime.started|updated|resolved`. None on `cancelled`, `scheduled`, `batch_submitted`, `initiated`.
