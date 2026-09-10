"""V4: sequential real Ledger requests deduplicate a complete journal DTO."""
import time
import uuid
import pytest
from helpers import db, trace

@pytest.mark.spec_id("V4")
@pytest.mark.status("CROSS_SERVICE_CONFIRMED")
def test_ledger_journal_dedupe_on_transactor_id_and_event(ledger_client, ledger_pg, merchant_m1):
    # Non-empty guard first: the assertion below is a NEGATIVE one (production has no
    # unique index on (transactor_id, transactor_event)), so an empty catalogue result
    # -- wrong table name, wrong schema, wrong search_path, a renamed table -- would
    # satisfy it vacuously. Require the catalogue to actually describe this table by
    # naming its primary key before believing anything it says about the absent index.
    indexes=db.fetchall(ledger_pg,"SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='journal'")
    names={str(i["indexname"]).strip() for i in indexes}
    assert indexes, "pg_indexes returned no rows for public.journal; the no-unique-index assertion would be vacuous"
    assert "journal_pkey" in names, ("public.journal primary key missing from pg_indexes", indexes)
    trace.record("journal_index_catalogue",{"table":"public.journal","index_count":len(indexes),"index_names":sorted(names),
                                            "guard":"primary key journal_pkey observed before the negative assertion"})
    assert not any("UNIQUE" in i["indexdef"].upper() and "transactor_id" in i["indexdef"] and "transactor_event" in i["indexdef"] for i in indexes), indexes
    tid = "pout_" + uuid.uuid4().hex[:14]
    body = {"merchant_id": merchant_m1["merchant_id"], "transactor_id": tid,
            "transactor_event":"payout_initiated", "currency":"INR", "transaction_date":int(time.time()),
            "amount":"100", "base_amount":"100", "commission":"0", "tax":"0",
            "money_params":{"amount":"100","base_amount":"100","commission":"0","tax":"0"},
            "identifiers":{"banking_account_id":"bacc_"+merchant_m1["banking_account_id"]},
            "notes":{"balance_id":merchant_m1["balance_id"]},"additional_params":{}}
    responses=[ledger_client.post("/twirp/rzp.ledger.journal.v1.JournalAPI/Create",body=body,
                                  headers={"Ledger-Tenant":"X"}) for _ in range(2)]
    assert responses[0].status==200,responses[0]
    assert responses[1].status==400,responses[1]
    assert responses[1].json()=={
        "code":"invalid_argument",
        "msg":"validation_failure: record_already_exist: BAD_REQUEST_RECORD_ALREADY_EXIST",
    },responses[1]
    rows=db.fetchall(ledger_pg,"SELECT id FROM journal WHERE transactor_id=%s AND transactor_event=%s",(tid,"payout_initiated"))
    assert len(rows)==1, rows
    assert responses[0].json()["id"] == rows[0]["id"]
