"""V23 -- Fee fail-closed: pricing dependency 500 => payout rejected.

Covers: C27. The real call payouts makes is POST
{monolith_base_url}/payouts_service/fetch_pricing_info (payouts/pkg/api/
fetch_pricing.go:15-16) -- i.e. through monolith-stub, NOT the standalone
pricing-stub container (pricing-stub/CONTRACT.md flags this exact
ambiguity itself). Neither stub's current CONTRACT.md documents this path
with fault injection, so this verifier probes monolith-stub first and skips
with a fixture-naming reason if unsupported, per VERIFIER_SPEC.md V23.
"""
import pytest

from helpers import payouts_flow as pf


@pytest.mark.spec_id("V23")
@pytest.mark.status("BLOCKED_BY_FIDELITY_GAP")
def test_pricing_500_rejects_payout(ps_public_client, payouts_mysql, monolith_stub_client, merchant_m1):
    if not merchant_m1["fund_account_id"] or not merchant_m1["account_number"]:
        pytest.skip("missing fixture: ARENA_M1_FUND_ACCOUNT_ID / ARENA_M1_ACCOUNT_NUMBER")
    if not monolith_stub_client.health_ok():
        pytest.skip("missing fixture: monolith-stub unreachable")

    probe = monolith_stub_client.post(
        "/payouts_service/fetch_pricing_info",
        body={"merchant_id": merchant_m1["merchant_id"], "amount": 100, "purpose": "payout"},
        headers={"X-Test-Fault": "500"},
    )
    if probe.status not in (200, 500):
        pytest.skip(
            "missing fixture: monolith-stub does not implement /payouts_service/fetch_pricing_info "
            "with fault injection yet -- see pricing-stub/CONTRACT.md TODO and VERIFIER_SPEC.md V23 "
            "(probe returned %s: %s)" % (probe.status, probe.text)
        )
    if probe.status != 500:
        pytest.skip(
            "monolith-stub answered /payouts_service/fetch_pricing_info but the X-Test-Fault=500 fault "
            "injection header had no effect (got %s) -- cannot exercise the fail-closed path" % probe.status
        )

    passport_jwt = pf.passport_or_skip(merchant_m1)
    body = pf.build_create_body(
        merchant_m1["fund_account_id"], merchant_m1["account_number"], amount=100,
        extra={"__test_fault_pricing": True},
    )
    resp = pf.create_payout(ps_public_client, passport_jwt, body)

    if resp.status in (200, 201):
        payout_id = resp.json().get("id")
        payout_row = pf.get_payout_row(payouts_mysql, payout_id) if payout_id else None
        assert payout_row is not None and payout_row["status"] == "failed", (
            "pricing dependency failure must fail-closed (reject or immediately fail the payout), "
            "got create status %s and payout row %r" % (resp.status, payout_row)
        )
    else:
        assert resp.status >= 400, "expected a non-2xx rejection on pricing failure, got %s" % resp.status
