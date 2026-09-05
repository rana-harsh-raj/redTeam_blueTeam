"""V14 -- State machine legality: no illegal transition ever recorded in payout_logs.

Covers: general state-machine soundness underlying C22/C23/C26 and the
invariant list; cross-checks the transition table hardcoded below from
payouts/internal/app/payouts/state_machine.go and
internal/app/common/appConstants/states.go.

Pure read-only audit -- creates no payouts of its own. More valuable the
more of the other verifiers in this package have already run in the same
session (it audits whatever payout_logs rows exist).
"""
import pytest

from helpers import db


# Reconstructed from payouts/internal/app/payouts/state_machine.go's
# sm.Event(...).To(...).From(...) declarations (see the reversals/state-
# management skill docs cited in VERIFIER_SPEC.md V14). Each entry: to_state
# -> set of legal from_states for a transition landing on that state.
LEGAL_TRANSITIONS = {
    "created": {
        "create_request_submitted", "pending", "scheduled", "queued", "on_hold",
        "ledger_response_awaited", "batch_submitted", "created",
    },
    "ledger_response_awaited": {
        "create_request_submitted", "pending", "scheduled", "queued", "on_hold",
        "batch_submitted", "ledger_response_awaited",
    },
    "initiated": {"created", "initiated"},
    "processed": {"initiated", "processed"},
    "failed": {
        "create_request_submitted", "created", "pending", "scheduled", "queued", "on_hold",
        "ledger_response_awaited", "batch_submitted", "initiated", "failed",
    },
    "reversed": {
        "create_request_submitted", "ledger_response_awaited", "pending", "on_hold", "queued",
        "scheduled", "processed", "failed", "initiated", "created", "batch_submitted", "reversed",
    },
    "pending": {"create_request_submitted", "pending"},
    "scheduled": {"create_request_submitted", "pending", "scheduled"},
    "batch_submitted": {"create_request_submitted", "pending", "scheduled", "batch_submitted"},
    "queued": {
        "create_request_submitted", "ledger_response_awaited", "pending", "scheduled", "on_hold", "queued",
    },
    "cancelled": {"queued", "scheduled", "on_hold"},
    "on_hold": {"create_request_submitted", "pending", "batch_submitted", "scheduled", "on_hold"},
    "rejected": {"pending"},
}

TERMINAL_STATES = {"processed", "failed", "reversed", "cancelled", "rejected"}
# Idempotent self-loops the transfer-webhook guard explicitly allows on an
# already-terminal state (appConstants.AllowedStateTransitionForTransferWebhook).
TERMINAL_SELF_LOOPS = {"processed", "reversed", "failed"}


@pytest.mark.spec_id("V14")
@pytest.mark.status("CROSS_SERVICE_CONFIRMED")
def test_no_illegal_transition_in_payout_logs(payouts_mysql):
    rows = db.fetchall(
        payouts_mysql,
        "SELECT payout_id, event, `from`, `to`, created_at, id "
        "FROM payout_logs ORDER BY payout_id, created_at, id",
    )
    if not rows:
        pytest.skip("no payout_logs rows exist yet -- run the create-path verifiers first")

    by_payout = {}
    for row in rows:
        by_payout.setdefault(row["payout_id"], []).append(row)

    violations = []
    for payout_id, logs in by_payout.items():
        terminal_entries = 0
        for row in logs:
            frm, to = row["from"], row["to"]
            legal_froms = LEGAL_TRANSITIONS.get(to)
            if legal_froms is None:
                violations.append((payout_id, row, "unknown 'to' state %r" % to))
                continue
            if frm not in legal_froms:
                violations.append((payout_id, row, "%r -> %r is not a declared transition" % (frm, to)))
            if to in TERMINAL_STATES and not (to in TERMINAL_SELF_LOOPS and frm == to):
                terminal_entries += 1
        if terminal_entries > 1:
            violations.append(
                (payout_id, logs, "more than one non-idempotent transition into a terminal state (%d)" % terminal_entries)
            )

    assert not violations, "illegal state transitions found in payout_logs:\n" + "\n".join(
        "  payout=%s: %s (%r)" % (v[0], v[2], v[1]) for v in violations
    )
