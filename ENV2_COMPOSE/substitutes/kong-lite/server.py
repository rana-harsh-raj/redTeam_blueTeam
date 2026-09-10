#!/usr/bin/env python3
"""kong-lite: minimal reverse proxy standing in for Kong. See CONTRACT.md.

Adds, on top of the base proxy: Basic-Auth merchant-key verification
against seeds/merchants.json, X-Passport-JWT-V1 minting (RS256, pure-stdlib
signer in _common/rsa_sign.py -- see that module's header for why no
cryptography/pyjwt dependency was added), a service Basic-Auth header for
the payouts-api call (cred.API, per payouts/internal/routing/router/
payout_routes.go's `middleware.BasicAuth(cred.API, cred.Workflow)`), and
stripping the client's own inbound Authorization header before forwarding.

Not built on _common/base_stub.py (that module answers locally; this one
must forward bytes to an upstream arena service), but mirrors its
logging/health conventions.
"""
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "/app")
from _common.rsa_sign import (  # noqa: E402
    parse_rsa_private_key_der, pem_to_der, rsa_sign_pkcs1v15_sha256, b64url,
)

SERVICE_NAME = "kong-lite"
LISTEN_PORT = int(os.environ.get("STUB_PORT", "8080"))

DEFAULT_ROUTES = {
    "/v1/payouts": "http://payouts-api:9400",
    "/v1/contacts": "http://payouts-api:9400",
    "/v1/fund_accounts": "http://payouts-api:9400",
    "/twirp/rzp.payouts": "http://payouts-api:9400",
    "/twirp/ledger": "http://ledger-api:8080",
    "/v1/fts": "http://fts-web:8080",
    "/twirp/cfa": "http://cfa-server:8081",
    "/v1/balances": "http://xbalances-server:8080",
}

MERCHANTS_FILE = os.environ.get("KONG_MERCHANTS_FILE", "/app/seed/merchants.json")
SECRETS_DIR = os.environ.get("KONG_MERCHANT_SECRETS_DIR", "/run/secrets/merchants")
PASSPORT_PRIVATE_KEY_FILE = os.environ.get("PASSPORT_PRIVATE_KEY_FILE", "/run/secrets/passport_private_key")
PASSPORT_IDENTIFIER = os.environ.get("PASSPORT_IDENTIFIER", "arena-passport-1")
PASSPORT_ORG = os.environ.get("PASSPORT_ORG", "ARENAORG000001")
PASSPORT_PRODUCT = os.environ.get("PASSPORT_PRODUCT", "banking")
# Synthetic issuer identity; the pinned Passport handler verifies the configured kid/signature.
PASSPORT_ISS = os.environ.get("PASSPORT_ISS", "https://identity.arena.invalid")
PASSPORT_SUB = os.environ.get("PASSPORT_SUB", "urn:arena:payouts")
PASSPORT_TTL_SEC = int(os.environ.get("PASSPORT_TTL_SEC", "300"))  # real edge default, findings/21 §2

# cred.API -- the Basic-Auth pair payouts' inbound `middleware.BasicAuth(cred.API,
# cred.Workflow)` accepts on the /v1/payouts route group (payout_routes.go).
PS_API_AUTH_USER = os.environ.get("PS_API_AUTH_USER", "api")
PS_API_AUTH_PASS_FILE = os.environ.get("PS_API_AUTH_PASS_FILE", "/run/secrets/auth_api_payouts")

# --- ARENA CONTROL: opt-in dashboard/proxy passport shape -------------------
# NOT a production behaviour. In production the dashboard/proxy passport is
# minted by the monolith's BasicAuth (api/app/Http/BasicAuth/BasicAuth.php:1246
# setPassportImpersonationClaims(PASSPORT_IMPERSONATION_TYPE_USER_MERCHANT,
# merchantId), constant at :143) and serialised by
# edge/kong-plugins/kong-plugin-upstream-jwt/kong/plugins/upstream-jwt/access.lua
# :135-152; there is no session/user store in the arena to derive it from.
#
# These request headers let a caller that has ALREADY authenticated with the
# merchant's own API key ask kong-lite for that claim shape. The merchant
# identity still comes from the verified credential -- the headers only choose
# the consumer/impersonation *shape* and name the dashboard user -- so no
# cross-merchant identity can be asserted. They are consumed here and never
# forwarded upstream. Default behaviour (no headers) is byte-identical to before.
ARENA_CONSUMER_TYPE_HEADER = "X-Arena-Passport-Consumer-Type"
ARENA_USER_ID_HEADER = "X-Arena-User-Id"
ARENA_USER_ROLE_HEADER = "X-Dashboard-User-Role"
ARENA_CONTROL_HEADERS = {ARENA_CONSUMER_TYPE_HEADER.lower(), ARENA_USER_ID_HEADER.lower(),
                         ARENA_USER_ROLE_HEADER.lower()}
# goutils/passport helpers.go:32 impersonationTypeUserMerchant; the same literal
# the monolith constant PASSPORT_IMPERSONATION_TYPE_USER_MERCHANT carries.
PASSPORT_IMPERSONATION_TYPE_USER_MERCHANT = "user_merchant"
# goutils/passport helpers.go:14-18 ConsumerTypeUser / ConsumerTypeMerchant.
PASSPORT_CONSUMER_TYPE_USER = "user"
PASSPORT_CONSUMER_TYPE_MERCHANT = "merchant"
_ARENA_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,40}$")


def _log(msg):
    sys.stderr.write("[%s] %s %s\n" % (SERVICE_NAME, time.strftime("%Y-%m-%dT%H:%M:%S"), msg))
    sys.stderr.flush()


def _load_routes():
    raw = os.environ.get("KONG_LITE_ROUTES_JSON", "")
    if not raw:
        return dict(DEFAULT_ROUTES)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        _log("KONG_LITE_ROUTES_JSON invalid, falling back to defaults: %r" % exc)
        return dict(DEFAULT_ROUTES)


ROUTES = _load_routes()

# --- M3 merchant-edge route policy (source-derived) ------------------------
# Source of truth: RED_LOOP/registry/merchant-gateway-routes.json. Compiled
# here so kong-lite stays self-contained (stdlib-only, no extra mount).
#
# Default OFF: when KONG_ENFORCE_ROUTE_POLICY != "1", kong-lite behaves exactly
# as frozen Twin v1.0 (whole-prefix proxy + uniform cred.API injection), so the
# frozen verifier suite is behaviour-neutral. When ON, only PUBLIC merchant
# routes are proxied; internal/admin/workflow routes that share the /v1/payouts
# prefix return 404 as a real public edge (that never registered them) would;
# cred.API is injected ONLY for payouts-api-bound public routes (never for the
# xbalances /v1/balances read surface); and client-supplied identity headers
# (x-merchant-id / x-entity-id) are stripped so a merchant cannot assert an
# identity the edge is responsible for minting. See payout_routes.go et al.
from route_policy import classify_request, STRIP_IDENTITY_HEADERS as _STRIP_IDENTITY_HEADERS

ENFORCE_ROUTE_POLICY = os.environ.get("KONG_ENFORCE_ROUTE_POLICY", "0") == "1"


# key_id ("rzp_test_..."/"rzp_live_...") -> {merchant_id, mode, secret, roles}
KEY_TABLE = {}


def _read_secret_file(path):
    if not path or not os.path.exists(path):
        return ""
    with open(path) as f:
        return f.read().strip()


def _load_merchants():
    if not os.path.exists(MERCHANTS_FILE):
        _log("no merchants file at %s -- kong-lite will reject ALL Basic-Auth" % MERCHANTS_FILE)
        return
    with open(MERCHANTS_FILE) as f:
        data = json.load(f)
    for mid, rec in data.get("merchants", {}).items():
        secret_file = rec.get("secret_file", "")
        secret = _read_secret_file(os.path.join(SECRETS_DIR, "%s.txt" % secret_file))
        for key_id, mode in ((rec.get("key_id_test"), "test"), (rec.get("key_id_live"), "live")):
            if not key_id:
                continue
            KEY_TABLE[key_id] = {"merchant_id": mid, "mode": mode, "secret": secret,
                                  "roles": rec.get("roles", [])}
    _log("loaded %d API key(s) for %d merchant(s) from %s" %
         (len(KEY_TABLE), len(data.get("merchants", {})), MERCHANTS_FILE))


_load_merchants()

# --- passport signing key, loaded once at boot ---
_PASSPORT_N = _PASSPORT_D = _PASSPORT_KEY_SIZE = None
if os.path.exists(PASSPORT_PRIVATE_KEY_FILE):
    with open(PASSPORT_PRIVATE_KEY_FILE) as f:
        _priv_pem = f.read()
    _PASSPORT_N, _PASSPORT_D, _PASSPORT_KEY_SIZE = parse_rsa_private_key_der(pem_to_der(_priv_pem))
    _log("loaded passport signing key (kid=%s, %d-bit)" % (PASSPORT_IDENTIFIER, _PASSPORT_KEY_SIZE * 8))
else:
    _log("WARNING: no passport private key at %s -- passport minting will fail closed (401)" %
         PASSPORT_PRIVATE_KEY_FILE)

_PS_API_PASSWORD = _read_secret_file(PS_API_AUTH_PASS_FILE)


def _match_upstream(path):
    best = None
    for prefix, upstream in ROUTES.items():
        if path.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, upstream)
    return best[1] if best else None


def _passport_claims(merchant_id, mode, roles, dashboard_user_id=None, now=None):
    """The passport payload. Split out of _mint_passport_jwt so the claim shape can
    be asserted offline without a signing key (tests/test_passport_claims.py).

    Default (dashboard_user_id is None) -- merchant API-key private auth,
    findings/21 §2.2(a): consumer {id: <merchant>, type: "merchant"}. goutils
    passport helpers.go:207 GetLegacyAuthType maps that to LegacyAuthTypePrivate.

    Dashboard/proxy shape (dashboard_user_id set) -- what payouts'
    internal/app/payouts/validation.go ValidateCancel requires before it will
    cancel a SCHEDULED payout (`authType != passportSdk.LegacyAuthTypeProxy` ->
    CancelScheduledPayoutInvalidAuth). helpers.go:212 returns
    LegacyAuthTypeProxy only for
    consumer.Type == ConsumerTypeUser AND impersonation != nil AND
    impersonation.Type == "user_merchant"; internal/auth/authHelper.go
    getMerchantIdFromPassport then reads the merchant from
    impersonation.Consumer (not from consumer), which is why the merchant id
    moves into the impersonation block. Wire field names follow access.lua
    :146-152 (`impersonation.type`, `impersonation.consumer.{id,type}`); the Go
    ImpersonationClaims struct has no json tags, and encoding/json matches field
    names case-insensitively, so the lowercase keys the edge emits bind to
    Type/Consumer.
    """
    now = int(time.time()) if now is None else int(now)
    payload = {
        "iss": PASSPORT_ISS,
        "sub": PASSPORT_SUB,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "nbf": now,
        "exp": now + PASSPORT_TTL_SEC,
        "identified": True,
        "authenticated": True,
        "mode": mode,
        "org": PASSPORT_ORG,
        "product": PASSPORT_PRODUCT,
        "consumer": {"id": merchant_id, "type": PASSPORT_CONSUMER_TYPE_MERCHANT},
    }
    if dashboard_user_id:
        payload["consumer"] = {"id": dashboard_user_id, "type": PASSPORT_CONSUMER_TYPE_USER}
        payload["impersonation"] = {
            "type": PASSPORT_IMPERSONATION_TYPE_USER_MERCHANT,
            "consumer": {"id": merchant_id, "type": PASSPORT_CONSUMER_TYPE_MERCHANT},
        }
    if roles:
        payload["roles"] = list(roles)
    return payload


def _mint_passport_jwt(merchant_id, mode, roles, dashboard_user_id=None):
    if _PASSPORT_N is None:
        return None
    header = {"typ": "JWT", "alg": "RS256", "kid": PASSPORT_IDENTIFIER}
    payload = _passport_claims(merchant_id, mode, roles, dashboard_user_id)
    signing_input = (b64url(json.dumps(header, separators=(",", ":")).encode()) + "." +
                      b64url(json.dumps(payload, separators=(",", ":")).encode())).encode()
    sig = rsa_sign_pkcs1v15_sha256(signing_input, _PASSPORT_N, _PASSPORT_D, _PASSPORT_KEY_SIZE)
    return signing_input.decode() + "." + b64url(sig)


def _dashboard_override(headers):
    """ARENA CONTROL. Read the opt-in dashboard/proxy passport request headers.

    Returns (dashboard_user_id, extra_roles). (None, []) means "mint the normal
    merchant passport" -- i.e. absent or unusable headers change nothing, and a
    request that never sends them is handled exactly as before.

    Only ever called AFTER the merchant credential has been verified, so the
    caller can pick the passport shape and the dashboard user id but never the
    merchant: that still comes from the API key.
    """
    want = (headers.get(ARENA_CONSUMER_TYPE_HEADER) or "").strip().lower()
    if want != PASSPORT_CONSUMER_TYPE_USER:
        return None, []
    uid = (headers.get(ARENA_USER_ID_HEADER) or "").strip()
    if not uid or not _ARENA_ID_RE.match(uid):
        _log("ignoring %s: %s missing or malformed" % (ARENA_CONSUMER_TYPE_HEADER,
                                                       ARENA_USER_ID_HEADER))
        return None, []
    role = (headers.get(ARENA_USER_ROLE_HEADER) or "").strip()
    roles = [role] if role and _ARENA_ID_RE.match(role) else []
    return uid, roles


def _authenticate(handler):
    """Basic-Auth against seeds/merchants.json. Returns (merchant_id, mode,
    roles) on success, or None (caller sends 401)."""
    auth_header = handler.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        key_id, _, secret = decoded.partition(":")
    except Exception:  # noqa: BLE001
        return None
    rec = KEY_TABLE.get(key_id)
    if rec is None or not rec["secret"] or rec["secret"] != secret:
        return None
    return rec["merchant_id"], rec["mode"], rec["roles"]


class ProxyHandler(BaseHTTPRequestHandler):
    server_version = "kong-lite/1.0"

    def log_message(self, fmt, *args):
        _log(fmt % args)

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _health(self):
        self._json(200, {"status": "ok", "service": SERVICE_NAME, "routes": len(ROUTES),
                          "merchants_loaded": len(set(v["merchant_id"] for v in KEY_TABLE.values()))})

    def _mint_endpoint(self):
        # Arena-only helper for the verifier: mint a passport JWT for a synthetic merchant without
        # going through the API-key path (PASSPORT_SIGNER_URL contract in verifier/helpers/passport.py).
        # Same signer/claim shape as the proxy path; refuses non-arena merchant ids.
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad_json"})
        merchant_id = str((req.get("consumer") or {}).get("id") or "")
        if not merchant_id.startswith("ARENA"):
            return self._json(403, {"error": "non_arena_merchant_id"})
        # ARENA CONTROL (same opt-in as the proxy path's headers): an optional
        # "user": {"id": "..."} asks for the dashboard/proxy claim shape.
        user_id = str((req.get("user") or {}).get("id") or "") or None
        if user_id and not _ARENA_ID_RE.match(user_id):
            return self._json(400, {"error": "bad_user_id"})
        jwt = _mint_passport_jwt(merchant_id, req.get("mode") or "live", req.get("roles") or [],
                                 dashboard_user_id=user_id)
        if jwt is None:
            return self._json(500, {"error": "passport_signing_key_unavailable"})
        return self._json(200, {"token": jwt})

    def _proxy(self):
        if self.path in ("/health", "/_arena/health"):
            return self._health()
        if self.path == "/_arena/mint" and self.command == "POST":
            return self._mint_endpoint()

        inject_api_cred = True
        if ENFORCE_ROUTE_POLICY:
            decision, upstream, inject_api_cred = classify_request(self.command, self.path)
            if decision == "deny" or upstream is None:
                # A real public edge never registered this route: 404 no_route,
                # indistinguishable from not-found, before any auth leak.
                self._json(404, {"error": "no_route", "detail": "no merchant route for path",
                                  "path": self.path})
                return
        else:
            upstream = _match_upstream(self.path)
            if upstream is None:
                self._json(404, {"error": "no_route", "detail": "no upstream configured for path prefix",
                                  "path": self.path})
                return

        auth = _authenticate(self)
        if auth is None:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Authorization Required"')
            body = json.dumps({"error": "unauthorized", "detail": "invalid or missing merchant API key"}).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        merchant_id, mode, roles = auth

        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None

        # Strip the client's inbound Authorization (the merchant API-key
        # credential is Kong-lite's own concern, never forwarded upstream)
        # and every other hop-by-hop header; add the passport + service
        # Basic-Auth headers a real Kong/edge would add.
        # ARENA CONTROL headers are consumed here and never reach the upstream.
        _drop = {"host", "content-length", "authorization"} | ARENA_CONTROL_HEADERS
        if ENFORCE_ROUTE_POLICY:
            # The edge mints identity; a client cannot supply it. cred.API used to
            # be injected across the whole prefix — now only for routes that need it.
            _drop |= _STRIP_IDENTITY_HEADERS
        headers = {k: v for k, v in self.headers.items() if k.lower() not in _drop}

        dash_user, dash_roles = _dashboard_override(self.headers)
        if dash_roles:
            roles = list(roles) + [r for r in dash_roles if r not in roles]
        if dash_user:
            _log("arena control: minting a dashboard/proxy passport for merchant %s "
                 "(user %s, roles %r)" % (merchant_id, dash_user, roles))
        jwt = _mint_passport_jwt(merchant_id, mode, roles, dashboard_user_id=dash_user)
        if jwt is None:
            self._json(500, {"error": "passport_signing_key_unavailable"})
            return
        headers["X-Passport-JWT-V1"] = jwt

        if inject_api_cred and PS_API_AUTH_USER and _PS_API_PASSWORD:
            token = base64.b64encode(("%s:%s" % (PS_API_AUTH_USER, _PS_API_PASSWORD)).encode()).decode()
            headers["Authorization"] = "Basic %s" % token

        req = urllib.request.Request(upstream + self.path, data=body, headers=headers, method=self.command)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() not in ("transfer-encoding", "connection"):
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp.read())
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(e.read() or b"{}")
        except urllib.error.URLError as e:
            self._json(502, {"error": "upstream_unreachable", "detail": str(e.reason), "path": self.path})

    def do_GET(self):
        self._proxy()

    def do_POST(self):
        self._proxy()

    def do_PUT(self):
        self._proxy()

    def do_PATCH(self):
        self._proxy()

    def do_DELETE(self):
        self._proxy()


if __name__ == "__main__":
    addr = ("0.0.0.0", LISTEN_PORT)
    httpd = ThreadingHTTPServer(addr, ProxyHandler)
    _log("listening on %s, %d routes, %d keys configured" % (str(addr), len(ROUTES), len(KEY_TABLE)))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
