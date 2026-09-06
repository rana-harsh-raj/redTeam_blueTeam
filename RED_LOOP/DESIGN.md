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
