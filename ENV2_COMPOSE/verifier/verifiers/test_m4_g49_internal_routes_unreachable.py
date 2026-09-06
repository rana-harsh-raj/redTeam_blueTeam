"""G49 -- Internal-only routes are unreachable from merchant credentials through
kong-lite (hardened), and the denial is at the gateway (not the absence of the
route): the same route reached from inside the arena network with a service
credential is non-404.
"""
import pytest

from helpers import m4_boundary as m4
from helpers.m4_boundary import hardened_edge, merchant_m1, make_isolated_scenario  # noqa: F401

isolated_scenario = make_isolated_scenario("hold")


@pytest.mark.spec_id("G49")
@pytest.mark.status("END_TO_END_CONFIRMED")
def test_internal_routes_unreachable_from_merchant_edge(hardened_edge, merchant_m1, ps_internal_client):
    """G49 internal-route unreachability + gateway-not-absence proof.

    Invariant (formal): for every internal route group I (payouts_internal,
    inflight_reservations, /v1/internal, admin/dev_admin, cron,
    banking_account_statement, workflow, notify, twirp, not-implemented CRUD),
    a merchant-authenticated request through the hardened edge returns gateway
    404 no_route and carries no canned cross-tenant markers; yet the route
    exists (a service-credential request from inside the arena to a harmless
    GET returns non-404).

    Setup: enumerate probes from T06's handoff list plus every denied-route
    regex in RED_LOOP/registry/merchant-gateway-routes.json (mounted read-only).
    A = M1 authenticates with its own API key.

    Action: each probe through kong-lite with A's key.
    Expected observable effect: HTTP 404, body error == "no_route", no marker
    in {slitfa12345678, 10000000000000, 528226169544}.

    Negative control / gateway-not-absence proof: the read-only internal route
    GET /v1/inflight_reservations reached DIRECTLY (inside the arena network,
    service Basic cred) returns 200 -- so the merchant-edge 404 is the gateway
    refusing to route, not the route being absent. (No mutating internal route
    is exercised.)

    Evidence source: trace jsonl (m4_observation per probe, m4_g49_matrix).
    Fidelity level: END_TO_END_CONFIRMED.
    """
    a = m4.merchant_edge_or_fail(merchant_m1)
    probes = m4.all_internal_probes()
    assert len(probes) >= len(m4.T06_INTERNAL_PROBES), "probe enumeration collapsed"

    matrix = []
    failures = []
    for method, path in probes:
        resp = a.request(method, path)
        layer = m4.observe("internal_probe", resp, m4.LAYER_GATEWAY, method=method, path=path)
        leak = m4.has_canned_markers(resp.text)
        ok = (layer == m4.LAYER_GATEWAY) and not leak
        matrix.append({"method": method, "path": path, "status": resp.status, "layer": layer, "canned_leak": leak, "ok": ok})
        if not ok:
            failures.append((method, path, resp.status, resp.text[:200]))
    m4.trace.record("m4_g49_matrix", {"probe_count": len(matrix), "all_denied_at_gateway": not failures, "matrix": matrix})
    assert not failures, "internal routes not denied at the gateway (or leaked canned data): %r" % failures

    # gateway-not-absence: the route is reachable behind the edge with a service credential
    inside = ps_internal_client.get(
        "/v1/inflight_reservations?merchant_id=%s&balance_id=%s" % (merchant_m1["merchant_id"], merchant_m1["balance_id"]))
    assert m4.observe("internal_route_exists_behind_edge", inside, m4.LAYER_ALLOWED) == m4.LAYER_ALLOWED, inside
    assert inside.status != 404, "the internal route must exist behind the edge (proving edge denial != absence): %s" % inside
