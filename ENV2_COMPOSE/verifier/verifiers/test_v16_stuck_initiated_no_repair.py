"""V16 (I80, I70): a terminal FTS result whose relay to Payouts was dropped leaves
the payout stuck in ``initiated``, and *no production cron or job repairs it*.

STRONGER THAN PRODUCTION. Production has no repair mechanism at all for this case
(payouts adminClientController.go:267-356 -- ManualAction has exactly three cases,
none of which is a stuck-initiated repair; alert-rules payouts_rules.yaml:2222-2269
are dead through a label mismatch). Passive observation would therefore only show
that nothing happened to fire. This test instead *actively invokes every cron route
the external FastCron scheduler drives* -- the eight routes and request bodies of
``ENV2_COMPOSE/scripts/cron-driver/driver.py`` JOBS, verbatim, scoped to this
payout's own merchant and balance -- through the real ``cred.FastCron`` identity,
and only then asserts that nothing moved.

Proven claim: no production cron or job repairs a lost callback within the
observation window; this is stronger than production, which has no repair at all.
Not proven: the absence of every conceivable repair outside the bounded window, or
by a mechanism that is neither one of these cron routes nor a background worker
running during the window.
"""
import os
import time

import pytest

from helpers import db, payouts_flow as pf, trace
from helpers.wait import wait_until

TERMINAL_EVENTS = ("payout.processed", "payout.failed", "payout.reversed",
                   "payout.cancelled", "payout.rejected")


def _cron_requests(merchant):
    """Verbatim from scripts/cron-driver/driver.py JOBS + _body_for(), with the
    optional balance/merchant scoping filled in with THIS payout's own fixture so
    every route is pointed at the stuck payout rather than at an empty set."""
    return [
        # driver.py _body_for: only "partner_bank_downtime" is registered (dtos/v2 ProcessQueuedPayoutsRequest)
        ("/v1/cron/process_queued_payouts", {"type": "partner_bank_downtime"}),
        ("/v1/cron/process_queued_low_balance_payouts", {"balance_ids": [merchant["balance_id"]]}),
        ("/v1/cron/process_scheduled_payouts", {"balance_ids": [merchant["balance_id"]]}),
        ("/v1/cron/process_beneficiary_bank_on_hold_payouts", {}),
        ("/v1/cron/process_inflight_reservation_reconciliation", {}),
        ("/v1/cron/payouts_dual_write_failure_processing", {}),
        ("/v1/cron/process_batch_submitted_payouts", {}),
        # source DTO requires at least one merchant id
        ("/v1/cron/fund_management_payouts/check", {"merchant_ids": [merchant["merchant_id"]]}),
    ]


@pytest.mark.spec_id("V16")
@pytest.mark.status("CROSS_SERVICE_CONFIRMED")
def test_stuck_initiated_payout_has_no_automated_repair(ps_public_client, ps_fastcron_client, payouts_mysql,
                                                        fts_mysql, merchant_m1, monolith_stub_client,
                                                        mozart_mock_client, fts_client, merchant_sink_client):
    seconds = float(os.environ.get("STUCK_DETECTION_WAIT_SECONDS", "15"))
    assert 1 <= seconds <= 300
    pid = pf.seed_payout(ps_public_client, merchant_m1)
    bare = pf.db_id(pid)
    # Drop only after the bank hold and initial FTS details are both observed.
    pf.wait_for_held_handoff(payouts_mysql, fts_mysql, pid)
    control = monolith_stub_client.post("/_arena/relay", body={"payout_id": bare, "mode": "drop"})
    assert control.status == 200, control
    try:
        transfer = pf.finish_bank(mozart_mock_client, fts_client, fts_mysql, pid)
        wait_until(lambda: db.fetchone(fts_mysql, "SELECT status FROM transfers WHERE id=%s",
                                       (transfer["id"],))["status"] == "PROCESSED",
                   timeout=40, interval=.5, desc="FTS bank success")
        relay_log = monolith_stub_client.get("/_arena/log")
        assert relay_log.status == 200, relay_log
        dropped = [e for e in relay_log.json()["log"]
                   if e.get("kind") == "relay_dropped" and (e.get("payload") or {}).get("payout_id") == bare]
        assert dropped, "the terminal relay for this payout was never actually dropped"

        def observed():
            row = pf.get_payout_row(payouts_mysql, pid)
            deliveries = merchant_sink_client.get("/_arena/deliveries")
            assert deliveries.status == 200, deliveries
            terminal = [d for d in deliveries.json()["deliveries"]
                        if d.get("body", {}).get("event") in TERMINAL_EVENTS
                        and d.get("body", {}).get("payload", {}).get("payout", {}).get("entity", {}).get("id") == pid]
            return {
                "payout_status": row["status"],
                "payout_updated_at": row["updated_at"],
                "payout_log_count": len(pf.get_payout_logs(payouts_mysql, pid)),
                "fts_attempt_ids": [a["id"] for a in db.fetchall(
                    fts_mysql, "SELECT id FROM attempts WHERE transfer_id=%s ORDER BY id", (transfer["id"],))],
                "reversal_id": (pf.get_reversal_row(payouts_mysql, pid) or {}).get("id"),
                "terminal_webhook_count": len(terminal),
            }

        before = observed()
        assert before["payout_status"] == "initiated", before
        assert before["reversal_id"] is None, before
        assert before["terminal_webhook_count"] == 0, before
        trace.record("stuck_initiated_baseline", {"payout_id": pid, "fts_transfer_id": transfer["id"], **before})

        # Actively drive every production cron route, then assert nothing moved.
        cron_calls = []
        for path, body in _cron_requests(merchant_m1):
            response = ps_fastcron_client.post(path, body=body)
            cron_calls.append({"path": path, "request_body": body, "http_status": response.status})
            assert 200 <= response.status < 300, (path, response)
        trace.record("production_cron_routes_invoked", {"payout_id": pid, "calls": cron_calls,
                                                        "source": "scripts/cron-driver/driver.py JOBS"})

        end = time.monotonic() + seconds
        while time.monotonic() < end:
            assert pf.get_payout_row(payouts_mysql, pid)["status"] == "initiated"
            time.sleep(.5)

        after = observed()
        assert after == before, {"before": before, "after": after}
        trace.record("bounded_divergence", {
            "payout_id": pid,
            "fts_status": "PROCESSED",
            "payout_status": "initiated",
            "observation_seconds": seconds,
            "cron_routes_invoked": [c["path"] for c in cron_calls],
            "cron_http_statuses": [c["http_status"] for c in cron_calls],
            "unchanged": {"payout_updated_at": before["payout_updated_at"],
                          "payout_log_count": before["payout_log_count"],
                          "fts_attempt_ids": before["fts_attempt_ids"],
                          "reversal_id": None,
                          "terminal_webhook_count": 0},
            "claim": "no production cron or job repairs a lost callback within the window; stronger than "
                     "production which has no repair at all (every cron route the FastCron scheduler drives was "
                     "explicitly invoked and returned 2xx, and the payout, its payout_logs, its updated_at, its FTS "
                     "attempt chain, its reversal absence and its terminal-webhook absence are all unchanged)",
            "not_claimed": "absence of any repair outside this bounded window",
            "label": "stronger-than-production (I80)",
        })
    finally:
        clear = monolith_stub_client.post("/_arena/relay", body={"payout_id": bare, "mode": "deliver"})
        assert clear.status == 200, clear
