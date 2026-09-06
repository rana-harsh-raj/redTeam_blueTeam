# workflow-sim — Workflow (Cadence) engine substitute for the Payouts twin

A thin, protocol-faithful stand-in for Razorpay's Workflow Service (the
Cadence-backed approval engine). It exists so a payout that requires approval
can reach the real `pending` state and then be approved or rejected on demand,
without running the real Workflow/Cadence stack.

Everything here is grounded in the pinned payouts repo
(`.local/twin-repos/accepted/payouts`), primarily
`pkg/workflow/workflow_create.go`.

## Endpoints SERVED (inbound, called by payouts-api)

### `POST /twirp/rzp.workflows.workflow.v1.WorkflowAPI/Create`
The single outbound call the real Payouts service makes to decide a payout
needs approval (`WfCreate`, workflow_create.go:17).

- **Auth**: HTTP Basic. Username/password default `workflow`/`workflow`
  (`WORKFLOW_INBOUND_USER` / `WORKFLOW_INBOUND_PASS`). This mirrors payouts'
  own config `[workflow.auth] username="workflow" password="workflow"`
  (arena.toml) sent via `base.go:45 req.SetBasicAuth(key, secret)`.
- **Request** (`CreateRequestBody`, workflow_create.go:33-52, :244-261):
  `workflow.entity_id` (the payout id as `payout.GetID()`),
  `workflow.owner_id` (merchant id), `workflow.creator_id`,
  `workflow.diff.new.amount`, and `workflow.callback_details` — which carries
  the approve/reject callback URLs, methods, headers and success codes.
- **Behaviour**: parses the body, stores a **pending** record keyed by
  `entity_id`, and returns HTTP 200 with a freshly minted, **non-empty** `id`
  (`wfl_<hex>`). A non-empty id is exactly what payouts treats as "workflow
  created → payout pending" (read at workflow_create.go:217-220).
  Response fields mirror `CreateHttpResponse` (workflow_create.go:119-135):
  `id`, `config_id`, `entity_id`, `status` (`"created"`),
  `domain_status` (`"pending"`), `owner_id`, etc.
- **It never auto-approves or auto-rejects.** A create only parks a record.
  The decision is made out-of-band (see `/_arena/decide`). This is the whole
  point: the twin controls approval timing, like a human reviewer would.

## Endpoints CALLED (outbound, driving the REAL payouts-api)

Confirmed real Payouts routes (`internal/routing/router/payout_internal_routes.go`
:71 approve, :77 reject; group middleware :16). The exact URL/method/headers
are taken **verbatim** from the `callback_details` the create body supplied
(built by workflow_create.go `getCallbackDetails`), falling back to
constructing them from `PS_API_URL` + `entity_id` if absent.

- **APPROVE**: `POST {PS_API_URL}/v1/payouts/payouts_internal/{payout_id}/approve`
  body `{"queue_if_low_balance": <bool>}`
- **REJECT**:  `POST {PS_API_URL}/v1/payouts/payouts_internal/{payout_id}/reject`
  body `{}`

`{payout_id}` is used exactly as it appears in `entity_id` / the supplied
callback `url_path` — payouts builds both from the same `payout.GetID()`
(workflow_create.go:246, :298, :307), so they are always self-consistent and
workflow-sim never has to guess bare-vs-`pout_`-prefixed.

- **Auth**: HTTP Basic, `cred.Workflow`. Username `rzp_live`
  (`WORKFLOW_CALLBACK_USER`), password read from
  `WORKFLOW_CALLBACK_PASS_FILE` (default `/run/secrets/auth_workflow_payouts`).
  This matches payouts' inbound `[auth.workflow] username="rzp_live"`,
  `password={{SECRET.auth_workflow_payouts}}` (arena.toml:127-129), which the
  `/payouts_internal` group accepts via `middleware.BasicAuth(cred.API,
  cred.Workflow, ...)` (payout_internal_routes.go:16).
- **Headers** (`getCallbackHeaders`, workflow_create.go:87-90, :333-338):
  `x-creator-id: <creator/user id>` and `X-Razorpay-Account: <owner/merchant id>`.
- **Success**: HTTP **200, 201, or 409** are all treated as success
  (`getCallbackResponseHandler`, workflow_create.go:320-325). 409 =
  payout already transitioned (`PayoutNotInPendingStatus`,
  payoutController.go:1057) and is idempotent-safe.

## Control / evidence plane (`/_arena/*`)

All under `/_arena/` so the arena attacker broker denies them to the workload —
these are for the twin operator/verifier only, never the payout flow.

- `GET  /_arena/health`  → `{"status":"ok","service":"workflow-sim","pending":N}`
- `GET  /_arena/pending` → the stored pending records (evidence plane).
- `POST /_arena/decide`  → body `{"payout_id":..., "decision":"approve"|"reject",
  "queue_if_low_balance": <optional bool>}`. Fires the real payouts-api
  callback above, records the `status_code`+response, marks the record decided,
  and returns the outcome. If `queue_if_low_balance` is omitted on an approve,
  it falls back to the value in the stored `callback_details` payload.

`GET /health` and `/ping` are also answered (unauthenticated) for compose
healthchecks, per the arena stub convention.

## Fidelity limits (READ THIS)

workflow-sim is a **bounded stand-in for the Cadence/Workflow engine**, not a
reimplementation. Known gaps:

1. **No `/v1/workflow/state` map-writes.** The real engine also drives the
   `state_callbacks.created` / `.processed` (POST/PATCH `/v1/workflow/state`)
   transitions. workflow-sim ignores those; it only drives the terminal
   approve/reject `workflow_callbacks`. Flows that assert on intermediate
   workflow-state rows will diverge.
2. **Approvals are never automatic.** The real engine applies configured
   approval rules (levels, approvers, timeouts, auto-approve). workflow-sim
   holds every payout pending until a control-plane `/_arena/decide` call.
   There is no rule evaluation, no multi-level approval, no expiry.
3. **No persistence.** Pending records live in memory only; a restart forgets
   them.
4. **No config resolution.** It does not look up a real workflow `config_id`
   for the merchant; it echoes the supplied one or mints a synthetic
   `wfc_<hex>`.
5. **Single callback attempt.** No retry/backoff of the approve/reject call
   (the real engine has its own retry semantics).

**Consequently: any bug reproduced only because of workflow-sim's behaviour is
a twin-specific lead, NOT a production finding.** Confirm against the real
Workflow Service before reporting anything as a production issue.
