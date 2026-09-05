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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "/app")
from _common.base_stub import _log, StubHandler  # noqa: E402

LISTEN_PORT = int(os.environ.get("STUB_PORT", "8085"))
POLL_TO_SUCCESS_AFTER = int(os.environ.get("MOZART_SIM_POLL_TO_SUCCESS_AFTER", "2"))

STATUS_STATE = {}  # gateway_ref_no -> poll_count

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
        return int(v)
    except (TypeError, ValueError):
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


ACTION_HANDLERS = {
    "transfer_init": _transfer_init,
    "transfer_status": _transfer_status,
    "gateway_auth": _gateway_auth,
    "gateway_session": _gateway_session,
    "beneficiary_verify": _beneficiary_verify,
    "beneficiary_register": _beneficiary_register,
    "account_balance": _account_balance,
    "create_otp": _create_otp,
}


class MozartSimHandler(StubHandler):
    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "service": "mozart-sim"})
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
        status, payload = handler(body)
        self._send_json(status, payload)


def serve():
    addr = ("0.0.0.0", LISTEN_PORT)
    httpd = ThreadingHTTPServer(addr, MozartSimHandler)
    _log("mozart-sim listening on %s" % str(addr))
    httpd.serve_forever()


if __name__ == "__main__":
    serve()
