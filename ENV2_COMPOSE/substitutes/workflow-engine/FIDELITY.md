# workflow-engine — fidelity label (real vs reconstructed)

**This is a high-fidelity RECONSTRUCTION of the Razorpay Workflows/Cadence
approval decision-engine, not the original service.** The real engine
repository was unavailable for this milestone. This component was rebuilt from
the surrounding real evidence so the maker-checker business journey is
exercisable and the autonomous system has a real decision engine to investigate.

## What it is reconstructed FROM (real sources)

- `.local/twin-repos/accepted/payouts/pkg/workflow/workflow_create.go` — the
  real outbound `WfCreate` Twirp call, `CreateRequestBody`, `CreateHttpResponse`,
  `getCallbackDetails`, `getCallbackHeaders`, `getCallbackResponseHandler`
  (success = 200/201/409).
- `.local/twin-repos/accepted/payouts/internal/routing/router/payout_internal_routes.go`
  — the real approve (`:71`) / reject (`:77`) internal routes and the
  `BasicAuth(cred.API, cred.Workflow)` group middleware (`:16`).
- payouts `arena.toml` — `[workflow.auth]` (workflow/workflow) and
  `[auth.workflow]` (rzp_live + `{{SECRET.auth_workflow_payouts}}`).
- payouts state machine (`internal/app/payouts/state_machine.go`) — `pending`
  is entered on a non-empty workflow id; `rejected` is reached from `pending`.
- The existing `ENV2_COMPOSE/substitutes/workflow-sim` stub (create contract).

## What is REAL when integrated with the arena

- The create call is byte-compatible with payouts' real `WfCreate`
  (`POST /twirp/rzp.workflows.workflow.v1.WorkflowAPI/Create`, Basic
  workflow/workflow, non-empty `wfl_` id + `domain_status=pending`).
- The approve/reject callbacks target the REAL payouts internal routes with the
  real Basic identity (`rzp_live`) and headers (`x-creator-id`,
  `X-Razorpay-Account`), and treat 200/201/409 as success — so an approved
  workflow drives the real payout through its existing Direct/Shared path.

## What is RECONSTRUCTED (labelled, not original)

- **The decision engine itself**: organizations, actor identities/roles,
  approval policies, eligible-approver sets, maker/checker separation, N-of-M
  approvals, cancellation, expiry, stale-version (optimistic concurrency),
  duplicate-request + duplicate-approval protection, terminal-state freeze,
  audit history, durable SQLite state, restart recovery, and idempotent/retried
  callback delivery. The real engine's internal storage model, exact policy DSL,
  Cadence workflow/versioning mechanics, and org/RBAC schema are NOT known and
  are reconstructed to reproduce the observable semantics, not the internals.
- **payout-sink**: a minimal payout-side callback receiver used ONLY for the
  standalone benchmark, so callback identity/idempotency/retry are observable
  without booting the full arena. Against the real arena the callbacks go to the
  real payouts routes instead.

## Deliberately out of scope (the "final 1%")

Real Cadence timers/heartbeats, the production RBAC/permission service, the
exact production policy configuration, and multi-region durability. These do not
change the maker-checker invariants under test and were not chased.

## Invariants enforced (see wfengine/core.py `# [CHECK:...]`)

org-boundary · maker/checker separation · approval-integrity (N distinct
approvers, idempotent repeats) · state-integrity (terminal freeze, stale-version
rejection, expiry) · duplicate-request protection · callback identity/idempotency.
