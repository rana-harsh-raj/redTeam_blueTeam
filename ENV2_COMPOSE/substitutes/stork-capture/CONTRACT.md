# stork-capture — CONTRACT

Substitute for Stork (webhook/SMS/email fan-out). Wire contract taken
verbatim from `findings/25_stork_mozart.md` §A (full research on the real,
readable `stork` repo + `proto/stork/**`) — not guessed. See that file for
the complete backing analysis; this CONTRACT.md carries only what the stub
implements.

## Endpoints (Twirp JSON-over-HTTP, per `proto/stork/webhook/v1/webhook_api.proto`)

| Path | Purpose |
|---|---|
| `POST /twirp/rzp.stork.webhook.v1.WebhookAPI/Create` | register a synthetic merchant webhook subscription |
| `POST /twirp/rzp.stork.webhook.v1.WebhookAPI/List` | list webhooks for an owner |
| `POST /twirp/rzp.stork.webhook.v1.WebhookAPI/ProcessEvent` | fan-out + synchronous delivery (real Stork is async; this stub delivers inline since there's no queue to simulate) |
| `POST /twirp/rzp.stork.sms.v1.SMSAPI/Send` | SMS capture (log + store, no real delivery) |
| `POST /twirp/rzp.stork.email.v1.EmailAPI/Send` | email capture (log + store) |
| `GET /_captured/events` | debug: list everything ProcessEvent has fanned out, for test assertions |
| `GET /_arena/events?merchant=<owner_id>` | task-specified debug endpoint — alias of `/_captured/events`, optionally filtered by `owner_id` |

Auth: HTTP Basic, one shared username/password pair per calling service
(payouts uses `[stork]` creds — findings/25 §A.8), same convention as every
other stub (`_common/base_stub.py`).

## Seed data

`seeds/stork/subscriptions.json`, mounted at `/app/seed/subscriptions.json`
(`STORK_SEED_FILE` env) — the 3 arena merchants (`ARENAM00000001/2/3`),
each subscribed to all 8 `payout.*` events, `url` pointing at
`merchant-webhook-sink:8080/webhook/{merchant_id}`. Loaded via the same
code path `Create` uses (`_seed_webhook()`), so a seeded webhook and one
registered live via `Create` are indistinguishable to `ProcessEvent`/`List`.

## `ProcessEvent` — exact real shape (findings/25 §A.2)

Request:
```json
{"event": {"id":"", "created_at":"...", "service":"rx-live", "owner_id":"ARENA_M2", "owner_type":"merchant", "context":{}, "name":"payout.processed", "payload":"<json-encoded string, opaque to Stork>", "entity_id":"pout_ARENA000001"}}
```
Response: `{"event": {...same, "id" filled in by this stub if absent...}}`.

`payout.*` event-name constants (findings/25 §A.2, from
`payouts/internal/app/common/appConstants/webhooks.go`): `payout.initiated`,
`payout.processed`, `payout.reversed`, `payout.failed`, `payout.updated`,
`payout.queued`, `payout.rejected`, `payout.pending`.

## Delivery + signature (findings/25 §A.5) — reproduced exactly

For each active `Subscription` on the owner matching `event.name`, this stub
synchronously `POST`s to the webhook's registered `url` (in Env 2, the
seeded synthetic merchant's `url` points at `merchant-webhook-sink`, see
below) with headers:

- `Content-Type: application/json`
- `X-Razorpay-Event-Id: {generated_id}`
- `Request-Id: {generated_id}`
- `X-Razorpay-Signature: hex(HMAC-SHA256(webhook.secret, <delivered body>))`.

**Delivered HTTP body = `event.payload` itself (the raw JSON string),
not the outer `Event` envelope.** `payouts/pkg/stork/process_event.go`
builds `Event.Payload` as a JSON-encoded string of `ProcessEventPayload`
(the actual webhook content a merchant cares about — `entity, account_id,
event, contains, payload:{...}, created_at`); that string is what gets
delivered and what the signature covers, so a real merchant-side verifier
(HMAC over the raw bytes it received) matches. (Fixed in this pass — an
earlier version delivered `json.dumps(event)` (the whole envelope) while
signing only `event.payload`, which made every real HMAC verification fail
by construction; caught by an actual end-to-end curl test against
`merchant-webhook-sink`, see "Validated" below.)

No dedupe on `event.id` (matches real Stork — findings/25 §A.4: "Stork
provides at-least-once delivery per channel, not exactly-once"). `ProcessEvent`
always returns 200-equivalent success even if the downstream POST fails
(matches real Stork's fire-and-forget-from-the-caller's-perspective contract).

## Persistence + reordering

Every delivery attempt (success or failure) is appended as one JSON line to
`/data/events.jsonl` (`STORK_EVENTS_LOG_FILE` env) — durable across the
in-memory `CAPTURED_EVENTS` list, for a golden-run script to `tail`/parse
after the container exits. `STORK_REORDER=1` (env, task-specified for V21)
delays every even-indexed delivery for a given `owner_id` by
`STORK_REORDER_DELAY_SEC` (default 0.3s) so the following (odd-indexed)
delivery — sent without delay — lands at the merchant endpoint first; a
simple, deterministic way to reproduce Stork's real "at-least-once,
no ordering guarantee" contract (findings/25 §A.4) on demand instead of by
accident.

## `Create` request (findings/25 §A.10 worked example)

```json
{"webhook": {"service":"rx-live", "owner_id":"ARENA_M2", "owner_type":"merchant",
  "url":"http://merchant-webhook-sink:8080/webhook", "secret":"arena_test_secret_min5",
  "subscriptions": [{"eventmeta":{"name":"payout.processed"}}, {"eventmeta":{"name":"payout.reversed"}},
    {"eventmeta":{"name":"payout.failed"}}, {"eventmeta":{"name":"payout.queued"}}]}}
```
Enforces max 30 webhooks/owner (matches real Stork, findings/25 §A.3) — not
a meaningful limit at Env 2 scale but kept for parity.

## Health

`GET /health` -> 200.

## Companion service: merchant-webhook-sink

A separate stub (`substitutes/merchant-webhook-sink/`) plays the role of "the
merchant's own webhook endpoint" — it verifies the `X-Razorpay-Signature`
against the shared secret, stores every delivery (in-memory + `GET
/_arena/deliveries` + `/data/deliveries.jsonl`), and exposes
`GET /_received/events` for test assertions. Kept as its own container (not
folded into stork-capture) so a golden run can assert delivery
independently of the sender's internal capture log, mirroring how a real
merchant's server is a separate system from Stork.

## Validated

`python3 -m py_compile`; ran `stork-capture` (port 18808) and
`merchant-webhook-sink` (port 18809) together locally: `ProcessEvent` for
M1/`payout.processed` delivered to the sink with a valid
`X-Razorpay-Signature` (`signature_valid: true`, confirmed after the
delivered-body fix above); `GET /_arena/events?merchant=ARENAM00000001`
correctly filtered; `STORK_REORDER=1` with two back-to-back `ProcessEvent`
calls for the same owner delivered the 2nd event before the 1st (confirmed
via the sink's `received_at` timestamps); `/data/events.jsonl` and
`/data/deliveries.jsonl` both persisted the expected JSON lines.
