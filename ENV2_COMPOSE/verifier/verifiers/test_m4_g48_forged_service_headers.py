"""G48 -- Client-supplied service headers / forged passports / service-shaped
credentials confer no privilege. Identity stays the edge-minted passport
consumer; internal routes stay denied.
"""
import pytest

from helpers import payouts_flow as pf
from helpers import m4_boundary as m4
from helpers.m4_boundary import hardened_edge, merchant_m1, merchant_m2, make_isolated_scenario  # noqa: F401

isolated_scenario = make_isolated_scenario("hold")


@pytest.mark.spec_id("G48")
@pytest.mark.status("END_TO_END_CONFIRMED")
def test_client_supplied_service_headers_and_creds_do_not_escalate(
        hardened_edge, merchant_m1, merchant_m2, payouts_mysql, ledger_pg, xbalances_mysql, ps_service_auth):
    """G48 client header / credential forgery.

    Invariant (formal): no client-supplied header or credential changes the
    authenticated identity or grants access to a route the caller's minted
    passport does not already authorize.

    Setup: A = M1 authenticates with its own API key. Snapshot M2 state.

    Action / expected observable effect:
      1. GET own payout list with forged service headers attached
         (X-Service-Name, X-Task-Id, X-Passport-Actor-Type, X-Razorpay-Account:
         acc_M2, x-merchant-id: M2, X-Entity-Id: M2, and each forged
         X-Passport-JWT-V1 shape: alg=none, HS256, wrong-kid RS256,
         expired-unsigned RS256, genuine-header-tampered-payload) -> 200 and
         every returned item.merchant_id == M1 (edge overwrites the header and
         mints its own passport; kong strips client x-passport*).
      2. Forged passports sent to payouts-api DIRECTLY with a valid service
         Basic cred (bypassing the edge mint) -> payouts rejects them (401/500,
         no www-authenticate JWT bypass), proving the SDK, not just the edge,
         refuses non-RS256 / unknown-kid / expired tokens. Negative control:
         no passport at all -> 401.
      3. Service-credential-shaped Basic values presented at the merchant edge
         (username api / fts / rzp_live / x_balances ...) -> edge 401 (not a
         valid merchant key), and the internal route stays gateway-404.
    Negative control: the same list fetch with NO extra headers -> 200 M1 items.
    State control: M2 snapshot unchanged.

    Evidence source: trace jsonl. Fidelity level: END_TO_END_CONFIRMED
    (direct-to-payouts leg is CROSS_SERVICE for the SDK rejection).
    """
    a = m4.merchant_edge_or_fail(merchant_m1)
    victim = merchant_m2
    victim_before = m4.merchant_state(payouts_mysql, victim, ledger_pg, xbalances_mysql)

    control = a.get("/v1/payouts?count=100")
    assert control.status == 200, control
    control_items = {i["id"] for i in control.json().get("items", [])}
    assert all(i.get("merchant_id") == merchant_m1["merchant_id"] for i in control.json().get("items", []))

    forged = m4.forged_passports(victim["merchant_id"])
    for name, token in forged.items():
        headers = {"X-Service-Name": "payouts-admin", "X-Task-Id": "task_forged", "X-Payout-Actor-Type": "application",
                   "X-Razorpay-Account": "acc_" + victim["merchant_id"], "x-merchant-id": victim["merchant_id"],
                   "X-Entity-Id": victim["merchant_id"], "X-Passport-JWT-V1": token}
        resp = a.get("/v1/payouts?count=100", headers=headers)
        m4.observe("edge_forged_headers_%s" % name, resp, m4.LAYER_ALLOWED)
        assert resp.status == 200, "forged headers must not break the request: %s -> %s" % (name, resp)
        items = resp.json().get("items", [])
        assert all(i.get("merchant_id") == merchant_m1["merchant_id"] for i in items), \
            "forged identity (%s) leaked into the response: %s" % (name, resp.text[:300])
        # every item is A's and none is B's -- an exact-set compare against the control would be
        # brittle if another test/agent creates an M1 payout between fetches, so assert the invariant
        # (tenant scope), not set stability.
        assert victim["merchant_id"] not in resp.text, "forged headers %s leaked B into the list" % name

    # 2. forged passport straight to payouts-api with a valid service credential
    direct = m4.ArenaHTTPClient(m4.os.environ.get("PS_PUBLIC_URL", "http://payouts-api:9400"), basic_auth=ps_service_auth)
    no_passport = direct.get("/v1/payouts")
    m4.observe("direct_service_no_passport", no_passport, None)
    assert no_passport.status in (400, 401), "service cred without a passport must not read payouts: %s" % no_passport
    for name, token in forged.items():
        resp = direct.get("/v1/payouts", headers={"X-Passport-JWT-V1": token})
        layer = m4.observe("direct_forged_passport_%s" % name, resp, None)
        assert resp.status >= 400, "payouts-api accepted a forged passport (%s): %s" % (name, resp)
        assert layer != m4.LAYER_ALLOWED
        assert victim["merchant_id"] not in resp.text or resp.status >= 400

    # 3. service-credential-shaped Basic values at the merchant edge
    for user, pw in m4.service_shaped_basic_credentials():
        c = m4.raw_basic_client(user, pw)
        pub = c.get("/v1/payouts?count=1")
        m4.observe("edge_service_cred_public_%s" % user, pub, m4.LAYER_EDGE_AUTH)
        assert pub.status == 401, "service-shaped cred %s must not authenticate at the merchant edge: %s" % (user, pub)
        internal = c.get("/v1/payouts/manual_action")
        assert m4.observe("edge_service_cred_internal_%s" % user, internal, m4.LAYER_GATEWAY) == m4.LAYER_GATEWAY, internal

    victim_after = m4.merchant_state(payouts_mysql, victim, ledger_pg, xbalances_mysql)
    m4.assert_state_unchanged(victim_before, victim_after, "victim M2 state")
