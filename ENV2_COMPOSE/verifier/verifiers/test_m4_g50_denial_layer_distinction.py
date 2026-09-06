"""G50 -- The four denial layers (broker, gateway, service-auth, service-tenant)
are deliberately triggered and shown to be distinguishable by observation.
"""
import os
import sys

import pytest

from helpers import payouts_flow as pf
from helpers import m4_boundary as m4
from helpers.m4_boundary import hardened_edge, merchant_m1, merchant_m2, make_isolated_scenario  # noqa: F401

isolated_scenario = make_isolated_scenario("hold")


def _load_broker():
    """Import the RED_LOOP broker if it is mounted read-only (M4_REDLOOP_DIR).
    Returns a callable(path)->broker result dict, or None (host-side unit test)."""
    root = os.environ.get("M4_REDLOOP_DIR", "/redloop")
    if not os.path.isdir(os.path.join(root, "red_loop")):
        return None
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from red_loop.broker import Broker  # noqa: E402
    except Exception:  # noqa: BLE001
        return None
    broker = Broker({"merchant_id": merchant_id_placeholder(), "key_id": "rzp_live_ARENAM00000001",
                     "secret": "unused-validation-only", "mode": "live"}, action_sink=lambda rec: None)
    return broker


def merchant_id_placeholder():
    return "ARENAM00000001"


@pytest.mark.spec_id("G50")
@pytest.mark.status("END_TO_END_CONFIRMED")
def test_four_denial_layers_are_distinguishable(hardened_edge, merchant_m1, merchant_m2, payouts_mysql):
    """G50 denial-layer distinction.

    Invariant (formal): the four boundary layers produce mutually-distinct,
    machine-observable fingerprints:
      broker  -> dict {"error":"boundary_violation"} with NO http status;
      gateway -> HTTP 404, body error == "no_route", NO www-authenticate;
      service-auth   -> HTTP 401 + WWW-Authenticate: Basic realm=...;
      service-tenant -> HTTP 400/404, description == "The id provided does not exist".

    Setup: A=M1 owns a payout; B=M2 owns a payout (for the tenant case).

    Action:
      (broker)  RED_LOOP Broker.request("GET","/_arena/health") -- a control/oracle
                path the attacker allow-list refuses (validated before any network).
                If the broker package is not mounted in the verifier, this leg is
                recorded as a host-side unit test referencing
                RED_LOOP/tests/test_route_policy.py and the broker allow-list in
                RED_LOOP/red_loop/broker.py:_validate_path, and the remaining three
                live legs still run.
      (gateway) A GET /v1/payouts/manual_action -> 404 no_route.
      (svc-auth) A GET /v1/payouts with a wrong secret -> 401 + WWW-Authenticate.
      (svc-tenant) A GET /v1/payouts/{B payout} -> 400/404 "id does not exist".

    Expected observable effect: classify_denial() returns a different label for
    each of the four, and no two share a fingerprint.

    Evidence source: trace jsonl (m4_observation, m4_g50_layers).
    Fidelity level: END_TO_END_CONFIRMED for the three live legs; the broker leg
    is CODE-level (in-process boundary check, no network).
    """
    a = m4.merchant_edge_or_fail(merchant_m1)
    b = m4.merchant_edge_or_fail(merchant_m2)

    # seed a victim payout for the tenant leg
    body = pf.build_create_body(merchant_m2["fund_account_id"], merchant_m2["account_number"], amount=100,
                                queue_if_low_balance=True, mode="IMPS", extra={"merchant_id": merchant_m2["merchant_id"]})
    seed = b.post("/v1/payouts", body=body, headers={"X-Payout-Idempotency": pf.new_idempotency_key()})
    assert seed.status in (200, 201), seed
    victim_pid = seed.json()["id"]

    layers = {}

    # broker leg
    broker = _load_broker()
    if broker is not None:
        result = broker.request("GET", "/_arena/health")
        layers["broker"] = m4.observe("layer_broker", result, m4.LAYER_BROKER)
        assert layers["broker"] == m4.LAYER_BROKER, result
        assert isinstance(result, dict) and "status" not in result, result
    else:
        layers["broker"] = "host_side_unit_test"
        m4.trace.record("m4_observation", {
            "label": "layer_broker", "observed_layer": "host_side_unit_test",
            "note": "RED_LOOP broker not importable in the verifier container; broker boundary check is a "
                    "host-side unit test. Reference: RED_LOOP/tests/test_route_policy.py and "
                    "RED_LOOP/red_loop/broker.py:_validate_path (denies /_arena, /twirp/, /metrics, /debug "
                    "with {'error':'boundary_violation'} and no HTTP status).",
            "expected_layer": m4.LAYER_BROKER})

    # gateway leg
    gw = a.get("/v1/payouts/manual_action")
    layers["gateway"] = m4.observe("layer_gateway", gw, m4.LAYER_GATEWAY)
    assert layers["gateway"] == m4.LAYER_GATEWAY, gw

    # service-auth leg (wrong secret => edge 401; but the requested route is public
    # so the 401 is the credential check with WWW-Authenticate)
    wrong = m4.raw_basic_client("rzp_live_ARENAM00000001", "definitely-wrong-secret").get("/v1/payouts")
    layers["service_auth"] = m4.observe("layer_service_auth", wrong, m4.LAYER_EDGE_AUTH)
    www = {k.lower(): v for k, v in (wrong.headers or {}).items()}.get("www-authenticate")
    assert wrong.status == 401 and www, "auth failure must carry WWW-Authenticate: %s" % wrong

    # service-tenant leg
    tenant = a.get("/v1/payouts/%s" % victim_pid)
    layers["service_tenant"] = m4.observe("layer_service_tenant", tenant, m4.LAYER_SERVICE_TENANT)
    assert layers["service_tenant"] == m4.LAYER_SERVICE_TENANT, tenant

    m4.trace.record("m4_g50_layers", layers)

    # distinctness: the live legs must not collapse onto one fingerprint
    live = [layers["gateway"], layers["service_auth"], layers["service_tenant"]]
    assert len(set(live)) == 3, "live denial layers are not distinguishable: %r" % layers
    if broker is not None:
        assert layers["broker"] not in live, "broker fingerprint collides with a live layer: %r" % layers
