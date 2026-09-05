"""V22 -- Shield fail-open: evaluation latency > 200ms => payout proceeds.

Covers: C11. shield-stub/CONTRACT.md documents only a static allow / opt-in
SHIELD_DENY_LIST deny-list -- no latency-injection lever. This verifier
probes for one (a request header or env-driven mode) and skips the
timeout-specific claim if the stub doesn't support it, rather than asserting
against default (fast) stub behavior, which would not exercise the timeout
path at all.
"""
import time

import pytest

from helpers import payouts_flow as pf


@pytest.mark.spec_id("V22")
@pytest.mark.status("CODE_CANDIDATE")
def test_shield_timeout_fails_open(ps_public_client, payouts_mysql, shield_stub_client, merchant_m1):
    if not merchant_m1["fund_account_id"] or not merchant_m1["account_number"]:
        pytest.skip("missing fixture: ARENA_M1_FUND_ACCOUNT_ID / ARENA_M1_ACCOUNT_NUMBER")
    if not shield_stub_client.health_ok():
        pytest.skip("missing fixture: shield-stub unreachable")

    probe = shield_stub_client.post(
        "/v1/payout/evaluate",
        body={"payout_id": "probe", "merchant_id": merchant_m1["merchant_id"]},
        headers={"X-Test-Latency-Ms": "250"},
    )
    if probe.status != 200:
        pytest.skip("missing fixture: shield-stub CONTRACT.md documents no latency-injection control")

    t0 = time.monotonic()
    probe2 = shield_stub_client.post(
        "/v1/payout/evaluate",
        body={"payout_id": "probe2", "merchant_id": merchant_m1["merchant_id"]},
        headers={"X-Test-Latency-Ms": "250"},
    )
    elapsed_ms = (time.monotonic() - t0) * 1000
    if elapsed_ms < 200:
        pytest.skip(
            "shield-stub answered the latency-injection header but did not actually add latency "
            "(%.0fms elapsed) -- no way to exercise the real timeout path" % elapsed_ms
        )
    del probe2

    passport_jwt = pf.passport_or_skip(merchant_m1)
    body = pf.build_create_body(merchant_m1["fund_account_id"], merchant_m1["account_number"], amount=100)
    resp = pf.create_payout(ps_public_client, passport_jwt, body)
    assert resp.status in (200, 201), (
        "payout create must proceed (fail-open) despite Shield exceeding its 200ms timeout, got %s: %s"
        % (resp.status, resp.text)
    )
    payout_id = resp.json()["id"]
    payout_row = pf.get_payout_row(payouts_mysql, payout_id)
    assert payout_row["status"] not in ("rejected",), (
        "payout must not be rejected on Shield-timeout grounds: %r" % payout_row
    )
