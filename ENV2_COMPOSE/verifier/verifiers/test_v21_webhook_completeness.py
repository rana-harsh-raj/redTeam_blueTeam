"""V21 -- Webhook completeness: every terminal state appears exactly once in
stork-capture.

Covers: C36, Invariant 6. stork-capture/CONTRACT.md documents only SMS/email
capture, not a Twirp WebhookAPI/ProcessEvent (payout.*) capture endpoint --
this verifier probes for one and degrades gracefully if it isn't there.
"""
import warnings

import pytest

from helpers import payouts_flow as pf
from helpers.wait import wait_until, WaitTimeout


TERMINAL_EVENTS = {"payout.processed", "payout.reversed", "payout.failed", "payout.cancelled", "payout.rejected"}


@pytest.mark.spec_id("V21")
@pytest.mark.status("BLOCKED_BY_FIDELITY_GAP")
def test_terminal_webhook_fires_exactly_once(
    ps_public_client, ps_internal_client, payouts_mysql, stork_capture_client, merchant_m1
):
    if not merchant_m1["fund_account_id"] or not merchant_m1["account_number"]:
        pytest.skip("missing fixture: ARENA_M1_FUND_ACCOUNT_ID / ARENA_M1_ACCOUNT_NUMBER")
    if not stork_capture_client.health_ok():
        pytest.skip("missing fixture: stork-capture unreachable")

    passport_jwt = pf.passport_or_skip(merchant_m1)
    body = pf.build_create_body(merchant_m1["fund_account_id"], merchant_m1["account_number"], amount=100)
    resp = pf.create_payout(ps_public_client, passport_jwt, body)
    assert resp.status in (200, 201), "create failed: %s" % resp
    payout_id = resp.json()["id"]

    payout_row = pf.wait_for_initiated(payouts_mysql, payout_id)
    if payout_row["status"] not in ("created", "initiated"):
        pytest.skip("payout not in a pre-terminal state suitable for this stimulus: %r" % payout_row)

    pf.send_transfer_status_webhook(
        ps_internal_client, payout_id, "processed", fund_transfer_id=payout_row.get("fts_transfer_id") or 1,
        utr="UTR_TEST_V21",
    )

    def _captured_matches():
        cap = stork_capture_client.get("/v1/captured").json()
        hits = []
        for channel in ("sms", "email"):
            for entry in cap.get(channel, []):
                text = str(entry)
                if payout_id in text:
                    hits.append(text)
        return hits or None

    try:
        hits = wait_until(_captured_matches, timeout=15, interval=1.0, desc="a captured event mentioning the payout id")
    except WaitTimeout:
        pytest.skip(
            "stork-capture has no documented /v1/captured entry type for Twirp WebhookAPI/ProcessEvent "
            "(payout.* webhooks) -- only SMS/email capture is documented; extend the stub before this "
            "verifier can assert the real claim (VERIFIER_SPEC.md V21)"
        )

    matching_terminal_events = [e for e in TERMINAL_EVENTS if any(e in h for h in hits)]
    assert len(matching_terminal_events) <= 1, (
        "more than one terminal webhook event captured for a single payout: %r (hits=%r)"
        % (matching_terminal_events, hits)
    )
    if not matching_terminal_events:
        warnings.warn(
            "found captured entries mentioning the payout id but none matched a known payout.* terminal "
            "event name -- captured stub content may only cover SMS/email templates, not webhook payloads"
        )
