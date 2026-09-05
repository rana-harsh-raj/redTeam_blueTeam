# Workflow Service (WFS) — REAL recommended, substitute contract as fallback

Decision: **promote to REAL** when the approval family is in scope. `razorpay/workflows@080d71a` builds
(Go 1.25, goose, Twirp); the cost is Cadence (client `go.uber.org/cadence v0.13.4`, server from `uber/cadence`
compose with Cassandra). Minimal REAL footprint: Cassandra + Cadence server (tchannel 7933, grpc 7833) + MySQL 8
(10 migrations) + `cmd/api` + `cmd/workers -WORKFLOW_DOMAINS=payouts` (task list `payouts-approval`). Redis optional.
Config: `[clients.payouts_live] host=http://payouts-api:9400 username=payouts_live`, inbound `credentials.Payouts`
username `payouts`. Seed one `configs` row per merchant: `type=payout-approval, owner_type=merchant, service=rx_live,
org_id=<14>, enabled="true"` (string), template compiled from `RangeConfig{Range,Steps[{Step,Op AND|OR,Roles[{RoleName role|user_id|email,RoleId,ApprovalCount}]}]}`.
Cadence server version in prod is UNKNOWN (request to WFS/infra team).

## Substitute contract (if not REAL)
| RPC | Request (from PS `payouts/pkg/workflow/workflow_create.go:225-338`) | Response |
|---|---|---|
| `POST /twirp/rzp.workflows.workflow.v1.WorkflowAPI/Create` | `workflow{entity_id, entity_type "payout", title "", description "", config_id "" (never set by PS), config_version "1", diff{old{merchant_id:"",amount:0},new{merchant_id,amount}}, creator_id, creator_type user|merchant, owner_id, owner_type merchant, service rx_live, org_id, callback_details}` | 200 `{id, config_id, status:"initiated", domain_status:"created", ...}`; resolve config by `(owner_id, owner_type, service, org_id, enabled="true")`, most recent first |
| `ActionAPI/CreateWithEntityId` (monolith caller) | `{entity_id, entity_type, action approved|rejected, actor_id, actor_type, actor_property_key, actor_property_value, actor_meta, service, owner_id, owner_type, data, comment}` | 200; validate only `actor_property_key/value` string equality vs pending state rules (`StateIsNotPendingOnCurrentActor` otherwise) |
| callbacks to PS | `POST /v1/workflow/state`, `PATCH /v1/workflow/state/{id}` (headers `x-creator-id`, `X-Razorpay-Account`; body `{queue_if_low_balance}`), then on completion `POST /v1/payouts/payouts_internal/{payout_id}/approve` or `/reject`; Basic `payouts_live`; treat `[200,201,409]` as success; retry 2s ×5.0 backoff cap 1h, 30 days | — |
| expiry | none — never fire a callback on expiry (production gap to preserve) | — |
| self-approval | not prevented; optional DCS `skip_approval_if_creator_is_checker` auto-approve when maker role == checker role | — |
| `ConfigAPI/CreateV2` | admin seeding only | — |
