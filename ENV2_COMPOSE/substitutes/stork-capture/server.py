#!/usr/bin/env python3
"""stork-capture: substitute for Stork's WebhookAPI/SMSAPI/EmailAPI. See CONTRACT.md.

Wire shapes and delivery/signature behaviour are copied verbatim from
findings/25_stork_mozart.md section A (real stork repo + proto/stork/**
research). Delivers synchronously (real Stork is async via SQS workers; this
stub has no queue to simulate, so ProcessEvent fans out inline).
"""
import hashlib
import hmac
import itertools
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, "/app")
from _common.base_stub import serve, _log  # noqa: E402

MAX_WEBHOOKS_PER_OWNER = 30
DELIVERY_TIMEOUT_SEC = float(os.environ.get("STORK_DELIVERY_TIMEOUT_SEC", "5"))
SEED_FILE = os.environ.get("STORK_SEED_FILE", "/app/seed/subscriptions.json")
EVENTS_LOG_FILE = os.environ.get("STORK_EVENTS_LOG_FILE", "/data/events.jsonl")
# STORK_REORDER=1: delay every even-indexed (0th, 2nd, ...) delivery for a
# given owner_id by STORK_REORDER_DELAY_SEC so the odd-indexed one arrives
# first -- a simple, deterministic out-of-order delivery simulation for V21
# (merchant-side idempotency/ordering-handling tests). Real Stork does not
# guarantee delivery order either (findings/25 §A.4), this just makes the
# violation reproducible on demand instead of accidental/rare.
REORDER = os.environ.get("STORK_REORDER", "0") == "1"
REORDER_DELAY_SEC = float(os.environ.get("STORK_REORDER_DELAY_SEC", "0.3"))

_id_counter = itertools.count(1)
_owner_delivery_count = {}  # owner_id -> int, used by the REORDER pairing above


def _gen_id(prefix):
    return "%s_ARENA%06d" % (prefix, next(_id_counter))


# owner_id -> list of webhook dicts {id, service, owner_id, owner_type, url, secret, subscriptions:[event_name,...]}
WEBHOOKS = {}
CAPTURED_EVENTS = []  # debug log for GET /_captured/events and /_arena/events
CAPTURED_MESSAGES = []  # SMS/email sends


def _append_events_log(record):
    try:
        os.makedirs(os.path.dirname(EVENTS_LOG_FILE), exist_ok=True)
        with open(EVENTS_LOG_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as exc:
        _log("failed to append to %s: %r" % (EVENTS_LOG_FILE, exc))


def _seed_webhook(wh):
    owner_id = wh.get("owner_id", "")
    record = {
        "id": _gen_id("wh"),
        "service": wh.get("service", ""),
        "owner_id": owner_id,
        "owner_type": wh.get("owner_type", "merchant"),
        "url": wh.get("url", ""),
        "secret": wh.get("secret", ""),
        "subscriptions": [s.get("eventmeta", {}).get("name", "") for s in wh.get("subscriptions", [])],
        "disabled": wh.get("disabled", False),
    }
    WEBHOOKS.setdefault(owner_id, []).append(record)


def _load_seed():
    if not os.path.exists(SEED_FILE):
        _log("no seed file at %s -- stork-capture starting with no pre-registered webhooks" % SEED_FILE)
        return
    with open(SEED_FILE) as f:
        data = json.load(f)
    for wh in data.get("webhooks", []):
        _seed_webhook(wh)
    _log("loaded %d webhook subscription(s) from %s" % (sum(len(v) for v in WEBHOOKS.values()), SEED_FILE))


_load_seed()


def _create_webhook(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    wh = req.get("webhook", {})
    owner_id = wh.get("owner_id", "")
    existing = WEBHOOKS.setdefault(owner_id, [])
    if len(existing) >= MAX_WEBHOOKS_PER_OWNER:
        return 400, {"error": "max_webhooks_per_owner_exceeded"}
    _seed_webhook(wh)
    record = existing[-1]
    resp = dict(record)
    resp.pop("secret", None)
    resp["secret_exists"] = bool(record["secret"])
    return 200, {"webhook": resp}


def _list_webhooks(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    owner_id = req.get("owner_id", "")
    records = []
    for wh in WEBHOOKS.get(owner_id, []):
        r = dict(wh)
        r.pop("secret", None)
        r["secret_exists"] = bool(wh["secret"])
        # proto shape (rpc/stork/webhook/v1): subscriptions are Subscription messages {eventmeta{name}}
        r["subscriptions"] = [sub if isinstance(sub, dict) else {"eventmeta": {"name": sub}} for sub in wh.get("subscriptions", [])]
        records.append(r)
    return 200, {"webhooks": records}


def _deliver(webhook, event):
    event_id = _gen_id("ev")
    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Event-Id": event_id,
        "Request-Id": event_id,
    }
    # Delivered body = the raw payload JSON (payouts/pkg/stork/process_event.go
    # builds Event.Payload as a JSON-ENCODED STRING of ProcessEventPayload --
    # that string, not the outer Event envelope, is what a merchant actually
    # receives and verifies -- so the HMAC signs exactly the bytes sent, and
    # a real merchant-side verifier (HMAC over the raw received body) matches.
    payload_str = event.get("payload", "") or "{}"
    body = payload_str.encode("utf-8")
    if webhook.get("secret"):
        sig = hmac.new(webhook["secret"].encode(), body, hashlib.sha256).hexdigest()
        headers["X-Razorpay-Signature"] = sig
    req = urllib.request.Request(webhook["url"], data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=DELIVERY_TIMEOUT_SEC) as resp:
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except Exception as exc:  # noqa: BLE001 - matches real Stork: delivery failure is async/best-effort
        sys.stderr.write("[stork-capture] delivery to %s failed: %r\n" % (webhook["url"], exc))
        status = None
    record = {
        "event_id": event_id, "webhook_id": webhook["id"], "url": webhook["url"],
        "owner_id": webhook.get("owner_id", ""), "merchant": webhook.get("owner_id", ""),
        "event_name": event.get("name"), "delivered_status": status, "delivered_at": time.time(),
    }
    CAPTURED_EVENTS.append(record)
    _append_events_log(dict(record, event=event))


def _process_event(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    event = req.get("event", {})
    if not event.get("id"):
        event["id"] = _gen_id("ev")
    owner_id = event.get("owner_id", "")
    event_name = event.get("name", "")
    for wh in WEBHOOKS.get(owner_id, []):
        if wh.get("disabled"):
            continue
        if event_name not in wh.get("subscriptions", []):
            continue
        if REORDER:
            n = _owner_delivery_count.get(owner_id, 0)
            _owner_delivery_count[owner_id] = n + 1
            if n % 2 == 0:
                # delay this (even-indexed) delivery so the NEXT (odd-indexed)
                # call's delivery -- fired without delay -- lands first.
                threading.Timer(REORDER_DELAY_SEC, _deliver, args=(wh, event)).start()
                continue
        _deliver(wh, event)
    return 200, {"event": event}


def _sms_send(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    msg_id = _gen_id("sms")
    CAPTURED_MESSAGES.append({"type": "sms", "message_id": msg_id, "request": req, "at": time.time()})
    return 200, {"message_id": msg_id, "service": req.get("service", ""),
                 "owner_id": req.get("owner_id", ""), "owner_type": req.get("owner_type", ""),
                 "context": req.get("context", {})}


def _email_send(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    msg_id = _gen_id("email")
    CAPTURED_MESSAGES.append({"type": "email", "message_id": msg_id, "request": req, "at": time.time()})
    return 200, {"message_id": msg_id, "service": req.get("service", ""),
                 "owner_id": req.get("owner_id", ""), "owner_type": req.get("owner_type", ""),
                 "context": req.get("context", {})}


def _captured_events(handler, body):
    return 200, {"events": CAPTURED_EVENTS, "messages": CAPTURED_MESSAGES}


def _arena_events(handler, body):
    """GET /_arena/events?merchant=<owner_id> -- task-specified debug endpoint,
    an alias for /_captured/events with optional owner_id filtering."""
    parsed = urllib.parse.urlparse(handler.path)
    qs = urllib.parse.parse_qs(parsed.query)
    merchant = (qs.get("merchant") or [None])[0]
    events = CAPTURED_EVENTS if merchant is None else [e for e in CAPTURED_EVENTS if e.get("owner_id") == merchant]
    return 200, {"events": events}


ROUTES = {
    ("POST", "/twirp/rzp.stork.webhook.v1.WebhookAPI/Create"): _create_webhook,
    ("POST", "/twirp/rzp.stork.webhook.v1.WebhookAPI/List"): _list_webhooks,
    ("POST", "/twirp/rzp.stork.webhook.v1.WebhookAPI/ProcessEvent"): _process_event,
    ("POST", "/twirp/rzp.stork.sms.v1.SMSAPI/Send"): _sms_send,
    ("POST", "/twirp/rzp.stork.email.v1.EmailAPI/Send"): _email_send,
    ("GET", "/_captured/events"): _captured_events,
    ("GET", "/_arena/events"): _arena_events,
}

if __name__ == "__main__":
    serve(ROUTES)
