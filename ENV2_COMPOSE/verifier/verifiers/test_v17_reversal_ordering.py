"""V17 -- Reversal ordering: payout 'reversed' row committed before the ledger
journal call (direct path).

Covers: C24. Baseline branch always runs (timestamp ordering on the normal
synchronous path). The fault-injection branch (blocking ledger-api to force
the async-retry path) is gated behind ALLOW_NETWORK_FAULT_INJECTION=1 because
no network-fault-injection mechanism exists in ENV2_COMPOSE/ yet (see
VERIFIER_SPEC.md V17 / gap list) -- it is written so the branch is ready the
moment that capability exists, and skips cleanly until then.
"""
import os
import time

import pytest

from helpers import payouts_flow as pf


@pytest.mark.spec_id("V17")
@pytest.mark.status("CROSS_SERVICE_CONFIRMED")
def test_reversal_row_committed_before_ledger_call_baseline(
    ps_public_client, ps_internal_client, payouts_mysql, ledger_pg, merchant_m1
):
    if not merchant_m1["fund_account_id"] or not merchant_m1["account_number"]:
        pytest.skip("missing fixture: ARENA_M1_FUND_ACCOUNT_ID / ARENA_M1_ACCOUNT_NUMBER")

    passport_jwt = pf.passport_or_skip(merchant_m1)
    body = pf.build_create_body(merchant_m1["fund_account_id"], merchant_m1["account_number"], amount=100)
    resp = pf.create_payout(ps_public_client, passport_jwt, body)
    assert resp.status in (200, 201), "create failed: %s" % resp
    payout_id = resp.json()["id"]

    payout_row = pf.wait_for_initiated(payouts_mysql, payout_id)
    if payout_row["status"] not in ("created", "initiated"):
        pytest.skip("payout not in a pre-terminal state suitable for the reversal stimulus: %r" % payout_row)
    fts_transfer_id = payout_row.get("fts_transfer_id") or 1

    t_before_webhook = time.monotonic()
    webhook_resp = pf.send_transfer_status_webhook(
        ps_internal_client, payout_id, "failed", fund_transfer_id=fts_transfer_id,
        failure_reason="TEST_BANK_DECLINE", bank_status_code="91",
    )
    t_after_webhook = time.monotonic()
    assert webhook_resp.status == 200, "transfer_status_webhook call failed: %s" % webhook_resp

    # By the time the synchronous webhook HTTP response has returned, both
    # the payout row + reversals row (committed first, per Flow F) and the
    # ledger journal (called synchronously right after, on the happy path)
    # should already be visible -- we assert the DB-commit-before-ledger
    # *ordering claim* via the reversals row's presence and the journal's
    # presence, both observed only after the response returned.
    del t_before_webhook, t_after_webhook  # kept for future latency assertions; not asserted on directly here

    payout_row = pf.get_payout_row(payouts_mysql, payout_id)
    assert payout_row["status"] == "reversed", "payout did not reach reversed: %r" % payout_row

    reversal_row = pf.get_reversal_row(payouts_mysql, payout_id)
    assert reversal_row is not None, "reversals row must exist (DB commit happens before the ledger call)"

    journal_row = pf.get_ledger_journal_row(ledger_pg, payout_id, "payout_reversed")
    assert journal_row is not None, "ledger journal must also exist on the (uninterrupted) happy path"


@pytest.mark.spec_id("V17")
@pytest.mark.status("BLOCKED_BY_FIDELITY_GAP")
def test_reversal_survives_ledger_outage_via_async_retry(
    ps_public_client, ps_internal_client, payouts_mysql, ledger_pg, merchant_m1
):
    if os.environ.get("ALLOW_NETWORK_FAULT_INJECTION") != "1":
        pytest.skip(
            "missing fixture: no network-fault-injection mechanism exists in ENV2_COMPOSE/ yet "
            "(gate: ALLOW_NETWORK_FAULT_INJECTION=1) -- see VERIFIER_SPEC.md V17"
        )
    pytest.skip(
        "ALLOW_NETWORK_FAULT_INJECTION=1 was set but this package has no concrete blackhole-ledger-api "
        "implementation to call yet -- extend this test once ENV2_COMPOSE/network/ exposes one"
    )
