#!/usr/bin/env python3
"""mozart-sim: lightweight stdlib fallback for Mozart. See CONTRACT.md.

Route shape /{namespace}/{gateway}/{version}/{action}. base_stub's dispatch
matches on a fixed (METHOD, path_prefix) table, which doesn't fit a 4-segment
wildcard route -- this stub therefore does its own lightweight routing here
rather than importing base_stub.serve()'s ROUTES dispatch, but reuses its
auth/logging/health conventions directly for consistency.
"""
import json
import os
import sys
import time
import threading
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "/app")
from _common.base_stub import _log, StubHandler  # noqa: E402

LISTEN_PORT = int(os.environ.get("STUB_PORT", "8085"))
POLL_TO_SUCCESS_AFTER = int(os.environ.get("MOZART_SIM_POLL_TO_SUCCESS_AFTER", "2"))

STATUS_STATE = {}  # gateway_ref_no -> poll_count
SCENARIOS = {}  # merchant_id, attempt id or payout/source id -> explicit scenario
ATTEMPTS = {}   # attempt id -> chosen scenario and poll count
SCENARIO_LOCK = threading.RLock()
SCENARIO_NAMES = {"success", "failure", "insufficient_funds", "hold", "delayed_success", "returned", "ambiguous_with_utr", "ambiguous_without_utr", "duplicate", "timeout"}
EVENTS = []


def _scenario_control(body):
    key = str(body.get("key") or body.get("merchant_id") or body.get("payout_id") or "")
    if not key or len(key) > 100:
        return 400, {"error": "key required"}
    with SCENARIO_LOCK:
        if body.get("clear"):
            SCENARIOS.pop(key, None)
            ATTEMPTS.pop(key, None)
            return 200, {"cleared": key}
        scenario = body.get("scenario")
        polls = body.get("polls", 2)
        if scenario not in SCENARIO_NAMES or not isinstance(polls, int) or not 1 <= polls <= 100:
            return 400, {"error": "invalid scenario or polls"}
        rule = {"scenario": scenario, "polls": polls}
        SCENARIOS[key] = rule
        if key in ATTEMPTS:
            ATTEMPTS[key].update(rule)
            ATTEMPTS[key]["count"] = 0
        return 200, {"key": key, **rule}


def _controlled(action, body):
    attempt = _dig(body, "entities", "attempt", default={})
    aid = str(attempt.get("id") or attempt.get("gateway_ref_no") or "")
    keys = [str(attempt.get(k) or "") for k in ("id", "source_id", "merchant_id")]
    with SCENARIO_LOCK:
        rule = next((SCENARIOS[k] for k in keys if k in SCENARIOS), None)
        if action == "transfer_init" and rule:
            ATTEMPTS[aid] = {**rule, "count": 0, "merchant_id": attempt.get("merchant_id")}
        state = ATTEMPTS.get(aid)
        if state is None: return None
        scenario = state["scenario"]
        if action == "transfer_status": state["count"] += 1
        count, polls = state["count"], state["polls"]
        EVENTS.append({"action":action,"attempt_id":aid,"scenario":scenario,"poll":count,"at":time.time()})
        del EVENTS[:-1000]
    utr = "ARENA" + aid.zfill(10)[-10:]
    pending = {"gateway_ref_no": attempt.get("gateway_ref_no") or aid, "bank_status_code": "INITIATED"}
    if scenario == "timeout":
        time.sleep(30)
        # A gateway timeout has no valid Mozart success/error envelope.
        # FTS createEmptyResponse maps it to pending MOZART_INDETERMINATE.
        return 504, {}
    if scenario == "hold": return 200, _envelope(pending)
    if scenario == "delayed_success" and (action == "transfer_init" or count < polls):
        return 200, _envelope(pending)
    if scenario == "returned":
        # A success is terminal for normal FTS polling: explicit status check
        # is needed later to observe a return. Never invent an automatic poll.
        if action == "transfer_init" or count < polls:
            return 200, _envelope({"bank_status_code":"SUCCESS","utr":utr,"is_debited":True,"is_credited":True})
        return 200, _envelope({"bank_status_code":"RETURNED","return_utr":"RET"+utr}, False,
                             {"gateway_error_code":"RETURNED","internal_error_code":"RETURNED"})
    if scenario in ("failure", "insufficient_funds", "duplicate"):
        # FTS mozart/error_code.go: INVALID_ACCOUNT_NUMBER is terminal MERCHANT;
        # INSUFFICIENT_FUND is retryable INTERNAL for Shared accounts.
        code = {"failure":"INVALID_ACCOUNT_NUMBER", "insufficient_funds":"INSUFFICIENT_FUND",
                "duplicate":"DUPLICATE_TXN"}[scenario]
        return 200, _envelope({"bank_status_code":code,"is_debited":False,"is_credited":False},False,
            {"description":"synthetic bank "+scenario,"gateway_error_code":code,"gateway_error_description":"synthetic "+scenario,"gateway_status_code":200,"internal_error_code":code})
    if scenario.startswith("ambiguous_"):
        data = {"bank_status_code":"CBS:188","is_debited":None,"is_credited":None}
        if scenario.endswith("with_utr"): data["utr"] = utr
        return 200, _envelope(data,False,{"description":"synthetic ambiguous response","gateway_error_code":"CBS:188","internal_error_code":"TECHNICAL_ERROR_AMBIGUOUS"})
    return 200, _envelope({"bank_status_code":"SUCCESS","utr":utr,"is_debited":True,"is_credited":True})

ENVELOPE_DEFAULTS = {"error": None, "external_trace_id": "SIM_TRACE_ID", "mozart_id": "SIM_MOZART_ID",
                     "success": True, "next": {}}


def _envelope(data, success=True, error=None):
    env = dict(ENVELOPE_DEFAULTS)
    env["data"] = data
    env["success"] = success
    env["error"] = error
    return env


def _dig(body, *path, default=None):
    cur = body
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def _amount(body):
    v = _dig(body, "entities", "attempt", "amount")
    if v is None:
        v = body.get("amount")
    try:
        # FTS SetAttempt converts paise to a decimal rupee string.
        # Standalone top-level amount is explicitly paise for legacy fixtures.
        return int(Decimal(str(v)) * (100 if _dig(body, "entities", "attempt", "amount") is not None else 1))
    except (TypeError, ValueError, InvalidOperation):
        return None


def _account_suffix(body):
    acc = _dig(body, "entities", "fund_account", "bank_account", "account_number")
    if acc is None:
        acc = body.get("account_number", "")
    acc = str(acc)
    return acc[-2:] if len(acc) >= 2 else acc


def _gateway_ref(body):
    v = _dig(body, "entities", "attempt", "gateway_ref_no")
    if v is None:
        v = body.get("gateway_ref_no", "")
    return v


def _transfer_init(body):
    amt = _amount(body)
    # --- grounded scenario table, findings/25_stork_mozart.md §C + real
    # mozart/app/testdata fixture precedent, seeds/mozart_scenarios.json ---
    if amt == 100100:
        return 200, _envelope({"gateway_ref_no": "ARENAUTR0000001", "bank_status_code": "INITIATED"})
    if amt == 100200:
        return 200, _envelope(
            {"bank_status_code": "INSUFFICIENT_FUND", "utr": None, "is_credited": False, "is_debited": False,
             "remarks": "Insufficient balance in remitter account"},
            success=False,
            error={"description": "Insufficient balance in remitter account", "gateway_error_code": "INSUFFICIENT_FUND",
                   "gateway_error_description": "Insufficient balance in remitter account",
                   "gateway_status_code": 200, "internal_error_code": "INSUFFICIENT_FUND"})
    if amt == 100300:
        return 200, _envelope({"gateway_ref_no": "ARENAUTR0000003", "bank_status_code": "INITIATED"})
    if amt == 100400:
        return 200, _envelope({"gateway_ref_no": "ARENAUTR0000004", "bank_status_code": "INITIATED"})
    if amt == 100500:
        return 200, _envelope(
            {"utr": None, "bank_status_code": "DUPLICATE_TXN"}, success=False,
            error={"gateway_error_code": "DUPLICATE_TXN", "gateway_error_description": "Duplicate transaction reference",
                   "internal_error_code": "DUPLICATE_TXN"})
    if amt == 100600:
        return 200, _envelope({"gateway_ref_no": "ARENAUTR0000006", "bank_status_code": "INITIATED"})
    # --- legacy/generic scenarios (pre-existing, kept for back-compat) ---
    if amt == 1:
        return 200, _envelope({"utr": "SIM_UTR_MIN", "bank_status_code": "SUCCESS"})
    if amt == 100:
        return 200, _envelope({"utr": "SIM_UTR_100", "bank_status_code": "SUCCESS"})
    if amt == 200000:
        return 200, _envelope({"utr": "SIM_UTR_MAX", "bank_status_code": "SUCCESS"})
    if amt == 200005:
        return 200, _envelope({"internal_error_code": "INVALID_AMT"}, success=False,
                               error={"description": "invalid amount"})
    if amt == 300:
        return 200, _envelope({"internal_error_code": "VALIDATION_ERROR"}, success=False,
                               error={"description": "validation error"})
    if amt == 800:
        return 200, _envelope({"internal_error_code": "GATEWAY_ERROR_UNKNOWN_ERROR"}, success=False,
                               error={"description": "gateway error"})
    return 200, _envelope({"utr": "SIM_UTR_GENERIC", "bank_status_code": "SUCCESS"})


def _transfer_status(body):
    amt = _amount(body)
    ref = _gateway_ref(body)
    # --- grounded scenario table, keyed on gateway_ref_no per
    # mozart_scenarios.json's scenario_selection_key (transfer_status keys
    # on entities.attempt.gateway_ref_no, not amount, matching real Mozart
    # -mock's -- mozart/app/mock/mappings.go -- matching field per action) ---
    if ref == "ARENAUTR0000001":
        return 200, _envelope({"bank_status_code": "SUCCESS", "utr": "ARENAUTR0000001",
                                "is_credited": True, "is_debited": True, "remarks": "Success"})
    if ref == "ARENAUTR0000003":
        count = STATUS_STATE.get(ref, 0) + 1
        STATUS_STATE[ref] = count
        if count < POLL_TO_SUCCESS_AFTER:
            return 200, _envelope(
                {"bank_status_code": "PENDING", "utr": None, "remarks": "Transaction is pending for Approval"},
                success=False,
                error={"description": "Awaiting Confirmation", "gateway_error_code": "PENDING",
                       "internal_error_code": "PENDING"})
        return 200, _envelope({"bank_status_code": "SUCCESS", "utr": "ARENAUTR0000003",
                                "is_credited": True, "is_debited": True, "remarks": "Success"})
    if ref == "ARENAUTR0000004":
        return 200, _envelope(
            {"bank_status_code": "CBS:188", "utr": "ARENAUTR0000004", "is_credited": None, "is_debited": None,
             "remarks": "Ambiguous response from core banking, manual reconciliation required"},
            success=False,
            error={"description": "Core banking ambiguous response", "gateway_error_code": "CBS:188",
                   "gateway_error_description": "Core banking ambiguous response",
                   "gateway_status_code": 200, "internal_error_code": "TECHNICAL_ERROR_AMBIGUOUS"})
    if ref == "ARENAUTR0000006":
        count = STATUS_STATE.get(ref, 0) + 1
        STATUS_STATE[ref] = count
        if count == 1:
            return 200, _envelope({"bank_status_code": "SUCCESS", "utr": "ARENAUTR0000006",
                                    "is_credited": True, "is_debited": True})
        return 200, _envelope(
            {"bank_status_code": "RETURNED", "return_utr": "ARENARETUTR000001",
             "remarks": "Beneficiary account closed, amount returned"},
            success=False,
            error={"gateway_error_code": "RETURNED", "internal_error_code": "TECHNICAL_ERROR_RETURNED"})
    # --- legacy/generic scenarios (pre-existing, kept for back-compat) ---
    if amt == 100:
        return 200, _envelope({"bank_status_code": "REJECTED"}, success=False,
                               error={"description": "rejected"})
    if amt == 500:
        return 200, _envelope({}, success=False, error={"description": "transaction not found"})
    if amt == 1000:
        return 200, _envelope({}, success=False, error={"description": "vault bad request",
                                                          "internal_error_code": "INTERNAL_SERVER_ERROR"})
    if amt == 2000:
        return 200, _envelope({"gateway_session": {"token": "SIM_SESSION", "token_type": "Bearer",
                                                     "validity_duration": "3600"}})
    if amt == 3000:
        return 200, _envelope({"gateway_auth": {"token": "SIM_AUTH", "token_type": "Bearer",
                                                  "validity_duration": "3600"}})
    if ref:
        count = STATUS_STATE.get(ref, 0) + 1
        STATUS_STATE[ref] = count
        if count < POLL_TO_SUCCESS_AFTER:
            return 200, _envelope({"bank_status_code": "PENDING"})
        return 200, _envelope({"utr": "SIM_UTR_" + str(ref), "bank_status_code": "SUCCESS"})
    return 200, _envelope({"utr": "SIM_UTR_GENERIC", "bank_status_code": "SUCCESS"})


def _beneficiary_register(body):
    suffix = _account_suffix(body)
    if suffix == "00":
        return 200, _envelope({}, success=False, error={"description": "duplicate", "gateway_error_code": "StatusBeneRecordAlreadyExists"})
    if suffix == "01":
        return 200, _envelope({}, success=False, error={"description": "failed"})
    return 200, _envelope({"beneficiary_code": "SIM_BENE_OK"})


def _beneficiary_verify(body):
    suffix = _account_suffix(body)
    if suffix == "01":
        return 200, _envelope({}, success=False, error={"description": "failed"})
    return 200, _envelope({"beneficiary_name": "SIM Verified Beneficiary"})


def _account_balance(body):
    return 200, _envelope({"accountBalanceAmount": "10000000"})


def _gateway_auth(body):
    return 200, _envelope({"gateway_auth": {"token": "SIM_AUTH", "token_type": "Bearer", "validity_duration": "3600"}})


def _gateway_session(body):
    return 200, _envelope({"gateway_session": {"token": "SIM_SESSION", "token_type": "Bearer", "validity_duration": "3600"}})


def _create_otp(body):
    return 200, _envelope({"otp_reference": "SIM_OTP_REF"})


# ---------------------------------------------------------------------------
# M4 (T10): razorpayx/rbl/v2/account_statement -- bank current-account statement
# fetch, consumed by the REAL payouts worker rbl_banking_account_statement
# (payouts internal/app/bankingAccountStatement/processor/rbl_gateway.go). The
# response is the Mozart envelope with data = {"FetchAccStmtRes": {"Header":
# {...}, "AccStmtData": {"File_Data": base64(CSV)}}} exactly as Mozart's
# razorpayx/rbl/v2/account_statement.json ResponseMapper emits it; the CSV
# header line and 9-column row shape are what rbl_gateway.go
# ExtractTxnsFromBankResonse/ParseBankTransaction require. Statement content is
# driven per account_number through the control plane POST/GET /_arena/statement.
# ---------------------------------------------------------------------------
import base64
import datetime

STATEMENTS = {}        # account_number -> {"queue": [row], "options": {...}, "served": [...], "requests": [...]}
STATEMENT_LOCK = threading.RLock()
STATEMENT_OPTIONS = {"no_records", "failure", "http_error", "next_key", "page_size", "duplicate", "sticky"}
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
RBL_CSV_HEADER = "TRAN_ID,PTSN_NUM,TRAN_DATE,PSTD_DATE,TRAN_TYPE,C/D,TRAN_PARTICULAR,TRAN_AMT,TRAN_BALANCE"


def _rupees(paise):
    paise = int(paise)
    sign = "-" if paise < 0 else ""
    paise = abs(paise)
    return "%s%d.%02d" % (sign, paise // 100, paise % 100)


def _normalize_statement_row(row, idx):
    """Accept a loose row dict and return the exact 9 CSV cells rbl_gateway.go parses.
    Amounts are given in paise (amount_paise/balance_paise) and rendered in rupees
    (utils.ConvertRupeesToPaise reverses it); dates default to 'now IST' with the
    posted date 120 s in the past so the RblTxnTsOffset (60 s) guard admits the row."""
    now = datetime.datetime.now(IST)
    posted_default = (now - datetime.timedelta(seconds=120)).strftime("%d-%m-%Y %H:%M:%S")
    cells = [
        str(row.get("tran_id") or ("S%010d" % (int(time.time()) % 10**10 + idx))),
        str(row.get("ptsn") or row.get("bank_serial_number") or idx + 1),
        str(row.get("tran_date") or now.strftime("%d-%m-%Y")),
        str(row.get("posted_date") or posted_default),
        str(row.get("tran_type") or "TCI"),
        str(row.get("cd") or ("C" if str(row.get("type", "debit")).lower() == "credit" else "D")),
        str(row.get("particulars") or row.get("description") or ""),
        _rupees(row.get("amount_paise", 0)) if "amount_paise" in row else str(row.get("amount", "0.00")),
        _rupees(row.get("balance_paise", 0)) if "balance_paise" in row else str(row.get("balance", "0.00")),
    ]
    for c in cells:
        if "," in c or "\n" in c:
            raise ValueError("statement cell must not contain ',' or newline: %r" % c)
    if not cells[6]:
        raise ValueError("particulars required (TRAN_PARTICULAR is validated as required by the parser)")
    return cells


def _statement_control(body):
    acct = str(body.get("account_number") or "")
    if not acct or len(acct) > 48:
        return 400, {"error": "account_number required"}
    with STATEMENT_LOCK:
        st = STATEMENTS.setdefault(acct, {"queue": [], "options": {}, "served": [], "requests": []})
        if body.get("clear"):
            STATEMENTS[acct] = {"queue": [], "options": {}, "served": [], "requests": []}
            return 200, {"cleared": acct}
        opts = body.get("options")
        if opts is not None:
            if not isinstance(opts, dict) or any(k not in STATEMENT_OPTIONS for k in opts):
                return 400, {"error": "invalid options", "allowed": sorted(STATEMENT_OPTIONS)}
            if body.get("replace_options", True):
                st["options"] = dict(opts)
            else:
                st["options"].update(opts)
        rows = body.get("rows")
        if rows is not None:
            if not isinstance(rows, list) or len(rows) > 5000:
                return 400, {"error": "rows must be a list (<=5000)"}
            try:
                cells = [_normalize_statement_row(r, len(st["queue"]) + i) for i, r in enumerate(rows)]
            except (ValueError, TypeError) as exc:
                return 400, {"error": str(exc)}
            if body.get("mode", "append") == "replace":
                st["queue"] = cells
            else:
                st["queue"].extend(cells)
        return 200, {"account_number": acct, "queued": len(st["queue"]), "options": st["options"]}


def _statement_inspect(query):
    acct = (query.get("account_number") or [""])[0]
    with STATEMENT_LOCK:
        if acct:
            st = STATEMENTS.get(acct)
            return (200, {"account_number": acct, **st}) if st else (404, {"error": "unknown account"})
        return 200, {k: {"queued": len(v["queue"]), "options": v["options"], "served": len(v["served"]),
                         "requests": len(v["requests"])} for k, v in STATEMENTS.items()}


def _account_statement(body):
    attempt = _dig(body, "entities", "attempt", default={}) or {}
    source = _dig(body, "entities", "source_account", default={}) or {}
    acct = str(source.get("account_number") or "")
    creds = source.get("credentials") or {}
    header = {"Code": 0, "Corp_ID": str(creds.get("corp_id") or ""), "Status": "Success", "Status_Desc": "",
              "TranID": str(attempt.get("id") or ""), "account_no": acct,
              "from_date": str(attempt.get("from_date") or ""), "to_date": str(attempt.get("to_date") or ""),
              "next_key": ""}
    with STATEMENT_LOCK:
        st = STATEMENTS.setdefault(acct, {"queue": [], "options": {}, "served": [], "requests": []})
        opts = st["options"]
        req_rec = {"at": time.time(), "attempt": attempt, "credentials_present": bool(creds.get("auth_username")),
                   "queued_before": len(st["queue"]), "outcome": None}
        st["requests"].append(req_rec)
        del st["requests"][:-200]
        if opts.get("http_error"):
            req_rec["outcome"] = "http_error"
            return int(opts["http_error"]), {}
        if opts.get("failure"):
            # Bank-side technical failure: Status=Failure with a non-"No Records" description. The PS parser
            # (UnmarshalBankResponse) does not treat this as no-records, Validate() then fails on the missing
            # File_Data and the worker logs BankingAccountStatementFetchRetriesExhausted (retry limit 0).
            req_rec["outcome"] = "failure"
            header.update({"Status": "Failure", "Status_Desc": "Technical Failure", "Code": 500})
            return 200, _envelope({"FetchAccStmtRes": {"Header": header, "AccStmtData": {"File_Data": ""}}},
                                  success=False, error={"description": "synthetic bank statement failure",
                                                        "gateway_error_code": "TECHNICAL_FAILURE",
                                                        "gateway_status_code": 500,
                                                        "internal_error_code": "GATEWAY_ERROR"})
        if opts.get("no_records") or not st["queue"]:
            # Real RBL semantics (Mozart success criterion accepts Status=Failure && Status_Desc=='No Records Found';
            # PS maps it to RblAccountStatementNoRecords). NEVER answer Success with zero rows: rbl_gateway.go
            # ParseBankResponse indexes records[len(records)-1] unconditionally when no row was skipped.
            req_rec["outcome"] = "no_records"
            header.update({"Status": "Failure", "Status_Desc": "No Records Found"})
            return 200, _envelope({"FetchAccStmtRes": {"Header": header, "AccStmtData": {"File_Data": ""}}})
        page = int(opts.get("page_size") or 0)
        rows = st["queue"][:page] if page > 0 else list(st["queue"])
        if not opts.get("sticky"):
            del st["queue"][:len(rows)]
        if opts.get("duplicate"):
            rows = rows + rows   # same bank row twice in one file -> exercises DeDuplicateTransactionsLocal
        remaining = len(st["queue"]) if not opts.get("sticky") else 0
        if opts.get("next_key"):
            header["next_key"] = str(opts["next_key"])
        elif page > 0 and remaining > 0:
            header["next_key"] = "MORE%d" % remaining
        st["served"].append({"at": time.time(), "attempt_id": header["TranID"], "rows": len(rows),
                             "next_key": header["next_key"], "request_next_key": attempt.get("next_key") or ""})
        del st["served"][:-200]
        req_rec["outcome"] = "rows:%d" % len(rows)
    # No trailing newline: the parser splits on "\n" and a trailing empty line is an invalid 9-column row.
    csv = "\n".join([RBL_CSV_HEADER] + [",".join(r) for r in rows])
    file_data = base64.b64encode(csv.encode("utf-8")).decode("ascii")
    return 200, _envelope({"FetchAccStmtRes": {"Header": header, "AccStmtData": {"File_Data": file_data}}})


ACTION_HANDLERS = {
    "transfer_init": _transfer_init,
    "transfer_status": _transfer_status,
    "gateway_auth": _gateway_auth,
    "gateway_session": _gateway_session,
    "beneficiary_verify": _beneficiary_verify,
    "beneficiary_register": _beneficiary_register,
    "account_balance": _account_balance,
    "create_otp": _create_otp,
    "account_statement": _account_statement,
}


class MozartSimHandler(StubHandler):
    def do_GET(self):
        if self.path == "/_arena/scenarios":
            if not self._authorized():
                self._send_json(401, {"error":"unauthorized"}); return
            with SCENARIO_LOCK:
                self._send_json(200, {"rules":SCENARIOS,"attempts":ATTEMPTS,"events":EVENTS})
            return
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "service": "mozart-sim"})
            return
        if self.path.startswith("/_arena/statement"):
            if not self._authorized():
                self._send_json(401, {"error":"unauthorized"}); return
            from urllib.parse import parse_qs, urlparse
            status, payload = _statement_inspect(parse_qs(urlparse(self.path).query))
            self._send_json(status, payload)
            return
        self._send_json(404, {"error": "unrecognized_path", "path": self.path})

    def do_POST(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "service": "mozart-sim"})
            return
        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
            return

        if self.path == "/_arena/scenario":
            status, payload = _scenario_control(body)
            self._send_json(status, payload)
            return
        if self.path == "/_arena/statement":
            status, payload = _statement_control(body)
            self._send_json(status, payload)
            return
        parts = [p for p in self.path.split("/") if p]
        if len(parts) != 4:
            self._send_json(404, {"error": "unrecognized_path", "path": self.path,
                                   "expected": "/{namespace}/{gateway}/{version}/{action}"})
            return
        _namespace, _gateway, _version, action = parts
        handler = ACTION_HANDLERS.get(action)
        if handler is None:
            self._send_json(404, {"error": "unknown_action", "action": action})
            return
        controlled = _controlled(action, body) if action in ("transfer_init", "transfer_status") else None
        status, payload = controlled if controlled is not None else handler(body)
        self._send_json(status, payload)


def serve():
    addr = ("0.0.0.0", LISTEN_PORT)
    httpd = ThreadingHTTPServer(addr, MozartSimHandler)
    _log("mozart-sim listening on %s" % str(addr))
    httpd.serve_forever()


if __name__ == "__main__":
    serve()
