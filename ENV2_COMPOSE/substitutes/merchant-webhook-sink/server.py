#!/usr/bin/env python3
"""merchant-webhook-sink: substitute "merchant server" receiving Stork webhook
deliveries. See CONTRACT.md. No Basic-Auth (real merchant endpoints don't
authenticate the sender) -- simply don't set STUB_BASIC_AUTH_FILE for this
container and _common/base_stub.py's _authorized() stays permissive.
"""
import hashlib
import hmac
import json
import os
import sys
import time

sys.path.insert(0, "/app")
from _common.base_stub import serve, _log  # noqa: E402

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
DELIVERIES_LOG_FILE = os.environ.get("DELIVERIES_LOG_FILE", "/data/deliveries.jsonl")

RECEIVED = []
MERCHANT_SECRETS = {}
SEED_FILE = os.environ.get("STORK_SEED_FILE", "/app/seed/subscriptions.json")
if os.path.exists(SEED_FILE):
    with open(SEED_FILE) as f:
        for webhook in json.load(f).get("webhooks", []):
            MERCHANT_SECRETS[webhook["owner_id"]] = webhook.get("secret", "")


def _append_deliveries_log(record):
    try:
        os.makedirs(os.path.dirname(DELIVERIES_LOG_FILE), exist_ok=True)
        with open(DELIVERIES_LOG_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as exc:
        _log("failed to append to %s: %r" % (DELIVERIES_LOG_FILE, exc))


def _receive(handler, body):
    # Path shape: /webhook/{merchant_id} (task spec) -- also accept the bare
    # /webhook (no merchant segment) for back-compat with any caller that
    # doesn't scope the URL per merchant.
    parts = [p for p in handler.path.split("/") if p]
    merchant = parts[1] if len(parts) >= 2 and parts[0] == "webhook" else ""

    event_id = handler.headers.get("X-Razorpay-Event-Id", "")
    request_id = handler.headers.get("Request-Id", "")
    signature = handler.headers.get("X-Razorpay-Signature", "")

    signature_valid = None
    secret = MERCHANT_SECRETS.get(merchant) or WEBHOOK_SECRET
    if secret:
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        signature_valid = hmac.compare_digest(expected, signature)

    try:
        parsed_body = json.loads(body or b"{}")
    except json.JSONDecodeError:
        parsed_body = {"_raw": body.decode("utf-8", errors="replace")}

    record = {
        "merchant": merchant,
        "path": handler.path,
        "headers": {k: v for k, v in handler.headers.items() if k.lower() in ("x-razorpay-event-id", "request-id", "content-type")},
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "event_id": event_id,
        "request_id": request_id,
        "signature_present": bool(signature),
        "signature_valid": signature_valid,
        "received_at": time.time(),
        "body": parsed_body,
    }
    RECEIVED.append(record)
    _append_deliveries_log(record)
    return (200 if signature_valid is True else 401), {"received": signature_valid is True}


def _received_events(handler, body):
    return 200, {"events": RECEIVED}


def _arena_deliveries(handler, body):
    # Read-only evidence scope. The full capture and append-only log remain
    # available; verifier namespaces need not observe unrelated merchants.
    from urllib.parse import urlsplit, parse_qs
    query = parse_qs(urlsplit(handler.path).query)
    merchant = query.get('merchant', [''])[0]
    payout_id = query.get('payout_id', [''])[0]
    rows = [row for row in RECEIVED
            if (not merchant or row.get('merchant') == merchant)
            and (not payout_id or row.get('body', {}).get('payload', {}).get('payout', {}).get('entity', {}).get('id') == payout_id)]
    return 200, {"deliveries": rows}


ROUTES = {
    ("POST", "/webhook"): _receive,
    ("GET", "/_received/events"): _received_events,
    ("GET", "/_arena/deliveries"): _arena_deliveries,
}

if __name__ == "__main__":
    serve(ROUTES)
