#!/usr/bin/env python3
"""batch-sim: HIGH-FIDELITY REPLACEMENT for the payout batch types of
razorpay/batch. NOT the original Java service. See CONTRACT.md.

Fidelity anchors (file:line in the two read-only source trees; BATCH =
razorpay/batch @3839dd7, PS = razorpay/payouts as pinned by the twin):

  BATCH src/main/resources/payout.json
      endpoint "payouts/bulk", bulkSize 5, failOnServerError false,
      idempotentKey "idempotency_key", notesColumn "notes",
      optionalParameters -> payout_reference_id/payout_narration/payout_amount/
      payout_amount_rupees/contact_*/account_email/account_phone_number
  BATCH src/main/resources/payout_approval.json:66
      endpoint "payouts/bulk_approve", bulkSize 5, failOnServerError absent
      (Java default true, BulkApiCallDataProcessorImpl.java:88)
  BATCH src/main/resources/v2/payouts_*_bene_*_process.json
      endpoint "payouts/bulk", bulkSize 5, failOnServerError false,
      cacheIdempotencyEnabledBulkApiCallDataProcessor
  BATCH BulkApiCallDataProcessorImpl.java:106  idempotentKeyPrefix = "batch_"
  BATCH BulkApiCallDataProcessorImpl.java:163  idempotentKey = prefix + record.getId()
  BATCH CustomIdGenerator.java:37-51           14-char base62 entity id
  BATCH PayoutApiProcessorImpl.java:22-105     optionalParameters -> nested payload
  BATCH PayoutApprovalProcessorImpl.java:22-51 user_comment default "Bulk approved"
  BATCH CacheBasedIdempotencyHelper.java:26-62 SHA1(row composite key)
  BATCH PayoutConstant.java:48-59              cache/mutex key formats, TTLs, messages
  BATCH RestCallUtils.java:38-53               X-Batch-Id/X-Creator-Id/X-Creator-Type/X-Entity-Id
  BATCH StatusCodeUtil.java:19                 retryable 500,501,502,503,504
  BATCH application.properties:201-202         max-attempts 5, back-off 5000ms
  BATCH ApiCallDataProcessorHelper.java:96-101 fixed backoff by default
  BATCH BulkApiCallDataProcessorImpl.java:379-470  recovery / failOnServerError
  BATCH enums/BatchStatus.java:7, BatchEntryStatus.java:5, entity/Batch.java:38-129

  PS internal/routing/router/payout_internal_routes_with_passport.go:13-34
      POST /v1/payouts/bulk, BasicAuth(cred.API, cred.Workflow) + passport
  PS internal/app/dtos/bulkPayoutCreateRequest.go:7-48   request array DTO
  PS internal/app/dtos/bulkPayoutsAPIResponse.go:9-13    {entity,count,items}
  PS internal/app/dtos/bulkPayoutErrorResponse.go:9-19   per-row error item
  PS internal/app/dtos/payoutApiResponse.go:12-37        success item (has idempotency_key)
  PS internal/app/bulkPayoutsProcessor/constants.go:6    MaxBulkPayoutsLimit = 15
  PS internal/app/bulkPayoutsProcessor/core.go:352-390   duplicate ikey -> existing payout
  PS internal/constants/headers.go:18,20,37,38           x-batch-id/X-Entity-Id/x-creator-*
  PS internal/helpers/helpers.go:61-74                   x-batch-id is mandatory
  PS internal/routing/middleware/passport.go:37-78,157-162  passport required, empty list = no type check
  PS internal/routing/router/payout_internal_routes.go:70-80  payouts_internal/:id/approve|reject
  PS internal/controllers/payoutController.go:1058-1066  409 == already transitioned

Stdlib only (http.server + sqlite3), mirroring substitutes/workflow-sim/server.py
and substitutes/kong-lite/server.py conventions: logging, /health, secret-file
reads, ThreadingHTTPServer, /_arena/* control plane.
"""
import base64
import binascii
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# _common/ lives at /app/_common in the container image (substitutes/Dockerfile
# copies it there); on the host it is the sibling directory of this stub.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("/app", os.path.dirname(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:  # passport minting; optional so the stub still boots without the key
    from _common.rsa_sign import (  # noqa: E402
        parse_rsa_private_key_der, pem_to_der, rsa_sign_pkcs1v15_sha256, b64url,
    )
    _RSA_AVAILABLE = True
except ImportError:  # pragma: no cover - only when _common is missing
    _RSA_AVAILABLE = False

    def b64url(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------
SERVICE_NAME = os.environ.get("STUB_NAME", "batch-sim")
LISTEN_PORT = int(os.environ.get("STUB_PORT", "8094"))
DATA_DIR = os.environ.get("BATCH_SIM_DATA_DIR", "/data")

PS_API_URL = os.environ.get("PS_API_URL", "http://payouts-api:9400").rstrip("/")
# M7: "monolith" = bulk create goes to the shared API-monolith ingress as the `batch` internal application
# (Route.php payout_bulk_create, Route::$proxy, X-Entity-Id merchant) which mints the passport and forwards to
# PS; "direct" = the M6 collapsed hop (cred.API + self-minted passport straight to PS). Approve/reject always
# use PS_DIRECT_URL (the monolith bulk_approve route is outside the selected ingress surface).
UPSTREAM_MODE = os.environ.get("BATCH_SIM_UPSTREAM_MODE", "direct")
PS_DIRECT_URL = os.environ.get("PS_DIRECT_URL", PS_API_URL).rstrip("/")
BATCH_APP_AUTH_PASS_FILE = os.environ.get("BATCH_APP_AUTH_PASS_FILE", "/run/secrets/app_batch")
BATCH_APP_AUTH_PASS = os.environ.get("BATCH_APP_AUTH_PASS", "")  # test-only override

# cred.API -- payout_internal_routes_with_passport.go:16. Same pair kong-lite
# injects for PS calls (docker-compose.yml PS_API_AUTH_USER/PS_API_AUTH_PASS_FILE).
PS_API_AUTH_USER = os.environ.get("PS_API_AUTH_USER", "api")
PS_API_AUTH_PASS_FILE = os.environ.get("PS_API_AUTH_PASS_FILE", "/run/secrets/auth_api_payouts")
PS_API_AUTH_PASS = os.environ.get("PS_API_AUTH_PASS", "")  # test-only override

# cred.Workflow -- payout_internal_routes.go:16, identical to workflow-sim.
WORKFLOW_CALLBACK_USER = os.environ.get("WORKFLOW_CALLBACK_USER", "rzp_live")
WORKFLOW_CALLBACK_PASS_FILE = os.environ.get("WORKFLOW_CALLBACK_PASS_FILE",
                                             "/run/secrets/auth_workflow_payouts")
WORKFLOW_CALLBACK_PASS = os.environ.get("WORKFLOW_CALLBACK_PASS", "")  # test-only override

# Passport signer -- same key + claim shape as kong-lite/server.py:152-175.
PASSPORT_PRIVATE_KEY_FILE = os.environ.get("PASSPORT_PRIVATE_KEY_FILE",
                                           "/run/secrets/passport_private_key")
PASSPORT_IDENTIFIER = os.environ.get("PASSPORT_IDENTIFIER", "arena-passport-1")
PASSPORT_ORG = os.environ.get("PASSPORT_ORG", "ARENAORG000001")
PASSPORT_PRODUCT = os.environ.get("PASSPORT_PRODUCT", "banking")
PASSPORT_ISS = os.environ.get("PASSPORT_ISS", "https://identity.arena.invalid")
PASSPORT_SUB = os.environ.get("PASSPORT_SUB", "urn:arena:payouts")
PASSPORT_TTL_SEC = int(os.environ.get("PASSPORT_TTL_SEC", "300"))

# Optional inbound gate on batch-sim's own API ("user:pass" file), same
# mechanism as _common/base_stub.py's STUB_BASIC_AUTH_FILE.
INBOUND_AUTH_FILE = os.environ.get("BATCH_SIM_INBOUND_AUTH_FILE", "")

# BATCH application.properties:201-202 (resttemplate.retry.max-attempts=5,
# resttemplate.read.back-off-period=5000).
MAX_ATTEMPTS = int(os.environ.get("BATCH_SIM_MAX_ATTEMPTS", "5"))
BACKOFF_MS = int(os.environ.get("BATCH_SIM_BACKOFF_MS", "5000"))
HTTP_TIMEOUT = float(os.environ.get("BATCH_SIM_HTTP_TIMEOUT", "30"))

# StatusCodeUtil.java:19
RETRYABLE_STATUSES = (500, 501, 502, 503, 504)
# workflow-sim/CONTRACT.md + payoutController.go:1058-1066
APPROVE_SUCCESS_CODES = (200, 201, 409)
# bulkPayoutsProcessor/constants.go:6
MAX_BULK_PAYOUTS_LIMIT = 15

# PayoutConstant.java:48-59
PAYOUT_IDEMPOTENCY_CACHE_KEY_FMT = "payouts:%s:%s"
PAYOUT_IDEMPOTENCY_TTL_HOURS = 24
PAYOUT_IDEMPOTENCY_MUTEX_KEY_FMT = "payouts:mtx:%s:%s"
PAYOUT_IDEMPOTENCY_MUTEX_SECONDS = 300
DUPLICATE_PAYOUT_ERROR_MESSAGE = (
    "Duplicate payout found in last 24h with the id: %s. To bypass this check, "
    "change the payout reference id and try again.")
DUPLICATE_PAYOUT_IN_SAME_FILE_ERROR_MESSAGE = (
    "Duplicate payout found in the same file. To bypass this check, change the "
    "payout reference id and try again.")
MUTEX_ACQUIRE_FAILED_ERROR_MESSAGE = (
    "An error occurred while processing the payout as a duplicate payout might "
    "be in progress.")
PAYOUT_ERROR_STATUS_CODE = 400  # PayoutConstant.java:59

IDEMPOTENT_KEY_PREFIX = "batch_"  # BulkApiCallDataProcessorImpl.java:106

BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _log(msg):
    sys.stderr.write("[%s] %s %s\n" % (SERVICE_NAME, time.strftime("%Y-%m-%dT%H:%M:%S"), msg))
    sys.stderr.flush()


def _read_secret_file(path):
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return ""


_PS_API_PASSWORD = PS_API_AUTH_PASS or _read_secret_file(PS_API_AUTH_PASS_FILE)
_WORKFLOW_PASSWORD = WORKFLOW_CALLBACK_PASS or _read_secret_file(WORKFLOW_CALLBACK_PASS_FILE)

_PASSPORT_N = _PASSPORT_D = _PASSPORT_KEY_SIZE = None
if _RSA_AVAILABLE and os.path.exists(PASSPORT_PRIVATE_KEY_FILE):
    try:
        with open(PASSPORT_PRIVATE_KEY_FILE) as _fh:
            _PASSPORT_N, _PASSPORT_D, _PASSPORT_KEY_SIZE = parse_rsa_private_key_der(
                pem_to_der(_fh.read()))
        _log("loaded passport signing key (kid=%s, %d-bit)"
             % (PASSPORT_IDENTIFIER, _PASSPORT_KEY_SIZE * 8))
    except Exception as exc:  # noqa: BLE001 - never crash the stub on a bad key
        _log("WARNING: could not parse %s (%r); X-Passport-JWT-V1 will be omitted"
             % (PASSPORT_PRIVATE_KEY_FILE, exc))
else:
    _log("WARNING: no passport key at %s -- X-Passport-JWT-V1 will be omitted and "
         "the REAL payouts-api will reject /v1/payouts/bulk" % PASSPORT_PRIVATE_KEY_FILE)

_INBOUND_AUTH = _read_secret_file(INBOUND_AUTH_FILE) if INBOUND_AUTH_FILE else ""


# --------------------------------------------------------------------------
# batch type registry (CONTRACT.md section 2)
# --------------------------------------------------------------------------
V2_BENE_TYPES = (
    "payouts_amazonpay_bene_details_process",
    "payouts_amazonpay_bene_id_process",
    "payouts_bank_transfer_bene_details_process",
    "payouts_bank_transfer_bene_id_process",
    "payouts_upi_bene_details_process",
    "payouts_upi_bene_id_process",
)  # PayoutConstant.java:62-90

BATCH_TYPES = {
    "payout": {
        "kind": "create",
        "endpoint": "payouts/bulk",           # payout.json
        "bulk_size": 5,                       # payout.json "bulkSize": 5
        "fail_on_server_error": False,        # payout.json "failOnServerError": false
        "idempotency": "entry_id",            # BulkApiCallDataProcessorImpl.java:163
    },
    "payout_approval": {
        "kind": "approve",
        "endpoint": "payouts/bulk_approve",   # payout_approval.json:66
        "bulk_size": 5,
        "fail_on_server_error": True,         # field absent -> Java default true (:88)
        "idempotency": "entry_id",
    },
}
for _t in V2_BENE_TYPES:
    BATCH_TYPES[_t] = {
        "kind": "create",
        "endpoint": "payouts/bulk",
        "bulk_size": 5,
        "fail_on_server_error": False,
        "idempotency": "cache_sha1",          # cacheIdempotencyEnabledBulkApiCallDataProcessor
    }

# PayoutConstant.java:70-79 -- the v2 hash input columns, in source order
# (PayoutConstant.java:92-105 getPayoutIdempotencyColumns()).
V2_IDEMPOTENCY_COLUMNS = [
    "Beneficiary's Fund Account ID Wallet (Mandatory) Unique id linked to a Razorpay Fund account.",
    "Payout Amount (Mandatory) Amount should be in rupees",
    "Payout Reference ID (Optional) Eg: Bill no or Invoice No or Pay ID",
    "Payout Reference ID (Optional) Eg: Bill no., Invoice No, Pay ID",
    "Beneficiary's Phone No. Linked with Amazon Pay (Mandatory)",
    "Beneficiary's Account Number (Mandatory) Typically 9-18 digits",
    "IFSC Code (Mandatory) 11 digit code of the beneficiary’s bank account. Eg. HDFC0004277",
    "Payout Mode (Mandatory) Select IMPS/NEFT/RTGS",
    "Beneficiary's Fund Account ID (Mandatory) Unique id linked to a Razorpay Fund account.",
    "Beneficiary's UPI ID (Mandatory)",
]
# Arena convenience aliases for the same v2 hash inputs (batch-sim addition).
V2_IDEMPOTENCY_ALIASES = [
    "fund_account_id_wallet", "payout_amount_rupees", "payout_reference_id",
    "payout_reference_id", "account_phone_number", "fund_account_number",
    "fund_account_ifsc", "payout_mode", "fund_account_id", "fund_account_vpa",
]

# payout.json column headers -> (payload path, snake_case alias).
CREATE_FIELD_MAP = [
    ("RazorpayX Account Number", ("razorpayx_account_number",), "razorpayx_account_number"),
    ("Payout Amount", ("payout", "amount"), "payout_amount"),
    ("Payout Amount (in Rupees)", ("payout", "amount_in_rupees"), "payout_amount_rupees"),
    ("Payout Currency", ("payout", "currency"), "payout_currency"),
    ("Payout Mode", ("payout", "mode"), "payout_mode"),
    ("Payout Purpose", ("payout", "purpose"), "payout_purpose"),
    ("Payout Narration", ("payout", "narration"), "payout_narration"),
    ("Payout Reference Id", ("payout", "reference_id"), "payout_reference_id"),
    ("Fund Account Id", ("fund", "id"), "fund_account_id"),
    ("Fund Account Type", ("fund", "account_type"), "fund_account_type"),
    ("Fund Account Name", ("fund", "account_name"), "fund_account_name"),
    ("Fund Account Ifsc", ("fund", "account_IFSC"), "fund_account_ifsc"),
    ("Fund Account Number", ("fund", "account_number"), "fund_account_number"),
    ("Fund Account Vpa", ("fund", "account_vpa"), "fund_account_vpa"),
    ("Fund Account Phone Number", ("fund", "account_phone_number"), "account_phone_number"),
    ("Fund Account Email", ("fund", "account_email"), "account_email"),
    ("Contact Type", ("contact", "type"), "contact_type"),
    ("Contact Name", ("contact", "name"), "contact_name"),
    ("Contact Email", ("contact", "email"), "contact_email"),
    ("Contact Mobile", ("contact", "mobile"), "contact_mobile"),
    ("Contact Reference Id", ("contact", "reference_id"), "contact_reference_id"),
]

# payout_approval.json column headers -> alias
APPROVAL_FIELD_MAP = [
    ("Approve (A) / Reject (R) payout", "payout_update_action"),
    ("account_number (do not edit)", "account_number"),
    ("payout_id (do not edit)", "payout_id"),
    ("amount(Rupees) (do not edit)", "amount"),
    ("currency (do not edit)", "currency"),
    ("mode (do not edit)", "mode"),
    ("purpose (do not edit)", "purpose"),
    ("payout notes (do not edit)", "narration"),
    ("status (do not edit)", "status"),
    ("fund_account_id (do not edit)", "fund_account_id"),
    ("contact_id (do not edit)", "contact_id"),
    ("contact_name (do not edit)", "contact_name"),
]


# --------------------------------------------------------------------------
# ids -- CustomIdGenerator.java:37-51 (14 chars of base62, time-ordered)
# --------------------------------------------------------------------------
_ID_LOCK = threading.Lock()
_LAST_NANOS = [0]


def _to_base62(value, width):
    out = ""
    while value:
        value, rem = divmod(value, 62)
        out = BASE62[rem] + out
    out = out or "0"
    if len(out) >= width:
        return out[-width:]
    return "0" * (width - len(out)) + out


def new_id():
    """14-char base62 id: 10 chars of monotonic nanotime + 4 random, exactly as
    CustomIdGenerator asserts (`id.length() == 14`, CustomIdGenerator.java:49)."""
    with _ID_LOCK:
        nanos = time.time_ns()
        if nanos <= _LAST_NANOS[0]:
            nanos = _LAST_NANOS[0] + 1
        _LAST_NANOS[0] = nanos
    rand = int.from_bytes(os.urandom(5), "big")
    return _to_base62(nanos, 10) + _to_base62(rand, 4)


# --------------------------------------------------------------------------
# storage (SQLite; real Batch uses Postgres -- CONTRACT.md section 10)
# --------------------------------------------------------------------------
_DB_LOCK = threading.RLock()
_DB = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
  id TEXT PRIMARY KEY, entity_id TEXT, name TEXT, batch_type_id TEXT,
  mode TEXT, creator_id TEXT, creator_type TEXT, version TEXT,
  settings TEXT, status TEXT, total_count INTEGER DEFAULT 0,
  processed_count INTEGER DEFAULT 0, success_count INTEGER DEFAULT 0,
  failure_count INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0,
  amount INTEGER DEFAULT 0, processed_amount INTEGER DEFAULT 0,
  schedule INTEGER, error TEXT, created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS batch_entries (
  id TEXT PRIMARY KEY, batch_id TEXT, seq_number INTEGER,
  row_data TEXT, response_data TEXT, status TEXT, idempotency_key TEXT,
  created_at REAL, updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_entries_batch ON batch_entries(batch_id, seq_number);
CREATE TABLE IF NOT EXISTS idem_cache (
  cache_key TEXT PRIMARY KEY, value TEXT, expires_at REAL
);
"""


def db():
    global _DB
    if _DB is None:
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                "BATCH_SIM_DATA_DIR=%s is not creatable (%s). Under the arena's "
                "read-only root filesystem this path must be a mounted volume."
                % (DATA_DIR, exc))
        path = os.path.join(DATA_DIR, "batch_sim.db")
        if not os.access(DATA_DIR, os.W_OK):
            raise RuntimeError(
                "BATCH_SIM_DATA_DIR=%s is not writable by uid %d. A fresh Docker "
                "named volume is created root-owned unless the image pre-creates "
                "the mountpoint -- build with substitutes/batch-sim/Dockerfile "
                "(which does `mkdir -p /data && chown stub:stub /data`), not the "
                "shared substitutes/Dockerfile." % (DATA_DIR, os.getuid()))
        _DB = sqlite3.connect(path, check_same_thread=False)
        _DB.row_factory = sqlite3.Row
        _DB.executescript(SCHEMA)
        _DB.commit()
        _log("store ready at %s" % path)
    return _DB


def q(sql, args=(), commit=False):
    with _DB_LOCK:
        cur = db().execute(sql, args)
        if commit:
            db().commit()
        return cur.fetchall()


# --------------------------------------------------------------------------
# fault injection (control plane; bounded, local, destination never changes)
# --------------------------------------------------------------------------
_FAULT_LOCK = threading.Lock()
_FAULT = None  # {"count":int,"status":int|None,"drop":bool,"scope":str,"reason":str}
CALL_LOG = []
CALL_LOG_MAX = 200


def _fault_for(scope):
    with _FAULT_LOCK:
        f = _FAULT
        if not f or f["count"] <= 0:
            return None
        if f["scope"] not in ("any", scope):
            return None
        f["count"] -= 1
        return dict(f)


def _record_call(entry):
    CALL_LOG.append(entry)
    del CALL_LOG[:-CALL_LOG_MAX]


# --------------------------------------------------------------------------
# passport (same signer + claim shape as kong-lite/server.py:152-175)
# --------------------------------------------------------------------------
def mint_passport(merchant_id, mode="live", roles=None):
    if _PASSPORT_N is None:
        return None
    now = int(time.time())
    header = {"typ": "JWT", "alg": "RS256", "kid": PASSPORT_IDENTIFIER}
    payload = {
        "iss": PASSPORT_ISS, "sub": PASSPORT_SUB, "jti": str(uuid.uuid4()),
        "iat": now, "nbf": now, "exp": now + PASSPORT_TTL_SEC,
        "identified": True, "authenticated": True, "mode": mode or "live",
        "org": PASSPORT_ORG, "product": PASSPORT_PRODUCT,
        "consumer": {"id": merchant_id, "type": "merchant"},
    }
    if roles:
        payload["roles"] = roles
    signing_input = (b64url(json.dumps(header, separators=(",", ":")).encode()) + "." +
                     b64url(json.dumps(payload, separators=(",", ":")).encode())).encode()
    sig = rsa_sign_pkcs1v15_sha256(signing_input, _PASSPORT_N, _PASSPORT_D, _PASSPORT_KEY_SIZE)
    return signing_input.decode() + "." + b64url(sig)


def _basic(user, password):
    return "Basic " + base64.b64encode(("%s:%s" % (user, password)).encode()).decode()


# --------------------------------------------------------------------------
# row -> PS payload
# --------------------------------------------------------------------------
def _get(row, *names):
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    for name in names:
        if name in row:
            return row[name]
    return ""


def _s(value):
    """PS's BulkPayoutCreateRequest is all-strings (bulkPayoutCreateRequest.go:18-48)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _collect_notes(row):
    """payout.json "notesColumn": "notes" -- notes[key] / notes.key columns."""
    notes = {}
    raw = row.get("notes")
    if isinstance(raw, dict):
        notes.update({k: v for k, v in raw.items() if v not in (None, "")})
    for key, value in row.items():
        if value in (None, ""):
            continue
        m = re.match(r"^notes[\[.]([^\]]+)\]?$", str(key))
        if m:
            notes[m.group(1)] = value
    return notes


def build_create_payload(row, settings, idempotency_key):
    """payout.json `parameters` after PayoutApiProcessorImpl.passSettingsIfRequired
    (PayoutApiProcessorImpl.java:22-105). Shape = dtos.BulkPayoutCreateRequest
    (bulkPayoutCreateRequest.go:9-48)."""
    payload = {
        "razorpayx_account_number": "",
        "notes": {},
        "idempotency_key": idempotency_key,
        "payout": {"amount": "", "amount_in_rupees": "", "currency": "", "mode": "",
                   "purpose": "", "narration": "", "scheduled_at": "", "reference_id": ""},
        "fund": {"id": "", "account_type": "", "account_name": "", "account_IFSC": "",
                 "account_number": "", "account_vpa": "", "account_phone_number": "",
                 "account_email": ""},
        "contact": {"type": "", "name": "", "email": "", "mobile": "", "reference_id": ""},
    }
    for header, path, alias in CREATE_FIELD_MAP:
        value = _s(_get(row, header, alias))
        if len(path) == 1:
            payload[path[0]] = value
        else:
            payload[path[0]][path[1]] = value
    payload["notes"] = _collect_notes(row)
    # PayoutApiProcessorImpl.java:31,:38 -- scheduled_at comes from batch settings.
    scheduled_at = settings.get("scheduled_at")
    payload["payout"]["scheduled_at"] = _s(scheduled_at) if scheduled_at not in (None, "") else ""
    # bulkPayoutCreateRequest.go:27 -- present in the v2 templates only; sent
    # when the row or settings supply it (CONTRACT.md open question 3).
    skip = _get(row, "skip_workflow")
    if skip in (None, ""):
        skip = settings.get("skip_workflow", "")
    if skip not in (None, ""):
        payload["payout"]["skip_workflow"] = _s(skip)
    return payload


def build_approval_row(row, settings):
    """payout_approval.json `parameters` + PayoutApprovalProcessorImpl.java:22-51."""
    out = {}
    for header, alias in APPROVAL_FIELD_MAP:
        out[alias] = _s(_get(row, header, alias))
    out["user_comment"] = _s(settings.get("user_comment", "Bulk approved"))
    out["email"] = _s(settings.get("email", ""))
    out["queue_if_low_balance"] = bool(settings.get("queue_if_low_balance", False))
    return out


def _row_amount_paise(row):
    raw = _get(row, "Payout Amount", "payout_amount")
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------
# v2 cache-based idempotency (CacheBasedIdempotencyHelper.java:26-62)
# --------------------------------------------------------------------------
_MUTEX_LOCK = threading.Lock()
_MUTEXES = {}


def v2_row_hash(row):
    """SHA1 of the non-empty v2 idempotency columns joined by ':'
    (CacheBasedIdempotencyHelper.java:39-53, HashUtils.generateSha1Hash at :30)."""
    parts = []
    for header, alias in zip(V2_IDEMPOTENCY_COLUMNS, V2_IDEMPOTENCY_ALIASES):
        value = _get(row, header, alias)
        if value not in (None, ""):
            parts.append(_s(value))
    composite = ":".join(parts)
    if not composite:
        raise ValueError("recordCompositeKey is empty")
    return hashlib.sha1(composite.encode("utf-8")).hexdigest(), composite


def cache_get(key):
    now = time.time()
    rows = q("SELECT value, expires_at FROM idem_cache WHERE cache_key=?", (key,))
    if not rows:
        return None
    if rows[0]["expires_at"] is not None and rows[0]["expires_at"] < now:
        q("DELETE FROM idem_cache WHERE cache_key=?", (key,), commit=True)
        return None
    return rows[0]["value"]


def cache_set(key, value, ttl_seconds):
    q("INSERT INTO idem_cache(cache_key, value, expires_at) VALUES(?,?,?) "
      "ON CONFLICT(cache_key) DO UPDATE SET value=excluded.value, expires_at=excluded.expires_at",
      (key, value, time.time() + ttl_seconds), commit=True)


def _acquire_mutex(key):
    """Stand-in for RedisMutexLock (PayoutConstant.java:51-52, 300s). The worker
    is single-threaded, so this only ever fails under /_arena/fail-next."""
    with _MUTEX_LOCK:
        held_until = _MUTEXES.get(key, 0)
        if held_until > time.time():
            return False
        _MUTEXES[key] = time.time() + PAYOUT_IDEMPOTENCY_MUTEX_SECONDS
        return True


def _release_mutex(key):
    with _MUTEX_LOCK:
        _MUTEXES.pop(key, None)


# --------------------------------------------------------------------------
# outbound HTTP
# --------------------------------------------------------------------------
class Outcome(object):
    """One attempt's result: HTTP status + parsed body, or a transport failure."""

    def __init__(self, status=None, body=None, transport_error=None):
        self.status = status
        self.body = body
        self.transport_error = transport_error


def _http_post(url, headers, payload, scope):
    fault = _fault_for(scope)
    if fault:
        if fault.get("drop"):
            _record_call({"url": url, "scope": scope, "injected": "drop", "at": time.time()})
            return Outcome(transport_error="injected_connection_failure: %s"
                                           % fault.get("reason", "arena fault"))
        _record_call({"url": url, "scope": scope, "injected": fault["status"], "at": time.time()})
        return Outcome(status=fault["status"],
                       body={"error": {"code": "ARENA_INJECTED_DEPENDENCY_FAILURE",
                                       "description": fault.get("reason",
                                                                "synthetic dependency fault")}})
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            status, text = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status, text = exc.code, (exc.read() or b"").decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return Outcome(transport_error=str(getattr(exc, "reason", exc)))
    try:
        body = json.loads(text) if text else {}
    except ValueError:
        body = {"raw": text}
    return Outcome(status=status, body=body)


def post_with_retry(url, headers, payload, scope):
    """BATCH retry semantics: retry the WHOLE chunk on 500-504 and on transport
    failures, fixed backoff, max_attempts total (StatusCodeUtil.java:19,
    ApiCallDataProcessorHelper.java:66-69,:96-101, application.properties:201-202).
    4xx is never retried."""
    attempt = 0
    outcome = Outcome(transport_error="no attempt made")
    while attempt < MAX_ATTEMPTS:
        attempt += 1
        outcome = _http_post(url, headers, payload, scope)
        _record_call({"at": time.time(), "url": url, "scope": scope, "attempt": attempt,
                      "status": outcome.status, "transport_error": outcome.transport_error,
                      "rows": len(payload) if isinstance(payload, list) else 1,
                      "idempotency_keys": [p.get("idempotency_key") for p in payload]
                      if isinstance(payload, list) else None})
        retryable = (outcome.transport_error is not None
                     or (outcome.status in RETRYABLE_STATUSES))
        if not retryable:
            return outcome, attempt
        if attempt < MAX_ATTEMPTS:
            time.sleep(BACKOFF_MS / 1000.0)
    return outcome, attempt


# --------------------------------------------------------------------------
# batch processing
# --------------------------------------------------------------------------
WORK_QUEUE = []
WORK_CV = threading.Condition()
WORKER_STARTED = [False]


def enqueue(batch_id, force=False):
    with WORK_CV:
        WORK_QUEUE.append((batch_id, force))
        WORK_CV.notify()


def _set_status(batch_id, status, **fields):
    fields["status"] = status
    fields["updated_at"] = time.time()
    cols = ", ".join("%s=?" % k for k in fields)
    q("UPDATE batches SET %s WHERE id=?" % cols, tuple(fields.values()) + (batch_id,), commit=True)


def _entry_done(entry_id, status, response):
    q("UPDATE batch_entries SET status=?, response_data=?, updated_at=? WHERE id=?",
      (status, json.dumps(response), time.time(), entry_id), commit=True)


def _recount(batch_id):
    rows = q("SELECT status, COUNT(*) c FROM batch_entries WHERE batch_id=? GROUP BY status",
             (batch_id,))
    counts = {r["status"]: r["c"] for r in rows}
    success = counts.get("PROCESSED", 0)
    failure = counts.get("FAILED", 0)
    processed_amount = 0
    for r in q("SELECT row_data FROM batch_entries WHERE batch_id=? AND status='PROCESSED'",
               (batch_id,)):
        try:
            processed_amount += _row_amount_paise(json.loads(r["row_data"]))
        except (ValueError, TypeError):
            pass
    q("UPDATE batches SET success_count=?, failure_count=?, processed_count=?, "
      "processed_amount=?, updated_at=? WHERE id=?",
      (success, failure, success + failure, processed_amount, time.time(), batch_id),
      commit=True)


def _batch_app_password():
    if BATCH_APP_AUTH_PASS:
        return BATCH_APP_AUTH_PASS
    try:
        with open(BATCH_APP_AUTH_PASS_FILE) as f:
            return f.read().strip()
    except OSError:
        return ""


def _create_headers(batch, passport):
    """CONTRACT.md section 3.1. RestCallUtils.java:38-53 header names;
    PS constants/headers.go:18,20,37,38 reader side. M7 monolith mode: the Batch service's own
    internal-application credential (rzp_live + app secret, BasicAuth::appAuth) and no passport --
    the shared ingress mints the proxy passport for the X-Entity-Id merchant."""
    if UPSTREAM_MODE == "monolith":
        return {
            "Content-Type": "application/json",
            "Authorization": _basic("rzp_live", _batch_app_password()),
            "x-batch-id": batch["id"],
            "X-Entity-Id": batch["entity_id"] or "",
            "x-creator-id": batch["creator_id"] or "",
            "x-creator-type": batch["creator_type"] or "user",
        }
    headers = {
        "Content-Type": "application/json",
        "Authorization": _basic(PS_API_AUTH_USER, _PS_API_PASSWORD),
        "x-batch-id": batch["id"],
        "X-Entity-Id": batch["entity_id"] or "",
        "x-creator-id": batch["creator_id"] or "",
        # request_context.go:109-116: PS honours x-creator-id only for "user".
        "x-creator-type": batch["creator_type"] or "user",
    }
    if passport:
        headers["X-Passport-JWT-V1"] = passport
    return headers


def _approve_headers(batch, passport=None):
    """CONTRACT.md section 3.2 -- identical identity to workflow-sim."""
    return {
        "Content-Type": "application/json",
        "Authorization": _basic(WORKFLOW_CALLBACK_USER, _WORKFLOW_PASSWORD),
        "x-creator-id": batch["creator_id"] or "",
        "X-Razorpay-Account": batch["entity_id"] or "",
        "x-batch-id": batch["id"],  # INFERRED, see CONTRACT.md section 3.2
    }


def _chunk_error(entries, status, description, code="SERVER_ERROR", attempts=1):
    """BulkApiCallDataProcessorImpl.java:379-470 -- stamp every row in the chunk."""
    for entry in entries:
        _entry_done(entry["id"], "FAILED",
                    {"http_status_code": status, "attempts": attempts,
                     "item": {"idempotency_key": entry["idempotency_key"],
                              "error": {"code": code, "description": description}}})


def _process_create_chunk(batch, entries, settings, passport):
    """One POST /v1/payouts/bulk for up to bulk_size rows."""
    payload = []
    live = []
    for entry in entries:
        row = json.loads(entry["row_data"])
        payload.append(build_create_payload(row, settings, entry["idempotency_key"]))
        live.append(entry)
    if not payload:
        return True
    if len(payload) > MAX_BULK_PAYOUTS_LIMIT:  # bulkPayoutsProcessor/constants.go:6
        _chunk_error(live, 400,
                     "batch-sim refused a chunk of %d rows; MaxBulkPayoutsLimit is %d"
                     % (len(payload), MAX_BULK_PAYOUTS_LIMIT), code="BAD_REQUEST_ERROR")
        return True

    url = PS_API_URL + "/v1/payouts/bulk"
    outcome, attempts = post_with_retry(url, _create_headers(batch, passport), payload, "create")

    if outcome.transport_error is not None:
        # :384-407 -- timeouts are per-row errors regardless of failOnServerError.
        _chunk_error(live, 408, outcome.transport_error, code="GATEWAY_TIMEOUT", attempts=attempts)
        return True
    if outcome.status != 200:
        desc = _error_description(outcome.body) or "http %s" % outcome.status
        if outcome.status in RETRYABLE_STATUSES and BATCH_TYPES[batch["batch_type_id"]]["fail_on_server_error"]:
            _chunk_error(live, outcome.status, desc, attempts=attempts)
            return False  # -> JobFailureException, batch FAILED (:430-431)
        _chunk_error(live, outcome.status, desc,
                     code="BAD_REQUEST_ERROR" if outcome.status < 500 else "SERVER_ERROR",
                     attempts=attempts)
        return True

    # bulkPayoutsAPIResponse.go:9-13 -- items are successes then errors, so match
    # by idempotency_key, which both shapes carry (payoutApiResponse.go:29,
    # bulkPayoutErrorResponse.go:10).
    items = (outcome.body or {}).get("items") or []
    by_key = {}
    for item in items:
        if isinstance(item, dict) and item.get("idempotency_key"):
            by_key[item["idempotency_key"]] = item
    for entry in live:
        item = by_key.get(entry["idempotency_key"])
        if item is None:
            _entry_done(entry["id"], "FAILED",
                        {"http_status_code": 200, "attempts": attempts,
                         "item": {"idempotency_key": entry["idempotency_key"],
                                  "error": {"code": "SERVER_ERROR",
                                            "description": "no item for idempotency_key in "
                                                           "bulk response"}}})
            continue
        if item.get("error"):
            _entry_done(entry["id"], "FAILED",
                        {"http_status_code": item.get("http_status_code", 400),
                         "attempts": attempts, "item": item})
        else:
            _entry_done(entry["id"], "PROCESSED",
                        {"http_status_code": 200, "attempts": attempts, "item": item,
                         "payout_id": item.get("id")})
            _note_v2_payout(batch, entry, item.get("id"))
    return True


def _note_v2_payout(batch, entry, payout_id):
    """v2 cache value becomes '<ikey>:<payout id>' once the payout exists
    (CacheIdempotencyEnabledBulkApiCallDataProcessorImpl.java:118-132 read side)."""
    if BATCH_TYPES[batch["batch_type_id"]]["idempotency"] != "cache_sha1" or not payout_id:
        return
    try:
        row = json.loads(entry["row_data"])
        row_hash, _ = v2_row_hash(row)
    except (ValueError, TypeError):
        return
    key = PAYOUT_IDEMPOTENCY_CACHE_KEY_FMT % (batch["entity_id"], row_hash)
    bare = entry["idempotency_key"][len(IDEMPOTENT_KEY_PREFIX):]
    cache_set(key, "%s:%s" % (bare, payout_id), PAYOUT_IDEMPOTENCY_TTL_HOURS * 3600)


def _process_approve_row(batch, entry, settings):
    """One POST /v1/payouts/payouts_internal/{id}/{approve|reject}."""
    row = json.loads(entry["row_data"])
    approval = build_approval_row(row, settings)
    action_raw = (approval.get("payout_update_action") or "").strip().upper()
    action = {"A": "approve", "R": "reject"}.get(action_raw[:1] if action_raw else "")
    payout_id = approval.get("payout_id") or ""
    if action is None:
        _entry_done(entry["id"], "FAILED",
                    {"http_status_code": 400, "attempts": 0,
                     "item": {"idempotency_key": entry["idempotency_key"],
                              "error": {"code": "BAD_REQUEST_ERROR",
                                        "description": "invalid payout_update_action %r; "
                                                       "expected A or R" % action_raw}}})
        return True
    if not payout_id:
        _entry_done(entry["id"], "FAILED",
                    {"http_status_code": 400, "attempts": 0,
                     "item": {"idempotency_key": entry["idempotency_key"],
                              "error": {"code": "BAD_REQUEST_ERROR",
                                        "description": "missing payout_id"}}})
        return True

    url = "%s/v1/payouts/payouts_internal/%s/%s" % (PS_DIRECT_URL, payout_id, action)
    body = {"queue_if_low_balance": bool(approval["queue_if_low_balance"])} \
        if action == "approve" else {}
    outcome, attempts = post_with_retry(url, _approve_headers(batch), body, "approve")

    if outcome.transport_error is not None:
        _entry_done(entry["id"], "FAILED",
                    {"action": action, "payout_id": payout_id, "http_status_code": 408,
                     "attempts": attempts, "response": {"error": {
                         "code": "GATEWAY_TIMEOUT", "description": outcome.transport_error}}})
        return True
    ok = outcome.status in APPROVE_SUCCESS_CODES  # 200/201/409, payoutController.go:1058-1066
    _entry_done(entry["id"], "PROCESSED" if ok else "FAILED",
                {"action": action, "payout_id": payout_id,
                 "http_status_code": outcome.status, "attempts": attempts,
                 "response": outcome.body})
    if not ok and outcome.status in RETRYABLE_STATUSES \
            and BATCH_TYPES[batch["batch_type_id"]]["fail_on_server_error"]:
        return False  # payout_approval.json -> failOnServerError true -> batch FAILED
    return True


def _error_description(body):
    if not isinstance(body, dict):
        return None
    err = body.get("error")
    if isinstance(err, dict):
        return err.get("description") or err.get("code")
    if isinstance(err, str):
        return err
    return None


def _assign_v2_keys(batch, entries):
    """CacheIdempotencyEnabledBulkApiCallDataProcessorImpl.java:104-172.
    Returns the entries that still need an API call; the rest are already
    short-circuited with a local error."""
    to_send = []
    seen_in_chunk = {}
    for entry in entries:
        row = json.loads(entry["row_data"])
        try:
            row_hash, _ = v2_row_hash(row)
        except ValueError as exc:
            _entry_done(entry["id"], "FAILED",
                        {"http_status_code": PAYOUT_ERROR_STATUS_CODE, "attempts": 0,
                         "item": {"idempotency_key": entry["idempotency_key"],
                                  "error": {"code": "BAD_REQUEST_ERROR",
                                            "description": str(exc)}}})
            continue
        cache_key = PAYOUT_IDEMPOTENCY_CACHE_KEY_FMT % (batch["entity_id"], row_hash)
        if row_hash in seen_in_chunk:  # :66-71 duplicate row in the same file
            _entry_done(entry["id"], "FAILED",
                        {"http_status_code": PAYOUT_ERROR_STATUS_CODE, "attempts": 0,
                         "item": {"idempotency_key": entry["idempotency_key"],
                                  "error": {"code": "BAD_REQUEST_ERROR",
                                            "description":
                                                DUPLICATE_PAYOUT_IN_SAME_FILE_ERROR_MESSAGE}}})
            continue
        seen_in_chunk[row_hash] = True

        cached = cache_get(cache_key)
        if cached is not None:
            parts = cached.split(":")
            if len(parts) == 1:  # :120-129 in flight -> reuse the key
                key = IDEMPOTENT_KEY_PREFIX + parts[0]
                q("UPDATE batch_entries SET idempotency_key=? WHERE id=?",
                  (key, entry["id"]), commit=True)
                entry = dict(entry)
                entry["idempotency_key"] = key
                to_send.append(entry)
                continue
            # :131-136 already created -> no API call, local duplicate error
            _entry_done(entry["id"], "FAILED",
                        {"http_status_code": PAYOUT_ERROR_STATUS_CODE, "attempts": 0,
                         "item": {"idempotency_key": entry["idempotency_key"],
                                  "error": {"code": "BAD_REQUEST_ERROR",
                                            "description":
                                                DUPLICATE_PAYOUT_ERROR_MESSAGE % parts[1]}}})
            continue

        mutex_key = PAYOUT_IDEMPOTENCY_MUTEX_KEY_FMT % (batch["entity_id"], row_hash)
        if not _acquire_mutex(mutex_key):  # :166-169
            _entry_done(entry["id"], "FAILED",
                        {"http_status_code": PAYOUT_ERROR_STATUS_CODE, "attempts": 0,
                         "item": {"idempotency_key": entry["idempotency_key"],
                                  "error": {"code": "BAD_REQUEST_ERROR",
                                            "description": MUTEX_ACQUIRE_FAILED_ERROR_MESSAGE}}})
            continue
        try:
            bare = entry["idempotency_key"][len(IDEMPOTENT_KEY_PREFIX):]
            cache_set(cache_key, bare, PAYOUT_IDEMPOTENCY_TTL_HOURS * 3600)  # :154-158
            to_send.append(entry)
        finally:
            _release_mutex(mutex_key)
    return to_send


def process_batch(batch_id, force=False):
    rows = q("SELECT * FROM batches WHERE id=?", (batch_id,))
    if not rows:
        _log("process: unknown batch %s" % batch_id)
        return
    batch = dict(rows[0])
    spec = BATCH_TYPES.get(batch["batch_type_id"])
    if spec is None:
        _set_status(batch_id, "FAILED", error="unknown batch_type_id")
        return

    settings = json.loads(batch["settings"] or "{}")
    q("UPDATE batches SET attempts=attempts+1, updated_at=? WHERE id=?",
      (time.time(), batch_id), commit=True)

    # DataStaging step ("batchStatus": "STAGING") -- rows already persisted by
    # the create handler, so this is just the state transition.
    _set_status(batch_id, "STAGING")

    if force:
        pending = [dict(r) for r in q(
            "SELECT * FROM batch_entries WHERE batch_id=? ORDER BY seq_number", (batch_id,))]
    else:
        pending = [dict(r) for r in q(
            "SELECT * FROM batch_entries WHERE batch_id=? AND status!='PROCESSED' "
            "ORDER BY seq_number", (batch_id,))]

    _set_status(batch_id, "PROCESSING")

    passport = None
    if spec["kind"] == "create":
        passport = mint_passport(batch["entity_id"], batch["mode"] or "live")
        if passport is None:
            _log("no passport for batch %s -- the real payouts-api will reject this" % batch_id)

    size = min(int(spec["bulk_size"]), MAX_BULK_PAYOUTS_LIMIT)
    ok = True
    for start in range(0, len(pending), size):
        chunk = pending[start:start + size]
        if spec["kind"] == "create":
            if spec["idempotency"] == "cache_sha1":
                chunk = _assign_v2_keys(batch, chunk)
                if not chunk:
                    continue
            ok = _process_create_chunk(batch, chunk, settings, passport)
        else:
            for entry in chunk:
                ok = _process_approve_row(batch, entry, settings)
                if not ok:
                    break
        _recount(batch_id)
        if not ok:
            break

    _recount(batch_id)
    if not ok:
        # ErrorMessages.API_SERVER_DOWN -> JobFailureException (:430-431)
        _set_status(batch_id, "FAILED", error="API_SERVER_DOWN")
        _log("batch %s FAILED (failOnServerError)" % batch_id)
        return
    # OutputCreation step ("batchStatus": "OUTPUT"), then done.
    _set_status(batch_id, "OUTPUT")
    _set_status(batch_id, "COMPLETED")
    row = q("SELECT success_count, failure_count FROM batches WHERE id=?", (batch_id,))[0]
    _log("batch %s COMPLETED success=%d failure=%d"
         % (batch_id, row["success_count"], row["failure_count"]))


def worker_loop():
    while True:
        with WORK_CV:
            while not WORK_QUEUE:
                WORK_CV.wait()
            batch_id, force = WORK_QUEUE.pop(0)
        try:
            process_batch(batch_id, force=force)
        except Exception as exc:  # noqa: BLE001 - the worker must never die
            _log("worker error on %s: %r" % (batch_id, exc))
            try:
                _set_status(batch_id, "FAILED", error="batch_sim_internal: %r" % exc)
            except Exception:  # noqa: BLE001
                pass


def start_worker():
    if WORKER_STARTED[0]:
        return
    WORKER_STARTED[0] = True
    threading.Thread(target=worker_loop, name="batch-sim-worker", daemon=True).start()
    # Restart persistence: anything left mid-flight is re-queued. Idempotency
    # keys were persisted before the first PS call, so replaying a chunk cannot
    # create a second payout (core.go:352-390).
    stale = q("SELECT id FROM batches WHERE status IN "
              "('CREATED','SCHEDULED','STAGING','PROCESSING','OUTPUT','RESUMED')")
    for row in stale:
        _log("re-queueing in-flight batch %s after restart" % row["id"])
        enqueue(row["id"])


# --------------------------------------------------------------------------
# serialization
# --------------------------------------------------------------------------
RUNNING = ("STAGING", "VALIDATING", "PROCESSING", "OUTPUT", "RESUMED")


def _outcome(batch):
    """Derived, NON-SOURCE convenience field -- see CONTRACT.md section 7."""
    status = batch["status"]
    if status in ("CREATED", "SCHEDULED"):
        return "created"
    if status in RUNNING:
        return "processing"
    if status in ("FAILED", "VALIDATION_FAILED", "CANCELLED"):
        return "failed"
    if status == "COMPLETED":
        total = batch["total_count"] or 0
        failures = batch["failure_count"] or 0
        if failures == 0:
            return "processed"
        if failures >= total:
            return "failed"
        return "partially_processed"
    return status.lower()


def batch_json(batch):
    b = dict(batch)
    out = {
        "id": b["id"], "entity_id": b["entity_id"], "name": b["name"],
        "batch_type_id": b["batch_type_id"], "mode": b["mode"],
        "creator_id": b["creator_id"], "creator_type": b["creator_type"],
        "version": b["version"], "status": b["status"],
        "total_count": b["total_count"], "processed_count": b["processed_count"],
        "success_count": b["success_count"], "failure_count": b["failure_count"],
        "attempts": b["attempts"], "amount": b["amount"],
        "processed_amount": b["processed_amount"],
        "settings": json.loads(b["settings"] or "{}"),
        "schedule": b["schedule"], "created_at": b["created_at"],
        "updated_at": b["updated_at"], "outcome": _outcome(b),
    }
    if b.get("error"):
        out["error"] = b["error"]
    return out


def entry_json(entry):
    e = dict(entry)
    return {"id": e["id"], "batch_id": e["batch_id"], "seq_number": e["seq_number"],
            "row_data": e["row_data"], "response_data": e["response_data"],
            "status": e["status"], "idempotency_key": e["idempotency_key"],
            "created_at": e["created_at"], "updated_at": e["updated_at"]}


# --------------------------------------------------------------------------
# request parsing
# --------------------------------------------------------------------------
def parse_rows_from_text(text):
    """A file part is either a JSON array of row objects or a CSV whose first
    line is the header row (payout.json "linesToSkip": 1, "useFileHeaderName": true)."""
    stripped = text.strip()
    if stripped.startswith("[") or stripped.startswith("{"):
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            parsed = parsed.get("rows") or []
        return list(parsed)
    reader = csv.DictReader(io.StringIO(text))
    return [{k: v for k, v in row.items() if k is not None} for row in reader]


def parse_multipart(raw, content_type):
    """Minimal multipart/form-data reader (stdlib only; cgi is gone in 3.13)."""
    m = re.search(r'boundary="?([^";]+)"?', content_type)
    if not m:
        raise ValueError("multipart without boundary")
    boundary = ("--" + m.group(1)).encode()
    fields, files = {}, {}
    for part in raw.split(boundary):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        head, _, body = part.partition(b"\r\n\r\n")
        headers = head.decode("utf-8", "replace")
        name = re.search(r'name="([^"]*)"', headers)
        if not name:
            continue
        value = body.rstrip(b"\r\n")
        if "filename=" in headers:
            files[name.group(1)] = value.decode("utf-8", "replace")
        else:
            fields[name.group(1)] = value.decode("utf-8", "replace")
    return fields, files


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------
def err(code, description, status=400):
    return status, {"error": {"code": code, "description": description}}


class BatchSimHandler(BaseHTTPRequestHandler):
    server_version = "batch-sim/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        _log(fmt % args)

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        if not _INBOUND_AUTH:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            return base64.b64decode(header[6:]).decode("utf-8") == _INBOUND_AUTH
        except (ValueError, binascii.Error, UnicodeDecodeError):
            return False

    def _body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(length) if length else b""

    # ---- dispatch --------------------------------------------------------
    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = urllib.parse.parse_qs(query)
        if path in ("/health", "/ping", "/_arena/health"):
            return self._send_json(200, self._health())
        if not self._authorized():
            return self._send_json(*err("BAD_REQUEST_ERROR", "unauthorized", 401))
        try:
            status, payload = self._route_get(path, params)
        except Exception as exc:  # noqa: BLE001
            _log("GET %s failed: %r" % (path, exc))
            status, payload = err("SERVER_ERROR", "batch_sim_internal: %r" % exc, 500)
        self._send_json(status, payload)

    def do_POST(self):
        path, _, query = self.path.partition("?")
        params = urllib.parse.parse_qs(query)
        if not self._authorized():
            return self._send_json(*err("BAD_REQUEST_ERROR", "unauthorized", 401))
        raw = self._body()
        try:
            status, payload = self._route_post(path, params, raw)
        except Exception as exc:  # noqa: BLE001
            _log("POST %s failed: %r" % (path, exc))
            status, payload = err("SERVER_ERROR", "batch_sim_internal: %r" % exc, 500)
        self._send_json(status, payload)

    def do_PUT(self):
        self.do_POST()

    # ---- routes ----------------------------------------------------------
    @staticmethod
    def _canonical(path):
        """`/batch...` is the real service's prefix (BatchController.java:37);
        `/v1/batches...` is the arena-facing one. Normalise to the latter."""
        if path.startswith("/batch/") or path == "/batch":
            return "/v1/batches" + path[len("/batch"):]
        return path

    def _health(self):
        try:
            n = q("SELECT COUNT(*) c FROM batches")[0]["c"]
        except Exception:  # noqa: BLE001
            n = -1
        with WORK_CV:
            queued = len(WORK_QUEUE)
        return {"status": "ok", "service": SERVICE_NAME, "batches": n, "queued": queued,
                "worker_alive": WORKER_STARTED[0],
                "passport": "ready" if _PASSPORT_N is not None else "unavailable",
                "batch_types": sorted(BATCH_TYPES),
                # M7: where bulk creates go (monolith = shared ingress as the batch app; direct = PS)
                "upstream_mode": UPSTREAM_MODE, "ps_api_url": PS_API_URL, "ps_direct_url": PS_DIRECT_URL}

    def _route_get(self, path, params):
        path = self._canonical(path)
        if path == "/_arena/batches":
            where, args = [], []
            if params.get("entity_id"):
                where.append("entity_id=?")
                args.append(params["entity_id"][0])
            if params.get("status"):
                where.append("status=?")
                args.append(params["status"][0])
            sql = "SELECT * FROM batches"
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY created_at DESC LIMIT ?"
            args.append(int(params.get("limit", ["100"])[0]))
            rows = q(sql, tuple(args))
            return 200, {"count": len(rows), "batches": [batch_json(r) for r in rows]}
        if path == "/_arena/calls":
            return 200, {"count": len(CALL_LOG), "calls": list(CALL_LOG)}
        if path == "/_arena/faults":
            with _FAULT_LOCK:
                return 200, {"fault": dict(_FAULT) if _FAULT else None}

        m = re.match(r"^/_arena/batches/([A-Za-z0-9]+)$", path)
        if m:
            rows = q("SELECT * FROM batches WHERE id=?", (m.group(1),))
            if not rows:
                return err("BAD_REQUEST_ERROR", "batch not found", 404)
            entries = q("SELECT * FROM batch_entries WHERE batch_id=? ORDER BY seq_number",
                        (m.group(1),))
            payload = batch_json(rows[0])
            payload["entries"] = [entry_json(e) for e in entries]
            return 200, payload

        m = re.match(r"^/v1/batches/([A-Za-z0-9]+)/entries$", path)
        if m:
            args = [m.group(1)]
            sql = "SELECT * FROM batch_entries WHERE batch_id=?"
            if params.get("status"):
                sql += " AND status=?"
                args.append(params["status"][0])
            total = len(q(sql, tuple(args)))
            sql += " ORDER BY seq_number LIMIT ? OFFSET ?"
            args += [int(params.get("limit", ["1000"])[0]), int(params.get("offset", ["0"])[0])]
            rows = q(sql, tuple(args))
            return 200, {"count": len(rows), "total": total,
                         "entries": [entry_json(r) for r in rows]}

        m = re.match(r"^/v1/batches/([A-Za-z0-9]+)$", path)
        if m:
            rows = q("SELECT * FROM batches WHERE id=?", (m.group(1),))
            if not rows:
                return err("BAD_REQUEST_ERROR", "batch not found", 404)
            return 200, batch_json(rows[0])

        if path == "/v1/batches":
            args = []
            sql = "SELECT * FROM batches"
            if params.get("entity_id"):
                sql += " WHERE entity_id=?"
                args.append(params["entity_id"][0])
            sql += " ORDER BY created_at DESC LIMIT ?"
            args.append(int(params.get("limit", ["25"])[0]))
            rows = q(sql, tuple(args))
            return 200, {"count": len(rows), "items": [batch_json(r) for r in rows]}

        return err("BAD_REQUEST_ERROR", "no_stub_route GET %s" % path, 404)

    def _route_post(self, path, params, raw):
        path = self._canonical(path)

        if path == "/_arena/fail-next":
            return self._fail_next(raw)

        m = re.match(r"^/_arena/batches/([A-Za-z0-9]+)/reprocess$", path)
        if m:
            if not q("SELECT id FROM batches WHERE id=?", (m.group(1),)):
                return err("BAD_REQUEST_ERROR", "batch not found", 404)
            enqueue(m.group(1), force=True)
            return 200, {"id": m.group(1), "queued": True, "force": True}

        m = re.match(r"^/v1/batches/([A-Za-z0-9]+)/(process|trigger)$", path)
        if m:
            rows = q("SELECT * FROM batches WHERE id=?", (m.group(1),))
            if not rows:
                return err("BAD_REQUEST_ERROR", "batch not found", 404)
            enqueue(m.group(1))
            return 200, batch_json(rows[0])

        if path == "/v1/batches":
            return self._create(raw)

        return err("BAD_REQUEST_ERROR", "no_stub_route POST %s" % path, 404)

    def _fail_next(self, raw):
        global _FAULT
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return err("BAD_REQUEST_ERROR", "invalid json")
        if body.get("clear"):
            with _FAULT_LOCK:
                _FAULT = None
            return 200, {"cleared": True}
        scope = str(body.get("scope", "any"))
        if scope not in ("any", "create", "approve"):
            return err("BAD_REQUEST_ERROR", "scope must be any|create|approve")
        drop = bool(body.get("drop", False))
        status = body.get("status")
        if not drop:
            if status is None:
                status = 503
            if status not in (400, 401, 403, 409, 429, 500, 501, 502, 503, 504):
                return err("BAD_REQUEST_ERROR", "unsupported injected status")
        count = int(body.get("count", 1))
        if not 1 <= count <= 100:
            return err("BAD_REQUEST_ERROR", "count must be 1..100")
        with _FAULT_LOCK:
            _FAULT = {"count": count, "status": None if drop else int(status), "drop": drop,
                      "scope": scope, "reason": str(body.get("reason", "arena fault"))[:200]}
            return 200, {"fault": dict(_FAULT)}

    def _create(self, raw):
        ctype = self.headers.get("Content-Type", "") or ""
        fields, files = {}, {}
        rows = None
        if ctype.startswith("multipart/form-data"):
            # BatchController.java:48-59 create(@Valid BatchCreateDTO) -- multipart
            fields, files = parse_multipart(raw, ctype)
            for part_name in ("file", "multipartFile", "rows"):
                if part_name in files:
                    rows = parse_rows_from_text(files[part_name])
                    break
            if rows is None and fields.get("rows"):
                rows = parse_rows_from_text(fields["rows"])
            settings_raw = fields.get("settings")
        else:
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                return err("BAD_REQUEST_ERROR", "invalid json")
            if not isinstance(body, dict):
                return err("BAD_REQUEST_ERROR", "body must be an object")
            fields = {k: v for k, v in body.items() if k not in ("rows", "settings")}
            rows = body.get("rows")
            settings_raw = body.get("settings")

        # ErrorMessages.java:29 X_ENTITY_ID_NOT_PRESENT
        entity_id = self.headers.get("X-Entity-Id") or fields.get("entity_id") or ""
        if not entity_id:
            return err("X_ENTITY_ID_NOT_PRESENT", "X-Entity-Id is required")

        batch_type_id = fields.get("batch_type_id") or fields.get("batchTypeId") or ""
        if batch_type_id not in BATCH_TYPES:
            # @BatchTypeIdConstraint, BatchCreateDTO.java:40
            return err("BAD_REQUEST_ERROR",
                       "unsupported batch_type_id %r; batch-sim implements %s"
                       % (batch_type_id, ", ".join(sorted(BATCH_TYPES))))
        if not isinstance(rows, list) or not rows:
            return err("BAD_REQUEST_ERROR", "no rows supplied (JSON `rows` or a `file` part)")
        rows = [r for r in rows if isinstance(r, dict)]
        if not rows:
            return err("BAD_REQUEST_ERROR", "rows must be objects")

        if isinstance(settings_raw, str):
            try:
                settings = json.loads(settings_raw or "{}")
            except ValueError:
                return err("BAD_REQUEST_ERROR", "settings must be valid JSON")  # @ValidJson
        else:
            settings = settings_raw or {}
        if not isinstance(settings, dict):
            return err("BAD_REQUEST_ERROR", "settings must be a JSON object")

        batch_id = new_id()
        now = time.time()
        amount = sum(_row_amount_paise(r) for r in rows)
        q("INSERT INTO batches(id, entity_id, name, batch_type_id, mode, creator_id, "
          "creator_type, version, settings, status, total_count, amount, schedule, "
          "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (batch_id, entity_id, fields.get("name") or "", batch_type_id,
           fields.get("mode") or self.headers.get("mode") or "live",
           fields.get("creator_id") or self.headers.get("X-Creator-Id") or "",
           fields.get("creator_type") or self.headers.get("X-Creator-Type") or "user",
           str(fields.get("version") or "2.0"), json.dumps(settings), "CREATED",
           len(rows), amount,
           int(fields["schedule"]) if str(fields.get("schedule") or "").isdigit() else None,
           now, now), commit=True)

        # Entries (and therefore idempotency keys) are persisted BEFORE any PS
        # call, so a crash/retry replays the same keys.
        for seq, row in enumerate(rows, start=1):
            entry_id = new_id()
            q("INSERT INTO batch_entries(id, batch_id, seq_number, row_data, status, "
              "idempotency_key, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
              (entry_id, batch_id, seq, json.dumps(row), "CREATED",
               IDEMPOTENT_KEY_PREFIX + entry_id, now, now), commit=True)

        enqueue(batch_id)
        _log("created batch %s type=%s entity=%s rows=%d"
             % (batch_id, batch_type_id, entity_id, len(rows)))
        return 200, batch_json(q("SELECT * FROM batches WHERE id=?", (batch_id,))[0])


def serve():
    db()
    start_worker()
    httpd = ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), BatchSimHandler)
    _log("listening on 0.0.0.0:%d (ps=%s, types=%d, data=%s, passport=%s)"
         % (LISTEN_PORT, PS_API_URL, len(BATCH_TYPES), DATA_DIR,
            "ready" if _PASSPORT_N is not None else "unavailable"))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    serve()
