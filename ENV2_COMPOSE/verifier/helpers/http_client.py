"""Minimal HTTP client (stdlib urllib only -- no ``requests`` dependency, to
keep the verifier package to exactly the dependency list in the task brief:
stdlib + pymysql + psycopg[binary] + pymongo + redis).

Supports HTTP Basic auth and an optional ``X-Passport-JWT-V1`` header, per
VERIFIER_SPEC.md's auth conventions section.
"""
import base64
import json
import urllib.error
import urllib.request
from . import trace


class HTTPResponse:
    def __init__(self, status, headers, body_bytes):
        self.status = status
        self.headers = headers
        self._body = body_bytes or b""

    @property
    def text(self):
        return self._body.decode("utf-8", errors="replace")

    def json(self):
        if not self._body:
            return {}
        return json.loads(self._body)

    def __repr__(self):
        return "HTTPResponse(status=%s, body=%r)" % (self.status, self._body[:500])


class ServiceUnreachable(Exception):
    """Raised instead of a raw urllib error so callers can pytest.skip cleanly."""


class ArenaHTTPClient:
    def __init__(self, base_url, basic_auth=None, timeout=15, extra_headers=None):
        self.base_url = base_url.rstrip("/")
        self.basic_auth = basic_auth
        self.timeout = timeout
        self.extra_headers = extra_headers or {}

    def _headers(self, headers=None, passport_jwt=None):
        h = {"Content-Type": "application/json"}
        h.update(self.extra_headers)
        if headers:
            h.update(headers)
        if self.basic_auth:
            user, pw = self.basic_auth
            token = base64.b64encode(("%s:%s" % (user, pw)).encode()).decode()
            h["Authorization"] = "Basic %s" % token
        if passport_jwt:
            h["X-Passport-JWT-V1"] = passport_jwt
        return h

    def request(self, method, path, body=None, headers=None, passport_jwt=None, timeout=None):
        url = path if path.startswith("http") else self.base_url + path
        data = None
        if body is not None:
            data = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method,
                                      headers=self._headers(headers, passport_jwt))
        trace.record("http_request", {"method":method,"url":url,"body":body})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                result = HTTPResponse(resp.status, dict(resp.getheaders()), resp.read())
                try: payload = result.json()
                except ValueError: payload = result.text
                trace.record("http_response", {"method":method,"url":url,"status":result.status,"body":payload})
                return result
        except urllib.error.HTTPError as exc:
            result = HTTPResponse(exc.code, dict(exc.headers or {}), exc.read() or b"{}")
            trace.record("http_response", {"method":method,"url":url,"status":result.status,"body":result.text})
            return result
        except urllib.error.URLError as exc:
            raise ServiceUnreachable("%s %s unreachable: %s" % (method, url, exc.reason)) from exc
        except (ConnectionError, TimeoutError, OSError) as exc:
            raise ServiceUnreachable("%s %s unreachable: %s" % (method, url, exc)) from exc

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body=body, **kw)

    def patch(self, path, body=None, **kw):
        return self.request("PATCH", path, body=body, **kw)

    def put(self, path, body=None, **kw):
        return self.request("PUT", path, body=body, **kw)

    def health_ok(self, path="/health"):
        try:
            resp = self.get(path, timeout=5)
        except ServiceUnreachable:
            return False
        return 200 <= resp.status < 300
