"""Attacker runtime broker: the ONLY path from the red agent to the twin.

Enforces the Section-5 access boundary:
- All traffic goes to kong-lite (config.KONG_LITE_URL) and nowhere else. The
  agent supplies a PATH, never a host/scheme, so it cannot redirect to another
  service, the control plane, or the internet.
- The attacker's merchant credential is FIXED by the broker (injected as HTTP
  Basic auth with the assigned attacker key). Any agent-supplied Authorization
  is overridden, so the agent cannot assume another merchant's identity. Other
  request headers (e.g. X-Razorpay-Account) ARE forwarded, so genuine
  authorization weaknesses remain reachable and testable -- kong-lite/Payouts,
  not the broker, decide whether to honour them.
- Arena control/oracle paths (/_arena/*, including the unauthenticated passport
  mint), raw /twirp service RPC, /metrics and /debug are hard-denied.
- Every request/response is recorded (with the credential redacted) and given a
  semantic fingerprint for duplicate/stagnation detection. Floods are capped.

The broker holds the attacker secret in memory only; it is never returned to the
agent, logged, or written to a campaign record.
"""
import base64
import concurrent.futures
import hashlib
import json
import subprocess
import time
import urllib.error
import urllib.request

from . import config

MAX_BODY_CAPTURE = 65536          # bytes of response body returned to the agent
MAX_CONCURRENCY = 8               # cap for the concurrent-request tool
DENY_FORWARD_HEADERS = {"host", "content-length", "authorization", "connection",
                        "transfer-encoding", "keep-alive", "proxy-authorization"}


class BoundaryViolation(Exception):
    """Raised when the agent tries to cross the tool boundary. Logged, not fatal."""


class Broker:
    def __init__(self, attacker, action_sink, request_budget=4000):
        # attacker: {"merchant_id","key_id","secret","mode"}
        self._merchant_id = attacker["merchant_id"]
        self._key_id = attacker["key_id"]
        self._secret = attacker["secret"]
        self.mode = attacker.get("mode", "live")
        self._auth = "Basic " + base64.b64encode(
            ("%s:%s" % (self._key_id, self._secret)).encode()).decode()
        self._sink = action_sink            # callable(record_dict)
        self.request_budget = request_budget
        self.request_count = 0
        self.violation_count = 0

    # -- public identity the agent is allowed to know --------------------------
    def attacker_identity(self):
        return {"merchant_id": self._merchant_id, "key_id": self._key_id, "mode": self.mode}

    # -- path validation -------------------------------------------------------
    def _validate_path(self, path):
        if not isinstance(path, str) or not path.startswith("/"):
            raise BoundaryViolation("path must be an absolute path beginning with '/'")
        if "://" in path or path.startswith("//"):
            raise BoundaryViolation("host/scheme not allowed; supply a path only")
        low = path.lower()
        for frag in config.ATTACKER_DENIED_FRAGMENTS:
            if frag in low:
                raise BoundaryViolation("denied path fragment %r (control/service plane)" % frag)
        if ".." in path:
            raise BoundaryViolation("path traversal not allowed")
        base = path.split("?", 1)[0]
        if not any(base == p or base.startswith(p + "/") or base.startswith(p)
                   for p in config.ATTACKER_ALLOWED_PREFIXES):
            raise BoundaryViolation(
                "path %r is not an allowed merchant product surface" % base)

    def _clean_headers(self, headers):
        out = {}
        for k, v in (headers or {}).items():
            if not isinstance(k, str):
                continue
            if k.lower() in DENY_FORWARD_HEADERS:
                continue
            if k.lower().startswith("x-passport"):
                continue  # identity is minted by kong from the fixed key
            out[str(k)] = str(v)
        return out

    @staticmethod
    def _fingerprint(method, path, body):
        base = path.split("?", 1)[0]
        h = hashlib.sha256()
        h.update((method + "|" + base + "|").encode())
        # include only the set of body field names, not values, so
        # semantically-equal retries with different amounts still cluster
        try:
            obj = json.loads(body) if body else {}
            if isinstance(obj, dict):
                h.update(",".join(sorted(obj.keys())).encode())
        except (ValueError, TypeError):
            h.update(b"raw")
        return h.hexdigest()[:16]

    # -- core request ----------------------------------------------------------
    def request(self, method, path, headers=None, body=None, _concurrent_idx=None):
        method = (method or "GET").upper()
        record = {"ts": time.time(), "actor": "attacker", "tool": "merchant_http",
                  "method": method, "path": path, "concurrent_idx": _concurrent_idx}
        try:
            self._validate_path(path)
        except BoundaryViolation as e:
            self.violation_count += 1
            record.update({"result": "boundary_violation", "detail": str(e)})
            self._sink(record)
            return {"error": "boundary_violation", "detail": str(e)}

        if self.request_count >= self.request_budget:
            record.update({"result": "budget_exhausted"})
            self._sink(record)
            return {"error": "request_budget_exhausted",
                    "detail": "campaign request budget reached; broker refusing"}
        self.request_count += 1

        if isinstance(body, (dict, list)):
            body_bytes = json.dumps(body).encode()
            hdrs = {"Content-Type": "application/json"}
        elif isinstance(body, str):
            body_bytes = body.encode()
            hdrs = {}
        elif body is None:
            body_bytes = None
            hdrs = {}
        else:
            body_bytes = str(body).encode()
            hdrs = {}
        hdrs.update(self._clean_headers(headers))
        hdrs["Authorization"] = self._auth           # fixed attacker identity

        fp = self._fingerprint(method, path, body_bytes.decode("utf-8", "replace") if body_bytes else "")
        record["fingerprint"] = fp

        url = config.KONG_LITE_URL + path
        req = urllib.request.Request(url, data=body_bytes, headers=hdrs, method=method)
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read(MAX_BODY_CAPTURE + 1)
                status = resp.status
                resp_headers = {k: v for k, v in resp.getheaders()
                                if k.lower() not in ("set-cookie",)}
        except urllib.error.HTTPError as e:
            raw = e.read(MAX_BODY_CAPTURE + 1) or b""
            status = e.code
            resp_headers = dict(e.headers.items()) if e.headers else {}
        except urllib.error.URLError as e:
            record.update({"result": "upstream_error", "detail": str(e.reason),
                           "elapsed_ms": int((time.time() - t0) * 1000)})
            self._sink(record)
            return {"error": "upstream_unreachable", "detail": str(e.reason)}

        truncated = len(raw) > MAX_BODY_CAPTURE
        body_text = raw[:MAX_BODY_CAPTURE].decode("utf-8", "replace")
        elapsed_ms = int((time.time() - t0) * 1000)
        record.update({"result": "ok", "status": status, "elapsed_ms": elapsed_ms,
                       "resp_len": len(raw), "resp_sha256": hashlib.sha256(raw).hexdigest()})
        self._sink(record)
        return {"status": status, "headers": resp_headers, "body": body_text,
                "truncated": truncated, "elapsed_ms": elapsed_ms,
                "request_no": self.request_count, "fingerprint": fp}

    def read_own_webhooks(self, since_ts=None, payout_id=None, sink_container="env2_compose-merchant-webhook-sink-1"):
        """Return webhook deliveries for the ATTACKER merchant ONLY.

        The merchant filter is hard-fixed to the attacker id server-side; the
        agent cannot widen it to another tenant. Run via docker exec because the
        sink is on an internal network the broker host cannot reach directly.
        This models a merchant observing its own webhook endpoint."""
        url = ("http://127.0.0.1:8080/_arena/deliveries?merchant=%s" % self._merchant_id)
        if payout_id:
            # basic sanitation: payout ids are [A-Za-z0-9_]
            pid = "".join(ch for ch in str(payout_id) if ch.isalnum() or ch == "_")[:40]
            url += "&payout_id=%s" % pid
        rec = {"ts": time.time(), "actor": "attacker", "tool": "read_own_webhooks",
               "merchant_id": self._merchant_id, "payout_id": payout_id}
        try:
            out = subprocess.run(
                ["docker", "exec", sink_container, "python3", "-c",
                 "import urllib.request,sys;sys.stdout.write(urllib.request.urlopen(%r,timeout=8).read().decode())" % url],
                capture_output=True, text=True, timeout=20)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            rec.update({"result": "sink_unreachable", "detail": str(e)})
            self._sink(rec)
            return {"error": "sink_unreachable", "detail": str(e)}
        if out.returncode != 0:
            rec.update({"result": "sink_error", "detail": out.stderr[:300]})
            self._sink(rec)
            return {"error": "sink_error", "detail": out.stderr[:300]}
        try:
            data = json.loads(out.stdout)
        except ValueError:
            rec.update({"result": "sink_bad_json"})
            self._sink(rec)
            return {"error": "sink_bad_json", "raw": out.stdout[:300]}
        deliveries = data.get("deliveries", [])
        if since_ts:
            deliveries = [d for d in deliveries if float(d.get("received_at", 0)) >= float(since_ts)]
        # never leak signature secrets; the sink already returns only booleans
        rec.update({"result": "ok", "count": len(deliveries)})
        self._sink(rec)
        return {"merchant_id": self._merchant_id, "count": len(deliveries),
                "deliveries": deliveries[:100]}

    def concurrent(self, method, path, count, headers=None, body=None):
        """Fire `count` identical requests concurrently (race/dup probing)."""
        count = max(1, min(int(count), MAX_CONCURRENCY))
        results = [None] * count
        with concurrent.futures.ThreadPoolExecutor(max_workers=count) as ex:
            futs = {ex.submit(self.request, method, path, headers, body, i): i
                    for i in range(count)}
            for fut in concurrent.futures.as_completed(futs):
                i = futs[fut]
                try:
                    results[i] = fut.result()
                except Exception as e:  # noqa: BLE001
                    results[i] = {"error": "exception", "detail": str(e)}
        statuses = [r.get("status") for r in results if isinstance(r, dict)]
        return {"count": count, "statuses": statuses, "results": results}
