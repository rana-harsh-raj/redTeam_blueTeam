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
    if WEBHOOK_SECRET:
        expected = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        signature_valid = hmac.compare_digest(expected, signature)

    try:
        parsed_body = json.loads(body or b"{}")
    except json.JSONDecodeError:
        parsed_body = {"_raw": body.decode("utf-8", errors="replace")}

    record = {
        "merchant": merchant,
        "path": handler.path,
        "headers": {k: v for k, v in handler.headers.items()},
        "event_id": event_id,
        "request_id": request_id,
        "signature_present": bool(signature),
        "signature_valid": signature_valid,
        "received_at": time.time(),
        "body": parsed_body,
    }
    RECEIVED.append(record)
    _append_deliveries_log(record)
    return 200, {"received": True}


def _received_events(handler, body):
    return 200, {"events": RECEIVED}


def _arena_deliveries(handler, body):
    return 200, {"deliveries": RECEIVED}


ROUTES = {
    ("POST", "/webhook"): _receive,
    ("GET", "/_received/events"): _received_events,
    ("GET", "/_arena/deliveries"): _arena_deliveries,
}

if __name__ == "__main__":
    serve(ROUTES)
