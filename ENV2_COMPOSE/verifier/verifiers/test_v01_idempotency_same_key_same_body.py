"""V1 -- Idempotency: same X-Payout-Idempotency key, same body -> same payout id.

Covers: C5, Invariant 3. See VERIFIER_SPEC.md V1 for full preconditions,
stimulus and assertion detail; this file implements exactly that.
"""
import time

import pytest

from helpers import payouts_flow as pf


@pytest.mark.spec_id("V1")
@pytest.mark.status("END_TO_END_CONFIRMED")
def test_idempotent_create_same_key_same_body(ps_public_client, payouts_mysql, merchant_m1):
    if not merchant_m1["fund_account_id"] or not merchant_m1["account_number"]:
        pytest.skip("missing fixture: ARENA_M1_FUND_ACCOUNT_ID / ARENA_M1_ACCOUNT_NUMBER")

    passport_jwt = pf.passport_or_skip(merchant_m1)
    idem_key = pf.new_idempotency_key()
    body = pf.build_create_body(
        merchant_m1["fund_account_id"], merchant_m1["account_number"], amount=100, queue_if_low_balance=True, mode="NEFT"
    )

    resp1 = pf.create_payout(ps_public_client, passport_jwt, body, idempotency_key=idem_key)
    assert resp1.status in (200, 201), "first create failed: %s" % resp1
    payout_id_1 = resp1.json().get("id")
    assert payout_id_1, "first create response missing id: %s" % resp1.text

    time.sleep(0.5)  # small jitter, per spec ("random 0-2s jitter")

    resp2 = pf.create_payout(ps_public_client, passport_jwt, body, idempotency_key=idem_key)
    assert resp2.status in (200, 201), "second (duplicate) create failed: %s" % resp2
    payout_id_2 = resp2.json().get("id")

    assert payout_id_1 == payout_id_2, "duplicate idempotent create returned a different payout id"

    key_row = pf.get_idempotency_key_row(payouts_mysql, idem_key, merchant_m1["merchant_id"])
    assert key_row is not None, "no idempotency_keys row for the key used"
    assert key_row["source_id"] == pf.db_id(payout_id_1)  # rows keep the bare id; the API returns pout_<id>

    payout_row = pf.get_payout_row(payouts_mysql, payout_id_1)
    assert payout_row is not None, "no payouts row for the created payout id"
