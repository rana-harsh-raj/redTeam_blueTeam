"""V2 -- Idempotency: same X-Payout-Idempotency key, different body -> 400 BAD_REQUEST.

Covers: C5, errorclass.SameIdempotencyKeyDifferentRequest
(payouts/internal/routing/middleware/idempotency_key.go:214-232).
"""
import pytest

from helpers import payouts_flow as pf


@pytest.mark.spec_id("V2")
@pytest.mark.status("CROSS_SERVICE_CONFIRMED")
def test_idempotent_create_same_key_different_body_rejected(ps_public_client, payouts_mysql, merchant_m1):
    if not merchant_m1["fund_account_id"] or not merchant_m1["account_number"]:
        pytest.skip("missing fixture: ARENA_M1_FUND_ACCOUNT_ID / ARENA_M1_ACCOUNT_NUMBER")

    passport_jwt = pf.passport_or_skip(merchant_m1)
    idem_key = pf.new_idempotency_key()
    body = pf.build_create_body(merchant_m1["fund_account_id"], merchant_m1["account_number"], amount=100)

    resp1 = pf.create_payout(ps_public_client, passport_jwt, body, idempotency_key=idem_key)
    assert resp1.status in (200, 201), "seed create failed: %s" % resp1

    key_row_before = pf.get_idempotency_key_row(payouts_mysql, idem_key, merchant_m1["merchant_id"])
    assert key_row_before is not None
    request_hash_before = key_row_before["request_hash"]

    mismatched_body = dict(body)
    mismatched_body["amount"] = body["amount"] + 1

    resp2 = pf.create_payout(ps_public_client, passport_jwt, mismatched_body, idempotency_key=idem_key)

    assert resp2.status == 400, "expected 400 BAD_REQUEST for same key/different body, got %s: %s" % (
        resp2.status,
        resp2.text,
    )

    key_row_after = pf.get_idempotency_key_row(payouts_mysql, idem_key, merchant_m1["merchant_id"])
    assert key_row_after["request_hash"] == request_hash_before, "request_hash must not be overwritten on mismatch"

    payouts_for_key = pf.get_payout_row(payouts_mysql, key_row_after["source_id"])
    assert payouts_for_key is not None, "the original payout for the key must still be the only one"
