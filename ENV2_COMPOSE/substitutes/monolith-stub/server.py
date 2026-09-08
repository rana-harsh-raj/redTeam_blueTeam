#!/usr/bin/env python3
"""monolith-stub: legacy api-monolith substitute. See CONTRACT.md.

Endpoints/DTOs below are grounded in findings/20_api_monolith.md's route
table (confirmed via Route.php grep) plus payouts/pkg/api/*.go client DTOs
(read directly for exact JSON field names -- see each handler's docstring).
Seed data lives under seeds/monolith/*.json + seeds/pricing.json (shared
with pricing-stub), mounted read-only. A scoped MySQL connection reads the Payouts row directly;
the separate API balance connection can update only balance and updated_at.
"""
import decimal
import re
import itertools
import json
import os
import sys
import time
import threading
import urllib.error
import urllib.request
import base64
import uuid

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
CREDIT_BALANCES = {}  # ASSUMED optional synthetic reward-credit balances, keyed by balance_id
CREDIT_DEDUCTIONS = {}  # (payout_id, source_type) -> original request and consumed amount
CREDIT_REVERSALS = set()
STATE_LOCK = threading.RLock()
FTA_LOCKS = {}
FTA_RESULTS = {}
FTA_RETRY_PENDING = set()
FTS_CREATE_MODE = os.environ.get("FTS_CREATE_MODE", "swallow")
FTS_RETRY_DELAY_SECONDS = float(os.environ.get("FTS_RETRY_DELAY_SECONDS", "1"))  # ASSUMED local retry cadence
FTS_RETRY_ATTEMPTS = int(os.environ.get("FTS_RETRY_ATTEMPTS", "3"))  # ASSUMED bounded substitute retry count


# In-memory sink log for dual_write / source_update / status_details_source_update
# / mail_and_sms -- inspectable at GET /_arena/log for tests/manual poking.
LOG = []
RELAY_CONTROLS = {}
RELAY_PENDING = {}
RELAY_LOCK = threading.Lock()


def _load_json(path, default):
    if not os.path.exists(path):
        _log("no seed file at %s, using default" % path)
        return default
    with open(path) as f:
        return json.load(f)


def _load_all():
    global MERCHANTS, FUND_ACCOUNTS, MISC, PRICING, FREE_PAYOUT_COUNTERS, CREDIT_BALANCES
    MERCHANTS = _load_json(MERCHANTS_FILE, {}).get("merchants", {})
    FUND_ACCOUNTS = _load_json(FUND_ACCOUNTS_FILE, {}).get("fund_accounts", {})
    MISC = _load_json(MISC_FILE, {})
    pricing_doc = _load_json(PRICING_FILE, {})
    PRICING = pricing_doc.get("plans", {})
    PRICING["_overrides"] = pricing_doc.get("special_case_overrides", {})
    FREE_PAYOUT_COUNTERS = {v["balance_id"]: dict(v) for v in pricing_doc.get("free_payout_counters", {}).values()
                            if isinstance(v, dict) and "balance_id" in v}
    CREDIT_BALANCES = dict(pricing_doc.get("reward_credit_balances", {}))
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
    # M6 fix: apply runtime feature overrides (POST /_arena/merchant_features) to the merchant
    # record PS actually parses (merchant.feature list), so IsFeatureEnabled(payouts_on_hold, ...)
    # can observe them. Before, overrides only reached the DA-ledger emitter (_merchant_features).
    merchant = dict(rec.get("merchant", {}))
    merchant["feature"] = sorted(f for f, on in _merchant_features(mid).items() if on)
    return 200, {"merchant": merchant, "merchant_detail": rec.get("merchant_detail", {})}


# --- POST /payouts_service/fetch_pricing_info ---
# route confirmed findings/20:37; request/response payouts/pkg/api/fetch_pricing.go
def _fetch_pricing_info(handler, body):
    try:
        req = json.loads(body or b"{}")
        required = ("payout_id", "merchant_id", "balance_id", "amount", "method", "mode", "channel")
        if any(k not in req for k in required) or type(req["amount"]) is not int or req["amount"] < 1:
            return 400, {"error": {"code":"BAD_REQUEST_ERROR", "description":"missing or invalid pricing fields"}}
    except (json.JSONDecodeError,TypeError):
        return 400, {"error":{"code":"BAD_REQUEST_ERROR","description":"invalid JSON"}}
    mid, amount = req["merchant_id"], req["amount"]
    plan_id = MERCHANTS.get(mid,{}).get("merchant",{}).get("pricing_plan_id")
    plan = PRICING.get(mid) or PRICING.get(plan_id) or {}
    if req.get("fee_type") == "free_payout":
        return 200, {"fees":0,"tax":0,"pricing_rule_id":plan.get("plan_id",plan_id or ""),"error":"","code":""}
    if req.get("purpose") in ("rzp_fees", "refund"):
        override = PRICING.get("_overrides",{}).get("purpose_"+req["purpose"],{})
        return 200, {"fees":override.get("fees",0),"tax":override.get("tax",0),"pricing_rule_id":plan.get("plan_id",plan_id or ""),"error":"","code":""}
    rules = plan.get("rules",{})
    # Existing mode dictionaries are explicit wildcard rules for channel/method/slab.
    # New fixtures can supply a list with all dimensions. No unseeded price is invented.
    candidates = ([dict(rule,mode=mode) for mode,rule in rules.items() if isinstance(rule,dict)]
                  if isinstance(rules,dict) else rules)
    matched = [r for r in candidates if all(r.get(k,"*") in ("*",req.get(k,"")) for k in ("method","mode","channel","fee_type"))
               and r.get("min_amount",0) <= amount <= r.get("max_amount",2**63-1)]
    if len(matched)!=1:
        return 200, {"fees":0,"tax":0,"pricing_rule_id":"","error":"no unique seeded pricing rule","code":"BAD_REQUEST_PRICING_RULE_NOT_FOUND"}
    rule=matched[0]
    response={"fees":int(rule["fees"]),"tax":int(rule["tax"]),"pricing_rule_id":rule.get("id",plan.get("plan_id",plan_id or "")),"error":"","code":""}
    _log_sink("pricing_rule_selected",{"request":req,"response":response})
    return 200,response


def _deduct_credits(handler, body):
    try:
        req=json.loads(body or b"{}")
        if any(k not in req for k in ("payout_id","fees","tax","status","merchant_id","balance_id")):
            raise ValueError("required fields missing")
        fees,tax=int(req["fees"]),int(req["tax"])
        if fees<tax or tax<0: raise ValueError("invalid amounts")
    except (ValueError,TypeError):
        return 400,{"error":{"code":"BAD_REQUEST_ERROR","description":"invalid credits request"}}
    key=(req["payout_id"],"payout")
    with STATE_LOCK:
        if key in CREDIT_DEDUCTIONS:
            # api Processor/Base.php:4909-4914: duplicate uses request fees minus tax.
            return 200,{"fees":fees-tax,"tax":0,"credits_used":True}
        available=int(CREDIT_BALANCES.get(req["balance_id"],0))
        credit_amount=fees-tax
        if credit_amount>0 and available>=credit_amount:
            CREDIT_BALANCES[req["balance_id"]]=available-credit_amount
            CREDIT_DEDUCTIONS[key]={"balance_id":req["balance_id"],"amount":credit_amount}
            response={"fees":credit_amount,"tax":0,"credits_used":True}
        else:
            response={"fees":fees,"tax":tax,"credits_used":False}
    _log_sink("deduct_credits",{"request":req,"response":response})
    return 200,response


def _reverse_credits(handler, body):
    try:
        req=json.loads(body or b"{}")
        if any(k not in req for k in ("entity_type","payout_id","merchant_id","balance_id","fee_type")):
            raise ValueError("missing fields")
        if req["entity_type"] not in ("payout","reversal") or (req["entity_type"]=="reversal" and not req.get("reversal_id")):
            raise ValueError("invalid source")
    except (ValueError,TypeError):
        return 400,{"error":{"code":"BAD_REQUEST_ERROR","description":"invalid credit reversal"}}
    key=(req["payout_id"],"payout")
    with STATE_LOCK:
        deduction=CREDIT_DEDUCTIONS.get(key)
        if deduction and key not in CREDIT_REVERSALS:
            balance_id=deduction["balance_id"]
            CREDIT_BALANCES[balance_id]=int(CREDIT_BALANCES.get(balance_id,0))+deduction["amount"]
            CREDIT_REVERSALS.add(key)
    _log_sink("reverse_credits",req)
    return 200,{"success":True}


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
    updated = list(dict.fromkeys(sd.get("source_type") for sd in req.get("source_details", []) if sd.get("source_type")))
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
    return 200, {"status": "success"}


# --- POST /payouts_service/decrement_free_payouts ---
def _decrement_free_payouts(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    balance_id=req.get("balance_id","")
    if not balance_id or not req.get("merchant_id") or not req.get("payout_id"):
        return 400,{"error":{"code":"BAD_REQUEST_ERROR","description":"missing free-payout fields"}}
    try:
        rows=_balance_rows([balance_id])
    except (ValueError,TypeError):
        return 400,{"error":{"code":"BAD_REQUEST_ERROR","description":"invalid balance id"}}
    if len(rows)!=1: return 404,{"error":{"code":"BAD_REQUEST_ERROR","description":"balance not found"}}
    if rows[0].get("type")!="banking": return 200,None
    with STATE_LOCK:
        counter=FREE_PAYOUT_COUNTERS.setdefault(balance_id,{"balance_id":balance_id,"free_payouts_consumed":0,
                    "free_payouts_consumed_last_reset_at":int(time.time())})
        counter["free_payouts_consumed"]=max(0,counter["free_payouts_consumed"]-1)
        response=dict(counter)
    _log_sink("decrement_free_payouts",req)
    return 200,response


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
# PS moves the payout to `initiated` before this call (processor/payoutFTS.go CreateFTS) and learns the
# terminal state from the FTS status webhook relayed below. The real monolith
# reads Payouts DB by primary key (api Payout/Repository.php:getPayoutServicePayout).
FTS_URL = os.environ.get("FTS_URL", "http://fts-web:8080")
FTS_AUTH_USER = os.environ.get("FTS_AUTH_USER", "api_monolith")   # fts [users.api] (auth.go: app = prefix before "_")
MONOLITH_SECRET_FILE = os.environ.get("STUB_BASIC_AUTH_FILE", "/run/secrets/monolith_basic_auth")


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


def _db(kind):
    # Driver installed only in this substitute image. No root credentials mounted.
    import pymysql
    if kind == "payouts":
        host, name, user = "mysql-payouts", "payouts", "monolith_reader"
    else:
        host, name, user = "mysql-apidb-stub", "api_local", "monolith_balance"
    secret = "/run/secrets/auth_monolith_%s_db" % ("payouts" if kind == "payouts" else "balance")
    with open(secret) as f:
        password = f.read().strip()
    return pymysql.connect(host=host, database=name, user=user, password=password,
                           cursorclass=pymysql.cursors.DictCursor, autocommit=True,
                           connect_timeout=5, read_timeout=5, write_timeout=5)


def _ps_get_payout(payout_id):
    if not re.fullmatch(r"[A-Za-z0-9]{14}", payout_id):
        return None
    with _db("payouts") as db, db.cursor() as cursor:
        cursor.execute("SELECT * FROM payouts WHERE id = %s LIMIT 1", (payout_id,))
        payout = cursor.fetchone()
    if payout:
        _log_sink("payout_merchant_resolved", {"payout_id": payout_id,
                   "merchant_id": payout["merchant_id"], "origin": "payouts.payouts"})
    return payout


def _create_fta(handler, body):
    payout_id=handler.path.rstrip("/").split("/")[-1].removeprefix("pout_")
    with STATE_LOCK: lock=FTA_LOCKS.setdefault(payout_id,threading.Lock())
    with lock:
        return _create_fta_locked(handler,body)


def _create_fta_locked(handler, body):
    payout_id = handler.path.rstrip("/").split("/")[-1]
    bare_id = payout_id.split("_", 1)[1] if "_" in payout_id else payout_id
    try:
        payout = _ps_get_payout(bare_id)
    except Exception as exc:
        _log_sink("payout_lookup_failed", {"payout_id": bare_id, "error_type": type(exc).__name__})
        return 503, {"error": "payout_database_unavailable"}
    if payout is None:
        return 200, {"status": None, "error": "payout not found in payouts service"}
    account_type = "direct" if str(payout.get("account_type") or payout.get("balance_account_type") or "").lower() == "direct" else "shared"
    merchant_id = payout.get("merchant_id")
    if merchant_id in MERCHANTS and str(MERCHANTS[merchant_id].get("account_type", "")).lower() == "direct":
        account_type = "direct"
    if payout.get("status") not in ("created", "initiated"):
        return 200, {"status": payout.get("status"), "error": None}
    if payout.get("fts_transfer_id"):
        return 200,{"status":payout.get("status"),"error":None,"fund_transfer_id":payout["fts_transfer_id"]}
    if account_type != "direct" and not payout.get("transaction_id"):
        return 200, {"status": payout.get("status"), "error": "Ledger entry not found in payout."}
    fixture = MERCHANTS.get(merchant_id, {})
    try:
        fts_fund_account_id = int(fixture["fts_fund_account_id"])
        fts_source_account_id = int(fixture["fts_source_account_id"])
        if min(fts_fund_account_id, fts_source_account_id) <= 0:
            raise ValueError("invalid fixture")
    except (KeyError, TypeError, ValueError):
        return 503, {"error": "missing_fts_fixture_mapping", "merchant_id": merchant_id}
    fts_body = {
        "product": "PAYOUT",
        "merchant_id": merchant_id,
        "transfer": {
            "amount": int(payout.get("amount") or 0),
            "source_id": bare_id,
            "source_type": "payout",
            "preferred_mode": payout.get("mode") or "IMPS",
        },
        "account": {"fund_account_id": fts_fund_account_id},
    }
    if account_type == "direct":
        # fts marks a transfer DIRECT only when transfer.preferred_source_account_id is present
        # (fts internal/transfer/service.go:549); the monolith sends it for current-account merchants.
        fts_body["transfer"]["preferred_source_account_id"] = fts_source_account_id
    if bare_id in FTA_RESULTS:
        return 200,{"status":payout.get("status"),"error":None,"fund_transfer_id":FTA_RESULTS[bare_id]}
    success, fts = _send_fts_create(bare_id,fts_body)
    if not success:
        if FTS_CREATE_MODE=="error":
            return 200,{"status":None,"error":"fts transfer create failed"}
        with STATE_LOCK:
            if bare_id not in FTA_RETRY_PENDING:
                FTA_RETRY_PENDING.add(bare_id)
                timer=threading.Timer(FTS_RETRY_DELAY_SECONDS,_retry_fts_create,args=(bare_id,fts_body,1))
                timer.daemon=True
                timer.start()
        return 200,{"status":payout.get("status"),"error":None}
    return 200,{"status":payout.get("status"),"error":None,"fund_transfer_id":fts.get("fund_transfer_id")}


def _send_fts_create(payout_id, fts_body):
    auth=base64.b64encode((FTS_AUTH_USER+":"+_monolith_secret()).encode()).decode()
    try:
        status,response=_http("POST",FTS_URL+"/v1/transfer",fts_body,{"Authorization":"Basic "+auth},timeout=1)
    except Exception as exc:
        status,response=0,{"error_type":type(exc).__name__}
    _log_sink("create_fta",{"payout_id":payout_id,"fts_status_code":status,"fts_response":response})
    if status in (200,201):
        if response.get("fund_transfer_id"):
            with STATE_LOCK: FTA_RESULTS[payout_id]=response["fund_transfer_id"]
        return True,response
    return False,response


def _retry_fts_create(payout_id,fts_body,attempt):
    success,_=_send_fts_create(payout_id,fts_body)
    _log_sink("create_fta_async_retry",{"payout_id":payout_id,"attempt":attempt,"success":success})
    if not success and attempt<FTS_RETRY_ATTEMPTS:
        timer=threading.Timer(FTS_RETRY_DELAY_SECONDS,_retry_fts_create,args=(payout_id,fts_body,attempt+1))
        timer.daemon=True
        timer.start()
    else:
        with STATE_LOCK: FTA_RETRY_PENDING.discard(payout_id)


# --- API balance mirror. Shared truth is Ledger; direct truth is x-balances.
# Reading this endpoint does not invent funds or silently refresh updated_at.
LEDGER_URL = os.environ.get("LEDGER_URL", "http://ledger-api:8080")
LEDGER_AUTH_PASS_FILE = os.environ.get("LEDGER_AUTH_PASS_FILE", "/run/secrets/auth_payouts_ledger")
LEDGER_AUTH_USER = os.environ.get("LEDGER_AUTH_USER", "payouts_key")


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
    candidates = []
    for acc in resp.get("accounts") or []:
        ent = acc.get("entities") or {}
        if "payable" in (ent.get("account_type") or []) and "merchant_va" in (ent.get("fund_account_type") or []):
            candidates.append(acc)
    if len(candidates) != 1:
        # A merchant with multiple balances needs explicit banking-account mapping.
        # Never pick whichever account happened to be returned first.
        return None
    try:
        amount = decimal.Decimal(str(candidates[0]["balance"]))
        if not amount.is_finite() or amount != amount.to_integral_value():
            return None
        return int(amount)
    except (KeyError, TypeError, ValueError, decimal.InvalidOperation):
        return None


def _balance_rows(balance_ids):
    if not isinstance(balance_ids, list) or not balance_ids or any(
            not isinstance(b, str) or not re.fullmatch(r"[A-Za-z0-9]{14}", b) for b in balance_ids):
        raise ValueError("balance_ids must be a nonempty list of 14-character IDs")
    with _db("balance") as db, db.cursor() as cursor:
        cursor.execute("SELECT id, merchant_id, type, account_type, balance, updated_at FROM balance WHERE id IN (" +
                       ",".join(["%s"] * len(balance_ids)) + ")", tuple(balance_ids))
        return cursor.fetchall()


def _internal_balances_queued(handler, body):
    try:
        req = json.loads(body or b"{}")
        rows = _balance_rows(req.get("balance_ids"))
    except (ValueError, TypeError):
        return 400, {"error": "invalid_balance_ids"}
    except Exception as exc:
        return 503, {"error": "balance_database_unavailable", "error_type": type(exc).__name__}
    out = {row["id"]: int(row["balance"]) for row in rows}
    _log_sink("internal_balances_queued", {"request": req, "balances": out, "origin": "api_local.balance"})
    return 200, {"balances": out}


def _arena_balance_sync(handler, body):
    """Explicit synthetic equivalent of Ledger -> API mirror -> queued event.

    Does not create money: tests must first perform their intended Ledger top-up.
    The mirror commit precedes the event; failed delivery remains observable and
    the real six-hour cron can recover it. Direct balances use the real x-balances path.
    """
    try:
        req = json.loads(body or b"{}")
        if set(req) - {"balance_ids", "deliver_event"}:
            raise ValueError("unknown fields")
        if "deliver_event" in req and type(req["deliver_event"]) is not bool:
            raise ValueError("deliver_event must be boolean")
        rows = _balance_rows(req.get("balance_ids"))
        if len(rows) != len(set(req["balance_ids"])):
            return 404, {"error": "balance_not_found"}
        if any(row["account_type"] != "shared" for row in rows):
            return 422, {"error": "direct_balance_owned_by_x_balances"}
        balances = {}
        for row in rows:
            value = _ledger_merchant_balance(row["merchant_id"])
            if value is None:
                return 502, {"error": "ledger_balance_unavailable", "balance_id": row["id"]}
            balances[row["id"]] = value
        now = int(time.time())
        with _db("balance") as db, db.cursor() as cursor:
            db.begin()
            for balance_id, value in balances.items():
                cursor.execute("UPDATE balance SET balance = %s, updated_at = %s WHERE id = %s",
                               (value, now, balance_id))
            db.commit()
        _log_sink("balance_mirror_updated", {"balances": balances, "updated_at": now, "origin": "ledger"})
        if not req.get("deliver_event", True):
            return 200, {"balances": balances, "updated_at": now, "event_delivered": False}
        with open(PS_RELAY_AUTH_PASS_FILE) as f:
            auth = base64.b64encode((PS_RELAY_AUTH_USER + ":" + f.read().strip()).encode()).decode()
        status, response = _http("POST", PS_RELAY_URL.split("/v1/")[0] + "/v1/payouts/balance_update_event",
                                 {"balance_ids": list(balances)}, {"Authorization": "Basic " + auth})
        _log_sink("balance_update_event", {"balance_ids": list(balances), "status": status, "response": response})
        return (200 if 200 <= status < 300 else 502), {"balances": balances, "updated_at": now,
                "event_delivered": 200 <= status < 300, "event_status": status, "response": response}
    except (ValueError, TypeError):
        return 400, {"error": "invalid_balance_sync_request"}
    except Exception as exc:
        _log_sink("balance_sync_failed", {"error_type": type(exc).__name__})
        return 503, {"error": "balance_sync_failed", "error_type": type(exc).__name__}

# --- POST /update_fts_fund_transfer --- relay to payouts-api, single attempt,
# no retry (findings/20 §4), controlled by PS_RELAY_MODE=relay|drop.
def _update_fts_fund_transfer(handler, body, controlled=True):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    _log_sink("update_fts_fund_transfer_inbound", req)
    if controlled:
        payout_id = str(req.get("source_id", "")).removeprefix("pout_")
        with RELAY_LOCK:
            control = dict(RELAY_CONTROLS.get(payout_id, {}))
        action = control.get("action", "deliver")
        if action == "drop":
            _log_sink("relay_dropped", {"payout_id": payout_id})
            return 200, {"relayed": False, "mode": "drop"}
        if action in ("hold", "delay"):
            with RELAY_LOCK:
                RELAY_PENDING.setdefault(payout_id, []).append(body)
            if action == "delay":
                timer = threading.Timer(control["delay_seconds"], _release_relay, args=(payout_id, False))
                timer.daemon = True
                timer.start()
            return 200, {"relayed": False, "mode": action}
        if action == "duplicate":
            results = [_update_fts_fund_transfer(handler, body, False) for _ in range(control.get("copies", 2))]
            return (200 if all(r[0] == 200 for r in results) else 502), {"mode": action, "results": results}

    if controlled and PS_RELAY_MODE == "drop":
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
    if fts_status not in ("CREATED","INITIATED","PROCESSED","FAILED","REVERSED"):
        _log_sink("fts_unknown_status_ignored",{"payout_id":bare_src,"status":fts_status})
        return 200,{"message":"webhook update skipped due to unknown status"}
    try:
        payout = _ps_get_payout(bare_src)
    except Exception:
        return 503, {"error": "payout_database_unavailable"}
    if not payout:
        return 404, {"error": "payout_not_found"}
    if fts_status == "FAILED":
        m = MERCHANTS.get(payout.get("merchant_id"), {})
        payout_status = "failed" if str(m.get("account_type", "shared")).lower() == "direct" else "reversed"
    if payout.get("status") in ("cancelled","rejected","pending","queued","scheduled","on_hold") or (
            payout.get("status")==payout_status and payout_status in ("processed","failed","reversed")):
        _log_sink("fts_terminal_or_invalid_repeat_ignored",{"payout_id":bare_src,"status":payout_status})
        return 200,{"message":"webhook update skipped due to invalid state transition"}
    ps_body = {
        "source_id": bare_src,
        "status": payout_status,
        "failure_reason": req.get("failure_reason"),
        "bank_status_code": req.get("bank_status_code"),
        "fts_fund_account_id": str(req.get("source_account_id") or ""),
        # api Payout/Core.php:949-951 forwards the selected SOURCE account.
        # payouts ledger client lowercases bank_account_type for discovery.
        "fts_account_type": str(req.get("bank_account_type") or "").lower(),
        # PHP Attempt/Core.php normalizes incoming FTS status before the
        # Payouts DTO is built; reversal dispatch matches these lowercase values.
        "fts_status": fts_status.lower(),
    }
    # The monolith first syncs the FTA details into PS (payouts internal/app/dtos/payoutUpdate.go DetailsUpdateRequest:
    # fund_transfer_id is required; utr/mode/channel/bank_status_code/failure_reason/remarks/return_utr/gateway_ref_no
    # optional) via POST /v1/payouts/update_payouts_details_with_fts, then the status update. Same headers/identity.
    # Narration belongs to the FTS transfer and is absent from the strict Payouts DetailsUpdateRequest DTO.
    details = {"source_id": bare_src}
    try:
        details["fund_transfer_id"] = int(req.get("fund_transfer_id") or req.get("id") or 0)
    except (TypeError, ValueError):
        details["fund_transfer_id"] = 0
    for k in ("utr", "mode", "channel", "bank_status_code", "failure_reason", "remarks", "return_utr", "gateway_ref_no"):
        if req.get(k) not in (None, ""):
            details[k] = str(req.get(k))
    details["fta_status"] = fts_status.lower()
    if details["fund_transfer_id"]:
        try:
            # payouts payout_internal_routes.go: /update_payouts_details_with_fts is POST (the status route is PATCH)
            d_req = urllib.request.Request(PS_RELAY_URL.replace("update_payouts_with_fts", "update_payouts_details_with_fts"),
                                           data=json.dumps(details).encode(), headers=headers, method="POST")
            with urllib.request.urlopen(d_req, timeout=10) as d_resp:
                _log_sink("update_payouts_details_with_fts", {"status": d_resp.status, "body": details})
        except urllib.error.HTTPError as exc:
            _log_sink("update_payouts_details_with_fts", {"status": exc.code, "body": details})
            return 502, {"error": "payout_details_update_failed", "relay_status_code": exc.code}
        except Exception as exc:  # noqa: BLE001
            _log_sink("update_payouts_details_with_fts", {"error_type": type(exc).__name__})
            return 502, {"error": "payout_details_update_failed"}
    if fts_status in ("CREATED","INITIATED"):
        return 200,{"message":"details updated; no terminal status call"}
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

    _log_sink("payout_status_relay", {"body": ps_body, "status": status})
    return (200 if status and 200 <= status < 300 else 502), {"relayed": relayed, "mode": "relay", "relay_status_code": status}


def _release_relay(payout_id, reverse=False, order=None):
    with RELAY_LOCK:
        messages = RELAY_PENDING.get(payout_id, [])
        if order is not None and (not isinstance(order, list) or
                any(type(i) is not int for i in order) or sorted(order) != list(range(len(messages)))):
            raise ValueError("order must contain every pending message index exactly once")
        messages = RELAY_PENDING.pop(payout_id, [])
    if order is not None:
        messages = [messages[i] for i in order]
    if reverse:
        messages.reverse()
    results = [_update_fts_fund_transfer(None, body, False) for body in messages]
    _log_sink("relay_released", {"payout_id": payout_id, "reverse": reverse, "results": results})
    return results


def _arena_relay_control(handler, body):
    try:
        req = json.loads(body or b"{}")
        payout_id = req["payout_id"].removeprefix("pout_")
        if not re.fullmatch(r"[A-Za-z0-9]{14}", payout_id):
            raise ValueError("invalid payout ID")
        action = req.get("action", req.get("mode"))
        action = "hold" if action == "reorder" else action
        req["action"] = action
        if "delay_ms" in req:
            req["delay_seconds"] = req["delay_ms"] / 1000
        if action == "release":
            results = _release_relay(payout_id, bool(req.get("reverse", False)), req.get("order"))
            return (200 if all(r[0] == 200 for r in results) else 502), {"results": results}
        if action not in ("deliver", "drop", "hold", "delay", "duplicate"):
            raise ValueError("invalid action")
        if action == "delay" and (not isinstance(req.get("delay_seconds"), (int, float)) or
                                  not 0 < req["delay_seconds"] <= 300):
            raise ValueError("invalid delay")
        if action == "duplicate" and (type(req.get("copies", 2)) is not int or not 2 <= req.get("copies", 2) <= 5):
            raise ValueError("invalid copies")
        with RELAY_LOCK:
            RELAY_CONTROLS[payout_id] = req
        return 200, {"payout_id": payout_id, "action": action}
    except (KeyError, ValueError, TypeError, AttributeError):
        return 400, {"error": "invalid_relay_control"}


def _arena_relay_release(handler, body):
    try:
        req = json.loads(body or b"{}")
        req["action"] = "release"
        return _arena_relay_control(handler, json.dumps(req).encode())
    except (ValueError, TypeError):
        return 400, {"error": "invalid_release_request"}


def _arena_log(handler, body):
    return 200, {"log": LOG}



def _auxiliary_sink(handler,body):
    try: req=json.loads(body or b"{}")
    except ValueError: return 400,{"error":{"code":"BAD_REQUEST_ERROR","description":"invalid JSON"}}
    _log_sink(handler.path.rsplit("/",1)[-1],req)
    return 200,{}  # ASSUMED response shape; no monolith transaction is fabricated.


# ---------------------------------------------------------------------------
# Direct-account (DA) ledger emitter -- substitute for the monolith's
# BankingAccountStatement/Core.php:5175-5228 sendToLedgerPostSourceEntityProcessing ->
# :5243-5278 processLedgerPayoutForDirect -> Transaction/Processor/Ledger/Payout.php:235-425
# pushTransactionToLedgerForDirect -> Ledger/Base.php:180 pushToLedgerSns.
#
# Trigger point (declared deviation): the real monolith emits from its OWN statement-fetch
# linking path (saveAccountStatementV2 :3081-3091); the XAS-triggered route payoutUpdateByBASRecon
# has its ledger call commented out (Payout/Core.php:10327,10359). The twin has no monolith
# statement fetcher, so this substitute emits when the XAS-shaped payout_update reaches it and
# the relay to the REAL PS UpdatePayoutAfterBASRecon succeeded (2xx) -- i.e. the same moment a
# Direct payout becomes linked to a debit/credit statement.
#
# Transport: SNS topic api-ledger-journal-create-live (payouts appConstants/constants.go:381,
# Ledger/Base.php LEDGER_TRANSACTION_CREATE) -> SQS journal_create consumed by the REAL
# ledger-worker-journal-create (SNS envelope unwrapped by ledger job_sqs/base.go extractRawContent).
# ---------------------------------------------------------------------------
LEDGER_ENABLED = os.environ.get("LEDGER_ENABLED", "true").lower() != "false"   # applications.ledger.enabled
DA_LEDGER_TOPIC = os.environ.get("DA_LEDGER_SNS_TOPIC", "api-ledger-journal-create-live")
DA_LEDGER_QUEUE = os.environ.get("DA_LEDGER_SQS_QUEUE", "journal_create")
DA_LEDGER_IKEY_MODE = os.environ.get("DA_LEDGER_IKEY_MODE", "uuid")   # uuid (monolith: Uuid::uuid1 per publish) | deterministic
SQS_ENDPOINT = os.environ.get("ARENA_SQS_ENDPOINT", "http://localstack:4566")
AWS_REGION, AWS_ACCOUNT = "ap-south-1", "000000000000"
LEDGER_EMITS = []
FEATURE_OVERRIDES = {}   # merchant_id -> {feature: bool}; runtime, lost on restart (merchants.json is the boot source)
_TOPIC_ARN = [None]
_TOPIC_LOCK = threading.Lock()
DA_EVENTS = {"processed": ("da_payout_processed", "da_payout_processed_recon"),
             "reversed": ("da_payout_reversed", "da_payout_reversed_recon"),
             "fee_processed": ("da_fee_payout_processed",), "fee_reversed": ("da_fee_payout_reversed",)}


def _aws_headers(service):
    now = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return {"Authorization": "AWS4-HMAC-SHA256 Credential=arena/%s/%s/%s/aws4_request, SignedHeaders=host;x-amz-date, Signature=0"
                             % (now[:8], AWS_REGION, service), "X-Amz-Date": now}


def _aws_post(data, headers, timeout=20):
    req = urllib.request.Request(SQS_ENDPOINT.rstrip("/") + "/", data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, (exc.read() or b"").decode("utf-8", "replace")


def _sqs(action, body):
    h = {"Content-Type": "application/x-amz-json-1.0", "X-Amz-Target": "AmazonSQS.%s" % action}
    h.update(_aws_headers("sqs"))
    st, text = _aws_post(json.dumps(body).encode(), h)
    if st != 200:
        raise RuntimeError("sqs %s %s: %s" % (action, st, text[:200]))
    return json.loads(text or "{}")


def _sns(action, params):
    import urllib.parse
    form = dict(params, Action=action, Version="2010-03-31")
    h = {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}
    h.update(_aws_headers("sns"))
    st, text = _aws_post(urllib.parse.urlencode(form).encode(), h)
    if st != 200:
        raise RuntimeError("sns %s %s: %s" % (action, st, text[:200]))
    return text


def _xml(text, tag):
    m = re.search(r"<%s>(.*?)</%s>" % (tag, tag), text, re.S)
    return m.group(1).strip() if m else None


def _ensure_ledger_topic():
    """Idempotent: CreateTopic returns the existing ARN; Subscribe of the same endpoint returns the
    existing subscription. RawMessageDelivery=false keeps the production SNS envelope."""
    with _TOPIC_LOCK:
        if _TOPIC_ARN[0]:
            return _TOPIC_ARN[0]
        arn = _xml(_sns("CreateTopic", {"Name": DA_LEDGER_TOPIC}), "TopicArn")
        _sqs("GetQueueUrl", {"QueueName": DA_LEDGER_QUEUE})   # queue must pre-exist (seeds/localstack/init-queues.sh)
        _sns("Subscribe", {"TopicArn": arn, "Protocol": "sqs",
                           "Endpoint": "arn:aws:sqs:%s:%s:%s" % (AWS_REGION, AWS_ACCOUNT, DA_LEDGER_QUEUE),
                           "Attributes.entry.1.key": "RawMessageDelivery", "Attributes.entry.1.value": "false"})
        _TOPIC_ARN[0] = arn
        _log("ledger SNS topic %s -> SQS %s wired" % (arn, DA_LEDGER_QUEUE))
        return arn


def _publish_journal(payload):
    """pushToLedgerSns: SNS publish (retried in the monolith, 3 sync retries). Falls back to a direct
    SQS SendMessage of the raw JSON (recorded as a transport deviation) if SNS is unavailable."""
    message = json.dumps(payload)
    last = None
    for _ in range(3):
        try:
            arn = _ensure_ledger_topic()
            mid = _xml(_sns("Publish", {"TopicArn": arn, "Message": message}), "MessageId")
            return {"transport": "sns:%s->sqs:%s" % (DA_LEDGER_TOPIC, DA_LEDGER_QUEUE), "message_id": mid}
        except Exception as exc:  # noqa: BLE001
            last = str(exc)[:200]
    try:
        out = _sqs("SendMessage", {"QueueUrl": "%s/%s/%s" % (SQS_ENDPOINT.rstrip("/"), AWS_ACCOUNT, DA_LEDGER_QUEUE), "MessageBody": message})
        return {"transport": "sqs_direct_fallback:%s" % DA_LEDGER_QUEUE, "message_id": out.get("MessageId"), "sns_error": last}
    except Exception as exc:  # noqa: BLE001
        return {"transport": "failed", "error": str(exc)[:200], "sns_error": last}


def _merchant_features(merchant_id):
    rec = MERCHANTS.get(merchant_id, {})
    feats = {f: True for f in (rec.get("merchant", {}).get("feature") or [])}
    feats.update(FEATURE_OVERRIDES.get(merchant_id, {}))
    return feats


def _feature_on(merchant_id, name):
    return bool(_merchant_features(merchant_id).get(name))


def _basd_for_balance(balance_id, channel):
    """Payout.php getDefaultPayloadForDirectPayout: banking_account_statement_details by
    (account_number, channel). The twin's BASD rows live in the PS DB (the monolith inserts them
    there, Details/Core.php:219-237); the balance_id lookup is equivalent (PS GetBasDetailsID)."""
    with _db("payouts") as db, db.cursor() as cursor:
        cursor.execute("SELECT id, account_number, channel FROM banking_account_statement_details "
                       "WHERE balance_id=%s AND status='active' ORDER BY created_at LIMIT 2", (balance_id,))
        rows = cursor.fetchall()
    rows = [r for r in rows if not channel or str(r.get("channel") or "").lower() == channel.lower()] or rows
    return rows[0] if rows else None


def _ps_reversal(payout_id):
    with _db("payouts") as db, db.cursor() as cursor:
        cursor.execute("SELECT * FROM reversals WHERE payout_id=%s ORDER BY created_at DESC LIMIT 1", (payout_id,))
        return cursor.fetchone()


def _ikey(transactor_id, transactor_event, bas_id):
    if DA_LEDGER_IKEY_MODE == "deterministic":
        import hashlib
        return str(uuid.UUID(hashlib.sha256(("%s|%s|%s" % (transactor_id, transactor_event, bas_id)).encode()).hexdigest()[:32]))
    return str(uuid.uuid1())   # Payout.php: Uuid::uuid1()->toString() per publish


def _da_journal_payload(payout, basd, transactor_event, transactor_id, transaction_id, transaction_date, api_transaction_id):
    notes_raw = payout.get("notes")
    try:
        pnotes = json.loads(notes_raw) if isinstance(notes_raw, str) and notes_raw else (notes_raw or {})
    except ValueError:
        pnotes = {}
    identifiers = {"banking_account_stmt_detail_id": "basd_" + basd["id"]}
    if isinstance(pnotes, dict) and pnotes.get("product_id"):
        identifiers["product_id"] = pnotes["product_id"]
    additional = {}
    if isinstance(pnotes, dict) and pnotes.get("account_type"):
        additional["account_type"] = pnotes["account_type"]
    if not transactor_event.endswith("_recon") and str(payout.get("fee_type") or "") == "reward_fee":
        additional["fee_accounting"] = "reward"   # updatePayloadForFeeCredits
    payload = {
        "tenant": "X", "mode": "live",
        "idempotency_key": _ikey(transactor_id, transactor_event, transaction_id),
        "merchant_id": payout["merchant_id"], "currency": payout.get("currency") or "INR",
        "amount": str(int(payout.get("amount") or 0)), "base_amount": str(int(payout.get("amount") or 0)),
        "commission": str(int(payout.get("fees") or 0)), "tax": str(int(payout.get("tax") or 0)),
        "identifiers": json.dumps(identifiers), "additional_params": json.dumps(additional),
        # Payout.php:365-368 notes = {balance_id: bal_<id>, transaction_id: <signed txn>}. The monolith signs
        # its own `transactions` row (txn_); the twin has no monolith transaction entity, so the PS mirror
        # form bas_<bas_id> (payouts/core.go:7758) is used. Declared deviation.
        "notes": json.dumps({"balance_id": "bal_" + str(payout.get("balance_id") or ""), "transaction_id": "bas_" + str(transaction_id or "")}),
        "transactor_id": transactor_id, "transactor_event": transactor_event,
        "transaction_date": int(transaction_date or 0),
    }
    if api_transaction_id:
        payload["api_transaction_id"] = api_transaction_id
    return payload


def _emit(event, payload, extra):
    rec = {"at": time.time(), "transactor_event": event, "transactor_id": payload.get("transactor_id"),
           "merchant_id": payload.get("merchant_id"), "payload": payload}
    rec.update(extra)
    rec.update(_publish_journal(payload))
    with STATE_LOCK:
        LEDGER_EMITS.append(rec)
        del LEDGER_EMITS[:-2000]
    _log("da_ledger_emit %s %s via %s" % (event, payload.get("transactor_id"), rec.get("transport")))
    return rec


def _skip(reason, req, **extra):
    rec = {"at": time.time(), "skipped": reason, "merchant_id": req.get("merchant_id"), "entity_id": req.get("entity_id"),
           "entity_type": req.get("entity_type"), "bas_id": req.get("bas_id")}
    rec.update(extra)
    with STATE_LOCK:
        LEDGER_EMITS.append(rec)
    _log("da_ledger_skipped %s %s" % (reason, json.dumps(extra, default=str)[:300]))
    return rec


def send_to_ledger_post_source_entity_processing(req, relay_status):
    """Reproduces Core.php:3081-3091 gate + :5175-5228 + :5243-5278 + Payout.php:235-425."""
    mid, entity_type, bas_id = req.get("merchant_id"), req.get("entity_type"), req.get("bas_id")
    if not (relay_status and 200 <= relay_status < 300):
        return _skip("relay_not_2xx", req, relay_status=relay_status)
    if not LEDGER_ENABLED:
        return _skip("ledger_disabled", req)
    if entity_type not in ("payout", "payout_reversal"):
        return _skip("entity_type_not_payout", req)
    try:
        payout = _ps_get_payout(str(req.get("entity_id") or ""))
    except Exception as exc:  # noqa: BLE001
        return _skip("payout_lookup_failed", req, error_type=type(exc).__name__)
    if not payout:
        return _skip("payout_not_found", req)
    merchant_rec = MERCHANTS.get(mid, {})
    if str(merchant_rec.get("account_type", "shared")).lower() != "direct":
        return _skip("shared_or_primary_balance", req)                      # Core.php:5256-5259
    feats = _merchant_features(mid)
    if feats.get("high_tps_composite_payout"):
        return _skip("high_tps_composite_payout", req)                      # :5262-5265
    # Core.php:3081-3091: PS-owned payout + debit statement + Splitz payout_service_txn_recon ON ->
    # the monolith defers to PS ("which already does this") -- but PS has it commented out (core.go:7284).
    # Preserved production gap: nothing posts. Modelled as the per-merchant feature payout_service_txn_recon.
    if feats.get("payout_service_txn_recon") and entity_type == "payout":
        return _skip("da_ledger_skipped_ps_recon", req, features=feats,
                     note="production gap preserved: monolith defers to PS UpdatePayoutAfterBASRecon whose ledger call is commented out")
    if not feats.get("da_ledger_journal_writes"):
        return _skip("da_ledger_journal_writes_off", req, features=feats)   # :5268-5271
    purpose = str(payout.get("purpose") or "")
    if purpose == "rzp_charge_collections":
        return _skip("charge_collections_not_modelled", req)
    try:
        basd = _basd_for_balance(payout.get("balance_id"), payout.get("channel") or "")
    except Exception as exc:  # noqa: BLE001
        return _skip("basd_lookup_failed", req, error_type=type(exc).__name__)
    if not basd:
        return _skip("basd_not_found", req, balance_id=payout.get("balance_id"))
    emitted = []
    converted = bool(req.get("converted_from_external"))
    if entity_type == "payout":
        events = DA_EVENTS["fee_processed"] if purpose == "rzp_fees" else DA_EVENTS["processed"]
        if converted:
            # Payout/Core.php:10003-10016 sendExtToPayoutEventToLedger: an external row re-identified as a payout
            # posts the single re-class event DA_EXT_PAYOUT_PROCESSED (no _recon, no api_transaction_id).
            events = ("da_ext_fee_payout_processed",) if purpose == "rzp_fees" else ("da_ext_payout_processed",)
        for ev in events:
            if ev.startswith("da_ext_"):
                payload = _da_journal_payload(payout, basd, ev, "pout_" + payout["id"], payout.get("transaction_id") or bas_id,
                                              payout.get("updated_at"), None)
            elif ev.endswith("_recon"):
                # DA_PAYOUT_PROCESSED_RECON: transactor_id pout_, transaction_id = payout txn, date = bas.transaction_date
                payload = _da_journal_payload(payout, basd, ev, "pout_" + payout["id"], payout.get("transaction_id") or bas_id,
                                              req.get("transaction_date"), None)
            else:
                # DA_PAYOUT_PROCESSED: api_transaction_id = payout txn id; date = payout.processed_at (PS has no such
                # column: updated_at used, as the PS mirror does, payouts/core.go:7703) -- declared deviation
                payload = _da_journal_payload(payout, basd, ev, "pout_" + payout["id"], payout.get("transaction_id") or bas_id,
                                              payout.get("updated_at"), payout.get("transaction_id") or bas_id)
            emitted.append(_emit(ev, payload, {"bas_id": bas_id, "entity_id": payout["id"], "basd_id": basd["id"]}))
    else:
        try:
            reversal = _ps_reversal(payout["id"])
        except Exception as exc:  # noqa: BLE001
            return _skip("reversal_lookup_failed", req, error_type=type(exc).__name__)
        if not reversal:
            return _skip("reversal_not_found", req)
        events = DA_EVENTS["fee_reversed"] if purpose == "rzp_fees" else DA_EVENTS["reversed"]
        if converted:
            events = ("da_ext_fee_payout_reversed",) if purpose == "rzp_fees" else ("da_ext_payout_reversed",)
        for ev in events:
            rid = "rvrsl_" + reversal["id"]
            if ev.startswith("da_ext_"):
                payload = _da_journal_payload(payout, basd, ev, rid, reversal.get("transaction_id") or bas_id, reversal.get("created_at"), None)
            elif ev.endswith("_recon"):
                payload = _da_journal_payload(payout, basd, ev, rid, reversal.get("transaction_id") or bas_id, req.get("transaction_date"), None)
            else:
                payload = _da_journal_payload(payout, basd, ev, rid, reversal.get("transaction_id") or bas_id, reversal.get("created_at"),
                                              reversal.get("transaction_id") or bas_id)
            emitted.append(_emit(ev, payload, {"bas_id": bas_id, "entity_id": payout["id"], "reversal_id": reversal["id"], "basd_id": basd["id"]}))
    return {"emitted": [e["transactor_event"] for e in emitted]}


def _banking_statement_payout_update(handler,body):
    """POST /v1/banking_account_statement/payout_update -- api Payout/Core.php:10305-10318 payoutUpdateByBASRecon:
    forwards the XAS EnrichmentUpdateRequest (9 fields: bas_id, entity_id, entity_type, merchant_id, transaction_date,
    converted_from_external, utr, grn, cms_ref_no) VERBATIM to PS /v1/payouts/banking_account_statement/payout_update
    (payouts dtos.PayoutUpdateBASEntityRequest reads the same 9 names). Then the DA ledger emitter (above)."""
    try:
        req=json.loads(body or b"{}")
        if any(k not in req for k in ("bas_id","entity_id","entity_type","merchant_id","transaction_date")):
            raise ValueError("missing fields")
        if req["entity_type"] not in ("payout","payout_reversal"): raise ValueError("invalid entity")
        with open(PS_RELAY_AUTH_PASS_FILE) as f:
            auth=base64.b64encode((PS_RELAY_AUTH_USER+":"+f.read().strip()).encode()).decode()
        status,response=_http("POST",PS_RELAY_URL.split("/v1/")[0]+"/v1/payouts/banking_account_statement/payout_update",req,{"Authorization":"Basic "+auth})
        _log_sink("banking_statement_payout_update",{"request":req,"status":status,"response":response})
        ledger = send_to_ledger_post_source_entity_processing(req, status)
        _log_sink("da_ledger_emitter",{"entity_id":req.get("entity_id"),"bas_id":req.get("bas_id"),"result":ledger})
        return status,response
    except (ValueError,TypeError):
        return 400,{"error":{"code":"BAD_REQUEST_ERROR","description":"invalid statement update"}}
    except Exception:
        return 502,{"error":{"code":"SERVER_ERROR","description":"statement update relay failed"}}


def _arena_ledger_emits(handler, body):
    import urllib.parse
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    mid = (q.get("merchant_id") or [None])[0]
    with STATE_LOCK:
        rows = [e for e in LEDGER_EMITS if not mid or e.get("merchant_id") == mid]
    return 200, {"emits": rows, "topic": DA_LEDGER_TOPIC, "queue": DA_LEDGER_QUEUE, "ikey_mode": DA_LEDGER_IKEY_MODE,
                 "ledger_enabled": LEDGER_ENABLED}


def _arena_merchant_features(handler, body):
    """POST {merchant_id, features:{name:bool}} sets runtime overrides on top of merchants.json `merchant.feature`
    (loaded at boot only). GET ?merchant_id= returns the effective set."""
    import urllib.parse
    if handler.command == "GET":
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
        mid = (q.get("merchant_id") or [""])[0]
        return 200, {"merchant_id": mid, "features": _merchant_features(mid), "overrides": FEATURE_OVERRIDES.get(mid, {})}
    try:
        req = json.loads(body or b"{}")
        mid = req["merchant_id"]
        feats = req["features"]
        if not re.fullmatch(r"[A-Za-z0-9]{14}", mid) or not isinstance(feats, dict) or \
                any(not isinstance(v, bool) or not re.fullmatch(r"[a-z0-9_]{1,64}", k) for k, v in feats.items()):
            raise ValueError("invalid")
    except (KeyError, ValueError, TypeError):
        return 400, {"error": {"code": "BAD_REQUEST_ERROR", "description": "merchant_id (14 chars) and features {name: bool} required"}}
    with STATE_LOCK:
        FEATURE_OVERRIDES.setdefault(mid, {}).update(feats)
    _log_sink("merchant_features_override", {"merchant_id": mid, "features": feats})
    return 200, {"merchant_id": mid, "features": _merchant_features(mid), "overrides": FEATURE_OVERRIDES[mid]}

ROUTES = {
    ("POST", "/payouts_service/create_ledger"): _auxiliary_sink,
    ("POST", "/payouts_service/free_payout_rollback"): _auxiliary_sink,
    ("POST", "/payouts_service/create"): _auxiliary_sink,
    ("POST", "/v1/payouts/banking_account_statement/payout_update"): _banking_statement_payout_update,
    # api Route.php:4837 `banking_account_statement/payout_update` (payouts_service group): XAS gateway posts /v1/banking_account_statement/payout_update
    ("POST", "/banking_account_statement/payout_update"): _banking_statement_payout_update,
    ("POST", "/payouts/banking_account_statement/payout_update"): _banking_statement_payout_update,
    ("GET", "/internal/merchants/"): _get_merchant,
    ("POST", "/payouts_service/fetch_pricing_info"): _fetch_pricing_info,
    ("POST", "/payouts_service/deduct_credits"): _deduct_credits,
    ("POST", "/payouts_service/reverse_credits"): _reverse_credits,
    ("POST", "/payouts_service/source_update"): _source_update,
    ("POST", "/payouts_service/status_details_source_update"): _status_details_source_update,
    ("POST", "/payouts_service/mail_and_sms"): _mail_and_sms,
    ("POST", "/payouts_service/dual_write"): _dual_write,
    ("POST", "/payouts_service/decrement_free_payouts"): _decrement_free_payouts,
    # M7: GET /fund_accounts_internal/{id} is served by the shared ingress (substitutes/api-ingress) from explicit
    # merchant-owned records; payouts' [api] host now points at the ingress, so this route is retired here.
    # The owner-less record this handler used to return was the mechanism behind the M6 D-7 cross-tenant use.
    ("POST", "/merchant/on_hold_slas_internal"): _on_hold_slas,
    ("GET", "/actor_info_internal/"): _actor_info,
    ("POST", "/users_internal"): _users_internal,
    ("POST", "/update_fts_fund_transfer"): _update_fts_fund_transfer,
    ("POST", "/payouts_service/create_fta/"): _create_fta,
    ("GET", "/internal_balances_queued"): _internal_balances_queued,
    ("POST", "/_arena/balance-sync"): _arena_balance_sync,
    ("POST", "/_arena/relay-control"): _arena_relay_control,
    ("POST", "/_arena/relay/release"): _arena_relay_release,
    ("POST", "/_arena/relay"): _arena_relay_control,
    ("GET", "/_arena/log"): _arena_log,
    ("GET", "/_arena/ledger_emits"): _arena_ledger_emits,
    ("GET", "/_arena/merchant_features"): _arena_merchant_features,
    ("POST", "/_arena/merchant_features"): _arena_merchant_features,
}

if __name__ == "__main__":
    serve(ROUTES)
