"""V4 -- Ledger dedupe on (transactor_id, transactor_event).

Covers: C18 ("app-level mutex, no DB unique index -- test it"), Invariant 3.
Fires two genuinely concurrent Twirp Create calls (not sequential) to
exercise the mutex in ledger/internal/journal/server.go:47-63 rather than
only the request-hash comparison path.
"""
import threading
import uuid

import pytest

from helpers import db


@pytest.mark.spec_id("V4")
@pytest.mark.status("CROSS_SERVICE_CONFIRMED")
def test_ledger_journal_dedupe_on_transactor_id_and_event(ledger_client, ledger_pg, merchant_m1):
    transactor_id = "pouttest%s" % uuid.uuid4().hex[:8]
    transactor_event = "payout_initiated"
    body = {
        "merchant_id": merchant_m1["merchant_id"],
        "transactor_id": transactor_id,
        "transactor_event": transactor_event,
        "amount": "1.000000",
        "currency": "INR",
    }

    responses = [None, None]

    def _call(idx):
        responses[idx] = ledger_client.post(
            "/twirp/rzp.ledger.journal.v1.JournalAPI/Create", body=body
        )

    t1 = threading.Thread(target=_call, args=(0,))
    t2 = threading.Thread(target=_call, args=(1,))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert responses[0] is not None and responses[1] is not None, "one of the two concurrent calls never returned"

    rows = db.fetchall(
        ledger_pg,
        "SELECT id FROM journal WHERE transactor_id=%s AND transactor_event=%s",
        (transactor_id, transactor_event),
    )
    assert len(rows) == 1, (
        "expected exactly one journal row for (transactor_id, transactor_event) despite no DB unique index "
        "(C18) -- got %d. This is the catalog's own 'invariant weak -- test it' flag; a value > 1 here is a "
        "confirmed finding, not a test-harness bug." % len(rows)
    )
