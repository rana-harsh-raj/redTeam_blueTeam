"""V14: own scenario transition log matches the pinned Payouts state machine table."""
import pytest

from helpers import db


# Reconstructed from payouts/internal/app/payouts/state_machine.go's
# sm.Event(...).To(...).From(...) declarations (see the reversals/state-
# management skill docs cited in VERIFIER_SPEC.md V14). Each entry: to_state
# -> set of legal from_states for a transition landing on that state.
LEGAL_TRANSITIONS = {'batch_submitted': {'scheduled', 'pending', 'create_request_submitted'},
 'cancelled': {'on_hold', 'scheduled', 'queued'},
 'created': {'batch_submitted',
             'create_request_submitted',
             'ledger_response_awaited',
             'on_hold',
             'pending',
             'queued',
             'scheduled'},
 'failed': {'batch_submitted',
            'create_request_submitted',
            'created',
            'initiated',
            'ledger_response_awaited',
            'on_hold',
            'pending',
            'queued',
            'scheduled'},
 'initiated': {'initiated', 'created'},
 'ledger_response_awaited': {'batch_submitted',
                             'create_request_submitted',
                             'on_hold',
                             'pending',
                             'queued',
                             'scheduled'},
 'on_hold': {'batch_submitted',
             'create_request_submitted',
             'pending',
             'scheduled'},
 'pending': {'create_request_submitted'},
 'processed': {'initiated'},
 'queued': {'create_request_submitted',
            'ledger_response_awaited',
            'on_hold',
            'pending',
            'scheduled'},
 'rejected': {'pending'},
 'reversed': {'batch_submitted',
              'create_request_submitted',
              'created',
              'failed',
              'initiated',
              'ledger_response_awaited',
              'on_hold',
              'pending',
              'processed',
              'queued',
              'scheduled'},
 'scheduled': {'pending', 'create_request_submitted'}}


@pytest.mark.spec_id("V14")
@pytest.mark.status("CROSS_SERVICE_CONFIRMED")
def test_no_illegal_transition_in_payout_logs(payouts_mysql,fts_mysql,ps_public_client,merchant_m1):
    from helpers import payouts_flow as pf
    pid=pf.seed_payout(ps_public_client,merchant_m1)
    # Snapshot after the actual held bank response and FTS details callback.
    pf.wait_for_held_handoff(payouts_mysql,fts_mysql,pid)
    rows=pf.get_payout_logs(payouts_mysql,pid)
    assert rows, "own scenario must produce payout_logs"

    by_payout = {}
    for row in rows:
        by_payout.setdefault(row["payout_id"], []).append(row)

    violations = []
    for payout_id, logs in by_payout.items():
        for row in logs:
            frm, to = row["from"], row["to"]
            legal_froms = LEGAL_TRANSITIONS.get(to)
            if legal_froms is None:
                violations.append((payout_id, row, "unknown 'to' state %r" % to))
                continue
            if frm not in legal_froms:
                violations.append((payout_id, row, "%r -> %r is not a declared transition" % (frm, to)))

    assert not violations, "illegal state transitions found in payout_logs:\n" + "\n".join(
        "  payout=%s: %s (%r)" % (v[0], v[2], v[1]) for v in violations
    )
