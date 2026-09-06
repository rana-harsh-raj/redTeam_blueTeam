"""H-D3 probe (as a TEST, not a finding) -- GET /v1/payouts/free_payout/{balance_id}
resolves the banking account by balance_id with NO merchant scoping
(freePayout/core.go GetFreePayoutAttributes -> GetBankingAccountByBalanceID),
unlike the scoped payout fetch. This test RECORDS the runtime outcome and, if
the runtime leaks another tenant's balance config, is marked xfail (candidate
for autonomous replay), never asserting a pass.
"""
import pytest

from helpers import payouts_flow as pf
from helpers import m4_boundary as m4
from helpers.m4_boundary import hardened_edge, merchant_m1, merchant_m2, make_isolated_scenario  # noqa: F401

isolated_scenario = make_isolated_scenario("hold")


@pytest.mark.spec_id("H-D3")
@pytest.mark.status("CODE_CANDIDATE")
@pytest.mark.xfail(strict=False, reason="candidate H-D3 -- for autonomous replay: free_payout has no merchant scoping")
def test_free_payout_cross_tenant_ownership(hardened_edge, merchant_m1, merchant_m2, payouts_mysql):
    """H-D3 free_payout ownership gap.

    Invariant (formal, DESIRED): GET /v1/payouts/free_payout/{balance_id} for a
    balance not owned by the caller is denied (404/400), matching the scoped
    payout fetch. The handler currently omits the merchant filter, so this
    invariant may not hold at runtime -- hence xfail(strict=False).

    Setup: A = M1, B = M2, each with a real seeded balance
    (ARENABAL000001 / ARENABAL000002).

    Action:
      * own control:   A GET free_payout/{A balance}   -> expect 200 (A's config)
      * malformed ctrl: A GET free_payout/NOTABALANCE  -> expect 400 invalid balance id
      * scoped compare: A GET /v1/payouts/free_payout is public+allowed, while
                        cross-tenant payout fetch (recorded in G46) is 404 --
                        the contrast localizes the gap to free_payout.
      * probe:         A GET free_payout/{B balance}   -> RECORD status+body; the
                        DESIRED-secure assertion is that this is denied. If it
                        returns 200 with B's free-payout config, the assertion
                        fails and the test xfails (candidate, not a suite failure).

    Expected observable effect: recorded verbatim; own control 200; malformed 400;
    no state mutation (free_payout is a read).

    Evidence source: trace jsonl (m4_hd3_probe with raw request/response).
    Fidelity level: CODE_CANDIDATE (routes to the autonomous judge/replay).
    """
    a = m4.merchant_edge_or_fail(merchant_m1)

    own = a.get("/v1/payouts/free_payout/%s" % merchant_m1["balance_id"])
    m4.observe("hd3_own_control", own, m4.LAYER_ALLOWED)
    assert own.status == 200, "own free_payout control must be 200: %s" % own

    malformed = a.get("/v1/payouts/free_payout/NOTABALANCE")
    m4.observe("hd3_malformed_control", malformed, m4.LAYER_SERVICE_VALIDATION)
    assert malformed.status == 400, "malformed balance id must be 400: %s" % malformed

    cross = a.get("/v1/payouts/free_payout/%s" % merchant_m2["balance_id"])
    layer = m4.classify_denial(cross)
    m4.trace.record("m4_hd3_probe", {
        "attacker": merchant_m1["merchant_id"], "victim_balance_id": merchant_m2["balance_id"],
        "request": {"method": "GET", "path": "/v1/payouts/free_payout/%s" % merchant_m2["balance_id"],
                    "auth_as": a.key_id},
        "response": {"status": cross.status, "body": cross.text[:1000], "observed_layer": layer},
        "own_control_status": own.status, "malformed_control_status": malformed.status,
        "candidate": "H-D3", "disposition": "route to autonomous replay if 200 with foreign balance config"})

    # DESIRED-secure expectation (xfail if the runtime leaks): a non-owned balance
    # must be denied the way the scoped payout fetch is.
    assert cross.status in (400, 404), \
        "H-D3 candidate: free_payout returned %s for a non-owned balance (body=%s)" % (cross.status, cross.text[:300])
