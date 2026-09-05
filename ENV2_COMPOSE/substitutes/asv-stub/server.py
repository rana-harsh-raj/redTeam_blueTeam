#!/usr/bin/env python3
"""asv-stub: skeleton JSON facade for Account Service. See CONTRACT.md for the
gRPC-vs-REST fidelity gap this stub does NOT resolve."""
import json
import os
import sys

sys.path.insert(0, "/app")
from _common.base_stub import serve  # noqa: E402

ACTIVATED_DEFAULT = os.environ.get("ACTIVATED_DEFAULT", "true").lower() == "true"
HOLD_FUNDS_DEFAULT = os.environ.get("HOLD_FUNDS_DEFAULT", "false").lower() == "true"


def _fetch_account(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    mid = req.get("merchant_id", "unknown")
    return 200, {
        "activated": ACTIVATED_DEFAULT,
        "hold_funds": HOLD_FUNDS_DEFAULT,
        "account_id": "acc_%s" % mid,
        "status": "active" if ACTIVATED_DEFAULT else "inactive",
    }


ROUTES = {
    ("POST", "/account/fetch"): _fetch_account,
}

if __name__ == "__main__":
    serve(ROUTES)
