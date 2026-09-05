"""V6 (I21): real FTS and bank-success route produces a balanced, Shared-only
``payout_processed`` journal whose FTS source identifiers are the ones the relay
actually carried, and which credits the Ledger account that Ledger's own
``account_details`` describe as that FTS source account. No fallback.

I21's three named clauses and where each is asserted:

(a) ``fts_fund_account_id`` == the FTS transfer's ``source_account_id`` and
    ``fts_account_type`` == lower(``bank_account_type``) of the FTS source account.

    SCHEMA NOTE -- these identifiers are **not persisted anywhere in Ledger**.
    ``\\d journal`` on postgres-ledger shows only
    ``id, merchant_id, amount, base_amount, currency, transactor_id,
    transactor_event, transaction_date, created_at, updated_at,
    created_at_microsecond, updated_at_microsecond, tenant`` -- no ``identifiers``
    and no ``extra_info`` column -- and the only table that could carry them,
    ``ledger_entry_details.notes``, is never written on the payout path
    (``SELECT count(*) FROM ledger_entry_details`` = 0 on a booted arena).
    payouts ``pkg/ledger/ledger_journal_create.go:210-218`` puts them in the
    *request* ``Identifiers`` map, which Ledger consumes for account discovery and
    discards. Clause (a) is therefore asserted against what Payouts received and
    stored, which is what that request is built from:
      * the ``fts_info`` payout meta (payouts ``core.go:2532-2542`` writes keys
        ``status``/``fund_account_id``/``account_type``;
        ``asyncFailureHandlingHelper.go:498-510`` feeds those two values verbatim
        into the processed-journal request), and
      * the relay payload the monolith actually sent
        (``substitutes/monolith-stub/server.py:625-629``, which lower-cases
        ``bank_account_type`` exactly as api ``Payout/Core.php:949-951`` does)
        whenever the monolith relay is the active transport for this run.
    Clause (b) below then proves the identifiers were correct end-to-end, because
    Ledger's own discovery landed the credit on the matching FTS account.

(b) the FTS account credited by the processed journal carries
    ``account_details.entities.fund_account_type == [lower(bank_account_type)]`` and
    ``entities.fts_fund_account_id == [str(source_account_id)]``, under the matching
    Nodal/Current top-level parent (``Razorpay Nodal Payable`` /
    ``Current Payable``, itself parentless and carrying the same
    ``fund_account_type``). Asserted directly against Ledger Postgres.

(c) the journal's entries sum to zero and exactly one such journal row exists.
"""
import json

import pytest

from helpers import db, payouts_flow as pf, trace
from helpers.wait import wait_until


def _entities(row):
    value = (row or {}).get("entities") or {}
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    if isinstance(value, str):
        value = json.loads(value)
    return value


def _account_details(ledger_pg, account_id):
    return db.fetchone(
        ledger_pg,
        "SELECT account_id, account_name, parent_account_id, merchant_id, entities "
        "FROM account_details WHERE account_id=%s",
        (str(account_id).strip(),),
    )


@pytest.mark.spec_id("V6")
@pytest.mark.status("END_TO_END_CONFIRMED")
def test_payout_processed_journal_mirrors_ledger(ps_public_client, ps_internal_client, payouts_mysql,
                                                 ledger_pg, merchant_m1, mozart_mock_client, fts_mysql,
                                                 monolith_stub_client):
    pid = pf.seed_payout(ps_public_client, merchant_m1, 10000)
    payout = pf.wait_for_status(payouts_mysql, pid, "processed")
    assert payout["utr"]
    transfer = pf.transfer_metadata(fts_mysql, pid)
    assert transfer["status"] == "PROCESSED" and transfer["utr"] == payout["utr"]

    # The two values I21 names, read from the real FTS rows (transfers joined to
    # source_accounts inside transfer_metadata -- fts source_account.go:99-101).
    expected_fund_account_id = str(transfer["source_account_id"])
    expected_account_type = str(transfer["bank_account_type"] or "").lower()
    assert expected_fund_account_id and expected_fund_account_id != "0", transfer
    assert expected_account_type in ("nodal", "current", "corp_card"), transfer

    # ---- clause (c): the journal itself -------------------------------------
    journal = pf.wait_for_processed_journal(ledger_pg, pid)
    entries = pf.get_ledger_entries(ledger_pg, journal["id"])
    assert pf.ledger_entries_sum_to_zero(entries)
    assert pf.count_ledger_journal_rows(ledger_pg, pid, "payout_processed") == 1

    # ---- clause (a): the identifiers Payouts actually carried ---------------
    meta = wait_until(
        lambda: db.fetchone(
            payouts_mysql,
            "SELECT meta_value FROM payout_meta_temporary "
            "WHERE payout_id=%s AND meta_name='fts_info' AND deleted_at IS NULL",
            (pf.db_id(pid),)),
        timeout=15, interval=.5,
        desc="fts_info payout meta written by ledgerProcessingForPayoutProcessed")
    meta_value = meta["meta_value"]
    if isinstance(meta_value, (bytes, bytearray)):
        meta_value = meta_value.decode()
    if isinstance(meta_value, str):
        meta_value = json.loads(meta_value)
    assert meta_value.get("fund_account_id") == expected_fund_account_id, meta_value
    # ledger_journal_create.go:217 applies strings.ToLower to whatever arrived; the
    # monolith relay already lower-cases it (server.py:625-629). Assert the value is
    # the FTS account type, and separately assert the relay's own lower-casing below.
    assert str(meta_value.get("account_type") or "").lower() == expected_account_type, meta_value
    assert str(meta_value.get("status") or "").lower() == "processed", meta_value

    relay_log = monolith_stub_client.get("/_arena/log")
    assert relay_log.status == 200, relay_log
    bare = pf.db_id(pid)
    relayed = [e for e in relay_log.json()["log"]
               if e.get("kind") == "payout_status_relay"
               and str((e.get("payload") or {}).get("body", {}).get("source_id") or "") == bare
               and str((e.get("payload") or {}).get("body", {}).get("status") or "") == "processed"]
    if relayed:
        body = relayed[-1]["payload"]["body"]
        # verbatim, already lower-cased by the relay -- no re-normalisation here.
        assert body.get("fts_fund_account_id") == expected_fund_account_id, body
        assert body.get("fts_account_type") == expected_account_type, body
        relay_cross_check = "asserted against the monolith relay payload"
    else:
        relay_cross_check = ("not applicable: no monolith payout_status_relay entry for this payout "
                             "(non-monolith status transport in this run); fts_info meta assertion stands")

    # ---- clause (b): Ledger's own description of the credited FTS account ---
    credits = [e for e in entries if str(e["type"]).lower() == "credit"]
    assert len(credits) == 1, entries
    account = _account_details(ledger_pg, credits[0]["account_id"])
    assert account, ("credited account has no account_details row", credits[0])
    account_entities = _entities(account)
    assert account_entities.get("fts_fund_account_id") == [expected_fund_account_id], account
    assert account_entities.get("fund_account_type") == [expected_account_type], account
    parent_id = account["parent_account_id"]
    assert parent_id, ("credited FTS account has no parent account", account)
    parent = _account_details(ledger_pg, parent_id)
    assert parent, ("parent account has no account_details row", parent_id)
    parent_entities = _entities(parent)
    assert parent_entities.get("fund_account_type") == [expected_account_type], parent
    assert parent["parent_account_id"] is None, ("matching parent must be a top-level pool", parent)

    trace.record("i21_processed_journal_identifiers", {
        "payout_id": pid,
        "journal_id": journal["id"],
        "fts_transfer_id": transfer["id"],
        "expected_fts_fund_account_id": expected_fund_account_id,
        "expected_fts_account_type": expected_account_type,
        "payout_meta_fts_info": meta_value,
        "relay_cross_check": relay_cross_check,
        "credited_account_id": str(credits[0]["account_id"]).strip(),
        "credited_account_name": str(account["account_name"]).strip(),
        "credited_account_entities": account_entities,
        "matching_parent_account_id": str(parent_id).strip(),
        "matching_parent_account_name": str(parent["account_name"]).strip(),
        "ledger_identifier_persistence": "journal has no identifiers/extra_info column and ledger_entry_details is "
                                         "not written on this path; clause (a) is asserted on the values Payouts "
                                         "received and stored, clause (b) on Ledger's resulting account choice",
    })
