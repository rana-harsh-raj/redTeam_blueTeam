"""V13 -- Direct-rail reservation: released on terminal event.

Covers: C16, Invariant 5 ("release on terminal").
"""
import warnings

import pytest

from helpers import payouts_flow as pf


@pytest.mark.spec_id("V13")
@pytest.mark.status("CODE_CANDIDATE")  # synthetic webhook stimulus; see VERIFIER_SPEC.md V13
def test_reservation_released_on_terminal_event(
    ps_public_client, ps_internal_client, payouts_mysql, merchant_m2
):
    if not merchant_m2["fund_account_id"] or not merchant_m2["account_number"] or not merchant_m2["balance_id"]:
        pytest.skip(
            "missing fixture: ARENA_M2_FUND_ACCOUNT_ID / ARENA_M2_ACCOUNT_NUMBER / ARENA_M2_BALANCE_ID"
        )

    passport_jwt = pf.passport_or_skip(merchant_m2)
    body = pf.build_create_body(merchant_m2["fund_account_id"], merchant_m2["account_number"], amount=100)
    resp = pf.create_payout(ps_public_client, passport_jwt, body)
    assert resp.status in (200, 201), "create failed: %s" % resp
    payout_id = resp.json()["id"]

    before = pf.get_inflight_reservations(
        ps_internal_client, merchant_m2["merchant_id"], merchant_m2["balance_id"]
    ).json()
    before_live = {i["payout_id"]: i["amount"] for i in before.get("items", []) if i.get("state") == "live"}
    if payout_id not in before_live:
        pytest.skip("created payout never reached a live reservation -- gate may not have reserved it")

    payout_row = pf.wait_for_initiated(payouts_mysql, payout_id)
    fts_transfer_id = payout_row.get("fts_transfer_id") or 1
    pf.send_transfer_status_webhook(
        ps_internal_client, payout_id, "processed", fund_transfer_id=fts_transfer_id, utr="UTR_TEST_V13"
    )

    after = pf.get_inflight_reservations(
        ps_internal_client, merchant_m2["merchant_id"], merchant_m2["balance_id"]
    ).json()
    after_items_by_id = {i["payout_id"]: i for i in after.get("items", [])}

    reserved_total_before = before.get("reserved_total", 0)
    reserved_total_after = after.get("reserved_total", 0)
    assert reserved_total_after <= reserved_total_before - before_live[payout_id] + 1e-6 or (
        payout_id not in after_items_by_id
    ), "reserved_total did not decrease by the terminal payout's amount"

    if payout_id in after_items_by_id:
        assert after_items_by_id[payout_id]["state"] != "live", (
            "payout still shows state='live' after a terminal webhook: %r" % after_items_by_id[payout_id]
        )

    warnings.warn(
        "V13 drove the terminal event via a synthetic transfer_status_webhook call, not mozart-mock"
    )
