# M5 source map — grounding the workflow-engine reconstruction

The Razorpay Workflows/Cadence decision engine repository was unavailable for
this milestone. The `workflow-engine` component is a high-fidelity
RECONSTRUCTION built from the surrounding real evidence in the pinned twin
(`.local/twin-repos/accepted/payouts`). This maps each reconstructed behaviour
to its real anchor.

## Real anchors (payouts twin)

| Reconstructed behaviour | Real source anchor |
|---|---|
| Create RPC + request/response shape | `pkg/workflow/workflow_create.go` — `WfCreate` (:17), `CreateRequestBody` (:33-52,:244-261), `CreateHttpResponse` (:119-135) |
| Non-empty `wfl_` id ⇒ payout `pending` | read at `workflow_create.go:217-220` |
| Approve / reject internal routes | `internal/routing/router/payout_internal_routes.go` approve `:71`, reject `:77`, group `BasicAuth(cred.API, cred.Workflow)` `:16` |
| Callback URL/method/headers | `getCallbackDetails`, `getCallbackHeaders` (`x-creator-id`, `X-Razorpay-Account`) `workflow_create.go:87-90,:333-338` |
| Callback success = 200/201/409 | `getCallbackResponseHandler` `workflow_create.go:320-325`; 409 = `PayoutNotInPendingStatus` (`payoutController.go:1057`) |
| Service inbound auth workflow/workflow | payouts `arena.toml` `[workflow.auth]` |
| Callback identity rzp_live + secret | payouts `arena.toml` `[auth.workflow]` username `rzp_live`, `{{SECRET.auth_workflow_payouts}}` |
| State machine `pending → approved / rejected` | `internal/app/payouts/state_machine.go` (rejected only FROM pending) |
| Local workflow tables | payouts `workflow_config`, `workflow_entity_map`, `workflow_state_map` |
| Feature gate | `PAYOUT_WORKFLOWS` |
| Create-only "park a record" contract | existing `ENV2_COMPOSE/substitutes/workflow-sim` |

## Reconstructed WITHOUT a real anchor (labelled)

These are the decision-engine internals the real repo would own; reconstructed
to reproduce observable maker-checker semantics, not the original implementation:

- organizations, actor identities + role model, bearer-token auth;
- approval policy object (required approvals, eligible-approver set, separation
  flag, expiry) and its evaluation order;
- N-of-M distinct-approver counting + idempotent duplicate-approval handling;
- optimistic-concurrency `version` (stale-decision protection);
- terminal-state freeze, cancellation, lazy + swept expiry;
- durable SQLite state model + restart recovery;
- idempotent, retried, at-least-once callback delivery.

The exact production storage schema, policy DSL, Cadence timer/heartbeat
mechanics, and the production RBAC/permission service are UNKNOWN and out of
scope (the deliberate "final 1%").

## Blind benchmark

Four mutant copies of the reconstructed engine, one surgical single-property
source patch each (`RED_LOOP/m5/benchmark/mutations.py`), spanning four
invariant families: org-boundary, maker/checker separation, approval-integrity,
state-integrity. Defects are seeded ONLY in the reconstruction, never claimed
against real source.
