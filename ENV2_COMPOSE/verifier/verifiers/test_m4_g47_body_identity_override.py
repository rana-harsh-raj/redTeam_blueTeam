"""G47 -- Request-body ``merchant_id`` / ``account_number`` cannot move a payout
onto another tenant. Identity is the edge-minted passport consumer
(payouts core.go:415-418 overwrites params.MerchantID; authHelper.go:13-25).
"""
import pytest

from helpers import payouts_flow as pf
from helpers import m4_boundary as m4
from helpers.m4_boundary import hardened_edge, merchant_m1, merchant_m2, make_isolated_scenario  # noqa: F401

isolated_scenario = make_isolated_scenario("hold")


@pytest.mark.spec_id("G47")
@pytest.mark.status("END_TO_END_CONFIRMED")
@pytest.mark.parametrize("direction", ["m1_as_m2", "m2_as_m1"])
def test_body_merchant_id_and_account_number_cannot_override_identity(
        direction, hardened_edge, merchant_m1, merchant_m2, payouts_mysql, ledger_pg, xbalances_mysql):
    """G47 body identity override.

    Invariant (formal): for a create request authenticated as A, the persisted
    payout satisfies merchant_id == A and balance_id in balances(A), for every
    body value of merchant_id / account_number; and state(B) is unchanged.

    Setup: snapshot B's merchant state (payout counts, banking account, ledger /
    x-balances balance). A authenticates with its own API key through kong-lite.

    Action:
      a. body merchant_id = B, fund_account_id/account_number = A's own -> expect
         200 and the payout persisted under A (override discarded), B untouched.
      b. body merchant_id = B AND account_number = B's -> expect 400 (B's account
         is not resolvable inside A's scope), no payout persisted for A or B.
      c. body merchant_id = "short" (malformed, len != 14) -> 400 validation.
    Negative control: body merchant_id = A (the monolith-shaped request) -> 200 under A.

    Expected observable effect: response.merchant_id == A and DB row
    merchant_id == A, balance_id == A's balance for (a) and the control;
    (b),(c) leave no payouts row (reference_id marker absent); B's state snapshot
    identical before/after.

    Evidence source: trace jsonl (http_*, m4_observation, m4_state_snapshot, db_read).
    Fidelity level: END_TO_END_CONFIRMED.
    """
    attacker, victim = (merchant_m1, merchant_m2) if direction == "m1_as_m2" else (merchant_m2, merchant_m1)
    a = m4.merchant_edge_or_fail(attacker)
    victim_before = m4.merchant_state(payouts_mysql, victim, ledger_pg, xbalances_mysql)
    marker = "m4g47-%s" % direction

    def create(body_merchant_id, account_number, label, idem=None):
        body = pf.build_create_body(attacker["fund_account_id"], account_number, amount=100,
                                    queue_if_low_balance=True, mode="IMPS",
                                    extra={"merchant_id": body_merchant_id, "reference_id": "%s-%s" % (marker, label)})
        resp = a.post("/v1/payouts", body=body, headers={"X-Payout-Idempotency": idem or pf.new_idempotency_key()})
        m4.observe(label, resp, None, body_merchant_id=body_merchant_id, account_number=account_number)
        return resp

    # negative control: the honest request
    control = create(attacker["merchant_id"], attacker["account_number"], "control_own_merchant_id")
    assert control.status in (200, 201), control
    assert control.json()["merchant_id"] == attacker["merchant_id"]

    # (a) body merchant_id = B
    override = create(victim["merchant_id"], attacker["account_number"], "body_merchant_id_is_victim")
    assert override.status in (200, 201, 400), override
    if override.status in (200, 201):
        assert override.json()["merchant_id"] == attacker["merchant_id"], \
            "payout created under body merchant_id, not the passport merchant: %s" % override.text
        row = m4.payout_row_state(payouts_mysql, override.json()["id"])
        assert row["merchant_id"] == attacker["merchant_id"], row
        assert row["balance_id"] == attacker["balance_id"], row
        assert row["balance_id"] != victim["balance_id"]

    # (b) body merchant_id = B and B's account_number
    both = create(victim["merchant_id"], victim["account_number"], "body_merchant_and_account_are_victim")
    assert both.status == 400, "B's account number must not resolve under A: %s" % both

    # (c) malformed merchant_id
    malformed = create("short", attacker["account_number"], "body_merchant_id_malformed")
    assert malformed.status == 400, malformed

    # persisted rows: every row carrying this test's marker belongs to A
    rows = pf.db.fetchall(payouts_mysql,
                          "SELECT id, merchant_id, balance_id, reference_id FROM payouts WHERE reference_id LIKE %s",
                          (marker + "%",))
    assert rows, "control create must have persisted"
    bad = [r for r in rows if r["merchant_id"] != attacker["merchant_id"] or r["balance_id"] != attacker["balance_id"]]
    assert not bad, "rows persisted outside A's scope: %r" % bad
    assert not [r for r in rows if r["reference_id"].endswith("are_victim") or r["reference_id"].endswith("malformed")]

    victim_after = m4.merchant_state(payouts_mysql, victim, ledger_pg, xbalances_mysql)
    m4.assert_state_unchanged(victim_before, victim_after, "victim %s state" % victim["key"])
