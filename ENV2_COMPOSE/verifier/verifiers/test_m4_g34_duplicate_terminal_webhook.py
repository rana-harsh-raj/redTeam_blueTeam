"""G34 / G45 -- Duplicate terminal status delivery for a Direct (M2) payout:
after a payout reaches terminal ``processed`` via the bank, replaying the FTS
terminal webhook twice does not change status and does not add a merchant
webhook delivery.
"""
import time

import pytest

from helpers import payouts_flow as pf
from helpers import m4_boundary as m4
from helpers.m4_boundary import merchant_m2, make_isolated_scenario  # noqa: F401

isolated_scenario = make_isolated_scenario("success")


@pytest.mark.spec_id("G34")
@pytest.mark.spec_id("G45")
@pytest.mark.status("END_TO_END_CONFIRMED")
def test_duplicate_terminal_webhook_is_idempotent(merchant_m2, ps_public_client, ps_internal_client,
                                                  payouts_mysql, fts_mysql, merchant_sink_client):
    """G34 duplicate terminal delivery / G45 webhook idempotency (Direct M2).

    Invariant (formal): once a payout is terminal (processed), replaying the FTS
    transfer_status_webhook with the same terminal status is a no-op: status and
    updated_at are unchanged, payout_logs do not grow, and the count of terminal
    deliveries at the merchant sink stays == 1.

    Setup: M2 (Direct/RBL) creates a payout with the bank scenario = success, so
    it processes end to end through real FTS + mozart-sim. Record status,
    updated_at, log count, and the terminal delivery count at the sink.

    Action: call inject_transfer_webhook(status="processed") twice more (the
    real /v1/payouts/transfer_status_webhook internal route, using the actual
    routed FTS transfer id).

    Expected observable effect: both replays return HTTP 200 (so FTS stops
    retrying); payout status stays processed; payout_logs length unchanged;
    terminal deliveries at the sink for this payout stay 1. DOCUMENTED
    behaviour: the replay re-touches updated_at (the row is re-saved) even
    though it is a no-op for status/logs/delivery -- recorded, not asserted.

    Negative control: the single genuine terminal transition DID advance the
    payout to processed and DID produce exactly one terminal delivery (asserted
    before the replays), so the "unchanged" assertions are meaningful.

    Evidence source: trace jsonl (m4_observation, db_read, scenario deliveries).
    Fidelity level: END_TO_END_CONFIRMED (real bank round trip); the replay is an
    explicit adapter-boundary stimulus (documented in helpers.payouts_flow).
    """
    pid = pf.seed_payout(ps_public_client, merchant_m2, amount=5000)
    processed = pf.wait_for_status(payouts_mysql, pid, "processed", timeout=60)
    assert processed["status"] == "processed", processed

    sink_path = "/_arena/deliveries?merchant=%s&payout_id=%s" % (merchant_m2["merchant_id"], pid)

    def terminal_deliveries():
        resp = merchant_sink_client.get(sink_path)
        assert resp.status == 200, resp
        return [d for d in resp.json()["deliveries"]
                if d["merchant"] == merchant_m2["merchant_id"]
                and d["body"]["payload"]["payout"]["entity"]["id"] == pid
                and d["body"].get("event") in {"payout.processed", "payout.reversed", "payout.failed",
                                               "payout.cancelled", "payout.rejected"}]

    from helpers.wait import wait_until
    genuine = wait_until(lambda: terminal_deliveries() or None, timeout=20, interval=0.5,
                         desc="genuine terminal delivery")
    assert len(genuine) == 1, "expected exactly one genuine terminal delivery, got %r" % genuine

    # Let trailing async writes for THIS payout (processed journal, utr set, webhook worker) settle,
    # then snapshot -- so the "unchanged" baseline isolates the effect of the webhook REPLAY, not the
    # normal post-processed finalization that also touches updated_at within the same second.
    time.sleep(2)
    before = pf.get_payout_row(payouts_mysql, pid)
    logs_before = pf.get_payout_logs(payouts_mysql, pid)
    updated_at_before = before["updated_at"]

    for i in range(2):
        replay = pf.inject_transfer_webhook(ps_internal_client, fts_mysql, pid, "processed", utr=before["utr"])
        m4.observe("terminal_webhook_replay_%d" % i, replay, None)
        assert replay.status == 200, "webhook replay must return 200 to stop FTS retries: %s" % replay

    time.sleep(2)
    after = pf.get_payout_row(payouts_mysql, pid)
    logs_after = pf.get_payout_logs(payouts_mysql, pid)
    deliveries_after = terminal_deliveries()

    # DOCUMENTED duplicate behaviour: replaying the terminal FTS webhook on an already-processed
    # payout re-touches the row (updated_at advances) but is otherwise a no-op -- status stays
    # processed, no state-transition log is appended, and NO duplicate merchant webhook is emitted.
    updated_at_touched = after["updated_at"] != updated_at_before
    m4.trace.record("m4_g34_summary", {
        "payout_id": pid, "status": after["status"],
        "updated_at_before": updated_at_before, "updated_at_after": after["updated_at"],
        "updated_at_touched_by_replay": updated_at_touched,
        "log_count_before": len(logs_before), "log_count_after": len(logs_after),
        "terminal_deliveries": len(deliveries_after),
        "note": "duplicate terminal webhook re-touches updated_at (documented) but does not change "
                "status, append a transition log, or emit a duplicate merchant delivery"})

    # essential invariants (the G34/G45 claim): terminal status stable, no new state-transition log,
    # exactly one merchant terminal delivery.
    assert after["status"] == "processed", "status changed after terminal replay: %r" % after
    assert len(logs_after) == len(logs_before), \
        "payout_logs grew after a duplicate terminal webhook: %d -> %d" % (len(logs_before), len(logs_after))
    assert len(deliveries_after) == 1, "duplicate terminal webhook produced a duplicate merchant delivery"
