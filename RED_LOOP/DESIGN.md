# RED_LOOP — autonomous code-aware red agent (Milestone 2)

A lean, honest, evidence-driven exploitation loop against the frozen Payouts
Twin (`twin-v1.0`, runtime freeze `219ca48bbb5e51db9347b335ac0a50241656045b`).
One code-aware adversary chooses its own direction; deterministic system
evidence, not prose, decides whether it succeeded; a different model family can
independently reproduce a credible result.

## Three separated roles
- **Primary red agent** (`claude-opus-4-8`): the only explorer. White/gray-box
  source access + one ordinary merchant identity. Picks its own targets and
  hypotheses. No named vuln, endpoint, or answer is ever given to it.
- **Independent reproducer** (`gpt-5.5`, different provider): activates only for a
  judge-positive candidate. Gets a minimal bundle (actor, steps, inputs, claimed
  result, necessary code slices) — never the primary's narrative, scratchpad, the
  judge classification, or the known-gap registry.
- **Deterministic judge**: authoritative. Reads payouts/FTS/ledger/webhook/
  ownership state directly and classifies against a judge-only known-gap registry.
  Neither the red agent nor the reproducer can approve its own result.

## The attacker boundary (the security core)
`red_loop/broker.py` is the only path from the agent to the twin:
- traffic goes to **kong-lite `127.0.0.1:18080` only**; the agent supplies a path,
  never a host, so it cannot redirect anywhere else;
- the attacker's merchant credential is **fixed and injected**; any agent-supplied
  `Authorization` is overridden, so identity cannot be swapped. Other headers
  (e.g. `X-Razorpay-Account`) ARE forwarded so real authorization weaknesses stay
  reachable — kong/Payouts, not the broker, decide whether to honour them;
- `/_arena/*` (including kong-lite's unauthenticated passport-mint oracle),
  `/twirp/*`, `/metrics`, `/debug` are hard-denied; path traversal blocked;
- every request/response is recorded with a semantic fingerprint; floods capped.

The webhook-read tool (`read_own_webhooks`) is scoped to the attacker merchant
server-side (docker exec into the sink with the merchant param hard-fixed).

## Evidence access (judge)
`red_loop/judge.py` reads authoritative state via `docker exec` (DBs are on a
docker-internal network): payouts (`payouts`, `payout_logs`, `reversals`,
`idempotency_keys`), FTS (`transfers`), Ledger (`journal`, `ledger_entries`,
`accounts`+`account_details`), and the webhook sink. It computes invariant
violations (ownership, ledger zero-sum, idempotency de-dup) over the campaign's
payouts and returns one of the Section-13 verdicts. Known gaps
(`registry/known_gaps.yaml`) — Kafka expected failures, declared deviations, the
`/_arena/*` substitute oracles, fidelity ceilings — downgrade a demonstrated
effect to CALIBRATION_REDISCOVERY / TWIN_SPECIFIC_LEAD / etc. instead of a new
finding.

## Durable campaign state
`red_loop/state.py` — append-only JSONL (manifest, hypotheses, actions,
observations, candidates, model_calls, events) fsync'd per write, plus a
content-addressed evidence store. The model's chat context is a cache; this is
authoritative and survives model-call failure / restart.

## The loop
`red_loop/campaign.py` — system=mandate, then: complete → record model call →
dispatch tool calls (broker/corpus/store) → checkpoint → stagnation check →
repeat. Candidates trigger an inline deterministic pre-adjudication that returns
only a COARSE outcome to the agent. Generous safety ceilings (turns/wall/request
budget), a `STOP` kill-switch file, and stagnation detection (repeated
fingerprints + no new observations) — not small research caps.

## Model routing
`claude-opus-5` is budget-capped on this key and content-filtered on the
adversarial mandate, so the strongest usable Claude, `claude-opus-4-8`, is
primary; `gpt-5.5` (OpenAI) is the reproducer, giving cross-provider diversity.
See `registry/model-selection.json`.

## Product-surface additions (Section 9), additive and default-off
- **workflow-sim** (`ENV2_COMPOSE/substitutes/workflow-sim/`): serves the real
  `WorkflowAPI/Create` (payout → pending) and drives the REAL Payouts
  `payouts_internal/{id}/approve|reject` callbacks — approval is never automatic,
  only via the control-plane `/_arena/decide`. Activated by repointing
  `[workflow] host`.
- **Stork duplicate** (`stork-capture` env `STORK_DUPLICATE_TERMINAL`, default
  off / byte-identical to frozen): duplicate terminal webhook with identical
  event_id+body, for merchant-dedupe testing. Control-plane only.
- **Channel DOWN + second channel** (`RED_LOOP/surface/channel_control.py`):
  drives the real `partner_bank_health` internal route (cred.FTS) to set a
  channel down/up; shared-vs-direct already gives a second routing configuration;
  optional runtime icici provisioning. Control-plane only, never attacker-reachable.

## What this milestone deliberately does NOT do
No production/staging/DevStack access, no real data/credentials, no internet
egress, no Daytona, no repo-clone modification, no moving the frozen tag, no
seeding/weakening to manufacture a finding. "No accepted finding" is an honest
outcome.

## Lifecycle v2 (M4)

M4 adds a structured hypothesis lifecycle **additively** — the M1–M3.1 runtime
(`campaign.py`/`state.py`/`judge.py`/`reproducer.py`) is unchanged and still runs
exactly as before. New modules the loop opts into:

- **`red_loop/hypotheses.py`** — the lifecycle state machine + semantic
  fingerprinting. States: `proposed → claimed → experiment_defined → executing →
  evidence_gathered → (falsified | blocked | supported) → replay_requested →
  (reproduced | rejected) → (accepted | closed)`, plus coordinator-only
  `deprioritized` (requires a machine-readable reason). Illegal transitions raise
  `LifecycleError`. Every record carries id, semantic fingerprint (normalized
  claim + target-assets hash), typed `target_assets`, `claim`, labelled
  `preconditions` (`verified|observed|assumed`), `suspected_cause`,
  `expected_observation`, a bounded `experiment` (actions/shape, success/stop
  condition, `max_actions`, `max_duration_s`), `evidence_refs`, `owner`,
  `priority` (int, **higher first**), `status`, `status_reason`, `blocker`
  (fixed enum; **required iff** `status==blocked`), `next_best_action`,
  timestamps, and a transition history. **`blocked` ≠ `falsified`**: falsifying
  requires non-empty `evidence_refs` **and** an executed experiment; blocking
  requires a `Blocker`. Duplicate proposals collide on fingerprint, return the
  existing id and record a `duplicate_suppressed` event. Durable in
  `hypotheses_v2.jsonl`; legacy `hypotheses.jsonl` is projected on read.
- **`red_loop/leases.py`** — cooperative task leases (`leases.jsonl`): claim with
  owner + TTL, heartbeat, expiry detection, reassignment with a handoff record
  (`handoffs.jsonl`), restart-safe recovery of expired leases on resume.
- **`red_loop/experiments.py`** — the cross-merchant / identity-override template
  with mandatory arms (`own_merchant_control`, `merchant_b_variant`,
  `malformed_or_absent_identity_variant`, `expected_denial_control`,
  `state_observation`, `clean_reset`) and `replay_required_if_unauthorized_effect`.
  A denial is recorded as `denied_at_layer ∈ {broker,gateway,service,unknown}` and
  never counted as "service safe"; an own-merchant success is only a control. The
  validator refuses `supported`/`falsified` unless the merchant-B arm executed
  with a state observation, and refuses `accepted` without an independent replay.
- **`red_loop/contexts.py`** — four exploration policies (`broad_coverage`,
  `deep_direct_accounting`, `identity_tenant_boundary`, `concurrency_event_order`),
  each a mandate suffix + tool allow-list + correlation-id prefix + artifact dir +
  a merchant-namespace slot the coordinator fills with fresh Direct merchants
  (accepts `MerchantDescriptor`s). `ContextSupervisor` runs them sequentially with
  per-context budgets and a machine-readable summary. The `Dispatcher` gained an
  optional `allowed_tools` filter (default `None` = all).
- **`red_loop/soak.py`** — unattended loop over contexts: periodic checkpoints,
  scenario rotation, fingerprint duplicate suppression, stagnation detection,
  automatic reassignment on expired lease, wall-clock + cycle budgets, and a
  final `m4-direct-e2e-soak.json`. Never idle-loops: with no productive work and
  no open hypotheses it records `no_productive_work` and stops.
- **`red_loop/lifecycle_gate.py`** — the closing gate: a run cannot close
  `successful` while its highest-priority hypothesis is `proposed`/`claimed` (or
  any top-tier hypothesis lacks a resolved status), a `blocked` lacks a valid
  blocker, or a `supported` unauthorized effect lacks an independent replay.
  Machine-readable reasons; `export_lifecycle()` writes
  `m4-hypothesis-lifecycle.json`. This closes the M3.1 hole where acceptance
  ignored hypotheses entirely.
- **`surface/m4_recovery_matrix.py`** — failure-recovery scenarios
  (`explorer_process_kill`, `subagent_kill`, `model_provider_timeout` via an
  in-process fake gateway, `expired_lease`, `partially_written_artifact`,
  `interrupted_replay`, `interrupted_acceptance`). The docker-dependent scenarios
  (`service_restart`, `queue_consumer_restart`, `database_restart`) are
  environment-gated: `blocked: environment_unavailable` unless
  `M4_DOCKER_RECOVERY=1`. The model-timeout scenario uses a throwaway fake key and
  never reads the real `LITELLM_API_KEY` or makes a real model call.

Hooks into the existing loop are minimal: `campaign._progress_signature` also
counts v2 terminal states; the stagnation/recon replans write a machine-readable
`replans.jsonl` record before stopping; `tools.Dispatcher` optionally takes
`hyp_manager`/`lease_manager`/`allowed_tools` and exposes the
`define_experiment`/`mark_blocked`/`request_replay` tools (no-ops unless a
`hyp_manager` is wired); `run.py resume` recovers stranded leases.
