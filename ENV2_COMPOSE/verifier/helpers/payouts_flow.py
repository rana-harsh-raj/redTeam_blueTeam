"""Shared building blocks for driving the payouts create/webhook/cron paths
and reading the resulting rows back out of payouts MySQL and ledger Postgres.
Kept here rather than duplicated across ``verifiers/test_v*.py`` so each test
file stays focused on the one invariant it names in its docstring.

Every function takes already-connected clients/DB handles (from conftest.py
fixtures) -- nothing here opens a connection itself.
"""
import uuid

import pytest

from . import db
from .passport import get_passport_jwt


def passport_or_skip(merchant):
    """Resolve a passport JWT for a merchant fixture dict (see conftest.py's
    _merchant()), or pytest.skip() naming the missing fixture. Centralized
    here (rather than duplicated per test file) per VERIFIER_SPEC.md gap #9:
    every payoutRoutes-calling verifier needs this same resolution."""
    token = get_passport_jwt(merchant["key"], merchant["merchant_id"])
    if not token:
        pytest.skip(
            "missing fixture: PASSPORT_STATIC_JWT_%s or PASSPORT_SIGNER_URL "
            "(see VERIFIER_SPEC.md gap #9 -- kong-lite does not mint passports)" % merchant["key"]
        )
    return token


def new_idempotency_key():
    return str(uuid.uuid4())


def build_create_body(fund_account_id, account_number, amount, currency="INR", mode="IMPS",
                       purpose="payout", queue_if_low_balance=False, extra=None):
    body = {
        "fund_account_id": fund_account_id,
        "account_number": account_number,
        "amount": amount,
        "currency": currency,
        "mode": mode,
        "purpose": purpose,
        "queue_if_low_balance": queue_if_low_balance,
    }
    if extra:
        body.update(extra)
    return body


def create_payout(ps_client, passport_jwt, body, idempotency_key=None):
    """POST /v1/payouts against payouts-api (via kong-lite or direct), per
    VERIFIER_SPEC.md's corrected auth model: ps_client already carries the
    PS_SERVICE Basic-Auth pair; passport_jwt supplies the merchant identity.
    payouts-api's FundAccountPayoutRequest additionally requires merchant_id
    (len=14) in the body -- in production the monolith supplies it from the
    authenticated merchant; here it is derived from the passport's consumer id
    so the body carries exactly what the monolith would send.
    Returns the raw HTTPResponse -- callers assert on .status/.json().
    """
    headers = {}
    if idempotency_key:
        headers["X-Payout-Idempotency"] = idempotency_key
    if "merchant_id" not in body and passport_jwt:
        body = dict(body)
        body["merchant_id"] = _merchant_id_from_passport(passport_jwt)
    return ps_client.post("/v1/payouts", body=body, headers=headers, passport_jwt=passport_jwt)


def _merchant_id_from_passport(jwt):
    import base64
    import json as _json
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = _json.loads(base64.urlsafe_b64decode(payload))
        return (claims.get("consumer") or {}).get("id") or ""
    except Exception:
        return ""


def db_id(public_id):
    """payouts-api returns public ids with an entity prefix (pout_<14 chars>); rows are keyed by the bare id."""
    if public_id and "_" in public_id:
        return public_id.split("_", 1)[1]
    return public_id


def get_payout_row(payouts_mysql, payout_id):
    return db.fetchone(payouts_mysql, "SELECT * FROM payouts WHERE id=%s", (db_id(payout_id),))


def get_idempotency_key_row(payouts_mysql, idempotency_key, merchant_id):
    return db.fetchone(
        payouts_mysql,
        "SELECT * FROM idempotency_keys WHERE idempotency_key=%s AND merchant_id=%s",
        (idempotency_key, merchant_id),
    )


def get_payout_logs(payouts_mysql, payout_id):
    return db.fetchall(
        payouts_mysql,
        "SELECT payout_id, event, `from`, `to`, mode, triggered_by, created_at "
        "FROM payout_logs WHERE payout_id=%s ORDER BY created_at, id",
        (payout_id,),
    )


def get_reversal_row(payouts_mysql, payout_id):
    return db.fetchone(payouts_mysql, "SELECT * FROM reversals WHERE payout_id=%s", (db_id(payout_id),))


def get_ledger_journal_row(ledger_pg, transactor_id, transactor_event):
    return db.fetchone(
        ledger_pg,
        "SELECT * FROM journal WHERE transactor_id=%s AND transactor_event=%s "
        "ORDER BY created_at DESC LIMIT 1",
        (transactor_id, transactor_event),
    )


def count_ledger_journal_rows(ledger_pg, transactor_id, transactor_event):
    row = db.fetchone(
        ledger_pg,
        "SELECT COUNT(*) AS c FROM journal WHERE transactor_id=%s AND transactor_event=%s",
        (transactor_id, transactor_event),
    )
    return row["c"] if row else 0


def get_ledger_entries(ledger_pg, journal_id):
    return db.fetchall(
        ledger_pg,
        "SELECT account_id, type, amount FROM ledger_entries WHERE journal_id=%s",
        (journal_id,),
    )


def ledger_entries_sum_to_zero(entries):
    total = 0
    for e in entries:
        amt = float(e["amount"])
        total += amt if str(e["type"]).lower() == "debit" else -amt
    return abs(total) < 1e-6


def get_account_balance(ledger_pg, merchant_id):
    """MerchantBalance account balance (tenant X): the account whose account_details.entities carry
    account_type payable + fund_account_type merchant_va -- a merchant has several accounts (vendor payable,
    commission, GST) and only this one is the spendable balance payouts authorises against."""
    row = db.fetchone(
        ledger_pg,
        "SELECT a.balance FROM accounts a JOIN account_details d ON d.account_id = a.id "
        "WHERE a.merchant_id=%s AND d.entities @> '{\"account_type\": [\"payable\"], \"fund_account_type\": [\"merchant_va\"]}'::jsonb "
        "ORDER BY a.created_at LIMIT 1",
        (merchant_id,),
    )
    return row["balance"] if row else None


def send_transfer_status_webhook(ps_internal_client, source_id, status, fund_transfer_id=1,
                                  source_type="payout", **extra):
    """Simulates the FTS -> PS webhook (payouts internal route, service
    Basic-Auth only -- see payout_internal_routes.go /transfer_status_webhook).
    This is the "synthetic webhook" stimulus VERIFIER_SPEC.md flags as
    downgrading a verifier's ceiling below END_TO_END_CONFIRMED whenever a
    real mozart-mock round trip isn't available.
    """
    body = {
        "fund_transfer_id": fund_transfer_id,
        "source_id": db_id(source_id),  # payouts looks the payout up by its bare 14-char id
        "source_type": source_type,
        "status": status,
    }
    body.update(extra)
    return ps_internal_client.post("/v1/payouts/transfer_status_webhook", body=body)


def get_inflight_reservations(ps_internal_client, merchant_id, balance_id):
    resp = ps_internal_client.get(
        "/v1/inflight_reservations?merchant_id=%s&balance_id=%s" % (merchant_id, balance_id)
    )
    return resp


def ledger_topup(ledger_client, merchant, amount, banking_account_id=None):
    """Credit the merchant's MerchantBalance through a real ledger journal (transactor_event
    positive_adjustment_processed, tenant X) -- the ledger caches balances, so direct SQL updates on
    `accounts` are invisible to payouts' balance checks. ledger_client must carry the LEDGER_BASIC_AUTH pair."""
    import time as _time
    import uuid as _uuid
    bank_acc = banking_account_id or ("bacc_" + merchant["balance_id"].replace("BAL", "BA0"))  # ARENABAL000001 -> bacc_ARENABA0000001
    body = {
        "merchant_id": merchant["merchant_id"],
        "currency": "INR",
        "transactor_id": "arenatopup_%s" % _uuid.uuid4().hex[:14],
        "transactor_event": "positive_adjustment_processed",
        "transaction_date": int(_time.time()),
        "identifiers": {"banking_account_id": bank_acc},
        # ledger JournalCreateRequest carries amount/base_amount/commission/tax at the top level as well
        # (rpc/ledger/journal/v1); ValidateCreateRequest dereferences them, so they must be present.
        "amount": str(int(amount)),
        "base_amount": str(int(amount)),
        "commission": "0",
        "tax": "0",
        "money_params": {"amount": str(int(amount)), "base_amount": str(int(amount))},
        "notes": {"balance_id": merchant["balance_id"], "arena": "verifier top-up"},
        "additional_params": {},
    }
    return ledger_client.post("/twirp/rzp.ledger.journal.v1.JournalAPI/Create", body=body,
                              headers={"Ledger-Tenant": "X"})


def wait_for_initiated(payouts_mysql, payout_id, timeout=90.0):
    """Wait until PS has moved the payout to `initiated` and learned its FTS transfer id (create_fta via the
    monolith relay, then the details sync) -- the synthetic FTS status webhook is only accepted from that state.
    Returns the payouts row (possibly still without fts_transfer_id if the wait timed out)."""
    from .wait import wait_until, WaitTimeout
    try:
        wait_until(
            lambda: (lambda r: r is not None and r.get("status") not in ("create_request_submitted", "ledger_response_awaited", "created") and (r.get("fts_transfer_id") or 0) > 0 and r)(get_payout_row(payouts_mysql, payout_id)),
            timeout=timeout, interval=1.0, desc="payout %s initiated with fts_transfer_id" % payout_id,
        )
    except WaitTimeout:
        pass
    return get_payout_row(payouts_mysql, payout_id)
