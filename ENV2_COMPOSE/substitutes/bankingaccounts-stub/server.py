#!/usr/bin/env python3
"""bankingaccounts-stub: minimal facade for the Banking Accounts service as payouts-api calls it
(payouts/pkg/bankingAccountService). Only the route the Direct-account (M2/RBL) create path needs
today; extend per CONTRACT.md."""
import json
import os
import sys
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, "/app")
from _common.base_stub import serve  # noqa: E402

MERCHANTS_FILE = os.environ.get("MONOLITH_MERCHANTS_FILE", "/app/seed/merchants.json")


def _load_merchants():
    try:
        with open(MERCHANTS_FILE) as f:
            return json.load(f).get("merchants", {})
    except (OSError, ValueError):
        return {}


MERCHANTS = _load_merchants()
_MERCHANTS_MTIME = None


def _merchants():
    """M4 (T10): re-read merchants.json when its mtime changes so a provisioner that appends a merchant
    (T07 note: the file was read once at import -> 404 until restart) no longer needs a container restart.
    POST /_arena/reload forces it."""
    global MERCHANTS, _MERCHANTS_MTIME
    try:
        mtime = os.stat(MERCHANTS_FILE).st_mtime
    except OSError:
        return MERCHANTS
    if mtime != _MERCHANTS_MTIME:
        loaded = _load_merchants()
        if loaded:
            MERCHANTS = loaded
        _MERCHANTS_MTIME = mtime
    return MERCHANTS


def _reload(handler, body):
    global _MERCHANTS_MTIME
    _MERCHANTS_MTIME = None
    return 200, {"reloaded": True, "merchants": len(_merchants())}


def _banking_account_credentials(handler, body):
    # GET /merchant/{merchant_id}/banking_account_by_account_number/{account_number}/credentials
    # (payouts pkg/bankingAccountService/fetch_banking_creds.go:14; consumed by
    # bankingAccountStatement/processor/rbl_gateway.go GetRequestDataForMozart via GetCredentials()).
    # Response shape = FetchBankingCredsResponse{data: FetchBankingCredsBaseResponse{id, corp_id, user_id, urn,
    # credentials{auth_username, auth_password, client_id, client_secret, corp_id}}}. Values are SYNTHETIC
    # (arena-only): the real banking-accounts service returns the merchant's RBL API credentials from its vault.
    parts = urlparse(handler.path).path.rstrip("/").split("/")
    # ['', 'merchant', mid, 'banking_account_by_account_number', acct, 'credentials']
    if len(parts) != 6 or parts[1] != "merchant" or parts[3] != "banking_account_by_account_number" or parts[5] != "credentials":
        return 404, {"error": {"code": "BAD_REQUEST_ERROR", "description": "no_stub_route"}}
    merchant_id, account_number = parts[2], parts[4]
    m = _merchants().get(merchant_id)
    if m is None:
        return 404, {"error": {"code": "BAD_REQUEST_ERROR", "description": "merchant not found"}}
    return 200, {"data": {
        "id": m.get("banking_account_id", "ARENABA" + merchant_id[-7:]),
        "corp_id": m.get("corp_id", "ARENACORP"),
        "user_id": m.get("bank_user_id", "arena"),
        "urn": "",
        "credentials": {
            "auth_username": "arena",
            "auth_password": "arena-rbl-pass",
            "client_id": "arena-rbl-client",
            "client_secret": "arena-rbl-secret",
            "corp_id": m.get("corp_id", "ARENACORP"),
        },
        "merchant_id": merchant_id,
        "account_number": account_number,
    }}


def _shield_merchant_details(handler, body):
    # GET /payouts/shield/merchant/{merchant_id}/details?account_number=...
    parsed = urlparse(handler.path)
    parts = parsed.path.rstrip("/").split("/")
    merchant_id = parts[-2] if len(parts) >= 2 else ""
    account_number = (parse_qs(parsed.query).get("account_number") or [""])[0]
    m = _merchants().get(merchant_id)
    if m is None:
        return 404, {"error": {"code": "BAD_REQUEST_ERROR", "description": "merchant not found"}}
    return 200, {"data": {
        "merchant_id": merchant_id,
        "business_id": m.get("business_id", "ARENABIZ%s" % merchant_id[-6:]),
        "business_type": m.get("business_type", "private_limited_company"),
        "sales_team": m.get("sales_team", "SME"),
        "account_number": account_number,
    }}


ROUTES = {
    ("GET", "/payouts/shield/merchant/"): _shield_merchant_details,
    ("GET", "/merchant/"): _banking_account_credentials,   # M4 (T10): bank credentials for statement fetch
    ("POST", "/_arena/reload"): _reload,
}

if __name__ == "__main__":
    serve(ROUTES)
