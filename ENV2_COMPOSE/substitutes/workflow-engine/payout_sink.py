#!/usr/bin/env python3
"""payout-sink — a minimal stand-in for the payout side that RECEIVES the
engine's terminal callbacks, so callback identity / retry / idempotency are
observable without booting the full 66-container arena.

It mirrors the payout-side contract the real engine talks to:
  * requires HTTP Basic (rzp_live + shared secret) — a forged/absent identity
    is rejected 401 (callback-identity enforcement);
  * treats a repeated Idempotency-Key as already-applied -> HTTP 409 (which the
    engine counts as success), proving delivery idempotency;
  * records every delivery to a JSONL file for evidence.

Env: SINK_HOST/SINK_PORT, SINK_USER/SINK_PASS, SINK_LOG.
"""
import base64
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USER = os.environ.get("SINK_USER", "rzp_live")
PASS = os.environ.get("SINK_PASS", "workflow-secret")
LOG = os.environ.get("SINK_LOG", "./payout_sink.jsonl")
_seen = {}
_lock = threading.Lock()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _basic_ok(self):
        h = self.headers.get("Authorization", "")
        if not h.startswith("Basic "):
            return False
        try:
            u, _, p = base64.b64decode(h[6:]).decode().partition(":")
        except Exception:
            return False
        return u == USER and p == PASS

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"status": "ok", "service": "payout-sink"})
        if self.path == "/_deliveries":
            with _lock:
                return self._send(200, {"deliveries": list(_seen.values())})
        return self._send(404, {"error": "no_route"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b""
        idem = self.headers.get("Idempotency-Key", "")
        rec = {
            "path": self.path,
            "creator": self.headers.get("x-creator-id"),
            "account": self.headers.get("X-Razorpay-Account"),
            "idem": idem,
            "authed": self._basic_ok(),
            "at": time.time(),
        }
        with open(LOG, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        # [callback-identity] reject an unauthenticated / forged caller
        if not self._basic_ok():
            return self._send(401, {"error": "unauthenticated_callback"})
        with _lock:
            if idem and idem in _seen:
                # [idempotency] already applied -> 409 (engine treats as success)
                return self._send(409, {"status": "already_applied", "idem": idem})
            if idem:
                _seen[idem] = rec
        return self._send(200, {"status": "applied"})


def main():
    host = os.environ.get("SINK_HOST", "127.0.0.1")
    port = int(os.environ.get("SINK_PORT", "8097"))
    httpd = ThreadingHTTPServer((host, port), H)
    print(f"payout-sink up on http://{host}:{port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
