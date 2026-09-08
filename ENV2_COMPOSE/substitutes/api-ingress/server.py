#!/usr/bin/env python3
"""api-ingress -- the shared API-monolith ingress layer of the Payouts/Source-to-Pay twin (M7).

CONTRACT_FAITHFUL_REPLACEMENT of the API monolith's *ingress* responsibilities for the selected
merchant, dashboard, internal-application and administrator surface (contract/routes.json, derived
mechanically from the pinned api/app/Http/Route.php by scripts/m7/derive_contract.py). It is NOT
the monolith: every business decision it makes is a source-cited authorization / identity /
ownership / idempotency rule of the monolith's BasicAuth + middleware + Payout/FundAccount services,
after which the request is forwarded to the REAL Payouts service (payouts-api) with the passport
and service credentials the monolith would present, or -- for the payouts-service-facing callback
group the M6 monolith-stub already implements -- passed through to that substitute.

Identity contexts (api/app/Http/BasicAuth/BasicAuth.php):
  merchant      Basic rzp_{live,test}_<key>:<secret>       -> Type::PRIVATE_AUTH   (private routes)
  user          Basic dashboard:<session token>            -> proxy auth: dashboard user impersonating
                                                              the merchant of the session (Route::$proxy)
  application   Basic rzp_live:<app secret> (key blank)    -> appAuth / Type::PRIVILEGE_AUTH; tenant ONLY
                                                              from X-Razorpay-Account (checkAndSetAccountScope)
  admin         Basic admin:<admin token>                  -> Route::$admin only
A route is served only when the caller's context is one the route's Route.php group admits and
(for internal routes) the application is in Route::$internalApps[app]. Anything else answers the
monolith's routeNotFound() (BasicAuth::appAuth -> ApiResponse::routeNotFound, HTTP 400
BAD_REQUEST_URL_NOT_FOUND) or 401 BAD_REQUEST_UNAUTHORIZED for a bad merchant credential.

Tenant authority: the merchant of a public route is the merchant OF THE CREDENTIAL. Caller-supplied
identity headers (x-merchant-id, X-Entity-Id, X-Razorpay-Account, X-Passport-JWT-V1, X-Dashboard-*,
X-Payout-Actor-*, App-User-ID) are stripped and re-issued by the ingress from the authenticated
context; body.merchant_id is overwritten (payouts CreatePayoutToFundAccount does the same from the
passport). Ownership: fund accounts, contacts and banking accounts are explicit merchant-owned
records (SQLite `resources`), looked up merchant-scoped exactly like FundAccount/Service.php:212
findByPublicIdAndMerchant -> BAD_REQUEST_INVALID_ID (HTTP 400) when the caller's tenant does not own it.

Stdlib only. Health/reset/evidence live under /health and /_ingress/*, never under /v1.
"""
import base64
import hashlib
import json
import os
import re
import secrets as pysecrets
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "/app")
from _common.rsa_sign import (b64url, parse_rsa_private_key_der, pem_to_der,  # noqa: E402
                              rsa_sign_pkcs1v15_sha256)

SERVICE_NAME = os.environ.get("STUB_NAME", "api-ingress")
LISTEN_PORT = int(os.environ.get("STUB_PORT", "8080"))
DB_PATH = os.environ.get("INGRESS_DB", "/data/ingress.sqlite")
PS_API_URL = os.environ.get("PS_API_URL", "http://payouts-api:9400").rstrip("/")
MONOLITH_STUB_URL = os.environ.get("MONOLITH_STUB_URL", "http://monolith-stub:8080").rstrip("/")
S2P_VP_SOURCE_URL = os.environ.get("S2P_VP_SOURCE_URL", "").rstrip("/")
UPSTREAM_TIMEOUT = float(os.environ.get("INGRESS_UPSTREAM_TIMEOUT", "30"))
KONG_MERCHANTS_FILE = os.environ.get("KONG_MERCHANTS_FILE", "/app/seed/merchants.json")
MONOLITH_MERCHANTS_FILE = os.environ.get("MONOLITH_MERCHANTS_FILE", "/app/seed/monolith_merchants.json")
MONOLITH_FUND_ACCOUNTS_FILE = os.environ.get("MONOLITH_FUND_ACCOUNTS_FILE", "/app/seed/fund_accounts.json")
SECRETS_KONG_DIR = os.environ.get("SECRETS_KONG_DIR", "/run/secrets/kong")
SECRETS_INGRESS_DIR = os.environ.get("SECRETS_INGRESS_DIR", "/run/secrets/ingress")
CONTRACT_FILE = os.environ.get("INGRESS_CONTRACT_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "contract", "routes.json"))
PASSPORT_IDENTIFIER = os.environ.get("PASSPORT_IDENTIFIER", "arena-passport-1")
PASSPORT_ORG = os.environ.get("PASSPORT_ORG", "ARENAORG000001")
PASSPORT_PRODUCT = os.environ.get("PASSPORT_PRODUCT", "banking")
PASSPORT_ISS = os.environ.get("PASSPORT_ISS", "https://identity.arena.invalid")
PASSPORT_SUB = os.environ.get("PASSPORT_SUB", "urn:arena:api-ingress")
PASSPORT_TTL_SEC = int(os.environ.get("PASSPORT_TTL_SEC", "300"))
SYNTHETIC_ADMIN_ID = os.environ.get("INGRESS_ADMIN_ID", "ARENAADMIN00001")
MAX_BODY = 4 * 1024 * 1024
SCHEMA_VERSION = 1
# Internal applications whose requests the monolith's MerchantIdempotencyHandler subjects to
# merchant idempotency (MerchantIdempotencyHandler.php:86-98) besides strict private auth.
IDEMPOTENT_APPS = {"vendor_payments", "payout_links", "accounts_receivable", "settlements_service", "xpayroll",
                   "xperience", "capital_early_settlements", "cross_border_import_service", "scrooge"}
# payouts/internal/app/contact/type.go:16-21 -- internal contact types an app may pay
INTERNAL_APP_TO_CONTACT_TYPES = {"vendor_payments": ["rzp_tax_pay"], "capital_collections_client": ["rzp_capital_collections"],
                                 "charge_collections_internal": ["rzp_charge_collections"], "xpayroll": ["rzp_xpayroll"]}
INTERNAL_CONTACT_TYPES = {"rzp_fees", "rzp_tax_pay", "rzp_capital_collections", "rzp_charge_collections", "rzp_xpayroll"}
# api VendorPayments/Service.php:490-498 -- source types whose status updates the monolith pushes to vendor-payments
VP_SOURCE_TYPES = {"vendor_payments", "tax_payments", "vendor_settlements", "vendor_advance"}
STRIPPED_HEADERS = {"authorization", "x-merchant-id", "x-entity-id", "x-razorpay-account", "x-passport-jwt-v1",
                    "x-dashboard-user-id", "x-dashboard-merchant-id", "x-dashboard-user-role", "x-payout-actor-id",
                    "x-payout-actor-type", "x-payout-actor-property-key", "x-payout-actor-property-value",
                    "app-user-id", "user-session-id", "x-creator-id", "x-creator-type", "x-batch-id",
                    "x-payouts-service-proxy", "host", "content-length", "connection", "accept-encoding",
                    "x-razorpay-merchantid", "x-admin-token"}
MERCHANT_KEY_RE = re.compile(r"^rzp_(test|live)_([A-Za-z0-9]{14})$")
ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
TAX_PAYMENT_ID_RE = re.compile(r"^txpy_[A-Za-z0-9]{14}$")


def _log(msg):
    sys.stderr.write("[%s] %s %s\n" % (SERVICE_NAME, time.strftime("%Y-%m-%dT%H:%M:%S"), msg))
    sys.stderr.flush()


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def _now():
    return int(time.time())


def bare(pid):
    return pid.split("_", 1)[1] if pid and pid.startswith(("pout_", "fa_", "cont_", "txpy_", "bal_")) else pid


# ---------------------------------------------------------------------------
# persistent state (SQLite on a named volume; survives restarts, wiped by /_ingress/reset)
# ---------------------------------------------------------------------------
_DB_LOCK = threading.RLock()
SCHEMA = """
CREATE TABLE IF NOT EXISTS resources(kind TEXT, id TEXT, merchant_id TEXT, contact_id TEXT, contact_type TEXT,
  record TEXT, source TEXT, created_at INTEGER, PRIMARY KEY(kind,id));
CREATE TABLE IF NOT EXISTS banking_accounts(account_number TEXT PRIMARY KEY, merchant_id TEXT, balance_id TEXT,
  account_type TEXT, channel TEXT, source TEXT, created_at INTEGER);
CREATE TABLE IF NOT EXISTS payout_details(payout_id TEXT PRIMARY KEY, tax_payment_id TEXT, tagged_by_app TEXT,
  tagged_by_tenant TEXT, request_id TEXT, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS internal_payouts(payout_id TEXT PRIMARY KEY, merchant_id TEXT, app TEXT, source_details TEXT,
  idempotency_key TEXT, request_id TEXT, created_at INTEGER);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT, merchant_id TEXT, roles TEXT, created_at INTEGER);
CREATE TABLE IF NOT EXISTS otps(token TEXT PRIMARY KEY, otp TEXT, user_id TEXT, merchant_id TEXT, action TEXT,
  consumed INTEGER DEFAULT 0, created_at INTEGER);
CREATE TABLE IF NOT EXISTS idempotency(scope TEXT, key TEXT, route TEXT, request_hash TEXT, status INTEGER,
  response TEXT, request_id TEXT, created_at INTEGER, PRIMARY KEY(scope,key));
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT, ts INTEGER, method TEXT,
  path TEXT, route TEXT, identity_type TEXT, identity_id TEXT, tenant TEXT, upstream TEXT, upstream_status INTEGER,
  status INTEGER, decision TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS callbacks(id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT, ts INTEGER, payout_id TEXT,
  merchant_id TEXT, source_type TEXT, source_id TEXT, payout_status TEXT, target TEXT, status INTEGER, response TEXT);
CREATE TABLE IF NOT EXISTS meta(name TEXT PRIMARY KEY, value TEXT);
"""


def db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _DB_LOCK, db() as c:
        c.executescript(SCHEMA)
        c.execute("INSERT OR REPLACE INTO meta(name,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
        c.execute("INSERT OR IGNORE INTO meta(name,value) VALUES('boot_id',?)", (uuid.uuid4().hex,))


def audit(request_id, method, path, route, ctx, tenant, upstream, upstream_status, status, decision, detail=None):
    with _DB_LOCK, db() as c:
        c.execute("INSERT INTO audit(request_id,ts,method,path,route,identity_type,identity_id,tenant,upstream,upstream_status,status,decision,detail)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (request_id, _now(), method, path, route, (ctx or {}).get("type"), (ctx or {}).get("id"), tenant, upstream,
                   upstream_status, status, decision, json.dumps(detail, sort_keys=True, default=str)[:2000] if detail is not None else None))


# ---------------------------------------------------------------------------
# registries: merchant API keys (kong seed + secret files), merchants, owned resources (seeds, mtime-reloaded)
# ---------------------------------------------------------------------------
class Registry:
    def __init__(self):
        self.lock = threading.Lock()
        self.keys = {}          # key_id -> {merchant_id, mode, secret, roles}
        self.merchants = {}     # merchant_id -> monolith merchant record
        self.apps = {}          # app secret -> app name
        self.admin_token = ""
        self.monolith_basic = ""
        self.ps_api_pass = ""
        self.workflow_pass = ""
        self.mtimes = {}
        self.seed_fund_accounts = 0
        self.passport = None
        self.reload(force=True)

    def _mtime(self, p):
        try:
            return os.stat(p).st_mtime_ns
        except OSError:
            return None

    def _changed(self):
        paths = [KONG_MERCHANTS_FILE, MONOLITH_MERCHANTS_FILE, MONOLITH_FUND_ACCOUNTS_FILE,
                 os.path.join(SECRETS_KONG_DIR, "merchants")]
        cur = {p: self._mtime(p) for p in paths}
        if cur != self.mtimes:
            self.mtimes = cur
            return True
        return False

    def reload(self, force=False):
        with self.lock:
            if not force and not self._changed():
                return False
            keys = {}
            try:
                data = json.load(open(KONG_MERCHANTS_FILE))
                for mid, rec in data.get("merchants", {}).items():
                    secret = _read(os.path.join(SECRETS_KONG_DIR, "merchants", "%s.txt" % rec.get("secret_file", "")))
                    for key_id, mode in ((rec.get("key_id_test"), "test"), (rec.get("key_id_live"), "live")):
                        if key_id and secret:
                            keys[key_id] = {"merchant_id": mid, "mode": mode, "secret": secret, "roles": rec.get("roles", [])}
            except (OSError, ValueError) as e:
                _log("merchant key seed unreadable: %r" % e)
            merchants = {}
            try:
                merchants = json.load(open(MONOLITH_MERCHANTS_FILE)).get("merchants", {})
            except (OSError, ValueError) as e:
                _log("monolith merchants seed unreadable: %r" % e)
            self.keys, self.merchants = keys, merchants
            self._load_secrets()
            self.seed_fund_accounts = self._load_seed_fund_accounts()
            _log("registry: %d api keys, %d merchants, %d seed fund accounts, %d app identities" %
                 (len(keys), len(merchants), self.seed_fund_accounts, len(self.apps)))
            return True

    def _load_secrets(self):
        apps = {}
        for name in ("vendor_payments", "xpayroll", "batch", "workflows", "merchant_dashboard", "payout_links",
                     "accounting_integrations", "vendor_experience", "xperience"):
            v = _read(os.path.join(SECRETS_INGRESS_DIR, "app_%s" % name))
            if v:
                apps[v] = name
        # payouts-service -> api identity: payouts [api.auth] password (auth_monolith_shared), username rzp_live
        v = _read(os.path.join(SECRETS_INGRESS_DIR, "auth_monolith_shared"))
        if v:
            apps[v] = "payouts_service"
        self.apps = apps
        self.admin_token = _read(os.path.join(SECRETS_INGRESS_DIR, "admin_token"))
        self.monolith_basic = _read(os.path.join(SECRETS_INGRESS_DIR, "monolith_basic_auth"))
        self.ps_api_pass = _read(os.path.join(SECRETS_KONG_DIR, "auth_api_payouts"))
        self.workflow_pass = _read(os.path.join(SECRETS_KONG_DIR, "auth_workflow_payouts"))
        key_pem = _read(os.path.join(SECRETS_KONG_DIR, "passport_private_key"))
        if key_pem:
            try:
                self.passport = parse_rsa_private_key_der(pem_to_der(key_pem))
            except Exception as e:  # noqa: BLE001
                _log("passport key unparseable: %r" % e)

    def _load_seed_fund_accounts(self):
        """Seed fund accounts become explicit ownership records (source='seed'). A seed record without an
        owner is recorded with merchant_id NULL: nobody can use it (fail closed, findByPublicIdAndMerchant)."""
        try:
            doc = json.load(open(MONOLITH_FUND_ACCOUNTS_FILE))
        except (OSError, ValueError) as e:
            _log("fund account seed unreadable: %r" % e)
            return 0
        fas = doc.get("fund_accounts", doc)
        n = 0
        with _DB_LOCK, db() as c:
            for key, rec in fas.items():
                fid = bare(rec.get("id") or key)
                contact = rec.get("contact") or {}
                c.execute("INSERT OR REPLACE INTO resources(kind,id,merchant_id,contact_id,contact_type,record,source,created_at)"
                          " VALUES('fund_account',?,?,?,?,?,'seed',?)",
                          (fid, rec.get("merchant_id"), bare(rec.get("contact_id") or contact.get("id") or ""),
                           contact.get("type"), json.dumps(rec, sort_keys=True), _now()))
                if contact.get("id"):
                    c.execute("INSERT OR IGNORE INTO resources(kind,id,merchant_id,contact_id,contact_type,record,source,created_at)"
                              " VALUES('contact',?,?,NULL,?,?,'seed',?)",
                              (bare(contact["id"]), rec.get("merchant_id"), contact.get("type"), json.dumps(contact, sort_keys=True), _now()))
                n += 1
        return n


REG = None


# ---------------------------------------------------------------------------
# passport (same signer + claim shape as kong-lite/server.py; consumer types per goutils passport v3)
# ---------------------------------------------------------------------------
def mint_passport(consumer_type, consumer_id, mode="live", roles=None, impersonated_merchant=None, app_name=None):
    if not REG.passport:
        return None
    n, d, size = REG.passport
    now = int(time.time())
    header = {"typ": "JWT", "alg": "RS256", "kid": PASSPORT_IDENTIFIER}
    consumer = {"id": consumer_id, "type": consumer_type}
    if consumer_type == "application":
        consumer["meta"] = {"name": app_name or consumer_id}   # BasicAuth::setPassportConsumerClaims(..., ['name' => app])
    payload = {"iss": PASSPORT_ISS, "sub": PASSPORT_SUB, "jti": str(uuid.uuid4()), "iat": now, "nbf": now,
               "exp": now + PASSPORT_TTL_SEC, "identified": True, "authenticated": True, "mode": mode,
               "org": PASSPORT_ORG, "product": PASSPORT_PRODUCT, "consumer": consumer}
    if consumer_type == "user" and impersonated_merchant:
        payload["impersonation"] = {"type": "user_merchant", "consumer": {"id": impersonated_merchant, "type": "merchant"}}
    if roles:
        payload["roles"] = list(roles)
    signing_input = (b64url(json.dumps(header, separators=(",", ":")).encode()) + "." +
                     b64url(json.dumps(payload, separators=(",", ":")).encode())).encode()
    sig = rsa_sign_pkcs1v15_sha256(signing_input, n, d, size)
    return signing_input.decode() + "." + b64url(sig)


def basic(user, password):
    return "Basic " + base64.b64encode(("%s:%s" % (user, password)).encode()).decode()


# ---------------------------------------------------------------------------
# errors in the monolith's public shape
# ---------------------------------------------------------------------------
def err(status, code, description, field=None, extra=None):
    e = {"code": code, "description": description}
    if field:
        e["field"] = field
    if extra:
        e.update(extra)
    return status, {"error": e}


ROUTE_NOT_FOUND = err(400, "BAD_REQUEST_ERROR", "The requested URL was not found on the server.")
UNAUTHORIZED = err(401, "BAD_REQUEST_ERROR", "Authentication failed")
FORBIDDEN = err(400, "BAD_REQUEST_ERROR", "Access forbidden for requested resource")
INVALID_ID = err(400, "BAD_REQUEST_ERROR", "The id provided does not exist")


def invalid_id(field=None):
    return err(400, "BAD_REQUEST_ERROR", "The id provided does not exist", field=field)
ACCESS_DENIED = err(400, "BAD_REQUEST_ERROR", "Access Denied")


# ---------------------------------------------------------------------------
# HTTP upstream helper
# ---------------------------------------------------------------------------
def upstream(method, url, body=None, headers=None, timeout=UPSTREAM_TIMEOUT):
    data = None
    h = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode() if not isinstance(body, (bytes, bytearray)) else bytes(body)
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, raw, r.headers.get("Content-Type", "application/json")
    except urllib.error.HTTPError as e:
        raw = e.read() or b""
        return e.code, raw, e.headers.get("Content-Type", "application/json")
    except (urllib.error.URLError, OSError) as e:
        return 502, json.dumps({"error": {"code": "GATEWAY_ERROR", "description": "upstream unreachable: %s" % str(e)[:120]}}).encode(), "application/json"


def jbody(raw):
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {"_raw": raw.decode("utf-8", "replace")[:500]}


# ---------------------------------------------------------------------------
# request context
# ---------------------------------------------------------------------------
class Ctx:
    def __init__(self, handler):
        self.h = handler
        self.method = handler.command
        parsed = urllib.parse.urlsplit(handler.path)
        self.path = parsed.path.rstrip("/") or "/"
        self.query = parsed.query
        self.headers = {k.lower(): v for k, v in handler.headers.items()}
        self.request_id = self.headers.get("x-request-id") or ("ing_" + uuid.uuid4().hex[:20])
        self.raw = b""
        self.body = None
        self.identity = None      # {"type": merchant|user|application|admin, "id":..., ...}
        self.tenant = None        # authoritative merchant id for this request (or None)
        self.route = None

    def read_body(self):
        length = int(self.headers.get("content-length", "0") or 0)
        if length > MAX_BODY:
            return False
        self.raw = self.h.rfile.read(length) if length else b""
        try:
            self.body = json.loads(self.raw) if self.raw else {}
        except ValueError:
            self.body = None
        return True


def authenticate(ctx):
    """BasicAuth.php: private (merchant key), proxy (dashboard session), app (rzp_live + app secret), admin."""
    auth = ctx.headers.get("authorization", "")
    if not auth.startswith("Basic "):
        return None
    try:
        user, _, password = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
    except Exception:  # noqa: BLE001
        return None
    m = MERCHANT_KEY_RE.match(user)
    if m:
        rec = REG.keys.get(user)
        if rec is None or not rec["secret"] or not pysecrets.compare_digest(rec["secret"], password):
            return {"type": "invalid_merchant_key"}
        return {"type": "merchant", "id": rec["merchant_id"], "merchant_id": rec["merchant_id"], "mode": rec["mode"],
                "roles": rec["roles"], "key_id": user}
    if user == "rzp_live":
        for secret, app in REG.apps.items():
            if pysecrets.compare_digest(secret, password):
                return {"type": "application", "id": app, "app": app}
        return {"type": "invalid_app"}
    if user == "admin":
        if REG.admin_token and pysecrets.compare_digest(REG.admin_token, password):
            return {"type": "admin", "id": SYNTHETIC_ADMIN_ID}
        return {"type": "invalid_admin"}
    if user == "dashboard":
        with _DB_LOCK, db() as c:
            row = c.execute("SELECT * FROM sessions WHERE token=?", (password,)).fetchone()
        if row:
            return {"type": "user", "id": row["user_id"], "user_id": row["user_id"], "merchant_id": row["merchant_id"],
                    "roles": json.loads(row["roles"] or "[]"), "mode": "live"}
        return {"type": "invalid_session"}
    return {"type": "unknown_credential"}


def resolve_tenant(ctx):
    """Authoritative tenant. merchant/user: the credential's merchant, never a header.
    application/admin: X-Razorpay-Account (BasicAuth::checkAndSetAccountScope) and it must be a known merchant."""
    ident = ctx.identity
    if ident["type"] in ("merchant", "user"):
        return ident["merchant_id"], None
    if ident["type"] in ("application", "admin"):
        acct = (ctx.headers.get("x-razorpay-account") or "").strip()
        if not acct:
            return None, None
        if acct not in REG.merchants:
            return None, err(400, "BAD_REQUEST_ERROR", "The id provided does not exist", field="X-Razorpay-Account")
        return acct, None
    return None, None


# ---------------------------------------------------------------------------
# route table (every entry cites contract/routes.json by Route.php route name)
# ---------------------------------------------------------------------------
class Route:
    def __init__(self, name, method, pattern, contexts, handler, apps=None, idempotent=False, needs_tenant=True, role=None):
        self.name, self.method, self.rx, self.contexts, self.handler = name, method, re.compile(pattern), set(contexts), handler
        self.apps, self.idempotent, self.needs_tenant, self.role = set(apps or []), idempotent, needs_tenant, role


def load_contract():
    try:
        return json.load(open(CONTRACT_FILE)).get("routes", {})
    except (OSError, ValueError) as e:
        _log("contract inventory unreadable (%r); refusing to serve routes without evidence" % e)
        return {}


CONTRACT = {}


def route_apps(name):
    return set((CONTRACT.get(name) or {}).get("internal_apps") or [])


ROUTES = []


def route(name, method, pattern, contexts, apps=None, idempotent=False, needs_tenant=True):
    def deco(fn):
        ROUTES.append(Route(name, method, pattern, contexts, fn, apps, idempotent, needs_tenant))
        return fn
    return deco


def match_route(ctx):
    for r in ROUTES:
        if r.method != ctx.method:
            continue
        m = r.rx.match(ctx.path)
        if m:
            return r, m
    return None, None


def request_hash(body):
    """MerchantIdempotencyHandler::getHashOfRequestBody -- sha256 of the key-sorted JSON body."""
    return hashlib.sha256(json.dumps(body if isinstance(body, dict) else {}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def ps_headers(ctx, passport, extra=None, include_idempotency=True):
    """Headers the monolith presents to the Payouts service (Services/PayoutService/Base.php):
    service Basic cred.API, X-Passport-JWT-V1, actor headers, idempotency key, request id."""
    h = {"Authorization": basic("api", REG.ps_api_pass), "Content-Type": "application/json",
         "X-Request-ID": ctx.request_id, "X-Razorpay-TaskId": ctx.request_id}
    if passport:
        h["X-Passport-JWT-V1"] = passport
    ident = ctx.identity
    if ident["type"] == "application":              # Base.php addActorsToHeaders
        h["X-Payout-Actor-Id"] = ident["app"]; h["X-Payout-Actor-Type"] = "application"
    elif ctx.tenant:
        h["X-Payout-Actor-Id"] = ctx.tenant; h["X-Payout-Actor-Type"] = "merchant"
    if ident["type"] == "user":
        h["App-User-ID"] = ident["user_id"]
    if include_idempotency and ctx.headers.get("x-payout-idempotency"):
        h["X-Payout-Idempotency"] = ctx.headers["x-payout-idempotency"]
    if ctx.tenant and ident["type"] in ("application", "admin"):
        h["x-merchant-id"] = ctx.tenant                # PS request_context.go: merchant from the trusted hop
    for k, v in ctx.headers.items():                   # pass-through of harmless client headers only
        if k not in STRIPPED_HEADERS and k not in ("x-request-id", "x-payout-idempotency", "content-type") and not k.startswith("x-arena-"):
            h[k] = v
    if extra:
        h.update(extra)
    return h


def passport_for(ctx):
    ident = ctx.identity
    if ident["type"] == "merchant":
        return mint_passport("merchant", ident["merchant_id"], ident.get("mode", "live"), ident.get("roles"))
    if ident["type"] == "user":
        return mint_passport("user", ident["user_id"], "live", ident.get("roles"), impersonated_merchant=ident["merchant_id"])
    if ident["type"] == "application":
        return mint_passport("application", ident["app"], "live", app_name=ident["app"])
    if ident["type"] == "admin":
        return mint_passport("admin", ident["id"], "live")
    return None


def forward_ps(ctx, method, path, body=None, extra_headers=None, passport=True, query=None):
    url = PS_API_URL + path + (("?" + query) if query else "")
    h = ps_headers(ctx, passport_for(ctx) if passport else None, extra_headers)
    st, raw, ctype = upstream(method, url, body, h)
    return st, raw, ctype, url


def forward_stub(ctx, method, path, raw_body, query=None):
    """Pass-through to the M6 monolith-stub (payouts-service-facing callback group); the stub's own
    inbound credential is the ingress's, never the caller's."""
    url = MONOLITH_STUB_URL + path + (("?" + query) if query else "")
    user, _, pw = (REG.monolith_basic or "rzp_live:").partition(":")
    h = {"Authorization": basic(user, pw), "Content-Type": ctx.headers.get("content-type", "application/json"),
         "X-Request-ID": ctx.request_id}
    st, raw, ctype = upstream(method, url, raw_body if raw_body else None, h)
    return st, raw, ctype, url


# --- ownership records ------------------------------------------------------
def owned(kind, rid, merchant_id):
    """findByPublicIdAndMerchant: the record must exist AND belong to the caller's merchant."""
    with _DB_LOCK, db() as c:
        row = c.execute("SELECT * FROM resources WHERE kind=? AND id=?", (kind, bare(rid))).fetchone()
    if row is None or not row["merchant_id"] or row["merchant_id"] != merchant_id:
        return None
    return dict(row)


def fund_account_for_ps(rec):
    """Services/PayoutService/Create.php:521-560 generateFundAccountResponseForPayoutsService: the PS-facing shape carries
    id/entity/contact_id/account_type/active/batch_id/created_at + bank details + contact -- and NO merchant_id."""
    fa = json.loads(rec["record"])
    out = {"id": "fa_" + bare(fa.get("id") or rec["id"]), "entity": "fund_account", "contact_id": "cont_" + bare(rec["contact_id"] or ""),
           "account_type": fa.get("account_type"), "active": bool(fa.get("active", True)), "batch_id": fa.get("batch_id", ""),
           "created_at": fa.get("created_at", rec["created_at"])}
    for k in ("bank_account", "vpa", "card", "wallet"):
        if fa.get(k):
            out[k] = fa[k]
    if fa.get("contact"):
        out["contact"] = fa["contact"]
    return out


def fund_account_public(rec):
    fa = json.loads(rec["record"])
    fa = dict(fa); fa.pop("merchant_id", None)
    fa["id"] = "fa_" + bare(fa.get("id") or rec["id"])
    return fa


# ---------------------------------------------------------------------------
# route handlers -- merchant / dashboard
# ---------------------------------------------------------------------------
@route("payout_create", "POST", r"^/v1/payouts$", {"merchant"}, idempotent=True)
def h_payout_create(ctx, m):
    body = dict(ctx.body or {}); body["merchant_id"] = ctx.tenant          # tenant is the credential's merchant
    st, raw, ctype, url = forward_ps(ctx, "POST", "/v1/payouts", body)
    return st, raw, ctype, url


@route("payout_create_with_otp", "POST", r"^/v1/payouts_with_otp$", {"user"}, idempotent=True)
def h_payout_create_with_otp(ctx, m):
    body = dict(ctx.body or {})
    otp, token = str(body.pop("otp", "") or ""), str(body.pop("token", "") or "")
    if not verify_otp(ctx.identity["user_id"], ctx.tenant, otp, token, "create_payout"):
        return err(400, "BAD_REQUEST_ERROR", "Verification failed because of incorrect OTP.", field="otp")
    body["merchant_id"] = ctx.tenant
    return forward_ps(ctx, "POST", "/v1/payouts", body)


@route("payout_fetch_by_id", "GET", r"^/v1/payouts/(pout_[A-Za-z0-9]{14})$", {"merchant", "user"})
def h_payout_fetch(ctx, m):
    return forward_ps(ctx, "GET", "/v1/payouts/" + m.group(1), query=ctx.query)


@route("payout_fetch_multiple", "GET", r"^/v1/payouts$", {"merchant", "user"})
def h_payout_fetch_multiple(ctx, m):
    return forward_ps(ctx, "GET", "/v1/payouts", query=ctx.query)


@route("payout_cancel", "POST", r"^/v1/payouts/(pout_[A-Za-z0-9]{14})/cancel$", {"merchant", "user"})
def h_payout_cancel(ctx, m):
    return forward_ps(ctx, "POST", "/v1/payouts/cancel_payout/" + bare(m.group(1)), ctx.body or {})


def _workflow_action(ctx, pid, action):
    """Services/PayoutService/Workflow.php approve/rejectPayoutViaMicroservice: POST /payouts/payouts_internal/{id}/{action}/
    with the caller's passport; the payout must belong to the merchant (Payout/Service.php:1179 findByPublicIdAndMerchant),
    which PS enforces itself on the internal route from x-merchant-id."""
    body = {"queue_if_low_balance": bool((ctx.body or {}).get("queue_if_low_balance", True))} if action == "approve" else {}
    extra = {"Authorization": basic("rzp_live", REG.workflow_pass), "x-merchant-id": ctx.tenant}
    # Workflow.php appends a trailing slash ("/approve/"); payouts' gin router registers the path without it and
    # answers 307 RedirectTrailingSlash, which the monolith's HTTP client follows. The ingress addresses the
    # registered path directly (same effective request, no redirect round-trip).
    return forward_ps(ctx, "POST", "/v1/payouts/payouts_internal/%s/%s" % (bare(pid), action), body, extra_headers=extra)


@route("payout_approve", "POST", r"^/v1/payouts/(pout_[A-Za-z0-9]{14})/approve$", {"user", "merchant"})
def h_payout_approve(ctx, m):
    return _workflow_action(ctx, m.group(1), "approve")


@route("payout_reject", "POST", r"^/v1/payouts/(pout_[A-Za-z0-9]{14})/reject$", {"user", "merchant"})
def h_payout_reject(ctx, m):
    return _workflow_action(ctx, m.group(1), "reject")


@route("payout_bulk_create", "POST", r"^/v1/payouts/bulk$", {"application"}, apps=["batch"])
def h_payout_bulk_create(ctx, m):
    """Batch service -> monolith payouts/bulk (Route::$proxy) -> PS POST /v1/payouts/bulk (proxy passport: the batch's
    creator user impersonating the merchant of X-Entity-Id; payouts request_context.go reads x-batch-id/X-Entity-Id)."""
    entity = (ctx.headers.get("x-entity-id") or "").strip()
    if not entity or entity not in REG.merchants:
        return invalid_id("X-Entity-Id")
    ctx.tenant = entity
    creator = (ctx.headers.get("x-creator-id") or "").strip() or "batch"
    passport = mint_passport("user", creator, "live", impersonated_merchant=entity)
    h = ps_headers(ctx, passport, {"x-batch-id": ctx.headers.get("x-batch-id", ""), "X-Entity-Id": entity,
                                   "x-creator-id": creator, "x-creator-type": ctx.headers.get("x-creator-type", "user")})
    st, raw, ctype = upstream("POST", PS_API_URL + "/v1/payouts/bulk", ctx.raw, h)
    return st, raw, ctype, PS_API_URL + "/v1/payouts/bulk"


# ---------------------------------------------------------------------------
# route handlers -- internal applications (Route::$internal, Route::$internalApps)
# ---------------------------------------------------------------------------
@route("payout_create_internal", "POST", r"^/v1/payouts_internal$", {"application"}, idempotent=True)
def h_payout_create_internal(ctx, m):
    body = dict(ctx.body or {}); body["merchant_id"] = ctx.tenant
    return forward_ps(ctx, "POST", "/v1/payouts/payouts_internal", body)


@route("payout_create_on_internal_contact", "POST", r"^/v1/internalContactPayout$", {"application"}, idempotent=True)
def h_internal_contact_payout(ctx, m):
    """Payout/Service.php:330-362 fundAccountPayoutOnInternalContact, then PS /v1/payouts/internal_contact_payout."""
    body = dict(ctx.body or {})
    if "fund_account_id" not in body:
        return err(400, "BAD_REQUEST_ERROR", "fund_account_id is required")
    rec = owned("fund_account", body["fund_account_id"], ctx.tenant)
    if rec is None:
        return invalid_id()
    if rec["contact_type"] not in INTERNAL_CONTACT_TYPES:
        return err(400, "BAD_REQUEST_ERROR", "Please send fund accounts of internal type contacts only")
    if rec["contact_type"] not in INTERNAL_APP_TO_CONTACT_TYPES.get(ctx.identity["app"], []):
        return err(400, "BAD_REQUEST_ERROR", "AppNotPermittedToCreatePayoutOnThisContactType")
    body["merchant_id"] = ctx.tenant
    st, raw, ctype, url = forward_ps(ctx, "POST", "/v1/payouts/internal_contact_payout", body)
    if st == 200:
        resp = jbody(raw)
        if resp.get("id"):
            with _DB_LOCK, db() as c:
                c.execute("INSERT OR REPLACE INTO internal_payouts(payout_id,merchant_id,app,source_details,idempotency_key,request_id,created_at) VALUES(?,?,?,?,?,?,?)",
                          (bare(resp["id"]), ctx.tenant, ctx.identity["app"], json.dumps(body.get("source_details") or []),
                           ctx.headers.get("x-payout-idempotency"), ctx.request_id, _now()))
    return st, raw, ctype, url


@route("payout_fetch_by_id_internal", "GET", r"^/v1/payouts_internal/(pout_[A-Za-z0-9]{14})$", {"application"})
def h_payout_fetch_internal(ctx, m):
    return forward_ps(ctx, "GET", "/v1/payouts/payouts_internal/" + bare(m.group(1)), extra_headers={"x-merchant-id": ctx.tenant})


@route("payout_update_tax_payment_id", "PATCH", r"^/v1/payouts_internal/(pout_[A-Za-z0-9]{14})/tax-payment-id$", {"application"})
def h_update_tax_payment(ctx, m):
    """Payout/Service.php:6241 updateTaxPayment -> Validator UPDATE_TAX_PAYMENT (tax_payment_id required|string|size:19)
    -> PayoutsDetails/Core.php:378 updatePayoutDetails([$payoutId]) : an id-only update with NO merchant scoping in the
    pinned source. The twin follows the source and RECORDS the tenant of the caller so the evidence can show whether a
    cross-tenant tag was attempted (see M7_OWNERSHIP_INVESTIGATION.md)."""
    body = ctx.body or {}
    tp = body.get("tax_payment_id")
    if not isinstance(tp, str) or len(tp) != 19:
        return err(400, "BAD_REQUEST_VALIDATION_FAILURE", "The tax payment id must be 19 characters.", field="tax_payment_id")
    if not TAX_PAYMENT_ID_RE.match(tp):
        return err(400, "BAD_REQUEST_ERROR", "The id provided does not exist", field="tax_payment_id")
    with _DB_LOCK, db() as c:
        c.execute("INSERT OR REPLACE INTO payout_details(payout_id,tax_payment_id,tagged_by_app,tagged_by_tenant,request_id,updated_at) VALUES(?,?,?,?,?,?)",
                  (bare(m.group(1)), bare(tp), ctx.identity["app"], ctx.tenant, ctx.request_id, _now()))
    return 200, json.dumps({"status": "SUCCESS"}).encode(), "application/json", "ingress:payout_details"


@route("payout_approve_internal", "POST", r"^/v1/payouts_internal/(pout_[A-Za-z0-9]{14})/approve$", {"application"})
def h_payout_approve_internal(ctx, m):
    return _workflow_action(ctx, m.group(1), "approve")


@route("payout_reject_internal", "POST", r"^/v1/payouts_internal/(pout_[A-Za-z0-9]{14})/reject$", {"application"})
def h_payout_reject_internal(ctx, m):
    return _workflow_action(ctx, m.group(1), "reject")


@route("payout_cancel_internal", "POST", r"^/v1/payouts_internal/(pout_[A-Za-z0-9]{14})/cancel$", {"application"})
def h_payout_cancel_internal(ctx, m):
    return forward_ps(ctx, "POST", "/v1/payouts/cancel_payout/" + bare(m.group(1)), ctx.body or {}, extra_headers={"x-merchant-id": ctx.tenant})


@route("fund_account_get_internal", "GET", r"^/v1/fund_accounts_internal/(fa_[A-Za-z0-9]{14})$", {"application"})
def h_fund_account_get(ctx, m):
    """FundAccount/Service.php:210-212 fetch(): findByPublicIdAndMerchant($id, $this->merchant) -- the beneficiary is
    resolved ONLY within the calling tenant. Response shape: PS-facing (Create.php:521) when the caller is
    payouts_service, toArrayPublic otherwise. Neither carries merchant_id (ownership is enforced, not disclosed)."""
    rec = owned("fund_account", m.group(1), ctx.tenant)
    if rec is None:
        return invalid_id()
    out = fund_account_for_ps(rec) if ctx.identity["app"] == "payouts_service" else fund_account_public(rec)
    return 200, json.dumps(out).encode(), "application/json", "ingress:resources"


@route("fund_account_list_internal", "GET", r"^/v1/fund_accounts_internal$", {"application"})
def h_fund_account_list(ctx, m):
    q = urllib.parse.parse_qs(ctx.query)
    with _DB_LOCK, db() as c:
        rows = c.execute("SELECT * FROM resources WHERE kind='fund_account' AND merchant_id=? ORDER BY created_at", (ctx.tenant,)).fetchall()
    items = [fund_account_public(dict(r)) for r in rows if not q.get("contact_id") or bare(q["contact_id"][0]) == bare(r["contact_id"] or "")]
    return 200, json.dumps({"entity": "collection", "count": len(items), "items": items}).encode(), "application/json", "ingress:resources"


def _create_resource(ctx, kind, body, rid, contact_id, contact_type, record):
    with _DB_LOCK, db() as c:
        c.execute("INSERT INTO resources(kind,id,merchant_id,contact_id,contact_type,record,source,created_at) VALUES(?,?,?,?,?,?,?,?)",
                  (kind, rid, ctx.tenant, contact_id, contact_type, json.dumps(record, sort_keys=True), "created:" + ctx.identity["app"], _now()))


@route("fund_account_create_internal", "POST", r"^/v1/fund_accounts_internal$", {"application"})
def h_fund_account_create(ctx, m):
    body = ctx.body or {}
    if not body.get("contact_id"):
        return err(400, "BAD_REQUEST_VALIDATION_FAILURE", "The contact id field is required.", field="contact_id")
    contact = owned("contact", body["contact_id"], ctx.tenant)
    if contact is None:
        return invalid_id("contact_id")
    if body.get("account_type") != "bank_account" or not isinstance(body.get("bank_account"), dict):
        return err(400, "BAD_REQUEST_VALIDATION_FAILURE", "The account type field is invalid.", field="account_type")
    fid = "ARENA" + uuid.uuid4().hex[:9].upper()
    ba = body["bank_account"]
    rec = {"id": fid, "entity": "fund_account", "contact_id": "cont_" + contact["id"], "account_type": "bank_account",
           "bank_account": {"ifsc": ba.get("ifsc"), "bank_name": ba.get("bank_name", "Synthetic Bank"), "name": ba.get("name"),
                            "account_number": ba.get("account_number"), "id": "ba_" + uuid.uuid4().hex[:14].upper()},
           "active": True, "batch_id": "", "created_at": _now(), "contact": json.loads(contact["record"]), "merchant_id": ctx.tenant}
    _create_resource(ctx, "fund_account", body, fid, contact["id"], contact["contact_type"], rec)
    return 200, json.dumps(fund_account_public({"record": json.dumps(rec), "id": fid})).encode(), "application/json", "ingress:resources"


@route("contact_get_internal", "GET", r"^/v1/contacts_internal/(cont_[A-Za-z0-9]{14})$", {"application"})
def h_contact_get(ctx, m):
    rec = owned("contact", m.group(1), ctx.tenant)
    if rec is None:
        return invalid_id()
    return 200, json.dumps(json.loads(rec["record"])).encode(), "application/json", "ingress:resources"


@route("contact_list_internal", "GET", r"^/v1/contacts_internal$", {"application"})
def h_contact_list(ctx, m):
    """vendor-payments internal_tax_contact.go searches {type: rzp_tax_pay, active: 1, expand: fund_accounts} (SearchReq in a
    GET body); the monolith lists the caller's merchant's contacts only."""
    q = urllib.parse.parse_qs(ctx.query); body = ctx.body if isinstance(ctx.body, dict) else {}
    want_type = (q.get("type") or [body.get("type")])[0]
    with _DB_LOCK, db() as c:
        rows = c.execute("SELECT * FROM resources WHERE kind='contact' AND merchant_id=? ORDER BY created_at", (ctx.tenant,)).fetchall()
        fas = c.execute("SELECT * FROM resources WHERE kind='fund_account' AND merchant_id=?", (ctx.tenant,)).fetchall()
    items = []
    for r in rows:
        if want_type and r["contact_type"] != want_type:
            continue
        item = json.loads(r["record"])
        item["fund_accounts"] = [fund_account_public(dict(f)) for f in fas if bare(f["contact_id"] or "") == r["id"]]
        items.append(item)
    return 200, json.dumps({"entity": "collection", "count": len(items), "items": items}).encode(), "application/json", "ingress:resources"


@route("contact_create_internal", "POST", r"^/v1/contacts_internal$", {"application"})
def h_contact_create(ctx, m):
    body = ctx.body or {}
    ctype = body.get("type") or "vendor"
    if ctype in INTERNAL_CONTACT_TYPES and ctype not in INTERNAL_APP_TO_CONTACT_TYPES.get(ctx.identity["app"], []):
        return err(400, "BAD_REQUEST_ERROR", "AppNotPermittedToCreateInternalContactOfThisType", field="type")
    cid = "ARENA" + uuid.uuid4().hex[:9].upper()
    rec = {"id": "cont_" + cid, "entity": "contact", "name": body.get("name", ""), "type": ctype, "active": True,
           "email": body.get("email", ""), "contact": body.get("contact", ""), "created_at": _now()}
    _create_resource(ctx, "contact", body, cid, None, ctype, rec)
    return 200, json.dumps(rec).encode(), "application/json", "ingress:resources"


@route("banking_accounts_list_internal", "GET", r"^/v1/banking_accounts_internal$", {"application"})
def h_banking_accounts_list(ctx, m):
    with _DB_LOCK, db() as c:
        rows = c.execute("SELECT * FROM banking_accounts WHERE merchant_id=?", (ctx.tenant,)).fetchall()
    items = [{"id": "ba_" + hashlib.sha256(r["account_number"].encode()).hexdigest()[:14], "account_number": r["account_number"],
              "channel": r["channel"], "account_type": r["account_type"], "balance": {"id": r["balance_id"]}} for r in rows]
    return 200, json.dumps({"count": len(items), "items": items}).encode(), "application/json", "ingress:banking_accounts"


def verify_otp(user_id, merchant_id, otp, token, action):
    with _DB_LOCK, db() as c:
        row = c.execute("SELECT * FROM otps WHERE token=? AND user_id=? AND merchant_id=? AND consumed=0", (token, user_id, merchant_id)).fetchone()
        if row is None or not pysecrets.compare_digest(row["otp"], otp or ""):
            return False
        c.execute("UPDATE otps SET consumed=1 WHERE token=?", (token,))
    return True


@route("vendor_payment_verify_otp", "POST", r"^/v1/vendor-payments/verify-otp$", {"application"})
def h_vp_verify_otp(ctx, m):
    """VendorPaymentController@verifyOtp: user_id required; UserCore->verifyOtp(input, merchant, user) -> {success}."""
    body = ctx.body or {}
    if "user_id" not in body:
        return 200, json.dumps("User Id Required").encode(), "application/json", "ingress:otps"
    ok = verify_otp(str(body.get("user_id")), ctx.tenant, str(body.get("otp", "")), str(body.get("token", "")), body.get("action", ""))
    return 200, json.dumps({"success": ok}).encode(), "application/json", "ingress:otps"


# ---------------------------------------------------------------------------
# route handlers -- administrator (Route::$admin)
# ---------------------------------------------------------------------------
@route("admin_get_free_payouts_attributes", "GET", r"^/v1/admin/payouts/([A-Za-z0-9_]{1,40})/free_payout$", {"admin"}, needs_tenant=False)
def h_admin_free_payout(ctx, m):
    return forward_ps(ctx, "GET", "/v1/admin/payouts/%s/free_payout" % bare(m.group(1)))


@route("payout_reject_admin_bulk", "POST", r"^/v1/admin/payouts/cancel$", {"admin"})
def h_admin_bulk_reject(ctx, m):
    """Payout/Service.php:1614 bulkRejectFundAccountPayout: payout_ids scoped to the merchant (findManyByPublicIdsAndMerchant),
    each rejected; for PS payouts the reject is the microservice reject (Workflow.php)."""
    ids = (ctx.body or {}).get("payout_ids")
    if not isinstance(ids, list) or not ids:
        return err(400, "BAD_REQUEST_VALIDATION_FAILURE", "The payout ids field is required.", field="payout_ids")
    results = []
    for pid in ids:
        if not re.match(r"^pout_[A-Za-z0-9]{14}$", str(pid)):
            results.append({"payout_id": pid, "error": {"code": "BAD_REQUEST_ERROR", "description": "The id provided does not exist"}})
            continue
        st, raw, _, _ = _workflow_action(ctx, pid, "reject")
        results.append({"payout_id": pid, "status_code": st, "response": jbody(raw)})
    return 200, json.dumps({"admin": ctx.identity["id"], "merchant_id": ctx.tenant, "count": len(results), "items": results}).encode(), "application/json", "ps:workflow-reject"


# ---------------------------------------------------------------------------
# payouts-service callback group -> monolith-stub pass-through (+ SourceUpdater relay to vendor-payments)
# ---------------------------------------------------------------------------
def relay_source_update(ctx, body):
    """api Models/Payout/SourceUpdater/Factory.php:31-35 -> VendorPaymentUpdater -> VendorPayments/Service.php:480-503
    pushPayoutStatusUpdate: {payout_status, payout_id, merchant_id, source_type, source_id} to vendor-payments'
    PayoutStatusChange. The twin delivers it to the Source-to-Pay source driver's callback adapter."""
    if not S2P_VP_SOURCE_URL or not isinstance(body, dict):
        return None
    details = [d for d in (body.get("source_details") or []) if isinstance(d, dict) and d.get("source_type") in VP_SOURCE_TYPES]
    if not details:
        return None
    pid = bare(str(body.get("payout_id") or ""))
    with _DB_LOCK, db() as c:
        row = c.execute("SELECT * FROM internal_payouts WHERE payout_id=?", (pid,)).fetchone()
    merchant_id = row["merchant_id"] if row else None
    if not merchant_id:
        # source_update for a payout the ingress did not create for an internal app: PS knows the merchant; ask it.
        return {"skipped": "payout not registered as an internal-app payout", "payout_id": pid}
    status = body.get("expected_current_status") or body.get("status") or ""
    out = []
    for d in details:
        payload = {"payout_id": "pout_" + pid, "payout_status": status, "merchant_id": merchant_id,
                   "source_type": d["source_type"], "source_id": d.get("source_id", "")}
        h = {"Content-Type": "application/json", "X-Request-ID": ctx.request_id, "X-Razorpay-TaskId": ctx.request_id,
             "X-Merchant-Id": merchant_id}
        st, raw, _ = upstream("POST", S2P_VP_SOURCE_URL + "/_replica/status", payload, h, timeout=15)
        with _DB_LOCK, db() as c:
            c.execute("INSERT INTO callbacks(request_id,ts,payout_id,merchant_id,source_type,source_id,payout_status,target,status,response) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (ctx.request_id, _now(), pid, merchant_id, d["source_type"], d.get("source_id", ""), status,
                       S2P_VP_SOURCE_URL + "/_replica/status", st, raw.decode("utf-8", "replace")[:1000]))
        out.append({"source_type": d["source_type"], "status": st})
    return out


SERVICE_PASS_THROUGH_PREFIXES = ("/v1/payouts_service/", "/v1/internal/merchants/", "/v1/merchant/on_hold_slas_internal",
                                 "/v1/actor_info_internal/", "/v1/users_internal", "/v1/internal_balances_queued",
                                 "/v1/update_fts_fund_transfer", "/v1/payouts/banking_account_statement/payout_update",
                                 "/v1/banking_account_statement/payout_update", "/v1/payouts/purposes")


# ---------------------------------------------------------------------------
# control plane (never under /v1): health, reset, evidence, synthetic identity provider, registry
# ---------------------------------------------------------------------------
def control(ctx):
    p, m = ctx.path, ctx.method
    if p in ("/health", "/ping") and m == "GET":
        return 200, {"status": "ok", "service": SERVICE_NAME}
    if p == "/_ingress/health" and m == "GET":
        with _DB_LOCK, db() as c:
            counts = {k: c.execute("SELECT count(*) FROM %s" % k).fetchone()[0] for k in ("resources", "banking_accounts", "sessions", "otps", "idempotency", "audit", "payout_details", "internal_payouts", "callbacks")}
            boot = c.execute("SELECT value FROM meta WHERE name='boot_id'").fetchone()
        return 200, {"status": "ok", "service": SERVICE_NAME, "schema_version": SCHEMA_VERSION, "boot_id": boot[0] if boot else None,
                     "api_keys": len(REG.keys), "merchants": len(REG.merchants), "app_identities": sorted(REG.apps.values()),
                     "passport_signer": bool(REG.passport), "ps_api_url": PS_API_URL, "monolith_stub_url": MONOLITH_STUB_URL,
                     "s2p_vp_source_url": S2P_VP_SOURCE_URL or None, "contract_routes": len(CONTRACT), "served_routes": len(ROUTES), "tables": counts}
    if p == "/_ingress/contract" and m == "GET":
        return 200, {"served": [{"name": r.name, "method": r.method, "pattern": r.rx.pattern, "contexts": sorted(r.contexts),
                                 "internal_apps": sorted(route_apps(r.name)), "idempotent": r.idempotent,
                                 "source_refs": (CONTRACT.get(r.name) or {}).get("source_refs", [])} for r in ROUTES]}
    # everything below needs the synthetic admin identity (the control plane is an operator surface)
    ident = authenticate(ctx)
    if not ident or ident["type"] != "admin":
        return 401, {"error": {"code": "BAD_REQUEST_ERROR", "description": "Authentication failed"}}
    body = ctx.body if isinstance(ctx.body, dict) else {}
    if p == "/_ingress/reset" and m == "POST":
        with _DB_LOCK, db() as c:
            for t in ("sessions", "otps", "idempotency", "audit", "payout_details", "internal_payouts", "callbacks"):
                c.execute("DELETE FROM %s" % t)
            c.execute("DELETE FROM resources WHERE source<>'seed'")
            c.execute("DELETE FROM banking_accounts")
            c.execute("INSERT OR REPLACE INTO meta(name,value) VALUES('last_reset',?)", (str(_now()),))
        REG.reload(force=True)
        return 200, {"reset": True, "at": _now()}
    if p == "/_ingress/session" and m == "POST":
        mid, uid = body.get("merchant_id"), body.get("user_id")
        if not mid or mid not in REG.merchants or not uid or not ID_RE.match(str(uid)):
            return 400, {"error": {"code": "BAD_REQUEST_ERROR", "description": "merchant_id must be a known merchant and user_id well-formed"}}
        token = pysecrets.token_urlsafe(24)
        with _DB_LOCK, db() as c:
            c.execute("INSERT INTO sessions(token,user_id,merchant_id,roles,created_at) VALUES(?,?,?,?,?)", (token, uid, mid, json.dumps(body.get("roles") or []), _now()))
        return 200, {"session_token": token, "user_id": uid, "merchant_id": mid, "identity": "dashboard user (proxy auth)"}
    if p == "/_ingress/otp" and m == "POST":
        mid, uid = body.get("merchant_id"), body.get("user_id")
        if not mid or not uid:
            return 400, {"error": {"code": "BAD_REQUEST_ERROR", "description": "merchant_id and user_id required"}}
        otp = "%06d" % pysecrets.randbelow(1000000); token = pysecrets.token_urlsafe(16)
        with _DB_LOCK, db() as c:
            c.execute("INSERT INTO otps(token,otp,user_id,merchant_id,action,created_at) VALUES(?,?,?,?,?,?)", (token, otp, uid, mid, body.get("action", ""), _now()))
        return 200, {"otp": otp, "token": token, "user_id": uid, "merchant_id": mid}
    if p == "/_ingress/registry/internal_contact" and m == "POST":
        mid = body.get("merchant_id"); ctype = body.get("type", "rzp_tax_pay"); ba = body.get("bank_account") or {}
        if not mid or mid not in REG.merchants or ctype not in INTERNAL_CONTACT_TYPES or not ba.get("account_number") or not ba.get("ifsc"):
            return 400, {"error": {"code": "BAD_REQUEST_ERROR", "description": "merchant_id (known), internal type and bank_account{account_number,ifsc,name} required"}}
        cid = "ARENA" + uuid.uuid4().hex[:9].upper(); fid = "ARENA" + uuid.uuid4().hex[:9].upper()
        contact = {"id": "cont_" + cid, "entity": "contact", "name": body.get("name", "Razorpay Tax Payment"), "type": ctype, "active": True, "created_at": _now()}
        fa = {"id": fid, "entity": "fund_account", "contact_id": "cont_" + cid, "account_type": "bank_account", "active": True, "batch_id": "",
              "bank_account": {"id": "ba_" + uuid.uuid4().hex[:14].upper(), "ifsc": ba["ifsc"], "bank_name": ba.get("bank_name", "Synthetic Bank"),
                               "name": ba.get("name", contact["name"]), "account_number": ba["account_number"]},
              "created_at": _now(), "contact": contact, "merchant_id": mid}
        with _DB_LOCK, db() as c:
            c.execute("INSERT INTO resources(kind,id,merchant_id,contact_id,contact_type,record,source,created_at) VALUES('contact',?,?,NULL,?,?,'registered',?)", (cid, mid, ctype, json.dumps(contact, sort_keys=True), _now()))
            c.execute("INSERT INTO resources(kind,id,merchant_id,contact_id,contact_type,record,source,created_at) VALUES('fund_account',?,?,?,?,?,'registered',?)", (fid, mid, cid, ctype, json.dumps(fa, sort_keys=True), _now()))
        return 200, {"contact_id": "cont_" + cid, "fund_account_id": "fa_" + fid, "merchant_id": mid, "type": ctype}
    if p == "/_ingress/registry/banking_account" and m == "POST":
        mid = body.get("merchant_id"); acct = str(body.get("account_number") or "")
        if not mid or mid not in REG.merchants or not acct:
            return 400, {"error": {"code": "BAD_REQUEST_ERROR", "description": "merchant_id (known) and account_number required"}}
        with _DB_LOCK, db() as c:
            c.execute("INSERT OR REPLACE INTO banking_accounts(account_number,merchant_id,balance_id,account_type,channel,source,created_at) VALUES(?,?,?,?,?,?,?)",
                      (acct, mid, body.get("balance_id", ""), body.get("account_type", "shared"), body.get("channel", ""), "registered", _now()))
        return 200, {"merchant_id": mid, "account_number": acct}
    if p == "/_ingress/registry/fund_account" and m == "POST":
        fid = bare(str(body.get("id") or "")); mid = body.get("merchant_id")
        if not fid or not mid or mid not in REG.merchants:
            return 400, {"error": {"code": "BAD_REQUEST_ERROR", "description": "id and merchant_id (known) required"}}
        rec = body.get("record") or {}
        rec = dict(rec); rec["merchant_id"] = mid; rec.setdefault("id", fid)
        contact = rec.get("contact") or {}
        with _DB_LOCK, db() as c:
            c.execute("INSERT OR REPLACE INTO resources(kind,id,merchant_id,contact_id,contact_type,record,source,created_at) VALUES('fund_account',?,?,?,?,?,'registered',?)",
                      (fid, mid, bare(rec.get("contact_id") or contact.get("id") or ""), contact.get("type"), json.dumps(rec, sort_keys=True), _now()))
            if contact.get("id"):
                c.execute("INSERT OR REPLACE INTO resources(kind,id,merchant_id,contact_id,contact_type,record,source,created_at) VALUES('contact',?,?,NULL,?,?,'registered',?)",
                          (bare(contact["id"]), mid, contact.get("type"), json.dumps(contact, sort_keys=True), _now()))
        return 200, {"id": "fa_" + fid, "merchant_id": mid}
    if p == "/_ingress/registry/reload" and m == "POST":
        REG.reload(force=True)
        return 200, {"reloaded": True, "api_keys": len(REG.keys), "merchants": len(REG.merchants), "seed_fund_accounts": REG.seed_fund_accounts}
    if p == "/_ingress/evidence" and m == "GET":
        q = urllib.parse.parse_qs(ctx.query)
        with _DB_LOCK, db() as c:
            if q.get("request_id"):
                rows = c.execute("SELECT * FROM audit WHERE request_id=? ORDER BY id", (q["request_id"][0],)).fetchall()
            elif q.get("tenant"):
                rows = c.execute("SELECT * FROM audit WHERE tenant=? ORDER BY id DESC LIMIT 500", (q["tenant"][0],)).fetchall()
            else:
                rows = c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT %d" % min(int((q.get("limit") or ["200"])[0]), 2000)).fetchall()
            cbs = c.execute("SELECT * FROM callbacks ORDER BY id DESC LIMIT 200").fetchall()
            tags = c.execute("SELECT * FROM payout_details ORDER BY updated_at DESC LIMIT 200").fetchall()
            ipo = c.execute("SELECT * FROM internal_payouts ORDER BY created_at DESC LIMIT 200").fetchall()
            idem = c.execute("SELECT scope,key,route,request_hash,status,request_id,created_at FROM idempotency ORDER BY created_at DESC LIMIT 200").fetchall()
        return 200, {"audit": [dict(r) for r in rows], "callbacks": [dict(r) for r in cbs], "payout_details": [dict(r) for r in tags],
                     "internal_payouts": [dict(r) for r in ipo], "idempotency": [dict(r) for r in idem]}
    if p == "/_ingress/registry/fund_accounts" and m == "GET":
        q = urllib.parse.parse_qs(ctx.query)
        with _DB_LOCK, db() as c:
            if q.get("merchant_id"):
                rows = c.execute("SELECT kind,id,merchant_id,contact_id,contact_type,source,created_at FROM resources WHERE merchant_id=?", (q["merchant_id"][0],)).fetchall()
            elif q.get("id"):
                rows = c.execute("SELECT kind,id,merchant_id,contact_id,contact_type,source,created_at FROM resources WHERE id=?", (bare(q["id"][0]),)).fetchall()
            else:
                rows = c.execute("SELECT kind,id,merchant_id,contact_id,contact_type,source,created_at FROM resources LIMIT 500").fetchall()
            bas = c.execute("SELECT * FROM banking_accounts").fetchall()
        return 200, {"resources": [dict(r) for r in rows], "banking_accounts": [dict(r) for r in bas]}
    return 404, {"error": {"code": "BAD_REQUEST_ERROR", "description": "no control route %s %s" % (m, p)}}


# ---------------------------------------------------------------------------
# dispatcher
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "api-ingress/1.0"

    def log_message(self, fmt, *args):
        _log(fmt % args)

    def _send(self, status, raw, ctype="application/json", extra=None):
        if not isinstance(raw, (bytes, bytearray)):
            raw = json.dumps(raw).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _dispatch(self):
        ctx = Ctx(self)
        if not ctx.read_body():
            return self._send(413, {"error": {"code": "BAD_REQUEST_ERROR", "description": "body too large"}})
        REG.reload()
        try:
            if ctx.path.startswith("/_ingress") or ctx.path in ("/health", "/ping"):
                st, payload = control(ctx)
                return self._send(st, payload, extra={"X-Request-ID": ctx.request_id})
            st, raw, ctype = self._business(ctx)
            self._send(st, raw, ctype, extra={"X-Request-ID": ctx.request_id})
        except Exception as exc:  # noqa: BLE001 -- the ingress must never crash the process
            _log("handler error %s %s: %r" % (ctx.method, ctx.path, exc))
            audit(ctx.request_id, ctx.method, ctx.path, None, ctx.identity, ctx.tenant, None, None, 500, "ingress_error", repr(exc))
            self._send(500, {"error": {"code": "SERVER_ERROR", "description": "ingress internal error"}})

    def _business(self, ctx):
        ident = authenticate(ctx)
        if ident is None or ident["type"] == "unknown_credential":
            audit(ctx.request_id, ctx.method, ctx.path, None, None, None, None, None, 401, "no_credential")
            return UNAUTHORIZED[0], json.dumps(UNAUTHORIZED[1]).encode(), "application/json"
        if ident["type"].startswith("invalid_"):
            # a bad merchant key is 401 (BasicAuth key auth); a bad app/admin/session credential falls through to
            # routeNotFound like BasicAuth::appAuth (the monolith never confirms an internal route exists)
            st, payload = UNAUTHORIZED if ident["type"] == "invalid_merchant_key" else ROUTE_NOT_FOUND
            audit(ctx.request_id, ctx.method, ctx.path, None, ident, None, None, None, st, ident["type"])
            return st, json.dumps(payload).encode(), "application/json"
        ctx.identity = ident
        tenant, terr = resolve_tenant(ctx)
        if terr:
            audit(ctx.request_id, ctx.method, ctx.path, None, ident, None, None, None, terr[0], "unknown_account_header")
            return terr[0], json.dumps(terr[1]).encode(), "application/json"
        ctx.tenant = tenant
        r, m = match_route(ctx)
        if r is None:
            # payouts-service callback group: pass through to the M6 monolith-stub (same identity the stub expects)
            if ident["type"] == "application" and ident["app"] == "payouts_service" and ctx.path.startswith(SERVICE_PASS_THROUGH_PREFIXES):
                relay = relay_source_update(ctx, ctx.body) if ctx.path == "/v1/payouts_service/source_update" else None
                st, raw, ctype, url = forward_stub(ctx, ctx.method, ctx.path, ctx.raw, ctx.query)
                audit(ctx.request_id, ctx.method, ctx.path, "stub:pass-through", ident, tenant, url, st, st, "pass_through", {"relay": relay})
                return st, raw, ctype
            audit(ctx.request_id, ctx.method, ctx.path, None, ident, tenant, None, None, 400, "route_not_found")
            return ROUTE_NOT_FOUND[0], json.dumps(ROUTE_NOT_FOUND[1]).encode(), "application/json"
        ctx.route = r.name
        if r.name not in CONTRACT:
            audit(ctx.request_id, ctx.method, ctx.path, r.name, ident, tenant, None, None, 400, "route_without_contract_evidence")
            return ROUTE_NOT_FOUND[0], json.dumps(ROUTE_NOT_FOUND[1]).encode(), "application/json"
        # --- authorization: context class admitted by the route's Route.php group ---
        if ident["type"] not in r.contexts:
            st, payload = ROUTE_NOT_FOUND
            audit(ctx.request_id, ctx.method, ctx.path, r.name, ident, tenant, None, None, st, "context_not_admitted")
            return st, json.dumps(payload).encode(), "application/json"
        if ident["type"] == "application":
            allowed = r.apps or route_apps(r.name)
            if ident["app"] not in allowed:
                audit(ctx.request_id, ctx.method, ctx.path, r.name, ident, tenant, None, None, 400, "app_not_in_internalApps")
                return ROUTE_NOT_FOUND[0], json.dumps(ROUTE_NOT_FOUND[1]).encode(), "application/json"
        if r.needs_tenant and not tenant and ident["type"] in ("application", "admin") and r.name != "payout_bulk_create":
            audit(ctx.request_id, ctx.method, ctx.path, r.name, ident, None, None, None, 400, "missing_account_scope")
            e = err(400, "BAD_REQUEST_ERROR", "The X-Razorpay-Account header is required", field="X-Razorpay-Account")
            return e[0], json.dumps(e[1]).encode(), "application/json"
        if ctx.body is None and ctx.method in ("POST", "PATCH") and ctx.raw:
            e = err(400, "BAD_REQUEST_ERROR", "invalid JSON body")
            return e[0], json.dumps(e[1]).encode(), "application/json"
        # --- merchant idempotency (MerchantIdempotencyHandler.php) ---
        idem_scope = None
        idem_key = ctx.headers.get("x-payout-idempotency", "")
        if r.idempotent and (ident["type"] in ("merchant", "user") or (ident["type"] == "application" and ident["app"] in IDEMPOTENT_APPS)):
            if not idem_key:
                if ident["type"] == "application":
                    e = err(400, "BAD_REQUEST_ERROR", "Idempotency key is missing. Include idempotency header and key in the request.", field="X-Payout-Idempotency")
                    audit(ctx.request_id, ctx.method, ctx.path, r.name, ident, tenant, None, None, 400, "missing_idempotency_key")
                    return e[0], json.dumps(e[1]).encode(), "application/json"
            else:
                idem_scope = "%s:%s" % (tenant, r.name)
                rh = request_hash(ctx.body)
                with _DB_LOCK, db() as c:
                    row = c.execute("SELECT * FROM idempotency WHERE scope=? AND key=?", (idem_scope, idem_key)).fetchone()
                if row is not None:
                    if row["request_hash"] != rh:
                        e = err(400, "BAD_REQUEST_ERROR", "Different request body sent for the same Idempotency Header")
                        audit(ctx.request_id, ctx.method, ctx.path, r.name, ident, tenant, None, None, 400, "idempotency_payload_conflict", {"key": idem_key})
                        return e[0], json.dumps(e[1]).encode(), "application/json"
                    audit(ctx.request_id, ctx.method, ctx.path, r.name, ident, tenant, "ingress:idempotency", row["status"], row["status"], "idempotent_replay", {"key": idem_key, "first_request_id": row["request_id"]})
                    return row["status"], row["response"].encode(), "application/json"
        # --- handler ---
        result = r.handler(ctx, m)
        if len(result) == 2:                      # (status, payload) decided locally by the ingress
            st, payload = result; raw, ctype, url = json.dumps(payload).encode(), "application/json", None
        else:                                     # (status, raw, content-type, upstream url)
            st, raw, ctype, url = result
        if idem_scope and st == 200:
            with _DB_LOCK, db() as c:
                c.execute("INSERT OR REPLACE INTO idempotency(scope,key,route,request_hash,status,response,request_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                          (idem_scope, idem_key, r.name, request_hash(ctx.body), st, raw.decode("utf-8", "replace"), ctx.request_id, _now()))
        audit(ctx.request_id, ctx.method, ctx.path, r.name, ident, ctx.tenant, url, st if url else None, st,
              "served" if st < 400 else "denied_or_failed", {"upstream_body": raw.decode("utf-8", "replace")[:300]} if st >= 400 else None)
        return st, raw, ctype

    do_GET = _dispatch
    do_POST = _dispatch
    do_PATCH = _dispatch
    do_PUT = _dispatch
    do_DELETE = _dispatch


def main():
    global REG, CONTRACT
    init_db()
    CONTRACT = load_contract()
    REG = Registry()
    _log("serving %d contract routes (%d in inventory) on :%d -> PS %s, stub %s, s2p %s" %
         (len(ROUTES), len(CONTRACT), LISTEN_PORT, PS_API_URL, MONOLITH_STUB_URL, S2P_VP_SOURCE_URL or "-"))
    ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
