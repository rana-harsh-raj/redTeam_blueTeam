#!/usr/bin/env python3
"""
Shared skeleton for Env 2 substitute (F2) stubs.

Every stub in substitutes/<name>/server.py imports this module and only
supplies its own ROUTES table. This file is stdlib-only (http.server) so the
stub containers need no package manager and no network access at build time.

Design constraints this file exists to satisfy (see ENV2_COMPOSE/README.md):
  - runtime image contains no credentials -- Basic-Auth pairs used to gate
    stub endpoints are read from files under /run/secrets (docker secrets),
    never baked into the image or source.
  - stub never calls out beyond the rzp-arena network -- it only answers
    inbound requests and, where the substitute spec requires a callback
    (Stork -> PS, Mozart -> FTS poll answers, monolith relay -> PS), it
    calls other *.rzp-arena service names only, resolved via arena DNS.
  - every stub answers GET /health with 200 unconditionally (no auth, no
    dependency checks) so compose healthchecks are cheap and reliable.
"""
import base64
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SERVICE_NAME = os.environ.get("STUB_NAME", "unknown-stub")
LISTEN_PORT = int(os.environ.get("STUB_PORT", "8080"))
AUTH_FILE = os.environ.get("STUB_BASIC_AUTH_FILE", "")  # "user:pass" contents, optional


def _load_expected_auth():
    if not AUTH_FILE or not os.path.exists(AUTH_FILE):
        return None
    with open(AUTH_FILE, "r") as f:
        content = f.read().strip()
    if not content:
        return None
    return content  # "user:pass"


EXPECTED_AUTH = _load_expected_auth()


def _log(msg):
    sys.stderr.write("[%s] %s %s\n" % (SERVICE_NAME, time.strftime("%Y-%m-%dT%H:%M:%S"), msg))
    sys.stderr.flush()


class StubHandler(BaseHTTPRequestHandler):
    server_version = "rzp-arena-stub/1.0"
    routes = {}  # populated by subclass module: {(METHOD, path_prefix): callable}

    def log_message(self, fmt, *args):
        _log(fmt % args)

    def _authorized(self):
        if EXPECTED_AUTH is None:
            return True  # stub has no configured credential -> open (dev-only, arena is internal-only)
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
        except Exception:
            return False
        return decoded == EXPECTED_AUTH

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self, method):
        if self.path == "/health" or self.path == "/ping":
            self._send_json(200, {"status": "ok", "service": SERVICE_NAME})
            return
        if not self._authorized():
            self._send_json(401, {"error": {"code": "BAD_REQUEST_ERROR", "description": "unauthorized"}})
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw_body = self.rfile.read(length) if length else b""
        # Longest-prefix-first: a route dict may register both "/x/Evaluate"
        # and "/x/EvaluateBulk" -- since the latter has the former as a
        # string prefix, naive insertion-order iteration can match the
        # wrong (shorter) route depending on dict order. Sorting by prefix
        # length descending makes matching order-independent and always
        # picks the most specific route, like a real router would.
        # Real callers address the monolith as /v1/<route> (payouts pkg/api: /v1/internal/merchants/…,
        # /v1/fund_accounts_internal/…); route tables are registered without the version prefix, so try
        # the raw path first and then the path with a leading /v1 stripped.
        candidates = [self.path]
        if self.path.startswith("/v1/"):
            candidates.append(self.path[3:])
        for candidate in candidates:
            for (m, prefix), handler in sorted(self.routes.items(), key=lambda kv: -len(kv[0][1])):
                if m == method and candidate.startswith(prefix):
                    self.path = candidate
                    try:
                        status, payload = handler(self, raw_body)
                    except Exception as exc:  # noqa: BLE001 - stub must never crash the process
                        _log("handler error on %s %s: %r" % (method, self.path, exc))
                        status, payload = 500, {"error": {"code": "SERVER_ERROR", "description": "stub_internal_error: %s" % exc}}
                    self._send_json(status, payload)
                    return
        # object-shaped error: payouts' api.ErrorResponse unmarshals "error" into a struct
        self._send_json(404, {"error": {"code": "BAD_REQUEST_ERROR", "description": "no_stub_route %s %s" % (method, self.path)}})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_PUT(self):
        self._dispatch("PUT")


def serve(routes):
    StubHandler.routes = routes
    addr = ("0.0.0.0", LISTEN_PORT)
    httpd = ThreadingHTTPServer(addr, StubHandler)
    _log("listening on %s (routes=%d)" % (str(addr), len(routes)))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
