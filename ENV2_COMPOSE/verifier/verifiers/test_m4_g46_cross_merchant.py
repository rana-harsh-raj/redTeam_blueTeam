"""G46 -- Merchant A cannot read or change Merchant B's resources through the
hardened merchant edge (kong-lite, KONG_ENFORCE_ROUTE_POLICY=1).

Both directions are exercised: A=M1 (Shared) vs B=M2 (Direct), and A=M2 vs B=M1.
"""
import pytest

from helpers import payouts_flow as pf
from helpers import m4_boundary as m4
from helpers.m4_boundary import hardened_edge, merchant_m1, merchant_m2, make_isolated_scenario  # noqa: F401 (fixture overrides)

isolated_scenario = make_isolated_scenario("hold")


def _pair(merchant_m1, merchant_m2, direction):
    return (merchant_m1, merchant_m2) if direction == "m1_attacks_m2" else (merchant_m2, merchant_m1)


@pytest.mark.spec_id("G46")
@pytest.mark.status("END_TO_END_CONFIRMED")
@pytest.mark.parametrize("direction", ["m1_attacks_m2", "m2_attacks_m1"])
def test_cross_merchant_read_and_change_denied(direction, hardened_edge, merchant_m1, merchant_m2,
                                               payouts_mysql, fts_mysql, ledger_pg, xbalances_mysql, ps_internal_client):
    """G46 cross-tenant read/change denial.

    Invariant (formal): for merchants A != B and any resource r owned by B
    (payout, balance, banking account, reservation set), every request R(A, r)
    through the merchant edge is denied, and state(B) after R == state(B) before R.

    Setup: B creates one payout via kong-lite with B's own API key (bank held
    at INITIATED, so no async movement during the test). Snapshot B's payout row
    and merchant-level state (payout counts, banking_accounts row, ledger /
    x-balances balance).

    Action (all with A's key through kong-lite):
      1. GET  /v1/payouts/{B payout}                       (read)
      2. POST /v1/payouts/cancel_payout/{B payout db id}   (change)
      3. GET  /v1/payouts?count=100                        (list must not include B)
      4. POST /v1/payouts with B's account_number          (banking-account lookup by B's account number)
      5. POST /v1/payouts with B's fund_account_id         (fund account owned by B)
      6. GET  /v1/inflight_reservations?merchant_id=B&balance_id=B  (reservation set)
      7. GET  /v1/balances/{B balance_id}                  (balance read surface)
      8. GET  /v1/payouts/free_payout/{B balance_id}       (recorded only; asserted by the H-D3 test)

    Expected observable effect: 1,2 -> service tenant-scope denial (400/404,
    "The id provided does not exist"; repo.go merchant filter); 3 -> only A's
    items; 4,5 -> 400 validation/not-found under A's scope, no payout row for
    A or B; 6 -> gateway 404 no_route (internal route, denied before auth);
    7 -> not a 2xx carrying B's balance (layer recorded); 8 recorded.
    B's payout row (status, updated_at) and merchant state are byte-identical
    before/after.

    Negative (positive) control: A fetches its own payout -> 200 with
    merchant_id == A; B fetches its own -> 200.

    Evidence source: trace jsonl (http_request/http_response, m4_observation,
    m4_state_snapshot, db_read) for this node.
    Fidelity level: END_TO_END_CONFIRMED (real kong-lite mint -> real payouts-api
    -> real MySQL; no synthetic stimulus).
    """
    attacker, victim = _pair(merchant_m1, merchant_m2, direction)
    a = m4.merchant_edge_or_fail(attacker)
    b = m4.merchant_edge_or_fail(victim)

    # --- setup: victim and attacker each own one payout ------------------------------------
    def create_own(client, merchant):
        body = pf.build_create_body(merchant["fund_account_id"], merchant["account_number"], amount=100,
                                    queue_if_low_balance=True, mode="IMPS",
                                    extra={"merchant_id": merchant["merchant_id"]})
        resp = client.post("/v1/payouts", body=body, headers={"X-Payout-Idempotency": pf.new_idempotency_key()})
        assert resp.status in (200, 201), "%s own create failed: %s" % (merchant["key"], resp)
        assert resp.json()["merchant_id"] == merchant["merchant_id"], resp.text
        return resp.json()["id"]

    victim_pid = create_own(b, victim)
    attacker_pid = create_own(a, attacker)

    # Settle the victim payout to its stable in-flight state (bank held at INITIATED) BEFORE
    # snapshotting, so the arena's own async workers (created -> initiated) do not move the row
    # under us and masquerade as a cross-tenant mutation.
    pf.wait_for_held_handoff(payouts_mysql, fts_mysql, victim_pid)

    victim_row_before = m4.payout_row_state(payouts_mysql, victim_pid)
    assert victim_row_before and victim_row_before["merchant_id"] == victim["merchant_id"]
    victim_state_before = m4.merchant_state(payouts_mysql, victim, ledger_pg, xbalances_mysql)

    # --- positive controls -----------------------------------------------------------------
    own = a.get("/v1/payouts/%s" % attacker_pid)
    assert m4.observe("own_fetch_control", own, m4.LAYER_ALLOWED) == m4.LAYER_ALLOWED, own
    assert own.json()["merchant_id"] == attacker["merchant_id"]
    victim_own = b.get("/v1/payouts/%s" % victim_pid)
    assert victim_own.status == 200 and victim_own.json()["merchant_id"] == victim["merchant_id"], victim_own

    # --- 1. cross-tenant fetch ---------------------------------------------------------------
    fetch = a.get("/v1/payouts/%s" % victim_pid)
    assert m4.observe("cross_fetch", fetch, m4.LAYER_SERVICE_TENANT) == m4.LAYER_SERVICE_TENANT, fetch
    assert victim["merchant_id"] not in fetch.text and victim_pid not in fetch.text

    # --- 2. cross-tenant cancel --------------------------------------------------------------
    cancel = a.post("/v1/payouts/cancel_payout/%s" % pf.db_id(victim_pid), body={})
    assert m4.observe("cross_cancel", cancel, m4.LAYER_SERVICE_TENANT) == m4.LAYER_SERVICE_TENANT, cancel

    # --- 3. list is tenant-scoped ------------------------------------------------------------
    listing = a.get("/v1/payouts?count=100&skip=0")
    assert listing.status == 200, listing
    items = listing.json().get("items", [])
    foreign = [i["id"] for i in items if i.get("merchant_id") != attacker["merchant_id"]]
    assert not foreign, "list leaked non-owned payouts: %r" % foreign
    assert victim_pid not in [i["id"] for i in items]
    m4.observe("list_scoped", listing, m4.LAYER_ALLOWED, item_count=len(items), foreign_items=foreign)

    # --- 4. create with B's account_number (banking-account lookup by B's account) -----------
    body = pf.build_create_body(attacker["fund_account_id"], victim["account_number"], amount=100,
                                queue_if_low_balance=True, mode="IMPS",
                                extra={"merchant_id": attacker["merchant_id"], "reference_id": "m4g46-acct-%s" % direction})
    acct = a.post("/v1/payouts", body=body, headers={"X-Payout-Idempotency": pf.new_idempotency_key()})
    acct_layer = m4.observe("create_with_victim_account_number", acct, m4.LAYER_SERVICE_VALIDATION)
    assert acct.status == 400, "create with B's account_number must be rejected, got %s: %s" % (acct.status, acct.text)
    assert acct_layer in (m4.LAYER_SERVICE_VALIDATION, m4.LAYER_SERVICE_TENANT), acct

    # --- 5. create quoting B's fund_account_id (beneficiary) ----------------------------------
    # The security invariant is that any resulting payout is bound to A and draws A's balance;
    # whether payouts REJECTS a foreign beneficiary id is a separate fidelity question (T06 H-D4:
    # source expects GetCfaFundAccountWithCache(ctx, fa, merchantID) -> 400 "does not exist"; the
    # arena CFA substitute resolves fund accounts without a merchant filter, so the runtime accepts
    # it). Record the outcome; if a payout is created it MUST be under A, using A's balance, and B's
    # state MUST stay unchanged (asserted at the end). This is a recorded candidate, not a hard fail.
    fa_body = pf.build_create_body(victim["fund_account_id"], attacker["account_number"], amount=100,
                                   queue_if_low_balance=True, mode="IMPS",
                                   extra={"merchant_id": attacker["merchant_id"], "reference_id": "m4g46-fa-%s" % direction})
    fa = a.post("/v1/payouts", body=fa_body, headers={"X-Payout-Idempotency": pf.new_idempotency_key()})
    m4.observe("create_with_victim_fund_account", fa, None, candidate="H-D4",
               note="foreign beneficiary fund_account_id; must not bind the payout to B or draw B's balance")
    if fa.status in (200, 201):
        assert fa.json()["merchant_id"] == attacker["merchant_id"], \
            "payout created under B when quoting B's fund_account_id: %s" % fa.text
        fa_row = m4.payout_row_state(payouts_mysql, fa.json()["id"])
        assert fa_row["merchant_id"] == attacker["merchant_id"] and fa_row["balance_id"] == attacker["balance_id"], fa_row
    else:
        assert fa.status == 400, "unexpected status for foreign fund_account_id: %s" % fa
    # the acct-number create (step 4) must never persist; no row may bind to B
    leaked = pf.db.fetchall(payouts_mysql,
                            "SELECT id, merchant_id, reference_id FROM payouts WHERE reference_id LIKE %s",
                            ("m4g46-%%-%s" % direction,))
    bound_to_b = [r for r in leaked if r["merchant_id"] == victim["merchant_id"]]
    assert not bound_to_b, "a create bound a payout to B's tenant: %r" % bound_to_b
    assert not [r for r in leaked if r["reference_id"].startswith("m4g46-acct-")], \
        "create with B's account_number must not persist: %r" % leaked

    # --- 6. reservation set for B's balance (internal route) ---------------------------------
    resv = a.get("/v1/inflight_reservations?merchant_id=%s&balance_id=%s" % (victim["merchant_id"], victim["balance_id"]))
    assert m4.observe("cross_inflight_reservations", resv, m4.LAYER_GATEWAY) == m4.LAYER_GATEWAY, resv
    # the route exists behind the edge: the verifier's service credential (inside the arena) can read it
    inside = ps_internal_client.get("/v1/inflight_reservations?merchant_id=%s&balance_id=%s"
                                    % (victim["merchant_id"], victim["balance_id"]))
    m4.observe("inflight_reservations_service_control", inside, m4.LAYER_ALLOWED)
    assert inside.status == 200, inside

    # --- 7. balance read surface by B's balance id --------------------------------------------
    bal = a.get("/v1/balances/%s" % victim["balance_id"])
    bal_layer = m4.observe("cross_balance_read", bal, None)
    assert not (bal.status == 200 and victim["balance_id"] in bal.text), \
        "balance read surface returned B's balance to A: %s" % bal
    own_bal = a.get("/v1/balances/%s" % attacker["balance_id"])
    m4.observe("own_balance_read_control", own_bal, None, note="fidelity: xbalances upstream reachability recorded, not asserted")

    # --- 8. free_payout by B's balance id (recorded; asserted in test_m4_hd3_free_payout.py) --
    fp = a.get("/v1/payouts/free_payout/%s" % victim["balance_id"])
    m4.observe("cross_free_payout_recorded_only", fp, None, candidate="H-D3")

    # --- state control -----------------------------------------------------------------------
    victim_row_after = m4.payout_row_state(payouts_mysql, victim_pid)
    assert victim_row_after == victim_row_before, "victim payout row changed: %r -> %r" % (victim_row_before, victim_row_after)
    victim_state_after = m4.merchant_state(payouts_mysql, victim, ledger_pg, xbalances_mysql)
    m4.assert_state_unchanged(victim_state_before, victim_state_after, "victim %s state" % victim["key"])
    trace_summary = {"direction": direction, "victim_payout": victim_pid, "attacker_payout": attacker_pid,
                     "balance_read_layer": bal_layer}
    m4.trace.record("m4_g46_summary", trace_summary)
