"""V16 -- Stuck-initiated detection: FTS terminal + payout 'initiated', no
automated repair (expected finding, not a bug in this suite).

Covers: C22, Invariant 2. Reproduces the documented gap by mutating FTS's own
DB directly (standing in for "the webhook got dropped") and then asserting
the divergence PERSISTS -- a passing convergence would itself be the
noteworthy result and is surfaced loudly rather than silently celebrated.
"""
import os
import time

import pytest

from helpers import db, payouts_flow as pf


@pytest.mark.spec_id("V16")
@pytest.mark.status("CROSS_SERVICE_CONFIRMED")
def test_stuck_initiated_payout_has_no_automated_repair(
    ps_public_client, payouts_mysql, fts_mysql, merchant_m1
):
    if not merchant_m1["fund_account_id"] or not merchant_m1["account_number"]:
        pytest.skip("missing fixture: ARENA_M1_FUND_ACCOUNT_ID / ARENA_M1_ACCOUNT_NUMBER")

    passport_jwt = pf.passport_or_skip(merchant_m1)
    body = pf.build_create_body(merchant_m1["fund_account_id"], merchant_m1["account_number"], amount=100)
    resp = pf.create_payout(ps_public_client, passport_jwt, body)
    assert resp.status in (200, 201), "create failed: %s" % resp
    payout_id = resp.json()["id"]

    payout_row = pf.wait_for_initiated(payouts_mysql, payout_id)
    fts_transfer_id = payout_row.get("fts_transfer_id")
    if payout_row["status"] != "initiated" or not fts_transfer_id:
        pytest.skip(
            "payout did not reach 'initiated' with an fts_transfer_id (status=%r, fts_transfer_id=%r) -- "
            "cannot set up the stuck-initiated precondition" % (payout_row["status"], fts_transfer_id)
        )

    # Simulate "the FTS->PS webhook was dropped": flip FTS's own transfer row
    # to a terminal status WITHOUT going through PS at all.
    db.execute(
        fts_mysql,
        "UPDATE transfers SET status='PROCESSED', utr='UTR_STUCK_TEST' WHERE id=%s",
        (fts_transfer_id,),
    )

    wait_seconds = float(os.environ.get("STUCK_DETECTION_WAIT_SECONDS", "90"))
    time.sleep(wait_seconds)

    payout_row_after = pf.get_payout_row(payouts_mysql, payout_id)
    transfer_row_after = db.fetchone(fts_mysql, "SELECT status, utr FROM transfers WHERE id=%s", (fts_transfer_id,))

    still_initiated = payout_row_after["status"] == "initiated"
    fts_is_terminal = transfer_row_after["status"] in ("PROCESSED", "FAILED", "REVERSED")

    assert fts_is_terminal, "test setup failed: FTS transfer did not stay terminal"

    if still_initiated:
        # This IS the expected, documented finding (Invariant 2 / C22): no
        # code path in this closure repairs it automatically.
        return

    pytest.fail(
        "UNEXPECTED: the stuck-initiated divergence self-healed (payout status is now %r) -- "
        "this contradicts CONTROL_AND_INVARIANT_CATALOG.md's Invariant 2 ('no automated repair'). "
        "Investigate what closed the gap: either a repair mechanism now exists that the catalog "
        "doesn't document, or this test's wait/setup is wrong." % payout_row_after["status"]
    )
