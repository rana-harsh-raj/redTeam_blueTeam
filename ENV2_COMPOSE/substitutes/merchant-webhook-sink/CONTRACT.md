# merchant-webhook-sink — CONTRACT

Plays the role of "the merchant's own server" receiving `payout.*` webhook
deliveries from `stork-capture`. Exists so a golden run can assert webhook
delivery independently of the sender's internal log, and so real
merchant-side signature-verification code can be tested against a receiver
that reproduces Stork's real header/signature contract
(`findings/25_stork_mozart.md` §A.5, also summarized in
`substitutes/stork-capture/CONTRACT.md`).

## Endpoint

`POST /webhook/{merchant_id}` (also accepts the bare `POST /webhook` for
back-compat) — **no Basic-Auth** (a real merchant endpoint doesn't
authenticate Stork; Stork authenticates itself to callers, not the
reverse). `seeds/stork/subscriptions.json` registers each arena merchant's
webhook `url` as `http://merchant-webhook-sink:8080/webhook/{merchant_id}`
so deliveries for different merchants are distinguishable in this sink's
own log without needing per-merchant containers. Accepts any JSON body
(the body IS the raw `event.payload` JSON string stork-capture delivers —
see its CONTRACT.md) plus headers:
- `X-Razorpay-Event-Id`
- `Request-Id`
- `X-Razorpay-Signature` (present only if the webhook was registered with a
  `secret` — see `stork-capture`'s `Create` call in `seeds/`)

Verifies `X-Razorpay-Signature == hex(HMAC-SHA256(WEBHOOK_SECRET, raw_body))`
when `WEBHOOK_SECRET` env var is set (must match the `secret` used when
registering the webhook via `stork-capture`'s `Create` RPC — see
`seeds/*_stork_subscriptions.json`). Every received delivery is stored
in-memory, verified or not, with a `signature_valid` flag — a failed
verification does NOT reject the request (returns 200 either way, matching
how a real merchant endpoint typically still ACKs receipt) but is visible in
the debug endpoint below, so a scenario can assert "merchant would have
rejected this."

## Debug endpoints

- `GET /_received/events` -> `{"events": [{merchant, path, headers, event_id, request_id, signature_valid, received_at, body}, ...]}`
- `GET /_arena/deliveries` (task-specified alias, same shape as above) — for
  test assertions (webhook ordering checks per BOM §3 Env 4 flow H,
  idempotency-dedupe checks per BOM §2 flow B).

Every delivery is also appended as one JSON line to `/data/deliveries.jsonl`
(`DELIVERIES_LOG_FILE` env), durable across the in-memory list.

## Health

`GET /health` -> 200.

## Validated

`python3 -m py_compile`; exercised end-to-end against `stork-capture` (see
its CONTRACT.md's "Validated" section) — received a real HMAC-signed
delivery at `/webhook/ARENAM00000001`, `signature_valid: true`, visible at
both `/_arena/deliveries` and `/data/deliveries.jsonl`.
