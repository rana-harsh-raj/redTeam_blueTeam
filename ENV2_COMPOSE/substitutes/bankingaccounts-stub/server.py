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


def _shield_merchant_details(handler, body):
    # GET /payouts/shield/merchant/{merchant_id}/details?account_number=...
    parsed = urlparse(handler.path)
    parts = parsed.path.rstrip("/").split("/")
    merchant_id = parts[-2] if len(parts) >= 2 else ""
    account_number = (parse_qs(parsed.query).get("account_number") or [""])[0]
    m = MERCHANTS.get(merchant_id)
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
}

if __name__ == "__main__":
    serve(ROUTES)
