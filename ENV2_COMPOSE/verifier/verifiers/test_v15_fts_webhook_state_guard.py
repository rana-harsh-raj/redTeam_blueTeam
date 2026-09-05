"""V15 -- FTS webhook state guard: webhook on a non-initiated (terminal) payout
=> 200, no change.

Covers: C23, AllowedStateTransitionForTransferWebhook
(payouts/internal/app/common/appConstants/states.go),
IsValidStateTransitionForTransferWebhook (fts_transfer_status_webhook.go:645).
"""
import pytest

from helpers import payouts_flow as pf


@pytest.mark.spec_id("V15")
@pytest.mark.status("CROSS_SERVICE_CONFIRMED")
def test_webhook_on_cancelled_payout_is_noop(
    ps_public_client, ps_internal_client, payouts_mysql, merchant_m1, ledger_pg
):
    if not merchant_m1["fund_account_id"] or not merchant_m1["account_number"]:
        pytest.fail("missing fixture: ARENA_M1_FUND_ACCOUNT_ID / ARENA_M1_ACCOUNT_NUMBER")

    passport_jwt = pf.passport_or_skip(merchant_m1)

    # Get a payout into a cancellable, non-initiated state: create a small,
    # easily-queued/created payout and cancel it while it's still pre-terminal.
    body = pf.build_create_body(
        merchant_m1["fund_account_id"], merchant_m1["account_number"], amount=int(pf.get_account_balance(ledger_pg,merchant_m1["merchant_id"]))+100000, queue_if_low_balance=True, mode="NEFT",
    )
    resp = pf.create_payout(ps_public_client, passport_jwt, body)
    assert resp.status in (200, 201), "create failed: %s" % resp
    payout_id = resp.json()["id"]

    payout_row = pf.wait_for_status(payouts_mysql, payout_id,"queued")
    if payout_row["status"] not in ("queued", "scheduled", "on_hold"):
        pytest.fail(
            "seed payout did not land in a cancellable state (%r) -- cannot set up the terminal precondition "
            "this verifier needs" % payout_row["status"]
        )

    cancel_resp = ps_public_client.post(
        "/v1/payouts/cancel_payout/%s" % pf.db_id(payout_id), body={}, passport_jwt=passport_jwt
    )
    assert cancel_resp.status in (200, 201), "cancel failed: %s" % cancel_resp

    payout_row = pf.get_payout_row(payouts_mysql, payout_id)
    assert payout_row["status"] == "cancelled", "payout did not reach cancelled: %r" % payout_row
    logs_before = pf.get_payout_logs(payouts_mysql, payout_id)
    updated_at_before = payout_row["updated_at"]

    webhook_resp = pf.send_transfer_status_webhook(
        ps_internal_client, payout_id, "processed", fund_transfer_id=1, utr="UTR_SHOULD_BE_IGNORED"
    )

    assert webhook_resp.status == 200, (
        "webhook on an illegal fromState must still return 200 (to stop FTS retries), got %s: %s"
        % (webhook_resp.status, webhook_resp.text)
    )

    payout_row_after = pf.get_payout_row(payouts_mysql, payout_id)
    assert payout_row_after["status"] == "cancelled", (
        "payout status changed after an illegal webhook: %r" % payout_row_after
    )
    assert payout_row_after["updated_at"] == updated_at_before, "updated_at changed after an illegal webhook"

    logs_after = pf.get_payout_logs(payouts_mysql, payout_id)
    assert len(logs_after) == len(logs_before), "payout_logs grew after a skipped/illegal webhook transition"
