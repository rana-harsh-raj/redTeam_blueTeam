#!/usr/bin/env python3
"""xas-sim: contract-faithful substitute for x-account-statements (XAS). See CONTRACT.md.

Replaces the health-only xas-sink. Reproduces, from the pristine XAS clone
(x-account-statements@e73fd5a):

  * source-event consumer  internal/job/source_processing + internal/source_events/service/service.go
      - SQS queue `x_account_statement_source_event` on LocalStack (same name PS publishes to)
      - body = bare JSON of payouts' job.XasEventDetails (PerformWithoutSerialization)
      - schema read with XAS field names (internal/source_events/model/dto.go): the PS producer
        writes `event_created_timestamp`, XAS reads `event_create_timestamp` -> stored 0.
        Reproduced, not fixed (T03 Finding 3 / C-T03-10); both spellings are logged per event.
      - Validate(): utr || gateway_ref_no || cms_ref_no else SourceEventInvalid
      - persistence key UNIQUE(event_id, entity_type); on duplicate the stored row is reused
  * enrichment  internal/account_statements/enrich/service/service.go + enrichers/payout_enricher.go
      - keys tried in order utr -> gateway_ref_number (UPPER) -> cms_ref_number (= bank_transaction_id)
      - statements matched with account_number + amount; source events with balance_id + amount
      - statement-driven: no event -> entity_type=external; else enrich with events[0]
      - event-driven: 0 stmts -> no-op; >2 -> TooManyStmtsForUtr no-op; two of same type ->
        UnexpectedStmtType no-op; else enrich each (typically one debit + one credit)
      - PayoutEnricher: credit -> payout_reversal, debit -> payout; refuses to relink a statement
        already linked to a DIFFERENT non-external entity (StatementAlreadyLinkedError, no-op)
  * outbound dual-write  internal/account_statements/dual_write/service/service.go +
    internal/gateway/api/{params,service}.go
      - POST {api}/v1/banking_account_statement/payout_update with the 9-field
        EnrichmentUpdateRequest {bas_id, entity_id, entity_type, merchant_id, transaction_date,
        converted_from_external, utr, grn, cms_ref_no}, HTTP Basic, only when entity_type is
        payout|payout_reversal (CDC Update path); POST /v1/statement/dual_write {id} re-sends
        unconditionally (DualWriteForStatement)
      - Kafka CDC is NOT reproduced: the outbound call is made synchronously right after the
        enrichment write (declared deviation)
  * GET /v1/account_statements/fetch_multiple_by_reference_numbers
      internal/account_statements/get/service/service.go:425-480 + get/validate/validate.go:174-215
      proto x/x-account-statements/statement/v1/statement.proto (int64 fields serialised as JSON
      strings, as grpc-gateway does and as payouts/pkg/xAccountStatement and fts/internal/providers/xas
      parse them)

Statement ingestion (XAS fetch workers + Mozart) is NOT reproduced here: the arena's statement
of record is the REAL payouts `banking_account_statement` table (filled by the real
rbl_banking_account_statement worker or by the harness). The harness mirrors those rows into this
substitute through POST /_arena/statements/sync, which applies XAS's own dedupe tuple
(fetch/channels/banks/rbl/rbl.go:740-750). Everything is in-memory; /_arena/state exposes it.
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, "/app")
from _common.base_stub import serve, _log  # noqa: E402
import awslite  # noqa: E402

SOURCE_EVENT_QUEUE = os.environ.get("XAS_SOURCE_EVENT_QUEUE", "x_account_statement_source_event")
SQS_ENDPOINT = os.environ.get("XAS_SQS_ENDPOINT", "http://localstack:4566")
CONSUME = os.environ.get("XAS_CONSUME_SOURCE_EVENTS", "1") == "1"
API_BASE = os.environ.get("XAS_API_BASE_URL", "http://monolith-stub:8080")   # xas config [ApiService].BaseURL
API_AUTH_FILE = os.environ.get("XAS_API_AUTH_FILE", "/run/secrets/monolith_basic_auth")  # "user:pass"
POLL_WAIT_SECONDS = int(os.environ.get("XAS_POLL_WAIT_SECONDS", "5"))

ENTITY_PAYOUT = "payout"
ENTITY_PAYOUT_REVERSAL = "payout_reversal"
ENTITY_EXTERNAL = "external"
KEY_UTR, KEY_GRN, KEY_CMS = "utr", "gateway_ref_number", "cms_ref_number"
# XAS DedupeFilters for RBL (rbl.go:740-750)
DEDUPE_TUPLE = ("account_number", "posted_date", "type", "bank_serial_number", "amount", "channel", "bank_transaction_id")

LOCK = threading.RLock()
STATE = {
    "accounts": {},        # (account_number, channel) -> account row
    "statements": {},      # id -> statement row (XAS account_statements shape)
    "source_events": {},   # (event_id, entity_type) -> row
    "links": [],           # enrichment decisions (audit)
    "outbound": [],        # payout_update calls made
    "fetch_log": [],       # fetch_multiple_by_reference_numbers requests/responses
    "consumer": {"started": False, "queue": SOURCE_EVENT_QUEUE, "received": 0, "deleted": 0, "errors": 0, "last_error": None,
                 "last_poll_at": None},
    "started_at": int(time.time()),
}
COUNTER = [0]


def _now():
    return int(time.time())


def _new_id(prefix="XASSTMT"):
    COUNTER[0] += 1
    return (prefix + uuid.uuid4().hex.upper())[:14]


def _audit(kind, **fields):
    rec = {"kind": kind, "at": time.time()}
    rec.update(fields)
    with LOCK:
        STATE["links"].append(rec)
        del STATE["links"][:-2000]
    _log("%s %s" % (kind, json.dumps(fields, default=str)[:600]))
    return rec


# ---------------------------------------------------------------------------
# accounts (XAS `accounts` table, UNIQUE(account_number, channel))
# ---------------------------------------------------------------------------
def upsert_account(row):
    for k in ("account_number", "channel", "balance_id", "merchant_id"):
        if not row.get(k):
            raise ValueError("account requires %s" % k)
    key = (row["account_number"], row["channel"])
    with LOCK:
        existing = STATE["accounts"].get(key)
        rec = {"id": (existing or {}).get("id") or row.get("id") or _new_id("XASACC"), "status": row.get("status", "active"),
               "merchant_id": row["merchant_id"], "account_number": row["account_number"],
               "account_type": row.get("account_type", "direct"), "channel": row["channel"],
               "balance_id": row["balance_id"], "created_at": (existing or {}).get("created_at") or _now(),
               "updated_at": _now()}
        STATE["accounts"][key] = rec
    return rec, existing is not None


def account_by_number(account_number, channel):
    with LOCK:
        return STATE["accounts"].get((account_number, channel))


def accounts_by_balance(balance_id):
    with LOCK:
        return [a for a in STATE["accounts"].values() if a["balance_id"] == balance_id]


# ---------------------------------------------------------------------------
# statements store (XAS `account_statements`, mirrored from PS banking_account_statement)
# ---------------------------------------------------------------------------
STATEMENT_FIELDS = ("id", "entity_id", "entity_type", "channel", "merchant_id", "account_number", "bank_transaction_id",
                    "type", "utr", "amount", "currency", "description", "category", "bank_serial_number", "balance",
                    "balance_currency", "transaction_date", "posted_date", "gateway_ref_number", "created_at", "updated_at",
                    "fetched_at_nano")


def _dedupe_key(row):
    return tuple(str(row.get(k) if row.get(k) is not None else "") for k in DEDUPE_TUPLE)


def ingest_statement(row, source_label="substitute_injected"):
    """Persist one statement row (PS banking_account_statement column names). Applies the XAS
    dedupe tuple; keeps the PS id when supplied so bas_id stays identical across PS/XAS."""
    for k in ("account_number", "channel", "type", "amount"):
        if row.get(k) in (None, ""):
            raise ValueError("statement requires %s" % k)
    rec = {k: row.get(k) for k in STATEMENT_FIELDS}
    for k in ("amount", "balance", "transaction_date", "posted_date", "created_at", "updated_at", "fetched_at_nano"):
        if rec.get(k) not in (None, ""):
            rec[k] = int(rec[k])
    rec["entity_id"] = rec.get("entity_id") or ""
    rec["entity_type"] = rec.get("entity_type") or ""
    rec["currency"] = rec.get("currency") or "INR"
    rec["balance_currency"] = rec.get("balance_currency") or "INR"
    for k in ("utr", "gateway_ref_number", "bank_transaction_id", "description", "category", "bank_serial_number", "merchant_id"):
        rec[k] = rec.get(k) or ""
    rec["created_at"] = rec.get("created_at") or _now()
    rec["updated_at"] = rec.get("updated_at") or _now()
    rec["fetched_at_nano"] = rec.get("fetched_at_nano") or int(time.time() * 1e9)
    rec["_source"] = source_label
    key = _dedupe_key(rec)
    with LOCK:
        for existing in STATE["statements"].values():
            if _dedupe_key(existing) == key:
                return existing, True
        if rec.get("id") and rec["id"] in STATE["statements"]:
            return STATE["statements"][rec["id"]], True
        rec["id"] = rec.get("id") or _new_id()
        STATE["statements"][rec["id"]] = rec
    return rec, False


def _find_statements(key, value, account_number, amount):
    """repo.go FindStatementByUTR / ByGrn (UPPER) / ByBankTransactionID: <key> AND account_number AND amount."""
    with LOCK:
        rows = list(STATE["statements"].values())
    out = []
    for s in rows:
        if s["account_number"] != account_number or int(s["amount"]) != int(amount):
            continue
        if key == KEY_UTR and s["utr"] == value:
            out.append(s)
        elif key == KEY_GRN and s["gateway_ref_number"].upper() == value.upper():
            out.append(s)
        elif key == KEY_CMS and s["bank_transaction_id"] == value:
            out.append(s)
    return out


def _find_source_events(key, value, balance_id, amount):
    """repo.go FindSourceEventByUTR / ByGrn / ByCms: <key> AND balance_id AND amount."""
    with LOCK:
        rows = list(STATE["source_events"].values())
    out = []
    for e in rows:
        if e["balance_id"] != balance_id or int(e["amount"]) != int(amount):
            continue
        if key == KEY_UTR and e["utr"] == value:
            out.append(e)
        elif key == KEY_GRN and e["gateway_reference_number"].upper() == value.upper():
            out.append(e)
        elif key == KEY_CMS and e["cms_reference_number"] == value:
            out.append(e)
    return out


def fetch_entity_for_enrichment(entity, utr, grn, cms, account_number=None, balance_id=None, amount=0):
    """service.go FetchEntityForEnrichment: identifiers tried in order; first non-empty key that
    returns >=1 row wins. Returns (rows, key_used)."""
    for key, value in ((KEY_UTR, utr), (KEY_GRN, grn), (KEY_CMS, cms)):
        if not value:
            continue
        if entity == "account_statement":
            rows = _find_statements(key, value, account_number, amount)
        else:
            rows = _find_source_events(key, value, balance_id, amount)
        if rows:
            return rows, key
    return [], None


# ---------------------------------------------------------------------------
# enrichment
# ---------------------------------------------------------------------------
def _enrich_statement_with_event(event, stmt):
    """enrichers/payout_enricher.go EnrichStatement. Returns (changed, decision)."""
    if stmt["entity_type"] and stmt["entity_type"] != ENTITY_EXTERNAL and stmt["entity_id"] != event["entity_id"]:
        _audit("StatementAlreadyLinkedError", statement_id=stmt["id"], entity_type=stmt["entity_type"],
               existing_entity_id=stmt["entity_id"], new_entity_id=event["entity_id"], source_event_id=event["id"])
        return False, "refused_relink"
    same_entity = stmt["entity_id"] == event["entity_id"]
    if same_entity:
        _audit("StatementAlreadyLinkedToSameEntity", statement_id=stmt["id"], entity_id=stmt["entity_id"], source_event_id=event["id"])
    old_type, old_entity = stmt["entity_type"], stmt["entity_id"]
    new_type = ENTITY_PAYOUT_REVERSAL if stmt["type"] == "credit" else ENTITY_PAYOUT
    with LOCK:
        stmt["entity_type"] = new_type
        stmt["entity_id"] = event["entity_id"]
        stmt["updated_at"] = _now()
    changed = (old_type, old_entity) != (new_type, event["entity_id"])
    _audit("PayoutSourceEnrichmentComplete", statement_id=stmt["id"], source_event_id=event["id"], entity_type=new_type,
           entity_id=event["entity_id"], changed=changed, converted_from_external=(old_type == ENTITY_EXTERNAL and changed))
    # CDC dual_write.Update: only when entity_type is payout|payout_reversal. Kafka CDC not reproduced:
    # called synchronously here. An unchanged row produces no CDC change event -> no outbound.
    if changed:
        dual_write_update(stmt, old_type)
    else:
        with LOCK:
            STATE["outbound"].append({"at": time.time(), "statement_id": stmt["id"], "skipped": "row_unchanged_no_cdc_event"})
    return changed, ("linked" if changed else "unchanged")


def mark_external(stmt):
    with LOCK:
        stmt["entity_type"] = ENTITY_EXTERNAL
        stmt["updated_at"] = _now()
    _audit("EnrichedAsExternal", statement_id=stmt["id"], type=stmt["type"])


def enrich_with_statement(statement_id):
    """service.go EnrichWithStatement (statement arrives after / without the event)."""
    with LOCK:
        stmt = STATE["statements"].get(statement_id)
    if not stmt:
        return {"error": "StatementFetchError", "id": statement_id}
    account = account_by_number(stmt["account_number"], stmt["channel"])
    if not account:
        _audit("AccountFetchFailed", statement_id=statement_id, account_number=stmt["account_number"], channel=stmt["channel"])
        return {"error": "AccountFetchFailed", "id": statement_id}
    events, key = fetch_entity_for_enrichment("source_event", stmt["utr"], stmt["gateway_ref_number"], stmt["bank_transaction_id"],
                                              balance_id=account["balance_id"], amount=stmt["amount"])
    if not events:
        mark_external(stmt)
        return {"statement_id": statement_id, "result": "external"}
    changed, decision = _enrich_statement_with_event(events[0], stmt)
    return {"statement_id": statement_id, "result": decision, "matched_key": key, "source_event_id": events[0]["id"]}


def enrich_with_source(event):
    """service.go EnrichWithSource (event arrives; look for statements)."""
    accounts = accounts_by_balance(event["balance_id"])
    if not accounts:
        _audit("AccountNotFound", source_event_id=event["id"], balance_id=event["balance_id"])
        return {"source_event_id": event["id"], "result": "account_not_found"}
    acc = accounts[0]
    stmts, key = fetch_entity_for_enrichment("account_statement", event["utr"], event["gateway_reference_number"],
                                             event["cms_reference_number"], account_number=acc["account_number"], amount=event["amount"])
    if not stmts:
        _audit("StatementNotFound", source_event_id=event["id"], utr=event["utr"])
        return {"source_event_id": event["id"], "result": "statement_not_found"}
    if len(stmts) > 2:
        _audit("TooManyStmtsForUtr", source_event_id=event["id"], count=len(stmts), matched_key=key)
        return {"source_event_id": event["id"], "result": "too_many_statements_noop", "count": len(stmts)}
    if len(stmts) == 2 and stmts[0]["type"] == stmts[1]["type"]:
        _audit("UnexpectedStmtType", source_event_id=event["id"], statement_ids=[s["id"] for s in stmts], type=stmts[0]["type"])
        return {"source_event_id": event["id"], "result": "dual_same_type_noop"}
    results = []
    for s in stmts:
        changed, decision = _enrich_statement_with_event(event, s)
        results.append({"statement_id": s["id"], "decision": decision})
    return {"source_event_id": event["id"], "result": "enriched", "matched_key": key, "statements": results}


# ---------------------------------------------------------------------------
# source events
# ---------------------------------------------------------------------------
def handle_source_event(raw, transport):
    """source_events/service.go Handle: validate, persist (UNIQUE(event_id, entity_type)), enrich."""
    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError("not an object")
    except (ValueError, TypeError) as exc:
        _audit("SourceEventDataError", transport=transport, error=str(exc)[:200])
        return {"result": "invalid_json"}
    # XAS dto.go reads `event_create_timestamp`; PS emits `event_created_timestamp` (job file :41).
    has_ps_spelling = "event_created_timestamp" in body
    has_xas_spelling = "event_create_timestamp" in body
    event = {
        "entity_id": str(body.get("entity_id") or ""),
        "entity_type": str(body.get("entity_type") or ""),
        "utr": str(body.get("utr") or ""),
        "event_create_timestamp": int(body.get("event_create_timestamp") or 0),
        "event_id": str(body.get("event_id") or ""),
        "gateway_reference_number": str(body.get("gateway_ref_no") or ""),
        "cms_reference_number": str(body.get("cms_ref_no") or ""),
        "status": str(body.get("status") or ""),
        "mode": str(body.get("mode") or ""),
        "amount": int(body.get("amount") or 0),
        "balance_id": str(body.get("balance_id") or ""),
        "_schema": {"event_created_timestamp_present": has_ps_spelling, "event_create_timestamp_present": has_xas_spelling,
                    "event_created_timestamp_value": body.get("event_created_timestamp"),
                    "note": "XAS persists event_create_timestamp only; PS field name differs -> 0 stored (production mismatch reproduced)"},
        "_transport": transport,
    }
    _log("source_event schema: event_created_timestamp(PS)=%s event_create_timestamp(XAS)=%s -> stored event_create_timestamp=%d"
         % (has_ps_spelling, has_xas_spelling, event["event_create_timestamp"]))
    if not (event["utr"] or event["gateway_reference_number"] or event["cms_reference_number"]):
        _audit("SourceEventInvalid", entity_id=event["entity_id"], event_id=event["event_id"])
        return {"result": "invalid_no_reference", "event_id": event["event_id"]}
    key = (event["event_id"], event["entity_type"])
    with LOCK:
        existing = STATE["source_events"].get(key)
        if existing is None:
            event["id"] = _new_id("XASSE")
            event["created_at"] = _now()
            event["updated_at"] = _now()
            event["_replays"] = 0
            STATE["source_events"][key] = event
            stored, duplicate = event, False
        else:
            existing["_replays"] += 1
            stored, duplicate = existing, True
    if duplicate:
        _audit("SourceEventDuplicateUsingStoredRow", event_id=key[0], entity_type=key[1], stored_id=stored["id"], replays=stored["_replays"])
    enrich = enrich_with_source(stored)
    return {"result": "handled", "duplicate": duplicate, "stored_id": stored["id"], "enrichment": enrich}


def _consumer_loop():
    st = STATE["consumer"]
    st["started"] = True
    while True:
        try:
            msgs = awslite.sqs_receive(SOURCE_EVENT_QUEUE, wait_seconds=POLL_WAIT_SECONDS, endpoint=SQS_ENDPOINT)
            st["last_poll_at"] = _now()
        except Exception as exc:  # noqa: BLE001
            st["errors"] += 1
            st["last_error"] = str(exc)[:300]
            time.sleep(3)
            continue
        for m in msgs:
            st["received"] += 1
            try:
                handle_source_event(m.get("Body") or "", "sqs:" + SOURCE_EVENT_QUEUE)
            except Exception as exc:  # noqa: BLE001
                st["errors"] += 1
                st["last_error"] = "handle: %s" % str(exc)[:300]
            try:
                awslite.sqs_delete(SOURCE_EVENT_QUEUE, m.get("ReceiptHandle"), endpoint=SQS_ENDPOINT)
                st["deleted"] += 1
            except Exception as exc:  # noqa: BLE001
                st["errors"] += 1
                st["last_error"] = "delete: %s" % str(exc)[:300]


def _ensure_queue_and_start():
    for attempt in range(60):
        try:
            _, created = awslite.sqs_ensure_queue(SOURCE_EVENT_QUEUE, endpoint=SQS_ENDPOINT)
            _log("queue %s %s" % (SOURCE_EVENT_QUEUE, "created" if created else "present"))
            break
        except Exception as exc:  # noqa: BLE001
            _log("queue ensure failed (attempt %d): %s" % (attempt, str(exc)[:200]))
            time.sleep(2)
    if CONSUME:
        t = threading.Thread(target=_consumer_loop, name="xas-source-event-consumer", daemon=True)
        t.start()


# ---------------------------------------------------------------------------
# outbound: dual_write -> monolith payout_update
# ---------------------------------------------------------------------------
def _api_auth_header():
    try:
        with open(API_AUTH_FILE) as f:
            pair = f.read().strip()
    except OSError:
        return None
    import base64
    return "Basic " + base64.b64encode(pair.encode()).decode()


def enrichment_update_request(stmt, old_entity_type=""):
    """dual_write/service.go getSourceUpdateInput -> gateway/api/params.go EnrichmentUpdateRequest."""
    return {
        "bas_id": stmt["id"],
        "entity_id": stmt["entity_id"],
        "entity_type": stmt["entity_type"],
        "merchant_id": stmt["merchant_id"],
        "transaction_date": int(stmt.get("transaction_date") or 0),
        "converted_from_external": bool(old_entity_type == ENTITY_EXTERNAL and stmt["entity_type"] != old_entity_type),
        "utr": stmt["utr"],
        "grn": stmt["gateway_ref_number"],
        "cms_ref_no": stmt["bank_transaction_id"],
    }


def _post_enrichment_update(body, trigger):
    url = API_BASE.rstrip("/") + "/v1/banking_account_statement/payout_update"
    headers = {"Content-Type": "application/json"}
    auth = _api_auth_header()
    if auth:
        headers["Authorization"] = auth
    rec = {"at": time.time(), "trigger": trigger, "url": url, "request": body, "status": None, "response": None}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            rec["status"] = resp.status
            rec["response"] = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        rec["status"] = exc.code
        try:
            rec["response"] = json.loads(exc.read() or b"{}")
        except Exception:  # noqa: BLE001
            rec["response"] = {}
    except Exception as exc:  # noqa: BLE001
        rec["status"] = "ERR"
        rec["response"] = {"error_type": type(exc).__name__}
    with LOCK:
        STATE["outbound"].append(rec)
        del STATE["outbound"][:-2000]
    _log("EnrichmentUpdate %s bas_id=%s entity_type=%s -> %s" % (trigger, body["bas_id"], body["entity_type"], rec["status"]))
    return rec


def dual_write_update(stmt, old_entity_type):
    if stmt["entity_type"] not in (ENTITY_PAYOUT, ENTITY_PAYOUT_REVERSAL):
        return None
    return _post_enrichment_update(enrichment_update_request(stmt, old_entity_type), "cdc_update")


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------
def _json(body):
    try:
        return json.loads(body or b"{}")
    except (ValueError, TypeError):
        return None


def _bad(desc, code="BAD_REQUEST_ERROR"):
    # Foundation/grpc-gateway style error object (fts providers/xas/base.go ErrorObject)
    return 400, {"error": {"code": code, "description": desc, "step": "", "reason": "validation_failure", "field": "", "source": "xas",
                           "metadata": {}, "action": ""}}


def _stmt_public(s):
    """AccountStatementData with proto int64 fields as JSON strings (grpc-gateway)."""
    return {
        "id": s["id"], "entity_id": s["entity_id"], "entity_type": s["entity_type"], "channel": s["channel"],
        "merchant_id": s["merchant_id"], "account_number": s["account_number"], "bank_transaction_id": s["bank_transaction_id"],
        "type": s["type"], "utr": s["utr"], "amount": str(int(s["amount"])), "currency": s["currency"],
        "description": s["description"], "category": s["category"], "bank_serial_number": s["bank_serial_number"],
        "balance": str(int(s.get("balance") or 0)), "balance_currency": s["balance_currency"],
        "transaction_date": str(int(s.get("transaction_date") or 0)), "posted_date": str(int(s.get("posted_date") or 0)),
        "gateway_ref_number": s["gateway_ref_number"], "created_at": str(int(s["created_at"])), "updated_at": str(int(s["updated_at"])),
        "fetched_at_nano": str(int(s.get("fetched_at_nano") or 0)),
    }


def _fetch_multiple_by_reference_numbers(handler, body):
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    g = lambda k: (q.get(k) or [None])[0]  # noqa: E731
    utr, grn, cms = g("utr"), g("gateway_reference_number"), g("cms_reference_number")
    req = {"type": g("type") or "", "account_number": g("account_number") or "", "amount": g("amount"), "channel": g("channel") or "",
           "utr": utr, "gateway_reference_number": grn, "cms_reference_number": cms}
    if utr is None and grn is None and cms is None:
        return _bad("at least one reference number (utr, gateway_reference_number, or cms_reference_number) must be provided")
    try:
        amount = int(req["amount"])
    except (TypeError, ValueError):
        amount = 0
    if not req["account_number"] or not amount or not req["channel"]:
        return _bad("account_number: cannot be blank; amount: cannot be blank; channel: cannot be blank.")
    rows, key = fetch_entity_for_enrichment("account_statement", utr or "", grn or "", cms or "",
                                            account_number=req["account_number"], amount=amount)
    filtered = [s for s in rows if (not req["type"] or s["type"] == req["type"]) and s["channel"] == req["channel"]]
    resp = {"statements": [_stmt_public(s) for s in filtered], "page": 0, "limit": 0, "total_pages": 0,
            "total_count": str(len(rows)), "has_more": False}
    with LOCK:
        STATE["fetch_log"].append({"at": time.time(), "caller": handler.headers.get("User-Agent", ""),
                                   "auth_user": _basic_user(handler), "request": req, "matched_key": key,
                                   "returned": len(filtered), "statement_ids": [s["id"] for s in filtered],
                                   "entity_types": [s["entity_type"] for s in filtered]})
        del STATE["fetch_log"][:-500]
    return 200, resp


def _basic_user(handler):
    h = handler.headers.get("Authorization", "")
    if h.startswith("Basic "):
        try:
            import base64
            return base64.b64decode(h[6:]).decode().split(":", 1)[0]
        except Exception:  # noqa: BLE001
            return "?"
    return None


def _get_account_statements(handler, body):
    """GET /v1/account_statements?account_number&channel&... minimal listing (harness convenience)."""
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    with LOCK:
        rows = list(STATE["statements"].values())
    for k in ("account_number", "channel", "merchant_id", "type", "entity_type", "utr"):
        v = (q.get(k) or [None])[0]
        if v is not None:
            rows = [s for s in rows if s.get(k) == v]
    return 200, {"statements": [_stmt_public(s) for s in rows], "page": 1, "limit": len(rows), "total_pages": 1,
                 "total_count": str(len(rows)), "has_more": False}


def _statement_dual_write(handler, body):
    """POST /v1/statement/dual_write {id}: DualWriteForStatement -> EnrichmentUpdate unconditionally."""
    req = _json(body)
    if not req or not req.get("id"):
        return _bad("id: cannot be blank.")
    with LOCK:
        stmt = STATE["statements"].get(req["id"])
    if not stmt:
        return 404, {"error": {"code": "NOT_FOUND", "description": "statement not found", "source": "xas"}}
    rec = _post_enrichment_update(enrichment_update_request(stmt, ""), "dual_write_api")
    return 200, {"status": "success" if rec["status"] == 200 else "failed", "relay_status": rec["status"]}


def _enrich_statements(handler, body):
    """POST /v1/statement/enrich_statements {id}: EnrichStatement (re-run enrichment when unlinked/external)."""
    req = _json(body)
    if not req or not req.get("id"):
        return _bad("id: cannot be blank.")
    with LOCK:
        stmt = STATE["statements"].get(req["id"])
    if not stmt:
        return 404, {"error": {"code": "NOT_FOUND", "description": "statement not found", "source": "xas"}}
    if stmt["entity_type"] in ("", ENTITY_EXTERNAL):
        return 200, {"status": "success", "enrichment": enrich_with_statement(stmt["id"])}
    return 200, {"status": "success", "enrichment": {"result": "already_linked", "entity_type": stmt["entity_type"]}}


def _source_event_fetch(handler, body):
    """POST /v1/source_event/fetch {source, entity_ids}: pull fallback. The monolith route
    /v1/payouts_internal/{id}/source_event_info is not served by monolith-stub -> reported, not faked."""
    req = _json(body) or {}
    return 501, {"error": {"code": "NOT_IMPLEMENTED", "description": "source_event pull fallback (monolith source_event_info) not modelled",
                           "source": "xas", "metadata": {"entity_ids": req.get("entity_ids", [])}}}


# ---- arena control plane ----
def _arena_accounts(handler, body):
    req = _json(body)
    if not req:
        return _bad("invalid JSON")
    try:
        rec, existed = upsert_account(req)
    except ValueError as exc:
        return _bad(str(exc))
    return 200, {"account": rec, "existed": existed}


def _arena_statements_sync(handler, body):
    """POST /_arena/statements/sync {statements:[<PS banking_account_statement rows>], label?, enrich?}
    Mirrors REAL PS rows into the XAS store (XAS's fetch pipeline persists the same shape), applies the
    XAS dedupe tuple, then runs statement-driven enrichment for each NEW row (fetch service enqueues
    one statement_enrichment job per saved row). Optional balance_id per row registers the account."""
    req = _json(body)
    if not req or not isinstance(req.get("statements"), list):
        return _bad("statements: list required")
    label = req.get("label") or "substitute_injected"
    do_enrich = req.get("enrich", True)
    out = []
    for row in req["statements"]:
        try:
            if row.get("balance_id") and row.get("merchant_id"):
                upsert_account({"account_number": row["account_number"], "channel": row["channel"], "balance_id": row["balance_id"],
                                "merchant_id": row["merchant_id"]})
            rec, existed = ingest_statement(row, label)
        except (ValueError, TypeError) as exc:
            out.append({"error": str(exc), "row": row})
            continue
        item = {"id": rec["id"], "existed": existed}
        if do_enrich and not existed and rec["entity_type"] in ("", ENTITY_EXTERNAL):
            item["enrichment"] = enrich_with_statement(rec["id"])
        out.append(item)
    return 200, {"results": out}


def _arena_source_events_enqueue(handler, body):
    """POST /_arena/source_events/enqueue <PS XasEventDetails JSON>: SendMessage to the REAL queue so the
    consumer path is exercised (label substitute_injected; use when the PS producer gate is off)."""
    req = _json(body)
    if not req:
        return _bad("invalid JSON")
    try:
        resp = awslite.sqs_send(SOURCE_EVENT_QUEUE, json.dumps(req), endpoint=SQS_ENDPOINT)
    except Exception as exc:  # noqa: BLE001
        return 502, {"error": {"code": "QUEUE_UNAVAILABLE", "description": str(exc)[:300]}}
    return 200, {"queue": SOURCE_EVENT_QUEUE, "message_id": resp.get("MessageId"), "label": "substitute_injected"}


def _arena_source_events_direct(handler, body):
    """POST /_arena/source_events: handle an event in-process (bypasses SQS)."""
    return 200, handle_source_event(body.decode("utf-8", "replace"), "direct")


def _arena_state(handler, body):
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    mid = (q.get("merchant_id") or [None])[0]
    with LOCK:
        stmts = [s for s in STATE["statements"].values() if not mid or s["merchant_id"] == mid]
        accts = [a for a in STATE["accounts"].values() if not mid or a["merchant_id"] == mid]
        bal_ids = {a["balance_id"] for a in accts}
        events = [e for e in STATE["source_events"].values() if not mid or e["balance_id"] in bal_ids]
        return 200, {"accounts": accts, "statements": stmts, "source_events": events, "links": list(STATE["links"]),
                     "outbound": list(STATE["outbound"]), "fetch_log": list(STATE["fetch_log"]),
                     "consumer": dict(STATE["consumer"]), "started_at": STATE["started_at"]}


def _arena_reset(handler, body):
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    mid = (q.get("merchant_id") or [None])[0]
    if not mid:
        return _bad("merchant_id query parameter required (per-merchant reset only)")
    with LOCK:
        accts = {k: a for k, a in STATE["accounts"].items() if a["merchant_id"] == mid}
        bal_ids = {a["balance_id"] for a in accts.values()}
        n_s = [k for k, s in STATE["statements"].items() if s["merchant_id"] == mid]
        n_e = [k for k, e in STATE["source_events"].items() if e["balance_id"] in bal_ids]
        for k in n_s:
            del STATE["statements"][k]
        for k in n_e:
            del STATE["source_events"][k]
        for k in accts:
            del STATE["accounts"][k]
    return 200, {"merchant_id": mid, "removed": {"accounts": len(accts), "statements": len(n_s), "source_events": len(n_e)}}


ROUTES = {
    ("GET", "/v1/account_statements/fetch_multiple_by_reference_numbers"): _fetch_multiple_by_reference_numbers,
    ("GET", "/v1/account_statements"): _get_account_statements,
    ("POST", "/v1/statement/dual_write"): _statement_dual_write,
    ("POST", "/v1/statement/enrich_statements"): _enrich_statements,
    ("POST", "/v1/source_event/fetch"): _source_event_fetch,
    ("POST", "/_arena/accounts"): _arena_accounts,
    ("POST", "/_arena/statements/sync"): _arena_statements_sync,
    ("POST", "/_arena/source_events/enqueue"): _arena_source_events_enqueue,
    ("POST", "/_arena/source_events"): _arena_source_events_direct,
    ("GET", "/_arena/state"): _arena_state,
    ("POST", "/_arena/reset"): _arena_reset,
}

if __name__ == "__main__":
    _ensure_queue_and_start()
    serve(ROUTES)
