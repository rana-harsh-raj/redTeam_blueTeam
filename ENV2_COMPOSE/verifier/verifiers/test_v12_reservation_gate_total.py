"""V12 -- Direct-rail reservation gate: reservation total equals live reservations.

Covers: C16, Invariant 5.
"""
import pytest

from helpers import payouts_flow as pf


@pytest.mark.spec_id("V12")
@pytest.mark.status("END_TO_END_CONFIRMED")
def test_inflight_reservation_total_equals_live_reservations(ps_public_client, ps_internal_client, merchant_m2):
    if not merchant_m2["fund_account_id"] or not merchant_m2["account_number"] or not merchant_m2["balance_id"]:
        pytest.skip(
            "missing fixture: ARENA_M2_FUND_ACCOUNT_ID / ARENA_M2_ACCOUNT_NUMBER / ARENA_M2_BALANCE_ID"
        )

    passport_jwt = pf.passport_or_skip(merchant_m2)
    created_ids = []
    for _ in range(3):
        body = pf.build_create_body(merchant_m2["fund_account_id"], merchant_m2["account_number"], amount=100)
        resp = pf.create_payout(ps_public_client, passport_jwt, body)
        assert resp.status in (200, 201), "create failed: %s" % resp
        created_ids.append(resp.json()["id"])

    inspect_resp = pf.get_inflight_reservations(
        ps_internal_client, merchant_m2["merchant_id"], merchant_m2["balance_id"]
    )
    assert inspect_resp.status == 200, "GET /v1/inflight_reservations failed: %s" % inspect_resp
    inspect = inspect_resp.json()

    live_items = [i for i in inspect.get("items", []) if i.get("state") == "live"]
    live_total = sum(i["amount"] for i in live_items)

    assert inspect.get("reserved_total") == live_total, (
        "reserved_total (%s) must equal the sum of live reservation amounts (%s): items=%r"
        % (inspect.get("reserved_total"), live_total, inspect.get("items"))
    )

    live_ids = {i["payout_id"] for i in live_items}
    for payout_id in created_ids:
        assert payout_id in live_ids, "created payout %s missing from live reservations: %r" % (
            payout_id,
            inspect.get("items"),
        )

    assert inspect.get("store_trusted") is True, (
        "store_trusted=false -- reservation store is not authoritative (reconciler needs a run); "
        "this is itself a finding, not a harness bug"
    )
