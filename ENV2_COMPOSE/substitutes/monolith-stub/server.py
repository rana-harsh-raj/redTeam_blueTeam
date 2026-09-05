#!/usr/bin/env python3
"""monolith-stub: legacy api-monolith substitute. See CONTRACT.md.

Endpoints/DTOs below are grounded in findings/20_api_monolith.md's route
table (confirmed via Route.php grep) plus payouts/pkg/api/*.go client DTOs
(read directly for exact JSON field names -- see each handler's docstring).
Seed data lives under seeds/monolith/*.json + seeds/pricing.json (shared
with pricing-stub), mounted read-only, stdlib-only JSON (no DB driver).
"""
import itertools
import json
import os
import sys
import time
import urllib.error
import urllib.request
import base64

sys.path.insert(0, "/app")
from _common.base_stub import serve, _log  # noqa: E402

MERCHANTS_FILE = os.environ.get("MONOLITH_MERCHANTS_FILE", "/app/seed/merchants.json")
FUND_ACCOUNTS_FILE = os.environ.get("MONOLITH_FUND_ACCOUNTS_FILE", "/app/seed/fund_accounts.json")
MISC_FILE = os.environ.get("MONOLITH_MISC_FILE", "/app/seed/misc.json")
PRICING_FILE = os.environ.get("MONOLITH_PRICING_FILE", "/app/seed/pricing.json")

# update_fts_fund_transfer relay target -- PATCH payouts-api/v1/payouts/update_payouts_with_fts
# with cred.API Basic-Auth, single attempt, per findings/13_fts_payouts_status_path.md
# finding #2/#3 (monolith's own Status.updatePayoutStatusViaFTS: one HTTP call,
# no retry, re-throws on failure -- the monolith is NOT responsible for retrying).
PS_RELAY_MODE = os.environ.get("PS_RELAY_MODE", "relay")  # relay|drop
PS_RELAY_URL = os.environ.get(
    "PS_RELAY_URL",
    "http://%s:%s/v1/payouts/update_payouts_with_fts" % (
        os.environ.get("PAYOUTS_API_HOST", "payouts-api"),
        os.environ.get("PAYOUTS_API_PORT", "9400"),
    ),
)
PS_RELAY_AUTH_USER = os.environ.get("PS_RELAY_AUTH_USER", "")
PS_RELAY_AUTH_PASS_FILE = os.environ.get("PS_RELAY_AUTH_PASS_FILE", "")

_counter = itertools.count(1)

MERCHANTS = {}
FUND_ACCOUNTS = {}
MISC = {}
PRICING = {}
FREE_PAYOUT_COUNTERS = {}

# In-memory sink log for dual_write / source_update / status_details_source_update
# / mail_and_sms -- inspectable at GET /_arena/log for tests/manual poking.
LOG = []


def _load_json(path, default):
    if not os.path.exists(path):
        _log("no seed file at %s, using default" % path)
        return default
    with open(path) as f:
        return json.load(f)


def _load_all():
    global MERCHANTS, FUND_ACCOUNTS, MISC, PRICING, FREE_PAYOUT_COUNTERS
    MERCHANTS = _load_json(MERCHANTS_FILE, {}).get("merchants", {})
    FUND_ACCOUNTS = _load_json(FUND_ACCOUNTS_FILE, {}).get("fund_accounts", {})
    MISC = _load_json(MISC_FILE, {})
    pricing_doc = _load_json(PRICING_FILE, {})
    PRICING = pricing_doc.get("plans", {})
    PRICING["_overrides"] = pricing_doc.get("special_case_overrides", {})
    FREE_PAYOUT_COUNTERS = pricing_doc.get("free_payout_counters", {})
    _log("loaded %d merchant(s), %d fund_account(s), %d pricing plan(s)" %
         (len(MERCHANTS), len(FUND_ACCOUNTS), len(PRICING) - 1))


_load_all()


def _log_sink(kind, payload):
    LOG.append({"kind": kind, "at": time.time(), "payload": payload})


# --- GET /internal/merchants/{id} ---
# route confirmed findings/20:47; response DTO payouts/pkg/api/merchant_config.go
def _get_merchant(handler, body):
    mid = handler.path.rsplit("/", 1)[-1]
    rec = MERCHANTS.get(mid)
    if rec is None:
        return 404, {"error": {"code": "BAD_REQUEST_ERROR", "description": "merchant not found"}}
    return 200, rec


# --- POST /payouts_service/fetch_pricing_info ---
# route confirmed findings/20:37; request/response payouts/pkg/api/fetch_pricing.go
def _fetch_pricing_info(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    mid = req.get("merchant_id", "")
    mode = req.get("mode", "")
    purpose = req.get("purpose", "")
    fee_type = req.get("fee_type", "")
    overrides = PRICING.get("_overrides", {})
    if purpose == "rzp_fees":
        fees, tax = overrides.get("purpose_rzp_fees", {}).get("fees", 0), overrides.get("purpose_rzp_fees", {}).get("tax", 0)
    elif purpose == "refund":
        fees, tax = overrides.get("purpose_refund", {}).get("fees", 0), overrides.get("purpose_refund", {}).get("tax", 0)
    elif fee_type == "free_payout":
        fees, tax = overrides.get("fee_type_free_payout", {}).get("fees", 0), overrides.get("fee_type_free_payout", {}).get("tax", 0)
    else:
        plan = PRICING.get(mid, {})
        rule = plan.get("rules", {}).get(mode, {"fees": 200, "tax": 36})
        fees, tax = rule.get("fees", 200), rule.get("tax", 36)
    return 200, {"fees": fees, "tax": tax, "pricing_rule_id": PRICING.get(mid, {}).get("plan_id", "ARENAPLAN000000"),
                 "error": "", "code": ""}


# --- POST /payouts_service/deduct_credits ---
def _deduct_credits(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    _log_sink("deduct_credits", req)
    return 200, {"fees": req.get("fees", 0), "tax": req.get("tax", 0), "credits_used": False, "error": "", "code": ""}


# --- POST /payouts_service/reverse_credits ---
def _reverse_credits(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    _log_sink("reverse_credits", req)
    return 200, {"success": True, "error": "", "code": ""}


# --- POST /payouts_service/source_update ---
def _source_update(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    _log_sink("source_update", req)
    return 200, {"sources_updated": True}


# --- POST /payouts_service/status_details_source_update ---
def _status_details_source_update(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    _log_sink("status_details_source_update", req)
    updated = [sd.get("source_id") for sd in req.get("source_details", []) if sd.get("source_id")]
    return 200, {"sources_updated": updated}


# --- POST /payouts_service/mail_and_sms ---
def _mail_and_sms(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    _log_sink("mail_and_sms", req)
    return 200, {}


# --- POST /payouts_service/dual_write ---
PS_DUAL_WRITE_MODE = os.environ.get("PS_DUAL_WRITE_MODE", "ok")  # ok | fail: "fail" reproduces the 2026-09-03 production
# incident where the monolith dual-write handler (ApprovedPayoutProcessor) errored (ARCHITECTURE_DELTA W4 / catalog C42)


def _dual_write(handler, body):
    if PS_DUAL_WRITE_MODE == "fail":
        return 500, {"error": {"code": "SERVER_ERROR", "description": "arena: dual-write failure mode (PS_DUAL_WRITE_MODE=fail)"}}
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    _log_sink("dual_write", req)
    return 200, {"status": "queued"}


# --- POST /payouts_service/decrement_free_payouts ---
def _decrement_free_payouts(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    mid = req.get("merchant_id", "")
    counter = FREE_PAYOUT_COUNTERS.setdefault(
        mid, {"balance_id": req.get("balance_id", ""), "free_payouts_consumed": 0,
              "free_payouts_consumed_last_reset_at": int(time.time())})
    counter["free_payouts_consumed"] = counter.get("free_payouts_consumed", 0) + 1
    _log_sink("decrement_free_payouts", req)
    return 200, {"balance_id": counter.get("balance_id", req.get("balance_id", "")),
                 "free_payouts_consumed": counter["free_payouts_consumed"],
                 "free_payouts_consumed_last_reset_at": counter.get("free_payouts_consumed_last_reset_at", 0)}


# --- GET /fund_accounts_internal/fa_{id} ---
def _get_fund_account(handler, body):
    tail = handler.path.rsplit("/", 1)[-1]
    fa_id = tail[3:] if tail.startswith("fa_") else tail
    rec = FUND_ACCOUNTS.get(fa_id)
    if rec is None:
        return 404, {"error": {"code": "BAD_REQUEST_ERROR", "description": "fund account not found"}}
    return 200, rec


# --- POST /merchant/on_hold_slas_internal ---
def _on_hold_slas(handler, body):
    return 200, {"merchant_slas": MISC.get("on_hold_slas", {}).get("merchant_slas", {})}


# --- GET /actor_info_internal/{user_id} --- (best-effort, see seeds/monolith/misc.json)
def _actor_info(handler, body):
    uid = handler.path.rsplit("/", 1)[-1]
    rec = MISC.get("actor_info", {}).get(uid)
    if rec is None:
        return 404, {"error": {"code": "BAD_REQUEST_ERROR", "description": "actor not found"}}
    return 200, rec


# --- POST /users_internal --- (best-effort, see seeds/monolith/misc.json)
def _users_internal(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    users = MISC.get("users_internal", {})
    result = {uid: users[uid] for uid in req.get("user_ids", []) if uid in users}
    return 200, result



# --- POST /payouts_service/create_fta/{payout_id} --- the monolith's FTA creation on behalf of Payouts Service
# (api/app/Models/Payout/Processor/Base.php createFTAForPayoutService): read the PS payout, create the fund
# transfer in FTS (POST /v1/transfer, monolith identity), return {"status": <payout status>, "error": null}.
# PS then moves the payout to `initiated` itself (payouts processor/payoutFTS.go CreateFTS) and learns the
# terminal state from the FTS status webhook relayed below. Arena shortcut: PS' GET /v1/payouts/{id} needs
# a merchant passport, and the payout id does not reveal the merchant, so we mint (kong-lite /_arena/mint)
# for each seeded merchant until one answers 200 -- the real monolith reads its own DB instead.
FTS_URL = os.environ.get("FTS_URL", "http://fts-web:8080")
KONG_MINT_URL = os.environ.get("KONG_MINT_URL", "http://kong-lite:8080/_arena/mint")
FTS_AUTH_USER = os.environ.get("FTS_AUTH_USER", "api_monolith")   # fts [users.api] (auth.go: app = prefix before "_")
MONOLITH_SECRET_FILE = os.environ.get("STUB_BASIC_AUTH_FILE", "/run/secrets/monolith_basic_auth")
FTS_FUND_ACCOUNT_BY_ACCOUNT_TYPE = {"shared": 900001, "direct": 900002}   # seeds/s4 fts.sql routing rows


def _monolith_secret():
    try:
        with open(MONOLITH_SECRET_FILE) as f:
            return f.read().strip().split(":", 1)[1]
    except Exception:
        return ""


def _http(method, url, body=None, headers=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except Exception:
            return exc.code, {}


def _ps_get_payout(payout_id):
    ps_auth = None
    if PS_RELAY_AUTH_USER and PS_RELAY_AUTH_PASS_FILE and os.path.exists(PS_RELAY_AUTH_PASS_FILE):
        with open(PS_RELAY_AUTH_PASS_FILE) as f:
            ps_auth = base64.b64encode(("%s:%s" % (PS_RELAY_AUTH_USER, f.read().strip())).encode()).decode()
    base = PS_RELAY_URL.split("/v1/")[0]
    for merchant_id in sorted(MERCHANTS):
        st, tok = _http("POST", KONG_MINT_URL, {"consumer": {"id": merchant_id, "type": "merchant"}, "mode": "live", "roles": []})
        if st != 200 or not tok.get("token"):
            continue
        headers = {"X-Passport-JWT-V1": tok["token"]}
        if ps_auth:
            headers["Authorization"] = "Basic %s" % ps_auth
        # PS addresses payouts by their signed public id (pout_<14 chars>)
        st, payout = _http("GET", "%s/v1/payouts/pout_%s" % (base, payout_id), headers=headers)
        if st == 200 and payout.get("id"):
            return payout
    return None


def _create_fta(handler, body):
    payout_id = handler.path.rstrip("/").split("/")[-1]
    bare_id = payout_id.split("_", 1)[1] if "_" in payout_id else payout_id
    payout = _ps_get_payout(bare_id)
    if payout is None:
        return 200, {"status": None, "error": "payout not found in payouts service"}
    account_type = "direct" if str(payout.get("account_type") or payout.get("balance_account_type") or "").lower() == "direct" else "shared"
    merchant_id = payout.get("merchant_id")
    if merchant_id in MERCHANTS and str(MERCHANTS[merchant_id].get("account_type", "")).lower() == "direct":
        account_type = "direct"
    fts_body = {
        "product": "PAYOUT",
        "merchant_id": merchant_id,
        "transfer": {
            "amount": int(payout.get("amount") or 0),
            "source_id": bare_id,
            "source_type": "payout",
            "preferred_mode": payout.get("mode") or "IMPS",
        },
        "account": {"fund_account_id": FTS_FUND_ACCOUNT_BY_ACCOUNT_TYPE[account_type]},
    }
    if account_type == "direct":
        # fts marks a transfer DIRECT only when transfer.preferred_source_account_id is present
        # (fts internal/transfer/service.go:549); the monolith sends it for current-account merchants.
        fts_body["transfer"]["preferred_source_account_id"] = FTS_FUND_ACCOUNT_BY_ACCOUNT_TYPE["direct"]
    auth = base64.b64encode(("%s:%s" % (FTS_AUTH_USER, _monolith_secret())).encode()).decode()
    st, fts = _http("POST", FTS_URL + "/v1/transfer", fts_body, {"Authorization": "Basic %s" % auth}, timeout=15)
    _log_sink("create_fta", {"payout_id": bare_id, "fts_status_code": st, "fts_response": fts})
    if st not in (200, 201):
        return 200, {"status": payout.get("status"), "error": "fts transfer create failed: %s" % json.dumps(fts)[:200]}
    return 200, {"status": payout.get("status"), "error": None, "fund_transfer_id": fts.get("fund_transfer_id")}


# --- GET /internal_balances_queued --- payouts' low-balance dequeue cron asks the monolith for VA balances
# (payouts/pkg/api/fetch_balances.go: GET with body {"balance_ids": [...]} -> {"balances": {id: paise}}). In
# production the monolith's `balance` table is kept in step with the ledger by its journal-created consumer; the
# arena has no such consumer, so this stub reads the merchant balance straight from ledger-api
# (AccountAPI/FetchByEntitiesAndMerchantID, MerchantBalance account: payable/merchant_va) for shared merchants and
# returns a large synthetic balance for direct (x-balances-backed) merchants.
LEDGER_URL = os.environ.get("LEDGER_URL", "http://ledger-api:8080")
LEDGER_AUTH_PASS_FILE = os.environ.get("LEDGER_AUTH_PASS_FILE", "/run/secrets/auth_payouts_ledger")
LEDGER_AUTH_USER = os.environ.get("LEDGER_AUTH_USER", "payouts_key")
DIRECT_SYNTHETIC_BALANCE = int(os.environ.get("DIRECT_SYNTHETIC_BALANCE", "100000000"))


def _ledger_merchant_balance(merchant_id):
    """MerchantBalance account balance from ledger-api (AccountAPI/FetchByMerchantID; the entity-filtered RPC needs
    a shape this stub does not reproduce). Picks the account whose entities say payable + merchant_va."""
    try:
        with open(LEDGER_AUTH_PASS_FILE) as f:
            pw = f.read().strip()
    except OSError:
        return None
    auth = base64.b64encode(("%s:%s" % (LEDGER_AUTH_USER, pw)).encode()).decode()
    st, resp = _http("POST", LEDGER_URL + "/twirp/rzp.ledger.account.v1.AccountAPI/FetchByMerchantID",
                     {"merchant_id": merchant_id}, {"Authorization": "Basic %s" % auth, "Ledger-Tenant": "X"})
    if st != 200:
        return None
    for acc in resp.get("accounts") or []:
        ent = acc.get("entities") or {}
        if "payable" in (ent.get("account_type") or []) and "merchant_va" in (ent.get("fund_account_type") or []):
            try:
                return int(float(acc.get("balance") or 0))
            except (TypeError, ValueError):
                return None
    return None


def _internal_balances_queued(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        req = {}
    out = {}
    for balance_id in req.get("balance_ids") or []:
        merchant = next((m for m, v in MERCHANTS.items() if v.get("balance_id") == balance_id), None)
        info = MERCHANTS.get(merchant or "", {})
        if str(info.get("account_type", "shared")).lower() == "direct":
            out[balance_id] = DIRECT_SYNTHETIC_BALANCE
        else:
            bal = _ledger_merchant_balance(merchant) if merchant else None
            out[balance_id] = bal if bal is not None else 0
    _log_sink("internal_balances_queued", {"request": req, "balances": out})
    return 200, {"balances": out}

# --- POST /update_fts_fund_transfer --- relay to payouts-api, single attempt,
# no retry (findings/20 §4), controlled by PS_RELAY_MODE=relay|drop.
def _update_fts_fund_transfer(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    _log_sink("update_fts_fund_transfer_inbound", req)

    if PS_RELAY_MODE == "drop":
        _log("PS_RELAY_MODE=drop: swallowing update_fts_fund_transfer, no PATCH sent")
        return 200, {"relayed": False, "mode": "drop"}

    headers = {"Content-Type": "application/json"}
    if PS_RELAY_AUTH_USER and PS_RELAY_AUTH_PASS_FILE and os.path.exists(PS_RELAY_AUTH_PASS_FILE):
        with open(PS_RELAY_AUTH_PASS_FILE) as f:
            pw = f.read().strip()
        token = base64.b64encode(("%s:%s" % (PS_RELAY_AUTH_USER, pw)).encode()).decode()
        headers["Authorization"] = "Basic %s" % token

    src = str(req.get("source_id") or "")
    bare_src = src.split("_", 1)[1] if "_" in src else src
    # api/app/Models/Payout/Status.php $ftaToPayoutStatusMap: DEFAULT/SHARED map FTA FAILED -> payout REVERSED
    # (the merchant was debited and is credited back); DIRECT (RBL/ICICI/AXIS) maps FAILED -> FAILED.
    fts_status = str(req.get("status") or "").upper()
    payout_status = fts_status.lower()
    if fts_status == "FAILED":
        payout = _ps_get_payout(bare_src) or {}
        m = MERCHANTS.get(payout.get("merchant_id") or "", {})
        payout_status = "failed" if str(m.get("account_type", "shared")).lower() == "direct" else "reversed"
    ps_body = {
        "source_id": bare_src,
        "status": payout_status,
        "failure_reason": req.get("failure_reason"),
        "bank_status_code": req.get("bank_status_code"),
        "fts_fund_account_id": str(req.get("fund_account_id") or ""),
        # ledger X config for payout_processed credits the FTS payable account discovered by
        # {fts_fund_account_id: $fts_fund_account_id, fund_account_type: $fts_account_type}; the arena seeds both
        # FTS source accounts (pool 900001, direct 900002) as fund_account_type "nodal" (seeds/s4/ledger.sql).
        "fts_account_type": os.environ.get("ARENA_FTS_ACCOUNT_TYPE", "nodal"),
        "fts_status": str(req.get("status") or ""),
    }
    # The monolith first syncs the FTA details into PS (payouts internal/app/dtos/payoutUpdate.go DetailsUpdateRequest:
    # fund_transfer_id is required; utr/mode/channel/bank_status_code/failure_reason/remarks/return_utr/gateway_ref_no
    # optional) via PATCH /v1/payouts/update_payouts_details_with_fts, then the status update. Same headers/identity.
    details = {"source_id": bare_src}
    try:
        details["fund_transfer_id"] = int(req.get("fund_transfer_id") or req.get("id") or 0)
    except (TypeError, ValueError):
        details["fund_transfer_id"] = 0
    for k in ("utr", "mode", "channel", "bank_status_code", "failure_reason", "remarks", "return_utr", "gateway_ref_no", "narration"):
        if req.get(k) not in (None, ""):
            details[k] = str(req.get(k))
    details["fta_status"] = fts_status
    if details["fund_transfer_id"]:
        try:
            # payouts payout_internal_routes.go: /update_payouts_details_with_fts is POST (the status route is PATCH)
            d_req = urllib.request.Request(PS_RELAY_URL.replace("update_payouts_with_fts", "update_payouts_details_with_fts"),
                                           data=json.dumps(details).encode(), headers=headers, method="POST")
            with urllib.request.urlopen(d_req, timeout=10) as d_resp:
                _log_sink("update_payouts_details_with_fts", {"status": d_resp.status, "body": details})
        except urllib.error.HTTPError as exc:
            _log_sink("update_payouts_details_with_fts", {"status": exc.code, "body": details, "error": exc.read().decode(errors="replace")[:300]})
        except Exception as exc:  # noqa: BLE001
            _log_sink("update_payouts_details_with_fts", {"error": repr(exc)})
    relayed, status = False, None
    try:
        http_req = urllib.request.Request(PS_RELAY_URL, data=json.dumps(ps_body).encode(),
                                           headers=headers, method="PATCH")
        with urllib.request.urlopen(http_req, timeout=10) as resp:
            status = resp.status
            relayed = True
    except urllib.error.HTTPError as exc:
        status = exc.code
        relayed = True  # request WAS sent, just got a non-2xx -- matches "commit nothing, single-attempt PATCH"
    except Exception as exc:  # noqa: BLE001 -- single-attempt, no retry, matches real monolith behaviour
        _log("update_fts_fund_transfer relay failed: %r" % exc)

    return 200, {"relayed": relayed, "mode": "relay", "relay_status_code": status}


def _arena_log(handler, body):
    return 200, {"log": LOG}


ROUTES = {
    ("GET", "/internal/merchants/"): _get_merchant,
    ("POST", "/payouts_service/fetch_pricing_info"): _fetch_pricing_info,
    ("POST", "/payouts_service/deduct_credits"): _deduct_credits,
    ("POST", "/payouts_service/reverse_credits"): _reverse_credits,
    ("POST", "/payouts_service/source_update"): _source_update,
    ("POST", "/payouts_service/status_details_source_update"): _status_details_source_update,
    ("POST", "/payouts_service/mail_and_sms"): _mail_and_sms,
    ("POST", "/payouts_service/dual_write"): _dual_write,
    ("POST", "/payouts_service/decrement_free_payouts"): _decrement_free_payouts,
    ("GET", "/fund_accounts_internal/"): _get_fund_account,
    ("POST", "/merchant/on_hold_slas_internal"): _on_hold_slas,
    ("GET", "/actor_info_internal/"): _actor_info,
    ("POST", "/users_internal"): _users_internal,
    ("POST", "/update_fts_fund_transfer"): _update_fts_fund_transfer,
    ("POST", "/payouts_service/create_fta/"): _create_fta,
    ("GET", "/internal_balances_queued"): _internal_balances_queued,
    ("GET", "/_arena/log"): _arena_log,
}

if __name__ == "__main__":
    serve(ROUTES)
